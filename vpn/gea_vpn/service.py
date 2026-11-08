# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""service - GEA-VPN running on this computer: the page at http://127.0.0.1:8795/ that waits for the connect
information, takes it (the member's zip dropped on the page, or its text pasted), brings the tunnel up through
WireGuard and keeps it up from boot, and says how it is doing - the hub's last handshake, whether the office
answers through the tunnel; and the records it keeps: the configuration it was given (owner-only; it holds this
computer's key), what it says of the member, and a log of what was done. Standard library; one thread for the page."""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from . import NAME, __version__, bundle as B, wg as W

PORT = 8795
OFFICE_PORT = 8765


def default_data_dir() -> str:
    if W.WINDOWS:
        return os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'GEA-VPN')
    return '/var/lib/gea-vpn'


def _utc() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class State:
    """What the program keeps under its data folder."""

    def __init__(self, data_dir: str):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.conf = os.path.join(self.dir, f'{B.IFACE}.conf')
        self.state_file = os.path.join(self.dir, 'state.json')
        self.log_file = os.path.join(self.dir, 'log.txt')
        self.lock = threading.Lock()

    def load(self) -> dict:
        try:
            with open(self.state_file, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, st: dict) -> None:
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.state_file)

    def log(self, line: str) -> None:
        with self.lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f'{_utc()}  {line}\n')

    def tail(self, n: int = 30) -> list:
        try:
            with open(self.log_file, encoding='utf-8') as f:
                return f.read().splitlines()[-n:]
        except OSError:
            return []

    def write_conf(self, text: str) -> str:
        fd = os.open(self.conf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        try:
            os.chmod(self.conf, 0o600)
        except OSError:
            pass
        return self.conf

    def forget_conf(self) -> None:
        try:
            os.remove(self.conf)
        except OSError:
            pass


class Client:
    """The actions and the live picture."""

    def __init__(self, data_dir: Optional[str] = None, office_port: int = OFFICE_PORT, dry: bool = False):
        self.state = State(data_dir or default_data_dir())
        self.office_port = int(office_port)
        self.dry = bool(dry)                      # the self-test: everything but the call to WireGuard
        self.t_started = time.time()

    # -- the actions ----------------------------------------------------------------------------------------------------
    def connect(self, data: bytes, filename: str = '', by: str = '') -> dict:
        info = B.read_connect_info(data, filename)
        public = {k: v for k, v in info.items() if k != 'conf'}
        path = self.state.write_conf(info['conf'])
        r = {'ok': True, 'how': 'dry run: WireGuard not called'} if self.dry else W.install_tunnel(path)
        st = self.state.load()
        st.update({'member': public, 'source': os.path.basename(filename) or 'pasted text', 'connected_utc': _utc(), 'by': by or '', 'tunnel': r,
                   'program': NAME, 'version': __version__})
        self.state.save(st)
        self.state.log(f"connect: {public['name'] or '(unnamed)'} {public['address']} -> hub {public['hub']['endpoint']} from {st['source']}" + (f' by {by}' if by else '')
                       + f" :: {'ok - ' + r.get('how', '') if r.get('ok') else 'FAILED - ' + r.get('error', '')}")
        if not r.get('ok'):
            raise RuntimeError(r.get('error', 'the tunnel could not be installed'))
        return {'ok': True, 'member': public, 'tunnel': r}

    def disconnect(self, by: str = '') -> dict:
        r = {'ok': True, 'how': 'dry run: WireGuard not called'} if self.dry else W.uninstall_tunnel()
        st = self.state.load()
        st['disconnected_utc'] = _utc(); st['tunnel'] = r
        self.state.save(st)
        self.state.log(f"disconnect{(' by ' + by) if by else ''} :: {r.get('how', '')}")
        return {'ok': True, 'tunnel': r}

    def forget(self, by: str = '') -> dict:
        self.disconnect(by)
        self.state.forget_conf()
        self.state.save({'program': NAME, 'version': __version__, 'forgotten_utc': _utc()})
        self.state.log(f"forget: the connect information removed from this computer{(' by ' + by) if by else ''}")
        return {'ok': True}

    # -- the picture ---------------------------------------------------------------------------------------------------
    def probe_office(self, address: Optional[str]) -> dict:
        if not address:
            return {'reachable': None, 'note': 'this member is not told the office\'s address'}
        try:
            with socket.create_connection((address, self.office_port), timeout=2.0):
                return {'reachable': True, 'note': f'the office answers at {address}:{self.office_port} through the tunnel'}
        except OSError as e:
            return {'reachable': False, 'note': f'{address}:{self.office_port} does not answer ({e.strerror or e})'}

    def live(self, probes: bool = True) -> dict:
        st = self.state.load()
        wgst = W.status()
        member = st.get('member')
        have_conf = os.path.isfile(self.state.conf)
        if not wgst['installed']:
            phase, words = 'no-wireguard', f"WireGuard is not installed on this computer: install it from {wgst['install_from']}, then come back here"
        elif not member or not have_conf:
            phase, words = 'waiting', 'ready for the connect information: the office makes it in GEA-Program (Administration, GEA-VPN, the member\'s zip) - drop it here or paste its text'
        elif wgst.get('up') and wgst.get('talking'):
            net_name = member.get('network') or "the site's network"
            phase, words = 'up', f"connected: {member['name'] or 'this member'} is {member['address']} on {net_name}; " + wgst['note']
        elif wgst.get('up'):
            phase, words = 'no-handshake', wgst['note']
        else:
            phase, words = 'down', 'the connect information is here but the tunnel is not up: ' + wgst.get('note', '') + ' - Connect again, or Disconnect and Forget to start over'
        office = self.probe_office(member.get('office_address')) if (probes and member and wgst.get('up')) else {'reachable': None, 'note': 'not probed' if probes else 'not probed'}
        return {'program': NAME, 'version': __version__, 'phase': phase, 'words': words, 'wireguard': {k: v for k, v in wgst.items() if k != 'tools'}, 'tools': wgst['tools'],
                'member': member, 'source': st.get('source'), 'connected_utc': st.get('connected_utc'), 'office': office, 'data_dir': self.state.dir, 'log': self.state.tail(),
                'uptime_s': time.time() - self.t_started, 'windows': W.WINDOWS, 'utc': _utc()}


# -- the page ---------------------------------------------------------------------------------------------------------------

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEA-VPN</title><style>
:root{--bg:#0f1419;--panel:#1a222c;--ink:#e8edf2;--muted:#8fa0b0;--good:#2ec27e;--warn:#f5a623;--bad:#ff5c5c;--accent:#4aa3ff;--grid:#2b3744}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,Segoe UI,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:10px 16px;border-bottom:1px solid var(--grid)}header h1{font-size:18px;margin:0}
.state{font-weight:700;padding:4px 12px;border-radius:6px;background:#18352a;color:var(--good)}.state.bad{background:#3a1c1c;color:var(--bad)}.state.warn{background:#3a2f12;color:var(--warn)}.state.wait{background:#1c2a3a;color:var(--accent)}
main{max-width:900px;margin:0 auto;padding:12px}.panel{background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:14px;margin-bottom:12px}.panel h2{font-size:14px;margin:0 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.words{font-size:18px;line-height:1.4;margin:4px 0 10px}.muted{color:var(--muted)}.small{font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}.tile{background:#121a22;border:1px solid var(--grid);border-radius:8px;padding:8px}.tile .v{font-size:18px;font-weight:700;word-break:break-all}.tile .k{color:var(--muted);font-size:12px}.tile.bad .v{color:var(--bad)}.tile.good .v{color:var(--good)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--grid);background:#223040;color:var(--ink);cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#06101a;font-weight:700}button.danger{background:#5a1f1f;border-color:#7a2b2b}button:disabled{opacity:.5;cursor:default}
input,textarea{font:inherit;padding:7px 9px;border-radius:6px;border:1px solid var(--grid);background:#0b1015;color:var(--ink)}textarea{width:100%;min-height:120px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.drop{border:2px dashed var(--grid);border-radius:10px;padding:22px;text-align:center;color:var(--muted)}.drop.over{border-color:var(--accent);color:var(--ink)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}pre{background:#0b1015;border:1px solid var(--grid);border-radius:8px;padding:8px;font-size:12px;overflow:auto;max-height:220px;margin:0}
#toast{position:fixed;right:16px;bottom:16px;background:#223040;padding:10px 14px;border-radius:8px;display:none;max-width:60%}
</style></head><body>
<header><h1>GEA-VPN</h1><span class="state wait" id="state">…</span><span style="flex:1"></span><span class="muted small">__VERSION__ · this computer only (127.0.0.1)</span></header>
<main>
 <div class="panel"><div class="words" id="words">…</div><div class="tiles" id="tiles"></div></div>
 <div class="panel" id="connectbox"><h2>The connect information</h2>
  <p class="small muted">The office makes it in GEA-Program: Administration, the GEA-VPN card, add this computer as a member, download its zip (<code>gea-vpn-&lt;member&gt;.zip</code>). It holds this computer's own key: take it by a channel of its own, and nothing else by the same channel. Drop the zip here, choose it, or paste the text of <code>gea-vpn.conf</code>.</p>
  <div class="drop" id="drop">drop the zip or the .conf here<br><span class="small">or</span><br><input type="file" id="file" accept=".zip,.conf,.txt"></div>
  <div class="row" style="margin-top:10px"><textarea id="text" placeholder="…or paste the text of gea-vpn.conf here"></textarea></div>
  <div class="row"><input id="who" placeholder="your name" style="width:160px"><button class="primary" id="connect">Connect</button><span class="small muted">brings the tunnel up now and at every start of this computer</span></div>
 </div>
 <div class="panel" id="membox" style="display:none"><h2>This member</h2><pre id="member"></pre>
  <div class="row"><input id="who2" placeholder="your name" style="width:160px"><button id="disconnect">Disconnect</button><button class="danger" id="forget">Disconnect and forget</button><span class="small muted">forget removes the connect information from this computer; the office removes the member and makes a new zip</span></div>
 </div>
 <div class="panel"><h2>What was done</h2><pre id="log"></pre></div>
 <p class="small muted" id="foot"></p>
</main><div id="toast"></div>
<script>
const $=s=>document.querySelector(s);let L=null;const who=localStorage.getItem('gea-vpn-who')||'';$('#who').value=who;$('#who2').value=who;
function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',6000)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function post(u,body,isForm){const r=await fetch(u,{method:'POST',headers:isForm?{'X-GEA-VPN':'1'}:{'Content-Type':'application/json','X-GEA-VPN':'1'},body:isForm?body:JSON.stringify(body||{})});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
function tile(k,v,cls){return `<div class="tile ${cls||''}"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`}
function render(l){L=l;const st=$('#state');const cls={up:'',waiting:'wait','no-wireguard':'bad',down:'bad','no-handshake':'warn'}[l.phase]||'';st.className='state '+cls;
 st.textContent={up:'connected',waiting:'ready for the connect information','no-wireguard':'WireGuard missing',down:'tunnel down','no-handshake':'no answer from the hub'}[l.phase]||l.phase;
 $('#words').textContent=l.words;const w=l.wireguard,m=l.member,h=w.hub||{};
 $('#tiles').innerHTML=tile('WireGuard',w.installed?'installed':'not installed',w.installed?'good':'bad')+tile('tunnel',w.up?'up':'not up',w.up?'good':(m?'bad':''))
  +(m?tile('this computer',m.address):'')+(m&&m.hub?tile('the hub',m.hub.endpoint):'')
  +(w.up?tile('last handshake',h.latest_handshake_utc?(h.handshake_age_s<180?Math.round(h.handshake_age_s)+' s ago':Math.round(h.handshake_age_s/60)+' min ago'):'never',w.talking?'good':'bad'):'')
  +(w.up?tile('received / sent',Math.round((h.rx_bytes||0)/1024)+' / '+Math.round((h.tx_bytes||0)/1024)+' KB'):'')
  +(m&&m.office_address?tile('the office '+m.office_address,l.office.reachable==null?'—':(l.office.reachable?'answers':'no answer'),l.office.reachable==null?'':(l.office.reachable?'good':'bad')):'');
 $('#connectbox').style.display=(l.phase==='waiting'||l.phase==='no-wireguard'||l.phase==='down')?'':'none';
 $('#membox').style.display=m?'':'none';
 if(m){$('#member').textContent=[`${m.name||'(unnamed)'}${m.kind?' ('+m.kind+')':''}${m.network?' on '+m.network:''}`,`this computer: ${m.address}`,`the hub: ${m.hub.endpoint}${m.hub.address?' ('+m.hub.address+' inside)':''}`,`this computer's public key: ${m.public_key}`,m.office_address?`the office: ${m.office_address}`:'',m.reaches_subnet?`reaches the whole network ${m.reaches_subnet}`:'',`pre-shared key: ${m.preshared?'yes':'no'}; keepalive every ${m.keepalive_s} s`,`connected ${l.connected_utc||''} from ${l.source||''}`].filter(Boolean).join('\n')}
 $('#log').textContent=(l.log||[]).join('\n')||'(nothing yet)';
 $('#foot').textContent=`${l.program} ${l.version} · the records under ${l.data_dir} · the configuration it was given is kept there owner-only; it holds this computer's key. ${l.windows?'The tunnel is a Windows service (WireGuardTunnel$gea-vpn) that starts with the computer.':'The tunnel is wg-quick@gea-vpn under systemd.'}`}
async function tick(){try{const r=await fetch('/live.json');render(await r.json())}catch(e){$('#state').className='state bad';$('#state').textContent='no answer from the program'}}
async function sendFile(f){const fd=new FormData();fd.append('file',f,f.name);fd.append('by',$('#who').value);try{const j=await post('/api/connect',fd,true);toast('connected: '+(j.member.name||j.member.address));tick()}catch(e){toast(e.message)}}
$('#file').onchange=()=>{if($('#file').files[0])sendFile($('#file').files[0])};
const dz=$('#drop');dz.ondragover=e=>{e.preventDefault();dz.classList.add('over')};dz.ondragleave=()=>dz.classList.remove('over');dz.ondrop=e=>{e.preventDefault();dz.classList.remove('over');if(e.dataTransfer.files[0])sendFile(e.dataTransfer.files[0])};
$('#connect').onclick=async()=>{localStorage.setItem('gea-vpn-who',$('#who').value);const t=$('#text').value.trim();if(!t)return toast('drop the zip, choose it, or paste the text first');try{const j=await post('/api/connect',{text:t,by:$('#who').value});toast('connected: '+(j.member.name||j.member.address));$('#text').value='';tick()}catch(e){toast(e.message)}};
$('#disconnect').onclick=async()=>{try{await post('/api/disconnect',{by:$('#who2').value});toast('disconnected');tick()}catch(e){toast(e.message)}};
$('#forget').onclick=async()=>{if(!confirm('Disconnect and remove the connect information from this computer? The office will have to make a new zip.'))return;try{await post('/api/forget',{by:$('#who2').value});toast('forgotten');tick()}catch(e){toast(e.message)}};
tick();setInterval(tick,5000);
</script></body></html>"""


def _multipart(body: bytes, ctype: str) -> dict:
    """The one file and the fields of a multipart/form-data body, without cgi."""
    m = re.search(r'boundary=("?)([^";]+)\1', ctype)
    if not m:
        raise ValueError('no boundary')
    bnd = ('--' + m.group(2)).encode()
    out = {'fields': {}, 'file': None, 'filename': ''}
    for part in body.split(bnd)[1:]:
        if part.strip() in (b'', b'--'):
            continue
        head, _, data = part.partition(b'\r\n\r\n')
        data = data[:-2] if data.endswith(b'\r\n') else data
        h = head.decode('utf-8', 'replace')
        nm = re.search(r'name="([^"]*)"', h); fn = re.search(r'filename="([^"]*)"', h)
        if fn:
            out['file'] = data; out['filename'] = fn.group(1)
        elif nm:
            out['fields'][nm.group(1)] = data.decode('utf-8', 'replace')
    return out


class Server:
    def __init__(self, client: Client, port: int = PORT, bind: str = '127.0.0.1'):
        self.client = client; self.port = int(port); self.bind = bind
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        cl = self.client; srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = 'application/json'):
                self.send_response(code)
                self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('X-Frame-Options', 'DENY')
                self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                try:
                    if path == '/':
                        return self._send(200, PAGE.replace('__VERSION__', __version__).encode('utf-8'), 'text/html; charset=utf-8')
                    if path == '/live.json':
                        return self._send(200, json.dumps(cl.live()).encode('utf-8'))
                    if path == '/health':
                        l = cl.live(probes=False)
                        return self._send(200, json.dumps({'program': NAME, 'version': __version__, 'phase': l['phase'], 'up': l['wireguard'].get('up', False), 'member': (l['member'] or {}).get('address')}).encode('utf-8'))
                    return self._send(404, b'{"error":"no such page"}')
                except Exception as e:
                    return self._send(500, json.dumps({'error': f'{type(e).__name__}: {e}'}).encode('utf-8'))

            def do_POST(self):
                path = urlparse(self.path).path
                # a page from this computer only: the header a browser form of another site cannot set, and no foreign origin
                origin = self.headers.get('Origin') or ''
                if self.headers.get('X-GEA-VPN') != '1' or (origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost')):
                    return self._send(403, b'{"error":"the page\'s own requests only"}')
                n = int(self.headers.get('Content-Length') or 0)
                if n > 4 << 20:
                    return self._send(413, b'{"error":"too large"}')
                raw = self.rfile.read(n) if n else b''
                ctype = self.headers.get('Content-Type') or ''
                try:
                    if ctype.startswith('multipart/form-data'):
                        mp = _multipart(raw, ctype); body = {'file': mp['file'], 'filename': mp['filename'], **mp['fields']}
                    else:
                        body = json.loads(raw or b'{}')
                except ValueError as e:
                    return self._send(400, json.dumps({'error': f'bad request: {e}'}).encode('utf-8'))
                try:
                    if path == '/api/connect':
                        if body.get('file') is not None:
                            res = cl.connect(body['file'], body.get('filename') or '', str(body.get('by') or ''))
                        elif body.get('text'):
                            res = cl.connect(str(body['text']).encode('utf-8'), 'pasted.conf', str(body.get('by') or ''))
                        else:
                            return self._send(400, b'{"error":"no connect information: a zip, a .conf file, or its text"}')
                        return self._send(200, json.dumps(res).encode('utf-8'))
                    if path == '/api/disconnect':
                        return self._send(200, json.dumps(cl.disconnect(str(body.get('by') or ''))).encode('utf-8'))
                    if path == '/api/forget':
                        return self._send(200, json.dumps(cl.forget(str(body.get('by') or ''))).encode('utf-8'))
                    return self._send(404, b'{"error":"no such action"}')
                except (B.BundleError, ValueError, RuntimeError) as e:
                    return self._send(400, json.dumps({'error': str(e)}).encode('utf-8'))
                except Exception as e:
                    return self._send(500, json.dumps({'error': f'{type(e).__name__}: {e}'}).encode('utf-8'))

        self.server = ThreadingHTTPServer((self.bind, self.port), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name='gea-vpn-page', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown(); self.server.server_close()

    @property
    def url(self) -> str:
        return f'http://{self.bind}:{self.port}'


def run_forever(client: Client, port: int = PORT, bind: str = '127.0.0.1') -> int:
    import signal
    done = threading.Event()

    def _sig(*a):
        done.set()
    for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, 'SIGBREAK', None)):
        if s is None:
            continue
        try:
            signal.signal(s, _sig)
        except (ValueError, OSError):
            pass
    srv = Server(client, port, bind)
    srv.start()
    client.state.log(f'{NAME} {__version__} serving {srv.url}')
    stop_file = os.path.join(client.state.dir, 'STOP')
    try:
        while not done.is_set():
            done.wait(1.0)
            if os.path.isfile(stop_file):
                break
    finally:
        srv.stop()
        try:
            os.remove(stop_file)
        except OSError:
            pass
        client.state.log('stopped')
    return 0
