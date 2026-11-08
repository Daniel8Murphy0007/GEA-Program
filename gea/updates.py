# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""updates - the program's own new versions, from inside the program.

Every version is a release on GitHub with the kits attached - GEA-Program-Setup-<v>.exe, GEA-Reader-Setup-<v>.exe,
GEA-Recorder-<v>.tar.gz, GEA-Driller-<v>.tar.gz, GEA-VPN-Setup-<v>.exe, GEA-VPN-<v>.tar.gz - each with the SHA-256 GitHub recorded when it was uploaded. This module asks GitHub
what the newest release is, fetches the kits into the workspace's `installers\\` folder (each checked against its
recorded SHA-256 before it is kept; older kits moved to `installers\\_older\\`), and installs the setup from there by
clicking: the setup stops the control panel, installs over the old version, keeps the workspace, and the control
panel is started again - a helper outside the program does the last step, since the program is what is being
replaced. The Audit/Update page's card drives it; `gea update --release`, `--fetch`, `--install` do the same.

Nothing is fetched or installed unless a person asks; the check alone reaches GitHub, read-only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

REPO = 'Daniel8Murphy0007/GEA-Program'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
KITS = ('GEA-Program-Setup-', 'GEA-Reader-Setup-', 'GEA-Recorder-', 'GEA-Driller-', 'GEA-VPN-Setup-', 'GEA-VPN-')     # GEA-VPN-Setup- before GEA-VPN-: a prefix match
INSTALLERS = 'installers'


def _ver_t(v: str):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).lstrip('v').split('.'))


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'GEA-Program update', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_release(d: dict) -> dict:
    """GitHub's release JSON -> {tag, version, published_utc, assets: [{name, bytes, sha256, url, kit}]}: the kits only."""
    tag = str(d.get('tag_name') or '')
    assets = []
    for a in d.get('assets') or []:
        name = str(a.get('name') or '')
        kit = next((k for k in KITS if name.startswith(k)), None)
        if not kit:
            continue
        dig = str(a.get('digest') or '')
        assets.append({'name': name, 'bytes': int(a.get('size') or 0), 'sha256': dig.split(':', 1)[1] if dig.startswith('sha256:') else None,
                       'url': str(a.get('browser_download_url') or ''), 'kit': kit, 'updated_utc': a.get('updated_at')})
    return {'tag': tag, 'version': tag.lstrip('v'), 'published_utc': d.get('published_at'), 'assets': assets, 'url': d.get('html_url'), 'name': d.get('name')}


def latest(timeout: float = 8.0, fetch: Callable[[str, float], bytes] = _get) -> dict:
    """The newest release, or {'error': ...} when GitHub cannot be reached (the page says so, nothing more)."""
    try:
        return parse_release(json.loads(fetch(API, timeout).decode('utf-8')))
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}', 'tag': None, 'version': None, 'assets': []}


def installers_dir(ws) -> str:
    return os.path.join(ws.path, INSTALLERS)


def on_hand(ws) -> List[dict]:
    """The kits in installers\\: name, version, bytes, whether a fetch record vouches for its hash."""
    d = installers_dir(ws)
    rec = {}
    try:
        with open(os.path.join(d, 'fetched.json'), encoding='utf-8') as f:
            rec = {x['name']: x for x in json.load(f).get('files', [])}
    except (OSError, ValueError):
        pass
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            m = re.match(r'(GEA-Program-Setup-|GEA-Reader-Setup-|GEA-Recorder-|GEA-Driller-|GEA-VPN-Setup-|GEA-VPN-)(\d+\.\d+\.\d+)', n)
            if m and os.path.isfile(os.path.join(d, n)):
                out.append({'name': n, 'kit': m.group(1), 'version': m.group(2), 'bytes': os.path.getsize(os.path.join(d, n)), 'verified': n in rec and rec[n].get('verified', False)})
    return out


def state(ws, running_version: str, check: bool = True, timeout: float = 8.0, fetch: Callable[[str, float], bytes] = _get) -> dict:
    rel = latest(timeout, fetch) if check else {'tag': None, 'version': None, 'assets': [], 'skipped': True}
    have = on_hand(ws)
    newest = rel.get('version')
    st = 'unknown' if not newest else ('current' if _ver_t(newest) <= _ver_t(running_version) else 'behind')
    here = [h for h in have if newest and h['version'] == newest]
    return {'release': rel, 'state': st, 'running': running_version, 'installers_dir': installers_dir(ws), 'on_hand': have,
            'newest_on_hand': {k: any(h['kit'] == k for h in here) for k in KITS if any(a.get('kit') == k for a in rel.get('assets', []))} if newest else {},
            'can_install': sys.platform.startswith('win') and any(h['kit'] == 'GEA-Program-Setup-' and h['verified'] for h in here),
            'last_fetch': _last_fetch(ws), 'last_install': _last_install(ws)}


def _last_fetch(ws) -> Optional[dict]:
    try:
        with open(os.path.join(installers_dir(ws), 'fetched.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _last_install(ws) -> Optional[dict]:
    try:
        with open(os.path.join(installers_dir(ws), 'install.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def fetch_kits(ws, actor: str = 'system', release: Optional[dict] = None, timeout: float = 600.0, fetch: Callable[[str, float], bytes] = _get,
               progress: Optional[Callable[[str], None]] = None) -> dict:
    """The newest release's kits into installers\\, each checked against GitHub's SHA-256 before it is kept (a kit
    without a recorded digest is kept and marked unverified; one that fails its digest is not kept); kits of other
    versions moved to installers\\_older\\. Returns what was done."""
    rel = release or latest(timeout=15.0, fetch=fetch)
    if rel.get('error') or not rel.get('assets'):
        raise RuntimeError(rel.get('error') or 'the release lists no kits')
    d = installers_dir(ws)
    os.makedirs(d, exist_ok=True)
    say = progress or (lambda s: None)
    files = []
    for a in rel['assets']:
        dst = os.path.join(d, a['name'])
        if os.path.isfile(dst) and a['sha256'] and _sha256(dst) == a['sha256']:
            files.append({'name': a['name'], 'bytes': a['bytes'], 'sha256': a['sha256'], 'verified': True, 'fetched': False}); say(f"{a['name']}: already here, hash right")
            continue
        say(f"fetching {a['name']} ({a['bytes'] / 1e6:.1f} MB)")
        data = fetch(a['url'], timeout)
        got = hashlib.sha256(data).hexdigest()
        if a['sha256'] and got != a['sha256']:
            files.append({'name': a['name'], 'bytes': len(data), 'sha256': got, 'verified': False, 'fetched': True, 'rejected': 'the SHA-256 is not the one GitHub recorded; not kept'})
            say(f"{a['name']}: WRONG HASH, not kept")
            continue
        with open(dst + '.part', 'wb') as f:
            f.write(data)
        os.replace(dst + '.part', dst)
        files.append({'name': a['name'], 'bytes': len(data), 'sha256': got, 'verified': bool(a['sha256']), 'fetched': True})
        say(f"{a['name']}: {len(data) / 1e6:.1f} MB, {'hash right' if a['sha256'] else 'no digest recorded by GitHub - kept, unverified'}")
    # older kits aside
    older = os.path.join(d, '_older')
    moved = []
    for h in on_hand(ws):
        if h['version'] != rel['version']:
            os.makedirs(older, exist_ok=True)
            try:
                shutil.move(os.path.join(d, h['name']), os.path.join(older, h['name']))
                moved.append(h['name'])
            except OSError:
                pass
    rec = {'version': rel['version'], 'tag': rel['tag'], 'fetched_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'by': actor, 'files': files, 'moved_aside': moved, 'dir': d}
    with open(os.path.join(d, 'fetched.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    ws.audit(actor, 'update.fetch', {'version': rel['version'], 'files': [(x['name'], x['verified']) for x in files], 'rejected': [x['name'] for x in files if x.get('rejected')], 'moved_aside': moved})
    return rec


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def install_command(ws, version: str, app_dir: str, pythonw: str, reader: bool = True) -> List[str]:
    """What the helper runs: the program's setup silently over the old version with this workspace, the reader's
    setup when its kit is here, then the launcher so the control panel comes back. A cmd of its own, detached,
    because the program is what the setup replaces."""
    d = installers_dir(ws)
    setup = os.path.join(d, f'GEA-Program-Setup-{version}.exe')
    log = os.path.join(d, 'gea-program-setup.log')
    parts = [f'start "" /wait "{setup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /WORKSPACE="{ws.path}" /LOG="{log}"']
    rsetup = os.path.join(d, f'GEA-Reader-Setup-{version}.exe')
    if reader and os.path.isfile(rsetup):
        parts.append(f'start "" /wait "{rsetup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /TASKS="desktopicon,zipverb" /LOG="{os.path.join(d, "gea-reader-setup.log")}"')
    parts.append(f'start "" "{pythonw}" -m gea launch')
    return ['cmd', '/c', ' & '.join(parts)]


def install(ws, actor: str, version: str, popen: Callable = subprocess.Popen) -> dict:
    """Run the fetched setup: the control panel stops while it runs (a minute), the launcher brings it back."""
    from . import launcher as L
    if not sys.platform.startswith('win') and popen is subprocess.Popen:
        raise RuntimeError('the setup installs on Windows; on another system install the kit from the release page')
    kit = next((h for h in on_hand(ws) if h['kit'] == 'GEA-Program-Setup-' and h['version'] == version), None)
    if kit is None:
        raise RuntimeError(f'GEA-Program-Setup-{version}.exe is not in {installers_dir(ws)}: fetch the kits first')
    if not kit['verified']:
        raise RuntimeError(f"{kit['name']} was not verified against the release's SHA-256: fetch it again")
    app = L.kit_dir()
    app_dir = str(app) if app else os.path.dirname(sys.executable)
    pythonw = L.gui_python()
    cmd = install_command(ws, version, app_dir, pythonw)
    kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'close_fds': True, 'cwd': L.neutral_cwd()}
    if L.is_windows():
        kw['creationflags'] = L.DETACHED_PROCESS | L.CREATE_NEW_PROCESS_GROUP
    rec = {'version': version, 'started_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'by': actor, 'command': cmd, 'log': os.path.join(installers_dir(ws), 'gea-program-setup.log'),
           'from_version': __import__('gea').__version__}
    with open(os.path.join(installers_dir(ws), 'install.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    ws.audit(actor, 'update.install', {'version': version, 'from': rec['from_version']})
    popen(cmd, **kw)
    return rec
