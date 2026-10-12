# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""dlis - the DLIS (API RP66 V1) reader: the format a well log is delivered in, read as the standard lays it out.

A LAS file is the export; the log itself arrives as DLIS, and the strata survey read only the export. RP66 V1
is a binary container: an 80-byte Storage Unit Label; Visible Records, each with a 4-byte header (length,
then FF 01); inside them Logical Record Segments - a 4-byte header (length, attributes, type) and a body, the
attributes saying whether the segment is part of an Explicitly Formatted Logical Record (the metadata: file
header, origin, channels, frames, parameters, tools...) or an Indirectly Formatted one (the frame data), and
whether a predecessor or successor segment continues it, and which trailer fields (padding, checksum,
trailing length) follow the body. An EFLR is a set of objects described by a template: component
descriptors (set, attribute, object) with the representation codes of RP66 Appendix B - FSINGL, FDOUBL, the
integers, UVARI, IDENT, ASCII, DTIME, OBNAME, OBJREF, UNITS and the rest. A FRAME object names its channels
and its index; a CHANNEL object gives each curve its representation code, units and dimension; each FDATA
record carries the frame's name, a frame number and the channel values in the frame's order.

This reader does the whole of that with no dependency and hands the survey what the LAS port hands it: a
LiveStream with the frame's index as the index and each channel as a curve with its unit. It names what it
cannot do rather than guessing: an encrypted record, a representation code it does not decode, a channel with
a dimension it does not lay out, a frame whose index is not the first channel. A writer makes small DLIS
files from channels, so the reader is proven on files this program made before it is run on a public one.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .ports import LiveStream, StreamChannel

SUL_LEN = 80
# representation codes (RP66 V1 Appendix B): code -> (name, fixed size or None)
RC = {1: ('FSHORT', 2), 2: ('FSINGL', 4), 3: ('FSING1', 8), 4: ('FSING2', 12), 5: ('ISINGL', 4), 6: ('VSINGL', 4), 7: ('FDOUBL', 8), 8: ('FDOUB1', 16),
      9: ('FDOUB2', 24), 10: ('CSINGL', 8), 11: ('CDOUBL', 16), 12: ('SSHORT', 1), 13: ('SNORM', 2), 14: ('SLONG', 4), 15: ('USHORT', 1), 16: ('UNORM', 2),
      17: ('ULONG', 4), 18: ('UVARI', None), 19: ('IDENT', None), 20: ('ASCII', None), 21: ('DTIME', 8), 22: ('ORIGIN', None), 23: ('OBNAME', None),
      24: ('OBJREF', None), 25: ('ATTREF', None), 26: ('STATUS', 1), 27: ('UNITS', None)}
EFLR_TYPES = {0: 'FILE-HEADER', 1: 'ORIGIN', 2: 'AXIS', 3: 'CHANNEL', 4: 'FRAME', 5: 'STATIC', 6: 'SCRIPT', 7: 'UPDATE', 8: 'UDI', 9: 'LONG-NAME',
              10: 'SPEC', 11: 'DICT'}


class DlisError(ValueError):
    pass


# ---- the representation codes ----------------------------------------------------------------------------------------------
class _Cursor:
    __slots__ = ('b', 'p')

    def __init__(self, b: bytes, p: int = 0):
        self.b, self.p = b, p

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.b):
            raise DlisError(f'record ends inside a value ({n} bytes needed, {len(self.b) - self.p} left)')
        out = self.b[self.p:self.p + n]; self.p += n
        return out

    def uvari(self) -> int:
        b0 = self.take(1)[0]
        if b0 < 0x80:
            return b0
        if b0 < 0xC0:
            return ((b0 & 0x3F) << 8) | self.take(1)[0]
        rest = self.take(3)
        return ((b0 & 0x3F) << 24) | (rest[0] << 16) | (rest[1] << 8) | rest[2]

    def ident(self) -> str:
        n = self.take(1)[0]
        return self.take(n).decode('ascii', 'replace')

    def ascii(self) -> str:
        n = self.uvari()
        return self.take(n).decode('ascii', 'replace')

    def obname(self) -> Tuple[int, int, str]:
        origin = self.uvari(); copy = self.take(1)[0]; name = self.ident()
        return (origin, copy, name)

    def value(self, code: int) -> Any:
        if code == 2:
            return struct.unpack('>f', self.take(4))[0]
        if code == 7:
            return struct.unpack('>d', self.take(8))[0]
        if code == 1:                                                  # FSHORT: 16-bit, 12-bit mantissa / 4-bit exponent (RP66 B.1)
            v = struct.unpack('>H', self.take(2))[0]
            m = v >> 4
            if m & 0x800:
                m -= 0x1000
            return float(m) * 2.0 ** (-(v & 0x0F))
        if code == 5:                                                  # ISINGL: IBM single
            raw = self.take(4)
            s = -1.0 if raw[0] & 0x80 else 1.0
            e = (raw[0] & 0x7F) - 64
            m = int.from_bytes(raw[1:], 'big') / float(1 << 24)
            return s * m * 16.0 ** e
        if code == 6:                                                  # VSINGL: VAX single
            raw = self.take(4)
            w = struct.unpack('<HH', raw)
            bits = (w[0] << 16) | w[1]
            sign = -1.0 if bits & 0x80000000 else 1.0
            e = (bits >> 23) & 0xFF
            f = bits & 0x7FFFFF
            return 0.0 if e == 0 else sign * (0.5 + f / float(1 << 24)) * 2.0 ** (e - 128)
        if code == 12:
            return struct.unpack('>b', self.take(1))[0]
        if code == 13:
            return struct.unpack('>h', self.take(2))[0]
        if code == 14:
            return struct.unpack('>i', self.take(4))[0]
        if code in (15, 26):
            return self.take(1)[0]
        if code == 16:
            return struct.unpack('>H', self.take(2))[0]
        if code == 17:
            return struct.unpack('>I', self.take(4))[0]
        if code in (18, 22):
            return self.uvari()
        if code in (19, 27):
            return self.ident()
        if code == 20:
            return self.ascii()
        if code == 21:
            b = self.take(8)
            y = 1900 + b[0]; tz = b[1] >> 4; mo = b[1] & 0x0F; d, h, mi, s = b[2], b[3], b[4], b[5]; ms = struct.unpack('>H', b[6:8])[0]
            try:
                return datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone.utc).isoformat() + ('' if tz == 0 else f' (tz code {tz})')
            except ValueError:
                return f'{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}.{ms:03d}'
        if code == 23:
            return self.obname()
        if code == 24:
            t = self.ident(); return (t, self.obname())
        if code == 25:
            t = self.ident(); o = self.obname(); lab = self.ident(); return (t, o, lab)
        if code in (3, 4, 8, 9, 10, 11):
            n = RC[code][1]; raw = self.take(n)
            if code in (3, 4):
                return struct.unpack('>f', raw[:4])[0]
            if code in (8, 9):
                return struct.unpack('>d', raw[:8])[0]
            if code == 10:
                return complex(*struct.unpack('>ff', raw))
            return complex(*struct.unpack('>dd', raw))
        raise DlisError(f'representation code {code} is not one this reader decodes')


# ---- the physical and logical layout ----------------------------------------------------------------------------------------------
@dataclass
class Segment:
    attrs: int
    type: int
    body: bytes

    @property
    def eflr(self) -> bool:
        return bool(self.attrs & 0x80)


@dataclass
class LogicalRecord:
    eflr: bool
    type: int
    body: bytes
    encrypted: bool = False


def read_sul(data: bytes) -> dict:
    if len(data) < SUL_LEN:
        raise DlisError('file shorter than the 80-byte storage unit label')
    sul = data[:SUL_LEN]
    seq, ver, struct_, maxlen, setid = sul[0:4], sul[4:9], sul[9:15], sul[15:20], sul[20:80]
    if ver[:2] != b'V1' or struct_.strip() != b'RECORD':
        raise DlisError(f'not a DLIS V1 storage unit label (version {ver!r}, structure {struct_!r}) - a LAS or DLIS V2 file is not this reader\'s')
    return {'sequence': seq.decode('ascii', 'replace').strip(), 'version': ver.decode('ascii', 'replace'), 'structure': struct_.decode('ascii', 'replace').strip(),
            'max_record_length': int(maxlen.decode('ascii', 'replace').strip() or 0), 'storage_set_id': setid.decode('ascii', 'replace').strip()}


def segments(data: bytes) -> List[Segment]:
    """Every logical record segment of every visible record, in order."""
    out: List[Segment] = []
    p = SUL_LEN
    n = len(data)
    while p + 4 <= n:
        vlen = struct.unpack('>H', data[p:p + 2])[0]
        if data[p + 2:p + 4] != b'\xff\x01':
            raise DlisError(f'visible record at byte {p}: format version bytes {data[p + 2:p + 4].hex()} are not FF 01')
        if vlen < 4 or p + vlen > n:
            raise DlisError(f'visible record at byte {p}: length {vlen} runs past the file ({n - p} bytes left)')
        q = p + 4
        end = p + vlen
        while q + 4 <= end:
            slen = struct.unpack('>H', data[q:q + 2])[0]
            attrs, ltype = data[q + 2], data[q + 3]
            if slen < 4 or q + slen > end:
                raise DlisError(f'logical record segment at byte {q}: length {slen} runs past its visible record')
            body = data[q + 4:q + slen]
            if attrs & 0x10:                          # encrypted: kept as a segment, its body untouched, so the record can be named and skipped
                out.append(Segment(attrs, ltype, body))
                q += slen
                continue
            if attrs & 0x02:                          # trailing length
                body = body[:-2]
            if attrs & 0x04:                          # checksum
                body = body[:-2]
            if attrs & 0x01 and body:                 # padding: the last byte is the pad count, itself included
                pad = body[-1]
                body = body[:-pad] if pad <= len(body) else b''
            out.append(Segment(attrs, ltype, body))
            q += slen
        p = end
    return out


def logical_records(segs: List[Segment]) -> List[LogicalRecord]:
    out: List[LogicalRecord] = []
    cur: Optional[LogicalRecord] = None
    for s in segs:
        enc = bool(s.attrs & 0x10)
        if not (s.attrs & 0x40):                      # no predecessor: a new record
            cur = LogicalRecord(s.eflr, s.type, s.body, enc)
        elif cur is not None:
            cur.body += s.body
            cur.encrypted = cur.encrypted or enc
        else:
            cur = LogicalRecord(s.eflr, s.type, s.body, enc)
        if not (s.attrs & 0x20):                      # no successor: the record is complete
            out.append(cur); cur = None
    if cur is not None:
        out.append(cur)
    return out


# ---- EFLR: sets, templates, objects -------------------------------------------------------------------------------------------
@dataclass
class Attribute:
    label: str = ''
    count: int = 1
    code: int = 19
    units: str = ''
    value: Any = None


@dataclass
class EflrSet:
    type: str
    name: str
    template: List[Attribute] = field(default_factory=list)
    objects: Dict[Tuple[int, int, str], Dict[str, Attribute]] = field(default_factory=dict)


def parse_eflr(body: bytes) -> EflrSet:
    c = _Cursor(body)
    desc = c.take(1)[0]
    role, fmt = desc >> 5, desc & 0x1F
    if role not in (5, 6, 7):
        raise DlisError(f'EFLR does not start with a set component (role {role})')
    st = EflrSet(type=c.ident() if fmt & 0x10 else '', name=c.ident() if fmt & 0x08 else '')
    current_obj: Optional[Dict[str, Attribute]] = None
    ti = 0
    while c.p < len(c.b):
        desc = c.take(1)[0]
        role, fmt = desc >> 5, desc & 0x1F
        if role in (1, 2) and current_obj is None:                     # template attribute (ATTRIB / INVATR)
            a = Attribute()
            if fmt & 0x10:
                a.label = c.ident()
            if fmt & 0x08:
                a.count = c.uvari()
            if fmt & 0x04:
                a.code = c.take(1)[0]
            if fmt & 0x02:
                a.units = c.ident()
            if fmt & 0x01:
                a.value = [c.value(a.code) for _ in range(a.count)]
            st.template.append(a)
        elif role == 0 and current_obj is None:                        # absent attribute in the template
            st.template.append(Attribute(label=''))
        elif role == 3:                                                # object
            name = c.obname() if fmt & 0x10 else (0, 0, '')
            current_obj = {}
            st.objects[name] = current_obj
            ti = 0
        elif role in (0, 1, 2) and current_obj is not None:            # the object's attributes, in template order
            if ti >= len(st.template):
                raise DlisError(f'object in set {st.type} has more attributes than its template ({len(st.template)})')
            t = st.template[ti]; ti += 1
            a = Attribute(label=t.label, count=t.count, code=t.code, units=t.units, value=t.value)
            if role == 0:
                a.value = None
            else:
                if fmt & 0x10:
                    a.label = c.ident()
                if fmt & 0x08:
                    a.count = c.uvari()
                if fmt & 0x04:
                    a.code = c.take(1)[0]
                if fmt & 0x02:
                    a.units = c.ident()
                if fmt & 0x01:
                    a.value = [c.value(a.code) for _ in range(a.count)]
            current_obj[a.label] = a
        else:
            raise DlisError(f'component role {role} where it cannot be (byte {c.p - 1} of the set {st.type})')
    return st


# ---- the file ------------------------------------------------------------------------------------------------------------------
@dataclass
class Channel:
    name: Tuple[int, int, str]
    long_name: str
    code: int
    units: str
    dimension: List[int]
    properties: List[str]


@dataclass
class Frame:
    name: Tuple[int, int, str]
    channels: List[Tuple[int, int, str]]
    index_type: str
    direction: str
    spacing: Optional[float]
    spacing_units: str
    index_min: Optional[float]
    index_max: Optional[float]
    description: str


def _text(v) -> str:
    """An attribute's text: an ASCII/IDENT value, or the name part of an OBNAME / OBJREF reference to a LONG-NAME object."""
    if not v:
        return ''
    x = v[0]
    if isinstance(x, tuple):
        return str(x[-1][2] if len(x) == 2 and isinstance(x[1], tuple) else x[2] if len(x) == 3 and isinstance(x[2], str) else x)
    return str(x)


def _val(obj: Dict[str, Attribute], label: str, default=None):
    a = obj.get(label)
    if a is None or a.value is None or len(a.value) == 0:
        return default
    return a.value


class DlisFile:
    def __init__(self, data: bytes, name: str = 'dlis'):
        self.name = name
        self.sul = read_sul(data)
        self.records = logical_records(segments(data))
        self.sets: List[EflrSet] = []
        self.unparsed: List[str] = []
        self.channels: Dict[Tuple[int, int, str], Channel] = {}
        self.frames: Dict[Tuple[int, int, str], Frame] = {}
        self.origin: Dict[str, Any] = {}
        self.file_header: Dict[str, Any] = {}
        self.parameters: Dict[str, Any] = {}
        self.encrypted: Dict[str, int] = {}
        for r in self.records:
            if r.encrypted:
                k = (EFLR_TYPES.get(r.type, 'type ' + str(r.type)) if r.eflr else 'IFLR type ' + str(r.type))
                self.encrypted[k] = self.encrypted.get(k, 0) + 1
                continue
            if not r.eflr:
                continue
            try:
                st = parse_eflr(r.body)
            except DlisError as e:
                self.unparsed.append(f'{EFLR_TYPES.get(r.type, "type " + str(r.type))}: {e}')
                continue
            self.sets.append(st)
            if st.type == 'CHANNEL':
                for nm, obj in st.objects.items():
                    self.channels[nm] = Channel(nm, _text(_val(obj, 'LONG-NAME')),
                                                code=int((_val(obj, 'REPRESENTATION-CODE', [2]) or [2])[0]), units=str((_val(obj, 'UNITS', ['']) or [''])[0]),
                                                dimension=[int(x) for x in (_val(obj, 'DIMENSION', [1]) or [1])], properties=[str(x) for x in (_val(obj, 'PROPERTIES', []) or [])])
            elif st.type == 'FRAME':
                for nm, obj in st.objects.items():
                    sp = _val(obj, 'SPACING'); spu = obj['SPACING'].units if 'SPACING' in obj else ''
                    self.frames[nm] = Frame(nm, [tuple(x) for x in (_val(obj, 'CHANNELS', []) or [])], str((_val(obj, 'INDEX-TYPE', ['']) or [''])[0]),
                                            str((_val(obj, 'DIRECTION', ['']) or [''])[0]), (float(sp[0]) if sp else None), spu,
                                            (float(_val(obj, 'INDEX-MIN')[0]) if _val(obj, 'INDEX-MIN') else None),
                                            (float(_val(obj, 'INDEX-MAX')[0]) if _val(obj, 'INDEX-MAX') else None), str((_val(obj, 'DESCRIPTION', ['']) or [''])[0]))
            elif st.type == 'ORIGIN':
                for nm, obj in st.objects.items():
                    self.origin = {k: (a.value[0] if a.value and len(a.value) == 1 else a.value) for k, a in obj.items() if a.value is not None}
                    break
            elif st.type == 'FILE-HEADER':
                for nm, obj in st.objects.items():
                    self.file_header = {k: (a.value[0] if a.value and len(a.value) == 1 else a.value) for k, a in obj.items() if a.value is not None}
                    break
            elif st.type == 'PARAMETER':
                for nm, obj in st.objects.items():
                    v = _val(obj, 'VALUES')
                    if v is not None:
                        self.parameters[nm[2]] = {'values': v, 'units': obj['VALUES'].units if 'VALUES' in obj else ''}

    def describe(self) -> dict:
        return {'storage_unit': self.sul, 'file_header': {k: str(v) for k, v in self.file_header.items()},
                'origin': {k: str(v) for k, v in self.origin.items()}, 'n_records': len(self.records),
                'eflr_sets': [(s.type, s.name, len(s.objects)) for s in self.sets], 'unparsed': self.unparsed, 'encrypted_records': dict(self.encrypted),
                'channels': [{'name': c.name[2], 'origin': c.name[0], 'copy': c.name[1], 'long_name': c.long_name, 'code': RC.get(c.code, ('?',))[0], 'units': c.units,
                              'dimension': c.dimension} for c in self.channels.values()],
                'frames': [{'name': f.name[2], 'channels': [c[2] for c in f.channels], 'index_type': f.index_type, 'direction': f.direction, 'spacing': f.spacing,
                            'spacing_units': f.spacing_units, 'index_min': f.index_min, 'index_max': f.index_max} for f in self.frames.values()],
                'parameters': {k: {'values': [str(x) for x in v['values']][:8], 'units': v['units']} for k, v in list(self.parameters.items())[:40]}}

    def frame_data(self, frame_name: Optional[str] = None) -> Tuple[Frame, np.ndarray, List[Channel], List[str]]:
        """The FDATA records of one frame as a (rows x channels) array of floats, with the channels laid out and the
        notes on what was not: a multi-dimensional channel is left out and named, a non-numeric one likewise."""
        if not self.frames:
            raise DlisError('no FRAME object in the file (nothing names the channels of the data records)')
        if frame_name is None:
            if len(self.frames) > 1:
                raise DlisError(f'the file holds {len(self.frames)} frames ({", ".join(f.name[2] for f in self.frames.values())}); name one with frame=')
            frame = next(iter(self.frames.values()))
        else:
            frame = next((f for f in self.frames.values() if f.name[2] == frame_name), None)
            if frame is None:
                raise DlisError(f'no frame {frame_name!r} (frames: {", ".join(f.name[2] for f in self.frames.values())})')
        chans: List[Channel] = []
        notes: List[str] = []
        for nm in frame.channels:
            c = self.channels.get(nm)
            if c is None:
                raise DlisError(f'frame {frame.name[2]} names channel {nm[2]} which has no CHANNEL object')
            chans.append(c)
        layout: List[Tuple[Channel, bool]] = []       # (channel, numeric scalar)
        for c in chans:
            n = 1
            for d in c.dimension:
                n *= int(d)
            numeric = c.code in (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 26)
            if n != 1:
                notes.append(f'channel {c.name[2]}: dimension {c.dimension} ({n} values per sample) is read past, not laid out')
            elif not numeric:
                notes.append(f'channel {c.name[2]}: representation code {RC.get(c.code, ("?",))[0]} is not numeric; read past')
            layout.append((c, n == 1 and numeric))
        rows: List[List[float]] = []
        frame_nums: List[int] = []
        for r in self.records:
            if r.eflr or r.type != 0 or r.encrypted:
                continue
            c = _Cursor(r.body)
            nm = c.obname()
            if nm != frame.name:
                continue
            frame_nums.append(c.uvari())
            row: List[float] = []
            for ch, scalar in layout:
                n = 1
                for d in ch.dimension:
                    n *= int(d)
                vals = [c.value(ch.code) for _ in range(n)]
                if scalar:
                    v = vals[0]
                    row.append(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float('nan'))
            rows.append(row)
        kept = [ch for ch, scalar in layout if scalar]
        arr = np.array(rows, dtype=float) if rows else np.zeros((0, len(kept)))
        return frame, arr, kept, notes


def read(path_or_bytes, frame: Optional[str] = None, null_values: Tuple[float, ...] = (-999.25, -999.0, -9999.0)) -> LiveStream:
    """The frame's channels as a LiveStream: the first channel is the index (depth or time, as the frame says); the
    absent-value sentinels become NaN. Meta carries the file header, origin, frame and what was not laid out."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data, name = bytes(path_or_bytes), 'dlis'
    else:
        p = Path(path_or_bytes)
        data, name = p.read_bytes(), p.stem
    f = DlisFile(data, name)
    fr, arr, chans, notes = f.frame_data(frame)
    if not chans:
        raise DlisError(f'frame {fr.name[2]} has no scalar numeric channel to read')
    if arr.shape[0] == 0:
        raise DlisError(f'frame {fr.name[2]} has no data records')
    for nv in null_values:
        arr[arr == nv] = np.nan
    index = arr[:, 0]
    channels = {c.name[2]: StreamChannel(name=c.name[2], unit=c.units, values=arr[:, j]) for j, c in enumerate(chans) if j > 0}
    meta = {'frame': fr.name[2], 'index_channel': chans[0].name[2], 'index_units': chans[0].units, 'index_type': fr.index_type, 'direction': fr.direction,
            'spacing': '' if fr.spacing is None else f'{fr.spacing:g} {fr.spacing_units}',
            'notes': '; '.join(notes + ([f"encrypted records skipped: {', '.join(f'{k} x{n}' for k, n in f.encrypted.items())}"] if f.encrypted else []) + f.unparsed),
            'storage_set': f.sul['storage_set_id'], 'file_id': str(f.file_header.get('ID', '')), 'sequence': str(f.file_header.get('SEQUENCE-NUMBER', ''))}
    for k in ('WELL-NAME', 'FIELD-NAME', 'COMPANY', 'PRODUCER-NAME', 'CREATION-TIME', 'FILE-ID', 'PRODUCT', 'VERSION', 'WELL-ID'):
        if k in f.origin:
            meta[k] = str(f.origin[k])
    ik = 'depth' if 'DEPTH' in (fr.index_type or '').upper() or 'DEPTH' in chans[0].name[2].upper() else ('time_s' if 'TIME' in (fr.index_type or '').upper() else 'depth')
    return LiveStream(name=name, source_format='dlis', index_kind=ik, index=index, channels=channels, meta=meta)


# ---- a writer, so the reader is proven on files this program made --------------------------------------------------------------
def _uvari(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return struct.pack('>H', 0x8000 | n)
    return struct.pack('>I', 0xC0000000 | n)


def _ident(s: str) -> bytes:
    b = s.encode('ascii')
    return bytes([len(b)]) + b


def _ascii(s: str) -> bytes:
    b = s.encode('ascii')
    return _uvari(len(b)) + b


def _obname(origin: int, copy: int, name: str) -> bytes:
    return _uvari(origin) + bytes([copy]) + _ident(name)


def _encode(code: int, v: Any) -> bytes:
    if code == 2:
        return struct.pack('>f', float(v))
    if code == 7:
        return struct.pack('>d', float(v))
    if code == 13:
        return struct.pack('>h', int(v))
    if code == 14:
        return struct.pack('>i', int(v))
    if code == 15:
        return bytes([int(v)])
    if code == 16:
        return struct.pack('>H', int(v))
    if code == 17:
        return struct.pack('>I', int(v))
    if code in (18, 22):
        return _uvari(int(v))
    if code in (19, 27):
        return _ident(str(v))
    if code == 20:
        return _ascii(str(v))
    if code == 23:
        return _obname(*v)
    if code == 21:
        dt = v
        return bytes([dt.year - 1900, dt.month, dt.day, dt.hour, dt.minute, dt.second]) + struct.pack('>H', dt.microsecond // 1000)
    raise DlisError(f'the writer does not encode representation code {code}')


def _attr(label: str, code: int, values: List[Any], units: str = '') -> bytes:
    fmt = 0x10 | 0x08 | 0x04 | 0x01 | (0x02 if units else 0)
    out = bytes([(1 << 5) | fmt]) + _ident(label) + _uvari(len(values)) + bytes([code])
    if units:
        out += _ident(units)
    return out + b''.join(_encode(code, x) for x in values)


def _eflr(set_type: str, set_name: str, template: List[Tuple[str, int, str]], objects: List[Tuple[Tuple[int, int, str], List[Optional[List[Any]]]]]) -> bytes:
    """A set: the template (label, code, units) and the objects (name, one value list per template entry or None for absent)."""
    out = bytes([(7 << 5) | 0x10 | (0x08 if set_name else 0)]) + _ident(set_type) + (_ident(set_name) if set_name else b'')
    for label, code, units in template:
        out += bytes([(1 << 5) | 0x10 | 0x04 | (0x02 if units else 0)]) + _ident(label) + bytes([code]) + (_ident(units) if units else b'')
    for name, values in objects:
        out += bytes([(3 << 5) | 0x10]) + _obname(*name)
        for (label, code, units), v in zip(template, values):
            if v is None:
                out += bytes([0])
            else:
                fmt = 0x08 | 0x01
                out += bytes([(1 << 5) | fmt]) + _uvari(len(v)) + b''.join(_encode(code, x) for x in v)
    return out


def _segment(body: bytes, eflr: bool, ltype: int, first: bool, last: bool) -> bytes:
    attrs = (0x80 if eflr else 0) | (0 if first else 0x40) | (0 if last else 0x20)
    if (len(body) + 4) % 2:                                             # segments are an even number of bytes: pad, and say so
        body = body + b'\x01'
        attrs |= 0x01
    return struct.pack('>H', len(body) + 4) + bytes([attrs, ltype]) + body


def write(path: str, channels: List[Tuple[str, str, int, List[float]]], frame_name: str = 'FRAME1', index_type: str = 'BOREHOLE-DEPTH',
          well: str = 'GATE WELL', max_record: int = 8192, file_id: str = 'gea gate file') -> str:
    """A DLIS file from channels [(name, units, code 2|7|13|14, values)]; the first channel is the frame's index."""
    assert all(len(c[3]) == len(channels[0][3]) for c in channels), 'every channel needs the same number of samples'
    records: List[Tuple[bytes, bool, int]] = []
    records.append((_eflr('FILE-HEADER', '', [('SEQUENCE-NUMBER', 20, ''), ('ID', 20, '')], [((0, 0, '1'), [['1'], [file_id]])]), True, 0))
    records.append((_eflr('ORIGIN', '', [('FILE-ID', 20, ''), ('FILE-TYPE', 19, ''), ('PRODUCT', 19, ''), ('VERSION', 19, ''), ('WELL-NAME', 19, ''), ('FIELD-NAME', 19, ''),
                                         ('COMPANY', 19, ''), ('PRODUCER-NAME', 19, ''), ('CREATION-TIME', 21, '')],
                          [((0, 0, 'ORIGIN1'), [[file_id], ['PLAYBACK'], ['gea-program'], ['1'], [well], ['GATE'], ['GEA'], ['gea-program'], [datetime(2026, 1, 1, 0, 0, 0)]])]), True, 1))
    records.append((_eflr('CHANNEL', '', [('LONG-NAME', 20, ''), ('PROPERTIES', 19, ''), ('REPRESENTATION-CODE', 15, ''), ('UNITS', 27, ''), ('DIMENSION', 18, ''), ('ELEMENT-LIMIT', 18, '')],
                          [((0, 0, nm), [[nm + ' long name'], None, [code], [units], [1], [1]]) for nm, units, code, _ in channels]), True, 3))
    idx = channels[0][3]
    records.append((_eflr('FRAME', '', [('DESCRIPTION', 20, ''), ('CHANNELS', 23, ''), ('INDEX-TYPE', 19, ''), ('DIRECTION', 19, ''), ('SPACING', 7, channels[0][1]),
                                        ('INDEX-MIN', 7, channels[0][1]), ('INDEX-MAX', 7, channels[0][1])],
                          [((0, 0, frame_name), [['the gate frame'], [(0, 0, nm) for nm, _, _, _ in channels], [index_type], ['INCREASING' if idx[-1] >= idx[0] else 'DECREASING'],
                                                 [float(idx[1] - idx[0]) if len(idx) > 1 else 0.0], [float(min(idx))], [float(max(idx))]])]), True, 4))
    for i in range(len(idx)):
        body = _obname(0, 0, frame_name) + _uvari(i + 1) + b''.join(_encode(code, vals[i]) for _, _, code, vals in channels)
        records.append((body, False, 0))
    # pack into visible records, splitting a logical record into segments when it would not fit
    out = bytearray()
    out += b'   1' + b'V1.00' + b'RECORD' + f'{max_record:5d}'.encode('ascii') + f'{file_id:<60s}'.encode('ascii')[:60]
    vr = bytearray()

    def flush():
        nonlocal vr
        if vr:
            out.extend(struct.pack('>H', len(vr) + 4) + b'\xff\x01' + vr)
            vr = bytearray()
    cap = max_record - 4
    for body, eflr, ltype in records:
        chunk = cap - 8
        pieces = [body[i:i + chunk] for i in range(0, len(body), chunk)] or [b'']
        for k, pc in enumerate(pieces):
            seg = _segment(pc, eflr, ltype, k == 0, k == len(pieces) - 1)
            if len(vr) + len(seg) > cap:
                flush()
            vr += seg
    flush()
    Path(path).write_bytes(bytes(out))
    return path
