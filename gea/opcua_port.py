# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""opcua_port - the OPC UA client port: read and subscribe, read-only.

    pip install asyncua          (the optional dependency; the package works
                                  fully without it, this port simply refuses)

Configuration is a JSON file the client owns:

    {
      "endpoint": "opc.tcp://host:4840/gateway/server/",
      "security": {"policy": "None", "mode": "None",
                   "certificate": null, "private_key": null},
      "auth": {"username_env": "GEA_OPCUA_USER", "password_env": "GEA_OPCUA_PASSWORD"},
      "subscription": {"publishing_interval_ms": 1000, "queue_size": 10},
      "nodes": [
        {"node_id": "ns=2;s=Well12.DHP", "tag_id": "P_dh_psi_F12", "unit": "psi",
         "tag_class": "downhole_pressure", "scale": 14.503773773, "description": "downhole gauge, bar->psi"}
      ]
    }

Credentials are never in the file: only the names of the environment
variables that hold them. Security policy Basic256Sha256 with SignAndEncrypt
is supported when a certificate and key are given.

Quality: the OPC UA StatusCode of every value maps to the record layer's
flags and the rule names the code - Good -> GOOD; Uncertain_* -> STALE
("OPC UA status Uncertain ..."); Bad_* -> GAP (value withheld). Timestamps:
the source timestamp when the server supplies it, else the server timestamp,
else arrival; the ingest timestamp is always arrival, so latency is measured.

Two ways in: `read_once` (one Read of all nodes) and `subscribe` (a
subscription with DataChange notifications for a duration). Every message
can be written to a Recording (JSON lines) and replayed with `replay`, which
is how the mapping and quality logic are tested without a server, and how
a site session is reproduced offline.

Headless-safe: numpy + stdlib; asyncua only inside guarded paths.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, Recording, iso, utc_now
from .ports import LiveStream, PortSpec, PORT_REGISTRY
from .sample_record import SampleRecord

ASYNCUA_AVAILABLE = False
try:
    from asyncua.sync import Client as _SyncClient           # noqa: F401
    ASYNCUA_AVAILABLE = True
except ImportError:
    _SyncClient = None

EXAMPLE_CONFIG = {
    "endpoint": "opc.tcp://127.0.0.1:4840/gea/example/",
    "security": {"policy": "None", "mode": "None", "certificate": None, "private_key": None},
    "auth": {"username_env": "GEA_OPCUA_USER", "password_env": "GEA_OPCUA_PASSWORD"},
    "subscription": {"publishing_interval_ms": 1000, "queue_size": 10},
    "nodes": [
        {"node_id": "ns=2;s=Well.F12.DownholePressure", "tag_id": "P_raw_psi_S1", "unit": "psi",
         "tag_class": "downhole_pressure", "scale": 1.0, "description": "downhole quartz gauge pressure"},
        {"node_id": "ns=2;s=Well.F12.DownholeTemperature", "tag_id": "T_raw_F_S1", "unit": "degF",
         "tag_class": "downhole_temperature", "scale": 1.0, "description": "downhole gauge temperature"},
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
    d = json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith('_')}))   # never mutate the caller's dict
    if not d.get('endpoint'):
        raise ValueError("opcua config needs 'endpoint'")
    d['_mappings'] = load_mappings(d.get('nodes', []), 'node_id')
    return d


# ---------------------------------------------------------------------------
# Status code -> quality flag
# ---------------------------------------------------------------------------
def status_to_quality(status_name: Optional[str], status_value: Optional[int] = None) -> tuple:
    """OPC UA StatusCode -> (flag, rule). Severity is the top two bits of the
    32-bit code: 00 Good, 01 Uncertain, 10 Bad."""
    name = status_name or ''
    if status_value is not None:
        sev = (int(status_value) >> 30) & 0x3
        if sev == 0:
            return 'GOOD', ''
        if sev == 1:
            return 'STALE', f'OPC UA status Uncertain {name or hex(status_value)}'
        return 'GAP', f'OPC UA status Bad {name or hex(status_value)}'
    if name.startswith('Good') or name == '':
        return 'GOOD', ''
    if name.startswith('Uncertain'):
        return 'STALE', f'OPC UA status {name}'
    return 'GAP', f'OPC UA status {name}'


def _dv_fields(dv) -> dict:
    """Pull what we need out of an asyncua DataValue without depending on its exact shape."""
    val = getattr(dv, 'Value', None)
    val = getattr(val, 'Value', val)
    sc = getattr(dv, 'StatusCode_', None) or getattr(dv, 'StatusCode', None)
    sc_name = getattr(sc, 'name', None) if sc is not None else None
    sc_val = getattr(sc, 'value', None) if sc is not None else None
    src = getattr(dv, 'SourceTimestamp', None)
    srv = getattr(dv, 'ServerTimestamp', None)
    return {'value': val, 'status_name': sc_name, 'status_value': sc_val,
            'source_ts': iso(src) if src else None, 'server_ts': iso(srv) if srv else None}


def message_to_record(m: TagMapping, msg: dict, received_at: Optional[datetime] = None) -> SampleRecord:
    """A recorded/received message dict -> SampleRecord."""
    flag, rule = status_to_quality(msg.get('status_name'), msg.get('status_value'))
    ts = msg.get('source_ts') or msg.get('server_ts')
    src_dt = datetime.fromisoformat(ts.replace('Z', '+00:00')) if ts else None
    recv = received_at or (datetime.fromisoformat(msg['received_at'].replace('Z', '+00:00')) if msg.get('received_at') else utc_now())
    return make_record(m, None if flag == 'GAP' else msg.get('value'), src_dt, flag, rule, recv)


# ---------------------------------------------------------------------------
# The tap
# ---------------------------------------------------------------------------
class OpcUaTap:
    """Read-only OPC UA client over asyncua's synchronous wrapper."""

    def __init__(self, config, recording_path: Optional[str] = None):
        if not ASYNCUA_AVAILABLE:
            raise NotImplementedError("port 'opcua' requires the optional dependency: pip install asyncua "
                                      "(protocol code is implemented; only the dependency is missing)")
        self.cfg = load_config(config)
        self.mappings: Dict[str, TagMapping] = self.cfg['_mappings']
        self.recording = Recording(recording_path)
        self.client = None
        self.records: List[SampleRecord] = []

    # -- connection ---------------------------------------------------------------
    def connect(self, timeout_s: float = 4.0) -> 'OpcUaTap':
        from asyncua.sync import Client
        self.client = Client(url=self.cfg['endpoint'], timeout=timeout_s)
        auth = self.cfg.get('auth') or {}
        user = os.environ.get(auth.get('username_env', ''), '') if auth.get('username_env') else ''
        pwd = os.environ.get(auth.get('password_env', ''), '') if auth.get('password_env') else ''
        if user:
            self.client.set_user(user)
            self.client.set_password(pwd)
        sec = self.cfg.get('security') or {}
        if sec.get('policy') and sec['policy'] != 'None':
            if not (sec.get('certificate') and sec.get('private_key')):
                raise ValueError('security policy set but certificate/private_key missing')
            self.client.set_security_string(f"{sec['policy']},{sec.get('mode', 'SignAndEncrypt')},{sec['certificate']},{sec['private_key']}")
        self.client.connect()
        return self

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.disconnect()
            finally:
                self.client = None

    # -- read ---------------------------------------------------------------------
    def read_once(self) -> List[SampleRecord]:
        out = []
        for node_id, m in self.mappings.items():
            node = self.client.get_node(node_id)
            dv = node.read_data_value()
            recv = utc_now()
            msg = {'node_id': node_id, 'received_at': iso(recv), **_dv_fields(dv)}
            self.recording.write(msg)
            out.append(message_to_record(m, msg, recv))
        self.records.extend(out)
        return out

    # -- subscribe --------------------------------------------------------------------
    def subscribe(self, duration_s: float, on_record: Optional[Callable[[SampleRecord], None]] = None) -> List[SampleRecord]:
        sub_cfg = self.cfg.get('subscription') or {}
        period = float(sub_cfg.get('publishing_interval_ms', 1000))
        tap = self
        got: List[SampleRecord] = []

        class _Handler:
            def datachange_notification(self, node, val, data):
                recv = utc_now()
                node_id = node.nodeid.to_string()
                m = tap.mappings.get(node_id)
                if m is None:
                    return
                dv = getattr(getattr(data, 'monitored_item', None), 'Value', None)
                fields = _dv_fields(dv) if dv is not None else {'value': val, 'status_name': None, 'status_value': None, 'source_ts': None, 'server_ts': None}
                msg = {'node_id': node_id, 'received_at': iso(recv), **fields}
                tap.recording.write(msg)
                r = message_to_record(m, msg, recv)
                got.append(r)
                if on_record:
                    on_record(r)

        sub = self.client.create_subscription(period, _Handler())
        nodes = [self.client.get_node(n) for n in self.mappings]
        handles = sub.subscribe_data_change(nodes, queuesize=int(sub_cfg.get('queue_size', 10)))
        try:
            time.sleep(max(0.0, float(duration_s)))
        finally:
            try:
                sub.unsubscribe(handles)
                sub.delete()
            except Exception:
                pass
        self.records.extend(got)
        return got

    def to_stream(self, name: str = 'opcua') -> LiveStream:
        return records_to_stream(self.records, name=name, source_format='opcua', meta={'endpoint': self.cfg['endpoint']})


# ---------------------------------------------------------------------------
# Replay (no dependency) and the registry reader
# ---------------------------------------------------------------------------
def replay(config, recording_path: str) -> List[SampleRecord]:
    """Recorded messages -> records through the same mapping and quality logic."""
    cfg = load_config(config)
    out = []
    for msg in Recording.read(recording_path):
        m = cfg['_mappings'].get(msg.get('node_id'))
        if m is None:
            continue
        out.append(message_to_record(m, msg))
    return out


def read_opcua(config) -> LiveStream:
    """Registry reader: config dict/path with endpoint + nodes; optional
    'replay' (recording path, no connection), 'mode' read|subscribe,
    'duration_s', 'recording' (path to write)."""
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    if d.get('replay'):
        recs = replay(d, d['replay'])
        return records_to_stream(recs, name='opcua-replay', source_format='opcua', meta={'replay': d['replay']})
    tap = OpcUaTap(d, recording_path=d.get('recording')).connect(float(d.get('timeout_s', 4.0)))
    try:
        if d.get('mode', 'read') == 'subscribe':
            tap.subscribe(float(d.get('duration_s', 10.0)))
        else:
            tap.read_once()
        return tap.to_stream()
    finally:
        tap.close()


def _refuse_no_dep(*a, **k):
    raise NotImplementedError("port 'opcua' requires the optional dependency: pip install asyncua "
                              "(protocol code is implemented; only the dependency is missing; "
                              "replay of a recording works without it via opcua_port.replay)")


PORT_REGISTRY['opcua'] = PortSpec(
    name='opcua', transport='OPC UA client (opc.tcp; read + subscribe; Basic256Sha256 when certificates are given)',
    status=('IMPLEMENTED_REQUIRES_SITE_CONFIG' if ASYNCUA_AVAILABLE else 'DECLARED_DEPENDENCY_MISSING'),
    reader=(read_opcua if ASYNCUA_AVAILABLE else _refuse_no_dep),
    detail='READ-ONLY; node map is client-supplied; StatusCode -> quality flag; source timestamp kept, ingest timestamp stamped; '
           'recording + replay for offline reproduction')
