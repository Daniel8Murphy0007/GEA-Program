# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""mqtt_port - the MQTT subscriber port (3.1.1 / 5.0) with Sparkplug B.

    pip install paho-mqtt        (the optional dependency; the package works
                                  fully without it, this port simply refuses;
                                  the payload decoders and replay need nothing)

Configuration is a JSON file the client owns:

    {
      "broker": {"host": "broker.site", "port": 8883, "tls": true, "ca_cert": null,
                 "client_id": "gea-edge-01", "keepalive_s": 60, "protocol": "5"},
      "auth": {"username_env": "GEA_MQTT_USER", "password_env": "GEA_MQTT_PASSWORD"},
      "qos": 1,
      "topics": [
        {"topic": "site/well12/dhp", "payload": "number", "tag_id": "P_dh_psi_F12", "unit": "psi"},
        {"topic": "site/well12/gauge", "payload": "json", "value_path": "value", "time_path": "ts",
         "time_format": "iso", "quality_path": "q", "good_values": ["GOOD", "good", 192],
         "tag_id": "T_dh_F_F12", "unit": "degF"},
        {"topic": "spBv1.0/OIL/DDATA/edge1/well12", "payload": "sparkplug_b", "metric": "DHP",
         "tag_id": "P_dh_psi_F12b", "unit": "psi", "scale": 14.503773773}
      ],
      "sparkplug_aliases": {"3": "DHP"}
    }

Payloads: `number` (plain text), `json` (dotted value/time/quality paths,
time as iso | epoch_s | epoch_ms) and `sparkplug_b`, decoded here from the
protobuf wire format without a protobuf library: Payload.timestamp/metrics/
seq and Metric name/alias/timestamp/datatype/is_null/value plus the
"Quality" property when present. Aliases resolve from NBIRTH/DBIRTH messages
seen in the session or from the config. Credentials are never in the file:
only the names of the environment variables that hold them.

Quality: Sparkplug is_null -> GAP; a "Quality" property of 192 -> GOOD, any
other value -> STALE with the value named; JSON quality outside good_values
-> STALE; an unparseable payload -> GAP with the reason. Source timestamps
from the payload when present, else arrival; ingest timestamp is arrival.

Every message can be written to a Recording (topic, base64 payload, arrival)
and replayed with `replay` - the offline path for tests and site
reproduction.

Headless-safe: numpy + stdlib; paho only inside guarded paths.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, Recording, iso, utc_now
from .ports import LiveStream, PortSpec, PORT_REGISTRY
from .sample_record import SampleRecord

PAHO_AVAILABLE = False
try:
    import paho.mqtt.client as _paho                     # noqa: F401
    PAHO_AVAILABLE = True
except ImportError:
    _paho = None

EXAMPLE_CONFIG = {
    "broker": {"host": "127.0.0.1", "port": 1883, "tls": False, "ca_cert": None, "client_id": "gea-edge-01",
               "keepalive_s": 60, "protocol": "5"},
    "auth": {"username_env": "GEA_MQTT_USER", "password_env": "GEA_MQTT_PASSWORD"},
    "qos": 1,
    "topics": [
        {"topic": "gea/well/F12/dhp_psi", "payload": "number", "tag_id": "P_raw_psi_S1", "unit": "psi", "tag_class": "downhole_pressure"},
        {"topic": "gea/well/F12/gauge", "payload": "json", "value_path": "temperature.value", "time_path": "ts",
         "time_format": "iso", "quality_path": "quality", "good_values": ["GOOD", 192], "tag_id": "T_raw_F_S1", "unit": "degF",
         "tag_class": "downhole_temperature"},
        {"topic": "spBv1.0/GEA/DDATA/edge1/F12", "payload": "sparkplug_b", "metric": "DHP", "tag_id": "P_raw_psi_S2",
         "unit": "psi", "tag_class": "downhole_pressure", "scale": 14.503773773, "description": "bar -> psi"},
    ],
    "sparkplug_aliases": {},
}

# ---------------------------------------------------------------------------
# Sparkplug B: minimal protobuf codec (wire format, no library)
# ---------------------------------------------------------------------------
SPB_DATATYPES = {1: 'Int8', 2: 'Int16', 3: 'Int32', 4: 'Int64', 5: 'UInt8', 6: 'UInt16', 7: 'UInt32', 8: 'UInt64',
                 9: 'Float', 10: 'Double', 11: 'Boolean', 12: 'String', 13: 'DateTime', 14: 'Text'}


def _read_varint(b: bytes, i: int) -> Tuple[int, int]:
    shift, out = 0, 0
    while True:
        if i >= len(b):
            raise ValueError('truncated varint')
        c = b[i]; i += 1
        out |= (c & 0x7F) << shift
        if not (c & 0x80):
            return out, i
        shift += 7


def _fields(b: bytes):
    """Yield (field_number, wire_type, value) over one protobuf message."""
    i = 0
    while i < len(b):
        key, i = _read_varint(b, i)
        fn, wt = key >> 3, key & 0x7
        if wt == 0:
            v, i = _read_varint(b, i)
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 2:
            n, i = _read_varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f'unsupported wire type {wt}')
        yield fn, wt, v


def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        c = n & 0x7F; n >>= 7
        if n:
            out.append(c | 0x80)
        else:
            out.append(c); return bytes(out)


def _key(fn: int, wt: int) -> bytes:
    return _varint((fn << 3) | wt)


def _len_field(fn: int, payload: bytes) -> bytes:
    return _key(fn, 2) + _varint(len(payload)) + payload


def _decode_property_set(b: bytes) -> Dict[str, object]:
    keys, vals = [], []
    for fn, wt, v in _fields(b):
        if fn == 1:
            keys.append(v.decode('utf-8', 'replace'))
        elif fn == 2:
            pv = None
            for f2, w2, v2 in _fields(v):
                if f2 in (3, 4):
                    pv = v2
                elif f2 == 5:
                    pv = struct.unpack('<f', v2)[0]
                elif f2 == 6:
                    pv = struct.unpack('<d', v2)[0]
                elif f2 == 7:
                    pv = bool(v2)
                elif f2 == 8:
                    pv = v2.decode('utf-8', 'replace')
            vals.append(pv)
    return dict(zip(keys, vals))


def _decode_metric(b: bytes) -> dict:
    m = {'name': None, 'alias': None, 'timestamp_ms': None, 'datatype': None, 'is_null': False, 'value': None, 'properties': {}}
    for fn, wt, v in _fields(b):
        if fn == 1:
            m['name'] = v.decode('utf-8', 'replace')
        elif fn == 2:
            m['alias'] = v
        elif fn == 3:
            m['timestamp_ms'] = v
        elif fn == 4:
            m['datatype'] = v
        elif fn == 7:
            m['is_null'] = bool(v)
        elif fn == 9:
            m['properties'] = _decode_property_set(v)
        elif fn in (10, 11):
            m['value'] = v
        elif fn == 12:
            m['value'] = struct.unpack('<f', v)[0]
        elif fn == 13:
            m['value'] = struct.unpack('<d', v)[0]
        elif fn == 14:
            m['value'] = bool(v)
        elif fn == 15:
            m['value'] = v.decode('utf-8', 'replace')
    # signed integer datatypes arrive as two's complement in the unsigned varint
    dt = m['datatype']
    if isinstance(m['value'], int) and dt in (1, 2, 3, 4):
        bits = {1: 8, 2: 16, 3: 32, 4: 64}[dt]
        if m['value'] >= 1 << (bits - 1):
            m['value'] -= 1 << bits
    return m


def decode_sparkplug_b(payload: bytes) -> dict:
    """Sparkplug B Payload -> {'timestamp_ms', 'seq', 'metrics': [...]}."""
    out = {'timestamp_ms': None, 'seq': None, 'metrics': []}
    for fn, wt, v in _fields(payload):
        if fn == 1:
            out['timestamp_ms'] = v
        elif fn == 2:
            out['metrics'].append(_decode_metric(v))
        elif fn == 3:
            out['seq'] = v
    return out


def encode_sparkplug_b(timestamp_ms: int, metrics: List[dict], seq: int = 0) -> bytes:
    """The inverse, enough to build test payloads and to talk to a real
    Sparkplug host: metric dicts with name/alias/timestamp_ms/datatype/value/
    is_null/properties(Quality)."""
    body = _key(1, 0) + _varint(int(timestamp_ms))
    for m in metrics:
        mb = b''
        if m.get('name') is not None:
            mb += _len_field(1, m['name'].encode('utf-8'))
        if m.get('alias') is not None:
            mb += _key(2, 0) + _varint(int(m['alias']))
        if m.get('timestamp_ms') is not None:
            mb += _key(3, 0) + _varint(int(m['timestamp_ms']))
        dt = int(m.get('datatype', 10))
        mb += _key(4, 0) + _varint(dt)
        if m.get('is_null'):
            mb += _key(7, 0) + _varint(1)
        elif 'value' in m and m['value'] is not None:
            v = m['value']
            if dt in (1, 2, 3, 5, 6, 7):
                mb += _key(10, 0) + _varint(int(v) & 0xFFFFFFFF if dt in (3, 7) else int(v) & ((1 << (8 if dt in (1, 5) else 16)) - 1))
            elif dt in (4, 8, 13):
                mb += _key(11, 0) + _varint(int(v))
            elif dt == 9:
                mb += _key(12, 5) + struct.pack('<f', float(v))
            elif dt == 10:
                mb += _key(13, 1) + struct.pack('<d', float(v))
            elif dt == 11:
                mb += _key(14, 0) + _varint(1 if v else 0)
            else:
                mb += _len_field(15, str(v).encode('utf-8'))
        props = m.get('properties') or {}
        if props:
            pb = b''
            for k, pv in props.items():
                pb += _len_field(1, k.encode('utf-8'))
                if isinstance(pv, bool):
                    pvb = _key(1, 0) + _varint(11) + _key(7, 0) + _varint(1 if pv else 0)
                elif isinstance(pv, int):
                    pvb = _key(1, 0) + _varint(3) + _key(3, 0) + _varint(pv)
                elif isinstance(pv, float):
                    pvb = _key(1, 0) + _varint(10) + _key(6, 1) + struct.pack('<d', pv)
                else:
                    pvb = _key(1, 0) + _varint(12) + _len_field(8, str(pv).encode('utf-8'))
                pb += _len_field(2, pvb)
            mb += _len_field(9, pb)
        body += _len_field(2, mb)
    body += _key(3, 0) + _varint(int(seq))
    return body


# ---------------------------------------------------------------------------
# Config, topic matching, decoding to records
# ---------------------------------------------------------------------------
def write_example_config(path) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE_CONFIG, f, indent=1)
    return str(path)


def _address(entry: dict) -> str:
    return f"{entry['topic']}#{entry['metric']}" if entry.get('payload') == 'sparkplug_b' else entry['topic']


def load_config(config) -> dict:
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    d = json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith('_')}))   # never mutate the caller's dict
    if not (d.get('broker') or {}).get('host'):
        raise ValueError("mqtt config needs broker.host")
    topics = d.get('topics', [])
    for t in topics:
        if 'topic' not in t:
            raise ValueError(f'topic entry needs "topic": {t}')
        t.setdefault('payload', 'number')
        if t['payload'] == 'sparkplug_b' and not t.get('metric'):
            raise ValueError(f'sparkplug_b entry needs "metric": {t}')
        t['_address'] = _address(t)
    d['_mappings'] = load_mappings([{**t, 'address': t['_address']} for t in topics], 'address')
    d['_entries'] = {t['_address']: t for t in topics}
    d['_aliases'] = {int(k): v for k, v in (d.get('sparkplug_aliases') or {}).items()}
    return d


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard match (+ single level, # multi level)."""
    p, t = pattern.split('/'), topic.split('/')
    for i, seg in enumerate(p):
        if seg == '#':
            return True
        if i >= len(t):
            return False
        if seg != '+' and seg != t[i]:
            return False
    return len(p) == len(t)


SPB_TYPES = ('NBIRTH', 'NDATA', 'DBIRTH', 'DDATA')


def spb_topic_matches(pattern: str, topic: str) -> bool:
    """Sparkplug topic match: the message-type segment (NBIRTH/NDATA/DBIRTH/
    DDATA) is interchangeable, so a BIRTH on the same edge node and device
    matches a DATA entry (BIRTH carries values and teaches aliases)."""
    p, t = pattern.split('/'), topic.split('/')
    if len(p) != len(t) or len(p) < 4:
        return topic_matches(pattern, topic)
    for i, (a, b) in enumerate(zip(p, t)):
        if i == 2 and a in SPB_TYPES and b in SPB_TYPES:
            continue
        if a != '+' and a != b:
            return False
    return True


def learn_aliases(topic: str, payload: bytes, aliases: Dict[int, str]) -> None:
    """Any Sparkplug BIRTH teaches alias -> name, whether or not a topic entry matches it."""
    parts = topic.split('/')
    if len(parts) >= 4 and parts[0].startswith('spBv1.0') and parts[2] in ('NBIRTH', 'DBIRTH'):
        try:
            for met in decode_sparkplug_b(payload)['metrics']:
                if met['name'] is not None and met['alias'] is not None:
                    aliases[int(met['alias'])] = met['name']
        except Exception:
            pass


def _dig(obj, path: str):
    cur = obj
    for part in path.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _parse_time(v, fmt: str) -> Optional[datetime]:
    if v is None:
        return None
    try:
        if fmt == 'epoch_ms':
            return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc)
        if fmt == 'epoch_s':
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        s = str(v).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def message_to_records(cfg: dict, topic: str, payload: bytes, received_at: Optional[datetime] = None,
                       alias_table: Optional[Dict[int, str]] = None) -> List[SampleRecord]:
    """One MQTT message -> zero or more records (Sparkplug carries many metrics)."""
    recv = received_at or utc_now()
    out: List[SampleRecord] = []
    aliases = alias_table if alias_table is not None else cfg['_aliases']
    learn_aliases(topic, payload, aliases)
    for addr, entry in cfg['_entries'].items():
        kind = entry['payload']
        if not (spb_topic_matches(entry['topic'], topic) if kind == 'sparkplug_b' else topic_matches(entry['topic'], topic)):
            continue
        m: TagMapping = cfg['_mappings'][addr]
        if kind == 'number':
            try:
                val = float(payload.decode('utf-8').strip())
                out.append(make_record(m, val, None, 'GOOD', '', recv))
            except (ValueError, UnicodeDecodeError) as e:
                out.append(make_record(m, None, None, 'GAP', f'unparseable number payload ({type(e).__name__})', recv))
        elif kind == 'json':
            try:
                obj = json.loads(payload.decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as e:
                out.append(make_record(m, None, None, 'GAP', f'unparseable JSON payload ({type(e).__name__})', recv))
                continue
            val = _dig(obj, entry.get('value_path', 'value'))
            ts = _parse_time(_dig(obj, entry['time_path']), entry.get('time_format', 'iso')) if entry.get('time_path') else None
            flag, rule = 'GOOD', ''
            if entry.get('quality_path'):
                q = _dig(obj, entry['quality_path'])
                good = entry.get('good_values', ['GOOD', 192, True])
                if q is not None and q not in good:
                    flag, rule = 'STALE', f'JSON quality {q!r} not in good_values'
            if val is None:
                flag, rule = 'GAP', f"no value at {entry.get('value_path', 'value')}"
            out.append(make_record(m, val, ts, flag, rule, recv))
        elif kind == 'sparkplug_b':
            try:
                pl = decode_sparkplug_b(payload)
            except Exception as e:
                out.append(make_record(m, None, None, 'GAP', f'unparseable Sparkplug B payload ({type(e).__name__}: {e})', recv))
                continue
            msg_type = topic.split('/')[2] if topic.count('/') >= 2 else ''
            for met in pl['metrics']:
                if met['name'] is not None and met['alias'] is not None:
                    aliases[int(met['alias'])] = met['name']       # BIRTH messages teach aliases
                name = met['name'] if met['name'] is not None else (aliases.get(int(met['alias'])) if met['alias'] is not None else None)
                if name != entry['metric']:
                    continue
                ts = datetime.fromtimestamp((met['timestamp_ms'] or pl['timestamp_ms'] or 0) / 1000.0, tz=timezone.utc) if (met['timestamp_ms'] or pl['timestamp_ms']) else None
                if met['is_null']:
                    out.append(make_record(m, None, ts, 'GAP', f'Sparkplug metric {name} is_null ({msg_type})', recv))
                    continue
                q = met['properties'].get('Quality') if met['properties'] else None
                if q is not None and int(q) != 192:
                    out.append(make_record(m, met['value'], ts, 'STALE', f'Sparkplug Quality property {q} (192 = good)', recv))
                else:
                    out.append(make_record(m, met['value'], ts, 'GOOD', '', recv))
    return out


# ---------------------------------------------------------------------------
# The tap (paho) and replay (no dependency)
# ---------------------------------------------------------------------------
class MqttTap:
    def __init__(self, config, recording_path: Optional[str] = None):
        if not PAHO_AVAILABLE:
            raise NotImplementedError("port 'mqtt' requires the optional dependency: pip install paho-mqtt "
                                      "(protocol code is implemented; only the dependency is missing)")
        self.cfg = load_config(config)
        self.recording = Recording(recording_path)
        self.records: List[SampleRecord] = []
        self.aliases: Dict[int, str] = dict(self.cfg['_aliases'])
        self.client = None
        self.connected = False
        self.errors: List[str] = []

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = (str(reason_code) in ('Success', '0') or getattr(reason_code, 'value', 1) == 0)
        if not self.connected:
            self.errors.append(f'connect refused: {reason_code}')
            return
        qos = int(self.cfg.get('qos', 1))
        for t in self.cfg['_entries'].values():
            client.subscribe(t['topic'], qos=qos)

    def _on_message(self, client, userdata, msg):
        recv = utc_now()
        self.recording.write({'topic': msg.topic, 'payload_b64': base64.b64encode(msg.payload).decode('ascii'),
                              'received_at': iso(recv), 'qos': msg.qos, 'retain': bool(msg.retain)})
        recs = message_to_records(self.cfg, msg.topic, msg.payload, recv, self.aliases)
        self.records.extend(recs)
        if self.on_record:
            for r in recs:
                self.on_record(r)

    def run(self, duration_s: float, on_record: Optional[Callable[[SampleRecord], None]] = None) -> List[SampleRecord]:
        import paho.mqtt.client as mqtt
        self.on_record = on_record
        b = self.cfg['broker']
        proto = mqtt.MQTTv5 if str(b.get('protocol', '5')) == '5' else mqtt.MQTTv311
        kw = {'client_id': b.get('client_id', 'gea-edge'), 'protocol': proto}
        if hasattr(mqtt, 'CallbackAPIVersion'):
            kw['callback_api_version'] = mqtt.CallbackAPIVersion.VERSION2
        self.client = mqtt.Client(**kw)
        auth = self.cfg.get('auth') or {}
        user = os.environ.get(auth.get('username_env', ''), '') if auth.get('username_env') else ''
        if user:
            self.client.username_pw_set(user, os.environ.get(auth.get('password_env', ''), ''))
        if b.get('tls'):
            self.client.tls_set(ca_certs=b.get('ca_cert') or None)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        start = len(self.records)
        try:
            self.client.connect(b['host'], int(b.get('port', 1883)), keepalive=int(b.get('keepalive_s', 60)))
        except OSError as e:
            raise ConnectionError(f"could not connect to MQTT broker {b['host']}:{b.get('port', 1883)}: {e}") from e
        self.client.loop_start()
        try:
            time.sleep(max(0.0, float(duration_s)))
        finally:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:
                pass
        if self.errors:
            raise ConnectionError('; '.join(self.errors))
        return self.records[start:]

    def to_stream(self, name: str = 'mqtt') -> LiveStream:
        return records_to_stream(self.records, name=name, source_format='mqtt', meta={'broker': self.cfg['broker']['host']})


def replay(config, recording_path: str) -> List[SampleRecord]:
    cfg = load_config(config)
    aliases = dict(cfg['_aliases'])
    out: List[SampleRecord] = []
    for msg in Recording.read(recording_path):
        recv = datetime.fromisoformat(msg['received_at'].replace('Z', '+00:00')) if msg.get('received_at') else None
        out.extend(message_to_records(cfg, msg['topic'], base64.b64decode(msg['payload_b64']), recv, aliases))
    return out


def read_mqtt(config) -> LiveStream:
    """Registry reader: config dict/path with broker + topics; optional
    'replay' (recording path, no connection), 'duration_s', 'recording'."""
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    if d.get('replay'):
        return records_to_stream(replay(d, d['replay']), name='mqtt-replay', source_format='mqtt', meta={'replay': d['replay']})
    tap = MqttTap(d, recording_path=d.get('recording'))
    tap.run(float(d.get('duration_s', 10.0)))
    return tap.to_stream()


def _refuse_no_dep(*a, **k):
    raise NotImplementedError("port 'mqtt' requires the optional dependency: pip install paho-mqtt "
                              "(protocol code is implemented; only the dependency is missing; "
                              "replay of a recording works without it via mqtt_port.replay)")


PORT_REGISTRY['mqtt'] = PortSpec(
    name='mqtt', transport='MQTT 3.1.1 / 5.0 subscriber (TLS optional); payloads number | json | Sparkplug B',
    status=('IMPLEMENTED_REQUIRES_SITE_CONFIG' if PAHO_AVAILABLE else 'DECLARED_DEPENDENCY_MISSING'),
    reader=(read_mqtt if PAHO_AVAILABLE else _refuse_no_dep),
    detail='READ-ONLY subscriber; topic map is client-supplied; Sparkplug B decoded from the wire format (no protobuf dependency), '
           'aliases learned from BIRTH; quality from Sparkplug Quality / JSON quality; recording + replay')
