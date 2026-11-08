# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""wg - WireGuard on this computer, as the program drives it. On Windows: WireGuard for Windows (wireguard.com),
whose `wireguard.exe /installtunnelservice` makes a tunnel a service that starts with the computer, and whose
`wg.exe show` says how it is doing - both need administrator rights, which is why the program runs as a service
of its own. On Linux: the kernel's WireGuard with `wg-quick@gea-vpn` under systemd, the configuration in
/etc/wireguard. Everything is asked of the tools and reported as they said it; nothing is guessed, and when a tool
is missing the program says which and where it comes from."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional

IFACE = 'gea-vpn'
WINDOWS = os.name == 'nt'
WG_HOME_WIN = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'WireGuard')
INSTALL_FROM = 'https://www.wireguard.com/install/'


def tools() -> Dict[str, Optional[str]]:
    """Where the tools are on this computer (None when missing)."""
    if WINDOWS:
        wgexe = os.path.join(WG_HOME_WIN, 'wireguard.exe'); wg = os.path.join(WG_HOME_WIN, 'wg.exe')
        return {'wireguard': wgexe if os.path.isfile(wgexe) else None, 'wg': wg if os.path.isfile(wg) else shutil.which('wg'), 'wg-quick': None, 'systemctl': None}
    return {'wireguard': None, 'wg': shutil.which('wg'), 'wg-quick': shutil.which('wg-quick'), 'systemctl': shutil.which('systemctl')}


def installed() -> bool:
    t = tools()
    return bool(t['wireguard'] or t['wg-quick'] or t['wg'])


def _run(cmd: List[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def conf_path() -> str:
    """Where the tunnel's configuration lives once installed (Linux); on Windows the service keeps its own copy."""
    return f'/etc/wireguard/{IFACE}.conf'


def install_tunnel(conf_file: str) -> dict:
    """The tunnel from this configuration file, started now and at every boot; a tunnel already there is replaced."""
    t = tools()
    if WINDOWS:
        if not t['wireguard']:
            return {'ok': False, 'error': f'WireGuard for Windows is not installed ({INSTALL_FROM})'}
        base = os.path.splitext(os.path.basename(conf_file))[0]
        if base != IFACE:
            return {'ok': False, 'error': f'the configuration file is named {base}.conf; the tunnel is named after it and must be {IFACE}.conf'}
        _run([t['wireguard'], '/uninstalltunnelservice', IFACE])        # whatever was there, quietly
        time.sleep(1.0)
        r = _run([t['wireguard'], '/installtunnelservice', os.path.abspath(conf_file)])
        if r.returncode != 0:
            return {'ok': False, 'error': f'wireguard.exe /installtunnelservice: exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}'}
        return {'ok': True, 'how': 'wireguard.exe /installtunnelservice: a Windows service that starts with the computer'}
    if not t['wg-quick'] or not t['systemctl']:
        return {'ok': False, 'error': 'wg-quick or systemctl is missing: install WireGuard (apt-get install wireguard)'}
    try:
        os.makedirs('/etc/wireguard', mode=0o700, exist_ok=True)
        dst = conf_path()
        with open(conf_file, 'r', encoding='utf-8') as f:
            text = f.read()
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(dst, 0o600)
    except OSError as e:
        return {'ok': False, 'error': f'cannot write {conf_path()}: {e} (run as root)'}
    up = _run([t['systemctl'], 'is-active', f'wg-quick@{IFACE}']).stdout.strip() == 'active'
    if up:
        s = _run([t['wg-quick'], 'strip', IFACE])
        if s.returncode == 0 and t['wg']:
            p = subprocess.run([t['wg'], 'syncconf', IFACE, '/dev/stdin'], input=s.stdout, capture_output=True, text=True)
            if p.returncode == 0:
                return {'ok': True, 'how': 'the running tunnel was given the new configuration (wg syncconf)'}
        _run([t['systemctl'], 'restart', f'wg-quick@{IFACE}'])
        return {'ok': True, 'how': 'the tunnel was restarted with the new configuration'}
    r = _run([t['systemctl'], 'enable', '--now', f'wg-quick@{IFACE}'], timeout=60)
    if r.returncode != 0:
        return {'ok': False, 'error': f'systemctl enable --now wg-quick@{IFACE}: exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}'}
    return {'ok': True, 'how': f'wg-quick@{IFACE} enabled under systemd: up now and at every boot'}


def uninstall_tunnel() -> dict:
    t = tools()
    if WINDOWS:
        if not t['wireguard']:
            return {'ok': True, 'how': 'WireGuard is not installed; nothing to remove'}
        r = _run([t['wireguard'], '/uninstalltunnelservice', IFACE])
        return {'ok': True, 'how': 'the tunnel service removed' if r.returncode == 0 else f'wireguard.exe said exit {r.returncode}: {(r.stderr or r.stdout).strip()[:120]}'}
    if t['systemctl']:
        _run([t['systemctl'], 'disable', '--now', f'wg-quick@{IFACE}'], timeout=60)
    try:
        os.remove(conf_path())
    except OSError:
        pass
    return {'ok': True, 'how': f'wg-quick@{IFACE} disabled and its configuration removed'}


def parse_dump(text: str) -> dict:
    """`wg show <iface> dump`: the interface line, then one line per peer."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {}
    head = lines[0].split('\t')
    out = {'public_key': head[1] if len(head) > 1 else None, 'listen_port': int(head[2]) if len(head) > 2 and head[2].isdigit() else None, 'peers': []}
    for l in lines[1:]:
        f = l.split('\t')
        if len(f) < 8:
            continue
        hs = int(f[4]) if f[4].isdigit() else 0
        out['peers'].append({'public_key': f[0], 'endpoint': f[2] if f[2] != '(none)' else None, 'allowed': f[3], 'latest_handshake': hs,
                             'rx_bytes': int(f[5]) if f[5].isdigit() else 0, 'tx_bytes': int(f[6]) if f[6].isdigit() else 0,
                             'keepalive_s': int(f[7]) if f[7].isdigit() else 0})
    return out


def status() -> dict:
    """How the tunnel is doing, asked of `wg show`: installed, up, the hub's latest handshake and its age, the bytes."""
    t = tools()
    out: dict = {'installed': installed(), 'up': False, 'tools': t, 'install_from': INSTALL_FROM if WINDOWS else 'apt-get install wireguard'}
    if not t['wg']:
        out['note'] = 'WireGuard is not installed on this computer' if not out['installed'] else 'the wg tool is missing'
        return out
    try:
        r = _run([t['wg'], 'show', IFACE, 'dump'], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        out['note'] = f'wg show: {type(e).__name__}: {e}'
        return out
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        out['note'] = f'the tunnel {IFACE} is not up here' + (f' ({msg[:120]})' if msg else '')
        out['configured'] = _configured(t)
        return out
    d = parse_dump(r.stdout)
    out.update({'up': True, 'configured': True, 'public_key': d.get('public_key'), 'peers': d.get('peers', [])})
    if d.get('peers'):
        p = d['peers'][0]
        now = time.time()
        out['hub'] = {'endpoint': p['endpoint'], 'latest_handshake_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(p['latest_handshake'])) if p['latest_handshake'] else None,
                      'handshake_age_s': (now - p['latest_handshake']) if p['latest_handshake'] else None, 'rx_bytes': p['rx_bytes'], 'tx_bytes': p['tx_bytes'], 'keepalive_s': p['keepalive_s']}
        age = out['hub']['handshake_age_s']
        out['talking'] = age is not None and age < 180.0
        out['note'] = ('the tunnel is up and the hub answered ' + (f'{age:.0f} s ago' if age is not None else 'never')) if out['talking'] else \
                      ('the tunnel is up but the hub has not answered' + (f' for {age / 60:.0f} min' if age is not None else ' yet') + ': the hub, its port, or this computer\'s internet')
    else:
        out['note'] = 'the tunnel is up with no peer'
    return out


def _configured(t: Dict[str, Optional[str]]) -> bool:
    if WINDOWS:
        r = _run(['sc', 'query', f'WireGuardTunnel${IFACE}'])
        return r.returncode == 0
    return os.path.isfile(conf_path())
