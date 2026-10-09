# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""etp - the live-port leg on the streaming protocol the newer rigs speak: Energistics ETP v1.2 over WebSocket.

The WITSML port of v0.4.0 polls a 1.4.1.1 SOAP store: GetFromStore every few seconds with a start index,
parse the log XML, carry the rows forward. The rigs and the data platforms commissioned since have moved to
WITSML 2.x, and WITSML 2.x has no SOAP: the transport is ETP, a WebSocket session carrying Avro-encoded
messages, and the real-time path is Protocol 21 (ChannelSubscribe) - the customer asks a store for the
metadata of the channels it names, subscribes, and the store pushes every new index value as it is written.
This module is that customer, built to the specification's own schemas:

- the Avro binary codec, driven by the message schemas of ETP v1.2 as published (the twenty-three this
  port needs, in `etp12_schemas.json`; the ETP v1.2 Avro schemas are Energistics' specification, taken
  from the Geosiris `etptypes` 1.2.0 distribution, Apache-2.0); zig-zag varints, strings, bytes, fixed,
  enums, arrays and maps with their block counts, unions by branch index, records field by field;
- the WebSocket client (RFC 6455: the handshake with `Sec-WebSocket-Protocol: etp12.energistics.org`,
  `etp-encoding: binary` and the payload-size limits; masked binary frames; fragments reassembled;
  ping answered; close answered), and the minimal server the simulator stands on;
- the session: RequestSession with the customer roles for Discovery (3), Store (4) and ChannelSubscribe
  (21), OpenSession read for what the store supports; client message ids even from 2, server odd from 1,
  as both reference implementations assign them; every ProtocolException carried to the caller with its
  code and message; CloseSession on the way out;
- the tap: GetChannelMetadata on the channel URIs the operator declared, SubscribeChannels with the
  latest index requested, then ChannelData as it arrives, each DataItem a SampleRecord under the declared
  tag mapping with the store's index as the source time (DateTime indexes are microseconds since the epoch
  in ETP v1.2; an ElapsedTime index is seconds from the channel's declared start; a depth index is carried
  as the record's depth and the arrival time stands as its time, which the record says);
- a simulator store for the gate and for a site with no rig yet: two channels of a synthetic disposal
  well at one hertz, every message encoded and decoded through the same codec the tap uses.

What this module will not call a measurement:

- a time for a depth-indexed sample: the arrival time is written and the record says so;
- a tag it was not told: a channel the mapping does not name is counted and left out, as every port here
  does;
- a unit it did not read: the store's uom on the channel metadata is carried, and converted only when the
  mapping declares a target unit;
- a value from a message it could not decode: the message is counted as unreadable with its header, and
  the stream carries on.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .live_ports import Recording, load_mappings, make_record, records_to_stream, utc_now, iso
from .ports import LiveStream
from .sample_record import SampleRecord

ETP_SUBPROTOCOL = 'etp12.energistics.org'
ETP_VERSION = (1, 2, 0, 0)
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'etp12_schemas.json')
# message flags (ETP v1.2 MessageHeader.messageFlags)
FLAG_MULTIPART, FLAG_FINAL, FLAG_NO_DATA, FLAG_COMPRESSED, FLAG_ACK, FLAG_EXTENSION = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
DEFAULT_MAX_FRAME = 1 << 20
DEFAULT_MAX_MESSAGE = 16 << 20

EXAMPLE_CONFIG = {
    'url': 'ws://127.0.0.1:9800/',
    'auth': {'bearer_env': 'GEA_ETP_TOKEN'},
    'application': 'GEA-Program', 'timeout_s': 30.0, 'keep_unmapped': True, 'latest_index_count': 1,
    'channels': [
        {'uri': 'eml:///witsml20.Well(w-1)/witsml20.Wellbore(wb-1)/witsml20.Log(log-1)/witsml20.ChannelSet(cs-1)/witsml20.Channel(P_surf)',
         'tag_id': 'P_surf_psi', 'unit': 'psi', 'tag_class': 'pressure', 'scale': 1.0, 'description': 'surface injection pressure'},
        {'uri': 'eml:///witsml20.Well(w-1)/witsml20.Wellbore(wb-1)/witsml20.Log(log-1)/witsml20.ChannelSet(cs-1)/witsml20.Channel(Q)',
         'tag_id': 'Q_bpm', 'unit': 'bbl/min', 'tag_class': 'rate', 'scale': 1.0, 'description': 'injection rate'},
    ],
}


def write_example_config(path) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE_CONFIG, f, indent=1)
    return str(path)


def load_config(config) -> dict:
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    d = json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith('_')}))
    if not d.get('url'):
        raise ValueError("etp config needs 'url' (ws:// or wss://)")
    u = urlsplit(str(d['url']))
    if u.scheme not in ('ws', 'wss'):
        raise ValueError("etp config 'url' must be ws:// or wss://")
    if not d.get('channels'):
        raise ValueError("etp config needs 'channels': one entry per channel URI with its tag_id and unit")
    for c in d['channels']:
        if not c.get('uri') or not c.get('tag_id'):
            raise ValueError("every etp channel needs 'uri' and 'tag_id'")
    d['_mappings'] = load_mappings(d['channels'], 'uri')
    return d


# ==============================================================================================================
# Avro binary, driven by the schema JSON
# ==============================================================================================================
class Avro:
    """The binary encoding of Apache Avro for the schema shapes ETP uses. Named types are registered once
    and referenced by name afterwards, as the schemas do."""

    PRIMITIVES = ('null', 'boolean', 'int', 'long', 'float', 'double', 'string', 'bytes')

    def __init__(self, schemas: Dict[str, dict]):
        self.named: Dict[str, dict] = {}
        for s in schemas.values():
            self._register(s)
        self.messages: Dict[Tuple[int, int], dict] = {}
        self.by_name: Dict[str, dict] = {}
        for s in schemas.values():
            if s.get('protocol') is not None and s.get('messageType') is not None:
                self.messages[(int(s['protocol']), int(s['messageType']))] = s
            self.by_name[s['fullName']] = s
            self.by_name[s['name']] = s

    def _register(self, t: Any) -> None:
        if isinstance(t, dict):
            k = t.get('type')
            if k in ('record', 'enum', 'fixed'):
                fn = t.get('fullName') or (t.get('namespace', '') + '.' + t['name'] if t.get('namespace') else t['name'])
                self.named.setdefault(fn, t)
                self.named.setdefault(t['name'], t)
            if k == 'record':
                for f in t['fields']:
                    self._register(f['type'])
            elif k == 'array':
                self._register(t['items'])
            elif k == 'map':
                self._register(t['values'])
        elif isinstance(t, list):
            for x in t:
                self._register(x)

    def resolve(self, t: Any) -> Any:
        if isinstance(t, str) and t not in self.PRIMITIVES:
            if t not in self.named:
                raise ValueError(f'unknown Avro type {t!r}')
            return self.named[t]
        return t

    # -- primitives -------------------------------------------------------------------------------------------
    @staticmethod
    def _zigzag(n: int) -> bytes:
        n = (n << 1) ^ (n >> 63)
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    @staticmethod
    def _unzigzag(buf: bytes, pos: int) -> Tuple[int, int]:
        shift, n = 0, 0
        while True:
            if pos >= len(buf):
                raise ValueError('truncated varint')
            b = buf[pos]; pos += 1
            n |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return (n >> 1) ^ -(n & 1), pos

    # -- encode -----------------------------------------------------------------------------------------------
    def encode(self, t: Any, v: Any, out: Optional[bytearray] = None) -> bytes:
        out = bytearray() if out is None else out
        t = self.resolve(t)
        if isinstance(t, list):                                   # union
            idx, branch = self._pick_branch(t, v)
            out += self._zigzag(idx)
            self.encode(branch, v if not (isinstance(v, dict) and '$type' in v) else {k: x for k, x in v.items() if k != '$type'}, out)
            return bytes(out)
        k = t if isinstance(t, str) else t['type']
        if k == 'null':
            pass
        elif k == 'boolean':
            out.append(1 if v else 0)
        elif k in ('int', 'long'):
            out += self._zigzag(int(v))
        elif k == 'float':
            out += struct.pack('<f', float(v))
        elif k == 'double':
            out += struct.pack('<d', float(v))
        elif k == 'string':
            b = str(v).encode('utf-8'); out += self._zigzag(len(b)); out += b
        elif k == 'bytes':
            b = bytes(v); out += self._zigzag(len(b)); out += b
        elif k == 'fixed':
            b = bytes(v)
            if len(b) != int(t['size']):
                raise ValueError(f"fixed {t['name']} needs {t['size']} bytes, got {len(b)}")
            out += b
        elif k == 'enum':
            syms = t['symbols']
            if v not in syms:
                raise ValueError(f"{v!r} is not a symbol of {t['name']} {syms}")
            out += self._zigzag(syms.index(v))
        elif k == 'array':
            items = list(v or [])
            if items:
                out += self._zigzag(len(items))
                for x in items:
                    self.encode(t['items'], x, out)
            out += self._zigzag(0)
        elif k == 'map':
            items = dict(v or {})
            if items:
                out += self._zigzag(len(items))
                for key, x in items.items():
                    self.encode('string', key, out); self.encode(t['values'], x, out)
            out += self._zigzag(0)
        elif k == 'record':
            v = v or {}
            for f in t['fields']:
                if f['name'] in v:
                    self.encode(f['type'], v[f['name']], out)
                elif 'default' in f:
                    self.encode(f['type'], f['default'], out)
                else:
                    raise ValueError(f"record {t['name']}: field {f['name']} has no value and no default")
        else:
            raise ValueError(f'unsupported Avro type {k!r}')
        return bytes(out)

    def _pick_branch(self, union: list, v: Any) -> Tuple[int, Any]:
        """Which branch of a union a Python value goes out as: an explicit '$type' on a dict, else the first
        branch whose primitive kind fits."""
        branches = [self.resolve(b) for b in union]
        names = [(b if isinstance(b, str) else (b.get('fullName') or b.get('name') or b['type'])) for b in branches]
        if isinstance(v, dict) and '$type' in v:
            want = v['$type']
            for i, (b, n) in enumerate(zip(branches, names)):
                if n == want or (isinstance(b, dict) and (b.get('name') == want or b.get('fullName') == want)):
                    return i, b
            raise ValueError(f'no union branch named {want!r} in {names}')
        kinds = [(b if isinstance(b, str) else b['type']) for b in branches]
        def first(*ks):
            for k in ks:
                if k in kinds:
                    return kinds.index(k)
            return None
        if v is None:
            i = first('null')
        elif isinstance(v, bool):
            i = first('boolean')
        elif isinstance(v, int):
            i = first('long', 'int', 'double', 'float')
        elif isinstance(v, float):
            i = first('double', 'float')
        elif isinstance(v, str):
            i = first('string', 'enum')
        elif isinstance(v, (bytes, bytearray)):
            i = first('bytes', 'fixed')
        elif isinstance(v, dict):
            i = first('record', 'map')
        elif isinstance(v, (list, tuple)):
            i = first('array')
        else:
            i = None
        if i is None:
            raise ValueError(f'no union branch for a value of type {type(v).__name__} in {names}')
        return i, branches[i]

    # -- decode -----------------------------------------------------------------------------------------------
    def decode(self, t: Any, buf: bytes, pos: int = 0) -> Tuple[Any, int]:
        t = self.resolve(t)
        if isinstance(t, list):
            idx, pos = self._unzigzag(buf, pos)
            if not (0 <= idx < len(t)):
                raise ValueError(f'union index {idx} out of range')
            branch = self.resolve(t[idx])
            v, pos = self.decode(branch, buf, pos)
            if isinstance(branch, dict) and branch.get('type') == 'record' and isinstance(v, dict):
                v['$type'] = branch.get('fullName') or branch['name']
            return v, pos
        k = t if isinstance(t, str) else t['type']
        if k == 'null':
            return None, pos
        if k == 'boolean':
            return buf[pos] != 0, pos + 1
        if k in ('int', 'long'):
            return self._unzigzag(buf, pos)
        if k == 'float':
            return struct.unpack('<f', buf[pos:pos + 4])[0], pos + 4
        if k == 'double':
            return struct.unpack('<d', buf[pos:pos + 8])[0], pos + 8
        if k in ('string', 'bytes'):
            n, pos = self._unzigzag(buf, pos)
            b = buf[pos:pos + n]
            if len(b) != n:
                raise ValueError('truncated string/bytes')
            return (b.decode('utf-8') if k == 'string' else bytes(b)), pos + n
        if k == 'fixed':
            n = int(t['size'])
            return bytes(buf[pos:pos + n]), pos + n
        if k == 'enum':
            i, pos = self._unzigzag(buf, pos)
            return t['symbols'][i], pos
        if k == 'array':
            out = []
            while True:
                n, pos = self._unzigzag(buf, pos)
                if n == 0:
                    break
                if n < 0:                                       # a block with a byte size follows
                    n = -n; _, pos = self._unzigzag(buf, pos)
                for _ in range(n):
                    x, pos = self.decode(t['items'], buf, pos); out.append(x)
            return out, pos
        if k == 'map':
            out = {}
            while True:
                n, pos = self._unzigzag(buf, pos)
                if n == 0:
                    break
                if n < 0:
                    n = -n; _, pos = self._unzigzag(buf, pos)
                for _ in range(n):
                    key, pos = self.decode('string', buf, pos); x, pos = self.decode(t['values'], buf, pos); out[key] = x
            return out, pos
        if k == 'record':
            out = {}
            for f in t['fields']:
                out[f['name']], pos = self.decode(f['type'], buf, pos)
            return out, pos
        raise ValueError(f'unsupported Avro type {k!r}')


_AVRO: Optional[Avro] = None


def avro() -> Avro:
    global _AVRO
    if _AVRO is None:
        with open(SCHEMA_FILE, encoding='utf-8') as f:
            _AVRO = Avro(json.load(f))
    return _AVRO


HEADER = 'Energistics.Etp.v12.Datatypes.MessageHeader'


def encode_message(protocol: int, message_type: int, body: dict, message_id: int, correlation_id: int = 0, flags: int = 0) -> bytes:
    a = avro()
    schema = a.messages.get((protocol, message_type))
    if schema is None:
        raise ValueError(f'no schema for protocol {protocol} message {message_type}')
    hdr = a.encode(HEADER, {'protocol': protocol, 'messageType': message_type, 'correlationId': correlation_id, 'messageId': message_id, 'messageFlags': flags})
    return hdr + a.encode(schema, body)


def decode_message(buf: bytes) -> Tuple[dict, Optional[dict], Optional[str]]:
    """The header always; the body when this port knows the schema; else the body left undecoded and named."""
    a = avro()
    hdr, pos = a.decode(HEADER, buf, 0)
    if hdr['messageFlags'] & FLAG_EXTENSION:
        ext_schema = a.by_name.get('MessageHeaderExtension')
        if ext_schema is not None:
            _, pos = a.decode(ext_schema, buf, pos)
        else:                                                   # not carried by this port's schema set: the body cannot be placed
            return hdr, None, 'message carries a header extension this port does not read'
    schema = a.messages.get((hdr['protocol'], hdr['messageType']))
    if schema is None:
        return hdr, None, f"no schema for protocol {hdr['protocol']} message {hdr['messageType']}"
    body, _ = a.decode(schema, buf, pos)
    return hdr, body, schema['fullName'].rsplit('.', 1)[1]


# ==============================================================================================================
# WebSocket (RFC 6455), the part of it ETP uses
# ==============================================================================================================
_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class WebSocket:
    """One WebSocket over one socket: frames in and out, masking on the client side, fragments reassembled,
    ping answered. Binary frames carry the ETP messages."""

    def __init__(self, sock: socket.socket, client: bool, max_message: int = DEFAULT_MAX_MESSAGE):
        self.sock = sock
        self.client = client
        self.max_message = max_message
        self.closed = False
        self._lock = threading.Lock()
        self._buf = b''

    # -- frames out ----------------------------------------------------------------------------------------------
    def send(self, payload: bytes, opcode: int = 0x2) -> None:
        n = len(payload)
        head = bytearray([0x80 | opcode])
        mask_bit = 0x80 if self.client else 0
        if n < 126:
            head.append(mask_bit | n)
        elif n < 65536:
            head.append(mask_bit | 126); head += struct.pack('>H', n)
        else:
            head.append(mask_bit | 127); head += struct.pack('>Q', n)
        if self.client:
            mask = os.urandom(4)
            head += mask
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if n < 65536 else _mask_fast(payload, mask)
        with self._lock:
            self.sock.sendall(bytes(head) + payload)

    def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                self.send(struct.pack('>H', code), 0x8)
            except OSError:
                pass
            self.closed = True

    # -- frames in -----------------------------------------------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError('the WebSocket closed')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self) -> Tuple[int, bytes]:
        """The next complete message: (opcode, payload). Control frames are handled here; a close returns (8, payload)."""
        message = bytearray(); opcode_msg = None
        while True:
            b0, b1 = self._read_exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            masked, n = b1 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read_exact(8))[0]
            if n > self.max_message:
                raise ValueError(f'a WebSocket frame of {n} bytes exceeds the message limit {self.max_message}')
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(n)
            if mask:
                payload = _mask_fast(payload, mask)
            if opcode == 0x8:
                self.closed = True
                return 8, payload
            if opcode == 0x9:                                   # ping -> pong
                self.send(payload, 0xA); continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                opcode_msg = opcode; message = bytearray(payload)
            elif opcode == 0x0:
                message += payload
            if fin:
                return opcode_msg or 0x2, bytes(message)
            if len(message) > self.max_message:
                raise ValueError('a fragmented WebSocket message exceeds the message limit')


def _mask_fast(payload: bytes, mask: bytes) -> bytes:
    import numpy as np
    a = np.frombuffer(payload, dtype=np.uint8)
    m = np.tile(np.frombuffer(mask, dtype=np.uint8), len(a) // 4 + 1)[:len(a)]
    return (a ^ m).tobytes()


def ws_connect(url: str, headers: Optional[Dict[str, str]] = None, timeout_s: float = 30.0, max_frame: int = DEFAULT_MAX_FRAME,
               max_message: int = DEFAULT_MAX_MESSAGE) -> Tuple[WebSocket, Dict[str, str]]:
    """The client handshake with the ETP headers; returns the socket and the server's response headers."""
    u = urlsplit(url)
    host, port = u.hostname, u.port or (443 if u.scheme == 'wss' else 80)
    path = (u.path or '/') + (('?' + u.query) if u.query else '')
    raw = socket.create_connection((host, port), timeout=timeout_s)
    if u.scheme == 'wss':
        raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    hdr = {'Host': f'{host}:{port}', 'Upgrade': 'websocket', 'Connection': 'Upgrade', 'Sec-WebSocket-Key': key, 'Sec-WebSocket-Version': '13',
           'Sec-WebSocket-Protocol': ETP_SUBPROTOCOL, 'etp-encoding': 'binary',
           'MaxWebSocketFramePayloadSize': str(max_frame), 'MaxWebSocketMessagePayloadSize': str(max_message)}
    hdr.update(headers or {})
    req = f'GET {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in hdr.items()) + '\r\n'
    raw.sendall(req.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = raw.recv(4096)
        if not chunk:
            raise ConnectionError('the server closed during the WebSocket handshake')
        buf += chunk
        if len(buf) > 65536:
            raise ConnectionError('the WebSocket handshake response is not HTTP')
    head, rest = buf.split(b'\r\n\r\n', 1)
    lines = head.decode('latin-1').split('\r\n')
    status = lines[0]
    resp = {k.strip().lower(): v.strip() for k, v in (ln.split(':', 1) for ln in lines[1:] if ':' in ln)}
    if ' 101 ' not in status:
        raise ConnectionError(f'the server refused the WebSocket upgrade: {status}')
    want = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
    if resp.get('sec-websocket-accept') != want:
        raise ConnectionError('the WebSocket accept key does not match')
    if resp.get('sec-websocket-protocol', '') != ETP_SUBPROTOCOL:
        raise ConnectionError(f"the server did not accept the {ETP_SUBPROTOCOL} subprotocol (got {resp.get('sec-websocket-protocol')!r})")
    ws = WebSocket(raw, client=True, max_message=max_message)
    ws._buf = rest
    return ws, resp


def ws_accept(conn: socket.socket, max_message: int = DEFAULT_MAX_MESSAGE) -> Tuple[WebSocket, Dict[str, str]]:
    """The server side of the handshake, as the simulator uses it: the subprotocol must be ETP's."""
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError('the client closed during the handshake')
        buf += chunk
    head, rest = buf.split(b'\r\n\r\n', 1)
    lines = head.decode('latin-1').split('\r\n')
    req = {k.strip().lower(): v.strip() for k, v in (ln.split(':', 1) for ln in lines[1:] if ':' in ln)}
    key = req.get('sec-websocket-key')
    protos = [p.strip() for p in req.get('sec-websocket-protocol', '').split(',') if p.strip()]
    if not key or ETP_SUBPROTOCOL not in protos:
        conn.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
        raise ConnectionError(f'not an ETP client: subprotocol {protos}')
    if req.get('etp-encoding', 'binary').lower() != 'binary':
        conn.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
        raise ConnectionError('this store speaks binary ETP only')
    accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
    conn.sendall((f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n'
                  f'Sec-WebSocket-Protocol: {ETP_SUBPROTOCOL}\r\netp-encoding: binary\r\n\r\n').encode())
    ws = WebSocket(conn, client=False, max_message=max_message)
    ws._buf = rest
    return ws, req


# ==============================================================================================================
# the session (customer side)
# ==============================================================================================================
class EtpError(Exception):
    def __init__(self, code: int, message: str, protocol: int = 0):
        super().__init__(f'ETP {code}: {message}')
        self.code, self.message, self.protocol = code, message, protocol


def now_us() -> int:
    return int(time.time() * 1_000_000)


class EtpSession:
    """A customer's session with a store over one WebSocket."""

    def __init__(self, url: str, application: str = 'GEA-Program', version: str = '', headers: Optional[Dict[str, str]] = None,
                 timeout_s: float = 30.0, recording: Optional[Recording] = None):
        self.url, self.application, self.version = url, application, version
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self.ws: Optional[WebSocket] = None
        self.server: Dict[str, Any] = {}
        self._next_id = 2                                        # the client's ids are even from 2; the store's odd from 1
        self._lock = threading.Lock()
        self.recording = recording or Recording(None)
        self.counts = {'sent': 0, 'received': 0, 'unreadable': 0, 'exceptions': 0}
        self.store_ids: List[int] = []                           # the last message ids the store used (its are odd)
        self.client_instance_id = uuid.uuid4().bytes

    def _id(self) -> int:
        with self._lock:
            i = self._next_id; self._next_id += 2
            return i

    def send(self, protocol: int, message_type: int, body: dict, correlation_id: int = 0, flags: int = 0) -> int:
        mid = self._id()
        self.ws.send(encode_message(protocol, message_type, body, mid, correlation_id, flags))
        self.counts['sent'] += 1
        return mid

    def recv(self) -> Tuple[dict, Optional[dict], Optional[str]]:
        """The next ETP message; a ProtocolException is raised as EtpError, a CloseSession as ConnectionError."""
        while True:
            op, payload = self.ws.recv()
            if op == 8:
                raise ConnectionError('the store closed the WebSocket')
            try:
                hdr, body, name = decode_message(payload)
            except Exception as e:
                self.counts['unreadable'] += 1
                self.recording.write({'received_at': iso(utc_now()), 'unreadable': str(e), 'bytes': len(payload)})
                continue
            self.counts['received'] += 1
            self.store_ids.append(int(hdr['messageId'])); self.store_ids = self.store_ids[-50:]
            self.recording.write({'received_at': iso(utc_now()), 'header': hdr, 'name': name, 'body': body if name not in ('ChannelData',) else {'n': len(body.get('data', []))}})
            if body is None:
                self.counts['unreadable'] += 1
                continue
            if hdr['protocol'] == 0 and hdr['messageType'] == 1000:
                self.counts['exceptions'] += 1
                err = body.get('error') or next(iter((body.get('errors') or {}).values()), None) or {'code': -1, 'message': 'protocol exception without a message'}
                raise EtpError(int(err.get('code', -1)), str(err.get('message', '')), hdr['protocol'])
            if hdr['protocol'] == 0 and hdr['messageType'] == 8:       # Ping -> Pong
                self.send(0, 9, {'currentDateTime': now_us()}, correlation_id=hdr['messageId'])
                continue
            if hdr['protocol'] == 0 and hdr['messageType'] == 5:       # CloseSession
                raise ConnectionError(f"the store closed the session: {body.get('reason')}")
            return hdr, body, name

    def open(self) -> dict:
        self.ws, resp = ws_connect(self.url, self.headers, self.timeout_s)
        self.ws.sock.settimeout(self.timeout_s)
        req = {'applicationName': self.application, 'applicationVersion': self.version or '0', 'clientInstanceId': self.client_instance_id,
               'requestedProtocols': [{'protocol': 3, 'protocolVersion': {'major': 1, 'minor': 2, 'revision': 0, 'patch': 0}, 'role': 'store', 'protocolCapabilities': {}},
                                      {'protocol': 4, 'protocolVersion': {'major': 1, 'minor': 2, 'revision': 0, 'patch': 0}, 'role': 'store', 'protocolCapabilities': {}},
                                      {'protocol': 21, 'protocolVersion': {'major': 1, 'minor': 2, 'revision': 0, 'patch': 0}, 'role': 'store', 'protocolCapabilities': {}}],
               'supportedDataObjects': [{'qualifiedType': 'witsml20.*', 'dataObjectCapabilities': {}}, {'qualifiedType': 'witsml21.*', 'dataObjectCapabilities': {}}],
               'supportedCompression': [], 'supportedFormats': ['xml'], 'currentDateTime': now_us(), 'earliestRetainedChangeTime': 0,
               'serverAuthorizationRequired': False, 'endpointCapabilities': {}}
        self.send(0, 1, req)
        hdr, body, name = self.recv()
        if name != 'OpenSession':
            raise ConnectionError(f'expected OpenSession, got {name}')
        self.server = {'application': body.get('applicationName'), 'version': body.get('applicationVersion'),
                       'session_id': uuid.UUID(bytes=body['sessionId']).hex if body.get('sessionId') else None,
                       'protocols': sorted(int(p['protocol']) for p in body.get('supportedProtocols') or []),
                       'data_objects': [d['qualifiedType'] for d in body.get('supportedDataObjects') or []],
                       'formats': body.get('supportedFormats'), 'handshake': resp}
        missing = [p for p in (21,) if p not in self.server['protocols']]
        if missing:
            raise ConnectionError(f"the store does not offer protocol {missing}: {self.server['protocols']}")
        return self.server

    def close(self, reason: str = 'done') -> None:
        try:
            if self.ws and not self.ws.closed:
                self.send(0, 5, {'reason': reason})
                self.ws.close()
        except Exception:
            pass
        try:
            if self.ws:
                self.ws.sock.close()
        except Exception:
            pass

    # -- protocol 21 ---------------------------------------------------------------------------------------------
    def channel_metadata(self, uris: List[str]) -> Dict[str, dict]:
        mid = self.send(21, 1, {'uris': {f'c{i}': u for i, u in enumerate(uris)}})
        out: Dict[str, dict] = {}
        while True:
            hdr, body, name = self.recv()
            if name == 'GetChannelMetadataResponse' and hdr['correlationId'] == mid:
                for k, m in (body.get('metadata') or {}).items():
                    out[m['uri']] = m
                if (hdr['messageFlags'] & FLAG_FINAL) or not (hdr['messageFlags'] & FLAG_MULTIPART):
                    return out
            # anything else while waiting is dropped: the stream has not been asked for yet

    def subscribe(self, channel_ids: List[int], latest_index_count: int = 1) -> dict:
        subs = {f's{i}': {'channelId': cid, 'startIndex': {'item': None}, 'dataChanges': True, 'requestLatestIndexCount': latest_index_count}
                for i, cid in enumerate(channel_ids)}
        mid = self.send(21, 3, {'channels': subs})
        while True:
            hdr, body, name = self.recv()
            if name == 'SubscribeChannelsResponse' and hdr['correlationId'] == mid:
                return body


# ==============================================================================================================
# the tap
# ==============================================================================================================
def index_time(index_meta: dict, value: Any, start: Optional[datetime]) -> Tuple[Optional[datetime], Optional[float], str]:
    """A ChannelData index into a source time: DateTime is microseconds since the epoch; ElapsedTime is seconds
    from the channel's start when one was declared; a depth is carried as depth and has no time."""
    kind = (index_meta or {}).get('indexKind', 'DateTime')
    if value is None:
        return None, None, 'no index'
    if kind == 'DateTime':
        return datetime.fromtimestamp(int(value) / 1_000_000.0, tz=timezone.utc), None, 'DateTime'
    if kind == 'ElapsedTime':
        if start is None:
            return None, None, 'ElapsedTime without a declared start'
        return start + timedelta(seconds=float(value)), None, 'ElapsedTime'
    if kind in ('MeasuredDepth', 'TrueVerticalDepth'):
        return None, float(value), kind
    return None, None, kind


class EtpTap:
    def __init__(self, config, recording_path: Optional[str] = None):
        self.cfg = load_config(config)
        auth = self.cfg.get('auth') or {}
        headers = {}
        tok = os.environ.get(auth.get('bearer_env', ''), '') if auth.get('bearer_env') else ''
        if tok:
            headers['Authorization'] = 'Bearer ' + tok
        self.session = EtpSession(self.cfg['url'], self.cfg.get('application', 'GEA-Program'), headers=headers,
                                  timeout_s=float(self.cfg.get('timeout_s', 30.0)), recording=Recording(recording_path))
        self.records: List[SampleRecord] = []
        self.metadata: Dict[int, dict] = {}
        self.unmapped: Dict[str, int] = {}
        self.stop_event = threading.Event()
        self.state: Dict[str, Any] = {'status': 'STOPPED'}
        from . import __version__
        self.session.version = __version__

    def connect(self) -> dict:
        srv = self.session.open()
        uris = [c['uri'] for c in self.cfg['channels']]
        meta = self.session.channel_metadata(uris)
        missing = [u for u in uris if u not in meta]
        if not meta:
            raise ConnectionError(f'the store returned no metadata for the {len(uris)} channel URI(s) declared')
        self.metadata = {int(m['id']): m for m in meta.values()}
        ids = [int(m['id']) for m in meta.values()]
        sub = self.session.subscribe(ids, int(self.cfg.get('latest_index_count', 1)))
        self.state = {'status': 'CONNECTED', 'store': srv, 'channels': len(ids), 'missing_uris': missing, 'subscribed': sub}
        return self.state

    def _records_from(self, body: dict, recv: datetime) -> List[SampleRecord]:
        out = []
        for item in body.get('data') or []:
            m = self.metadata.get(int(item['channelId']))
            if m is None:
                self.unmapped[f"id:{item['channelId']}"] = self.unmapped.get(f"id:{item['channelId']}", 0) + 1
                continue
            mp = self.cfg['_mappings'].get(m['uri'])
            if mp is None:
                self.unmapped[m['uri']] = self.unmapped.get(m['uri'], 0) + 1
                continue
            idx_meta = (m.get('indexes') or [{}])[0]
            idx_vals = item.get('indexes') or []
            iv = idx_vals[0].get('item') if idx_vals else None
            t, depth, kind = index_time(idx_meta, iv, None)
            raw = (item.get('value') or {}).get('item')
            if isinstance(raw, dict):                            # an array value: not a scalar sample; the first element stands, the rest is named
                vals = raw.get('values')
                raw = vals[0] if vals else None
            v = None if raw is None else (float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else raw)
            quality, rule = ('GOOD', '') if v is not None and not isinstance(v, str) else ('BAD', 'no numeric value')
            if t is None:
                rule = (rule + '; ' if rule else '') + f'{kind}: the arrival time stands as the record time'
            if depth is not None:
                rule = (rule + '; ' if rule else '') + f'depth index {depth:g}'
            out.append(make_record(mp, v, t, quality, rule, recv))
        return out

    def run(self, duration_s: Optional[float] = 30.0, on_record: Optional[Callable[[SampleRecord], None]] = None) -> List[SampleRecord]:
        if self.state.get('status') != 'CONNECTED':
            self.connect()
        got: List[SampleRecord] = []
        t_end = None if duration_s is None else time.time() + float(duration_s)
        self.session.ws.sock.settimeout(1.0)
        try:
            while not self.stop_event.is_set() and (t_end is None or time.time() < t_end):
                try:
                    hdr, body, name = self.session.recv()
                except socket.timeout:
                    continue
                except ConnectionError as e:
                    if got:                                      # the store closed after the record: what arrived stands
                        self.state['status'] = 'STOPPED'; self.state['stopped_by_store'] = str(e)
                        break
                    raise
                if name == 'ChannelData':
                    recs = self._records_from(body, utc_now())
                    for r in recs:
                        if on_record:
                            on_record(r)
                    got.extend(recs); self.records.extend(recs)
                elif name == 'SubscriptionsStopped':
                    self.state['status'] = 'STOPPED'; self.state['stopped_by_store'] = body.get('reason')
                    break                                        # the store ended the stream: the caller sees the state and decides
        finally:
            if self.stop_event.is_set() or (t_end is not None):
                self.session.close('tap done')
                self.state['status'] = 'STOPPED'
        return got

    def stop(self) -> None:
        self.stop_event.set()

    def to_stream(self, name: str = 'etp') -> LiveStream:
        return records_to_stream(self.records, name=name, source_format='etp', meta={'url': self.cfg['url']})


def replay(config, recording_path: str) -> List[SampleRecord]:
    """A recording holds the decoded messages as received; the ChannelData entries in it carry only their counts
    (the samples went to the records CSV), so a replay reproduces the session's shape - what was asked, what the
    store answered, what it refused - and names that it does not re-create samples."""
    cfg = load_config(config)
    out: List[SampleRecord] = []
    n = {'messages': 0, 'channel_data': 0, 'exceptions': 0, 'unreadable': 0}
    for msg in Recording.read(recording_path):
        n['messages'] += 1
        if msg.get('unreadable'):
            n['unreadable'] += 1
        elif msg.get('name') == 'ChannelData':
            n['channel_data'] += 1
        elif msg.get('name') == 'ProtocolException':
            n['exceptions'] += 1
    replay.summary = n       # type: ignore[attr-defined]
    return out


def read_etp(config, duration_s: float = 10.0) -> LiveStream:
    tap = EtpTap(config)
    tap.run(duration_s)
    return tap.to_stream()


# ==============================================================================================================
# the simulator store
# ==============================================================================================================
def simulated_values(k: int, seed: int = 1) -> Dict[str, float]:
    import math
    import random
    rng = random.Random(seed * 100003 + k)
    return {'P_surf': 1500.0 + 25.0 * math.sin(k / 60.0) + rng.gauss(0, 1.5), 'Q': 5.0 + 0.3 * math.sin(k / 45.0) + rng.gauss(0, 0.05)}


def simulate_store(port: int = 0, frames: int = 60, interval_s: float = 1.0, seed: int = 1, host: str = '127.0.0.1',
                   channel_uris: Optional[List[str]] = None, verbose: bool = False):
    """A store with two channels at one hertz, for the gate and for a site with no rig: RequestSession ->
    OpenSession, GetChannelMetadata, SubscribeChannels, then ChannelData until the frame count is reached or
    the customer closes. Returns (thread, port, stop_event)."""
    uris = channel_uris or [c['uri'] for c in EXAMPLE_CONFIG['channels']]
    names = ['P_surf', 'Q']
    uoms = ['psi', 'bbl/min']
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port)); srv.listen(2)
    actual = srv.getsockname()[1]
    stop = threading.Event()
    stats = {'sessions': 0, 'frames_sent': 0, 'messages': []}

    def serve_one(conn: socket.socket) -> None:
        conn.settimeout(10.0)
        ws, req = ws_accept(conn)
        next_id = [1]

        def send(protocol, mtype, body, corr=0, flags=0):
            mid = next_id[0]; next_id[0] += 2
            ws.send(encode_message(protocol, mtype, body, mid, corr, flags))
            return mid

        subscribed: Dict[int, int] = {}
        session_open = False
        t0 = utc_now()
        k = 0
        last_push = time.time()
        ws.sock.settimeout(0.2)
        while not stop.is_set():
            try:
                op, payload = ws.recv()
            except socket.timeout:
                op, payload = None, b''
            if op == 8:
                break
            if op is not None:
                try:
                    hdr, body, name = decode_message(payload)
                except Exception as e:
                    if verbose:
                        print('store: unreadable', e)
                    continue
                stats['messages'].append(name)
                if name == 'RequestSession':
                    want = {int(p['protocol']) for p in body.get('requestedProtocols') or []}
                    send(0, 2, {'applicationName': 'GEA simulated store', 'applicationVersion': '0', 'serverInstanceId': uuid.uuid4().bytes,
                                'supportedProtocols': [{'protocol': p, 'protocolVersion': {'major': 1, 'minor': 2, 'revision': 0, 'patch': 0}, 'role': 'customer', 'protocolCapabilities': {}}
                                                       for p in sorted(want & {3, 4, 21})],
                                'supportedDataObjects': [{'qualifiedType': 'witsml20.*', 'dataObjectCapabilities': {}}], 'supportedCompression': '',
                                'supportedFormats': ['xml'], 'currentDateTime': now_us(), 'earliestRetainedChangeTime': 0, 'sessionId': uuid.uuid4().bytes,
                                'endpointCapabilities': {}}, corr=hdr['messageId'])
                    session_open = True
                elif not session_open:
                    send(0, 1000, {'error': {'message': 'no session: send RequestSession first', 'code': 1}, 'errors': {}}, corr=hdr['messageId'])
                elif name == 'GetChannelMetadata':
                    meta = {}
                    for key, uri in (body.get('uris') or {}).items():
                        if uri in uris:
                            i = uris.index(uri)
                            meta[key] = {'uri': uri, 'id': i + 1,
                                         'indexes': [{'indexKind': 'DateTime', 'interval': {'startIndex': {'item': int(t0.timestamp() * 1e6)}, 'endIndex': {'item': None}, 'uom': 'us', 'depthDatum': ''},
                                                      'direction': 'Increasing', 'name': 'Time', 'uom': 'us', 'depthDatum': '', 'indexPropertyKindUri': 'eml:///eml21.PropertyKind(time)', 'filterable': True}],
                                         'channelName': names[i], 'dataKind': 'typeDouble', 'uom': uoms[i], 'depthDatum': '', 'channelClassUri': '',
                                         'status': 'Active', 'source': 'GEA simulated store', 'axisVectorLengths': [], 'attributeMetadata': [], 'customData': {}}
                    send(21, 2, {'metadata': meta}, corr=hdr['messageId'], flags=FLAG_MULTIPART | FLAG_FINAL)
                elif name == 'SubscribeChannels':
                    bad = {}
                    for key, s in (body.get('channels') or {}).items():
                        cid = int(s['channelId'])
                        if 1 <= cid <= len(uris):
                            subscribed[cid] = int(s.get('requestLatestIndexCount') or 0)
                        else:
                            bad[key] = {'message': f'no channel {cid}', 'code': 11}
                    send(21, 12, {'success': {k2: '' for k2 in (body.get('channels') or {}) if k2 not in bad}}, corr=hdr['messageId'])
                    if bad:
                        send(0, 1000, {'error': None, 'errors': bad}, corr=hdr['messageId'])
                elif name == 'UnsubscribeChannels':
                    for key, cid in (body.get('channelIds') or {}).items():
                        subscribed.pop(int(cid), None)
                    send(21, 8, {'reason': 'unsubscribed by the customer', 'channelIds': {}}, corr=hdr['messageId'])
                elif name == 'Ping':
                    send(0, 9, {'currentDateTime': now_us()}, corr=hdr['messageId'])
                elif name == 'CloseSession':
                    break
                else:
                    send(0, 1000, {'error': {'message': f'{name}: not a message this store answers', 'code': 2}, 'errors': {}}, corr=hdr['messageId'])
            if session_open and subscribed and time.time() - last_push >= interval_s and k < frames:
                vals = simulated_values(k, seed)
                items = [{'channelId': cid, 'indexes': [{'item': now_us()}], 'value': {'item': float(vals[names[cid - 1]])}, 'valueAttributes': []}
                         for cid in sorted(subscribed)]
                send(21, 4, {'data': items})
                stats['frames_sent'] += 1
                k += 1; last_push = time.time()
                if k >= frames:
                    send(21, 8, {'reason': 'the simulated record is over', 'channelIds': {}})
                    send(0, 5, {'reason': 'the simulated record is over'})
                    break
        try:
            ws.close()
        except Exception:
            pass
        conn.close()

    def loop():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            stats['sessions'] += 1
            try:
                serve_one(conn)
            except Exception as e:
                if verbose:
                    print('store: session ended:', e)
        srv.close()

    th = threading.Thread(target=loop, name='gea-etp-store', daemon=True)
    th.start()
    th.stats = stats           # type: ignore[attr-defined]
    return th, actual, stop


def selftest(frames: int = 5, interval_s: float = 0.2) -> dict:
    """The codec round-trips every message this port sends; the simulated store and the tap talk through a
    real WebSocket on the loopback; the records carry the store's own index as their time."""
    a = avro()
    rt = {}
    for key, body in {
        (0, 1): {'applicationName': 'x', 'applicationVersion': '1', 'clientInstanceId': b'\x01' * 16, 'requestedProtocols': [{'protocol': 21, 'protocolVersion': {'major': 1, 'minor': 2, 'revision': 0, 'patch': 0}, 'role': 'store', 'protocolCapabilities': {'MaxDataItemCount': {'item': 1000}}}],
                'supportedDataObjects': [{'qualifiedType': 'witsml20.*', 'dataObjectCapabilities': {}}], 'supportedCompression': [], 'supportedFormats': ['xml'], 'currentDateTime': 1, 'earliestRetainedChangeTime': 0, 'serverAuthorizationRequired': False, 'endpointCapabilities': {}},
        (21, 4): {'data': [{'channelId': 7, 'indexes': [{'item': 1700000000000000}], 'value': {'item': 1501.25}, 'valueAttributes': []},
                           {'channelId': 8, 'indexes': [{'item': 12.5}], 'value': {'item': 'text'}, 'valueAttributes': []},
                           {'channelId': 9, 'indexes': [{'item': None}], 'value': {'item': {'$type': 'Energistics.Etp.v12.Datatypes.ArrayOfDouble', 'values': [1.0, 2.0]}}, 'valueAttributes': []}]},
        (0, 1000): {'error': {'message': 'm', 'code': 5}, 'errors': {'k': {'message': 'n', 'code': 6}}},
        (3, 1): {'context': {'uri': 'eml:///', 'depth': 1, 'dataObjectTypes': [], 'navigableEdges': 'Primary', 'includeSecondaryTargets': False, 'includeSecondarySources': False}, 'scope': 'targets', 'countObjects': False, 'storeLastWriteFilter': None, 'activeStatusFilter': None, 'includeEdges': False},
    }.items():
        buf = encode_message(key[0], key[1], body, 2, 0, 0)
        hdr, back, name = decode_message(buf)
        rt[name] = (hdr['messageId'] == 2 and hdr['protocol'] == key[0] and hdr['messageType'] == key[1], back)
    cd = rt['ChannelData'][1]
    th, port, stop = simulate_store(port=0, frames=frames, interval_s=interval_s, seed=3)
    cfg = json.loads(json.dumps(EXAMPLE_CONFIG)); cfg['url'] = f'ws://127.0.0.1:{port}/'
    tap = EtpTap(cfg)
    st = dict(tap.connect())                                     # a copy: run() ends by marking the live state STOPPED
    got = tap.run(duration_s=frames * interval_s + 3.0)
    stop.set(); th.join(timeout=3)
    tags = sorted({r.tag_id for r in got})
    times = [r.timestamp_utc for r in got]
    # a wrong URI is named, not invented
    th2, port2, stop2 = simulate_store(port=0, frames=2, interval_s=0.1)
    cfg2 = json.loads(json.dumps(cfg)); cfg2['url'] = f'ws://127.0.0.1:{port2}/'; cfg2['channels'][1]['uri'] = 'eml:///witsml20.Channel(nope)'
    tap2 = EtpTap(cfg2); st2 = dict(tap2.connect()); tap2.run(duration_s=1.5); stop2.set(); th2.join(timeout=3)
    # a non-ETP client is refused at the handshake
    th3, port3, stop3 = simulate_store(port=0, frames=1, interval_s=0.1)
    refused = False
    try:
        s = socket.create_connection(('127.0.0.1', port3), timeout=3)
        s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\nSec-WebSocket-Version: 13\r\n\r\n')
        refused = b'400' in s.recv(200); s.close()
    finally:
        stop3.set(); th3.join(timeout=3)
    checks = {
        'codec_round_trip': all(v[0] for v in rt.values()) and rt['RequestSession'][1]['requestedProtocols'][0]['protocolCapabilities']['MaxDataItemCount']['item'] == 1000,
        'channel_data_unions': cd['data'][0]['value']['item'] == 1501.25 and cd['data'][0]['indexes'][0]['item'] == 1700000000000000 and cd['data'][1]['indexes'][0]['item'] == 12.5
                               and cd['data'][1]['value']['item'] == 'text' and cd['data'][2]['indexes'][0]['item'] is None and cd['data'][2]['value']['item']['values'] == [1.0, 2.0]
                               and cd['data'][2]['value']['item']['$type'].endswith('ArrayOfDouble'),
        'exception_round_trip': rt['ProtocolException'][1]['error']['code'] == 5 and rt['ProtocolException'][1]['errors']['k']['code'] == 6,
        'session_opened': st['status'] == 'CONNECTED' and 21 in st['store']['protocols'] and st['channels'] == 2 and st['store']['handshake'].get('sec-websocket-protocol') == ETP_SUBPROTOCOL,
        'ids_even_client_odd_store': tap.session._next_id % 2 == 0 and tap.session._next_id > 2 and bool(tap.session.store_ids) and all(i % 2 == 1 for i in tap.session.store_ids),
        'records_streamed': len(got) == 2 * frames and tags == ['P_surf_psi', 'Q_bpm'],
        'store_index_is_the_time': all(t and t.endswith('Z') for t in times) and all(r.quality_flag == 'GOOD' for r in got) and all(r.unit in ('psi', 'bbl/min') for r in got),
        'latency_measured': all(r.latency_s() is not None and 0 <= r.latency_s() < 5.0 for r in got),
        'missing_uri_named': st2['channels'] == 1 and st2['missing_uris'] == ['eml:///witsml20.Channel(nope)'],
        'non_etp_refused': refused,
        'counts': tap.session.counts['sent'] >= 3 and tap.session.counts['received'] >= frames + 2 and tap.session.counts['exceptions'] == 0,
    }
    return {'label': 'SIMULATION_SELF_TEST', 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'n_records': len(got), 'tags': tags,
            'store': st['store'], 'counts': tap.session.counts, 'frames_sent': th.stats['frames_sent'], 'store_messages': th.stats['messages']}


# --------------------------------------------------------------------------------------------------------------
# the port registry: the protocol is named at import, as every port here is
# --------------------------------------------------------------------------------------------------------------
from .ports import PORT_REGISTRY, PortSpec  # noqa: E402

PORT_REGISTRY['etp'] = PortSpec(
    name='etp', transport='Energistics ETP v1.2 over WebSocket (WITSML 2.x stores; ChannelSubscribe, protocol 21)',
    status='IMPLEMENTED_REQUIRES_SITE_CONFIG', reader=read_etp,
    detail='READ-ONLY customer: subscribes to the channel URIs the site declares and the store pushes; stdlib WebSocket and Avro; a simulated store for testing')
