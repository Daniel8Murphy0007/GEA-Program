# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""dysfunction - three things a drilling record shows before the crew has to deal with them: the off-bottom
pressure falling at a steady flow (a washout), the differential pressure spiking with the bit stopped (a motor
stall), and the pick-up weight leaving its own trend connection after connection (the hole getting tight).

Each is an old driller's rule, written down so it is applied to every stand of every well the same way, and each
is replayed over the whole of a well's record so the page shows what it would have said and when - beside what
the rig did next.

- WASHOUT. At each drilling connection the string is picked up off bottom with the pumps still at the drilling
  rate: that pressure is the string, the motor and the bit with no work in it. It should creep up as the hole
  gets longer. The reading at each connection is the median standpipe pressure of those seconds (bit a foot or
  more off bottom, flow within 5 % of the drilling rate, before the slips are set - after them the pressure is
  still building as the string fills, and is no reading); it is compared with the median of the five comparable
  readings before it in the same bit run, each brought to the same flow by pressure ~ flow^1.8 (and only readings
  within 10 % of that flow are compared at all). A reading 6 % and 100 psi or more below is FLAGGED; two flagged
  readings running open an event; the readings before it stay the baseline until it ends - at the trip out of the
  hole, at the pressure coming back, or at the end of the record. Where fewer than half the connections give such
  a reading (a crew that stops the pumps before picking up) the recorder's own zero is used instead: standpipe
  less differential pressure while drilling, which is the off-bottom pressure the recorder was last zeroed at.
- MOTOR STALL. While on bottom with the pumps on, the differential pressure is compared with its own median over
  the five minutes before (in ten-second blocks, the last half minute left out, and only once the bit has been on
  bottom for 200 s of those five minutes). A rise of 300 psi and 60 % of that median or more, reached within half
  a minute, over in a minute and a half, under which the hole made a quarter or less of the footage it had been
  making, is a STALL. A rise the bit drilled on under is weight or harder rock, and is counted apart. Needs a
  record every five seconds or faster.
- TIGHT HOLE. At each connection the pick-up weight is the median hook load while the string moves up before the
  slips are set, the slack-off weight the median while it moves down after they are pulled - the string rotating
  and not rotating kept apart, because rotation takes the drag out of the hook load. Each is set against a line
  through the previous fifteen connections of its own kind in the same bit run (Theil-Sen, against depth); the
  scatter is that line's own (1.48 x the median absolute residual, 2 klbf at least). A pick-up 3 scatters and 10
  klbf or more above the line, or a slack-off as far below, is FLAGGED; two flagged in three connections open an
  event, which ends when three running are back on the line, at a trip, after twelve connections (a new level,
  not a trend) or at the end of the record.

The numbers 6 %, 100 psi, 60 %, 300 psi, 3 scatters and 10 klbf are settings (`records/dysfunction.json`), each
changed with its source. An event at the end of a record that is still being written raises an alert
(`drilling.washout`, `drilling.stall`, `drilling.tight`) once.

What it will not call a measurement: a washout - the pressure fell at a steady flow, which is also a pump losing
a valve, a lighter mud, a lost nozzle or a gauge; the trip finds which, and the page prints whether a trip
followed; a stall - a pressure spike the bit stopped under, the motor's own report says what it did to the motor;
stuck pipe - a pick-up weight above its own trend, which is cuttings, a ledge, a dogleg after a slide or the mud,
and the torque-and-drag tab holds the model of what it should be; and a rule's silence - no event means the rule
did not fire on this record, not that the string was sound.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {'washout_fall': 0.06, 'washout_min_psi': 100.0, 'stall_rise': 0.6, 'stall_min_psi': 300.0, 'drag_sigma': 3.0, 'drag_min_klbf': 10.0}
BOUNDS = {'washout_fall': (0.02, 0.5), 'washout_min_psi': (20.0, 2000.0), 'stall_rise': (0.2, 5.0), 'stall_min_psi': (50.0, 3000.0), 'drag_sigma': (1.5, 10.0), 'drag_min_klbf': (2.0, 200.0)}
WORDS = {'washout_fall': 'the fall in off-bottom pressure that flags a reading, as a fraction', 'washout_min_psi': 'and the least fall in psi',
         'stall_rise': 'the rise in differential pressure over its median that is a stall, as a fraction', 'stall_min_psi': 'and the least rise in psi',
         'drag_sigma': 'how many scatters off its trend a pick-up or slack-off weight is flagged at', 'drag_min_klbf': 'and the least distance in klbf'}
FLOW_EXP = 1.8
WASH_BASE, WASH_MIN_BASE = 5, 3
DRAG_BASE, DRAG_MIN_BASE, DRAG_MAX_EVENT = 15, 6, 12
STALL_MAX_CADENCE_S = 5.0
STALL_MAX_S = 90.0
LIVE_SOURCE_S, LIVE_EVENT_S, LIVE_RECORD_S = 7200.0, 1800.0, 2 * 86400.0
EVENTS = {'washout': 'drilling.washout', 'stall': 'drilling.stall', 'tight': 'drilling.tight'}
_NAMES = {'diff': ('differential pressure', 'diff press', 'diff'), 'wob': ('weight on bit', 'wob')}
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


class DysfunctionError(ValueError):
    pass


def _utc(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------------------------------------------
# the settings
# --------------------------------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.path, 'records', 'dysfunction.json')


def settings(ws) -> dict:
    s = {**DEFAULTS, 'sources': {}}
    p = _path(ws)
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                s[k] = float(d[k])
        s['sources'] = dict(d.get('sources') or {})
    return s


def set_settings(ws, actor: str, source: str = '', **kw) -> dict:
    s = settings(ws)
    changed = {}
    for k, v in kw.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if k not in DEFAULTS:
            raise DysfunctionError(f'no setting named {k}: {", ".join(DEFAULTS)}')
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise DysfunctionError(f'{k} must be a number')
        lo, hi = BOUNDS[k]
        if not (lo <= x <= hi):
            raise DysfunctionError(f'{k} must be between {lo:g} and {hi:g} ({WORDS[k]})')
        if x != s[k]:
            changed[k] = [s[k], x]; s[k] = x
            if x == DEFAULTS[k]:
                s['sources'].pop(k, None)
    if not changed:
        return s
    if any(s[k] != DEFAULTS[k] for k in changed) and not (source or '').strip():
        raise DysfunctionError('a changed setting needs its source: who set it and on what grounds')
    for k in changed:
        if s[k] != DEFAULTS[k]:
            s['sources'][k] = source.strip()
    os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
    with open(_path(ws), 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1, sort_keys=True)
    ws.audit(actor, 'dysfunction.settings', {'changed': changed, 'source': source})
    return s


# --------------------------------------------------------------------------------------------------------------
# washout: the off-bottom pressure at each connection
# --------------------------------------------------------------------------------------------------------------
def _i(t: np.ndarray, x: float) -> int:
    return int(min(np.searchsorted(t, x), len(t) - 1))


def washout_readings(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], conns: List[dict], drilling: np.ndarray) -> dict:
    """One reading a connection: the standpipe pressure with the bit off bottom and the pumps still at the drilling rate
    before the slips are set (after them the pressure is still building as the string fills, and is no reading), and
    the recorder's own zero (standpipe less differential pressure) over the ten minutes of drilling after it."""
    spp, flow, bit, hole, diff = (ch.get(k) for k in ('spp', 'flow', 'bit', 'hole', 'diff'))
    if spp is None or flow is None or bit is None or hole is None:
        return {'status': 'NO CHANNELS', 'readings': [], 'why': 'needs the standpipe pressure, the pump output, the bit depth and the hole depth'}
    off = (np.nan_to_num(hole) - np.nan_to_num(bit)) >= 1.0
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    need = max(3, int(round(10.0 / cad)))
    out = []
    for c in conns:
        i_off = _i(t, c['t_off']); i_set = _i(t, c['t_off'] + c['pre_min'] * 60.0)
        i_pull = _i(t, c['t_off'] + (c['pre_min'] + c['slips_min']) * 60.0); i_on = _i(t, c['t_off'] + c['w2w_min'] * 60.0)
        a = _i(t, c['t_off'] - 600.0)
        dsel = drilling[a:i_off + 1] & (flow[a:i_off + 1] > 0)
        if dsel.sum() < need:
            continue
        qd = float(np.median(flow[a:i_off + 1][dsel]))
        r = {'t': float(c['t_off']), 'time': c.get('time'), 'depth_ft': c.get('depth_ft'), 'drilling_flow': round(qd, 1), 'psi': None, 'flow': None, 'how': None, 'zero_psi': None}
        lo, hi = _i(t, c['t_off'] + 5.0), i_set                          # the first five seconds off bottom the motor is still unloading
        if hi > lo:
            sel = off[lo:hi] & (np.abs(flow[lo:hi] / qd - 1.0) <= 0.05) & (spp[lo:hi] > 0)
            if sel.sum() >= need:
                r.update(psi=round(float(np.median(spp[lo:hi][sel])), 1), flow=round(float(np.median(flow[lo:hi][sel])), 1), how='before the slips', seconds=round(float(sel.sum() * cad), 0))
        if diff is not None:
            z = _i(t, t[i_on] + 600.0)
            sel = drilling[i_on:z] & ~np.isnan(diff[i_on:z]) & (np.abs(flow[i_on:z] / qd - 1.0) <= 0.05)
            if sel.sum() >= need:
                r['zero_psi'] = round(float(np.median((spp[i_on:z] - diff[i_on:z])[sel])), 1)
                r['zero_flow'] = round(float(np.median(flow[i_on:z][sel])), 1)
        out.append(r)
    n_meas = sum(1 for r in out if r['psi'] is not None); n_zero = sum(1 for r in out if r['zero_psi'] is not None)
    use = 'measured' if n_meas >= max(WASH_MIN_BASE + 2, 0.5 * len(out)) else ('zero' if n_zero >= max(WASH_MIN_BASE + 2, 0.5 * len(out)) else None)
    return {'status': 'OK' if use else 'TOO FEW READINGS', 'use': use, 'readings': out, 'connections': len(conns), 'measured': n_meas, 'zeroed': n_zero,
            'why': None if use else f'{n_meas} of {len(conns)} connections have ten seconds off bottom at the drilling rate, and {n_zero} a recorder zero'}


def washout(readings: List[dict], trips: List[dict], use: str, P: dict, t_end: float) -> List[dict]:
    """The events in a list of readings (each a dict with t, psi or zero_psi, flow). Marks each reading it judged."""
    pk, qk = ('psi', 'flow') if use == 'measured' else ('zero_psi', 'zero_flow')
    outs = sorted(x['t_start'] for x in trips if x.get('direction') == 'OUT')
    events: List[dict] = []
    base: List[dict] = []; pending: List[dict] = []; ev: Optional[dict] = None; back = 0
    run = 0

    def close(why: str, at: Optional[float] = None):
        nonlocal ev, back
        if ev is not None:
            ev['ended'] = why
            if at is not None:
                ev['trip_after_h'] = round((at - ev['t']) / 3600.0, 2)
            events.append(ev)
        ev = None; back = 0

    for r in readings:
        if r.get(pk) is None or not r.get(qk):
            continue
        nrun = int(np.searchsorted(outs, r['t']))
        if nrun != run:                                   # a trip out of the hole between this reading and the last: a new string, a new baseline
            close('a trip out of the hole followed', outs[run] if run < len(outs) else None)
            base, pending, run = [], [], nrun
        comp = [b[pk] * (r[qk] / b[qk]) ** FLOW_EXP for b in base if abs(b[qk] / r[qk] - 1.0) <= 0.10]
        if len(comp) < WASH_MIN_BASE:
            if ev is None:
                base.append(r)
            continue
        b0 = float(np.median(comp[-WASH_BASE:]))
        fall = 1.0 - r[pk] / b0
        flagged = fall >= P['washout_fall'] and (b0 - r[pk]) >= P['washout_min_psi']
        r.update(baseline_psi=round(b0, 1), fall=round(fall, 4), flagged=bool(flagged))
        if ev is not None:
            if fall < P['washout_fall'] / 2.0:
                back += 1
                if back >= 2:
                    close('the pressure came back'); base.append(r)
            else:
                back = 0
                ev['readings'] += 1; ev['t_last'] = r['t']
                if fall > ev['fall']:
                    ev.update(fall=round(fall, 4), fall_psi=round(b0 - r[pk], 0))
            continue
        if flagged:
            pending.append(r)
            if len(pending) >= 2:
                f = max(pending, key=lambda x: x['fall'])
                ev = {'kind': 'washout', 't': pending[0]['t'], 'time': pending[0].get('time'), 'depth_ft': pending[0].get('depth_ft'), 'readings': len(pending), 't_last': r['t'],
                      'baseline_psi': f['baseline_psi'], 'fall': f['fall'], 'fall_psi': round(f['baseline_psi'] - f[pk], 0), 'flow': r[qk], 'ended': None}
                pending = []
        else:
            pending = []
            base.append(r)
    if ev is not None:
        nxt = [o for o in outs if o > ev['t_last']]
        if nxt:
            close('a trip out of the hole followed', nxt[0])
        else:
            ev['open'] = True
            close('open at the end of the record')
    return events


# --------------------------------------------------------------------------------------------------------------
# motor stall: the differential pressure against its own last five minutes
# --------------------------------------------------------------------------------------------------------------
def stalls(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], pumps: np.ndarray, sliding: np.ndarray, P: dict) -> dict:
    diff, bit, hole = (ch.get(k) for k in ('diff', 'bit', 'hole'))
    if diff is None or bit is None or hole is None:
        return {'status': 'NO CHANNELS', 'events': [], 'why': 'needs the differential pressure, the bit depth and the hole depth'}
    n = len(t)
    cad = float(np.median(np.diff(t))) if n > 1 else 1.0
    if cad > STALL_MAX_CADENCE_S:
        return {'status': 'TOO COARSE', 'events': [], 'why': f'the record is every {cad:g} s; a stall is over in a few seconds, so this needs every {STALL_MAX_CADENCE_S:g} s or faster'}
    hole0 = np.nan_to_num(hole); gap = hole0 - np.nan_to_num(bit)
    onb = pumps & (gap < 1.0) & ~np.isnan(diff)
    blk = np.floor((t - t[0]) / 10.0).astype(np.int64)
    nb = int(blk[-1]) + 1
    dt = np.diff(t, append=t[-1] + cad); dt = np.where((dt > 0) & (dt <= 60.0), dt, cad)
    db = np.diff(hole0, prepend=hole0[0]); db = np.where((db > 0) & (db < 5.0), db, 0.0)
    d0 = np.where(onb, np.nan_to_num(diff), 0.0)
    cnt = np.bincount(blk, weights=onb.astype(np.float64), minlength=nb)
    with np.errstate(divide='ignore', invalid='ignore'):
        bm = np.where(cnt >= max(2.0, 5.0 / cad), np.bincount(blk, weights=d0, minlength=nb) / cnt, np.nan)
        secs = np.bincount(blk, weights=np.where(onb, dt, 0.0), minlength=nb)
        rate = np.where(secs >= 5.0, np.bincount(blk, weights=np.where(onb, db, 0.0), minlength=nb) / secs, np.nan)
    lag, win = 3, 30
    from numpy.lib.stride_tricks import sliding_window_view

    def rolled(a: np.ndarray) -> np.ndarray:
        pad = np.concatenate([np.full(win + lag, np.nan), a])
        w = sliding_window_view(pad, win)[:nb]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            m = np.nanmedian(w, axis=1)
        return np.where((~np.isnan(w)).sum(axis=1) >= 20, m, np.nan)              # on bottom for 200 s or more of the five minutes: drilling was established

    base_b, rate_b = rolled(bm), rolled(rate)
    base = base_b[blk]; rate0 = rate_b[blk]
    thr = np.maximum(P['stall_min_psi'], P['stall_rise'] * np.maximum(np.nan_to_num(base), 0.0))
    with np.errstate(invalid='ignore'):
        over = onb & ~np.isnan(base) & ((diff - base) >= thr)
    events = []; slow = through = 0
    if over.any():
        from . import rig_state as RS
        merged = RS._clean(over, t, 0.0, 20.0)
        for a, b in RS._runs(merged):
            dur = float(t[b - 1] - t[a]) + cad
            if dur > STALL_MAX_S:
                slow += 1; continue                                    # a new level of differential pressure, not a spike
            i30 = _i(t, t[a] - 30.0)
            if onb[i30] and (diff[i30] - base[a]) >= 0.5 * thr[a]:
                slow += 1; continue                                    # it was already most of the way there half a minute before: a ramp
            j = _i(t, t[b - 1] + 60.0)
            picked = bool(np.any(gap[b:j + 1] >= 1.0)) if j >= b else False
            k = _i(t, t[a] + max(dur, 20.0))
            made = float(hole0[k] - hole0[a]); expected = float(np.nan_to_num(rate0[a]) * (t[k] - t[a]))
            stopped = expected > 0.05 and made <= 0.25 * expected
            if not stopped:
                through += 1; continue                                 # the bit drilled on under it: weight, or harder rock
            pk = int(a + np.nanargmax(diff[a:b]))
            events.append({'kind': 'stall', 't': float(t[a]), 'depth_ft': round(float(hole0[a]), 1), 'baseline_psi': round(float(base[a]), 0), 'peak_psi': round(float(diff[pk]), 0),
                           'rise_psi': round(float(diff[pk] - base[a]), 0), 'seconds': round(dur, 0), 'mode': 'slide' if sliding[max(a - 1, 0)] else 'rotary',
                           'bit_stopped': bool(stopped), 'picked_up': picked})
    return {'status': 'OK', 'events': events, 'ramps': slow, 'drilled_through': through, 'cadence_s': cad,
            'on_bottom_hours': round(float(np.where(onb, dt, 0.0).sum() / 3600.0), 1)}


# --------------------------------------------------------------------------------------------------------------
# tight hole: the pick-up and slack-off weights against their own trend
# --------------------------------------------------------------------------------------------------------------
WEIGHT_KEYS = (('pickup_still', 1.0, 'pickup', 'not rotating'), ('pickup_turning', 1.0, 'pickup', 'rotating'), ('slackoff_still', -1.0, 'slackoff', 'not rotating'), ('slackoff_turning', -1.0, 'slackoff', 'rotating'))


def weights(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], conns: List[dict], slips: np.ndarray) -> dict:
    """The pick-up and slack-off weight at each connection: the median hook load while the string moves up before the
    slips are set, and down after they are pulled - the string rotating (30 rpm or more) and not rotating (under 5)
    apart, because rotation takes the drag out of the hook load and the two are different quantities."""
    hl, bit, rpm = ch.get('hookload'), ch.get('bit'), ch.get('rpm')
    if hl is None or bit is None:
        return {'status': 'NO CHANNELS', 'readings': [], 'why': 'needs the hook load and the bit depth'}
    from . import rig_state as RS
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    vbit = RS._rate(np.nan_to_num(bit), t, max(5.0, 2.0 * cad + 1e-6))
    r0 = np.nan_to_num(rpm) if rpm is not None else np.zeros(len(t))
    kinds = (('still', r0 < 5.0), ('turning', r0 >= 30.0))
    need = max(3, int(round(5.0 / cad)))
    out = []
    for c in conns:
        i_off = _i(t, c['t_off']); i_set = _i(t, c['t_off'] + c['pre_min'] * 60.0)
        i_pull = _i(t, c['t_off'] + (c['pre_min'] + c['slips_min']) * 60.0); i_on = _i(t, c['t_off'] + c['w2w_min'] * 60.0)
        r = {'t': float(c['t_off']), 'time': c.get('time'), 'depth_ft': c.get('depth_ft')}
        for key, lo, hi, sign in (('pickup', i_off, i_set, -1.0), ('slackoff', i_pull, i_on, 1.0)):
            for kind, rsel in kinds:
                r[f'{key}_{kind}'] = None
                if hi <= lo:
                    continue
                sel = (sign * vbit[lo:hi] > RS.P['move_ft_s']) & (np.abs(vbit[lo:hi]) < RS.P['jump_ft_s']) & ~slips[lo:hi] & (hl[lo:hi] > 0) & rsel[lo:hi]
                if sel.sum() >= need:
                    r[f'{key}_{kind}'] = round(float(np.median(hl[lo:hi][sel])), 1)
        out.append(r)
    n = {k: sum(1 for r in out if r[k] is not None) for k, _, _, _ in WEIGHT_KEYS}
    ok = any(v >= DRAG_MIN_BASE + 2 for v in n.values())
    return {'status': 'OK' if ok else 'TOO FEW READINGS', 'readings': out, 'connections': len(conns), 'counts': n,
            'why': None if ok else (f"of {len(conns)} connections too few have the string moving on the hook for five seconds either side of the slips ("
                                    + ", ".join(f"{n[k]} {kind} {how}" for k, _, kind, how in WEIGHT_KEYS) + ")")}


def _line(x: np.ndarray, y: np.ndarray) -> Tuple[float, float, float]:
    """Theil-Sen: the median slope of every pair, the median intercept, and the scatter (1.4826 x the median absolute
    residual)."""
    i, j = np.triu_indices(len(x), 1)
    dx = x[j] - x[i]
    ok = np.abs(dx) > 1.0
    m = float(np.median((y[j] - y[i])[ok] / dx[ok])) if ok.sum() >= 3 else 0.0
    c = float(np.median(y - m * x))
    return m, c, 1.4826 * float(np.median(np.abs(y - (m * x + c))))


def tight(readings: List[dict], trips: List[dict], P: dict) -> List[dict]:
    outs = sorted(x['t_start'] for x in trips if x.get('direction') == 'OUT')
    events: List[dict] = []
    base: List[dict] = []; last3: List[dict] = []; ev: Optional[dict] = None
    run = 0

    def close(why: str, at: Optional[float] = None):
        nonlocal ev
        if ev is not None:
            ev['ended'] = why
            if at is not None:
                ev['trip_after_h'] = round((at - ev['t']) / 3600.0, 2)
            events.append(ev)
        ev = None

    for r in readings:
        if r.get('depth_ft') is None:
            continue
        nrun = int(np.searchsorted(outs, r['t']))
        if nrun != run:
            close('a trip out of the hole followed', outs[run] if run < len(outs) else None)
            base, last3, run = [], [], nrun
        fl = False
        for key, sign, tag, kind in WEIGHT_KEYS:
            b = [x for x in base if x.get(key) is not None][-DRAG_BASE:]
            if r.get(key) is None or len(b) < DRAG_MIN_BASE:
                continue
            m, c, sc = _line(np.asarray([x['depth_ft'] for x in b], dtype=np.float64), np.asarray([x[key] for x in b], dtype=np.float64))
            pred = m * r['depth_ft'] + c
            off = sign * (r[key] - pred)
            lim = max(P['drag_sigma'] * max(sc, 2.0), P['drag_min_klbf'])
            if f'{tag}_off_klbf' not in r or off / lim > r[f'{tag}_off_klbf'] / r[f'{tag}_limit_klbf']:
                r[f'{tag}_klbf'] = r[key]; r[f'{tag}_kind'] = kind
                r[f'{tag}_trend_klbf'] = round(pred, 1); r[f'{tag}_off_klbf'] = round(off, 1); r[f'{tag}_limit_klbf'] = round(lim, 1)
            if off >= lim:
                fl = True
        judged = any(f'{tag}_off_klbf' in r for tag in ('pickup', 'slackoff'))
        if not judged:
            base.append(r); continue
        r['flagged'] = fl
        last3 = (last3 + [r])[-3:]
        n_fl = sum(1 for x in last3 if x['flagged'])
        if ev is None:
            if n_fl >= 2:
                first = next(x for x in last3 if x['flagged'])
                ev = {'kind': 'tight', 't': first['t'], 'time': first.get('time'), 'depth_ft': first.get('depth_ft'), 'connections': 0, 'pickup_over_klbf': 0.0, 'slackoff_under_klbf': 0.0, 't_last': r['t'], 'ended': None}
                for x in last3:
                    if x['t'] >= first['t']:
                        ev['connections'] += 1
                        ev['pickup_over_klbf'] = max(ev['pickup_over_klbf'], x.get('pickup_off_klbf') or 0.0); ev['slackoff_under_klbf'] = max(ev['slackoff_under_klbf'], x.get('slackoff_off_klbf') or 0.0)
            elif not fl:
                base.append(r)
            continue
        ev['connections'] += 1; ev['t_last'] = r['t']; ev['depth_to_ft'] = r.get('depth_ft')
        ev['pickup_over_klbf'] = max(ev['pickup_over_klbf'], r.get('pickup_off_klbf') or 0.0); ev['slackoff_under_klbf'] = max(ev['slackoff_under_klbf'], r.get('slackoff_off_klbf') or 0.0)
        if n_fl == 0 and len(last3) == 3:
            ev['connections'] -= 3
            close('back on the trend'); base.extend(last3); last3 = []
        elif ev['connections'] >= DRAG_MAX_EVENT:
            close('a new level: twelve connections off the old trend'); base = []; last3 = []
    if ev is not None:
        nxt = [o for o in outs if o > ev['t_last']]
        if nxt:
            close('a trip out of the hole followed', nxt[0])
        else:
            ev['open'] = True
            close('open at the end of the record')
    return events


# --------------------------------------------------------------------------------------------------------------
# one record, and the workspace
# --------------------------------------------------------------------------------------------------------------
def analyse_arrays(t: np.ndarray, t0: datetime, ch: Dict[str, Optional[np.ndarray]], drilling: np.ndarray, rs: dict, P: dict) -> dict:
    """Everything for one record from its arrays and its rig states (`rs`: rig_state.analyse_arrays' result)."""
    from . import rig_state as RS
    t = np.asarray(t, dtype=np.float64)
    st = rs['_state']
    flow, spp = ch.get('flow'), ch.get('spp')
    pumps = (np.nan_to_num(flow) > RS.P['flow_gpm']) if flow is not None else ((np.nan_to_num(spp) > RS.P['spp_psi']) if spp is not None else np.zeros(len(t), bool))
    stamp = lambda s: (t0 + timedelta(seconds=float(s))).isoformat(timespec='seconds')
    wr = washout_readings(t, ch, rs['connections'], np.asarray(drilling, dtype=bool))
    w_ev = washout(wr['readings'], rs['trips'], wr['use'], P, float(t[-1])) if wr['status'] == 'OK' else []
    sl = stalls(t, ch, pumps, st == RS.SLIDE, P)
    wt = weights(t, ch, rs['connections'], np.isin(st, (RS.SLIPS, RS.OFFHOOK)))
    t_ev = tight(wt['readings'], rs['trips'], P) if wt['status'] == 'OK' else []
    outs = sorted(x['t_start'] for x in rs['trips'] if x.get('direction') == 'OUT')
    for e in sl['events']:
        k = int(np.searchsorted(outs, e['t']))
        if k < len(outs) and outs[k] - e['t'] <= 86400.0:
            e['trip_after_h'] = round((outs[k] - e['t']) / 3600.0, 2)
    for e in w_ev + sl['events'] + t_ev:
        e['time'] = stamp(e['t'])
        if e.get('t_last') is not None:
            e['last_time'] = stamp(e['t_last'])
    for r in wr['readings'] + wt['readings']:
        r['time'] = r.get('time') or stamp(r['t'])
    foot = rs.get('footage') or {}
    drilled = (foot.get('rotary_ft') or 0.0) + (foot.get('slide_ft') or 0.0)
    return {'t_end': float(t[-1]), 'record_end': stamp(t[-1]), 'record_start': stamp(t[0]), 'connections': len(rs['connections']), 'drilled_ft': round(drilled, 0),
            'washout': {**{k: v for k, v in wr.items() if k != 'readings'}, 'readings': wr['readings'], 'events': w_ev},
            'stall': {**sl, 'per_1000_ft': (round(len(sl['events']) / drilled * 1000.0, 2) if drilled > 0 and sl['status'] == 'OK' else None)},
            'tight': {**{k: v for k, v in wt.items() if k != 'readings'}, 'readings': wt['readings'], 'events': t_ev}}


def _find(c, names: Tuple[str, ...]) -> Optional[str]:
    low = {n.lower(): n for n in c._ch}
    for want in names:
        if want in low:
            return low[want]
    for want in names:
        for l, n in low.items():
            if l.startswith(want) or (len(want) > 3 and want in l):
                return n
    return None


def well(ws, well_id: str) -> dict:
    from . import drill_string as DS
    from . import rig_state as RS
    c = DS._cache(ws, well_id, build=True)
    P = settings(ws)
    key = (ws.path, well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'), tuple(P[k] for k in sorted(DEFAULTS)),
           RS.settings(ws).get('block_weight_klbf', {}).get(well_id))
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    rs = RS.well(ws, well_id)
    ch = {k: (np.asarray(c.channel(v), dtype=np.float64) if v in c._ch else None) for k, v in RS._CH.items()}
    dn = _find(c, _NAMES['diff'])
    ch['diff'] = np.asarray(c.channel(dn), dtype=np.float64) if dn else None
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    r = analyse_arrays(np.asarray(c.index, dtype=np.float64), t0, ch, np.asarray(c.extra('drilling'), dtype=bool), rs, P)
    w = ws.well(well_id)
    r.update(well=well_id, display=w.get('display') or well_id, source_mtime=c.meta.get('source_mtime'), channels={**{k: v for k, v in RS._CH.items() if ch.get(k) is not None}, **({'diff': dn} if dn else {})})
    with _LOCK:
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = r
    return r


def _mtime_s(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def is_live(r: dict, now: Optional[datetime] = None) -> bool:
    """A record still being written: its file changed within the last two hours, and its own last sample is within
    two days of now on the recorder's clock (a clock of unknown zone, hence the two days) - a file from years ago
    copied in today is not a rig drilling today."""
    m = _mtime_s(r.get('source_mtime'))
    n_ = now or datetime.now(timezone.utc)
    if m is None or (n_.timestamp() - m) > LIVE_SOURCE_S:
        return False
    try:
        end = datetime.fromisoformat(str(r.get('record_end')))
    except ValueError:
        return True
    return abs((n_.replace(tzinfo=None) - end.replace(tzinfo=None)).total_seconds()) <= LIVE_RECORD_S


def live_events(ws, now: Optional[datetime] = None) -> List[dict]:
    """The events an alert is raised for: in a record still being written, an event still open at its end or one that
    began in its last half hour."""
    from . import series_cache as SCH
    out = []
    ts = (now or datetime.now(timezone.utc)).timestamp()
    for w in ws.wells():
        wid = w['id']
        try:                                              # the file's own date first: a finished record costs nothing here
            if ts - os.path.getmtime(SCH.source_path(ws, wid)) > LIVE_SOURCE_S:
                continue
            r = well(ws, wid)
        except Exception:
            continue
        if not is_live(r, now):
            continue
        for kind in ('washout', 'stall', 'tight'):
            for e in r[kind]['events']:
                if e.get('open') or (r['t_end'] - e['t']) <= LIVE_EVENT_S:
                    out.append({**e, 'well_id': wid, 'well': r['display'], 'event': EVENTS[kind], 'key': f"{EVENTS[kind]}:{wid}:{e['time']}", 'detail': describe(e)})
    return out


def describe(e: dict) -> str:
    if e['kind'] == 'washout':
        return (f"off-bottom pressure {e['fall'] * 100:.0f}% ({e['fall_psi']:,.0f} psi) below the {e['baseline_psi']:,.0f} psi of the stands before, at {e['flow']:g} gal/min, "
                f"from {e['depth_ft']:,.0f} ft over {e['readings']} connection(s)")
    if e['kind'] == 'stall':
        return (f"differential pressure up {e['rise_psi']:,.0f} psi on {e['baseline_psi']:,.0f} for {e['seconds']:g} s at {e['depth_ft']:,.0f} ft ({e['mode']}), "
                + ('the bit stopped' if e['bit_stopped'] else 'picked up off bottom') + (' and was picked up' if e['bit_stopped'] and e['picked_up'] else ''))
    return (f"from {e['depth_ft']:,.0f} ft over {e['connections']} connection(s): pick-up up to {e['pickup_over_klbf']:g} klbf above its trend"
            + (f", slack-off up to {e['slackoff_under_klbf']:g} klbf below" if e['slackoff_under_klbf'] > 0 else ''))


def page(ws, now: Optional[datetime] = None) -> dict:
    from . import rig_state as RS
    P = settings(ws)
    out: Dict[str, Any] = {'status': 'OK', 'wells': [], 'lines': [], 'notes': [], 'settings': {k: P[k] for k in DEFAULTS}, 'sources': P['sources'], 'defaults': dict(DEFAULTS),
                           'bounds': {k: list(v) for k, v in BOUNDS.items()}, 'words': dict(WORDS), 'generated_utc': _utc(now)}
    for wid in RS._wells_with_record(ws):
        try:
            r = well(ws, wid)
        except Exception as e:
            out['notes'].append(f'{wid}: {type(e).__name__}: {e}'); continue
        row = {k: v for k, v in r.items() if k not in ('t_end',)}
        row['live'] = is_live(r, now)
        st = dict(row['stall']); ev = st['events']
        st['n'] = len(ev)
        st['by_mode'] = {m: sum(1 for e in ev if e['mode'] == m) for m in ('rotary', 'slide')}
        st['events'] = sorted(ev, key=lambda e: -e['rise_psi'])[:200] if len(ev) > 200 else ev
        st['listed'] = len(st['events'])
        row['stall'] = st
        out['wells'].append(row)
    if not out['wells']:
        out['status'] = 'EMPTY'
        out['lines'].append('Nothing to replay: no well in the workspace has a drilling record (add the wells\' drilling recorder exports on the Wells tab).')
        return out
    L = out['lines']
    for w in out['wells']:
        wo, sl, tg = w['washout'], w['stall'], w['tight']
        bits = []
        if wo['status'] == 'OK':
            n = len(wo['events']); tr = sum(1 for e in wo['events'] if e.get('trip_after_h') is not None)
            bits.append(f"off-bottom pressure read at {wo['measured'] if wo['use'] == 'measured' else wo['zeroed']} of {wo['connections']} connections"
                        + (' (the recorder\'s zero)' if wo['use'] == 'zero' else '') + f": {n} washout event(s)" + (f", {tr} followed by a trip out" if n else ''))
        else:
            bits.append(f"washout not judged ({wo.get('why')})")
        if sl['status'] == 'OK':
            bits.append(f"{sl['n']} motor stall(s) in {sl['on_bottom_hours']:g} on-bottom hours" + (f" ({sl['per_1000_ft']:g} per 1,000 ft; {sl['by_mode']['slide']} sliding)" if sl['n'] else ''))
        else:
            bits.append(f"stalls not judged ({sl.get('why')})")
        if tg['status'] == 'OK':
            bits.append(f"{len(tg['events'])} tight-hole event(s) in {sum(1 for x in tg['readings'] if 'flagged' in x)} connections weighed")
        else:
            bits.append(f"tight hole not judged ({tg.get('why')})")
        L.append(f"{w['display']} ({w['record_start'][:10]} to {w['record_end'][:10]}, {w['drilled_ft']:,.0f} ft drilled" + (', still being written' if w['live'] else '') + '): ' + '; '.join(bits) + '.')
    L.append('Each rule is replayed over the whole record: what it would have said, and when. A washout event is a fall in pressure at a steady flow - the trip finds the cause; '
             'a stall is a spike the bit stopped under; a tight-hole event is a pick-up weight off its own trend. No event means the rule did not fire, not that the string was sound.')
    from . import public_pad as _PP
    cr = _PP.credit(ws)
    if cr:
        out['public_source'] = cr
        out['lines'].append(cr['line'])
        if cr['reconstructed']:
            out['notes'].append(', '.join(cr['reconstructed']) + ': the bit depth of this record is reconstructed from the block and the slips, not recorded - exact on bottom, approximate over a trip')
    return out


def all_events(p: dict) -> List[dict]:
    rows = []
    for w in p['wells']:
        for kind in ('washout', 'stall', 'tight'):
            for e in w[kind]['events']:
                rows.append({'well': w['display'], **e, 'what': describe(e)})
    return sorted(rows, key=lambda e: (e['well'], e['time']))


def text(p: dict) -> str:
    out = ['Drilling alerts, replayed over the record', '=' * 41]
    out.extend(p['lines'])
    for e in all_events(p)[:400]:
        tail = f" - {e['ended']}" + (f" {e['trip_after_h']:g} h later" if e.get('trip_after_h') is not None else '') if e.get('ended') else ''
        out.append(f"  {e['well'][:18]:18s}  {e['time'][:16].replace('T', ' ')}  {e['kind']:8s}  {e['what']}{tail}")
    for n in p.get('notes', []):
        out.append('  note: ' + n)
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'dysfunction')
    p['program_version'] = program_version
    with open(os.path.join(d, 'drilling_alerts.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'drilling_alerts.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Drilling alerts, replayed over the record\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p) + '\n```\n')
    with open(os.path.join(d, 'events.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'time', 'kind', 'depth_ft', 'what', 'ended', 'trip_after_h'])
        for e in all_events(p):
            wr.writerow([e['well'], e['time'], e['kind'], e.get('depth_ft'), e['what'], e.get('ended') or '', e.get('trip_after_h') if e.get('trip_after_h') is not None else ''])
    files = ['drilling_alerts.json', 'drilling_alerts.md', 'events.csv']
    ws.audit(actor, 'dysfunction.report', {'files': files, 'wells': [w['well'] for w in p['wells']], 'events': len(all_events(p))})
    return {'folder': d, 'files': files, 'page': p}


# --------------------------------------------------------------------------------------------------------------
# the labelled record and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_record(faults: bool = True, seed: int = 3, stands: int = 40, ends_now: bool = False) -> dict:
    """A one-second record of `stands` stands of 95 ft at 150 ft/h with a connection after each (30 s picked up off
    bottom with the pumps on, four minutes in the slips, a minute back to bottom). With `faults`: stands 10-14 at
    500 gal/min instead of 550 (NOT a washout); three motor stalls (stands 5, 16, 17: +700 psi for 15 s, the bit
    stopped, then picked up); the off-bottom pressure falling 3.5 % a stand from stand 18 until a trip out of 2,000 ft
    after stand 23, sound again after it; the pick-up weight climbing 6 klbf a connection from stand 34 to the end of
    the record. `ends_now` dates it so that its last sample is now. SIMULATION."""
    rng = np.random.default_rng(seed)
    cols: Dict[str, list] = {k: [] for k in ('hole', 'bit', 'hookload', 'block', 'flow', 'spp', 'rpm', 'diff', 'drill')}
    S = {'hole': 10000.0, 'bit': 10000.0, 'block': 105.0, 'ref': 0.0}
    BW = 40.0
    truth = {'stalls': [], 'washout_from_stand': 18 if faults else None, 'tight_from_stand': 34 if faults else None, 'trip_after_stand': 23 if faults else None}

    def add(n, hole, bit, hl, blk, flow, spp, rpm, drill):
        n = int(n)
        f = lambda v: (np.full(n, float(v)) if np.isscalar(v) else np.asarray(v, dtype=np.float64))
        hole, bit, hl, blk, flow, spp, rpm = (f(v) for v in (hole, bit, hl, blk, flow, spp, rpm))
        flow = flow + np.where(flow > 0, rng.normal(0, 2.0, n), 0.0); spp = spp + np.where(spp > 0, rng.normal(0, 8.0, n), 0.0); hl = hl + rng.normal(0, 0.6, n)
        for k, v in (('hole', hole), ('bit', bit), ('hookload', hl), ('block', blk), ('flow', flow), ('spp', spp), ('rpm', rpm)):
            cols[k].append(v)
        cols['diff'].append(np.where(spp > 0, spp - S['ref'], 0.0)); cols['drill'].append(np.full(n, bool(drill)))
        S['hole'], S['bit'], S['block'] = float(hole[-1]), float(bit[-1]), float(blk[-1])

    def n_samples() -> int:
        return int(sum(len(a) for a in cols['hole']))

    rate = 150.0 / 3600.0
    for k in range(stands):
        q = 500.0 if (faults and 10 <= k <= 14) else 550.0
        wf = 1.0 - 0.035 * (k - 17) if (faults and 18 <= k <= 23) else 1.0
        dadd = min(30.0, 6.0 * (k - 33)) if (faults and k >= 34) else 0.0
        p_off = lambda d: 2600.0 * (q / 550.0) ** FLOW_EXP * (1.0 + (d - 10000.0) / 60000.0) * wf
        pu = 200.0 + 0.002 * (S['hole'] - 10000.0) + dadd + rng.normal(0, 1.0)
        so = 185.0 - 0.001 * (S['hole'] - 10000.0) - 0.7 * dadd + rng.normal(0, 1.0)
        if S['ref'] == 0.0:
            S['ref'] = p_off(S['hole'])
        parts = [(0.4, True), (0.6, False)] if (faults and k in (5, 16, 17)) else [(1.0, False)]
        for share, stall_after in parts:
            n = int(95.0 * share / rate)
            h = S['hole'] + rate * (np.arange(n) + 1)
            add(n, h, h, 180.0, S['block'] - rate * (np.arange(n) + 1), q, p_off(h) + 500.0, 120.0, True)
            if stall_after:
                truth['stalls'].append(n_samples())
                add(3, S['hole'], S['hole'], 176.0, S['block'], q, p_off(S['hole']) + 500.0 + 700.0 * (np.arange(3) + 1) / 3.0, 120.0, False)
                add(15, S['hole'], S['hole'], 172.0, S['block'], q, p_off(S['hole']) + 1200.0, 60.0, False)
                add(5, S['hole'], S['hole'] - (np.arange(5) + 1), pu, S['block'] + (np.arange(5) + 1), q, p_off(S['hole']), 0.0, False)
                add(15, S['hole'], S['bit'], 190.0, S['block'], q, p_off(S['hole']), 60.0, False)
                add(5, S['hole'], S['bit'] + (np.arange(5) + 1), so, S['block'] - (np.arange(5) + 1), q, p_off(S['hole']), 60.0, False)
        # the connection
        add(30, S['hole'], S['hole'] - (np.arange(30) + 1), pu, S['block'] + (np.arange(30) + 1), q, p_off(S['hole']), 0.0, False)
        S['ref'] = p_off(S['hole'])                                                      # the recorder zeroes its differential pressure off bottom
        b0 = S['block']
        add(240, S['hole'], S['bit'], BW, b0 + (105.0 + 30.0 - b0) * np.minimum(1.0, (np.arange(240) + 1) / 144.0), 0.0, 0.0, 0.0, False)
        add(10, S['hole'], S['bit'], pu, S['block'], q * (np.arange(10) + 1) / 10.0, p_off(S['hole']) * ((np.arange(10) + 1) / 10.0) ** 2, 0.0, False)
        add(50, S['hole'], S['bit'] + 30.0 * (np.arange(50) + 1) / 50.0, so, S['block'] - 30.0 * (np.arange(50) + 1) / 50.0, q, p_off(S['hole']), 0.0, False)
        if faults and k == 23:                                                           # the trip that finds it: 2,000 ft out and back in
            add(600, S['hole'], S['bit'], 190.0, S['block'], q, p_off(S['hole']), 30.0, False)
            add(300, S['hole'], S['hole'] - 300.0 * (np.arange(300) + 1) / 300.0, pu, 105.0, 0.0, 0.0, 0.0, False)
            for _ in range(20):
                add(60, S['hole'], S['bit'] - 100.0 * (np.arange(60) + 1) / 60.0, 198.0, 5.0 + 100.0 * (np.arange(60) + 1) / 60.0, 0.0, 0.0, 0.0, False)
                add(90, S['hole'], S['bit'], BW, 105.0 - 100.0 * np.minimum(1.0, (np.arange(90) + 1) / 54.0), 0.0, 0.0, 0.0, False)
            add(1200, S['hole'], S['bit'], 196.0, 5.0, 0.0, 0.0, 0.0, False)
            for _ in range(20):
                add(60, S['hole'], S['bit'], BW, 5.0 + 100.0 * np.minimum(1.0, (np.arange(60) + 1) / 36.0), 0.0, 0.0, 0.0, False)
                add(50, S['hole'], S['bit'] + 100.0 * (np.arange(50) + 1) / 50.0, 190.0, 105.0 - 100.0 * (np.arange(50) + 1) / 50.0, 0.0, 0.0, 0.0, False)
            add(300, S['hole'], S['bit'] + 300.0 * (np.arange(300) + 1) / 300.0, 188.0, 105.0, 550.0, 2600.0, 60.0, False)
            S['ref'] = 2600.0 * (1.0 + (S['hole'] - 10000.0) / 60000.0)
    # the last stand: drilling to the end of the record
    n = int(40.0 / rate); h = S['hole'] + rate * (np.arange(n) + 1)
    add(n, h, h, 180.0, S['block'] - rate * (np.arange(n) + 1), 550.0, 2600.0 * (1.0 + (h - 10000.0) / 60000.0) + 500.0, 120.0, True)
    arr = {k: np.concatenate(v) for k, v in cols.items()}
    drilling = arr.pop('drill').astype(bool)
    t0 = (datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(seconds=len(drilling))) if ends_now else datetime(2026, 9, 1, 6, 0, 0)
    return {'t': np.arange(len(drilling), dtype=np.float64), 't0': t0, 'ch': arr, 'drilling': drilling, 'truth': truth, 'block_weight': BW}


def write_edr(rec: dict, path: str) -> str:
    """The labelled record as a drilling recorder's export (the columns a rig's recorder gives)."""
    ch = rec['ch']; t0 = rec['t0']
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Hook Load (klbs)', 'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'On Bottom (unitless)']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(hdr)
        d = rec['drilling']
        for i in range(len(d)):
            ts = t0 + timedelta(seconds=float(rec['t'][i]))
            on = int(ch['hole'][i] - ch['bit'][i] < 0.5)
            w.writerow([ts.strftime('%Y/%m/%d'), ts.strftime('%H:%M:%S'), f"{ch['hole'][i]:.2f}", f"{ch['bit'][i]:.2f}", f"{150.0 if d[i] else 0.0:.1f}", f"{20.0 if d[i] else 0.0:.1f}", f"{ch['rpm'][i]:.1f}",
                        f"{ch['spp'][i]:.1f}", f"{ch['hookload'][i]:.1f}", f"{ch['diff'][i]:.1f}", f"{ch['flow'][i]:.1f}", f"{ch['block'][i]:.2f}", str(on)])
    return path


def analyse_record(rec: dict, P: Optional[dict] = None) -> dict:
    from . import rig_state as RS
    rs = RS.analyse_arrays(rec['t'], rec['t0'], rec['ch'], rec['drilling'], rec['ch']['hole'], rec.get('block_weight'))
    return analyse_arrays(rec['t'], rec['t0'], rec['ch'], rec['drilling'], rs, {**DEFAULTS, **(P or {})})


def selftest() -> dict:
    checks = []
    rec = synthetic_record(True)
    r = analyse_record(rec)
    wo, sl, tg = r['washout'], r['stall'], r['tight']
    checks.append({'check': 'an off-bottom reading at every connection, measured with the bit off bottom', 'value': {'connections': r['connections'], 'measured': wo['measured'], 'use': wo['use']},
                   'pass': r['connections'] >= 38 and wo['measured'] >= r['connections'] - 1 and wo['use'] == 'measured'})
    ev = wo['events']
    checks.append({'check': 'the washout: one event, from the second stand of the fall, a sixth of the pressure gone by the last connection, ended by the trip out', 'value': [(e['time'], e['depth_ft'], e['fall'], e['ended'], e.get('trip_after_h')) for e in ev],
                   'pass': len(ev) == 1 and 10000 + 95 * 19 <= ev[0]['depth_ft'] <= 10000 + 95 * 21 and 0.14 <= ev[0]['fall'] <= 0.20 and ev[0]['ended'].startswith('a trip out') and 0 < ev[0]['trip_after_h'] < 5})
    q_ok = not any(x.get('flagged') for x in wo['readings'] if 10000 + 95 * 10 < (x['depth_ft'] or 0) < 10000 + 95 * 17)
    checks.append({'check': 'the pumps slowed by a tenth for five stands: the pressure fell by a sixth and no reading is flagged', 'value': q_ok, 'pass': q_ok})
    st_t = [e['t'] for e in sl['events']]
    checks.append({'check': 'the three stalls, each within five seconds of where it was put, the rise 700 psi, the bit stopped', 'value': [(e['t'], e['rise_psi'], e['seconds'], e['bit_stopped'], e['picked_up']) for e in sl['events']],
                   'pass': len(st_t) == 3 and all(abs(a - b) <= 5 for a, b in zip(st_t, rec['truth']['stalls'])) and all(600 <= e['rise_psi'] <= 800 and e['bit_stopped'] and e['picked_up'] for e in sl['events'])})
    te = tg['events']
    checks.append({'check': 'the tightening hole: one event from the second or third connection of the climb, open at the end of the record', 'value': [(e['time'], e['depth_ft'], e['connections'], e['pickup_over_klbf'], e['ended']) for e in te],
                   'pass': len(te) == 1 and 10000 + 95 * 34 <= te[0]['depth_ft'] <= 10000 + 95 * 37 and te[0].get('open') and 20 <= te[0]['pickup_over_klbf'] <= 36 and te[0]['slackoff_under_klbf'] > 8})
    clean = analyse_record(synthetic_record(False))
    checks.append({'check': 'a sound record: no event of any kind', 'value': {k: len(clean[k]['events']) for k in ('washout', 'stall', 'tight')}, 'pass': all(len(clean[k]['events']) == 0 for k in ('washout', 'stall', 'tight'))})
    strict = analyse_record(rec, {'washout_fall': 0.3, 'stall_min_psi': 900.0, 'drag_min_klbf': 60.0})
    checks.append({'check': 'the settings are the rule: at 30 %, 900 psi and 60 klbf none of it is an event', 'value': {k: len(strict[k]['events']) for k in ('washout', 'stall', 'tight')},
                   'pass': all(len(strict[k]['events']) == 0 for k in ('washout', 'stall', 'tight'))})
    coarse = stalls(rec['t'][::10] , {k: v[::10] for k, v in rec['ch'].items()}, np.ones(len(rec['t'][::10]), bool), np.zeros(len(rec['t'][::10]), bool), DEFAULTS)
    checks.append({'check': 'a record every ten seconds is not judged for stalls, and says so', 'value': coarse['status'], 'pass': coarse['status'] == 'TOO COARSE' and not coarse['events']})
    m, c, sc = _line(np.arange(15.0) * 100.0, 200.0 + 0.02 * np.arange(15.0) * 100.0 + np.r_[np.zeros(14), 50.0])
    checks.append({'check': 'the trend line shrugs off one wild point (Theil-Sen)', 'value': [round(m, 4), round(c, 2)], 'pass': abs(m - 0.02) < 1e-6 and abs(c - 200.0) < 1e-6})
    return {'ok': all(x['pass'] for x in checks), 'checks': checks, 'label': 'SIMULATION'}
