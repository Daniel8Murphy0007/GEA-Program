"""sbom — the software bill of materials, generated from the running
environment (SCC 16.0; software with licences).

Eight fields per component, the minimum a client inspection asks for:
    name, version, supplier, licence, hash, identifier (purl), relationship
    (root / direct / optional), generated timestamp.

Nothing is typed in by hand: versions and licences come from the installed
distributions' metadata (importlib.metadata); the program's own components
are hashed from the source files that ship; optional components (plotting,
desktop UI) are listed as optional whether or not they are installed, with
'not installed' recorded when absent. A component whose licence is not
declared in its metadata is recorded as 'not declared in metadata', never
guessed.

Headless-safe: standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
OPTIONAL = {'matplotlib': 'plotting (figures in reports)', 'PyQt6': 'desktop operator application'}
DIRECT = {'numpy': 'numerical arrays (all engines)'}


def _iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dist(name: str) -> Optional[dict]:
    try:
        import importlib.metadata as m
        md = m.metadata(name)
    except Exception:
        return None
    lic = md.get('License-Expression') or md.get('License') or ''
    if not lic or len(lic) > 80:
        cls = [c for c in md.get_all('Classifier') or [] if c.startswith('License ::')]
        lic = cls[-1].split('::')[-1].strip() if cls else (lic[:80] if lic else 'not declared in metadata')
    files = []
    try:
        files = [f for f in (m.files(name) or []) if str(f).endswith('RECORD')]
    except Exception:
        pass
    h = 'n/a'
    try:
        if files:
            h = hashlib.sha256(files[0].read_binary()).hexdigest()[:16]
    except Exception:
        pass
    return {'version': md.get('Version', ''), 'supplier': (md.get('Author') or md.get('Maintainer') or md.get('Author-email') or 'not declared in metadata')[:60],
            'licence': lic, 'hash': h, 'home': md.get('Home-page', '')}


def _tree_hash(dirpath: str) -> str:
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(dirpath)):
        for fn in sorted(files):
            if fn.endswith('.py'):
                p = os.path.join(root, fn)
                h.update(fn.encode()); h.update(open(p, 'rb').read())
    return h.hexdigest()[:16]


def generate(program_name: str = 'Downhole Gauge Monitoring', program_version: str = '') -> dict:
    from . import __version__
    ver = program_version or __version__
    ts = _iso()
    comps: List[dict] = []
    comps.append({'name': program_name, 'version': ver, 'supplier': 'ENRGYONE', 'licence': 'per the client agreement',
                  'hash': _tree_hash(_HERE), 'identifier': f'pkg:generic/downhole-gauge-monitoring@{ver}',
                  'relationship': 'root', 'generated_utc': ts})
    comps.append({'name': 'Python', 'version': platform.python_version(), 'supplier': 'Python Software Foundation',
                  'licence': 'PSF License', 'hash': 'n/a (runtime)', 'identifier': f'pkg:generic/python@{platform.python_version()}',
                  'relationship': 'runtime', 'generated_utc': ts})
    for name, role in DIRECT.items():
        d = _dist(name)
        comps.append({'name': name, 'version': d['version'] if d else 'not installed', 'supplier': d['supplier'] if d else '-',
                      'licence': d['licence'] if d else '-', 'hash': d['hash'] if d else '-',
                      'identifier': f"pkg:pypi/{name.lower()}@{d['version']}" if d else f'pkg:pypi/{name.lower()}',
                      'relationship': f'direct ({role})', 'generated_utc': ts})
    for name, role in OPTIONAL.items():
        d = _dist(name)
        comps.append({'name': name, 'version': d['version'] if d else 'not installed', 'supplier': d['supplier'] if d else '-',
                      'licence': d['licence'] if d else '-', 'hash': d['hash'] if d else '-',
                      'identifier': f"pkg:pypi/{name.lower()}@{d['version']}" if d else f'pkg:pypi/{name.lower()}',
                      'relationship': f'optional ({role})', 'generated_utc': ts})
    return {'format': 'eight-field SBOM (name, version, supplier, licence, hash, identifier, relationship, generated)',
            'generated_utc': ts, 'platform': platform.platform(), 'python': sys.version.split()[0],
            'components': comps, 'n_components': len(comps)}


def write(sbom: dict, out_dir: str, basename: str = 'sbom') -> Dict[str, str]:
    import csv
    os.makedirs(out_dir, exist_ok=True)
    pj = os.path.join(out_dir, basename + '.json')
    pc = os.path.join(out_dir, basename + '.csv')
    with open(pj, 'w', encoding='utf-8') as f:
        json.dump(sbom, f, indent=1)
    with open(pc, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['name', 'version', 'supplier', 'licence', 'hash', 'identifier', 'relationship', 'generated_utc'])
        w.writeheader()
        for c in sbom['components']:
            w.writerow(c)
    return {'json': pj, 'csv': pc}
