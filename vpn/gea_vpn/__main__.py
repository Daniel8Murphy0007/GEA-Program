# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gea-vpn - the command line.

    gea-vpn serve [--port 8795] [--data DIR]                   the page, waiting for the connect information (what the service runs)
    gea-vpn connect FILE [--by NAME] [--data DIR]               the connect information from a zip or a .conf: the tunnel up now and at boot
    gea-vpn disconnect [--data DIR]                             the tunnel down and removed from the boot
    gea-vpn forget [--data DIR]                                 disconnect, and the connect information removed from this computer
    gea-vpn status [--json] [--data DIR]                        how the tunnel is doing
    gea-vpn install [--data DIR] [--port 8795]                  the program as a service (systemd on Linux; a scheduled task as SYSTEM on Windows)
    gea-vpn uninstall-service                                   that service removed (the tunnel is left as it is)
    gea-vpn selftest [--json]                                   the connect information read and refused, the page, the records; exit 0 when right
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from . import NAME, __version__, bundle as B, service as S, wg as W, x25519


def _say(a, obj) -> None:
    print(json.dumps(obj, indent=1) if getattr(a, 'json', False) else obj)


def selftest(as_json: bool = False) -> int:
    """The connect information read from a zip, a file and text, refused when wrong; the records written owner-only;
    the page serving and answering, its actions on a dry client; the status asked of WireGuard, whatever it says."""
    import io
    import urllib.error
    import urllib.request
    import zipfile
    checks = []

    def add(name, ok, detail=''):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    priv, pub = x25519.keypair_b64(); hub_priv, hub_pub = x25519.keypair_b64(); psk = x25519.preshared_b64()
    conf = (f"# GEA-VPN: Pad 3 rig PC (driller) on Pad 3 - made 2026-11-08T00:00:00Z by GEA-Program; keep this file to yourself\n"
            f"[Interface]\nPrivateKey = {priv}\nAddress = 10.77.0.4/32\n\n[Peer]\n# the hub\nPublicKey = {hub_pub}\nPresharedKey = {psk}\n"
            f"Endpoint = vpn.pad3site.com:51820\nAllowedIPs = 10.77.0.1/32, 10.77.0.2/32\nPersistentKeepalive = 25\n")
    info = B.check_conf(conf)
    add('the connect information read', info['name'] == 'Pad 3 rig PC' and info['kind'] == 'driller' and info['address'] == '10.77.0.4' and info['hub']['host'] == 'vpn.pad3site.com'
        and info['hub']['port'] == 51820 and info['hub']['address'] == '10.77.0.1' and info['office_address'] == '10.77.0.2' and info['public_key'] == pub and info['preshared'],
        f"{info['name']} {info['address']} -> {info['hub']['endpoint']}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('gea-vpn-pad-3-rig-pc/gea-vpn.conf', conf); z.writestr('gea-vpn-pad-3-rig-pc/README.txt', 'x')
    z_info = B.read_connect_info(buf.getvalue(), 'gea-vpn-pad-3-rig-pc.zip')
    add('the zip read', z_info['address'] == '10.77.0.4' and z_info['public_key'] == pub)
    refusals = []
    for bad, why in ((conf.replace(priv, 'notakey'), 'key'), (conf.replace('[Peer]', '[Peer]\nPublicKey = ' + hub_pub + '\n[Peer]'), 'peers'),
                     (conf.replace('Endpoint = vpn.pad3site.com:51820', 'Endpoint = vpn.pad3site.com'), 'endpoint'), (conf.replace('10.77.0.4/32', '8.8.8.8/32'), 'public address'),
                     (b'PK\x03\x04junk', 'zip'), ('hello', 'text')):
        try:
            B.read_connect_info(bad if isinstance(bad, bytes) else bad.encode(), 'x.zip' if isinstance(bad, bytes) else 'x.conf'); refusals.append(f'{why}: taken')
        except B.BundleError as e:
            refusals.append(f'{why}: refused ({e})')
    add('wrong information refused', all('refused' in r for r in refusals), '; '.join(r.split(' (')[0] for r in refusals))
    d = tempfile.mkdtemp(prefix='gea-vpn-selftest-')
    cl = S.Client(data_dir=d, dry=True)
    srv = S.Server(cl, port=0); srv.start()
    try:
        with urllib.request.urlopen(srv.url + '/', timeout=5) as r:
            page = r.read().decode('utf-8')
        with urllib.request.urlopen(srv.url + '/live.json', timeout=5) as r:
            live0 = json.load(r)
        add('the page serves and waits', 'GEA-VPN' in page and 'connect information' in page and live0['phase'] in ('waiting', 'no-wireguard') and live0['member'] is None, live0['phase'])
        rq = urllib.request.Request(srv.url + '/api/connect', data=json.dumps({'text': conf, 'by': 'selftest'}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
            urllib.request.urlopen(rq, timeout=5); no_header = False
        except urllib.error.HTTPError as e:
            no_header = e.code == 403
        rq = urllib.request.Request(srv.url + '/api/connect', data=json.dumps({'text': conf, 'by': 'selftest'}).encode(), headers={'Content-Type': 'application/json', 'X-GEA-VPN': '1'}, method='POST')
        with urllib.request.urlopen(rq, timeout=5) as r:
            res = json.load(r)
        with urllib.request.urlopen(srv.url + '/live.json', timeout=5) as r:
            live1 = json.load(r)
        st = os.stat(cl.state.conf)
        add('the page takes the connect information (a request without the page\'s own header refused)', no_header and res['ok'] and res['member']['address'] == '10.77.0.4' and live1['member']['name'] == 'Pad 3 rig PC'
            and live1['phase'] in ('down', 'no-wireguard', 'up', 'no-handshake') and (os.name == 'nt' or (st.st_mode & 0o777) == 0o600), f"phase {live1['phase']}")
        rq = urllib.request.Request(srv.url + '/api/forget', data=b'{"by":"selftest"}', headers={'Content-Type': 'application/json', 'X-GEA-VPN': '1'}, method='POST')
        with urllib.request.urlopen(rq, timeout=5) as r:
            json.load(r)
        with urllib.request.urlopen(srv.url + '/live.json', timeout=5) as r:
            live2 = json.load(r)
        add('forgotten: the information gone and the page waiting again', not os.path.isfile(cl.state.conf) and live2['member'] is None and len(cl.state.tail()) >= 2)
    finally:
        srv.stop()
    wgst = W.status()
    add('WireGuard asked, whatever it says', isinstance(wgst.get('installed'), bool) and 'note' in wgst or wgst.get('up'), wgst.get('note', 'up'))
    ok = all(c['ok'] for c in checks)
    if as_json:
        print(json.dumps({'program': NAME, 'version': __version__, 'ok': ok, 'checks': checks, 'wireguard': {k: v for k, v in wgst.items() if k != 'tools'}}, indent=1))
    else:
        for c in checks:
            print(f"  {'OK  ' if c['ok'] else 'FAIL'} {c['check']}" + (f"  - {c['detail']}" if c['detail'] else ''))
        print(f"{NAME} {__version__} selftest: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


SYSTEMD_UNIT = """[Unit]
Description=GEA-VPN - the client program of the site's private network (the page at 127.0.0.1:{port})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m gea_vpn serve --port {port} --data {data}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_service(data: str, port: int) -> int:
    if W.WINDOWS:
        pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        exe = pyw if os.path.isfile(pyw) else sys.executable
        tr = f'"{exe}" -m gea_vpn serve --port {port} --data "{data}"'
        for cmd in (['schtasks', '/Create', '/TN', 'GEA-VPN', '/SC', 'ONSTART', '/RU', 'SYSTEM', '/RL', 'HIGHEST', '/TR', tr, '/F'], ['schtasks', '/Run', '/TN', 'GEA-VPN']):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"{' '.join(cmd[:3])}: exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}"); return 1
        print(f'GEA-VPN installed as the scheduled task GEA-VPN (SYSTEM, at start-up) and started; the page: http://127.0.0.1:{port}/')
        return 0
    unit = SYSTEMD_UNIT.format(python=sys.executable, port=port, data=data)
    try:
        with open('/etc/systemd/system/gea-vpn.service', 'w', encoding='utf-8') as f:
            f.write(unit)
    except OSError as e:
        print(f'cannot write the unit: {e} (run as root)'); return 1
    for cmd in (['systemctl', 'daemon-reload'], ['systemctl', 'enable', '--now', 'gea-vpn']):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{' '.join(cmd)}: exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}"); return 1
    print(f'GEA-VPN installed as the systemd service gea-vpn and started; the page: http://127.0.0.1:{port}/')
    return 0


def uninstall_service() -> int:
    if W.WINDOWS:
        subprocess.run(['schtasks', '/End', '/TN', 'GEA-VPN'], capture_output=True)
        r = subprocess.run(['schtasks', '/Delete', '/TN', 'GEA-VPN', '/F'], capture_output=True, text=True)
        print('the scheduled task GEA-VPN removed' if r.returncode == 0 else f'schtasks: {(r.stderr or r.stdout).strip()[:200]}')
        return 0
    subprocess.run(['systemctl', 'disable', '--now', 'gea-vpn'], capture_output=True)
    try:
        os.remove('/etc/systemd/system/gea-vpn.service')
    except OSError:
        pass
    subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
    print('the service gea-vpn removed')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='gea-vpn', description=f'{NAME} {__version__} - the client of the site\'s private network')
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('serve'); p.add_argument('--port', type=int, default=S.PORT); p.add_argument('--bind', default='127.0.0.1'); p.add_argument('--data', default=None)
    p = sub.add_parser('connect'); p.add_argument('file'); p.add_argument('--by', default=''); p.add_argument('--data', default=None); p.add_argument('--json', action='store_true')
    for n in ('disconnect', 'forget'):
        p = sub.add_parser(n); p.add_argument('--by', default=''); p.add_argument('--data', default=None); p.add_argument('--json', action='store_true')
    p = sub.add_parser('status'); p.add_argument('--data', default=None); p.add_argument('--json', action='store_true')
    p = sub.add_parser('install'); p.add_argument('--data', default=None); p.add_argument('--port', type=int, default=S.PORT)
    sub.add_parser('uninstall-service')
    p = sub.add_parser('selftest'); p.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd is None:
        ap.print_help(); return 2
    if a.cmd == 'selftest':
        return selftest(a.json)
    if a.cmd == 'install':
        return install_service(a.data or S.default_data_dir(), a.port)
    if a.cmd == 'uninstall-service':
        return uninstall_service()
    cl = S.Client(data_dir=a.data)
    if a.cmd == 'serve':
        return S.run_forever(cl, a.port, a.bind)
    try:
        if a.cmd == 'connect':
            with open(a.file, 'rb') as f:
                data = f.read()
            res = cl.connect(data, os.path.basename(a.file), a.by)
            _say(a, res if a.json else f"connected: {res['member']['name'] or res['member']['address']} -> {res['member']['hub']['endpoint']}; {res['tunnel']['how']}")
            return 0
        if a.cmd == 'disconnect':
            res = cl.disconnect(a.by); _say(a, res if a.json else res['tunnel']['how']); return 0
        if a.cmd == 'forget':
            cl.forget(a.by); _say(a, {'ok': True} if a.json else 'forgotten'); return 0
        if a.cmd == 'status':
            l = cl.live()
            if a.json:
                print(json.dumps(l, indent=1))
            else:
                print(f"{NAME} {__version__}: {l['words']}")
                if l['member']:
                    for line in B.public_lines(l['member']):
                        print('  ' + line)
                print('  ' + l['office']['note'])
            return 0
    except (B.BundleError, RuntimeError, OSError) as e:
        print(f'gea-vpn: {e}', file=sys.stderr)
        return 1
    return 2


if __name__ == '__main__':
    sys.exit(main())
