# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""monitor - the screen on the box, for the person on the pad.

A monitor on the Pi's HDMI port (or any browser on the pad network at /live) shows what the box is doing right
now, large enough to read from across a doghouse and with nothing to press: whether it is RECORDING, in green,
or what is wrong, in red; the clock and what its time is worth; the rig's bit depth and hole depth as they come
in; the disk; and, for every channel, the last minute of signal scrolling by with its level, and for the ground
sensors a spectrogram of the last few minutes. A channel that has gone quiet (a cable off) or is clipping (a
sensor saturated) is named in red. The page asks the box for its last second once a second (/live.json); the box
keeps the last two minutes of every channel as min/max per tenth of a second, the level per second, and one
spectrum column per second for the ground channels - a few hundred kilobytes, never written to disk.

The kiosk: kiosk.sh in the kit sets the Pi to sign in at boot and open this page full-screen, with the screen
blanking off, so the box with a monitor plugged in is its own instrument panel."""
from __future__ import annotations

import collections
import math
import threading
import time
from typing import Deque, Dict, List, Optional

import numpy as np

BIN_S = 0.1                 # the trace: min and max per tenth of a second
KEEP_S = 120.0              # how far back the trace goes
SPEC_S = 4.0                # the spectrum: the last four seconds of a ground channel
SPEC_KEEP = 300             # columns kept: five minutes at one a second
SPEC_BAND = (1.0, 80.0)
FULL_SCALE = 2 ** 23 - 1
QUIET_COUNTS = 2.0          # a channel whose last ten seconds move less than this is quiet (a cable off)
CLIP_FRAC = 0.98            # a sample beyond this fraction of full scale is a clip


class ChannelRing:
    def __init__(self, ch: dict, rate: float):
        self.ch = ch
        self.rate = rate
        self.per_bin = max(1, int(round(BIN_S * rate)))
        self.bins: Deque = collections.deque(maxlen=int(KEEP_S / BIN_S))     # (t, min, max)
        self.levels: Deque = collections.deque(maxlen=int(KEEP_S))              # (t, rms, clips)
        self.recent = np.zeros(0, dtype=np.float64)                             # the last SPEC_S seconds, for the spectrum
        self.spec: Deque = collections.deque(maxlen=SPEC_KEEP)                   # (t, [db ...])
        self.freqs: List[float] = []
        self._carry = np.zeros(0, dtype=np.int32)
        self._sec_acc: List[np.ndarray] = []
        self._sec_t: Optional[float] = None
        self._last_spec_t = 0.0
        self.ground = ch.get('kind') in ('geophone', 'accelerometer', 'seismometer')

    def add(self, x: np.ndarray, t_first: float) -> None:
        if len(x) == 0:
            return
        x = np.asarray(x)
        n_carry = len(self._carry)
        if n_carry:
            x = np.concatenate([self._carry, x]); t_first -= n_carry / self.rate
        n_full = len(x) // self.per_bin
        if n_full:
            b = x[:n_full * self.per_bin].reshape(n_full, self.per_bin)
            mins, maxs = b.min(axis=1), b.max(axis=1)
            for k in range(n_full):
                self.bins.append((round(t_first + k * self.per_bin / self.rate, 3), int(mins[k]), int(maxs[k])))
        self._carry = x[n_full * self.per_bin:].astype(np.int32)
        # the level and the clips, per second
        if self._sec_t is None:
            self._sec_t = math.floor(t_first)
        self._sec_acc.append(x[:n_full * self.per_bin].astype(np.float64))
        t_end = t_first + n_full * self.per_bin / self.rate
        while t_end >= self._sec_t + 1.0 and self._sec_acc:
            a = np.concatenate(self._sec_acc)
            need = int(round((self._sec_t + 1.0 - (t_end - len(a) / self.rate)) * self.rate))
            need = max(0, min(need, len(a)))
            sec, rest = a[:need], a[need:]
            if len(sec):
                self.levels.append((float(self._sec_t), float(np.sqrt(np.mean(sec * sec))), int(np.sum(np.abs(sec) >= CLIP_FRAC * FULL_SCALE))))
            self._sec_acc = [rest] if len(rest) else []
            self._sec_t += 1.0
        # the spectrum column, once a second, from the last SPEC_S seconds
        if self.ground:
            keep = int(SPEC_S * self.rate)
            self.recent = np.concatenate([self.recent, x[:n_full * self.per_bin].astype(np.float64)])[-keep:]
            if len(self.recent) >= keep and t_end - self._last_spec_t >= 1.0:
                self._last_spec_t = t_end
                seg = self.recent - self.recent.mean()
                w = np.hanning(len(seg))
                P = np.abs(np.fft.rfft(seg * w)) ** 2
                F = np.fft.rfftfreq(len(seg), 1.0 / self.rate)
                sel = (F >= SPEC_BAND[0]) & (F <= min(SPEC_BAND[1], 0.45 * self.rate))
                if sel.any():
                    db = 10.0 * np.log10(P[sel] + 1e-9)
                    # 48 bins across the band, averaged, so a column is small
                    nb = min(48, int(sel.sum()))
                    idx = np.linspace(0, int(sel.sum()), nb + 1).astype(int)
                    col = [round(float(db[idx[i]:idx[i + 1]].mean()), 1) for i in range(nb) if idx[i + 1] > idx[i]]
                    self.freqs = [round(float(F[sel][min(idx[i], int(sel.sum()) - 1)]), 2) for i in range(len(col))]
                    self.spec.append((round(t_end, 1), col))

    def snapshot(self, since: float) -> dict:
        bins = [b for b in self.bins if b[0] >= since]
        lv = list(self.levels)[-10:]
        rms = lv[-1][1] if lv else None
        clips = sum(l[2] for l in lv)
        span = (max(b[2] for b in bins) - min(b[1] for b in bins)) if bins else None
        quiet = bool(bins) and span is not None and span < QUIET_COUNTS and len(bins) > 50
        return {'id': self.ch['id'], 'code': self.ch['code'], 'kind': self.ch['kind'], 'rate': self.rate, 'trace': bins, 'rms': None if rms is None else round(rms, 1),
                'clips_10s': clips, 'quiet': quiet, 'spectrum': list(self.spec)[-1] if self.spec else None, 'freqs': self.freqs if self.ground else []}


class Monitor:
    def __init__(self, cfg: dict):
        fs = float(cfg['adc']['sample_rate'])
        self.rings: List[ChannelRing] = [ChannelRing(c, float(c.get('rate', fs))) for c in cfg['adc']['channels']]
        self.fs = fs
        self.lock = threading.Lock()

    def add_block(self, block: np.ndarray, t_first: float) -> None:
        with self.lock:
            for j, r in enumerate(self.rings):
                ratio = int(round(self.fs / r.rate))
                x = block[j]
                if ratio > 1:
                    n_full = len(x) // ratio
                    if n_full == 0:
                        continue
                    x = np.round(x[:n_full * ratio].reshape(n_full, ratio).mean(axis=1)).astype(np.int32)
                r.add(x, t_first)

    def live(self, health: dict, window_s: float = 60.0) -> dict:
        now = time.time()
        with self.lock:
            chans = [r.snapshot(now - window_s - 5.0) for r in self.rings]
        rig = health.get('rig') or {}
        last = rig.get('last') or {}
        clock = health.get('clock') or {}
        problems = []
        if not health.get('recording'):
            problems.append(health.get('state') or 'NOT RECORDING')
        if clock.get('quality', 0) < 90:
            problems.append(f"clock: {clock.get('detail', 'unknown')} (quality {clock.get('quality', 0)})")
        if rig.get('enabled') and not rig.get('connected'):
            problems.append('rig feed not connected')
        if health.get('free_mb', 1e9) < 2000:
            problems.append(f"disk: {health.get('free_mb')} MB free")
        for c in chans:
            if c['quiet']:
                problems.append(f"{c['id']} {c['code']}: no signal (cable?)")
            if c['clips_10s'] > 0:
                problems.append(f"{c['id']} {c['code']}: clipping ({c['clips_10s']} samples in 10 s)")
        return {'t': now, 'utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now)), 'site': health.get('site'), 'station': health.get('station'),
                'recording': bool(health.get('recording')), 'state': health.get('state'), 'clock': clock, 'rig': {'enabled': rig.get('enabled'), 'connected': rig.get('connected'),
                'bit_depth': last.get('bit_depth'), 'hole_depth': last.get('hole_depth'), 'rop': last.get('rop'), 'hookload': last.get('hookload'), 'utc': last.get('utc')},
                'free_mb': health.get('free_mb'), 'n_files': health.get('n_files'), 'uptime_s': health.get('uptime_s'), 'problems': problems, 'channels': chans,
                'window_s': window_s, 'spectrum_band_hz': list(SPEC_BAND)}


LIVE_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GEA Recorder - live</title>
<style>
html,body{margin:0;background:#0b0f14;color:#e6edf3;font:18px system-ui,sans-serif;height:100%}
#top{display:flex;align-items:center;gap:24px;padding:10px 18px;background:#121820;border-bottom:2px solid #1f2a36;flex-wrap:wrap}
#state{font-size:40px;font-weight:800;letter-spacing:1px}
.ok{color:#2ecc71}.bad{color:#ff5c5c}.warn{color:#ffd166}
.kv{font-size:20px;color:#b9c6d3}.kv b{color:#fff;font-size:26px}
#problems{padding:8px 18px;font-size:26px;font-weight:700;color:#fff;background:#8b1e1e;display:none;white-space:pre-line}
#rows{padding:8px 12px}
.row{display:grid;grid-template-columns:150px 1fr 320px;gap:10px;align-items:stretch;margin:6px 0;background:#0f151c;border:1px solid #1f2a36;border-radius:8px;padding:6px}
.lab{font-size:22px;font-weight:700}.lab small{display:block;font-size:14px;color:#8ea0b3;font-weight:400}
.lab .lvl{font-size:16px;color:#b9c6d3;margin-top:6px}
canvas{display:block;width:100%;height:var(--h,110px);background:#0b0f14;border-radius:4px}
#foot{padding:6px 18px;color:#8ea0b3;font-size:15px}
</style></head><body>
<div id="top"><div id="state" class="bad">STARTING</div><div class="kv">clock <b id="clk">-</b> <span id="clkd"></span></div><div class="kv">bit depth <b id="bit">-</b></div><div class="kv">hole depth <b id="hole">-</b></div><div class="kv">disk <b id="disk">-</b></div><div class="kv">files <b id="files">-</b></div><div class="kv"><b id="utc">-</b> UTC</div></div>
<div id="problems"></div>
<div id="rows"></div>
<div id="foot">GEA Recorder - the last 60 s of every channel, its level, and the spectrogram of the ground sensors (1-80 Hz, the last 5 min). Nothing to press. <span id="site"></span></div>
<script>
const specHist = {};
function el(id){return document.getElementById(id)}
function fmt(v,d){return v===null||v===undefined||v===''?'-':(typeof v==='number'?v.toLocaleString(undefined,{maximumFractionDigits:d===undefined?0:d}):v)}
function drawTrace(c, tr, t_now, win){const g=c.getContext('2d');const W=c.width=c.clientWidth*devicePixelRatio,H=c.height=c.clientHeight*devicePixelRatio;g.fillStyle='#0b0f14';g.fillRect(0,0,W,H);
 if(!tr.length){g.fillStyle='#8ea0b3';g.font=(14*devicePixelRatio)+'px system-ui';g.fillText('no samples yet',10,H/2);return}
 let lo=Infinity,hi=-Infinity;for(const b of tr){lo=Math.min(lo,b[1]);hi=Math.max(hi,b[2])}const mid=(lo+hi)/2;let span=Math.max(hi-lo,10);span*=1.1;
 g.strokeStyle='#1f2a36';g.beginPath();for(let s=0;s<=win;s+=10){const x=W-(s/win)*W;g.moveTo(x,0);g.lineTo(x,H)}g.stroke();
 g.strokeStyle='#6fd3a0';g.lineWidth=Math.max(devicePixelRatio,W/(win/0.1)*0.9);g.beginPath();for(const b of tr){const x=W-((t_now-b[0])/win)*W;const ya=H/2-(b[1]-mid)/span*H,yb=H/2-(b[2]-mid)/span*H;const top=Math.min(ya,yb),bot=Math.max(ya,yb);g.moveTo(x,top);g.lineTo(x,Math.max(bot,top+1))}g.stroke();
 g.fillStyle='#8ea0b3';g.font=(12*devicePixelRatio)+'px system-ui';g.fillText('±'+fmt(Math.round(span/2))+' counts',6,14*devicePixelRatio);g.fillText('60 s ago',6,H-6);g.fillText('now',W-40*devicePixelRatio,H-6)}
function drawSpec(c, id, col){const g=c.getContext('2d');const W=c.width=c.clientWidth*devicePixelRatio,H=c.height=c.clientHeight*devicePixelRatio;
 if(!specHist[id])specHist[id]=[];const h=specHist[id];if(col&&(!h.length||h[h.length-1][0]!==col[0]))h.push(col);while(h.length>300)h.shift();
 g.fillStyle='#0b0f14';g.fillRect(0,0,W,H);if(!h.length){return}
 let dlo=Infinity,dhi=-Infinity;for(const [t,v] of h)for(const d of v){if(d<dlo)dlo=d;if(d>dhi)dhi=d}const cw=W/300;
 for(let k=0;k<h.length;k++){const v=h[k][1];const ch=H/v.length;const x=W-(h.length-k)*cw;for(let b=0;b<v.length;b++){const u=(v[b]-dlo)/Math.max(dhi-dlo,1);g.fillStyle=`hsl(${220-220*u},80%,${22+48*u}%)`;g.fillRect(x,H-(b+1)*ch,Math.ceil(cw),Math.ceil(ch))}}
 g.fillStyle='#b9c6d3';g.font=(12*devicePixelRatio)+'px system-ui';g.fillText('80 Hz',4,14*devicePixelRatio);g.fillText('1 Hz',4,H-6)}
async function tick(){let L;try{const r=await fetch('/live.json',{cache:'no-store'});L=await r.json()}catch(e){el('state').textContent='NO ANSWER FROM THE BOX';el('state').className='bad';return}
 el('state').textContent=L.recording?'RECORDING':'NOT RECORDING';el('state').className=L.recording?'ok':'bad';
 el('clk').textContent=L.clock.quality+'/100';el('clk').className=L.clock.quality>=90?'ok':'bad';el('clkd').textContent=L.clock.pps?'GPS':(L.clock.locked?'network':'free-running');
 el('bit').textContent=fmt(L.rig.bit_depth);el('hole').textContent=fmt(L.rig.hole_depth);el('disk').textContent=fmt(L.free_mb)+' MB';el('files').textContent=fmt(L.n_files);el('utc').textContent=L.utc;el('site').textContent=L.site+' · '+L.station;
 const p=el('problems');if(L.problems.length){p.style.display='block';p.textContent=L.problems.join('\n')}else p.style.display='none';
 const rows=el('rows');document.documentElement.style.setProperty('--h',Math.max(56,Math.floor((innerHeight-el('top').offsetHeight-el('problems').offsetHeight-70)/Math.max(L.channels.length,1))-22)+'px');if(rows.children.length!==L.channels.length){rows.innerHTML=L.channels.map(c=>`<div class="row" id="r_${c.id}"><div class="lab">${c.id} <small>${c.code} · ${c.kind} · ${c.rate}/s</small><div class="lvl" id="l_${c.id}"></div></div><canvas id="t_${c.id}"></canvas><canvas id="s_${c.id}"></canvas></div>`).join('')}
 for(const c of L.channels){drawTrace(el('t_'+c.id),c.trace,L.t,L.window_s);const lv=el('l_'+c.id);lv.innerHTML=(c.rms===null?'':'level '+fmt(c.rms)+' counts')+(c.quiet?' <span class="bad">NO SIGNAL</span>':'')+(c.clips_10s?' <span class="bad">CLIPPING</span>':'');
  const sc=el('s_'+c.id);if(c.freqs&&c.freqs.length){sc.style.display='block';drawSpec(sc,c.id,c.spectrum)}else sc.style.display='none'}}
tick();setInterval(tick,1000);
</script></body></html>"""
