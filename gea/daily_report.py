# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""daily_report - the daily drilling report, written from the drilling record.

A rig sends one sheet every morning: where the hole was at the start of the day and at its end, what the rig did
hour by hour, how it drilled, what it tripped, what went wrong. The crew writes it from memory and the tour sheets,
to the quarter hour. The drilling recorder has the same day second by second, and the pages of this program each
read one part of it. This module puts those parts on one sheet for one well and one report day:

- the depths: the hole at the start and the end of the day, the footage, the bit at the end;
- the time log, to the quarter hour as a daily report is kept: each quarter hour named by what the rig mostly did
  in it (the rig-time page's own states, a connection's slips counted with the drilling or the tripping around it),
  like quarters joined into lines with their depths, and the hours with no record shown as that, so the lines
  always come to 24 hours;
- the hours by rig state, the connections and their weight-to-weight time, the time lost against the pad's targets;
- the drilling: feet rotated and slid, hours on bottom, the rate while on bottom, and the weight, the rotary speed,
  the torque, the flow and the pressures while drilling - the middle value and the range most of the day sat in;
- the mud the record carries: weight in and out and the active pit volume at the start and the end, where the
  recorder has them;
- the trips, with the trip sheet's reading of each where there is one; the drilling alerts and the kick and loss
  events that began in the day; the survey stations taken in the day's hole; the drill-string runs in the hole;
- and what the crew adds: remarks for the day and the plan for the next, typed by an operator, kept with a name.

The report day starts at the hour the contract's terms give (the Invoice check tab; 06:00 until it is declared) and
is named by the date it starts on - or the date it ends on, where the terms say the contractor dates his reports
so - and every sheet says the hours it covers. The clock is the drilling recorder's own.

What it will not call a measurement: anything a daily report holds that the recorder does not - the crew, the cost,
the mud check, the bit's grading, the weather, the reason for anything. Those are the crew's to write, and the
remarks are where they go."""
from __future__ import annotations

import csv
import html as _html
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import rig_state as RS

QUARTER_S = 900.0
OPS = ('DRILLING', 'TRIPPING IN', 'TRIPPING OUT', 'REAMING / WASHING', 'CIRCULATING', 'IN SLIPS', 'OFF THE HOOK', 'STATIONARY', 'NO RECORD')
O_DRILL, O_TIN, O_TOUT, O_REAM, O_CIRC, O_SLIPS, O_OFF, O_STAT, O_NOREC = range(9)
# the rig-time page's state of a sample -> the operation a daily report would write
_OP_OF = {RS.ROT: O_DRILL, RS.SLIDE: O_DRILL, RS.REAM: O_REAM, RS.WASH: O_REAM, RS.CIRC: O_CIRC, RS.TIN: O_TIN, RS.TOUT: O_TOUT, RS.SLIPS: O_SLIPS, RS.OFFHOOK: O_OFF, RS.STAT: O_STAT}
JOINED = (O_REAM, O_CIRC, O_SLIPS, O_STAT)
LEAD_MIN_S = 120.0              # a quarter hour is named by an operation other than the slips when that operation has two minutes of it
PARAMS = (('wob', 'Weight on bit', ('weight on bit', 'wob')), ('rpm', 'Rotary speed', ('rotary rpm', 'rpm')), ('torque', 'Torque', ('convertible torque', 'rotary torque', 'surface torque', 'torque')),
          ('flow', 'Flow in', ('total pump output', 'flow in', 'pump output')), ('spp', 'Standpipe pressure', ('standpipe pressure', 'spp')),
          ('diff', 'Differential pressure', ('differential pressure', 'diff press')), ('rop', 'Rate of penetration', ('rate of penetration', 'rop')))
MUD = (('mw_in', 'Mud weight in', ('mud weight in', 'mud density in', 'mw in', 'density in')), ('mw_out', 'Mud weight out', ('mud weight out', 'mud density out', 'mw out', 'density out')),
       ('pit', 'Active pit volume', ('total mud volume', 'pit volume active', 'active pit volume', 'pit total', 'pvt total mud volume', 'total active volume')))
REMARK_MAX = 4000
NOT_IN_THE_RECORD = [
    "the crew, its hours and who was on tour",
    "the cost of the day",
    "the mud check: viscosity, solids, chemistry - the recorder carries a density and a volume at most",
    "the bit's make and grading, and the assembly, unless a drill-string run is declared on the Drill string tab",
    "the weather, the safety meeting, the visitors",
    "the reason for anything: a quarter hour named CIRCULATING was pumps on with the bit still, and why is the crew's to say - in the remarks",
]
NOT_A_MEASUREMENT = [
    "the time log's lines: each quarter hour is named by what the rig mostly did in it, so a ten-minute circulation inside an hour of drilling is in the hours by rig state and not on a line of its own",
    "the rig states themselves: they are the rig-time page's rules on hook load, block, bit, pumps and rotary, with that page's cautions - a recorder whose block weight is off moves every slips time",
    "the footage: it is the rises in the recorder's hole depth that a bit can make in a second, summed over the day - a depth the driller corrected is left out of it, and so is hole made where there is no record; the sheet says when the day's depths and its footage differ",
    "the alerts: each is a place in the record to look at, as its own page says, and what it was is for the crew and the daily report they sign",
] + ["not in the record, and so not on the sheet unless typed into the remarks: " + '; '.join(NOT_IN_THE_RECORD)]

_LOCK = threading.Lock()


class DailyReportError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _r(v, n: int = 1):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


# -- the days of a record -------------------------------------------------------------------------------------------------

def day_base(t0: datetime, day_start_h: float) -> datetime:
    """The start of the report day the record begins in: the day's hour on the record's first date, or the day
    before's when the record begins earlier in the morning than the day does."""
    base = datetime(t0.year, t0.month, t0.day) + timedelta(hours=float(day_start_h))
    return base if base <= t0 else base - timedelta(days=1)


def _bounds(t0: datetime, base: datetime, k: int) -> Tuple[float, float]:
    """Report day k as seconds on the record's own axis."""
    a = (base + timedelta(days=int(k)) - t0).total_seconds()
    return a, a + 86400.0


def _hhmm(dt: datetime) -> str:
    return dt.strftime('%H:%M')


# -- one day's sheet, from arrays -------------------------------------------------------------------------------------------

def _stat(x: np.ndarray) -> Optional[dict]:
    x = x[np.isfinite(x)]
    if len(x) < 30:
        return None
    return {'median': _r(float(np.median(x)), 1), 'p10': _r(float(np.percentile(x, 10)), 1), 'p90': _r(float(np.percentile(x, 90)), 1), 'seconds': int(len(x))}


def day_slices(t: np.ndarray, t0: datetime, day_start_h: float) -> Tuple[datetime, List[Tuple[int, int, int]]]:
    """The report days that hold samples, each as (its number from the record's first day, its first sample, its last):
    the record's time runs forward, so a day is one stretch of it."""
    base = day_base(t0, day_start_h)
    off = (t0 - base).total_seconds()
    k0 = int(math.floor((float(t[0]) + off) / 86400.0)); k1 = int(math.floor((float(t[-1]) + off) / 86400.0))
    edges = np.array([k * 86400.0 - off for k in range(k0, k1 + 2)], dtype=np.float64)
    ix = np.searchsorted(t, edges, side='left')
    return base, [(k0 + j, int(ix[j]), int(ix[j + 1]) - 1) for j in range(k1 - k0 + 1) if ix[j + 1] > ix[j]]


def time_log(t: np.ndarray, st: np.ndarray, dtv: np.ndarray, hole: Optional[np.ndarray], bit: Optional[np.ndarray], a: float, t0: datetime,
             conn_t: Optional[np.ndarray] = None) -> List[dict]:
    """The 96 quarter hours from second `a` of the record's axis, each named, and like neighbours joined. The arrays
    are the day's own samples."""
    nq = 96; ns = len(RS.STATES) - 1
    q = np.clip(((t - a) // QUARTER_S).astype(np.int64), 0, nq - 1)
    sec = np.zeros((nq, ns))
    np.add.at(sec, (q, np.clip(st.astype(np.int64), 0, ns - 1)), dtv)
    sec = np.minimum(sec, QUARTER_S)
    have = sec.sum(axis=1)
    over = have > QUARTER_S
    sec[over] *= (QUARTER_S / have[over])[:, None]
    have = np.minimum(have, QUARTER_S)
    ops = np.zeros((nq, len(OPS)))
    for s_, o_ in _OP_OF.items():
        ops[:, o_] += sec[:, s_]
    ops[:, O_NOREC] = QUARTER_S - have
    name = np.zeros(nq, dtype=np.int64)
    for i in range(nq):
        if ops[i, O_NOREC] > QUARTER_S / 2.0:
            name[i] = O_NOREC; continue
        lead = ops[i].copy(); lead[O_SLIPS] = 0.0; lead[O_NOREC] = 0.0
        j = int(np.argmax(lead))
        name[i] = j if lead[j] >= LEAD_MIN_S or lead[j] >= ops[i, O_SLIPS] else O_SLIPS
    # one quarter hour of circulating, reaming, standing or slips between two quarters of the same drilling or tripping is that
    # drilling or tripping stopping to work the pipe: it is joined to them, and its minutes stay in the line's own hours by state
    for i in range(1, nq - 1):
        if name[i] in JOINED and name[i - 1] == name[i + 1] and name[i - 1] in (O_DRILL, O_TIN, O_TOUT) and (i < 2 or name[i - 2] not in JOINED or name[i - 2] == name[i - 1]):
            name[i] = name[i - 1]
    first = np.full(nq, -1, dtype=np.int64); last = np.full(nq, -1, dtype=np.int64)
    if len(q):
        uq, fi = np.unique(q, return_index=True)
        first[uq] = fi
        uq2, li = np.unique(q[::-1], return_index=True)
        last[uq2] = len(q) - 1 - li
    cq = np.zeros(nq, dtype=np.int64)
    if conn_t is not None and len(conn_t):
        cm = (conn_t >= a) & (conn_t < a + 86400.0)
        np.add.at(cq, np.clip(((conn_t[cm] - a) // QUARTER_S).astype(np.int64), 0, nq - 1), 1)
    lines: List[dict] = []
    i = 0
    while i < nq:
        j = i
        while j + 1 < nq and name[j + 1] == name[i]:
            j += 1
        f_ = next((first[k] for k in range(i, j + 1) if first[k] >= 0), -1); l_ = next((last[k] for k in range(j, i - 1, -1) if last[k] >= 0), -1)
        d = lambda arr, k: (_r(float(arr[k]), 0) if arr is not None and k >= 0 and math.isfinite(float(arr[k])) else None)
        hours = {RS.STATES[s_]: _r(float(sec[i:j + 1, s_].sum() / 3600.0), 2) for s_ in range(ns) if sec[i:j + 1, s_].sum() >= 36.0}
        line = {'from': _hhmm(t0 + timedelta(seconds=a + i * QUARTER_S)), 'to': _hhmm(t0 + timedelta(seconds=a + (j + 1) * QUARTER_S)), 'hours': round((j - i + 1) * 0.25, 2),
                'operation': OPS[int(name[i])], 'hole_from_ft': d(hole, f_), 'hole_to_ft': d(hole, l_), 'bit_from_ft': d(bit, f_), 'bit_to_ft': d(bit, l_),
                'states_h': hours, 'no_record_h': _r(float(ops[i:j + 1, O_NOREC].sum() / 3600.0), 2), 'connections': int(cq[i:j + 1].sum())}
        line['words'] = _words(line)
        lines.append(line)
        i = j + 1
    return lines


def _ft(v) -> str:
    return '-' if v is None else f'{v:,.0f}'


def _words(l: dict) -> str:
    op = l['operation']; s = l['states_h']
    part = lambda k, w: (f"{s[k]:g} h {w}" if s.get(k) else None)
    if op == 'DRILLING':
        made = (l['hole_to_ft'] - l['hole_from_ft']) if None not in (l['hole_from_ft'], l['hole_to_ft']) else None
        other = round(sum(s.get(k, 0.0) for k in ('CIRCULATING', 'REAMING', 'WASHING', 'STATIONARY')), 2)
        bits = [x for x in (part('ROTARY DRILLING', 'rotating'), part('SLIDE DRILLING', 'sliding'), part('IN SLIPS', 'in the slips'), (f'{other:g} h circulating and working the pipe' if other >= 0.05 else None)) if x]
        return (f"Drilled from {_ft(l['hole_from_ft'])} to {_ft(l['hole_to_ft'])} ft" + (f" ({made:,.0f} ft)" if made is not None else '') + (': ' + ', '.join(bits) if bits else '')
                + (f"; {l['connections']} connection{'s' if l['connections'] != 1 else ''}" if l['connections'] else ''))
    if op in ('TRIPPING IN', 'TRIPPING OUT'):
        return f"Tripped {'in' if op.endswith('IN') else 'out'}, the bit from {_ft(l['bit_from_ft'])} to {_ft(l['bit_to_ft'])} ft" + (f"; {s['IN SLIPS']:g} h of it in the slips" if s.get('IN SLIPS') else '')
    if op == 'REAMING / WASHING':
        return f"Reamed and washed, the bit between {_ft(l['bit_from_ft'])} and {_ft(l['bit_to_ft'])} ft"
    if op == 'CIRCULATING':
        return f"Circulated, the bit at {_ft(l['bit_to_ft'])} ft"
    if op == 'IN SLIPS':
        return f"In the slips, the bit at {_ft(l['bit_to_ft'])} ft"
    if op == 'OFF THE HOOK':
        return "Off the hook: the string hung off, or no string on the hook"
    if op == 'STATIONARY':
        return f"Stationary, the pumps off, the bit at {_ft(l['bit_to_ft'])} ft"
    return "No record"


def carried(hole: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """The hole depth with its last reading carried over the samples that have none."""
    if hole is None:
        return None
    hb = np.asarray(hole, dtype=np.float64)
    ok = np.isfinite(hb)
    if not ok.any():
        return None
    ix = np.where(ok, np.arange(len(hb)), -1)
    np.maximum.accumulate(ix, out=ix)
    return np.where(ix >= 0, hb[np.maximum(ix, 0)], np.nan)


def new_hole(hb: np.ndarray) -> np.ndarray:
    """The hole made at each sample: the depth's rise since the sample before, where it is a rise a bit can make in
    a sample. A step of 50 ft or more is a correction of the depth, or hole made where there is no record, and a
    fall is a correction, a plug back or a sidetrack - neither is footage drilled in the record."""
    db = np.diff(hb, prepend=hb[0])
    return np.where(np.isfinite(db) & (db > 0) & (db < 50.0), db, 0.0)


def sheet_from_arrays(t: np.ndarray, t0: datetime, st: np.ndarray, dtv: np.ndarray, hole: Optional[np.ndarray], bit: Optional[np.ndarray], connections: List[dict], trips: List[dict],
                      day_start_h: float, date: str, channels: Optional[Dict[str, Tuple[str, str, np.ndarray]]] = None, dated: str = 'start') -> dict:
    """One report day's sheet from one well's arrays (t in seconds on the record's axis from t0, the recorder's clock,
    running forward; st and dtv as the rig-time page makes them; `hole` the hole depth with its spikes taken out).
    `channels` is key -> (name, unit, values) for the drilling and mud readings the record carries. Only the day's
    own samples are read from any of them."""
    base, sl = day_slices(t, t0, day_start_h)
    shift = 1 if dated == 'end' else 0                       # a report dated by the day it ends on carries the next date
    keys = {(base + timedelta(days=k_ + shift)).date().isoformat(): (k_, a_, b_) for k_, a_, b_ in sl}
    if date not in keys:
        some = sorted(keys)
        raise DailyReportError(f"the record has no sample in the report day {date}; its days run from {some[0]} to {some[-1]}" if some else 'the record has no sample')
    k, i0, i1 = keys[date]
    a, b = _bounds(t0, base, k)
    lo = max(i0 - 1, 0)
    t_d = np.asarray(t[i0:i1 + 1], dtype=np.float64); st = np.asarray(st[i0:i1 + 1]); dtv = np.asarray(dtv[i0:i1 + 1], dtype=np.float64)
    ns = len(RS.STATES) - 1
    hours = {RS.STATES[s_]: _r(float(dtv[st == s_].sum() / 3600.0), 2) for s_ in range(ns)}
    rec_h = min(float(dtv.sum() / 3600.0), 24.0)
    hb = carried(hole[lo:i1 + 1]) if hole is not None else None      # the day's hole depth, and the sample before the day's first
    h_from = h_to = made = None; hmax = db = None
    if hb is not None:
        db_all = new_hole(hb)
        hmax = hb[i0 - lo:]; db = db_all[i0 - lo:]
        fin = np.isfinite(hmax)
        h_from = float(hb[0]) if lo < i0 and math.isfinite(float(hb[0])) else (float(hmax[fin][0]) if fin.any() else None)
        h_to = float(hmax[fin][-1]) if fin.any() else None
        made = float(db.sum())
    bit_d = np.asarray(bit[i0:i1 + 1], dtype=np.float64) if bit is not None else None
    foot = {'rotary_ft': None, 'slide_ft': None}
    if hmax is not None:
        foot = {'rotary_ft': _r(float(db[st == RS.ROT].sum()), 1), 'slide_ft': _r(float(db[st == RS.SLIDE].sum()), 1)}
    on_h = float(dtv[(st == RS.ROT) | (st == RS.SLIDE)].sum() / 3600.0)
    moved = (h_to - h_from) if None not in (h_from, h_to) else None
    conns = [c for c in connections if a <= c['t_off'] < b]
    w2w = [c['w2w_min'] for c in conns if c.get('w2w_min') is not None]
    tr = [{kk: x.get(kk) for kk in ('direction', 'start', 'end', 'from_ft', 'to_ft', 'feet', 'hours', 'elapsed_hours', 'breaks_hours', 'speed_ft_h', 'connections', 't_start')}
          for x in trips if a <= x['t_start'] < b]
    drilling = (st == RS.ROT) | (st == RS.SLIDE)
    params = []; mud = []
    for key, label, _n in PARAMS:
        if channels and key in channels:
            name, unit, v = channels[key]
            s_ = _stat(np.asarray(v[i0:i1 + 1], dtype=np.float64)[drilling])
            if s_:
                params.append({'key': key, 'what': label, 'channel': name, 'unit': unit, **s_})
    for key, label, _n in MUD:
        if channels and key in channels:
            name, unit, v = channels[key]
            seg = np.asarray(v[i0:i1 + 1], dtype=np.float64); seg = seg[np.isfinite(seg)]
            if len(seg) >= 30:
                head = float(np.median(seg[:min(600, len(seg))])); tail = float(np.median(seg[-min(600, len(seg)):]))
                mud.append({'key': key, 'what': label, 'channel': name, 'unit': unit, 'start': _r(head, 2 if key != 'pit' else 1), 'end': _r(tail, 2 if key != 'pit' else 1),
                            'low': _r(float(np.percentile(seg, 1)), 2 if key != 'pit' else 1), 'high': _r(float(np.percentile(seg, 99)), 2 if key != 'pit' else 1)})
    log = time_log(t_d, st, dtv, hmax, bit_d, a, t0, np.array([c['t_off'] for c in connections], dtype=np.float64))
    day0 = t0 + timedelta(seconds=a)
    k_first = sl[0][0]
    return {'date': date, 'dated_by': 'the day it ends on' if shift else 'the day it starts on', 'report_no': int(k - k_first) + 1, 'days_in_record': int(sl[-1][0] - k_first) + 1, 'day_starts': f'{int(day_start_h):02d}:{int(round((day_start_h % 1) * 60)):02d}',
            'covers': {'from': day0.isoformat(timespec='minutes'), 'to': (day0 + timedelta(days=1)).isoformat(timespec='minutes')},
            'record': {'first': (t0 + timedelta(seconds=float(t_d[0]))).isoformat(timespec='seconds'), 'last': (t0 + timedelta(seconds=float(t_d[-1]))).isoformat(timespec='seconds'),
                       'hours': _r(rec_h, 2), 'no_record_h': _r(max(24.0 - rec_h, 0.0), 2), 'whole_day': bool(rec_h >= 23.9), 'ends_in_this_day': bool(i1 == len(t) - 1)},
            'depth': {'hole_from_ft': _r(h_from, 0), 'hole_to_ft': _r(h_to, 0), 'footage_ft': _r(made, 0), 'depth_moved_ft': _r(moved, 0),
                      'apart': bool(made is not None and moved is not None and abs(moved - made) > max(5.0, 0.02 * abs(made))), 'bit_at_end_ft': _r(float(bit_d[-1]), 0) if bit_d is not None and math.isfinite(float(bit_d[-1])) else None},
            'hours': hours,
            'drilling': {**foot, 'on_bottom_h': _r(on_h, 2), 'rop_on_bottom_ft_h': _r(((foot['rotary_ft'] or 0.0) + (foot['slide_ft'] or 0.0)) / on_h, 1) if on_h > 0.02 and foot['rotary_ft'] is not None else None,
                         'slide_share_pct': _r(100.0 * (foot['slide_ft'] or 0.0) / ((foot['rotary_ft'] or 0.0) + (foot['slide_ft'] or 0.0)), 1) if foot['rotary_ft'] is not None and (foot['rotary_ft'] or 0.0) + (foot['slide_ft'] or 0.0) > 0 else None,
                         'ft_per_day': _r(made * 24.0 / rec_h, 0) if made is not None and rec_h > 1.0 else None},
            'connections': {'n': len(conns), 'median_w2w_min': _r(float(np.median(w2w)), 2) if w2w else None, 'slowest_w2w_min': _r(max(w2w), 1) if w2w else None},
            'trips': tr, 'parameters': params, 'mud': mud, 'time_log': log, '_bounds': (a, b)}


# -- the workspace ----------------------------------------------------------------------------------------------------------

def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'daily_report.json')


def _load(ws) -> dict:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remarks(ws, well_id: str, date: str) -> Optional[dict]:
    return ((_load(ws).get('remarks') or {}).get(well_id) or {}).get(date)


def set_remarks(ws, actor: str, well_id: str, date: str, text: str, plan: str = '') -> dict:
    """The crew's words for a day: what happened that the record cannot say, and the plan for the next day."""
    if well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except (TypeError, ValueError):
        raise DailyReportError('the report day as a date: 2026-09-01')
    text = str(text or '').strip(); plan = str(plan or '').strip()
    if len(text) > REMARK_MAX or len(plan) > REMARK_MAX:
        raise DailyReportError(f'remarks of {REMARK_MAX:,} characters at most')
    with _LOCK:
        d = _load(ws)
        r = d.setdefault('remarks', {}).setdefault(well_id, {})
        if not text and not plan:
            had = r.pop(date, None)
            rec = None
        else:
            had = r.get(date)
            rec = {'text': text, 'plan': plan, 'by': actor, 'utc': _utc()}
            r[date] = rec
        tmp = _path(ws) + '.part'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, _path(ws))
    ws.audit(actor, 'daily_report.remarks' if rec else 'daily_report.remarks_removed', {'well': well_id, 'date': date, 'characters': len(text) + len(plan), 'replaced': bool(had)})
    return {'well': well_id, 'date': date, 'remarks': rec}


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


def _channels(c) -> Dict[str, Tuple[str, str, np.ndarray]]:
    out = {}
    for key, _label, names in PARAMS + MUD:
        n = _find(c, names)
        if n:
            out[key] = (n, c.unit(n) or '', c.channel(n))                # the cache's own array: the sheet reads the day's samples of it and no more
    return out


def _base(ws, well_id: str):
    from . import drill_string as DS
    r = RS.well(ws, well_id)
    c = DS._cache(ws, well_id, build=True)
    t0 = datetime.fromisoformat(r['t0'])
    hole_n = _find(c, ('hole depth',)); bit_n = _find(c, ('bit depth',))
    bottom = c.extra('bottom')                               # the cache's hole depth with its spikes taken out: what the rig-time page reads
    hole = bottom if bottom is not None else (c.channel(hole_n) if hole_n else None)
    bit = c.channel(bit_n) if bit_n else None
    return r, c, t0, hole, bit


def days(ws, well_id: str) -> List[dict]:
    """Every report day of a well's record that holds samples, a row each: what the list on the page shows."""
    r, c, t0, hole, bit = _base(ws, well_id)
    h0 = RS._day_start(ws)
    t, st, dtv = r['_t'], r['_state'], r['_dtv']
    base, sl = day_slices(t, t0, h0)
    shift = 1 if _dated(ws) == 'end' else 0
    hb = carried(hole) if hole is not None else None
    db = new_hole(hb) if hb is not None else None
    ct = np.array([x['t_off'] for x in r['connections']], dtype=np.float64); tt = np.array([x['t_start'] for x in r['trips']], dtype=np.float64)
    noted = (_load(ws).get('remarks') or {}).get(well_id) or {}
    out = []
    for k, i0, i1 in sl:
        s_ = st[i0:i1 + 1]; d_ = dtv[i0:i1 + 1]
        hf = ht = made = None
        if hb is not None:
            seg = hb[i0:i1 + 1]; fin = np.isfinite(seg)
            hf = float(hb[i0 - 1]) if i0 > 0 and math.isfinite(float(hb[i0 - 1])) else (float(seg[fin][0]) if fin.any() else None)
            ht = float(seg[fin][-1]) if fin.any() else None
            made = float(db[i0:i1 + 1].sum()) - (float(db[i0]) if i0 == 0 else 0.0)
        hrs = lambda *ss: round(float(sum(d_[s_ == x].sum() for x in ss) / 3600.0), 2)
        a, b = _bounds(t0, base, int(k))
        date = (base + timedelta(days=int(k) + shift)).date().isoformat()
        out.append({'date': date, 'report_no': int(k - sl[0][0]) + 1, 'record_h': round(min(float(d_.sum() / 3600.0), 24.0), 2),
                    'hole_from_ft': _r(hf, 0), 'hole_to_ft': _r(ht, 0), 'footage_ft': _r(made, 0),
                    'drilling_h': hrs(RS.ROT, RS.SLIDE), 'tripping_h': hrs(RS.TIN, RS.TOUT), 'slips_h': hrs(RS.SLIPS),
                    'connections': int(((ct >= a) & (ct < b)).sum()), 'trips': int(((tt >= a) & (tt < b)).sum()), 'remarks': date in noted})
    return out


def _say(describe, e: dict) -> str:
    try:
        return str(describe(e))
    except Exception:                                        # an event its own page words differently: the sheet lists it without the words
        return ''


def _alerts(ws, well_id: str, a: float, b: float) -> List[dict]:
    out: List[dict] = []
    try:
        from . import dysfunction as DY
        d = DY.well(ws, well_id)
        for kind in DY.KINDS:
            for e in (d.get(kind) or {}).get('events') or []:
                if a <= float(e.get('t', -1)) < b:
                    out.append({'page': 'Drilling alerts', 'kind': kind, 'time': e.get('time'), 'depth_ft': e.get('depth_ft'), 'words': _say(DY.describe, e), 't': float(e['t'])})
    except Exception:                                        # the alerts are an extra on the sheet: a record they cannot read does not stop the day's report
        pass
    try:
        from . import kick as KD
        kd = KD.well(ws, well_id)
        t0 = datetime.fromisoformat(RS.well(ws, well_id)['t0'])
        for e in kd.get('flowbacks') or []:
            if e.get('status') == 'ABOVE' and a <= float(e.get('t', -1)) < b:
                out.append({'page': 'Kick & loss', 'kind': 'flowback above its fingerprint', 'time': (t0 + timedelta(seconds=float(e['t']))).isoformat(timespec='seconds'),
                            'depth_ft': e.get('depth_ft'), 'words': _say(KD.describe, e), 't': float(e['t'])})
        for e in (kd.get('circulating') or {}).get('events') or []:
            if e.get('kind') != 'TRANSFER' and a <= float(e.get('t', -1)) < b:
                out.append({'page': 'Kick & loss', 'kind': str(e.get('kind')).lower(), 'time': (t0 + timedelta(seconds=float(e['t']))).isoformat(timespec='seconds'),
                            'depth_ft': e.get('depth_ft'), 'words': _say(KD.describe, e), 't': float(e['t'])})
    except Exception:
        pass
    out.sort(key=lambda x: x['t'])
    for x in out:
        x.pop('t', None)
    return out


def _trip_sheet(ws, well_id: str, trips: List[dict]) -> None:
    try:
        from . import trip_sheet as TS
        ts = TS.well(ws, well_id)
        by = {round(float(x['t_start'])): x for x in ts.get('trips') or []}
        for x in trips:
            y = by.get(round(float(x['t_start'])))
            if y:
                x['trip_sheet'] = {'status': y.get('status'), 'measured_bbl': y.get('measured_bbl'), 'expected_bbl': y.get('expected_bbl'), 'read_from': y.get('source')}
    except Exception:
        pass


def _surveys(ws, well_id: str, hole_from: Optional[float], hole_to: Optional[float]) -> Optional[dict]:
    if hole_from is None or hole_to is None:
        return None
    try:
        from . import well3d as W3
        tr = W3.trajectory_of(ws, well_id)
    except Exception:
        return None
    if not tr or not tr.get('stations'):
        return None
    st = [s for s in tr['stations'] if s.get('md') is not None and hole_from < float(s['md']) <= hole_to]
    last = st[-1] if st else None
    return {'stations': len(st), 'last': ({'md_ft': _r(float(last['md']), 1), 'inclination_deg': _r(float(last['incl']), 2), 'azimuth_deg': _r(float(last['azi']), 2)} if last else None),
            'attached': len(tr['stations'])}


def _strings(ws, well_id: str, day0: datetime) -> List[dict]:
    try:
        from . import drill_string as DS
        out = []
        for r in DS.runs(ws, well_id):
            s = datetime.fromisoformat(r['start']) if r.get('start') else None; e = datetime.fromisoformat(r['end']) if r.get('end') else None
            if (s is None or s < day0 + timedelta(days=1)) and (e is None or e >= day0):
                out.append({'name': r.get('name'), 'start': r.get('start'), 'end': r.get('end'), 'components': len(r.get('components') or [])})
        return out
    except Exception:
        return []


_LOST: Dict[tuple, dict] = {}


def _lost(ws, well_id: str, r: dict) -> dict:
    """The rig-time page's time lost by report day. That page works its targets out over the whole pad each time it
    is asked; a sheet is asked for day after day, so the answer is kept for as long as the record and the declared
    targets are the ones they were."""
    try:
        st = os.stat(RS._path(ws)); sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    key = (os.path.abspath(ws.path), well_id, r.get('t0'), r.get('samples'), sig, tuple(sorted(RS._wells_with_record(ws))))
    with _LOCK:
        if key in _LOST:
            return _LOST[key]
    try:
        out = RS.lost_by_day(ws, well_id)
    except Exception:                                        # the time lost is an extra on the sheet
        out = {}
    with _LOCK:
        if len(_LOST) > 32:
            _LOST.clear()
        _LOST[key] = out
    return out


def sheet(ws, well_id: str, date: Optional[str] = None) -> dict:
    """One well's daily drilling report for one report day (the last day of the record when none is named)."""
    w = next((x for x in ws.wells() if x['id'] == well_id), None)
    if w is None:
        raise KeyError(f'no well {well_id}')
    r, c, t0, hole, bit = _base(ws, well_id)
    h0 = RS._day_start(ws)
    if date is None:
        base, sl = day_slices(r['_t'], t0, h0)
        date = (base + timedelta(days=int(sl[-1][0]) + (1 if _dated(ws) == 'end' else 0))).date().isoformat()
    s = sheet_from_arrays(r['_t'], t0, r['_state'], r['_dtv'], hole, bit, r['connections'], r['trips'], h0, date, _channels(c), _dated(ws))
    a, b = s.pop('_bounds')
    s['connections']['lost_h'] = (_lost(ws, well_id, r).get(s['covers']['from'][:10]) or {}).get('lost_h')   # the rig-time page names a day by its start
    _trip_sheet(ws, well_id, s['trips'])
    for x in s['trips']:
        x.pop('t_start', None)
    s['alerts'] = _alerts(ws, well_id, a, b)
    s['surveys'] = _surveys(ws, well_id, s['depth']['hole_from_ft'], s['depth']['hole_to_ft'])
    s['strings'] = _strings(ws, well_id, datetime.fromisoformat(s['covers']['from']))
    s['remarks'] = remarks(ws, well_id, date)
    s.update({'well': well_id, 'display': w.get('display') or well_id, 'site': ws.manifest.get('name') or '', 'clock': "the drilling recorder's own clock",
              'source': (c.meta.get('name') or '') and f"{c.meta.get('name')} ({c.meta.get('source_format')})", 'how': list(r.get('how') or [])})
    s['lines'] = _lines(s)
    return s


def _lines(s: dict) -> List[str]:
    d = s['depth']; h = s['hours']; dr = s['drilling']; cn = s['connections']; rec = s['record']
    out = [f"Report {s['report_no']} of {s['days_in_record']}, dated {s['date']} by {s['dated_by']}: the day from {s['covers']['from'].replace('T', ' ')} to {s['covers']['to'].replace('T', ' ')}, by {s['clock']}."]
    if d['footage_ft'] is not None:
        out.append(f"The hole depth read {d['hole_from_ft']:,.0f} ft at the start of the day and {d['hole_to_ft']:,.0f} ft at its end; {d['footage_ft']:,.0f} ft of new hole in the record" + (f", {dr['rotary_ft']:,.0f} ft rotated and {dr['slide_ft']:,.0f} ft slid" if dr['rotary_ft'] is not None else '')
                   + (f", at {dr['rop_on_bottom_ft_h']:,.0f} ft/h over {dr['on_bottom_h']:g} h on bottom." if dr['rop_on_bottom_ft_h'] is not None else '.'))
        if d['apart']:
            gap = d['depth_moved_ft'] - d['footage_ft']
            out.append(f"The depth moved {abs(gap):,.0f} ft {'more' if gap > 0 else 'less'} than the hole the record shows being made: " + ('a correction of the depth, or hole made where there is no record.' if gap > 0 else
                       'a correction of the depth, a plug back or a sidetrack.'))
    trip_h = (h['TRIPPING IN'] or 0) + (h['TRIPPING OUT'] or 0)
    out.append(f"The {rec['hours']:g} h of record: {dr['on_bottom_h']:g} h drilling, {h['IN SLIPS']:g} h in the slips, {trip_h:g} h tripping, {(h['REAMING'] or 0) + (h['WASHING'] or 0):g} h reaming and washing, "
               f"{h['CIRCULATING']:g} h circulating, {h['OFF THE HOOK']:g} h off the hook, {h['STATIONARY']:g} h stationary"
               + (f"; {rec['no_record_h']:g} h of the day have no record" + (' - the record ends in this day' if rec['ends_in_this_day'] else '') + '.' if rec['no_record_h'] >= 0.1 else '.'))
    if cn['n']:
        out.append(f"{cn['n']} drilling connection{'s' if cn['n'] != 1 else ''}, weight to weight {cn['median_w2w_min']:g} min at the middle and {cn['slowest_w2w_min']:g} at the slowest"
                   + (f"; {cn['lost_h']:g} h lost against the pad's targets." if cn.get('lost_h') else '.'))
    for x in s['trips']:
        ts = x.get('trip_sheet')
        out.append(f"A trip {'in' if x['direction'] == 'IN' else 'out'} from {x['from_ft']:,.0f} to {x['to_ft']:,.0f} ft, {x['hours']:.2f} h handling pipe at {x['speed_ft_h']:,.0f} ft/h"
                   + ('.' if not ts or not ts.get('status') else ('; the trip sheet could not read its mud.' if ts['status'] in ('NOT READ', 'NO BASIS', 'LITTLE READ', 'TRANSFER') else f"; the trip sheet reads it {str(ts['status']).lower()}.")))
    if s['alerts']:
        kinds: Dict[str, int] = {}
        for a_ in s['alerts']:
            kinds[a_['kind']] = kinds.get(a_['kind'], 0) + 1
        out.append(f"{len(s['alerts'])} place{'s' if len(s['alerts']) != 1 else ''} in the day's record to look at: " + ', '.join(f"{v} {k}" for k, v in kinds.items()) + '.')
    else:
        out.append("No drilling alert and no kick or loss event began in the day.")
    if s['surveys'] and s['surveys']['stations']:
        l_ = s['surveys']['last']
        out.append(f"{s['surveys']['stations']} survey station{'s' if s['surveys']['stations'] != 1 else ''} in the day's hole; the last at {l_['md_ft']:,.0f} ft, {l_['inclination_deg']:g} deg at {l_['azimuth_deg']:g}.")
    if not s['remarks']:
        out.append("No remarks are typed for this day: the crew, the cost, the mud check and the reasons are not in the record.")
    return out


def page(ws, well_id: Optional[str] = None, date: Optional[str] = None) -> dict:
    """The tab: the wells that have a drilling record, one well's days, one day's sheet. Reading it writes nothing."""
    ids = RS._wells_with_record(ws)
    wells = []
    for wid in ids:
        w = ws.well(wid)
        wells.append({'well': wid, 'display': w.get('display') or wid})
    out = {'generated_utc': _utc(), 'status': 'OK' if wells else 'NO DRILLING RECORD', 'wells': wells, 'well': None, 'days': [], 'sheet': None, 'day_starts': None,
           'not_a_measurement': list(NOT_A_MEASUREMENT), 'not_in_the_record': list(NOT_IN_THE_RECORD), 'remark_max': REMARK_MAX,
           'lines': [] if wells else ["No well of this workspace has a drilling recorder's export attached: attach one on the Wells tab, and its days are written here."]}
    if not wells:
        return out
    if well_id is not None and well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    if well_id is not None and well_id not in ids:
        raise DailyReportError(f"{well_id} has no drilling recorder's export attached")
    wid = well_id or ids[0]
    out['well'] = wid
    try:
        out['days'] = days(ws, wid)
        s = sheet(ws, wid, date)
    except DailyReportError:
        raise
    except Exception as e:                                   # a record the rig-time page cannot read: the tab says so and lists the other wells
        out.update(status='NOT READ', lines=[f"{ws.well(wid).get('display') or wid}: the record could not be read - {type(e).__name__}: {e}"])
        return out
    out.update(sheet=s, day_starts=s['day_starts'], lines=list(s['lines']), day_start_declared=_day_start_declared(ws))
    return out


def _dated(ws) -> str:
    """Whether the contract's terms date a report by the day it starts on (the default) or the day it ends on."""
    try:
        from . import invoice_check as IC
        return 'end' if IC.terms(ws).get('report_date') == 'end' else 'start'
    except Exception:
        return 'start'


def _day_start_declared(ws) -> bool:
    try:
        from . import invoice_check as IC
        return bool(IC.terms(ws).get('day_start'))
    except Exception:
        return False


# -- the words and the files ---------------------------------------------------------------------------------------------

def text(p: dict) -> str:
    s = p.get('sheet')
    if not s:
        return '\n'.join(p['lines'])
    d = s['depth']
    out = [f"Daily drilling report - {s['display']} - {s['date']} (report {s['report_no']} of {s['days_in_record']})"]
    out += ['  ' + l for l in s['lines']]
    out += ['', '  The time log:', f"  {'from':<6s}{'to':<6s}{'hours':>6s}  {'operation':<18s} what"]
    out += [f"  {l['from']:<6s}{l['to']:<6s}{l['hours']:>6.2f}  {l['operation']:<18s} {l['words']}" for l in s['time_log']]
    if s['parameters']:
        out += ['', '  While drilling (the middle value, and the range nine tenths of the drilling sat in):']
        out += [f"  {x['what']:<24s} {x['median']:>10,.1f} {x['unit']:<8s} {x['p10']:,.1f} to {x['p90']:,.1f}" for x in s['parameters']]
    if s['mud']:
        out += ['', '  The mud the record carries:'] + [f"  {x['what']:<24s} {x['start']:>10,.2f} at the start, {x['end']:,.2f} at the end {x['unit']}" for x in s['mud']]
    if s['alerts']:
        out += ['', "  Places in the day's record to look at:"] + [f"  {str(a_['time'] or '')[11:16]:<6s} {a_['kind']:<12s} {a_['words']}" for a_ in s['alerts']]
    rm = s.get('remarks')
    if rm:
        out += ['', f"  Remarks ({rm['by']}, {rm['utc']}): {rm['text']}"] + ([f"  Plan for the next day: {rm['plan']}"] if rm.get('plan') else [])
    return '\n'.join(out)


_CSS = ("body{font:13px/1.45 system-ui,Segoe UI,Arial,sans-serif;color:#141412;margin:24px;max-width:980px}h1{font-size:20px;margin:0 0 2px}h2{font-size:14px;margin:18px 0 6px;border-bottom:1px solid #999}"
        "table{border-collapse:collapse;width:100%;margin:4px 0}td,th{border:1px solid #bbb;padding:3px 6px;text-align:left;vertical-align:top}th{background:#eee}.n{text-align:right}"
        ".muted{color:#555}.box{border:1px solid #bbb;padding:6px 8px;min-height:38px;white-space:pre-wrap}@media print{body{margin:8mm}h2{break-after:avoid}tr{break-inside:avoid}}")


def html_sheet(s: dict, program_version: str) -> str:
    """The sheet as one page that prints: no script, nothing fetched."""
    e = _html.escape
    f0 = lambda v: '-' if v is None else f'{v:,.0f}'
    g = lambda v: '-' if v is None else f'{v:g}'
    d = s['depth']; dr = s['drilling']; cn = s['connections']; h = s['hours']; rec = s['record']
    H = ['<!doctype html>', '<html lang="en"><head><meta charset="utf-8">', f"<title>Daily drilling report - {e(s['display'])} - {e(s['date'])}</title>", f'<style>{_CSS}</style></head><body>',
         f"<h1>Daily drilling report</h1><p class=\"muted\">{e(s['site'])} · <b>{e(s['display'])}</b> · report {s['report_no']} of {s['days_in_record']} · the day from "
         f"{e(s['covers']['from'].replace('T', ' '))} to {e(s['covers']['to'].replace('T', ' '))}, {e(s['clock'])} · written from the drilling record by GEA-Program {e(program_version)}</p>",
         '<h2>The day</h2><table><tr><th>Hole at the start, ft</th><th>Hole at the end, ft</th><th>Footage, ft</th><th>Bit at the end, ft</th><th>Rotated, ft</th><th>Slid, ft</th><th>On bottom, h</th><th>Rate on bottom, ft/h</th><th>Record, h</th></tr>',
         f"<tr><td class=\"n\">{f0(d['hole_from_ft'])}</td><td class=\"n\">{f0(d['hole_to_ft'])}</td><td class=\"n\">{f0(d['footage_ft'])}</td><td class=\"n\">{f0(d['bit_at_end_ft'])}</td><td class=\"n\">{f0(dr['rotary_ft'])}</td>"
         f"<td class=\"n\">{f0(dr['slide_ft'])}</td><td class=\"n\">{g(dr['on_bottom_h'])}</td><td class=\"n\">{f0(dr['rop_on_bottom_ft_h'])}</td><td class=\"n\">{g(rec['hours'])}</td></tr></table>",
         '<ul>' + ''.join(f'<li>{e(l)}</li>' for l in s['lines']) + '</ul>',
         '<h2>The time log</h2><table><tr><th>From</th><th>To</th><th>Hours</th><th>Operation</th><th>What the record shows</th></tr>'
         + ''.join(f"<tr><td>{e(l['from'])}</td><td>{e(l['to'])}</td><td class=\"n\">{l['hours']:.2f}</td><td>{e(l['operation'].capitalize())}</td><td>{e(l['words'])}</td></tr>" for l in s['time_log'])
         + f"<tr><th colspan=\"2\">The day</th><th class=\"n\">{sum(l['hours'] for l in s['time_log']):.2f}</th><th colspan=\"2\"></th></tr></table>",
         '<h2>Hours by rig state</h2><table><tr>' + ''.join(f'<th>{e(k.capitalize())}</th>' for k in h) + '<th>No record</th></tr><tr>' + ''.join(f'<td class="n">{g(v)}</td>' for v in h.values())
         + f"<td class=\"n\">{g(rec['no_record_h'])}</td></tr></table>",
         f"<p>Drilling connections: {cn['n']}" + (f", weight to weight {g(cn['median_w2w_min'])} min at the middle, {g(cn['slowest_w2w_min'])} at the slowest" if cn['n'] else '')
         + (f"; {g(cn['lost_h'])} h lost against the pad's targets" if cn.get('lost_h') else '') + '.</p>']
    if s['parameters']:
        H.append('<h2>While drilling</h2><table><tr><th>Reading</th><th>Channel</th><th>Middle value</th><th>Nine tenths of the drilling between</th><th>Unit</th></tr>'
                 + ''.join(f"<tr><td>{e(x['what'])}</td><td>{e(x['channel'])}</td><td class=\"n\">{x['median']:,.1f}</td><td class=\"n\">{x['p10']:,.1f} and {x['p90']:,.1f}</td><td>{e(x['unit'])}</td></tr>" for x in s['parameters']) + '</table>')
    if s['mud']:
        H.append('<h2>The mud the record carries</h2><table><tr><th>Reading</th><th>Channel</th><th>At the start</th><th>At the end</th><th>Lowest and highest</th><th>Unit</th></tr>'
                 + ''.join(f"<tr><td>{e(x['what'])}</td><td>{e(x['channel'])}</td><td class=\"n\">{x['start']:,.2f}</td><td class=\"n\">{x['end']:,.2f}</td><td class=\"n\">{x['low']:,.2f} and {x['high']:,.2f}</td><td>{e(x['unit'])}</td></tr>" for x in s['mud']) + '</table>')
    if s['trips']:
        H.append('<h2>Trips</h2><table><tr><th>Start</th><th>Direction</th><th>Bit from, ft</th><th>Bit to, ft</th><th>Hours handling pipe</th><th>Speed, ft/h</th><th>The trip sheet</th></tr>'
                 + ''.join(f"<tr><td>{e(str(x['start'] or '').replace('T', ' '))}</td><td>{'In' if x['direction'] == 'IN' else 'Out'}</td><td class=\"n\">{f0(x['from_ft'])}</td><td class=\"n\">{f0(x['to_ft'])}</td><td class=\"n\">{x['hours']:.2f}</td>"
                           f"<td class=\"n\">{f0(x['speed_ft_h'])}</td><td>{e(str((x.get('trip_sheet') or {}).get('status') or 'not read').lower())}</td></tr>" for x in s['trips']) + '</table>')
    H.append("<h2>Places in the day's record to look at</h2>" + ('<table><tr><th>Time</th><th>Depth, ft</th><th>From</th><th>What</th><th>As the record shows it</th></tr>'
             + ''.join(f"<tr><td>{e(str(a_['time'] or '').replace('T', ' '))}</td><td class=\"n\">{f0(a_['depth_ft'])}</td><td>{e(a_['page'])}</td><td>{e(a_['kind'])}</td><td>{e(a_['words'])}</td></tr>" for a_ in s['alerts']) + '</table>'
             if s['alerts'] else '<p>No drilling alert and no kick or loss event began in the day.</p>'))
    if s['surveys']:
        l_ = s['surveys']['last']
        H.append(f"<p>Survey stations in the day's hole: {s['surveys']['stations']}" + (f"; the last at {l_['md_ft']:,.0f} ft, {g(l_['inclination_deg'])} deg at {g(l_['azimuth_deg'])}" if l_ else '') + '.</p>')
    if s['strings']:
        H.append('<p>Drill-string runs in the hole in the day, as declared: ' + '; '.join(e(str(x['name'])) for x in s['strings']) + '.</p>')
    rm = s.get('remarks') or {}
    H.append(f"<h2>Remarks</h2><div class=\"box\">{e(rm.get('text') or '')}</div>" + (f"<p class=\"muted\">Typed by {e(rm['by'])}, {e(rm['utc'])}.</p>" if rm else '<p class="muted">None typed.</p>')
             + f"<h2>Plan for the next day</h2><div class=\"box\">{e(rm.get('plan') or '')}</div>")
    H.append('<h2>What this sheet is not</h2><ul>' + ''.join(f'<li>{e(x)}</li>' for x in NOT_A_MEASUREMENT) + '</ul></body></html>')
    return '\n'.join(H)


def write_report(ws, program_version: str, well_id: Optional[str] = None, date: Optional[str] = None, actor: str = 'system', every_day: bool = False) -> dict:
    """The sheet as files: reports/daily/<well>/<date>/daily_report.html (it prints), .md, .json and time_log.csv.
    `every_day` writes every report day of the well's record."""
    p = page(ws, well_id, None if every_day else date)
    if not p.get('sheet'):
        raise DailyReportError(p['lines'][0])
    wid = p['well']
    dates = [d['date'] for d in p['days']] if every_day else [p['sheet']['date']]
    files_all: List[str] = []; folder = ''
    for dt in dates:
        s = p['sheet'] if dt == p['sheet']['date'] else sheet(ws, wid, dt)
        folder = ws.dir('reports', 'daily', wid, dt)
        with open(os.path.join(folder, 'daily_report.json'), 'w', encoding='utf-8') as f:
            json.dump({'program_version': program_version, 'generated_utc': _utc(), 'sheet': json.loads(json.dumps(s, default=str)), 'not_a_measurement': NOT_A_MEASUREMENT}, f, indent=1)
        with open(os.path.join(folder, 'daily_report.md'), 'w', encoding='utf-8') as f:
            f.write(f"# Daily drilling report - {s['display']} - {s['date']}\n\nWritten from the drilling record by GEA-Program {program_version}.\n\n```\n" + text({'sheet': s, 'lines': s['lines']})
                    + '\n```\n\nWhat this sheet is not:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
        with open(os.path.join(folder, 'daily_report.html'), 'w', encoding='utf-8') as f:
            f.write(html_sheet(s, program_version))
        with open(os.path.join(folder, 'time_log.csv'), 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            wr.writerow(['well', 'report_day', 'from', 'to', 'hours', 'operation', 'hole_from_ft', 'hole_to_ft', 'bit_from_ft', 'bit_to_ft', 'connections', 'no_record_h', 'what'])
            for l in s['time_log']:
                wr.writerow([s['display'], s['date'], l['from'], l['to'], l['hours'], l['operation'], *('' if l[k] is None else l[k] for k in ('hole_from_ft', 'hole_to_ft', 'bit_from_ft', 'bit_to_ft')),
                             l['connections'], l['no_record_h'], l['words']])
        files_all += [f'{dt}/{n}' for n in ('daily_report.html', 'daily_report.md', 'daily_report.json', 'time_log.csv')]
    ws.audit(actor, 'daily_report.report', {'well': wid, 'days': dates, 'files': len(files_all)})
    return {'folder': os.path.dirname(folder) if every_day else folder, 'files': files_all if every_day else [x.split('/', 1)[1] for x in files_all], 'days': dates, 'page': p}


# -- the self-test: a record whose day is known --------------------------------------------------------------------------------

def selftest() -> dict:
    from . import dysfunction as DY
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    rec = DY.synthetic_record(True)                                  # 40 stands of 95 ft from 06:00 on 2026-09-01, a trip of 2,000 ft out and back after the 24th
    rs = RS.analyse_arrays(rec['t'], rec['t0'], rec['ch'], rec['drilling'], rec['ch']['hole'], rec.get('block_weight'))
    ch = {'flow': ('Total Pump Output', 'gal/min', rec['ch']['flow']), 'spp': ('Standpipe Pressure', 'psi', rec['ch']['spp']), 'rpm': ('Rotary RPM', 'rpm', rec['ch']['rpm'])}
    mk = lambda date, h0=6.0: sheet_from_arrays(rs['_t'], rec['t0'], rs['_state'], rs['_dtv'], rec['ch']['hole'], rec['ch']['bit'], rs['connections'], rs['trips'], h0, date, ch)
    d1 = mk('2026-09-01'); d2 = mk('2026-09-02')
    total = float(rec['ch']['hole'][-1] - rec['ch']['hole'][0])
    ck("two report days from 06:00: the first whole, the second ending with the record",
       d1['report_no'] == 1 and d2['report_no'] == 2 and d1['days_in_record'] == 2 and d1['record']['whole_day'] and not d2['record']['whole_day'] and d2['record']['ends_in_this_day']
       and d1['covers'] == {'from': '2026-09-01T06:00', 'to': '2026-09-02T06:00'})
    ck("every time log comes to 24 hours, and the first day's has no hour without a record",
       abs(sum(l['hours'] for l in d1['time_log']) - 24.0) < 1e-9 and abs(sum(l['hours'] for l in d2['time_log']) - 24.0) < 1e-9
       and all(l['operation'] != 'NO RECORD' for l in d1['time_log']) and d2['time_log'][-1]['operation'] == 'NO RECORD')
    ck("the two days' footage is the record's, and each day's is what was rotated and slid in it",
       abs((d1['depth']['footage_ft'] + d2['depth']['footage_ft']) - total) <= 2.0 and d1['depth']['hole_to_ft'] == d2['depth']['hole_from_ft']
       and abs(d1['depth']['footage_ft'] - (d1['drilling']['rotary_ft'] + d1['drilling']['slide_ft'])) <= 2.0)
    ck("the connections of the two days are the record's, five and a half minutes weight to weight",
       d1['connections']['n'] + d2['connections']['n'] == len(rs['connections']) >= 38 and 5.4 <= d1['connections']['median_w2w_min'] <= 5.6)
    ops1 = [l['operation'] for l in d1['time_log']]
    ck("the trip is on the first day's log, out and then in, between two lines of drilling",
       len(d1['trips']) == 2 and [x['direction'] for x in d1['trips']] == ['OUT', 'IN'] and 'TRIPPING OUT' in ops1 and 'TRIPPING IN' in ops1
       and ops1.index('TRIPPING OUT') < ops1.index('TRIPPING IN') and ops1[0] == 'DRILLING' and ops1[-1] == 'DRILLING' and len(d2['trips']) == 0)
    ck("a connection's slips are counted with the drilling around it: no line of the log is the slips alone, and the hours by rig state still show them",
       'IN SLIPS' not in ops1 and d1['hours']['IN SLIPS'] > 1.0 and d1['time_log'][0]['states_h'].get('IN SLIPS', 0) > 0.5)
    ck("the drilling is 150 ft/h on bottom at 550 gal/min, as the record was made",
       abs(d1['drilling']['rop_on_bottom_ft_h'] - 150.0) < 3.0 and abs(next(x for x in d1['parameters'] if x['key'] == 'flow')['median'] - 550.0) < 3.0
       and next(x for x in d1['parameters'] if x['key'] == 'rpm')['median'] == 120.0)
    m1 = mk('2026-09-01', 0.0)
    ck("a day that starts at midnight is another day: the record begins six hours into it",
       m1['covers']['from'] == '2026-09-01T00:00' and m1['time_log'][0]['operation'] == 'NO RECORD' and m1['time_log'][0]['hours'] == 6.0 and abs(m1['record']['hours'] - 18.0) < 0.01)
    try:
        mk('2026-10-01'); none = False
    except DailyReportError:
        none = True
    ck("a day the record does not reach is named as that", none)
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'a made record: forty stands and a trip, from 06:00'}
