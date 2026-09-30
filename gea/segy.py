# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""segy - Part 6 of the subsurface surveying tool: SEISMIC INGEST
SEG-Y is the industry's seismic interchange format; this reader
brings the first WAVEFIELD modality into the read-only ingestion family.

STATUS, honestly: READER_VALIDATED_BY_ROUND_TRIP; AWAITING_FIELD_SEGY.
No field SEG-Y exists in the catalogue yet, so validation is by exact
round-trip against a minimal spec-conformant writer (used ONLY for
validation - the writer never touches archives). The day a licensed field
volume lands, this reader is its door; until then no seismic claim is made.

SCOPE (SEG-Y rev 1, the working subset):
  - 3200-byte textual header (EBCDIC auto-detected and transcoded, or ASCII)
  - 400-byte binary file header: sample interval (bytes 3217-3218),
    samples/trace (3221-3222), data format code (3225-3226)
  - traces: 240-byte trace headers (inline/crossline at the rev1 standard
    byte positions 189/193, source coords at 73/77 with the scalar at 71)
    + samples in format 5 (IEEE float32 big-endian) or format 1 (IBM
    float32, converted exactly)
  - anything else REFUSES with the format code named - no guessing

READ-ONLY, as every ingest port: it never writes, never modifies, never
'fixes' a volume. Byte-swapped or nonconforming files refuse visibly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List

SUPPORTED_FORMATS = {1: 'IBM float32', 5: 'IEEE float32'}


def _ibm_to_float(b: bytes) -> float:
    """Exact IBM System/360 hexadecimal float -> Python float."""
    (u,) = struct.unpack('>I', b)
    if u == 0:
        return 0.0
    sign = -1.0 if u >> 31 else 1.0
    exponent = ((u >> 24) & 0x7F) - 64
    mantissa = (u & 0x00FFFFFF) / float(0x1000000)
    return sign * mantissa * (16.0 ** exponent)


def _float_to_ibm(x: float) -> bytes:
    """Exact inverse for the round-trip validator (writer is test-only)."""
    if x == 0.0:
        return b'\x00\x00\x00\x00'
    sign = 0x80000000 if x < 0 else 0
    x = abs(x)
    exponent = 64
    while x >= 1.0:
        x /= 16.0
        exponent += 1
    while x < 0.0625 and exponent > 0:
        x *= 16.0
        exponent -= 1
    mantissa = int(x * 0x1000000) & 0x00FFFFFF
    return struct.pack('>I', sign | (exponent << 24) | mantissa)


@dataclass
class SegyTrace:
    inline: int
    crossline: int
    source_x: float
    source_y: float
    samples: List[float] = field(default_factory=list)


@dataclass
class SegyVolume:
    textual_header: str
    encoding: str                    # 'EBCDIC' | 'ASCII'
    sample_interval_us: int
    n_samples: int
    format_code: int
    format_name: str
    traces: List[SegyTrace] = field(default_factory=list)
    status: str = 'READER_VALIDATED_BY_ROUND_TRIP; AWAITING_FIELD_SEGY'

    def summary(self) -> Dict:
        return {'n_traces': len(self.traces),
                'n_samples': self.n_samples,
                'sample_interval_us': self.sample_interval_us,
                'trace_length_ms': self.n_samples * self.sample_interval_us / 1000.0,
                'format': self.format_name, 'encoding': self.encoding,
                'status': self.status}


def read_segy(path: str) -> SegyVolume:
    """Read a SEG-Y rev1 file (see module scope). Refuses unsupported
    format codes and truncated files by name, never by guess."""
    with open(path, 'rb') as f:
        raw_text = f.read(3200)
        if len(raw_text) < 3200:
            raise ValueError('SEG-Y refused: file shorter than the 3200-byte '
                             'textual header')
        if raw_text[:1] == b'C':
            text, enc = raw_text.decode('ascii', 'replace'), 'ASCII'
        else:
            text, enc = raw_text.decode('cp037', 'replace'), 'EBCDIC'
        bin_hdr = f.read(400)
        if len(bin_hdr) < 400:
            raise ValueError('SEG-Y refused: truncated binary file header')
        (interval,) = struct.unpack('>H', bin_hdr[16:18])
        (n_samples,) = struct.unpack('>H', bin_hdr[20:22])
        (fmt,) = struct.unpack('>H', bin_hdr[24:26])
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError('SEG-Y refused: data format code %d is outside '
                             'the supported subset %s - no guessing'
                             % (fmt, sorted(SUPPORTED_FORMATS)))
        if n_samples == 0:
            raise ValueError('SEG-Y refused: binary header declares zero '
                             'samples per trace')
        vol = SegyVolume(textual_header=text, encoding=enc,
                         sample_interval_us=interval, n_samples=n_samples,
                         format_code=fmt, format_name=SUPPORTED_FORMATS[fmt])
        trace_bytes = n_samples * 4
        while True:
            th = f.read(240)
            if not th:
                break
            if len(th) < 240:
                raise ValueError('SEG-Y refused: truncated trace header at '
                                 'trace %d' % (len(vol.traces) + 1))
            data = f.read(trace_bytes)
            if len(data) < trace_bytes:
                raise ValueError('SEG-Y refused: truncated samples at trace '
                                 '%d' % (len(vol.traces) + 1))
            (scal,) = struct.unpack('>h', th[70:72])
            (sx,) = struct.unpack('>i', th[72:76])
            (sy,) = struct.unpack('>i', th[76:80])
            (il,) = struct.unpack('>i', th[188:192])
            (xl,) = struct.unpack('>i', th[192:196])
            k = (abs(scal) if scal else 1)
            factor = (1.0 / k) if scal < 0 else float(k)
            if fmt == 5:
                samples = list(struct.unpack('>%df' % n_samples, data))
            else:
                samples = [_ibm_to_float(data[i:i + 4])
                           for i in range(0, trace_bytes, 4)]
            vol.traces.append(SegyTrace(
                inline=il, crossline=xl,
                source_x=sx * factor, source_y=sy * factor,
                samples=samples))
        return vol


def write_segy_minimal(path: str, traces: List[List[float]],
                       sample_interval_us: int = 2000,
                       fmt: int = 5) -> None:
    """TEST-ONLY minimal spec-conformant writer: exists solely so the reader
    can be validated by exact round-trip in the absence of licensed field
    data. Never used on archives; never part of ingestion."""
    n_samples = len(traces[0])
    with open(path, 'wb') as f:
        header_line = ('C 1 segy round-trip validation volume - '
                       'TEST ONLY, not field data').ljust(80)
        f.write((header_line + ' ' * (3200 - 80)).encode('ascii')[:3200])
        bin_hdr = bytearray(400)
        bin_hdr[16:18] = struct.pack('>H', sample_interval_us)
        bin_hdr[20:22] = struct.pack('>H', n_samples)
        bin_hdr[24:26] = struct.pack('>H', fmt)
        f.write(bytes(bin_hdr))
        for t_i, samples in enumerate(traces):
            th = bytearray(240)
            th[70:72] = struct.pack('>h', 1)
            th[72:76] = struct.pack('>i', 1000 + t_i)
            th[76:80] = struct.pack('>i', 2000 + t_i)
            th[188:192] = struct.pack('>i', 10 + t_i)
            th[192:196] = struct.pack('>i', 20 + t_i)
            f.write(bytes(th))
            if fmt == 5:
                f.write(struct.pack('>%df' % n_samples, *samples))
            else:
                for s in samples:
                    f.write(_float_to_ibm(s))
