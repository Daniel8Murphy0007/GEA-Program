# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""dnp3 - the DNP3 (IEEE 1815) master port, read-only: what pad RTUs and pipeline SCADA speak in North America.

DNP3 is three layers on one TCP connection (port 20000) or a serial line. The data link frame: start 05 64,
length, control, destination and source addresses, a CRC over the header and one over every 16-byte block of
user data (CRC-16/DNP, polynomial 0x3D65 reflected, final complement). The transport function: one byte,
FIR / FIN / sequence, that cuts an application fragment into frames. The application layer: control (FIR,
FIN, CON, UNS, sequence), a function code, in a response two bytes of internal indications, then object
headers - group, variation, qualifier and range - each followed by the objects it announces.

This master does two things and nothing else: an INTEGRITY POLL (a READ of class 1, 2, 3 events and the
class 0 static data, the way every master starts and the way it re-synchronises), and an EVENT POLL (a READ
of class 1, 2, 3). It confirms what the outstation asks it to confirm (CON set), it answers an unsolicited
response with the confirm the specification requires and reads the events it carries, and it never sends
a control, a write, a time set, a restart or a file transfer - there is no code path for any of them. What
it reads: analog inputs (group 30, every variation; the events of group 32 with the outstation's own 48-bit
timestamp as the source time), binary inputs (group 1 and the events of group 2), counters (group 20 and
the events of group 22). The quality flags of each object are kept: ONLINE and nothing else set is GOOD;
COMM_LOST, OVER_RANGE, REFERENCE_ERR, RESTART or not ONLINE is BAD with the flag named; a REMOTE or LOCAL
FORCED value is kept with the flag named in the rule. An object this master does not parse ends the fragment
with the group and variation named, so nothing is silently guessed past.

The outstation's point list is the site's, with its source: `mapping` entries address a point as `ai:3`,
`bi:1` or `ctr:0`, and the port refuses to run without one. `SimOutstation` is an in-process outstation
(TCP) for the site's rehearsal and the gate: static floats, binaries and counters, events with time, a
confirm requested, an unsolicited null response at connect, and the faults a line shows.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, utc_now
from .sample_record import SampleRecord

START = b'\x05\x64'
FC_CONFIRM, FC_READ, FC_RESPONSE, FC_UNSOLICITED = 0x00, 0x01, 0x81, 0x82
FLAG_NAMES = {0x01: 'ONLINE', 0x02: 'RESTART', 0x04: 'COMM_LOST', 0x08: 'REMOTE_FORCED', 0x10: 'LOCAL_FORCED', 0x20: 'OVER_RANGE', 0x40: 'REFERENCE_ERR', 0x80: 'ROLLOVER'}
IIN_NAMES = {0x0001: 'BROADCAST', 0x0002: 'CLASS_1_EVENTS', 0x0004: 'CLASS_2_EVENTS', 0x0008: 'CLASS_3_EVENTS', 0x0010: 'NEED_TIME', 0x0020: 'LOCAL_CONTROL',
             0x0040: 'DEVICE_TROUBLE', 0x0080: 'DEVICE_RESTART', 0x0100: 'NO_FUNC_CODE_SUPPORT', 0x0200: 'OBJECT_UNKNOWN', 0x0400: 'PARAMETER_ERROR',
             0x0800: 'EVENT_BUFFER_OVERFLOW', 0x1000: 'ALREADY_EXECUTING', 0x2000: 'CONFIG_CORRUPT'}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

EXAMPLE_CONFIG = {
    'transport': 'sim', 'host': '127.0.0.1', 'port': 20000, 'master': 1, 'outstation': 10, 'polls': 3, 'interval_s': 1.0, 'timeout_s': 5.0,
    'mapping': [{'address': 'ai:0', 'tag_id': 'DHP_psi', 'unit': 'psi', 'tag_class': 'pressure'},
                {'address': 'ai:1', 'tag_id': 'DHT_degF', 'unit': 'degF', 'tag_class': 'temperature'},
                {'address': 'ai:2', 'tag_id': 'Q_bpm', 'unit': 'bbl/min', 'tag_class': 'rate'},
                {'address': 'bi:0', 'tag_id': 'PUMP_RUN', 'unit': '', 'tag_class': 'status'},
                {'address': 'ctr:0', 'tag_id': 'INJ_bbl', 'unit': 'bbl', 'tag_class': 'volume'}],
    'point_source': 'EXAMPLE - the stand-in outstation this port ships for rehearsal, not a device',
}


# ---- CRC-16/DNP and the data link frame -------------------------------------------------------------------------------------
def crc_dnp(data: bytes) -> int:
    """CRC-16/DNP: polynomial 0x3D65, reflected (0xA6BC), initial 0, result complemented; transmitted low byte first."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def link_frame(control: int, dest: int, src: int, user: bytes) -> bytes:
    hdr = START + bytes([5 + len(user), control]) + struct.pack('<HH', dest, src)
    out = hdr + struct.pack('<H', crc_dnp(hdr))
    for i in range(0, len(user), 16):
        blk = user[i:i + 16]
        out += blk + struct.pack('<H', crc_dnp(blk))
    return out


def parse_link(buf: bytes) -> Tuple[Optional[dict], int, str]:
    """One frame from the front of buf: (frame, bytes consumed, why) - frame None with 0 consumed when more bytes are needed,
    None with a count when the bytes must be discarded (bad start, bad CRC)."""
    i = buf.find(START)
    if i < 0:
        return None, len(buf), 'no start bytes' if buf else ''
    if i:
        return None, i, f'{i} byte(s) before the start bytes discarded'
    if len(buf) < 10:
        return None, 0, ''
    length, control = buf[2], buf[3]
    dest, src = struct.unpack('<HH', buf[4:8])
    if crc_dnp(buf[:8]) != struct.unpack('<H', buf[8:10])[0]:
        return None, 2, 'header CRC mismatch'
    n_user = length - 5
    n_blocks = (n_user + 15) // 16
    total = 10 + n_user + 2 * n_blocks
    if len(buf) < total:
        return None, 0, ''
    user = b''
    p = 10
    for k in range(n_blocks):
        m = min(16, n_user - 16 * k)
        blk = buf[p:p + m]
        if crc_dnp(blk) != struct.unpack('<H', buf[p + m:p + m + 2])[0]:
            return None, total, f'data block {k} CRC mismatch'
        user += blk
        p += m + 2
    return {'control': control, 'dest': dest, 'src': src, 'user': user, 'fc': control & 0x0F, 'prm': bool(control & 0x40), 'dir': bool(control & 0x80)}, total, ''


# ---- the application layer: objects -----------------------------------------------------------------------------------------
def _flags(f: int) -> Tuple[str, str]:
    names = [n for bit, n in FLAG_NAMES.items() if f & bit]
    bad = [n for n in names if n in ('RESTART', 'COMM_LOST', 'OVER_RANGE', 'REFERENCE_ERR')]
    if not (f & 0x01):
        return 'BAD', 'not ONLINE' + (f" ({', '.join(names)})" if names else '')
    if bad:
        return 'BAD', ', '.join(bad)
    forced = [n for n in names if n in ('REMOTE_FORCED', 'LOCAL_FORCED')]
    return 'GOOD', ', '.join(forced)


def _time48(b: bytes) -> datetime:
    ms = int.from_bytes(b[:6], 'little')
    return EPOCH + timedelta(milliseconds=ms)


def _time48_bytes(dt: datetime) -> bytes:
    return int((dt - EPOCH).total_seconds() * 1000).to_bytes(6, 'little')


# (group, variation) -> (size in bytes per object, decoder(bytes) -> (value, flags, time or None)); None size = packed bits
_FMT = {
    (30, 1): (5, lambda b: (struct.unpack('<i', b[1:5])[0], b[0], None)),
    (30, 2): (3, lambda b: (struct.unpack('<h', b[1:3])[0], b[0], None)),
    (30, 3): (4, lambda b: (struct.unpack('<i', b[:4])[0], 0x01, None)),
    (30, 4): (2, lambda b: (struct.unpack('<h', b[:2])[0], 0x01, None)),
    (30, 5): (5, lambda b: (struct.unpack('<f', b[1:5])[0], b[0], None)),
    (30, 6): (9, lambda b: (struct.unpack('<d', b[1:9])[0], b[0], None)),
    (32, 1): (5, lambda b: (struct.unpack('<i', b[1:5])[0], b[0], None)),
    (32, 2): (3, lambda b: (struct.unpack('<h', b[1:3])[0], b[0], None)),
    (32, 3): (11, lambda b: (struct.unpack('<i', b[1:5])[0], b[0], _time48(b[5:11]))),
    (32, 4): (9, lambda b: (struct.unpack('<h', b[1:3])[0], b[0], _time48(b[3:9]))),
    (32, 5): (5, lambda b: (struct.unpack('<f', b[1:5])[0], b[0], None)),
    (32, 6): (9, lambda b: (struct.unpack('<d', b[1:9])[0], b[0], None)),
    (32, 7): (11, lambda b: (struct.unpack('<f', b[1:5])[0], b[0], _time48(b[5:11]))),
    (32, 8): (15, lambda b: (struct.unpack('<d', b[1:9])[0], b[0], _time48(b[9:15]))),
    (1, 2): (1, lambda b: (1.0 if b[0] & 0x80 else 0.0, b[0] & 0x7F, None)),
    (2, 1): (1, lambda b: (1.0 if b[0] & 0x80 else 0.0, b[0] & 0x7F, None)),
    (2, 2): (7, lambda b: (1.0 if b[0] & 0x80 else 0.0, b[0] & 0x7F, _time48(b[1:7]))),
    (20, 1): (5, lambda b: (struct.unpack('<I', b[1:5])[0], b[0], None)),
    (20, 2): (3, lambda b: (struct.unpack('<H', b[1:3])[0], b[0], None)),
    (20, 5): (4, lambda b: (struct.unpack('<I', b[:4])[0], 0x01, None)),
    (20, 6): (2, lambda b: (struct.unpack('<H', b[:2])[0], 0x01, None)),
    (22, 1): (5, lambda b: (struct.unpack('<I', b[1:5])[0], b[0], None)),
    (22, 2): (3, lambda b: (struct.unpack('<H', b[1:3])[0], b[0], None)),
    (22, 5): (11, lambda b: (struct.unpack('<I', b[1:5])[0], b[0], _time48(b[5:11]))),
    (22, 6): (9, lambda b: (struct.unpack('<H', b[1:3])[0], b[0], _time48(b[3:9]))),
}
_PREFIX = {30: 'ai', 32: 'ai', 1: 'bi', 2: 'bi', 20: 'ctr', 22: 'ctr'}
_PACKED = {(1, 1), (10, 1)}


def parse_objects(data: bytes) -> Tuple[List[dict], str]:
    """Every object of a response fragment's object section: [{'group','variation','index','value','flags','time'}], and
    the reason parsing stopped, if it did (an object this master does not parse, named)."""
    out: List[dict] = []
    p = 0
    while p < len(data):
        if p + 3 > len(data):
            return out, f'{len(data) - p} trailing byte(s) after the last object header'
        g, v, q = data[p], data[p + 1], data[p + 2]
        p += 3
        pref = q & 0x70
        rng = q & 0x0F
        indexes: List[int] = []
        count = 0
        if rng == 0:
            start, stop = data[p], data[p + 1]; p += 2
            indexes = list(range(start, stop + 1))
        elif rng == 1:
            start, stop = struct.unpack('<HH', data[p:p + 4]); p += 4
            indexes = list(range(start, stop + 1))
        elif rng == 6:
            continue                                            # a request-style header (all objects) carries no objects in a response
        elif rng in (7, 8, 9):
            n = {7: 1, 8: 2, 9: 4}[rng]
            count = int.from_bytes(data[p:p + n], 'little'); p += n
        else:
            return out, f'object g{g}v{v}: qualifier {q:#04x} not handled'
        key = (g, v)
        if key in _PACKED:
            n = len(indexes) if indexes else count
            nbytes = (n + 7) // 8
            bits = data[p:p + nbytes]; p += nbytes
            for k in range(n):
                idx = indexes[k] if indexes else k
                out.append({'group': g, 'variation': v, 'index': idx, 'value': 1.0 if (bits[k // 8] >> (k % 8)) & 1 else 0.0, 'flags': 0x01, 'time': None})
            continue
        if key not in _FMT:
            return out, f'object g{g}v{v} is not one this master reads (analog inputs 30/32, binary inputs 1/2, counters 20/22)'
        size, dec = _FMT[key]
        n = len(indexes) if indexes else count
        psize = {0x00: 0, 0x10: 1, 0x20: 2, 0x30: 4}.get(pref)
        if psize is None:
            return out, f'object g{g}v{v}: prefix code {pref >> 4} not handled'
        for k in range(n):
            if p + psize + size > len(data):
                return out, f'object g{g}v{v}: fragment ends inside object {k + 1} of {n}'
            idx = indexes[k] if indexes else (int.from_bytes(data[p:p + psize], 'little') if psize else k)
            p += psize
            val, fl, t = dec(data[p:p + size]); p += size
            out.append({'group': g, 'variation': v, 'index': idx, 'value': float(val), 'flags': int(fl), 'time': t})
    return out, ''


def read_request_fragment(seq: int, classes: Tuple[int, ...]) -> bytes:
    """A READ of the named classes (1, 2, 3 events; 0 static), qualifier 0x06 (all), one fragment."""
    body = bytes([0xC0 | (seq & 0x0F), FC_READ])
    for c in classes:
        body += bytes([60, {1: 2, 2: 3, 3: 4, 0: 1}[c], 0x06])
    return body


# ---- the master ------------------------------------------------------------------------------------------------------------
class Master:
    """A DNP3 master over one TCP connection, read-only: integrity poll, event poll, confirms, unsolicited handling."""

    def __init__(self, host: str, port: int = 20000, master: int = 1, outstation: int = 10, timeout_s: float = 5.0, sock=None):
        self.host, self.port, self.master, self.outstation, self.timeout = host, int(port), int(master), int(outstation), float(timeout_s)
        self.sock = sock
        self.buf = b''
        self.app_seq = 0
        self.tr_seq = 0
        self.iin: Tuple[int, int] = (0, 0)
        self.unsolicited: List[List[dict]] = []
        self.faults: List[str] = []
        self.frames_in = 0

    def connect(self) -> None:
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    # link + transport
    def _send_fragment(self, app: bytes) -> None:
        chunks = [app[i:i + 249] for i in range(0, len(app), 249)] or [b'']
        for k, ch in enumerate(chunks):
            th = (0x40 if k == 0 else 0) | (0x80 if k == len(chunks) - 1 else 0) | (self.tr_seq & 0x3F)
            self.tr_seq = (self.tr_seq + 1) & 0x3F
            self.sock.sendall(link_frame(0xC4, self.outstation, self.master, bytes([th]) + ch))

    def _read_frame(self) -> Optional[dict]:
        deadline = time.monotonic() + self.timeout
        while True:
            fr, used, why = parse_link(self.buf)
            if fr is not None:
                self.buf = self.buf[used:]
                self.frames_in += 1
                return fr
            if used:
                self.buf = self.buf[used:]
                if why:
                    self.faults.append(why)
                continue
            if time.monotonic() > deadline:
                return None
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError('the outstation closed the connection')
            self.buf += chunk

    def _read_fragment(self) -> Optional[bytes]:
        """Frames reassembled by the transport function into one application fragment (FIR..FIN)."""
        app = b''
        while True:
            fr = self._read_frame()
            if fr is None:
                return None
            if fr['src'] != self.outstation or fr['dest'] != self.master:
                self.faults.append(f"frame from {fr['src']} to {fr['dest']} ignored (this master is {self.master}, the outstation {self.outstation})")
                continue
            if fr['fc'] not in (3, 4) or not fr['user']:          # confirmed / unconfirmed user data only
                continue
            th = fr['user'][0]
            if th & 0x40:
                app = b''
            app += fr['user'][1:]
            if th & 0x80:
                return app

    def _confirm(self, seq: int, uns: bool) -> None:
        self._send_fragment(bytes([0xC0 | (0x10 if uns else 0) | (seq & 0x0F), FC_CONFIRM]))

    def _response(self, want_seq: int) -> Tuple[List[dict], str]:
        """The response to our request: every fragment (FIR..FIN at the application layer), confirms sent as asked,
        unsolicited responses on the way confirmed and kept aside."""
        objs: List[dict] = []
        while True:
            frag = self._read_fragment()
            if frag is None:
                return objs, 'no response within the timeout'
            ctl, fc = frag[0], frag[1]
            seq = ctl & 0x0F
            if fc == FC_UNSOLICITED:
                if ctl & 0x20:
                    self._confirm(seq, True)
                u_objs, _ = parse_objects(frag[4:])
                self.iin = (frag[2], frag[3])
                self.unsolicited.append(u_objs)
                continue
            if fc != FC_RESPONSE:
                self.faults.append(f'fragment with function {fc:#04x} ignored')
                continue
            if seq != want_seq:
                self.faults.append(f'response sequence {seq}, expected {want_seq} - ignored')
                continue
            self.iin = (frag[2], frag[3])
            part, why = parse_objects(frag[4:])
            objs.extend(part)
            if ctl & 0x20:
                self._confirm(seq, False)
            if why:
                return objs, why
            if ctl & 0x40:                                        # FIN
                return objs, ''

    def read(self, classes: Tuple[int, ...]) -> Tuple[List[dict], str]:
        seq = self.app_seq
        self.app_seq = (self.app_seq + 1) & 0x0F
        self._send_fragment(read_request_fragment(seq, classes))
        return self._response(seq)

    def integrity_poll(self) -> Tuple[List[dict], str]:
        return self.read((1, 2, 3, 0))

    def event_poll(self) -> Tuple[List[dict], str]:
        return self.read((1, 2, 3))

    def iin_names(self) -> List[str]:
        v = self.iin[0] | (self.iin[1] << 8)
        return [n for bit, n in IIN_NAMES.items() if v & bit]


# ---- the port: objects to records -------------------------------------------------------------------------------------------
class Dnp3Port:
    def __init__(self, config: dict, sock=None):
        self.cfg = dict(config)
        if not self.cfg.get('mapping'):
            raise ValueError('a point mapping is required (ai:<index>, bi:<index>, ctr:<index>) - the port maps what the site declares')
        if not self.cfg.get('point_source'):
            raise ValueError("the point list needs its source (point_source: the RTU's point list document and revision)")
        self.maps: Dict[str, TagMapping] = load_mappings(self.cfg['mapping'], 'address')
        for a in self.maps:
            kind, _, idx = a.partition(':')
            if kind not in ('ai', 'bi', 'ctr') or not idx.isdigit():
                raise ValueError(f'point address {a!r}: use ai:<index>, bi:<index> or ctr:<index>')
        self.master = Master(str(self.cfg.get('host', '127.0.0.1')), int(self.cfg.get('port', 20000)), int(self.cfg.get('master', 1)),
                             int(self.cfg.get('outstation', 10)), float(self.cfg.get('timeout_s', 5.0)), sock=sock)
        self.records: List[SampleRecord] = []
        self.unmapped: Dict[str, int] = {}
        self.polls = 0
        self.last_iin: List[str] = []

    def _take(self, objs: List[dict], now: datetime) -> int:
        n = 0
        for o in objs:
            addr = f"{_PREFIX.get(o['group'], 'g' + str(o['group']))}:{o['index']}"
            m = self.maps.get(addr)
            if m is None:
                self.unmapped[addr] = self.unmapped.get(addr, 0) + 1
                continue
            q, rule = _flags(o['flags'])
            self.records.append(make_record(m, o['value'] if q == 'GOOD' else None, o['time'] or now, q if q == 'GOOD' else 'BAD', rule, now))
            if q != 'GOOD':
                self.records[-1].value = o['value']
            n += 1
        return n

    def poll_once(self, integrity: bool) -> dict:
        now = utc_now()
        objs, why = self.master.integrity_poll() if integrity else self.master.event_poll()
        for u in self.master.unsolicited:
            self._take(u, now)
        self.master.unsolicited = []
        n = self._take(objs, now)
        self.polls += 1
        self.last_iin = self.master.iin_names()
        if why:
            self.master.faults.append(why)
        return {'objects': len(objs), 'records': n, 'why': why, 'iin': self.last_iin}

    def run(self, polls: Optional[int] = None, interval_s: Optional[float] = None) -> List[SampleRecord]:
        n = int(self.cfg.get('polls', 3) if polls is None else polls)
        dt = float(self.cfg.get('interval_s', 1.0) if interval_s is None else interval_s)
        self.master.connect()
        try:
            for k in range(n):
                self.poll_once(integrity=(k == 0))
                if dt > 0 and k < n - 1:
                    time.sleep(dt)
        finally:
            self.master.close()
        return self.records

    def stream(self):
        return records_to_stream(self.records, name=f'dnp3_{self.master.host}', source_format='dnp3')

    def summary(self) -> dict:
        good = sum(1 for r in self.records if r.quality_flag == 'GOOD')
        return {'polls': self.polls, 'records': len(self.records), 'good': good, 'bad': len(self.records) - good, 'unmapped': dict(self.unmapped),
                'iin': self.last_iin, 'faults': self.master.faults[-20:], 'frames_in': self.master.frames_in, 'outstation': self.master.outstation}


# ---- a stand-in outstation ----------------------------------------------------------------------------------------------------
class SimOutstation:
    """An in-process outstation on TCP: static analog inputs (g30v5 float with flags), binaries (g1v2), counters (g20v1);
    class 1 events (g32v7 analog with time, g2v2 binary with time); a confirm requested on every response; an
    unsolicited null response with CON at connect; `corrupt_every` wrecks every n-th frame's CRC."""

    def __init__(self, outstation: int = 10, master: int = 1, analogs: Optional[List[float]] = None, flags: Optional[List[int]] = None,
                 binaries: Optional[List[int]] = None, counters: Optional[List[int]] = None, corrupt_every: int = 0, events: bool = True):
        self.outstation, self.master = int(outstation), int(master)
        self.analogs = list(analogs if analogs is not None else [3185.5, 212.3, 7.25])
        self.flags = list(flags if flags is not None else [0x01] * len(self.analogs))
        self.binaries = list(binaries if binaries is not None else [1, 0])
        self.counters = list(counters if counters is not None else [123456])
        self.corrupt_every, self.n_out, self.events = int(corrupt_every), 0, events
        self.event_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        self.requests: List[Tuple[int, ...]] = []
        self.confirms = 0
        self.lock = threading.Lock()
        self.tr_seq = 0

    def set_analog(self, i: int, value: float, flags: int = 0x01) -> None:
        with self.lock:
            self.analogs[i] = float(value); self.flags[i] = int(flags)

    # the objects of a response
    def _static(self) -> bytes:
        out = b''
        if self.analogs:
            out += bytes([30, 5, 0x00, 0, len(self.analogs) - 1]) + b''.join(bytes([f]) + struct.pack('<f', v) for v, f in zip(self.analogs, self.flags))
        if self.binaries:
            out += bytes([1, 2, 0x00, 0, len(self.binaries) - 1]) + b''.join(bytes([0x01 | (0x80 if b else 0)]) for b in self.binaries)
        if self.counters:
            out += bytes([20, 1, 0x00, 0, len(self.counters) - 1]) + b''.join(bytes([0x01]) + struct.pack('<I', c) for c in self.counters)
        return out

    def _events(self) -> bytes:
        if not self.events:
            return b''
        t = _time48_bytes(self.event_time)
        out = bytes([32, 7, 0x17, 1, 0]) + bytes([0x01]) + struct.pack('<f', self.analogs[0] + 0.5) + t     # one analog event, index 0, prefixed
        out += bytes([2, 2, 0x17, 1, 1]) + bytes([0x01 | 0x80]) + t                                           # one binary event, index 1
        return out

    def _send(self, c: socket.socket, app: bytes) -> None:
        th = 0xC0 | (self.tr_seq & 0x3F); self.tr_seq = (self.tr_seq + 1) & 0x3F
        fr = link_frame(0x44, self.master, self.outstation, bytes([th]) + app)
        self.n_out += 1
        if self.corrupt_every and self.n_out % self.corrupt_every == 0:
            fr = fr[:-1] + bytes([fr[-1] ^ 0xFF])
        c.sendall(fr)

    def serve(self, host: str = '127.0.0.1', port: int = 0):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port)); srv.listen(2); srv.settimeout(0.5)
        stop = threading.Event()

        def run():
            while not stop.is_set():
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._conn, args=(c, stop), daemon=True).start()
        th = threading.Thread(target=run, daemon=True); th.start()
        return _Handle(srv, stop, th), srv.getsockname()[1]

    def _conn(self, c: socket.socket, stop: threading.Event) -> None:
        c.settimeout(0.5)
        buf = b''
        try:
            self._send(c, bytes([0xF0, FC_UNSOLICITED, 0x80, 0x00]))        # unsolicited null response: FIR FIN CON UNS, seq 0, IIN DEVICE_RESTART
            while not stop.is_set():
                try:
                    data = c.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while True:
                    fr, used, _ = parse_link(buf)
                    if fr is None:
                        if used:
                            buf = buf[used:]; continue
                        break
                    buf = buf[used:]
                    app = fr['user'][1:]
                    if len(app) < 2:
                        continue
                    ctl, fc = app[0], app[1]
                    if fc == FC_CONFIRM:
                        self.confirms += 1
                        continue
                    if fc != FC_READ:
                        continue
                    classes = tuple({1: 0, 2: 1, 3: 2, 4: 3}[app[i + 1]] for i in range(2, len(app), 3) if app[i] == 60)
                    with self.lock:
                        self.requests.append(classes)
                        body = (self._events() if any(cl in (1, 2, 3) for cl in classes) else b'') + (self._static() if 0 in classes else b'')
                    self._send(c, bytes([0xE0 | (ctl & 0x0F), FC_RESPONSE, 0x00, 0x00]) + body)     # FIR FIN CON; IIN clear
        finally:
            c.close()


class _Handle:
    def __init__(self, srv, stop, th):
        self.srv, self.stop, self.th = srv, stop, th

    def close(self) -> None:
        self.stop.set()
        try:
            self.srv.close()
        except OSError:
            pass
        self.th.join(timeout=2)


def load_config(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with Path(path_or_dict).open(encoding='utf-8') as f:
        return json.load(f)


def read_dnp3(config) -> 'LiveStream':   # noqa: F821
    cfg = load_config(config)
    if cfg.get('transport') == 'sim':
        sim = SimOutstation(int(cfg.get('outstation', 10)), int(cfg.get('master', 1)))
        h, port = sim.serve()
        try:
            port_ = Dnp3Port({**cfg, 'host': '127.0.0.1', 'port': port})
            port_.run()
        finally:
            h.close()
        return port_.stream()
    port_ = Dnp3Port(cfg)
    port_.run()
    return port_.stream()


def selftest() -> dict:
    """The CRC against the specification's worked frame, a link round trip, the object parser on every variation this
    master reads, and a poll against the stand-in with its confirm, its unsolicited response and a corrupt frame."""
    out = {}
    # IEEE 1815 Annex: the reset-link frame 05 64 05 C0 01 00 00 04 has CRC E9 21
    out['crc_spec_frame'] = crc_dnp(bytes.fromhex('056405C001000004')) == 0x21E9
    fr, used, why = parse_link(link_frame(0xC4, 10, 1, bytes(range(40))))
    out['link_round_trip'] = fr is not None and fr['user'] == bytes(range(40)) and fr['dest'] == 10 and fr['src'] == 1 and used == 10 + 40 + 6
    objs, why = parse_objects(bytes([30, 5, 0x00, 0, 1, 0x01]) + struct.pack('<f', 1.5) + bytes([0x24]) + struct.pack('<f', 2.5)
                              + bytes([32, 7, 0x17, 1, 3, 0x01]) + struct.pack('<f', 9.0) + _time48_bytes(datetime(2026, 1, 1, tzinfo=timezone.utc))
                              + bytes([1, 1, 0x00, 0, 2, 0b101]) + bytes([20, 1, 0x01, 5, 0, 5, 0, 0x01]) + struct.pack('<I', 77))
    out['objects'] = (not why and [(o['group'], o['variation'], o['index'], o['value']) for o in objs]
                      == [(30, 5, 0, 1.5), (30, 5, 1, 2.5), (32, 7, 3, 9.0), (1, 1, 0, 1.0), (1, 1, 1, 0.0), (1, 1, 2, 1.0), (20, 1, 5, 77.0)]
                      and objs[2]['time'] == datetime(2026, 1, 1, tzinfo=timezone.utc) and _flags(0x25) == ('BAD', 'COMM_LOST, OVER_RANGE') and _flags(0x24)[1].startswith('not ONLINE'))
    _, why2 = parse_objects(bytes([50, 1, 0x07, 1]) + bytes(6))
    out['unknown_named'] = 'g50v1' in why2
    sim = SimOutstation(corrupt_every=0)
    h, port = sim.serve()
    try:
        p = Dnp3Port({**EXAMPLE_CONFIG, 'transport': 'tcp', 'host': '127.0.0.1', 'port': port})
        p.run(polls=2, interval_s=0.05)
        for _ in range(40):                                   # the last confirm is still on its way when the socket closes
            if sim.confirms >= 3:
                break
            time.sleep(0.05)
        s = p.summary()
        vals = {r.tag_id: r.value for r in p.records if r.quality_flag == 'GOOD' and not r.timestamp_utc.startswith('2026-10-11T12:00:00')}
        out['poll'] = (s['polls'] == 2 and abs(vals['DHP_psi'] - 3185.5) < 1e-3 and vals['PUMP_RUN'] == 1.0 and vals['INJ_bbl'] == 123456.0
                       and sim.requests[0] == (1, 2, 3, 0) and sim.requests[1] == (1, 2, 3) and sim.confirms >= 3)
        ev = [r for r in p.records if r.tag_id == 'DHP_psi' and r.timestamp_utc.startswith('2026-10-11T12:00:00')]
        out['event_time_is_source'] = bool(ev) and abs(ev[0].value - 3186.0) < 1e-3 and ev[0].latency_s() > 0
    finally:
        h.close()
    bad = SimOutstation(corrupt_every=2)
    h, port = bad.serve()
    try:
        p2 = Dnp3Port({**EXAMPLE_CONFIG, 'transport': 'tcp', 'host': '127.0.0.1', 'port': port, 'timeout_s': 1.5})
        p2.run(polls=2, interval_s=0.05)
        out['corrupt_named'] = any('CRC mismatch' in f for f in p2.master.faults)
    finally:
        h.close()
    out['ok'] = all(out.values())
    return out
