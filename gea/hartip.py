# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""hartip - the HART-IP port, read-only: the gauge's own transmitter, asked for its variables over the network.

A downhole gauge's surface panel, a wellhead pressure transmitter, a flow computer - a great many of them are
HART instruments, and HART-IP (HART Communication Foundation, HCF_SPEC-085) carries the HART command set over
TCP or UDP, port 5094, from a HART-IP gateway or a native HART-IP device. One HART-IP message is an 8-byte
header - version (1), message type (0 request, 1 response, 2 publish, 3 NAK), message id (0 session initiate,
1 session close, 2 keep-alive, 3 token-passing PDU), status, sequence number, byte count - and, for a PDU, the
HART token-passing frame: delimiter (0x82 for a long frame from a master), the 5-byte long address (the
manufacturer and device type codes with the primary-master bit, then the 3-byte device id), the command, the
byte count, the data, and a longitudinal-parity check byte over everything from the delimiter.

This port opens a session (message 0: master type, inactivity timeout), then sends command 0 (read unique
identifier: the long address of whatever answers address 0, with its manufacturer, device type and revision)
and, on every poll, command 3 (read dynamic variables and loop current: the loop current in mA, then up to four
variables each as a unit code and an IEEE float) and, when the mapping asks for variables beyond the fourth,
command 9 (read device variables with status: each with its code, classification, unit, value and status
byte). The response code (byte 0 of the data) and the device status (byte 1) are kept per record: a response
code that is an error names it and the record is GAP; a device status with DEVICE_MALFUNCTION or a variable
status not GOOD is BAD with the bit named. Units come from the HART unit-code table (the common pressure,
temperature, flow and level codes) and are converted to the mapping's unit where a conversion is known. It
never writes: there is no code path for a command that changes a device.

`SimDevice` is an in-process HART-IP device on TCP for the site's rehearsal and the gate.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .live_ports import TagMapping, conversion, load_mappings, make_record, records_to_stream, utc_now
from .sample_record import SampleRecord

PORT = 5094
MSG_REQUEST, MSG_RESPONSE, MSG_PUBLISH, MSG_NAK = 0, 1, 2, 3
ID_SESSION_INITIATE, ID_SESSION_CLOSE, ID_KEEPALIVE, ID_PDU = 0, 1, 2, 3
# HART unit codes (HCF_SPEC-183, common engineering units)
UNIT_CODES = {1: 'inH2O', 2: 'inHg', 3: 'ftH2O', 4: 'mmH2O', 5: 'mmHg', 6: 'psi', 7: 'bar', 8: 'mbar', 11: 'Pa', 12: 'kPa', 13: 'torr', 14: 'atm',
              15: 'ft3/min', 16: 'gal/min', 17: 'L/min', 19: 'm3/h', 20: 'ft/s', 21: 'm/s', 22: 'gal/s', 24: 'L/s', 32: 'degC', 33: 'degF', 35: 'K',
              39: 'mA', 40: 'gal', 41: 'L', 43: 'm3', 44: 'ft', 45: 'm', 46: 'bbl', 47: 'in', 48: 'cm', 49: 'mm', 57: '%', 58: 'V',
              90: 'g/cm3', 91: 'kg/m3', 92: 'lb/gal', 93: 'lb/ft3', 95: 'sg', 123: 'bbl/d', 131: 'bbl/h', 132: 'bbl/min', 235: 'gal/d', 250: 'none'}
RESPONSE_CODES = {0: '', 1: 'undefined', 2: 'invalid selection', 3: 'passed parameter too large', 4: 'passed parameter too small', 5: 'too few data bytes received',
                  6: 'device-specific command error', 7: 'in write-protect mode', 16: 'access restricted', 32: 'busy', 64: 'command not implemented'}
DEVICE_STATUS = {0x80: 'DEVICE_MALFUNCTION', 0x40: 'CONFIGURATION_CHANGED', 0x20: 'COLD_START', 0x10: 'MORE_STATUS_AVAILABLE', 0x08: 'LOOP_CURRENT_FIXED',
                 0x04: 'LOOP_CURRENT_SATURATED', 0x02: 'NON_PV_OUT_OF_LIMITS', 0x01: 'PV_OUT_OF_LIMITS'}
VAR_STATUS = {0xC0: 'GOOD', 0x80: 'MANUAL/FIXED', 0x40: 'POOR_ACCURACY', 0x00: 'BAD'}

EXAMPLE_CONFIG = {
    'transport': 'sim', 'host': '127.0.0.1', 'port': PORT, 'polls': 3, 'interval_s': 1.0, 'timeout_s': 5.0,
    'mapping': [{'address': 'pv', 'tag_id': 'DHP_psi', 'unit': 'psi', 'tag_class': 'pressure'},
                {'address': 'sv', 'tag_id': 'DHT_degF', 'unit': 'degF', 'tag_class': 'temperature'},
                {'address': 'loop_mA', 'tag_id': 'LOOP_mA', 'unit': 'mA', 'tag_class': 'diagnostic'},
                {'address': 'dv:5', 'tag_id': 'Q_bpm', 'unit': 'bbl/min', 'tag_class': 'rate'}],
    'device_source': 'EXAMPLE - the stand-in device this port ships for rehearsal, not an instrument',
}


# ---- the frames ------------------------------------------------------------------------------------------------------------
def hartip_header(msg_type: int, msg_id: int, seq: int, n: int, status: int = 0) -> bytes:
    return struct.pack('>BBBBHH', 1, msg_type, msg_id, status, seq & 0xFFFF, 8 + n)


def parse_header(b: bytes) -> dict:
    v, t, i, st, seq, cnt = struct.unpack('>BBBBHH', b[:8])
    return {'version': v, 'type': t, 'id': i, 'status': st, 'seq': seq, 'count': cnt}


def checksum(b: bytes) -> int:
    x = 0
    for c in b:
        x ^= c
    return x


def pdu(address: bytes, command: int, data: bytes = b'', delimiter: int = 0x82) -> bytes:
    """A long-frame HART PDU from a master: delimiter, 5-byte address, command, byte count, data, check byte."""
    body = bytes([delimiter]) + address + bytes([command, len(data)]) + data
    return body + bytes([checksum(body)])


def parse_pdu(b: bytes) -> Tuple[Optional[dict], str]:
    """A PDU from a device: (fields, '') or (None, why). Expands bytes 0 and 1 of the data as the response code and device status."""
    if len(b) < 2:
        return None, 'PDU too short'
    d = b[0]
    addr_len = 5 if d & 0x80 else 1
    if len(b) < 1 + addr_len + 3:
        return None, f'PDU too short for a {"long" if addr_len == 5 else "short"} frame'
    if checksum(b[:-1]) != b[-1]:
        return None, 'check byte mismatch'
    p = 1 + addr_len
    cmd, n = b[p], b[p + 1]
    if len(b) != p + 2 + n + 1:
        return None, f'byte count {n} does not match the frame ({len(b) - p - 3} data bytes present)'
    data = b[p + 2:p + 2 + n]
    out = {'delimiter': d, 'address': b[1:1 + addr_len], 'command': cmd, 'data': data}
    if n >= 2:
        out['response_code'], out['device_status'] = data[0], data[1]
        out['payload'] = data[2:]
    return out, ''


def unit_name(code: int) -> str:
    return UNIT_CODES.get(code, f'unit code {code}')


def parse_cmd3(payload: bytes) -> dict:
    """Command 3: loop current (float, mA), then up to four (unit code, float) pairs - PV, SV, TV, QV."""
    out = {'loop_mA': struct.unpack('>f', payload[:4])[0] if len(payload) >= 4 else None, 'vars': {}}
    names = ('pv', 'sv', 'tv', 'qv')
    p = 4
    for nm in names:
        if p + 5 > len(payload):
            break
        out['vars'][nm] = {'unit_code': payload[p], 'unit': unit_name(payload[p]), 'value': struct.unpack('>f', payload[p + 1:p + 5])[0]}
        p += 5
    return out


def parse_cmd9(payload: bytes) -> dict:
    """Command 9: extended device status, then per variable: code, classification, unit code, float value, status byte."""
    out = {'extended_status': payload[0] if payload else 0, 'vars': {}}
    p = 1
    while p + 8 <= len(payload):
        code, cls, uc = payload[p], payload[p + 1], payload[p + 2]
        val = struct.unpack('>f', payload[p + 3:p + 7])[0]
        st = payload[p + 7]
        out['vars'][code] = {'classification': cls, 'unit_code': uc, 'unit': unit_name(uc), 'value': val, 'status': st,
                             'status_name': VAR_STATUS.get(st & 0xC0, 'BAD')}
        p += 8
    return out


def parse_cmd0(payload: bytes) -> dict:
    """Command 0: the unique identifier: expanded device type, request preambles, HART revision, device revision,
    software revision, hardware revision/signalling, flags, device id (3 bytes) ... as far as the device sends."""
    out = {}
    if len(payload) >= 12:
        out = {'device_type': struct.unpack('>H', payload[1:3])[0], 'min_preambles': payload[3], 'hart_revision': payload[4], 'device_revision': payload[5],
               'software_revision': payload[6], 'hardware_revision': payload[7] >> 3, 'flags': payload[8], 'device_id': payload[9:12].hex()}
        if len(payload) >= 17:
            out['manufacturer_id'] = struct.unpack('>H', payload[14:16])[0] if len(payload) >= 16 else None
    return out


# ---- the client ------------------------------------------------------------------------------------------------------------
class HartIpClient:
    def __init__(self, host: str, port: int = PORT, timeout_s: float = 5.0, sock=None):
        self.host, self.port, self.timeout = host, int(port), float(timeout_s)
        self.sock = sock
        self.seq = 1
        self.address = bytes(5)                      # polling address 0 until command 0 answers with the long address
        self.identity: dict = {}
        self.faults: List[str] = []

    def connect(self) -> dict:
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        body = bytes([1]) + struct.pack('>I', int(self.timeout * 1000) * 6)       # master type 1 (primary), inactivity close timer ms
        hdr = self._exchange(MSG_REQUEST, ID_SESSION_INITIATE, body)
        if hdr['status'] != 0:
            raise ConnectionError(f"HART-IP session refused, status {hdr['status']}")
        return hdr

    def close(self) -> None:
        if self.sock:
            try:
                self._exchange(MSG_REQUEST, ID_SESSION_CLOSE, b'', wait=False)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _exchange(self, msg_type: int, msg_id: int, body: bytes, wait: bool = True) -> dict:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFF
        self.sock.sendall(hartip_header(msg_type, msg_id, seq, len(body)) + body)
        if not wait:
            return {}
        while True:
            hb = self._recv_exact(8)
            hdr = parse_header(hb)
            hdr['body'] = self._recv_exact(hdr['count'] - 8)
            if hdr['type'] == MSG_PUBLISH:          # a burst message on the way is not our answer
                continue
            if hdr['seq'] != seq:
                self.faults.append(f"sequence {hdr['seq']} answered, {seq} asked - ignored")
                continue
            if hdr['type'] == MSG_NAK:
                raise ConnectionError(f"HART-IP NAK, status {hdr['status']}")
            return hdr

    def _recv_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('the device closed the connection')
            buf += chunk
        return buf

    def command(self, cmd: int, data: bytes = b'') -> Tuple[Optional[dict], str]:
        hdr = self._exchange(MSG_REQUEST, ID_PDU, pdu(self.address, cmd, data))
        p, why = parse_pdu(hdr['body'])
        if p is None:
            return None, why
        if p.get('command') != cmd:
            return None, f"command {p.get('command')} answered, {cmd} asked"
        rc = p.get('response_code', 0)
        if rc and rc not in (8, 9, 10, 11, 12, 13, 14, 15):     # 8..15 are warnings, not errors (HCF_SPEC-307)
            return p, f'response code {rc}: {RESPONSE_CODES.get(rc, "device-specific")}'
        return p, ''

    def identify(self) -> dict:
        p, why = self.command(0)
        if p is None or why:
            raise ConnectionError(f'command 0 (read unique identifier) failed: {why}')
        self.identity = parse_cmd0(p['payload'])
        if 'device_type' in self.identity:
            dt = self.identity['device_type']
            self.address = bytes([((dt >> 8) & 0x3F) | 0x80, dt & 0xFF]) + bytes.fromhex(self.identity['device_id'])
        return self.identity


# ---- the port ------------------------------------------------------------------------------------------------------------
class HartIpPort:
    def __init__(self, config: dict, sock=None):
        self.cfg = dict(config)
        if not self.cfg.get('mapping'):
            raise ValueError('a variable mapping is required (pv, sv, tv, qv, loop_mA, dv:<code>) - the port maps what the site declares')
        if not self.cfg.get('device_source'):
            raise ValueError("the mapping needs its source (device_source: the instrument's datasheet or device description)")
        self.maps: Dict[str, TagMapping] = load_mappings(self.cfg['mapping'], 'address')
        for a in self.maps:
            if not (a in ('pv', 'sv', 'tv', 'qv', 'loop_mA') or (a.startswith('dv:') and a[3:].isdigit())):
                raise ValueError(f'variable address {a!r}: use pv, sv, tv, qv, loop_mA or dv:<device variable code>')
        self.dv_codes = [int(a[3:]) for a in self.maps if a.startswith('dv:')]
        self.client = HartIpClient(str(self.cfg.get('host', '127.0.0.1')), int(self.cfg.get('port', PORT)), float(self.cfg.get('timeout_s', 5.0)), sock=sock)
        self.records: List[SampleRecord] = []
        self.polls = 0
        self.device_status: List[str] = []

    def _record(self, addr: str, value: Optional[float], unit_in: str, now: datetime, quality: str, rule: str) -> None:
        m = self.maps.get(addr)
        if m is None:
            return
        v = value
        if v is not None and unit_in and m.unit and unit_in != m.unit and not m.unit_in:
            conv = conversion(unit_in, m.unit)
            if conv is None:
                quality, rule = 'BAD', f"device unit {unit_in} has no conversion to {m.unit}"
                v = None
            else:
                v = v * conv[0] + conv[1]
        self.records.append(make_record(m, v, now, quality, rule, now))

    def poll_once(self) -> dict:
        now = utc_now()
        out = {'cmd3': None, 'cmd9': None, 'faults': []}
        p, why = self.client.command(3)
        if p is None or why:
            out['faults'].append(f'command 3: {why}')
            for a in ('pv', 'sv', 'tv', 'qv', 'loop_mA'):
                self._record(a, None, '', now, 'GAP', f'command 3: {why}')
        else:
            ds = p.get('device_status', 0)
            self.device_status = [n for bit, n in DEVICE_STATUS.items() if ds & bit]
            bad = 'DEVICE_MALFUNCTION' in self.device_status
            c3 = parse_cmd3(p['payload']); out['cmd3'] = c3
            self._record('loop_mA', c3['loop_mA'], 'mA', now, 'BAD' if bad else 'GOOD', 'DEVICE_MALFUNCTION' if bad else '')
            for nm, v in c3['vars'].items():
                q, rule = ('BAD', 'DEVICE_MALFUNCTION') if bad else ('GOOD', '')
                if nm == 'pv' and ds & 0x01:
                    q, rule = 'BAD', 'PV_OUT_OF_LIMITS'
                elif nm != 'pv' and ds & 0x02:
                    q, rule = 'BAD', 'NON_PV_OUT_OF_LIMITS'
                self._record(nm, v['value'], v['unit'], now, q, rule)
        if self.dv_codes:
            p9, why9 = self.client.command(9, bytes(self.dv_codes[:8]))
            if p9 is None or why9:
                out['faults'].append(f'command 9: {why9}')
                for c in self.dv_codes:
                    self._record(f'dv:{c}', None, '', now, 'GAP', f'command 9: {why9}')
            else:
                c9 = parse_cmd9(p9['payload']); out['cmd9'] = c9
                for c in self.dv_codes:
                    v = c9['vars'].get(c)
                    if v is None:
                        self._record(f'dv:{c}', None, '', now, 'GAP', f'device variable {c} not in the answer')
                    else:
                        good = v['status_name'] == 'GOOD'
                        self._record(f'dv:{c}', v['value'], v['unit'], now, 'GOOD' if good else 'BAD', '' if good else f"variable status {v['status_name']}")
        self.polls += 1
        return out

    def run(self, polls: Optional[int] = None, interval_s: Optional[float] = None) -> List[SampleRecord]:
        n = int(self.cfg.get('polls', 3) if polls is None else polls)
        dt = float(self.cfg.get('interval_s', 1.0) if interval_s is None else interval_s)
        self.client.connect()
        try:
            self.client.identify()
            for k in range(n):
                self.poll_once()
                if dt > 0 and k < n - 1:
                    time.sleep(dt)
        finally:
            self.client.close()
        return self.records

    def stream(self):
        return records_to_stream(self.records, name=f'hartip_{self.client.host}', source_format='hartip')

    def summary(self) -> dict:
        good = sum(1 for r in self.records if r.quality_flag == 'GOOD')
        return {'polls': self.polls, 'records': len(self.records), 'good': good, 'not_good': len(self.records) - good, 'identity': self.client.identity,
                'device_status': self.device_status, 'faults': self.client.faults[-20:]}


# ---- a stand-in device ----------------------------------------------------------------------------------------------------
class SimDevice:
    """An in-process HART-IP device on TCP: command 0 with a device type and id, command 3 with the loop current and four
    variables (PV psi, SV degF, TV bar, QV %), command 9 for device variables by code; `malfunction` sets the device
    status bit; `fixed_variable` answers one variable with status MANUAL/FIXED; `refuse_cmd9` answers 64 (not implemented)."""

    def __init__(self, pv: float = 3185.5, sv: float = 212.3, device_type: int = 0x26A1, device_id: str = '0a0b0c', malfunction: bool = False,
                 refuse_cmd9: bool = False, fixed_variable: Optional[int] = None, dvs: Optional[Dict[int, Tuple[int, float]]] = None):
        self.pv, self.sv = float(pv), float(sv)
        self.device_type, self.device_id = device_type, device_id
        self.malfunction, self.refuse_cmd9, self.fixed_variable = malfunction, refuse_cmd9, fixed_variable
        self.dvs = dict(dvs if dvs is not None else {5: (132, 7.25), 6: (46, 12345.0)})   # code -> (unit code, value)
        self.commands: List[int] = []
        self.sessions = 0
        self.lock = threading.Lock()

    def set(self, pv: Optional[float] = None, sv: Optional[float] = None) -> None:
        with self.lock:
            if pv is not None:
                self.pv = float(pv)
            if sv is not None:
                self.sv = float(sv)

    def _status(self) -> bytes:
        return bytes([0, 0x80 if self.malfunction else 0])

    def _answer(self, req: dict) -> bytes:
        cmd = req['command']
        self.commands.append(cmd)
        addr = bytes([((self.device_type >> 8) & 0x3F) | 0x80, self.device_type & 0xFF]) + bytes.fromhex(self.device_id)
        with self.lock:
            if cmd == 0:
                data = self._status() + bytes([254]) + struct.pack('>H', self.device_type) + bytes([5, 7, 3, 1, 2 << 3, 0]) + bytes.fromhex(self.device_id) + bytes([5, 0]) + struct.pack('>H', 0x0026) + bytes([0])
            elif cmd == 3:
                data = self._status() + struct.pack('>f', 4.0 + 16.0 * min(max(self.pv / 5000.0, 0.0), 1.0))
                for uc, v in ((6, self.pv), (33, self.sv), (7, self.pv * 0.0689476), (57, 55.0)):
                    data += bytes([uc]) + struct.pack('>f', v)
            elif cmd == 9:
                if self.refuse_cmd9:
                    data = bytes([64, 0x80 if self.malfunction else 0])
                else:
                    data = self._status() + bytes([0])
                    for code in req['data']:
                        if code in self.dvs:
                            uc, v = self.dvs[code]
                            st = 0x80 if self.fixed_variable == code else 0xC0
                            data += bytes([code, 0, uc]) + struct.pack('>f', v) + bytes([st])
            else:
                data = bytes([64, 0x80 if self.malfunction else 0])
        return pdu(addr, cmd, data, delimiter=0x86)

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
        try:
            while not stop.is_set():
                try:
                    hb = c.recv(8)
                except socket.timeout:
                    continue
                if not hb:
                    break
                while len(hb) < 8:
                    more = c.recv(8 - len(hb))
                    if not more:
                        return
                    hb += more
                hdr = parse_header(hb)
                body = b''
                while len(body) < hdr['count'] - 8:
                    more = c.recv(hdr['count'] - 8 - len(body))
                    if not more:
                        return
                    body += more
                if hdr['id'] == ID_SESSION_INITIATE:
                    self.sessions += 1
                    c.sendall(hartip_header(MSG_RESPONSE, ID_SESSION_INITIATE, hdr['seq'], len(body)) + body)
                elif hdr['id'] == ID_KEEPALIVE:
                    c.sendall(hartip_header(MSG_RESPONSE, ID_KEEPALIVE, hdr['seq'], 0))
                elif hdr['id'] == ID_SESSION_CLOSE:
                    c.sendall(hartip_header(MSG_RESPONSE, ID_SESSION_CLOSE, hdr['seq'], 0)); break
                elif hdr['id'] == ID_PDU:
                    req, why = parse_pdu(body)
                    if req is None:
                        c.sendall(hartip_header(MSG_NAK, ID_PDU, hdr['seq'], 0, status=1)); continue
                    ans = self._answer(req)
                    c.sendall(hartip_header(MSG_RESPONSE, ID_PDU, hdr['seq'], len(ans)) + ans)
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


def read_hartip(config) -> 'LiveStream':   # noqa: F821
    cfg = load_config(config)
    if cfg.get('transport') == 'sim':
        sim = SimDevice()
        h, port = sim.serve()
        try:
            p = HartIpPort({**cfg, 'host': '127.0.0.1', 'port': port})
            p.run()
        finally:
            h.close()
        return p.stream()
    p = HartIpPort(cfg)
    p.run()
    return p.stream()


def selftest() -> dict:
    out = {}
    fr = pdu(bytes([0xA6, 0xA1, 0x0A, 0x0B, 0x0C]), 3)
    p, why = parse_pdu(fr)
    out['pdu_round_trip'] = p is not None and p['command'] == 3 and p['address'] == bytes([0xA6, 0xA1, 0x0A, 0x0B, 0x0C]) and not why
    bad = fr[:-1] + bytes([fr[-1] ^ 1])
    out['check_byte_named'] = parse_pdu(bad)[1] == 'check byte mismatch'
    c3 = parse_cmd3(struct.pack('>f', 12.0) + bytes([6]) + struct.pack('>f', 3185.5) + bytes([33]) + struct.pack('>f', 212.3))
    out['cmd3'] = c3['loop_mA'] == 12.0 and c3['vars']['pv']['unit'] == 'psi' and abs(c3['vars']['pv']['value'] - 3185.5) < 1e-3 and c3['vars']['sv']['unit'] == 'degF' and 'tv' not in c3['vars']
    sim = SimDevice(); h, port = sim.serve()
    try:
        prt = HartIpPort({**EXAMPLE_CONFIG, 'transport': 'tcp', 'host': '127.0.0.1', 'port': port})
        prt.run(polls=2, interval_s=0.05)
        s = prt.summary()
        vals = {r.tag_id: r.value for r in prt.records if r.quality_flag == 'GOOD'}
        out['poll'] = (s['polls'] == 2 and abs(vals['DHP_psi'] - 3185.5) < 1e-3 and abs(vals['DHT_degF'] - 212.3) < 1e-3 and abs(vals['Q_bpm'] - 7.25) < 1e-3
                       and abs(vals['LOOP_mA'] - (4 + 16 * 3185.5 / 5000)) < 1e-3 and s['identity']['device_type'] == 0x26A1 and s['identity']['device_id'] == '0a0b0c'
                       and sim.commands[:2] == [0, 3] and prt.client.address == bytes([0xA6, 0xA1, 0x0A, 0x0B, 0x0C]) and sim.sessions == 1)
    finally:
        h.close()
    sim2 = SimDevice(malfunction=True, refuse_cmd9=True); h, port = sim2.serve()
    try:
        prt2 = HartIpPort({**EXAMPLE_CONFIG, 'transport': 'tcp', 'host': '127.0.0.1', 'port': port})
        prt2.run(polls=1)
        q = {r.tag_id: (r.quality_flag, r.rule_fired) for r in prt2.records}
        out['status_named'] = q['DHP_psi'] == ('BAD', 'DEVICE_MALFUNCTION') and q['Q_bpm'][0] == 'GAP' and 'command not implemented' in q['Q_bpm'][1]
    finally:
        h.close()
    # a unit the device sends in bar, the mapping asks in psi: converted
    sim3 = SimDevice(dvs={5: (7, 2.0)}); h, port = sim3.serve()
    try:
        prt3 = HartIpPort({**EXAMPLE_CONFIG, 'transport': 'tcp', 'host': '127.0.0.1', 'port': port,
                           'mapping': [{'address': 'dv:5', 'tag_id': 'P2_psi', 'unit': 'psi'}]})
        prt3.run(polls=1)
        out['unit_converted'] = abs(prt3.records[-1].value - 2.0 * 14.503773773) < 1e-6 and prt3.records[-1].unit == 'psi'
    finally:
        h.close()
    out['ok'] = all(out.values())
    return out
