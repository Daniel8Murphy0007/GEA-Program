# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""modbus_rtu - the Modbus RTU port: the serial line most wellsite gauge panels actually speak.

A surface-readout panel on a pad is, more often than not, an RS-485 drop with a Modbus RTU slave on it, not a
TCP device: the same registers as Modbus TCP, framed as the RTU specification lays them out - address, function,
data, CRC-16 (polynomial 0xA001, low byte first), frames separated by a silence of 3.5 character times. This
port frames and parses RTU itself, with no dependency: the serial line through pyserial (the `serial` extra,
already the WITS0 port's), or the same bytes over a TCP socket for the serial-to-Ethernet converters that pass
RTU through untouched ("RTU over TCP"), or an in-process stand-in slave for the gate and the site's rehearsal.

    {
      "transport": "serial" | "tcp" | "sim",
      "device": "COM3", "baudrate": 9600, "parity": "N", "stopbits": 1,      serial
      "host": "192.168.10.20", "port": 4001,                                   tcp (RTU over TCP)
      "unit_id": 1, "table": "holding" | "input", "word_order": "big" | "little",
      "polls": 10, "interval_s": 1.0, "timeout_s": 2.0,
      "mapping": [ {"address": 100, "tag_id": "DHP_psi", "type": "float32", "unit": "psi", "scale": 1.0}, ... ],
      "map_source": "the panel's register map, document and revision"
    }

READ-ONLY: functions 03 (read holding registers) and 04 (read input registers) are the only requests this port
makes; there is no code path that writes a register or a coil. The register map is the site's, with its
source, and the port refuses to run without it. Each poll's values become SampleRecords with the arrival as
both the source time (an RTU slave carries no clock) and the ingest time, so the latency is the poll's own.
An exception response (function | 0x80, exception code) is named, a CRC that does not match is named and the
frame dropped, a silent slave is a timeout with the address in it.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, utc_now
from .sample_record import SampleRecord

EXCEPTIONS = {1: 'ILLEGAL FUNCTION', 2: 'ILLEGAL DATA ADDRESS', 3: 'ILLEGAL DATA VALUE', 4: 'SLAVE DEVICE FAILURE', 5: 'ACKNOWLEDGE',
              6: 'SLAVE DEVICE BUSY', 8: 'MEMORY PARITY ERROR', 10: 'GATEWAY PATH UNAVAILABLE', 11: 'GATEWAY TARGET DEVICE FAILED TO RESPOND'}
TYPES = {'float32': 2, 'uint16': 1, 'int16': 1, 'uint32': 2, 'int32': 2, 'float64': 4}
FUNCTIONS = {'holding': 3, 'input': 4}

EXAMPLE_CONFIG = {
    'transport': 'sim', 'unit_id': 1, 'table': 'holding', 'word_order': 'big', 'polls': 5, 'interval_s': 0.2, 'timeout_s': 2.0,
    'mapping': [{'address': 100, 'tag_id': 'DHP_psi', 'type': 'float32', 'unit': 'psi', 'tag_class': 'pressure'},
                {'address': 102, 'tag_id': 'DHT_degF', 'type': 'float32', 'unit': 'degF', 'tag_class': 'temperature'},
                {'address': 104, 'tag_id': 'Q_bpm', 'type': 'int16', 'unit': 'bbl/min', 'scale': 0.01}],
    'map_source': 'EXAMPLE - the stand-in slave this port ships for rehearsal, not a device',
}


def crc16(data: bytes) -> int:
    """Modbus CRC-16: polynomial 0xA001 (reflected 0x8005), initial 0xFFFF; transmitted low byte first."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def frame(unit_id: int, pdu: bytes) -> bytes:
    body = bytes([unit_id & 0xFF]) + pdu
    return body + struct.pack('<H', crc16(body))


def check_frame(data: bytes) -> Tuple[Optional[bytes], str]:
    """A received frame: (unit + pdu without the CRC, '') when the CRC matches, else (None, why)."""
    if len(data) < 4:
        return None, f'frame too short ({len(data)} bytes)'
    body, crc = data[:-2], struct.unpack('<H', data[-2:])[0]
    if crc16(body) != crc:
        return None, f'CRC mismatch (got {crc:04x}, computed {crc16(body):04x})'
    return body, ''


def read_request(unit_id: int, table: str, address: int, count: int) -> bytes:
    if table not in FUNCTIONS:
        raise ValueError(f"table must be 'holding' or 'input', got {table!r}")
    if not 1 <= count <= 125:
        raise ValueError(f'count must be 1..125, got {count}')
    return frame(unit_id, struct.pack('>BHH', FUNCTIONS[table], address, count))


def parse_read_response(data: bytes, unit_id: int, function: int, count: int) -> Tuple[Optional[List[int]], str]:
    """The registers of a read response, or (None, why) - exception, CRC, wrong unit, wrong length each named."""
    body, why = check_frame(data)
    if body is None:
        return None, why
    if body[0] != unit_id:
        return None, f'answer from unit {body[0]}, asked unit {unit_id}'
    if body[1] == (function | 0x80):
        code = body[2] if len(body) > 2 else 0
        return None, f'exception {code} {EXCEPTIONS.get(code, "unknown")} to function {function:02d}'
    if body[1] != function:
        return None, f'function {body[1]} in the answer, asked {function}'
    n = body[2]
    if n != count * 2 or len(body) != 3 + n:
        return None, f'byte count {n} for {count} registers ({len(body) - 3} bytes present)'
    return list(struct.unpack('>' + 'H' * count, body[3:3 + n])), ''


def decode(words: List[int], rtype: str, word_order: str = 'big') -> float:
    """The mapped type from the raw 16-bit registers, in the map's word order."""
    if rtype not in TYPES:
        raise ValueError(f'unknown register type {rtype!r}')
    ws = list(words) if word_order == 'big' else list(reversed(words))
    raw = b''.join(struct.pack('>H', w) for w in ws)
    if rtype == 'uint16':
        return float(ws[0])
    if rtype == 'int16':
        return float(struct.unpack('>h', raw[:2])[0])
    if rtype == 'uint32':
        return float(struct.unpack('>I', raw[:4])[0])
    if rtype == 'int32':
        return float(struct.unpack('>i', raw[:4])[0])
    if rtype == 'float32':
        return float(struct.unpack('>f', raw[:4])[0])
    return float(struct.unpack('>d', raw[:8])[0])


def encode(value: float, rtype: str, word_order: str = 'big') -> List[int]:
    """The registers a value occupies (the stand-in slave's side of `decode`)."""
    if rtype == 'uint16':
        raw = struct.pack('>H', int(value) & 0xFFFF)
    elif rtype == 'int16':
        raw = struct.pack('>h', int(value))
    elif rtype == 'uint32':
        raw = struct.pack('>I', int(value) & 0xFFFFFFFF)
    elif rtype == 'int32':
        raw = struct.pack('>i', int(value))
    elif rtype == 'float32':
        raw = struct.pack('>f', float(value))
    else:
        raw = struct.pack('>d', float(value))
    ws = [struct.unpack('>H', raw[i:i + 2])[0] for i in range(0, len(raw), 2)]
    return ws if word_order == 'big' else list(reversed(ws))


# ---- transports: the same bytes on a serial line, a TCP socket, or an in-process slave ------------------------------------
class SerialTransport:
    """pyserial, the `serial` extra. The frame gap is the port's inter-byte timeout."""

    def __init__(self, device: str, baudrate: int = 9600, parity: str = 'N', stopbits: int = 1, timeout_s: float = 2.0):
        try:
            import serial  # type: ignore
        except ImportError:
            raise NotImplementedError("the serial transport needs pyserial: pip install \"gea-program[serial]\" (RTU over TCP and the stand-in need nothing)")
        self.ser = serial.Serial(device, baudrate=int(baudrate), bytesize=8, parity=parity, stopbits=stopbits, timeout=timeout_s,
                                 inter_byte_timeout=max(3.5 * 11.0 / float(baudrate), 0.002))
        self.name = device

    def exchange(self, req: bytes, expect: int) -> bytes:
        self.ser.reset_input_buffer()
        self.ser.write(req)
        return self.ser.read(expect)

    def close(self) -> None:
        self.ser.close()


class TcpTransport:
    """RTU over TCP: a serial-to-Ethernet converter that passes the RTU frame through untouched (not Modbus TCP's MBAP)."""

    def __init__(self, host: str, port: int = 4001, timeout_s: float = 2.0):
        self.sock = socket.create_connection((host, int(port)), timeout=timeout_s)
        self.sock.settimeout(timeout_s)
        self.name = f'{host}:{port}'

    def exchange(self, req: bytes, expect: int) -> bytes:
        self.sock.sendall(req)
        buf = b''
        while len(buf) < expect:
            try:
                chunk = self.sock.recv(expect - len(buf))
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if len(buf) >= 5 and buf[1] & 0x80:            # an exception frame is 5 bytes
                break
        return buf

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class SimSlave:
    """An in-process RTU slave with a register table: the site's rehearsal and the gate's device. `values` is
    {address: [registers]}; a request outside it answers ILLEGAL DATA ADDRESS; `corrupt_every` answers every n-th
    request with a wrong CRC, `silent` never answers - the faults a real line shows."""

    def __init__(self, unit_id: int = 1, registers: Optional[Dict[int, int]] = None, corrupt_every: int = 0, silent: bool = False):
        self.unit_id = int(unit_id)
        self.registers: Dict[int, int] = dict(registers or {})
        self.corrupt_every, self.silent, self.n = int(corrupt_every), silent, 0
        self.name = 'sim'
        self.lock = threading.Lock()

    def set(self, address: int, value: float, rtype: str = 'float32', word_order: str = 'big') -> None:
        with self.lock:
            for i, w in enumerate(encode(value, rtype, word_order)):
                self.registers[address + i] = w

    def answer(self, req: bytes) -> bytes:
        self.n += 1
        if self.silent:
            return b''
        body, why = check_frame(req)
        if body is None or body[0] != self.unit_id:
            return b''                                        # a slave does not answer a frame that is not its own
        fn, address, count = struct.unpack('>BHH', body[1:6])
        if fn not in (3, 4):
            return frame(self.unit_id, bytes([fn | 0x80, 1]))
        with self.lock:
            missing = [a for a in range(address, address + count) if a not in self.registers]
            if missing:
                return frame(self.unit_id, bytes([fn | 0x80, 2]))
            regs = [self.registers[a] for a in range(address, address + count)]
        out = frame(self.unit_id, bytes([fn, count * 2]) + struct.pack('>' + 'H' * count, *regs))
        if self.corrupt_every and self.n % self.corrupt_every == 0:
            out = out[:-1] + bytes([out[-1] ^ 0xFF])
        return out

    def exchange(self, req: bytes, expect: int) -> bytes:
        return self.answer(req)

    def close(self) -> None:
        pass

    def serve_tcp(self, host: str = '127.0.0.1', port: int = 0):
        """The same slave behind a TCP socket (RTU over TCP), for the gate's TcpTransport. Returns (server, port)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port)); srv.listen(4)
        stop = threading.Event()

        def run():
            srv.settimeout(0.5)
            while not stop.is_set():
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._serve_conn, args=(c, stop), daemon=True).start()
        th = threading.Thread(target=run, daemon=True); th.start()
        return _TcpHandle(srv, stop, th), srv.getsockname()[1]

    def _serve_conn(self, c: socket.socket, stop: threading.Event) -> None:
        c.settimeout(0.5)
        try:
            while not stop.is_set():
                try:
                    data = c.recv(260)
                except socket.timeout:
                    continue
                if not data:
                    break
                ans = self.answer(data)
                if ans:
                    c.sendall(ans)
        finally:
            c.close()


class _TcpHandle:
    def __init__(self, srv, stop, th):
        self.srv, self.stop, self.th = srv, stop, th

    def close(self) -> None:
        self.stop.set()
        try:
            self.srv.close()
        except OSError:
            pass
        self.th.join(timeout=2)


# ---- the port ------------------------------------------------------------------------------------------------------------
@dataclass
class RegisterTag:
    mapping: TagMapping
    address: int
    rtype: str

    @property
    def words(self) -> int:
        return TYPES[self.rtype]


class RtuPort:
    """READ-ONLY polling of the mapped registers over one transport; every poll becomes SampleRecords."""

    def __init__(self, config: dict, transport=None):
        self.cfg = dict(config)
        if not self.cfg.get('mapping'):
            raise ValueError('a register mapping is required - the port maps what the site declares; it never guesses registers')
        if not self.cfg.get('map_source'):
            raise ValueError('the register map needs its source (map_source: the panel document and revision) - no invented device layouts')
        self.unit_id = int(self.cfg.get('unit_id', 1))
        self.table = str(self.cfg.get('table', 'holding'))
        if self.table not in FUNCTIONS:
            raise ValueError(f"table must be 'holding' or 'input', got {self.table!r}")
        self.word_order = str(self.cfg.get('word_order', 'big'))
        maps = load_mappings(self.cfg['mapping'], 'address')
        self.tags: List[RegisterTag] = []
        for e in self.cfg['mapping']:
            rtype = str(e.get('type', 'float32'))
            if rtype not in TYPES:
                raise ValueError(f'register {e.get("address")}: unknown type {rtype!r} (one of {tuple(TYPES)})')
            self.tags.append(RegisterTag(maps[str(e['address'])], int(e['address']), rtype))
        self.transport = transport
        self.records: List[SampleRecord] = []
        self.faults: List[dict] = []
        self.polls = 0

    def open(self):
        if self.transport is not None:
            return self.transport
        t = str(self.cfg.get('transport', 'sim'))
        if t == 'serial':
            self.transport = SerialTransport(str(self.cfg['device']), int(self.cfg.get('baudrate', 9600)), str(self.cfg.get('parity', 'N')),
                                             int(self.cfg.get('stopbits', 1)), float(self.cfg.get('timeout_s', 2.0)))
        elif t == 'tcp':
            self.transport = TcpTransport(str(self.cfg['host']), int(self.cfg.get('port', 4001)), float(self.cfg.get('timeout_s', 2.0)))
        elif t == 'sim':
            sim = SimSlave(self.unit_id)
            for i, tg in enumerate(self.tags):
                sim.set(tg.address, [3185.5, 212.3, 1250.0, 7.0][i % 4], tg.rtype, self.word_order)
            self.transport = sim
        else:
            raise ValueError(f"transport must be serial, tcp or sim, got {t!r}")
        return self.transport

    def poll_once(self) -> Dict[str, Optional[float]]:
        """One read per mapped register (a panel's registers are rarely contiguous); the faults named per read."""
        tr = self.open()
        now = utc_now()
        out: Dict[str, Optional[float]] = {}
        fn = FUNCTIONS[self.table]
        for tg in self.tags:
            req = read_request(self.unit_id, self.table, tg.address, tg.words)
            ans = tr.exchange(req, 5 + 2 * tg.words)
            if not ans:
                why = f'no answer from unit {self.unit_id} at register {tg.address} ({getattr(tr, "name", "?")})'
                regs = None
            else:
                regs, why = parse_read_response(ans, self.unit_id, fn, tg.words)
            if regs is None:
                self.faults.append({'at': now.isoformat(), 'register': tg.address, 'why': why})
                self.records.append(make_record(tg.mapping, None, now, 'GAP', why, now))
                out[tg.mapping.tag_id] = None
                continue
            v = decode(regs, tg.rtype, self.word_order)
            self.records.append(make_record(tg.mapping, v, now, 'GOOD', '', now))
            out[tg.mapping.tag_id] = self.records[-1].value
        self.polls += 1
        return out

    def run(self, polls: Optional[int] = None, interval_s: Optional[float] = None) -> List[SampleRecord]:
        n = int(self.cfg.get('polls', 10) if polls is None else polls)
        dt = float(self.cfg.get('interval_s', 1.0) if interval_s is None else interval_s)
        try:
            for k in range(n):
                self.poll_once()
                if dt > 0 and k < n - 1:
                    time.sleep(dt)
        finally:
            self.close()
        return self.records

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()

    def stream(self):
        return records_to_stream(self.records, name=f"modbus_rtu_{getattr(self.transport, 'name', 'sim')}", source_format='modbus_rtu')

    def summary(self) -> dict:
        good = sum(1 for r in self.records if r.quality_flag == 'GOOD')
        return {'polls': self.polls, 'records': len(self.records), 'good': good, 'gaps': len(self.records) - good, 'faults': self.faults[-20:],
                'transport': getattr(self.transport, 'name', str(self.cfg.get('transport'))), 'unit_id': self.unit_id, 'table': self.table}


def load_config(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with Path(path_or_dict).open(encoding='utf-8') as f:
        return json.load(f)


def read_modbus_rtu(config) -> 'LiveStream':   # noqa: F821 - the registry reader
    cfg = load_config(config)
    port = RtuPort(cfg)
    port.run()
    return port.stream()


def selftest() -> dict:
    """The codec and a poll against the stand-in: CRC vectors from the specification, a round trip of every type,
    an exception, a corrupt frame, a silent slave."""
    out = {}
    out['crc_spec_vector'] = crc16(bytes.fromhex('010300000002')) == 0x0BC4      # the specification's own example: 01 03 00 00 00 02 -> C4 0B
    out['types'] = all(abs(decode(encode(v, t, o), t, o) - v) < 1e-6 for v, t in ((3185.5, 'float32'), (-7, 'int16'), (65000, 'uint16'), (-100000, 'int32'),
                                                                                  (4000000000, 'uint32'), (2.5, 'float64')) for o in ('big', 'little'))
    port = RtuPort(EXAMPLE_CONFIG)
    vals = port.poll_once()
    out['poll'] = abs(vals['DHP_psi'] - 3185.5) < 1e-3 and abs(vals['DHT_degF'] - 212.3) < 1e-3 and abs(vals['Q_bpm'] - 12.5) < 1e-6
    sim = port.transport
    exc = parse_read_response(sim.answer(read_request(1, 'holding', 900, 2)), 1, 3, 2)
    out['exception_named'] = exc[0] is None and 'ILLEGAL DATA ADDRESS' in exc[1]
    bad = SimSlave(1, corrupt_every=1); bad.set(100, 1.0)
    crc = parse_read_response(bad.answer(read_request(1, 'holding', 100, 2)), 1, 3, 2)
    out['crc_named'] = crc[0] is None and 'CRC mismatch' in crc[1]
    quiet = RtuPort({**EXAMPLE_CONFIG, 'mapping': EXAMPLE_CONFIG['mapping'][:1]}, transport=SimSlave(1, silent=True))
    quiet.poll_once()
    out['silent_named'] = quiet.records[-1].quality_flag == 'GAP' and 'no answer from unit 1 at register 100' in quiet.records[-1].rule_fired
    out['ok'] = all(out.values())
    return out


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
