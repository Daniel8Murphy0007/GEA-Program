# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""launcher - the program as a desktop program: start the control panel when it is not running, open it in a
window of its own, and put its icon in the notification area.

    gea launch                         start the control panel if it is not running, then open its window
    gea launch --background            start it (and its icon) without opening a window - what the logon task runs
    gea launch --selftest              say what it would do, start nothing

The window is Microsoft Edge's app window (`msedge --app=URL`: the page with no tabs, no address bar, its own
taskbar button), else Google Chrome's, else the default browser. Edge is part of Windows 10 and 11, so the app
window is what nearly every machine gets; a site that removed it gets a browser tab, which works the same.

The control panel itself is the kit's start-dashboard.cmd in a window of its own, minimised: the window that
starts it again when the panel asks for a restart and that says why it stopped when it stops. Closing that
window stops the panel; the icon in the notification area (gea tray) says so and can start it again.

Where the site's data is: --workspace, else GEA_WORKSPACE, else the folder the installer recorded in the kit's
site-workspace.txt, else %LOCALAPPDATA%\\GEA-Program\\site. The launcher reads the workspace's folder name and
its run log; it writes nothing into it (start-dashboard.cmd creates a workspace that does not exist yet).

Standard library only; it runs under the kit's pythonw.exe, which has no console, so it never prints there:
what it has to say goes in a message box.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

DEFAULT_PORT = 8765
WORKSPACE_FILE = 'site-workspace.txt'
APP_BROWSERS = (('Microsoft Edge', 'msedge.exe'), ('Google Chrome', 'chrome.exe'))
UNIX_APP_BROWSERS = (('Microsoft Edge', 'microsoft-edge'), ('Google Chrome', 'google-chrome'), ('Chromium', 'chromium'), ('Chromium', 'chromium-browser'))
WINDOW_SIZE = (1440, 960)

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
SW_SHOWMINNOACTIVE = 7
STARTF_USESHOWWINDOW = 0x00000001


class LaunchError(Exception):
    pass


def is_windows() -> bool:
    return os.name == 'nt'


def kit_dir(executable: Optional[str] = None) -> Optional[Path]:
    """The install kit this Python belongs to (the folder holding python\\ and start-dashboard.cmd), or None for a
    pip-installed program."""
    exe = Path(executable or sys.executable).resolve()
    d = exe.parent.parent
    return d if (d / 'start-dashboard.cmd').is_file() and exe.parent.name.lower() == 'python' else None


def default_workspace(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    if is_windows() or env.get('LOCALAPPDATA'):
        base = env.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        return os.path.join(base, 'GEA-Program', 'site')
    return os.path.join(os.path.expanduser('~'), '.local', 'share', 'gea-program', 'site')


def read_workspace_file(kit: Optional[Path]) -> Optional[str]:
    """The workspace the installer recorded: the first line of site-workspace.txt in the kit, or None."""
    if kit is None:
        return None
    try:
        with open(Path(kit) / WORKSPACE_FILE, encoding='utf-8-sig') as f:
            line = f.readline().strip().strip('"')
    except OSError:
        return None
    return line or None


def resolve_workspace(arg: Optional[str] = None, env: Optional[dict] = None, kit: Optional[Path] = None) -> tuple:
    """(path, how): --workspace, GEA_WORKSPACE, the installer's choice, the default - in that order."""
    env = os.environ if env is None else env
    if arg:
        return os.path.abspath(arg), '--workspace'
    if env.get('GEA_WORKSPACE'):
        return env['GEA_WORKSPACE'], 'GEA_WORKSPACE'
    f = read_workspace_file(kit)
    if f:
        return f, f'the installer\'s choice ({WORKSPACE_FILE})'
    return default_workspace(env), 'the default'


def check_workspace_choice(path: Optional[str], kit: Optional[Path]) -> Optional[str]:
    """Why a folder cannot be the site workspace of this installation, or None. The program's own folder is
    removed by its uninstaller and replaced by every upgrade, so the site's data is never kept in it, nor it in
    the site's data."""
    if not path or not str(path).strip():
        return 'no folder given'
    path = str(path).strip().strip('"')
    if not os.path.isabs(path):
        return f'{path} is not a full path (a drive letter or a \\\\server\\share first)'
    p = os.path.normcase(os.path.normpath(path))
    if os.path.dirname(p) == p:
        return f'{path} is the root of a drive; choose a folder on it (for example C:\\site)'
    if kit is not None:
        k = os.path.normcase(os.path.normpath(str(kit)))
        if p == k or p.startswith(k.rstrip(os.sep) + os.sep):
            return f'{path} is inside the program\'s own folder ({kit}), which an upgrade replaces and the uninstaller removes'
        if k.startswith(p.rstrip(os.sep) + os.sep):
            return f'the program\'s own folder ({kit}) is inside {path}; keep the site\'s data and the program apart'
    return None


def set_workspace(kit: Optional[Path], path: str) -> str:
    """Record the site workspace for this installation (site-workspace.txt in the kit, UTF-8): what the launcher,
    the icon and start-dashboard.cmd read. Nothing is written into the workspace."""
    if kit is None:
        raise LaunchError('there is no install kit here to record a workspace in (a pip-installed program takes --workspace or GEA_WORKSPACE)')
    why = check_workspace_choice(path, kit)
    if why:
        raise LaunchError(why)
    path = os.path.normpath(str(path).strip().strip('"'))
    with open(Path(kit) / WORKSPACE_FILE, 'w', encoding='utf-8', newline='') as f:
        f.write(path)
    return path


SITE_NAME_MAX = 80


def check_site_name(name: Optional[str]) -> Optional[str]:
    """Why a site name cannot be used, or None. Empty is allowed: the workspace is then named after its folder."""
    name = (name or '').strip()
    if len(name) > SITE_NAME_MAX:
        return f'a site name of {len(name)} characters; {SITE_NAME_MAX} at most (it heads every report)'
    if any(c in name for c in '"\r\n\t'):
        return 'a site name holds no quotation marks, line breaks or tabs'
    return None


def create_workspace(path: str, site_name: Optional[str], actor: str = 'installer') -> Optional[str]:
    """Make the workspace with its name when there is none at `path` yet; an existing workspace is left exactly as it
    is (its name is changed on the Administration page). Returns the name it was made with, or None when it existed."""
    if os.path.isfile(os.path.join(path, 'workspace.json')):
        return None
    why = check_site_name(site_name)
    if why:
        raise LaunchError(why)
    from .workspace import Workspace
    name = (site_name or '').strip() or os.path.basename(os.path.normpath(path))
    Workspace.create(path, name, actor=actor)
    return name


def port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def last_start(ws: str, max_bytes: int = 65536) -> Optional[dict]:
    """The run log's last 'start' line (the service writes its address there as it starts), or None."""
    p = os.path.join(ws, 'records', 'runlog.jsonl')
    try:
        with open(p, 'rb') as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(0, n - max_bytes))
            tail = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('event') == 'start':
            return row
    return None


def dashboard_url(ws: str, port: int, since_utc: Optional[str] = None) -> str:
    """The control panel's address: the one the running service wrote in the run log when it is on this port
    (https:// with the certificate's name once HTTPS is on), else http://127.0.0.1:<port>/."""
    row = last_start(ws)
    url = (row or {}).get('url') or ''
    if url and f':{int(port)}/' in url and (since_utc is None or str(row.get('utc') or '') >= since_utc):
        if '://0.0.0.0:' in url or '://[::]:' in url:
            url = url.replace('0.0.0.0', '127.0.0.1').replace('[::]', '127.0.0.1')
        return url
    return f'http://127.0.0.1:{int(port)}/'


def _app_path_from_registry(exe: str) -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    sub = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + exe
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, 'KEY_WOW64_32KEY', 0), getattr(winreg, 'KEY_WOW64_64KEY', 0)):
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as k:
                    v, _ = winreg.QueryValueEx(k, '')
                    v = os.path.expandvars(str(v).strip('"'))
                    if v:
                        return v
            except OSError:
                continue
    return None


def _known_paths(exe: str, env: dict) -> list:
    rel = {'msedge.exe': (r'Microsoft\Edge\Application',), 'chrome.exe': (r'Google\Chrome\Application',)}.get(exe, ())
    roots = [env.get('ProgramFiles(x86)'), env.get('ProgramFiles'), env.get('LOCALAPPDATA')]
    return [os.path.join(r, d, exe) for r in roots if r for d in rel]


def find_app_browser(registry: Optional[Callable[[str], Optional[str]]] = None, exists: Callable[[str], bool] = os.path.isfile,
                     env: Optional[dict] = None, which: Callable[[str], Optional[str]] = shutil.which,
                     windows: Optional[bool] = None) -> Optional[tuple]:
    """(name, path) of a browser that opens an app window (--app=URL): Edge, then Chrome; None when neither is here."""
    env = os.environ if env is None else env
    windows = is_windows() if windows is None else windows
    if windows:
        registry = registry or _app_path_from_registry
        for name, exe in APP_BROWSERS:
            for cand in [registry(exe)] + _known_paths(exe, env):
                if cand and exists(cand):
                    return name, cand
        return None
    for name, exe in UNIX_APP_BROWSERS:
        p = which(exe)
        if p:
            return name, p
    return None


def window_profile_dir(env: Optional[dict] = None) -> str:
    """The app window's own browser profile: %LOCALAPPDATA%\\GEA-Program\\window. The control panel's window does not
    share the person's everyday Edge profile - its cookie settings, its extensions (a cookie or privacy blocker
    there kept the sign-in from holding: the page signed in and came straight back to Sign in, v0.66.2), its
    tabs and its sign-ins. It keeps its own session cookie and nothing else."""
    env = os.environ if env is None else env
    base = env.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local') if (is_windows() or env.get('LOCALAPPDATA')) \
        else os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'GEA-Program', 'window')


def app_window_args(exe: str, url: str, size: tuple = WINDOW_SIZE, profile: Optional[str] = None) -> list:
    profile = profile or window_profile_dir()
    return [exe, f'--app={url}', f'--window-size={int(size[0])},{int(size[1])}', f'--user-data-dir={profile}',
            '--no-first-run', '--no-default-browser-check', '--disable-extensions']


def open_window(url: str, browser: Optional[tuple] = None, popen: Callable = subprocess.Popen, web_open: Optional[Callable] = None,
                find: Callable = find_app_browser) -> str:
    """Open the control panel in an app window when a browser here makes one, else in the default browser.
    Says which."""
    b = browser if browser is not None else find()
    if b:
        try:
            popen(app_window_args(b[1], url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            return f'app window ({b[0]})'
        except OSError:
            pass
    if web_open is None:
        import webbrowser
        web_open = webbrowser.open
    if web_open(url, new=1):
        return 'the default browser'
    raise LaunchError(f'no browser could be opened here; open {url} yourself')


def console_python(executable: Optional[str] = None) -> str:
    exe = Path(executable or sys.executable)
    if exe.name.lower() == 'pythonw.exe' and (exe.parent / 'python.exe').is_file():
        return str(exe.parent / 'python.exe')
    return str(exe)


def gui_python(executable: Optional[str] = None) -> str:
    exe = Path(executable or sys.executable)
    if exe.name.lower() == 'python.exe' and (exe.parent / 'pythonw.exe').is_file():
        return str(exe.parent / 'pythonw.exe')
    return str(exe)


def service_command(kit: Optional[Path], ws: str, port: int, executable: Optional[str] = None) -> list:
    """What starts the control panel: the kit's start-dashboard.cmd (its restart loop, its window), or for a
    pip-installed program `python -m gea serve`."""
    if kit is not None:
        return [os.environ.get('COMSPEC') or 'cmd.exe', '/c', str(Path(kit) / 'start-dashboard.cmd'), '--no-browser']
    return [console_python(executable), '-m', 'gea', 'serve', '--workspace', ws, '--port', str(int(port)), '--no-browser']


def service_popen_kwargs(windows: Optional[bool] = None) -> dict:
    """How the control panel's window is started. On Windows: a console of its own, minimised, and NO standard
    handles passed. Passing any of stdin, stdout or stderr makes Python hand the child the parent's other handles
    too - and the launcher runs under pythonw.exe, which has none, so Python gives the child a pipe nobody reads:
    the panel's first line of output then fails and it stops before it opens its port (v0.65.0 and v0.66.0: "did
    not start within 120 seconds" from the Start-menu shortcut, while the build's test, run from a console,
    passed). With no handles given, the new console's own are used."""
    windows = is_windows() if windows is None else windows
    if not windows:
        return {'start_new_session': True, 'stdin': subprocess.DEVNULL, 'close_fds': True}
    si_cls = getattr(subprocess, 'STARTUPINFO', None)           # Windows only; a stand-in where the check runs elsewhere
    si = si_cls() if si_cls else __import__('types').SimpleNamespace(dwFlags=0, wShowWindow=0)
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE
    return {'creationflags': CREATE_NEW_CONSOLE, 'startupinfo': si, 'close_fds': True}


def start_service(kit: Optional[Path], ws: str, port: int, popen: Callable = subprocess.Popen) -> list:
    cmd = service_command(kit, ws, port)
    env = dict(os.environ, GEA_WORKSPACE=ws, GEA_PORT=str(int(port)))
    popen(cmd, cwd=str(kit) if kit else None, env=env, **service_popen_kwargs())
    return cmd


def tray_command(ws: str, port: int, executable: Optional[str] = None) -> list:
    return [gui_python(executable), '-m', 'gea', 'tray', '--workspace', ws, '--port', str(int(port))]


def start_tray(ws: str, port: int, popen: Callable = subprocess.Popen) -> Optional[list]:
    """The notification-area icon, in a process of its own (it keeps itself to one per port)."""
    if not is_windows():
        return None
    cmd = tray_command(ws, port)
    popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
          creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    return cmd


def stop_command(kit: Optional[Path], with_tray: bool = False) -> Optional[list]:
    if kit is None or not is_windows():
        return None
    return [os.environ.get('COMSPEC') or 'cmd.exe', '/c', str(Path(kit) / 'stop-dashboard.cmd')] + (['--all'] if with_tray else [])


def stop_service(kit: Optional[Path], with_tray: bool = False, run: Callable = subprocess.run) -> bool:
    """stop-dashboard.cmd, without a window. False when there is no kit to stop it with (a pip-installed
    program's control panel stops with its own window, or Ctrl+C)."""
    cmd = stop_command(kit, with_tray)
    if cmd is None:
        return False
    run(cmd, cwd=str(kit), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, timeout=60)
    return True


def say(title: str, text: str, error: bool = False) -> None:
    """A message: on the console when there is one, else a message box (pythonw has no console)."""
    if sys.stdout is not None and getattr(sys.stdout, 'write', None):
        try:
            print(text, flush=True)
            return
        except Exception:
            pass
    if is_windows():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
        except Exception:
            pass


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def plan(workspace: Optional[str] = None, port: int = DEFAULT_PORT) -> dict:
    """What a launch would do, here and now - nothing is started."""
    kit = kit_dir()
    ws, how = resolve_workspace(workspace, kit=kit)
    running = port_open(port)
    b = find_app_browser()
    return {'kit': str(kit) if kit else None, 'workspace': ws, 'workspace_from': how, 'workspace_exists': os.path.isfile(os.path.join(ws, 'workspace.json')),
            'port': int(port), 'running': running, 'url': dashboard_url(ws, port) if running else f'http://127.0.0.1:{int(port)}/',
            'window': (f'app window ({b[0]}: {b[1]})' if b else 'the default browser (no Edge or Chrome found for an app window)'),
            'start': None if running else service_command(kit, ws, port), 'tray': tray_command(ws, port) if is_windows() else None}


def plan_text(p: dict) -> str:
    lines = [f"kit:        {p['kit'] or '(none - a pip-installed program: the control panel starts with python -m gea serve)'}",
             f"workspace:  {p['workspace']}  (from {p['workspace_from']}; {'exists' if p['workspace_exists'] else 'created at the first start'})",
             f"port:       {p['port']} - {'the control panel is running: ' + p['url'] if p['running'] else 'nothing is listening: it would be started'}",
             f"window:     {p['window']}"]
    if p['start']:
        lines.append('start:      ' + subprocess.list2cmdline(p['start']))
    if p['tray']:
        lines.append('icon:       ' + subprocess.list2cmdline(p['tray']))
    return '\n'.join(lines)


def launch(workspace: Optional[str] = None, port: int = DEFAULT_PORT, background: bool = False, tray: bool = True,
           wait_s: float = 120.0) -> int:
    kit = kit_dir()
    ws, _how = resolve_workspace(workspace, kit=kit)
    t0 = _utc_now()
    started = False
    if not port_open(port):
        try:
            start_service(kit, ws, port)
        except OSError as e:
            say('GEA-Program', f'The control panel could not be started: {e}', error=True)
            return 1
        started = True
    if tray:
        try:
            start_tray(ws, port)
        except OSError:
            pass                                   # the icon is a convenience; the panel runs without it
    if background:
        return 0
    deadline = time.monotonic() + wait_s
    while not port_open(port):
        if time.monotonic() > deadline:
            say('GEA-Program', f'The control panel did not start within {int(wait_s)} seconds. Its window '
                               f'("GEA - Operations Control Panel", minimised on the taskbar) says why; the run log is '
                               f'{os.path.join(ws, "records", "runlog.jsonl")}.', error=True)
            return 1
        time.sleep(0.5)
    url = dashboard_url(ws, port)
    if started:                                    # the service writes its address a moment after its port opens
        for _ in range(20):
            if last_start(ws) and str(last_start(ws).get('utc') or '') >= t0:
                break
            time.sleep(0.25)
        url = dashboard_url(ws, port)
    try:
        open_window(url)
    except LaunchError as e:
        say('GEA-Program', str(e), error=True)
        return 1
    return 0


def main(a) -> int:
    if getattr(a, 'set_workspace', None):
        try:
            path = set_workspace(kit_dir(), a.set_workspace)
        except LaunchError as e:
            say('GEA-Program', f'The site workspace was not recorded: {e}', error=True)
            return 2
        made = None
        if getattr(a, 'site_name', None) is not None:
            try:
                made = create_workspace(path, a.site_name)
            except (LaunchError, OSError) as e:
                say('GEA-Program', f'The site workspace was recorded but not made: {e}', error=True)
                return 2
        say('GEA-Program', f'site workspace: {path}  (recorded in {Path(kit_dir()) / WORKSPACE_FILE})'
                           + (f'; made, named {made}' if made else ('; it exists and is used as it is' if getattr(a, 'site_name', None) is not None else '')))
        return 0
    if getattr(a, 'selftest', False):
        p = plan(a.workspace, a.port)
        print(plan_text(p))
        if getattr(a, 'json', False):
            print(json.dumps(p, indent=1))
        return 0
    return launch(a.workspace, a.port, background=a.background, tray=not a.no_tray)
