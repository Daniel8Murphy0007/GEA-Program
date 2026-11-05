# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""status - the driller's screen on the rig PC (port 8790): the section with the model's layer window and the hole
drilled through it, the recommendation in the driller's words with its reasons and confidence, the rig's numbers
against the limits, Accept / Reject / Did something else with the operator's name, the closed-loop switch when
the configuration allows it, the last decisions; and the files GEA pulls: /manifest.json, /decisions.jsonl,
/commands.jsonl, /surveys.csv. Standard library; one thread."""
from __future__ import annotations

import html
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import urlparse

from . import NAME, __version__

LIVE_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEA Driller - __WELL__</title><style>
:root{--bg:#0f1419;--panel:#1a222c;--ink:#e8edf2;--muted:#8fa0b0;--good:#2ec27e;--warn:#f5a623;--bad:#ff5c5c;--accent:#4aa3ff;--grid:#2b3744}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,Segoe UI,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:10px 16px;border-bottom:1px solid var(--grid)}header h1{font-size:18px;margin:0}
.state{font-weight:700;padding:4px 12px;border-radius:6px;background:#18352a;color:var(--good)}.state.bad{background:#3a1c1c;color:var(--bad)}.state.warn{background:#3a2f12;color:var(--warn)}
main{display:grid;grid-template-columns:3fr 2fr;gap:12px;padding:12px}@media(max-width:900px){main{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:12px}.panel h2{font-size:14px;margin:0 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
canvas{width:100%;height:auto;display:block;background:#0b1015;border-radius:8px}
.rec{font-size:20px;line-height:1.35;margin:4px 0 8px}.rec b{color:var(--accent)}.reasons{color:var(--muted);font-size:13px;margin:0;padding-left:18px}.reasons li{margin:2px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}.tile{background:#121a22;border:1px solid var(--grid);border-radius:8px;padding:8px}.tile .v{font-size:22px;font-weight:700}.tile .k{color:var(--muted);font-size:12px}.tile.over .v{color:var(--bad)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--grid);background:#223040;color:var(--ink);cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#06101a;font-weight:700}button.danger{background:#5a1f1f;border-color:#7a2b2b}button:disabled{opacity:.5;cursor:default}
input,select{font:inherit;padding:7px 9px;border-radius:6px;border:1px solid var(--grid);background:#0b1015;color:var(--ink)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}.muted{color:var(--muted)}.small{font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--grid);text-align:left}
.arm{border:1px solid var(--warn);border-radius:8px;padding:10px;margin-top:10px}.arm.on{border-color:var(--bad);background:#2a1414}
#toast{position:fixed;right:16px;bottom:16px;background:#223040;padding:10px 14px;border-radius:8px;display:none}
</style></head><body>
<header><h1>GEA Driller · __WELL__</h1><span class="state" id="state">…</span><span class="muted small" id="feed"></span><span style="flex:1"></span><span class="muted small">__VERSION__ · <a href="/" style="color:var(--muted)">status</a></span></header>
<main>
<div>
 <div class="panel"><h2>The section · the model's layer window and the hole drilled through it</h2><canvas id="sec" width="1200" height="420"></canvas><p class="small muted" id="secnote"></p></div>
 <div class="panel" style="margin-top:12px"><h2>The rig now</h2><div class="tiles" id="tiles"></div></div>
 <div class="panel" style="margin-top:12px"><h2>Decisions</h2><table><thead><tr><th>Time (UTC)</th><th>MD ft</th><th>Recommendation</th><th>Answer</th></tr></thead><tbody id="hist"></tbody></table></div>
</div>
<div>
 <div class="panel"><h2>Recommendation</h2><div class="rec" id="rec">waiting for the first decision…</div><ul class="reasons" id="reasons"></ul>
  <div class="row" style="margin-top:10px"><input id="who" placeholder="your name" style="width:150px"><button class="primary" id="accept">Accept</button><button id="reject">Reject</button><button id="override">Did something else…</button></div>
  <div id="ovbox" class="row" style="display:none"><input id="ovb" placeholder="build °/100ft" style="width:110px"><input id="ovw" placeholder="WOB klb" style="width:90px"><input id="ovr" placeholder="rpm" style="width:80px"><input id="ovf" placeholder="gpm" style="width:80px"><select id="ovc"><option value="">drilling</option><option>circulate</option><option>ream_wash</option></select><input id="ovn" placeholder="note" style="width:160px"><button id="ovgo">Record it</button></div>
  <p class="small muted" id="answers"></p>
  <div id="armbox"></div>
 </div>
 <div class="panel" style="margin-top:12px"><h2>The agent</h2><p class="small muted" id="agent"></p></div>
</div>
</main><div id="toast"></div>
<script>
const $=s=>document.querySelector(s);let L=null;const who=localStorage.getItem('gea-driller-who')||'';$('#who').value=who;$('#who').onchange=()=>localStorage.setItem('gea-driller-who',$('#who').value);
function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',4000)}
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function drawSection(l){const c=$('#sec'),x=c.getContext('2d'),W=c.width,H=c.height;x.clearRect(0,0,W,H);const m=l.model,path=l.path||[];if(!path.length)return;
 const vs0=0,vs1=Math.max(m.lateral_length_ft,path[path.length-1].vs+200);const tops=[],bases=[];for(let i=0;i<=60;i++){const vs=vs0+(vs1-vs0)*i/60;const t=m.layer_top_tvd_ft+vs*Math.tan(m.dip_deg*Math.PI/180);tops.push([vs,t]);bases.push([vs,t+m.thickness_ft])}
 const tv=path.map(p=>p.tvd).concat(tops.map(p=>p[1]),bases.map(p=>p[1]));let tmin=Math.min(...tv)-20,tmax=Math.max(...tv)+20;const X=v=>40+(v-vs0)/(vs1-vs0)*(W-60),Y=t=>20+(t-tmin)/(tmax-tmin)*(H-50);
 x.fillStyle='#16321f';x.beginPath();tops.forEach((p,i)=>i?x.lineTo(X(p[0]),Y(p[1])):x.moveTo(X(p[0]),Y(p[1])));bases.slice().reverse().forEach(p=>x.lineTo(X(p[0]),Y(p[1])));x.closePath();x.fill();
 x.strokeStyle='#2ec27e';x.lineWidth=1.5;x.setLineDash([6,4]);[tops,bases].forEach(a=>{x.beginPath();a.forEach((p,i)=>i?x.lineTo(X(p[0]),Y(p[1])):x.moveTo(X(p[0]),Y(p[1])));x.stroke()});x.setLineDash([]);
 x.strokeStyle='#4aa3ff';x.lineWidth=3;x.beginPath();path.forEach((p,i)=>i?x.lineTo(X(p.vs),Y(p.tvd)):x.moveTo(X(p.vs),Y(p.tvd)));x.stroke();
 const b=path[path.length-1];x.fillStyle='#f5a623';x.beginPath();x.arc(X(b.vs),Y(b.tvd),6,0,7);x.fill();
 if(l.current&&l.current.recommend){const r=l.current.recommend,k=r.build_deg_100ft;const ang=(l.rig.continuous_inc!=null?l.rig.continuous_inc:90)+k*0.9;const dx=90*Math.sin(ang*Math.PI/180),dy=90*Math.cos(ang*Math.PI/180);x.strokeStyle='#f5a623';x.setLineDash([3,3]);x.beginPath();x.moveTo(X(b.vs),Y(b.tvd));x.lineTo(X(b.vs+dx),Y(b.tvd+dy));x.stroke();x.setLineDash([])}
 x.fillStyle='#8fa0b0';x.font='12px system-ui';for(let i=0;i<=4;i++){const t=tmin+(tmax-tmin)*i/4;x.fillText(t.toFixed(0)+' ft TVD',4,Y(t)+4)}for(let i=0;i<=4;i++){const v=vs0+(vs1-vs0)*i/4;x.fillText(v.toFixed(0)+' ft',X(v)-14,H-6)}
 $('#secnote').textContent=`The green band is the model's layer window (top ${m.layer_top_tvd_ft.toFixed(0)} ft TVD at the heel, ${m.thickness_ft.toFixed(0)} ft thick, dip ${m.dip_deg.toFixed(1)}°); the blue line is the hole from the surveys (${l.n_surveys} stations) and the continuous inclination; the orange dot is the bit; the dotted stub is where the recommendation points. The true layer is not known - that is what the gamma and the deep resistivity are for.`}
function tile(k,v,over){return `<div class="tile ${over?'over':''}"><div class="v">${v}</div><div class="k">${k}</div></div>`}
function render(l){L=l;const st=$('#state');st.className='state'+(l.ok?'':' bad');st.textContent=l.state;$('#feed').textContent=l.feed_note;drawSection(l);const r=l.rig,lim=l.limits;
 $('#tiles').innerHTML=tile('hole depth ft',r.hole_depth!=null?r.hole_depth.toFixed(0):'—')+tile('gamma API',r.gamma!=null?r.gamma.toFixed(0):'—')+tile('inclination °',r.continuous_inc!=null?r.continuous_inc.toFixed(1):'—')+tile('ROP ft/h',r.rop!=null?r.rop.toFixed(0):'—')+tile('WOB klb',r.wob!=null?r.wob.toFixed(0):'—')+tile('rpm',r.rpm!=null?r.rpm.toFixed(0):'—')+tile('flow gpm',r.flow_in!=null?r.flow_in.toFixed(0):'—')+tile('vibration',r.vibration!=null?r.vibration.toFixed(2):'—',r.vibration!=null&&r.vibration>lim.vibration_index)+tile('ECD ppg',r.ecd!=null?r.ecd.toFixed(2):'—',r.ecd!=null&&r.ecd>lim.frac_gradient_ppg)+tile('deep res. ft',r.deep_res_dist!=null?r.deep_res_dist.toFixed(1):'no tool')+tile('cuttings bed',(l.advisor.hole_model.bed*100).toFixed(0)+'%',l.advisor.hole_model.bed>0.5);
 const c=l.current;if(c&&c.recommend){$('#rec').innerHTML=`<b>${esc(c.recommend.words)}</b><div class="small muted">at ${c.md.toFixed(0)} ft MD · ${c.utc.slice(11,19)} UTC · value ${c.value}${c.armed?' · <span style="color:var(--bad)">ARMED: sent as a command</span>':''}</div>`;$('#reasons').innerHTML=c.reasons.map(x=>`<li>${esc(x)}</li>`).join('');const answered=!!c.answer;['#accept','#reject','#override'].forEach(s=>$(s).disabled=answered||!!c.armed)}
 else{$('#rec').textContent=l.ok?'waiting for the next decision…':l.state;$('#reasons').innerHTML=''}
 const a=l.advisor.answers;$('#answers').textContent=`${l.advisor.decisions} decisions on record · accepted ${a.accepted}, rejected ${a.rejected}, done otherwise ${a.overridden}, unanswered ${a.unanswered}`;
 $('#hist').innerHTML=l.advisor.history.slice().reverse().map(h=>`<tr><td>${h.utc.slice(11,19)}</td><td>${h.md.toFixed(0)}</td><td>${esc(h.recommend.words)}</td><td>${h.answer?esc(h.answer.what+(h.answer.by?' · '+h.answer.by:'')):(h.armed?'command':'—')}</td></tr>`).join('');
 const p=l.advisor.policy;$('#agent').textContent=`Policy trained ${p.trained_steps?p.trained_steps.toLocaleString():'?'} twin steps in ${p.iterations||'?'} batches (${p.trained_utc||'shipped'}). ${l.advisor.downlink}. ${l.twin?'THIS IS THE TWIN AS THE RIG - a simulation, labelled: scene '+l.twin.seed+', '+l.twin.in_layer_pct.toFixed(0)+'% of the hole in the layer so far.':''}`;
 const ab=$('#armbox');if(!l.advisor.closed_loop_allowed){ab.innerHTML='<p class="small muted">Advisory only: the closed loop is not allowed in this configuration.</p>';return}
 const ar=l.advisor.armed;if(ar){ab.className='arm on';ab.innerHTML=`<b>CLOSED LOOP ARMED</b> by ${esc(ar.by)} for ${ar.from_md.toFixed(0)}–${ar.to_md.toFixed(0)} ft MD since ${ar.utc.slice(11,19)} UTC · ${ar.commands} command(s) · ${ar.sends?'the downlink sends':'the downlink keeps and does not send'}<div class="row"><button class="danger" id="disarm">Disarm now</button></div>`;$('#disarm').onclick=async()=>{try{await post('/api/disarm',{by:$('#who').value});toast('disarmed')}catch(e){toast(e.message)}}}
 else{ab.className='arm';ab.innerHTML=`<b>Closed loop</b> <span class="small muted">arm the agent for one section; it disarms itself on a stale feed, a crossed limit or the section's end</span><div class="row"><input id="armto" placeholder="to MD ft" style="width:110px"><input id="armn" placeholder="note" style="width:160px"><button class="danger" id="arm">Arm for this section</button></div>`;$('#arm').onclick=async()=>{const to=parseFloat($('#armto').value);if(!(to>0))return toast('the section\'s end MD');if(!confirm(`Arm the closed loop to ${to} ft MD as ${$('#who').value||'(no name)'}? Every decision becomes a command until the section is drilled or anything crosses a limit.`))return;try{await post('/api/arm',{by:$('#who').value,to_md:to,note:$('#armn').value});toast('armed')}catch(e){toast(e.message)}}}}
async function tick(){try{const r=await fetch('/live.json');render(await r.json())}catch(e){$('#state').className='state bad';$('#state').textContent='no answer from the program'}}
$('#accept').onclick=async()=>{try{await post('/api/answer',{what:'accept',by:$('#who').value});toast('accepted');tick()}catch(e){toast(e.message)}};
$('#reject').onclick=async()=>{try{await post('/api/answer',{what:'reject',by:$('#who').value});toast('rejected');tick()}catch(e){toast(e.message)}};
$('#override').onclick=()=>{$('#ovbox').style.display=$('#ovbox').style.display==='none'?'flex':'none'};
$('#ovgo').onclick=async()=>{const o={};const f=(id,k)=>{const v=parseFloat($(id).value);if(!isNaN(v))o[k]=v};f('#ovb','build_deg_100ft');f('#ovw','wob_klb');f('#ovr','rpm');f('#ovf','flow_gpm');if($('#ovc').value)o.conditioning=$('#ovc').value;try{await post('/api/answer',{what:'override',by:$('#who').value,note:$('#ovn').value,override:o});toast('recorded');$('#ovbox').style.display='none';tick()}catch(e){toast(e.message)}};
tick();setInterval(tick,2000);
</script></body></html>"""

STATUS_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>{name} - {well}</title><style>body{{font:15px system-ui,sans-serif;margin:16px;color:#1b2430}}.ok{{color:#1b6e3a;font-weight:600}}.bad{{color:#b03030;font-weight:600}}.muted{{color:#666}}h1{{font-size:20px;margin:0 0 4px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:3px 8px;text-align:left}}</style></head><body>
<h1>{name} {version} · {site} · {well}</h1>
<p class="{cls}">{state}</p>
<p>Rig feed: {feed} · Hole depth {md} ft · Decisions on record {n_dec} · Closed loop: {cl}</p>
<p>Latest recommendation: <b>{rec}</b></p>
<p class="muted"><a href="/live"><b>The driller's screen</b></a> · Files: <a href="/manifest.json">manifest.json</a> · <a href="/decisions.jsonl">decisions.jsonl</a> · <a href="/commands.jsonl">commands.jsonl</a> · <a href="/surveys.csv">surveys.csv</a> · <a href="/health">health</a>. This page refreshes every 5 s.</p>
</body></html>"""


class StatusServer:
    def __init__(self, cfg: dict, live: Callable[[], dict], actions: dict, data_dir: str):
        self.cfg = cfg; self.live = live; self.actions = actions; self.data_dir = data_dir
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port = 0

    def start(self) -> None:
        srv = self
        cfg = self.cfg

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = 'application/json', extra=None):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                try:
                    if path == '/':
                        l = srv.live()
                        page = STATUS_PAGE.format(name=NAME, version=__version__, site=html.escape(cfg['site']), well=html.escape(cfg['well']), cls='ok' if l['ok'] else 'bad',
                                                  state=html.escape(l['state']), feed=html.escape(l['feed_note']), md=f"{l['rig'].get('hole_depth') or 0:,.0f}",
                                                  n_dec=l['advisor']['decisions'], cl=html.escape('ARMED' if l['advisor']['armed'] else ('allowed, not armed' if l['advisor']['closed_loop_allowed'] else 'not allowed')),
                                                  rec=html.escape(l['current']['recommend']['words'] if l.get('current') else '—'))
                        return self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')
                    if path == '/live':
                        return self._send(200, LIVE_PAGE.replace('__WELL__', html.escape(cfg['well'])).replace('__VERSION__', __version__).encode('utf-8'), 'text/html; charset=utf-8')
                    if path == '/live.json':
                        return self._send(200, json.dumps(srv.live()).encode('utf-8'))
                    if path == '/health':
                        l = srv.live()
                        return self._send(200, json.dumps({'program': NAME, 'version': __version__, 'site': cfg['site'], 'well': cfg['well'], 'ok': l['ok'], 'state': l['state'],
                                                           'decisions': l['advisor']['decisions'], 'armed': bool(l['advisor']['armed']), 'hole_depth': l['rig'].get('hole_depth'), 'twin': l.get('twin') is not None}).encode('utf-8'))
                    if path in ('/manifest.json', '/decisions.jsonl', '/commands.jsonl', '/surveys.csv') or path.startswith('/files/'):
                        rel = path[len('/files/'):] if path.startswith('/files/') else path[1:]
                        full = os.path.normpath(os.path.join(srv.data_dir, rel))
                        if not full.startswith(os.path.normpath(srv.data_dir)) or not os.path.isfile(full):
                            return self._send(404, b'{"error":"no such file"}')
                        with open(full, 'rb') as f:
                            data = f.read()
                        ct = 'application/json' if full.endswith('.json') else ('text/csv' if full.endswith('.csv') else 'text/plain')
                        return self._send(200, data, ct)
                    return self._send(404, b'{"error":"no such page"}')
                except Exception as e:
                    return self._send(500, json.dumps({'error': f'{type(e).__name__}: {e}'}).encode('utf-8'))

            def do_POST(self):
                path = urlparse(self.path).path
                n = int(self.headers.get('Content-Length') or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b'{}') if n else {}
                except ValueError:
                    return self._send(400, b'{"error":"the body is JSON"}')
                fn = srv.actions.get(path)
                if fn is None:
                    return self._send(404, b'{"error":"no such action"}')
                try:
                    return self._send(200, json.dumps(fn(body)).encode('utf-8'))
                except ValueError as e:
                    return self._send(400, json.dumps({'error': str(e)}).encode('utf-8'))
                except Exception as e:
                    return self._send(500, json.dumps({'error': f'{type(e).__name__}: {e}'}).encode('utf-8'))

        self.server = ThreadingHTTPServer((self.cfg['status'].get('bind', '0.0.0.0'), int(self.cfg['status'].get('port', 8790))), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name='status', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown(); self.server.server_close()
