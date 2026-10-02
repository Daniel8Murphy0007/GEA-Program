# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""housekeeping - keep a long-running site from filling its disk, without
losing a record.

Three jobs, each a dry run unless told to apply:

    rotate   the append-only logs (records/audit.jsonl, records/notifications.jsonl,
             each well's alarm_events.jsonl) are segmented when they pass a size:
             the file is renamed <name>.<UTC stamp> and a fresh one starts. Nothing
             is deleted - segments are the record. Readers that rebuild state from
             the alarm log read the live file; a segmented alarm log is sealed with
             a PROCESSED watermark, so the live file starts from a known point.
    jobs     finished job folders (DONE/FAILED/CANCELLED) older than `keep_days`,
             beyond the newest `keep_n`, are removed. The audit log already carries
             every submission and finish; the folder held the log text.
    records  live recording files (patch_<name>_<YYYYMMDD>.records.csv) and the
             folded stream files older than `keep_days` are removed, except the
             newest stream file per well, which the dashboard reads.

    gea housekeeping --workspace C:\\site              shows what would happen
    gea housekeeping --workspace C:\\site --apply      does it, and audits it

Scheduling it daily from Administration is the normal way to run it.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import List, Optional

DEFAULTS = {'rotate_mb': 50.0, 'jobs_keep_days': 30, 'jobs_keep_n': 500, 'records_keep_days': 90}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def rotate_file(path: str, max_mb: float, seal: Optional[str] = None, apply: bool = False) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    size = os.path.getsize(path)
    if size < max_mb * 1024 * 1024:
        return None
    target = f'{path}.{_stamp()}'
    act = {'file': path, 'size_mb': round(size / 1048576, 2), 'segment': target, 'applied': apply}
    if apply:
        if seal:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(seal if seal.endswith('\n') else seal + '\n')
        os.replace(path, target)
        with open(path, 'a', encoding='utf-8'):
            pass
    return act


def _alarm_seal(path: str) -> Optional[str]:
    """The last PROCESSED watermark of an alarm log, re-written as the first line of the fresh file."""
    last = None
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get('event') == 'PROCESSED':
                    last = e
    except OSError:
        return None
    return json.dumps(last, sort_keys=True) if last else None


def run(workspace_path: str, apply: bool = False, rotate_mb: float = DEFAULTS['rotate_mb'], jobs_keep_days: int = DEFAULTS['jobs_keep_days'],
        jobs_keep_n: int = DEFAULTS['jobs_keep_n'], records_keep_days: int = DEFAULTS['records_keep_days'], actor: str = 'housekeeping') -> dict:
    from .workspace import Workspace
    ws = Workspace(workspace_path)
    now = time.time()
    out = {'applied': apply, 'rotated': [], 'jobs_removed': [], 'records_removed': [], 'bytes_freed': 0}
    # 1. rotation
    for p in (ws.audit_path, os.path.join(ws.path, 'records', 'notifications.jsonl')):
        r = rotate_file(p, rotate_mb, apply=apply)
        if r:
            out['rotated'].append(r)
    wells_dir = os.path.join(ws.reports_dir, 'wells')
    if os.path.isdir(wells_dir):
        for name in os.listdir(wells_dir):
            p = os.path.join(wells_dir, name, 'alarm_events.jsonl')
            if os.path.isfile(p) and os.path.getsize(p) >= rotate_mb * 1048576:
                seal = _alarm_seal(p)
                r = rotate_file(p, rotate_mb, apply=apply)
                if r:
                    r['watermark_carried'] = bool(seal)
                    if apply and seal:                      # the fresh file starts at the watermark, so nothing is re-processed
                        with open(p, 'a', encoding='utf-8') as f:
                            f.write(seal + '\n')
                    out['rotated'].append(r)
    # 2. jobs
    jobs_dir = os.path.join(ws.path, 'jobs')
    if os.path.isdir(jobs_dir):
        finished = []
        for jid in os.listdir(jobs_dir):
            d = os.path.join(jobs_dir, jid)
            jp = os.path.join(d, 'job.json')
            if not os.path.isfile(jp):
                continue
            try:
                with open(jp, encoding='utf-8') as f:
                    j = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if j.get('status') in ('DONE', 'FAILED', 'CANCELLED'):
                finished.append((os.path.getmtime(jp), jid, d))
        finished.sort(reverse=True)
        for mtime, jid, d in finished[jobs_keep_n:]:
            if now - mtime > jobs_keep_days * 86400:
                size = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(d) for fn in fns)
                out['jobs_removed'].append({'job': jid, 'age_days': round((now - mtime) / 86400, 1), 'bytes': size})
                out['bytes_freed'] += size
                if apply:
                    shutil.rmtree(d, ignore_errors=True)
    # 3. live records
    for w in ws.wells():
        live = os.path.join(ws.path, 'wells', w['id'], 'records', 'live')
        if not os.path.isdir(live):
            continue
        streams = sorted(fn for fn in os.listdir(live) if fn.endswith('_stream.csv'))
        newest_stream = streams[-1] if streams else None
        for fn in os.listdir(live):
            p = os.path.join(live, fn)
            if not os.path.isfile(p) or fn == newest_stream or fn.endswith('.state.json'):
                continue
            if not (fn.endswith('.records.csv') or fn.endswith('_stream.csv')):
                continue
            age = now - os.path.getmtime(p)
            if age > records_keep_days * 86400:
                size = os.path.getsize(p)
                out['records_removed'].append({'well_id': w['id'], 'file': fn, 'age_days': round(age / 86400, 1), 'bytes': size})
                out['bytes_freed'] += size
                if apply:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    if apply:
        ws.audit(actor, 'housekeeping', {'rotated': len(out['rotated']), 'jobs_removed': len(out['jobs_removed']),
                                         'records_removed': len(out['records_removed']), 'bytes_freed': out['bytes_freed']})
    return out
