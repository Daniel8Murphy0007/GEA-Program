# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""writer - the files on the box.

    <dir>/<YYYY>/<DDD>/<NET>.<ID>.<CODE>.<YYYY>.<DDD>.<HHMM>.mseed         one file per channel per hour (output.file_minutes shortens it; the SEED station code is
                                                                           the sensor's id, so GEA pairs each record with its sensor)
    <dir>/<YYYY>/<DDD>/<same>.sha256                                       the file's SHA-256, written as the hour closes
    <dir>/manifest.json                                                    every closed file: name, bytes, SHA-256, start, end, channel,
                                                                           samples, the timing quality; the box, the site, the sensors
    <dir>/sensors.csv                                                      sensor_id, lat, lon, x_ft, y_ft, kind, code - the array as GEA reads it
    <dir>/rig/...                                                          the rig feed (rig.py)

Samples are appended as they come (every flush_s seconds, in whole 112-sample records), so a power cut loses at
most that many seconds and never a file. A channel recorded below the ADC rate is averaged down (a boxcar over
the ratio: the anti-alias a bench can reason about) before it is written. The manifest is rebuilt whenever a file
closes; a file that is open is not in it. When a mirror (a USB stick's mount point) is present, each closed file
and the manifest are copied there too. The box never deletes: when free space falls under the floor it stops
recording and the status page says so."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from . import __version__, mseed

FT_PER_DEG_LAT = 364000.0


def ft_to_latlon(x_ft: float, y_ft: float, lat0: float, lon0: float):
    lat = lat0 + y_ft / FT_PER_DEG_LAT
    lon = lon0 + x_ft / (FT_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return round(lat, 7), round(lon, 7)


def file_key(t: float, file_s: float = 3600.0) -> str:
    """The file a sample at t belongs to: the start of its span (an hour by default), as YYYY.DDD.HHMM."""
    t0 = math.floor(t / file_s) * file_s
    d = datetime.fromtimestamp(t0, tz=timezone.utc)
    return f'{d.year:04d}.{d.timetuple().tm_yday:03d}.{d.hour:02d}{d.minute:02d}'


def hour_key(t: float) -> str:
    return file_key(t, 3600.0)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class ChannelFile:
    """One channel's open hour: the file, its sequence number, the samples not yet written."""

    def __init__(self, path: str, t_first: float, rate: float, meta: dict):
        self.path = path
        self.rate = rate
        self.meta = meta
        self.t_next = t_first                 # the time of the first sample still unwritten
        self.pending = np.zeros(0, dtype=np.int32)
        self.seq = 1
        self.n_written = 0
        self.t_start = t_first
        self.tq_min = 100
        self.tq_max = 0
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def add(self, samples: np.ndarray) -> None:
        self.pending = np.concatenate([self.pending, samples.astype(np.int32)])

    def flush(self, net: str, sta: str, chan_id: str, code: str, timing_quality: int, final: bool = False) -> int:
        n_rec = len(self.pending) // mseed.SAMPLES_PER_RECORD
        tail = len(self.pending) - n_rec * mseed.SAMPLES_PER_RECORD
        if final and tail:
            n_rec += 1
        if n_rec == 0:
            return 0
        written = 0
        with open(self.path, 'ab') as f:
            for k in range(n_rec):
                chunk = self.pending[k * mseed.SAMPLES_PER_RECORD:(k + 1) * mseed.SAMPLES_PER_RECORD]
                if len(chunk) == 0:
                    break
                f.write(mseed.record(net, chan_id[:5], '', code, self.t_next, self.rate, chunk, self.seq, timing_quality))
                self.seq += 1
                self.t_next += len(chunk) / self.rate
                written += len(chunk)
        self.pending = self.pending[written:]
        self.n_written += written
        self.tq_min = min(self.tq_min, timing_quality); self.tq_max = max(self.tq_max, timing_quality)
        return written

    @property
    def t_end(self) -> float:
        return self.t_next


class Writer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = cfg['output']
        self.dir = self.out['dir']
        self.mirror = self.out.get('mirror') or ''
        self.net, self.sta = str(cfg['network']), str(cfg['station'])
        self.fs = float(cfg['adc']['sample_rate'])
        self.channels: List[dict] = cfg['adc']['channels']
        self.open: Dict[int, ChannelFile] = {}
        self.closed: List[dict] = []
        self.lock = threading.Lock()
        self.last_flush = time.monotonic()
        self.decim_carry: Dict[int, np.ndarray] = {}
        self.stopped_reason: Optional[str] = None
        self.bytes_written = 0
        self.file_s = 60.0 * float(self.out.get('file_minutes', 60))
        os.makedirs(self.dir, exist_ok=True)
        self._load_manifest()
        self.write_sensors()
        self.write_manifest()

    # -- the array as GEA reads it ------------------------------------------------------------------------------------------
    def sensors_rows(self) -> List[dict]:
        wh = self.cfg.get('wellhead') or {}
        lat0, lon0 = wh.get('lat'), wh.get('lon')
        rows = []
        for c in self.channels:
            x, y = float(c.get('x_ft', 0.0)), float(c.get('y_ft', 0.0))
            lat, lon = ft_to_latlon(x, y, float(lat0), float(lon0)) if lat0 is not None and lon0 is not None else ('', '')
            rows.append({'sensor_id': str(c['id']), 'lat': lat, 'lon': lon, 'x_ft': x, 'y_ft': y, 'kind': c['kind'], 'code': c['code'], 'rate': c.get('rate', self.fs),
                         'dipole_ft': c.get('dipole_ft', ''), 'azimuth_deg': c.get('azimuth_deg', ''), 'note': c.get('note', '')})
        return rows

    def write_sensors(self) -> str:
        p = os.path.join(self.dir, 'sensors.csv')
        rows = self.sensors_rows()
        with open(p, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        return p

    # -- the manifest ----------------------------------------------------------------------------------------------------------
    def _manifest_path(self) -> str:
        return os.path.join(self.dir, 'manifest.json')

    def _load_manifest(self) -> None:
        try:
            with open(self._manifest_path(), encoding='utf-8') as f:
                self.closed = json.load(f).get('files', [])
        except (OSError, ValueError):
            self.closed = []

    def write_manifest(self) -> dict:
        m = {'program': 'GEA Recorder', 'version': __version__, 'site': self.cfg['site'], 'network': self.net, 'station': self.sta,
             'wellhead': self.cfg.get('wellhead', {}), 'written_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'sensors': self.sensors_rows(), 'files': self.closed, 'n_files': len(self.closed), 'bytes': sum(f['bytes'] for f in self.closed),
             'note': 'every sample on the box\'s one clock; the timing quality of each hour is in its records (blockette 1001) and here'}
        tmp = self._manifest_path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(m, f, indent=1)
        os.replace(tmp, self._manifest_path())
        self._mirror(self._manifest_path())
        self._mirror(os.path.join(self.dir, 'sensors.csv'))
        return m

    def _mirror(self, path: str) -> None:
        if not self.mirror or not os.path.isdir(self.mirror):
            return
        try:
            rel = os.path.relpath(path, self.dir)
            dst = os.path.join(self.mirror, 'gea-recorder', self.sta, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(path, dst)
        except OSError:
            pass

    # -- samples in -----------------------------------------------------------------------------------------------------------
    def free_mb(self) -> float:
        try:
            return shutil.disk_usage(self.dir).free / 1048576.0
        except OSError:
            return float('inf')

    def _path_for(self, c: dict, t: float) -> str:
        d = datetime.fromtimestamp(t, tz=timezone.utc)
        name = f"{self.net}.{c['id']}.{c['code']}.{file_key(t, self.file_s)}.mseed"
        return os.path.join(self.dir, f'{d.year:04d}', f'{d.timetuple().tm_yday:03d}', name)

    def add_block(self, block: np.ndarray, t_first: float, timing_quality: int) -> None:
        """A block from the source: each channel decimated to its rate, appended to its hour's file (a new hour
        closes the old file, hashes it and rebuilds the manifest), flushed every flush_s seconds."""
        if self.free_mb() < float(self.out.get('min_free_mb', 500)):
            self.stopped_reason = f'free space under {self.out.get("min_free_mb", 500)} MB: recording stopped, nothing deleted'
            return
        self.stopped_reason = None
        with self.lock:
            for j, c in enumerate(self.channels):
                rate = float(c.get('rate', self.fs))
                ratio = int(round(self.fs / rate))
                x = block[j]
                t0 = t_first
                if ratio > 1:
                    carry = self.decim_carry.get(j, np.zeros(0, dtype=np.int64))
                    n_carry = len(carry)
                    x = np.concatenate([carry, x.astype(np.int64)])
                    n_full = len(x) // ratio
                    self.decim_carry[j] = x[n_full * ratio:]
                    t0 = t_first - n_carry / self.fs
                    x = np.round(x[:n_full * ratio].reshape(n_full, ratio).mean(axis=1)).astype(np.int32) if n_full else np.zeros(0, dtype=np.int32)
                    if len(x) == 0:
                        continue
                # the samples go to the hour they fall in: a block that straddles the hour is split at it
                while len(x):
                    cf = self.open.get(j)
                    if cf is not None and file_key(cf.t_start, self.file_s) != file_key(t0 + 1e-6, self.file_s):
                        self._close(j, cf, c, timing_quality)
                        cf = None
                    if cf is None:
                        cf = ChannelFile(self._path_for(c, t0), t0, rate, c)
                        self.open[j] = cf
                    next_hour = (math.floor(t0 / self.file_s) + 1) * self.file_s
                    k = int(math.ceil((next_hour - t0) * rate - 1e-9))
                    k = max(1, min(k, len(x)))
                    cf.add(x[:k])
                    t0 += k / rate
                    x = x[k:]
            now = time.monotonic()
            if now - self.last_flush >= float(self.out.get('flush_s', 5)):
                self.flush(timing_quality)
                self.last_flush = now

    def flush(self, timing_quality: int, final: bool = False) -> int:
        n = 0
        for j, cf in list(self.open.items()):
            c = self.channels[j]
            n += cf.flush(self.net, self.sta, str(c['id']), str(c['code']), timing_quality, final=final)
            if final:
                self._close(j, cf, c, timing_quality)
        return n

    def _close(self, j: int, cf: ChannelFile, c: dict, timing_quality: int) -> None:
        cf.flush(self.net, self.sta, str(c['id']), str(c['code']), timing_quality, final=True)
        self.open.pop(j, None)
        if not os.path.isfile(cf.path) or cf.n_written == 0:
            return
        digest = sha256_file(cf.path)
        with open(cf.path + '.sha256', 'w', encoding='utf-8') as f:
            f.write(f'{digest}  {os.path.basename(cf.path)}\n')
        size = os.path.getsize(cf.path)
        self.bytes_written += size
        rel = os.path.relpath(cf.path, self.dir).replace(os.sep, '/')
        entry = {'file': rel, 'bytes': size, 'sha256': digest, 'sensor_id': str(c['id']), 'code': str(c['code']), 'kind': c['kind'], 'rate': cf.rate,
                 'samples': cf.n_written, 'start_utc': datetime.fromtimestamp(cf.t_start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                 'end_utc': datetime.fromtimestamp(cf.t_end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'timing_quality': [cf.tq_min, cf.tq_max]}
        self.closed = [e for e in self.closed if e['file'] != rel] + [entry]
        self._mirror(cf.path); self._mirror(cf.path + '.sha256')
        self.write_manifest()

    def stop(self, timing_quality: int) -> None:
        with self.lock:
            self.flush(timing_quality, final=True)
            self.write_manifest()

    def levels(self) -> List[dict]:
        """Each channel's open hour: samples written, the last record's time."""
        out = []
        for j, c in enumerate(self.channels):
            cf = self.open.get(j)
            out.append({'sensor_id': c['id'], 'code': c['code'], 'kind': c['kind'], 'rate': c.get('rate', self.fs),
                        'file': os.path.relpath(cf.path, self.dir) if cf else None, 'samples_this_hour': cf.n_written if cf else 0,
                        'pending': int(len(cf.pending)) if cf else 0})
        return out
