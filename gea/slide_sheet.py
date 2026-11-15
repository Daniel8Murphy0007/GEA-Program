"""slide_sheet - each slide and each rotated stretch of a well, and what the assembly did to the hole in them.

A steerable assembly drills two ways: rotated, when the string turns and the hole goes where the assembly tends to
take it; and slid, when the string is held and the bent motor is pointed - its toolface - to build, drop or turn. A
directional driller's slide sheet is the list of those stretches. This module makes it from two things the site
already has: the drilling record (which says, second by second, whether the bit was drilling with the string turning
or not, and where the bottom of the hole was) and the directional survey (which says how the hole's inclination and
azimuth changed between its stations).

The sheet: every slide and every rotated stretch in the order they were drilled - from, to, footage, drilling time,
rate, weight on bit and differential pressure, the toolface held where the record carries one, and the inclination
and azimuth the survey gives at its two ends. The courses: between each two survey stations, how much was slid and
how much rotated, the dogleg, the build and the turn. The tendency: for each run of an assembly (between two trips to
surface), what the hole did per hundred feet in the courses that were only rotated - the assembly's build or drop,
and above five degrees of inclination its walk - and in the courses with a slide, the dogleg per hundred feet slid.

What it will not call a measurement: the slide itself (it is the record's rotary speed under ten turns a minute while
drilling - a string rocked through that, or a rotary-steerable tool, reads otherwise); a toolface the record does not
carry; the dogleg per hundred feet slid as the motor's own yield (the whole of a course's dogleg is set against its
slide footage, the rotated part's share included); the attitude between two stations (it is carried evenly from one
to the next); the bit's own attitude (a survey station is behind the bit, and the sheet says what the hole did, not
what the assembly was doing at the moment); a tendency from fewer than three courses.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
SET_BACK_FT = 2.0                  # the hole depth falling this far below where the stretch had reached is the depth set back
MIN_FT = 3.0                       # a slide or a rotated stretch shorter than this is folded into the one before it
SURFACE_FT = 300.0                 # the bit above this, with the hole deeper than DEEP_FT, is the assembly at surface: a new run
DEEP_FT = 600.0
ROTATED_MAX_SLIDE = 0.05           # a course with no more than this share slid counts as rotated
COURSE_FT = 30.0                   # survey stations closer than this are taken together: a course is this long or longer
MIN_COURSE_FT = 20.0               # a course shorter than this gives no rate
SLID_MIN_FT = 10.0                 # a course with this much slid gives a dogleg per hundred feet slid
COVER_MIN = 0.8                    # a course the record drilled less than this share of is not judged
WALK_MIN_INC = 5.0                 # below this inclination an azimuth is not a direction to walk from
TF_GRAVITY_MIN_INC = 5.0           # below this inclination the toolface is the magnetic one
MIN_COURSES = 5
MAX_ROP_FT_H = 2000.0              # the bottom of the hole moving faster than this over a minute is the depth being set, not drilled
MEANS = (('Weight on Bit', 'wob'), ('Differential Pressure', 'diff'), ('Rotary RPM', 'rpm'), ('Standpipe Pressure', 'spp'), ('Total Pump Output', 'flow'))
NOT_A_MEASUREMENT = [
    'the slide: it is the record\'s rotary speed under ten turns a minute while drilling - a string rocked through that, or a rotary-steerable tool, reads otherwise',
    'a toolface the record does not carry: the column is left out, and nothing is put in its place',
    'the dogleg per hundred feet slid as the motor\'s own yield: the whole of a course\'s dogleg is set against its slide footage, the rotated part\'s share included',
    'the attitude between two survey stations: it is carried evenly from one to the next',
    'the bit\'s own attitude: a survey station is behind the bit, and the sheet says what the hole did',
    f'a tendency from fewer than {MIN_COURSES} courses: it is given as not enough',
]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def circular(deg: np.ndarray, weight: Optional[np.ndarray] = None) -> Tuple[Optional[float], Optional[float]]:
    """The mean direction of angles in degrees, and how tightly they were held (the circular standard deviation)."""
    a = np.asarray(deg, dtype=np.float64)
    ok = np.isfinite(a)
    if not ok.any():
        return None, None
    w = np.ones(len(a)) if weight is None else np.asarray(weight, dtype=np.float64)
    w = np.where(ok, w, 0.0)
    if w.sum() <= 0:
        return None, None
    r = np.radians(np.where(ok, a, 0.0))
    c, s = float((w * np.cos(r)).sum() / w.sum()), float((w * np.sin(r)).sum() / w.sum())
    R = math.hypot(c, s)
    mean = math.degrees(math.atan2(s, c)) % 360.0
    spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(R)))) if R > 1e-12 else 180.0
    return mean, min(180.0, spread)


def toolface_words(tf: Optional[float], gravity: bool) -> str:
    if tf is None:
        return ''
    if not gravity:
        return f'{tf:.0f} magnetic'
    t = (tf + 180.0) % 360.0 - 180.0
    return 'high side' if abs(t) < 0.5 else 'low side' if abs(abs(t) - 180.0) < 0.5 else f"{abs(t):.0f} {'right' if t > 0 else 'left'}"


def dogleg_deg(i1: float, a1: float, i2: float, a2: float) -> float:
    i1, a1, i2, a2 = (math.radians(v) for v in (i1, a1, i2, a2))
    c = math.cos(i2 - i1) - math.sin(i1) * math.sin(i2) * (1.0 - math.cos(a2 - a1))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _turn(a1: float, a2: float) -> float:
    return (a2 - a1 + 180.0) % 360.0 - 180.0


def attitude_at(md_ft: float, st: List[dict]) -> Optional[Tuple[float, float]]:
    """The inclination and azimuth at a depth, carried evenly between the stations either side; None outside them."""
    if not st or md_ft < st[0]['md_ft'] - 1e-6 or md_ft > st[-1]['md_ft'] + 1e-6:
        return None
    for a, b in zip(st, st[1:]):
        if a['md_ft'] <= md_ft <= b['md_ft']:
            f = (md_ft - a['md_ft']) / (b['md_ft'] - a['md_ft']) if b['md_ft'] > a['md_ft'] else 0.0
            return a['inc'] + f * (b['inc'] - a['inc']), (a['azi'] + f * _turn(a['azi'], b['azi'])) % 360.0
    return st[-1]['inc'], st[-1]['azi']


def sheet(t, state, bottom, bit, chans: Dict[str, np.ndarray], stations: List[dict], rot_code: int = 0, slide_code: int = 1,
          toolface: Optional[Dict[str, np.ndarray]] = None, t0: Optional[datetime] = None) -> dict:
    """The slide sheet, the courses and the tendency from one well's arrays: t (seconds), the rig state of every sample,
    the running bottom of the hole and the bit depth (ft), the channels to average, and the survey stations
    ({md_ft, inc, azi}). `toolface`: {'gravity': ..., 'magnetic': ...} in degrees, either or both."""
    t = np.asarray(t, dtype=np.float64); st_ = np.asarray(state)
    bot = np.nan_to_num(np.asarray(bottom, dtype=np.float64))
    n = len(t)
    dtv = np.diff(t, prepend=t[0]) if n else np.zeros(0)
    med = float(np.median(dtv[1:])) if n > 2 else 1.0
    dtv = np.where((dtv > 0) & (dtv <= 10.0 * max(med, 1.0)), dtv, 0.0)
    db = np.diff(bot, prepend=bot[0]) if n else np.zeros(0)
    db = np.where((db > 0) & (db < 50), db, 0.0)
    drl = (st_ == rot_code) | (st_ == slide_code)
    if n > 2:                                                          # a depth set forward and counted up is not hole drilled: left out of the sheet
        fast = np.zeros(n, dtype=bool)
        for win in (10.0, 60.0):                                         # over ten seconds and over a minute
            back = np.minimum(np.searchsorted(t, t - win, side='left'), np.maximum(np.arange(n) - 1, 0))
            span = np.maximum(t - t[back], 1.0)
            fast |= (bot - bot[back]) / span * 3600.0 > MAX_ROP_FT_H
        fast = fast | np.r_[fast[1:], False]
        set_ft = float(db[fast & drl].sum())
        db = np.where(fast, 0.0, db); drl = drl & ~fast
        fc = np.cumsum(fast)
    else:
        set_ft = 0.0; fc = np.zeros(n, dtype=int)
    tf = toolface or {}
    # the runs of an assembly: a new one each time drilling starts again after the bit has been at surface
    run_of = np.zeros(n, dtype=int)
    if bit is not None and n:
        b = np.nan_to_num(np.asarray(bit, dtype=np.float64), nan=1e9)
        at_surface = (b < SURFACE_FT) & (bot > DEEP_FT)
        seen = False; k = 1
        for i in range(n):
            if at_surface[i]:
                seen = True
            elif drl[i] and seen:
                k += 1; seen = False
            run_of[i] = k
    else:
        run_of[:] = 1
    idx = np.flatnonzero(drl)
    raw: List[dict] = []
    for i in idx.tolist():
        kind = 'SLIDE' if st_[i] == slide_code else 'ROTATE'
        # the depth set back, or set forward and counted up: what follows is a stretch of its own
        back = bool(raw) and (float(bot[i] - db[i]) < raw[-1]['to_ft'] - SET_BACK_FT or fc[i] != fc[raw[-1]['last']])
        if raw and raw[-1]['kind'] == kind and raw[-1]['run'] == int(run_of[i]) and not back:
            r = raw[-1]
        else:
            r = {'kind': kind, 'run': int(run_of[i]), 'from_ft': float(bot[i] - db[i]), 'i': [], 'i0': i, 'set_back': back}
            raw.append(r)
        r['i'].append(i); r['to_ft'] = float(bot[i]); r['last'] = i
    for r in raw:
        ii = np.asarray(r.pop('i'))
        r['_ii'] = ii; r['footage_ft'] = float(db[ii].sum()); r['seconds'] = float(dtv[ii].sum())
    # a stretch too short to be one is folded into the one before it (or, the first, into the next), and like neighbours joined
    def fold(rows: List[dict]) -> List[dict]:
        out: List[dict] = []
        for r in rows:
            if out and out[-1]['run'] == r['run'] and not r.get('set_back') and (r['footage_ft'] < MIN_FT or out[-1]['kind'] == r['kind']):
                p = out[-1]
                if r['kind'] != p['kind']:
                    p['folded_ft'] = p.get('folded_ft', 0.0) + r['footage_ft']
                p['_ii'] = np.concatenate([p['_ii'], r['_ii']]); p['to_ft'] = max(p['to_ft'], r['to_ft']); p['footage_ft'] += r['footage_ft']; p['seconds'] += r['seconds']
            else:
                out.append(r)
        k = 0
        while k < len(out) - 1:                                          # one too short with none before it to take it: into the one after it
            a, b_ = out[k], out[k + 1]
            if a['footage_ft'] < MIN_FT and a['run'] == b_['run'] and not b_.get('set_back'):
                b_['_ii'] = np.concatenate([a['_ii'], b_['_ii']]); b_['from_ft'] = min(a['from_ft'], b_['from_ft']); b_['footage_ft'] += a['footage_ft']; b_['seconds'] += a['seconds']
                b_['folded_ft'] = b_.get('folded_ft', 0.0) + a['footage_ft']; b_['set_back'] = a.get('set_back', False)
                del out[k]
            else:
                k += 1
        return out
    rows = fold(fold(raw))
    short_ft = float(sum(r['footage_ft'] for r in rows if r['footage_ft'] < MIN_FT))
    rows = [r for r in rows if r['footage_ft'] >= MIN_FT]                 # and one alone, with no neighbour in its run, is no stretch at all
    too_fast = [r for r in rows if r['seconds'] <= 0 or r['footage_ft'] / (r['seconds'] / 3600.0) > MAX_ROP_FT_H]
    set_ft += float(sum(r['footage_ft'] for r in too_fast))              # a whole stretch faster than any bit drills is the depth being set too
    rows = [r for r in rows if not (r['seconds'] <= 0 or r['footage_ft'] / (r['seconds'] / 3600.0) > MAX_ROP_FT_H)]
    T = (lambda s_: (t0 + timedelta(seconds=float(s_))).isoformat(timespec='seconds')) if t0 is not None else (lambda s_: None)
    out_rows = []
    for k, r in enumerate(rows):
        ii = r.pop('_ii')
        row: Dict[str, Any] = {'n': k + 1, 'kind': r['kind'], 'run': r['run'], 'from_ft': round(r['from_ft'], 1), 'to_ft': round(r['to_ft'], 1), 'footage_ft': round(r['footage_ft'], 1),
                               'minutes': round(r['seconds'] / 60.0, 1), 'rop_ft_h': (round(r['footage_ft'] / (r['seconds'] / 3600.0), 1) if r['seconds'] > 0 else None),
                               'start': T(t[ii[0]]), 'end': T(t[ii[-1]]), 'folded_ft': round(r.get('folded_ft', 0.0), 1)}
        for name, key in MEANS:
            a = chans.get(name)
            if a is not None:
                v = np.asarray(a, dtype=np.float64)[ii]
                row[key] = round(float(np.nanmean(v)), 1) if np.isfinite(v).any() else None
        a0, a1 = attitude_at(r['from_ft'], stations), attitude_at(r['to_ft'], stations)
        if a0 and a1:
            row.update(inc_from=round(a0[0], 2), azi_from=round(a0[1], 1), inc_to=round(a1[0], 2), azi_to=round(a1[1], 1), build_deg=round(a1[0] - a0[0], 2), turn_deg=round(_turn(a0[1], a1[1]), 1))
        if r['kind'] == 'SLIDE' and tf:
            inc_here = a0[0] if a0 else None
            grav = tf.get('gravity') is not None and (inc_here is None or inc_here >= TF_GRAVITY_MIN_INC or tf.get('magnetic') is None)
            src = tf.get('gravity') if grav else tf.get('magnetic')
            if src is not None:
                m, sp = circular(np.asarray(src, dtype=np.float64)[ii], db[ii] if db[ii].sum() > 0 else None)
                v_ = np.asarray(src, dtype=np.float64)[ii]; v_ = v_[np.isfinite(v_)]
                if m is not None and len(v_) >= 6 and float(np.ptp(v_)) < 0.05:
                    row.update(toolface_held=True)                       # the same value at every sample: a number left standing, not a toolface read
                elif m is not None:
                    row.update(toolface_deg=round(m, 1), toolface_spread_deg=round(sp, 1), toolface_gravity=bool(grav), toolface=toolface_words(m, bool(grav)))
        out_rows.append(row)
    # the courses: station to station, a station nearer than COURSE_FT to the one before it passed over
    thin: List[dict] = []
    for k, s_ in enumerate(stations):
        if not thin or s_['md_ft'] - thin[-1]['md_ft'] >= COURSE_FT or k == len(stations) - 1:
            thin.append(s_)
    courses = []
    for a, b_ in zip(thin, thin[1:]):
        length = b_['md_ft'] - a['md_ft']
        if length <= 0:
            continue
        sl = ro = 0.0; tfs: List[Tuple[float, float]] = []; run = None
        for r in out_rows:
            lo, hi = max(r['from_ft'], a['md_ft']), min(r['to_ft'], b_['md_ft'])
            if hi <= lo or r['to_ft'] <= r['from_ft']:
                continue
            part = (hi - lo) / (r['to_ft'] - r['from_ft']) * r['footage_ft']
            if r['kind'] == 'SLIDE':
                sl += part
                if r.get('toolface_deg') is not None and r.get('toolface_gravity'):
                    tfs.append((r['toolface_deg'], part))
            else:
                ro += part
            run = r['run'] if run is None else run
        cover = (sl + ro) / length
        dl = dogleg_deg(a['inc'], a['azi'], b_['inc'], b_['azi'])
        c: Dict[str, Any] = {'from_ft': round(a['md_ft'], 1), 'to_ft': round(b_['md_ft'], 1), 'length_ft': round(length, 1), 'inc_from': a['inc'], 'inc_to': b_['inc'], 'azi_from': a['azi'], 'azi_to': b_['azi'],
                             'dogleg_deg': round(dl, 3), 'dls_deg_100ft': round(dl / length * 100.0, 2), 'build_deg_100ft': round((b_['inc'] - a['inc']) / length * 100.0, 2),
                             'turn_deg_100ft': (round(_turn(a['azi'], b_['azi']) / length * 100.0, 2) if (a['inc'] + b_['inc']) / 2.0 >= WALK_MIN_INC else None),
                             'slide_ft': round(sl, 1), 'rotate_ft': round(ro, 1), 'cover': round(min(cover, 9.99), 2), 'run': run}
        if cover >= COVER_MIN and length >= MIN_COURSE_FT:
            c['slide_share'] = round(sl / (sl + ro), 3)
            c['kind'] = 'ROTATED' if c['slide_share'] <= ROTATED_MAX_SLIDE else 'SLID'
            if sl >= SLID_MIN_FT:
                c['dogleg_per_100ft_slid'] = round(dl / sl * 100.0, 2)
            if tfs:
                m, _ = circular(np.array([x for x, _ in tfs]), np.array([w for _, w in tfs]))
                c['toolface_deg'] = round(m, 1) if m is not None else None
        else:
            c['slide_share'] = None
            c['kind'] = 'NOT IN THIS RECORD' if cover < COVER_MIN else 'TOO SHORT'
        courses.append(c)
    # the tendency, run by run and over the well
    def stats(v: List[float]) -> Optional[dict]:
        if len(v) < MIN_COURSES:
            return None
        a = np.asarray(v, dtype=np.float64)
        return {'n': int(len(a)), 'median': round(float(np.median(a)), 2), 'p25': round(float(np.percentile(a, 25)), 2), 'p75': round(float(np.percentile(a, 75)), 2)}

    def tendency(cs: List[dict], rs: List[dict]) -> dict:
        rot = [c for c in cs if c['kind'] == 'ROTATED']
        sld = [c for c in cs if c.get('dogleg_per_100ft_slid') is not None]
        sf = sum(r['footage_ft'] for r in rs if r['kind'] == 'SLIDE'); rf = sum(r['footage_ft'] for r in rs if r['kind'] == 'ROTATE')
        sh = sum(r['minutes'] for r in rs if r['kind'] == 'SLIDE') / 60.0; rh = sum(r['minutes'] for r in rs if r['kind'] == 'ROTATE') / 60.0
        sl_len = [r['footage_ft'] for r in rs if r['kind'] == 'SLIDE']
        return {'slide_ft': round(sf, 1), 'rotate_ft': round(rf, 1), 'slide_share': (round(sf / (sf + rf), 3) if sf + rf > 0 else None), 'slides': len(sl_len),
                'median_slide_ft': (round(float(np.median(sl_len)), 1) if sl_len else None), 'slide_rop_ft_h': (round(sf / sh, 1) if sh > 0 else None), 'rotate_rop_ft_h': (round(rf / rh, 1) if rh > 0 else None),
                'from_ft': (min(r['from_ft'] for r in rs) if rs else None), 'to_ft': (max(r['to_ft'] for r in rs) if rs else None),
                'rotated_courses': len(rot), 'slid_courses': len(sld), 'rotary_build_deg_100ft': stats([c['build_deg_100ft'] for c in rot]),
                'rotary_walk_deg_100ft': stats([c['turn_deg_100ft'] for c in rot if c['turn_deg_100ft'] is not None]),
                'dogleg_per_100ft_slid': stats([c['dogleg_per_100ft_slid'] for c in sld]),
                'inc_range': ([round(min(min(c['inc_from'], c['inc_to']) for c in cs), 1), round(max(max(c['inc_from'], c['inc_to']) for c in cs), 1)] if cs else None)}
    runs = []
    for k in sorted({r['run'] for r in out_rows}):
        rs = [r for r in out_rows if r['run'] == k]
        cs = [c for c in courses if c.get('run') == k and c['kind'] in ('ROTATED', 'SLID')]
        runs.append({'run': k, **tendency(cs, rs)})
    judged = [c for c in courses if c['kind'] in ('ROTATED', 'SLID')]
    return {'rows': out_rows, 'courses': courses, 'runs': runs, 'well': tendency(judged, out_rows), 'toolface': ('gravity and magnetic' if tf.get('gravity') is not None and tf.get('magnetic') is not None
                                                                                                                    else 'gravity' if tf.get('gravity') is not None else 'magnetic' if tf.get('magnetic') is not None else None),
            'stations': len(stations), 'courses_judged': len(judged), 'depth_set_ft': round(set_ft, 1), 'short_ft': round(short_ft, 1),
            'toolface_read': sum(1 for r in out_rows if r.get('toolface_deg') is not None), 'toolface_held': sum(1 for r in out_rows if r.get('toolface_held'))}


# ---------------------------------------------------------------------------------------------------------------
# the workspace
# ---------------------------------------------------------------------------------------------------------------
def _toolface_channels(c) -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    names = {n.lower().replace(' ', ''): n for n in c._ch}
    for key, want in (('gravity', ('gravitytoolface', 'gravitytoolfacefresh', 'gtf')), ('magnetic', ('magnetictoolface', 'magnetictoolfacefresh', 'mtf'))):
        for w in want:
            if w in names:
                out[key] = np.asarray(c.channel(names[w]), dtype=np.float64); break
    if not out and 'toolface' in names:
        out['gravity'] = np.asarray(c.channel(names['toolface']), dtype=np.float64)
    return out


def well(ws, well_id: str) -> dict:
    """One well's slide sheet, from its drilling record and its survey; or why there is none."""
    from . import drill_string as DS
    from . import rig_state as RS
    from . import well3d as W3
    w = ws.well(well_id)
    base = {'well_id': well_id, 'display': w.get('display') or well_id}
    try:
        c = DS._cache(ws, well_id, build=True)
        rs = RS.well(ws, well_id)
    except Exception as e:
        return {**base, 'status': 'NO DRILLING RECORD', 'why': str(e.args[0]) if e.args else type(e).__name__}
    stations: List[dict] = []
    tj = None
    try:
        tj = W3.trajectory_of(ws, well_id) if w.get('trajectory') else None
    except Exception:
        tj = None
    if tj and tj['stations']:
        stations = [{'md_ft': s['md'] / FT, 'inc': float(s['incl']), 'azi': float(s['azi'])} for s in tj['stations']]
    bottom = c.extra('bottom')
    if bottom is None:
        return {**base, 'status': 'NO DRILLING RECORD', 'why': 'the record has no hole depth'}
    chans = {name: np.asarray(c.channel(name), dtype=np.float64) for name, _ in MEANS if name in c._ch}
    tf = _toolface_channels(c)
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None) if c.meta.get('start_time') else None
    r = sheet(rs['_t'], rs['_state'], np.asarray(bottom, dtype=np.float64), (np.asarray(c.channel('Bit Depth'), dtype=np.float64) if 'Bit Depth' in c._ch else None), chans, stations,
              RS.ROT, RS.SLIDE, tf, t0)
    r.update(base)
    r['status'] = 'OK' if r['rows'] else 'NOTHING DRILLED'
    r['survey'] = ((tj['meta'].get('source') or '') if tj else None)
    r['has_rpm'] = 'Rotary RPM' in c._ch
    r['clock'] = "the drilling recorder's own clock"
    notes = []
    if not r['has_rpm']:
        notes.append('the record has no rotary speed: every stretch reads as slid, and none is')
    if not stations:
        notes.append('no directional survey is attached: the sheet has the stretches and their footage, and no attitude, course or tendency')
    if r['toolface'] is None:
        notes.append('the record carries no toolface: the column is left out')
    elif r.get('toolface_held'):
        notes.append(f"in {r['toolface_held']} slide(s) the record's toolface is the same number at every sample - a value left standing, not a toolface read - and none is given for them")
    try:
        from . import public_pad as PP
        if r['display'] in ((PP.credit(ws) or {}).get('reconstructed') or []):
            notes.append('the bit depth of this record is reconstructed from the block and the slips, not recorded: its stretches are where the reconstruction puts them')
    except Exception:
        pass
    if r.get('depth_set_ft', 0) >= 1.0:
        notes.append(f"{r['depth_set_ft']:,.0f} ft of the record's depth moved faster than {MAX_ROP_FT_H:,.0f} ft/h - the depth being set, not hole drilled - and is left out of the sheet")
    r['notes'] = notes
    return r


def _summary(r: dict) -> dict:
    if r.get('status') != 'OK':
        return {k: r.get(k) for k in ('well_id', 'display', 'status', 'why')}
    w = r['well']
    return {'well_id': r['well_id'], 'display': r['display'], 'status': 'OK', 'slides': w['slides'], 'slide_ft': w['slide_ft'], 'rotate_ft': w['rotate_ft'], 'slide_share': w['slide_share'],
            'slide_rop_ft_h': w['slide_rop_ft_h'], 'rotate_rop_ft_h': w['rotate_rop_ft_h'], 'median_slide_ft': w['median_slide_ft'], 'runs': len(r['runs']), 'stations': r['stations'],
            'courses_judged': r['courses_judged'], 'toolface': r['toolface'], 'rotary_build_deg_100ft': w['rotary_build_deg_100ft'], 'rotary_walk_deg_100ft': w['rotary_walk_deg_100ft'],
            'dogleg_per_100ft_slid': w['dogleg_per_100ft_slid'], 'notes': r['notes']}


def page(ws, well_id: Optional[str] = None, full: bool = True) -> dict:
    """Every well with a drilling record: its summary; the chosen well (or the first that reads) with its sheet, its
    courses and its runs."""
    from . import rig_state as RS
    ids = RS._wells_with_record(ws)
    out: Dict[str, Any] = {'generated_utc': _utc(), 'title': ws.manifest.get('name') or '', 'wells': [], 'not_a_measurement': NOT_A_MEASUREMENT, 'lines': [],
                           'rules': {'min_ft': MIN_FT, 'rotated_max_slide': ROTATED_MAX_SLIDE, 'slid_min_ft': SLID_MIN_FT, 'walk_min_inc': WALK_MIN_INC, 'min_courses': MIN_COURSES, 'surface_ft': SURFACE_FT, 'course_ft': COURSE_FT, 'max_rop_ft_h': MAX_ROP_FT_H}}
    res = {}
    for wid in ids:
        try:
            res[wid] = well(ws, wid)
        except Exception as e:
            res[wid] = {'well_id': wid, 'display': (ws.well(wid).get('display') or wid), 'status': 'NOT READ', 'why': f'{type(e).__name__}: {e}'}
        out['wells'].append(_summary(res[wid]))
    ok = [wid for wid in ids if res[wid].get('status') == 'OK']
    pick = well_id if well_id in res else (ok[0] if ok else (ids[0] if ids else None))
    out['well'] = pick
    out['status'] = 'OK' if ok else 'NO DRILLING RECORD'
    if pick is not None:
        d = res[pick]
        out['detail'] = d if full else {k: v for k, v in d.items() if k not in ('rows', 'courses')}
    out['lines'] = _lines(out, res)
    try:
        from . import public_pad as PP
        cr = PP.credit(ws)
        if cr:
            out['public_source'] = {'name': cr['name'], 'licence': cr['licence'], 'credit': cr['credit']}
            out['lines'].append(cr['line'])
    except Exception:
        pass
    return out


def _st(s: Optional[dict], unit: str = 'deg/100 ft') -> str:
    return 'not enough courses' if not s else f"{s['median']:+.2f} {unit} (the middle half {s['p25']:+.2f} to {s['p75']:+.2f}, {s['n']} courses)"


def _lines(p: dict, res: Dict[str, dict]) -> List[str]:
    if not p['wells']:
        return ['No well of this site has a drilling record: attach a drilling recorder\'s export on the Wells tab, and a directional survey on the 3D tab, and the slide sheet is made from the two.']
    L = []
    for s in p['wells']:
        if s['status'] != 'OK':
            L.append(f"{s['display']}: {s['status'].lower()}" + (f" - {s['why']}" if s.get('why') else '') + '.')
            continue
        t = (f"{s['display']}: {s['slides']} slide(s), {s['slide_ft']:,.0f} ft slid of {s['slide_ft'] + s['rotate_ft']:,.0f} ft drilled ({(s['slide_share'] or 0) * 100:.1f}%)"
             + (f", a median slide of {s['median_slide_ft']:,.0f} ft" if s['median_slide_ft'] is not None else '')
             + (f"; sliding at {s['slide_rop_ft_h']:,.1f} ft/h against {s['rotate_rop_ft_h']:,.1f} rotating" if s['slide_rop_ft_h'] and s['rotate_rop_ft_h'] else '')
             + f"; {s['runs']} run(s) of an assembly")
        if s['stations']:
            t += (f"; over {s['courses_judged']} survey courses, rotated the hole built {_st(s['rotary_build_deg_100ft'])}"
                  + (f" and walked {_st(s['rotary_walk_deg_100ft'])}" if s['rotary_walk_deg_100ft'] else '') + f"; with a slide in the course, the dogleg per 100 ft slid was "
                  + (f"{s['dogleg_per_100ft_slid']['median']:.1f} deg (the middle half {s['dogleg_per_100ft_slid']['p25']:.1f} to {s['dogleg_per_100ft_slid']['p75']:.1f}, {s['dogleg_per_100ft_slid']['n']} courses)"
                     if s['dogleg_per_100ft_slid'] else 'not enough courses'))
        L.append(t + '.' + (' ' + ' '.join(n_[0].upper() + n_[1:] + '.' for n_ in s['notes']) if s['notes'] else ''))
    L.append(f"A stretch is slid when the record's rotary speed is under ten turns a minute while drilling; one under {MIN_FT:g} ft is folded into the stretch before it. A course is rotated when no more than "
             f"{ROTATED_MAX_SLIDE * 100:.0f}% of it was slid. A new run begins each time drilling starts again after the bit has been above {SURFACE_FT:g} ft.")
    return L


def text(p: dict) -> str:
    out = ['Slide sheet: each slide and each rotated stretch, and what the assembly did', '=' * 76]
    out.extend(p['lines'])
    d = p.get('detail')
    if d and d.get('status') == 'OK':
        out.append(f"-- {d['display']}: the runs")
        out.append(f"  {'run':>3s} {'from ft':>9s} {'to ft':>9s} {'slid ft':>8s} {'rot ft':>9s} {'slid %':>6s} {'slides':>6s} {'slide ft/h':>10s} {'rot ft/h':>9s}  rotary build / walk / dogleg per 100 ft slid")
        for r in d['runs']:
            out.append(f"  {r['run']:>3d} {r['from_ft']:>9,.0f} {r['to_ft']:>9,.0f} {r['slide_ft']:>8,.0f} {r['rotate_ft']:>9,.0f} {(r['slide_share'] or 0) * 100:>6.1f} {r['slides']:>6d} "
                       f"{(r['slide_rop_ft_h'] if r['slide_rop_ft_h'] is not None else float('nan')):>10,.1f} {(r['rotate_rop_ft_h'] if r['rotate_rop_ft_h'] is not None else float('nan')):>9,.1f}  "
                       f"{_st(r['rotary_build_deg_100ft'])} / {_st(r['rotary_walk_deg_100ft'])} / {_st(r['dogleg_per_100ft_slid'], 'deg')}")
        rows = d.get('rows')
        if rows:
            sl = [r for r in rows if r['kind'] == 'SLIDE']
            out.append(f"-- {d['display']}: the slides ({len(sl)}; the rotated stretches between them are in the report)")
            out.append(f"  {'#':>4s} {'run':>3s} {'from ft':>9s} {'to ft':>9s} {'ft':>6s} {'min':>6s} {'ft/h':>6s} {'toolface':>12s} {'held +/-':>8s} {'inc':>6s} {'build':>6s} {'turn':>6s}")
            for r in sl[:400]:
                out.append(f"  {r['n']:>4d} {r['run']:>3d} {r['from_ft']:>9,.1f} {r['to_ft']:>9,.1f} {r['footage_ft']:>6.1f} {r['minutes']:>6.1f} {(r['rop_ft_h'] if r['rop_ft_h'] is not None else float('nan')):>6.1f} "
                           f"{(r.get('toolface') or ('not read' if r.get('toolface_held') else '')):>12s} {(str(r['toolface_spread_deg']) if r.get('toolface_spread_deg') is not None else ''):>8s} "
                           f"{(format(r['inc_from'], '.1f') if r.get('inc_from') is not None else ''):>6s} {(format(r['build_deg'], '+.2f') if r.get('build_deg') is not None else ''):>6s} "
                           f"{(format(r['turn_deg'], '+.1f') if r.get('turn_deg') is not None and r.get('inc_from', 0) >= WALK_MIN_INC else ''):>6s}")
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws, full=False)
    d = ws.dir('reports', 'slide_sheet')
    files = ['slide_sheet.json', 'slide_sheet.md', 'slide_sheet.csv', 'courses.csv', 'runs.csv']
    all_ = {s['well_id']: well(ws, s['well_id']) for s in p['wells'] if s['status'] == 'OK'}
    with open(os.path.join(d, 'slide_sheet.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **{k: v for k, v in p.items() if k != 'detail'}, 'sheets': all_}, f, indent=1, default=str)
    with open(os.path.join(d, 'slide_sheet.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Slide sheet\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines'])
                + '\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'slide_sheet.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        cols = ['n', 'kind', 'run', 'from_ft', 'to_ft', 'footage_ft', 'minutes', 'rop_ft_h', 'start', 'end', 'wob', 'diff', 'rpm', 'spp', 'flow', 'toolface_deg', 'toolface', 'toolface_spread_deg', 'inc_from', 'azi_from',
                'inc_to', 'azi_to', 'build_deg', 'turn_deg', 'folded_ft']
        wr.writerow(['well'] + cols)
        for r in all_.values():
            for x in r['rows']:
                wr.writerow([r['display']] + ['' if x.get(k) is None else x.get(k) for k in cols])
    with open(os.path.join(d, 'courses.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        cols = ['from_ft', 'to_ft', 'length_ft', 'run', 'kind', 'slide_ft', 'rotate_ft', 'slide_share', 'inc_from', 'inc_to', 'azi_from', 'azi_to', 'dogleg_deg', 'dls_deg_100ft', 'build_deg_100ft', 'turn_deg_100ft',
                'dogleg_per_100ft_slid', 'toolface_deg', 'cover']
        wr.writerow(['well'] + cols)
        for r in all_.values():
            for x in r['courses']:
                wr.writerow([r['display']] + ['' if x.get(k) is None else x.get(k) for k in cols])
    with open(os.path.join(d, 'runs.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'run', 'from_ft', 'to_ft', 'slide_ft', 'rotate_ft', 'slide_share', 'slides', 'median_slide_ft', 'slide_rop_ft_h', 'rotate_rop_ft_h', 'rotated_courses', 'rotary_build_median_deg_100ft',
                     'rotary_walk_median_deg_100ft', 'slid_courses', 'dogleg_per_100ft_slid_median'])
        for r in all_.values():
            for x in r['runs']:
                m = lambda s_: '' if not s_ else s_['median']
                wr.writerow([r['display'], x['run'], x['from_ft'], x['to_ft'], x['slide_ft'], x['rotate_ft'], x['slide_share'], x['slides'], x['median_slide_ft'], x['slide_rop_ft_h'], x['rotate_rop_ft_h'],
                             x['rotated_courses'], m(x['rotary_build_deg_100ft']), m(x['rotary_walk_deg_100ft']), x['slid_courses'], m(x['dogleg_per_100ft_slid'])])
    ws.audit(actor, 'slide_sheet.report', {'wells': len(all_), 'slides': sum(r['well']['slides'] for r in all_.values())})
    return {'folder': d, 'files': files}


# ---------------------------------------------------------------------------------------------------------------
# the self-test: a hole whose every slide and every degree is known
# ---------------------------------------------------------------------------------------------------------------
def synthetic(yield_deg_100ft: float = 10.0, drop_deg_100ft: float = -0.3, seed: int = 3) -> dict:
    """A record and a survey made together. Run 1: rotated to 2,000 ft, vertical. Run 2 (after a trip): ten stands of
    95 ft from 2,000 ft, the first 30 ft of each slid at toolface 20 right - the hole builds by the yield over each
    slide - then twenty stands rotated, dropping at the rotary tendency; survey stations at each stand's two ends."""
    rop = 120.0                                                       # ft/h, a foot every 30 s
    t: List[float] = []; st: List[int] = []; bot: List[float] = []; bit: List[float] = []; rpm: List[float] = []; tfg: List[float] = []
    now = 0.0; depth = 0.0
    rng = np.random.default_rng(seed)

    def drill(to: float, slide: bool):
        nonlocal now, depth
        while depth < to - 1e-9:
            depth = min(to, depth + rop / 3600.0 * 5.0); now += 5.0
            t.append(now); st.append(1 if slide else 0); bot.append(depth); bit.append(depth); rpm.append(0.0 if slide else 80.0); tfg.append(20.0 + float(rng.normal(0.0, 6.0)) if slide else float('nan'))

    def idle(seconds: float, at: float, state: int = 9):
        nonlocal now
        for _ in range(int(seconds / 5.0)):
            now += 5.0; t.append(now); st.append(state); bot.append(depth); bit.append(at); rpm.append(0.0); tfg.append(float('nan'))
    stations = [{'md_ft': 0.0, 'inc': 0.0, 'azi': 0.0}]
    drill(2000.0, False); stations.append({'md_ft': 2000.0, 'inc': 0.0, 'azi': 0.0})
    idle(1800.0, 100.0, 6)                                              # out of the hole and back: the bit at 100 ft
    inc = 0.0; az = 90.0
    for k in range(10):
        top = 2000.0 + 95.0 * k
        idle(120.0, top - 2.0, 7)
        drill(top + 30.0, True); drill(top + 95.0, False)
        inc += yield_deg_100ft * 0.30
        stations.append({'md_ft': top + 95.0, 'inc': round(inc, 4), 'azi': az})
    for k in range(20):
        top = 2950.0 + 95.0 * k
        idle(120.0, top - 2.0, 7)
        drill(top + 95.0, False)
        inc += drop_deg_100ft * 0.95; az += 0.5 * 0.95
        stations.append({'md_ft': top + 95.0, 'inc': round(inc, 4), 'azi': round(az, 4)})
    return {'t': np.array(t), 'state': np.array(st), 'bottom': np.array(bot), 'bit': np.array(bit), 'chans': {'Rotary RPM': np.array(rpm)}, 'toolface': {'gravity': np.array(tfg)}, 'stations': stations,
            'yield': yield_deg_100ft, 'drop': drop_deg_100ft}


def write_edr(s: dict, path: str, t0: Optional[datetime] = None) -> str:
    """The made record as a drilling recorder's export, with the toolface under the name a recorder gives it."""
    t0 = t0 or datetime(2026, 5, 1, 6, 0, 0)
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)', 'Hook Load (klbs)',
           'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'On Bottom (unitless)', 'Gravity Toolface (degrees)']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for i in range(len(s['t'])):
            ts = t0 + timedelta(seconds=float(s['t'][i]))
            drl = int(s['state'][i]) in (0, 1); slips = int(s['state'][i]) == 7
            tf = s['toolface']['gravity'][i]
            w.writerow([ts.strftime('%Y/%m/%d'), ts.strftime('%H:%M:%S'), f"{s['bottom'][i]:.2f}", f"{s['bit'][i]:.2f}", f"{120.0 if drl else 0.0:.1f}", f"{25.0 if drl else 0.0:.1f}", f"{s['chans']['Rotary RPM'][i]:.1f}",
                        f"{2800.0 if drl else 0.0:.1f}", f"{45.0 if slips else 190.0:.1f}", f"{350.0 if drl else 0.0:.1f}", f"{600.0 if drl else 0.0:.1f}", f"{(40.0 + (i % 12)) if not slips else 5.0:.2f}",
                        str(int(drl)), ('' if tf != tf else f'{tf % 360.0:.1f}')])
    return path


def selftest() -> dict:
    checks = []
    s = synthetic()
    r = sheet(s['t'], s['state'], s['bottom'], s['bit'], s['chans'], s['stations'], 0, 1, s['toolface'], datetime(2026, 5, 1))
    sl = [x for x in r['rows'] if x['kind'] == 'SLIDE']
    checks.append({'check': 'ten slides of 30 ft, each from the top of its stand, at 120 ft/h; the rotated stretches between them; two runs of an assembly, the trip between them',
                   'value': [len(sl), [x['footage_ft'] for x in sl[:3]], sl[0]['from_ft'], sl[0]['rop_ft_h'], len(r['runs']), [x['run'] for x in r['rows'][:3]]],
                   'pass': len(sl) == 10 and all(abs(x['footage_ft'] - 30.0) < 0.2 for x in sl) and abs(sl[0]['from_ft'] - 2000.0) < 0.2 and abs(sl[3]['from_ft'] - 2285.0) < 0.2 and all(abs(x['rop_ft_h'] - 120.0) < 1.0 for x in sl)
                           and len(r['runs']) == 2 and r['rows'][0]['run'] == 1 and r['rows'][0]['kind'] == 'ROTATE' and abs(r['rows'][0]['footage_ft'] - 2000.0) < 0.5 and sl[0]['run'] == 2})
    checks.append({'check': 'the toolface of each slide: 20 right of high side, held within some six degrees; the words for it', 'value': [sl[0]['toolface_deg'], sl[0]['toolface_spread_deg'], sl[0]['toolface']],
                   'pass': all(abs(x['toolface_deg'] - 20.0) < 3.0 and 3.0 < x['toolface_spread_deg'] < 9.0 and x['toolface'].endswith('right') and x['toolface_gravity'] for x in sl)
                           and toolface_words(350.0, True) == '10 left' and toolface_words(0.2, True) == 'high side' and toolface_words(180.0, True) == 'low side' and toolface_words(275.0, False) == '275 magnetic'})
    cs = r['courses']; slid = [c for c in cs if c['kind'] == 'SLID']; rot = [c for c in cs if c['kind'] == 'ROTATED']
    w2 = r['runs'][1]
    checks.append({'check': 'the courses: ten with 30 ft slid of 95, each a dogleg of 3 degrees - 10 degrees per 100 ft slid; twenty-one rotated, the twenty in the build section dropping 0.3 and walking 0.5 degrees per 100 ft',
                   'value': [len(slid), len(rot), slid[0]['slide_ft'], slid[0]['dogleg_per_100ft_slid'], w2['dogleg_per_100ft_slid'], w2['rotary_build_deg_100ft'], w2['rotary_walk_deg_100ft']],
                   'pass': len(slid) == 10 and len(rot) == 21 and all(abs(c['slide_ft'] - 30.0) < 0.3 and abs(c['slide_share'] - 30.0 / 95.0) < 0.004 and abs(c['dogleg_per_100ft_slid'] - 10.0) < 0.12 for c in slid)
                           and abs(w2['dogleg_per_100ft_slid']['median'] - 10.0) < 0.12 and w2['dogleg_per_100ft_slid']['n'] == 10 and abs(w2['rotary_build_deg_100ft']['median'] + 0.3) < 0.01
                           and w2['rotary_build_deg_100ft']['n'] == 20 and abs(w2['rotary_walk_deg_100ft']['median'] - 0.5) < 0.01 and r['runs'][0]['rotary_build_deg_100ft'] is None
                           and abs(slid[0]['toolface_deg'] - 20.0) < 3.0})
    w = r['well']
    checks.append({'check': 'the well: 300 ft slid of 4,850 ft drilled, 6.2%; the same rate sliding and rotating, as it was made', 'value': [w['slide_ft'], w['rotate_ft'], w['slide_share'], w['slide_rop_ft_h'], w['rotate_rop_ft_h']],
                   'pass': abs(w['slide_ft'] - 300.0) < 1.0 and abs(w['rotate_ft'] - 4550.0) < 2.0 and abs(w['slide_share'] - 300.0 / 4850.0) < 0.001 and abs(w['slide_rop_ft_h'] - 120.0) < 1.0 and abs(w['rotate_rop_ft_h'] - 120.0) < 1.0
                           and w['slides'] == 10 and w['median_slide_ft'] == 30.0})
    a = attitude_at(2047.5, s['stations']); b = attitude_at(99999.0, s['stations'])
    row = sl[0]
    checks.append({'check': 'the attitude carried evenly between two stations; none beyond the last; a slide\'s build from the survey at its two ends; the dogleg of a turn through north',
                   'value': [a, b, row['inc_from'], row['inc_to'], round(dogleg_deg(10.0, 350.0, 10.0, 10.0), 3), round(_turn(350.0, 10.0), 1)],
                   'pass': abs(a[0] - 1.5) < 1e-9 and b is None and row['inc_from'] == 0.0 and abs(row['inc_to'] - 3.0 * 30.0 / 95.0) < 0.01 and abs(dogleg_deg(10.0, 350.0, 10.0, 10.0) - 3.4558) < 0.001 and _turn(350.0, 10.0) == 20.0})
    # a flicker of rotation in a slide, a record without rotary speed or survey, an empty record
    s2 = synthetic()
    k = int(np.flatnonzero(s2['state'] == 1)[40]); s2['state'][k:k + 3] = 0
    r2 = sheet(s2['t'], s2['state'], s2['bottom'], s2['bit'], s2['chans'], s2['stations'], 0, 1, s2['toolface'])
    r3 = sheet(s['t'], s['state'], s['bottom'], s['bit'], {}, [], 0, 1, None)
    r4 = sheet(np.zeros(0), np.zeros(0, dtype=int), np.zeros(0), np.zeros(0), {}, [], 0, 1, None)
    f2 = [x for x in r2['rows'] if x['kind'] == 'SLIDE']
    checks.append({'check': 'three samples of rotation inside a slide are folded into it, and said; without a survey the stretches stand and no course or tendency is given; an empty record gives an empty sheet',
                   'value': [len(f2), max(x['folded_ft'] for x in f2), len(r3['rows']), r3['courses'], r3['toolface'], r4['rows']],
                   'pass': len(f2) == 10 and 0.2 < max(x['folded_ft'] for x in f2) < MIN_FT and abs(sum(x['footage_ft'] for x in f2) - 300.0) < 1.0 and len(r3['rows']) == len(r['rows']) and r3['courses'] == []
                           and r3['toolface'] is None and 'inc_from' not in r3['rows'][0] and r4['rows'] == [] and r4['well']['slide_share'] is None})
    m, sp = circular(np.array([350.0, 10.0, 0.0])); m2, sp2 = circular(np.array([float('nan')]))
    checks.append({'check': 'the mean of 350, 10 and 0 degrees is north, not 120; of nothing, nothing', 'value': [round(m, 3), round(sp, 2), m2], 'pass': (m < 1e-6 or m > 360.0 - 1e-6) and 5.0 < sp < 10.0 and m2 is None and sp2 is None})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
