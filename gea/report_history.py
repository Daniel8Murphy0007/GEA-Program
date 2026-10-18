"""Report history: every client report a workspace writes, kept as it was written.

A report is written to a fixed name - `reports/pad_report.html`, `reports/wells/<well>/well_report.html` - so the
latest is always where a reader expects it, and each new one replaces the last. This module keeps the one it
replaces: every time a client report is written inside a workspace's `reports` folder, a copy of every file it
wrote goes to `reports/history/<where>/<name>/<UTC time>/`, and a line in `reports/history/index.jsonl` records
the report id, the time, and each file's size and SHA-256. Nothing is pruned unless the site asks
(`gea reports-history --prune --keep N`); a report written outside a workspace (a sample, an export) keeps no history.

The history is a copy, not a second source: the report at its usual path is the one the program reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

_LOCK = threading.Lock()
SKIP_DIRS = ('history', 'dossiers', 'month_end')


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def locate(out_dir: str) -> Optional[Tuple[str, str]]:
    """(the workspace's reports folder, the report's folder relative to it) when `out_dir` is inside a workspace's
    reports folder and not inside its history, dossiers or month-end packets; else None."""
    d = os.path.abspath(out_dir)
    cur = d
    for _ in range(8):
        if os.path.basename(cur) == 'reports' and os.path.isfile(os.path.join(os.path.dirname(cur), 'workspace.json')):
            rel = os.path.relpath(d, cur).replace(os.sep, '/')
            rel = '' if rel == '.' else rel
            if rel.split('/')[0] in SKIP_DIRS:
                return None
            return cur, rel
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return None


def root(reports_dir: str) -> str:
    return os.path.join(reports_dir, 'history')


def _stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H%M%SZ')


def record(out_dir: str, basename: str, paths: dict, report_id: str = '', title: str = '') -> Optional[dict]:
    """Keep a copy of the files a report just wrote. Returns the history entry, or None outside a workspace."""
    loc = locate(out_dir)
    if loc is None:
        return None
    reports_dir, rel = loc
    files = []
    for k, v in paths.items():
        for p in (v if isinstance(v, list) else [v]):
            if p and os.path.isfile(p):
                files.append(p)
    if not files:
        return None
    family = (rel + '/' if rel else '') + basename
    with _LOCK:
        now = datetime.now(timezone.utc)
        base = os.path.join(root(reports_dir), *family.split('/'))
        stamp = _stamp(now)
        dest = os.path.join(base, stamp)
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(base, f'{stamp}-{n}')
        os.makedirs(dest)
        rows = []
        for p in sorted(set(files)):
            q = os.path.join(dest, os.path.basename(p))
            shutil.copy2(p, q)
            rows.append({'name': os.path.basename(p), 'bytes': os.path.getsize(q), 'sha256': _sha(q)})
        entry = {'utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'family': family, 'report_id': report_id or '', 'title': title or '',
                 'dir': os.path.relpath(dest, reports_dir).replace(os.sep, '/'), 'files': rows}
        with open(os.path.join(root(reports_dir), 'index.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def entries(reports_dir: str, family: Optional[str] = None) -> List[dict]:
    p = os.path.join(root(reports_dir), 'index.jsonl')
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if family is None or e.get('family') == family:
                if os.path.isdir(os.path.join(reports_dir, e['dir'])):
                    out.append(e)
    out.sort(key=lambda e: (e['utc'], e['dir']), reverse=True)
    return out


def summary(reports_dir: str) -> dict:
    """Every report family with its versions, newest first, and the history's size on disk."""
    es = entries(reports_dir)
    fams: Dict[str, List[dict]] = {}
    for e in es:
        fams.setdefault(e['family'], []).append(e)
    size = 0
    r = root(reports_dir)
    if os.path.isdir(r):
        for dp, _, fn in os.walk(r):
            size += sum(os.path.getsize(os.path.join(dp, n)) for n in fn)
    return {'families': [{'family': k, 'versions': v, 'n': len(v), 'latest': v[0]['utc']} for k, v in sorted(fams.items())],
            'n_versions': len(es), 'bytes': size}


def prune(reports_dir: str, keep: int, family: Optional[str] = None, apply: bool = False) -> dict:
    """Remove all but the newest `keep` versions of each family (or of one). Nothing is removed without `apply`."""
    if keep < 1:
        raise ValueError('keep at least one version')
    gone, freed = [], 0
    fams: Dict[str, List[dict]] = {}
    for e in entries(reports_dir, family):
        fams.setdefault(e['family'], []).append(e)
    for fam, vs in fams.items():
        for e in vs[keep:]:
            d = os.path.join(reports_dir, e['dir'])
            b = sum(os.path.getsize(os.path.join(d, n)) for n in os.listdir(d)) if os.path.isdir(d) else 0
            gone.append(e['dir']); freed += b
            if apply:
                shutil.rmtree(d, ignore_errors=True)
    return {'applied': apply, 'removed': gone, 'bytes_freed': freed}
