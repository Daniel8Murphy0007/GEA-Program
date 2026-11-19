# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Procedures: each connection and each trip of a drilling record against the rules the operator declares.

A drilling program says how a connection is to be made and how a trip is to be started: circulate so long before
the slips, ream the stand, stop the pumps first, bring them back up slowly, check for flow before pulling out, do not
pull faster than so much. Whether the crews did is in the drilling recorder, second by second, and nobody reads it.
This module does, for the part of a procedure a recorder can see.

WHAT IS MEASURED, at every drilling connection the Rig time tab finds (weight to weight: the last second drilling to
the first second drilling again):
- the seconds the pumps ran after the bit came off bottom and before the slips were set;
- the reaming passes in that time: each time the bit was run back down 20 ft or more with the pumps and the rotary on;
- whether the pumps were off at the second the slips were set, and how far off bottom the bit was at the last second
  they ran;
- the seconds the pump rate took, the last time the pumps were started before drilling again, from stopped to nine
  tenths of the rate drilling resumed at;
- the weight-to-weight minutes.
And at every trip of 1,000 ft or more:
- the pipe's speed: the speed that 95 in 100 of its moving seconds stayed under, taken over ten seconds, in ft/min;
- for a trip out that starts within 500 ft of bottom, between the last second drilling (or the trip before, and no
  further back than six hours) and the trip: the minutes the pumps ran, and the longest stretch with the pumps off and
  the pipe still - a flow check, as far as a recorder can see one.

WHEN THE PUMPS ARE ON. The pump rate above 50 gal/min - but a recorder works its pump rate out from the strokes, and
some hold the last figure for most of a minute after the strokes stop. Read as it stands, that makes every connection
look as if it were made with the pumps running. So where the standpipe pressure is recorded too, the pumps are off
from the second the pressure began a fall that took it under a quarter of its greatest in the two minutes before
(pumps_on). A reading or two dropped by the recorder is not a stop.

A RULE is one of those measures with a figure and where it came from: "at least 60 s circulating off bottom before
the slips are set (the drilling program, section 4.2)". It may be kept to one well and to a stretch of hole. Each
connection or trip the rule covers is MET, NOT MET, or NOT READ where the record cannot give the measure. With no
rule declared the page still gives what the crews do now - the middle value and the spread of each measure - which is
where a rule usually starts.

What it will not call a measurement: compliance with a procedure. It is the part of a procedure a recorder can
see. A flow check here is the pumps off and the pipe still; whether anyone watched the flow line is not in a
recorder. A survey taken, a pill pumped, the pipe filled, who was at the brake: none is in it."""
from __future__ import annotations

import csv
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import rig_state as RS

REAM_PASS_FT = 20.0             # the bit run back down this far, pumps and rotary on, is one reaming pass
RAMP_SHARE = 0.9                # the pumps are "up" at this share of the rate drilling resumes at
DROPOUT_S = 3.0                 # a reading that drops out for this long or less, between two that read, is the recorder and not the rig
HELD_LOOK_S = 120.0             # a pump rate still reading while the standpipe pressure has gone is a held figure: the pressure against its greatest in this long before
HELD_SHARE = 0.25
HELD_MIN_PSI = 60.0
TRIP_FROM_BOTTOM_FT = 500.0     # a trip out that starts this close to bottom is a trip off bottom: what came before it is asked of that one
LOOK_BACK_S = 6 * 3600.0        # before a trip out: back to the last second drilling, and no further than this
SPEED_WINDOW_S = 10.0
SPEED_PCT = 95.0
LIST_MAX = 60
SERIES_MAX = 400
LIVE_EVENT_S = 7200.0
EVENT = 'procedure.departed'
# kind -> (what it is asked of, its words, its unit, 'min' the measure must be at least the figure / 'max' at most / 'yes' must be so, the bounds a figure is kept to)
KINDS: Dict[str, dict] = {
    'conn.circulate_before_s': {'on': 'connection', 'name': 'circulating before the slips', 'ask': 'at least {v} s circulating off bottom before the slips are set',
                                'words': 'the seconds the pumps ran after the bit came off bottom and before the slips were set', 'unit': 's', 'sense': 'min', 'bounds': (5.0, 3600.0)},
    'conn.ream_passes': {'on': 'connection', 'name': 'reaming passes', 'ask': 'at least {v} reaming pass{es} before the slips are set',
                         'words': 'the reaming passes before the slips were set: the bit run back down 20 ft or more with the pumps and the rotary on', 'unit': 'passes', 'sense': 'min', 'bounds': (1.0, 10.0)},
    'conn.off_bottom_ft': {'on': 'connection', 'name': 'off bottom when the pumps stop', 'ask': 'the bit at least {v} ft off bottom when the pumps are stopped',
                           'words': 'how far off bottom the bit was at the last second the pumps ran before the slips', 'unit': 'ft', 'sense': 'min', 'bounds': (1.0, 200.0)},
    'conn.pumps_off_before_slips': {'on': 'connection', 'name': 'pumps off at the slips', 'ask': 'the pumps off when the slips are set',
                                    'words': 'whether the pumps were off at the second the slips were set', 'unit': '', 'sense': 'yes', 'bounds': None},
    'conn.pump_ramp_s': {'on': 'connection', 'name': 'bringing the pumps up', 'ask': 'at least {v} s to bring the pumps up after a connection',
                         'words': 'the seconds the pump rate took, the last time the pumps were started before drilling again, from stopped to nine tenths of the rate drilling resumed at', 'unit': 's', 'sense': 'min', 'bounds': (2.0, 1800.0)},
    'conn.w2w_max_min': {'on': 'connection', 'name': 'weight to weight', 'ask': 'weight to weight in no more than {v} min',
                         'words': 'the minutes from the last second drilling to the first second drilling again', 'unit': 'min', 'sense': 'max', 'bounds': (1.0, 60.0)},
    'trip.circulate_before_min': {'on': 'trip off bottom', 'name': 'circulating before a trip out', 'ask': 'at least {v} min circulating before a trip out off bottom',
                                  'words': 'the minutes the pumps ran between the last second drilling and a trip out that starts off bottom', 'unit': 'min', 'sense': 'min', 'bounds': (1.0, 600.0)},
    'trip.flow_check_min': {'on': 'trip off bottom', 'name': 'flow check before a trip out', 'ask': 'the pumps off and the pipe still at least {v} min before a trip out off bottom',
                            'words': 'the longest stretch with the pumps off and the pipe still between the last second drilling and a trip out that starts off bottom: a flow check, as far as a recorder sees one',
                            'unit': 'min', 'sense': 'min', 'bounds': (1.0, 120.0)},
    'trip.max_speed_ft_min': {'on': 'trip', 'name': "the pipe's speed", 'ask': 'the pipe no faster than {v} ft/min on a trip',
                              'words': "the speed that 95 in 100 of a trip's moving seconds stayed under, taken over ten seconds", 'unit': 'ft/min', 'sense': 'max', 'bounds': (10.0, 600.0)},
}
MEASURE = {'conn.circulate_before_s': 'circ_before_s', 'conn.ream_passes': 'ream_passes', 'conn.off_bottom_ft': 'off_bottom_ft', 'conn.pumps_off_before_slips': 'pumps_off_at_slips',
           'conn.pump_ramp_s': 'pump_ramp_s', 'conn.w2w_max_min': 'w2w_min', 'trip.circulate_before_min': 'circ_before_min',
           'trip.flow_check_min': 'flow_check_min', 'trip.max_speed_ft_min': 'speed_ft_min'}
NOT_A_MEASUREMENT = [
    "compliance with a procedure: this is the part of a procedure a drilling recorder can see, and a procedure asks for much it cannot - a survey taken, a pill pumped, the pipe filled, the flow line watched",
    "a flow check: here it is the pumps off and the pipe still for so long before a trip out; whether the well was watched in that time is not in a recorder",
    "a reaming pass: the bit run back down 20 ft or more with the pumps and the rotary on, before the slips; a pass shorter than that, or one made without rotation, is not counted",
    "the second the pumps stopped or started: the rate crossing 50 gal/min, and the standpipe pressure's fall where the record has it; a recorder that smooths its pump rate lengthens every ramp, one that reads it in steps shortens it, and one sampled every ten seconds cannot place a stop or time a ramp closer than that",
    "who did it: the tour is the half of the report day the event began in, not a crew",
    "the reason: a connection that did not meet a rule may have had a good one - a kick drill, a survey, a tight spot - and the daily report is where that is written",
]
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


class ProcedureError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _r(v, n: int = 1):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


# -- what a record shows at a connection and at a trip -------------------------------------------------------------------------

def _rollmax(x: np.ndarray, k: int) -> np.ndarray:
    """The greatest of each sample and the k-1 before it (blocks' running maxima from both ends: one pass, no window copied)."""
    n = len(x); k = max(1, int(k))
    if k == 1 or n == 0:
        return x.copy()
    pad = (-n) % k
    y = np.concatenate([x, np.full(pad, -np.inf)]).reshape(-1, k)
    pre = np.maximum.accumulate(y, axis=1).ravel()
    suf = np.maximum.accumulate(y[:, ::-1], axis=1)[:, ::-1].ravel()
    out = pre[:n].copy()
    out[k - 1:] = np.maximum(suf[:n - k + 1], pre[k - 1:n])
    return out


def _rate_on(t: np.ndarray, flow: np.ndarray) -> np.ndarray:
    """The pump rate above 50 gal/min, a reading or two dropped by the recorder not taken as a stop."""
    return RS._clean(np.nan_to_num(np.asarray(flow, dtype=np.float64)) > RS.P['flow_gpm'], t, 0.0, DROPOUT_S)


def pumps_on(t: np.ndarray, flow: Optional[np.ndarray], spp: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """Whether the pumps are running, sample by sample. The pump rate above 50 gal/min - but a recorder works its
    pump rate out from the strokes and may hold the last figure for a minute after the strokes stop, so where the
    standpipe pressure is recorded too, the pumps are off from the second the pressure began a fall that took it under
    a quarter of its greatest in the two minutes before, for as long as it stays there. A reading or two dropped
    (three seconds or less) is not a stop. With no pump rate: the standpipe pressure above 100 psi."""
    if flow is None:
        return None if spp is None else (np.nan_to_num(np.asarray(spp, dtype=np.float64)) > RS.P['spp_psi'])
    on = _rate_on(t, flow)
    if spp is None or len(on) < 3:
        return on
    p = np.nan_to_num(np.asarray(spp, dtype=np.float64))
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    k = max(2, int(round(HELD_LOOK_S / max(cad, 1e-6))))
    top = _rollmax(p, k)
    low = on & (top >= HELD_MIN_PSI) & (p < HELD_SHARE * top)
    low = ~RS._clean(~low, t, 0.0, DROPOUT_S)                    # a pressure reading or two dropped is not the pumps stopping
    out = on & ~low
    for a, _b in RS._runs(low):
        j = a
        while j > 0 and a - j < k and on[j - 1] and p[j - 1] < 0.9 * top[a]:
            j -= 1
        out[j:a] = False
    return out


def _pumps_rot(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    flow = np.nan_to_num(np.asarray(ch['flow'], dtype=np.float64)) if ch.get('flow') is not None else None
    rpm = np.nan_to_num(np.asarray(ch['rpm'], dtype=np.float64)) if ch.get('rpm') is not None else None
    return pumps_on(t, ch.get('flow'), ch.get('spp')), ((rpm > RS.P['rpm']) if rpm is not None else None), flow


def measure_connections(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], rs: dict) -> List[dict]:
    """Each drilling connection the rig-time reading found, with what the record shows of how it was made. A measure
    the record cannot give is None."""
    t = np.asarray(t, dtype=np.float64); dtv = rs['_dtv']
    pumps, rot, flow = _pumps_rot(t, ch)
    bit = np.nan_to_num(np.asarray(ch['bit'], dtype=np.float64)) if ch.get('bit') is not None else None
    bot = np.nan_to_num(np.asarray(ch['hole'], dtype=np.float64)) if ch.get('hole') is not None else None
    fon = _rate_on(t, flow) if flow is not None else None
    out = []
    for c in rs['connections']:
        i0 = int(np.searchsorted(t, c['t_off']))                                        # the last second drilling
        i_set = int(np.searchsorted(t, c['t_off'] + c['pre_min'] * 60.0))
        i1 = min(int(np.searchsorted(t, c['t_off'] + c['w2w_min'] * 60.0)), len(t) - 1)  # the first second drilling again
        i_set = min(max(i_set, i0 + 1), len(t) - 1)
        m = {'t': float(c['t_off']), 'depth_ft': c.get('depth_ft'), 'w2w_min': float(c['w2w_min']), 'circ_before_s': None, 'ream_passes': None, 'off_bottom_ft': None,
             'pumps_off_at_slips': None, 'pump_ramp_s': None}
        a, b = i0 + 1, i_set                                                            # off bottom, before the slips
        if pumps is not None:
            m['circ_before_s'] = float(dtv[a:b][pumps[a:b]].sum()) if b > a else 0.0
            m['pumps_off_at_slips'] = bool(not pumps[i_set])
            if bit is not None and bot is not None and not pumps[i_set]:
                on = np.flatnonzero(pumps[i0:i_set + 1])
                j = i0 + int(on[-1]) if len(on) else i0                                 # the last second the pumps ran
                m['off_bottom_ft'] = max(float(bot[j] - bit[j]), 0.0)
        if flow is not None and i1 > i_set:                                             # the last time the pumps were started before drilling again
            off = np.flatnonzero(~fon[i_set:i1])
            seg = flow[i1:min(int(np.searchsorted(t, t[i1] + 120.0)), len(t))]
            rate = float(np.median(seg)) if len(seg) else 0.0
            if len(off) and rate >= 2.0 * RS.P['flow_gpm']:                             # a record whose pump rate reads nothing while drilling cannot time a ramp
                j0 = i_set + int(off[-1])                                               # the last second the rate read as stopped
                up = np.flatnonzero(flow[j0:min(i1 + len(seg), len(t))] >= RAMP_SHARE * rate)
                if len(up):
                    m['pump_ramp_s'] = float(t[j0 + int(up[0])] - t[j0])
        if bit is not None and pumps is not None and rot is not None:
            passes = 0
            if b > a + 1:
                for x, y in RS._legs(bit, np.arange(a, b), REAM_PASS_FT / 2.0):
                    if bit[y] - bit[x] >= REAM_PASS_FT and float(np.mean(pumps[x:y + 1] & rot[x:y + 1])) >= 0.5:
                        passes += 1
            m['ream_passes'] = passes
        out.append(m)
    return out


def measure_trips(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], rs: dict) -> List[dict]:
    """Each trip the rig-time reading found, with the pipe's speed and - for a trip out that starts off bottom - what
    came before it, back to the last second drilling (or the trip before, and no further than six hours)."""
    t = np.asarray(t, dtype=np.float64); st = rs['_state']; dtv = rs['_dtv']
    pumps, _rot, _flow = _pumps_rot(t, ch)
    bit = np.nan_to_num(np.asarray(ch['bit'], dtype=np.float64)) if ch.get('bit') is not None else None
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    k = max(1, int(round(SPEED_WINDOW_S / max(cad, 1e-6))))
    drilling = (st == RS.ROT) | (st == RS.SLIDE)
    out = []; prev_end = 0
    for x in sorted(rs['trips'], key=lambda q: q['t_start']):
        ia = int(np.searchsorted(t, x['t_start'])); iz = min(int(np.searchsorted(t, x['t_end'])), len(t) - 1)
        hb = x.get('hole_bottom_ft')
        m = {'t': float(x['t_start']), 't_end': float(x['t_end']), 'direction': x['direction'], 'from_ft': x['from_ft'], 'to_ft': x['to_ft'], 'depth_ft': hb, 'feet': x['feet'],
             'off_bottom': bool(x['direction'] == 'OUT' and hb is not None and hb - x['from_ft'] <= TRIP_FROM_BOTTOM_FT),
             'speed_ft_min': None, 'circ_before_min': None, 'flow_check_min': None, '_speed': None}
        if bit is not None and iz - ia > k:
            seg_b = bit[ia:iz + 1]; seg_t = t[ia:iz + 1]
            v = np.abs(seg_b[k:] - seg_b[:-k]) / np.maximum(seg_t[k:] - seg_t[:-k], 1e-6)          # ft/s over the window
            ok = (v > RS.P['move_ft_s']) & (v < RS.P['jump_ft_s']) & ((seg_t[k:] - seg_t[:-k]) < 3.0 * SPEED_WINDOW_S + cad)
            m['_speed'] = (seg_b[:-k][ok], v[ok] * 60.0)                                             # the bit's depth and its speed, ft/min
            if ok.sum() >= 10:
                m['speed_ft_min'] = float(np.percentile(v[ok] * 60.0, SPEED_PCT))
        if m['off_bottom'] and pumps is not None:
            lo = max(int(np.searchsorted(t, x['t_start'] - LOOK_BACK_S)), prev_end)
            d = np.flatnonzero(drilling[lo:ia])
            a = lo + int(d[-1]) + 1 if len(d) else lo
            m['since_drilling'] = bool(len(d))
            m['circ_before_min'] = 0.0; m['flow_check_min'] = 0.0
            if ia > a:
                m['circ_before_min'] = float(dtv[a:ia][pumps[a:ia]].sum() / 60.0)
                still = ~pumps[a:ia] & ~np.isin(st[a:ia], (RS.TIN, RS.TOUT, RS.REAM, RS.WASH, RS.ROT, RS.SLIDE))
                best = 0.0
                for p, q in RS._runs(still):
                    best = max(best, float(dtv[a + p:a + q].sum()))
                m['flow_check_min'] = best / 60.0
        prev_end = max(prev_end, iz)
        out.append(m)
    return out


# -- the rules -----------------------------------------------------------------------------------------------------------------

def check_rule(kind: str, value, well: Optional[str] = None, from_md_ft=None, to_md_ft=None, direction: Optional[str] = None, source: str = '') -> dict:
    """A rule checked, not kept."""
    if kind not in KINDS:
        raise ProcedureError(f"a rule is one of: {', '.join(KINDS)}")
    K = KINDS[kind]
    rule = {'kind': kind, 'well': (well or '').strip() or None, 'from_md_ft': None, 'to_md_ft': None, 'direction': None}
    if K['sense'] == 'yes':
        rule['value'] = None
    else:
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise ProcedureError(f"{K['name']}: a figure in {K['unit']}")
        lo, hi = K['bounds']
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ProcedureError(f"{K['name']}: a figure between {lo:g} and {hi:g} {K['unit']}")
        rule['value'] = x
    for key, v in (('from_md_ft', from_md_ft), ('to_md_ft', to_md_ft)):
        if v not in (None, ''):
            try:
                rule[key] = float(v)
            except (TypeError, ValueError):
                raise ProcedureError('the stretch of hole a rule is kept to: depths in ft MD')
            if not 0.0 <= rule[key] <= 50000.0:
                raise ProcedureError('the stretch of hole a rule is kept to: depths between 0 and 50,000 ft MD')
    if rule['from_md_ft'] is not None and rule['to_md_ft'] is not None and rule['to_md_ft'] <= rule['from_md_ft']:
        raise ProcedureError('the stretch of hole a rule is kept to must end deeper than it starts')
    if kind == 'trip.max_speed_ft_min':
        d = (direction or 'both').strip().lower()
        if d not in ('in', 'out', 'both'):
            raise ProcedureError('a speed rule is for trips in, trips out, or both')
        rule['direction'] = d
    if not (source or '').strip():
        raise ProcedureError('say where the rule came from (the drilling program and its section, the operator\'s standard)')
    rule['source'] = source.strip()[:300]
    return rule


def words(rule: dict) -> str:
    K = KINDS[rule['kind']]
    v = rule.get('value')
    s = K['ask'].format(v=format(v, 'g') if v is not None else '', es='' if v == 1 else 'es')
    if rule.get('direction') and rule['direction'] != 'both':
        s += f" {rule['direction']}"
    if rule.get('from_md_ft') is not None or rule.get('to_md_ft') is not None:
        s += f", between {rule.get('from_md_ft') or 0:,.0f} ft and " + (f"{rule['to_md_ft']:,.0f} ft" if rule.get('to_md_ft') is not None else 'the bottom')
    return s


def _covers(rule: dict, well_id: Optional[str], depth_ft: Optional[float]) -> bool:
    if rule.get('well') and well_id is not None and rule['well'] != well_id:
        return False
    if depth_ft is None:
        return rule.get('from_md_ft') is None and rule.get('to_md_ft') is None
    return (rule.get('from_md_ft') is None or depth_ft >= rule['from_md_ft']) and (rule.get('to_md_ft') is None or depth_ft <= rule['to_md_ft'])


def _judge(rule: dict, ev: dict) -> Tuple[str, Optional[float]]:
    K = KINDS[rule['kind']]; key = MEASURE[rule['kind']]
    v = ev.get(key)
    if rule['kind'] == 'trip.max_speed_ft_min' and (rule.get('from_md_ft') is not None or rule.get('to_md_ft') is not None):
        sp = ev.get('_speed')                                                                    # the speed inside the stretch of hole the rule is kept to
        v = None
        if sp is not None:
            keep = (sp[0] >= (rule.get('from_md_ft') or -1e9)) & (sp[0] <= (rule.get('to_md_ft') if rule.get('to_md_ft') is not None else 1e9))
            if keep.sum() >= 10:
                v = float(np.percentile(sp[1][keep], SPEED_PCT))
    if v is None:
        return 'NOT READ', None
    if K['sense'] == 'yes':
        return ('MET' if v else 'NOT MET'), (1.0 if v else 0.0)
    v = float(v)
    return ('MET' if (v >= rule['value'] - 1e-9 if K['sense'] == 'min' else v <= rule['value'] + 1e-9) else 'NOT MET'), v


def _u(v, unit: str) -> str:
    return f"{v:g} {'pass' if unit == 'passes' and v == 1 else unit}"


def _stat(xs: List[float]) -> Optional[dict]:
    if not xs:
        return None
    a = np.asarray(xs, dtype=np.float64)
    return {'n': int(len(a)), 'median': _r(float(np.median(a)), 1), 'p10': _r(float(np.percentile(a, 10)), 1), 'p90': _r(float(np.percentile(a, 90)), 1)}


def analyse_arrays(t: np.ndarray, t0: datetime, ch: Dict[str, Optional[np.ndarray]], rs: dict, rules: List[dict], well_id: Optional[str] = None, day_start_h: float = 6.0) -> dict:
    """One record against the rules: what the crews do (every measure's middle and spread), and each rule's count."""
    a = judge(measure_connections(t, ch, rs), measure_trips(t, ch, rs), t0, rules, well_id, day_start_h)
    a.update({'t_last': float(t[-1]) if len(t) else None, 'channels': {k: ch.get(k) is not None for k in ('flow', 'spp', 'rpm', 'bit', 'hole')}})
    return a


def judge(conns: List[dict], trs: List[dict], t0: datetime, rules: List[dict], well_id: Optional[str] = None, day_start_h: float = 6.0) -> dict:
    """What was measured at each connection and trip, against the rules."""
    when = lambda s: (t0 + timedelta(seconds=float(s))).isoformat(timespec='seconds')

    def tour(s: float) -> str:
        h = ((t0 + timedelta(seconds=float(s))) - timedelta(hours=day_start_h)).hour
        return 'day' if h < 12 else 'night'
    practice = []
    for kind, K in KINDS.items():
        evs = conns if K['on'] == 'connection' else [x for x in trs if K['on'] == 'trip' or x['off_bottom']]
        vals = [e[MEASURE[kind]] for e in evs if e.get(MEASURE[kind]) is not None]
        if K['sense'] == 'yes':
            practice.append({'kind': kind, 'on': K['on'], 'name': K['name'], 'words': K['words'], 'unit': '', 'events': len(evs), 'read': len(vals), 'yes': int(sum(1 for v in vals if v)),
                             'yes_pct': _r(100.0 * sum(1 for v in vals if v) / len(vals), 0) if vals else None})
        else:
            practice.append({'kind': kind, 'on': K['on'], 'name': K['name'], 'words': K['words'], 'unit': K['unit'], 'events': len(evs), 'read': len(vals), **(_stat([float(v) for v in vals]) or {'n': 0, 'median': None, 'p10': None, 'p90': None})})
    judged = []
    for rule in rules:
        K = KINDS[rule['kind']]
        if rule.get('well') and well_id is not None and rule['well'] != well_id:
            continue
        evs = conns if K['on'] == 'connection' else [x for x in trs if (K['on'] == 'trip' and (rule.get('direction') in (None, 'both') or x['direction'] == rule['direction'].upper())) or (K['on'] == 'trip off bottom' and x['off_bottom'])]
        cnt = {'MET': 0, 'NOT MET': 0, 'NOT READ': 0}; by = {'day': {'MET': 0, 'NOT MET': 0}, 'night': {'MET': 0, 'NOT MET': 0}}
        deps = []; vals = []; series = []
        speed_scope = rule['kind'] == 'trip.max_speed_ft_min'
        for e in evs:
            if not speed_scope and not _covers(rule, well_id, e.get('depth_ft')):
                continue
            s, v = _judge(rule, e)
            cnt[s] += 1
            if s != 'NOT READ':
                by[tour(e['t'])][s] += 1; vals.append(v); series.append([when(e['t']), _r(v, 1), s == 'MET', e.get('depth_ft')])
            if s == 'NOT MET':
                deps.append({'t': e['t'], 'time': when(e['t']), 'depth_ft': e.get('depth_ft'), 'measured': _r(v, 1), 'tour': tour(e['t']),
                             'what': ('connection' if K['on'] == 'connection' else f"trip {'in' if e.get('direction') == 'IN' else 'out'}")})
        n = cnt['MET'] + cnt['NOT MET']
        worst = sorted(deps, key=(lambda d: -d['measured']) if K['sense'] == 'max' else (lambda d: d['measured']))
        judged.append({'id': rule.get('id'), 'kind': rule['kind'], 'name': K['name'], 'rule': words(rule), 'source': rule.get('source'), 'on': K['on'], 'unit': K['unit'], 'sense': K['sense'], 'value': rule.get('value'),
                       'covered': sum(cnt.values()), 'judged': n, 'met': cnt['MET'], 'not_met': cnt['NOT MET'], 'not_read': cnt['NOT READ'], 'met_pct': _r(100.0 * cnt['MET'] / n, 1) if n else None,
                       'median': (_r(float(np.median(vals)), 1) if vals and K['sense'] != 'yes' else None),
                       'by_tour': {k: {**v, 'met_pct': _r(100.0 * v['MET'] / (v['MET'] + v['NOT MET']), 1) if v['MET'] + v['NOT MET'] else None} for k, v in by.items()},
                       'departures': [{k: v for k, v in d.items()} for d in sorted(deps, key=lambda d: d['t'])[-LIST_MAX:]], 'worst': [{k: v for k, v in d.items() if k != 't'} for d in worst[:5]],
                       'departures_all': len(deps), 'series': series[-SERIES_MAX:]})
    return {'connections': len(conns), 'trips': len(trs), 'trips_out': sum(1 for x in trs if x['direction'] == 'OUT'), 'trips_off_bottom': sum(1 for x in trs if x['off_bottom']), 'practice': practice, 'rules': judged}


# -- the workspace ------------------------------------------------------------------------------------------------------------------

def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'procedures.json')


def rules(ws) -> List[dict]:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            d = json.load(f)
        return [r for r in (d.get('rules') or []) if isinstance(r, dict) and r.get('kind') in KINDS]
    except (OSError, ValueError):
        return []


def _save(ws, rs_: List[dict]) -> None:
    tmp = _path(ws) + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'rules': rs_}, f, indent=1)
    os.replace(tmp, _path(ws))


def add_rule(ws, actor: str, kind: str, value=None, well: Optional[str] = None, from_md_ft=None, to_md_ft=None, direction: Optional[str] = None, source: str = '') -> dict:
    rule = check_rule(kind, value, well, from_md_ft, to_md_ft, direction, source)
    if rule['well'] and not any(w['id'] == rule['well'] for w in ws.wells()):
        raise KeyError(f"no well {rule['well']}")
    with _LOCK:
        cur = rules(ws)
        n = 1 + max([int(str(r.get('id', 'R0'))[1:]) for r in cur if str(r.get('id', '')).startswith('R') and str(r.get('id'))[1:].isdigit()] or [0])
        rule.update({'id': f'R{n}', 'by': actor, 'utc': _utc()})
        cur.append(rule)
        _save(ws, cur)
    ws.audit(actor, 'procedures.rule', {k: rule[k] for k in ('id', 'kind', 'value', 'well', 'from_md_ft', 'to_md_ft', 'direction', 'source')})
    return rule


def remove_rule(ws, actor: str, rule_id: str) -> dict:
    with _LOCK:
        cur = rules(ws)
        hit = next((r for r in cur if r.get('id') == rule_id), None)
        if hit is None:
            raise KeyError(f'no rule {rule_id}')
        _save(ws, [r for r in cur if r.get('id') != rule_id])
    ws.audit(actor, 'procedures.rule_removed', {'id': rule_id, 'kind': hit['kind'], 'value': hit.get('value')})
    return hit


def well(ws, well_id: str) -> dict:
    """One well's record against the rules. What was measured at its connections and trips is kept until the record
    changes; the rules are applied each time, so declaring one costs nothing."""
    from . import drill_string as DS
    c = DS._cache(ws, well_id, build=True)
    key = (os.path.abspath(ws.path), well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'), RS.settings(ws).get('block_weight_klbf', {}).get(well_id))
    with _LOCK:
        m = _MEMO.get(key)
    if m is None:
        r = RS.well(ws, well_id)
        bottom = c.extra('bottom')
        ch = {'flow': c.channel('Total Pump Output') if 'Total Pump Output' in c._ch else None, 'spp': c.channel('Standpipe Pressure') if 'Standpipe Pressure' in c._ch else None,
              'rpm': c.channel('Rotary RPM') if 'Rotary RPM' in c._ch else None, 'bit': c.channel('Bit Depth') if 'Bit Depth' in c._ch else None,
              'hole': bottom if bottom is not None else (c.channel('Hole Depth') if 'Hole Depth' in c._ch else None)}
        t = np.asarray(r['_t'], dtype=np.float64); t0 = datetime.fromisoformat(r['t0'])
        m = {'conns': measure_connections(t, ch, r), 'trips': measure_trips(t, ch, r), 't0': t0, 't_last': float(t[-1]), 'record_start': r['t0'],
             'record_end': (t0 + timedelta(seconds=float(t[-1]))).isoformat(timespec='seconds'), 'channels': {k: ch.get(k) is not None for k in ('flow', 'spp', 'rpm', 'bit', 'hole')}}
        with _LOCK:
            if len(_MEMO) > 16:
                _MEMO.clear()
            _MEMO[key] = m
    a = judge(m['conns'], m['trips'], m['t0'], rules(ws), well_id, RS._day_start(ws))
    w = ws.well(well_id)
    a.update({'well': well_id, 'display': w.get('display') or well_id, 'record_start': m['record_start'], 'record_end': m['record_end'], 't_last': m['t_last'], 'channels': m['channels'],
              'source_mtime': c.meta.get('source_mtime'), 'status': 'OK', 'clock': "the drilling recorder's own clock"})
    return a


def page(ws, well_id: Optional[str] = None) -> dict:
    """The tab: the rules, and every well with a drilling record against them. Reading it writes nothing."""
    rl = rules(ws)
    out = {'generated_utc': _utc(), 'status': 'OK', 'kinds': [{'kind': k, **{x: v[x] for x in ('on', 'name', 'words', 'unit', 'sense')}, 'bounds': list(v['bounds']) if v['bounds'] else None} for k, v in KINDS.items()],
           'rules': [{**r, 'words': words(r)} for r in rl], 'wells': [], 'well': None, 'result': None, 'not_a_measurement': list(NOT_A_MEASUREMENT),
           'all_wells': [{'id': w['id'], 'display': w.get('display') or w['id']} for w in ws.wells()], 'lines': []}
    ids = RS._wells_with_record(ws)
    if well_id is not None and well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    if not ids:
        out.update(status='NO DRILLING RECORD', lines=["No well of this workspace has a drilling recorder's export attached: attach one on the Wells tab, and its connections and trips are read here."])
        return out
    for wid in ids:
        try:
            r = well(ws, wid)
            n = sum(x['judged'] for x in r['rules']); m = sum(x['met'] for x in r['rules'])
            out['wells'].append({'well': wid, 'display': r['display'], 'status': 'OK', 'connections': r['connections'], 'trips': r['trips'], 'judged': n, 'met': m,
                                 'met_pct': _r(100.0 * m / n, 1) if n else None, 'not_met': n - m, 'rules': [{'id': x['id'], 'met_pct': x['met_pct'], 'judged': x['judged'], 'not_met': x['not_met']} for x in r['rules']]})
        except Exception as e:                               # a record the rig-time reading cannot make sense of is named, and the others are still read
            out['wells'].append({'well': wid, 'display': ws.well(wid).get('display') or wid, 'status': 'NOT READ', 'why': f'{type(e).__name__}: {e}'})
    good = [w for w in out['wells'] if w['status'] == 'OK']
    pick = well_id if well_id in [w['well'] for w in good] else (good[0]['well'] if good else None)
    out['well'] = pick
    if pick:
        r = well(ws, pick)
        out['result'] = {k: v for k, v in r.items() if not k.startswith('_')}
        out['lines'] = _lines(r, rl)
    return out


def _lines(r: dict, rl: List[dict]) -> List[str]:
    out = [f"{r['display']}: {r['connections']} drilling connections and {r['trips']} trips of 1,000 ft or more ({r['trips_out']} out) in the record, by the Rig time tab's reading."]
    if not r['rules']:
        out.append("No rule is declared for this well. The table of what the crews do now - the middle value of each measure and the range eight in ten fall in - is where a rule usually starts.")
    for x in r['rules']:
        if not x['judged']:
            out.append(f"{x['id']}, {x['rule']}: none of the {x['covered']} it covers could be read from the record." if x['covered'] else f"{x['id']}, {x['rule']}: nothing in the record is covered by it.")
            continue
        s = f"{x['id']}, {x['rule']}: met at {x['met']} of {x['judged']} ({x['met_pct']:g}%)"
        d, n = x['by_tour']['day'], x['by_tour']['night']
        if d['met_pct'] is not None and n['met_pct'] is not None:
            s += f"; the day tour {d['met_pct']:g}%, the night tour {n['met_pct']:g}%"
        if x['not_read']:
            s += f"; {x['not_read']} not read"
        if x['worst']:
            w = x['worst'][0]
            s += (f". The furthest from it: {_u(w['measured'], x['unit'])} at {w['time'].replace('T', ' ')}" if x['sense'] != 'yes' else f". The last not met: {x['departures'][-1]['time'].replace('T', ' ')}")
            s += f", {w['depth_ft']:,.0f} ft" if x['sense'] != 'yes' and w.get('depth_ft') is not None else ''
        out.append(s + '.')
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return '\n'.join(p['lines'] or ['Procedures: nothing to read'])
    out = [f"Procedures - {r['display']}"] + ['  ' + l for l in p['lines']]
    out += ['', '  What the crews do now (the middle value, and the range eight in ten fall in):']
    for x in r['practice']:
        if x['unit'] == '' and 'yes_pct' in x:
            out.append(f"  {x['on']:<15s} {x['name']:<30s} " + (f"{x['yes']} of {x['read']} ({x['yes_pct']:g}%)" if x['read'] else 'not read'))
        else:
            out.append(f"  {x['on']:<15s} {x['name']:<30s} " + (f"{_u(x['median'], x['unit'])} ({x['p10']:g} to {x['p90']:g}; {x['n']} read)" if x.get('n') else 'not read'))
    return '\n'.join(out)


def write_report(ws, program_version: str, well_id: Optional[str] = None, actor: str = 'system') -> dict:
    p = page(ws, well_id)
    r = p.get('result')
    if not r:
        raise ProcedureError((p['lines'] or ['nothing to write'])[0])
    d = ws.dir('reports', 'procedures', r['well'])
    with open(os.path.join(d, 'procedures.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, 'generated_utc': p['generated_utc'], 'rules': p['rules'], 'result': json.loads(json.dumps(r, default=str)), 'not_a_measurement': NOT_A_MEASUREMENT}, f, indent=1)
    with open(os.path.join(d, 'procedures.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Procedures - {r['display']}\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p)
                + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'departures.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'rule', 'the_rule', 'source', 'what', 'time', 'depth_ft', 'measured', 'unit', 'tour'])
        for x in r['rules']:
            for dep in x['departures']:
                wr.writerow([r['display'], x['id'], x['rule'], x['source'], dep['what'], dep['time'], '' if dep['depth_ft'] is None else dep['depth_ft'], '' if dep['measured'] is None else dep['measured'], x['unit'], dep['tour']])
    files = ['procedures.json', 'procedures.md', 'departures.csv']
    ws.audit(actor, 'procedures.report', {'well': r['well'], 'files': files, 'rules': len(r['rules'])})
    return {'folder': d, 'files': files, 'page': p}


def live_events(ws, now: Optional[datetime] = None) -> List[dict]:
    """What an alert is raised for: in a record still being written, a connection or a trip that began in its last two
    hours and did not meet a declared rule - once for the rule and the event."""
    from . import dysfunction as DY
    from . import series_cache as SCH
    out = []
    if not rules(ws):
        return out
    ts = (now or datetime.now(timezone.utc)).timestamp()
    for w in ws.wells():
        wid = w['id']
        try:
            if ts - os.path.getmtime(SCH.source_path(ws, wid)) > DY.LIVE_SOURCE_S:
                continue
            r = well(ws, wid)
        except Exception:
            continue
        if not DY.is_live(r, now):
            continue
        for x in r['rules']:
            for d in x['departures']:
                if r['t_last'] - d['t'] <= LIVE_EVENT_S:
                    out.append({'event': EVENT, 'key': f"{EVENT}:{wid}:{x['id']}:{d['time']}", 'well_id': wid, 'well': r['display'], 'time': d['time'], 'depth_ft': d.get('depth_ft'),
                                'detail': f"a {d['what']} did not meet {x['id']} ({x['rule']})" + (f": {_u(d['measured'], x['unit'])}" if x['sense'] != 'yes' and d.get('measured') is not None else '')})
    return out


# -- a made record, and the self-test -----------------------------------------------------------------------------------------------

def made_record(stands: int = 12, circ_s=(60,), passes=(1,), ramp_s=(30,), off_ft=(30.0,), pumps_on_slips=(), held_s: float = 0.0, trip_after: Optional[int] = 8, flow_check_min: float = 12.0,
                circ_min: float = 25.0, pull_ft_min: float = 120.0, run_ft_min: float = 90.0, ends_now: bool = False) -> dict:
    """A one-second record of stands of 90 ft at 180 ft/h whose connections are made a known way: after each stand the
    bit is picked up `off_ft`, reamed down and up `passes` times (30 ft a pass), held until the pumps have run at
    least `circ_s` seconds off bottom, the pumps stopped (not at the stands in `pumps_on_slips`), the slips set for
    three minutes, the pumps brought up over `ramp_s` seconds, and the bit run back to bottom. Each of circ_s, passes,
    ramp_s and off_ft is taken in turn, stand by stand. With `held_s`, the pump rate goes on reading its last figure
    that long after the pumps stop, as a recorder that works it out from the strokes does, while the standpipe
    pressure falls at once. Stand
    `trip_after` ends in a trip and not a connection: `circ_min` minutes circulating, `flow_check_min` with the pumps
    off and the pipe still, 2,000 ft out at `pull_ft_min` and back in at `run_ft_min`. `ends_now` dates it so that its
    last sample is now. SIMULATION."""
    cols: Dict[str, list] = {k: [] for k in ('hole', 'bit', 'hookload', 'block', 'flow', 'spp', 'rpm', 'drill')}
    S = {'hole': 9000.0, 'bit': 9000.0}
    BW, Q = 40.0, 600.0

    def add(n, bit, hl, flow, rpm, drill=False, hole=None):
        n = int(round(n))
        if n <= 0:
            return
        b = np.full(n, float(bit)) if np.isscalar(bit) else np.asarray(bit, dtype=np.float64)
        h = np.full(n, S['hole']) if hole is None else np.asarray(hole, dtype=np.float64)
        f = np.full(n, float(flow)) if np.isscalar(flow) else np.asarray(flow, dtype=np.float64)
        cols['hole'].append(h); cols['bit'].append(b); cols['hookload'].append(np.full(n, float(hl))); cols['block'].append(60.0 - (b - b[0]) if n else b)
        cols['flow'].append(f); cols['spp'].append(f * 4.5); cols['rpm'].append(np.full(n, float(rpm))); cols['drill'].append(np.full(n, bool(drill)))
        S['bit'] = float(b[-1]); S['hole'] = float(h[-1])

    def move(to, ft_s, hl, flow, rpm):
        n = max(1, int(round(abs(to - S['bit']) / ft_s)))
        add(n, S['bit'] + (to - S['bit']) * (np.arange(n) + 1) / n, hl, flow, rpm)
    rate = 180.0 / 3600.0
    truth = []
    for k in range(stands):
        n = int(90.0 / rate); h = S['hole'] + rate * (np.arange(n) + 1)
        add(n, h, 180.0, Q, 100.0, True, h)
        if k == stands - 1:
            break
        bottom = S['hole']
        if trip_after is not None and k == trip_after:                                           # this stand ends in the trip, not in a connection
            move(bottom - 30.0, 1.0, 195.0, Q, 60.0)
            add(circ_min * 60.0 - 30.0, S['bit'], 190.0, Q, 60.0)                                # circulating off bottom
            add(flow_check_min * 60.0, S['bit'], 190.0, 0.0, 0.0)                                # the pumps off, the pipe still
            for _ in range(20):                                                                  # twenty stands out
                move(S['bit'] - 100.0, pull_ft_min / 60.0, 200.0, 0.0, 0.0)
                add(60, S['bit'], BW, 0.0, 0.0)
            add(600, S['bit'], 195.0, 0.0, 0.0)
            for _ in range(20):
                add(60, S['bit'], BW, 0.0, 0.0)
                move(S['bit'] + 100.0, run_ft_min / 60.0, 185.0, 0.0, 0.0)
            add(60, S['bit'], 190.0, Q * (np.arange(60) + 1) / 60.0, 0.0)
            move(bottom, 1.0, 185.0, Q, 100.0)
            continue
        cs, ps, rp, of = (x[k % len(x)] for x in (circ_s, passes, ramp_s, off_ft))
        t_circ = 0
        move(bottom - of, 1.0, 195.0, Q, 100.0); t_circ += int(round(of))                        # picked up, pumps and rotary on
        for _ in range(int(ps)):                                                                 # a pass: down 30 ft and back up, at 1 ft/s
            down = min(S['bit'] + 30.0, bottom - 1.0)
            t_circ += 2 * int(round(down - S['bit']))
            move(down, 1.0, 185.0, Q, 100.0)
            move(bottom - of, 1.0, 195.0, Q, 100.0)
        hold = max(int(cs) - t_circ, 0)
        add(hold, S['bit'], 190.0, Q, 100.0)
        on_slips = k in pumps_on_slips
        if not on_slips:
            add(10, S['bit'], 190.0, 0.0, 0.0)                                                   # the pumps and the rotary stopped, the weight still on the hook
        add(180, S['bit'], BW, Q if on_slips else 0.0, 0.0)                                      # in the slips
        if on_slips:
            add(5, S['bit'], 190.0, Q, 0.0)
        else:
            add(5, S['bit'], 190.0, 0.0, 0.0)
            add(int(rp), S['bit'], 190.0, Q * (np.arange(int(rp)) + 1) / max(int(rp), 1), 0.0)   # the pumps brought up
        move(bottom, 1.0, 185.0, Q, 100.0)
        truth.append({'stand': k, 'circ_before_s': float(t_circ + hold), 'ream_passes': int(ps), 'pump_ramp_s': None if on_slips else float(rp), 'off_bottom_ft': None if on_slips else float(of),
                      'pumps_off_at_slips': not on_slips})
    arr = {k: np.concatenate(v) for k, v in cols.items()}
    if held_s > 0:                                                                               # the rate held after each stop; the pressure is not
        f = arr['flow']; stops = np.flatnonzero((f[1:] <= 0.0) & (f[:-1] > RS.P['flow_gpm'])) + 1
        for i in stops:
            z = i
            while z < len(f) and z - i < int(held_s) and f[z] <= 0.0:
                z += 1
            f[i:z] = f[i - 1]
    drilling = arr.pop('drill').astype(bool)
    arr['diff'] = np.zeros(len(drilling))
    t0 = (datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(seconds=len(drilling))) if ends_now else datetime(2026, 9, 1, 6, 0, 0)
    return {'t': np.arange(len(drilling), dtype=np.float64), 't0': t0, 'ch': arr, 'drilling': drilling, 'block_weight': BW, 'truth': truth,
            'trip': {'flow_check_min': flow_check_min, 'circ_min': circ_min, 'pull_ft_min': pull_ft_min, 'run_ft_min': run_ft_min}}


def analyse_record(rec: dict, rules_: Optional[List[dict]] = None) -> dict:
    rs = RS.analyse_arrays(rec['t'], rec['t0'], rec['ch'], rec['drilling'], rec['ch']['hole'], rec.get('block_weight'))
    a = analyse_arrays(rec['t'], rec['t0'], rec['ch'], rs, rules_ or [])
    a['_conns'] = measure_connections(rec['t'], rec['ch'], rs); a['_trips'] = measure_trips(rec['t'], rec['ch'], rs)
    return a


def selftest() -> dict:
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    rec = made_record(stands=12, circ_s=(60, 120, 45), passes=(1, 2, 0), ramp_s=(30, 8, 60), off_ft=(30.0, 40.0, 30.0), pumps_on_slips=(4,))
    mk = lambda kind, value=None, **kw: {'id': kw.pop('id', 'R1'), **check_rule(kind, value, source='the drilling program (test)', **kw)}
    rl = [mk('conn.circulate_before_s', 60, id='R1'), mk('conn.ream_passes', 1, id='R2'), mk('conn.pumps_off_before_slips', id='R3'), mk('conn.pump_ramp_s', 20, id='R4'),
          mk('conn.w2w_max_min', 6.0, id='R5'), mk('trip.flow_check_min', 10, id='R6'), mk('trip.max_speed_ft_min', 100, id='R7'), mk('trip.circulate_before_min', 30, id='R8'),
          mk('conn.off_bottom_ft', 35, id='R9')]
    a = analyse_record(rec, rl)
    C = a['_conns']; T = rec['truth']; R = {x['id']: x for x in a['rules']}
    n = len(T)
    ck("every connection made is found: ten, the eleventh break between twelve stands being the trip", a['connections'] == 10 == n)
    ck("the seconds circulated off bottom before the slips are the seconds the record was made with, to three",
       all(abs(C[i]['circ_before_s'] - T[i]['circ_before_s']) <= 3.0 for i in range(n)))
    ck("the reaming passes are counted as made: one, two, none, in turn", [c['ream_passes'] for c in C] == [t_['ream_passes'] for t_ in T])
    ck("the one connection made with the pumps on is the fifth, and it has no ramp and no off-bottom distance",
       [c['pumps_off_at_slips'] for c in C] == [t_['pumps_off_at_slips'] for t_ in T] and C[4]['pump_ramp_s'] is None and C[4]['off_bottom_ft'] is None)
    ck("the pumps' ramp, from 50 gal/min to nine tenths of the drilling rate, is that share of the ramp the record was made with (30, 8 and 60 s in turn), to three seconds",
       all(abs(C[i]['pump_ramp_s'] - (RAMP_SHARE - RS.P['flow_gpm'] / 600.0) * T[i]['pump_ramp_s']) <= 3.0 for i in range(n) if T[i]['pump_ramp_s'] is not None))
    ck("the bit off bottom when the pumps stopped is the distance picked up: 30, 40, 30 ft",
       all(abs(C[i]['off_bottom_ft'] - T[i]['off_bottom_ft']) <= 1.0 for i in range(n) if T[i]['off_bottom_ft'] is not None))
    held = analyse_record(made_record(stands=12, circ_s=(60, 120, 45), passes=(1, 2, 0), ramp_s=(30, 8, 60), off_ft=(30.0, 40.0, 30.0), pumps_on_slips=(4,), held_s=40.0), rl)
    H = held['_conns']; ht = next(x for x in held['_trips'] if x['direction'] == 'OUT')
    ck("a pump rate that reads on for forty seconds after the pumps stop changes nothing read: the standpipe pressure says when they stopped",
       len(H) == n and all(abs(H[i]['circ_before_s'] - C[i]['circ_before_s']) <= 2.0 and H[i]['pumps_off_at_slips'] == C[i]['pumps_off_at_slips']
                           and (H[i]['off_bottom_ft'] is None) == (C[i]['off_bottom_ft'] is None) and abs((H[i]['off_bottom_ft'] or 0.0) - (C[i]['off_bottom_ft'] or 0.0)) <= 1.0 for i in range(n))
       and abs(ht['flow_check_min'] - 12.0) <= 0.6 and [x['met'] for x in held['rules']] == [x['met'] for x in a['rules']])
    tr = a['_trips']
    out_ = next(x for x in tr if x['direction'] == 'OUT'); in_ = next(x for x in tr if x['direction'] == 'IN')
    ck("before the trip out: twenty-five minutes circulated and a twelve-minute flow check, each to half a minute",
       abs(out_['circ_before_min'] - 25.0) <= 0.6 and abs(out_['flow_check_min'] - 12.0) <= 0.6)
    ck("the pipe was pulled at 120 ft/min and run at 90, each to five", abs(out_['speed_ft_min'] - 120.0) <= 5.0 and abs(in_['speed_ft_min'] - 90.0) <= 5.0)
    ck("the rules are counted as the record was made: 60 s met where 60 or 120 were circulated, one pass where one or two were made",
       (R['R1']['met'], R['R1']['not_met']) == (sum(1 for t_ in T if t_['circ_before_s'] >= 60), sum(1 for t_ in T if t_['circ_before_s'] < 60))
       and (R['R2']['met'], R['R2']['not_met']) == (sum(1 for t_ in T if t_['ream_passes'] >= 1), sum(1 for t_ in T if t_['ream_passes'] < 1)))
    ck("pumps off before the slips: nine of ten; a ramp of 20 s: not met where it was 8, not read where the pumps never stopped",
       (R['R3']['met'], R['R3']['not_met']) == (9, 1) and R['R4']['not_read'] == 1 and R['R4']['not_met'] == sum(1 for t_ in T if t_['pump_ramp_s'] == 8)
       and (R['R5']['met'], R['R5']['not_met']) == (sum(1 for c in C if c['w2w_min'] <= 6.0), sum(1 for c in C if c['w2w_min'] > 6.0)) and 0 < R['R5']['not_met'] < n)
    ck("the trip: a ten-minute flow check met, thirty minutes circulating not met, 100 ft/min met running in and not pulling out",
       (R['R6']['met'], R['R6']['not_met']) == (1, 0) and (R['R8']['met'], R['R8']['not_met']) == (0, 1) and (R['R7']['met'], R['R7']['not_met']) == (1, 1)
       and R['R7']['worst'][0]['what'] == 'trip out')
    ck("the furthest from a rule is listed first: the 45 s connections under the 60 s rule", R['R1']['worst'][0]['measured'] <= 48.0 and R['R9']['not_met'] == sum(1 for t_ in T if t_['off_bottom_ft'] == 30.0))
    bad = 0
    for args in (('conn.circulate_before_s', 1), ('conn.ream_passes', 'two'), ('nothing', 5), ('trip.max_speed_ft_min', 5000)):
        try:
            check_rule(args[0], args[1], source='x')
        except ProcedureError:
            bad += 1
    try:
        check_rule('conn.w2w_max_min', 10, source='')
    except ProcedureError:
        bad += 1
    ck("a figure out of bounds, a word for a number, a rule that is not one and a rule with no source are each turned away", bad == 5)
    scoped = analyse_record(rec, [mk('conn.circulate_before_s', 60, from_md_ft=9500.0, id='S1'), mk('trip.max_speed_ft_min', 100, direction='in', id='S2')])
    S1, S2 = scoped['rules']
    ck("a rule kept to a stretch of hole covers the connections in it and no others; a speed rule for trips in does not judge the trip out",
       S1['covered'] == sum(1 for c in C if c['depth_ft'] >= 9500.0) < n and (S2['met'], S2['not_met']) == (1, 0))
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'a made record: twelve stands and a trip, each connection made a known way'}
