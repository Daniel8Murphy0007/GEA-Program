# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""config - the box's one file: what it records, from what, at what rate, into where. JSON, checked on load so a
mistake is named before anything is recorded."""
from __future__ import annotations

import json
import os
from typing import Dict, List

DEFAULT_PATH = '/etc/gea-recorder/config.json'

EXAMPLE: Dict = {
    "site": "Pad 3",
    "network": "GE",
    "station": "P3",
    "wellhead": {"lat": 31.0, "lon": -102.0, "note": "the surface position the station coordinates are measured from"},
    "clock": {"source": "chrony", "note": "chrony disciplined by GPS PPS on GPIO 18 (see README); 'system' records the box's own clock and says so"},
    "adc": {
        "driver": "sim",
        "note": "sim (no hardware, a test signal) | ads1256 (the 8-channel 24-bit SPI HAT) | csv (replay a file)",
        "sample_rate": 200,
        "spi_bus": 0, "spi_device": 0, "drdy_gpio": 17, "gain": 1, "vref_v": 2.5,
        "block_s": 0.5,
        "channels": [
            {"index": 0, "id": "REF", "code": "HHZ", "kind": "geophone", "x_ft": 40, "y_ft": 0, "rate": 200, "note": "the reference, beside the substructure"},
            {"index": 1, "id": "S01", "code": "HHZ", "kind": "geophone", "x_ft": 1800, "y_ft": 900, "rate": 200},
            {"index": 2, "id": "S02", "code": "HHZ", "kind": "geophone", "x_ft": -1200, "y_ft": 1500, "rate": 200},
            {"index": 3, "id": "E01", "code": "LQE", "kind": "electrode", "x_ft": 0, "y_ft": 0, "dipole_ft": 100, "azimuth_deg": 90, "rate": 10, "note": "an east-west dipole, 100 ft"},
            {"index": 4, "id": "E02", "code": "LQN", "kind": "electrode", "x_ft": 0, "y_ft": 0, "dipole_ft": 100, "azimuth_deg": 0, "rate": 10}
        ]
    },
    "rig": {"enabled": False, "transport": "tcp", "host": "10.0.0.5", "port": 5001, "serial": "/dev/ttyUSB0", "baud": 9600,
            "note": "the EDR's WITS0 feed: bit depth, hole depth and the rig channels, logged raw and as a CSV on the box's clock"},
    "output": {"dir": "/var/lib/gea-recorder/data", "mirror": "", "min_free_mb": 500, "flush_s": 5, "file_minutes": 60,
               "note": "mirror: a USB stick's mount point, copied to as each file closes, when present; file_minutes: a file's span (60: one per hour)"},
    "status": {"port": 8780, "bind": "0.0.0.0"},
}

KINDS = ('geophone', 'accelerometer', 'seismometer', 'electrode', 'pressure', 'other')
DRIVERS = ('sim', 'ads1256', 'csv')


class ConfigError(ValueError):
    pass


def load(path: str = DEFAULT_PATH) -> dict:
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    return check(cfg)


def write_example(path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE, f, indent=1)
    return path


def check(cfg: dict) -> dict:
    """The config as given, with every field present and every value in range, or ConfigError naming the first fault."""
    if not isinstance(cfg, dict):
        raise ConfigError('the config is a JSON object')
    for k in ('site', 'network', 'station', 'adc', 'output'):
        if k not in cfg:
            raise ConfigError(f'the config needs "{k}"')
    net, sta = str(cfg['network']), str(cfg['station'])
    if not (1 <= len(net) <= 2 and net.isalnum()) or not (1 <= len(sta) <= 5 and sta.isalnum()):
        raise ConfigError('network is 1-2 letters or digits, station 1-5 (SEED codes)')
    adc = cfg['adc']
    if adc.get('driver') not in DRIVERS:
        raise ConfigError(f'adc.driver is one of {", ".join(DRIVERS)}')
    fs = float(adc.get('sample_rate', 0))
    if not (1.0 <= fs <= 4000.0):
        raise ConfigError('adc.sample_rate is 1 to 4000 samples/s')
    chans: List[dict] = adc.get('channels') or []
    if not chans:
        raise ConfigError('adc.channels lists at least one channel')
    seen = set()
    for c in chans:
        for k in ('index', 'id', 'code', 'kind'):
            if k not in c:
                raise ConfigError(f'every channel needs "{k}" (channel {c})')
        if c['kind'] not in KINDS:
            raise ConfigError(f'channel {c["id"]}: kind is one of {", ".join(KINDS)}')
        if not (isinstance(c['index'], int) and 0 <= c['index'] < 32):
            raise ConfigError(f'channel {c["id"]}: index is the ADC input, 0-31')
        if len(str(c['code'])) != 3:
            raise ConfigError(f'channel {c["id"]}: code is a 3-letter SEED channel code (HHZ, LQE...)')
        key = (str(c['id'])[:5], str(c['code']))
        if key in seen:
            raise ConfigError(f'channel {c["id"]}/{c["code"]} is listed twice')
        seen.add(key)
        r = float(c.get('rate', fs))
        if r > fs + 1e-9 or r <= 0 or abs(fs / r - round(fs / r)) > 1e-9:
            raise ConfigError(f'channel {c["id"]}: rate {r} must divide the ADC rate {fs:g}')
        c['rate'] = r
    out = cfg['output']
    if not out.get('dir'):
        raise ConfigError('output.dir is the folder the files go in')
    cfg.setdefault('clock', {'source': 'system'})
    cfg.setdefault('rig', {'enabled': False})
    cfg.setdefault('status', {'port': 8780, 'bind': '0.0.0.0'})
    cfg.setdefault('wellhead', {})
    adc.setdefault('block_s', 0.5)
    out.setdefault('flush_s', 5)
    out.setdefault('min_free_mb', 500)
    out.setdefault('file_minutes', 60)
    if not (1 <= float(out['file_minutes']) <= 1440) or 60 % int(out['file_minutes']) not in (0,) and int(out['file_minutes']) % 60 != 0:
        raise ConfigError('output.file_minutes divides an hour (1, 2, 5, 10, 15, 20, 30, 60) or is a whole number of hours')
    return cfg
