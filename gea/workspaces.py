# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""workspaces - more than one workspace on one computer, opened from the control panel.

A site's workspace holds its wells, reports, accounts and keys, and a pad's report and dossier cover every well in it.
A well that is not the site's - a demonstration well, a well being looked at for a client - belongs in a workspace of
its own. This module keeps the list of workspaces this computer has used (%LOCALAPPDATA%\\GEA-Program\\workspaces.json:
folder, name, the port its control panel last used, when it was last opened), makes a new one, and opens one: its
control panel started on a port of its own - the one it used before, else the first free one from 8766 - and opened
in a window of its own, exactly as the Start-menu shortcut opens the site's (`gea launch`), so nothing is typed.

A new workspace may take the current one's accounts (the users file, with its password hashes), so the same people
sign in to both; or none, and the first person to open it makes the administrator account. Keys, records and data
are never copied.

Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

FIRST_PORT, LAST_PORT = 8766, 8799


def registry_path(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    if env.get('GEA_WORKSPACES_FILE'):
        return env['GEA_WORKSPACES_FILE']
    base = env.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'GEA-Program', 'workspaces.json')


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(str(p).strip().strip('"')))


def _load() -> List[dict]:
    p = registry_path()
    try:
        with open(p, encoding='utf-8') as f:
            rows = json.load(f).get('workspaces', [])
        return [r for r in rows if isinstance(r, dict) and r.get('path')]
    except (OSError, ValueError):
        return []


def _store(rows: List[dict]) -> None:
    p = registry_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'workspaces': rows}, f, indent=1)
    os.replace(p + '.tmp', p)


def remember(path: str, name: str, port: Optional[int] = None) -> dict:
    rows = _load()
    key = _norm(path)
    row = next((r for r in rows if _norm(r['path']) == key), None)
    if row is None:
        row = {'path': os.path.abspath(path), 'added_utc': _utc()}
        rows.append(row)
    row['name'] = name or row.get('name') or os.path.basename(os.path.normpath(path))
    if port:
        row['port'] = int(port)
    row['last_opened_utc'] = _utc()
    _store(rows)
    return row


def _name_of(path: str) -> Optional[str]:
    try:
        with open(os.path.join(path, 'workspace.json'), encoding='utf-8') as f:
            return json.load(f).get('name')
    except (OSError, ValueError):
        return None


def listing(current_path: str, current_port: Optional[int] = None) -> List[dict]:
    """Every workspace this computer has used, the current one first."""
    from . import launcher as L
    rows = _load()
    if not any(_norm(r['path']) == _norm(current_path) for r in rows):
        rows.insert(0, {'path': os.path.abspath(current_path), 'name': _name_of(current_path), 'port': current_port})
    out = []
    for r in rows:
        cur = _norm(r['path']) == _norm(current_path)
        exists = os.path.isfile(os.path.join(r['path'], 'workspace.json'))
        port = current_port if cur else r.get('port')
        out.append({'path': r['path'], 'name': (_name_of(r['path']) or r.get('name') or os.path.basename(os.path.normpath(r['path']))) if exists else r.get('name'),
                    'exists': exists, 'current': cur, 'port': port, 'running': bool(port) and (cur or L.port_open(port)),
                    'last_opened_utc': r.get('last_opened_utc')})
    out.sort(key=lambda x: (not x['current'], (x['name'] or '').lower()))
    return out


def create(path: str, name: str, actor: str, copy_users_from: Optional[str] = None, current_path: Optional[str] = None) -> dict:
    """A new workspace in a folder of its own: never inside the program, never inside another workspace or around
    it, never over an existing workspace."""
    from . import launcher as L
    from .workspace import Workspace, WorkspaceError
    path = str(path or '').strip().strip('"')
    name = str(name or '').strip()
    why = L.check_workspace_choice(path, L.kit_dir())
    if why:
        raise WorkspaceError(why)
    msg = L.check_site_name(name) if name else None
    if msg:
        raise WorkspaceError(msg)
    if current_path:
        a, b = _norm(path), _norm(current_path)
        if a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep):
            raise WorkspaceError(f'{path} and the workspace open here ({current_path}) would hold one another; choose a folder beside it')
    if os.path.isfile(os.path.join(path, 'workspace.json')):
        raise WorkspaceError(f'{path} is already a workspace ({_name_of(path) or "unnamed"}): open it instead')
    if copy_users_from and _norm(copy_users_from) == _norm(path):
        copy_users_from = None
    ws = Workspace.create(path, name or os.path.basename(os.path.normpath(path)), actor=actor)
    copied = False
    if copy_users_from:
        src = os.path.join(copy_users_from, 'users.json')
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(ws.path, 'users.json'))
            copied = True
    ws.audit(actor, 'workspace.create', {'path': ws.path, 'name': ws.manifest.get('name'), 'accounts_from': copy_users_from if copied else None})
    remember(ws.path, ws.manifest.get('name') or name)
    return {'path': ws.path, 'name': ws.manifest.get('name'), 'accounts_copied': copied}


def free_port(taken: Callable[[int], bool], skip: Optional[set] = None) -> int:
    skip = skip or set()
    for p in range(FIRST_PORT, LAST_PORT + 1):
        if p not in skip and not taken(p):
            return p
    raise OSError(f'no free port between {FIRST_PORT} and {LAST_PORT}')


def launch_command(path: str, port: int, executable: Optional[str] = None) -> List[str]:
    from . import launcher as L
    return [L.gui_python(executable), '-m', 'gea', 'launch', '--workspace', path, '--port', str(int(port)), '--no-tray']


def open_workspace(path: str, current_path: str, current_port: int, actor: str = 'system', popen: Optional[Callable] = None,
                   taken: Optional[Callable[[int], bool]] = None) -> dict:
    """Open another workspace's control panel in a window of its own: on the port it used before when that port is
    free or still its own, else the first free port from 8766. The launch starts it when it is not running."""
    from . import launcher as L
    from .workspace import WorkspaceError
    popen = popen or subprocess.Popen
    path = str(path or '').strip().strip('"')
    if not os.path.isfile(os.path.join(path, 'workspace.json')):
        raise WorkspaceError(f'{path} is not a workspace')
    if _norm(path) == _norm(current_path):
        raise WorkspaceError('this is the workspace already open here')
    taken = taken or L.port_open
    row = next((r for r in _load() if _norm(r['path']) == _norm(path)), {})
    port = row.get('port')
    mine = False
    if port and taken(port):
        st = L.last_start(path) or {}
        mine = f':{int(port)}/' in str(st.get('url') or '')       # still this workspace's own panel on that port
    if not port or int(port) == int(current_port) or (taken(port) and not mine):
        port = free_port(taken, skip={int(current_port)})
    cmd = launch_command(path, port)
    kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'close_fds': True,
          'cwd': L.neutral_cwd()}
    if L.is_windows():
        kw['creationflags'] = L.DETACHED_PROCESS | L.CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    popen(cmd, **kw)
    remember(path, _name_of(path) or '', port)
    return {'path': os.path.abspath(path), 'name': _name_of(path), 'port': int(port), 'url': f'http://127.0.0.1:{int(port)}/',
            'already_running': bool(mine), 'command': cmd}
