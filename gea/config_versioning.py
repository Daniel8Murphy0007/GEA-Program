"""config_versioning — version-controlled configuration with export, import
and rollback (SOW 4.2.1.4 change and continuity; 4.2.24 audit trail).

Every configuration the program runs on - the well-test criteria file, the
alarm definitions, the tag catalogue, the drift-monitor thresholds - is
committed here as a numbered version with author, note, timestamp, sha256
and a key-level diff against the previous version. Nothing is ever deleted:
a rollback is a new version whose content equals an older one, recorded as
such. `export` writes any version to a file; `import_file` commits a file.

Store layout (one directory):

    <dir>/<name>/v0001.json      the content
    <dir>/<name>/history.jsonl   one line per version: meta + diff summary

Headless-safe: standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _iso(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]


def _flatten(obj: Any, prefix: str = '') -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f'{prefix}{k}.'))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f'{prefix}[{i}].'))
    else:
        out[prefix[:-1]] = obj
    return out


def diff(old: Any, new: Any) -> dict:
    a, b = _flatten(old), _flatten(new)
    added = sorted(k for k in b if k not in a)
    removed = sorted(k for k in a if k not in b)
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    return {'added': added, 'removed': removed,
            'changed': [{'key': k, 'before': a[k], 'after': b[k]} for k in changed],
            'n_changes': len(added) + len(removed) + len(changed)}


class ConfigStore:
    def __init__(self, root: str):
        self.root = str(root)
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, name: str) -> str:
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        return d

    def history(self, name: str) -> List[dict]:
        p = os.path.join(self._dir(name), 'history.jsonl')
        if not os.path.exists(p):
            return []
        with open(p, encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]

    def latest_version(self, name: str) -> int:
        h = self.history(name)
        return h[-1]['version'] if h else 0

    def get(self, name: str, version: Optional[int] = None) -> Any:
        v = version or self.latest_version(name)
        if v == 0:
            raise KeyError(f'no versions of {name}')
        with open(os.path.join(self._dir(name), f'v{v:04d}.json'), encoding='utf-8') as f:
            return json.load(f)

    def commit(self, name: str, content: Any, author: str, note: str = '', now: Optional[datetime] = None,
               rollback_of: Optional[int] = None) -> dict:
        prev_v = self.latest_version(name)
        prev = self.get(name, prev_v) if prev_v else None
        if prev is not None and _sha(prev) == _sha(content) and rollback_of is None:
            return {'name': name, 'version': prev_v, 'unchanged': True, 'sha256': _sha(content)}
        v = prev_v + 1
        with open(os.path.join(self._dir(name), f'v{v:04d}.json'), 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=1, sort_keys=True, default=str)
        meta = {'name': name, 'version': v, 'timestamp_utc': _iso(now), 'author': author, 'note': note,
                'sha256': _sha(content), 'previous_version': prev_v or None,
                'diff': diff(prev, content) if prev is not None else {'added': sorted(_flatten(content)), 'removed': [], 'changed': [], 'n_changes': len(_flatten(content))},
                'rollback_of': rollback_of}
        with open(os.path.join(self._dir(name), 'history.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(meta, default=str) + '\n')
        return meta

    def import_file(self, name: str, path: str, author: str, note: str = '', now: Optional[datetime] = None) -> dict:
        with open(path, encoding='utf-8') as f:
            content = json.load(f)
        return self.commit(name, content, author, note or f'imported from {os.path.basename(path)}', now=now)

    def export(self, name: str, path: str, version: Optional[int] = None) -> str:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.get(name, version), f, indent=1, sort_keys=True)
        return path

    def rollback(self, name: str, to_version: int, author: str, note: str = '', now: Optional[datetime] = None) -> dict:
        target = self.get(name, to_version)
        return self.commit(name, target, author, note or f'rollback to v{to_version:04d}', now=now, rollback_of=to_version)

    def names(self) -> List[str]:
        return sorted(d for d in os.listdir(self.root) if os.path.isdir(os.path.join(self.root, d)))

    def summary(self) -> List[dict]:
        out = []
        for n in self.names():
            h = self.history(n)
            if h:
                out.append({'name': n, 'versions': len(h), 'latest': h[-1]['version'], 'sha256': h[-1]['sha256'],
                            'last_change_utc': h[-1]['timestamp_utc'], 'last_author': h[-1]['author'],
                            'rollbacks': sum(1 for e in h if e.get('rollback_of'))})
        return out
