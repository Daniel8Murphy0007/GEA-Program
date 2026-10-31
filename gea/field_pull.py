# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""field_pull - the black box's files into the workspace.

GEA Recorder (the field recorder on the pad, `recorder/` in this repository) writes hourly miniSEED files, a SHA-256
beside each, a manifest listing every closed file, a sensors CSV with each sensor's position, and the rig feed as a
CSV on the same clock. This module fetches them - from the box's status page on the pad network
(http://<the box>:8780) or from the USB mirror carried to the office (a folder) - verifies every file against its
SHA-256, keeps them under field/<box>/ in the workspace exactly as the box wrote them, and makes the Seismic tab's
stations from them: one array station of the ground-motion sensors (geophones, accelerometers, seismometers), one
of the electrode channels when there are any, each sensor's hours joined in order into one record per sensor,
with the sensors CSV. A later pull fetches only the hours the workspace lacks and remakes the stations.

Nothing is re-sampled, filtered or edited: a record in the workspace is byte for byte a record the box wrote, and
the manifest's hashes say so."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

GROUND = ('geophone', 'accelerometer', 'seismometer')


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Box:
    """Where the files are: a URL (the box's page) or a folder (the mirror)."""

    def __init__(self, source: str, timeout: float = 30.0):
        self.source = source.strip()
        self.is_url = self.source.lower().startswith(('http://', 'https://'))
        self.timeout = timeout
        if not self.is_url and not os.path.isdir(self.source):
            raise ValueError(f'{self.source} is neither the box\'s address (http://...) nor a folder')

    def read(self, rel: str) -> bytes:
        if self.is_url:
            url = self.source.rstrip('/') + '/' + (rel if rel in ('manifest.json', 'sensors.csv', 'rig/depth.csv') else 'files/' + urllib.parse.quote(rel))
            req = urllib.request.Request(url, headers={'User-Agent': 'GEA-Program field pull'})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        p = os.path.join(self.source, rel)
        with open(p, 'rb') as f:
            return f.read()

    def has(self, rel: str) -> bool:
        if self.is_url:
            return True
        return os.path.isfile(os.path.join(self.source, rel))


def manifest_of(box: Box) -> dict:
    m = json.loads(box.read('manifest.json').decode('utf-8'))
    if m.get('program') != 'GEA Recorder' or 'files' not in m or 'sensors' not in m:
        raise ValueError('this is not a GEA Recorder manifest')
    return m


def pull(ws, source: str, actor: str = 'system', display: Optional[str] = None, timeout: float = 30.0) -> dict:
    """Fetch what the workspace lacks, verify it, and make or remake the stations. Returns what was done."""
    box = Box(source, timeout)
    m = manifest_of(box)
    sta = str(m.get('station') or 'box')
    root = os.path.join(ws.path, 'field', sta)
    os.makedirs(root, exist_ok=True)
    fetched, kept, bad = [], [], []
    for e in m['files']:
        rel = e['file']
        dst = os.path.join(root, rel.replace('/', os.sep))
        if os.path.isfile(dst) and _sha256(dst) == e['sha256']:
            kept.append(rel); continue
        data = box.read(rel)
        if hashlib.sha256(data).hexdigest() != e['sha256']:
            bad.append(rel); continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        fetched.append(rel)
    for rel in ('sensors.csv', 'rig/depth.csv'):
        try:
            if box.has(rel):
                data = box.read(rel)
                p = os.path.join(root, rel.replace('/', os.sep)); os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'wb') as f:
                    f.write(data)
        except Exception:
            pass
    with open(os.path.join(root, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(m, f, indent=1)
    stations = make_stations(ws, m, root, actor, display)
    rec = {'source': source, 'box': sta, 'site': m.get('site'), 'fetched': len(fetched), 'kept': len(kept), 'bad': bad, 'n_files': len(m['files']),
           'stations': stations, 'pulled_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'manifest_written_utc': m.get('written_utc'),
           'sensors': len(m['sensors']), 'rig_rows': _count_rows(os.path.join(root, 'rig', 'depth.csv'))}
    with open(os.path.join(root, 'last_pull.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    ws.audit(actor, 'field.pull', {k: v for k, v in rec.items() if k != 'stations'} | {'stations': [s['id'] for s in stations]})
    return rec


def _count_rows(p: str) -> int:
    try:
        with open(p, encoding='utf-8') as f:
            return max(sum(1 for _ in f) - 1, 0)
    except OSError:
        return 0


def joined_records(root: str, entries: List[dict]) -> bytes:
    """A sensor's hours, in order, as one miniSEED: the records appended, nothing changed."""
    out = bytearray()
    for e in sorted(entries, key=lambda x: x['start_utc']):
        with open(os.path.join(root, e['file'].replace('/', os.sep)), 'rb') as f:
            out += f.read()
    return bytes(out)


def make_stations(ws, m: dict, root: str, actor: str, display: Optional[str]) -> List[dict]:
    """The ground-motion array and, when there are electrodes, the electrode station, from the files on hand."""
    from .workspace import WorkspaceError, slug
    sensors = m['sensors']
    by_sensor: Dict[str, List[dict]] = {}
    for e in m['files']:
        if os.path.isfile(os.path.join(root, e['file'].replace('/', os.sep))):      # a file that failed its hash is not on hand
            by_sensor.setdefault(e['sensor_id'], []).append(e)
    site = str(m.get('site') or m.get('station') or 'field')
    base = display or f'{site} field'
    groups = [('array', [s for s in sensors if s['kind'] in GROUND and s['sensor_id'] in by_sensor], f'{base} array'),
              ('electrodes', [s for s in sensors if s['kind'] == 'electrode' and s['sensor_id'] in by_sensor], f'{base} electrodes')]
    made = []
    work = os.path.join(root, '_stations')
    os.makedirs(work, exist_ok=True)
    for key, sens, name in groups:
        if not sens:
            continue
        files, names = [], []
        for s in sens:
            data = joined_records(root, by_sensor[s['sensor_id']])
            fn = f"{m.get('network', 'GE')}.{s['sensor_id']}.{s.get('code', '')}.mseed"
            p = os.path.join(work, fn)
            with open(p, 'wb') as f:
                f.write(data)
            files.append(p); names.append(fn)
        sens_csv = os.path.join(work, f'sensors_{key}.csv')
        with open(sens_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['sensor_id', 'lat', 'lon', 'x_ft', 'y_ft', 'kind', 'code'])
            for s in sens:
                w.writerow([s['sensor_id'], s.get('lat', ''), s.get('lon', ''), s.get('x_ft', ''), s.get('y_ft', ''), s.get('kind', ''), s.get('code', '')])
        sid = slug(name)
        wh = m.get('wellhead') or {}
        lat = wh.get('lat'); lon = wh.get('lon')
        hours = sorted({'.'.join(e['file'].split('.')[-4:-1]) for s in sens for e in by_sensor[s['sensor_id']]})
        note = (f"from GEA Recorder {m.get('version', '')}, box {m.get('station')}: {len(sens)} sensors, {len(hours)} hour(s) ({hours[0]} to {hours[-1]}); "
                f"every record byte for byte as the box wrote it (field/{m.get('station')}/manifest.json)")
        if sid in ws.manifest.get('seismic', []):
            st = ws.update_seismic_station(sid, files, filenames=names, sensors_csv=sens_csv if lat is not None else None, note=note, actor=actor)
        else:
            st = ws.add_seismic_station(name, files, lat=lat, lon=lon, actor=actor, sensors_csv=sens_csv if lat is not None else None, filenames=names,
                                        datum='WGS84', band=[8.0, 80.0] if key == 'array' else [0.01, 5.0], note=note)
        made.append({'id': st['id'], 'display': name, 'kind': key, 'sensors': [s['sensor_id'] for s in sens], 'hours': len(hours)})
    shutil.rmtree(work, ignore_errors=True)
    return made


def last_pulls(ws) -> List[dict]:
    out = []
    d = os.path.join(ws.path, 'field')
    if not os.path.isdir(d):
        return out
    for box in sorted(os.listdir(d)):
        p = os.path.join(d, box, 'last_pull.json')
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
    return out
