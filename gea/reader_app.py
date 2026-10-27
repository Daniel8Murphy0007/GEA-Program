# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""reader_app - GEA Reader as a Windows program: the reader page in a window of its own, with its own icon and its
own taskbar button, started from the Start menu, the desktop, or "Check with GEA Reader" on a .zip file.

GEA-Reader-Setup-<version>.exe installs it for the signed-in user (tools/reader_installer.iss, built by
tools/build_reader.py): a small embeddable Python, this file (as app\\gea_reader.py), winident.py beside it, the page
(GEA-Reader.html) and its icon (reader.ico). Nothing of GEA-Program is installed, and nothing reaches the network:
the page is the same static page a site hands out, which checks the dossier inside the browser.

What a start does:
1. writes the page to %LOCALAPPDATA%\\GEA-Reader\\page - with the dossier inside it when one was named on the
   command line (Explorer's "Check with GEA Reader"), because a page opened from disk may read only the files a
   person hands it;
2. opens it in Microsoft Edge's app window (else Chrome's; else the default browser) with a browser profile of its
   own (%LOCALAPPDATA%\\GEA-Reader\\window, no extensions), so fingerprints a client saves stay in the reader;
3. gives that window GEA Reader's taskbar identity - AppUserModelID ENRGYONE.GEAReader, reader.ico, its name and
   its relaunch command - so the taskbar shows the reader's icon, not Edge's (winident.py, as GEA-Program does).

`--selftest` says what a start would do here and checks the page, the icon and the embedding without opening a
window. Standard library only; winident.py, beside this file in the kit, is loaded from its file.
"""
from __future__ import annotations

import base64
import json
import ntpath
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

AUMID = 'ENRGYONE.GEAReader'
NAME = 'GEA Reader'
PAGE = 'GEA-Reader.html'
ICON = 'reader.ico'
MAX_EMBED = 150 * 1048576            # a dossier larger than this is chosen in the window instead (the page holds it twice)
OPEN_MARK = '<script id="gea-core">'


def kit_dir() -> Path:
    """The reader's installation: app\\gea_reader.py sits one folder below it."""
    here = Path(os.path.abspath(__file__)).parent
    return here.parent if here.name.lower() == 'app' else here


def data_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return Path(base) / 'GEA-Reader'


def template_text(kit: Optional[Path] = None) -> str:
    """The page as the kit carries it (GEA-Reader.html, made by the build); inside GEA-Program, made on the spot."""
    p = (kit or kit_dir()) / PAGE
    if p.is_file():
        return p.read_text(encoding='utf-8')
    try:
        from .reader import page as _page             # inside GEA-Program
    except ImportError:
        raise FileNotFoundError(f'{p} is missing: the reader is not installed completely')
    return _page(None)


def page_with(template: str, dossier: Optional[str]) -> Tuple[str, str]:
    """(html, note): the page, with the named dossier inside it, or the page as it is and why the dossier is not."""
    if not dossier:
        return template, ''
    if OPEN_MARK not in template:
        raise ValueError('the reader page has lost its script marker')
    p = Path(dossier)
    name = p.name.replace('"', '').replace('<', '').replace('>', '').replace('&', '')
    note = ''
    try:
        n = p.stat().st_size
    except OSError as e:
        n, note = 0, f'{p.name} could not be opened ({e.strerror or e}); choose it here instead'
    if not note and n > MAX_EMBED:
        note = f'{p.name} is {n / 1048576:,.0f} MB, too large to hand to the window this way; drop it here or choose it'
    if note:
        tag = f'<script id="gea-open" type="application/octet-stream" data-name="{name}" data-note="{note.replace(chr(34), "")}"></script>\n'
    else:
        data = base64.b64encode(p.read_bytes()).decode('ascii')
        tag = f'<script id="gea-open" type="application/octet-stream" data-name="{name}">{data}</script>\n'
    return template.replace(OPEN_MARK, tag + OPEN_MARK, 1), note


def write_page(dossier: Optional[str] = None, kit: Optional[Path] = None, out_dir: Optional[Path] = None) -> Tuple[Path, str]:
    """The page to open: GEA-Reader.html, or open-<id>.html holding the dossier. Pages older than ten minutes are
    removed first (the window has read them long before)."""
    out_dir = out_dir or (data_dir() / 'page')
    out_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in out_dir.glob('open-*.html'):
        try:
            if now - old.stat().st_mtime > 600:
                old.unlink()
        except OSError:
            pass
    html, note = page_with(template_text(kit), dossier)
    out = out_dir / (f'open-{uuid.uuid4().hex[:12]}.html' if dossier else PAGE)
    out.write_text(html, encoding='utf-8')
    return out, note


def browser_candidates() -> List[Tuple[str, str]]:
    env = os.environ
    roots = [env.get('ProgramFiles(x86)'), env.get('ProgramFiles'), env.get('LOCALAPPDATA')]
    out = []
    for label, rel in (('Microsoft Edge', ('Microsoft', 'Edge', 'Application', 'msedge.exe')),
                       ('Google Chrome', ('Google', 'Chrome', 'Application', 'chrome.exe'))):
        for r in roots:
            if r:
                out.append((label, os.path.join(r, *rel)))
    return out


def find_browser() -> Optional[Tuple[str, str]]:
    for label, p in browser_candidates():
        if os.path.isfile(p):
            return label, p
    for label, exe in (('Microsoft Edge', 'msedge'), ('Google Chrome', 'chrome')):
        w = shutil.which(exe)
        if w:
            return label, w
    return None


def window_args(exe: str, page: Path, profile: Path) -> List[str]:
    return [exe, f'--app={page.resolve().as_uri()}', f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--window-size=1200,900']


def say(text: str, error: bool = False) -> None:
    if sys.stdout is not None and getattr(sys.stdout, 'write', None):
        try:
            print(text, flush=True)
            return
        except Exception:
            pass
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, NAME, 0x10 if error else 0x40)
        except Exception:
            pass


def winident():
    """winident.py: the program's own module inside GEA-Program; in the reader's kit, the copy beside this file,
    loaded from that file (the kit's Python has no package to import it from)."""
    try:
        from . import winident as WI              # inside GEA-Program
        return WI
    except ImportError:
        pass
    p = Path(os.path.abspath(__file__)).parent / 'winident.py'
    if not p.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('gea_reader_winident', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def claim(kit: Path, exe: str, claim_window=None) -> int:
    """Give the reader's window its own taskbar identity (and not Edge's): GEA Reader's AppUserModelID, icon,
    name and relaunch command, through winident.py."""
    if claim_window is None:
        WI = winident()
        if WI is None:
            return 0
        claim_window = WI.claim_window
    return claim_window(str(kit), aumid=AUMID, title_prefix=NAME, name=NAME, icon_file=ICON,
                        command_args="-m gea_reader", exe_name=ntpath.basename(exe))


def open_window(dossier: Optional[str] = None) -> int:
    kit = kit_dir()
    page, note = write_page(dossier, kit)
    b = find_browser()
    if b is None:
        os.startfile(str(page)) if os.name == 'nt' else subprocess.Popen(['xdg-open', str(page)])
        return 0
    profile = data_dir() / 'window'
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200 if os.name == 'nt' else 0    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(window_args(b[1], page, profile), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags)
    if os.name == 'nt':
        claim(kit, b[1])
    return 0


def selftest(as_json: bool = False) -> int:
    kit = kit_dir()
    checks = []

    def add(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    try:
        text = template_text(kit)
    except (OSError, FileNotFoundError) as e:
        text = ''
        add('the page', False, str(e))
    else:
        add('the page', OPEN_MARK in text and "default-src 'none'" in text and '__ICON__' not in text, str(kit / PAGE))
    ico = kit / ICON
    add('the icon', ico.is_file() and ico.read_bytes()[:4] == b'\x00\x00\x01\x00', str(ico))
    WI = winident()
    add('the taskbar identity', WI is not None and hasattr(WI, 'claim_window'),
        f'winident.py; AppUserModelID {AUMID}' if WI is not None else 'winident.py is missing beside the program')
    tmp = data_dir() / 'selftest'
    try:
        z = tmp / 'probe.zip'
        tmp.mkdir(parents=True, exist_ok=True)
        z.write_bytes(b'PK\x05\x06' + b'\x00' * 18)
        out, note = write_page(str(z), kit, tmp)
        h = out.read_text(encoding='utf-8')
        add('a dossier handed to the window', not note and 'data-name="probe.zip">UEsFBg' in h and h.index('id="gea-open"') < h.index(OPEN_MARK), str(out))
    except Exception as e:
        add('a dossier handed to the window', False, f'{type(e).__name__}: {e}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    b = find_browser()
    browser = f'{b[0]} ({b[1]})' if b else 'none found: the default browser opens the page, without a window of its own'
    ok = all(c['ok'] for c in checks)
    if as_json:
        print(json.dumps({'ok': ok, 'kit': str(kit), 'browser': browser, 'checks': checks}, indent=1))
    else:
        print(f"{NAME} self-test: {'OK' if ok else 'FAILED'} - {kit}")
        for c in checks:
            print(f"  {'ok' if c['ok'] else 'NO'}  {c['check']}: {c['detail']}")
        print(f'  window: {browser}; profile {data_dir() / "window"}')
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--selftest' in argv:
        return selftest('--json' in argv)
    dossier = next((a for a in argv if not a.startswith('--')), None)
    try:
        return open_window(dossier)
    except Exception as e:                                   # pythonw has no console: say it in a box
        say(f'GEA Reader could not open: {type(e).__name__}: {e}', error=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
