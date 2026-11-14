"""Kick and loss detection: the mud that comes back, and the mud in the pits, against their own baselines.

A well that is in balance returns what is pumped into it. When the formation gives fluid to the well (a kick) more
comes back than went in, and the pits rise; when the formation takes mud (losses) less comes back and the pits fall.
The drilling recorder writes the two measurements that show it - the volume of mud in the active pits and the flow
in the return line - and this module reads them two ways, neither with a model of the well:

- THE FLOWBACK AT EACH PUMPS-OFF. When the pumps stop, the mud in the surface lines and the annulus above the bell
  nipple drains back, and the pits rise by some barrels over a few minutes: every rig has its own curve, and it is
  much the same from one connection to the next. So each pumps-off is read as a curve - the pit volume above what
  it was when the pumps stopped, at 30, 60, 120, 180 and 300 seconds - and set against the FINGERPRINT of the
  connections before it (the median of the last eight made from a like pump rate, and their scatter). A flowback
  that runs above its fingerprint at the last two readings (by four of its scatters and 4 bbl) is the well giving
  fluid with the pumps off - or a transfer into the pits during the connection, which is caught as a step and left
  out; one that runs below it is the hole taking mud, or the return line lined up to the trip tank. Where the record has a return-flow channel, the fraction of the flow still running at the
  same moment is read against its own fingerprint too, and the two are said to agree or not.
- THE PITS WHILE CIRCULATING. With the pumps steady (within a tenth of their own ten-minute median, and five
  minutes after they came on), the change in pit volume over ten minutes is set against the same change over the
  two hours of steady circulation before it - its own trend, which already carries the hole being drilled deeper.
  A change that leaves that trend by 8 bbl and four of its scatters, the pits themselves moving that way by 4 bbl, is an event; and it is named by what the return
  line did over the same ten minutes: a gain with the returns up is KICK-LIKE; a gain with the returns flat is mud
  added or moved at surface, as far as the record can say; a fall with the returns down is LOSSES; a fall with the
  returns flat is volume leaving at surface. Without a return-flow channel it is a gain or a fall, and the page
  says it cannot tell which kind. A step of 5 bbl inside half a minute is a transfer.

Nothing here closes a well in or tells a driller to: an event is a place in the record to look at, with what the
rig did next beside it.
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {'flowback_sigma': 4.0, 'flowback_min_bbl': 4.0, 'gain_sigma': 4.0, 'gain_min_bbl': 8.0, 'returns_change': 0.10}
BOUNDS = {'flowback_sigma': (1.5, 10.0), 'flowback_min_bbl': (0.5, 50.0), 'gain_sigma': (2.0, 12.0), 'gain_min_bbl': (1.0, 100.0), 'returns_change': (0.02, 0.5)}
WORDS = {'flowback_sigma': 'how many scatters above its fingerprint a flowback must run', 'flowback_min_bbl': 'and the least excess in barrels',
         'gain_sigma': 'how many scatters off its trend a ten-minute pit change must be', 'gain_min_bbl': 'and the least change in barrels',
         'returns_change': 'the change in the return flow, as a fraction of its level, that counts as up or down'}
TAUS = (30.0, 60.0, 120.0, 180.0, 300.0)
FINGERPRINT_N = 8
FINGERPRINT_MIN = 4
LIKE_RATE = 0.30                 # a like pump rate: within 30 %
OFF_MIN_S = 60.0
ON_MIN_S = 120.0
STEP_BBL = 5.0                   # a change this large inside half a minute is mud moved at surface
BIN_S = 30.0
WINDOW_BINS = 20                 # ten minutes
TREND_BINS = 240                 # two hours
SETTLE_S = 300.0
LIVE_EVENT_S = 1800.0
EVENTS = {'flowback': 'kick.flowback', 'gain': 'kick.gain', 'loss': 'kick.loss'}
NAMES = {'q': ('total pump output', 'flow in', 'pump output', 'flow in rate'),
         'pit': ('total mud volume', 'pit volume active', 'active pit volume', 'pit total', 'pvt total mud volume', 'total active volume'),
         'out': ('flow', 'flow out', 'return flow', 'flow out percent', 'flow paddle', 'mud flow out'),
         'hole': ('hole depth',), 'bit': ('bit depth',)}
KINDS = {'KICK-LIKE': 'the pits rose and the returns rose with them, the pumps steady: what a kick looks like',
         'GAIN, RETURNS FLAT': 'the pits rose and the returns did not: mud added or moved at surface, as far as the record can say',
         'GAIN': 'the pits rose; the record has no return flow to say whether the well gave it',
         'LOSSES': 'the pits fell and the returns fell with them, the pumps steady: the hole taking mud',
         'FALL, RETURNS FLAT': 'the pits fell and the returns did not: volume leaving at surface (dumping, the solids control), as far as the record can say',
         'FALL': 'the pits fell; the record has no return flow to say whether the hole took it',
         'TRANSFER': 'a step in the pit volume inside half a minute: mud moved at surface'}
NOT_A_MEASUREMENT = [
    'an influx: a flowback above its fingerprint, or a gain with the returns up, is what a kick looks like in these two channels - a flow check, a shut-in and the pressures say whether it was one',
    'the size of a kick: the pit gain is a volume at surface, with the pits\' own level sensors, and whatever was added or dumped in the same minutes is in it',
    'a clean bill: no event means these rules did not fire, not that the well was in balance - gas in solution, a slow seep and a pit sensor that was not counted in the total are all invisible here',
    'the return flow as a rate where it is a paddle: its percent is a paddle\'s deflection, read only against itself',
    'a cause for a fall in the pits: losses, a washed-out dump valve and the shakers running mud overboard all take volume',
]
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


class KickError(ValueError):
    pass


def _utc(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws) -> str:
    return os.path.join(ws.path, 'records', 'kick.json')


def settings(ws) -> dict:
    s = dict(DEFAULTS)
    s['source'] = ''
    if ws is not None:
        try:
            with open(_path(ws), encoding='utf-8') as f:
                s.update(json.load(f))
        except (OSError, ValueError):
            pass
    return s


def set_settings(ws, actor: str, source: str = '', **kw) -> dict:
    s = settings(ws)
    changed = False
    for k, v in kw.items():
        if k not in DEFAULTS or v is None or (isinstance(v, str) and not v.strip()):
            continue
        x = float(v)
        lo, hi = BOUNDS[k]
        if not (math.isfinite(x) and lo <= x <= hi):
            raise KickError(f'{k} ({WORDS[k]}) must be between {lo:g} and {hi:g}')
        s[k] = x
        changed = True
    if changed:
        if not (source or '').strip():
            raise KickError('the rules\' numbers need where they came from (e.g. "well control procedure, rev 4")')
        s['source'] = source.strip()
        s['utc'] = _utc(); s['by'] = actor
        os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
        with open(_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
        os.replace(_path(ws) + '.tmp', _path(ws))
        ws.audit(actor, 'kick.settings', {k: s[k] for k in list(DEFAULTS) + ['source']})
    return s


# -- the arithmetic ------------------------------------------------------------------------------------------------------
def _med5(v: np.ndarray, k: int = 5) -> np.ndarray:
    n = len(v)
    if n < k:
        return v.copy()
    h = k // 2
    pad = np.concatenate([np.full(h, v[0]), v, np.full(h, v[-1])])
    return np.nanmedian(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)


def _at(t: np.ndarray, v: np.ndarray, x: float, half: float = 2.5) -> Optional[float]:
    a, b = int(np.searchsorted(t, x - half)), int(np.searchsorted(t, x + half, side='right'))
    seg = v[a:b]
    seg = seg[~np.isnan(seg)]
    return float(np.median(seg)) if len(seg) else None


def _mad(x: List[float]) -> Tuple[float, float]:
    a = np.asarray(x, dtype=np.float64)
    m = float(np.median(a))
    return m, float(1.4826 * np.median(np.abs(a - m)))


def pumps_off(t: np.ndarray, q: np.ndarray) -> Tuple[List[dict], np.ndarray, float]:
    """Each time the pumps stopped after two minutes or more of pumping and stayed off a minute or more."""
    qq = np.nan_to_num(q)
    run = qq[qq > 0]
    thr = max(30.0, 0.1 * float(np.percentile(run, 90))) if len(run) else 30.0
    on = _med5(qq) > thr
    ed = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(np.int8), [0]])))
    spans = [(int(ed[i]), int(ed[i + 1]) - 1) for i in range(0, len(ed), 2)]            # the stretches of pumping
    out = []
    for k, (a, b) in enumerate(spans):
        nxt = spans[k + 1][0] if k + 1 < len(spans) else len(t)
        off_a, off_b = b + 1, nxt - 1
        if off_a >= len(t) or t[b] - t[a] < ON_MIN_S or t[off_b] - t[off_a] < OFF_MIN_S:
            continue
        i0, i1 = int(np.searchsorted(t, t[b] - 180.0)), int(np.searchsorted(t, t[b] - 30.0))
        qb = float(np.median(qq[max(a, i0):max(a + 1, i1)]))
        out.append({'a': off_a, 'b': off_b, 't': float(t[off_a]), 'off_s': float(t[off_b] - t[off_a]), 'rate_before': round(qb, 1)})
    return out, on, thr


def flowbacks(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], offs: List[dict], P: dict) -> List[dict]:
    """Each pumps-off as a curve, against the fingerprint of the ones before it."""
    pit, out_ = ch.get('pit'), ch.get('out')
    rows: List[dict] = []
    for e in offs:
        a, b = e['a'], e['b']
        r = {'t': e['t'], 'off_s': round(e['off_s']), 'rate_before': e['rate_before'], 'depth_ft': None, 'dv': {}, 'out': {}, 'status': 'NO READING', 'why': None}
        if ch.get('hole') is not None and not math.isnan(ch['hole'][a]):
            r['depth_ft'] = round(float(ch['hole'][a]), 1)
        if pit is None:
            r['why'] = 'no pit volume in the record'
            rows.append(r); continue
        v0 = _at(t, pit, t[a] - 8.0, 8.0)
        if v0 is None:
            r['why'] = 'no pit volume as the pumps stopped'
            rows.append(r); continue
        seg = pit[a:b + 1]
        ok = ~np.isnan(seg)
        if ok.sum() > 12:
            ts, vs = t[a:b + 1][ok], seg[ok]
            j = np.searchsorted(ts, ts + 30.0)
            j = np.clip(j, 0, len(ts) - 1)
            step = np.abs(vs[j] - vs)
            fast = (ts[j] - ts <= 31.0) & (step >= STEP_BBL)
            if fast.any():
                # a flowback climbs a few barrels a minute at most and slows; five in half a minute after the first half minute is a transfer
                late = fast & (ts - ts[0] >= 30.0)
                if late.any():
                    r['transfer'] = True
        for tau in TAUS:
            if tau <= e['off_s']:
                x = _at(t, pit, t[a] + tau)
                if x is not None:
                    r['dv'][str(int(tau))] = round(x - v0, 2)
        if out_ is not None:
            f0 = _at(t, out_, t[a] - 35.0, 25.0)
            if f0 is not None and f0 > 1.0:
                r['out_before'] = round(f0, 1)
                for tau in TAUS:
                    if tau <= e['off_s']:
                        x = _at(t, out_, t[a] + tau)
                        if x is not None:
                            r['out'][str(int(tau))] = round(x / f0, 3)
        if not r['dv']:
            r['why'] = 'the pumps came back on inside half a minute'
            rows.append(r); continue
        if r.get('transfer'):
            r['status'] = 'TRANSFER'
            r['why'] = 'a step of 5 bbl or more inside half a minute during the pumps-off: mud moved at surface'
            rows.append(r); continue
        # the eight before it from a like rate, whatever was said of them: a flowback that stays at a new level becomes the fingerprint, as a rig's does when its lines are changed
        prev = [x for x in rows if x['status'] in ('NORMAL', 'NO BASELINE', 'ABOVE', 'BELOW') and x['rate_before'] > 0 and abs(x['rate_before'] - r['rate_before']) <= LIKE_RATE * max(r['rate_before'], 1.0)][-FINGERPRINT_N:]
        taus = [k for k in r['dv'] if float(k) >= 60.0] or list(r['dv'])
        base = {}
        for k in taus:
            vals = [x['dv'][k] for x in prev if k in x['dv']]
            if len(vals) >= FINGERPRINT_MIN:
                m, s = _mad(vals)
                base[k] = {'median': round(m, 2), 'scatter': round(s, 2), 'n': len(vals), 'limit': round(max(P['flowback_sigma'] * s, P['flowback_min_bbl']), 2)}
        r['fingerprint'] = base
        if not base:
            r['status'] = 'NO BASELINE'
            r['why'] = f'fewer than {FINGERPRINT_MIN} pumps-offs from a like pump rate before this one'
            rows.append(r); continue
        last = sorted(base, key=float)[-2:]
        ex = [r['dv'][k] - base[k]['median'] for k in last]
        lim = [base[k]['limit'] for k in last]
        r['excess_bbl'] = round(ex[-1], 2)
        r['read_at_s'] = int(float(last[-1]))
        if all(x > l for x, l in zip(ex, lim)):
            r['status'] = 'ABOVE'
        elif all(-x > l for x, l in zip(ex, lim)):
            r['status'] = 'BELOW'
        else:
            r['status'] = 'NORMAL'
        k = last[-1]
        ov = [x['out'][k] for x in prev if k in x.get('out', {})]
        if k in r['out'] and len(ov) >= FINGERPRINT_MIN:
            m, s = _mad(ov)
            r['out_fingerprint'] = {'median': round(m, 3), 'scatter': round(s, 3)}
            r['returns_running'] = bool(r['out'][k] - m > max(3.0 * s, 0.10))
        rows.append(r)
    return rows


def circulating(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], on: np.ndarray, P: dict) -> dict:
    """The ten-minute change in pit volume with the pumps steady, against its own two hours; each departure named by
    what the return line did."""
    pit, q, out_ = ch.get('pit'), ch.get('q'), ch.get('out')
    if pit is None or len(t) < 3:
        return {'status': 'NO PIT VOLUME', 'events': [], 'steady_h': 0.0}
    nb = int((t[-1] - t[0]) // BIN_S) + 1
    idx = np.clip(((t - t[0]) // BIN_S).astype(np.int64), 0, nb - 1)
    cnt = np.bincount(idx, minlength=nb).astype(np.float64)

    def binned(v):
        okv = ~np.isnan(v)
        s = np.bincount(idx[okv], weights=v[okv], minlength=nb)
        c = np.bincount(idx[okv], minlength=nb).astype(np.float64)
        return np.where(c > 0, s / np.maximum(c, 1), np.nan)
    V = binned(pit); Q = binned(np.nan_to_num(q)); O = binned(out_) if out_ is not None else None
    H = binned(ch['hole']) if ch.get('hole') is not None else None
    on_share = np.bincount(idx, weights=on.astype(np.float64), minlength=nb) / np.maximum(cnt, 1)
    full = (on_share > 0.99) & (cnt > 0)
    # how long the pumps have been on, in bins; and the rate against its own ten-minute median
    since = np.zeros(nb)
    for k in range(nb):
        since[k] = since[k - 1] + 1 if (full[k] and k > 0) else (1.0 if full[k] else 0.0)
    steady = full & (since * BIN_S >= SETTLE_S + BIN_S)
    for k in range(nb):
        if steady[k]:
            w = Q[max(0, k - WINDOW_BINS):k + 1]
            m = float(np.nanmedian(w))
            if m <= 0 or abs(Q[k] - m) > 0.1 * m:
                steady[k] = False
    d10 = np.full(nb, np.nan)
    do = np.full(nb, np.nan)
    step = np.zeros(nb, bool)
    for k in range(WINDOW_BINS, nb):
        if steady[k] and steady[k - WINDOW_BINS:k + 1].all() and not np.isnan(V[k]) and not np.isnan(V[k - WINDOW_BINS]):
            d10[k] = V[k] - V[k - WINDOW_BINS]
            if O is not None and not np.isnan(O[k]) and not np.isnan(O[k - WINDOW_BINS]):
                lvl = float(np.nanmedian(O[k - WINDOW_BINS:k + 1]))
                if lvl > 1.0:
                    do[k] = (float(np.nanmean(O[k - 3:k + 1])) - float(np.nanmean(O[k - WINDOW_BINS:k - WINDOW_BINS + 4]))) / lvl
        if k > 0 and full[k] and full[k - 1] and not np.isnan(V[k]) and not np.isnan(V[k - 1]) and abs(V[k] - V[k - 1]) >= STEP_BBL:
            step[k] = True
    flagged = np.zeros(nb, np.int8)
    dev = np.full(nb, np.nan)
    hist: List[float] = []
    hk: List[int] = []
    for k in range(nb):
        if np.isnan(d10[k]):
            continue
        while hk and k - hk[0] > TREND_BINS:
            hk.pop(0); hist.pop(0)
        if step[k - WINDOW_BINS:k + 1].any():
            continue                                                                    # a window with a transfer in it is not the well
        if len(hist) >= 20:
            m, s = _mad(hist)
            lim = max(P['gain_sigma'] * s, P['gain_min_bbl'])
            dev[k] = d10[k] - m
            if dev[k] > lim and d10[k] >= 0.5 * P['gain_min_bbl']:                       # off its trend, and the pits themselves rose: a fall that slows is not a gain
                flagged[k] = 1
            elif -dev[k] > lim and d10[k] <= -0.5 * P['gain_min_bbl']:
                flagged[k] = -1
        if not flagged[k]:
            hist.append(float(d10[k])); hk.append(k)
    events: List[dict] = []
    k = 0
    while k < nb:
        if step[k]:
            j = k
            while j + 1 < nb and step[j + 1]:
                j += 1
            ch_ = float(V[j] - V[k - 1])
            events.append({'kind': 'TRANSFER', 't': float(t[0] + (k - 1) * BIN_S), 'minutes': round((j - k + 1) * BIN_S / 60.0, 1), 'change_bbl': round(ch_, 1),
                           'depth_ft': None if H is None or np.isnan(H[k]) else round(float(H[k]), 1)})
            k = j + 1
            continue
        if flagged[k]:
            sgn = int(flagged[k])
            j = k
            while j + 1 < nb and (flagged[j + 1] == sgn or (j + 2 < nb and flagged[j + 2] == sgn and not np.isnan(d10[j + 1]))):
                j += 1
            seg = slice(k, j + 1)
            peak = k + int(np.nanargmax(np.abs(np.where(np.isnan(dev[seg]), 0.0, dev[seg]))))
            a0 = max(0, k - WINDOW_BINS)
            ret = None if np.isnan(do[seg]).all() else float(np.nanmax(do[seg]) if sgn > 0 else np.nanmin(do[seg]))
            if ret is None:
                kind = 'GAIN' if sgn > 0 else 'FALL'
            elif sgn > 0:
                kind = 'KICK-LIKE' if ret >= P['returns_change'] else 'GAIN, RETURNS FLAT'
            else:
                kind = 'LOSSES' if ret <= -P['returns_change'] else 'FALL, RETURNS FLAT'
            events.append({'kind': kind, 't': float(t[0] + a0 * BIN_S), 'minutes': round((j - a0 + 1) * BIN_S / 60.0, 1), 'change_bbl': round(float(d10[peak]), 1),
                           'off_trend_bbl': round(float(dev[peak]), 1), 'returns_change': None if ret is None else round(ret, 3), 'rate_gpm': round(float(np.nanmedian(Q[seg])), 0),
                           'depth_ft': None if H is None or np.isnan(H[peak]) else round(float(H[peak]), 1), 'open': bool(j >= nb - 2)})
            k = j + 1
            continue
        k += 1
    return {'status': 'OK', 'events': events, 'steady_h': round(float(steady.sum()) * BIN_S / 3600.0, 1), 'judged_h': round(float((~np.isnan(d10)).sum()) * BIN_S / 3600.0, 1),
            'bins': {'t0': float(t[0]), 'bin_s': BIN_S, 'n': nb}}


def analyse_arrays(t: np.ndarray, t0: datetime, ch: Dict[str, Optional[np.ndarray]], P: dict) -> dict:
    """Both readings of one record. `ch`: q (pump output), pit (active volume, bbl), out (return flow), hole, bit."""
    stamp = lambda x: (t0 + timedelta(seconds=float(x))).strftime('%Y-%m-%dT%H:%M:%S')
    out: Dict[str, object] = {'samples': int(len(t)), 't_end': float(t[-1]) if len(t) else 0.0, 'record_start': stamp(t[0]) if len(t) else None, 'record_end': stamp(t[-1]) if len(t) else None,
                              'has': {k: ch.get(k) is not None and not np.isnan(ch[k]).all() for k in ('q', 'pit', 'out')}}
    if ch.get('q') is None or not out['has']['q']:
        out.update(status='NO PUMP RATE', why='the record has no pump output: neither reading can be made', flowbacks=[], circulating={'status': 'NO PUMP RATE', 'events': []})
        return out
    if not out['has']['pit']:
        out.update(status='NO PIT VOLUME', why='the record has no active pit volume: there is nothing to read a gain or a fall from', flowbacks=[], circulating={'status': 'NO PIT VOLUME', 'events': []})
        return out
    for k in ('pit', 'out'):
        if ch.get(k) is not None and np.isnan(ch[k]).all():
            ch[k] = None
    offs, on, thr = pumps_off(t, ch['q'])
    fb = flowbacks(t, ch, offs, P)
    for r in fb:
        r['time'] = stamp(r['t'])
    ci = circulating(t, ch, on, P)
    pt = np.array([o['t'] for o in offs]) if offs else np.zeros(0)
    for e in ci['events']:
        e['time'] = stamp(e['t'])
        e['t_end'] = e['t'] + e['minutes'] * 60.0
        nxt = pt[pt >= e['t']]
        e['pumps_off_after_min'] = round(float(nxt[0] - e['t_end']) / 60.0, 1) if len(nxt) and nxt[0] - e['t_end'] <= 1800.0 else None
    read = [r for r in fb if r['status'] in ('NORMAL', 'ABOVE', 'BELOW')]
    out.update(status='OK', pumps_on_gpm=round(thr, 1), flowbacks=fb, circulating=ci,
               counts={'pumps_off': len(fb), 'read': len(read), 'above': sum(1 for r in fb if r['status'] == 'ABOVE'), 'below': sum(1 for r in fb if r['status'] == 'BELOW'),
                       'transfer': sum(1 for r in fb if r['status'] == 'TRANSFER'), 'no_baseline': sum(1 for r in fb if r['status'] == 'NO BASELINE'),
                       **{k: sum(1 for e in ci['events'] if e['kind'] == k) for k in KINDS}})
    d120 = [r['dv']['120'] for r in read if '120' in r['dv']]
    out['typical_flowback_bbl'] = round(float(np.median(d120)), 1) if d120 else None
    return out


def _find(c, names: Tuple[str, ...]) -> Optional[str]:
    low = {n.lower(): n for n in c._ch}
    for want in names:
        if want in low:
            return low[want]
    for want in names:
        for l, n in low.items():
            if l.startswith(want + ' '):
                return n
    return None


def well(ws, well_id: str) -> dict:
    from . import drill_string as DS
    c = DS._cache(ws, well_id, build=True)
    P = settings(ws)
    key = (os.path.abspath(ws.path), well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'), tuple(P[k] for k in sorted(DEFAULTS)))
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    names = {k: _find(c, v) for k, v in NAMES.items()}
    ch = {k: (np.array(c.channel(n), dtype=np.float64) if n else None) for k, n in names.items()}
    notes = []
    for k in ('q', 'pit'):
        if names[k] and ch[k] is not None:
            u = (c.unit(names[k]) or '').lower()
            if k == 'pit' and u in ('m3', 'm^3'):
                ch[k] = ch[k] * 6.289811
            if k == 'q' and u in ('l/min', 'lpm'):
                ch[k] = ch[k] / 3.785412
    if names['out']:
        notes.append(f"the return flow is {names['out']} ({c.unit(names['out']) or 'no unit'}): read only against itself")
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    r = analyse_arrays(np.asarray(c.index, dtype=np.float64), t0, ch, P)
    w = ws.well(well_id)
    r.update(well=well_id, display=w.get('display') or well_id, source_mtime=c.meta.get('source_mtime'), channels={k: v for k, v in names.items() if v}, notes=notes)
    with _LOCK:
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = r
    return r


def trace(ws, well_id: str, t_from: float, t_to: float, n: int = 400) -> dict:
    """The pit volume, the pump output and the return flow between two moments of a record, for a chart."""
    from . import drill_string as DS
    c = DS._cache(ws, well_id, build=True)
    t = np.asarray(c.index, dtype=np.float64)
    a, b = int(np.searchsorted(t, t_from)), int(np.searchsorted(t, t_to, side='right'))
    k = max(1, (b - a) // n)
    out = {'t': [round(float(x - t_from), 1) for x in t[a:b:k]]}
    for key, names in NAMES.items():
        if key in ('hole', 'bit'):
            continue
        nm = _find(c, names)
        if nm:
            v = np.asarray(c.channel(nm), dtype=np.float64)[a:b:k]
            out[key] = [None if math.isnan(x) else round(float(x), 2) for x in v]
    return out


def describe(e: dict) -> str:
    if 'dv' in e:
        fp = (e.get('fingerprint') or {}).get(str(e.get('read_at_s')), {})
        s = (f"flowback {e['dv'].get(str(e.get('read_at_s')), 0):+.1f} bbl {e.get('read_at_s')} s after the pumps stopped, against {fp.get('median', 0):+.1f} for the "
             f"{fp.get('n', 0)} connections before it ({e.get('excess_bbl', 0):+.1f} bbl)")
        if e.get('returns_running') is True:
            s += '; the return line still running above its own fingerprint'
        elif e.get('returns_running') is False:
            s += '; the return line down as usual'
        return s
    if e['kind'] == 'TRANSFER':
        return f"{e['change_bbl']:+.1f} bbl in {e['minutes']:g} min: mud moved at surface"
    s = f"{e['change_bbl']:+.1f} bbl in its ten minutes of greatest change"
    if e.get('off_trend_bbl') is not None:
        s += f" ({e['off_trend_bbl']:+.1f} bbl off the trend of the two hours before), off its trend for {e['minutes']:g} min"
    if e.get('returns_change') is not None:
        s += f", the returns {e['returns_change'] * 100:+.0f} %"
    if e.get('pumps_off_after_min') is not None:
        s += f"; the pumps were stopped {e['pumps_off_after_min']:g} min after it"
    return s


def _mtime_s(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None


def live_events(ws, now: Optional[datetime] = None) -> List[dict]:
    """What an alert is raised for: in a record still being written, a flowback above its fingerprint or a kick-like
    gain or losses in its last half hour."""
    from . import dysfunction as DY
    from . import series_cache as SCH
    out = []
    ts = (now or datetime.now(timezone.utc)).timestamp()
    for w in ws.wells():
        wid = w['id']
        try:
            if ts - os.path.getmtime(SCH.source_path(ws, wid)) > DY.LIVE_SOURCE_S:
                continue
            r = well(ws, wid)
        except Exception:
            continue
        if r.get('status') != 'OK' or not DY.is_live(r, now):
            continue
        for e in r['flowbacks']:
            if e['status'] == 'ABOVE' and r['t_end'] - e['t'] <= LIVE_EVENT_S:
                out.append({**e, 'well_id': wid, 'well': r['display'], 'event': EVENTS['flowback'], 'key': f"{EVENTS['flowback']}:{wid}:{e['time']}", 'detail': describe(e)})
        for e in r['circulating']['events']:
            if e['kind'] in ('KICK-LIKE', 'LOSSES') and (e.get('open') or r['t_end'] - e['t'] <= LIVE_EVENT_S):
                ev = EVENTS['gain' if e['kind'] == 'KICK-LIKE' else 'loss']
                out.append({**e, 'well_id': wid, 'well': r['display'], 'event': ev, 'key': f"{ev}:{wid}:{e['time']}", 'detail': describe(e)})
    return out


def page(ws, well_id: Optional[str] = None, now: Optional[datetime] = None, full: bool = True) -> dict:
    """Every well's counts and events. With `full` off, only the chosen well keeps its long lists (for a screen)."""
    from . import dysfunction as DY
    P = settings(ws)
    wells = []
    for w in ws.wells():
        try:
            r = well(ws, w['id'])
        except Exception as e:
            if type(e).__name__ in ('EdrError', 'FileNotFoundError', 'KeyError', 'WorkspaceError', 'ValueError'):
                continue
            raise
        wells.append(r)
    out = {'status': 'OK' if wells else 'NO DRILLING RECORD', 'generated_utc': _utc(now), 'settings': {k: P[k] for k in DEFAULTS}, 'settings_source': P.get('source') or '',
           'bounds': BOUNDS, 'words': WORDS, 'kinds': KINDS, 'taus': [int(x) for x in TAUS], 'not_a_measurement': NOT_A_MEASUREMENT, 'title': ws.manifest.get('name') or 'this workspace'}
    rows = []
    lines = []
    for r in wells:
        c = r.get('counts') or {}
        row = {'well': r['well'], 'display': r['display'], 'status': r['status'], 'why': r.get('why'), 'record_start': r.get('record_start'), 'record_end': r.get('record_end'),
               'channels': r.get('channels'), 'notes': r.get('notes') or [], 'counts': c, 'typical_flowback_bbl': r.get('typical_flowback_bbl'), 'live': False,
               'steady_h': (r.get('circulating') or {}).get('steady_h'), 'judged_h': (r.get('circulating') or {}).get('judged_h'), 'has': r.get('has')}
        if r['status'] == 'OK':
            row['live'] = DY.is_live(r, now)
            row['flowbacks'] = [{k: v for k, v in f.items() if k != 't'} | {'t': f['t']} for f in r['flowbacks']]
            row['events'] = [{**e, 'reads': describe(e)} for e in r['circulating']['events']]
            lines.append(f"{r['display']} ({(r['record_start'] or '')[:10]} to {(r['record_end'] or '')[:10]}): {c['pumps_off']} pumps-offs, {c['read']} read against a fingerprint"
                         + (f" (typically {r['typical_flowback_bbl']:+.1f} bbl back after two minutes)" if r.get('typical_flowback_bbl') is not None else '')
                         + f": {c['above']} above it, {c['below']} below it, {c['transfer']} with a transfer in them; circulating, {row['judged_h']:g} h judged: "
                         + f"{c['KICK-LIKE']} kick-like, {c['GAIN, RETURNS FLAT'] + c['GAIN']} other gain(s), {c['LOSSES']} losses, {c['FALL, RETURNS FLAT'] + c['FALL']} other fall(s), {c['TRANSFER']} transfer(s)"
                         + ('' if r['has']['out'] else ' - no return flow in this record, so a gain cannot be told from mud added') + '.')
        else:
            lines.append(f"{r['display']}: not judged - {r.get('why')}.")
        rows.append(row)
    out['wells'] = rows
    if not wells:
        lines.append('No well has a drilling recorder export attached.')
    else:
        lines.append('An event is a place in the record to look at. A flowback above its fingerprint, or a gain with the returns up, is what a kick looks like in these two channels; '
                     'a flow check and the shut-in pressures say whether it was one.')
    out['lines'] = lines
    cur = next((r for r in rows if r['well'] == well_id), None) or next((r for r in rows if r['status'] == 'OK'), None)
    out['well'] = cur['well'] if cur else None
    if not full:
        for r in rows:
            if r['well'] != out['well'] and r['status'] == 'OK':
                r['flowbacks'] = [f for f in r['flowbacks'] if f['status'] == 'ABOVE']
                r['events'] = [e for e in r['events'] if e['kind'] in ('KICK-LIKE', 'LOSSES')]
    try:
        from . import public_pad as PP
        cr = PP.credit(ws)
        if cr and wells:
            out['public_source'] = cr
            out['lines'].append(cr['line'])
    except Exception:
        pass
    return out


def text(p: dict) -> str:
    L = ['Kick and loss detection, replayed over the record', '=' * 49] + list(p.get('lines') or [])
    for r in p.get('wells') or []:
        if r['status'] != 'OK':
            continue
        for f in r.get('flowbacks') or []:
            if f['status'] == 'ABOVE':
                L.append(f"  {r['display'][:18]:<19} {f['time'][:16].replace('T', ' ')}  flowback above      {'' if f.get('depth_ft') is None else format(f['depth_ft'], ',.0f') + ' ft: '}{describe(f)}")
        ev = [e for e in r.get('events') or [] if e['kind'] in ('KICK-LIKE', 'LOSSES')]
        rest = sorted((e for e in r.get('events') or [] if e['kind'] in ('GAIN', 'FALL')), key=lambda e: -abs(e.get('off_trend_bbl') or 0))
        for e in sorted(ev + rest[:10], key=lambda e: e['time']):
            L.append(f"  {r['display'][:18]:<19} {e['time'][:16].replace('T', ' ')}  {e['kind'].lower():<19} {'' if e.get('depth_ft') is None else format(e['depth_ft'], ',.0f') + ' ft: '}{e['reads']}")
        if len(rest) > 10:
            L.append(f"  {r['display']}: and {len(rest) - 10} smaller gain(s) and fall(s) this record cannot name, in the report")
        for n in r.get('notes') or []:
            L.append(f"  note: {r['display']}: {n}")
    return '\n'.join(L)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'kick')
    with open(os.path.join(d, 'kick.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **p}, f, indent=1, default=str)
    with open(os.path.join(d, 'kick.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Kick and loss detection, replayed over the record\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines'])
                + '\n\n```\n' + text(p) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'flowbacks.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'time', 'depth_ft', 'rate_before_gpm', 'off_s'] + [f'gain_{int(x)}s_bbl' for x in TAUS] + ['status', 'excess_bbl', 'read_at_s', 'returns_running', 'why'])
        for r in p['wells']:
            for x in r.get('flowbacks') or []:
                wr.writerow([r['display'], x['time'], x.get('depth_ft', ''), x['rate_before'], x['off_s']] + [x['dv'].get(str(int(k)), '') for k in TAUS]
                            + [x['status'], x.get('excess_bbl', ''), x.get('read_at_s', ''), '' if x.get('returns_running') is None else x['returns_running'], x.get('why') or ''])
    with open(os.path.join(d, 'events.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'time', 'kind', 'depth_ft', 'minutes', 'change_bbl', 'off_trend_bbl', 'returns_change', 'rate_gpm', 'pumps_off_after_min'])
        for r in p['wells']:
            for e in r.get('events') or []:
                wr.writerow([r['display'], e['time'], e['kind'], e.get('depth_ft', ''), e['minutes'], e['change_bbl'], e.get('off_trend_bbl', ''), '' if e.get('returns_change') is None else e['returns_change'],
                             e.get('rate_gpm', ''), '' if e.get('pumps_off_after_min') is None else e['pumps_off_after_min']])
    files = ['kick.json', 'kick.md', 'flowbacks.csv', 'events.csv']
    ws.audit(actor, 'kick.report', {'files': files, 'wells': [r['well'] for r in p['wells']]})
    return {'folder': d, 'files': files, 'page': p}


# -- a labelled record ---------------------------------------------------------------------------------------------------
def synthetic_record(faults: bool = True, seed: int = 5, stands: int = 30, ends_now: bool = False, returns: bool = True) -> dict:
    """The drilling alerts' labelled record with a pit volume and a return flow added: at each pumps-off the pits rise
    8 bbl toward a limit (half of it in 45 s) and the return flow dies away in 20 s; while drilling the pits fall
    0.07 bbl a foot. With `faults`: at stand 12's connection the well flows - 3 bbl a minute more for as long as the
    pumps are off, the return line holding a fifth of its flow; at stand 20's the flowback is 5 bbl short; during
    stand 15 a 30 bbl transfer in 20 s; during stand 17 a gain of 14 bbl over eight minutes with the returns up 15 %
    (KICK-LIKE); during stand 23 the same gain with the returns flat; during stand 26 a fall of 16 bbl over eight
    minutes with the returns down 15 %. SIMULATION."""
    from . import dysfunction as DY
    rec = DY.synthetic_record(False, seed=seed, stands=stands, ends_now=ends_now)
    rng = np.random.default_rng(seed + 100)
    t = rec['t']; q = rec['ch']['flow']; hole = rec['ch']['hole']
    n = len(t)
    on = q > 30.0
    pit = np.zeros(n)
    out_ = np.zeros(n)
    base = 400.0
    v = base
    k_off = None
    conn = 0
    drill_start: Dict[int, int] = {}
    stand = 0
    truth = {'flowing_at': None, 'short_at': None, 'transfer_at': None, 'kick_at': None, 'gain_flat_at': None, 'loss_at': None}
    extra = np.zeros(n)                # volume the faults add, cumulative
    ret = np.ones(n)                   # the return flow as a share of the pump rate
    was_drilling = False
    for i in range(n):
        d = bool(rec['drilling'][i])
        if d and not was_drilling:
            stand += 1
            drill_start[stand] = i
        was_drilling = d
    for st_, i0 in drill_start.items():
        if not faults:
            break
        if st_ == 15:
            extra[i0 + 300:] += 30.0 * np.clip((np.arange(n - i0 - 300)) / 20.0, 0, 1); truth['transfer_at'] = float(t[i0 + 300])
        if st_ in (17, 23):
            extra[i0 + 600:] += 14.0 * np.clip((np.arange(n - i0 - 600)) / 480.0, 0, 1)
            if st_ == 17:
                ret[i0 + 600:i0 + 1300] *= 1.0 + 0.15 * np.clip(np.arange(700) / 300.0, 0, 1)[:max(0, min(700, n - i0 - 600))]
                truth['kick_at'] = float(t[i0 + 600])
            else:
                truth['gain_flat_at'] = float(t[i0 + 600])
        if st_ == 26:
            extra[i0 + 600:] -= 16.0 * np.clip((np.arange(n - i0 - 600)) / 480.0, 0, 1)
            ret[i0 + 600:i0 + 1300] *= 1.0 - 0.15 * np.clip(np.arange(700) / 300.0, 0, 1)[:max(0, min(700, n - i0 - 600))]
            truth['loss_at'] = float(t[i0 + 600])
    fb = 0.0
    flow_extra = 0.0
    tail = 0.0
    for i in range(n):
        if i and on[i - 1] and not on[i]:
            k_off = i; conn += 1
            if faults and conn == 12:
                truth['flowing_at'] = float(t[i])
            if faults and conn == 20:
                truth['short_at'] = float(t[i])
        if not on[i] and k_off is not None:
            tau = float(t[i] - t[k_off])
            lim = 8.0 - (5.0 if (faults and conn == 20) else 0.0)
            fb = lim * (1.0 - 0.5 ** (tau / 45.0))
            flow_extra = 3.0 * tau / 60.0 if (faults and conn == 12) else 0.0
            tail = math.exp(-tau / 7.0) + (0.2 if (faults and conn == 12) else 0.0)
        elif on[i]:
            if k_off is not None and i and not on[i - 1]:
                base += flow_extra                                                   # what the well gave stays in the pits
                k_off_on = i
            if k_off is not None:
                fb = fb * 0.97 if fb > 0.01 else 0.0                                 # the lines fill again
            flow_extra = 0.0
            tail = 1.0
        pit[i] = base + fb + flow_extra - 0.07 * (hole[i] - hole[0]) + extra[i]
        out_[i] = 55.0 * (q[i] / 550.0 if on[i] else 0.0) * ret[i] + (55.0 * tail if not on[i] and k_off is not None else 0.0)
    pit = pit + rng.normal(0.0, 0.15, n)
    out_ = np.clip(out_ + rng.normal(0.0, 0.4, n), 0.0, None)
    rec['ch']['pit'] = pit
    rec['ch']['out'] = out_ if returns else None
    rec['ch']['q'] = q
    rec['truth_kick'] = truth
    return rec


def write_edr(rec: dict, path: str) -> str:
    """The labelled record as a recorder's export, with the pit volume and the return flow as a recorder names them."""
    ch = rec['ch']; t0 = rec['t0']
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Hook Load (klbs)', 'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'On Bottom (unitless)', 'Total Mud Volume (barrels)'] \
        + (['Flow (flow_percent)'] if ch.get('out') is not None else [])
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(hdr)
        d = rec['drilling']
        for i in range(len(d)):
            ts = t0 + timedelta(seconds=float(rec['t'][i]))
            row = [ts.strftime('%Y/%m/%d'), ts.strftime('%H:%M:%S'), f"{ch['hole'][i]:.2f}", f"{ch['bit'][i]:.2f}", f"{150.0 if d[i] else 0.0:.1f}", f"{20.0 if d[i] else 0.0:.1f}", f"{ch['rpm'][i]:.1f}",
                   f"{ch['spp'][i]:.1f}", f"{ch['hookload'][i]:.1f}", f"{ch['diff'][i]:.1f}", f"{ch['flow'][i]:.1f}", f"{ch['block'][i]:.2f}", str(int(ch['hole'][i] - ch['bit'][i] < 0.5)), f"{ch['pit'][i]:.2f}"]
            if ch.get('out') is not None:
                row.append(f"{ch['out'][i]:.2f}")
            w.writerow(row)
    return path


def analyse_record(rec: dict, P: Optional[dict] = None) -> dict:
    ch = {'q': rec['ch']['q'], 'pit': rec['ch'].get('pit'), 'out': rec['ch'].get('out'), 'hole': rec['ch']['hole'], 'bit': rec['ch']['bit']}
    return analyse_arrays(rec['t'], rec['t0'], ch, {**DEFAULTS, **(P or {})})


def selftest() -> dict:
    checks = []
    rec = synthetic_record(True)
    tr = rec['truth_kick']
    r = analyse_record(rec)
    fb = r['flowbacks']
    near = lambda rows, x, tol=5.0: [k for k, e in enumerate(rows) if abs(e['t'] - x) <= tol]
    kf = near(fb, tr['flowing_at']); ks = near(fb, tr['short_at'])
    above = [k for k, e in enumerate(fb) if e['status'] == 'ABOVE']; below = [k for k, e in enumerate(fb) if e['status'] == 'BELOW']
    checks.append({'check': 'the connection where the well flows is the one flowback above its fingerprint, with the return line still running', 'value': [above, kf, fb[kf[0]].get('excess_bbl') if kf else None],
                   'pass': above == kf and len(kf) == 1 and fb[kf[0]]['returns_running'] is True and fb[kf[0]]['excess_bbl'] > 5.0})
    checks.append({'check': 'the connection whose flowback is 5 bbl short is the one below it', 'value': [below, ks, fb[ks[0]].get('excess_bbl') if ks else None],
                   'pass': below == ks and len(ks) == 1 and -6.5 <= fb[ks[0]]['excess_bbl'] <= -3.5})
    normal = [e for e in fb if e['status'] == 'NORMAL']
    d120 = [e['dv']['120'] for e in normal if '120' in e['dv']]
    checks.append({'check': 'every other connection is read and is normal; the fingerprint is the rig\'s own 6.7 bbl at two minutes', 'value': [len(fb), len(normal), r['counts']['no_baseline'], r['typical_flowback_bbl']],
                   'pass': len(normal) + 2 + r['counts']['no_baseline'] + r['counts']['transfer'] == len(fb) and r['counts']['no_baseline'] == FINGERPRINT_MIN and abs(float(np.median(d120)) - 8.0 * (1 - 0.5 ** (120 / 45.0))) < 0.5})
    ev = r['circulating']['events']
    kind_at = lambda x: [e['kind'] for e in ev if e['t'] - 60.0 <= x <= e['t_end'] + 60.0]
    checks.append({'check': 'a gain with the returns up is kick-like; the same gain with the returns flat is not; a fall with the returns down is losses; a step is a transfer', 'value': [[e['kind'] for e in ev]],
                   'pass': kind_at(tr['kick_at']) == ['KICK-LIKE'] and kind_at(tr['gain_flat_at']) == ['GAIN, RETURNS FLAT'] and kind_at(tr['loss_at']) == ['LOSSES'] and 'TRANSFER' in kind_at(tr['transfer_at'])
                           and sorted(e['kind'] for e in ev) == ['GAIN, RETURNS FLAT', 'KICK-LIKE', 'LOSSES', 'TRANSFER']})
    kk = next((e for e in ev if e['kind'] == 'KICK-LIKE'), {})
    checks.append({'check': 'the kick-like gain is sized: some 14 bbl off the trend, the returns up some 15 %', 'value': [kk.get('off_trend_bbl'), kk.get('returns_change'), kk.get('minutes')],
                   'pass': bool(kk) and 9.0 <= kk['off_trend_bbl'] <= 16.0 and 0.10 <= kk['returns_change'] <= 0.20})
    r0 = analyse_record(synthetic_record(False))
    checks.append({'check': 'a record with none of it has no event and no flowback off its fingerprint', 'value': [r0['counts']['above'], r0['counts']['below'], len(r0['circulating']['events'])],
                   'pass': r0['counts']['above'] == 0 and r0['counts']['below'] == 0 and not r0['circulating']['events'] and r0['counts']['read'] >= 20})
    rn = analyse_record(synthetic_record(True, returns=False))
    kn = [e['kind'] for e in rn['circulating']['events'] if e['kind'] != 'TRANSFER']
    checks.append({'check': 'without a return flow the gains and the fall are found and not named', 'value': kn, 'pass': kn == ['GAIN', 'GAIN', 'FALL'] and rn['counts']['above'] == 1 and rn['flowbacks'][near(rn['flowbacks'], tr['flowing_at'])[0]].get('returns_running') is None})
    nop = synthetic_record(False); nop['ch']['pit'] = None
    rp = analyse_record(nop)
    noq = synthetic_record(False); noq['ch']['q'] = np.full(len(noq['t']), np.nan)
    rq = analyse_record(noq)
    checks.append({'check': 'a record with no pit volume, or no pump rate, is said not to be judged', 'value': [rp['status'], rq['status']], 'pass': rp['status'] == 'NO PIT VOLUME' and rq['status'] == 'NO PUMP RATE'})
    hard = analyse_record(rec, {'flowback_min_bbl': 50.0, 'gain_min_bbl': 100.0})
    checks.append({'check': 'the thresholds are the settings: raised out of reach, nothing is flagged', 'value': [hard['counts']['above'], [e['kind'] for e in hard['circulating']['events'] if e['kind'] != 'TRANSFER']],
                   'pass': hard['counts']['above'] == 0 and hard['counts']['below'] == 0 and not [e for e in hard['circulating']['events'] if e['kind'] != 'TRANSFER']})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION: a record built with each event at a known place'}
