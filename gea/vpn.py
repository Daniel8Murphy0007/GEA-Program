# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""vpn - GEA-VPN: the site's own private network across the public internet.

The programs run on separate computers in separate places: the control panel in the office, a recorder on each
pad (on a cellular modem, behind the carrier's address translation, reachable by nothing from outside), the
operator window, a client's reader. GEA-VPN joins them into one private network so the office reaches each box
by a fixed private address as if it sat on the pad's own cable - the pull, the live monitor, a live feed - and
nothing else on the internet can see or reach any of them.

It is built on WireGuard, the VPN every current Linux kernel carries and Windows installs from wireguard.com,
whose design and code have been reviewed for years; GEA writes no cryptography of its own into the tunnel. GEA's
part is the management, so that nothing is typed on the pad: the keys (X25519, made here or by `wg genkey`), the
addresses, the hub's configuration and installer, every member's configuration file with its key and a preshared
key, and the status. The hub is a small cloud server of the site's own (any Debian or Ubuntu machine with a
public address; a few dollars a month) that every member connects out to - so a box behind a cellular modem needs
no port opened anywhere - and that forwards between them under a rule: every member talks to the office, and the
boxes and readers never see one another.

    keys/vpn/network.json         the network: subnet, hub, members (private keys inside: the file is the site's)
    exports/vpn/hub/              install-hub.sh + gea-vpn.conf + README: run once on the hub, again after a change
    exports/vpn/<member>/         gea-vpn.conf (+ vpn.sh for a box): imported on that computer once

Standard library only."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import x25519 as X

FILE = os.path.join('keys', 'vpn', 'network.json')
KINDS = {'office': 'the control panel (GEA-Program) - reaches every member',
         'box': 'a recorder on a pad (its page on 8780) - reaches the office only',
         'operator': 'an operator window on another computer - reaches the office only',
         'reader': "a client's reader - reaches the office only",
         'other': 'another computer of the site - reaches the office only'}
PORTS = {'office': 8765, 'box': 8780, 'operator': None, 'reader': None, 'other': None}
DEFAULT_SUBNET = '10.77.0.0/24'
DEFAULT_PORT = 51820
IFACE = 'gea-vpn'


class VpnError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws) -> str:
    return os.path.join(ws.path, FILE)


def load(ws) -> Optional[dict]:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(ws, net: dict) -> None:
    p = _path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(net, f, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def keypair() -> Dict[str, str]:
    """A WireGuard key pair: from `wg genkey` where the tools are installed, else GEA's own X25519 (the same keys)."""
    wg = shutil.which('wg')
    if wg:
        try:
            priv = subprocess.run([wg, 'genkey'], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            pub = subprocess.run([wg, 'pubkey'], input=priv, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            if priv and pub:
                return {'private_key': priv, 'public_key': pub, 'made_by': 'wg genkey'}
        except Exception:
            pass
    priv, pub = X.keypair_b64()
    return {'private_key': priv, 'public_key': pub, 'made_by': 'x25519.py'}


def check_host(host: str) -> str:
    h = str(host or '').strip()
    if not h or len(h) > 253 or not re.match(r'^[A-Za-z0-9.\-:\[\]]+$', h):
        raise VpnError("the hub's public address: a host name (vpn.example.com) or an IP address")
    return h


def create(ws, actor: str, hub_host: str, hub_port: int = DEFAULT_PORT, subnet: str = DEFAULT_SUBNET, name: Optional[str] = None) -> dict:
    """The network: its subnet, its hub (address .1) with a key pair, and the office as the first member (.2)."""
    if load(ws):
        raise VpnError('this workspace has a network already; change the hub or add members instead')
    try:
        sn = ipaddress.ip_network(subnet, strict=True)
    except ValueError:
        raise VpnError(f'{subnet} is not a private subnet (10.77.0.0/24, say)')
    if not sn.is_private or sn.prefixlen > 29:
        raise VpnError('the subnet must be private and hold at least six addresses')
    port = int(hub_port)
    if not (1 <= port <= 65535):
        raise VpnError('the hub port is 1-65535 (51820 is WireGuard\'s usual)')
    hosts = list(sn.hosts())
    hub = {'host': check_host(hub_host), 'port': port, 'address': str(hosts[0]), **keypair()}
    net = {'schema': 1, 'name': name or f"{ws.manifest.get('name') or 'site'} network", 'subnet': str(sn), 'interface': IFACE, 'hub': hub, 'members': [],
           'created_utc': _utc(), 'created_by': actor, 'changed_utc': _utc()}
    _save(ws, net)
    ws.audit(actor, 'vpn.create', {'name': net['name'], 'subnet': net['subnet'], 'hub': f"{hub['host']}:{port}", 'hub_public_key': hub['public_key']})
    add_member(ws, actor, 'Office', 'office')
    return load(ws)


def set_hub(ws, actor: str, host: str, port: Optional[int] = None) -> dict:
    net = load(ws)
    if not net:
        raise VpnError('no network yet')
    net['hub']['host'] = check_host(host)
    if port:
        net['hub']['port'] = int(port)
    net['changed_utc'] = _utc()
    _save(ws, net)
    ws.audit(actor, 'vpn.hub', {'hub': f"{net['hub']['host']}:{net['hub']['port']}"})
    return net


def _slug(name: str) -> str:
    s = re.sub(r'[^A-Za-z0-9]+', '-', name.strip()).strip('-').lower()
    return s or 'member'


def add_member(ws, actor: str, name: str, kind: str) -> dict:
    """A member: the next free address, a key pair, a preshared key with the hub, what it may reach by its kind."""
    net = load(ws)
    if not net:
        raise VpnError('no network yet: make one first (the hub\'s public address)')
    if kind not in KINDS:
        raise VpnError(f"a member is one of {', '.join(KINDS)}")
    name = str(name or '').strip()
    if not (1 <= len(name) <= 40):
        raise VpnError('the member needs a name of 1-40 characters')
    mid = _slug(name)
    if any(m['id'] == mid for m in net['members']):
        raise VpnError(f'a member called {name} exists; names are one each')
    if kind == 'office' and any(m['kind'] == 'office' for m in net['members']):
        raise VpnError('one office per network: the control panel every member talks to')
    sn = ipaddress.ip_network(net['subnet'])
    taken = {net['hub']['address']} | {m['address'] for m in net['members']}
    addr = next((str(h) for h in sn.hosts() if str(h) not in taken), None)
    if addr is None:
        raise VpnError('the subnet is full')
    m = {'id': mid, 'name': name, 'kind': kind, 'address': addr, **keypair(), 'preshared_key': X.preshared_b64(), 'added_utc': _utc(), 'added_by': actor}
    net['members'].append(m)
    net['changed_utc'] = _utc()
    _save(ws, net)
    ws.audit(actor, 'vpn.member.add', {'id': mid, 'name': name, 'kind': kind, 'address': addr, 'public_key': m['public_key']})
    return m


def remove_member(ws, actor: str, member_id: str) -> dict:
    net = load(ws)
    if not net:
        raise VpnError('no network yet')
    m = next((x for x in net['members'] if x['id'] == member_id), None)
    if not m:
        raise VpnError(f'no member {member_id}')
    if m['kind'] == 'office':
        raise VpnError('the office stays: it is what every member talks to')
    net['members'] = [x for x in net['members'] if x['id'] != member_id]
    net['changed_utc'] = _utc()
    _save(ws, net)
    _drop_files(ws, member_id)                     # its configuration (its key) goes with it
    ws.audit(actor, 'vpn.member.remove', {'id': member_id, 'name': m['name'], 'address': m['address'],
                                          'note': 'its key is out of the hub once the hub installer is run again'})
    return m


# -- the configurations ------------------------------------------------------------------------------------------------

def member_conf(net: dict, m: dict) -> str:
    sn = ipaddress.ip_network(net['subnet'])
    office = next((x for x in net['members'] if x['kind'] == 'office'), None)
    reach = str(sn) if m['kind'] == 'office' else ', '.join(a for a in [net['hub']['address'] + '/32', (office['address'] + '/32') if office else None] if a)
    return (f"# GEA-VPN: {m['name']} ({m['kind']}) on {net['name']} - made {_utc()} by GEA-Program; keep this file to yourself: it holds this computer's key\n"
            f"[Interface]\nPrivateKey = {m['private_key']}\nAddress = {m['address']}/32\n\n"
            f"[Peer]\n# the hub\nPublicKey = {net['hub']['public_key']}\nPresharedKey = {m['preshared_key']}\n"
            f"Endpoint = {net['hub']['host']}:{net['hub']['port']}\nAllowedIPs = {reach}\nPersistentKeepalive = 25\n")


def hub_conf(net: dict) -> str:
    sn = ipaddress.ip_network(net['subnet'])
    office = next((x for x in net['members'] if x['kind'] == 'office'), None)
    rules_up, rules_down = [], []
    # forwarding: every member <-> the office; nothing else between members
    rules_up.append(f'iptables -A FORWARD -i {IFACE} -o {IFACE} -j DROP')
    if office:
        rules_up.insert(0, f"iptables -A FORWARD -i {IFACE} -o {IFACE} -s {office['address']}/32 -j ACCEPT")
        rules_up.insert(1, f"iptables -A FORWARD -i {IFACE} -o {IFACE} -d {office['address']}/32 -j ACCEPT")
    rules_down = [r.replace(' -A ', ' -D ') for r in rules_up]
    out = [f"# GEA-VPN hub for {net['name']} - made {_utc()} by GEA-Program; /etc/wireguard/{IFACE}.conf",
           '[Interface]', f"Address = {net['hub']['address']}/{sn.prefixlen}", f"ListenPort = {net['hub']['port']}", f"PrivateKey = {net['hub']['private_key']}",
           *[f'PostUp = {r}' for r in rules_up], *[f'PostDown = {r}' for r in rules_down], '']
    for m in net['members']:
        out += [f"[Peer]", f"# {m['name']} ({m['kind']}) {m['address']}", f"PublicKey = {m['public_key']}", f"PresharedKey = {m['preshared_key']}", f"AllowedIPs = {m['address']}/32", '']
    return '\n'.join(out)


HUB_INSTALL = r'''#!/bin/sh
# GEA-VPN hub - run once on the hub (a Debian or Ubuntu server with a public address), as root:   sudo sh install-hub.sh
# and again whenever GEA has made this folder anew (a member added or removed): it is safe to run again.
# What it does: installs WireGuard from the distribution, puts gea-vpn.conf in /etc/wireguard, turns packet forwarding
# on, opens the hub's port in the firewall it finds, starts the tunnel and sets it to start at boot (or reloads it
# when it is already up). Nothing else on the server is touched.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
IFACE=__IFACE__
PORT=__PORT__
echo "== GEA-VPN hub: WireGuard"
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y wireguard iptables >/dev/null
install -m 600 "$HERE/$IFACE.conf" /etc/wireguard/$IFACE.conf
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-gea-vpn.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null
if command -v ufw >/dev/null 2>&1; then ufw allow $PORT/udp >/dev/null 2>&1 || true; fi
if wg show $IFACE >/dev/null 2>&1; then
  wg-quick strip $IFACE > /run/gea-vpn.strip && wg syncconf $IFACE /run/gea-vpn.strip && rm -f /run/gea-vpn.strip || { wg-quick down $IFACE; wg-quick up $IFACE; }
  echo "   reloaded: the members are now as the configuration says"
else
  systemctl enable --now wg-quick@$IFACE
  echo "   started, and set to start at boot"
fi
wg show $IFACE
echo "== done. Members connect to $(hostname -f 2>/dev/null || hostname):$PORT; the hub's public key is above."
'''

BOX_INSTALL = r'''#!/bin/sh
# GEA-VPN on a recorder (Raspberry Pi) - run once, as root, with this folder's .conf beside it:   sudo sh vpn.sh
# Installs WireGuard, puts the box's configuration in /etc/wireguard, starts the tunnel and sets it to start at boot.
# From then on the box is at its private address from the office; nothing is opened on the pad's modem.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
IFACE=__IFACE__
CONF=$(ls "$HERE"/*.conf | head -n 1)
apt-get install -y wireguard >/dev/null
install -m 600 "$CONF" /etc/wireguard/$IFACE.conf
systemctl enable --now wg-quick@$IFACE
wg show $IFACE
echo "== GEA-VPN up: this box is __ADDRESS__ on the site's network; its page from the office: http://__ADDRESS__:8780/"
'''

WINDOWS_STEPS = '''GEA-VPN on this Windows computer - {name}

1. Install WireGuard for Windows once, from https://www.wireguard.com/install/ (the installer asks for administrator rights; nothing of GEA needs them).
2. Open WireGuard, click "Import tunnel(s) from file", choose {conf}.
3. Click Activate. The tunnel reconnects by itself whenever this computer is on the internet.

This computer is then {address} on {network}. Keep {conf} to yourself: it holds this computer's key. If it is lost, remove the member in GEA and add it again.
'''


def _drop_files(ws, member_id: str) -> None:
    root = os.path.join(ws.path, 'exports', 'vpn')
    shutil.rmtree(os.path.join(root, member_id), ignore_errors=True)
    try:
        os.remove(os.path.join(root, f'gea-vpn-{member_id}.zip'))
    except OSError:
        pass


def write_bundles(ws, actor: str, member_id: Optional[str] = None) -> dict:
    """The hub's installer folder and zip, and every member's (or one member's) configuration folder and zip,
    under exports/vpn/. Written anew each time; the audit line says what was written."""
    net = load(ws)
    if not net:
        raise VpnError('no network yet')
    root = ws.dir('exports', 'vpn')
    written = {}
    if member_id is None:
        keep = {'hub'} | {m['id'] for m in net['members']}
        for n in os.listdir(root):                 # a member removed earlier leaves nothing behind
            mid = n[len('gea-vpn-'):-len('.zip')] if n.startswith('gea-vpn-') and n.endswith('.zip') else n
            if mid not in keep:
                _drop_files(ws, mid)
        hd = os.path.join(root, 'hub')
        shutil.rmtree(hd, ignore_errors=True); os.makedirs(hd)
        with open(os.path.join(hd, f'{IFACE}.conf'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(hub_conf(net))
        with open(os.path.join(hd, 'install-hub.sh'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(HUB_INSTALL.replace('__IFACE__', IFACE).replace('__PORT__', str(net['hub']['port'])))
        with open(os.path.join(hd, 'README.txt'), 'w', encoding='utf-8') as f:
            f.write(f"GEA-VPN hub for {net['name']}\n\nCopy this folder to the hub (a Debian or Ubuntu server with a public address; {net['hub']['host']}:{net['hub']['port']} "
                    f"as GEA has it) and run, as root:\n\n    sudo sh install-hub.sh\n\nRun it again whenever GEA makes this folder anew (a member added or removed).\n"
                    f"The hub forwards between the office and every member; members never reach one another.\nMembers ({len(net['members'])}):\n"
                    + ''.join(f"  {m['address']:<15} {m['name']} ({m['kind']})\n" for m in net['members']))
        for p in (os.path.join(hd, f'{IFACE}.conf'),):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        z = os.path.join(root, 'gea-vpn-hub.zip')
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
            for n in os.listdir(hd):
                zf.write(os.path.join(hd, n), f'gea-vpn-hub/{n}')
        written['hub'] = {'dir': hd, 'zip': z}
    for m in net['members']:
        if member_id is not None and m['id'] != member_id:
            continue
        md = os.path.join(root, m['id'])
        shutil.rmtree(md, ignore_errors=True); os.makedirs(md)
        conf = os.path.join(md, f'{IFACE}.conf')                 # the file's name is the tunnel's name, on Windows and on the Pi
        with open(conf, 'w', encoding='utf-8', newline='\n') as f:
            f.write(member_conf(net, m))
        try:
            os.chmod(conf, 0o600)
        except OSError:
            pass
        if m['kind'] == 'box':
            with open(os.path.join(md, 'vpn.sh'), 'w', encoding='utf-8', newline='\n') as f:
                f.write(BOX_INSTALL.replace('__IFACE__', IFACE).replace('__ADDRESS__', m['address']))
        else:
            with open(os.path.join(md, 'README.txt'), 'w', encoding='utf-8') as f:
                f.write(WINDOWS_STEPS.format(name=m['name'], conf=os.path.basename(conf), address=m['address'], network=net['name']))
        z = os.path.join(root, f"gea-vpn-{m['id']}.zip")
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
            for n in os.listdir(md):
                zf.write(os.path.join(md, n), f"gea-vpn-{m['id']}/{n}")
        written[m['id']] = {'dir': md, 'zip': z, 'conf': conf}
    ws.audit(actor, 'vpn.bundles', {'written': sorted(written), 'members': len(net['members'])})
    return written


# -- the status -----------------------------------------------------------------------------------------------------------

def probe(address: str, port: Optional[int], timeout: float = 1.5) -> dict:
    if not port:
        return {'reachable': None, 'note': 'no service to probe on this member'}
    try:
        with socket.create_connection((address, int(port)), timeout=timeout):
            return {'reachable': True, 'note': f'{address}:{port} answers'}
    except OSError as e:
        return {'reachable': False, 'note': f'{address}:{port}: {e.strerror or e}'}


def local_wireguard() -> dict:
    """Whether WireGuard is installed on this computer and the site's tunnel is up (asked of the `wg` tool)."""
    wg = shutil.which('wg') or (r'C:\Program Files\WireGuard\wg.exe' if os.path.isfile(r'C:\Program Files\WireGuard\wg.exe') else None)
    if not wg:
        return {'installed': False, 'up': False, 'note': 'WireGuard is not installed on this computer (wireguard.com/install)'}
    try:
        r = subprocess.run([wg, 'show', IFACE, 'latest-handshakes'], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return {'installed': True, 'up': False, 'note': f'the tunnel {IFACE} is not up here ({(r.stderr or r.stdout).strip()[:120]})'}
        hs = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                hs[parts[0]] = int(parts[1])
        return {'installed': True, 'up': True, 'handshakes': hs, 'note': f'the tunnel {IFACE} is up here'}
    except Exception as e:
        return {'installed': True, 'up': False, 'note': f'wg: {type(e).__name__}: {e}'}


def status(ws, probes: bool = True, office_port: Optional[int] = None) -> dict:
    net = load(ws)
    if not net:
        return {'network': None, 'kinds': KINDS}
    pub = {'name': net['name'], 'subnet': net['subnet'], 'interface': IFACE, 'hub': {k: v for k, v in net['hub'].items() if k != 'private_key'},
           'members': [{k: v for k, v in m.items() if k not in ('private_key', 'preshared_key')} for m in net['members']],
           'created_utc': net['created_utc'], 'changed_utc': net['changed_utc']}
    local = local_wireguard()
    for m in pub['members']:
        port = office_port if m['kind'] == 'office' and office_port else PORTS.get(m['kind'])
        m['probe'] = probe(m['address'], port) if probes and local.get('up') else {'reachable': None, 'note': 'the tunnel is not up on this computer' if probes else 'not probed'}
        if local.get('handshakes') and m['public_key'] in local['handshakes']:
            m['last_handshake_utc'] = datetime.fromtimestamp(local['handshakes'][m['public_key']], tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if local['handshakes'][m['public_key']] else None
    root = os.path.join(ws.path, 'exports', 'vpn')
    bundles = {n: os.path.join(root, n) for n in (os.listdir(root) if os.path.isdir(root) else []) if n.endswith('.zip')}
    return {'network': pub, 'local': local, 'bundles': bundles, 'kinds': KINDS, 'exports_dir': root}


def text(ws) -> str:
    st = status(ws, probes=False)
    if not st['network']:
        return 'GEA-VPN: no network yet'
    n = st['network']
    lines = [f"GEA-VPN {n['name']}: {n['subnet']}, hub {n['hub']['host']}:{n['hub']['port']} ({n['hub']['address']}), key {n['hub']['public_key'][:12]}...",
             f"  WireGuard here: {st['local']['note']}"]
    for m in n['members']:
        lines.append(f"  {m['address']:<15} {m['name']:<24} {m['kind']:<9} key {m['public_key'][:12]}...")
    return '\n'.join(lines)
