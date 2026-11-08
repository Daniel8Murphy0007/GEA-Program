# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_vpn - GEA-VPN, the client program of the site's private network, as the kits a person installs:

    python tools/build_vpn.py                 # dist/GEA-VPN-<version>.tar.gz: the Linux kit (the wheel, the source, install.sh, README)
    python tools/build_vpn.py --win64         # dist/gea-vpn-<version>-win64/: the folder GEA-VPN-Setup-<version>.exe installs (Inno Setup, tools/vpn_installer.iss)
    python tools/build_vpn.py --win64 --python-zip C:\\dl\\python-3.12.7-embed-amd64.zip   # when python.org is not reachable here

The Windows kit holds python/ (CPython's embeddable distribution, its ._pth adding ..\\app), app/gea_vpn/ (the
program), wireguard/wireguard-amd64-<ver>.msi (WireGuard for Windows' own installer, fetched from
download.wireguard.com when reachable - the build workflow checks its Authenticode signature is WireGuard LLC's
before the setup is made; without it the setup says to install WireGuard from wireguard.com), vpn.ico, GEA-VPN.url,
README.txt, MANIFEST.json, SHA256SUMS.txt. The self-test runs on the built program before any archive is made.
Stdlib only; `pip wheel` for the wheel."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'vpn'
sys.path.insert(0, str(ROOT / 'tools'))
import build_installer as BI          # noqa: E402  the embeddable Python's download, as the GEA-Program kit gets it

WG_VERSION = '0.5.3'
WG_MSI = f'wireguard-amd64-{WG_VERSION}.msi'
WG_URL = f'https://download.wireguard.com/windows-client/{WG_MSI}'

URL_SHORTCUT = '[InternetShortcut]\r\nURL=http://127.0.0.1:8795/\r\n'

README_WIN = '''GEA-VPN {version}
=============

The client of the site's private network. Installed, it waits for the connect information the office makes in
GEA-Program (Administration, GEA-VPN, the member's zip): open GEA-VPN from the Start menu (the page at
http://127.0.0.1:8795/), drop the zip on it, press Connect. The tunnel is a Windows service from then on, up at
every start; the page says when the hub answered and whether the office answers through it.

The tunnel is WireGuard for Windows (wireguard.com). The setup installs it when the computer has none - the
official installer, {msi}, travels in the wireguard folder. The program runs as the scheduled task GEA-VPN (as
SYSTEM, at start-up), because installing a tunnel service needs administrator rights and a person should not.

What it keeps, under %ProgramData%\\GEA-VPN (administrators only): gea-vpn.conf (the connect information as given;
it holds this computer's key), state.json (what it says of the member; no key), log.txt (what was done).
Uninstalling removes the program, the task and the tunnel service, and leaves that folder.

Files: python\\ (Python's embeddable distribution), app\\gea_vpn\\ (the program), wireguard\\ (WireGuard's installer,
when it was reachable at build), vpn.ico, GEA-VPN.url. SHA256SUMS.txt lists every file's SHA-256.
Licence: Mozilla Public License 2.0. https://github.com/Daniel8Murphy0007/GEA-Program
'''


def version() -> str:
    for line in (SRC / 'gea_vpn' / '__init__.py').read_text(encoding='utf-8').splitlines():
        if line.startswith('__version__'):
            return line.split('"')[1]
    raise SystemExit('no __version__ in vpn/gea_vpn/__init__.py')


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def icon_module():
    spec = importlib.util.spec_from_file_location('gea_icon', ROOT / 'gea' / 'icon.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def selftest(python: str, cwd: Path, env_path: Path) -> None:
    import os
    env = dict(os.environ); env['PYTHONPATH'] = str(env_path)
    r = subprocess.run([python, '-m', 'gea_vpn', 'selftest', '--json'], cwd=str(cwd), capture_output=True, text=True, env=env)
    res = json.loads(r.stdout or '{}') if r.returncode in (0, 1) else {}
    if r.returncode != 0 or not res.get('ok'):
        print(r.stdout); print(r.stderr); raise SystemExit('the GEA-VPN self-test failed on the built program')
    print(f"== self-test OK: {', '.join(c['check'] for c in res['checks'])}")


def sums(kit: Path, extra: dict) -> None:
    for pc in kit.rglob('__pycache__'):
        shutil.rmtree(pc, ignore_errors=True)
    files = sorted(p for p in kit.rglob('*') if p.is_file())
    (kit / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.relative_to(kit).as_posix()}\n' for p in files), encoding='utf-8')
    (kit / 'MANIFEST.json').write_text(json.dumps({'name': 'gea-vpn', 'version': version(), 'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                   'commit': BI.git_commit(), 'n_files': len(files) + 1, 'bytes': sum(p.stat().st_size for p in files), **extra}, indent=1), encoding='utf-8')


def build_linux(out: Path, no_selftest: bool = False, no_wheel: bool = False) -> Path:
    ver = version()
    kit = out / f'gea-vpn-{ver}'
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)
    print(f'== GEA-VPN {ver} (Linux kit)')
    if no_wheel or importlib.util.find_spec('pip') is None:
        print('   (no pip here: the kit carries the source and install.sh installs from it; the wheel is left out)')
    else:
        # the wheel from a copy of the source, so the build leaves nothing in the checkout
        import tempfile
        with tempfile.TemporaryDirectory(prefix='gea-vpn-wheel-') as td:
            cp = Path(td) / 'vpn'
            shutil.copytree(SRC, cp, ignore=shutil.ignore_patterns('__pycache__', 'build', '*.egg-info'))
            subprocess.run([sys.executable, '-m', 'pip', 'wheel', str(cp), '--no-deps', '-w', str(kit), '-q'], check=True)
    shutil.copytree(SRC / 'gea_vpn', kit / 'gea_vpn', ignore=shutil.ignore_patterns('__pycache__'))
    for n in ('pyproject.toml', 'README.md', 'install.sh'):
        shutil.copyfile(SRC / n, kit / n)
    if not no_selftest:
        selftest(sys.executable, kit, kit)
    sums(kit, {'platform': 'linux', 'wheel': next((p.name for p in kit.glob('gea_vpn-*.whl')), None)})
    tgz = out / f'GEA-VPN-{ver}.tar.gz'
    with tarfile.open(tgz, 'w:gz') as tf:
        tf.add(kit, arcname=kit.name)
    print(f'== {tgz} ({tgz.stat().st_size / 1e6:.2f} MB)')
    return tgz


def fetch_wireguard(out: Path, wg_msi: Path | None) -> Path | None:
    dst = out / WG_MSI
    if wg_msi and wg_msi.is_file():
        shutil.copyfile(wg_msi, dst); return dst
    if dst.is_file() and dst.stat().st_size > 1 << 20:
        return dst
    try:
        print(f'== WireGuard for Windows {WG_VERSION} from {WG_URL}')
        with urllib.request.urlopen(WG_URL, timeout=60) as r, open(dst, 'wb') as f:
            shutil.copyfileobj(r, f)
        return dst
    except Exception as e:
        print(f'   not fetched ({type(e).__name__}: {e}): the setup will say to install WireGuard from wireguard.com')
        try:
            dst.unlink()
        except OSError:
            pass
        return None


def build_win64(out: Path, py: str, python_zip: Path | None, wg_msi: Path | None, no_selftest: bool = False) -> Path:
    ver = version()
    kit = out / f'gea-vpn-{ver}-win64'
    if kit.exists():
        shutil.rmtree(kit)
    (kit / 'app').mkdir(parents=True)
    print(f'== GEA-VPN {ver} (Windows kit {kit.name})')
    print('== embeddable Python')
    with zipfile.ZipFile(BI.fetch_python(py, out, python_zip)) as z:
        z.extractall(kit / 'python')
    pth = next((kit / 'python').glob('python*._pth'))
    text = pth.read_text(encoding='utf-8')
    if '..\\app' not in text:
        text = text.rstrip('\n') + '\n..\\app\n'
    pth.write_text(text, encoding='utf-8')
    shutil.copytree(SRC / 'gea_vpn', kit / 'app' / 'gea_vpn', ignore=shutil.ignore_patterns('__pycache__'))
    msi = fetch_wireguard(out, wg_msi)
    if msi:
        (kit / 'wireguard').mkdir()
        shutil.copyfile(msi, kit / 'wireguard' / WG_MSI)
    (kit / 'vpn.ico').write_bytes(icon_module().ico_bytes(kind='vpn'))
    (kit / 'GEA-VPN.url').write_text(URL_SHORTCUT, encoding='utf-8', newline='')
    (kit / 'README.txt').write_text(README_WIN.format(version=ver, msi=WG_MSI), encoding='utf-8')
    if not no_selftest:
        selftest(sys.executable, kit, kit / 'app')          # the program as the kit carries it, on this Python (the embeddable one runs on Windows only)
    sums(kit, {'platform': 'win64', 'python': py, 'wireguard_msi': (WG_MSI if msi else None), 'wireguard_sha256': (sha256(msi) if msi else None)})
    print(f"== {sum(1 for p in kit.rglob('*') if p.is_file())} files, {sum(p.stat().st_size for p in kit.rglob('*') if p.is_file()) / 1e6:.1f} MB")
    return kit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='build GEA-VPN, the client program of the site\'s private network')
    ap.add_argument('--out', default=str(ROOT / 'dist'))
    ap.add_argument('--win64', action='store_true', help='the Windows kit folder (the setup is made from it by Inno Setup) instead of the Linux tar.gz')
    ap.add_argument('--python', default=BI.PY_DEFAULT, help='CPython version of the embeddable distribution (win64)')
    ap.add_argument('--python-zip', default=None, help='a downloaded python-<ver>-embed-amd64.zip, when python.org is not reachable from here')
    ap.add_argument('--wireguard-msi', default=None, help=f'a downloaded {WG_MSI}, when download.wireguard.com is not reachable from here')
    ap.add_argument('--no-selftest', action='store_true')
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.win64:
        build_win64(out, a.python, Path(a.python_zip) if a.python_zip else None, Path(a.wireguard_msi) if a.wireguard_msi else None, a.no_selftest)
    else:
        build_linux(out, a.no_selftest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
