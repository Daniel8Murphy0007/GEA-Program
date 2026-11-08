# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""bundle - the connect information, as the office makes it in GEA-Program: the member's zip (`gea-vpn-<member>.zip`
holding `gea-vpn.conf`), or the .conf file itself, or its text pasted. Read, checked key by key (a WireGuard key is 32
bytes in base64; an address is one of a private subnet; the hub's endpoint is a name or an address and a port) and
turned into what the program keeps: the configuration as the tunnel needs it, and what it says of the member - the
name, the kind, the network, the address, the hub, the office's address when the member is told it. Nothing is
guessed: a file that is not a member's configuration is refused with the reason."""
from __future__ import annotations

import base64
import binascii
import io
import ipaddress
import re
import zipfile
from typing import Dict, List, Optional

from . import x25519

IFACE = 'gea-vpn'
HEAD = re.compile(r'^#\s*GEA-VPN:\s*(?P<name>.+?)\s*\((?P<kind>[a-z]+)\)\s*on\s*(?P<network>.+?)\s*-\s*made\s', re.M)


class BundleError(ValueError):
    pass


def _key(s: str, what: str) -> str:
    s = (s or '').strip()
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise BundleError(f'{what} is not a WireGuard key (base64)')
    if len(raw) != 32:
        raise BundleError(f'{what} is not a WireGuard key (32 bytes)')
    return s


def parse_conf(text: str) -> dict:
    """The sections and keys of a WireGuard configuration, in order; a key seen twice is an error."""
    sections: List[Dict[str, str]] = []
    cur: Optional[Dict[str, str]] = None
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip() if not raw.lstrip().startswith('#') else ''
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            cur = {'__section__': line[1:-1].strip().lower()}
            sections.append(cur)
            continue
        if cur is None or '=' not in line:
            raise BundleError(f'not a WireGuard configuration: "{raw.strip()[:60]}"')
        k, v = (x.strip() for x in line.split('=', 1))
        if k in cur:
            raise BundleError(f'{k} given twice in [{cur["__section__"]}]')
        cur[k] = v
    return {'sections': sections}


def read_connect_info(data: bytes, filename: str = '') -> dict:
    """The connect information from the bytes of a zip, a .conf file, or pasted text."""
    name = (filename or '').lower()
    text: Optional[str] = None
    if data[:4] == b'PK\x03\x04' or name.endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                confs = [n for n in z.namelist() if n.lower().endswith('.conf') and not n.endswith('/')]
                if not confs:
                    raise BundleError('the zip holds no .conf file; the office\'s member zip holds gea-vpn.conf')
                if len(confs) > 1:
                    raise BundleError(f'the zip holds {len(confs)} .conf files; a member\'s zip holds one')
                text = z.read(confs[0]).decode('utf-8', 'replace')
        except zipfile.BadZipFile:
            raise BundleError('not a zip file')
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise BundleError('neither a zip nor a text configuration')
    return check_conf(text)


def check_conf(text: str) -> dict:
    """A member's configuration checked: one [Interface] with a key and an address, one [Peer] (the hub) with its key,
    its endpoint and what it carries; the member's name, kind and network from the first line when GEA wrote it."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    p = parse_conf(text)
    ifs = [s for s in p['sections'] if s['__section__'] == 'interface']
    peers = [s for s in p['sections'] if s['__section__'] == 'peer']
    if len(ifs) != 1:
        raise BundleError(f'{len(ifs)} [Interface] sections; a member\'s configuration has one')
    if len(peers) != 1:
        raise BundleError(f'{len(peers)} [Peer] sections; a member connects to one hub')
    i, h = ifs[0], peers[0]
    priv = _key(i.get('PrivateKey', ''), 'the member\'s PrivateKey')
    try:
        addr = ipaddress.ip_interface(i.get('Address', '').strip())
    except ValueError:
        raise BundleError('the [Interface] Address is not an address')
    if not addr.ip.is_private:
        raise BundleError(f'the member\'s address {addr.ip} is not private; a GEA-VPN address is')
    pub = _key(h.get('PublicKey', ''), 'the hub\'s PublicKey')
    psk = _key(h.get('PresharedKey', ''), 'the PresharedKey') if h.get('PresharedKey') else None
    ep = h.get('Endpoint', '').strip()
    m = re.match(r'^(?P<host>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+):(?P<port>\d{1,5})$', ep)
    if not m or not (1 <= int(m.group('port')) <= 65535):
        raise BundleError('the hub\'s Endpoint is host:port, with a name or an address')
    allowed = []
    for a in (h.get('AllowedIPs') or '').split(','):
        a = a.strip()
        if not a:
            continue
        try:
            allowed.append(str(ipaddress.ip_network(a, strict=False)))
        except ValueError:
            raise BundleError(f'AllowedIPs: {a} is not a network')
    if not allowed:
        raise BundleError('the hub\'s AllowedIPs says nothing; the member would reach nothing')
    keep = int(h.get('PersistentKeepalive', '0') or 0)
    hd = HEAD.search(text)
    info = {'name': hd.group('name') if hd else '', 'kind': hd.group('kind') if hd else '', 'network': hd.group('network') if hd else ''}
    # the office's address: the one /32 told to a member that is not the hub's own; a member told the subnet is the office
    hub_ip = None
    office = None
    for a in allowed:
        n = ipaddress.ip_network(a)
        if n.prefixlen == n.max_prefixlen:
            ip = str(n.network_address)
            if hub_ip is None:
                hub_ip = ip
            else:
                office = ip
    whole = [a for a in allowed if ipaddress.ip_network(a).prefixlen < ipaddress.ip_network(a).max_prefixlen]
    return {'conf': text if text.endswith('\n') else text + '\n', 'iface': IFACE, 'address': str(addr.ip), 'prefix': addr.network.prefixlen,
            'public_key': x25519.public_from_b64(priv), 'hub': {'public_key': pub, 'endpoint': ep, 'host': m.group('host'), 'port': int(m.group('port')), 'address': hub_ip},
            'preshared': bool(psk), 'allowed': allowed, 'reaches_subnet': whole[0] if whole else None, 'office_address': office, 'keepalive_s': keep, **info}


def public_lines(info: dict) -> List[str]:
    """What the page says of the connect information - no key."""
    out = [f"member {info['name'] or '(unnamed)'}" + (f" ({info['kind']})" if info['kind'] else '') + (f" on {info['network']}" if info['network'] else ''),
           f"this computer: {info['address']}", f"the hub: {info['hub']['endpoint']}" + (f" ({info['hub']['address']} inside)" if info['hub']['address'] else ''),
           f"this computer's public key: {info['public_key']}"]
    if info['office_address']:
        out.append(f"the office: {info['office_address']}")
    if info['reaches_subnet']:
        out.append(f"reaches the whole network {info['reaches_subnet']}")
    out.append('pre-shared key: ' + ('yes' if info['preshared'] else 'no') + f"; keepalive every {info['keepalive_s']} s" if info['keepalive_s'] else 'no keepalive')
    return out
