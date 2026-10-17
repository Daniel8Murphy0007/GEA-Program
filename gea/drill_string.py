"""The drill string: what was run in the hole, how much each piece has been used, and how the downhole motor behaved.

A run is the operator's declaration of one trip in the hole: the components from the bit up (each a class from the
tool library, with its maker, model, serial, outside diameter and length), the stretch of the drilling record it
covers on the recorder's own clock, and - for each component that has one - its service interval and where that
interval came from. The program measures the rest from the drilling record of the well it ran in:

- usage: hours in the hole (the run's span of the record), circulating hours (pump output above a floor), rotating
  hours (rotary above a floor while circulating), drilling hours (the record's own drilling samples), the footage
  drilled (the running bottom of the hole), the gallons pumped; summed per serial across every run of every well,
  against the declared service interval;
- the motor: its revolutions (the gallons pumped times the motor's revolutions per gallon), its no-load speed and
  the bit's (the rotary speed plus the motor's), the differential pressure on bottom against the maker's maximum
  operating and maximum differential, the hours above the first, the stalls (the differential at or above the
  second while pumping, two seconds or longer), and a torque estimate from the differential (the maker's maximum
  torque over its maximum differential, times the differential: an estimate, called one);
- its stability: the drilling record is sampled once a second, so it carries the oscillations slower than half a
  hertz - stick-slip in the surface torque, the differential swinging as the motor loads and unloads. Each block of
  about two hours of drilling is cut into 256 s pieces, each detrended, Hann-windowed and transformed, and the
  pieces averaged (Welch's method); in 0.03 to 0.45 Hz the strongest frequency is reported when it is a peak inside the
  band and stands at least four times above the band's median, and the band's RMS against the channel's mean is the oscillation index. The
  stability rule compares the first third of the blocks with the last: a distinct frequency moved by 25 % or more,
  or an oscillation index changed by half again or more, is a change - the program's rule, stated as such.
  The motor's own frequencies (the rotor's turning, and its whirl at its lobe count times that) lie far above what
  a one-second record holds; they are given from the declared motor and the flow, for a dynamics sub to confirm.

Motor figures come from the shipped catalogue (`reference/mud_motor_specs.json`, the maker's published spec sheets)
or from the operator's own declaration with its source; the program does not supply a figure the run does not name.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

REF = os.path.join(os.path.dirname(__file__), 'reference', 'mud_motor_specs.json')
CATEGORIES = ('bits', 'drive', 'measure', 'bha')
MOTOR_CLASSES = ('pdm_motor', 'turbodrill')
LIFE_BASES = ('circulating_hours', 'drilling_hours', 'rotating_hours', 'hours_in_hole', 'footage_ft', 'revolutions')
FLOW_FLOOR_GPM = 50.0              # pumping, for circulating hours
ROTARY_FLOOR_RPM = 5.0
STALL_MIN_S = 2
WELCH_N = 256
BAND_HZ = (0.03, 0.45)
PEAK_RATIO = 4.0
BLOCK_S = 7200                      # about two hours of drilling per block
SEG_MIN_S = 300                     # a contiguous drilling stretch shorter than this is not transformed
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}
_SPECS = None


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# -- the motor catalogue ------------------------------------------------------------------------------
def catalogue() -> dict:
    global _SPECS
    if _SPECS is None:
        with open(REF, encoding='utf-8') as f:
            _SPECS = json.load(f)
    return _SPECS


def motor_specs() -> List[dict]:
    return catalogue()['motors']


def motor_spec(mid: str) -> dict:
    m = next((x for x in motor_specs() if x['id'] == mid), None)
    if m is None:
        raise KeyError(f'no motor {mid!r} in the catalogue; `gea tools --action motors` lists them')
    return {**m, 'source': f"{catalogue()['publisher']}, {catalogue()['title']} ({catalogue()['url']})"}


def motor_text() -> str:
    c = catalogue()
    L = [f"Downhole motors: {len(c['motors'])} power sections from {c['publisher']}, {c['title']}", f"  {c['url']} (read {c['fetched']})", '']
    L.append(f"  {'id':<34} {'OD in':>6} {'lobes':>5} {'stg':>5} {'gpm':>10} {'rev/gal':>7} {'op dP':>6} {'max dP':>6} {'torque':>7}")
    for m in c['motors']:
        L.append(f"  {m['id']:<34} {m['od_in']:>6.3f} {m['lobes']:>5} {m['stages']:>5} {m['flow_gpm'][0]:>4}-{m['flow_gpm'][1]:<5} {m['rev_per_gal']:>7.2f} "
                 f"{m['max_operating_diff_psi']:>6,} {m['max_diff_psi']:>6,} {m['max_torque_ft_lb']:>7,}")
    L += ['', '  ' + c['how']]
    return '\n'.join(L)


# -- the runs -----------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.path, 'drillstring', 'runs.json')


def runs(ws, well_id: Optional[str] = None) -> List[dict]:
    p = _path(ws)
    if not os.path.isfile(p):
        return []
    with open(p, encoding='utf-8') as f:
        rs = json.load(f).get('runs', [])
    return [r for r in rs if well_id is None or r['well_id'] == well_id]


def _save(ws, rs: List[dict]) -> None:
    p = _path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'runs': rs}, f, indent=1)
    os.replace(tmp, p)


def _num(v, what: str, positive: bool = True) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{what} is not a number: {v!r}')
    if not math.isfinite(x) or (positive and x <= 0):
        raise ValueError(f'{what} must be a positive number, not {v!r}')
    return x


def _when(v, what: str) -> Optional[str]:
    if v in (None, ''):
        return None
    try:
        return datetime.fromisoformat(str(v).replace('Z', '')).isoformat(timespec='seconds')
    except ValueError:
        raise ValueError(f"{what} is not a date and time (YYYY-MM-DDTHH:MM on the recorder's clock): {v!r}")


def _component(c: dict, i: int) -> dict:
    from .tool_taxonomy import TAXONOMY
    cid = str(c.get('class_id') or '').strip()
    if cid not in TAXONOMY or TAXONOMY[cid]['category'] not in CATEGORIES:
        raise ValueError(f'component {i + 1}: {cid!r} is not a bit, drive, measurement or BHA class of the tool library')
    out = {'class_id': cid, 'class_name': TAXONOMY[cid]['name'], 'maker': str(c.get('maker') or '').strip(), 'model': str(c.get('model') or '').strip(),
           'serial': str(c.get('serial') or '').strip(), 'od_in': _num(c.get('od_in'), f'component {i + 1} outside diameter'),
           'length_ft': _num(c.get('length_ft'), f'component {i + 1} length'), 'connection': str(c.get('connection') or '').strip(),
           'fleet_id': str(c.get('fleet_id') or '').strip() or None, 'note': str(c.get('note') or '').strip()}
    lim = _num(c.get('service_limit'), f'component {i + 1} service interval')
    if lim is not None:
        basis = str(c.get('service_basis') or 'circulating_hours')
        if basis not in LIFE_BASES:
            raise ValueError(f"component {i + 1}: the service interval is counted in one of {', '.join(LIFE_BASES)}")
        if basis == 'revolutions' and cid not in MOTOR_CLASSES:
            raise ValueError(f'component {i + 1}: only a motor is counted in revolutions')
        src = str(c.get('service_source') or '').strip()
        if not src:
            raise ValueError(f"component {i + 1}: a service interval needs its source (the maker's manual, the rental contract, the shop's rule)")
        out['service'] = {'limit': lim, 'basis': basis, 'source': src}
    if cid in MOTOR_CLASSES:
        if c.get('motor_id'):
            m = motor_spec(str(c['motor_id']))
            out['motor'] = {k: m[k] for k in ('id', 'name', 'rev_per_gal', 'flow_gpm', 'max_operating_diff_psi', 'max_diff_psi', 'max_torque_ft_lb',
                                              'rotor_lobes', 'stator_lobes', 'stages', 'od_in')} | {'source': m['source']}
            out['od_in'] = out['od_in'] or m['od_in']
            out['maker'] = out['maker'] or 'MS Directional (Mpact)'
            out['model'] = out['model'] or m['name']
        elif c.get('motor'):
            d = c['motor']
            src = str(d.get('source') or '').strip()
            if not src:
                raise ValueError(f"component {i + 1}: a motor's own figures need their source (its spec sheet)")
            lob = str(d.get('lobes') or '').strip()
            if lob and not re.fullmatch(r'\d+/\d+', lob):
                raise ValueError(f'component {i + 1}: lobes are written rotor/stator, e.g. 7/8')
            out['motor'] = {'id': None, 'name': out['model'] or 'declared motor', 'rev_per_gal': _num(d.get('rev_per_gal'), 'revolutions per gallon'),
                            'flow_gpm': [_num(d.get('flow_min_gpm'), 'minimum flow') or 0.0, _num(d.get('flow_max_gpm'), 'maximum flow') or 0.0],
                            'max_operating_diff_psi': _num(d.get('max_operating_diff_psi'), 'maximum operating differential'),
                            'max_diff_psi': _num(d.get('max_diff_psi'), 'maximum differential'),
                            'max_torque_ft_lb': _num(d.get('max_torque_ft_lb'), 'maximum torque'),
                            'rotor_lobes': int(lob.split('/')[0]) if lob else None, 'stator_lobes': int(lob.split('/')[1]) if lob else None,
                            'stages': _num(d.get('stages'), 'stages'), 'od_in': out['od_in'], 'source': src}
            if out['motor']['rev_per_gal'] is None:
                raise ValueError(f'component {i + 1}: a motor needs its revolutions per gallon')
    if not out['maker'] and not out['model'] and not out['serial']:
        out['model'] = out['class_name']
    return out


def save_run(ws, actor: str, well_id: str, name: str, components: List[dict], start: Optional[str] = None, end: Optional[str] = None,
             depth_in_ft=None, depth_out_ft=None, note: str = '', run_id: Optional[str] = None) -> dict:
    """Declare (or replace) a run: the string from the bit up and the stretch of the record it covers."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    if not (name or '').strip():
        raise ValueError('a run needs a name (e.g. "Run 3 - lateral")')
    if not components:
        raise ValueError('a run needs its components, from the bit up')
    comps = [_component(c, i) for i, c in enumerate(components)]
    a, b = _when(start, 'the start'), _when(end, 'the end')
    if a and b and b <= a:
        raise ValueError('the run ends before it starts')
    with _LOCK:
        rs = runs(ws)
        old = next((r for r in rs if r['id'] == run_id), None) if run_id else None
        if run_id and old is None:
            raise KeyError(f'no run {run_id}')
        rec = {'id': run_id or uuid.uuid4().hex[:10], 'well_id': well_id, 'name': name.strip(), 'start': a, 'end': b,
               'depth_in_ft': _num(depth_in_ft, 'the depth in', positive=False), 'depth_out_ft': _num(depth_out_ft, 'the depth out', positive=False),
               'components': comps, 'note': (note or '').strip(), 'declared_by': actor, 'declared_utc': _utc()}
        rs = [r for r in rs if r['id'] != rec['id']] + [rec]
        rs.sort(key=lambda r: (r['well_id'], r['start'] or '', r['name']))
        _save(ws, rs)
    ws.audit(actor, 'drillstring.run.' + ('replace' if old else 'declare'), {'run': rec['id'], 'well': well_id, 'name': rec['name'],
                                                                            'components': [c['class_id'] for c in comps]})
    return rec


def remove_run(ws, actor: str, run_id: str) -> dict:
    with _LOCK:
        rs = runs(ws)
        r = next((x for x in rs if x['id'] == run_id), None)
        if r is None:
            raise KeyError(f'no run {run_id}')
        _save(ws, [x for x in rs if x['id'] != run_id])
    ws.audit(actor, 'drillstring.run.remove', {'run': run_id, 'well': r['well_id'], 'name': r['name']})
    return {'removed': run_id}


# -- the record ---------------------------------------------------------------------------------------
def _cache(ws, wid: str, build: bool = True):
    from . import series_cache as SCH
    c = SCH.ensure(ws, wid) if build else SCH.load(SCH.cache_dir(ws, wid), SCH.source_path(ws, wid))
    if c is None:
        raise KeyError(f"{wid}'s chart cache is not built: open the well's charts once (or refresh)")
    if c.extra('drilling') is None:
        raise KeyError(f'{wid} is not a drilling-recorder well: the drill string is tracked from a drilling record')
    return c


def _ch(c, name) -> Optional[np.ndarray]:
    return np.asarray(c.channel(name), dtype=np.float64) if name in c._ch else None


def _span(c, start: Optional[str], end: Optional[str]) -> Tuple[int, int, datetime]:
    t0 = datetime.fromisoformat(c.meta['start_time'])
    idx = c.index
    i0 = 0 if not start else int(np.searchsorted(idx, (datetime.fromisoformat(start) - t0).total_seconds(), 'left'))
    i1 = len(idx) if not end else int(np.searchsorted(idx, (datetime.fromisoformat(end) - t0).total_seconds(), 'right'))
    return i0, max(i0, i1), t0


def _segments(mask: np.ndarray, min_len: int) -> List[Tuple[int, int]]:
    if not len(mask):
        return []
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    a, b = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(int(x), int(y)) for x, y in zip(a, b) if y - x >= min_len]


def _welch(x: np.ndarray, n: int = WELCH_N) -> Tuple[np.ndarray, int]:
    """Averaged one-sided PSD (units^2/Hz at 1 Hz) of 50 %-overlapped, linearly detrended, Hann-windowed pieces."""
    w = np.hanning(n); s2 = float((w * w).sum()); t = np.arange(n)
    acc = np.zeros(n // 2 + 1); k = 0
    for s in range(0, len(x) - n + 1, n // 2):
        seg = x[s:s + n]
        if not np.isfinite(seg).all():
            continue
        p = np.polyfit(t, seg, 1)
        acc += np.abs(np.fft.rfft((seg - np.polyval(p, t)) * w)) ** 2; k += 1
    if k == 0:
        return acc, 0
    psd = 2.0 * acc / (k * s2)
    psd[0] /= 2.0
    return psd, k


def _peak(psd: np.ndarray, f: np.ndarray, mean_level: float) -> dict:
    band = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1])
    pb, fb = psd[band], f[band]
    df = float(f[1] - f[0])
    rms = float(math.sqrt(max(0.0, pb.sum() * df)))
    i = int(np.argmax(pb)); med = float(np.median(pb)) or 1e-30
    ratio = float(pb[i] / med)
    interior = 0 < i < len(pb) - 1 and pb[i] >= pb[i - 1] and pb[i] >= pb[i + 1]      # a spectrum still rising at the band's edge is not a peak
    return {'f_hz': round(float(fb[i]), 4) if (ratio >= PEAK_RATIO and interior) else None, 'peak_f_hz': round(float(fb[i]), 4), 'ratio': round(ratio, 2),
            'rms': round(rms, 4), 'index': round(rms / abs(mean_level), 4) if mean_level else None}


def _t(t0: datetime, s: float) -> str:
    return (t0 + timedelta(seconds=round(float(s)))).isoformat()


def _r(v, nd=3):
    if v is None:
        return None
    v = float(v)
    return round(v, nd) if math.isfinite(v) else None


def track(ws, well_id: str, start: Optional[str] = None, end: Optional[str] = None, motor: Optional[dict] = None, build: bool = True) -> dict:
    """The record over a run's span: usage, and the motor's statistics and stability (motor figures only when one is named)."""
    c = _cache(ws, well_id, build)
    key = (well_id, start, end, json.dumps(motor, sort_keys=True, default=str), c.meta.get('built_utc'), c.meta.get('source_mtime'))
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    i0, i1, t0 = _span(c, start, end)
    idx = np.asarray(c.index[i0:i1], dtype=np.float64)
    out = {'well_id': well_id, 'start': _t(t0, idx[0]) if len(idx) else start, 'end': _t(t0, idx[-1]) if len(idx) else end,
           'clock': "the drilling recorder's own clock (not UTC)", 'samples': int(len(idx))}
    if len(idx) < 2:
        out.update({'usage': None, 'why': 'the run covers no samples of the record'})
        return out
    dtv = np.diff(idx, append=idx[-1] + 1.0); dtv = np.where(dtv > 60.0, 1.0, dtv)        # a gap in the record counts one sample, not the gap
    flow, dp, rpm = _ch(c, 'Total Pump Output'), _ch(c, 'Differential Pressure'), _ch(c, 'Rotary RPM')
    tq, rop, wob = _ch(c, 'Convertible Torque'), _ch(c, 'Rate Of Penetration'), _ch(c, 'Weight on Bit')
    sl = slice(i0, i1)
    drl = np.asarray(c.extra('drilling'), dtype=bool)[sl]
    bottom = c.extra('bottom')
    fl = flow[sl] if flow is not None else None
    circ = (np.nan_to_num(fl) > FLOW_FLOOR_GPM) if fl is not None else np.zeros(len(idx), bool)
    rot = circ & (np.nan_to_num(rpm[sl]) > ROTARY_FLOOR_RPM) if rpm is not None else np.zeros(len(idx), bool)
    gallons = float(np.nansum(np.where(circ, fl, 0.0) * dtv) / 60.0) if fl is not None else None
    foot = None
    if bottom is not None:
        b = np.asarray(bottom[sl], dtype=np.float64)
        if np.isfinite(b).any():
            prior = np.asarray(bottom[:i0 + 1], dtype=np.float64); prior = prior[np.isfinite(prior)]
            b0 = float(prior[-1]) if len(prior) else float(np.nanmin(b))     # the bottom as the run found it
            foot = max(0.0, float(np.nanmax(b)) - b0)
            out['depth_from_ft'], out['depth_to_ft'] = _r(b0, 1), _r(float(np.nanmax(b)), 1)
    out['usage'] = {'hours_in_hole': _r(dtv.sum() / 3600.0, 2), 'circulating_hours': _r((dtv * circ).sum() / 3600.0, 2),
                    'rotating_hours': _r((dtv * rot).sum() / 3600.0, 2), 'drilling_hours': _r((dtv * drl).sum() / 3600.0, 2),
                    'footage_ft': _r(foot, 1), 'gallons': _r(gallons, 0),
                    'how': f"hours in the hole are the hours the record covers in the run's span (a gap in the record is not counted); circulating is pump output above {FLOW_FLOOR_GPM:.0f} gal/min; rotating "
                           f"is the rotary above {ROTARY_FLOOR_RPM:.0f} rpm while circulating; drilling is the record's own drilling samples; the "
                           f"footage is the running bottom of the hole"}
    rpg = (motor or {}).get('rev_per_gal')
    if rpg and gallons is not None:
        out['usage']['revolutions'] = _r(rpg * gallons, 0)
    # the motor's statistics, hour by hour of the record
    m = motor or {}
    mop, mmax, mtq = m.get('max_operating_diff_psi'), m.get('max_diff_psi'), m.get('max_torque_ft_lb')
    slope = (mtq / mmax) if (mtq and mmax) else None
    stats = {'motor': m or None, 'torque_slope_ft_lb_per_psi': _r(slope, 3)}
    if dp is not None:
        d = dp[sl]
        on = drl & np.isfinite(d)
        if on.any():
            stats['diff_on_bottom_psi'] = {'p50': _r(np.percentile(d[on], 50), 0), 'p95': _r(np.percentile(d[on], 95), 0), 'max': _r(np.max(d[on]), 0)}
            edges = np.arange(0.0, max(float(np.percentile(d[on], 99.5)), (mmax or 0) * 1.05, 100.0) + 100.0, 100.0)
            h, _ = np.histogram(np.clip(d[on], 0, edges[-1] - 1e-6), bins=edges, weights=dtv[on] / 3600.0)
            stats['histogram'] = {'edges_psi': [float(e) for e in edges], 'hours': [_r(v, 3) for v in h],
                                  'how': 'hours on bottom in each 100 psi of differential pressure'}
        if mop:
            stats['hours_above_max_operating'] = _r((dtv * (on & (d > mop))).sum() / 3600.0, 2)
        if mmax:
            hot = circ & np.isfinite(d) & (d >= mmax)
            st = []
            for a, b in _segments(hot, STALL_MIN_S):
                if st and (idx[a] - st[-1]['_end']) <= 30.0:
                    st[-1]['_end'] = idx[b - 1]; st[-1]['seconds'] += int(b - a); st[-1]['peak_psi'] = max(st[-1]['peak_psi'], _r(np.max(d[a:b]), 0))
                    continue
                st.append({'at': _t(t0, idx[a]), 'seconds': int(b - a), 'peak_psi': _r(np.max(d[a:b]), 0), 'depth_ft': _r(np.nanmax(np.asarray(bottom[i0 + a:i0 + b])), 1) if bottom is not None else None, '_end': idx[b - 1]})
            for s in st:
                s.pop('_end', None)
            stats['stalls'] = st
            stats['stall_rule'] = (f"the differential at or above the motor's maximum ({mmax:,.0f} psi) while pumping, {STALL_MIN_S} s or longer; "
                                   f"stalls within 30 s of each other are one")
    # hour-by-hour series
    hrs = np.floor((idx - idx[0]) / 3600.0).astype(np.int64)
    nh = int(hrs[-1]) + 1
    ser = {'t': [], 'drilling_h': [], 'flow_gpm': [], 'diff_psi': [], 'diff_p95_psi': [], 'surface_rpm': [], 'motor_rpm': [], 'bit_rpm': [],
           'torque_est_ft_lb': [], 'surface_torque': [], 'rop_ft_h': [], 'whirl_hz': [], 'rotor_hz': []}
    order = np.argsort(hrs, kind='stable')
    bounds = np.searchsorted(hrs[order], np.arange(nh + 1))
    for h in range(nh):
        k = order[bounds[h]:bounds[h + 1]]
        if not len(k):
            continue
        dk = k[drl[k]]
        ck = k[circ[k]]
        mean = lambda arr, kk: _r(np.nanmean(arr[sl][kk]), 2) if (arr is not None and len(kk) and np.isfinite(arr[sl][kk]).any()) else None
        f_ = mean(flow, ck)
        ser['t'].append(_t(t0, idx[k[0]])); ser['drilling_h'].append(_r(dtv[dk].sum() / 3600.0, 3))
        ser['flow_gpm'].append(f_); ser['diff_psi'].append(mean(dp, dk))
        ser['diff_p95_psi'].append(_r(np.nanpercentile(dp[sl][dk], 95), 0) if (dp is not None and len(dk) and np.isfinite(dp[sl][dk]).any()) else None)
        sr = mean(rpm, dk); ser['surface_rpm'].append(sr); ser['surface_torque'].append(mean(tq, dk)); ser['rop_ft_h'].append(mean(rop, dk))
        mr = _r(rpg * f_, 1) if (rpg and f_ is not None) else None
        ser['motor_rpm'].append(mr); ser['bit_rpm'].append(_r((sr or 0.0) + mr, 1) if mr is not None else None)
        dpm = ser['diff_psi'][-1]
        ser['torque_est_ft_lb'].append(_r(slope * max(0.0, dpm), 0) if (slope and dpm is not None) else None)
        ser['rotor_hz'].append(_r(mr / 60.0, 3) if mr is not None else None)
        ser['whirl_hz'].append(_r(mr / 60.0 * m['rotor_lobes'], 3) if (mr is not None and m.get('rotor_lobes')) else None)
    stats['hourly'] = ser
    if rpg:
        stats['how_speed'] = ("the motor's speed is its revolutions per gallon times the pump output - its no-load speed; under load it turns "
                              "slower. The bit turns at the rotary speed plus the motor's")
        if m.get('rotor_lobes'):
            stats['how_whirl'] = (f"the rotor turns at the motor's speed and its centre whirls the other way at {m['rotor_lobes']} times that - the "
                                  f"frequencies a downhole dynamics sub would see; the one-second record cannot")
    if slope:
        stats['how_torque'] = (f"an estimate: the maker's maximum torque over its maximum differential ({slope:,.2f} ft-lb per psi) times the "
                               f"differential")
    out['motor_stats'] = stats
    out['stability'] = _stability(idx, drl, tq[sl] if tq is not None else None, dp[sl] if dp is not None else None, t0)
    with _LOCK:
        _MEMO[key] = out
        if len(_MEMO) > 64:
            _MEMO.pop(next(iter(_MEMO)))
    return out


def _stability(idx: np.ndarray, drl: np.ndarray, tq: Optional[np.ndarray], dp: Optional[np.ndarray], t0: datetime) -> dict:
    """The slow oscillations of the surface torque and the differential, block by block of drilling, and the rule."""
    out = {'band_hz': list(BAND_HZ), 'piece_s': WELCH_N, 'block_s': BLOCK_S, 'peak_ratio': PEAK_RATIO, 'blocks': [], 'spectra': None,
           'how': (f"each block of about {BLOCK_S // 3600} h of drilling cut into {WELCH_N} s pieces (half overlapped), each detrended and "
                   f"Hann-windowed, the spectra averaged (Welch); in {BAND_HZ[0]}-{BAND_HZ[1]} Hz the strongest frequency is reported when it "
                   f"is a peak inside the band (not the band's edge) and stands {PEAK_RATIO:g} times above the band's median; the band's RMS over the channel's mean is the oscillation index"),
           'rule': ('a change: a distinct frequency of the first third of the blocks and of the last differing by 25 % or more, or an oscillation '
                    'index differing by a factor of 1.5 or more; the program\'s rule, not a standard')}
    regular = np.r_[np.diff(idx) <= 1.5, False]
    segs = _segments(drl & regular, SEG_MIN_S)
    if not segs:
        out['verdict'] = {'status': 'NOT ENOUGH DRILLING', 'lines': [f'no stretch of {SEG_MIN_S} s or more of unbroken drilling at one sample a second']}
        return out
    f = np.fft.rfftfreq(WELCH_N, 1.0)
    blocks, cur, acc = [], [], 0
    for a, b in segs:                                    # a long stretch is cut at the block's length; a short tail joins the next block
        while b - a > 0:
            take = min(b - a, BLOCK_S - acc)
            if b - (a + take) < SEG_MIN_S:
                take = b - a
            cur.append((a, a + take)); acc += take; a += take
            if acc >= BLOCK_S:
                blocks.append(cur); cur, acc = [], 0
    if cur and (acc >= BLOCK_S / 2 or not blocks):
        blocks.append(cur)
    allspec = {'torque': [], 'diff': []}
    for bl in blocks:
        row = {'t': _t(t0, idx[bl[0][0]]), 't_end': _t(t0, idx[bl[-1][1] - 1]), 'drilling_h': round(sum(b - a for a, b in bl) / 3600.0, 2)}
        for name, arr in (('torque', tq), ('diff', dp)):
            if arr is None:
                continue
            acc_p, k = np.zeros(len(f)), 0
            means = []
            for a, b in bl:
                p, n = _welch(arr[a:b])
                if n:
                    acc_p += p * n; k += n; means.append(float(np.nanmean(arr[a:b])))
            if not k:
                continue
            psd = acc_p / k
            row[name] = _peak(psd, f, float(np.mean(means)))
            row[name]['mean'] = round(float(np.mean(means)), 3)
            allspec[name].append(psd)
        out['blocks'].append(row)
    n = len(out['blocks'])
    third = max(1, n // 3)
    band = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1])
    out['spectra'] = {'f_hz': [round(float(x), 5) for x in f[band]]}
    for name in ('torque', 'diff'):
        if len(allspec[name]) >= 2:
            out['spectra'][name] = {'first': [_r(v, 6) for v in np.mean(allspec[name][:third], axis=0)[band]],
                                    'last': [_r(v, 6) for v in np.mean(allspec[name][-third:], axis=0)[band]]}
    lines, changed = [], []
    if n < 6:
        out['verdict'] = {'status': 'TOO FEW BLOCKS', 'lines': [f'{n} block(s) of drilling: the rule compares thirds of six or more']}
        return out
    for name, label in (('torque', 'surface torque'), ('diff', 'differential pressure')):
        rows = [b[name] for b in out['blocks'] if name in b]
        if len(rows) < 6:
            continue
        a_, z_ = rows[:third], rows[-third:]
        fa = [r['f_hz'] for r in a_ if r['f_hz']]; fz = [r['f_hz'] for r in z_ if r['f_hz']]
        ia = [r['index'] for r in a_ if r['index'] is not None]; iz = [r['index'] for r in z_ if r['index'] is not None]
        if fa and fz:
            m0, m1 = float(np.median(fa)), float(np.median(fz))
            moved = abs(m1 - m0) / m0 >= 0.25
            lines.append(f"{label}: a distinct oscillation at {m0:.3f} Hz ({1 / m0:.0f} s) in the first third, {m1:.3f} Hz ({1 / m1:.0f} s) in the last"
                         + (' - moved' if moved else ''))
            if moved:
                changed.append(f'{label} frequency')
        else:
            lines.append(f"{label}: a distinct oscillation in {sum(1 for r in rows if r['f_hz'])} of {len(rows)} blocks"
                         + ('' if (fa and fz) else ' (not in both the first and the last third, so its frequency is not compared)'))
        if ia and iz and np.median(ia) > 0:
            r_ = float(np.median(iz) / np.median(ia))
            lines.append(f"{label}: oscillation index {np.median(ia):.3f} in the first third, {np.median(iz):.3f} in the last ({r_:.2f} times)")
            if r_ >= 1.5 or r_ <= 1 / 1.5:
                changed.append(f'{label} oscillation')
    out['verdict'] = {'status': 'CHANGED' if changed else 'STEADY', 'changed': changed, 'lines': lines, 'blocks': n}
    return out


# -- the tab's data -----------------------------------------------------------------------------------
def _run_motor(r: dict) -> Optional[dict]:
    return next((c['motor'] for c in r['components'] if c.get('motor')), None)


def run_view(ws, r: dict, build: bool = True) -> dict:
    tr = track(ws, r['well_id'], r['start'], r['end'], _run_motor(r), build)
    return {**r, 'track': tr}


def tracking(ws, build: bool = True) -> List[dict]:
    """Every serial (or unserialised component, by run) across every run: its runs, its usage summed, against its interval."""
    by: Dict[str, dict] = {}
    for r in runs(ws):
        try:
            tr = track(ws, r['well_id'], r['start'], r['end'], _run_motor(r), build)
            u = tr.get('usage') or {}
        except KeyError as e:
            u, tr = {}, {'why': str(e.args[0])}
        for i, comp in enumerate(r['components']):
            key = comp.get('fleet_id') or (f"{comp['class_id']}|{comp['maker']}|{comp['model']}|{comp['serial']}" if comp['serial'] else f"{r['id']}#{i}")
            e = by.setdefault(key, {'key': key, 'class_id': comp['class_id'], 'class_name': comp['class_name'], 'maker': comp['maker'], 'model': comp['model'],
                                    'serial': comp['serial'], 'fleet_id': comp.get('fleet_id'), 'runs': [], 'service': None,
                                    'totals': {b: 0.0 for b in LIFE_BASES}})
            e['runs'].append({'run': r['id'], 'name': r['name'], 'well_id': r['well_id'], 'start': tr.get('start'), 'end': tr.get('end')})
            for b in LIFE_BASES:
                v = u.get(b)
                if b == 'revolutions' and comp['class_id'] not in MOTOR_CLASSES:
                    v = None
                if v is not None:
                    e['totals'][b] += float(v)
            if comp.get('service'):
                e['service'] = comp['service']          # the latest declaration stands
    out = []
    for e in by.values():
        e['totals'] = {k: round(v, 2) for k, v in e['totals'].items()}
        s = e['service']
        if s:
            used = e['totals'].get(s['basis'], 0.0)
            e['life'] = {'used': round(used, 2), 'limit': s['limit'], 'basis': s['basis'], 'remaining': round(s['limit'] - used, 2),
                         'used_pct': round(100.0 * used / s['limit'], 1), 'status': 'DUE' if used >= s['limit'] else ('NEAR' if used >= 0.85 * s['limit'] else 'OK')}
        out.append(e)
    out.sort(key=lambda e: (-(e.get('life') or {}).get('used_pct', -1), e['class_name'], e['serial']))
    return out


def page(ws, well_id: Optional[str] = None, build: bool = True) -> dict:
    """Everything the tab draws: the wells with records, the runs of one, each with its track, the record without a run, the
    tracking across the fleet, and the motor catalogue."""
    import os as _os
    wells = [w for w in ws.wells() if _os.path.isfile(_os.path.join(ws.reports_dir, 'wells', w['id'], 'drilling_record.json'))]
    if well_id and not any(w['id'] == well_id for w in wells):
        raise KeyError(f'{well_id!r} is not a well with a drilling record')
    wid = well_id or (wells[0]['id'] if wells else None)
    out = {'wells': [{'id': w['id'], 'display': w.get('display') or w['id'], 'runs': len(runs(ws, w['id']))} for w in wells], 'well_id': wid,
           'runs': [], 'unassigned': None, 'tracking': tracking(ws, build), 'catalogue': {k: catalogue()[k] for k in ('publisher', 'title', 'url', 'fetched', 'how')},
           'motors': motor_specs(), 'life_bases': list(LIFE_BASES), 'categories': list(CATEGORIES)}
    if wid:
        rs = runs(ws, wid)
        for r in rs:
            try:
                out['runs'].append(run_view(ws, r, build))
            except KeyError as e:
                out['runs'].append({**r, 'track': {'why': str(e.args[0])}})
        if not rs:
            try:
                out['unassigned'] = {**track(ws, wid, None, None, None, build),
                                     'note': ('no run is declared, so this is the whole record: every trip and every string in it. A change in its stability '
                                              'may be a change of hole section or of string rather than of one motor - declare the runs to follow each')}
            except KeyError as e:
                out['unassigned'] = {'why': str(e.args[0])}
    return out


def text(p: dict) -> str:
    L = [f"Drill string: {len(p['wells'])} drilling well(s); {sum(w['runs'] for w in p['wells'])} run(s) declared."]
    for r in p['runs']:
        tr = r['track']; u = tr.get('usage') or {}
        L.append(f"  {r['name']} ({r['well_id']}): {tr.get('start', '?')} to {tr.get('end', '?')} - " + (
            f"{u.get('hours_in_hole', 0):,.1f} h in the hole, {u.get('circulating_hours', 0):,.1f} h circulating, {u.get('drilling_hours', 0):,.1f} h drilling, "
            f"{u.get('footage_ft') or 0:,.0f} ft" + (f", {u['revolutions']:,.0f} motor revolutions" if u.get('revolutions') else '') if u else tr.get('why', '')))
        for c in r['components']:
            L.append(f"    - {c['class_name']}: {c['maker']} {c['model']} {('s/n ' + c['serial']) if c['serial'] else ''}".rstrip())
        ms = tr.get('motor_stats') or {}
        if ms.get('diff_on_bottom_psi'):
            d = ms['diff_on_bottom_psi']
            L.append(f"    differential on bottom: median {d['p50']:,.0f} psi, 95th percentile {d['p95']:,.0f}, max {d['max']:,.0f}"
                     + (f"; {ms['hours_above_max_operating']:,.2f} h above the maximum operating" if ms.get('hours_above_max_operating') is not None else '')
                     + (f"; {len(ms['stalls'])} stall(s)" if ms.get('stalls') is not None else ''))
        v = (tr.get('stability') or {}).get('verdict') or {}
        if v:
            L.append(f"    stability: {v['status']}" + (f" ({', '.join(v.get('changed', []))})" if v.get('changed') else ''))
            for ln in v.get('lines', []):
                L.append(f'      {ln}')
    if p.get('unassigned') and not p['runs']:
        tr = p['unassigned']; v = (tr.get('stability') or {}).get('verdict') or {}
        L.append(f"  No run declared for {p['well_id']}: the whole record - " + (f"stability {v.get('status')}" if v else tr.get('why', '')))
        if tr.get('note'):
            L.append(f"      ({tr['note']})")
        for ln in v.get('lines', []):
            L.append(f'      {ln}')
    due = [e for e in p['tracking'] if (e.get('life') or {}).get('status') in ('DUE', 'NEAR')]
    for e in due:
        L.append(f"  {e['life']['status']}: {e['class_name']} {e['maker']} {e['model']} {e['serial']} - {e['life']['used']:,.1f} of {e['life']['limit']:,.1f} {e['life']['basis'].replace('_', ' ')}")
    return '\n'.join(L)


# -- a synthetic run for the gate -----------------------------------------------------------------------
def synthetic_motor_record(tmp: str, hours: float = 16.0) -> dict:
    """A labelled synthetic drilling-recorder export of one motor run: pumping 500 gal/min, drilling on bottom at a
    differential of about 600 psi, the surface torque oscillating at 0.10 Hz with a small amplitude for the first half
    and four times that in the second (a change the rule must find), and one 20 s excursion of the differential to
    1,900 psi (a stall against a 1,820 psi maximum)."""
    import csv
    from .edr import DATE_COL, TIME_COL
    p = os.path.join(tmp, 'SYNTHETIC_motor_run.csv')
    rng = np.random.default_rng(7)
    n = int(hours * 3600)
    t0 = datetime(2026, 8, 1, 0, 0, 0)
    tt = np.arange(n, dtype=np.float64)
    half = n // 2
    amp = np.where(tt < half, 0.25, 1.0)
    torque = 9.0 + amp * np.sin(2 * np.pi * 0.10 * tt) + rng.normal(0, 0.05, n)
    dp = 600.0 + 40.0 * np.sin(2 * np.pi * 0.10 * tt + 0.7) + rng.normal(0, 8.0, n)
    stall = int(n * 0.7)
    dp[stall:stall + 20] = 1900.0
    depth = 8000.0 + tt * (60.0 / 3600.0)
    hdr = [DATE_COL, TIME_COL, 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'On Bottom (unitless)', 'Weight on Bit (klbs)',
           'Rotary RPM (rpm)', 'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Convertible Torque (kft_lb)', 'Gamma (api)']
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(hdr)
        for i in range(n):
            ti = t0 + timedelta(seconds=i)
            w.writerow([ti.strftime('%Y/%m/%d'), ti.strftime('%H:%M:%S'), f'{depth[i]:.2f}', f'{depth[i]:.2f}', '60.00', '1', '25.0', '50.0',
                        f'{dp[i]:.1f}', '500.0', f'{torque[i]:.3f}', '80.0'])
    return {'path': p, 'start': t0.isoformat(), 'samples': n, 'f_hz': 0.10, 'stall_at': (t0 + timedelta(seconds=stall)).isoformat(), 'gallons': 500.0 * n / 60.0}
