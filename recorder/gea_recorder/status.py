# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""status - the box's one page, on the pad network (port 8780): recording or not and why, the clock and what its
time is worth, each channel's level, the rig feed's last depth, the disk, the hours closed; and the files for
GEA to pull: /manifest.json, /sensors.csv, /rig/depth.csv, /files/<path>. Read-only: the page has no button.
Standard library; one thread."""
from __future__ import annotations

import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

from . import NAME, __version__

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>{name} - {site}</title><style>body{{font:15px system-ui,sans-serif;margin:16px;color:#1b2430;background:#fff}}table{{border-collapse:collapse;margin:8px 0}}td,th{{border:1px solid #ccc;padding:3px 8px;text-align:left}}
.ok{{color:#1b6e3a;font-weight:600}}.bad{{color:#b03030;font-weight:600}}.muted{{color:#666}}h1{{font-size:20px;margin:0 0 4px}}</style></head><body>
<h1>{name} {version} · {site} · station {station}</h1>
<p class="{cls}">{state}</p>
<p>Clock: <b>{clock_detail}</b> (timing quality {tq}/100{offset}) · Source: {source} · Up {up}</p>
<table><tr><th>Sensor</th><th>Code</th><th>Kind</th><th>Rate</th><th>Current file</th><th>Samples in it</th></tr>{rows}</table>
<p>Rig feed: {rig}</p>
<p>Disk: {free:,.0f} MB free in {dir} · {n_files} files closed ({mb_total:,.0f} MB){mirror}</p>
<p class="muted">Files: <a href="/manifest.json">manifest.json</a> · <a href="/sensors.csv">sensors.csv</a> · <a href="/rig/depth.csv">rig/depth.csv</a> · <a href="/files/">files</a> · <a href="/health">health</a>. This page refreshes every 5 s and has nothing to press: the box records when it has power.</p>
</body></html>"""


def _fmt_up(s: float) -> str:
    s = int(s)
    return f'{s // 86400}d {s % 86400 // 3600}h {s % 3600 // 60}m' if s >= 3600 else f'{s // 60}m {s % 60}s'


class StatusServer:
    def __init__(self, cfg: dict, health: Callable[[], dict], data_dir: str):
        self.cfg = cfg
        self.health = health
        self.data_dir = os.path.abspath(data_dir)
        st = cfg.get('status') or {}
        self.port = int(st.get('port', 8780)); self.bind = str(st.get('bind', '0.0.0.0'))
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def render(self) -> str:
        h = self.health()
        rows = ''.join(f"<tr><td>{html.escape(str(c['sensor_id']))}</td><td>{html.escape(str(c['code']))}</td><td>{html.escape(str(c['kind']))}</td><td>{c['rate']:g}</td>"
                       f"<td class=\"muted\">{html.escape(str(c['file'] or '-'))}</td><td>{c['samples_this_hour']:,}</td></tr>" for c in h['channels'])
        rig = h['rig']
        if not rig['enabled']:
            rig_txt = 'not configured'
        elif rig['connected']:
            last = rig['last'] or {}
            rig_txt = f"connected ({rig['transport']}), {rig['frames']:,} frames; last bit depth {html.escape(str(last.get('bit_depth', '-')))}, hole depth {html.escape(str(last.get('hole_depth', '-')))} at {html.escape(str(last.get('utc', '-')))}"
        else:
            rig_txt = f"<span class=\"bad\">not connected</span> ({html.escape(str(rig.get('error') or 'waiting'))})"
        ck = h['clock']
        return PAGE.format(name=NAME, version=__version__, site=html.escape(str(self.cfg['site'])), station=html.escape(str(self.cfg['station'])),
                           cls='ok' if h['recording'] else 'bad', state=html.escape(h['state']), clock_detail=html.escape(ck['detail']), tq=ck['quality'],
                           offset=(f", offset {ck['offset_s'] * 1000:.3f} ms" if ck.get('offset_s') is not None else ''), source=html.escape(h['source']),
                           up=_fmt_up(h['uptime_s']), rows=rows, rig=rig_txt, free=h['free_mb'], dir=html.escape(self.data_dir), n_files=h['n_files'],
                           mb_total=h['bytes_closed'] / 1048576.0, mirror=(f" · mirror {html.escape(h['mirror'])}" if h.get('mirror') else ''))

    def start(self) -> None:
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                path = unquote(urlparse(self.path).path)
                if path in ('/', '/index.html'):
                    return self._send(200, server.render().encode('utf-8'), 'text/html; charset=utf-8')
                if path == '/health':
                    return self._send(200, json.dumps(server.health(), indent=1).encode(), 'application/json')
                if path in ('/manifest.json', '/sensors.csv', '/rig/depth.csv'):
                    return self._file(path.lstrip('/'))
                if path.startswith('/files/'):
                    rel = path[len('/files/'):]
                    if rel in ('', '/'):
                        return self._listing()
                    return self._file(rel)
                self._send(404, b'not here', 'text/plain')

            def _file(self, rel: str) -> None:
                full = os.path.abspath(os.path.join(server.data_dir, rel))
                if not full.startswith(server.data_dir + os.sep) and full != server.data_dir or not os.path.isfile(full):
                    return self._send(404, b'no such file', 'text/plain')
                ctype = 'application/json' if full.endswith('.json') else 'text/csv' if full.endswith('.csv') else 'text/plain' if full.endswith(('.log', '.sha256')) else 'application/vnd.fdsn.mseed'
                with open(full, 'rb') as f:
                    body = f.read()
                self._send(200, body, ctype)

            def _listing(self) -> None:
                names = []
                for dp, dn, fn in os.walk(server.data_dir):
                    for f in fn:
                        names.append(os.path.relpath(os.path.join(dp, f), server.data_dir).replace(os.sep, '/'))
                names.sort()
                body = ''.join(f'<li><a href="/files/{html.escape(n)}">{html.escape(n)}</a></li>' for n in names)
                self._send(200, f'<!doctype html><meta charset="utf-8"><title>files</title><ul>{body}</ul>'.encode(), 'text/html; charset=utf-8')

        self.httpd = ThreadingHTTPServer((self.bind, self.port), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='status', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown(); self.httpd.server_close()
