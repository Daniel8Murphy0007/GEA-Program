# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""patches - the patch panel: supervised live connections that stay up.

A *patch* is a named connection from a source on the site network to a well in
the workspace: a protocol (`wits0`, `witsml`, `opcua`, `mqtt`, `modbus_g6`), the
site's map for it (a JSON file under the well's `source/` folder, versioned in
the configuration store), the well it feeds, a priority (when two patches carry
the same tag, the higher priority wins at the same instant), and a staleness
limit. The supervisor keeps every enabled patch running: it opens the source,
hands every record to the well's live record files, reconnects with exponential
backoff (2, 4, 8 ... 60 s) when the source drops, and keeps a heartbeat.

State per patch (in memory, and in `wells/<id>/records/live/patch_<name>.state.json`
every few seconds): STOPPED, CONNECTING, CONNECTED, DEGRADED (connected but no
sample within the staleness limit), DOWN (the last attempt failed; retrying),
with the time it entered that state, the last sample time, samples total and
per minute, latency p50/p95 over the last 200 samples, reconnect count, the
last error, and the last value of every tag. The page reads that state; the
alarm engine sees staleness on the records themselves.

Records land in `wells/<id>/records/live/patch_<name>_<YYYYMMDD>.records.csv`
(one row per sample, appended as they arrive); `build_live_stream` folds the
day's record files of every patch on a well into one time-indexed stream CSV
that the reports and the trend plots read. The local disk is the buffer: a
link that drops loses nothing that was already received, and the reports see
the gap as GAP, never as silence.

Headless-safe: stdlib + the port modules (each guards its own dependency).
"""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .sample_record import SampleRecord, RECORD_COLUMNS
from .live_ports import records_to_stream, iso, utc_now
from .workspace import Workspace, WorkspaceError, slug, sha256_file, utc_now_iso

PROTOCOLS = ('wits0', 'witsml', 'opcua', 'mqtt', 'modbus_g6')
STATES = ('STOPPED', 'CONNECTING', 'CONNECTED', 'DEGRADED', 'DOWN')
BACKOFF_S = (2, 4, 8, 16, 32, 60)


def _mod(protocol: str):
    name = {'wits0': 'gea.wits0', 'witsml': 'gea.witsml', 'opcua': 'gea.opcua_port', 'mqtt': 'gea.mqtt_port', 'modbus_g6': 'gea.modbus'}[protocol]
    return __import__(name, fromlist=['x'])


def validate_port_config(protocol: str, config: dict) -> None:
    """The same check the service applies on commit: the port's own loader must accept it."""
    if protocol not in PROTOCOLS:
        raise ValueError(f'protocol must be one of {PROTOCOLS}')
    mod = _mod(protocol)
    if protocol == 'modbus_g6':
        from .modbus import load_register_map
        load_register_map(config)
        return
    mod.load_config(config)


class PatchStore:
    """patches.json in the workspace."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.path = os.path.join(ws.path, 'patches.json')
        self._lock = threading.Lock()
        if not os.path.isfile(self.path):
            self._save({'patches': []})

    def _load(self) -> dict:
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def _save(self, d: dict) -> None:
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    def list(self) -> List[dict]:
        return self._load()['patches']

    def get(self, name: str) -> dict:
        for p in self.list():
            if p['name'] == name:
                return p
        raise WorkspaceError(f'no such patch: {name}')

    def add(self, name: str, protocol: str, well_id: str, config: dict, actor: str, priority: int = 1,
            stale_after_s: float = 120.0, auto_restart: bool = True, enabled: bool = True) -> dict:
        name = slug(name)
        if protocol not in PROTOCOLS:
            raise WorkspaceError(f'protocol must be one of {PROTOCOLS}')
        self.ws.well(well_id)                                       # must exist
        validate_port_config(protocol, config)
        with self._lock:
            d = self._load()
            if any(p['name'] == name for p in d['patches']):
                raise WorkspaceError(f'patch exists: {name}')
            src = self.ws.dir('wells', well_id, 'source')
            fname = f'patch_{name}.{protocol}.json'
            with open(os.path.join(src, fname), 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=1)
            from .config_versioning import ConfigStore
            ConfigStore(self.ws.config_dir).commit(f'patch.{name}', config, actor, f'{protocol} map for {well_id}')
            p = {'name': name, 'protocol': protocol, 'well_id': well_id, 'config': fname, 'priority': int(priority),
                 'stale_after_s': float(stale_after_s), 'auto_restart': bool(auto_restart), 'enabled': bool(enabled),
                 'created_by': actor, 'created_utc': utc_now_iso(), 'sha256': sha256_file(os.path.join(src, fname))}
            d['patches'].append(p)
            self._save(d)
        self.ws.audit(actor, 'patch.add', {'name': name, 'protocol': protocol, 'well': well_id, 'priority': priority}, inputs=[os.path.join(src, fname)])
        return p

    def set_config(self, name: str, config: dict, actor: str) -> dict:
        p = self.get(name)
        validate_port_config(p['protocol'], config)
        path = self.config_path(p)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=1)
        from .config_versioning import ConfigStore
        meta = ConfigStore(self.ws.config_dir).commit(f'patch.{name}', config, actor, 'patch map edited on the dashboard')
        with self._lock:
            d = self._load()
            for q in d['patches']:
                if q['name'] == name:
                    q['sha256'] = sha256_file(path)
            self._save(d)
        self.ws.audit(actor, 'patch.config', {'name': name, 'version': meta.get('version')}, inputs=[path])
        return meta

    def update(self, name: str, actor: str, **fields) -> dict:
        allowed = {'enabled', 'auto_restart', 'priority', 'stale_after_s'}
        bad = set(fields) - allowed
        if bad:
            raise WorkspaceError(f'cannot change {sorted(bad)}')
        with self._lock:
            d = self._load()
            for q in d['patches']:
                if q['name'] == name:
                    q.update(fields)
                    self._save(d)
                    self.ws.audit(actor, 'patch.update', {'name': name, **fields})
                    return q
        raise WorkspaceError(f'no such patch: {name}')

    def remove(self, name: str, actor: str) -> None:
        with self._lock:
            d = self._load()
            d['patches'] = [q for q in d['patches'] if q['name'] != name]
            self._save(d)
        self.ws.audit(actor, 'patch.remove', {'name': name})

    def config_path(self, p: dict) -> str:
        return os.path.join(self.ws.path, 'wells', p['well_id'], 'source', p['config'])

    def load_config(self, p: dict) -> dict:
        with open(self.config_path(p), encoding='utf-8') as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# The record sink: daily CSV per patch, appended as records arrive
# ---------------------------------------------------------------------------
class RecordSink:
    def __init__(self, ws: Workspace, well_id: str, patch_name: str):
        self.dir = ws.dir('wells', well_id, 'records', 'live')
        self.patch = patch_name
        self._lock = threading.Lock()
        self._day = None
        self._f = None
        self._w = None

    def _open(self, day: str) -> None:
        if self._f:
            self._f.close()
        path = os.path.join(self.dir, f'patch_{self.patch}_{day}.records.csv')
        new = not os.path.isfile(path) or os.path.getsize(path) == 0
        self._f = open(path, 'a', newline='', encoding='utf-8')
        self._w = csv.DictWriter(self._f, fieldnames=RECORD_COLUMNS)
        if new:
            self._w.writeheader()
        self._day = day

    def write(self, r: SampleRecord) -> None:
        day = r.ingest_timestamp_utc[:10].replace('-', '') if r.ingest_timestamp_utc else utc_now().strftime('%Y%m%d')
        with self._lock:
            if day != self._day:
                self._open(day)
            self._w.writerow(r.row())
            self._f.flush()

    def close(self) -> None:
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None


def read_records_csv(path: str) -> List[SampleRecord]:
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            v = row.get('value', '')
            out.append(SampleRecord(tag_id=row['tag_id'], timestamp_utc=row['timestamp_utc'], value=(float(v) if v not in ('', None) else None),
                                    unit=row.get('unit', ''), quality_flag=row.get('quality_flag', 'GOOD'), rule_fired=row.get('rule_fired', ''),
                                    source_layer=row.get('source_layer', 'FIELD_EDGE'), ingest_timestamp_utc=row.get('ingest_timestamp_utc', '')))
    return out


def build_live_stream(ws: Workspace, well_id: str, days: Optional[List[str]] = None, out_path: Optional[str] = None) -> Optional[str]:
    """Fold the record files of every patch on a well into one historian-style stream
    CSV (priority order: low first, high last, so the higher priority wins a tie).
    Returns the path, or None when there are no records."""
    d = os.path.join(ws.path, 'wells', well_id, 'records', 'live')
    if not os.path.isdir(d):
        return None
    store = PatchStore(ws)
    prio = {p['name']: int(p.get('priority', 1)) for p in store.list() if p['well_id'] == well_id}
    files = []
    for fn in os.listdir(d):
        if fn.startswith('patch_') and fn.endswith('.records.csv'):
            name = fn[len('patch_'):-len('.records.csv')]
            pname, _, day = name.rpartition('_')
            if days and day not in days:
                continue
            files.append((prio.get(pname, 0), pname, day, os.path.join(d, fn)))
    if not files:
        return None
    files.sort()
    records: List[SampleRecord] = []
    for _, _, _, path in files:
        records.extend(read_records_csv(path))
    if not records:
        return None
    st = records_to_stream(records, name=well_id, source_format='patches')
    out_path = out_path or os.path.join(d, f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}_stream.csv")
    t0 = datetime.fromisoformat(st.meta['start_time'])
    from datetime import timedelta
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['timestamp'] + list(st.channels))
        for i, t in enumerate(st.index):
            w.writerow([(t0 + timedelta(seconds=float(t))).isoformat()] + ['' if st.channels[c].values[i] != st.channels[c].values[i] else round(float(st.channels[c].values[i]), 4) for c in st.channels])
    return out_path


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------
class PatchRunner(threading.Thread):
    def __init__(self, sup: 'PatchSupervisor', patch: dict):
        super().__init__(name=f"patch-{patch['name']}", daemon=True)
        self.sup, self.patch = sup, patch
        self.stop_event = threading.Event()
        self.tap = None
        self.sink = RecordSink(sup.ws, patch['well_id'], patch['name'])
        self.state = {'name': patch['name'], 'protocol': patch['protocol'], 'well_id': patch['well_id'], 'status': 'STOPPED',
                      'since_utc': utc_now_iso(), 'last_sample_utc': None, 'samples_total': 0, 'samples_last_min': 0,
                      'latency_p50_s': None, 'latency_p95_s': None, 'reconnects': 0, 'last_error': None, 'tags': {}}
        self._lat: List[float] = []
        self._minute: List[float] = []
        self._lock = threading.Lock()

    # -- state ---------------------------------------------------------------------------
    def _set(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            if status != self.state['status']:
                self.state['status'] = status
                self.state['since_utc'] = utc_now_iso()
            if error is not None:
                self.state['last_error'] = error
        self.sup.ws.audit('system', 'patch.state', {'name': self.patch['name'], 'status': status, 'error': error})
        self._persist()

    def _persist(self) -> None:
        p = os.path.join(self.sup.ws.dir('wells', self.patch['well_id'], 'records', 'live'), f"patch_{self.patch['name']}.state.json")
        tmp = p + '.tmp'
        with self._lock:
            snap = dict(self.state)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, indent=1)
        os.replace(tmp, p)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            self._minute = [t for t in self._minute if now - t <= 60.0]
            self.state['samples_last_min'] = len(self._minute)
            if self.state['status'] == 'CONNECTED' and self.state['last_sample_utc']:
                last = datetime.fromisoformat(self.state['last_sample_utc'].replace('Z', '+00:00'))
                if (utc_now() - last).total_seconds() > float(self.patch.get('stale_after_s', 120.0)):
                    self.state['status'] = 'DEGRADED'
            return json.loads(json.dumps(self.state))

    def _on_record(self, r: SampleRecord) -> None:
        self.sink.write(r)
        with self._lock:
            self.state['samples_total'] += 1
            self.state['last_sample_utc'] = r.ingest_timestamp_utc or utc_now_iso()
            if self.state['status'] in ('CONNECTING', 'DEGRADED'):
                self.state['status'], self.state['since_utc'] = 'CONNECTED', utc_now_iso()
            self._minute.append(time.time())
            lat = r.latency_s()
            if lat is not None:
                self._lat.append(lat)
                if len(self._lat) > 200:
                    self._lat = self._lat[-200:]
                s = sorted(self._lat)
                self.state['latency_p50_s'] = round(s[len(s) // 2], 3)
                self.state['latency_p95_s'] = round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)
            self.state['tags'][r.tag_id] = {'value': r.value, 'unit': r.unit, 'quality': r.quality_flag, 'timestamp_utc': r.timestamp_utc}

    # -- one connection ---------------------------------------------------------------------
    def _session(self) -> None:
        proto, cfg = self.patch['protocol'], self.sup.store.load_config(self.patch)
        mod = _mod(proto)
        if proto == 'wits0':
            self.tap = mod.Wits0Tap(cfg)
            self.tap.run(None, on_record=self._on_record)
        elif proto == 'witsml':
            self.tap = mod.WitsmlTap(cfg)
            self.tap.run(None, on_record=self._on_record)
        elif proto == 'opcua':
            self.tap = mod.OpcUaTap(cfg).connect()
            try:
                for r in self.tap.read_once():
                    self._on_record(r)
                while not self.stop_event.is_set():
                    self.tap.subscribe(30.0, on_record=self._on_record)
            finally:
                self.tap.close()
        elif proto == 'mqtt':
            self.tap = mod.MqttTap(cfg)
            while not self.stop_event.is_set():
                self.tap.run(30.0, on_record=self._on_record)
        else:                                                   # modbus: poll the register map
            from .live_ports import TagMapping, make_record
            poll = float(cfg.get('poll_s', 5.0))
            while not self.stop_event.is_set():
                st = mod.read_modbus(cfg)
                recv = utc_now()
                for name, ch in st.channels.items():
                    v = ch.values[-1] if len(ch.values) else None
                    q = (ch.quality[-1] if getattr(ch, 'quality', None) else 'OK')
                    self._on_record(make_record(TagMapping(address=name, tag_id=name, unit=ch.unit), None if v != v else v, None,
                                                'GAP' if q == 'MISSING' else 'GOOD', '', recv))
                if self.stop_event.wait(poll):
                    break

    def run(self) -> None:
        attempt = 0
        while not self.stop_event.is_set():
            self._set('CONNECTING')
            try:
                self._session()
                if self.stop_event.is_set():
                    break
                raise ConnectionError('the source ended the session')
            except NotImplementedError as e:                      # a missing dependency never retries
                self._set('DOWN', str(e))
                break
            except Exception as e:                                # ConnectionError, OSError, a protocol error
                if self.stop_event.is_set():
                    break
                self._set('DOWN', f'{type(e).__name__}: {e}')
                if not self.patch.get('auto_restart', True):
                    break
                with self._lock:
                    self.state['reconnects'] += 1
                delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
                attempt += 1
                if self.stop_event.wait(delay):
                    break
                continue
            attempt = 0
        self.sink.close()
        self._set('STOPPED')

    def stop(self) -> None:
        self.stop_event.set()
        if self.tap is not None and hasattr(self.tap, 'stop'):
            try:
                self.tap.stop()
            except Exception:
                pass


class PatchSupervisor:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.store = PatchStore(ws)
        self.runners: Dict[str, PatchRunner] = {}
        self._lock = threading.Lock()

    def start(self, name: str, actor: str = 'system') -> dict:
        p = self.store.get(name)
        with self._lock:
            r = self.runners.get(name)
            if r and r.is_alive():
                raise WorkspaceError(f'patch {name} is already running')
            r = PatchRunner(self, p)
            self.runners[name] = r
            r.start()
        self.ws.audit(actor, 'patch.start', {'name': name})
        return r.snapshot()

    def stop(self, name: str, actor: str = 'system', wait_s: float = 5.0) -> dict:
        with self._lock:
            r = self.runners.get(name)
        if not r:
            raise WorkspaceError(f'patch {name} is not running')
        r.stop()
        r.join(wait_s)
        self.ws.audit(actor, 'patch.stop', {'name': name})
        return r.snapshot()

    def start_enabled(self) -> List[str]:
        started = []
        for p in self.store.list():
            if p.get('enabled', True):
                try:
                    self.start(p['name'])
                    started.append(p['name'])
                except WorkspaceError:
                    pass
        return started

    def stop_all(self) -> None:
        for name in list(self.runners):
            try:
                self.stop(name)
            except WorkspaceError:
                pass

    def states(self) -> List[dict]:
        out = []
        for p in self.store.list():
            r = self.runners.get(p['name'])
            if r and r.is_alive():
                s = r.snapshot()
            else:
                sp = os.path.join(self.ws.path, 'wells', p['well_id'], 'records', 'live', f"patch_{p['name']}.state.json")
                if os.path.isfile(sp):
                    with open(sp, encoding='utf-8') as f:
                        s = json.load(f)
                    s['status'] = 'STOPPED'
                else:
                    s = {'name': p['name'], 'protocol': p['protocol'], 'well_id': p['well_id'], 'status': 'STOPPED', 'since_utc': None,
                         'last_sample_utc': None, 'samples_total': 0, 'samples_last_min': 0, 'latency_p50_s': None, 'latency_p95_s': None,
                         'reconnects': 0, 'last_error': None, 'tags': {}}
            s['patch'] = p
            out.append(s)
        return out
