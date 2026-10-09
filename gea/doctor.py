# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""doctor - "which code is running, and can it serve?" in one screen.

    gea doctor                       the environment: Python, the package, duplicates, the page, PyPI
    gea doctor --workspace C:\\site   plus the workspace: manifest, folders, accounts, patches, free port
    gea doctor --json                machine-readable

Every finding carries a one-line fix. Exit code 0 when nothing blocks
serving, 1 when something does. `gea serve` runs the same checks at start
and refuses to serve when the code it would serve is not the code it was
started from (an installed copy shadowing a checkout, or the reverse), so a
stale page is never served silently.

Stdlib only; the PyPI check is best-effort with a short timeout and never
blocks an offline site.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import sys
import sysconfig
from typing import List, Optional

from . import supervisor as _SV

MIN_PY = (3, 10)


def _finding(level: str, what: str, fix: str = '') -> dict:
    return {'level': level, 'what': what, 'fix': fix}


def check_environment() -> List[dict]:
    from . import __version__
    f: List[dict] = []
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    f.append(_finding('info', f'Python {sys.version.split()[0]} at {sys.executable}'))
    if sys.version_info < MIN_PY:
        f.append(_finding('block', f'Python {sys.version_info.major}.{sys.version_info.minor} is below the minimum {MIN_PY[0]}.{MIN_PY[1]}',
                          'install Python 3.10 or newer and reinstall: python -m pip install --upgrade "gea-program[live]"'))
    f.append(_finding('info', f'gea-program {__version__} at {pkg_dir}'))
    # duplicates: another copy of the package on sys.path that is not this one
    others = []
    for p in sys.path:
        cand = os.path.join(p, 'gea', '__init__.py')
        if os.path.isfile(cand) and os.path.abspath(os.path.dirname(cand)) != os.path.abspath(pkg_dir):
            others.append(os.path.dirname(cand))
    for o in others:
        ver = ''
        try:
            for line in open(os.path.join(o, '__init__.py'), encoding='utf-8'):
                if line.startswith('__version__'):
                    ver = line.split('"')[1]
                    break
        except OSError:
            pass
        f.append(_finding('warn', f'another copy of the package is on the path: {o} (version {ver or "unknown"}); the one above is what runs',
                          'remove the copy you do not want: python -m pip uninstall gea-program, or delete the stray folder'))
    # the installed distribution record(s) vs the code that actually runs
    try:
        from importlib import metadata
        seen = False
        for dist in metadata.distributions():
            if (dist.metadata['Name'] or '').lower().replace('_', '-') != 'gea-program':
                continue
            seen = True
            loc = str(getattr(dist, 'locate_file', lambda x: dist._path)(''))
            direct = dist.read_text('direct_url.json') or ''
            editable = '"editable": true' in direct.replace(' ', '')
            src = ''
            if editable:
                try:
                    src = json.loads(direct).get('url', '').replace('file://', '')
                except json.JSONDecodeError:
                    pass
            runs_here = os.path.abspath(src) == os.path.abspath(os.path.dirname(pkg_dir)) if src else os.path.abspath(loc) == os.path.abspath(os.path.dirname(pkg_dir))
            if dist.version != __version__ or not runs_here:
                f.append(_finding('warn', f"pip's record says gea-program {dist.version}{' (editable, from ' + src + ')' if editable else ' at ' + loc}; "
                                          f"the code running is {__version__} at {pkg_dir}",
                                  'make them one: python -m pip install --upgrade "gea-program[live]" (released), or python -m pip install -e <checkout> (development)'))
            else:
                f.append(_finding('ok', f"pip's record matches the running code ({dist.version}{', editable' if editable else ''})"))
        if not seen:
            f.append(_finding('info', 'gea-program is not pip-installed; running from a folder on the path'))
    except Exception as e:                                        # metadata is a convenience, never a blocker
        f.append(_finding('info', f'could not read pip metadata: {e}'))
    page = os.path.join(pkg_dir, 'web', 'app.html')
    if os.path.isfile(page):
        f.append(_finding('ok', f'dashboard page present ({os.path.getsize(page) // 1024} KB)'))
    else:
        f.append(_finding('block', 'the dashboard page (gea/web/app.html) is missing from this installation',
                          'reinstall: python -m pip install --upgrade --force-reinstall "gea-program[live]"'))
    for mod, extra in (('numpy', ''), ('asyncua', 'opcua'), ('paho.mqtt', 'mqtt'), ('pymodbus', 'modbus'), ('serial', 'serial'), ('xlrd', 'xls'), ('matplotlib', 'plotting')):
        present = importlib.util.find_spec(mod.split('.')[0]) is not None
        if mod == 'numpy' and not present:
            f.append(_finding('block', 'numpy is missing (the only required dependency)', 'python -m pip install numpy'))
        else:
            f.append(_finding('ok' if present else 'info', f'{mod}: {"installed" if present else "not installed"}' + ('' if present or not extra else f' (optional: pip install "gea-program[{extra}]")')))
    # the launcher may be in the interpreter's Scripts or, after a per-user install, in the user Scripts folder
    exe = 'gea.exe' if os.name == 'nt' else 'gea'
    candidates = [sysconfig.get_path('scripts') or '']
    try:
        candidates.append(sysconfig.get_path('scripts', 'nt_user' if os.name == 'nt' else 'posix_user') or '')
    except KeyError:
        pass
    candidates = [c for c in candidates if c]
    found = [c for c in candidates if os.path.isfile(os.path.join(c, exe))]
    on_path = os.environ.get('PATH', '').split(os.pathsep)
    if candidates and not found:
        f.append(_finding('info', f'no `gea` launcher in {" or ".join(candidates)}; use `python -m gea ...`'))
    elif found and not any(c in on_path for c in found):
        f.append(_finding('warn', f'the `gea` launcher is in {found[0]}, which is not on PATH', 'use `python -m gea ...`, or add that folder to PATH'))
    launcher = started_through_launcher()
    if launcher:
        f.append(_finding('warn', f'this process was started through the console launcher {launcher}: while it runs, pip cannot replace '
                          f'that launcher on Windows (WinError 32), so the program cannot be updated in place',
                          'stop the panel, then update, then start it with `python -m gea serve` (the kit launcher and start-gea.cmd do)'))
    newest = pypi_newest()

    def _vt(v):
        return tuple(int(x) if x.isdigit() else 0 for x in v.split('.'))
    if newest and _vt(newest) > _vt(__version__):
        f.append(_finding('warn', f'PyPI has gea-program {newest}; this is {__version__}', 'python -m pip install --upgrade "gea-program[live]"'))
    elif newest and _vt(newest) < _vt(__version__):
        f.append(_finding('info', f'this is {__version__}, ahead of the newest release on PyPI ({newest}) - a checkout or a pre-release kit'))
    elif newest:
        f.append(_finding('ok', f'this is the newest release on PyPI ({newest})'))
    else:
        f.append(_finding('info', 'PyPI not reachable from here (offline site, or a proxy) - version not compared'))
    return f


def started_through_launcher(argv0: Optional[str] = None) -> Optional[str]:
    """The path of the `gea` / `gea.exe` console launcher this process was started through, or None when it was
    started as `python -m gea` (or from a checkout). On Windows the launcher .exe is held open by the process it
    started, so pip cannot replace it while the panel runs: `gea update` from such a panel, and `pip install
    --upgrade` beside it, fail with WinError 32. The kit launchers and start-gea.cmd use `python -m gea` for this."""
    a0 = sys.argv[0] if argv0 is None else argv0
    base = re.split(r'[\\/]', a0 or '')[-1].lower()                      # either separator: the launcher path may be Windows-shaped
    if base in ('gea', 'gea.exe', 'gea-script.py', 'gea-script.pyw'):
        return a0
    return None


def pypi_newest(timeout_s: float = 3.0) -> Optional[str]:
    try:
        import urllib.request
        with urllib.request.urlopen('https://pypi.org/pypi/gea-program/json', timeout=timeout_s) as r:
            return json.loads(r.read())['info']['version']
    except Exception:
        return None


def update(check_only: bool = False, extras: str = 'live,plotting,xls,desktop', as_json: bool = False) -> int:
    """`gea update`: the running version against PyPI; unless check_only, `python -m pip install --upgrade
    "gea-program[extras]"` from this interpreter. The service must be restarted afterwards - this function
    says so; it never restarts anything itself. An offline kit has no PyPI: it says to install the newer kit."""
    from . import __version__
    import subprocess
    newest = pypi_newest()
    vt = lambda v: tuple(int(x) if x.isdigit() else 0 for x in str(v).split('.'))
    res = {'running': __version__, 'newest_pypi': newest, 'python': sys.executable, 'extras': extras,
           'state': 'unknown' if newest is None else ('current' if vt(newest) <= vt(__version__) else 'behind'), 'ran_pip': False, 'returncode': None}
    launcher = started_through_launcher()
    res['started_through_launcher'] = launcher
    if newest is None:
        res['note'] = 'PyPI is not reachable from here; an offline installation is updated by installing the newer kit from the release page'
    if not check_only and newest is not None and res['state'] == 'behind' and launcher and os.name == 'nt':
        res['note'] = (f'not run: this program was started through {launcher}, which pip cannot replace while it runs (WinError 32); '
                       'stop the panel, update from a prompt (`python -m pip install --upgrade "gea-program[live]"`), then start it with `python -m gea serve`')
        res['returncode'] = 2
    elif not check_only and newest is not None and res['state'] == 'behind':
        spec = f'gea-program[{extras}]=={newest}' if extras else f'gea-program=={newest}'
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', spec], capture_output=True, text=True)
        res.update({'ran_pip': True, 'returncode': r.returncode, 'pip_tail': (r.stdout + r.stderr)[-1500:]})
        res['note'] = ('installed; restart the service (gea serve) to run the new version' if r.returncode == 0
                       else 'pip failed; the tail of its output is in pip_tail (a running gea.exe or service holding the files is the usual cause on Windows)')
    if as_json:
        print(json.dumps(res, indent=1))
    else:
        print(f"update: running {res['running']}, newest on PyPI {res['newest_pypi'] or 'unknown'} - {res['state']}")
        if res.get('ran_pip'):
            print(f"  pip --upgrade gea-program[{extras}]=={newest}: {'ok' if res['returncode'] == 0 else 'FAILED'}")
            if res['returncode'] != 0:
                print(res['pip_tail'])
        if res.get('note'):
            print('  ' + res['note'])
    return 0 if res['returncode'] in (None, 0) else 1


def port_free(host: str, port: int) -> bool:
    """Can the service bind this port? (The only question that matters for serving.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def check_workspace(path: str, host: str = '127.0.0.1', port: int = 8765) -> List[dict]:
    f: List[dict] = []
    from .workspace import Workspace, WorkspaceError
    try:
        ws = Workspace(path)
    except WorkspaceError as e:
        return [_finding('block', str(e), f'gea workspace --path "{path}" --action init --name "<site name>"')]
    s = ws.summary()
    f.append(_finding('ok', f"workspace '{s['name']}' at {ws.path}: {s['n_wells']} wells, created {s['created_utc']} by program {s['program_version']}"))
    for d in ('wells', 'config', 'monitor', 'reports', 'jobs', 'records'):
        if not os.path.isdir(os.path.join(ws.path, d)):
            f.append(_finding('warn', f'folder {d}/ is missing (it is recreated on use)'))
    users = os.path.join(ws.path, 'users.json')
    n_users = 0
    if os.path.isfile(users):
        try:
            n_users = len(json.load(open(users, encoding='utf-8')).get('users', []))
        except (OSError, json.JSONDecodeError):
            f.append(_finding('block', 'users.json is not valid JSON', 'restore it from a backup, or move it aside to start with a fresh administrator'))
    f.append(_finding('info', f'{n_users} account(s)' + ('' if n_users else ' - the first visit to the page creates the administrator')))
    try:
        from .patches import PatchStore
        ps = PatchStore(ws).list()
        f.append(_finding('info', f'{len(ps)} patch(es) defined, {sum(1 for p in ps if p.get("enabled", True))} enabled'))
    except Exception as e:
        f.append(_finding('warn', f'patches.json could not be read: {e}'))
    if port_free(host, port):
        f.append(_finding('ok', f'port {port} is free on {host}'))
    else:
        f.append(_finding('block', f'port {port} on {host} is already in use (another gea serve, or another program)',
                          f'stop the other service, or start with --port {port + 1}'))
    prev = _SV.previous_run(ws.path)
    if prev['status'] == 'UNCLEAN':
        f.append(_finding('warn', 'the last run of the control panel was killed rather than stopped: ' + prev['detail'],
                          'nothing to fix by hand - the next start brings the live patches up first and then rebuilds '
                          'every recorded dataset whose source has grown since. `gea serve` logs both.'))
    elif prev['status'] == 'FIRST_RUN':
        f.append(_finding('info', 'no run log yet: this workspace has not served before'))
    else:
        f.append(_finding('ok', 'the last run of the control panel stopped cleanly: ' + prev['detail']))
    f.append(_finding('info', 'auto-restart is ' + ('on' if _SV.auto_restart(ws.path) else 'off')
                      + ' for this workspace (' + _SV.auto_flag_path(ws.path) + ')'))
    try:
        test = os.path.join(ws.path, 'jobs', '.write_test')
        with open(test, 'w') as t:
            t.write('ok')
        os.remove(test)
        f.append(_finding('ok', 'workspace is writable'))
    except OSError as e:
        f.append(_finding('block', f'workspace is not writable: {e}', 'choose a folder your account can write to'))
    return f


def code_matches_launch() -> Optional[str]:
    """When `gea` was started from a checkout but imports an installed copy (or the reverse), say so."""
    pkg_dir = os.path.abspath(os.path.dirname(__file__))
    cwd_pkg = os.path.join(os.getcwd(), 'gea', '__init__.py')
    if os.path.isfile(cwd_pkg) and os.path.abspath(os.path.dirname(cwd_pkg)) != pkg_dir:
        return (f'you are in a checkout ({os.getcwd()}) but the running package is {pkg_dir}; '
                f'the page and commands come from the latter. Use `python -m pip install -e .` here, or run from elsewhere.')
    return None


def kit_dir() -> Optional[str]:
    """The installed kit this interpreter belongs to, or None when the program is being run from a checkout
    or a system Python. The kit is the folder holding the launcher, and the interpreter it starts lives two
    or three levels under it (python\\python.exe on Windows, python/bin/python3 or venv/bin/python here)."""
    d = os.path.dirname(os.path.abspath(sys.executable))
    for _ in range(4):
        if any(os.path.isfile(os.path.join(d, n)) for n in ('start-dashboard.cmd', 'start-dashboard.sh')):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def check_launcher(kit: Optional[str] = None) -> List[dict]:
    """Does the installed launcher honour the restart contract?

    This is the check that would have caught the defect it was written for. A kit built before the contract
    existed ends its batch file with the serve command and nothing after it, so when the control panel stops
    the window falls back to whatever shell was underneath - on a box whose console is a Python profile, a
    bare `>>>` where a program used to be. The launcher text is on disk and can simply be read, so there is
    no reason to find this out from an operator looking at an interpreter prompt.
    """
    f: List[dict] = []
    kit = kit or kit_dir()
    if not kit:
        f.append(_finding('info', 'not running from an installed kit, so there is no launcher to check '
                                  '(a checkout starts the panel with `python -m gea serve`, and the shell it '
                                  'returns to is the one you started it from)'))
        return f
    win = os.path.join(kit, 'start-dashboard.cmd')
    nix = os.path.join(kit, 'start-dashboard.sh')
    for path, is_win in ((win, True), (nix, False)):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        try:
            txt = open(path, 'r', encoding='utf-8', errors='replace').read()
        except OSError as e:
            f.append(_finding('warn', f'{name} could not be read: {e}'))
            continue
        low = txt.lower()
        restart = ('86' in txt) and ('goto gea_run' in low if is_win else 'continue' in low)
        if restart:
            f.append(_finding('ok', f'{name} starts the panel again when it exits with {_SV.EXIT_RESTART} '
                                    '(the Restart button in the control panel)'))
        else:
            f.append(_finding('warn', f'{name} does not act on exit code {_SV.EXIT_RESTART}: the Restart button will stop '
                                      'the panel and nothing will start it again',
                              'reinstall the kit built from this version - install.cmd/install.sh rewrites the launcher'))
        if is_win:
            tail = [ln for ln in txt.strip().splitlines() if ln.strip() and not ln.strip().startswith('rem ')]
            ends_ps = bool(tail) and tail[-1].lower().startswith('powershell')
            if ends_ps:
                f.append(_finding('ok', f'{name} hands the window to a PowerShell prompt when the panel stops, '
                                        'so the window never falls back to a Python prompt'))
            else:
                f.append(_finding('warn', f'{name} does not end with a PowerShell prompt: when the panel stops this window '
                                          'returns to whatever shell started it, which on a console opened from a Python '
                                          'profile leaves the operator at a Python prompt - a bare `>>>` where a program '
                                          'used to be, and an interpreter that is not this program',
                                  'reinstall the kit built from this version'))
        if 'auto_restart.flag' in low:
            f.append(_finding('ok', f'{name} reads records/auto_restart.flag, so the auto-restart switch in the control panel '
                                    f'is acted on (at most {_SV.AUTO_MAX} unexpected stops in a row)'))
        else:
            f.append(_finding('warn', f'{name} does not read records/auto_restart.flag: the auto-restart switch in the control '
                                      'panel will be recorded and will not be acted on',
                              'reinstall the kit built from this version'))
    if not os.path.isfile(win) and not os.path.isfile(nix):
        f.append(_finding('warn', f'no start-dashboard launcher in {kit}'))
    return f


def run(workspace: Optional[str] = None, host: str = '127.0.0.1', port: int = 8765, as_json: bool = False) -> int:
    findings = check_environment()
    m = code_matches_launch()
    if m:
        findings.append(_finding('warn', m, 'python -m pip install -e .   (in the checkout) or cd elsewhere'))
    findings += check_launcher()
    if workspace:
        findings += check_workspace(workspace, host, port)
    blocks = [x for x in findings if x['level'] == 'block']
    if as_json:
        print(json.dumps({'findings': findings, 'ok': not blocks}, indent=1))
    else:
        mark = {'ok': '  ok  ', 'info': ' info ', 'warn': ' WARN ', 'block': 'BLOCK '}
        for x in findings:
            print(f"[{mark[x['level']]}] {x['what']}")
            if x['fix']:
                print(f"         fix: {x['fix']}")
        print('[doctor] ' + ('nothing blocks serving' if not blocks else f'{len(blocks)} blocking finding(s) - see the fixes above'))
    return 0 if not blocks else 1
