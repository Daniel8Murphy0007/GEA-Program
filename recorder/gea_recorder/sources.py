# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""sources - where the samples come from.

Every source has the same shape: `open()`, then `read()` returns the next block as (int32 array [channels, n],
epoch seconds of the block's first sample), then `close()`. The service never knows which one it has.

- `sim`: no hardware; a test signal a bench can check - white noise, a 1.8 Hz pump line with harmonics on the
  geophone channels, a slow drift on the electrode channels, and a known calibration tone; paced to real time
  unless told to run free (the self-test);
- `ads1256`: the 8-channel 24-bit SPI ADC HAT (Texas Instruments ADS1256; the Waveshare High-Precision AD/DA
  board carries it): the chip cycles its multiplexer through the channels in use, each conversion read on DRDY;
  spidev and lgpio are imported only here, so the rest of the box runs anywhere;
- `csv`: a file of columns replayed, for a bench run from a recording.

A sample is an integer count. The sensitivity (counts per volt, volts per unit) is in the manifest, not applied.
"""
from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

import numpy as np

from . import clock


class Source:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.adc = cfg['adc']
        self.fs = float(self.adc['sample_rate'])
        self.channels: List[dict] = list(self.adc['channels'])
        self.n_ch = len(self.channels)
        self.block = max(1, int(round(float(self.adc.get('block_s', 0.5)) * self.fs)))
        self.sample_index = 0
        self.t0: Optional[float] = None

    def open(self) -> None:
        self.t0 = clock.now()
        self.sample_index = 0

    def close(self) -> None:
        pass

    def read(self) -> Tuple[np.ndarray, float]:
        raise NotImplementedError

    def describe(self) -> str:
        return type(self).__name__


class SimSource(Source):
    """A test signal: noise plus a pump line and its harmonics on the geophones, a drift on the electrodes, and a
    10 Hz calibration tone of 1,000 counts on every channel so a bench can check the scale in one look."""

    def __init__(self, cfg: dict, realtime: bool = True, seed: int = 1):
        super().__init__(cfg)
        self.realtime = realtime
        self.rng = np.random.default_rng(seed)
        self.next_due: Optional[float] = None

    def open(self) -> None:
        super().open()
        self.next_due = self.t0

    def read(self) -> Tuple[np.ndarray, float]:
        n = self.block
        k = self.sample_index + np.arange(n)
        t = k / self.fs
        t_first = self.t0 + self.sample_index / self.fs
        out = np.zeros((self.n_ch, n), dtype=np.int32)
        for j, ch in enumerate(self.channels):
            x = 200.0 * self.rng.normal(0.0, 1.0, n)
            if ch['kind'] in ('geophone', 'accelerometer', 'seismometer'):
                for h, a in ((1, 3000.0), (2, 1500.0), (3, 800.0), (4, 400.0)):
                    x += a * np.sin(2 * np.pi * 1.8 * h * t + 0.3 * h)
            elif ch['kind'] == 'electrode':
                x += 20000.0 * np.sin(2 * np.pi * t / 600.0) + 2000.0 * np.sin(2 * np.pi * 60.0 * t)
            x += 1000.0 * np.sin(2 * np.pi * 10.0 * t)
            out[j] = np.clip(np.round(x), -2 ** 23, 2 ** 23 - 1).astype(np.int32)
        self.sample_index += n
        if self.realtime:
            self.next_due += n / self.fs
            wait = self.next_due - clock.now()
            if wait > 0:
                time.sleep(wait)
        return out, t_first

    def describe(self) -> str:
        return f'sim: {self.n_ch} channels at {self.fs:g} samples/s, a test signal (no hardware)'


class CsvSource(Source):
    """Replay: a CSV whose columns are the channels, one row per sample, at the configured rate."""

    def __init__(self, cfg: dict, path: str, realtime: bool = False):
        super().__init__(cfg)
        self.path = path
        self.realtime = realtime
        self.data: Optional[np.ndarray] = None
        self.pos = 0

    def open(self) -> None:
        super().open()
        rows = np.loadtxt(self.path, delimiter=',', ndmin=2)
        if rows.shape[1] < self.n_ch:
            raise ValueError(f'{self.path}: {rows.shape[1]} columns, {self.n_ch} channels configured')
        self.data = np.round(rows[:, :self.n_ch].T).astype(np.int32)
        self.pos = 0

    def read(self) -> Tuple[np.ndarray, float]:
        if self.data is None or self.pos >= self.data.shape[1]:
            raise EOFError('the replay is finished')
        n = min(self.block, self.data.shape[1] - self.pos)
        blk = self.data[:, self.pos:self.pos + n]
        t_first = self.t0 + self.pos / self.fs
        self.pos += n
        self.sample_index = self.pos
        if self.realtime:
            time.sleep(n / self.fs)
        return blk, t_first

    def describe(self) -> str:
        return f'csv replay: {self.path}'


# -- the ADS1256 ----------------------------------------------------------------------------------------------------------

ADS1256_DRATE = {30000: 0xF0, 15000: 0xE0, 7500: 0xD0, 3750: 0xC0, 2000: 0xB0, 1000: 0xA1, 500: 0x92, 100: 0x82, 60: 0x72,
                 50: 0x63, 30: 0x53, 25: 0x43, 15: 0x33, 10: 0x23, 5: 0x13, 2.5: 0x03}
ADS1256_GAIN = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6}
CMD_WAKEUP, CMD_RDATA, CMD_RREG, CMD_WREG, CMD_SELFCAL, CMD_SYNC, CMD_RESET, CMD_SDATAC = 0x00, 0x01, 0x10, 0x50, 0xF0, 0xFC, 0xFE, 0x0F
REG_STATUS, REG_MUX, REG_ADCON, REG_DRATE = 0, 1, 2, 3


def ads1256_drate_for(fs: float, n_ch: int) -> int:
    """The chip's data rate that gives every channel at least `fs`, cycling through n_ch inputs (one conversion
    per channel per sample, plus the multiplexer's settling that the chip's own documentation puts at one
    conversion): the smallest rate code at or above fs * (n_ch + 1)."""
    need = fs * (n_ch + 1)
    for rate in sorted(ADS1256_DRATE):
        if rate >= need:
            return ADS1256_DRATE[rate]
    raise ValueError(f'{fs:g} samples/s on {n_ch} channels needs {need:g} conversions/s; the ADS1256 tops out at 30,000')


class Ads1256Source(Source):
    """The ADS1256 over SPI, one chip cycling its multiplexer through the configured inputs against AINCOM. Each
    conversion is waited for on DRDY. The block's first-sample time is the box's clock when its first conversion
    completed. This driver follows the datasheet (SBAS288) and the Waveshare board's wiring (SPI0, DRDY on GPIO 17,
    RESET on 18, CS on 22); it has run on the bench only through the simulator here - the first Pi with the HAT
    verifies the rate it achieves, which the status page reports as samples per second recorded."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.spi = None
        self.gpio = None
        self.h = None
        self.drdy = int(self.adc.get('drdy_gpio', 17))
        self.rst = int(self.adc.get('reset_gpio', 18))
        self.cs = int(self.adc.get('cs_gpio', 22))
        self.gain = int(self.adc.get('gain', 1))
        self.inputs = [int(c['index']) for c in self.channels]

    def open(self) -> None:
        import spidev                        # the hardware's own modules, here and nowhere else
        import lgpio
        super().open()
        self.spi = spidev.SpiDev()
        self.spi.open(int(self.adc.get('spi_bus', 0)), int(self.adc.get('spi_device', 0)))
        self.spi.max_speed_hz = 1_000_000
        self.spi.mode = 0b01
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.h, self.drdy)
        lgpio.gpio_claim_output(self.h, self.rst, 1)
        lgpio.gpio_claim_output(self.h, self.cs, 1)
        self.gpio = lgpio
        self._reset()
        self._write_reg(REG_STATUS, 0x04)                         # MSB first, auto-calibrate off, buffer off
        self._write_reg(REG_ADCON, ADS1256_GAIN[self.gain])      # clock out off, sensor detect off, the gain
        self._write_reg(REG_DRATE, ads1256_drate_for(self.fs, self.n_ch))
        self._cmd(CMD_SELFCAL)
        self._wait_drdy()

    def _cs(self, low: bool) -> None:
        self.gpio.gpio_write(self.h, self.cs, 0 if low else 1)

    def _reset(self) -> None:
        self.gpio.gpio_write(self.h, self.rst, 1); time.sleep(0.2)
        self.gpio.gpio_write(self.h, self.rst, 0); time.sleep(0.2)
        self.gpio.gpio_write(self.h, self.rst, 1); time.sleep(0.2)

    def _wait_drdy(self, timeout_s: float = 1.0) -> None:
        t_end = time.monotonic() + timeout_s
        while self.gpio.gpio_read(self.h, self.drdy):
            if time.monotonic() > t_end:
                raise TimeoutError('ADS1256: DRDY did not fall - is the HAT seated and SPI enabled?')

    def _cmd(self, c: int) -> None:
        self._cs(True); self.spi.writebytes([c]); self._cs(False)

    def _write_reg(self, reg: int, val: int) -> None:
        self._cs(True); self.spi.writebytes([CMD_WREG | reg, 0x00, val]); self._cs(False)

    def _read_data(self) -> int:
        self._cs(True)
        self.spi.writebytes([CMD_RDATA])
        time.sleep(10e-6)
        b = self.spi.readbytes(3)
        self._cs(False)
        v = (b[0] << 16) | (b[1] << 8) | b[2]
        return v - (1 << 24) if v & 0x800000 else v

    def _select(self, inp: int) -> None:
        self._write_reg(REG_MUX, ((inp & 0x07) << 4) | 0x08)     # AINp = input, AINn = AINCOM
        self._cmd(CMD_SYNC); self._cmd(CMD_WAKEUP)

    def read(self) -> Tuple[np.ndarray, float]:
        n = self.block
        out = np.zeros((self.n_ch, n), dtype=np.int32)
        t_first = None
        for i in range(n):
            for j, inp in enumerate(self.inputs):
                self._select(inp)
                self._wait_drdy()
                if t_first is None:
                    t_first = clock.now()
                out[j, i] = self._read_data()
        self.sample_index += n
        return out, float(t_first if t_first is not None else clock.now())

    def close(self) -> None:
        try:
            if self.spi:
                self.spi.close()
            if self.h is not None and self.gpio:
                self.gpio.gpiochip_close(self.h)
        except Exception:
            pass

    def describe(self) -> str:
        return f'ADS1256 on SPI, {self.n_ch} inputs at {self.fs:g} samples/s, gain {self.gain}'


def make(cfg: dict, **kw) -> Source:
    d = cfg['adc']['driver']
    if d == 'sim':
        return SimSource(cfg, realtime=kw.get('realtime', True), seed=int(kw.get('seed', 1)))
    if d == 'csv':
        return CsvSource(cfg, cfg['adc'].get('path') or kw.get('path', ''), realtime=kw.get('realtime', False))
    if d == 'ads1256':
        return Ads1256Source(cfg)
    raise ValueError(f'unknown adc.driver {d!r}')
