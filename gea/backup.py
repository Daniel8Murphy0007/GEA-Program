"""backup - the site, copied off the machine, and proven to come back.

The records are the product. Nothing in housekeeping, the workspace or the service copied the site anywhere;
the doctor told a person to restore users.json "from a backup" that no command wrote. This module writes it.

`make` zips the workspace - every well's source and records, the seismic stations with their source and live
day files, the configuration store, the monitor, the reports, users.json, the audit log, the patches, the
schedule - into one dated archive in a folder that should be on another disk or a share, with a manifest
(program version, site name, every file's SHA-256 and size) and the manifest's own hash beside it. The job
folders of finished runs and the upload scratch are left out: they are reproducible, and they are what grows.
`verify` opens an archive and checks every file against its manifest. `restore` extracts one into an empty
folder, verifies, opens it as a workspace and says what came back - a backup nobody has restored is a hope.
`status` says when the last backup was taken and whether that is too long ago; the doctor prints it.

What it will not do: back up into the workspace itself (refused - a lost disk loses both); restore over a
folder that has anything in it; call an archive good whose manifest does not match its files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

STALE_AFTER_H = 26.0                      # a daily backup that is more than a day and change old
EXCLUDE_TOP = ('jobs',)                   # taken selectively: schedule.json only
EXCLUDE_NAMES = ('_uploads', '__pycache__')
LOG_NAME = 'backups.jsonl'                # records/backups.jsonl: one line per backup taken from this workspace


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.realpath(child), os.path.realpath(parent)
    return c == p or c.startswith(p + os.sep)


def site_files(workspace_path: str) -> List[str]:
    """The relative paths a backup carries, sorted: everything but the job run folders and the upload scratch."""
    out: List[str] = []
    for root, dirs, files in os.walk(workspace_path):
        rel_root = os.path.relpath(root, workspace_path)
        top = rel_root.split(os.sep)[0] if rel_root != '.' else ''
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_NAMES and not (rel_root == '.' and d in EXCLUDE_TOP))
        for name in sorted(files):
            if name.endswith(('.tmp', '.lock')) or name in EXCLUDE_NAMES:
                continue
            rel = os.path.normpath(os.path.join(rel_root, name)) if rel_root != '.' else name
            out.append(rel.replace(os.sep, '/'))
    # the schedule is the one thing under jobs/ worth carrying: it is what a person set, not what a run left
    sched = os.path.join(workspace_path, 'jobs', 'schedule.json')
    if os.path.isfile(sched):
        out.append('jobs/schedule.json')
    return sorted(set(out))


def make(workspace_path: str, out_dir: str, actor: str = 'backup', keep: Optional[int] = None, label: str = '') -> dict:
    """Write `gea-site-<slug>-<UTC>.zip` into out_dir with MANIFEST.json inside and <zip>.sha256 beside it; prune to
    the newest `keep` archives of this site when asked; log the backup in records/backups.jsonl and the audit."""
    from . import __version__
    from .workspace import Workspace
    ws = Workspace(workspace_path)
    if _inside(out_dir, ws.path):
        raise ValueError(f'the backup folder {out_dir} is inside the workspace - a lost disk would lose both; use another disk or a share')
    os.makedirs(out_dir, exist_ok=True)
    slug = ''.join(c if c.isalnum() or c in '-_' else '-' for c in ws.manifest['name']).strip('-') or 'site'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = f'gea-site-{slug}-{stamp}.zip'
    k = 2
    while os.path.exists(os.path.join(out_dir, name)):      # two in one second: the second is numbered, never overwrites
        name = f'gea-site-{slug}-{stamp}-{k}.zip'; k += 1
    path = os.path.join(out_dir, name)
    files = site_files(ws.path)
    entries: Dict[str, dict] = {}
    total = 0
    t0 = time.time()
    with zipfile.ZipFile(path + '.part', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in files:
            src = os.path.join(ws.path, *rel.split('/'))
            try:
                data = open(src, 'rb').read()
            except OSError as e:                              # a file that vanished between the walk and the read (a rotated log)
                entries[rel] = {'skipped': str(e)}
                continue
            z.writestr(zipfile.ZipInfo.from_file(src, arcname=rel), data)
            entries[rel] = {'sha256': _sha256_bytes(data), 'bytes': len(data)}
            total += len(data)
        manifest = {'protocol': 'gea.backup/1', 'program_version': __version__, 'site': ws.manifest['name'], 'workspace': ws.path,
                    'generated_utc': _utc(), 'actor': actor, 'label': label, 'files': entries,
                    'n_files': sum(1 for v in entries.values() if 'sha256' in v), 'bytes': total,
                    'excluded': ['jobs/<run folders>', 'jobs/_uploads', '*.tmp', '*.lock'],
                    'wells': list(ws.manifest.get('wells', [])), 'seismic': list(ws.manifest.get('seismic', [])),
                    'users_json': 'users.json' in entries}
        z.writestr('MANIFEST.json', json.dumps(manifest, indent=1))
    os.replace(path + '.part', path)
    digest = _sha256(path)
    with open(path + '.sha256', 'w', encoding='utf-8') as f:
        f.write(f'{digest}  {name}\n')
    pruned = []
    if keep is not None and keep > 0:
        def _order(n: str):                                  # by the stamp, then the same-second number: a lexical sort puts '-2' before '.zip'
            m = re.match(rf'gea-site-{re.escape(slug)}-(\d{{8}}T\d{{6}}Z)(?:-(\d+))?\.zip$', n)
            return (m.group(1), int(m.group(2) or 1)) if m else ('', 0)
        mine = sorted((n for n in os.listdir(out_dir) if n.startswith(f'gea-site-{slug}-') and n.endswith('.zip')), key=_order)
        for old in mine[:-keep]:
            for p in (os.path.join(out_dir, old), os.path.join(out_dir, old + '.sha256')):
                if os.path.isfile(p):
                    os.remove(p)
            pruned.append(old)
    rec = {'utc': manifest['generated_utc'], 'path': path, 'sha256': digest, 'bytes_archive': os.path.getsize(path), 'bytes_files': total,
           'n_files': manifest['n_files'], 'seconds': round(time.time() - t0, 2), 'pruned': pruned, 'actor': actor, 'program_version': __version__}
    with open(os.path.join(ws.dir('records'), LOG_NAME), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec) + '\n')
    ws.audit(actor, 'backup.make', {'path': path, 'n_files': manifest['n_files'], 'bytes': total, 'sha256': digest, 'pruned': pruned})
    return rec


def verify(zip_path: str) -> dict:
    """Every file in the archive against MANIFEST.json, the archive against its .sha256 when that is beside it."""
    out = {'path': zip_path, 'status': 'OK', 'n_files': 0, 'mismatched': [], 'missing': [], 'extra': [], 'archive_sha256': None, 'archive_sha256_matches': None,
           'manifest': None}
    if not os.path.isfile(zip_path):
        out.update({'status': 'FAIL', 'error': 'no such file'})
        return out
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad is not None:
                out.update({'status': 'FAIL', 'error': f'corrupt member {bad}'})
                return out
            if 'MANIFEST.json' not in z.namelist():
                out.update({'status': 'FAIL', 'error': 'no MANIFEST.json - not a gea backup'})
                return out
            man = json.loads(z.read('MANIFEST.json'))
            out['manifest'] = {k: man.get(k) for k in ('protocol', 'program_version', 'site', 'generated_utc', 'n_files', 'bytes', 'wells', 'seismic', 'users_json')}
            names = set(n for n in z.namelist() if n != 'MANIFEST.json' and not n.endswith('/'))
            for rel, meta in man['files'].items():
                if 'sha256' not in meta:
                    continue
                if rel not in names:
                    out['missing'].append(rel)
                    continue
                if _sha256_bytes(z.read(rel)) != meta['sha256']:
                    out['mismatched'].append(rel)
                out['n_files'] += 1
            out['extra'] = sorted(names - set(man['files']))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        out.update({'status': 'FAIL', 'error': f'unreadable archive: {e}'})
        return out
    side = zip_path + '.sha256'
    out['archive_sha256'] = _sha256(zip_path)
    if os.path.isfile(side):
        with open(side, encoding='utf-8') as f:
            out['archive_sha256_matches'] = f.read().split()[0] == out['archive_sha256']
    if out['mismatched'] or out['missing'] or out['extra'] or out['archive_sha256_matches'] is False:
        out['status'] = 'FAIL'
    return out


def restore(zip_path: str, target: str, actor: str = 'restore') -> dict:
    """Into an empty (or absent) folder only; verified first; opened as a workspace afterwards and described."""
    from .workspace import Workspace
    v = verify(zip_path)
    if v['status'] != 'OK':
        raise ValueError(f"the archive does not verify: {v.get('error') or (v['mismatched'] + v['missing'] + v['extra'])}")
    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise ValueError(f'the restore target must be an empty folder: {target}')
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.filename == 'MANIFEST.json' or info.filename.endswith('/'):
                continue
            rel = os.path.normpath(info.filename)
            if rel.startswith('..') or os.path.isabs(rel):
                raise ValueError(f'refusing a path outside the target: {info.filename}')
            dst = os.path.join(target, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(info) as src, open(dst, 'wb') as out:
                shutil.copyfileobj(src, out)
        man = json.loads(z.read('MANIFEST.json'))
    back = [rel for rel, meta in man['files'].items() if 'sha256' in meta and _sha256(os.path.join(target, *rel.split('/'))) != meta['sha256']]
    if back:
        raise ValueError(f'{len(back)} file(s) differ after extraction: {back[:5]}')
    ws = Workspace(target)
    note = {'protocol': 'gea.restore/1', 'restored_utc': _utc(), 'from': zip_path, 'archive_sha256': v['archive_sha256'], 'backup_generated_utc': man['generated_utc'],
            'backup_program_version': man['program_version'], 'actor': actor}
    with open(os.path.join(target, 'restored_from.json'), 'w', encoding='utf-8') as f:
        json.dump(note, f, indent=1)
    ws.audit(actor, 'backup.restore', {'from': zip_path, 'archive_sha256': v['archive_sha256'], 'n_files': v['n_files']})
    s = ws.summary()
    return {'target': target, 'site': ws.manifest['name'], 'n_files': v['n_files'], 'wells': list(ws.manifest.get('wells', [])), 'seismic': list(ws.manifest.get('seismic', [])),
            'users_json': os.path.isfile(os.path.join(target, 'users.json')), 'backup_generated_utc': man['generated_utc'], 'summary': s}


def status(workspace_path: str, stale_after_h: float = STALE_AFTER_H) -> dict:
    """The last backup logged from this workspace, its age, whether it is still where the log says, and the verdict."""
    p = os.path.join(workspace_path, 'records', LOG_NAME)
    out = {'last': None, 'age_h': None, 'stale': True, 'never': True, 'present': None, 'count': 0, 'stale_after_h': stale_after_h}
    if not os.path.isfile(p):
        return out
    last = None
    n = 0
    with open(p, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    last = json.loads(line); n += 1
                except ValueError:
                    continue
    out['count'] = n
    if last is None:
        return out
    t = datetime.strptime(last['utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600.0
    out.update({'last': last, 'age_h': round(age_h, 2), 'stale': age_h > stale_after_h, 'never': False, 'present': os.path.isfile(last['path'])})
    return out


def report_text(r: dict, kind: str) -> str:
    if kind == 'make':
        return (f"backup: {r['path']}\n  {r['n_files']} files, {r['bytes_files'] / 1048576:.1f} MB of site in a {r['bytes_archive'] / 1048576:.1f} MB archive, "
                f"sha256 {r['sha256'][:16]}..., {r['seconds']} s" + (f"\n  pruned: {', '.join(r['pruned'])}" if r['pruned'] else '')
                + "\n  verify it once with --verify, and restore it once onto an empty folder before trusting it")
    if kind == 'verify':
        m = r.get('manifest') or {}
        head = f"verify: {r['path']} - {r['status']}"
        if r.get('error'):
            return head + f"\n  {r['error']}"
        return (head + f"\n  site {m.get('site')!r}, taken {m.get('generated_utc')} by gea-program {m.get('program_version')}: {r['n_files']} files checked"
                + (f", archive hash {'matches' if r['archive_sha256_matches'] else 'DOES NOT MATCH'} its .sha256" if r['archive_sha256_matches'] is not None else ', no .sha256 beside it')
                + (f"\n  mismatched: {r['mismatched']}" if r['mismatched'] else '') + (f"\n  missing: {r['missing']}" if r['missing'] else '')
                + (f"\n  extra: {r['extra']}" if r['extra'] else ''))
    if kind == 'restore':
        return (f"restored: {r['target']}\n  site {r['site']!r} from the backup of {r['backup_generated_utc']}: {r['n_files']} files, "
                f"{len(r['wells'])} well(s) {r['wells']}, {len(r['seismic'])} station(s), users.json {'present' if r['users_json'] else 'ABSENT'}"
                "\n  open it: gea serve --workspace <that folder> --port 8766, and look before you trust the copy")
    if kind == 'status':
        if r['never']:
            return 'backup: NEVER - no backup has been taken from this workspace (gea backup --workspace ... --out <another disk>)'
        l = r['last']
        return (f"backup: last {l['utc']} ({r['age_h']} h ago) -> {l['path']} ({'present' if r['present'] else 'NOT FOUND where the log says'}); "
                f"{r['count']} on record; {'STALE' if r['stale'] else 'current'} (stale after {r['stale_after_h']:g} h)")
    return json.dumps(r, indent=1)
