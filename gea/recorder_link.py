# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""recorder_link - the box's program on this computer: a simulated box to look at, and the monitor of any box.

GEA Recorder (recorder/; the kit installs its wheel beside GEA's) is the program on the Pi. This module lets the
control panel start it here, on this computer, with its simulator in place of the ADC - a box with no hardware,
recording its test signal into field\\sim-box\\ in the workspace and serving its monitor on port 8781 - so the
monitor, the pull and the whole chain can be seen and tried by clicking before a Pi exists; and stop it again
(gracefully: the files close and the manifest is written). It also says where any real box's monitor is: its
address plus /live. GEA imports nothing of the recorder; it finds the program by name at run time and runs it as a
process of its own, as the launcher runs the tray."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

SIM_PORT = 8781
SIM_DIR = 'sim-box'


def program() -> Optional[dict]:
    """Where the recorder's program is: installed beside GEA (the kit, or pip), or the checkout's recorder/ folder."""
    spec = importlib.util.find_spec('gea_recorder')
    if spec is not None and spec.origin:
        here = Path(spec.origin).resolve().parent
        if here.parent.name == 'recorder' and (here.parent / 'pyproject.toml').is_file():      # loaded from a checkout's folder, not installed
            return {'how': 'checkout', 'path': str(here), 'env': str(here.parent)}
        return {'how': 'installed', 'path': str(here), 'env': None}
    src = Path(__file__).resolve().parent.parent / 'recorder'
    if (src / 'gea_recorder' / '__init__.py').is_file():
        return {'how': 'checkout', 'path': str(src / 'gea_recorder'), 'env': str(src)}
    return None


def sim_dir(ws) -> str:
    return os.path.join(ws.path, 'field', SIM_DIR)


def sim_config(ws, port: int = SIM_PORT) -> str:
    """The simulated box's config: the site's name, the box called SIM, the test signal, files every minute so
    there is something to pull within a minute, the monitor on 127.0.0.1 only."""
    d = sim_dir(ws)
    os.makedirs(os.path.join(d, 'data'), exist_ok=True)
    name = str(ws.manifest.get('name') or 'site')
    cfg = {
        'site': name, 'network': 'GE', 'station': 'SIM',
        'wellhead': {'lat': 31.0, 'lon': -102.0, 'note': 'a stand-in position for the simulated box'},
        'clock': {'source': 'system'},
        'adc': {'driver': 'sim', 'sample_rate': 200, 'block_s': 0.5,
                'channels': [{'index': 0, 'id': 'REF', 'code': 'HHZ', 'kind': 'geophone', 'x_ft': 40, 'y_ft': 0, 'rate': 200},
                             {'index': 1, 'id': 'S01', 'code': 'HHZ', 'kind': 'geophone', 'x_ft': 1800, 'y_ft': 900, 'rate': 200},
                             {'index': 2, 'id': 'S02', 'code': 'HHZ', 'kind': 'geophone', 'x_ft': -1200, 'y_ft': 1500, 'rate': 200},
                             {'index': 3, 'id': 'S03', 'code': 'HHZ', 'kind': 'geophone', 'x_ft': 900, 'y_ft': -2000, 'rate': 200},
                             {'index': 4, 'id': 'E01', 'code': 'LQE', 'kind': 'electrode', 'x_ft': 0, 'y_ft': 0, 'dipole_ft': 100, 'azimuth_deg': 90, 'rate': 10},
                             {'index': 5, 'id': 'E02', 'code': 'LQN', 'kind': 'electrode', 'x_ft': 0, 'y_ft': 0, 'dipole_ft': 100, 'azimuth_deg': 0, 'rate': 10}]},
        'rig': {'enabled': False},
        'output': {'dir': os.path.join(d, 'data'), 'mirror': '', 'min_free_mb': 200, 'flush_s': 5, 'file_minutes': 1},
        'status': {'port': int(port), 'bind': '127.0.0.1'},
    }
    p = os.path.join(d, 'config.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)
    return p


def _run_path(ws) -> str:
    return os.path.join(sim_dir(ws), 'run.json')


def health(port: int = SIM_PORT, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/health', timeout=timeout) as r:
            h = json.load(r)
        return h if h.get('program') == 'GEA Recorder' else None
    except Exception:
        return None


def status(ws, port: int = SIM_PORT) -> dict:
    h = health(port)
    run = None
    try:
        with open(_run_path(ws), encoding='utf-8') as f:
            run = json.load(f)
    except (OSError, ValueError):
        pass
    prog = program()
    return {'program': prog['how'] if prog else None, 'running': h is not None, 'url': f'http://127.0.0.1:{int(port)}', 'monitor': f'http://127.0.0.1:{int(port)}/live',
            'pid': run.get('pid') if run else None, 'started_utc': run.get('started_utc') if run else None, 'health': h, 'folder': os.path.join(sim_dir(ws), 'data')}


def start_sim(ws, actor: str = 'system', port: int = SIM_PORT, popen=None, wait_s: float = 8.0) -> dict:
    """Start the simulated box as a process of its own (detached, as the tray is), and wait for its page."""
    from . import launcher as L
    from .workspace import WorkspaceError
    if health(port) is not None:
        st = status(ws, port); st['already_running'] = True
        return st
    prog = program()
    if prog is None:
        raise WorkspaceError("the recorder's program (gea-recorder) is not installed beside GEA: the kit and the setup carry it; a pip install takes `pip install gea-recorder`")
    cfg = sim_config(ws, port)
    cmd = [sys.executable, '-m', 'gea_recorder', 'run', '--config', cfg]
    env = dict(os.environ)
    if prog['env']:
        env['PYTHONPATH'] = prog['env'] + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    log = open(os.path.join(sim_dir(ws), 'console.log'), 'ab')
    kw = {'stdin': subprocess.DEVNULL, 'stdout': log, 'stderr': subprocess.STDOUT, 'close_fds': True, 'cwd': L.neutral_cwd(), 'env': env}
    if L.is_windows():
        kw['creationflags'] = L.CREATE_NEW_PROCESS_GROUP | L.DETACHED_PROCESS
    else:
        kw['start_new_session'] = True
    p = (popen or subprocess.Popen)(cmd, **kw)
    pid = int(getattr(p, 'pid', 0) or 0)
    with open(_run_path(ws), 'w', encoding='utf-8') as f:
        json.dump({'pid': pid, 'port': int(port), 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'command': cmd, 'by': actor}, f, indent=1)
    t_end = time.monotonic() + wait_s
    h = None
    while time.monotonic() < t_end and h is None:
        time.sleep(0.25)
        h = health(port, timeout=1.0)
    ws.audit(actor, 'field.sim_start', {'pid': pid, 'port': int(port), 'answered': h is not None, 'program': prog['how']})
    st = status(ws, port); st['already_running'] = False; st['command'] = cmd
    return st


def stop_sim(ws, actor: str = 'system', port: int = SIM_PORT, wait_s: float = 10.0) -> dict:
    """Stop it the way systemd would, so every file closes and the manifest is written: the STOP file the box watches,
    and the signal where one reaches it."""
    st = status(ws, port)
    pid = st.get('pid')
    if not st['running'] and not pid:
        return st | {'stopped': False, 'note': 'not running'}
    # the STOP file first: the box reads it once a second and closes every file (on Windows no signal reaches a detached
    # process); the signal as well where there is one
    stop_file = os.path.join(sim_dir(ws), 'data', 'STOP')
    try:
        with open(stop_file, 'w', encoding='utf-8') as f:
            f.write('stop requested by GEA\n')
        sent = 'STOP file'
    except OSError as e:
        sent = f'no STOP file ({e})'
    if pid and os.name != 'nt':
        try:
            os.kill(pid, signal.SIGTERM)
            sent += ' + SIGTERM'
        except (ProcessLookupError, PermissionError, OSError):
            pass
    t_end = time.monotonic() + wait_s
    while time.monotonic() < t_end and health(port, timeout=0.5) is not None:
        time.sleep(0.25)
    gone = health(port, timeout=0.5) is None
    if not gone and pid and os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        gone = True
    try:
        os.remove(_run_path(ws))
    except OSError:
        pass
    ws.audit(actor, 'field.sim_stop', {'pid': pid, 'signal': sent, 'stopped': gone})
    return status(ws, port) | {'stopped': gone, 'signal': sent}


def monitor_url(address: str) -> str:
    """Where a box's monitor is, from its address as a person types it."""
    a = str(address or '').strip().rstrip('/')
    if not a:
        raise ValueError("the box's address, as its page shows it (http://192.168.1.50:8780)")
    if not a.lower().startswith(('http://', 'https://')):
        a = 'http://' + a
    if a.count(':') < 2:
        a += ':8780'
    return a + '/live'
