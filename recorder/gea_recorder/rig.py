# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""rig - the rig's own feed, on the box's clock.

The EDR on most rigs will send WITS0 (the level-0 serial protocol every drilling recorder speaks: frames of
`&&`, lines of a four-digit item code and a value, `!!`) over a serial line or a TCP port. The box logs every
frame raw, exactly as received, with the box's time of receipt (rig/wits0.<hour>.log), and writes the items the
downstream arithmetic needs as a CSV on the same clock (rig/depth.csv): the bit depth and hole depth above all,
since they are the seismic source's position; the block height, hookload, rotary speed, torque, weight on bit,
standpipe pressure and flow beside them. Item codes are the WITS0 record 1 standard; a site whose EDR numbers
them differently maps them in the config (rig.items: {"0108": "bit_depth", ...}).

The feed is read; nothing is ever sent to the rig."""
from __future__ import annotations

import csv
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import clock

STANDARD_ITEMS: Dict[str, str] = {
    '0108': 'bit_depth', '0110': 'hole_depth', '0112': 'block_height', '0113': 'rop', '0114': 'hookload', '0116': 'wob',
    '0120': 'rpm', '0121': 'torque', '0123': 'flow_in', '0130': 'spp', '0105': 'rig_date', '0106': 'rig_time',
}
COLUMNS = ['utc', 'bit_depth', 'hole_depth', 'block_height', 'rop', 'hookload', 'wob', 'rpm', 'torque', 'flow_in', 'spp', 'rig_date', 'rig_time']


class FrameParser:
    """Feed bytes in any chunking; get complete frames out as {code: text} (the same shape GEA's reader uses)."""

    def __init__(self):
        self.buf = b''
        self.in_frame = False
        self.cur: Dict[str, str] = {}

    def feed(self, data: bytes) -> List[Dict[str, str]]:
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\n')
            if i < 0:
                break
            line, self.buf = self.buf[:i], self.buf[i + 1:]
            s = line.decode('ascii', 'replace').strip()
            if s == '&&':
                self.in_frame, self.cur = True, {}
            elif s == '!!':
                if self.in_frame:
                    out.append(self.cur)
                self.in_frame, self.cur = False, {}
            elif self.in_frame and len(s) >= 4 and s[:4].isdigit():
                self.cur[s[:4]] = s[4:].strip()
        return out


def frame_row(frame: Dict[str, str], items: Dict[str, str], t: float) -> dict:
    row = {'utc': datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
    for code, name in items.items():
        if code in frame and name in COLUMNS:
            row[name] = frame[code]
    return row


class RigLogger:
    """A thread reading the feed, logging raw frames and the depth CSV; `last` is what the status page shows."""

    def __init__(self, cfg: dict, out_dir: str):
        self.cfg = cfg.get('rig') or {}
        self.items = dict(STANDARD_ITEMS); self.items.update(self.cfg.get('items') or {})
        self.dir = os.path.join(out_dir, 'rig')
        os.makedirs(self.dir, exist_ok=True)
        self.frames = 0
        self.last: Optional[dict] = None
        self.error: Optional[str] = None
        self.connected = False
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._csv_path = os.path.join(self.dir, 'depth.csv')
        self._raw_hour = None
        self._raw = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name='rig', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self._raw:
            self._raw.close()

    def _open_raw(self, t: float):
        from .writer import file_key
        hk = file_key(t, 3600.0)
        if hk != self._raw_hour:
            if self._raw:
                self._raw.close()
            self._raw = open(os.path.join(self.dir, f'wits0.{hk}.log'), 'ab')
            self._raw_hour = hk
        return self._raw

    def handle(self, data: bytes, parser: FrameParser, t: Optional[float] = None) -> List[dict]:
        """Bytes received at time t: logged raw, parsed, the rows appended. Returns the rows (for the self-test)."""
        t = clock.now() if t is None else t
        raw = self._open_raw(t)
        raw.write(datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ').encode() + b' ' + data.replace(b'\r', b'').replace(b'\n', b'|') + b'\n')
        raw.flush()
        rows = []
        for fr in parser.feed(data):
            row = frame_row(fr, self.items, t)
            new = not os.path.isfile(self._csv_path)
            with open(self._csv_path, 'a', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
                if new:
                    w.writeheader()
                w.writerow(row)
            self.frames += 1
            self.last = row
            rows.append(row)
        return rows

    def _run(self) -> None:
        parser = FrameParser()
        while not self._stop.is_set():
            try:
                if self.cfg.get('transport', 'tcp') == 'serial':
                    self._serial_loop(parser)
                else:
                    self._tcp_loop(parser)
            except Exception as e:
                self.error = f'{type(e).__name__}: {e}'
                self.connected = False
                time.sleep(5)

    def _tcp_loop(self, parser: FrameParser) -> None:
        host, port = str(self.cfg.get('host', '127.0.0.1')), int(self.cfg.get('port', 5001))
        with socket.create_connection((host, port), timeout=10) as s:
            s.settimeout(2.0)
            self.connected = True; self.error = None
            while not self._stop.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError('the EDR closed the connection')
                self.handle(data, parser)

    def _serial_loop(self, parser: FrameParser) -> None:
        import serial                      # pyserial, only where a serial feed is configured
        with serial.Serial(str(self.cfg.get('serial', '/dev/ttyUSB0')), int(self.cfg.get('baud', 9600)), timeout=2) as ser:
            self.connected = True; self.error = None
            while not self._stop.is_set():
                data = ser.read(4096)
                if data:
                    self.handle(data, parser)

    def status(self) -> dict:
        return {'enabled': bool(self.cfg.get('enabled')), 'connected': self.connected, 'frames': self.frames, 'last': self.last, 'error': self.error,
                'transport': self.cfg.get('transport', 'tcp')}
