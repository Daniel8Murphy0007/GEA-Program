# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""reader - GEA Reader, the program a site gives its clients: one HTML file that checks a signed dossier and shows
what it holds, in any current browser, offline, with nothing to install.

A client who receives a dossier has had one way to check it: `verify_dossier.py`, inside the dossier, which needs
Python. GEA Reader does the same checks in the browser - the Ed25519 signature over manifest.json (RFC 8032, its own
arithmetic, tested against the RFC's vectors every time it opens), every file against the size and SHA-256 it was
signed with, nothing unsigned riding along, the key hand-overs - with the same names, in the same order, and then
lets the client read the reports as they were signed (an HTML report in a sandboxed frame that runs no script, a
PDF in the browser's own viewer, a table or a log as text) and save a copy. It reads the zip the client chooses and
nothing else: the page's own Content-Security-Policy allows no network request of any kind.

What it does not check, and says so on the page: the RFC 3161 time-stamp a dossier may carry (that needs the
authority's certificates and the computer's trusted roots; verify_dossier.py checks it). A valid signature shows the
files are byte-for-byte what the key's holder signed; who holds the key is answered by the fingerprint. A reader made
from a workspace (the Reports tab, or `gea reader --workspace`) carries the site's name and its signing keys'
fingerprints - the current one and every retired one in the key history, public halves only - so a client sees at
once whether a dossier is signed with the site's key, or with a newer key the site's key handed over to.

Standard library only.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Optional

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'reader.html')
FILENAME = 'GEA-Reader.html'
SITE_MARK = '<script id="gea-site" type="application/json">null</script>'


def site_info(ws) -> Optional[dict]:
    """The site's name and the fingerprints of every key it has signed with (public, never the seed)."""
    if ws is None:
        return None
    from . import dossier as DZ
    fps = []
    cur = DZ.key_info(ws)
    for k in DZ.key_history(ws).get('keys', []):
        if k.get('fingerprint') and k['fingerprint'] not in fps:
            fps.append(k['fingerprint'])
    if cur and cur['fingerprint'] not in fps:
        fps.append(cur['fingerprint'])
    return {'name': ws.manifest.get('name') or '', 'fingerprints': fps, 'current': cur['fingerprint'] if cur else None,
            'made_utc': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}


def _json_for_script(obj) -> str:
    """JSON safe inside a <script> element: no '<', '>' or '&' that could close it or start markup."""
    return json.dumps(obj, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def page(ws=None, version: Optional[str] = None) -> str:
    """The reader as one HTML file; with a workspace, the site's name and key fingerprints are written into it."""
    from . import __version__
    from . import icon as IC
    with open(SOURCE, encoding='utf-8') as f:
        html = f.read()
    if SITE_MARK not in html:
        raise RuntimeError('reader.html has lost its site marker')
    icon = 'data:image/png;base64,' + base64.b64encode(IC.png(64, kind='reader')).decode('ascii')
    html = html.replace('__ICON__', icon).replace('__VERSION__', version or __version__)
    info = site_info(ws)
    if info is not None:
        html = html.replace(SITE_MARK, '<script id="gea-site" type="application/json">' + _json_for_script(info) + '</script>')
    return html


def write(out: str, ws=None) -> dict:
    html = page(ws)
    if os.path.isdir(out):
        out = os.path.join(out, FILENAME)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html)
    info = site_info(ws)
    if ws is not None:
        ws.audit('cli', 'reader.write', {'path': os.path.abspath(out), 'fingerprints': (info or {}).get('fingerprints', [])})
    return {'path': os.path.abspath(out), 'bytes': len(html.encode('utf-8')), 'site': info}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog='gea reader', description='Write GEA Reader: the one-file program your clients open to check a dossier and read it.')
    ap.add_argument('--out', default=FILENAME, help='the file (or folder) to write, GEA-Reader.html by default')
    ap.add_argument('--workspace', default='', help="a site workspace: the reader then names the site and knows its signing keys' fingerprints")
    a = ap.parse_args(argv)
    ws = None
    if a.workspace:
        from .workspace import Workspace, WorkspaceError
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f'gea reader: {e}')
    r = write(a.out, ws)
    s = r['site']
    print(f"GEA Reader: {r['path']} ({r['bytes']:,} bytes)")
    if s:
        print(f"  made for {s['name'] or 'the site'}; it knows {len(s['fingerprints'])} signing key(s): " + ', '.join(s['fingerprints'])
              if s['fingerprints'] else f"  made for {s['name'] or 'the site'}; the site has no signing key yet (the first dossier makes one), so the reader asks the client for a fingerprint")
    else:
        print('  no workspace given: the reader asks the client for the fingerprint the site gave them')
    print('  give it to clients once, by any channel; give them the fingerprint separately (email, phone or contract)')
    return 0
