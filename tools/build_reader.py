# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_reader - GEA Reader as a Windows program of its own: the kit GEA-Reader-Setup-<version>.exe installs, and a
portable zip of the same folder.

    python tools/build_reader.py                       # dist/gea-reader-<version>-win64/ and dist/GEA-Reader-portable-<version>.zip
    python tools/build_reader.py --python-zip C:\\dl\\python-3.12.7-embed-amd64.zip   # when python.org is not reachable here

What the kit holds:

    python/            CPython's embeddable distribution (python.org); its ._pth adds ..\\app and nothing else
    app/gea_reader.py  the program (gea/reader_app.py): writes the page, opens it in Edge's app window with a
                       browser profile of its own, and gives the window GEA Reader's taskbar identity
    app/winident.py    the taskbar identity (gea/winident.py)
    GEA-Reader.html    the reader page (gea/web/reader.html, with its icon and version written in: the same page
                       `gea reader` writes without a workspace)
    reader.ico         the reader's icon (gea/icon.py, kind='reader'): a signed page with a green seal
    GEA-Reader.cmd     starts it from the folder, for the portable zip
    README.txt, MANIFEST.json, SHA256SUMS.txt

Nothing of GEA-Program and no third-party package: the reader is the page, and the page needs only a browser.
The build does not import the program (it loads icon.py on its own), so it runs before anything is installed.
Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import build_installer as BI          # noqa: E402  the embeddable Python's download, as the GEA-Program kit gets it

PAGE_SOURCE = ROOT / 'gea' / 'web' / 'reader.html'

LAUNCH_CMD = r'''@echo off
rem GEA Reader, from its own folder: the reader page in a window of its own (a .zip dossier named after it is opened in it)
start "" "%~dp0python\pythonw.exe" -m gea_reader %*
'''

README = '''GEA Reader {version}
=================

Checks a signed GEA dossier - the .zip file a site sends - and shows what it holds: the reports as they were
signed, the declarations, the audit log. It works offline and sends nothing anywhere.

Start it from the Start menu (GEA Reader), the desktop, or right-click a dossier in Explorer and choose
"Check with GEA Reader" (on Windows 11, under "Show more options"). From this folder: GEA-Reader.cmd.

The verdict:
  Valid                    every file is byte-for-byte what the site signed, and the key is one you saved
  Valid - compare the      the files are unchanged; save the site's fingerprint (it gave you one by email, phone
  fingerprint              or contract) and the reader will recognise its dossiers from then on
  Not valid                something was changed, added or removed after signing; the reader says what

What it does not check: the trusted time-stamp a dossier may carry. verify_dossier.py, inside every dossier,
checks it (Python 3.8 or later).

What it keeps: the fingerprints you save, and its window's browser profile, in %LOCALAPPDATA%\\GEA-Reader.
Uninstalling leaves them; delete that folder to remove them.

Files: python\\ (Python's embeddable distribution), app\\gea_reader.py and app\\winident.py (the program),
GEA-Reader.html (the page), reader.ico. SHA256SUMS.txt lists every file's SHA-256.
Licence: Mozilla Public License 2.0. https://github.com/Daniel8Murphy0007/GEA-Program
'''


def icon_module():
    spec = importlib.util.spec_from_file_location('gea_icon', ROOT / 'gea' / 'icon.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_page(version: str) -> str:
    """The reader page as `gea reader` writes it without a workspace: its icon and version written in."""
    html = PAGE_SOURCE.read_text(encoding='utf-8')
    icon = 'data:image/png;base64,' + base64.b64encode(icon_module().png(64, kind='reader')).decode('ascii')
    return html.replace('__ICON__', icon).replace('__VERSION__', version)


def unpack_python(zip_file: Path, pydir: Path) -> None:
    with zipfile.ZipFile(zip_file) as z:
        z.extractall(pydir)
    pth = next(pydir.glob('python*._pth'))
    text = pth.read_text(encoding='utf-8')
    if '..\\app' not in text:
        text = text.rstrip('\n') + '\n..\\app\n'
    pth.write_text(text, encoding='utf-8')


def build(out: Path, py: str, python_zip: Path | None, no_zip: bool = False) -> Path:
    ver = BI.version()
    kit = out / f'gea-reader-{ver}-win64'
    if kit.exists():
        shutil.rmtree(kit)
    (kit / 'app').mkdir(parents=True)
    print(f'== kit {kit.name}')
    print('== embeddable Python')
    unpack_python(BI.fetch_python(py, out, python_zip), kit / 'python')
    shutil.copyfile(ROOT / 'gea' / 'reader_app.py', kit / 'app' / 'gea_reader.py')
    shutil.copyfile(ROOT / 'gea' / 'winident.py', kit / 'app' / 'winident.py')
    (kit / 'GEA-Reader.html').write_text(make_page(ver), encoding='utf-8', newline='\n')
    (kit / 'reader.ico').write_bytes(icon_module().ico_bytes(kind='reader'))
    (kit / 'GEA-Reader.cmd').write_text(LAUNCH_CMD.replace('\n', '\r\n'), encoding='utf-8', newline='')
    (kit / 'README.txt').write_text(README.format(version=ver), encoding='utf-8')
    files = sorted(p for p in kit.rglob('*') if p.is_file())
    (kit / 'SHA256SUMS.txt').write_text(''.join(f'{BI.sha256(p)}  {p.relative_to(kit).as_posix()}\n' for p in files), encoding='utf-8')
    (kit / 'MANIFEST.json').write_text(json.dumps({
        'name': 'gea-reader', 'version': ver, 'platform': 'win64', 'python': py, 'commit': BI.git_commit(),
        'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'n_files': len(files),
        'bytes': sum(p.stat().st_size for p in files),
        'page_sha256': hashlib.sha256((kit / 'GEA-Reader.html').read_bytes()).hexdigest()}, indent=1), encoding='utf-8')
    print(f'== {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.1f} MB')
    if not no_zip:
        z = out / f'GEA-Reader-portable-{ver}.zip'
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(q for q in kit.rglob('*') if q.is_file()):
                zf.write(p, f'GEA Reader/{p.relative_to(kit).as_posix()}')
        print(f'== {z} ({z.stat().st_size / 1e6:.1f} MB)')
    return kit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='build GEA Reader, the Windows program a site gives its clients')
    ap.add_argument('--python', default=BI.PY_DEFAULT, help='CPython version of the embeddable distribution')
    ap.add_argument('--python-zip', default=None, help='a downloaded python-<ver>-embed-amd64.zip, when python.org is not reachable from here')
    ap.add_argument('--out', default=str(ROOT / 'dist'))
    ap.add_argument('--no-zip', action='store_true')
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    build(out, a.python, Path(a.python_zip) if a.python_zip else None, a.no_zip)
    return 0


if __name__ == '__main__':
    sys.exit(main())
