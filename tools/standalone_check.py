# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""standalone_check - GEA-Program is a self-contained package; this script proves it.

Three checks, each printed, all three gating (exit 1 on any finding):

  1. imports: every module under gea/ imports only the standard library, numpy,
     the declared optional extras (matplotlib, PyQt6, pymodbus, asyncua,
     paho-mqtt, xlrd, pyserial) and gea itself. No other package, ever.
  2. text: no tracked text file names another program's package, module or
     document-numbering scheme. The list of patterns is at the top of this file;
     add to it, never remove.
  3. metadata: pyproject.toml declares no dependency outside the allowed set,
     and the package data list names only files that exist.

Usage:  python tools/standalone_check.py            (from the repository root)
        python tools/standalone_check.py --quiet    (only the verdict line)
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
try:
    import tomllib                      # Python 3.11+
except ImportError:                     # 3.10: a small reader for the two tables this check needs
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_TOP = {
    'numpy', 'matplotlib', 'PyQt6', 'pymodbus', 'asyncua', 'paho', 'xlrd', 'serial', 'gea',
}
# Names of packages, modules and document schemes that belong to other programs.
# A tracked file naming any of them breaks the standalone guarantee.
FOREIGN_PATTERNS = [
    r'uqff',                       # any module or package name from that family
    r'star[-_ ]magic',             # the program GEA must not depend on or cite
    r'import_from_',               # any importer script
    r'IMPORT_RECORD',              # the import record it produced
    r'register_audit',             # the audit that measured what the import left behind
    r'\bv1\.\d+\.\d+\b',           # that program's simulator version chain (GEA is 0.x)
    r'\bv0\.[1-9]\d{2}\.\d+\b',    # that program's own version chain (three-digit minor)
    r'22Aug2026|grok_',            # the design-note thread the first template came from
    r'\bRule 7\b|\bPAPER_',        # its rule names and paper numbers
    r'aetheric',                   # sibling programs
    r'grok_conversation',          # working notes that are not part of any product
]
TEXT_EXT = {'.py', '.md', '.txt', '.toml', '.yml', '.yaml', '.json', '.csv', '.cff', '.ps1', '.html', '.gitignore', '.gitattributes'}
SELF = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, '/')


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = [l.strip() for l in out.splitlines() if l.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ('.git', '__pycache__', 'build', 'dist', '_transport', '_to_delete') and not d.endswith('.egg-info')]
        for f in fn:
            files.append(os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, '/'))
    return files


def check_imports() -> list[str]:
    findings = []
    pkg = os.path.join(ROOT, 'gea')
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith('.py'):
            continue
        tree = ast.parse(open(os.path.join(pkg, fn), encoding='utf-8').read(), fn)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split('.')[0]
                if top in ALLOWED_TOP or top in sys.stdlib_module_names:
                    continue
                findings.append(f'gea/{fn}:{node.lineno}: imports {n}')
    return findings


def check_text(files: list[str]) -> list[str]:
    findings = []
    rx = re.compile('|'.join(f'({p})' for p in FOREIGN_PATTERNS), re.IGNORECASE)
    for rel in files:
        if rel == SELF:
            continue
        ext = os.path.splitext(rel)[1] or os.path.basename(rel)
        if ext not in TEXT_EXT and os.path.basename(rel) not in TEXT_EXT:
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        try:
            text = open(p, encoding='utf-8').read()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if 'standalone-check: allow' in line:      # the vocabulary gate must spell what it blocks
                continue
            m = rx.search(line)
            if m:
                findings.append(f'{rel}:{i}: {m.group(0)!r} in: {line.strip()[:90]}')
    return findings


def _read_pyproject() -> tuple:
    """(dependency strings, package-data patterns for gea) from pyproject.toml."""
    path = os.path.join(ROOT, 'pyproject.toml')
    if tomllib is not None:
        with open(path, 'rb') as f:
            py = tomllib.load(f)
        proj = py.get('project', {})
        deps = list(proj.get('dependencies', []))
        for extra in proj.get('optional-dependencies', {}).values():
            deps += list(extra)
        return deps, list(py.get('tool', {}).get('setuptools', {}).get('package-data', {}).get('gea', []))
    text = open(path, encoding='utf-8').read()
    deps, pkg = [], []
    section = None
    for line in text.splitlines():
        t = line.strip()
        if t.startswith('['):
            section = t.strip('[]')
            continue
        strings = re.findall(r'"([^"]*)"', t)
        if section == 'project' and t.startswith('dependencies'):
            deps += strings
        elif section == 'project.optional-dependencies' and '=' in t:
            deps += strings
        elif section == 'tool.setuptools.package-data' and t.startswith('gea'):
            pkg += strings
    return deps, pkg


def check_metadata() -> list[str]:
    findings = []
    deps, package_data = _read_pyproject()
    for d in deps:
        name = re.split(r'[<>=!~\[; ]', d, maxsplit=1)[0].strip()
        if name.replace('-', '_') not in {'numpy', 'matplotlib', 'PyQt6', 'pymodbus', 'asyncua', 'paho_mqtt', 'xlrd', 'pyserial'}:
            findings.append(f'pyproject.toml: dependency outside the allowed set: {d}')
    for pattern in package_data:
        if '*' in pattern:
            continue
        if not os.path.exists(os.path.join(ROOT, 'gea', pattern)):
            findings.append(f'pyproject.toml: package-data names a missing file: {pattern}')
    return findings


def main(argv=None) -> int:
    quiet = '--quiet' in (argv or sys.argv[1:])
    files = tracked_files()
    results = [('imports', check_imports()), ('text', check_text(files)), ('metadata', check_metadata())]
    total = 0
    for name, findings in results:
        total += len(findings)
        if not quiet:
            print(f'[{name}] {len(findings)} finding(s)')
            for f in findings[:200]:
                print('   ', f)
    print(f'[STANDALONE] {"OK" if total == 0 else "FAIL"} - {len(files)} tracked files, {total} finding(s)')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
