# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_installer - the standalone install kit: one folder (and one zip) a
client runs with nothing installed, not even Python, and no internet.

    python tools/build_installer.py                      # Windows x64 kit for the current version
    python tools/build_installer.py --extras live,plotting,xls,desktop
    python tools/build_installer.py --platform linux     # a venv-based kit for a Linux site server
    python tools/build_installer.py --python-zip C:\\dl\\python-3.12.7-embed-amd64.zip   # when python.org is not reachable here

What the Windows kit contains (dist/gea-program-<version>-win64/):

    python/              CPython <ver> embeddable distribution (python.org), site-packages enabled
    report-samples/      one rendered example of every report (docs/report_samples, from this checkout)
    wheels/              gea-program-<version>-py3-none-any.whl, numpy, the chosen extras, pip, setuptools:
                         every file the install needs, for win_amd64 / cp312, with a SHA-256 list
    install.cmd          installs the wheels into python/ from the wheels/ folder - no network, no admin
    pip_bootstrap.py     runs pip from its wheel as a module (install.cmd uses it; pip will not install itself any other way on Windows)
    gea.cmd              the `gea` command: python\\python.exe -m gea ...
    start-dashboard.cmd  creates the site workspace if it is missing, starts the service, opens the browser
    stop-dashboard.cmd   stops the service started by start-dashboard
    register-service.cmd / unregister-service.cmd   a per-user scheduled task that starts the dashboard at logon
    verify.cmd           runs the acceptance gate from the installed kit (the client's own SAT evidence)
    uninstall.cmd        removes the kit's own files; the workspace (the client's data) is never touched
    README.txt, MANIFEST.json, SHA256SUMS.txt

The build runs here, with internet, in the repository (it builds the wheel
from the checkout, so what ships is what the gate passed). The kit runs
there, without. A client's site data lives in the workspace folder the kit
creates (default: %LOCALAPPDATA%\\GEA-Program\\site), outside the kit, so
re-installing a newer kit never touches it.

Stdlib only (subprocess to pip).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DEFAULT = '3.12.7'
EXTRAS_DEFAULT = 'live,plotting,xls,desktop'


def sh(cmd, **kw):
    print('  $', ' '.join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def version() -> str:
    for line in (ROOT / 'pyproject.toml').read_text(encoding='utf-8').splitlines():
        if line.startswith('version = '):
            return line.split('"')[1]
    raise SystemExit('no version in pyproject.toml')


def git_commit() -> str | None:
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_wheel(wheels: Path) -> Path:
    sh([sys.executable, '-m', 'pip', 'wheel', str(ROOT), '--no-deps', '-w', str(wheels), '-q'])
    w = sorted(wheels.glob('gea_program-*.whl'))
    if not w:
        raise SystemExit('the package wheel was not built')
    return w[-1]


def requirements_from_wheel(whl: Path, extras: str) -> list[str]:
    """The wheel's own Requires-Dist lines for the base package and the named extras - so the dependency
    download never names gea-program itself. (The v0.6.0 kit runs #4-#6 asked pip for `gea-program[...]`
    with the index on, and pip took PyPI's wheel of the same version over the one just built from the
    checkout, overwriting it: the kit then carried the released code, not the commit being built.)"""
    import email
    import re
    want = {e.strip() for e in extras.split(',') if e.strip()}
    reqs: list[str] = []
    with zipfile.ZipFile(whl) as z:
        meta = next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))
        msg = email.message_from_bytes(z.read(meta))
    for line in msg.get_all('Requires-Dist') or []:
        req, _, marker = line.partition(';')
        m = re.search(r"""extra\s*==\s*['"]([^'"]+)['"]""", marker)
        if m and m.group(1) not in want:
            continue
        reqs.append(req.strip())
    return reqs


def download_wheels(wheels: Path, pkg: Path, extras: str, py: str, target: str) -> None:
    reqs = requirements_from_wheel(pkg, extras)
    base = [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '-d', str(wheels), '--find-links', str(wheels)]
    if target == 'windows':
        tag = 'cp' + py.replace('.', '')[:3]
        base += ['--platform', 'win_amd64', '--python-version', py, '--implementation', 'cp', '--abi', tag]
    sh(base + reqs + ['pip', 'setuptools'])


def assert_checkout_wheel(wheels: Path, pkg: Path, built_sha: str) -> None:
    """The kit carries exactly one gea-program wheel and it is the one built from this checkout."""
    found = sorted(wheels.glob('gea_program-*.whl'))
    if found != [pkg] or sha256(pkg) != built_sha:
        raise SystemExit(f'the package wheel in the kit is not the one built from this checkout: {[p.name for p in found]} (sha changed: {sha256(pkg) != built_sha})')


def fetch_python(py: str, out: Path, zip_path: Path | None) -> Path:
    dst = out / f'python-{py}-embed-amd64.zip'
    if zip_path:
        shutil.copyfile(zip_path, dst)
        return dst
    url = f'https://www.python.org/ftp/python/{py}/python-{py}-embed-amd64.zip'
    print('  fetching', url, flush=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dst, 'wb') as f:
        shutil.copyfileobj(r, f)
    return dst


def unpack_python(zip_file: Path, pydir: Path) -> None:
    with zipfile.ZipFile(zip_file) as z:
        z.extractall(pydir)
    pth = next(pydir.glob('python*._pth'))
    text = pth.read_text(encoding='utf-8')
    text = text.replace('#import site', 'import site')
    if 'Lib\\site-packages' not in text:
        text = text.rstrip('\n') + '\nLib\\site-packages\n'
    pth.write_text(text, encoding='utf-8')
    (pydir / 'Lib' / 'site-packages').mkdir(parents=True, exist_ok=True)


WIN_SCRIPTS = {
'install.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
echo GEA-Program {version} - standalone install (no network, no administrator rights)
if not exist python\python.exe ( echo python\python.exe is missing - the kit is incomplete & exit /b 1 )
for %%f in (wheels\pip-*.whl) do set PIPWHL=%%f
if "%PIPWHL%"=="" ( echo pip wheel missing from wheels\ & exit /b 1 )
echo == installing the package and every dependency from wheels\ ...
rem pip refuses to install pip when it is run as "python wheel\pip" on Windows (it wants "python -m pip"); the embeddable
rem Python ignores PYTHONPATH, so pip_bootstrap.py puts the wheel on sys.path and runs pip as a module from inside it
python\python.exe pip_bootstrap.py install --no-index --find-links wheels --no-warn-script-location "gea-program[{extras}]=={version}" pip setuptools
if errorlevel 1 ( echo INSTALL FAILED & exit /b 1 )
python\python.exe -c "import gea, numpy; print('installed: gea-program', gea.__version__, '/ numpy', numpy.__version__)"
if errorlevel 1 ( echo INSTALL FAILED: the package does not import & exit /b 1 )
echo installed %date% %time% > INSTALLED.txt
echo.
echo == done. Next: start-dashboard.cmd  (or verify.cmd to run the acceptance gate first)
endlocal
''',
'gea.cmd': r'''@echo off
"%~dp0python\python.exe" -m gea %*
''',
'start-dashboard.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
if "%GEA_WORKSPACE%"=="" set GEA_WORKSPACE=%LOCALAPPDATA%\GEA-Program\site
if "%GEA_PORT%"=="" set GEA_PORT=8765
if not exist "%GEA_WORKSPACE%\workspace.json" (
  echo == creating the site workspace at %GEA_WORKSPACE%
  python\python.exe -m gea workspace --path "%GEA_WORKSPACE%" --action init --name "%COMPUTERNAME%" --actor installer
)
echo == starting the dashboard at http://127.0.0.1:%GEA_PORT%/  (workspace %GEA_WORKSPACE%)
echo    close this window or run stop-dashboard.cmd to stop it
if not "%1"=="--no-browser" start "" "http://127.0.0.1:%GEA_PORT%/"
python\python.exe -m gea serve --workspace "%GEA_WORKSPACE%" --port %GEA_PORT%
endlocal
''',
'stop-dashboard.cmd': r'''@echo off
echo == stopping any GEA-Program service started from this kit
for /f "tokens=2" %%p in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr /i "PID"') do (
  wmic process where "ProcessId=%%p" get CommandLine 2>nul | findstr /i "gea serve" >nul && taskkill /pid %%p /f >nul 2>&1 && echo    stopped pid %%p
)
echo done
''',
'register-service.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
echo == registering a per-user scheduled task "GEA-Program Dashboard" that starts the dashboard at logon
schtasks /create /tn "GEA-Program Dashboard" /tr "\"%~dp0start-dashboard.cmd\" --no-browser" /sc onlogon /rl limited /f
if errorlevel 1 ( echo could not register the task & exit /b 1 )
echo    registered. It starts at your next logon; to start it now: schtasks /run /tn "GEA-Program Dashboard"
endlocal
''',
'unregister-service.cmd': r'''@echo off
schtasks /delete /tn "GEA-Program Dashboard" /f
''',
'verify.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
echo == the acceptance gate from the installed kit (two to four minutes; the client's own SAT evidence)
python\python.exe -m gea accept
endlocal
''',
'pip_bootstrap.py': r'''# runs pip from the pip wheel in wheels\ as a module, so pip may install itself (Windows refuses "python wheel\pip install pip")
import glob, os, runpy, sys
here = os.path.dirname(os.path.abspath(__file__))
whl = sorted(glob.glob(os.path.join(here, 'wheels', 'pip-*.whl')))
if not whl:
    sys.exit('pip wheel missing from wheels\\')
sys.path.insert(0, whl[-1])
runpy.run_module('pip', run_name='__main__', alter_sys=True)
''',
'uninstall.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
echo This removes the kit's own files under %~dp0 (python\, wheels\ and these scripts).
echo The site workspace (your wells, reports, records and accounts) is NOT touched.
set /p OK=Type YES to continue:
if /i not "%OK%"=="YES" ( echo cancelled & exit /b 0 )
schtasks /delete /tn "GEA-Program Dashboard" /f >nul 2>&1
cd ..
rmdir /s /q "%~dp0"
echo removed
endlocal
''',
}

LINUX_SCRIPTS = {
'install.sh': r'''#!/bin/sh
# GEA-Program {version} - standalone install for a Linux site server (needs python3 >= 3.10 on the machine; no network)
set -e
cd "$(dirname "$0")"
PY=${{PYTHON:-python3}}
"$PY" -c "import sys; assert sys.version_info >= (3, 10), 'python3 >= 3.10 is required'"
"$PY" -m venv venv
./venv/bin/python -m pip install --no-index --find-links wheels --upgrade pip setuptools >/dev/null
./venv/bin/python -m pip install --no-index --find-links wheels "gea-program[{extras}]=={version}"
./venv/bin/python -c "import gea, numpy; print('installed: gea-program', gea.__version__, '/ numpy', numpy.__version__)"
date > INSTALLED.txt
echo "done. Next: ./start-dashboard.sh  (or ./verify.sh)"
''',
'gea': r'''#!/bin/sh
exec "$(dirname "$0")/venv/bin/python" -m gea "$@"
''',
'start-dashboard.sh': r'''#!/bin/sh
cd "$(dirname "$0")"
WS=${{GEA_WORKSPACE:-$HOME/.local/share/gea-program/site}}
PORT=${{GEA_PORT:-8765}}
[ -f "$WS/workspace.json" ] || ./venv/bin/python -m gea workspace --path "$WS" --action init --name "$(hostname)" --actor installer
echo "starting the dashboard at http://127.0.0.1:$PORT/ (workspace $WS); Ctrl+C stops it"
exec ./venv/bin/python -m gea serve --workspace "$WS" --port "$PORT"
''',
'verify.sh': r'''#!/bin/sh
cd "$(dirname "$0")" && exec ./venv/bin/python -m gea accept
''',
'gea-program.service': r'''[Unit]
Description=GEA-Program dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory={kitdir}
Environment=GEA_WORKSPACE={workspace}
ExecStart={kitdir}/start-dashboard.sh
Restart=on-failure

[Install]
WantedBy=default.target
''',
}

README = '''GEA-Program {version} - standalone install kit ({target})
=============================================================

This folder contains everything the program needs: {pyline}, the package,
and every dependency, as files. Nothing is downloaded. No administrator
rights are needed. Your site data lives in a separate folder (the
workspace) that this kit creates on first start and never deletes.

1. {install}
2. {start}
   Your browser opens http://127.0.0.1:{port}/ ; the first visit asks you to
   create the administrator account (name and a password of 8+ characters).
3. Wells -> add a well from a file (historian CSV, LAS) or the catalogue.
   Patch panel -> add a patch to read the drill floor (WITS0), a WITSML
   store, OPC UA, MQTT or Modbus. Home -> Refresh every report.
4. {verify}  runs the acceptance gate (266 checks) from this installation and
   is your own acceptance evidence (also on the Verification page).
5. report-samples\  holds one rendered example of every report the program
   writes, from the build in this kit; SAMPLES.md names the command behind each.

{service}

Stopping: close the window, or {stop}.
Updating: install a newer kit beside this one and start it; the workspace is
found automatically. Uninstalling: {uninstall} (the workspace stays).
Workspace location: {workspace}  (set GEA_WORKSPACE before starting to use
another folder; set GEA_PORT to change the port).

The dashboard listens on this computer only. To serve a control room, start
it with --host 0.0.0.0 behind the site's TLS proxy; see README.md in the
package for the service's security notes.

Contents are listed in MANIFEST.json; SHA256SUMS.txt holds the hash of every
file so the kit can be verified after copying to the site.

Licence: Mozilla Public License 2.0. (c) 2026 Daniel T. Murphy / ENRGYONE.
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='build the standalone install kit')
    ap.add_argument('--platform', choices=['windows', 'linux'], default='windows')
    ap.add_argument('--python', default=PY_DEFAULT, help='CPython version of the embeddable distribution (windows)')
    ap.add_argument('--python-zip', default=None, help='a downloaded python-<ver>-embed-amd64.zip, when python.org is not reachable from here')
    ap.add_argument('--extras', default=EXTRAS_DEFAULT, help='comma-separated extras to include (desktop adds PyQt6, ~100 MB)')
    ap.add_argument('--out', default=str(ROOT / 'dist'))
    ap.add_argument('--no-zip', action='store_true')
    a = ap.parse_args(argv)

    ver = version()
    tag = 'win64' if a.platform == 'windows' else f'linux-{platform.machine()}'
    out = Path(a.out)
    kit = out / f'gea-program-{ver}-{tag}'
    if kit.exists():
        shutil.rmtree(kit)
    wheels = kit / 'wheels'
    wheels.mkdir(parents=True)
    print(f'== kit {kit.name}')
    print('== the package wheel (from this checkout)')
    pkg = build_wheel(wheels)
    built_sha = sha256(pkg)
    print('== dependency wheels (from the package wheel\'s own requirements; gea-program itself is never asked of the index)')
    download_wheels(wheels, pkg, a.extras, a.python, a.platform)
    assert_checkout_wheel(wheels, pkg, built_sha)
    if a.platform == 'windows':
        print('== embeddable Python')
        z = fetch_python(a.python, out, Path(a.python_zip) if a.python_zip else None)
        unpack_python(z, kit / 'python')
        scripts = {k: v.replace('{version}', ver).replace('{extras}', a.extras) for k, v in WIN_SCRIPTS.items()}
        for name, body in scripts.items():
            (kit / name).write_text(body.replace('\n', '\r\n'), encoding='utf-8', newline='')
        readme = README.format(version=ver, target='Windows x64', pyline=f'its own Python {a.python}', install='Double-click install.cmd (about a minute).',
                               start='Double-click start-dashboard.cmd.', verify='verify.cmd', port='8765',
                               service='To have the dashboard start at logon: register-service.cmd (unregister-service.cmd undoes it).',
                               stop='stop-dashboard.cmd', uninstall='uninstall.cmd', workspace='%LOCALAPPDATA%\\GEA-Program\\site')
    else:
        for name, body in LINUX_SCRIPTS.items():
            body = body.replace('{version}', ver).replace('{extras}', a.extras).replace('{kitdir}', '/opt/gea-program').replace('{workspace}', '/var/lib/gea-program/site').replace('{{', '{').replace('}}', '}')
            p = kit / name
            p.write_text(body, encoding='utf-8')
            if not name.endswith('.service'):
                p.chmod(0o755)
        readme = README.format(version=ver, target='Linux', pyline='a virtual environment built from the machine\'s python3 (3.10 or newer)', install='./install.sh',
                               start='./start-dashboard.sh', verify='./verify.sh', port='8765',
                               service='To run as a service: copy the kit to /opt/gea-program, edit gea-program.service if the paths differ, then\n  cp gea-program.service ~/.config/systemd/user/ && systemctl --user enable --now gea-program',
                               stop='Ctrl+C', uninstall='delete the kit folder', workspace='~/.local/share/gea-program/site')
    (kit / 'README.txt').write_text(readme, encoding='utf-8')
    samples = ROOT / 'docs' / 'report_samples'
    if samples.is_dir():
        shutil.copytree(samples, kit / 'report-samples')
    # manifest + hashes
    files = sorted(p for p in kit.rglob('*') if p.is_file())
    sums = [(sha256(p), p.relative_to(kit).as_posix()) for p in files]
    (kit / 'SHA256SUMS.txt').write_text('\n'.join(f'{h}  {n}' for h, n in sums) + '\n', encoding='utf-8')
    manifest = {'name': 'gea-program', 'version': ver, 'platform': tag, 'python': (a.python if a.platform == 'windows' else 'system python3 >= 3.10'),
                'extras': a.extras.split(','), 'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'package_wheel': pkg.name, 'package_wheel_sha256': sha256(pkg), 'commit': git_commit(), 'wheels': sorted(p.name for p in wheels.glob('*.whl')),
                'n_files': len(files), 'bytes': sum(p.stat().st_size for p in files)}
    (kit / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    print(f'== {len(files)} files, {manifest["bytes"] / 1e6:.1f} MB, {len(manifest["wheels"])} wheels')
    if not a.no_zip:
        zpath = out / f'{kit.name}.zip'
        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in files + [kit / 'SHA256SUMS.txt', kit / 'MANIFEST.json']:
                z.write(p, f'{kit.name}/{p.relative_to(kit).as_posix()}')
        print(f'== {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
