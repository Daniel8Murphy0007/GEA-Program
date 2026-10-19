"""Rig time: what the rig was doing each second of the drilling record, its connections and trips, and the time lost
against the pad's own best.

A daily drilling report says how the rig spent the day in the crew's words, to the quarter hour. The drilling
recorder says it second by second, in the hook load, the block, the bit, the pumps and the rotary. This module reads
the second account:

- the rig state of every sample, from the channels a recorder carries: IN SLIPS (the hook load below the slips
  threshold - the travelling block's own weight and half again, the rule rig-state software uses); ROTARY DRILLING
  and SLIDE DRILLING (the record's own drilling samples, the rotary turning or not); REAMING and WASHING (off bottom,
  pumping, the bit moving, with or without the rotary); CIRCULATING (pumping, the bit still); TRIPPING IN and
  TRIPPING OUT (the pumps off, the bit moving down or up); STATIONARY (none of these); and NO RECORD for the gaps;
- the drilling connections: each break between two stretches of drilling, an hour or less, with the string set in
  the slips at least once and the bit kept within a stand of bottom - timed weight to weight (the last second
  drilling to the first second drilling again), with its three parts: off bottom to slips set (pre-connection),
  in the slips (slips set to slips pulled, the first set to the last pull), and slips pulled to drilling (post-
  connection); the hook load below the threshold for half an hour or more is OFF THE HOOK (the string hung off, or
  no string: rigging, casing, waiting), not a connection's slips;
- the trips: each run of tripping and of the slips between its stands, the pumps off, that moves the bit 1,000 ft
  or more - its direction, its footage, its hours handling pipe (the pipe moving and the stands in the slips) and its
  speed over them in ft/h, the speed with the pipe moving alone, the breaks inside it (circulating, filling pipe,
  waiting - the elapsed hours less the pipe-handling hours), and its connections in the slips;
- the time lost: each connection's weight to weight past the target, and each trip's pipe-handling hours past its
  footage at the target speed (the breaks inside a trip are reported, not counted as lost), summed per report day and per well. The target is the operator's declared value where there is
  one, and otherwise the pad's own best quarter - the 25th percentile of every connection on the pad, the 75th of
  every trip's speed in its direction: "invisible lost time", the shortfall against a target the crews have
  already met here, which a daily report does not show. With the contract's operating rate declared (the invoice
  check's terms), the hours are priced at it.

What it will not call a measurement: the cause of a long connection (a survey, a washout, a crew); a target
beyond what this pad has done; the slips themselves - a hook load threshold sees the weight leave the hook, and a
recorder whose block weight differs from the estimate moves every slips time; the estimate (from the hook load while
the block moves and the bit does not) is printed with each well.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

STATES = ('ROTARY DRILLING', 'SLIDE DRILLING', 'REAMING', 'WASHING', 'CIRCULATING', 'TRIPPING IN', 'TRIPPING OUT', 'IN SLIPS', 'OFF THE HOOK', 'STATIONARY',
          'NO RECORD')
ROT, SLIDE, REAM, WASH, CIRC, TIN, TOUT, SLIPS, OFFHOOK, STAT, NOREC = range(11)
P = {'flow_gpm': 50.0, 'spp_psi': 100.0, 'rpm': 10.0, 'move_ft_s': 0.2, 'jump_ft_s': 6.0, 'off_hook_min': 30.0, 'slips_factor': 1.5, 'slips_min_s': 30, 'smooth_s': 5,
     'conn_max_min': 60.0, 'conn_near_bottom_ft': 200.0, 'drill_merge_s': 20, 'trip_min_ft': 1000.0, 'trip_break_s': 600, 'gap_s': 60.0}
SOURCES = ('Rig-state rules as rig-state software states them (Innova Well Seeker rig states guide: in slips when the hook load is below the '
           'block weight and half again; on bottom by hole depth less bit depth; pumps by pressure or flow; rotating by RPM); connection '
           'and tripping KPIs and invisible lost time as Drilling Contractor reports them (Deconstructing invisible lost time, Bakken: '
           'weight-to-weight drilling connections 6.6-13.2 min, cased-hole tripping 2,100-3,400 ft/h, ILT the shortfall against a target)')
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws) -> str:
    return os.path.join(ws.path, 'drillstring', 'rig_time.json')


def settings(ws) -> dict:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'targets': {}, 'block_weight_klbf': {}}


def set_settings(ws, actor: str, source: str = '', w2w_min=None, trip_in_ft_h=None, trip_out_ft_h=None, block_weight: Optional[Tuple[str, object]] = None) -> dict:
    """Declare targets (weight-to-weight minutes, tripping speeds in and out, ft/h) with their source, or a well's block
    weight (klbf) where the estimate is wrong. An empty value removes a target."""
    s = settings(ws)
    tg = s.setdefault('targets', {})
    for k, v in (('w2w_min', w2w_min), ('trip_in_ft_h', trip_in_ft_h), ('trip_out_ft_h', trip_out_ft_h)):
        if v is None:
            continue
        if v == '':
            tg.pop(k, None); continue
        x = float(v)
        if not math.isfinite(x) or x <= 0:
            raise ValueError(f'{k} must be more than zero')
        tg[k] = x
    if any(v not in (None, '') for v in (w2w_min, trip_in_ft_h, trip_out_ft_h)):
        if not (source or '').strip():
            raise ValueError('a target needs where it came from (e.g. "drilling program 2026, section 4")')
        s['targets_source'] = source.strip()
    if block_weight:
        wid, v = block_weight
        ws.well(wid)
        if v in ('', None):
            s.setdefault('block_weight_klbf', {}).pop(wid, None)
        else:
            x = float(v)
            if not math.isfinite(x) or x <= 0:
                raise ValueError('the block weight must be more than zero')
            s.setdefault('block_weight_klbf', {})[wid] = x
    s['by'], s['utc'] = actor, _utc()
    os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
    with open(_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)
    os.replace(_path(ws) + '.tmp', _path(ws))
    with _LOCK:
        _MEMO.clear()
    ws.audit(actor, 'rig_time.settings', {k: s.get(k) for k in ('targets', 'targets_source', 'block_weight_klbf')})
    return s


# -- the arithmetic ------------------------------------------------------------------------------------------------------
def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    if not len(mask):
        return []
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def _clean(mask: np.ndarray, t: np.ndarray, min_s: float, fill_s: float) -> np.ndarray:
    """Close gaps shorter than fill_s, then drop runs shorter than min_s."""
    m = mask.copy()
    for a, b in _runs(~m):
        if 0 < a and b < len(m) and t[b] - t[a - 1] <= fill_s:
            m[a:b] = True
    for a, b in _runs(m):
        if t[b - 1] - t[a] < min_s:
            m[a:b] = False
    return m


def _rate(x: np.ndarray, t: np.ndarray, w: float) -> np.ndarray:
    """d x / d t over a window of w seconds centred on each sample."""
    lo = np.searchsorted(t, t - w / 2.0)
    hi = np.minimum(np.searchsorted(t, t + w / 2.0, 'right') - 1, len(t) - 1)
    dt = t[hi] - t[lo]
    return np.where(dt > 0, (x[hi] - x[lo]) / np.where(dt > 0, dt, 1.0), 0.0)


def classify(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]], drilling: np.ndarray, block_weight: Optional[float] = None) -> dict:
    """The rig state of every sample. `ch`: hookload (klbf), block (ft), bit (ft), hole (ft), flow (gal/min), spp (psi),
    rpm. Pure: the same arrays give the same states."""
    n = len(t)
    nan0 = lambda a: np.nan_to_num(np.asarray(a, dtype=np.float64)) if a is not None else None
    hl, blk, bit, hole, flow, spp, rpm = (nan0(ch.get(k)) for k in ('hookload', 'block', 'bit', 'hole', 'flow', 'spp', 'rpm'))
    pumps = (flow > P['flow_gpm']) if flow is not None else ((spp > P['spp_psi']) if spp is not None else np.zeros(n, bool))
    rot = (rpm > P['rpm']) if rpm is not None else np.zeros(n, bool)
    vbit = _rate(bit, t, P['smooth_s']) if bit is not None else np.zeros(n)
    vblk = _rate(blk, t, P['smooth_s']) if blk is not None else None
    bit_moving = (np.abs(vbit) > P['move_ft_s']) & (np.abs(vbit) < P['jump_ft_s'])     # faster than a block travels is the depth re-set, not the pipe
    how = []
    est = None
    if hl is not None:
        if block_weight:
            bw, how_bw = float(block_weight), f'the block weight declared, {block_weight:g} klbf'
        else:
            sel = (np.abs(vblk) > P['move_ft_s']) & ~bit_moving & ~pumps if vblk is not None else np.zeros(n, bool)
            cand = hl[sel & (hl > 0)]
            if len(cand) >= 120:
                bw = float(np.median(cand)); est = bw
                how_bw = f'the block weight estimated at {bw:.1f} klbf: the median hook load while the block moved and the bit did not ({len(cand):,} samples)'
            else:
                pos = hl[hl > 0]
                bw = float(np.percentile(pos, 2)) if len(pos) else 0.0; est = bw
                how_bw = f'the block weight taken as the 2nd percentile of the hook load, {bw:.1f} klbf (too few samples with the block moving and the bit still)'
        thr = bw * P['slips_factor']
        slips = hl < thr
        how.append(f'in the slips: hook load below {thr:.1f} klbf - {how_bw}, and half again')
    elif vblk is not None and bit is not None:
        slips = (np.abs(vblk) > P['move_ft_s']) & ~bit_moving
        thr, bw = None, None
        how.append('in the slips: no hook load in the record, so the block moving with the bit still')
    else:
        slips = np.zeros(n, bool); thr = bw = None
        how.append('in the slips: not detectable - the record has neither the hook load nor the block')
    slips = _clean(slips & ~drilling & ~bit_moving, t, P['slips_min_s'], 3.0)      # in the slips the bit does not move: a light string pushed into a lateral is not in the slips
    st = np.full(n, STAT, dtype=np.int8)
    st[pumps & ~bit_moving] = CIRC
    st[pumps & bit_moving & rot] = REAM
    st[pumps & bit_moving & ~rot] = WASH
    st[~pumps & bit_moving & (vbit > 0)] = TIN
    st[~pumps & bit_moving & (vbit < 0)] = TOUT
    st[slips] = SLIPS
    for a, b in _runs(slips):                      # the weight off the hook for half an hour or more is not a connection's slips
        if t[b - 1] - t[a] >= P['off_hook_min'] * 60.0:
            st[a:b] = OFFHOOK
    st[drilling & rot] = ROT
    st[drilling & ~rot] = SLIDE
    return {'state': st, 'slips': slips, 'pumps': pumps, 'rot': rot, 'block_weight_klbf': bw, 'block_weight_estimated': est is not None,
            'slips_threshold_klbf': thr, 'how': how}


def _dt(t: np.ndarray) -> np.ndarray:
    d = np.diff(t, append=t[-1] + (t[-1] - t[-2] if len(t) > 1 else 1.0))
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    return np.where(d > P['gap_s'], cad, d)


def connections(t: np.ndarray, st: np.ndarray, drilling: np.ndarray, slips: np.ndarray, bit: Optional[np.ndarray], bottom: Optional[np.ndarray]) -> Tuple[List[dict], List[dict]]:
    """Drilling connections between stretches of drilling, and the breaks that are not connections."""
    drl = _clean(drilling, t, 0.0, P['drill_merge_s'])
    runs = _runs(drl)
    conns, other = [], []
    for (a0, b0), (a1, b1) in zip(runs, runs[1:]):
        g0, g1 = b0, a1                                  # the samples between the two drilling stretches
        w2w = (t[a1] - t[b0 - 1]) / 60.0
        if w2w <= 0:
            continue
        gap_rec = np.any(np.diff(t[b0 - 1:a1 + 1]) > P['gap_s'])
        sr = [(a, b) for a, b in _runs(slips[g0:g1])]
        near = True
        if bit is not None and bottom is not None and g1 > g0:
            off = np.nan_to_num(bottom[g0:g1]) - np.nan_to_num(bit[g0:g1])
            near = float(np.max(off)) <= P['conn_near_bottom_ft']
        if w2w <= P['conn_max_min'] and sr and near and not gap_rec:
            s_set, s_pull = g0 + sr[0][0], g0 + sr[-1][1] - 1
            conns.append({'t_off': float(t[b0 - 1]), 'w2w_min': round(float(w2w), 2), 'pre_min': round(float(t[s_set] - t[b0 - 1]) / 60.0, 2),
                          'slips_min': round(float(t[s_pull] - t[s_set]) / 60.0, 2), 'post_min': round(float(t[a1] - t[s_pull]) / 60.0, 2),
                          'n_slips': len(sr), 'depth_ft': round(float(np.nan_to_num(bottom[b0 - 1])), 1) if bottom is not None else None})
        else:
            why = ('a gap in the record' if gap_rec else f'longer than {P["conn_max_min"]:g} min' if w2w > P['conn_max_min']
                   else 'the bit went more than a stand off bottom' if not near else 'the string was not set in the slips')
            other.append({'t_off': float(t[b0 - 1]), 'minutes': round(float(w2w), 1), 'why': why})
    return conns, other


def _legs(b: np.ndarray, idx: np.ndarray, turn_ft: float) -> List[Tuple[int, int]]:
    """Split the bit's path over the sample positions `idx` into legs at each reversal of turn_ft or more (a zig-zag
    filter: a pipe reciprocated a few feet does not end a trip)."""
    if len(idx) < 2:
        return []
    legs, start, ext, d = [], 0, 0, 0
    for k in range(1, len(idx)):
        v, ve = b[idx[k]], b[idx[ext]]
        if d == 0:
            if abs(v - b[idx[start]]) >= turn_ft:
                d = 1 if v > b[idx[start]] else -1; ext = k
            elif abs(v - b[idx[start]]) < 1e-9:
                pass
            continue
        if (v - ve) * d > 0:
            ext = k
        elif abs(v - ve) >= turn_ft:
            legs.append((start, ext)); start, ext, d = ext, k, -d
    legs.append((start, ext if d else len(idx) - 1))
    return [(idx[a], idx[z]) for a, z in legs if z > a]


def trips(t: np.ndarray, st: np.ndarray, slips: np.ndarray, pumps: np.ndarray, bit: Optional[np.ndarray], bottom: Optional[np.ndarray], dtv: np.ndarray) -> List[dict]:
    """Runs of tripping and the slips between stands, the pumps off, broken by anything else longer than ten minutes,
    and split where the bit turns back 500 ft or more; each leg of 1,000 ft or more is a trip."""
    if bit is None:
        return []
    b = np.nan_to_num(bit)
    base = np.isin(st, (TIN, TOUT)) | (slips & ~pumps)
    if bottom is not None:
        base &= (np.nan_to_num(bottom) - b) > P['conn_near_bottom_ft'] * 0.5
    tripping = _clean(base, t, 60.0, P['trip_break_s'])
    out = []
    for a, z in _runs(tripping):
        mv = a + np.flatnonzero(np.isin(st[a:z], (TIN, TOUT)))
        for ia, iz in _legs(b, mv, 500.0):
            ft = float(b[iz] - b[ia])
            if abs(ft) < P['trip_min_ft']:
                continue
            elapsed = float(dtv[ia:iz + 1].sum() / 3600.0)
            mv_h = float((dtv[ia:iz + 1] * np.isin(st[ia:iz + 1], (TIN, TOUT))).sum() / 3600.0)
            hrs = float((dtv[ia:iz + 1] * (np.isin(st[ia:iz + 1], (TIN, TOUT)) | (st[ia:iz + 1] == SLIPS))).sum() / 3600.0)
            sl = _runs(slips[ia:iz + 1])
            sl_min = [float(t[ia + y - 1] - t[ia + x]) / 60.0 for x, y in sl]
            out.append({'t_start': float(t[ia]), 't_end': float(t[iz]), 'direction': 'IN' if ft > 0 else 'OUT', 'from_ft': round(float(b[ia]), 0),
                        'to_ft': round(float(b[iz]), 0), 'feet': round(abs(ft), 0), 'hours': round(hrs, 3), 'elapsed_hours': round(elapsed, 3),
                        'breaks_hours': round(elapsed - hrs, 3),
                        'speed_ft_h': round(abs(ft) / hrs, 0) if hrs > 0 else None, 'moving_speed_ft_h': round(abs(ft) / mv_h, 0) if mv_h > 0 else None,
                        'connections': len(sl), 'median_slips_min': round(float(np.median(sl_min)), 2) if sl_min else None})
    return out


def analyse_arrays(t: np.ndarray, t0: datetime, ch: Dict[str, Optional[np.ndarray]], drilling: np.ndarray, bottom: Optional[np.ndarray],
                   block_weight: Optional[float] = None) -> dict:
    """Everything for one well's record, from its arrays (t in seconds from t0, the recorder's clock)."""
    t = np.asarray(t, dtype=np.float64)
    drilling = np.asarray(drilling, dtype=bool)
    c = classify(t, ch, drilling, block_weight)
    st, slips, pumps = c['state'], c['slips'], c['pumps']
    dtv = _dt(t)
    hours = {STATES[k]: round(float(dtv[st == k].sum() / 3600.0), 3) for k in range(len(STATES) - 1)}
    bit = np.asarray(ch['bit'], dtype=np.float64) if ch.get('bit') is not None else None
    bot = np.asarray(bottom, dtype=np.float64) if bottom is not None else None
    conns, other = connections(t, st, drilling, slips, bit, bot)
    tr = trips(t, st, slips, pumps, bit, bot, dtv)
    for x in conns:
        x['time'] = (t0 + timedelta(seconds=x['t_off'])).isoformat(timespec='seconds')
    for x in other:
        x['time'] = (t0 + timedelta(seconds=x['t_off'])).isoformat(timespec='seconds')
    for x in tr:
        x['start'] = (t0 + timedelta(seconds=x['t_start'])).isoformat(timespec='seconds')
        x['end'] = (t0 + timedelta(seconds=x['t_end'])).isoformat(timespec='seconds')
    foot = {}
    if bot is not None:
        db = np.diff(np.nan_to_num(bot), prepend=np.nan_to_num(bot[0]))
        db = np.where((db > 0) & (db < 50), db, 0.0)
        foot = {'rotary_ft': round(float(db[st == ROT].sum()), 1), 'slide_ft': round(float(db[st == SLIDE].sum()), 1)}
        for k, s_ in (('rotary', 'ROTARY DRILLING'), ('slide', 'SLIDE DRILLING')):
            foot[f'{k}_rop_ft_h'] = round(foot[f'{k}_ft'] / hours[s_], 1) if hours[s_] > 0 else None
    return {'t0': t0.isoformat(), 'samples': int(len(t)), 'record_hours': round(float(dtv.sum() / 3600.0), 3), 'hours': hours, 'connections': conns,
            'breaks': other, 'trips': tr, 'footage': foot, 'block_weight_klbf': c['block_weight_klbf'], 'block_weight_estimated': c['block_weight_estimated'],
            'slips_threshold_klbf': c['slips_threshold_klbf'], 'how': c['how'], '_state': st, '_t': t, '_dtv': dtv}


def _stats(xs: List[float]) -> Optional[dict]:
    if not xs:
        return None
    a = np.asarray(xs, dtype=np.float64)
    return {'n': int(len(a)), 'median': round(float(np.median(a)), 2), 'p25': round(float(np.percentile(a, 25)), 2), 'p75': round(float(np.percentile(a, 75)), 2),
            'mean': round(float(a.mean()), 2), 'total': round(float(a.sum()), 2)}


# -- the workspace -----------------------------------------------------------------------------------------------------
_CH = {'hookload': 'Hook Load', 'block': 'Block Height', 'bit': 'Bit Depth', 'hole': 'Hole Depth', 'flow': 'Total Pump Output', 'spp': 'Standpipe Pressure', 'rpm': 'Rotary RPM'}


def well(ws, well_id: str) -> dict:
    """One well's rig time from its chart cache (built on first use)."""
    from . import drill_string as DS
    c = DS._cache(ws, well_id, build=True)
    bw = settings(ws).get('block_weight_klbf', {}).get(well_id)
    key = (well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'), bw)
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    ch = {k: (np.asarray(c.channel(v), dtype=np.float64) if v in c._ch else None) for k, v in _CH.items()}
    bottom = c.extra('bottom')
    r = analyse_arrays(np.asarray(c.index, dtype=np.float64), t0, ch, np.asarray(c.extra('drilling'), dtype=bool),
                       np.asarray(bottom, dtype=np.float64) if bottom is not None else None, bw)
    w = ws.well(well_id)
    r.update({'well_id': well_id, 'display': w.get('display') or well_id, 'clock': "the drilling recorder's own clock",
              'channels': [v for k, v in _CH.items() if ch[k] is not None]})
    with _LOCK:
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = r
    return r


def _wells_with_record(ws) -> List[str]:
    from . import drill_string as DS
    out = []
    for w in ws.wells():
        try:
            DS._cache(ws, w['id'], build=True)
            out.append(w['id'])
        except (KeyError, OSError, ValueError):
            continue
    return out


def targets(ws, rs: List[dict]) -> dict:
    """The targets: declared where declared, else the pad's best quarter."""
    s = settings(ws)
    tg = s.get('targets', {})
    w2w = [c['w2w_min'] for r in rs for c in r['connections']]
    tin = [x['speed_ft_h'] for r in rs for x in r['trips'] if x['direction'] == 'IN' and x['speed_ft_h']]
    tout = [x['speed_ft_h'] for r in rs for x in r['trips'] if x['direction'] == 'OUT' and x['speed_ft_h']]
    out = {}
    for k, vals, q, unit in (('w2w_min', w2w, 25, 'min'), ('trip_in_ft_h', tin, 75, 'ft/h'), ('trip_out_ft_h', tout, 75, 'ft/h')):
        if k in tg:
            out[k] = {'value': tg[k], 'how': f"declared ({s.get('targets_source', '')})", 'unit': unit}
        elif len(vals) >= 4:
            out[k] = {'value': round(float(np.percentile(vals, q)), 2), 'how': f"this pad's best quarter: the {q}th percentile of {len(vals)}", 'unit': unit}
        else:
            out[k] = None
    return out


def _lost(r: dict, tg: dict) -> None:
    """Each connection's and trip's time past the target, in hours, on the item."""
    w = (tg.get('w2w_min') or {}).get('value')
    for c in r['connections']:
        c['lost_h'] = round(max(0.0, c['w2w_min'] - w) / 60.0, 4) if w else None
    for x in r['trips']:
        s = (tg.get('trip_in_ft_h' if x['direction'] == 'IN' else 'trip_out_ft_h') or {}).get('value')
        x['lost_h'] = round(max(0.0, x['hours'] - x['feet'] / s), 4) if s else None


def by_day(r: dict, day_start_h: float = 6.0) -> List[dict]:
    """Per report day (the recorder's clock, from day_start_h): hours by state, connections, trips, time lost."""
    t0 = datetime.fromisoformat(r['t0'])
    t, st, dtv = r['_t'], r['_state'], r['_dtv']
    base = datetime(t0.year, t0.month, t0.day) + timedelta(hours=day_start_h)
    off = (t0 - base).total_seconds()
    k = np.floor((t + off) / 86400.0).astype(np.int64)
    days = {}
    for d in np.unique(k):
        m = k == d
        key = (base + timedelta(days=int(d))).date().isoformat()
        days[key] = {'date': key, 'hours': {STATES[s]: round(float(dtv[m & (st == s)].sum() / 3600.0), 2) for s in range(len(STATES) - 1)},
                     'connections': 0, 'w2w': [], 'trips': 0, 'lost_h': 0.0}
    dayof = lambda ts: (base + timedelta(days=int(math.floor((ts + off) / 86400.0)))).date().isoformat()
    for c in r['connections']:
        D = days.get(dayof(c['t_off']))
        if D:
            D['connections'] += 1; D['w2w'].append(c['w2w_min']); D['lost_h'] += c.get('lost_h') or 0.0
    for x in r['trips']:
        D = days.get(dayof(x['t_start']))
        if D:
            D['trips'] += 1; D['lost_h'] += x.get('lost_h') or 0.0
    out = []
    for key in sorted(days):
        D = days[key]
        D['median_w2w_min'] = round(float(np.median(D.pop('w2w'))), 2) if D['connections'] else None
        D['lost_h'] = round(D['lost_h'], 2)
        out.append(D)
    return out


def _price(ws) -> Optional[float]:
    try:
        from . import invoice_check as IC
        return (IC.terms(ws).get('rates_usd_per_day') or {}).get('operating')
    except Exception:
        return None


def _day_start(ws) -> float:
    try:
        from . import invoice_check as IC
        t = IC.terms(ws).get('day_start') or '06:00'
    except Exception:
        t = '06:00'
    h, m = (int(x) for x in t.split(':'))
    return h + m / 60.0


def pad(ws) -> dict:
    """Every well with a drilling record, the pad's targets, and each well's time lost against them."""
    rs, errs = [], []
    for wid in _wells_with_record(ws):
        try:
            rs.append(well(ws, wid))
        except (KeyError, OSError, ValueError) as e:
            errs.append(f'{wid}: {e}')
    tg = targets(ws, rs)
    op = _price(ws)
    ds = _day_start(ws)
    wells = []
    for r in rs:
        _lost(r, tg)
        lost_c = sum(c['lost_h'] or 0.0 for c in r['connections'])
        lost_t = sum(x['lost_h'] or 0.0 for x in r['trips'])
        wells.append({'well_id': r['well_id'], 'display': r['display'], 't0': r['t0'], 'record_hours': r['record_hours'], 'hours': r['hours'],
                      'connections': _stats([c['w2w_min'] for c in r['connections']]), 'pre': _stats([c['pre_min'] for c in r['connections']]),
                      'slips': _stats([c['slips_min'] for c in r['connections']]), 'post': _stats([c['post_min'] for c in r['connections']]),
                      'connection_list': r['connections'], 'breaks': r['breaks'], 'trips': r['trips'], 'footage': r['footage'],
                      'block_weight_klbf': r['block_weight_klbf'], 'block_weight_estimated': r['block_weight_estimated'], 'slips_threshold_klbf': r['slips_threshold_klbf'],
                      'how': r['how'], 'channels': r['channels'], 'lost_connections_h': round(lost_c, 2), 'lost_trips_h': round(lost_t, 2),
                      'lost_h': round(lost_c + lost_t, 2), 'lost_usd': round((lost_c + lost_t) / 24.0 * op, 2) if op else None,
                      'days': by_day(r, ds)})
    return {'wells': wells, 'targets': tg, 'operating_usd_per_day': op, 'day_start_h': ds, 'errors': errs, 'sources': SOURCES,
            'settings': settings(ws), 'states': list(STATES[:-1]),
            'totals': {'lost_h': round(sum(w['lost_h'] for w in wells), 2), 'lost_usd': (round(sum(w['lost_usd'] or 0 for w in wells), 2) if op else None),
                       'connections': sum((w['connections'] or {}).get('n', 0) for w in wells), 'trips': sum(len(w['trips']) for w in wells)},
            'not_a_measurement': ["the cause of a long connection - a survey, a washout, a crew change; the record shows the time, not the reason",
                                  "a target beyond what this pad has done: the best quarter is this pad's own, not a technical limit",
                                  "the slips themselves: a hook load threshold sees the weight leave the hook, and a block weight that differs from the estimate moves every slips time"]}


def lost_by_day(ws, well_id: str) -> Dict[str, dict]:
    """Report day -> {lost_h, connections, median_w2w_min} for the invoice check (empty when the well has no record)."""
    try:
        P_ = pad(ws)
    except Exception:
        return {}
    for w in P_['wells']:
        if w['well_id'] == well_id:
            return {d['date']: {'lost_h': d['lost_h'], 'connections': d['connections'], 'median_w2w_min': d['median_w2w_min']} for d in w['days']}
    return {}


def page(ws) -> dict:
    p = pad(ws)
    for w in p['wells']:
        w['connection_list'] = w['connection_list'][-400:]
    return p


# -- the report ----------------------------------------------------------------------------------------------------------
def _f(v, d=1):
    return '-' if v is None else f'{v:,.{d}f}'


def build_report(ws, version: str, well_id: Optional[str] = None):
    from . import client_reports as CR
    P_ = pad(ws)
    ws_ = [w for w in P_['wells'] if well_id is None or w['well_id'] == well_id]
    if well_id and not ws_:
        raise KeyError(f'{well_id} has no drilling record to read')
    now = datetime.now(timezone.utc)
    name = ws_[0]['display'] if well_id else (ws.manifest.get('name') or 'the pad')
    rid = f"RIG-{''.join(ch if ch.isalnum() else '-' for ch in name)}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    tg = P_['targets']
    lost = sum(w['lost_h'] for w in ws_)
    front = [['Report ID', rid], ['Scope', name], ['Wells', str(len(ws_))], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Time lost against the targets', f"{lost:,.1f} h" + (f" ({lost / 24.0 * P_['operating_usd_per_day']:,.2f} USD at the contract's operating rate)" if P_['operating_usd_per_day'] else '')]]
    s1 = CR.Section('1', 'The targets', [], [])
    s1.tables.append(CR.Table(['Measure', 'Target', 'How'], [[k.replace('_', ' '), (f"{v['value']:,.2f} {v['unit']}" if v else 'none'), (v['how'] if v else 'fewer than four on the pad and none declared')]
                                                              for k, v in tg.items()]))
    s1.paragraphs.append('Time lost is each drilling connection\'s weight-to-weight time past the connection target, and each trip\'s hours past its footage at the '
                         'target speed. Where no target is declared, the target is this pad\'s own best quarter.')
    s2 = CR.Section('2', 'Hours by rig state', [], [])
    s2.tables.append(CR.Table(['Well'] + P_['states'] + ['Record (h)'], [[w['display']] + [_f(w['hours'][s], 1) for s in P_['states']] + [_f(w['record_hours'], 1)] for w in ws_]))
    s3 = CR.Section('3', 'Drilling connections', [], [])
    s3.tables.append(CR.Table(['Well', 'Connections', 'Weight to weight, median (P25-P75) min', 'Pre (off bottom to slips)', 'In the slips', 'Post (slips to drilling)', 'Lost (h)'],
                              [[w['display'], (w['connections'] or {}).get('n', 0)] +
                               [(f"{x['median']:.1f} ({x['p25']:.1f}-{x['p75']:.1f})" if x else '-') for x in (w['connections'], w['pre'], w['slips'], w['post'])] + [_f(w['lost_connections_h'], 2)]
                               for w in ws_]))
    s4 = CR.Section('4', 'Trips', [], [])
    rows = [[w['display'], x['start'], x['direction'], f"{x['from_ft']:,.0f} -> {x['to_ft']:,.0f}", _f(x['hours'], 2), _f(x['speed_ft_h'], 0), _f(x['moving_speed_ft_h'], 0),
             x['connections'], _f(x['median_slips_min'], 2), _f(x['lost_h'], 2)] for w in ws_ for x in w['trips']]
    if rows:
        s4.tables.append(CR.Table(['Well', 'Start', 'Direction', 'Bit depth (ft)', 'Hours', 'Speed (ft/h)', 'Moving (ft/h)', 'Connections', 'Slips, median (min)', 'Lost (h)'], rows))
    else:
        s4.paragraphs.append('No trip of 1,000 ft or more in the record.')
    s5 = CR.Section('5', 'Day by day', [], [])
    s5.tables.append(CR.Table(['Well', 'Report day', 'Drilling (h)', 'In slips (h)', 'Tripping (h)', 'Connections', 'Median W2W (min)', 'Lost (h)'],
                              [[w['display'], d['date'], _f(d['hours']['ROTARY DRILLING'] + d['hours']['SLIDE DRILLING'], 1), _f(d['hours']['IN SLIPS'], 1),
                                _f(d['hours']['TRIPPING IN'] + d['hours']['TRIPPING OUT'], 1), d['connections'], _f(d['median_w2w_min'], 1), _f(d['lost_h'], 2)]
                               for w in ws_ for d in w['days']]))
    s6 = CR.Section('6', 'How each state was read', [f"{w['display']}: " + '; '.join(w['how']) + '.' for w in ws_] +
                    ['Not a measurement here: ' + '; '.join(P_['not_a_measurement']) + '.'], [])
    doc = CR.Document(f"Rig time - {name}", rid, front, [s1, s2, s3, s4, s5, s6], footer=SOURCES,
                      data={'report_id': rid, 'targets': tg, 'wells': [{k: v for k, v in w.items() if k != 'connection_list'} for w in ws_]})
    return doc


def write_report(ws, version: str, actor: str = 'system', well_id: Optional[str] = None) -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well_id)
    out = os.path.join(ws.reports_dir, 'wells', well_id) if well_id else ws.reports_dir
    paths = CR.write(doc, out, 'rig_time' if well_id else 'rig_time_pad')
    ws.audit(actor, 'report.rig_time', {'well': well_id, 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


def text(p: dict) -> str:
    L = [f"Rig time: {len(p['wells'])} well(s); targets: " + '; '.join(f"{k.replace('_', ' ')} {v['value']:g} {v['unit']} ({v['how']})" if v else f"{k.replace('_', ' ')} none"
                                                                          for k, v in p['targets'].items())]
    for w in p['wells']:
        h = w['hours']
        L.append('')
        L.append(f"{w['display']}: {w['record_hours']:,.1f} h of record; drilling {h['ROTARY DRILLING'] + h['SLIDE DRILLING']:,.1f} h (slide {h['SLIDE DRILLING']:,.1f}), "
                 f"in slips {h['IN SLIPS']:,.1f} h, off the hook {h['OFF THE HOOK']:,.1f} h, tripping {h['TRIPPING IN'] + h['TRIPPING OUT']:,.1f} h, circulating {h['CIRCULATING']:,.1f} h, reaming/washing "
                 f"{h['REAMING'] + h['WASHING']:,.1f} h, stationary {h['STATIONARY']:,.1f} h")
        c = w['connections']
        if c:
            L.append(f"  {c['n']} drilling connections: weight to weight median {c['median']:.1f} min (P25 {c['p25']:.1f}, P75 {c['p75']:.1f}); pre {w['pre']['median']:.1f}, "
                     f"slips {w['slips']['median']:.1f}, post {w['post']['median']:.1f} min")
        for x in w['trips']:
            L.append(f"  trip {x['direction']:<3} {x['start'][:16]}  {x['from_ft']:>7,.0f} -> {x['to_ft']:>7,.0f} ft  {x['hours']:5.2f} h  {_f(x['speed_ft_h'], 0)} ft/h "
                     f"(moving {_f(x['moving_speed_ft_h'], 0)}; breaks {x['breaks_hours']:.2f} h)  {x['connections']} conn, slips median {_f(x['median_slips_min'], 2)} min")
        L.append(f"  time lost: {w['lost_h']:,.2f} h (connections {w['lost_connections_h']:,.2f}, trips {w['lost_trips_h']:,.2f})"
                 + (f", {w['lost_usd']:,.2f} USD at the operating rate" if w['lost_usd'] is not None else ''))
        L.append('  ' + '; '.join(w['how']))
    for e in p['errors']:
        L.append('not read: ' + e)
    return '\n'.join(L)


# -- a labelled scene for the gate ---------------------------------------------------------------------------------------
def selftest_edr(tmp: str) -> dict:
    """A one-second recorder export with known answers: four stands drilled at 190 ft/h (30 min each) with three
    connections between them of 6, 9 and 15 minutes weight to weight (1 min off bottom, then 4, 7 and 13 min in
    the slips, then 1 min back to bottom), a trip out of 3,000 ft in 30 stands of 100 ft (each 60 s moving at 100
    ft/min and 90 s in the slips), 20 min stationary, and a trip in of the same 3,000 ft (each stand 50 s moving and
    60 s in the slips), then 10 min circulating. The block weighs 40 klbf; the string 160 klbf more."""
    import csv as _csv
    p = os.path.join(tmp, 'rig_state_edr.csv')
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)',
           'Standpipe Pressure (psi)', 'Hook Load (klbs)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'On Bottom (unitless)']
    t = [datetime(2026, 8, 1, 6, 0, 0)]
    rows = []
    S = {'hole': 10000.0, 'bit': 10000.0, 'block': 10.0}

    def row(rop, rpm, pump, hl, on):
        rows.append([t[0].strftime('%Y/%m/%d'), t[0].strftime('%H:%M:%S'), f"{S['hole']:.2f}", f"{S['bit']:.2f}", f'{rop:.1f}', f'{20.0 if on else 0.0:.1f}',
                     f'{rpm:.1f}', f'{2500.0 if pump else 0.0:.0f}', f'{hl:.1f}', f'{550.0 if pump else 0.0:.0f}', f"{S['block']:.2f}", str(on)])
        t[0] += timedelta(seconds=1)

    def drill(minutes, rate=190.0):
        for _ in range(int(minutes * 60)):
            S['hole'] += rate / 3600.0; S['bit'] = S['hole']; S['block'] -= rate / 3600.0
            row(rate, 120.0, True, 180.0, 1)

    def pick_up(seconds, ft):                     # off bottom with the pumps on, bit and block going up together
        for _ in range(int(seconds)):
            S['bit'] -= ft / seconds; S['block'] += ft / seconds
            row(0.0, 0.0, True, 200.0, 0)

    def down(seconds, ft):
        for _ in range(int(seconds)):
            S['bit'] += ft / seconds; S['block'] -= ft / seconds
            row(0.0, 60.0, True, 195.0, 0)

    def slips(seconds, block_to):
        b0 = S['block']
        for i in range(int(seconds)):
            S['block'] = b0 + (block_to - b0) * min(1.0, (i + 1) / max(1.0, seconds * 0.6))
            row(0.0, 0.0, False, 40.0, 0)

    def trip_stand(direction, move_s, slips_s, ft=100.0):
        # out: pull a stand (bit and block up), set slips, lower the empty block; in: the reverse
        if direction == 'OUT':
            for _ in range(int(move_s)):
                S['bit'] -= ft / move_s; S['block'] += ft / move_s
                row(0.0, 0.0, False, 200.0, 0)
            slips(slips_s, S['block'] - ft)
        else:
            slips(slips_s, S['block'] + ft)
            for _ in range(int(move_s)):
                S['bit'] += ft / move_s; S['block'] -= ft / move_s
                row(0.0, 0.0, False, 200.0, 0)

    def still(minutes, pump=False):
        for _ in range(int(minutes * 60)):
            row(0.0, 0.0, pump, 200.0, 0)

    S['block'] = 105.0
    drill(30)
    for sl in (4, 7, 13):
        pick_up(30, 0.0)
        pick_up(30, 0.5)                         # 1 min off bottom (bit lifted half a foot)
        slips(sl * 60, 105.0)                    # the block back up for the next stand
        down(60, 0.5)                            # 1 min back to bottom
        drill(30)
    still(10, pump=True)                          # circulate bottoms up before the trip
    pick_up(300, 300.0)                           # washed 300 ft off bottom, then the trip
    for _ in range(30):
        trip_stand('OUT', 60, 90)
    still(20)
    for _ in range(30):
        trip_stand('IN', 50, 60)
    still(10, pump=True)
    with open(p, 'w', newline='', encoding='utf-8') as f:
        wr = _csv.writer(f); wr.writerow(hdr); wr.writerows(rows)
    # a trip is timed from its first second moving to its last: the trip out ends on a move, the trip in starts on one
    return {'path': p, 'expect': {'connections_w2w_min': [6.0, 9.0, 15.0], 'slips_min': [4.0, 7.0, 13.0], 'trip_out_ft_h': 3000.0 / ((30 * 150 - 90) / 3600.0),
                                  'trip_in_ft_h': 3000.0 / ((30 * 110 - 60) / 3600.0), 'block_weight_klbf': 40.0}}
