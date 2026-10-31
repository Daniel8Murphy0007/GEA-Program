# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""mseed - the records the box writes: SEED 2.4 data records, 512 bytes, big-endian, INT32 samples, a blockette
1000 (encoding, byte order, record length) and a blockette 1001 (the timing quality and the microseconds the
header's 0.1 ms field cannot hold). The plainest form the archives read and the one GEA-Program reads as it is
(its reader and writer were proven on each other); no compression, so a damaged record costs 112 samples and
nothing after it. A reader of this module's own is here too, for the self-test."""
from __future__ import annotations

import datetime as _dt
import struct
from typing import List, Tuple

import numpy as np

RECLEN = 512
SAMPLES_PER_RECORD = (RECLEN - 64) // 4            # 112


def _rate_to_factor(rate: float) -> Tuple[int, int]:
    if rate >= 1.0 and abs(rate - round(rate)) < 1e-9:
        return int(round(rate)), 1
    if rate < 1.0 and abs(1.0 / rate - round(1.0 / rate)) < 1e-9:
        return -int(round(1.0 / rate)), 1
    for mult in range(1, 1000):
        if abs(rate * mult - round(rate * mult)) < 1e-9:
            return int(round(rate * mult)), -mult
    raise ValueError(f'cannot express sample rate {rate} as SEED factor/multiplier')


def _btime(t: float) -> Tuple[int, int, int, int, int, int, int]:
    d = _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc)
    us = int(round(d.microsecond))
    frac = us // 100
    return d.year, d.timetuple().tm_yday, d.hour, d.minute, d.second, frac, us - frac * 100


def record(network: str, station: str, location: str, channel: str, t0: float, rate: float, samples: np.ndarray,
           seq: int, timing_quality: int = 0) -> bytes:
    """One 512-byte record holding up to 112 INT32 samples starting at epoch t0."""
    n = int(len(samples))
    if not (1 <= n <= SAMPLES_PER_RECORD):
        raise ValueError(f'a record holds 1 to {SAMPLES_PER_RECORD} samples, not {n}')
    fac, mult = _rate_to_factor(rate)
    year, doy, hh, mm, ss, frac, usec = _btime(t0)
    hdr = (f'{seq % 1000000:06d}'.encode() + b'D' + b' '
           + station[:5].ljust(5).encode() + location[:2].ljust(2).encode() + channel[:3].ljust(3).encode() + network[:2].ljust(2).encode()
           + struct.pack('>HHBBBBH', year, doy, hh, mm, ss, 0, frac)
           + struct.pack('>Hhh', n, fac, mult)
           + struct.pack('BBBB', 0, 0, 0, 2)
           + struct.pack('>iHH', 0, 64, 48))
    b1000 = struct.pack('>HHBBBB', 1000, 56, 3, 1, 9, 0)                    # INT32, big-endian, 2^9 = 512
    b1001 = struct.pack('>HHBbBB', 1001, 0, int(max(0, min(100, timing_quality))), usec, 0, 0)
    body = np.asarray(samples, dtype='>i4').tobytes().ljust(RECLEN - 64, b'\x00')
    rec = (hdr + b1000 + b1001).ljust(64, b'\x00') + body
    assert len(rec) == RECLEN
    return rec


def read(path: str) -> List[dict]:
    """The records of a file this module wrote: [{network, station, location, channel, starttime, rate, data, timing_quality}]."""
    out = []
    with open(path, 'rb') as f:
        buf = f.read()
    off = 0
    while off + RECLEN <= len(buf):
        h = buf[off:off + 64]
        station = h[8:13].decode('ascii').strip(); loc = h[13:15].decode('ascii').strip(); chan = h[15:18].decode('ascii').strip(); net = h[18:20].decode('ascii').strip()
        year, doy, hh, mm, ss, _, frac = struct.unpack('>HHBBBBH', h[20:30])
        n, fac, mult = struct.unpack('>Hhh', h[30:36])
        rate = (fac if fac > 0 else 1.0 / -fac) * (mult if mult > 0 else 1.0 / -mult)
        tq, usec = 0, 0
        b_off = 48
        while b_off and b_off < 64:
            btype, nxt = struct.unpack('>HH', h[b_off:b_off + 4])
            if btype == 1001:
                tq, usec = h[b_off + 4], struct.unpack('b', h[b_off + 5:b_off + 6])[0]
            b_off = nxt
        t0 = (_dt.datetime(year, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss,
                                                                                  microseconds=frac * 100 + usec)).timestamp()
        data = np.frombuffer(buf[off + 64:off + 64 + 4 * n], dtype='>i4').astype(np.int32)
        out.append({'network': net, 'station': station, 'location': loc, 'channel': chan, 'starttime': t0, 'rate': rate, 'data': data, 'timing_quality': int(tq)})
        off += RECLEN
    return out
