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
PORTS = ('opcua', 'mqtt', 'modbus_g6', 'wits0', 'witsml', 'etp')


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


def _group_truth(points) -> dict:
    """Truth points grouped by their point_id: when the ids are the rigs' ids, every rig has its own truth."""
    out: dict = {}
    for p in points or []:
        out.setdefault(p.point_id, []).append(p)
    return {k: v for k, v in out.items() if v}


class Workspace:
    """One client site folder."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.manifest_path = os.path.join(self.path, 'workspace.json')
        if not os.path.isfile(self.manifest_path):
            raise WorkspaceError(f'not a workspace (no workspace.json): {self.path} - create one with `gea workspace --path ... --action init`')
        with open(self.manifest_path, encoding='utf-8') as f:
            self.manifest = json.load(f)
        # a serving process sets this to put selected audit entries on its console as they happen; the record
        # on the disk is written first and is the record - the console line is a courtesy to whoever is watching
        self.on_audit = None

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

    def rename(self, name: str, actor: str = 'system') -> dict:
        """The site's name, as every report prints it. A name that is already this one is left alone and
        writes no audit line, so a launcher may say it on every start without filling the log."""
        name = (name or '').strip()
        if not name:
            raise WorkspaceError('a site needs a name')
        old = self.manifest.get('name')
        if old == name:
            return {'name': name, 'changed': False}
        self.manifest['name'] = name
        self._save()
        self.audit(actor, 'workspace.rename', {'from': old, 'to': name})
        return {'name': name, 'changed': True, 'from': old}

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
        hook = getattr(self, 'on_audit', None)
        if hook is not None:
            try:
                hook(entry)
            except Exception:                               # a console that cannot be written to never fails an action
                pass
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
                file_wells.append({'path': os.path.join(self.path, 'wells', w['id'], 'source', w['files'][0]), 'name': w['id'],
                                   'records_dir': os.path.join(self.path, 'wells', w['id'], 'records')})
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
                file_wells.append({'path': csv, 'name': w['id'], 'records_dir': os.path.join(self.path, 'wells', w['id'], 'records')})
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

    # -- the second leg: seismic stations -------------------------------------------------
    def seismic_dir(self, station_id: str) -> str:
        return self.dir('seismic', station_id)

    def seismic_stations(self) -> List[dict]:
        out = []
        for sid in self.manifest.get('seismic', []):
            p = os.path.join(self.path, 'seismic', sid, 'station.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
        return out

    def seismic_station(self, station_id: str) -> dict:
        p = os.path.join(self.path, 'seismic', station_id, 'station.json')
        if not os.path.isfile(p):
            raise WorkspaceError(f'no such seismic station: {station_id}')
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def add_seismic_station(self, display: str, files: List[str], lat: Optional[float] = None, lon: Optional[float] = None, actor: str = 'system',
                            stationxml: Optional[str] = None, sensors_csv: Optional[str] = None, sources_csv: Optional[str] = None,
                            band: Optional[List[float]] = None, filenames: Optional[List[str]] = None, note: str = '',
                            permits_csv: Optional[str] = None, datum: Optional[str] = None, permits_zone: Optional[str] = None,
                            permits_unit: str = 'm') -> dict:
        """A seismic station (one record) or an array (one record per sensor plus a sensors CSV), copied in verbatim
        and hashed, with the station file, the rigs list and the band the leg will use. A permit export may stand in
        for the rigs list: it is copied in as given and converted beside it, with the import note.

        `datum` is the datum the station's own position - and any sensor or rig row that does not say - is on. It is
        recorded rather than assumed: a station whose datum nobody states is marked UNKNOWN, and the refresh prints
        what that assumption is worth in metres at that site."""
        from . import geodesy as GD
        if not files:
            raise WorkspaceError('a seismic station needs at least one record file')
        if permits_csv and sources_csv:
            raise WorkspaceError('give the rigs list as --sources or as --permits, not both')
        for f in files:
            if not os.path.isfile(f):
                raise WorkspaceError(f'file not found: {f}')
        sid = slug(display)
        if sid in self.manifest.setdefault('seismic', []):
            raise WorkspaceError(f'seismic station already exists: {sid}')
        from .files import detect as _detect
        d = os.path.join(self.path, 'seismic', sid, 'source')
        os.makedirs(d, exist_ok=True)
        names = []
        hashes = {}
        for i, f in enumerate(files):
            kind = _detect(f)['kind']
            if kind not in ('mseed', 'sac'):
                raise WorkspaceError(f'{os.path.basename(f)} is {kind}, not a seismic record (miniSEED or SAC)')
            name = os.path.basename((filenames or [None] * len(files))[i] or f)
            shutil.copyfile(f, os.path.join(d, name))
            names.append(name)
            hashes[name] = sha256_file(os.path.join(d, name))
        extras = {}
        for key, src in (('stationxml', stationxml), ('sensors', sensors_csv), ('sources', sources_csv)):
            if src:
                if not os.path.isfile(src):
                    raise WorkspaceError(f'{key} file not found: {src}')
                name = os.path.basename(src)
                shutil.copyfile(src, os.path.join(d, name))
                extras[key] = name
                hashes[name] = sha256_file(os.path.join(d, name))
        permit_note = None
        if permits_csv:
            from . import permits as PM
            if not os.path.isfile(permits_csv):
                raise WorkspaceError(f'permits file not found: {permits_csv}')
            raw = os.path.basename(permits_csv)
            shutil.copyfile(permits_csv, os.path.join(d, raw))
            hashes[raw] = sha256_file(os.path.join(d, raw))
            try:
                permit_note = PM.import_permits(os.path.join(d, raw), os.path.join(d, 'rigs_from_permits.csv'),
                                                within=((lat, lon, 160.0) if lat is not None and lon is not None else None),
                                                datum=datum, zone=permits_zone, unit=permits_unit)
            except ValueError as e:
                raise WorkspaceError(f'permits: {e}')
            if permit_note['rows_out'] == 0:
                raise WorkspaceError(f"permits: no usable row ({permit_note['dropped']})")
            extras['sources'] = 'rigs_from_permits.csv'
            extras['permits'] = raw
            hashes['rigs_from_permits.csv'] = sha256_file(os.path.join(d, 'rigs_from_permits.csv'))
        if sensors_csv:
            from .seismic_array import load_sensors_csv
            n_sens = len(load_sensors_csv(os.path.join(d, extras['sensors']), datum))
            if n_sens != len(names):
                raise WorkspaceError(f'the sensors CSV lists {n_sens} sensors but {len(names)} records were given (one per sensor, in order)')
        station = {'id': sid, 'display': display, 'kind': 'array' if sensors_csv else 'single', 'files': names, 'lat': lat, 'lon': lon,
                   'datum': GD.datum_name(datum),
                   'stationxml': extras.get('stationxml'), 'sensors': extras.get('sensors'), 'sources': extras.get('sources'), 'permits': extras.get('permits'),
                   'permits_import': ({k: permit_note[k] for k in ('rows_in', 'rows_out', 'dropped', 'columns_used', 'end_assumed_rows', 'default_days', 'within', 'datum')} if permit_note else None),
                   'band_hz': list(band) if band else [1.0, 50.0], 'note': note, 'sha256': hashes,
                   'added_utc': utc_now_iso(), 'added_by': actor}
        with open(os.path.join(self.path, 'seismic', sid, 'station.json'), 'w', encoding='utf-8') as f:
            json.dump(station, f, indent=1)
        self.manifest['seismic'].append(sid)
        self._save()
        self.audit(actor, 'seismic.add', {'id': sid, 'kind': station['kind'], 'files': names, 'display': display},
                   inputs=[os.path.join(d, n) for n in list(names) + list(extras.values())])
        return station

    def remove_seismic_station(self, station_id: str, actor: str = 'system') -> None:
        if station_id not in self.manifest.get('seismic', []):
            raise WorkspaceError(f'no such seismic station: {station_id}')
        self.manifest['seismic'].remove(station_id)
        self._save()
        src = os.path.join(self.path, 'seismic', station_id)
        if os.path.isdir(src):
            os.rename(src, src + '.removed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        self.audit(actor, 'seismic.remove', {'id': station_id})

    def seismic_reports_dir(self, station_id: str) -> str:
        return self.dir('reports', 'seismic', station_id)

    # -- a live station: records arriving over SeedLink, folded into the station's files ---------------------------
    def live_dir(self, station_id: str) -> str:
        return self.dir('seismic', station_id, 'live')

    def add_live_station(self, display: str, config: dict, lat: Optional[float] = None, lon: Optional[float] = None, actor: str = 'system',
                         band: Optional[List[float]] = None, datum: Optional[str] = None, note: str = '', stationxml: Optional[str] = None,
                         sources_csv: Optional[str] = None) -> dict:
        """A station whose records arrive over SeedLink rather than as files: the record list starts empty, the
        SeedLink config is kept beside it, and the day files the port writes are folded into the list (hashed, like
        any file a person brought) by `fold_live_station`. The leg then runs on them exactly as on a brought file."""
        from . import geodesy as GD
        from . import seedlink as L
        try:
            cfg = L.load_config(config)
        except (ValueError, OSError) as e:
            raise WorkspaceError(f'seedlink config: {e}')
        sid = slug(display)
        if sid in self.manifest.setdefault('seismic', []):
            raise WorkspaceError(f'seismic station already exists: {sid}')
        d = os.path.join(self.path, 'seismic', sid, 'source')
        os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(self.path, 'seismic', sid, 'live'), exist_ok=True)
        hashes, extras = {}, {}
        for key, src in (('stationxml', stationxml), ('sources', sources_csv)):
            if src:
                if not os.path.isfile(src):
                    raise WorkspaceError(f'{key} file not found: {src}')
                name = os.path.basename(src)
                shutil.copyfile(src, os.path.join(d, name))
                extras[key] = name
                hashes[name] = sha256_file(os.path.join(d, name))
        clean = {k: v for k, v in cfg.items() if not k.startswith('_')}
        with open(os.path.join(self.path, 'seismic', sid, 'live', 'seedlink.json'), 'w', encoding='utf-8') as f:
            json.dump(clean, f, indent=1)
        station = {'id': sid, 'display': display, 'kind': 'single', 'files': [], 'lat': lat, 'lon': lon, 'datum': GD.datum_name(datum),
                   'stationxml': extras.get('stationxml'), 'sensors': None, 'sources': extras.get('sources'), 'permits': None, 'permits_import': None,
                   'band_hz': list(band) if band else [1.0, 50.0], 'note': note, 'sha256': hashes, 'added_utc': utc_now_iso(), 'added_by': actor,
                   'live': {'protocol': 'seedlink', 'host': clean['host'], 'port': clean['port'],
                            'stations': [f"{x['network']}_{x['station']}" for x in cfg['_streams']], 'enabled': True, 'folds': 0}}
        with open(os.path.join(self.path, 'seismic', sid, 'station.json'), 'w', encoding='utf-8') as f:
            json.dump(station, f, indent=1)
        self.manifest['seismic'].append(sid)
        self._save()
        self.audit(actor, 'seismic.live.add', {'id': sid, 'display': display, 'host': clean['host'], 'port': clean['port'], 'stations': station['live']['stations']},
                   inputs=[os.path.join(self.path, 'seismic', sid, 'live', 'seedlink.json')])
        return station

    def live_station_config(self, station_id: str) -> dict:
        st = self.seismic_station(station_id)
        if not st.get('live'):
            raise WorkspaceError(f'{station_id} is not a live station')
        with open(os.path.join(self.path, 'seismic', station_id, 'live', 'seedlink.json'), encoding='utf-8') as f:
            return json.load(f)

    def live_station_state(self, station_id: str) -> dict:
        p = os.path.join(self.path, 'seismic', station_id, 'live', 'seedlink_state.json')
        if not os.path.isfile(p):
            return {'status': 'IDLE', 'records': 0, 'channels': {}}
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {'status': 'UNREADABLE', 'records': 0, 'channels': {}}

    def set_live_enabled(self, station_id: str, enabled: bool, actor: str = 'system') -> dict:
        st = self.seismic_station(station_id)
        if not st.get('live'):
            raise WorkspaceError(f'{station_id} is not a live station')
        st['live']['enabled'] = bool(enabled)
        with open(os.path.join(self.path, 'seismic', station_id, 'station.json'), 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1)
        return st

    def fold_live_station(self, station_id: str, actor: str = 'system', include_open: bool = False) -> dict:
        """Move the day files the port has finished with (every day but the current UTC day, unless `include_open`)
        from live/ into source/, hash each, and append them to the station's record list - Z channels first, so the
        leg's primary trace is the vertical. What is folded is what the station sent, byte for byte; the refresh
        then runs on it as on any brought record."""
        st = self.seismic_station(station_id)
        if not st.get('live'):
            raise WorkspaceError(f'{station_id} is not a live station')
        live = os.path.join(self.path, 'seismic', station_id, 'live')
        src = os.path.join(self.path, 'seismic', station_id, 'source')
        os.makedirs(src, exist_ok=True)
        today = datetime.now(timezone.utc).strftime('%Y.%j')
        names = sorted((n for n in os.listdir(live) if n.endswith('.mseed')), key=lambda n: (not n.split('.')[3].endswith('Z'), n))
        folded, kept = [], []
        for n in names:
            day = '.'.join(n.split('.')[4:6])
            if day == today and not include_open:
                kept.append(n)
                continue
            if os.path.getsize(os.path.join(live, n)) == 0:
                continue
            target = n
            if target in st['files']:
                k = 2
                while f"{os.path.splitext(n)[0]}.{k}.mseed" in st['files']:
                    k += 1
                target = f"{os.path.splitext(n)[0]}.{k}.mseed"
            shutil.move(os.path.join(live, n), os.path.join(src, target))
            st['files'].append(target)
            st['sha256'][target] = sha256_file(os.path.join(src, target))
            folded.append(target)
        if folded:
            st['files'].sort(key=lambda n: (not n.split('.')[3].endswith('Z') if n.count('.') >= 5 else 1, n))
            st['live']['folds'] = int(st['live'].get('folds') or 0) + 1
            st['live']['last_fold_utc'] = utc_now_iso()
            with open(os.path.join(self.path, 'seismic', station_id, 'station.json'), 'w', encoding='utf-8') as f:
                json.dump(st, f, indent=1)
            self.audit(actor, 'seismic.live.fold', {'id': station_id, 'folded': folded, 'kept_open': kept}, inputs=[os.path.join(src, n) for n in folded])
        return {'id': station_id, 'folded': folded, 'kept_open': kept, 'files': st['files']}

    def refresh_seismic(self, station_id: str, actor: str = 'system', win_s: float = 600.0) -> dict:
        """Run the leg on a station: read, remove the response when a station file is there, the spectrum and
        persistent lines, the detectability test when a source list and a position are there, the beam and the
        array test for an array; write the Seismic Station report and the machine JSONs under reports/seismic/<id>/."""
        import numpy as np
        from . import __version__
        from . import seismic as S
        from . import seismic_detect as D
        from .client_reports import seismic_station_report, write as _write
        st = self.seismic_station(station_id)
        src = os.path.join(self.path, 'seismic', station_id, 'source')
        out = self.seismic_reports_dir(station_id)
        band = tuple(st.get('band_hz') or (1.0, 50.0))
        traces = []
        for name in st['files']:
            trs = S.read_any(os.path.join(src, name))
            if not trs:
                raise WorkspaceError(f'{name} holds no samples')
            traces.append(trs[0])
        response_note = None
        if st.get('stationxml'):
            from . import seismic_response as R
            resp = R.read_stationxml(os.path.join(src, st['stationxml']))
            done = []
            for tr in traces:
                try:
                    chan = R.select(resp, tr)
                    nyq = 0.5 * tr.sample_rate
                    pre = (max(band[0] * 0.5, 0.01), band[0], min(band[1], 0.8 * nyq), min(band[1] * 1.1, 0.9 * nyq))
                    tr2, note = R.remove_response(tr, chan, 'VEL', 60.0, pre)
                    done.append(tr2)
                    response_note = note
                except (LookupError, ValueError) as e:
                    response_note = {'unit': 'counts', 'not_removed': str(e)}
                    done.append(tr)
            traces = done
        info = [t.info() for t in traces]
        tr0 = traces[0]
        spectrum = lines = detect = beam_res = array_detect = None
        spec_h = None
        w_h = min(win_s, max(60.0, tr0.duration_s / 4))
        try:
            f, p = S.welch_psd(tr0.data, tr0.sample_rate)
            m = (f >= band[0]) & (f <= band[1])
            fb, pb = f[m], p[m]
            step = max(1, len(fb) // 2000)
            k = int(np.argmax(pb)) if len(pb) else 0
            spectrum = {'freqs_hz': [round(float(x), 5) for x in fb[::step]], 'psd_db': [round(float(10 * np.log10(max(v, 1e-30))), 2) for v in pb[::step]],
                        'band_hz': list(band), 'df_hz': float(f[1] - f[0]), 'n_bins': int(m.sum()),
                        'peak_hz': float(fb[k]) if len(fb) else None, 'peak_db': float(10 * np.log10(max(pb[k], 1e-30))) if len(pb) else None,
                        'unit': ('counts^2/Hz (response not removed)' if tr0.unit == 'counts' else f'({tr0.unit})^2/Hz (response removed)')}
            with open(os.path.join(out, 'spectrum.json'), 'w', encoding='utf-8') as fh:
                json.dump(spectrum, fh)
            w_h = min(win_s, max(60.0, tr0.duration_s / 4))
            spec = S.spectrogram(tr0, w_h)
            spec_h = spec
            lines = S.persistent_lines(spec, band)
            with open(os.path.join(out, 'lines.json'), 'w', encoding='utf-8') as fh:
                json.dump(lines, fh, indent=1)
        except ValueError as e:
            spectrum = None
            lines = {'lines': [], 'band_hz': list(band), 'windows': 0, 'win_s': win_s, 'snr_db': 6.0, 'min_fraction': 0.5, 'note': str(e),
                     'not_a_measurement': 'the source of any line'}
        st_datum = st.get('datum') or 'UNKNOWN'
        sources = D.load_sources_csv(os.path.join(src, st['sources']), st_datum) if st.get('sources') else []
        if sources and st.get('lat') is not None and st.get('lon') is not None:
            detect = D.detectability_test(tr0, float(st['lat']), float(st['lon']), sources, band, win_s=min(win_s, max(60.0, tr0.duration_s / 4)))
            with open(os.path.join(out, 'detect.json'), 'w', encoding='utf-8') as fh:
                json.dump(detect, fh, indent=1, default=str)
        if st.get('kind') == 'array' and st.get('sensors'):
            from . import seismic_array as AR
            sensors = AR.load_sensors_csv(os.path.join(src, st['sensors']), st_datum)
            b = AR.beam(traces, sensors, (band[0], min(band[1], 0.4 * tr0.sample_rate)), 10.0, 3.0, 61, 'bartlett')
            beam_res = {k: v for k, v in b.items() if not k.startswith('_')}
            with open(os.path.join(out, 'beam.json'), 'w', encoding='utf-8') as fh:
                json.dump(beam_res, fh, indent=1)
            if sources:
                array_detect = AR.array_detectability(traces, sensors, sources, (band[0], min(band[1], 0.4 * tr0.sample_rate)), min(win_s, max(60.0, tr0.duration_s / 4)))
                with open(os.path.join(out, 'array_detect.json'), 'w', encoding='utf-8') as fh:
                    json.dump(array_detect, fh, indent=1, default=str)
        # is this array any good? One bad sensor and every bearing it ever gave was wrong, so this runs
        # before anything that uses the array, and what it costs is measured rather than asserted.
        qc = qc_cost = None
        if st.get('kind') == 'array' and st.get('sensors') and len(traces) >= 3:
            from . import seismic_qc as QC
            try:
                sensors = AR.load_sensors_csv(os.path.join(src, st['sensors']), st_datum)
                qband = (band[0], min(band[1], 0.4 * tr0.sample_rate))
                qc = QC.array_qc(traces, sensors, qband)
                if qc.get('status') == 'DEGRADED':
                    qc_cost = QC.beam_cost(traces, sensors, qc, qband)
                with open(os.path.join(out, 'array_qc.json'), 'w', encoding='utf-8') as fh:
                    json.dump({'qc': qc, 'cost': qc_cost}, fh, indent=1, default=str)
            except ValueError:
                qc = qc_cost = None
        signatures = multi_beam = None
        if st.get('kind') == 'array' and st.get('sensors') and sources and len(traces) >= 3:
            from . import seismic_signature as SG
            import numpy as _np
            sensors = AR.load_sensors_csv(os.path.join(src, st['sensors']), st_datum)
            w = min(win_s, max(60.0, tr0.duration_s / 4))
            signatures = SG.learn_signatures(tr0, sources, (band[0], min(band[1], 0.4 * tr0.sample_rate)), w)
            with open(os.path.join(out, 'signatures.json'), 'w', encoding='utf-8') as fh:
                json.dump(signatures, fh, indent=1)
            # a window where two or more listed rigs were working: the case a single bearing cannot answer
            together = [i for i, n in enumerate(signatures['sources_working']) if n >= 2]
            if signatures['n_with_signature'] >= 2 and together:
                c = signatures['window_centres'][together[len(together) // 2]]
                win = [t.slice(c - w / 2, c + w / 2) for t in traces]
                try:
                    multi_beam = SG.beam_by_signature(win, sensors, signatures['signatures'], (band[0], min(band[1], 0.4 * tr0.sample_rate)))
                    with open(os.path.join(out, 'multi_beam.json'), 'w', encoding='utf-8') as fh:
                        json.dump(multi_beam, fh, indent=1)
                except ValueError:
                    multi_beam = None
        # which datum is everything on, and what does it cost if the answer is an assumption
        datum_check = None
        try:
            from . import geodesy as GD
            pts = []
            if st.get('lat') is not None and st.get('lon') is not None:
                pts.append(GD.Position(float(st['lat']), float(st['lon']), st_datum, 0.0, 'station'))
            for sn in (sensors if (st.get('kind') == 'array' and st.get('sensors')) else []):
                pts.append(GD.Position(sn.lat, sn.lon, sn.datum, sn.elevation_m, f'sensor {sn.sensor_id}'))
            for so in sources:
                pts.append(GD.Position(so.lat, so.lon, so.datum, 0.0, f'source {so.source_id}'))
            if pts:
                datum_check = GD.check_set(pts)
                datum_check['station_datum'] = st_datum
                datum_check['labels'] = {'station': 1 if st.get('lat') is not None else 0,
                                         'sensors': sum(1 for p_ in pts if p_.label.startswith('sensor')),
                                         'sources': sum(1 for p_ in pts if p_.label.startswith('source'))}
                # a row converted on the way in is WGS84 now; say where it came from, or the conversion is invisible
                as_given: dict = {}
                for obj in list(sources) + list(sensors if (st.get('kind') == 'array' and st.get('sensors')) else []):
                    g = getattr(obj, 'datum_as_given', '') or ''
                    if g and g != 'WGS84':
                        as_given[g] = as_given.get(g, 0) + 1
                datum_check['converted_on_load'] = as_given
                if as_given:
                    moved = max((GD.datum_separation_m(float(st.get('lat') or 0.0), float(st.get('lon') or 0.0), g, 'WGS84')['separation_m'] or 0.0)
                                for g in as_given if g != 'UNKNOWN') if any(g != 'UNKNOWN' for g in as_given) else 0.0
                    datum_check['converted_moved_m'] = round(moved, 2)
                with open(os.path.join(out, 'datum.json'), 'w', encoding='utf-8') as fh:
                    json.dump(datum_check, fh, indent=1)
        except (ValueError, KeyError):
            datum_check = None
        harmonics = None
        if spec_h is not None:
            from . import seismic_harmonic as HM
            hband = (band[0], min(band[1], 0.4 * tr0.sample_rate))
            try:
                tl = HM.track_lines(tr0, hband, w_h, spec=spec_h)
                rate = HM.rate_history(tr0, hband, w_h, spec=spec_h)
                ts = HM.tracked_signature(tl, source_id=station_id)
                fam = HM.harmonic_families(ts['freqs_hz'], ts['excess_db'], hband) if ts['status'] == 'OK' else None
                # the signature band learns a source's lines from an array; a single station has the same lines
                # from its own detectability test, so one geophone can still say when each source was working
                if signatures:
                    sigs, sig_from = signatures['signatures'], 'the signature band (an array working several sources at once)'
                elif detect and detect.get('sources'):
                    by_id: dict = {}
                    for r in detect['sources']:          # one machine may hold several rows (it worked, stopped, worked again)
                        g = by_id.setdefault(r['source_id'], {'source_id': r['source_id'], 'freqs_hz': [], 'excess_db': [], 'exclusive_windows': 0})
                        for x in (r.get('lines_hz') or []):
                            if not any(abs(x - y) <= 0.05 for y in g['freqs_hz']):
                                g['freqs_hz'].append(float(x))
                        g['exclusive_windows'] += int(r.get('exclusive_windows') or 0)
                    sigs = []
                    for g in by_id.values():
                        g['freqs_hz'].sort()
                        g['status'] = 'OK' if g['freqs_hz'] else 'NO_LINES'
                        g['detail'] = ("the lines the detectability test found in this source's exclusive windows" if g['freqs_hz']
                                       else "no line stood above its floor in this source's exclusive windows")
                        sigs.append(g)
                    sig_from = "the detectability test's own lines per source"
                else:
                    sigs, sig_from = [], 'nothing: no source list'
                sigfam = HM.signature_families(sigs, hband, tracks=tl) if sigs else None
                # the width a line is looked for in: the largest walk this record's tracker measured, capped
                tol = min(max(float(ts.get('drift_tol_frac') or 0.0), 0.0), 0.5)
                act = [HM.activity_from_signature(tr0, sg, hband, w_h, sources=sources, drift_tol_frac=tol, spec=spec_h)
                       for sg in (sigs if sources else [])]
                harmonics = {'protocol': 'workspace.harmonics/1', 'band_hz': list(hband), 'win_s': w_h, 'drift_tol_frac': round(tol, 4),
                             'signatures_from': sig_from, 'line_tracks': tl, 'rate': rate, 'tracked_signature': ts, 'families': fam,
                             'signature_families': sigfam, 'activity': act}
                with open(os.path.join(out, 'harmonics.json'), 'w', encoding='utf-8') as fh:
                    json.dump(harmonics, fh, indent=1, default=str)
            except ValueError:
                harmonics = None
        unlisted = None
        if harmonics and st.get('kind') == 'array' and st.get('sensors') and len(traces) >= 3:
            from . import seismic_unlisted as UL
            try:
                sens_u = AR.load_sensors_csv(os.path.join(src, st['sensors']), st_datum)
                uband = (band[0], min(band[1], 0.4 * tr0.sample_rate))
                unlisted = UL.find_unlisted(traces, sens_u, (signatures['signatures'] if signatures else []), uband, w_h,
                                            tracks=harmonics['line_tracks'], name=st.get('display') or station_id)
                with open(os.path.join(out, 'unlisted.json'), 'w', encoding='utf-8') as fh:
                    json.dump(unlisted, fh, indent=1, default=str)
            except (ValueError, KeyError):
                unlisted = None
        doc = seismic_station_report(st, info, spectrum and {k: v for k, v in spectrum.items() if k not in ('freqs_hz', 'psd_db')}, lines, detect, beam_res, array_detect,
                                     response_note, program_version=__version__, harmonics=harmonics, datum_check=datum_check,
                                     array_qc=({'qc': qc, 'cost': qc_cost} if qc else None), unlisted=unlisted)
        paths = _write(doc, out, basename='seismic_station_report')
        summary = {'station_id': station_id, 'report_id': doc.report_id, 'generated_utc': doc.data['evaluated_at_utc'], 'unit': (response_note or {}).get('unit', 'counts'),
                   'records': len(info), 'hours': round(sum(i['duration_s'] for i in info) / 3600.0 / max(len(info), 1), 2), 'lines': len(lines['lines']) if lines else 0,
                   'detect_status': detect.get('status') if detect else None,
                   'detected': sum(1 for r in detect['sources'] if r['verdict'] == 'DETECTED') if detect else None, 'n_sources': len(sources),
                   'radius': detect.get('radius') if detect else None,
                   'beam_back_azimuth_deg': beam_res['back_azimuth_deg'] if beam_res else None,
                   'pointed': sum(1 for r in array_detect['sources'] if r['verdict'] == 'POINTED') if array_detect else None,
                   'signatures': (signatures['n_with_signature'] if signatures else None),
                   'multi_pointed': (multi_beam['n_pointed'] if multi_beam else None),
                   'line_tracks': (harmonics['line_tracks']['n_tracks'] if harmonics else None),
                   'fundamental_hz': ((harmonics.get('families') or {}).get('fundamental_hz') if harmonics else None),
                   'rate_status': (harmonics['rate']['status'] if harmonics else None),
                   'sources_with_rate': ((harmonics.get('signature_families') or {}).get('n_with_fundamental') if harmonics else None),
                   'unattributed_lines': (len((((harmonics.get('signature_families') or {}).get('attribution') or {}).get('unattributed') or []))
                                          if harmonics and harmonics.get('signature_families') else None),
                   'activity_differs': (sum(1 for a in harmonics['activity'] if a.get('verdict') == 'DIFFERS') if harmonics else None),
                   'unlisted_status': (unlisted['status'] if unlisted else None),
                   'unlisted_found': (unlisted.get('n_found') if unlisted else None),
                   'qc_status': (qc['status'] if qc else None), 'qc_usable': (qc['n_usable'] if qc else None),
                   'qc_faults': (sum(len(v) for v in (qc.get('faults') or {}).values()) if qc else None),
                   'qc_bearing_change_deg': ((qc_cost or {}).get('bearing_change_deg') if qc_cost else None),
                   'datum': st_datum, 'datum_status': (datum_check['status'] if datum_check else None),
                   'datum_worst_m': (datum_check['worst_separation_m'] if datum_check else None),
                   'datum_if_wrong_m': ((datum_check.get('if_wrong') or {}).get('separation_m') if datum_check else None),
                   'response_removed': bool(response_note and response_note.get('unit', 'counts') != 'counts'),
                   'response_note': (response_note or {}).get('not_removed'), 'report': paths['html']}
        with open(os.path.join(out, 'summary.json'), 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=1)
        self.audit(actor, 'seismic.refresh', {'id': station_id, 'detected': summary['detected'], 'lines': summary['lines'], 'unit': summary['unit']},
                   inputs=[os.path.join(src, n) for n in st['files']])
        return summary

    # -- tracks: two or more array stations crossed over time -------------------------------
    def tracks(self) -> List[dict]:
        out = []
        for tid in self.manifest.get('tracks', []):
            p = os.path.join(self.path, 'tracks', tid, 'track.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
        return out

    def track(self, track_id: str) -> dict:
        p = os.path.join(self.path, 'tracks', track_id, 'track.json')
        if track_id not in self.manifest.get('tracks', []) or not os.path.isfile(p):
            raise WorkspaceError(f'no such track: {track_id}')
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def add_track(self, display: str, station_ids: List[str], actor: str = 'system', truth_csv: Optional[str] = None, band: Optional[List[float]] = None,
                  win_s: float = 600.0, note: str = '', truth_filename: Optional[str] = None) -> dict:
        """A track: two or more array stations of this workspace whose bearings are crossed window by window, with
        an optional ground-truth CSV (point_id, lat, lon, utc) - the lateral's surveyed points, or a permit's
        surface hole with its date - copied in and hashed."""
        if len(station_ids) < 2:
            raise WorkspaceError('a track needs two or more array stations')
        from .seismic_array import load_sensors_csv
        for sid in station_ids:
            st = self.seismic_station(sid)
            if st.get('kind') != 'array' or not st.get('sensors'):
                raise WorkspaceError(f'{sid} is not an array station (a track crosses arrays)')
        tid = slug(display)
        if tid in self.manifest.setdefault('tracks', []):
            raise WorkspaceError(f'track already exists: {tid}')
        d = os.path.join(self.path, 'tracks', tid)
        os.makedirs(d, exist_ok=True)
        hashes = {}
        truth_name = None
        if truth_csv:
            if not os.path.isfile(truth_csv):
                raise WorkspaceError(f'truth file not found: {truth_csv}')
            from .seismic_track import load_truth_csv
            try:
                n_truth = len(load_truth_csv(truth_csv))
            except (KeyError, ValueError) as e:
                raise WorkspaceError(f'the truth CSV needs point_id, lat, lon, utc columns: {e}')
            if n_truth < 1:
                raise WorkspaceError('the truth CSV holds no points')
            truth_name = os.path.basename(truth_filename or truth_csv)
            shutil.copyfile(truth_csv, os.path.join(d, truth_name))
            hashes[truth_name] = sha256_file(os.path.join(d, truth_name))
        track = {'id': tid, 'display': display, 'stations': list(station_ids), 'truth': truth_name, 'band_hz': list(band) if band else [1.0, 20.0],
                 'win_s': win_s, 'note': note, 'sha256': hashes, 'added_utc': utc_now_iso(), 'added_by': actor}
        with open(os.path.join(d, 'track.json'), 'w', encoding='utf-8') as f:
            json.dump(track, f, indent=1)
        self.manifest['tracks'].append(tid)
        self._save()
        self.audit(actor, 'track.add', {'id': tid, 'stations': station_ids, 'truth': truth_name, 'display': display},
                   inputs=[os.path.join(d, truth_name)] if truth_name else None)
        return track

    def remove_track(self, track_id: str, actor: str = 'system') -> None:
        if track_id not in self.manifest.get('tracks', []):
            raise WorkspaceError(f'no such track: {track_id}')
        self.manifest['tracks'].remove(track_id)
        self._save()
        src = os.path.join(self.path, 'tracks', track_id)
        if os.path.isdir(src):
            os.rename(src, src + '.removed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        self.audit(actor, 'track.remove', {'id': track_id})

    def track_reports_dir(self, track_id: str) -> str:
        return self.dir('reports', 'seismic', 'tracks', track_id)

    def refresh_track(self, track_id: str, actor: str = 'system') -> dict:
        """Bearing histories for every array of the track (the response removed where a station file is there),
        the position history, the verdict against the truth when one was given, the Seismic Track Report."""
        from . import __version__
        from . import seismic as S
        from . import seismic_track as T
        from . import seismic_array as AR
        from .client_reports import seismic_track_report, write as _write
        tk = self.track(track_id)
        out = self.track_reports_dir(track_id)
        band = tuple(tk.get('band_hz') or (1.0, 20.0))
        hists = []
        inputs = []
        traces_by_station: Dict[str, list] = {}
        for sid in tk['stations']:
            st = self.seismic_station(sid)
            src = os.path.join(self.path, 'seismic', sid, 'source')
            traces = []
            for name in st['files']:
                trs = S.read_any(os.path.join(src, name))
                if not trs:
                    raise WorkspaceError(f'{name} holds no samples')
                traces.append(trs[0])
                inputs.append(os.path.join(src, name))
            if st.get('stationxml'):
                from . import seismic_response as R
                resp = R.read_stationxml(os.path.join(src, st['stationxml']))
                done = []
                for tr in traces:
                    try:
                        chan = R.select(resp, tr)
                        nyq = 0.5 * tr.sample_rate
                        pre = (max(band[0] * 0.5, 0.01), band[0], min(band[1], 0.8 * nyq), min(band[1] * 1.1, 0.9 * nyq))
                        done.append(R.remove_response(tr, chan, 'VEL', 60.0, pre)[0])
                    except (LookupError, ValueError):
                        done.append(tr)
                traces = done
            sensors = AR.load_sensors_csv(os.path.join(src, st['sensors']))
            traces_by_station[sid] = traces
            h = T.bearing_history(traces, sensors, band, float(tk.get('win_s') or 600.0))
            h['array']['name'] = st['display']
            h['array']['station_id'] = sid
            hists.append(h)
            with open(os.path.join(out, f'bearings_{sid}.json'), 'w', encoding='utf-8') as fh:
                json.dump(h, fh, indent=1)
        truth = T.load_truth_csv(os.path.join(self.path, 'tracks', track_id, tk['truth'])) if tk.get('truth') else []
        pos = T.position_history(hists)
        with open(os.path.join(out, 'positions.json'), 'w', encoding='utf-8') as fh:
            json.dump(pos, fh, indent=1)
        # several rigs at once: when the arrays carry a rigs list, each rig is learned while it worked alone
        # and then beamed on its own lines, so every rig that two arrays point at gets its own track
        multi = None
        learned = None
        first_sources = next((self.seismic_station(sid).get('sources') for sid in tk['stations'] if self.seismic_station(sid).get('sources')), None)
        if first_sources:
            from . import seismic_signature as SG
            from . import seismic_detect as D
            sid0 = next(sid for sid in tk['stations'] if self.seismic_station(sid).get('sources'))
            srcs = D.load_sources_csv(os.path.join(self.path, 'seismic', sid0, 'source', first_sources))
            learned = SG.learn_signatures(traces_by_station[sid0][0], srcs, band, float(tk.get('win_s') or 600.0))
            with open(os.path.join(out, 'signatures.json'), 'w', encoding='utf-8') as fh:
                json.dump(learned, fh, indent=1)
            if learned['n_with_signature'] >= 1:
                per = []
                for sid in tk['stations']:
                    st2 = self.seismic_station(sid)
                    sens2 = AR.load_sensors_csv(os.path.join(self.path, 'seismic', sid, 'source', st2['sensors']))
                    try:
                        per.append(SG.multi_bearing_history(traces_by_station[sid], sens2, learned['signatures'], band,
                                                            float(tk.get('win_s') or 600.0), name=st2['display']))
                    except ValueError:
                        pass
                if len(per) >= 2:
                    by_src = {}
                    for ptid, pts in _group_truth(truth).items():
                        by_src[ptid] = pts
                    multi = SG.multi_track(per, by_src or None)
                    with open(os.path.join(out, 'multi_track.json'), 'w', encoding='utf-8') as fh:
                        json.dump(multi, fh, indent=1, default=str)
        ver = None
        if truth:
            ver = T.track_verdict(pos, truth)
            with open(os.path.join(out, 'verdict.json'), 'w', encoding='utf-8') as fh:
                json.dump(ver, fh, indent=1)
        doc = seismic_track_report(tk, hists, pos, ver, program_version=__version__, multi=multi, signatures=(learned if first_sources else None))
        paths = _write(doc, out, basename='seismic_track_report')
        tr = pos.get('track') or {}
        summary = {'track_id': track_id, 'report_id': doc.report_id, 'generated_utc': doc.data['evaluated_at_utc'], 'arrays': len(hists),
                   'windows': pos['n_windows'], 'positions': pos['n_positions'], 'segments': tr.get('n_segments'), 'heading_deg': tr.get('heading_deg'),
                   'length_km': tr.get('length_km'), 'ellipse_major_median_km': (tr.get('ellipse_major_km') or {}).get('median'),
                   'verdict': ver['verdict'] if ver else None, 'hit_fraction': ver['hit_fraction'] if ver else None, 'n_truth': len(truth), 'report': paths['html'],
                   'sources_learned': (multi and len([x for x in (multi.get('source_ids') or [])])) or None,
                   'sources_tracked': (multi['n_tracked'] if multi else None)}
        with open(os.path.join(out, 'summary.json'), 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=1)
        self.audit(actor, 'track.refresh', {'id': track_id, 'positions': summary['positions'], 'verdict': summary['verdict'], 'heading_deg': summary['heading_deg']}, inputs=inputs)
        return summary

    def track_results(self, track_id: str) -> dict:
        out = os.path.join(self.path, 'reports', 'seismic', 'tracks', track_id)
        res = {}
        for name in ('summary', 'positions', 'verdict', 'multi_track'):
            p = os.path.join(out, name + '.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as fh:
                    res[name] = json.load(fh)
        res['bearings'] = {}
        if os.path.isdir(out):
            for n in sorted(os.listdir(out)):
                if n.startswith('bearings_') and n.endswith('.json'):
                    with open(os.path.join(out, n), encoding='utf-8') as fh:
                        res['bearings'][n[len('bearings_'):-5]] = json.load(fh)
        return res

    SAR_ID = 'SIMULATION'

    def sar_film(self, actor: str = 'system', seed: int = 5, hours: float = 8.0, step_s: float = 600.0, band=(1.0, 20.0), method: str = 'bartlett', scene: str = 'rigs') -> dict:
        """The SAR panel's film: the labelled synthetic scene played forward in time, written under
        reports/seismic/SIMULATION/ so the page's /reports/ route serves it. Never a real record."""
        from . import seismic_film as FM
        film = FM.sar_film(seed=seed, hours=hours, step_s=step_s, band=tuple(band), method=method, scene=scene)
        out = self.seismic_reports_dir(self.SAR_ID)
        path = FM.write_film(film, os.path.join(out, 'sar_film.json'))
        summary = FM.film_summary(film)
        summary.update({'path': path, 'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'seed': seed,
                        'params': {'seed': seed, 'hours': hours, 'step_s': step_s, 'band_hz': list(band), 'method': method, 'scene': scene}})
        with open(os.path.join(out, 'sar_film.summary.json'), 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=1)
        self.audit(actor, 'seismic.sar_film', {'label': summary['label'], 'frames': summary['frames'], 'hours': hours, 'method': method, 'seed': seed, 'scene': scene})
        return summary

    def sar_film_summary(self) -> Optional[dict]:
        p = os.path.join(self.path, 'reports', 'seismic', self.SAR_ID, 'sar_film.summary.json')
        if not os.path.isfile(p):
            return None
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)

    def refresh_all(self, actor: str = 'system') -> dict:
        """Every report from its source again: the dashboard (every well) and every seismic station. The
        Audit/Update page's data update. Errors are collected, never hidden; the result lists them."""
        errors: List[str] = []
        n_wells = 0
        try:
            self.refresh_dashboard(actor=actor)
            n_wells = len(self.wells())
        except Exception as e:                                   # a refresh must not stop the stations behind it
            errors.append(f'dashboard: {e}')
        n_seis = 0
        for st in self.seismic_stations():
            try:
                self.refresh_seismic(st['id'], actor=actor)
                n_seis += 1
            except Exception as e:
                errors.append(f"seismic {st['id']}: {e}")
        n_tracks = 0
        for tk in self.tracks():
            try:
                self.refresh_track(tk['id'], actor=actor)
                n_tracks += 1
            except Exception as e:
                errors.append(f"track {tk['id']}: {e}")
        self.audit(actor, 'workspace.refresh_all', {'wells': n_wells, 'seismic': n_seis, 'tracks': n_tracks, 'errors': len(errors)})
        return {'wells': n_wells, 'seismic': n_seis, 'tracks': n_tracks, 'errors': errors}

    def month_end(self, period: str, actor: str = 'system', refresh: bool = True, catalog_csv: Optional[str] = None) -> dict:
        """The month's deliverable: the refresh for the period, then every report collected into
        reports/month_end/<period>/ with an index and a manifest, and zipped. 'previous' is the last closed month."""
        from . import month_end as ME
        if period == 'previous':
            period = ME.previous_period()
        return ME.assemble(self, period, actor=actor, refresh=refresh, catalog_csv=catalog_csv)

    def report_ages(self) -> List[dict]:
        """Each report family's generated time against its source's modification time - the staleness table."""
        rows = []
        dj = os.path.join(self.reports_dir, 'dashboard.json')
        if os.path.isfile(dj):
            gen = datetime.fromtimestamp(os.path.getmtime(dj), timezone.utc)
            for w in self.wells():
                srcp = os.path.join(self.path, 'wells', w['id'], 'source', w['files'][0]) if w.get('kind') == 'file' and w.get('files') else None
                src_m = datetime.fromtimestamp(os.path.getmtime(srcp), timezone.utc) if srcp and os.path.isfile(srcp) else None
                rows.append({'kind': 'well', 'id': w['id'], 'generated_utc': gen.strftime('%Y-%m-%dT%H:%M:%SZ'),
                             'source_utc': src_m.strftime('%Y-%m-%dT%H:%M:%SZ') if src_m else None,
                             'stale': bool(src_m and src_m > gen)})
        for st in self.seismic_stations():
            sm = self.seismic_results(st['id']).get('summary')
            src_dir = os.path.join(self.path, 'seismic', st['id'], 'source')
            src_m = max((os.path.getmtime(os.path.join(src_dir, n)) for n in st.get('files', []) if os.path.isfile(os.path.join(src_dir, n))), default=None)
            src_iso = datetime.fromtimestamp(src_m, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if src_m else None
            gen_iso = sm.get('generated_utc') if sm else None
            rows.append({'kind': 'seismic', 'id': st['id'], 'generated_utc': gen_iso, 'source_utc': src_iso,
                         'stale': bool(gen_iso is None or (src_iso and src_iso > gen_iso))})
        for tk in self.tracks():
            sm = self.track_results(tk['id']).get('summary')
            gen_iso = sm.get('generated_utc') if sm else None
            rows.append({'kind': 'track', 'id': tk['id'], 'generated_utc': gen_iso, 'source_utc': tk.get('added_utc'),
                         'stale': bool(gen_iso is None or (tk.get('added_utc') and tk['added_utc'] > gen_iso))})
        return rows

    def seismic_results(self, station_id: str) -> dict:
        out = os.path.join(self.path, 'reports', 'seismic', station_id)
        res = {}
        for name in ('summary', 'spectrum', 'lines', 'detect', 'beam', 'array_detect', 'signatures', 'multi_beam', 'harmonics', 'datum', 'array_qc', 'unlisted'):
            p = os.path.join(out, name + '.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as fh:
                    res[name] = json.load(fh)
        return res

    # -- a site: the engagement, not the leg --------------------------------------------------
    def sites(self) -> List[dict]:
        out = []
        for sid in self.manifest.get('sites', []):
            p = os.path.join(self.path, 'sites', sid, 'site.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
        return out

    def site(self, site_id: str) -> dict:
        p = os.path.join(self.path, 'sites', site_id, 'site.json')
        if not os.path.isfile(p):
            raise WorkspaceError(f'no such site: {site_id}')
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def add_site(self, display: str, wells: Optional[List[str]] = None, seismic: Optional[List[str]] = None,
                 tracks: Optional[List[str]] = None, client: str = '', note: str = '', actor: str = 'system') -> dict:
        """A site is the engagement: the wells, the seismic stations and the tracks that belong to one client's
        ground, held together so there is one thing to refresh and one thing to hand over.

        Until now the workspace held wells, stations and tracks as peers with nothing owning them. That made
        GEA three tools in one package rather than one program: nobody could ask what a client has, or what
        every leg of it says, without knowing the ids by heart."""
        sid = slug(display)
        if sid in self.manifest.setdefault('sites', []):
            raise WorkspaceError(f'site already exists: {sid}')
        have_w = {w['id'] for w in self.wells()}
        have_s = set(self.manifest.get('seismic', []))
        have_t = set(self.manifest.get('tracks', []))
        miss = ([f'well {x}' for x in (wells or []) if x not in have_w]
                + [f'seismic station {x}' for x in (seismic or []) if x not in have_s]
                + [f'track {x}' for x in (tracks or []) if x not in have_t])
        if miss:
            raise WorkspaceError('a site can only hold what this workspace holds; not here: ' + ', '.join(miss))
        if not (wells or seismic or tracks):
            raise WorkspaceError('a site with nothing in it is not a site: give --wells, --seismic or --tracks')
        site = {'id': sid, 'display': display, 'client': client, 'note': note,
                'wells': list(wells or []), 'seismic': list(seismic or []), 'tracks': list(tracks or []),
                'added_utc': utc_now_iso(), 'added_by': actor}
        os.makedirs(os.path.join(self.path, 'sites', sid), exist_ok=True)
        with open(os.path.join(self.path, 'sites', sid, 'site.json'), 'w', encoding='utf-8') as f:
            json.dump(site, f, indent=1)
        self.manifest['sites'].append(sid)
        self._save()
        self.audit(actor, 'site.add', {'id': sid, 'wells': len(site['wells']), 'seismic': len(site['seismic']), 'tracks': len(site['tracks'])})
        return site

    def remove_site(self, site_id: str, actor: str = 'system') -> None:
        if site_id not in self.manifest.get('sites', []):
            raise WorkspaceError(f'no such site: {site_id}')
        self.manifest['sites'].remove(site_id)
        self._save()
        d = os.path.join(self.path, 'sites', site_id)
        if os.path.isdir(d):
            os.rename(d, d + '.removed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        self.audit(actor, 'site.remove', {'id': site_id})

    def site_summary(self, site_id: str) -> dict:
        """What every leg of one site says, and whether any of it is older than what it was made from."""
        st = self.site(site_id)
        wells, seis, trks, warn = [], [], [], []
        for wid in st['wells']:
            try:
                w = self.well(wid)
            except WorkspaceError as e:
                warn.append(str(e)); continue
            rep = os.path.join(self.path, 'reports', 'wells', wid)
            idx = os.path.join(rep, 'index.html')
            wells.append({'id': wid, 'display': w.get('display'), 'kind': w.get('kind'),
                          'report': idx if os.path.isfile(idx) else None,
                          'refreshed_utc': (datetime.fromtimestamp(os.path.getmtime(idx), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                                            if os.path.isfile(idx) else None)})
            if not os.path.isfile(idx):
                warn.append(f'well {wid}: never refreshed')
        inv = {s['id']: s for s in self.seismic_inventory()['stations']}
        for sid2 in st['seismic']:
            row = inv.get(sid2)
            if row is None:
                warn.append(f'seismic station {sid2}: not in this workspace any more'); continue
            seis.append({'id': sid2, 'display': row['display'], 'kind': row['kind'], 'refreshed_utc': row['refreshed_utc'],
                         'detect_status': row['detect_status'], 'qc_status': row['qc_status'], 'datum_status': row['datum_status'],
                         'hours': sum(r['hours'] or 0.0 for r in row['records']), 'stale': [k for k, v in row['ran'].items() if v == 'stale']})
            if not row['ran']:
                warn.append(f'seismic station {sid2}: never refreshed')
            elif any(v == 'stale' for v in row['ran'].values()):
                warn.append(f'seismic station {sid2}: results older than the record they came from')
        for tid in st['tracks']:
            try:
                tk = self.track(tid)
            except (WorkspaceError, KeyError) as e:
                warn.append(f'track {tid}: {e}'); continue
            tres = self.track_results(tid)
            mt = tres.get('multi_track') or {}
            trks.append({'id': tid, 'display': tk.get('display'), 'stations': tk.get('stations'),
                         'n_tracked': mt.get('n_tracked'), 'verdict': (tres.get('verdict') or {}).get('verdict')})
        return {'protocol': 'workspace.site_summary/1', 'site': st, 'generated_utc': utc_now_iso(),
                'wells': wells, 'seismic': seis, 'tracks': trks, 'warnings': warn,
                'counts': {'wells': len(wells), 'seismic': len(seis), 'tracks': len(trks)},
                'basis': "each member's own last run, collected; nothing is recomputed here",
                'not_a_measurement': ['a site as a boundary on the ground: it is a list of what belongs to one engagement',
                                      "a leg's verdict that has not been refreshed since its source changed"]}

    # -- the Seismicity Response Area --------------------------------------------------------------------
    def well_stream(self, well_id: str):
        """The well's measured stream, read from its source, whatever kind of well it is."""
        from . import production_live_stream
        from .files import read_any
        w = self.well(well_id)
        if w['kind'] == 'catalog':
            stream, _ = production_live_stream(w['source']['entry'], w['source']['well'], w.get('station_md_ft') or 10000.0)
            return stream
        src = (os.path.join(self.path, 'wells', well_id, 'source', w['files'][0]) if w['kind'] == 'file'
               else self.latest_live_stream_csv(well_id))
        if not src:
            raise WorkspaceError(f'well {well_id}: no recorded stream yet')
        return read_any(src)

    def set_well(self, well_id: str, surface: Optional[dict] = None, disposal: Optional[dict] = None, actor: str = 'system') -> dict:
        """What the operator declares about a well and this program cannot measure: where its surface location
        is and on which datum; that it is a disposal well, its API and UIC numbers, its depth tier by the named
        formation, which of its channels are the surface injection pressure and the injection rate, and how its
        bottomhole pressure is known. Every one of these is recorded as a declaration and audited."""
        from . import geodesy as GD
        from .sra import BHP_METHODS, DEPTH_TIERS, RATE_UNITS
        w = self.well(well_id)
        if surface:
            lat, lon = surface.get('lat'), surface.get('lon')
            if lat is None or lon is None:
                raise WorkspaceError('a surface position needs --lat and --lon')
            d = GD.datum_name(surface.get('datum'))
            w['surface'] = {'lat': float(lat), 'lon': float(lon), 'datum': d}
        if disposal:
            cur = dict(w.get('disposal') or {})
            for k, v in disposal.items():
                if v is None:
                    continue
                if k == 'depth_tier' and v not in DEPTH_TIERS:
                    raise WorkspaceError(f"depth tier must be one of {DEPTH_TIERS}, got {v!r}")
                if k == 'bhp_method' and v not in BHP_METHODS:
                    raise WorkspaceError(f"bottomhole pressure method must be one of {tuple(BHP_METHODS)}, got {v!r}")
                if k == 'rate_unit' and str(v).lower() not in RATE_UNITS:
                    raise WorkspaceError(f"rate unit must be one of {tuple(RATE_UNITS)}, got {v!r}")
                cur[k] = v
            cur.setdefault('role', 'disposal')
            w['disposal'] = cur
        with open(os.path.join(self.well_dir(well_id), 'well.json'), 'w', encoding='utf-8') as f:
            json.dump(w, f, indent=1)
        self.audit(actor, 'well.declare', {'id': well_id, 'surface': w.get('surface'), 'disposal': w.get('disposal')})
        return w

    def well_identity(self, well_id: str) -> dict:
        """The well as PPDM's "What is a Well" names it, from its declarations and the site it belongs to."""
        from . import ppdm as P
        w = self.well(well_id)
        site = next((self.site(sid) for sid in self.manifest.get('sites', []) if well_id in (self.site(sid).get('wells') or [])), None)
        chans: List[str] = []
        try:
            st = self.well_stream(well_id)
            chans = list(getattr(st, 'channels', None) or [])
        except Exception:
            chans = []
        return P.well_identity(w, site, channels=chans)

    def sra_define(self, site_id: str, actor: str = 'system', **kw) -> dict:
        """The area a site answers to, recorded on the site."""
        from .sra import define
        st = self.site(site_id)
        st['sra'] = define(**kw)
        with open(os.path.join(self.path, 'sites', site_id, 'site.json'), 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1)
        self.audit(actor, 'site.sra_define', {'id': site_id, 'name': st['sra']['name'], 'radius_km': st['sra']['radius_km'],
                                               'plan_date': st['sra']['plan_date']})
        return st['sra']

    def sra_packet(self, site_id: str, catalog_csv: Optional[str] = None, aftershocks: Optional[List[str]] = None,
                   start: Optional[str] = None, end: Optional[str] = None, as_of: Optional[datetime] = None) -> dict:
        """The packet for one site: membership, the daily record per well, the seismicity if a catalogue was
        handed in, the schedule and the gaps. Nothing here fetches anything."""
        from . import sra as SR
        st = self.site(site_id)
        if not st.get('sra'):
            raise WorkspaceError(f'site {site_id} has no Seismicity Response Area defined: gea workspace --action sra-define')
        sra = st['sra']
        wells = [self.well(wid) for wid in st['wells']]
        stations = [self.seismic_station(sid) for sid in st['seismic'] if sid in self.manifest.get('seismic', [])]
        mem = SR.membership(sra, wells, stations)
        t_start = SR._parse_dt(start) if start else None
        t_end = SR._parse_dt(end) if end else None
        daily = {}
        for w in wells:
            if not (w.get('disposal') or {}).get('channel_pressure') and not (w.get('disposal') or {}).get('channel_rate'):
                continue                                   # nothing declared: nothing to record, and the membership names it
            try:
                daily[w['id']] = SR.daily_records(self.well_stream(w['id']), w['disposal'], t_start, t_end)
            except WorkspaceError as e:
                daily[w['id']] = {'status': 'REFUSED', 'detail': str(e), 'days': [], 'parameters': {}}
        seis = None
        if catalog_csv:
            seis = SR.seismicity(sra, SR.read_catalog(catalog_csv), as_of=as_of, aftershocks=aftershocks or [])
        return SR.packet(sra, mem, daily, seis, as_of=as_of, site=st, association=self.association(site_id), wells=wells)

    def write_sra_packet(self, site_id: str, catalog_csv: Optional[str] = None, aftershocks: Optional[List[str]] = None,
                         start: Optional[str] = None, end: Optional[str] = None, actor: str = 'system') -> dict:
        """The deliverable: the packet report, its machine record, and the daily export the tool is fed."""
        from . import __version__
        from . import sra as SR
        from .client_reports import sra_packet_report, write
        pk = self.sra_packet(site_id, catalog_csv, aftershocks, start, end)
        out = self.dir('reports', 'sites', site_id)
        doc = sra_packet_report(pk, program_version=__version__)
        paths = write(doc, out, 'sra_packet')
        rows = []
        # the daily export: every inside well's days, one line each
        daily = {}
        for w in pk['wells']:
            if w['inside'] and w.get('daily_status') not in (None, 'NOT RUN', 'REFUSED'):
                try:
                    daily[w['id']] = SR.daily_records(self.well_stream(w['id']), self.well(w['id'])['disposal'],
                                                      SR._parse_dt(start) if start else None, SR._parse_dt(end) if end else None)
                except WorkspaceError:
                    continue
        for w in pk['wells']:
            for day in daily.get(w['id'], {}).get('days', []):
                rows.append({**day, 'well': w['id'], 'api_number': w['api_number'], 'uic_number': w['uic_number'], 'bhp_method': w['bhp_method']})
        exp = SR.export_daily_csv(rows, os.path.join(out, 'sra_daily_export.csv'))
        paths['daily_csv'] = exp['path']
        self.audit(actor, 'site.sra_packet', {'id': site_id, 'status': pk['status'], 'gaps': len(pk['gaps']), 'rows': exp['rows'],
                                               'catalog': catalog_csv or None})
        return {'site': site_id, 'status': pk['status'], 'paths': paths, 'packet': pk, 'export_rows': exp['rows']}

    # -- association: the site's stations heard the same thing, or they did not ---------------------------------
    # -- machine vibration: the pump's record through ISO 20816-3 and the envelope -------------------------------
    def vibration_report(self, well_id: str, path: str, channel: Optional[str] = None, unit: str = 'g', rpm: Optional[float] = None,
                         group: Optional[int] = None, support: Optional[str] = None, bearing: Optional[dict] = None,
                         band_hz: Optional[List[float]] = None, sensitivity: Optional[float] = None, label: str = '', actor: str = 'system') -> dict:
        """A vibration record of the well's pump (or any machine the well is served by), copied in beside the well
        under machine/ with its hash, assessed, and written as reports/wells/<id>/vibration_report.*. Every
        declaration goes into the record and the audit."""
        from . import vibration as V
        from . import __version__
        from .client_reports import vibration_report as _doc, write as _write
        w = self.well(well_id)
        if not os.path.isfile(path):
            raise WorkspaceError(f'no such record: {path}')
        rec = V.read_record(path, channel)
        mdir = self.dir('wells', well_id, 'machine')
        dst = os.path.join(mdir, os.path.basename(path))
        if os.path.abspath(dst) != os.path.abspath(path):
            shutil.copy2(path, dst)
        r = V.assess(rec['x'], rec['fs'], unit, rpm, group=group, support=support, bearing=bearing,
                     band_hz=tuple(band_hz) if band_hz else None, label=label or f"{w.get('display') or well_id} machine", sensitivity=sensitivity)
        r['record'] = {'name': rec['name'], 'channel': rec['channel'], 'kind': rec['kind'], 'fs_hz': rec['fs'], 'seconds': rec['seconds'],
                       'unit_in_file': rec['unit_in_file'], 'sha256': sha256_file(dst), 'path': dst, 'notes': rec['notes']}
        r['gaps'] = list(r['gaps']) + list(rec['notes'])
        out = self.dir('reports', 'wells', well_id)
        paths = _write(_doc(r, program_version=__version__), out, basename='vibration_report')
        self.audit(actor, 'well.vibration', {'id': well_id, 'record': rec['name'], 'channel': rec['channel'], 'status': r['status'],
                                              'zone': (r.get('zone') or {}).get('zone'), 'rms_mm_s': (r.get('broadband') or {}).get('rms_mm_s'),
                                              'matched': ((r.get('envelope') or {}).get('match') or {}).get('matched'),
                                              'declared': r['declared']}, inputs=[dst])
        return {**r, 'paths': paths}

    def associate_site(self, site_id: str, vp_km_s: float = 5.8, depth_km: float = 6.0, band: Optional[List[float]] = None,
                       start: Optional[str] = None, end: Optional[str] = None, catalog_csv: Optional[str] = None,
                       rms_tol_s: float = 0.15, actor: str = 'system') -> dict:
        """Picks on every station of the site, associated into events under a declared model, located, and set
        against the catalogue if one is handed in. An array is one station: its sensors' picks are reduced to one
        by the median, at the station's own position. Written to reports/sites/<id>/association.json."""
        from . import seismic as S
        from . import seismic_assoc as SA
        from . import sra as SR
        st = self.site(site_id)
        sids = [x for x in st['seismic'] if x in self.manifest.get('seismic', [])]
        if len(sids) < 3:
            raise WorkspaceError(f'site {site_id} has {len(sids)} seismic station(s): association needs three or more')
        t_start = SR._parse_dt(start).timestamp() if start else None
        t_end = SR._parse_dt(end).timestamp() if end else None
        stations: Dict[str, SA.Station] = {}
        all_picks: List[SA.Pick] = []
        per_station = {}
        for sid in sids:
            srec = self.seismic_station(sid)
            if srec.get('lat') is None or srec.get('lon') is None:
                per_station[sid] = {'status': 'NO POSITION', 'picks': 0}
                continue
            stations[sid] = SA.Station(sid, float(srec['lat']), float(srec['lon']), srec.get('datum') or 'WGS84', srec.get('kind') or 'single')
            src = os.path.join(self.path, 'seismic', sid, 'source')
            b = tuple(band or srec.get('band_hz') or (2.0, 20.0))
            raw: List[SA.Pick] = []
            codes = None
            for name in srec['files']:
                for tr in S.read_any(os.path.join(src, name)):
                    if codes is None:                   # the record's own FDSN codes, kept for the QuakeML export's waveformID
                        codes = {'network': tr.network, 'station': tr.station, 'location': tr.location, 'channel': tr.channel}
                    if t_start is not None or t_end is not None:
                        tr = tr.slice(t_start if t_start is not None else tr.starttime, t_end if t_end is not None else tr.endtime)
                    if tr.npts > 0:
                        for pk in SA.picks(tr, band=(float(b[0]), float(b[1]))):
                            pk.station = sid
                            raw.append(pk)
            if srec.get('kind') == 'array' and len(srec['files']) > 1:
                raw = SA.collapse_array_picks(raw, sid)
            per_station[sid] = {'status': 'PICKED', 'picks': len(raw), 'kind': srec.get('kind'), 'files': len(srec['files']), 'band_hz': list(b),
                                'stream': codes}
            all_picks += raw
        if len(stations) < 3:
            raise WorkspaceError(f'only {len(stations)} station(s) of site {site_id} have a position: association needs three or more')
        res = SA.associate(all_picks, stations, vp_km_s, depth_km, rms_tol_s=rms_tol_s)
        cmp = None
        if catalog_csv:
            cat = SR.read_catalog(catalog_csv)
            cmp = SA.compare_catalog(res, cat['events'])
            cmp['catalog'] = {k: cat[k] for k in ('path', 'export_mtime_utc', 'n_events')}
        out = {'protocol': 'workspace.association/1', 'site': site_id, 'generated_utc': utc_now_iso(), 'stations': per_station,
               'period': {'start': start, 'end': end}, 'association': res, 'catalogue': cmp, 'label': None}
        d = self.dir('reports', 'sites', site_id)
        with open(os.path.join(d, 'association.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1)
        self.audit(actor, 'site.associate', {'id': site_id, 'stations': len(stations), 'picks': res['n_picks'], 'events': res['n_events'],
                                              'vp_km_s': vp_km_s, 'depth_km': depth_km, 'catalog': catalog_csv or None})
        return out

    def association(self, site_id: str) -> Optional[dict]:
        p = os.path.join(self.path, 'reports', 'sites', site_id, 'association.json')
        if not os.path.isfile(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    # -- QuakeML: the site's events in the catalogue format ----------------------------------------------------
    def quakeml_export(self, site_id: str, agency: str = '', out: Optional[str] = None, actor: str = 'system') -> dict:
        """The site's association, written as a QuakeML 1.2 catalogue under reports/sites/<id>/quakeml/ unless
        told otherwise, with the shape checked the way a reader checks it. Needs an association first."""
        from . import quakeml as Q
        from . import __version__
        st = self.site(site_id)
        doc = self.association(site_id)
        if doc is None:
            raise WorkspaceError(f'site {site_id} has no association yet: run --action associate first')
        stations = {}
        for sid in st['seismic']:
            if sid not in self.manifest.get('seismic', []):
                continue
            rec = dict(self.seismic_station(sid))
            per = (doc.get('stations') or {}).get(sid) or {}
            if per.get('stream'):
                rec['stream'] = per['stream']
            stations[sid] = rec
        b = Q.build(doc, st, stations, agency=agency, version=__version__)
        path = out or os.path.join(self.dir('reports', 'sites', site_id, 'quakeml'), f'{site_id}_events.xml')
        Q.write(b['tree'], path)
        v = Q.validate(path)
        summary = {'protocol': 'workspace.quakeml_export/1', 'site': site_id, 'path': path, 'schema': Q.SCHEMA,
                   'status': 'WRITTEN' if v['status'] == 'VALID_SHAPE' else 'WRITTEN_WITH_PROBLEMS',
                   'n_events': b['n_events'], 'n_picks': b['n_picks'], 'n_arrivals': b['n_arrivals'], 'gaps': b['gaps'],
                   'refused': b['refused'], 'agency': b['agency'], 'generated_utc': b['generated_utc'], 'validate': v,
                   'association_generated_utc': doc.get('generated_utc')}
        with open(os.path.join(os.path.dirname(path), 'export_summary.json'), 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=1)
        self.audit(actor, 'site.quakeml_export', {'id': site_id, 'status': summary['status'], 'events': b['n_events'], 'picks': b['n_picks'],
                                                   'gaps': len(b['gaps']), 'out': path})
        return summary

    # -- the OSDU-shaped export ----------------------------------------------------------------------------------
    def osdu_export(self, site_id: str, partition: str = '', acl_owners: Optional[List[str]] = None, acl_viewers: Optional[List[str]] = None,
                    legal_tags: Optional[List[str]] = None, countries: Optional[List[str]] = None, operator_org_id: str = '',
                    out_dir: Optional[str] = None, actor: str = 'system') -> dict:
        """The site as an OSDU Manifest with its files beside it, under reports/sites/<id>/osdu/ unless told
        otherwise. The partition, the ACL groups and the legal tag are the operator's declarations; without them
        the manifest is written and marked NOT LOADABLE, never filled in."""
        from . import osdu as O
        st = self.site(site_id)
        ctx = O.Context(partition=partition or '', acl_owners=list(acl_owners or []), acl_viewers=list(acl_viewers or []),
                        legal_tags=list(legal_tags or []), countries=list(countries or ['US']), operator_org_id=operator_org_id or '')
        wells = [self.well(w) for w in st['wells']]
        streams, files = {}, {}
        for w in wells:
            if w['kind'] == 'file' and w.get('files'):
                files[w['id']] = os.path.join(self.path, 'wells', w['id'], 'source', w['files'][0])
            elif w['kind'] == 'live':
                p = self.latest_live_stream_csv(w['id'])
                if p:
                    files[w['id']] = p
            if w['id'] in files:
                try:
                    streams[w['id']] = self.well_stream(w['id'])
                except Exception:
                    pass
        stations = [self.seismic_station(x) for x in st['seismic'] if x in self.manifest.get('seismic', [])]
        sfiles = {x['id']: [os.path.join(self.path, 'seismic', x['id'], 'source', n) for n in x['files']] for x in stations}
        out = out_dir or os.path.join(self.dir('reports', 'sites', site_id), 'osdu')
        summary = O.build_manifest(st, wells, streams, files, stations, sfiles, ctx, out)
        summary['validate'] = O.validate(json.load(open(summary['manifest'], encoding='utf-8')))
        self.audit(actor, 'site.osdu_export', {'id': site_id, 'status': summary['status'], 'records': summary['validate']['records'],
                                                'gaps': len(summary['gaps']), 'partition': partition or None, 'out': out})
        return summary

    def write_site_report(self, site_id: str, actor: str = 'system') -> dict:
        from . import __version__
        from .client_reports import site_report, write as _write
        summ = self.site_summary(site_id)
        out = self.dir('reports', 'sites', site_id)
        paths = _write(site_report(summ, program_version=__version__), out, basename='site_report')
        with open(os.path.join(out, 'summary.json'), 'w', encoding='utf-8') as fh:
            json.dump(summ, fh, indent=1, default=str)
        self.audit(actor, 'site.report', {'id': site_id, **summ['counts']})
        return {'report': paths['html'], 'paths': paths, 'summary': summ}

    # -- what we hold, and what it says over a period ----------------------------------------
    def seismic_inventory(self) -> dict:
        """What seismic this workspace holds. Not what it concluded - what it HAS: every station, every
        record, the span and the rate and the gaps, the checksum of each file, the datum the positions are
        on, which steps have been run and whether their output is older than the record it came from.

        Nothing in the leg answered this. A client asks it first and an auditor asks it last."""
        import os as _os
        rows, warn = [], []
        total_s = 0.0
        for st in self.seismic_stations():
            sid = st['id']
            src = _os.path.join(self.path, 'seismic', sid, 'source')
            out = _os.path.join(self.path, 'reports', 'seismic', sid)
            recs = []
            for name in st.get('files', []):
                path = _os.path.join(src, name)
                info = None
                try:
                    from . import seismic as S
                    trs = S.read_any(path)
                    if trs:
                        info = trs[0].info()
                except (ValueError, OSError) as e:
                    warn.append(f'{sid}/{name}: {e}')
                size = _os.path.getsize(path) if _os.path.isfile(path) else None
                mtime = _os.path.getmtime(path) if _os.path.isfile(path) else None
                recs.append({'file': name, 'bytes': size, 'sha256': (st.get('sha256') or {}).get(name),
                             'id': (info or {}).get('id'), 'start_utc': (info or {}).get('start'), 'end_utc': (info or {}).get('end'),
                             'sample_rate_hz': (info or {}).get('sample_rate_hz'), 'hours': (round((info or {}).get('duration_s', 0.0) / 3600.0, 3) if info else None),
                             'gaps': (info or {}).get('gaps'), 'encoding': (info or {}).get('encoding'), 'unit': (info or {}).get('unit'),
                             'source_mtime': mtime, 'unreadable': info is None})
                total_s += ((info or {}).get('duration_s') or 0.0)
            ran = {}
            newest_src = max([r['source_mtime'] for r in recs if r['source_mtime']] or [0.0])
            for name in ('summary', 'spectrum', 'lines', 'detect', 'beam', 'array_detect', 'signatures', 'multi_beam', 'harmonics', 'datum', 'array_qc', 'unlisted'):
                f = _os.path.join(out, name + '.json')
                if _os.path.isfile(f):
                    ran[name] = 'stale' if _os.path.getmtime(f) < newest_src else 'current'
            summ = {}
            fsum = _os.path.join(out, 'summary.json')
            if _os.path.isfile(fsum):
                with open(fsum, encoding='utf-8') as fh:
                    summ = json.load(fh)
            rows.append({'id': sid, 'display': st.get('display'), 'kind': st.get('kind'), 'lat': st.get('lat'), 'lon': st.get('lon'),
                         'datum': st.get('datum') or 'UNKNOWN', 'band_hz': st.get('band_hz'), 'added_utc': st.get('added_utc'), 'added_by': st.get('added_by'),
                         'stationxml': st.get('stationxml'), 'sensors': st.get('sensors'), 'sources': st.get('sources'), 'permits': st.get('permits'),
                         'n_records': len(recs), 'records': recs, 'ran': ran, 'refreshed_utc': summ.get('generated_utc'),
                         'unit': summ.get('unit'), 'response_removed': summ.get('response_removed'),
                         'n_sources': summ.get('n_sources'), 'detect_status': summ.get('detect_status'),
                         'qc_status': summ.get('qc_status'), 'datum_status': summ.get('datum_status')})
            if not ran:
                warn.append(f'{sid}: never refreshed, so nothing is known about this record beyond what it is')
            elif any(v == 'stale' for v in ran.values()):
                warn.append(f'{sid}: the source changed after the last run; what is reported for it is older than the record')
        tracks = []
        for tk in self.tracks():
            tracks.append({'id': tk.get('id'), 'display': tk.get('display'), 'stations': tk.get('stations'),
                           'band_hz': tk.get('band_hz'), 'added_utc': tk.get('added_utc'), 'truth': bool(tk.get('truth'))})
        return {'protocol': 'workspace.seismic_inventory/1', 'workspace': self.path, 'generated_utc': utc_now_iso(),
                'n_stations': len(rows), 'n_records': sum(r['n_records'] for r in rows), 'total_hours': round(total_s / 3600.0, 2),
                'stations': rows, 'tracks': tracks, 'warnings': warn,
                'basis': 'the workspace manifest and the files it holds, read now; every record opened and its span, rate and gaps taken from it',
                'not_a_measurement': ['the quality of a record: this says what is held, not whether it is any good - the array quality section of each '
                                      'station report says that',
                                      'coverage: a record exists for the hours it covers and for no others, and the gaps are counted above']}

    def seismic_field(self, since: Optional[str] = None, until: Optional[str] = None) -> dict:
        """Every station, every rig and every track over a period: who was heard, who was pointed at, who was
        tracked, what the array would not separate, and what was found that nobody listed. The wells have a
        monthly report; this is the seismic one."""
        inv = self.seismic_inventory()
        srcs: Dict[str, dict] = {}
        rows = []
        unlisted_all = []
        for st in inv['stations']:
            res = self.seismic_results(st['id'])
            det = res.get('detect') or {}
            qc = (res.get('array_qc') or {}).get('qc') or {}
            ul = res.get('unlisted') or {}
            har = res.get('harmonics') or {}
            sf = (har.get('signature_families') or {})
            rates = {r['source_id']: r.get('line_rate_per_min') for r in (sf.get('sources') or []) if r.get('line_rate_per_min')}
            acts = {a['source_id']: a for a in (har.get('activity') or [])}
            for r in (det.get('sources') or []):
                g = srcs.setdefault(r['source_id'], {'source_id': r['source_id'], 'heard_at': [], 'not_heard_at': [], 'distance_km': {},
                                                     'rate_per_min': None, 'activity': None})
                (g['heard_at'] if r.get('verdict') == 'DETECTED' else g['not_heard_at']).append(st['id'])
                g['distance_km'][st['id']] = r.get('distance_km')
                if r['source_id'] in rates and not g['rate_per_min']:
                    g['rate_per_min'] = rates[r['source_id']]
                a = acts.get(r['source_id'])
                if a and a.get('verdict') in ('AGREES', 'DIFFERS') and not g['activity']:
                    g['activity'] = {'verdict': a['verdict'], 'spells': a.get('n_spells'), 'station': st['id'],
                                     'agreement': a.get('agreement')}
            for u in (ul.get('sources') or []):
                if u.get('verdict') == 'POINTED':
                    unlisted_all.append({'station': st['id'], 'source_id': u['source_id'], 'back_azimuth_deg': u.get('back_azimuth_deg'),
                                         'tolerance_deg': u.get('tolerance_deg'), 'line_rate_per_min': u.get('line_rate_per_min'),
                                         'fundamental_hz': u.get('fundamental_hz')})
            rows.append({'id': st['id'], 'display': st['display'], 'kind': st['kind'], 'refreshed_utc': st['refreshed_utc'],
                         'detect_status': st['detect_status'], 'qc_status': qc.get('status') or st['qc_status'],
                         'qc_usable': qc.get('n_usable'), 'qc_sensors': qc.get('n_sensors'),
                         'radius': det.get('radius'), 'datum_status': st['datum_status'],
                         'unlisted': ul.get('n_found'), 'hours': sum(r['hours'] or 0.0 for r in st['records'])})
        tracks = []
        for tk in inv['tracks']:
            tres = self.track_results(tk['id']) if tk.get('id') else {}
            mt = (tres.get('multi_track') or {})
            per = {sid: (v.get('verdict') or {}).get('verdict') or v.get('status') for sid, v in (mt.get('tracks') or {}).items()}
            tracks.append({'id': tk['id'], 'display': tk['display'], 'stations': tk['stations'], 'n_tracked': mt.get('n_tracked'),
                           'per_source': per})
        return {'protocol': 'workspace.seismic_field/1', 'workspace': self.path, 'generated_utc': utc_now_iso(),
                'period': {'since': since, 'until': until}, 'stations': rows, 'sources': list(srcs.values()), 'tracks': tracks,
                'unlisted': unlisted_all, 'inventory': {k: inv[k] for k in ('n_stations', 'n_records', 'total_hours', 'warnings')},
                'basis': 'every station\'s own last run, collected; nothing is recomputed here',
                'not_a_measurement': ['a source not heard as a source not working: it may be outside the radius of every station here',
                                      'a period: this reports what each station\'s last run holds, whatever hours that run covered',
                                      'an unlisted candidate as a machine of any particular kind']}

    def write_seismic_dataset_report(self, actor: str = 'system') -> dict:
        """The Seismic Dataset Report: what this workspace holds."""
        from . import __version__
        from .client_reports import seismic_dataset_report, write as _write
        inv = self.seismic_inventory()
        out = self.dir('reports', 'seismic')
        paths = _write(seismic_dataset_report(inv, program_version=__version__), out, basename='seismic_dataset_report')
        with open(os.path.join(out, 'inventory.json'), 'w', encoding='utf-8') as fh:
            json.dump(inv, fh, indent=1, default=str)
        self.audit(actor, 'seismic.dataset-report', {'stations': inv['n_stations'], 'records': inv['n_records'], 'hours': inv['total_hours']})
        return {'report': paths['html'], 'paths': paths, 'inventory': inv}

    def write_seismic_field_report(self, actor: str = 'system', since: Optional[str] = None, until: Optional[str] = None) -> dict:
        """The Seismic Field Report: the whole site in one document."""
        from . import __version__
        from .client_reports import seismic_field_report, write as _write
        fld = self.seismic_field(since, until)
        out = self.dir('reports', 'seismic')
        paths = _write(seismic_field_report(fld, program_version=__version__), out, basename='seismic_field_report')
        with open(os.path.join(out, 'field.json'), 'w', encoding='utf-8') as fh:
            json.dump(fld, fh, indent=1, default=str)
        self.audit(actor, 'seismic.field-report', {'stations': len(fld['stations']), 'sources': len(fld['sources']),
                                                   'unlisted': len(fld['unlisted'])})
        return {'report': paths['html'], 'paths': paths, 'field': fld}

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
                'n_wells': len(wells), 'n_seismic': len(self.manifest.get('seismic', [])), 'n_tracks': len(self.manifest.get('tracks', [])),
                'n_sites': len(self.manifest.get('sites', [])), 'sites': [{'id': x['id'], 'display': x['display'], 'client': x.get('client', '')} for x in self.sites()],
                'audit_entries': len(self.audit_log()),
                'dashboard': os.path.join(self.reports_dir, 'index.html') if os.path.isfile(os.path.join(self.reports_dir, 'index.html')) else None}
