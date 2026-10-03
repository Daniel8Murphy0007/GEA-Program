# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""files - the site's files, seen from the dashboard: import roots, a browser
with type detection and preview, import into a well, export of any report or
an evidence pack to a chosen folder, watch folders, and the file-kind
detector every reader shares.

A browser cannot see the server's disks, so the administrator declares
*roots*: the folders the program may look in and write to (the historian
export share, the mud logger's output, a USB drop folder, the evidence
share). Nothing outside a root is ever listed, read or written through the
service; a path that tries to leave its root is declined.

    workspace.json:  "import_roots": [{"name": "historian", "path": "\\\\\\\\nas\\\\exports"}, ...]
                     "export_roots": [{"name": "evidence", "path": "D:\\\\GEA-evidence"}, ...]

Detection is by content, never by extension alone: LAS 2.0 (`~V`), a
PANGAEA export (`/* DATA DESCRIPTION`), an operator table transcription
(`/* OPERATOR TABLE TRANSCRIPTION`), SEG-Y (a 3200-byte textual header that
decodes as EBCDIC or ASCII and a plausible binary header), a vendor .xls
(OLE magic), a JSON port map, a historian CSV (first column a timestamp or
elapsed seconds), another CSV, or unknown. `read_any` dispatches to the
right reader and refuses with the detected kind when no reader fits (a
SEG-Y volume is a survey input, not a gauge stream).

Watch folders: `scan_root` imports every file under a root that the
workspace has not imported before (by SHA-256, recorded in
records/imported.jsonl), so a historian that append-exports or a logging
unit that drops a file an hour is picked up by a scheduled `gea files
--action watch`. The evidence pack is one zip: every report, the audit log,
the configuration history, patches, the workspace manifest, the SBOM, the
FAT/SAT protocols, with MANIFEST.json and SHA256SUMS.txt inside.

Stdlib only; readers come from the port modules.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .workspace import Workspace, WorkspaceError, sha256_file, utc_now_iso, slug

KINDS = ('las', 'historian_csv', 'csv', 'pangaea', 'operator_table', 'segy', 'mseed', 'sac', 'xls', 'json', 'unknown')
TEXT_SNIFF = 64 * 1024


def _is_timestamp(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        pass
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', s))


def detect(path: str) -> dict:
    """{'kind', 'detail', 'bytes', 'text'} for one file, from its first bytes."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(max(TEXT_SNIFF, 3600))
    if head[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return {'kind': 'xls', 'detail': 'OLE2 workbook (vendor survey export)', 'bytes': size, 'text': False}
    if head[:4] == b'PK\x03\x04':
        return {'kind': 'unknown', 'detail': 'zip container (xlsx or archive) - not an ingest format', 'bytes': size, 'text': False}
    # SEG-Y: 3200-byte textual header, EBCDIC or ASCII, then a 400-byte binary header
    if size > 3600:
        th = head[:3200]
        try:
            ebcdic = th.decode('cp500')
            asc = th.decode('ascii', 'replace')
        except Exception:
            ebcdic, asc = '', ''
        if ebcdic.startswith('C 1') or ebcdic.startswith('C1 ') or asc.startswith('C 1') or asc.startswith('C1 '):
            return {'kind': 'segy', 'detail': 'SEG-Y seismic volume (survey input; not a gauge stream)', 'bytes': size, 'text': False}
    # miniSEED: a six-digit sequence number and a data-quality byte; SAC: header version 6 or 7 at byte 304
    if size >= 256 and head[6:7] in (b'D', b'R', b'Q', b'M') and head[:6].strip(b' ').isdigit():
        return {'kind': 'mseed', 'detail': 'miniSEED seismic record (the second leg: gea seismic; not a gauge stream)', 'bytes': size, 'text': False}
    if size >= 632 and (head[304:308] in (b'\x06\x00\x00\x00', b'\x07\x00\x00\x00', b'\x00\x00\x00\x06', b'\x00\x00\x00\x07')):
        return {'kind': 'sac', 'detail': 'SAC seismic trace (the second leg: gea seismic; not a gauge stream)', 'bytes': size, 'text': False}
    try:
        text = head.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = head.decode('latin-1')
        except UnicodeDecodeError:
            return {'kind': 'unknown', 'detail': 'binary', 'bytes': size, 'text': False}
    if '\x00' in text[:4096]:
        return {'kind': 'unknown', 'detail': 'binary', 'bytes': size, 'text': False}
    lines = [l for l in text.splitlines() if l.strip()]
    body = [l for l in lines if not l.lstrip().startswith('#')]        # LAS exports often open with '#' comment lines
    first = body[0].strip() if body else (lines[0].strip() if lines else '')
    if first.startswith('~V') or first.upper().startswith('~VERSION'):
        return {'kind': 'las', 'detail': 'LAS 2.0 well log', 'bytes': size, 'text': True}
    if first.startswith('/* OPERATOR TABLE TRANSCRIPTION'):
        return {'kind': 'operator_table', 'detail': 'operator table transcription', 'bytes': size, 'text': True}
    if first.startswith('/* DATA DESCRIPTION'):
        return {'kind': 'pangaea', 'detail': 'PANGAEA text export', 'bytes': size, 'text': True}
    if first.startswith('{') or first.startswith('['):
        try:
            json.loads(text if size <= TEXT_SNIFF else open(path, encoding='utf-8').read())
            return {'kind': 'json', 'detail': 'JSON (a port map or configuration)', 'bytes': size, 'text': True}
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    if ',' in first or ';' in first or '\t' in first:
        delim = ',' if ',' in first else (';' if ';' in first else '\t')
        rows = list(csv.reader(io.StringIO('\n'.join(lines[:3])), delimiter=delim))
        if len(rows) >= 2 and len(rows[0]) >= 2 and _is_timestamp(rows[1][0]):
            return {'kind': 'historian_csv', 'detail': f'historian export: {len(rows[0])} columns, first column a timestamp', 'bytes': size, 'text': True}
        return {'kind': 'csv', 'detail': f'delimited text ({len(rows[0]) if rows else 0} columns); first column is not a timestamp', 'bytes': size, 'text': True}
    return {'kind': 'unknown', 'detail': 'text, no known header', 'bytes': size, 'text': True}


def preview(path: str, lines: int = 12) -> dict:
    d = detect(path)
    out = {'kind': d['kind'], 'detail': d['detail'], 'bytes': d['bytes'], 'lines': []}
    if d['text']:
        with open(path, encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if i >= lines:
                    break
                out['lines'].append(line.rstrip('\n')[:240])
    elif d['kind'] in ('mseed', 'sac'):
        try:
            from .seismic import read_any as _read_seis
            out['lines'] = [f"{t.id}  {t.info()['start']} to {t.info()['end']}  {t.sample_rate:g} Hz  {t.npts} samples  {t.encoding}  {len(t.gaps)} gap(s)" for t in _read_seis(path)[:12]]
        except Exception as e:
            out['lines'] = [f'seismic record could not be read: {e}']
    elif d['kind'] == 'segy':
        try:
            from .segy import read_segy
            v = read_segy(path)
            out['lines'] = [f'{v.encoding} textual header; {len(v.traces)} traces; {v.n_samples} samples at {v.sample_interval_us} us; {v.format_name}']
        except Exception as e:
            out['lines'] = [f'SEG-Y header could not be read: {e}']
    return out


def read_any(path: str, kind: Optional[str] = None):
    """A LiveStream from any supported stream file; raises ValueError with the kind otherwise."""
    kind = kind or detect(path)['kind']
    if kind == 'historian_csv':
        from .ports import read_historian_csv
        return read_historian_csv(path)
    if kind == 'las':
        from .ports import read_las
        return read_las(path)
    if kind == 'pangaea':
        from .profile_catalog import read_pangaea_txt
        return read_pangaea_txt(path)
    if kind == 'operator_table':
        from .profile_catalog import read_operator_table
        return read_operator_table(path)
    if kind == 'xls':
        from .profile_catalog import read_drift_xls
        return read_drift_xls(path)
    if kind == 'segy':
        raise ValueError('a SEG-Y volume is a survey input (gea survey), not a gauge stream')
    if kind in ('mseed', 'sac'):
        raise ValueError('a seismic record is the second leg\'s input (gea seismic), not a gauge stream')
    if kind == 'csv':
        raise ValueError('delimited text whose first column is not a timestamp - a historian export needs a timestamp (or elapsed seconds) first')
    raise ValueError(f'no reader for a file of kind {kind!r}')


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------
class Roots:
    def __init__(self, ws: Workspace):
        self.ws = ws

    def list(self, which: str) -> List[dict]:
        return list(self.ws.manifest.get(f'{which}_roots', []))

    def add(self, which: str, name: str, path: str, actor: str) -> dict:
        if which not in ('import', 'export'):
            raise WorkspaceError("which must be 'import' or 'export'")
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise WorkspaceError(f'not a folder: {path}')
        name = slug(name)
        roots = self.list(which)
        if any(r['name'] == name for r in roots):
            raise WorkspaceError(f'{which} root exists: {name}')
        r = {'name': name, 'path': path, 'added_by': actor, 'added_utc': utc_now_iso()}
        roots.append(r)
        self.ws.manifest[f'{which}_roots'] = roots
        self.ws._save()
        self.ws.audit(actor, f'root.add', {'which': which, 'name': name, 'path': path})
        return r

    def remove(self, which: str, name: str, actor: str) -> None:
        roots = [r for r in self.list(which) if r['name'] != name]
        self.ws.manifest[f'{which}_roots'] = roots
        self.ws._save()
        self.ws.audit(actor, 'root.remove', {'which': which, 'name': name})

    def resolve(self, which: str, name: str, rel: str = '') -> str:
        """An absolute path inside the named root, or WorkspaceError when it would leave it."""
        root = next((r for r in self.list(which) if r['name'] == name), None)
        if root is None:
            raise WorkspaceError(f'no {which} root named {name}')
        base = os.path.realpath(root['path'])
        full = os.path.realpath(os.path.join(base, rel or ''))
        if full != base and not full.startswith(base + os.sep):
            raise WorkspaceError('path leaves its root')
        return full


def browse(ws: Workspace, root_name: str, rel: str = '', detect_kinds: bool = True, limit: int = 500) -> dict:
    full = Roots(ws).resolve('import', root_name, rel)
    if not os.path.isdir(full):
        raise WorkspaceError(f'not a folder: {rel or "/"}')
    entries = []
    names = sorted(os.listdir(full), key=lambda n: (not os.path.isdir(os.path.join(full, n)), n.lower()))
    for n in names[:limit]:
        p = os.path.join(full, n)
        if os.path.isdir(p):
            entries.append({'name': n, 'type': 'dir'})
        else:
            e = {'name': n, 'type': 'file', 'bytes': os.path.getsize(p), 'modified_utc': datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            if detect_kinds:
                try:
                    d = detect(p)
                    e['kind'], e['detail'] = d['kind'], d['detail']
                except OSError as ex:
                    e['kind'], e['detail'] = 'unknown', str(ex)
            entries.append(e)
    return {'root': root_name, 'path': rel, 'entries': entries, 'truncated': len(names) > limit}


# ---------------------------------------------------------------------------
# Import, watch
# ---------------------------------------------------------------------------
def _imported_path(ws: Workspace) -> str:
    return os.path.join(ws.dir('records'), 'imported.jsonl')


def imported(ws: Workspace) -> Dict[str, dict]:
    p = _imported_path(ws)
    out = {}
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            for l in f:
                if l.strip():
                    e = json.loads(l)
                    out[e['sha256']] = e
    return out


def import_file(ws: Workspace, root_name: str, rel: str, actor: str, display: Optional[str] = None, station_md_ft: Optional[float] = None) -> dict:
    full = Roots(ws).resolve('import', root_name, rel)
    if not os.path.isfile(full):
        raise WorkspaceError(f'not a file: {rel}')
    d = detect(full)
    if d['kind'] in ('segy', 'mseed', 'sac', 'csv', 'unknown', 'json'):
        raise WorkspaceError(f"{os.path.basename(full)} is {d['detail']} - not a gauge stream the program ingests as a well")
    if d['kind'] == 'xls':
        import importlib.util
        if importlib.util.find_spec('xlrd') is None:
            raise WorkspaceError(f"{os.path.basename(full)} is a vendor .xls; reading it needs the optional xlrd (pip install \"gea-program[xls]\")")
    h = sha256_file(full)
    seen = imported(ws).get(h)
    if seen:
        raise WorkspaceError(f"already imported as well {seen['well_id']} on {seen['utc']} (same content)")
    w = ws.add_well_file(full, display=display or os.path.splitext(os.path.basename(full))[0], actor=actor, station_md_ft=station_md_ft)
    with open(_imported_path(ws), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'sha256': h, 'root': root_name, 'rel': rel, 'kind': d['kind'], 'well_id': w['id'], 'utc': utc_now_iso(), 'actor': actor}, sort_keys=True) + '\n')
    return w


def scan_root(ws: Workspace, root_name: str, actor: str = 'watch', kinds=('historian_csv', 'las', 'pangaea', 'operator_table', 'xls'), max_files: int = 50) -> dict:
    """Import every not-yet-imported stream file under a root (watch folder)."""
    base = Roots(ws).resolve('import', root_name)
    done = imported(ws)
    added, skipped = [], []
    for dp, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if not x.startswith('.')]
        for n in sorted(fn):
            p = os.path.join(dp, n)
            try:
                if os.path.getsize(p) == 0 or detect(p)['kind'] not in kinds:
                    continue
                if sha256_file(p) in done:
                    continue
                rel = os.path.relpath(p, base)
                w = import_file(ws, root_name, rel, actor, display=os.path.splitext(n)[0])
                added.append({'rel': rel, 'well_id': w['id']})
                if len(added) >= max_files:
                    break
            except (WorkspaceError, OSError) as e:
                skipped.append({'rel': os.path.relpath(p, base), 'reason': str(e)})
        if len(added) >= max_files:
            break
    ws.audit(actor, 'root.scan', {'root': root_name, 'added': len(added), 'skipped': len(skipped)})
    return {'root': root_name, 'added': added, 'skipped': skipped}


# ---------------------------------------------------------------------------
# Export, evidence pack
# ---------------------------------------------------------------------------
def export_report(ws: Workspace, report_rel: str, root_name: str, dest_rel: str, actor: str) -> dict:
    """Copy one file from reports/ (or a whole report folder) into an export root."""
    src = os.path.realpath(os.path.join(ws.reports_dir, report_rel))
    if not src.startswith(os.path.realpath(ws.reports_dir)) or not os.path.exists(src):
        raise WorkspaceError(f'no such report: {report_rel}')
    dest_dir = Roots(ws).resolve('export', root_name, dest_rel)
    os.makedirs(dest_dir, exist_ok=True)
    written = []
    if os.path.isdir(src):
        for dp, _, fn in os.walk(src):
            for n in fn:
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, src)
                q = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(q), exist_ok=True)
                shutil.copyfile(p, q)
                written.append(q)
    else:
        q = os.path.join(dest_dir, os.path.basename(src))
        shutil.copyfile(src, q)
        written.append(q)
    ws.audit(actor, 'export', {'what': report_rel, 'root': root_name, 'dest': dest_rel, 'files': len(written)}, inputs=written[:20])
    return {'files': written}


def evidence_pack(ws: Workspace, out_path: str, actor: str, include_audit: bool = True) -> dict:
    """Everything an auditor asks for, in one zip with a manifest and a hash list."""
    from . import __version__
    items = []
    def add(path: str, arc: str):
        if os.path.isfile(path):
            items.append((path, arc))
    for dp, dn, fn in os.walk(ws.reports_dir):
        dn[:] = [d for d in dn if d not in ('imported',)]
        for n in fn:
            p = os.path.join(dp, n)
            add(p, 'reports/' + os.path.relpath(p, ws.reports_dir).replace(os.sep, '/'))
    for dp, _, fn in os.walk(ws.config_dir):
        for n in fn:
            p = os.path.join(dp, n)
            add(p, 'config/' + os.path.relpath(p, ws.config_dir).replace(os.sep, '/'))
    for dp, _, fn in os.walk(os.path.join(ws.path, 'monitor')):
        for n in fn:
            p = os.path.join(dp, n)
            add(p, 'monitor/' + os.path.relpath(p, os.path.join(ws.path, 'monitor')).replace(os.sep, '/'))
    add(os.path.join(ws.path, 'workspace.json'), 'workspace.json')
    add(os.path.join(ws.path, 'patches.json'), 'patches.json')
    add(os.path.join(ws.path, 'records', 'imported.jsonl'), 'records/imported.jsonl')
    if include_audit:
        add(ws.audit_path, 'records/audit.jsonl')
    for w in ws.wells():
        add(os.path.join(ws.path, 'wells', w['id'], 'well.json'), f"wells/{w['id']}/well.json")
    sums = [(sha256_file(p), arc) for p, arc in items]
    manifest = {'program': 'gea-program', 'version': __version__, 'workspace': ws.manifest['name'], 'built_utc': utc_now_iso(), 'built_by': actor,
                'n_files': len(items), 'contents': ['reports/ (every report, the printed dashboard, model cards, SBOM, SLA, FAT/SAT)', 'config/ (the version store)',
                                                     'monitor/ (drift evaluations and change logs)', 'wells/*/well.json (sources and hashes)', 'patches.json', 'workspace.json',
                                                     'records/imported.jsonl'] + (['records/audit.jsonl'] if include_audit else [])}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p, arc in items:
            z.write(p, arc)
        z.writestr('SHA256SUMS.txt', '\n'.join(f'{h}  {a}' for h, a in sums) + '\n')
        z.writestr('MANIFEST.json', json.dumps(manifest, indent=1))
    manifest['path'] = out_path
    manifest['sha256'] = sha256_file(out_path)
    ws.audit(actor, 'evidence.pack', {'path': out_path, 'files': len(items), 'sha256': manifest['sha256']})
    return manifest
