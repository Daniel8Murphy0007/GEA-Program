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
    open-dashboard.cmd   the control panel in a window of its own (Edge's app window), started first when it is not running,
                         and its icon in the notification area (gea launch, gea tray)
    verify.cmd           runs the acceptance gate from the installed kit (the client's own SAT evidence)
    gea.ico              the program's icon (gea/icon.py), for the installer and its shortcuts
    uninstall.cmd        removes the kit's own files; the workspace (the client's data) is never touched
    README.txt, MANIFEST.json, SHA256SUMS.txt

The build workflow also makes GEA-Program-Setup-<version>.exe from the same kit folder: tools/installer.iss
(Inno Setup 6), a per-user installer with shortcuts, an Add/Remove Programs entry and in-place upgrades.

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
title GEA - Operations Control Panel
setlocal enabledelayedexpansion
cd /d "%~dp0"
rem the workspace: GEA_WORKSPACE when it is set (the launcher sets it), else the folder the installer recorded in
rem site-workspace.txt (UTF-8, read under code page 65001 so a folder name with accents arrives whole), else the default
if not "%GEA_WORKSPACE%"=="" goto gea_ws_done
if not exist "%~dp0site-workspace.txt" goto gea_ws_default
for /f "tokens=2 delims=:." %%c in ('chcp') do set "GEA_CP=%%c"
chcp 65001 >nul
set /p GEA_WORKSPACE=<"%~dp0site-workspace.txt"
chcp %GEA_CP% >nul
:gea_ws_default
if "%GEA_WORKSPACE%"=="" set GEA_WORKSPACE=%LOCALAPPDATA%\GEA-Program\site
:gea_ws_done
if "%GEA_PORT%"=="" set GEA_PORT=8765
if not exist "%GEA_WORKSPACE%\workspace.json" (
  echo == creating the site workspace at %GEA_WORKSPACE%
  python\python.exe -m gea workspace --path "%GEA_WORKSPACE%" --action init --name "Pad 3" --actor installer
)
echo == starting the dashboard on port %GEA_PORT%  (workspace %GEA_WORKSPACE%; http://127.0.0.1:%GEA_PORT%/, or https:// once HTTPS is on - gea tls)
echo    close this window or run stop-dashboard.cmd to stop it
rem the service opens the browser itself, once its port is listening - a browser opened from here raced the
rem server and showed a failure page from the second before. A scheduled start (--no-browser) and a restart
rem from the control panel (the page is already open) do not open another one.
set GEA_BROWSER=
if "%1"=="--no-browser" set GEA_BROWSER=--no-browser

rem The control panel decides how it ends and says so in its exit code: 86 means start me again,
rem anything else means stop. This window never falls back to whatever shell is underneath it -
rem on any other code it becomes a PowerShell prompt that names the program and how to start it.
rem
rem A code that is neither 0 nor 86 is a death, not a decision. The operator says in the control panel
rem whether the panel may be started again by itself after one of those, and that answer is written to
rem one file this script can read. Five deaths in a row and it stops asking: a program that crashes while
rem starting would otherwise flicker all night, and the operator needs the error, not the loop.
set GEA_FAILS=0
:gea_run
python\python.exe -m gea serve --workspace "%GEA_WORKSPACE%" --port %GEA_PORT% %GEA_BROWSER%
set GEA_RC=%ERRORLEVEL%
set GEA_BROWSER=--no-browser
if "%GEA_RC%"=="86" (
  set GEA_FAILS=0
  echo.
  echo == restart authorised from the control panel - starting it again
  echo.
  goto gea_run
)
if "%GEA_RC%"=="0" goto gea_stopped
rem reading the flag outside an if-block on purpose: a redirect inside parentheses is parsed before the
rem block runs and is a classic way to get a batch file that works everywhere except the one machine.
set GEA_AUTO=0
if exist "%GEA_WORKSPACE%\records\auto_restart.flag" goto gea_read_flag
goto gea_auto_done
:gea_read_flag
set /p GEA_AUTO=<"%GEA_WORKSPACE%\records\auto_restart.flag"
:gea_auto_done
if not "%GEA_AUTO%"=="1" goto gea_stopped
set /a GEA_FAILS=%GEA_FAILS%+1
if %GEA_FAILS% GTR 5 (
  echo.
  echo == the control panel has stopped unexpectedly 5 times in a row. Not starting it again by itself.
  goto gea_stopped
)
echo.
echo == the control panel stopped unexpectedly ^(exit %GEA_RC%^) - auto-restart is on, attempt %GEA_FAILS% of 5
echo.
timeout /t 5 /nobreak >nul 2>&1
goto gea_run

:gea_stopped
echo.
echo == the GEA Operations Control Panel has stopped ^(exit %GEA_RC%^)
echo    this window is a PowerShell prompt now; start-dashboard.cmd starts the panel again
endlocal & set GEA_RC=%GEA_RC%
powershell.exe -NoLogo -NoExit -Command "Set-Location -LiteralPath '%~dp0'; Write-Host ''; Write-Host 'GEA - Operations Control Panel: stopped (exit %GEA_RC%).' -ForegroundColor Cyan; Write-Host 'Start it again:  .\start-dashboard.cmd' -ForegroundColor Cyan; Write-Host 'Run log:         %GEA_WORKSPACE%\records\runlog.jsonl' -ForegroundColor DarkGray; Write-Host ''"
''',
'stop-dashboard.cmd': r'''@echo off
rem Stops the control panel started from this kit: the window running start-dashboard.cmd first, so its restart
rem loop cannot start the panel again, then any "gea serve" this kit's Python still runs. With --all also the
rem notification-area icon (the installer stops everything before an upgrade and before it uninstalls).
rem PowerShell's Get-CimInstance, not wmic: Windows 11 24H2 no longer has wmic, and the stop of v0.64 and
rem before found nothing to stop there.
setlocal
set "GEA_KIT=%~dp0"
set "GEA_ALL=0"
if /i "%~1"=="--all" set "GEA_ALL=1"
echo == stopping the GEA-Program control panel started from this kit
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$k = $env:GEA_KIT; $all = $env:GEA_ALL -eq '1'; $ic = [StringComparison]::OrdinalIgnoreCase; $ps = @(Get-CimInstance Win32_Process); $mine = @($ps | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($k, $ic) -and $_.Name -like 'python*.exe' }); $kill = @($ps | Where-Object { $_.Name -eq 'cmd.exe' -and ([string]$_.CommandLine).IndexOf($k + 'start-dashboard.cmd', $ic) -ge 0 }); foreach ($s in @($mine | Where-Object { [string]$_.CommandLine -match ' gea serve' })) { $kill += @($ps | Where-Object { $_.ProcessId -eq $s.ParentProcessId -and $_.Name -eq 'cmd.exe' -and ([string]$_.CommandLine) -like '*start-dashboard.cmd*' }); $kill += $s }; if ($all) { $kill += @($mine | Where-Object { [string]$_.CommandLine -match ' gea (tray|launch)' }) }; $done = @{}; foreach ($p in $kill) { if (-not $done.ContainsKey($p.ProcessId)) { $done[$p.ProcessId] = 1; & taskkill.exe /pid $p.ProcessId /t /f 2>&1 | Out-Null; Write-Host ('   stopped pid ' + $p.ProcessId + ' (' + $p.Name + ')') } }"
echo done
endlocal
''',
'register-service.cmd': r'''@echo off
setlocal
cd /d "%~dp0"
echo == registering a per-user scheduled task "GEA-Program Dashboard" that starts the dashboard at logon
rem the task runs the launcher, which starts the control panel in a window of its own and returns: a task that ran
rem start-dashboard.cmd itself was still "running" after three days and Task Scheduler's default limit stopped it
schtasks /create /tn "GEA-Program Dashboard" /tr "\"%~dp0python\pythonw.exe\" -m gea launch --background" /sc onlogon /rl limited /f
if errorlevel 1 ( echo could not register the task & exit /b 1 )
echo    registered. It starts at your next logon; to start it now: schtasks /run /tn "GEA-Program Dashboard"
endlocal
''',
'unregister-service.cmd': r'''@echo off
schtasks /delete /tn "GEA-Program Dashboard" /f
''',
'open-dashboard.cmd': r'''@echo off
rem the control panel in a window of its own (Edge's app window, else Chrome's, else the browser), started first
rem when it is not running, and its icon in the notification area - what the installer's GEA-Program shortcut runs
start "" "%~dp0python\pythonw.exe" -m gea launch %*
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
call "%~dp0stop-dashboard.cmd" --all
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
# The control panel decides how it ends and says so in its exit code: 86 means start it again, anything
# else means stop. This script never leaves the operator in a shell the program did not choose.
set -u
cd "$(dirname "$0")"
: "${GEA_WORKSPACE:=$HOME/.local/share/GEA-Program/site}"
: "${GEA_PORT:=8765}"
if [ ! -f "$GEA_WORKSPACE/workspace.json" ]; then
  echo "== creating the site workspace at $GEA_WORKSPACE"
  ./python/bin/python3 -m gea workspace --path "$GEA_WORKSPACE" --action init --name "Pad 3" --actor installer
fi
echo "== starting the dashboard on port $GEA_PORT  (workspace $GEA_WORKSPACE; http://127.0.0.1:$GEA_PORT/, or https:// once HTTPS is on - gea tls)"
echo "   Ctrl+C stops it, or run ./stop-dashboard.sh"
fails=0
# the service opens the browser itself once its port is listening; a restart (the page is already open) and
# a start with --no-browser (a server with no desktop) do not open another one
browser=""
[ "${1:-}" = "--no-browser" ] && browser="--no-browser"
while :; do
  ./python/bin/python3 -m gea serve --workspace "$GEA_WORKSPACE" --port "$GEA_PORT" $browser
  rc=$?
  browser="--no-browser"
  if [ "$rc" = "86" ]; then
    fails=0
    echo
    echo "== restart authorised from the control panel - starting it again"
    echo
    continue
  fi
  # a code that is neither 0 nor 86 is a death. The operator's answer to "may it start itself again" is
  # one character in one file; five deaths in a row and this stops asking and hands over the error.
  if [ "$rc" != "0" ] && [ "$(cat "$GEA_WORKSPACE/records/auto_restart.flag" 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
    fails=$((fails + 1))
    if [ "$fails" -le 5 ]; then
      echo
      echo "== the control panel stopped unexpectedly (exit $rc) - auto-restart is on, attempt $fails of 5"
      echo
      sleep 5
      continue
    fi
    echo
    echo "== the control panel has stopped unexpectedly 5 times in a row. Not starting it again by itself."
  fi
  echo
  echo "== the GEA Operations Control Panel has stopped (exit $rc)"
  echo "   start it again:  ./start-dashboard.sh"
  echo "   run log:         $GEA_WORKSPACE/records/runlog.jsonl"
  exit "$rc"
done
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
   Your browser opens http://127.0.0.1:{port}/ (https:// once HTTPS is on:
   see HTTPS on the Administration page); the first visit asks you to
   create the administrator account (name and a password of 8+ characters).
3. Wells -> add a well from a file (historian CSV, LAS) or the catalogue.
   Patch panel -> add a patch to read the drill floor (WITS0), a WITSML
   store, OPC UA, MQTT or Modbus. Home -> Refresh every report.
4. {verify}  runs every check of the acceptance gate from this installation
   and is your own acceptance evidence (also on the Verification page).
5. report-samples\\  holds one rendered example of every report the program
   writes, from the build in this kit; SAMPLES.md names the command behind each.

{desktop}{service}

Stopping: close the window, or {stop}.
Updating: install a newer kit beside this one and start it; the workspace is
found automatically. Uninstalling: {uninstall} (the workspace stays).
Workspace location: {workspace}  (set GEA_WORKSPACE before starting to use
another folder; set GEA_PORT to change the port).

The dashboard listens on this computer only. To serve a control room, turn
HTTPS on first (Administration -> HTTPS, or: gea help https) and start it
with --host 0.0.0.0; people can sign in with their company account
(gea help company-sign-in). IT_SECURITY.md in this folder answers what an IT
department asks before installing (also: gea security).

Contents are listed in MANIFEST.json; SHA256SUMS.txt holds the hash of every
file so the kit can be verified after copying to the site.
{check}
Licence: Mozilla Public License 2.0. (c) 2026 Daniel T. Murphy / ENRGYONE.
'''


WIN_DESKTOP = '''A window of its own: open-dashboard.cmd starts the control panel when it is
not running and opens it in Microsoft Edge's app window (no tabs, no address
bar, its own taskbar button; Chrome's when Edge is missing, else a browser
tab), with the program's icon in the notification area: running or stopped,
and a menu to open, start or stop it. GEA-Program-Setup-{version}.exe (beside
this kit on the release) installs this same kit for you with Start-menu and
desktop shortcuts that do this, an Add/Remove Programs entry, a start at
sign-in if you want one, and upgrades in place - no administrator rights.

'''


WIN_CHECK = '''
Checking this kit before installing it (what an IT department may ask):
  the build that made it - with the GitHub CLI, where the zip was saved:
    gh attestation verify gea-program-{version}-win64.zip -R Daniel8Murphy0007/GEA-Program
  every file against the catalog beside this folder, in PowerShell, in the
  folder the zip was extracted to, before install.cmd runs:
    Get-AuthenticodeSignature .\\gea-program-{version}-win64.cat
    Test-FileCatalog -CatalogFilePath .\\gea-program-{version}-win64.cat -Path .\\gea-program-{version}-win64
  (Valid: no file has changed. The signature names the publisher once the
  publisher's code-signing certificate is in place; until then it reads
  NotSigned. install.cmd then adds the installed packages to the folder,
  so the check is made before it.)
'''
LINUX_CHECK = '''
Checking this kit before installing it: with the GitHub CLI, where the zip
was saved:
    gh attestation verify gea-program-{version}-{tag}.zip -R Daniel8Murphy0007/GEA-Program
'''


def icon_module():
    """gea/icon.py loaded on its own (it imports nothing from the program, which this build does not install)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('gea_icon', ROOT / 'gea' / 'icon.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rezip(kit: Path, beside: list) -> Path:
    """The kit's zip made again from its folder - after the workflow has made (and, with a certificate, signed) its
    file catalog - with the given files beside the folder at the top of the zip. Every file of the folder, under the
    folder's name, as the build wrote it."""
    kit = Path(kit)
    if not (kit / 'MANIFEST.json').is_file():
        raise SystemExit(f'{kit} is not a kit folder (no MANIFEST.json)')
    zpath = kit.parent / f'{kit.name}.zip'
    files = sorted(p for p in kit.rglob('*') if p.is_file())
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, f'{kit.name}/{p.relative_to(kit).as_posix()}')
        for b in beside:
            z.write(b, Path(b).name)
    return zpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='build the standalone install kit')
    ap.add_argument('--platform', choices=['windows', 'linux'], default='windows')
    ap.add_argument('--python', default=PY_DEFAULT, help='CPython version of the embeddable distribution (windows)')
    ap.add_argument('--python-zip', default=None, help='a downloaded python-<ver>-embed-amd64.zip, when python.org is not reachable from here')
    ap.add_argument('--extras', default=EXTRAS_DEFAULT, help='comma-separated extras to include (desktop adds PyQt6, ~100 MB)')
    ap.add_argument('--out', default=str(ROOT / 'dist'))
    ap.add_argument('--no-zip', action='store_true')
    ap.add_argument('--rezip', default=None, help='zip an existing kit folder again (after its catalog is made and signed) instead of building')
    ap.add_argument('--beside', action='append', default=[], help='--rezip: a file to put beside the kit folder at the top of the zip (the catalog)')
    a = ap.parse_args(argv)
    if a.rezip:
        z = rezip(Path(a.rezip), [Path(b) for b in a.beside])
        print(f'== {z} ({z.stat().st_size / 1e6:.1f} MB)')
        return 0

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
        (kit / 'gea.ico').write_bytes(icon_module().ico_bytes())
        check = WIN_CHECK.format(version=ver)
        readme = README.format(desktop=WIN_DESKTOP.format(version=ver), version=ver, target='Windows x64', pyline=f'its own Python {a.python}', install='Double-click install.cmd (about a minute).', check=check,
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
        readme = README.format(desktop='', version=ver, target='Linux', pyline='a virtual environment built from the machine\'s python3 (3.10 or newer)', install='./install.sh', check=LINUX_CHECK.format(version=ver, tag=tag),
                               start='./start-dashboard.sh', verify='./verify.sh', port='8765',
                               service='To run as a service: copy the kit to /opt/gea-program, edit gea-program.service if the paths differ, then\n  cp gea-program.service ~/.config/systemd/user/ && systemctl --user enable --now gea-program',
                               stop='Ctrl+C', uninstall='delete the kit folder', workspace='~/.local/share/gea-program/site')
    (kit / 'README.txt').write_text(readme, encoding='utf-8')
    sec = ROOT / 'docs' / 'IT_SECURITY.md'
    if sec.is_file():
        shutil.copy2(sec, kit / 'IT_SECURITY.md')
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
