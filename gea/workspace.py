# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""workspace - the one folder a client site owns.

Everything the program reads for a site and everything it writes lives under
one directory, so the client backs it up, archives it and audits it like any
other project folder. Nothing leaves the site; nothing is kept anywhere else.

    <workspace>/
      workspace.json            manifest: name, created, program version, schema
      wells/<well_id>/
          well.json             what the well is: source kind, reference, hashes, who added it
          source/               the client's files, copied in verbatim and hashed
          records/live/         stream CSVs and recordings written by the live ports
      config/                   the version store (gauge specs, criteria, alarm
                                definitions, port maps): <name>/vNNNN.json + history.jsonl
      monitor/<well_id>/        the drift monitor's evaluations, change log and state
      reports/                  the dashboard (index.html), site-level reports and
                                reports/wells/<well_id>/ with each well's reports and records
      jobs/<job_id>/            every run: job.json + log.txt   (gea.jobs)
      records/audit.jsonl       who did what, when, from which inputs (hashed)
      users.json                accounts and roles                (gea.service)

Wells come in three kinds: 'file' (a historian CSV, LAS, SEG-Y or operator
table the client uploads), 'catalog' (one of the public archive entries in the
package) and 'live' (a port configuration: OPC UA node map, MQTT topic map or
Modbus register map). Adding a well never modifies the client's original file;
the copy under source/ is what the program reads, and its hash is recorded.

The audit log is append-only JSON lines: actor, UTC time, action, detail and
the SHA-256 of every input the action used. Every service action and every
`gea workspace` command writes one line.

Headless-safe: stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

SCHEMA = 1
_AUDIT_LOCK = threading.Lock()
KINDS = ('file', 'catalog', 'live')
PORTS = ('opcua', 'mqtt', 'modbus_g6', 'wits0', 'witsml')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def slug(text: str) -> str:
    """A filesystem-safe id from a display name: letters, digits, '-', '_', '.'."""
    s = re.sub(r'[^A-Za-z0-9._-]+', '-', text.strip()).strip('-.')
    return s[:80] or 'well'


class WorkspaceError(Exception):
    pass


class Workspace:
    """One client site folder."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.manifest_path = os.path.join(self.path, 'workspace.json')
        if not os.path.isfile(self.manifest_path):
            raise WorkspaceError(f'not a workspace (no workspace.json): {self.path} - create one with `gea workspace --path ... --action init`')
        with open(self.manifest_path, encoding='utf-8') as f:
            self.manifest = json.load(f)

    # -- creation -------------------------------------------------------------------
    @classmethod
    def create(cls, path: str, name: str, actor: str = 'system') -> 'Workspace':
        from . import __version__
        path = os.path.abspath(path)
        if os.path.isfile(os.path.join(path, 'workspace.json')):
            raise WorkspaceError(f'workspace already exists: {path}')
        for d in ('wells', 'config', 'monitor', 'reports', 'jobs', 'records'):
            os.makedirs(os.path.join(path, d), exist_ok=True)
        manifest = {'schema': SCHEMA, 'name': name, 'created_utc': utc_now_iso(),
                    'program_version': __version__, 'wells': []}
        with open(os.path.join(path, 'workspace.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=1)
        ws = cls(path)
        ws.audit(actor, 'workspace.init', {'name': name})
        return ws

    def _save(self) -> None:
        tmp = self.manifest_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.manifest, f, indent=1)
        os.replace(tmp, self.manifest_path)

    # -- paths ------------------------------------------------------------------------
    def dir(self, *parts: str) -> str:
        p = os.path.join(self.path, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    @property
    def config_dir(self) -> str:
        return self.dir('config')

    @property
    def reports_dir(self) -> str:
        return self.dir('reports')

    @property
    def jobs_dir(self) -> str:
        return self.dir('jobs')

    @property
    def audit_path(self) -> str:
        return os.path.join(self.dir('records'), 'audit.jsonl')

    def well_dir(self, well_id: str) -> str:
        return self.dir('wells', well_id)

    def monitor_dir(self, well_id: str) -> str:
        return self.dir('monitor', well_id)

    # -- audit ------------------------------------------------------------------------
    def audit(self, actor: str, action: str, detail: Optional[dict] = None, inputs: Optional[List[str]] = None) -> dict:
        entry = {'utc': utc_now_iso(), 'actor': actor or 'unknown', 'action': action, 'detail': detail or {},
                 'inputs': {os.path.relpath(p, self.path) if os.path.isabs(p) and p.startswith(self.path) else p: sha256_file(p)
                            for p in (inputs or []) if os.path.isfile(p)}}
        line = json.dumps(entry, sort_keys=True) + '\n'
        with _AUDIT_LOCK:                                   # one writer at a time: a patch thread and a request never interleave
            with open(self.audit_path, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
        return entry

    def audit_log(self, limit: Optional[int] = None) -> List[dict]:
        if not os.path.isfile(self.audit_path):
            return []
        rows = []
        with _AUDIT_LOCK:
            with open(self.audit_path, encoding='utf-8') as f:
                lines = f.read().split('\n')
        for l in lines:
            if not l.strip():
                continue
            try:
                rows.append(json.loads(l))
            except json.JSONDecodeError:               # a line still being written by another process: skipped, never fatal
                continue
        return rows[-limit:] if limit else rows

    # -- wells ------------------------------------------------------------------------
    def wells(self) -> List[dict]:
        out = []
        for wid in self.manifest.get('wells', []):
            p = os.path.join(self.path, 'wells', wid, 'well.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
        return out

    def well(self, well_id: str) -> dict:
        p = os.path.join(self.path, 'wells', well_id, 'well.json')
        if not os.path.isfile(p):
            raise WorkspaceError(f'no such well: {well_id}')
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def _register(self, well: dict, actor: str) -> dict:
        wid = well['id']
        if wid in self.manifest['wells']:
            raise WorkspaceError(f'well already exists: {wid}')
        d = self.well_dir(wid)
        for sub in ('source', 'records'):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        well.update({'added_utc': utc_now_iso(), 'added_by': actor})
        with open(os.path.join(d, 'well.json'), 'w', encoding='utf-8') as f:
            json.dump(well, f, indent=1)
        self.manifest['wells'].append(wid)
        self._save()
        self.audit(actor, 'well.add', {'id': wid, 'kind': well['kind'], 'display': well['display']},
                   inputs=[os.path.join(d, 'source', n) for n in well.get('files', [])])
        return well

    def add_well_file(self, src_path: str, display: Optional[str] = None, actor: str = 'system', station_md_ft: Optional[float] = None,
                      filename: Optional[str] = None) -> dict:
        """Copy the client's file in verbatim; the copy is what the program reads."""
        if not os.path.isfile(src_path):
            raise WorkspaceError(f'file not found: {src_path}')
        filename = os.path.basename(filename or src_path)
        display = display or os.path.splitext(filename)[0]
        wid = slug(display)
        d = os.path.join(self.path, 'wells', wid, 'source')
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, filename)
        shutil.copyfile(src_path, dst)
        well = {'id': wid, 'display': display, 'kind': 'file', 'files': [filename],
                'source': {'original_path': os.path.abspath(src_path), 'sha256': sha256_file(dst), 'bytes': os.path.getsize(dst)},
                'station_md_ft': station_md_ft}
        return self._register(well, actor)

    def add_well_catalog(self, entry: str, well_tag: str, station_md_ft: float, actor: str = 'system', display: Optional[str] = None) -> dict:
        from . import CATALOG
        if entry not in CATALOG:
            raise WorkspaceError(f'no such catalogue entry: {entry}')
        display = display or well_tag
        wid = slug(f'{entry}__{well_tag}')
        well = {'id': wid, 'display': display, 'kind': 'catalog', 'files': [],
                'source': {'entry': entry, 'well': well_tag}, 'station_md_ft': float(station_md_ft)}
        return self._register(well, actor)

    def add_well_live(self, display: str, port: str, config_path: str, actor: str = 'system', station_md_ft: Optional[float] = None) -> dict:
        """A live source: the port's client-owned map is copied in and versioned."""
        if port not in PORTS:
            raise WorkspaceError(f"port must be one of {PORTS}, got '{port}'")
        if not os.path.isfile(config_path):
            raise WorkspaceError(f'port configuration not found: {config_path}')
        with open(config_path, encoding='utf-8') as f:
            cfg = json.load(f)                      # must be JSON; a bad file fails here, before anything is written
        wid = slug(display)
        d = os.path.join(self.path, 'wells', wid, 'source')
        os.makedirs(d, exist_ok=True)
        fname = f'{port}_config.json'
        with open(os.path.join(d, fname), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=1)
        from .config_versioning import ConfigStore
        ConfigStore(self.config_dir).commit(f'{wid}.{port}', cfg, actor, f'port map for {display}')
        well = {'id': wid, 'display': display, 'kind': 'live', 'files': [fname],
                'source': {'port': port, 'config': fname, 'sha256': sha256_file(os.path.join(d, fname))},
                'station_md_ft': station_md_ft}
        well = self._register(well, actor)
        try:                                                     # the well's patch, so the supervisor keeps it fed
            from .patches import PatchStore
            PatchStore(self).add(wid, port, wid, cfg, actor, priority=1)
        except Exception:
            pass
        return well

    def remove_well(self, well_id: str, actor: str = 'system') -> None:
        """Unregister; the folder is kept (renamed) so nothing the client gave us is destroyed."""
        if well_id not in self.manifest['wells']:
            raise WorkspaceError(f'no such well: {well_id}')
        self.manifest['wells'].remove(well_id)
        self._save()
        src = os.path.join(self.path, 'wells', well_id)
        dst = os.path.join(self.path, 'wells', f'{well_id}.removed-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}')
        if os.path.isdir(src):
            os.rename(src, dst)
        self.audit(actor, 'well.remove', {'id': well_id, 'kept_as': os.path.basename(dst)})

    # -- the dashboard over the workspace -------------------------------------------------
    def dashboard_inputs(self) -> dict:
        """What dashboard.orchestrate needs, from the registered wells."""
        catalog_wells, file_wells, live_wells = [], [], []
        for w in self.wells():
            if w['kind'] == 'catalog':
                catalog_wells.append({'entry': w['source']['entry'], 'well': w['source']['well'], 'md_ft': w['station_md_ft'] or 10000.0})
            elif w['kind'] == 'file':
                file_wells.append({'path': os.path.join(self.path, 'wells', w['id'], 'source', w['files'][0]), 'name': w['id']})
            else:
                live_wells.append(w)
        return {'catalog_wells': catalog_wells, 'file_wells': file_wells, 'live_wells': live_wells}

    def refresh_dashboard(self, actor: str = 'system', td_ft: float = 10500.0, outages: Optional[List[str]] = None,
                          month: Optional[str] = None, criteria_path: Optional[str] = None, run_sat: bool = False) -> dict:
        """Run the report family for every well into reports/ and write index.html.
        Live wells are reported from their latest recording, if any, through a stream CSV."""
        from . import dashboard as D
        inp = self.dashboard_inputs()
        file_wells = list(inp['file_wells'])
        from .patches import build_live_stream
        for w in self.wells():                                   # any well fed by patches gets its records folded first
            try:
                build_live_stream(self, w['id'])
            except Exception:
                pass
        for w in inp['live_wells']:
            csv = self.latest_live_stream_csv(w['id'])
            if csv:
                file_wells.append({'path': csv, 'name': w['id']})
        for w in self.wells():                                   # a file or catalogue well with a patch: its live stream is reported beside it
            if w['kind'] != 'live' and self.latest_live_stream_csv(w['id']) and not any(fw['name'] == w['id'] + '.live' for fw in file_wells):
                file_wells.append({'path': self.latest_live_stream_csv(w['id']), 'name': w['id'] + '.live'})
        inputs = [fw['path'] for fw in file_wells]
        if criteria_path is None:
            cand = os.path.join(self.config_dir, 'criteria.json')
            criteria_path = cand if os.path.isfile(cand) else None
        res = D.orchestrate(self.reports_dir, inp['catalog_wells'], file_wells, td_ft=td_ft, outages=outages, month=month,
                            site_name=self.manifest['name'], criteria_path=criteria_path,
                            monitor_root=self.dir('monitor'), run_sat=run_sat)
        self.audit(actor, 'dashboard.refresh', {'wells': len(inp['catalog_wells']) + len(file_wells), 'month': month, 'sat': run_sat},
                   inputs=inputs + ([criteria_path] if criteria_path else []))
        return res

    def latest_live_stream_csv(self, well_id: str) -> Optional[str]:
        """The newest stream CSV a live port wrote for this well (records/live/*.csv)."""
        d = os.path.join(self.path, 'wells', well_id, 'records', 'live')
        if not os.path.isdir(d):
            return None
        csvs = sorted(f for f in os.listdir(d) if f.endswith('_stream.csv'))
        return os.path.join(d, csvs[-1]) if csvs else None

    # -- migration of an existing --out folder ----------------------------------------------
    def migrate_out_dir(self, out_dir: str, actor: str = 'system') -> dict:
        """Bring a folder written by `gea dashboard --out` into the workspace: each
        wells/<name>/well.json there names its source; catalogue wells re-register,
        file wells copy their source file in when it still exists; the reports are
        copied under reports/ so nothing is lost."""
        out_dir = os.path.abspath(out_dir)
        wells_dir = os.path.join(out_dir, 'wells')
        added, skipped = [], []
        if os.path.isdir(wells_dir):
            for name in sorted(os.listdir(wells_dir)):
                wj = os.path.join(wells_dir, name, 'well.json')
                if not os.path.isfile(wj):
                    continue
                with open(wj, encoding='utf-8') as f:
                    meta = json.load(f)
                src = str(meta.get('source', ''))
                try:
                    m = re.match(r'^(?P<entry>\S+) \(station MD (?P<md>[\d.]+) ft', src)
                    if m:
                        self.add_well_catalog(m.group('entry'), meta.get('display', name), float(m.group('md')), actor=actor)
                    elif os.path.isfile(src):
                        self.add_well_file(src, display=meta.get('display', name), actor=actor)
                    else:
                        skipped.append((name, 'source file no longer exists: ' + src)); continue
                    added.append(name)
                except WorkspaceError as e:
                    skipped.append((name, str(e)))
        if os.path.isdir(out_dir):
            shutil.copytree(out_dir, os.path.join(self.reports_dir, 'imported'), dirs_exist_ok=True)
        self.audit(actor, 'workspace.migrate', {'from': out_dir, 'added': added, 'skipped': skipped})
        return {'added': added, 'skipped': skipped}

    # -- summary --------------------------------------------------------------------------------
    def summary(self) -> dict:
        wells = self.wells()
        return {'name': self.manifest['name'], 'path': self.path, 'created_utc': self.manifest['created_utc'],
                'program_version': self.manifest['program_version'], 'schema': self.manifest['schema'],
                'wells': [{'id': w['id'], 'display': w['display'], 'kind': w['kind']} for w in wells],
                'n_wells': len(wells), 'audit_entries': len(self.audit_log()),
                'dashboard': os.path.join(self.reports_dir, 'index.html') if os.path.isfile(os.path.join(self.reports_dir, 'index.html')) else None}
