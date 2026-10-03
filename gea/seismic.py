# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic - raw seismic records in, spectra out. The ingest of the second leg.

This module reads the two binary formats the public archives and most field
recorders write - miniSEED (SEED 2.4 data records, Steim1/Steim2/integer/
float encodings) and SAC - with numpy and the standard library only, fetches
records from any FDSN web service (EarthScope/IRIS, TexNet, ...), and turns a
continuous record into the standard spectral products: Welch power spectral
density, a spectrogram, and the list of persistent spectral lines above the
local noise floor. Those products are what the detectability test
(`seismic_detect`) consumes.

What this module is: a reader and a spectrum estimator - textbook signal
processing (Welch 1967; the SEED 2.4 manual, appendix B, for the Steim
codes). What it is not: a locator, a tomograph or an imager. Nothing here
decides where a source is; it reports what is in the record, with the
instrument response left exactly as recorded (counts), because removing a
response needs the station's response file and a declared unit - a later
step, named when it happens.

    gea seismic --action info     --file day.mseed
    gea seismic --action spectrum --file day.mseed --band 1 50 --out spectrum.csv
    gea seismic --action fetch    --base https://service.iris.edu --network TX --station PB01 --channel HHZ \\
                                  --start 2024-03-01T00:00:00 --end 2024-03-01T01:00:00 --out tx.mseed
"""

from __future__ import annotations

import datetime as _dt
import io
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlencode

import numpy as np

# ---------------------------------------------------------------------------
# time helpers (epoch seconds, UTC)
# ---------------------------------------------------------------------------
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def parse_time(s: Union[str, float, int, _dt.datetime]) -> float:
    """ISO-8601 (with or without 'Z'/offset), a datetime, or epoch seconds -> epoch seconds UTC."""
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, _dt.datetime):
        if s.tzinfo is None:
            s = s.replace(tzinfo=_dt.timezone.utc)
        return s.timestamp()
    t = str(s).strip()
    if t.endswith('Z') or t.endswith('z'):
        t = t[:-1] + '+00:00'
    if 'T' not in t and ' ' in t:
        t = t.replace(' ', 'T', 1)
    m = re.match(r'^(.*?\d{2}:\d{2}:\d{2})\.(\d+)(.*)$', t)
    if m:                                             # Python 3.10 accepts 3 or 6 fraction digits only: pad to microseconds
        frac = (m.group(2) + '000000')[:6]
        t = f"{m.group(1)}.{frac}{m.group(3)}"
    d = _dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def iso(t: float, digits: int = 4) -> str:
    d = _EPOCH + _dt.timedelta(seconds=float(t))
    s = d.strftime('%Y-%m-%dT%H:%M:%S')
    frac = f"{d.microsecond / 1e6:.{digits}f}"[1:] if digits else ''
    return s + frac + 'Z'


def _btime_to_epoch(year: int, doy: int, hour: int, minute: int, sec: int, frac0001: int) -> float:
    d = _dt.datetime(year, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=doy - 1, hours=hour, minutes=minute, seconds=sec)
    return d.timestamp() + frac0001 / 10000.0


def _epoch_to_btime(t: float) -> Tuple[int, int, int, int, int, int]:
    d = _EPOCH + _dt.timedelta(seconds=float(t))
    frac = int(round((d.microsecond / 1e6) * 10000.0))
    if frac >= 10000:
        frac = 9999
    return d.year, d.timetuple().tm_yday, d.hour, d.minute, d.second, frac


# ---------------------------------------------------------------------------
# the trace
# ---------------------------------------------------------------------------
@dataclass
class Trace:
    """One continuous channel: counts as recorded (no response removed), evenly sampled."""
    network: str
    station: str
    location: str
    channel: str
    starttime: float                 # epoch seconds UTC of the first sample
    sample_rate: float
    data: np.ndarray
    source: str = ''                 # file or URL
    encoding: str = ''               # STEIM1 / STEIM2 / INT32 / INT16 / FLOAT32 / FLOAT64 / SAC
    gaps: List[dict] = field(default_factory=list)      # gaps inside the record that were bridged or split

    @property
    def id(self) -> str:
        return f"{self.network}.{self.station}.{self.location}.{self.channel}"

    @property
    def npts(self) -> int:
        return int(len(self.data))

    @property
    def endtime(self) -> float:
        return self.starttime + max(self.npts - 1, 0) / self.sample_rate

    @property
    def duration_s(self) -> float:
        return self.npts / self.sample_rate

    def times(self) -> np.ndarray:
        return self.starttime + np.arange(self.npts) / self.sample_rate

    def slice(self, t0: float, t1: float) -> "Trace":
        i0 = int(max(0, np.ceil((t0 - self.starttime) * self.sample_rate - 1e-6)))
        i1 = int(min(self.npts, np.floor((t1 - self.starttime) * self.sample_rate + 1e-6) + 1))
        if i1 <= i0:
            return Trace(self.network, self.station, self.location, self.channel, t0, self.sample_rate, self.data[:0], self.source, self.encoding)
        return Trace(self.network, self.station, self.location, self.channel, self.starttime + i0 / self.sample_rate, self.sample_rate,
                     self.data[i0:i1], self.source, self.encoding)

    def info(self) -> dict:
        d = np.asarray(self.data, dtype=float)
        return {'id': self.id, 'start': iso(self.starttime), 'end': iso(self.endtime), 'sample_rate_hz': self.sample_rate, 'npts': self.npts,
                'duration_s': round(self.duration_s, 3), 'encoding': self.encoding, 'unit': 'counts (instrument response not removed)',
                'min': float(d.min()) if d.size else None, 'max': float(d.max()) if d.size else None,
                'mean': round(float(d.mean()), 3) if d.size else None, 'gaps': len(self.gaps), 'source': self.source}


# ---------------------------------------------------------------------------
# Steim1 / Steim2 (SEED 2.4 appendix B)
# ---------------------------------------------------------------------------
def _sign_extend(v: np.ndarray, bits: int) -> np.ndarray:
    v = v.astype(np.int64) & ((1 << bits) - 1)
    return np.where(v >= (1 << (bits - 1)), v - (1 << bits), v)


def _steim_frames(buf: bytes, order: str) -> np.ndarray:
    n = len(buf) // 64
    return np.frombuffer(buf[:n * 64], dtype=order + 'u4').reshape(n, 16)


def steim1_decode(buf: bytes, nsamp: int, order: str = '>') -> np.ndarray:
    frames = _steim_frames(buf, order)
    if frames.size == 0:
        return np.zeros(0, dtype=np.int32)
    x0 = int(np.int32(frames[0, 1]))
    xn = int(np.int32(frames[0, 2]))
    diffs: List[np.ndarray] = []
    for fi in range(frames.shape[0]):
        w0 = int(frames[fi, 0])
        for k in range(1, 16):
            if fi == 0 and k in (1, 2):
                continue
            nib = (w0 >> (2 * (15 - k))) & 3
            w = frames[fi, k]
            if nib == 0:
                continue
            elif nib == 1:
                b = np.array([(w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF], dtype=np.int64)
                diffs.append(_sign_extend(b, 8))
            elif nib == 2:
                b = np.array([(w >> 16) & 0xFFFF, w & 0xFFFF], dtype=np.int64)
                diffs.append(_sign_extend(b, 16))
            else:
                diffs.append(np.array([int(np.int32(w))], dtype=np.int64))
    d = np.concatenate(diffs) if diffs else np.zeros(0, dtype=np.int64)
    d = d[:nsamp]
    if d.size == 0:
        return np.zeros(0, dtype=np.int32)
    x = np.cumsum(d)
    x = x - x[0] + x0                      # the first difference is against the previous record: the first sample is X0
    if x.size and int(x[-1]) != xn:
        raise ValueError(f"Steim1 reverse integration constant mismatch (got {int(x[-1])}, record says {xn})")
    return x.astype(np.int32)


def steim2_decode(buf: bytes, nsamp: int, order: str = '>') -> np.ndarray:
    frames = _steim_frames(buf, order)
    if frames.size == 0:
        return np.zeros(0, dtype=np.int32)
    x0 = int(np.int32(frames[0, 1]))
    xn = int(np.int32(frames[0, 2]))
    diffs: List[np.ndarray] = []
    for fi in range(frames.shape[0]):
        w0 = int(frames[fi, 0])
        for k in range(1, 16):
            if fi == 0 and k in (1, 2):
                continue
            nib = (w0 >> (2 * (15 - k))) & 3
            w = int(frames[fi, k])
            if nib == 0:
                continue
            if nib == 1:
                b = np.array([(w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF], dtype=np.int64)
                diffs.append(_sign_extend(b, 8))
                continue
            dnib = (w >> 30) & 3
            if nib == 2:
                if dnib == 1:
                    diffs.append(_sign_extend(np.array([w & 0x3FFFFFFF], dtype=np.int64), 30))
                elif dnib == 2:
                    diffs.append(_sign_extend(np.array([(w >> 15) & 0x7FFF, w & 0x7FFF], dtype=np.int64), 15))
                elif dnib == 3:
                    diffs.append(_sign_extend(np.array([(w >> 20) & 0x3FF, (w >> 10) & 0x3FF, w & 0x3FF], dtype=np.int64), 10))
                else:
                    raise ValueError("Steim2: nibble 2 with dnib 0 is undefined")
            else:   # nib == 3
                if dnib == 0:
                    diffs.append(_sign_extend(np.array([(w >> (6 * (4 - j))) & 0x3F for j in range(5)], dtype=np.int64), 6))
                elif dnib == 1:
                    diffs.append(_sign_extend(np.array([(w >> (5 * (5 - j))) & 0x1F for j in range(6)], dtype=np.int64), 5))
                elif dnib == 2:
                    diffs.append(_sign_extend(np.array([(w >> (4 * (6 - j))) & 0x0F for j in range(7)], dtype=np.int64), 4))
                else:
                    raise ValueError("Steim2: nibble 3 with dnib 3 is undefined")
    d = np.concatenate(diffs) if diffs else np.zeros(0, dtype=np.int64)
    d = d[:nsamp]
    if d.size == 0:
        return np.zeros(0, dtype=np.int32)
    x = np.cumsum(d)
    x = x - x[0] + x0
    if x.size and int(x[-1]) != xn:
        raise ValueError(f"Steim2 reverse integration constant mismatch (got {int(x[-1])}, record says {xn})")
    return x.astype(np.int32)


def steim1_encode(x: Sequence[int], max_frames: int, order: str = '>', prev: Optional[int] = None) -> Tuple[bytes, int]:
    """Greedy Steim1 packing of as many samples of x as fit in max_frames frames.
    Returns (bytes, number of samples packed). The first difference is against `prev` (0 when None)."""
    x = np.asarray(x, dtype=np.int64)
    n = len(x)
    if n == 0:
        return b'', 0
    d = np.diff(np.concatenate([[0 if prev is None else int(prev)], x]))
    frames: List[List[int]] = []
    nibbles: List[List[int]] = []
    i = 0
    while i < n and len(frames) < max_frames:
        words: List[int] = [0]
        nibs: List[int] = [0]
        if not frames:
            words += [0, 0]
            nibs += [0, 0]
        while len(words) < 16 and i < n:
            # try 4 x int8, 2 x int16, 1 x int32
            if i + 4 <= n and np.all(np.abs(d[i:i + 4]) <= 127):
                b = [int(v) & 0xFF for v in d[i:i + 4]]
                words.append((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]); nibs.append(1); i += 4
            elif i + 2 <= n and np.all(np.abs(d[i:i + 2]) <= 32767):
                b = [int(v) & 0xFFFF for v in d[i:i + 2]]
                words.append((b[0] << 16) | b[1]); nibs.append(2); i += 2
            elif abs(int(d[i])) <= 2 ** 31 - 1:
                words.append(int(d[i]) & 0xFFFFFFFF); nibs.append(3); i += 1
            else:
                raise ValueError("difference does not fit in 32 bits")
        while len(words) < 16:
            words.append(0); nibs.append(0)
        frames.append(words); nibbles.append(nibs)
    packed = i
    # the integration constants
    frames[0][1] = int(x[0]) & 0xFFFFFFFF
    frames[0][2] = int(x[packed - 1]) & 0xFFFFFFFF
    out = bytearray()
    for words, nibs in zip(frames, nibbles):
        w0 = 0
        for k in range(16):
            w0 |= (nibs[k] & 3) << (2 * (15 - k))
        words[0] = w0
        out += struct.pack(order + '16I', *words)
    return bytes(out), packed


_STEIM2_FORMS = [    # (count, bits, nibble, dnib) in order of density - the greedy encoder tries the densest first
    (7, 4, 3, 2), (6, 5, 3, 1), (5, 6, 3, 0), (4, 8, 1, None), (3, 10, 2, 3), (2, 15, 2, 2), (1, 30, 2, 1)]


def steim2_encode(x: Sequence[int], max_frames: int, order: str = '>', prev: Optional[int] = None) -> Tuple[bytes, int]:
    """Greedy Steim2 packing (the seven forms of SEED 2.4 appendix B); returns (bytes, samples packed)."""
    x = np.asarray(x, dtype=np.int64)
    n = len(x)
    if n == 0:
        return b'', 0
    d = np.diff(np.concatenate([[0 if prev is None else int(prev)], x]))
    frames: List[List[int]] = []
    nibbles: List[List[int]] = []
    i = 0
    while i < n and len(frames) < max_frames:
        words: List[int] = [0]
        nibs: List[int] = [0]
        if not frames:
            words += [0, 0]
            nibs += [0, 0]
        while len(words) < 16 and i < n:
            for cnt, bits, nib, dnib in _STEIM2_FORMS:
                if i + cnt <= n and np.all(d[i:i + cnt] >= -(1 << (bits - 1))) and np.all(d[i:i + cnt] <= (1 << (bits - 1)) - 1):
                    w = 0
                    for v in d[i:i + cnt]:
                        w = (w << bits) | (int(v) & ((1 << bits) - 1))
                    if dnib is not None:
                        w |= dnib << 30
                    words.append(w & 0xFFFFFFFF); nibs.append(nib); i += cnt
                    break
            else:
                raise ValueError("difference does not fit in 30 bits (Steim2)")
        while len(words) < 16:
            words.append(0); nibs.append(0)
        frames.append(words); nibbles.append(nibs)
    packed = i
    frames[0][1] = int(x[0]) & 0xFFFFFFFF
    frames[0][2] = int(x[packed - 1]) & 0xFFFFFFFF
    out = bytearray()
    for words, nibs in zip(frames, nibbles):
        w0 = 0
        for k in range(16):
            w0 |= (nibs[k] & 3) << (2 * (15 - k))
        words[0] = w0
        out += struct.pack(order + '16I', *words)
    return bytes(out), packed


# ---------------------------------------------------------------------------
# miniSEED
# ---------------------------------------------------------------------------
_ENCODINGS = {0: 'ASCII', 1: 'INT16', 3: 'INT32', 4: 'FLOAT32', 5: 'FLOAT64', 10: 'STEIM1', 11: 'STEIM2'}


def _sample_rate(fac: int, mult: int) -> float:
    if fac == 0 or mult == 0:
        return 0.0
    r = float(fac) if fac > 0 else -1.0 / fac
    r = r * mult if mult > 0 else r / (-mult)
    return r


def _rate_to_factor(rate: float) -> Tuple[int, int]:
    if rate >= 1.0 and abs(rate - round(rate)) < 1e-9:
        return int(round(rate)), 1
    if rate < 1.0 and abs(1.0 / rate - round(1.0 / rate)) < 1e-9:
        return -int(round(1.0 / rate)), 1
    # a non-integer rate: factor/multiplier search
    for mult in range(1, 1000):
        if abs(rate * mult - round(rate * mult)) < 1e-9:
            return int(round(rate * mult)), -mult
    raise ValueError(f"cannot express sample rate {rate} as SEED factor/multiplier")


def _parse_record(buf: bytes, off: int) -> Tuple[dict, int]:
    """One data record at `off`; returns (record dict, record length)."""
    hdr = buf[off:off + 48]
    if len(hdr) < 48:
        raise ValueError("truncated record header")
    if hdr[6:7] not in (b'D', b'R', b'Q', b'M'):
        raise ValueError(f"not a SEED data record at offset {off} (quality byte {hdr[6:7]!r})")
    # byte order from the year
    year_be = struct.unpack('>H', hdr[20:22])[0]
    order = '>' if 1900 <= year_be <= 2100 else '<'
    sta = hdr[8:13].decode('ascii', 'replace').strip()
    loc = hdr[13:15].decode('ascii', 'replace').strip()
    cha = hdr[15:18].decode('ascii', 'replace').strip()
    net = hdr[18:20].decode('ascii', 'replace').strip()
    year, doy, hh, mm, ss, _, frac = struct.unpack(order + 'HHBBBBH', hdr[20:30])
    nsamp, fac, mult = struct.unpack(order + 'Hhh', hdr[30:36])
    act, io_f, dq, nblk = hdr[36], hdr[37], hdr[38], hdr[39]
    tcorr, doff, boff = struct.unpack(order + 'iHH', hdr[40:48])
    enc = None
    reclen = None
    usec = 0
    p = boff
    for _ in range(nblk):
        if p == 0 or p + 4 > len(buf) - off:
            break
        btype, nxt = struct.unpack(order + 'HH', buf[off + p:off + p + 4])
        if btype == 1000:
            enc, word_order, rl = struct.unpack('BBB', buf[off + p + 4:off + p + 7])
            reclen = 1 << rl
            order = '>' if word_order == 1 else '<'
        elif btype == 1001:
            usec = struct.unpack('b', buf[off + p + 5:off + p + 6])[0]
        if nxt == 0:
            break
        p = nxt
    if reclen is None:
        # no blockette 1000: find the next record by scanning for the next header (4096 default)
        reclen = 4096
        for cand in (256, 512, 1024, 2048, 4096, 8192):
            nxt = off + cand
            if nxt >= len(buf) or (buf[nxt + 6:nxt + 7] in (b'D', b'R', b'Q', b'M') and buf[nxt:nxt + 6].isdigit()):
                reclen = cand
                break
    t0 = _btime_to_epoch(year, doy, hh, mm, ss, frac)
    if not (act & 0x02):                  # time correction not yet applied
        t0 += tcorr / 10000.0
    t0 += usec / 1e6
    rate = _sample_rate(fac, mult)
    data_buf = buf[off + doff:off + reclen]
    enc_name = _ENCODINGS.get(enc, f'UNKNOWN_{enc}') if enc is not None else 'STEIM1'
    if nsamp == 0:
        data = np.zeros(0, dtype=np.int32)
    elif enc_name == 'STEIM1':
        data = steim1_decode(data_buf, nsamp, order)
    elif enc_name == 'STEIM2':
        data = steim2_decode(data_buf, nsamp, order)
    elif enc_name == 'INT32':
        data = np.frombuffer(data_buf[:4 * nsamp], dtype=order + 'i4').astype(np.int32)
    elif enc_name == 'INT16':
        data = np.frombuffer(data_buf[:2 * nsamp], dtype=order + 'i2').astype(np.int32)
    elif enc_name == 'FLOAT32':
        data = np.frombuffer(data_buf[:4 * nsamp], dtype=order + 'f4').astype(np.float64)
    elif enc_name == 'FLOAT64':
        data = np.frombuffer(data_buf[:8 * nsamp], dtype=order + 'f8').astype(np.float64)
    else:
        raise ValueError(f"miniSEED encoding {enc} ({enc_name}) is not supported")
    if len(data) != nsamp:
        raise ValueError(f"record claims {nsamp} samples, decoded {len(data)}")
    return {'network': net, 'station': sta, 'location': loc, 'channel': cha, 'starttime': t0, 'sample_rate': rate,
            'data': data, 'encoding': enc_name, 'reclen': reclen, 'quality': hdr[6:7].decode()}, reclen


def read_mseed(src: Union[str, bytes, io.BufferedIOBase], gap_tolerance_samples: float = 0.5) -> List[Trace]:
    """Read a miniSEED file (path, bytes or file object) into continuous traces.
    Records of the same channel that follow within half a sample are joined; a
    larger gap or an overlap starts a new trace and is written in `gaps`."""
    if isinstance(src, (bytes, bytearray)):
        buf, name = bytes(src), '<bytes>'
    elif hasattr(src, 'read'):
        buf, name = src.read(), getattr(src, 'name', '<stream>')
    else:
        with open(src, 'rb') as f:
            buf = f.read()
        name = os.fspath(src)
    recs: List[dict] = []
    off = 0
    while off + 48 <= len(buf):
        if buf[off:off + 48] == b'\x00' * 48:
            off += 64
            continue
        rec, rl = _parse_record(buf, off)
        recs.append(rec)
        off += rl
    traces: List[Trace] = []
    by_id: Dict[str, List[dict]] = {}
    for r in recs:
        by_id.setdefault(f"{r['network']}.{r['station']}.{r['location']}.{r['channel']}", []).append(r)
    for cid, rs in by_id.items():
        rs.sort(key=lambda r: r['starttime'])
        cur: Optional[Trace] = None
        for r in rs:
            if r['sample_rate'] <= 0 or r['data'].size == 0:
                continue
            if cur is not None and abs(cur.sample_rate - r['sample_rate']) < 1e-9:
                expected = cur.endtime + 1.0 / cur.sample_rate
                delta = (r['starttime'] - expected) * cur.sample_rate
                if abs(delta) <= gap_tolerance_samples:
                    cur.data = np.concatenate([cur.data, r['data'].astype(cur.data.dtype, copy=False)])
                    continue
                cur.gaps.append({'at': iso(expected), 'gap_s': round(float(r['starttime'] - expected), 6),
                                 'kind': 'gap' if delta > 0 else 'overlap'})
            cur = Trace(r['network'], r['station'], r['location'], r['channel'], r['starttime'], r['sample_rate'],
                        r['data'].copy(), source=name, encoding=r['encoding'])
            traces.append(cur)
    # the gaps are a property of the channel: every trace of a channel lists the channel's splits
    for cid in by_id:
        ts = [t for t in traces if t.id == cid]
        allgaps = [g for t in ts for g in t.gaps]
        for t in ts:
            t.gaps = allgaps
    return traces


def write_mseed(traces: Sequence[Trace], path: str, encoding: str = 'STEIM1', reclen: int = 4096) -> str:
    """Write traces as SEED 2.4 data records with a blockette 1000 (big-endian). STEIM1 or INT32 for
    integer data; FLOAT32/FLOAT64 for float data. A writer exists so the reader can be proven on
    records this program made - and so a recorder's CSV can be archived in the format the archives use."""
    enc_code = {'STEIM1': 10, 'STEIM2': 11, 'INT32': 3, 'INT16': 1, 'FLOAT32': 4, 'FLOAT64': 5}[encoding]
    rl_log = int(round(np.log2(reclen)))
    if 1 << rl_log != reclen or reclen < 256:
        raise ValueError("record length must be a power of two >= 256")
    seq = 1
    with open(path, 'wb') as f:
        for tr in traces:
            fac, mult = _rate_to_factor(tr.sample_rate)
            data = np.asarray(tr.data)
            if encoding in ('STEIM1', 'STEIM2', 'INT32', 'INT16'):
                data = np.round(data).astype(np.int64)
            i = 0
            prev = None
            while i < len(data):
                t0 = tr.starttime + i / tr.sample_rate
                payload_room = reclen - 64
                if encoding in ('STEIM1', 'STEIM2'):
                    enc_fn = steim1_encode if encoding == 'STEIM1' else steim2_encode
                    body, n = enc_fn(data[i:], payload_room // 64, '>', prev)
                    body = body.ljust(payload_room, b'\x00')
                elif encoding == 'INT32':
                    n = min(payload_room // 4, len(data) - i)
                    body = np.asarray(data[i:i + n], dtype='>i4').tobytes().ljust(payload_room, b'\x00')
                elif encoding == 'INT16':
                    n = min(payload_room // 2, len(data) - i)
                    body = np.asarray(data[i:i + n], dtype='>i2').tobytes().ljust(payload_room, b'\x00')
                elif encoding == 'FLOAT32':
                    n = min(payload_room // 4, len(data) - i)
                    body = np.asarray(data[i:i + n], dtype='>f4').tobytes().ljust(payload_room, b'\x00')
                else:
                    n = min(payload_room // 8, len(data) - i)
                    body = np.asarray(data[i:i + n], dtype='>f8').tobytes().ljust(payload_room, b'\x00')
                if n == 0:
                    raise ValueError("no sample fits in a record")
                year, doy, hh, mm, ss, frac = _epoch_to_btime(t0)
                hdr = (f"{seq % 1000000:06d}".encode() + b'D' + b' '
                       + tr.station[:5].ljust(5).encode() + tr.location[:2].ljust(2).encode() + tr.channel[:3].ljust(3).encode() + tr.network[:2].ljust(2).encode()
                       + struct.pack('>HHBBBBH', year, doy, hh, mm, ss, 0, frac)
                       + struct.pack('>Hhh', n, fac, mult)
                       + struct.pack('BBBB', 0, 0, 0, 1)
                       + struct.pack('>iHH', 0, 64, 48))
                b1000 = struct.pack('>HHBBBB', 1000, 0, enc_code, 1, rl_log, 0)
                rec = (hdr + b1000).ljust(64, b'\x00') + body
                assert len(rec) == reclen
                f.write(rec)
                prev = int(data[i + n - 1]) if encoding in ('STEIM1', 'STEIM2') else None
                i += n
                seq += 1
    return path


# ---------------------------------------------------------------------------
# SAC (binary, 632-byte header)
# ---------------------------------------------------------------------------
_SAC_K = ['kstnm', 'kevnm', 'khole', 'ko', 'ka', 'kt0', 'kt1', 'kt2', 'kt3', 'kt4', 'kt5', 'kt6', 'kt7', 'kt8', 'kt9', 'kf',
          'kuser0', 'kuser1', 'kuser2', 'kcmpnm', 'knetwk', 'kdatrd', 'kinst']


def read_sac(path: str) -> Trace:
    with open(path, 'rb') as f:
        buf = f.read()
    if len(buf) < 632:
        raise ValueError("not a SAC file (shorter than its header)")
    order = '<'
    nvhdr = struct.unpack('<i', buf[304:308])[0]
    if nvhdr not in (6, 7):
        order = '>'
        nvhdr = struct.unpack('>i', buf[304:308])[0]
        if nvhdr not in (6, 7):
            raise ValueError("not a SAC file (header version is neither 6 nor 7 in either byte order)")
    fl = np.frombuffer(buf[:280], dtype=order + 'f4')
    it = np.frombuffer(buf[280:440], dtype=order + 'i4')
    ks = buf[440:632]
    strs = {}
    p = 0
    for name in _SAC_K:
        w = 16 if name == 'kevnm' else 8
        strs[name] = ks[p:p + w].decode('ascii', 'replace').strip().rstrip('\x00').strip()
        p += w
    delta, b = float(fl[0]), float(fl[5])
    npts = int(it[9])
    nzyear, nzjday, nzhour, nzmin, nzsec, nzmsec = [int(v) for v in it[0:6]]
    data = np.frombuffer(buf[632:632 + 4 * npts], dtype=order + 'f4').astype(np.float64)
    if len(data) != npts:
        raise ValueError(f"SAC header says {npts} points, file holds {len(data)}")
    ref = _dt.datetime(nzyear, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=nzjday - 1, hours=nzhour, minutes=nzmin, seconds=nzsec, milliseconds=nzmsec)
    t0 = ref.timestamp() + b
    clean = lambda s: '' if s in ('-12345', '') else s
    rate = 1.0 / delta
    if abs(rate - round(rate)) < 1e-4 * rate:      # delta is a float32 in the header: 0.01 reads back as 100.0000022 Hz
        rate = float(round(rate))
    tr = Trace(clean(strs['knetwk']), clean(strs['kstnm']), clean(strs['khole']), clean(strs['kcmpnm']), t0, rate, data, source=path, encoding='SAC')
    tr.sac = {'stla': float(fl[31]), 'stlo': float(fl[32]), 'stel': float(fl[33]), 'nvhdr': nvhdr, 'byte_order': order}   # type: ignore[attr-defined]
    return tr


def write_sac(tr: Trace, path: str, stla: float = -12345.0, stlo: float = -12345.0) -> str:
    fl = np.full(70, -12345.0, dtype='<f4')
    it = np.full(40, -12345, dtype='<i4')
    fl[0] = 1.0 / tr.sample_rate
    fl[5] = 0.0
    fl[6] = (tr.npts - 1) / tr.sample_rate
    fl[31], fl[32] = stla, stlo
    d = _EPOCH + _dt.timedelta(seconds=float(tr.starttime))
    it[0:6] = [d.year, d.timetuple().tm_yday, d.hour, d.minute, d.second, int(round(d.microsecond / 1000.0))]
    it[6] = 6            # nvhdr
    it[9] = tr.npts
    it[15] = 1           # iftype = ITIME
    it[35] = 1           # leven = TRUE
    ks = bytearray()
    for name in _SAC_K:
        w = 16 if name == 'kevnm' else 8
        val = {'kstnm': tr.station, 'kcmpnm': tr.channel, 'knetwk': tr.network, 'khole': tr.location}.get(name, '-12345')
        ks += (val or '-12345')[:w].ljust(w).encode('ascii', 'replace')
    with open(path, 'wb') as f:
        f.write(fl.tobytes()); f.write(it.tobytes()); f.write(bytes(ks))
        f.write(np.asarray(tr.data, dtype='<f4').tobytes())
    return path


def read_any(path: str) -> List[Trace]:
    """miniSEED or SAC, decided by content, not by the file name."""
    with open(path, 'rb') as f:
        head = f.read(632)
    if len(head) >= 48 and head[6:7] in (b'D', b'R', b'Q', b'M') and head[:6].strip(b' ').isdigit():
        return read_mseed(path)
    if len(head) >= 632:
        for order in ('<', '>'):
            if struct.unpack(order + 'i', head[304:308])[0] in (6, 7):
                return [read_sac(path)]
    raise ValueError(f"{path}: neither a miniSEED record nor a SAC file by its content")


# ---------------------------------------------------------------------------
# FDSN web services (dataselect, station) - any data centre that speaks them
# ---------------------------------------------------------------------------
FDSN_CENTRES = {
    'iris': 'https://service.iris.edu',             # EarthScope / IRIS DMC - the global archive
    'texnet': 'https://rtserve.beg.utexas.edu',     # TexNet (network TX), UT Bureau of Economic Geology
}


def fdsn_url(base: str, service: str, **params) -> str:
    base = FDSN_CENTRES.get(base.lower(), base).rstrip('/')
    q = {k: v for k, v in params.items() if v not in (None, '')}
    return f"{base}/fdsnws/{service}/1/query?" + urlencode(q)


def fdsn_fetch(base: str, network: str, station: str, channel: str, start, end, out: str, location: str = '*', timeout: float = 120.0) -> dict:
    """Download miniSEED from an FDSN dataselect service to `out`. The station's instrument response
    is NOT applied (the records stay in counts), and the request is written next to the file as .request.json."""
    import json
    import urllib.request
    t0, t1 = parse_time(start), parse_time(end)
    url = fdsn_url(base, 'dataselect', network=network, station=station, location=location or '*', channel=channel,
                   starttime=iso(t0, 6)[:-1], endtime=iso(t1, 6)[:-1], format='miniseed', nodata='404')
    req = urllib.request.Request(url, headers={'User-Agent': 'gea-program seismic ingest'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(out, 'wb') as f:
        f.write(data)
    rec = {'url': url, 'bytes': len(data), 'fetched_utc': iso(_dt.datetime.now(_dt.timezone.utc).timestamp(), 0), 'file': os.path.basename(out),
           'unit': 'counts (instrument response not removed)'}
    with open(out + '.request.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    return rec


def fdsn_stations(base: str, network: str, station: str = '*', channel: str = '*', start=None, end=None,
                  minlat=None, maxlat=None, minlon=None, maxlon=None, lat=None, lon=None, maxradius_deg=None, timeout: float = 60.0) -> List[dict]:
    """Station list (text format) with coordinates - the station's position the detectability test needs."""
    import urllib.request
    url = fdsn_url(base, 'station', network=network, station=station, channel=channel, level='station', format='text',
                   starttime=iso(parse_time(start), 0)[:-1] if start else None, endtime=iso(parse_time(end), 0)[:-1] if end else None,
                   minlatitude=minlat, maxlatitude=maxlat, minlongitude=minlon, maxlongitude=maxlon,
                   latitude=lat, longitude=lon, maxradius=maxradius_deg)
    req = urllib.request.Request(url, headers={'User-Agent': 'gea-program seismic ingest'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode('utf-8', 'replace')
    return parse_station_text(text)


def parse_station_text(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        p = [c.strip() for c in line.split('|')]
        if len(p) < 7:
            continue
        out.append({'network': p[0], 'station': p[1], 'latitude': float(p[2]), 'longitude': float(p[3]), 'elevation_m': float(p[4]) if p[4] else None,
                    'site': p[5], 'start': p[6], 'end': p[7] if len(p) > 7 else ''})
    return out


# ---------------------------------------------------------------------------
# spectra - Welch PSD, spectrogram, persistent lines
# ---------------------------------------------------------------------------
def demean_detrend(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n < 2:
        return x - (x.mean() if n else 0.0)
    t = np.arange(n, dtype=np.float64)
    a, b = np.polyfit(t, x, 1)
    return x - (a * t + b)


def welch_psd(x: np.ndarray, fs: float, nperseg: int = 4096, overlap: float = 0.5, window: str = 'hann') -> Tuple[np.ndarray, np.ndarray]:
    """Welch's method: one-sided PSD in (counts^2/Hz); Hann window, 50 % overlap by default."""
    x = demean_detrend(x)
    n = len(x)
    nperseg = int(min(nperseg, n))
    if nperseg < 16:
        raise ValueError("too few samples for a spectrum")
    step = max(1, int(nperseg * (1.0 - overlap)))
    win = np.hanning(nperseg) if window == 'hann' else np.ones(nperseg)
    scale = 1.0 / (fs * np.sum(win ** 2))
    acc = None
    k = 0
    for s in range(0, n - nperseg + 1, step):
        seg = x[s:s + nperseg] * win
        f = np.fft.rfft(seg)
        p = (np.abs(f) ** 2) * scale
        p[1:-1] *= 2.0
        acc = p if acc is None else acc + p
        k += 1
    freqs = np.fft.rfftfreq(nperseg, d=1.0 / fs)
    return freqs, acc / max(k, 1)


def spectrogram(tr: Trace, win_s: float = 60.0, step_s: Optional[float] = None, nperseg: Optional[int] = None) -> dict:
    """Welch PSD per window: times (window centres, epoch s), freqs, psd[t, f] (counts^2/Hz) and psd_db."""
    fs = tr.sample_rate
    nwin = int(round(win_s * fs))
    step = int(round((step_s or win_s) * fs))
    nperseg = nperseg or int(min(4096, max(256, 2 ** int(np.floor(np.log2(max(nwin // 4, 16)))))))
    x = np.asarray(tr.data, dtype=np.float64)
    times, rows = [], []
    for s in range(0, len(x) - nwin + 1, step):
        f, p = welch_psd(x[s:s + nwin], fs, nperseg=nperseg)
        rows.append(p)
        times.append(tr.starttime + (s + nwin / 2.0) / fs)
    if not rows:
        raise ValueError(f"the record ({tr.duration_s:.1f} s) is shorter than one window ({win_s:g} s)")
    psd = np.vstack(rows)
    return {'id': tr.id, 'times': np.asarray(times), 'freqs': f, 'psd': psd, 'psd_db': 10.0 * np.log10(np.maximum(psd, 1e-30)),
            'win_s': win_s, 'step_s': step_s or win_s, 'nperseg': nperseg, 'df_hz': float(f[1] - f[0]), 'unit': 'counts^2/Hz (response not removed)'}


def noise_floor_db(psd_db_row: np.ndarray, freqs: np.ndarray, width_hz: float = 2.0) -> np.ndarray:
    """A running median across frequency: the local floor a line must rise above."""
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    half = max(1, int(round(width_hz / df / 2)))
    n = len(psd_db_row)
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = np.median(psd_db_row[lo:hi])
    return out


def persistent_lines(spec: dict, band: Tuple[float, float] = (1.0, 50.0), snr_db: float = 6.0, min_fraction: float = 0.5,
                     floor_width_hz: float = 2.0, max_lines: int = 50) -> dict:
    """Frequency bins that stand `snr_db` above the local floor in at least `min_fraction` of the windows.
    A persistent line is the fingerprint of rotating machinery (a pump, a rotary table, an engine); a
    line that comes and goes with a window is weather or traffic. The list says which bins persisted,
    how far above the floor, and in what fraction of windows - it does not say what made them."""
    f = spec['freqs']
    m = (f >= band[0]) & (f <= band[1])
    pdb = spec['psd_db'][:, m]
    fb = f[m]
    excess = np.vstack([row - noise_floor_db(row, fb, floor_width_hz) for row in pdb])
    above = excess >= snr_db
    frac = above.mean(axis=0)
    # strength: the median excess in the windows where the bin is present; adjacent persistent bins are one line (window leakage)
    strength = np.array([np.median(excess[above[:, i], i]) if above[:, i].any() else 0.0 for i in range(len(fb))])
    cand = np.where(frac >= min_fraction)[0]
    lines = []
    run: List[int] = []
    for j in list(cand) + [None]:
        if j is not None and (not run or j == run[-1] + 1):
            run.append(int(j))
            continue
        if run:
            i = max(run, key=lambda q: strength[q])
            lines.append({'freq_hz': round(float(fb[i]), 4), 'median_excess_db': round(float(strength[i]), 2),
                          'persistence': round(float(frac[i]), 3), 'present_windows': int(above[:, i].sum()), 'windows': int(above.shape[0]),
                          'width_bins': len(run)})
        run = [int(j)] if j is not None else []
    lines.sort(key=lambda d: (-d['persistence'], -d['median_excess_db']))
    return {'id': spec['id'], 'band_hz': list(band), 'snr_db': snr_db, 'min_fraction': min_fraction, 'df_hz': spec['df_hz'],
            'windows': int(pdb.shape[0]), 'win_s': spec['win_s'], 'lines': lines[:max_lines],
            'basis': 'Welch PSD per window; a running median across frequency as the local floor; a line is a bin above the floor by snr_db in min_fraction of windows',
            'not_a_measurement': 'the source of any line: a persistent line is consistent with machinery, it does not name the machine or place it'}


def band_power(tr: Trace, band: Tuple[float, float], win_s: float = 60.0, step_s: Optional[float] = None) -> dict:
    """Band-limited power per window (counts^2) - the detectability test's observable."""
    spec = spectrogram(tr, win_s, step_s)
    m = (spec['freqs'] >= band[0]) & (spec['freqs'] <= band[1])
    p = spec['psd'][:, m].sum(axis=1) * spec['df_hz']
    return {'times': spec['times'], 'power': p, 'power_db': 10.0 * np.log10(np.maximum(p, 1e-30)), 'band_hz': list(band), 'win_s': win_s, 'spec': spec}


def spectrum_csv(freqs: np.ndarray, psd: np.ndarray, path: str, tr: Optional[Trace] = None) -> str:
    import csv
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['# Welch PSD', tr.id if tr else '', f'{tr.sample_rate:g} Hz' if tr else '', 'counts^2/Hz (instrument response not removed)'])
        w.writerow(['freq_hz', 'psd_counts2_per_hz', 'psd_db'])
        for fr, p in zip(freqs, psd):
            w.writerow([f'{fr:.5f}', f'{p:.6e}', f'{10 * np.log10(max(p, 1e-30)):.3f}'])
    return path
