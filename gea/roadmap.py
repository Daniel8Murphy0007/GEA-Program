# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""roadmap - the drilling roadmap: for every stretch of hole, the weight, the rotary speed, the flow and the
differential pressure the fastest stands on the offset wells were drilled with, and how a well ran against it.

A pad's wells go through the same rock. Each one's drilling record says, stand by stand, what the driller ran and
what the bit made of it; the next well is usually drilled from memory of the last. This module reads the records
the other way round: it puts every stand of every well into the stretch of hole it was drilled in, finds the
fastest quarter of the stands in each stretch, and writes down what those stands had in common - the window of
parameters they were drilled in. That table is the roadmap. Then it checks itself: each well is set against the
roadmap made from the OTHER wells, stand by stand, so the page can say whether the stands that were inside the
window were in fact the faster ones, and what the well would have saved at the roadmap's pace.

- THE STANDS. The hole is cut into stands of 100 ft of hole depth. A stand's on-bottom hours are the seconds the
  record was drilling in it; its rate is its footage over those hours; its weight on bit, rotary speed, flow,
  differential pressure, standpipe pressure and torque are the time-weighted means over the same seconds. A stand
  is ROTARY when the rotary turned for 70 % or more of its drilling time, SLIDE at 30 % or less, MIXED between;
  the roadmap is made for rotary and for slide stands apart, and mixed stands are counted and left out.
- THE STRETCHES. Down to the lateral a stretch is 1,000 ft of true vertical depth (the recorder's own TVD): wells
  on a pad meet the same formation at the same TVD, not the same measured depth. A record with no TVD channel
  has its measured depth stand in - right for a vertical hole, wrong for a deviated one - and the page names it. In the lateral - from the first
  stand where the hole gains or loses less than 17 ft of TVD in 100 ft of hole, and goes on doing so - a stretch
  is 1,000 ft along the hole from the heel. Where the workspace has formation-top layers and the well a surface
  position and a reference elevation, each stretch is named for the formation whose mapped top lies above it,
  and the map's bracket is printed.
- THE WINDOW. In each stretch, for rotary and for slide stands apart, with twelve stands or more: the BEST stands
  are the fastest quarter; the window of each parameter is the middle half (25th to 75th percentile) of what the
  best stands ran; the pace of the roadmap is the best stands' median rate, beside the median of all.
- THE CHECK. For each well, the roadmap is made again without it. Each of its stands gets a DISTANCE from the
  window the other wells give for its stretch: the largest shortfall or excess of its weight on bit, rotary speed
  (rotary stands) or flow beyond the window, as a fraction of the window's middle - nothing for a stand run inside
  it. A stand within a tenth is INSIDE. Then, stretch by stretch, the half of the well's stands that were nearer
  the window is set against the half that were farther, and the two rates are averaged over the stretches by
  footage: NEARER against FARTHER is the check, with the rank correlation between distance and rate beside it. A
  roadmap that means something has the nearer stands the faster ones, well after well. The ROOM is the on-bottom
  hours the well spent beyond what its footage would have taken at the roadmap's pace, counting only the stands
  slower than it - the most there was to gain, not a forecast.
- THE NEXT STAND. For the well whose record ends last (or the one named): the stretch its bit is in, the window
  there and in the next stretch, and its last ten minutes of drilling against them.

What it will not call a measurement: a cause - the fastest stands were drilled in this window; that the window
made them fast is the reading the check supports or does not; the bit, the motor and the mud, which the record
does not carry and which differ between wells (the Drill string tab holds the runs); a formation boundary (a
stretch is a depth, named for the mapped formation only where the map and the elevation are declared); a limit -
the window is what was run, not what the string, the motor or the hole can take (torque and drag, hydraulics and
the motor's own ratings are other tabs).
"""
from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

STAND_FT = 100.0
TVD_STRETCH_FT = 1000.0
LATERAL_STRETCH_FT = 1000.0
MIN_STANDS = 12
MAX_ROP_FT_H = 2000.0         # a stand 'drilled' faster than this is a depth the recorder was given, not hole a bit made
ROTARY_SHARE, SLIDE_SHARE = 0.7, 0.3
TOLERANCE = 0.10
PARAMS = (('wob', 'weight on bit', 'klbf'), ('rpm', 'rotary speed', 'rpm'), ('flow', 'flow', 'gal/min'), ('diff', 'differential pressure', 'psi'),
          ('spp', 'standpipe pressure', 'psi'), ('torque', 'torque', 'kft-lb'))
SETPOINTS = {'rotary': ('wob', 'rpm', 'flow'), 'slide': ('wob', 'flow')}
_NAMES = {'wob': ('weight on bit', 'wob'), 'rpm': ('rotary rpm', 'rpm'), 'flow': ('total pump output', 'flow in', 'pump output', 'flow'), 'diff': ('differential pressure', 'diff press', 'diff'),
          'spp': ('standpipe pressure', 'stand pipe pressure', 'spp'), 'torque': ('convertible torque', 'rotary torque', 'torque'), 'tvd': ('interpolated tvd', 'tvd')}
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


class RoadmapError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------------------------------------------
# the stands of one record
# --------------------------------------------------------------------------------------------------------------
def stands_of(t: np.ndarray, bottom: np.ndarray, drilling: np.ndarray, rotating: np.ndarray, ch: Dict[str, Optional[np.ndarray]], tvd: Optional[np.ndarray] = None,
              stand_ft: float = STAND_FT) -> dict:
    """The record cut into stands of hole depth. `t` seconds; `bottom` the hole depth, ft; `drilling` and `rotating`
    one flag a sample; `ch` the parameter arrays by key (any may be missing). Returns arrays, one entry a stand."""
    t = np.asarray(t, dtype=np.float64)
    n = len(t)
    bot = np.nan_to_num(np.asarray(bottom, dtype=np.float64))
    drl = np.asarray(drilling, dtype=bool)
    dt = np.diff(t, append=t[-1] + (t[-1] - t[-2] if n > 1 else 1.0)) if n else np.zeros(0)
    cad = float(np.median(dt)) if n > 1 else 1.0
    dt = np.where(dt <= 0, 0.0, np.where(dt <= max(60.0, 10 * cad), dt, cad))    # the sample before a gap in the record counts as one sample, not the gap
    db = np.diff(bot, prepend=bot[0] if n else 0.0)
    db = np.where((db > 0) & (db < 50.0), db, 0.0)                              # a re-set of the depth is not footage
    if n > 1:
        db[np.diff(t, prepend=t[0]) > max(60.0, 10 * cad)] = 0.0                # hole made while the recorder was not recording has no time to set it against
    k = np.floor(bot / stand_ft).astype(np.int64)
    k0 = int(k[drl].min()) if drl.any() else 0
    kk = np.clip(k - k0, 0, None)
    m = int(kk[drl].max()) + 1 if drl.any() else 0
    w = np.where(drl, dt, 0.0)
    secs = np.bincount(kk, weights=w, minlength=m)[:m]
    feet = np.bincount(kk, weights=np.where(drl, db, 0.0), minlength=m)[:m]
    rot_s = np.bincount(kk, weights=np.where(np.asarray(rotating, dtype=bool), w, 0.0), minlength=m)[:m]
    t_end = np.zeros(m); np.maximum.at(t_end, kk[drl], t[drl]) if drl.any() else None
    out: Dict[str, Any] = {'stand_ft': stand_ft, 'cadence_s': cad}
    good = (secs > 0) & (feet >= 0.8 * stand_ft)
    hours = secs / 3600.0
    with np.errstate(divide='ignore', invalid='ignore'):
        fast = good & (np.where(hours > 0, feet / hours, 0.0) > MAX_ROP_FT_H)        # the depth being counted, not hole being made
    good &= ~fast
    with np.errstate(divide='ignore', invalid='ignore'):
        rop = np.where(hours > 0, feet / hours, np.nan)
        share = np.where(secs > 0, rot_s / secs, np.nan)
        vals = {}
        for key, _, _ in PARAMS:
            a = ch.get(key)
            if a is None:
                vals[key] = None; continue
            a = np.asarray(a, dtype=np.float64)
            ok = drl & ~np.isnan(a)
            num = np.bincount(kk, weights=np.where(ok, a * dt, 0.0), minlength=m)[:m]
            den = np.bincount(kk, weights=np.where(ok, dt, 0.0), minlength=m)[:m]
            vals[key] = np.where(den > 0, num / den, np.nan)
        tv = None
        if tvd is not None:
            a = np.asarray(tvd, dtype=np.float64)
            ok = drl & ~np.isnan(a) & (a > 0)
            num = np.bincount(kk, weights=np.where(ok, a * dt, 0.0), minlength=m)[:m]
            den = np.bincount(kk, weights=np.where(ok, dt, 0.0), minlength=m)[:m]
            tv = np.where(den > 0, num / den, np.nan)
    md = (np.arange(m) + k0 + 0.5) * stand_ft
    mode = np.where(share >= ROTARY_SHARE, 0, np.where(share <= SLIDE_SHARE, 1, 2))          # 0 rotary, 1 slide, 2 mixed
    sel = np.flatnonzero(good)
    out.update(n=int(len(sel)), md=md[sel], hours=hours[sel], feet=feet[sel], rop=rop[sel], rotary_share=share[sel], mode=mode[sel], t_end=t_end[sel],
               tvd=(tv[sel] if tv is not None else None), **{key: (vals[key][sel] if vals[key] is not None else None) for key, _, _ in PARAMS},
               incomplete=int(((secs > 0) & ~good & ~fast).sum()), implausible=int(fast.sum()))
    return out


def lateral_start(md: np.ndarray, tvd: Optional[np.ndarray]) -> Optional[float]:
    """The measured depth of the heel: the first stand from which the hole gains or loses less than 17 ft of TVD in 100
    ft of hole (within ten degrees of horizontal) and nine in ten of the stands beyond it do the same - when 1,000 ft or
    more of hole lies beyond it. A lateral drilled up or down dip is still a lateral. None for a hole with none."""
    if tvd is None or len(md) < 8:
        return None
    ok = ~np.isnan(tvd)
    if ok.sum() < 8:
        return None
    m, v = md[ok], tvd[ok]
    slope = np.abs(np.gradient(v, m))
    k = np.ones(3) / 3.0
    slope = np.convolve(np.pad(slope, 1, mode='edge'), k, mode='valid')
    flat = slope < 0.17
    tail = np.cumsum(flat[::-1])[::-1] / np.arange(len(flat), 0, -1)
    hit = np.flatnonzero(flat & (tail >= 0.9))
    if not len(hit):
        return None
    i = int(hit[0])
    if m[-1] - m[i] < 1000.0:
        return None
    return float(m[i])


def stretch_keys(st: dict) -> Tuple[List[tuple], Optional[float]]:
    """Each stand's stretch: ('TVD', n) for n x 1,000 ft of true vertical depth, ('LAT', n) for n x 1,000 ft from the heel,
    and for a record with no TVD its measured depth stands in under the same ('TVD', n) - right for a vertical hole,
    wrong for a deviated one, and the page names every well it was done for. A stand with no TVD in a record that has
    one takes its TVD from the stands either side."""
    md, tvd = st['md'], st.get('tvd')
    heel = lateral_start(md, tvd)
    if tvd is not None and len(md):
        have = ~np.isnan(tvd)
        if 2 <= int(have.sum()) < len(md):                                      # a stand the survey did not reach is placed between its neighbours, not at its measured depth
            tvd = np.where(have, tvd, np.interp(md, md[have], tvd[have]))
    keys = []
    for i in range(len(md)):
        if heel is not None and md[i] >= heel:
            keys.append(('LAT', int((md[i] - heel) // LATERAL_STRETCH_FT)))
        elif tvd is not None and not np.isnan(tvd[i]):
            keys.append(('TVD', int(tvd[i] // TVD_STRETCH_FT)))
        else:
            keys.append(('TVD', int(md[i] // TVD_STRETCH_FT)))
    return keys, heel


def stretch_label(key: tuple) -> str:
    kind, n = key
    if kind == 'LAT':
        return f'lateral {n * LATERAL_STRETCH_FT:,.0f} to {(n + 1) * LATERAL_STRETCH_FT:,.0f} ft from the heel'
    return f"{'TVD' if kind == 'TVD' else 'MD'} {n * TVD_STRETCH_FT:,.0f} to {(n + 1) * TVD_STRETCH_FT:,.0f} ft"


def _order(key: tuple) -> tuple:
    return ({'MD': 0, 'TVD': 0, 'LAT': 1}[key[0]], key[1])


# --------------------------------------------------------------------------------------------------------------
# the roadmap of a set of wells
# --------------------------------------------------------------------------------------------------------------
def _q(a: np.ndarray, p: float) -> float:
    return float(np.percentile(a, p))


def build(wells: Dict[str, dict], leave_out: Optional[str] = None) -> Dict[tuple, dict]:
    """The roadmap from these wells' stands ({well: {'stands': ..., 'keys': [...]}}), one entry a (stretch, mode)."""
    pool: Dict[tuple, List[Tuple[str, int]]] = {}
    for wid, w in wells.items():
        if wid == leave_out:
            continue
        st = w['stands']
        for i, key in enumerate(w['keys']):
            mode = int(st['mode'][i])
            if mode == 2:
                continue
            pool.setdefault((key, 'rotary' if mode == 0 else 'slide'), []).append((wid, i))
    out: Dict[tuple, dict] = {}
    for (key, mode), items in pool.items():
        rop = np.asarray([wells[w]['stands']['rop'][i] for w, i in items])
        ent: Dict[str, Any] = {'stretch': key, 'mode': mode, 'label': stretch_label(key), 'n_stands': len(items), 'wells': sorted({w for w, _ in items}),
                               'feet': round(float(sum(wells[w]['stands']['feet'][i] for w, i in items)), 0), 'rop_all_ft_h': round(float(np.median(rop)), 1)}
        bw = {}
        for w in ent['wells']:
            ii = [i for x, i in items if x == w]
            st = wells[w]['stands']
            row = {'stands': len(ii), 'rop_ft_h': round(float(sum(st['feet'][i] for i in ii) / sum(st['hours'][i] for i in ii)), 1)}
            for p, _, _ in PARAMS:
                if st.get(p) is not None:
                    v = np.asarray([st[p][i] for i in ii], dtype=np.float64); v = v[~np.isnan(v)]
                    if len(v):
                        row[p] = round(float(np.median(v)), 2)
            bw[w] = row
        ent['by_well'] = bw
        ent['fastest_well'] = max(bw, key=lambda w: bw[w]['rop_ft_h']) if len(bw) > 1 else None
        if len(items) < MIN_STANDS:
            ent['status'] = 'TOO FEW STANDS'
            out[(key, mode)] = ent; continue
        cut = _q(rop, 75.0)
        best = [it for it, r in zip(items, rop) if r >= cut]
        ent.update(status='OK', n_best=len(best), rop_best_ft_h=round(float(np.median([wells[w]['stands']['rop'][i] for w, i in best])), 1), rop_cut_ft_h=round(cut, 1),
                   best_by_well={w: sum(1 for x, _ in best if x == w) for w in sorted({x for x, _ in best})}, window={})
        for p, _, _ in PARAMS:
            v = np.asarray([wells[w]['stands'][p][i] for w, i in best if wells[w]['stands'].get(p) is not None], dtype=np.float64)
            v = v[~np.isnan(v)]
            if len(v) >= max(3, len(best) // 2):
                allv = np.asarray([wells[w]['stands'][p][i] for w, i in items if wells[w]['stands'].get(p) is not None], dtype=np.float64)
                allv = allv[~np.isnan(allv)]
                ent['window'][p] = {'low': round(_q(v, 25.0), 2), 'mid': round(_q(v, 50.0), 2), 'high': round(_q(v, 75.0), 2), 'all_mid': round(float(np.median(allv)), 2)}
        if not any(p in ent['window'] for p in SETPOINTS[mode]):
            ent['status'] = 'TOO FEW STANDS'                 # the best stands carry none of the setpoints
        out[(key, mode)] = ent
    return out


def distance(ent: dict, mode: str, stand: Dict[str, float]) -> Optional[float]:
    """How far a stand's setpoints were from the window: the largest shortfall or excess of any of them beyond the
    window, as a fraction of the window's middle; 0 with every setpoint in the window; None with no window."""
    if ent.get('status') != 'OK':
        return None
    d = None
    for p in SETPOINTS[mode]:
        w = ent['window'].get(p); v = stand.get(p)
        if w is None or v is None or np.isnan(v) or not w['mid']:
            continue
        d = max(d or 0.0, max(w['low'] - v, v - w['high'], 0.0) / abs(w['mid']))
    return d


def inside(ent: dict, mode: str, stand: Dict[str, float]) -> Optional[bool]:
    """Whether a stand's setpoints were each within the window widened by a tenth of its middle; None with no window."""
    d = distance(ent, mode, stand)
    return None if d is None else bool(d <= TOLERANCE)


def _ranks(a: np.ndarray) -> np.ndarray:
    u, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    first = np.cumsum(cnt) - cnt
    return (first + (cnt - 1) / 2.0)[inv]


def check(wells: Dict[str, dict], wid: str) -> dict:
    """One well against the roadmap made from the others: stretch by stretch, the half of its stands that were nearer
    the window against the half that were farther."""
    rm = build(wells, leave_out=wid)
    st = wells[wid]['stands']; keys = wells[wid]['keys']
    groups: Dict[tuple, List[tuple]] = {}
    for i, key in enumerate(keys):
        mode = int(st['mode'][i])
        if mode == 2:
            continue
        mname = 'rotary' if mode == 0 else 'slide'
        ent = rm.get((key, mname))
        if ent is None:
            continue
        d = distance(ent, mname, {p: (float(st[p][i]) if st.get(p) is not None else None) for p, _, _ in PARAMS})
        if d is None:
            continue
        groups.setdefault((key, mname), []).append((d, float(st['feet'][i]), float(st['hours'][i]), ent['rop_best_ft_h']))
    rows = []
    n_in = n_out = 0; prize = 0.0; hours = 0.0; judged_ft = 0.0
    num_n = num_f = den = 0.0
    dd, rr = [], []
    rate = lambda xs: (round(sum(x[1] for x in xs) / sum(x[2] for x in xs), 1) if xs and sum(x[2] for x in xs) > 0 else None)
    for (key, mname), g in sorted(groups.items(), key=lambda kv: (_order(kv[0][0]), kv[0][1])):
        pace = g[0][3]
        ins = [x for x in g if x[0] <= TOLERANCE]; outs = [x for x in g if x[0] > TOLERANCE]
        over = sum(max(0.0, x[2] - x[1] / pace) for x in g)
        n_in += len(ins); n_out += len(outs); prize += over; hours += sum(x[2] for x in g); judged_ft += sum(x[1] for x in g)
        dd.extend(x[0] for x in g); rr.extend((x[1] / x[2]) / pace for x in g)
        row = {'stretch': list(key), 'label': stretch_label(key), 'mode': mname, 'stands': len(g), 'inside_stands': len(ins), 'outside_stands': len(outs), 'rop_ft_h': rate(g), 'pace_ft_h': pace,
               'median_distance': round(float(np.median([x[0] for x in g])), 3), 'prize_h': round(over, 2), 'nearer_rop_ft_h': None, 'farther_rop_ft_h': None}
        ds = np.asarray([x[0] for x in g])
        if len(g) >= 4 and ds.max() - ds.min() > 0.02:
            med = float(np.median(ds))
            near = [k for k, x in enumerate(g) if x[0] <= med]
            if len(near) == len(g):
                near = [k for k, x in enumerate(g) if x[0] < med]
            far = [k for k in range(len(g)) if k not in near]
            gn, gf = [g[k] for k in near], [g[k] for k in far]
            if gn and gf and rate(gn) and rate(gf):
                row['nearer_rop_ft_h'], row['farther_rop_ft_h'] = rate(gn), rate(gf)
                wgt = sum(x[1] for x in g)
                num_n += wgt * rate(gn); num_f += wgt * rate(gf); den += wgt
        rows.append(row)
    rho = None
    if len(dd) >= 10 and len(set(dd)) > 1:
        a, b = _ranks(np.asarray(dd)), _ranks(np.asarray(rr))
        rho = round(float(np.corrcoef(a, b)[0, 1]), 3)
    n_judged = n_in + n_out
    return {'well': wid, 'judged_stands': n_judged, 'judged_ft': round(judged_ft, 0), 'inside_stands': n_in, 'outside_stands': n_out,
            'inside_share': (round(n_in / n_judged, 3) if n_judged else None), 'rop_ft_h': (round(judged_ft / hours, 1) if hours > 0 else None),
            'nearer_rop_ft_h': (round(num_n / den, 1) if den else None), 'farther_rop_ft_h': (round(num_f / den, 1) if den else None),
            'paired_gain': (round(num_n / num_f - 1.0, 3) if den and num_f > 0 else None), 'paired_ft': round(den, 0), 'rank_correlation': rho,
            'prize_h': round(prize, 1), 'hours_judged': round(hours, 1), 'stretches': rows, 'from_wells': sorted(w for w in wells if w != wid)}


# --------------------------------------------------------------------------------------------------------------
# the workspace
# --------------------------------------------------------------------------------------------------------------
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


def well_stands(ws, well_id: str) -> dict:
    """One well's stands from its chart cache and its rig states (kept until the record changes)."""
    from . import drill_string as DS
    from . import rig_state as RS
    c = DS._cache(ws, well_id, build=True)
    key = (ws.path, well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'))
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    r = RS.well(ws, well_id)
    t = np.asarray(c.index, dtype=np.float64)
    names = {k: _find(c, v) for k, v in _NAMES.items()}
    ch = {k: (np.asarray(c.channel(names[k]), dtype=np.float64) if names.get(k) else None) for k, _, _ in PARAMS}
    tvd = np.asarray(c.channel(names['tvd']), dtype=np.float64) if names.get('tvd') else None
    tvd_how = f"the recorder's own TVD ({names['tvd']})" if names.get('tvd') else None
    bottom = c.extra('bottom')
    if bottom is None:
        raise RoadmapError(f'{well_id}: the record has no hole depth')
    drilling = np.asarray(c.extra('drilling'), dtype=bool)
    keep = c.extra('keep_b')
    if keep is not None:
        drilling = drilling & np.asarray(keep, dtype=bool)          # hole a later sidetrack abandoned is not the well's footage
    st = stands_of(t, np.asarray(bottom, dtype=np.float64), drilling, r['_state'] == RS.ROT, ch, tvd)
    keys, heel = stretch_keys(st)
    w = ws.well(well_id)
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    # the last ten minutes of drilling, for the next stand
    last = None
    di = np.flatnonzero(drilling)
    if len(di):
        sel = di[t[di] >= t[di[-1]] - 600.0]
        last = {'depth_ft': round(float(np.nan_to_num(np.asarray(bottom)[di[-1]])), 1), 'time': (t0 + timedelta(seconds=float(t[di[-1]]))).isoformat(timespec='seconds'),
                'tvd_ft': (round(float(tvd[di][~np.isnan(tvd[di])][-1]), 1) if tvd is not None and (~np.isnan(tvd[di])).any() else None),
                'rotary_share': round(float((r['_state'][sel] == RS.ROT).mean()), 2),
                **{k: (round(float(np.nanmean(ch[k][sel])), 2) if ch.get(k) is not None and not np.all(np.isnan(ch[k][sel])) else None) for k, _, _ in PARAMS}}
    out = {'well': well_id, 'display': w.get('display') or well_id, 'stands': st, 'keys': keys, 'heel_md_ft': heel, 'channels': {k: v for k, v in names.items() if v},
           'tvd_how': tvd_how, 'record_end': (t0 + timedelta(seconds=float(t[-1]))).isoformat(timespec='seconds'), 'last': last, 'surface': w.get('surface')}
    with _LOCK:
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = out
    return out


def formation_tops(ws, well_id: str) -> List[dict]:
    """The mapped formation tops at a well as depths below its reference elevation: [{formation, tvd_low_ft, tvd_high_ft,
    layer}], shallowest first; empty when there is no surface position, no elevation or no top layer."""
    from . import interval as IV
    w = ws.well(well_id)
    elev = ((w.get('surface') or {}).get('elevation') or {}).get('ft')
    if elev is None:
        return []
    try:
        st = ws.well_structure(well_id, 50.0, actor='roadmap', write=False)
    except Exception:
        return []
    if st.get('status') != 'OK':
        return []
    recs = {r['id']: r for r in ws.layers()}
    out = []
    for L in st.get('layers', []):
        rec = recs.get(L.get('layer_id'), {})
        if (rec.get('kind') or L.get('layer_kind')) != 'contours' or (rec.get('role') or 'top') != 'top':
            continue
        va = (rec.get('value_attr') or '').lower()
        if 'elev' not in va:
            continue
        br = IV.bracket_of(L)
        if br is None:
            continue
        f = 0.3048 if va.endswith('m') and 'ft' not in va else 1.0
        lo, hi = br[0] / f if f != 1.0 else br[0], br[1] / f if f != 1.0 else br[1]
        out.append({'formation': rec.get('formation') or rec.get('display') or L.get('layer_id'), 'layer': rec.get('display') or L.get('layer_id'),
                    'tvd_low_ft': round(float(elev) - hi, 0), 'tvd_high_ft': round(float(elev) - lo, 0)})
    out.sort(key=lambda r: (r['tvd_low_ft'] + r['tvd_high_ft']) / 2.0)
    return out


def _formation_at(tops: List[dict], tvd_mid: float) -> Optional[dict]:
    cur = None
    for tp in tops:
        if (tp['tvd_low_ft'] + tp['tvd_high_ft']) / 2.0 <= tvd_mid:
            cur = tp
    return cur


def wells_with_record(ws) -> List[str]:
    from . import rig_state as RS
    return RS._wells_with_record(ws)


def page(ws, subject: Optional[str] = None) -> dict:
    ids = wells_with_record(ws)
    out: Dict[str, Any] = {'status': 'OK', 'wells': [], 'stretches': [], 'checks': [], 'next': None, 'notes': [], 'lines': [], 'generated_utc': _utc(),
                           'constants': {'stand_ft': STAND_FT, 'tvd_stretch_ft': TVD_STRETCH_FT, 'lateral_stretch_ft': LATERAL_STRETCH_FT, 'min_stands': MIN_STANDS, 'tolerance': TOLERANCE}}
    data: Dict[str, dict] = {}
    for wid in ids:
        try:
            ws_ = well_stands(ws, wid)
        except (RoadmapError, KeyError, ValueError, OSError) as e:
            out['notes'].append(f'{wid}: {e}'); continue
        if ws_['stands']['n'] == 0:
            out['notes'].append(f"{ws_['display']}: no complete stand drilled in its record"); continue
        data[wid] = ws_
    if not data:
        out['status'] = 'EMPTY'
        out['lines'].append('No roadmap: no well in the workspace has a drilling record with stands drilled (add the wells\' drilling recorder exports on the Wells tab).')
        return out
    tops = []
    for wid in data:
        tops = formation_tops(ws, wid)
        if tops:
            out['formation_tops'] = {'well': wid, 'tops': tops}
            break
    if not tops:
        out['notes'].append('the stretches are named by depth alone: no well has a surface position, a reference elevation and a formation-top layer to name them by')
    no_tvd = [w['display'] for w in data.values() if not w['tvd_how']]
    if no_tvd and len(data) > 1:
        out['notes'].append(f"{', '.join(no_tvd)}: no true vertical depth in the record, so the measured depth stands in for it - right for a vertical hole, wrong for a deviated one")
    for wid, w in data.items():
        st = w['stands']
        mode = st['mode']
        out['wells'].append({'well': wid, 'display': w['display'], 'stands': st['n'], 'rotary_stands': int((mode == 0).sum()), 'slide_stands': int((mode == 1).sum()), 'mixed_stands': int((mode == 2).sum()),
                             'incomplete_stands': st['incomplete'], 'implausible_stands': st.get('implausible', 0), 'feet': round(float(st['feet'].sum()), 0), 'hours': round(float(st['hours'].sum()), 1),
                             'rop_ft_h': round(float(st['feet'].sum() / st['hours'].sum()), 1), 'md_from_ft': round(float(st['md'][0] - STAND_FT / 2), 0), 'md_to_ft': round(float(st['md'][-1] + STAND_FT / 2), 0),
                             'heel_md_ft': w['heel_md_ft'], 'tvd': w['tvd_how'] or 'none in the record: its measured depth stands in', 'channels': w['channels'], 'record_end': w['record_end']})
    rm = build(data)
    for (key, mode), ent in sorted(rm.items(), key=lambda kv: (_order(kv[0][0]), kv[0][1])):
        row = {**ent, 'stretch': list(key)}
        if key[0] == 'TVD' and tops:
            fm = _formation_at(tops, (key[1] + 0.5) * TVD_STRETCH_FT)
            if fm:
                row['formation'] = fm['formation']; row['formation_top_tvd_ft'] = [fm['tvd_low_ft'], fm['tvd_high_ft']]
        out['stretches'].append(row)
    if len(data) >= 2:
        for wid in data:
            ck = check(data, wid)
            ck['display'] = data[wid]['display']
            out['checks'].append(ck)
    else:
        out['notes'].append('one well with a record: a roadmap of its own stands, and no check - the check sets each well against the others')
    # the next stand
    subj = subject if subject in data else max(data, key=lambda w_: data[w_]['record_end'])
    if subject and subject not in data:
        out['notes'].append(f'{subject} has no drilling record with stands; the next stand is given for {data[subj]["display"]}')
    w = data[subj]; last = w['last']
    if last:
        others = build(data, leave_out=subj) if len(data) >= 2 else rm
        heel = w['heel_md_ft']
        if heel is not None and last['depth_ft'] >= heel:
            key = ('LAT', int((last['depth_ft'] - heel) // LATERAL_STRETCH_FT))
        elif last.get('tvd_ft') is not None:
            key = ('TVD', int(last['tvd_ft'] // TVD_STRETCH_FT))
        else:
            key = ('TVD', int(last['depth_ft'] // TVD_STRETCH_FT))
        mode = 'rotary' if last['rotary_share'] >= 0.5 else 'slide'
        nxt = (key[0], key[1] + 1)
        rows = []
        for kk in (key, nxt):
            ent = others.get((kk, mode))
            r_ = {'stretch': list(kk), 'label': stretch_label(kk), 'mode': mode, 'status': (ent or {}).get('status', 'NO STANDS'), 'window': (ent or {}).get('window'),
                  'pace_ft_h': (ent or {}).get('rop_best_ft_h'), 'n_stands': (ent or {}).get('n_stands', 0)}
            if kk == key and ent and ent.get('status') == 'OK':
                r_['now'] = {p: {'value': last.get(p), 'where': ('inside' if ent['window'].get(p) and last.get(p) is not None and ent['window'][p]['low'] - TOLERANCE * abs(ent['window'][p]['mid']) <= last[p] <= ent['window'][p]['high'] + TOLERANCE * abs(ent['window'][p]['mid'])
                                                                 else ('below' if ent['window'].get(p) and last.get(p) is not None and last[p] < ent['window'][p]['low'] else 'above' if ent['window'].get(p) and last.get(p) is not None else None))}
                             for p in SETPOINTS[mode]}
            rows.append(r_)
        out['next'] = {'well': subj, 'display': w['display'], 'depth_ft': last['depth_ft'], 'tvd_ft': last.get('tvd_ft'), 'time': last['time'], 'mode': mode, 'last_ten_minutes': last,
                       'from_wells': sorted(x for x in data if x != subj) if len(data) >= 2 else [subj], 'stretches': rows}
    # the words
    L = out['lines']
    n_ok = sum(1 for s_ in out['stretches'] if s_['status'] == 'OK')
    L.append(f"{len(data)} well(s) with a drilling record: {sum(w_['stands'] for w_ in out['wells']):,} stands of {STAND_FT:g} ft, {sum(w_['feet'] for w_ in out['wells']):,.0f} ft in "
             f"{sum(w_['hours'] for w_ in out['wells']):,.0f} on-bottom hours; a window in {n_ok} of {len(out['stretches'])} stretch-and-mode pairs (twelve stands or more).")
    for ck in out['checks']:
        if not ck['judged_stands']:
            L.append(f"{ck['display']}: no stand of it falls in a stretch the other wells' roadmap covers."); continue
        L.append(f"{ck['display']} against the roadmap of the other well(s): {ck['inside_stands']} of {ck['judged_stands']} stands inside the window ({ck['inside_share'] * 100:.0f}%)"
                 + (f"; stretch by stretch over {ck['paired_ft']:,.0f} ft, the half of its stands nearer the window made {ck['nearer_rop_ft_h']:g} ft/h against {ck['farther_rop_ft_h']:g} for the farther half ({ck['paired_gain'] * 100:+.0f}%)" if ck['paired_gain'] is not None else '; no stretch where some of its stands were nearer the window than others')
                 + (f", rank correlation of distance with rate {ck['rank_correlation']:+.2f}" if ck['rank_correlation'] is not None else '')
                 + f"; the room: its stands slower than the roadmap's pace took {ck['prize_h']:g} h longer than they would have at it, of {ck['hours_judged']:g} on-bottom hours.")
    if out['checks']:
        g = [c['paired_gain'] for c in out['checks'] if c['paired_gain'] is not None]
        if g:
            n_for = sum(1 for x in g if x > 0.03); n_against = sum(1 for x in g if x < -0.03)
            verdict = ('The check supports the roadmap' if n_for == len(g) else 'The check goes against the roadmap' if n_against == len(g) else 'The check is split' if n_for and n_against
                       else 'The check leans to the roadmap' if n_for * 2 >= len(g) else 'The check leans against the roadmap' if n_against * 2 >= len(g) else 'The check is not decisive')
            L.append(f"{verdict}: nearer the window was the faster half on {n_for} of {len(g)} well(s) and the slower on {n_against}. The fastest stands were drilled in the window; whether the window made them fast is what this line "
                     'says, and the bit, the motor and the mud are not in the record.')
    if out['next']:
        nx = out['next']; cur = nx['stretches'][0]
        if cur['status'] == 'OK':
            now = cur.get('now') or {}
            L.append(f"{nx['display']} at {nx['depth_ft']:,.0f} ft ({cur['label']}, {nx['mode']}): the window is " + ', '.join(f"{PN[p]} {cur['window'][p]['low']:g} to {cur['window'][p]['high']:g}" for p in SETPOINTS[nx['mode']] if cur['window'].get(p))
                     + f" at a pace of {cur['pace_ft_h']:g} ft/h; its last ten minutes: " + ', '.join(f"{PN[p]} {now[p]['value']:g} ({now[p]['where']})" for p in SETPOINTS[nx['mode']] if now.get(p) and now[p]['value'] is not None) + '.')
        else:
            L.append(f"{nx['display']} at {nx['depth_ft']:,.0f} ft ({cur['label']}, {nx['mode']}): the other wells give no window there ({cur['status'].lower()}).")
    L.append('The window is what the fastest quarter of the offset stands were drilled with - not a limit of the string, the motor or the hole, and not a cause.')
    from . import public_pad as _PP
    cr = _PP.credit(ws)
    if cr:
        out['public_source'] = cr
        L.append(cr['line'])
    return out


PN = {p: words for p, words, _ in PARAMS}
PU = {p: unit for p, _, unit in PARAMS}


def text(p: dict) -> str:
    out = ['Drilling roadmap', '=' * 16]
    out.extend(p['lines'])
    if p['stretches']:
        out.append('  stretch                                             mode    stands   all ft/h  best ft/h   weight on bit      rotary speed        flow            diff pressure')
        for s in p['stretches']:
            w = s.get('window') or {}
            f = lambda k: (f"{w[k]['low']:>7g}-{w[k]['high']:<7g}" if w.get(k) else f"{'-':^15s}")
            out.append(f"  {(s['label'] + (' (' + s['formation'] + ')' if s.get('formation') else ''))[:50]:50s}  {s['mode']:6s}  {s['n_stands']:6d}  {s['rop_all_ft_h']:9g}  "
                       + (f"{s['rop_best_ft_h']:9g}" if s['status'] == 'OK' else f"{'too few':>9s}") + f"   {f('wob')}  {f('rpm')}  {f('flow')}  {f('diff')}")
    for n in p.get('notes', []):
        out.append('  note: ' + n)
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'roadmap')
    p['program_version'] = program_version
    with open(os.path.join(d, 'roadmap.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'roadmap.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Drilling roadmap\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p) + '\n```\n')
    with open(os.path.join(d, 'roadmap.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['stretch', 'formation', 'mode', 'stands', 'wells', 'median_rop_ft_h', 'best_quarter_rop_ft_h'] + [f'{k}_{e}' for k, _, _ in PARAMS for e in ('low', 'mid', 'high')])
        for s in p['stretches']:
            w = s.get('window') or {}
            wr.writerow([s['label'], s.get('formation', ''), s['mode'], s['n_stands'], ' '.join(s['wells']), s['rop_all_ft_h'], s.get('rop_best_ft_h', '')]
                        + [(w[k][e] if w.get(k) else '') for k, _, _ in PARAMS for e in ('low', 'mid', 'high')])
    files = ['roadmap.json', 'roadmap.md', 'roadmap.csv']
    ws.audit(actor, 'roadmap.report', {'files': files, 'wells': [w_['well'] for w_ in p['wells']], 'stretches': len(p['stretches'])})
    return {'folder': d, 'files': files, 'page': p}


# --------------------------------------------------------------------------------------------------------------
# the labelled scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_record(seed: int, td_ft: float = 9000.0, kop_ft: float = 5000.0, best: Optional[Dict[str, Tuple[float, float]]] = None, follow: float = 0.5) -> dict:
    """One well's record as arrays, a sample a second of drilling only: vertical to the kick-off, a curve of 1,000 ft,
    then a lateral. In each stretch the rock has a best weight and rotary speed (`best`: by stretch kind, a (weight,
    speed) pair) and the rate falls off away from them; the driller runs near the best on a share `follow` of the stands
    and elsewhere on the rest. SIMULATION."""
    rng = np.random.default_rng(seed)
    best = best or {'upper': (25.0, 90.0), 'lower': (40.0, 140.0), 'lateral': (30.0, 170.0)}
    t, bot, drl, rot, wob, rpm, flow, diff, tvd = [], [], [], [], [], [], [], [], []
    now = 0.0; depth = 1000.0
    while depth < td_ft:
        zone = 'upper' if depth < 3000.0 else ('lower' if depth < kop_ft + 1000.0 else 'lateral')
        bw, br = best[zone]
        on_plan = rng.random() < follow
        w_ = bw * (1.0 + rng.normal(0, 0.04)) if on_plan else bw * rng.choice([0.55, 0.7, 1.35]) * (1.0 + rng.normal(0, 0.04))
        r_ = br * (1.0 + rng.normal(0, 0.04)) if on_plan else br * rng.choice([0.6, 0.75, 1.3]) * (1.0 + rng.normal(0, 0.04))
        sliding = (kop_ft <= depth < kop_ft + 1000.0) and rng.random() < 0.6
        peak = {'upper': 220.0, 'lower': 140.0, 'lateral': 180.0}[zone]
        rate = peak * np.exp(-((w_ / bw - 1.0) / 0.35) ** 2) * (1.0 if sliding else np.exp(-((r_ / br - 1.0) / 0.4) ** 2)) * (0.45 if sliding else 1.0) * (1.0 + rng.normal(0, 0.05))
        rate = max(rate, 8.0)
        secs = int(round(STAND_FT / rate * 3600.0))
        s = np.arange(secs)
        t.append(now + s); bot.append(depth + (s + 1) * rate / 3600.0); drl.append(np.ones(secs, bool)); rot.append(np.full(secs, not sliding))
        wob.append(np.full(secs, w_)); rpm.append(np.full(secs, 0.0 if sliding else r_)); flow.append(np.full(secs, 550.0 + rng.normal(0, 5)))
        diff.append(np.full(secs, 12.0 * w_ + rng.normal(0, 10)))
        d_mid = depth + (s + 1) * rate / 3600.0
        tvd.append(np.where(d_mid < kop_ft, d_mid, kop_ft + 636.6 * np.sin(np.clip((d_mid - kop_ft) / 1000.0, 0, 1) * np.pi / 2)))
        now += secs + 600.0                                             # the connection: ten minutes not drilling
        depth += STAND_FT
    cat = lambda a: np.concatenate(a)
    return {'t': cat(t), 'bottom': cat(bot), 'drilling': cat(drl), 'rotating': cat(rot), 'ch': {'wob': cat(wob), 'rpm': cat(rpm), 'flow': cat(flow), 'diff': cat(diff), 'spp': None, 'torque': None},
            'tvd': cat(tvd), 'best': best}


def write_edr(rec: dict, path: str, every: int = 10) -> str:
    """A labelled record as a drilling recorder's export, a row every `every` seconds of drilling."""
    from datetime import datetime as _dt
    t0 = _dt(2026, 9, 1, 6, 0, 0)
    ch = rec['ch']
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Interpolated TVD (feet)', 'On Bottom (unitless)']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for i in range(0, len(rec['t']), every):
            ts = t0 + timedelta(seconds=float(rec['t'][i]))
            w.writerow([ts.strftime('%Y/%m/%d'), ts.strftime('%H:%M:%S'), f"{rec['bottom'][i]:.2f}", f"{rec['bottom'][i]:.2f}", '100.0', f"{ch['wob'][i]:.2f}", f"{ch['rpm'][i]:.1f}",
                        f"{3000.0 + ch['diff'][i]:.1f}", f"{ch['diff'][i]:.1f}", f"{ch['flow'][i]:.1f}", f"{rec['tvd'][i]:.2f}", '1'])
    return path


def selftest() -> dict:
    checks = []
    recs = {f'W{i}': synthetic_record(seed=10 + i) for i in range(3)}
    wells = {}
    for wid, r in recs.items():
        st = stands_of(r['t'], r['bottom'], r['drilling'], r['rotating'], r['ch'], r['tvd'])
        keys, heel = stretch_keys(st)
        wells[wid] = {'stands': st, 'keys': keys, 'heel': heel}
    st0 = wells['W0']['stands']
    checks.append({'check': 'the stands: eighty a well of 100 ft, the footage and the hours the record\'s', 'value': {'stands': st0['n'], 'feet': round(float(st0['feet'].sum()), 0), 'hours': round(float(st0['hours'].sum()), 2)},
                   'pass': st0['n'] == 80 and abs(float(st0['feet'].sum()) - 8000.0) < 5.0 and abs(float(st0['hours'].sum()) * 3600.0 - float(recs['W0']['drilling'].sum())) < 1.0})
    checks.append({'check': 'the heel: where the hole comes within ten degrees of horizontal and stays there', 'value': wells['W0']['heel'], 'pass': wells['W0']['heel'] is not None and 5700.0 <= wells['W0']['heel'] <= 6050.0})
    checks.append({'check': 'slide stands in the curve only, rotary elsewhere', 'value': {'slide': int((st0['mode'] == 1).sum()), 'mixed': int((st0['mode'] == 2).sum())},
                   'pass': int((st0['mode'] == 1).sum()) >= 2 and all(5000.0 <= m <= 6000.0 for m in st0['md'][st0['mode'] == 1]) and int((st0['mode'] == 2).sum()) == 0})
    rm = build(wells)
    up = rm[(('TVD', 1), 'rotary')]; lat = rm[(('LAT', 1), 'rotary')]; dn = rm[(('TVD', 4), 'rotary')]
    hit = lambda e, w_, r_: e['window']['wob']['low'] * 0.9 <= w_ <= e['window']['wob']['high'] * 1.1 and e['window']['rpm']['low'] * 0.9 <= r_ <= e['window']['rpm']['high'] * 1.1
    checks.append({'check': 'the window holds the rock\'s own best weight and speed, stretch by stretch', 'value': {'upper': [up['window']['wob'], up['window']['rpm']], 'lower': [dn['window']['wob']['mid'], dn['window']['rpm']['mid']], 'lateral': [lat['window']['wob']['mid'], lat['window']['rpm']['mid']]},
                   'pass': hit(up, 25.0, 90.0) and hit(dn, 40.0, 140.0) and hit(lat, 30.0, 170.0) and not hit(up, 40.0, 140.0)})
    checks.append({'check': 'the roadmap\'s pace is faster than the median, and the best stands a quarter', 'value': {'all': up['rop_all_ft_h'], 'best': up['rop_best_ft_h'], 'n': up['n_stands'], 'n_best': up['n_best']},
                   'pass': up['rop_best_ft_h'] > 1.2 * up['rop_all_ft_h'] and 0.2 <= up['n_best'] / up['n_stands'] <= 0.4})
    cks = [check(wells, w_) for w_ in wells]
    checks.append({'check': 'each well against the others\' roadmap: the nearer half the faster, by a quarter or more, on every well, and the farther the slower', 'value': [(c['inside_stands'], c['judged_stands'], c['paired_gain'], c['rank_correlation']) for c in cks],
                   'pass': all(c['paired_gain'] is not None and c['paired_gain'] > 0.25 and c['rank_correlation'] < -0.3 for c in cks) and all(0.2 < c['inside_share'] < 0.8 for c in cks)})
    checks.append({'check': 'the room: hours beyond the roadmap\'s pace, more than none and less than all', 'value': [(c['prize_h'], c['hours_judged']) for c in cks], 'pass': all(0 < c['prize_h'] < c['hours_judged'] for c in cks)})
    # rock that does not care: the rate random, the same parameters - the check must not find a gain
    flat = {}
    for i in range(3):
        r = synthetic_record(seed=40 + i)
        st = stands_of(r['t'], r['bottom'], r['drilling'], r['rotating'], r['ch'], r['tvd'])
        rng = np.random.default_rng(90 + i)
        st['rop'] = st['rop'].mean() * (1.0 + 0.3 * rng.standard_normal(st['n'])).clip(0.3)
        st['hours'] = st['feet'] / st['rop']
        keys, _ = stretch_keys(st)
        flat[f'F{i}'] = {'stands': st, 'keys': keys}
    g = [check(flat, w_)['paired_gain'] for w_ in flat]
    checks.append({'check': 'rock that does not care (the rate shuffled): no gain found inside the window', 'value': [x for x in g], 'pass': all(x is None or abs(x) < 0.15 for x in g)})
    one = build({'W0': wells['W0']})
    few = one[(('TVD', 5), 'slide')]
    checks.append({'check': 'a stretch with fewer than twelve stands gives no window', 'value': [few['n_stands'], few['status']], 'pass': few['n_stands'] < 12 and few['status'] == 'TOO FEW STANDS' and inside(few, 'slide', {'wob': 25.0}) is None})
    checks.append({'check': 'each stretch names its fastest well and what each well ran', 'value': [up['fastest_well'], {w: v['rop_ft_h'] for w, v in up['by_well'].items()}],
                   'pass': up['fastest_well'] == max(up['by_well'], key=lambda w: up['by_well'][w]['rop_ft_h']) and all('wob' in v and 'rpm' in v for v in up['by_well'].values())})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
