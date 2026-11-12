# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""calibration - the plume and the front run many times over what nobody knows exactly, ranked against what is
measured: ranges where the other pages give one line, and which measurement would narrow them most.

The fourth fluid-flow module. The ledger is a record; the plume and the front are each ONE projection from one
set of declared numbers - a permeability, a thickness, a porosity, a compressibility, a fault taken as open or
sealing. None of those is known exactly, and the measurements that could say which values are right - a gauge in
a neighbour, the injector's own bottomhole pressure, a producer's water analysis - were until now only set
beside the projection. This module closes the loop, and every result of it is a SIMULATION.

- THE RANGES. Each uncertain number gets a range: declared with its source (a low and a high permeability from
  the core report), or - undeclared - a default spread about the declared value, named as a default (k a factor
  of three either way, thickness and porosity 30 %, total compressibility a factor of two). Each structure layer
  whose faults reach the grids is open in half the runs and sealing in the other half, unless that is switched
  off. The runs are a Latin hypercube over the ranges with a fixed seed, so the same inputs give the same runs;
  run 0 is the declared case itself.
- THE RUNS. Every run steps the plume (the pressure on a plan grid, day by day, as the Pressure plume tab does)
  and the front (the injected water around the main injection well, as the Fluid front tab does), on coarser
  grids than those pages and with the front's rates averaged over five days - a run takes a fraction of a
  second, and the page says how coarse.
- THE MEASUREMENTS. Pressure: every ledger well of the formation with a bottomhole pressure in its record - an
  observation well's gauge against the plume's rise in its cell; an injector's or a producer's own pressure
  against its cell's rise plus the well's own draw (Peaceman's well index, q mu ln(0.2 dx / r_w) /
  (2 pi 0.001127 k h)). Each series is compared after its own mean is taken off, measured and predicted alike:
  the level holds a skin and an initial pressure nobody declared, the shape through time is what the
  formation writes. Water: every producer's or observation well's measured TDS, as the share of injected water
  it implies, against the front's share that day. Each comparison is in units of its measurement error (25 psi
  and five points of share unless declared), root-mean-squared over its points; a run's misfit is the
  root-mean-square of its datasets' misfits.
- THE KEPT RUNS. A run is kept when its misfit is within 1.5 (that is, it fits about as well as the
  measurements are good) or within a quarter of the best run's. When the best run's misfit is over 3, NO RUN
  FITS: the declared ranges cannot make the measurement, and something outside them - a boundary, a leak, a
  wrong record - is what the page says to look for. With no measurement at all every run is kept, and the
  ranges are the spread of the declarations alone.
- THE RANGES OUT. For every forecast the other pages give as one number - the area of review and its reach now
  and in a year, each fault's rise and the share of the kept runs that take it past the friction line, the
  front's half radius, each producer's share and arrival - the tenth, fiftieth and ninetieth percentile of all
  the runs and of the kept runs, side by side: what the declarations allow, and what the measurements leave.
  Each uncertain number likewise: its range in, its range kept, and how much the measurements narrowed it.
- WHAT TO MEASURE NEXT. (Across the kept runs when eight or more were kept, else across the eight that fit best.)
  For each measurement not yet in the record - a gauge at a neighbour with none, a water
  analysis at a producer with none, a falloff test on the injector (which measures k h) - how far the kept runs
  disagree about what it would read, in units of its error, and by how much it would narrow the area of review
  and the front (the linear estimate: 1 - sqrt(1 - rho^2 v / (v + sigma^2)), rho the rank correlation across the
  kept runs between the reading and the forecast, v the readings' variance). The list is ranked: the first
  line is the measurement worth paying for.
- THE EARTHQUAKES. Where the workspace has a catalogue, each event near a mapped fault within the grid is
  listed with the rise the kept runs put on that fault that month - a statement of coincidence in the model's
  terms, not a cause, and not part of the ranking.

What it will not do: call a kept run true (it is a run the measurements do not contradict, on uniform rock);
turn the share of kept runs past the friction line into a probability of an earthquake; fit what was not
sampled (a boundary, a second layer, a leak are outside the ranges, and NO RUN FITS is how they show); run
itself - the runs are started by a person, the result is kept with the hash of everything it was computed
from, and the page says when the inputs have changed since.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import fluid as FL
from . import front as FR
from . import plume as PM

DEFAULT_RUNS = 48
PLUME_CELLS = 41
FRONT_CELLS = 41
FRONT_SPAN_DAYS = 5
FRONT_PROJ_SPAN_DAYS = 30
SIGMA_PSI = 25.0
SIGMA_SHARE = 0.05
SIGMA_LN_KH = 0.15
WELL_RADIUS_FT = 0.354
KEEP_MISFIT = 1.5
KEEP_OF_BEST = 1.25
NO_FIT = 3.0
DEFAULT_SPREAD = {'k_md': ('log', 3.0), 'thickness_ft': ('lin', 0.30), 'porosity': ('lin', 0.30), 'c_t_per_psi': ('log', 2.0)}
PARAM_WORDS = {'k_md': 'permeability, md', 'thickness_ft': 'net thickness, ft', 'porosity': 'porosity', 'c_t_per_psi': 'total compressibility, 1/psi'}
_LOCK = threading.Lock()


class CalibrationError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------------------------------------------
# settings: records/calibration.json
# --------------------------------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'calibration.json')


def _result_path(ws) -> str:
    return os.path.join(ws.dir('records'), 'calibration_result.json')


def settings(ws) -> dict:
    s = {'runs': DEFAULT_RUNS, 'seed': 7, 'sigma_psi': SIGMA_PSI, 'sigma_share': SIGMA_SHARE, 'well_radius_ft': WELL_RADIUS_FT, 'sample_sealing': True, 'ranges': {}}
    p = _path(ws)
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            s.update(json.load(f))
    return s


def set_settings(ws, actor: str, runs=None, sigma_psi=None, sigma_share=None, sample_sealing=None, well_radius_ft=None, seed=None) -> dict:
    with _LOCK:
        s = settings(ws)
        if runs not in (None, ''):
            v = int(runs)
            if not 8 <= v <= 200:
                raise CalibrationError('the runs are 8 to 200')
            s['runs'] = v
        if sigma_psi not in (None, ''):
            v = float(sigma_psi)
            if not 0.1 <= v <= 1000.0:
                raise CalibrationError('the pressure error is 0.1 to 1,000 psi')
            s['sigma_psi'] = v
        if sigma_share not in (None, ''):
            v = float(sigma_share)
            if not 0.005 <= v <= 0.5:
                raise CalibrationError('the share error is 0.005 to 0.5 (half a point to fifty points of share)')
            s['sigma_share'] = v
        if well_radius_ft not in (None, ''):
            v = float(well_radius_ft)
            if not 0.1 <= v <= 2.0:
                raise CalibrationError('the well radius is 0.1 to 2 ft')
            s['well_radius_ft'] = v
        if seed not in (None, ''):
            s['seed'] = int(seed)
        if sample_sealing is not None and sample_sealing != '':
            s['sample_sealing'] = sample_sealing in (True, 'true', 'yes', 'on', '1', 1)
        s['declared_by'] = actor; s['declared_utc'] = _utc()
        _write(_path(ws), s)
    ws.audit(actor, 'calibration.settings', {k: s.get(k) for k in ('runs', 'sigma_psi', 'sigma_share', 'sample_sealing', 'well_radius_ft', 'seed')})
    return s


def set_range(ws, actor: str, formation: str, parameter: str, low, high, source: str = '') -> dict:
    """An uncertain number's range for a formation, declared with its source; low and high both blank removes it."""
    if parameter not in DEFAULT_SPREAD:
        raise CalibrationError(f'the parameter is one of {tuple(DEFAULT_SPREAD)}, got {parameter!r}')
    formation = (formation or '').strip()
    if not formation:
        raise CalibrationError('a range is for a formation: name it')
    with _LOCK:
        s = settings(ws)
        fr = s['ranges'].setdefault(formation, {})
        if low in (None, '') and high in (None, ''):
            if parameter not in fr:
                raise CalibrationError(f'no range declared for {parameter} of {formation}')
            del fr[parameter]
        else:
            try:
                lo, hi = float(low), float(high)
            except (TypeError, ValueError):
                raise CalibrationError('a range needs a low and a high number')
            if not (0 < lo < hi):
                raise CalibrationError('a range is 0 < low < high')
            if not (source or '').strip():
                raise CalibrationError('a range needs its source (--source "core report 2024, 12 plugs")')
            fr[parameter] = {'low': lo, 'high': hi, 'source': source, 'declared_by': actor, 'declared_utc': _utc()}
        _write(_path(ws), s)
    ws.audit(actor, 'calibration.range', {'formation': formation, 'parameter': parameter, 'range': s['ranges'].get(formation, {}).get(parameter)})
    return s


def _write(path: str, obj) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------------------------------
# the scene: everything a run needs, taken once from the ledger
# --------------------------------------------------------------------------------------------------------------
def make_scene(formation: str, fm: dict, wells: List[dict], faults: List[dict], threshold_psi: float = PM.DEFAULT_THRESHOLD_PSI, front_projection_days: int = FR.DEFAULT_PROJECTION_DAYS,
               slip: Optional[Dict[str, float]] = None, events: Optional[List[dict]] = None) -> dict:
    """`wells` are the formation's ledger wells (days, role, surface); `faults` the fault segments in the main injector's
    frame as `plume.fault_segments` gives them (each with its layer); `slip` each fault's pressure to slip at ambient
    pressure in the worst declared case, by name, when a stress state is declared."""
    placed = [w for w in wells if w.get('days') and (w.get('surface') or {}).get('lat') is not None]
    inj = [w for w in placed if w.get('role') == 'injection' and any(d['volume_bbl'] for d in w['days'] if d['volume_bbl'] is not None)]
    if not inj:
        raise CalibrationError('no injection well with a volume record and a surface position in this formation')
    for k_ in ('k_md', 'thickness_ft', 'porosity'):
        if fm.get(k_) is None:
            raise CalibrationError(f'the formation has no {k_.replace("_", " ")} declared')
    main = inj[0]
    lat0, lon0 = PM._wgs(main['surface'])
    frame = PM._frame(lat0, lon0)
    wxy = {}
    for w in placed:
        la, lo = PM._wgs(w['surface']); wxy[w['well']] = PM._xy_ft(frame, la, lo)
    wxy[main['well']] = (0.0, 0.0)
    flow_days = sorted({d['date'] for w in placed for d in w['days'] if d['volume_bbl'] is not None})
    d0 = datetime.strptime(flow_days[0], '%Y-%m-%d'); d1 = datetime.strptime(flow_days[-1], '%Y-%m-%d')
    n_days = (d1 - d0).days + 1
    rate = {w['well']: np.zeros(n_days) for w in placed}
    for w in placed:
        for d in w['days']:
            if d['volume_bbl'] is not None:
                rate[w['well']][(datetime.strptime(d['date'], '%Y-%m-%d') - d0).days] += d['volume_bbl']
    # the measurements
    pressures, waters = [], []
    idays = [d for d in main['days'] if d['volume_bbl'] is not None]
    inj_tds = FR._weighted(idays, 'tds_mgl'); inj_sg = FR._weighted(idays, 'sg')
    fm_tds = fm.get('water_tds_mgl'); fm_sg = fm.get('water_sg')
    for w in placed:
        idx, val = [], []
        for d in w['days']:
            if d.get('bhp_psi') is not None:
                i = (datetime.strptime(d['date'], '%Y-%m-%d') - d0).days
                if 0 <= i < n_days:
                    idx.append(i); val.append(float(d['bhp_psi']))
        if len(idx) >= 5:
            pressures.append({'well': w['well'], 'display': w.get('display', w['well']), 'role': w.get('role'), 'days': np.asarray(idx), 'psi': np.asarray(val),
                              'kind': ('gauge' if w.get('role') == 'observation' else 'bottomhole')})
        if w.get('role') in ('production', 'observation') and inj_tds is not None and fm_tds is not None and abs(inj_tds - fm_tds) > 1.0:
            idx, val = [], []
            last = None
            for d in w['days']:
                if d.get('tds_mgl') is not None and d.get('density_from') == 'the report' and d['tds_mgl'] != last:
                    last = d['tds_mgl']
                    i = (datetime.strptime(d['date'], '%Y-%m-%d') - d0).days
                    if 0 <= i < n_days:
                        idx.append(i); val.append((float(d['tds_mgl']) - fm_tds) / (inj_tds - fm_tds))
            if idx:
                waters.append({'well': w['well'], 'display': w.get('display', w['well']), 'role': w.get('role'), 'days': np.asarray(idx), 'share': np.asarray(val)})
    return {'formation': formation, 'fm': dict(fm), 'main': main['well'], 'main_display': main.get('display', main['well']), 'frame': {'lat0': lat0, 'lon0': lon0},
            'wells': [{'well': w['well'], 'display': w.get('display', w['well']), 'role': w.get('role')} for w in placed], 'wxy': wxy, 'rate': rate, 'n_days': n_days,
            'start': flow_days[0], 'end': flow_days[-1], 'faults': faults, 'layers': sorted({f['layer'] for f in faults if f.get('segments')}),
            'threshold_psi': float(threshold_psi), 'front_projection_days': int(front_projection_days), 'pressures': pressures, 'waters': waters,
            'dsg': ((inj_sg - fm_sg) if (inj_sg is not None and fm_sg is not None) else None), 'mu': float(fm.get('mu_cp') or FL.MU_WATER_CP),
            'slip': dict(slip or {}), 'events': list(events or [])}


def run_plume(sc: dict, k: float, h: float, phi: float, ct: float, sealing: frozenset, well_radius_ft: float = WELL_RADIUS_FT, n: int = PLUME_CELLS) -> dict:
    """One plume: the rise at every placed well's cell each day (with its own draw for a flowing well), the area of review
    now and in a year continued, each fault's largest rise at every thirtieth day and in a year."""
    mu = sc['mu']; n_days = sc['n_days']
    r_inv = FL.radius_of_investigation_ft(k, (n_days + 365) * 24.0, phi, mu, ct)
    far = max(math.hypot(x, y) for x, y in sc['wxy'].values())
    half = max(2.0 * r_inv, 1.2 * far + 2000.0, 5000.0)
    dx = 2.0 * half / (n - 1)
    g = PM.Grid(n, dx, h, phi, k, mu, ct)
    for f in sc['faults']:
        if f['layer'] in sealing:
            for sg in f['segments']:
                g.seal_segment(*sg)
    cells = {wid: g.cell_of(x, y) for wid, (x, y) in sc['wxy'].items()}
    cells = {wid: c for wid, c in cells.items() if c is not None}
    sub = max(1, int(math.ceil(1.0 / g.dt_max_days())))
    if sub > PM.MAX_SUBSTEPS_PER_DAY:
        return {'ok': False, 'why': f'{sub} sub-steps a day'}
    dp = np.zeros((n, n))
    pw = {wid: np.zeros(n_days) for wid in cells}
    marks = list(range(29, n_days, 30)); marks = marks if (marks and marks[-1] == n_days - 1) else marks + [n_days - 1]
    fser = {i: [] for i, f in enumerate(sc['faults']) if f.get('segments')}
    for i in range(n_days):
        q: Dict[Tuple[int, int], float] = {}
        for wid, c in cells.items():
            q[c] = q.get(c, 0.0) + float(sc['rate'][wid][i])
        dp = PM.step_days(g, dp, q, 1.0, sub)
        for wid, c in cells.items():
            pw[wid][i] = dp[c]
        if i in marks:
            for fi in fser:
                fser[fi].append(PM._sample_fault(g, dp, sc['faults'][fi]['segments']) or 0.0)
    # the flowing wells' own draw on top of their cell: Peaceman's well index
    peace = mu * math.log(max(0.2 * dx / well_radius_ft, 1.0001)) / (2.0 * math.pi * PM.T_CONST * k * h)
    pwf = {wid: pw[wid] + peace * sc['rate'][wid] for wid in cells}
    aor = PM.area_of_review(g, dp, sc['threshold_psi'], 0.0, 0.0)
    last30 = {wid: float(sc['rate'][wid][-30:].mean()) for wid in cells}
    q = {}
    for wid, c in cells.items():
        q[c] = q.get(c, 0.0) + last30[wid]
    dp1 = PM.step_days(g, dp.copy(), q, 365.0, sub * 365)
    aor1 = PM.area_of_review(g, dp1, sc['threshold_psi'], 0.0, 0.0)
    return {'ok': True, 'dx_ft': dx, 'cell': pw, 'pwf': pwf, 'aor_acres': aor['area_acres'], 'aor_reach_ft': aor['reach_ft'], 'aor1_acres': aor1['area_acres'], 'aor1_reach_ft': aor1['reach_ft'],
            'marks': marks, 'fault_series': fser, 'fault_now': {fi: (v[-1] if v else 0.0) for fi, v in fser.items()},
            'fault_1y': {fi: (PM._sample_fault(g, dp1, sc['faults'][fi]['segments']) or 0.0) for fi in fser},
            'gain_1y': {wid: float(dp1[c] - dp[c]) for wid, c in cells.items()}, 'dp_max': float(dp.max())}


def run_front(sc: dict, k: float, h: float, phi: float, sealing: frozenset, n: int = FRONT_CELLS) -> dict:
    """One front around the main injector: every placed well's share of injected water through the record (five-day
    mean rates), the half radius now and at the end of the projection, each producer's arrival and share then."""
    mu = sc['mu']; n_days = sc['n_days']; proj = sc['front_projection_days']
    rate = sc['rate']; main = sc['main']
    last30 = {wid: float(r[-30:].mean()) for wid, r in rate.items()}
    cum_in = float(rate[main].sum())
    r_end = FL.swept_radius_ft(cum_in + max(last30[main], 0.0) * proj, h, phi)
    half = max(2.5 * r_end, 1000.0)
    for wid, (x, y) in sc['wxy'].items():
        d = math.hypot(x, y)
        if wid != main and d <= 4.0 * r_end:
            half = max(half, 1.25 * d)
    dx = 2.0 * half / (n - 1)
    g = PM.Grid(n, dx, h, phi, k, mu, 1.0)
    for f in sc['faults']:
        if f['layer'] in sealing:
            for sg in f['segments']:
                g.seal_segment(*sg)
    unit = {}
    for wid, (x, y) in sc['wxy'].items():
        u = FR.solve_unit(g, x, y, k * h, tol=1e-9)
        if not u['converged']:
            if wid == main:
                return {'ok': False, 'why': 'the injector is enclosed by sealing faults'}
            continue
        unit[wid] = u
    cells = {wid: u['cell'] for wid, u in unit.items() if u['cell'] is not None}
    G = FR.gravity_coefficient(k, mu, sc['dsg'], h) if sc['dsg'] else 0.0
    w = FR.Water(g, dx * dx * h * phi / FR.FT3_PER_BBL, G)
    watch = [wid for wid in cells if wid != main]

    def flow(rates):
        Fx = np.zeros((n, n - 1)); Fy = np.zeros((n - 1, n)); iq = {}; pq = {}
        for wid, q in rates.items():
            if wid not in unit or q == 0.0:
                continue
            Fx += q * unit[wid]['Fx']; Fy += q * unit[wid]['Fy']
            c = cells.get(wid)
            if c is not None:
                if q > 0:
                    iq[c] = iq.get(c, 0.0) + q
                else:
                    pq[c] = pq.get(c, 0.0) - q
        return Fx, Fy, iq, pq
    t_pts = [0]; s_pts = {wid: [0.0] for wid in watch}
    i = 0
    while i < n_days:
        span = min(FRONT_SPAN_DAYS, n_days - i)
        rates = {wid: float(r[i:i + span].mean()) for wid, r in rate.items()}
        if any(rates.values()) or G:
            Fx, Fy, iq, pq = flow(rates)
            w.advance(float(span), Fx, Fy, iq, pq, max_dt=float(span))
        i += span
        t_pts.append(i)
        for wid in watch:
            s_pts[wid].append(float(w.s[cells[wid]]))
    share = {wid: np.interp(np.arange(1, n_days + 1), t_pts, s_pts[wid]) for wid in watch}
    half_now = w.radii(0.0, 0.0)['half_equivalent_ft']
    arrival = {}
    for wid in watch:
        a = np.nonzero(share[wid] >= FR.ARRIVAL_SHARE)[0]
        arrival[wid] = float(a[0] + 1) if a.size else None
    Fx, Fy, iq, pq = flow(last30)
    t = 0; share_1y = {}
    prev = {wid: float(w.s[cells[wid]]) for wid in watch}
    while t < proj:
        span = min(FRONT_PROJ_SPAN_DAYS, proj - t)
        w.advance(float(span), Fx, Fy, iq, pq, max_dt=10.0)
        t += span
        for wid in watch:
            v = float(w.s[cells[wid]])
            if arrival[wid] is None and v >= FR.ARRIVAL_SHARE:
                b = prev[wid]
                arrival[wid] = n_days + t - span + (FR.ARRIVAL_SHARE - b) / max(v - b, 1e-12) * span
            prev[wid] = v
        if t >= 365 and not share_1y:
            share_1y = dict(prev)
    return {'ok': True, 'dx_ft': dx, 'share': share, 'half_now_ft': half_now, 'half_end_ft': w.radii(0.0, 0.0)['half_equivalent_ft'], 'arrival_day': arrival,
            'share_now': {wid: float(share[wid][-1]) for wid in watch}, 'share_1y': share_1y or dict(prev), 'share_end': dict(prev), 'in_grid': sorted(cells)}


# --------------------------------------------------------------------------------------------------------------
# the ranges, the sample, the misfit
# --------------------------------------------------------------------------------------------------------------
def parameter_ranges(fm: dict, declared: Optional[dict] = None) -> Dict[str, dict]:
    """Each uncertain number's centre (the formation's declaration), its range (declared with a source, or the default
    spread about the centre) and how it is sampled."""
    sd = FL.storage_declared(fm)
    centre = {'k_md': float(fm['k_md']), 'thickness_ft': float(fm['thickness_ft']), 'porosity': float(fm['porosity']), 'c_t_per_psi': float(sd['c_t_per_psi'])}
    out = {}
    for name, (how, spread) in DEFAULT_SPREAD.items():
        c = centre[name]
        d = (declared or {}).get(name)
        if d:
            lo, hi, src = float(d['low']), float(d['high']), d.get('source', '')
            kind = 'declared'
        elif how == 'log':
            lo, hi, src, kind = c / spread, c * spread, f'default: a factor of {spread:g} either way of the declared {c:g}', 'default'
        else:
            lo, hi, src, kind = c * (1 - spread), c * (1 + spread), f'default: {spread * 100:.0f}% either way of the declared {c:g}', 'default'
        if name == 'porosity':
            hi = min(hi, 0.45)
        out[name] = {'centre': c, 'low': lo, 'high': hi, 'scale': how, 'source': src, 'kind': kind, 'words': PARAM_WORDS[name],
                     'centre_inside': lo <= c <= hi}
    return out


def sample(ranges: Dict[str, dict], layers: List[str], declared_sealing: List[str], runs: int, seed: int, sample_sealing: bool) -> List[dict]:
    """A Latin hypercube over the ranges, fixed by its seed; run 0 is the declared case."""
    rng = np.random.default_rng(int(seed))
    names = list(ranges)
    out = [{'run': 0, 'declared_case': True, **{nm: ranges[nm]['centre'] for nm in names}, 'sealing': sorted(l for l in layers if l in declared_sealing)}]
    m = runs - 1
    cols = {}
    for nm in names:
        u = (rng.permutation(m) + rng.random(m)) / m
        r = ranges[nm]
        cols[nm] = np.exp(np.log(r['low']) + u * (np.log(r['high']) - np.log(r['low']))) if r['scale'] == 'log' else r['low'] + u * (r['high'] - r['low'])
    seal_cols = {}
    for l in layers:
        if sample_sealing:
            v = np.arange(m) % 2 == 0
            seal_cols[l] = rng.permutation(v)
        else:
            seal_cols[l] = np.full(m, l in declared_sealing)
    for i in range(m):
        out.append({'run': i + 1, 'declared_case': False, **{nm: float(cols[nm][i]) for nm in names}, 'sealing': sorted(l for l in layers if seal_cols[l][i])})
    return out


def _nrms(pred: np.ndarray, meas: np.ndarray, sigma: float, demean: bool) -> float:
    if demean:
        pred = pred - pred.mean(); meas = meas - meas.mean()
    return float(np.sqrt(np.mean(((pred - meas) / sigma) ** 2)))


def _sig(x: float, n: int = 4) -> float:
    """x to n significant figures."""
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        return x
    return round(x, n - 1 - int(math.floor(math.log10(abs(x)))))


def _pct(vals: List[float]) -> Optional[dict]:
    v = np.asarray([x for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))], dtype=float)
    if v.size == 0:
        return None
    return {'p10': _sig(np.percentile(v, 10)), 'p50': _sig(np.percentile(v, 50)), 'p90': _sig(np.percentile(v, 90)), 'lo': _sig(v.min()), 'hi': _sig(v.max()), 'n': int(v.size)}


def _width(p: Optional[dict], log: bool = False) -> Optional[float]:
    if not p:
        return None
    if log:
        return math.log(max(p['p90'], 1e-300) / max(p['p10'], 1e-300))
    return p['p90'] - p['p10']


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 4 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float); rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def _narrowing(y: np.ndarray, target: np.ndarray, sigma: float) -> float:
    """How much a reading y with error sigma would narrow the target across these runs: 1 - sqrt(1 - rho^2 v / (v + sigma^2))."""
    v = float(np.var(y))
    if v <= 0:
        return 0.0
    rho = _spearman(y, target)
    return 1.0 - math.sqrt(max(0.0, 1.0 - rho * rho * v / (v + sigma * sigma)))


def calibrate(sc: dict, ranges: Dict[str, dict], samples: List[dict], sigma_psi: float = SIGMA_PSI, sigma_share: float = SIGMA_SHARE, well_radius_ft: float = WELL_RADIUS_FT) -> dict:
    """Every sample run and scored; the kept runs; the ranges in and out; what to measure next."""
    runs = []
    for smp in samples:
        seal = frozenset(smp['sealing'])
        pl = run_plume(sc, smp['k_md'], smp['thickness_ft'], smp['porosity'], smp['c_t_per_psi'], seal, well_radius_ft)
        fr = run_front(sc, smp['k_md'], smp['thickness_ft'], smp['porosity'], seal)
        row: Dict[str, Any] = {**smp, 'ok': bool(pl.get('ok') and fr.get('ok')), 'why': pl.get('why') or fr.get('why'), 'plume': pl, 'front': fr, 'misfits': {}}
        if row['ok']:
            for m in sc['pressures']:
                series = pl['cell'] if m['kind'] == 'gauge' else pl['pwf']
                if m['well'] in series:
                    row['misfits']['p:' + m['well']] = _nrms(series[m['well']][m['days']], m['psi'], sigma_psi, demean=True)
            for m in sc['waters']:
                if m['well'] in fr['share']:
                    row['misfits']['w:' + m['well']] = _nrms(fr['share'][m['well']][m['days']], np.clip(m['share'], -0.2, 1.2), sigma_share, demean=False)
            row['misfit'] = (math.sqrt(sum(v * v for v in row['misfits'].values()) / len(row['misfits'])) if row['misfits'] else None)
        runs.append(row)
    good = [r for r in runs if r['ok']]
    if not good:
        raise CalibrationError('no run could be stepped: ' + '; '.join(sorted({str(r['why']) for r in runs})))
    measured = any(r['misfits'] for r in good)
    if measured:
        best = min(r['misfit'] for r in good)
        cut = max(KEEP_MISFIT, KEEP_OF_BEST * best)
        kept = [r for r in good if r['misfit'] <= cut]
        status = 'NO RUN FITS' if best > NO_FIT else 'CALIBRATED'
    else:
        best = None; cut = None; kept = list(good); status = 'NO MEASUREMENT'
    names = list(ranges)
    # the uncertain numbers: in, kept
    params = []
    for nm in names:
        log = ranges[nm]['scale'] == 'log'
        pa = _pct([r[nm] for r in good]); pk = _pct([r[nm] for r in kept])
        wa, wk = _width(pa, log), _width(pk, log)
        params.append({'parameter': nm, 'words': ranges[nm]['words'], 'declared': ranges[nm]['centre'], 'low': ranges[nm]['low'], 'high': ranges[nm]['high'], 'range_kind': ranges[nm]['kind'],
                       'source': ranges[nm]['source'], 'all': pa, 'kept': pk, 'narrowed': (round(1.0 - wk / wa, 3) if (wa and wk is not None and measured) else None),
                       'best': (min(good, key=lambda r: r['misfit'])[nm] if measured else None)})
    # the products the formation actually writes: transmissivity k h, storage h phi c_t, pore thickness h phi
    for nm, fn, words in (('k_h', lambda r: r['k_md'] * r['thickness_ft'], 'transmissivity k h, md ft'), ('h_phi', lambda r: r['thickness_ft'] * r['porosity'], 'pore thickness h phi, ft'),
                          ('h_phi_ct', lambda r: r['thickness_ft'] * r['porosity'] * r['c_t_per_psi'], 'storage h phi c_t, ft/psi')):
        pa = _pct([fn(r) for r in good]); pk = _pct([fn(r) for r in kept])
        wa, wk = _width(pa, True), _width(pk, True)
        params.append({'parameter': nm, 'words': words, 'declared': fn(good[0]) if good[0].get('declared_case') else None, 'derived': True, 'all': pa, 'kept': pk,
                       'narrowed': (round(1.0 - wk / wa, 3) if (wa and wk is not None and measured) else None), 'best': (fn(min(good, key=lambda r: r['misfit'])) if measured else None)})
    layers = sc['layers']
    sealing = [{'layer': l, 'sealing_share_all': round(sum(1 for r in good if l in r['sealing']) / len(good), 3), 'sealing_share_kept': round(sum(1 for r in kept if l in r['sealing']) / len(kept), 3)} for l in layers]
    # the forecasts
    def rng_(fn, log=False):
        a = _pct([fn(r) for r in good]); k_ = _pct([fn(r) for r in kept])
        wa, wk = _width(a, log), _width(k_, log)
        return {'all': a, 'kept': k_, 'declared_case': (fn(good[0]) if good[0].get('declared_case') else None), 'narrowed': (round(1.0 - wk / wa, 3) if (wa and wk is not None and measured and wa > 0) else None)}
    fc: Dict[str, Any] = {'aor_acres': rng_(lambda r: r['plume']['aor_acres']), 'aor_reach_ft': rng_(lambda r: r['plume']['aor_reach_ft']),
                          'aor_1y_acres': rng_(lambda r: r['plume']['aor1_acres']), 'aor_1y_reach_ft': rng_(lambda r: r['plume']['aor1_reach_ft']),
                          'front_half_ft': rng_(lambda r: r['front']['half_now_ft']), 'front_half_end_ft': rng_(lambda r: r['front']['half_end_ft'])}
    ffc = []
    for fi, f in enumerate(sc['faults']):
        if not f.get('segments'):
            continue
        pts = sc['slip'].get(f['name'])
        row = {'name': f['name'], 'layer': f['layer'], 'distance_km': f.get('distance_km'), 'rise_now_psi': rng_(lambda r, fi=fi: r['plume']['fault_now'].get(fi, 0.0)),
               'rise_1y_psi': rng_(lambda r, fi=fi: r['plume']['fault_1y'].get(fi, 0.0)), 'pressure_to_slip_psi': pts}
        if pts is not None:
            row['past_line_now_share'] = round(sum(1 for r in kept if r['plume']['fault_now'].get(fi, 0.0) >= pts) / len(kept), 3)
            row['past_line_1y_share'] = round(sum(1 for r in kept if r['plume']['fault_1y'].get(fi, 0.0) >= pts) / len(kept), 3)
        ffc.append(row)
    fc['faults'] = ffc
    wfc = []
    for w in sc['wells']:
        wid = w['well']
        if wid == sc['main']:
            continue
        row = {'well': wid, 'display': w['display'], 'role': w['role'], 'distance_ft': round(math.hypot(*sc['wxy'][wid]), 0),
               'rise_now_psi': rng_(lambda r, wid=wid: (float(r['plume']['cell'][wid][-1]) if wid in r['plume']['cell'] else None))}
        n_in = sum(1 for r in kept if wid in r['front']['share'])
        if n_in:
            arr = [r['front']['arrival_day'].get(wid) for r in kept if wid in r['front']['share']]
            row.update(share_now=rng_(lambda r, wid=wid: r['front']['share_now'].get(wid)), share_end=rng_(lambda r, wid=wid: r['front']['share_end'].get(wid)),
                       arrival_day=_pct([a for a in arr if a is not None]), arrives_share_of_kept=round(sum(1 for a in arr if a is not None) / len(kept), 3),
                       on_front_grid_share=round(n_in / len(kept), 3))
        wfc.append(row)
    fc['wells'] = wfc
    # what to measure next: how far the kept runs disagree about a reading, and how much it would narrow the forecasts
    # (across the kept runs when eight or more were kept; else across the eight that fit best, said so)
    if len(kept) >= 8 or not measured:
        basis, next_basis = kept, 'the kept runs'
    else:
        basis = sorted(good, key=lambda r: r['misfit'])[:8]; next_basis = f'the eight runs that fit best (only {len(kept)} kept)'
    kept_all, kept = kept, basis
    t_aor = np.log(np.maximum([r['plume']['aor1_acres'] for r in kept], 1.0))
    t_front = np.asarray([r['front']['half_end_ft'] for r in kept], dtype=float)
    have_p = {m['well'] for m in sc['pressures']}; have_w = {m['well'] for m in sc['waters']}
    cand = []
    y = np.log([r['k_md'] * r['thickness_ft'] for r in kept])
    cand.append({'what': f"a falloff test on {sc['main_display']}", 'reads': 'the transmissivity k h', 'sigma': f'{SIGMA_LN_KH * 100:.0f}%', 'spread_in_sigmas': round(float(np.std(y)) / SIGMA_LN_KH, 2),
                 'narrows_aor': round(_narrowing(y, t_aor, SIGMA_LN_KH), 3), 'narrows_front': round(_narrowing(y, t_front, SIGMA_LN_KH), 3)})
    if sc['main'] not in have_p:
        y = np.asarray([float(np.std(r['plume']['pwf'][sc['main']])) for r in kept])
        cand.append({'what': f"the bottomhole pressure of {sc['main_display']} through the record", 'reads': 'how its pressure moves with its rate and with time', 'sigma': f'{sigma_psi:g} psi',
                     'spread_in_sigmas': round(float(np.std(y)) / sigma_psi, 2), 'narrows_aor': round(_narrowing(y, t_aor, sigma_psi), 3), 'narrows_front': round(_narrowing(y, t_front, sigma_psi), 3)})
    for w in sc['wells']:
        wid = w['well']
        if wid == sc['main']:
            continue
        if wid not in have_p and all(wid in r['plume']['gain_1y'] for r in kept):
            y = np.asarray([r['plume']['gain_1y'][wid] for r in kept])
            cand.append({'what': f"a gauge at {w['display']} for a year", 'reads': 'the rise there over the next year', 'sigma': f'{sigma_psi:g} psi', 'spread_in_sigmas': round(float(np.std(y)) / sigma_psi, 2),
                         'narrows_aor': round(_narrowing(y, t_aor, sigma_psi), 3), 'narrows_front': round(_narrowing(y, t_front, sigma_psi), 3)})
        if wid not in have_w and w['role'] in ('production', 'observation') and sc['dsg'] is not None and all(wid in r['front']['share_1y'] for r in kept):
            y = np.asarray([r['front']['share_1y'][wid] for r in kept])
            cand.append({'what': f"a water analysis at {w['display']} in a year", 'reads': 'its share of injected water then', 'sigma': f'{sigma_share * 100:g} points of share',
                         'spread_in_sigmas': round(float(np.std(y)) / sigma_share, 2), 'narrows_aor': round(_narrowing(y, t_aor, sigma_share), 3), 'narrows_front': round(_narrowing(y, t_front, sigma_share), 3)})
    for c in cand:
        c['tells_nothing'] = c['spread_in_sigmas'] < 0.25          # the kept runs agree about it to within a quarter of its error
    cand.sort(key=lambda c: (c['tells_nothing'], -(c['narrows_aor'] + c['narrows_front'])))
    kept = kept_all
    # the measurements with their envelopes
    meas = []
    for m in sc['pressures']:
        key = 'p:' + m['well']; kind = m['kind']
        def ser(r, m=m, kind=kind):
            s_ = (r['plume']['cell'] if kind == 'gauge' else r['plume']['pwf'])[m['well']][m['days']]
            return s_ - s_.mean()
        A = np.asarray([ser(r) for r in good if key in r['misfits']]); K = np.asarray([ser(r) for r in kept if key in r['misfits']])
        if not A.size:
            continue
        step = max(1, len(m['days']) // 120)
        b = min((r for r in good if key in r['misfits']), key=lambda r: r['misfit'])
        meas.append({'key': key, 'well': m['well'], 'display': m['display'], 'kind': ('the gauge' if kind == 'gauge' else 'the bottomhole pressure') + ', its mean taken off', 'unit': 'psi', 'sigma': sigma_psi, 'n': int(len(m['days'])),
                     'days': [int(x) + 1 for x in m['days'][::step]], 'measured': [round(float(x), 1) for x in (m['psi'] - m['psi'].mean())[::step]],
                     'all_lo': [round(float(x), 1) for x in A.min(axis=0)[::step]], 'all_hi': [round(float(x), 1) for x in A.max(axis=0)[::step]],
                     'kept_lo': [round(float(x), 1) for x in K.min(axis=0)[::step]] if K.size else [], 'kept_hi': [round(float(x), 1) for x in K.max(axis=0)[::step]] if K.size else [],
                     'best': [round(float(x), 1) for x in ser(b)[::step]], 'best_misfit': round(b['misfits'][key], 2), 'declared_misfit': (round(good[0]['misfits'][key], 2) if good[0].get('declared_case') and key in good[0]['misfits'] else None)})
    for m in sc['waters']:
        key = 'w:' + m['well']
        A = np.asarray([r['front']['share'][m['well']][m['days']] for r in good if key in r['misfits']]); K = np.asarray([r['front']['share'][m['well']][m['days']] for r in kept if key in r['misfits']])
        if not A.size:
            continue
        b = min((r for r in good if key in r['misfits']), key=lambda r: r['misfit'])
        meas.append({'key': key, 'well': m['well'], 'display': m['display'], 'kind': 'the share of injected water its TDS implies', 'unit': 'share', 'sigma': sigma_share, 'n': int(len(m['days'])),
                     'days': [int(x) + 1 for x in m['days']], 'measured': [round(float(x), 4) for x in m['share']], 'all_lo': [round(float(x), 4) for x in A.min(axis=0)], 'all_hi': [round(float(x), 4) for x in A.max(axis=0)],
                     'kept_lo': [round(float(x), 4) for x in K.min(axis=0)] if K.size else [], 'kept_hi': [round(float(x), 4) for x in K.max(axis=0)] if K.size else [],
                     'best': [round(float(x), 4) for x in b['front']['share'][m['well']][m['days']]], 'best_misfit': round(b['misfits'][key], 2),
                     'declared_misfit': (round(good[0]['misfits'][key], 2) if good[0].get('declared_case') and key in good[0]['misfits'] else None)})
    # the earthquakes: the rise the kept runs put on the nearest mapped fault that month
    quakes = []
    for ev in sc['events']:
        fi = ev['fault_index']; di = ev['day']
        vals = []
        for r in kept:
            mk = r['plume']['marks']; ser_ = r['plume']['fault_series'].get(fi) or []
            if ser_:
                vals.append(float(np.interp(di, mk, ser_)) if di >= mk[0] else float(ser_[0]) * di / max(mk[0], 1))
        if vals:
            quakes.append({'event_id': ev['event_id'], 'time': ev['time'], 'magnitude': ev['magnitude'], 'fault': sc['faults'][fi]['name'], 'distance_km': ev['distance_km'], 'day': di + 1,
                           'rise_psi': _pct(vals), 'pressure_to_slip_psi': sc['slip'].get(sc['faults'][fi]['name'])})
    table = [{'run': r['run'], 'declared_case': r.get('declared_case', False), **{nm: r[nm] for nm in names}, 'sealing': r['sealing'], 'ok': r['ok'], 'why': r.get('why'),
              'misfit': (round(r['misfit'], 3) if r.get('misfit') is not None else None), 'misfits': {k_: round(v, 3) for k_, v in r['misfits'].items()}, 'kept': r in kept,
              'aor_1y_acres': (r['plume']['aor1_acres'] if r['ok'] else None), 'front_half_end_ft': (r['front']['half_end_ft'] if r['ok'] else None)} for r in runs]
    return {'status': status, 'measured': measured, 'n_runs': len(runs), 'n_ok': len(good), 'n_kept': len(kept), 'best_misfit': (round(best, 3) if best is not None else None),
            'keep_cut': (round(cut, 3) if cut is not None else None), 'declared_misfit': (round(good[0]['misfit'], 3) if (measured and good[0].get('declared_case') and good[0].get('misfit') is not None) else None),
            'declared_kept': bool(good[0].get('declared_case') and good[0] in kept), 'parameters': params, 'sealing': sealing, 'forecasts': fc, 'next': cand, 'next_basis': next_basis, 'measurements': meas, 'earthquakes': quakes,
            'runs': sorted(table, key=lambda r: (r['misfit'] is None, r['misfit'] if r['misfit'] is not None else 0.0, r['run'])),
            'grids': {'plume_cells': PLUME_CELLS, 'front_cells': FRONT_CELLS, 'front_span_days': FRONT_SPAN_DAYS,
                      'plume_dx_ft': _pct([r['plume']['dx_ft'] for r in good]), 'front_dx_ft': _pct([r['front']['dx_ft'] for r in good])}}


def summary_lines(sc: dict, res: dict, sigma_psi: float, sigma_share: float) -> List[str]:
    L = []
    nm = sc['formation']; fc = res['forecasts']
    L.append(f"{nm}: {res['n_runs']} runs over the ranges ({res['n_ok']} stepped), {sc['n_days']} days of record ({sc['start']} to {sc['end']}), around {sc['main_display']}; "
             f"plume on {PLUME_CELLS} cells, front on {FRONT_CELLS} with {FRONT_SPAN_DAYS}-day mean rates. SIMULATION.")
    if not res['measured']:
        L.append('NO MEASUREMENT: no well of this formation has a bottomhole pressure or a measured TDS in its record, so every run is kept - the ranges below are what the declarations alone allow.')
    else:
        ms = '; '.join(f"{m['display']} ({m['kind']}, {m['n']} point(s), best {m['best_misfit']:g}" + (f", the declared case {m['declared_misfit']:g}" if m['declared_misfit'] is not None else '') + ')' for m in res['measurements'])
        L.append(f"Measured against: {ms} - misfits in units of the measurement error ({sigma_psi:g} psi, {sigma_share * 100:g} points of share).")
        if res['status'] == 'NO RUN FITS':
            L.append(f"NO RUN FITS: the best run's misfit is {res['best_misfit']:g}, over {NO_FIT:g}. The declared ranges cannot make these measurements - look outside them: a boundary or a second layer, a leak, a wrong record, a well not in the ledger. "
                     f"The {res['n_kept']} nearest run(s) are shown, and are not to be leaned on.")
        else:
            L.append(f"CALIBRATED: {res['n_kept']} of {res['n_ok']} runs kept (misfit within {res['keep_cut']:g}; the best {res['best_misfit']:g}). The declared case's misfit is {res['declared_misfit']:g}: "
                     + ('it is among the kept.' if res['declared_kept'] else 'it is NOT among the kept - the measurements say the declared numbers are not the formation\'s.'))
    def rg(d, unit='', f='{:,.0f}'):
        a, k_ = d['all'], d['kept']
        if not a or not k_:
            return 'not computed'
        return (f.format(k_['p10']) + ' to ' + f.format(k_['p90']) + unit + f" (median {f.format(k_['p50'])})" + (f"; the declarations alone allow {f.format(a['p10'])} to {f.format(a['p90'])}" if res['measured'] else '')
                + (f", narrowed {d['narrowed'] * 100:.0f}%" if d.get('narrowed') is not None and d['narrowed'] > 0.02 else ''))
    L.append(f"Area of review over {sc['threshold_psi']:g} psi: now {rg(fc['aor_acres'], ' acres')}; in a year, continued, {rg(fc['aor_1y_acres'], ' acres')}, reaching {rg(fc['aor_1y_reach_ft'], ' ft')}.")
    L.append(f"The injected water's half radius: now {rg(fc['front_half_ft'], ' ft')}; at the end of the projection {rg(fc['front_half_end_ft'], ' ft')}.")
    for p in res['parameters']:
        if p.get('derived') and res['measured'] and p['narrowed'] is not None:
            L.append(f"{p['words']}: kept {p['kept']['p10']:g} to {p['kept']['p90']:g} of {p['all']['p10']:g} to {p['all']['p90']:g}" + (f" (declared {p['declared']:g})" if p.get('declared') is not None else '') + f", narrowed {max(p['narrowed'], 0) * 100:.0f}%.")
    for s_ in res['sealing']:
        if s_['sealing_share_all'] not in (0.0, 1.0):
            L.append(f"{s_['layer']}: sealing in {s_['sealing_share_kept'] * 100:.0f}% of the kept runs (of {s_['sealing_share_all'] * 100:.0f}% of all)"
                     + (' - the measurements favour it sealing.' if s_['sealing_share_kept'] > 0.75 and res['measured'] else ' - the measurements favour it open.' if s_['sealing_share_kept'] < 0.25 and res['measured'] else ' - the measurements do not tell.' if res['measured'] else '.'))
    for f in fc['faults'][:6]:
        k_ = f['rise_1y_psi']['kept']
        L.append(f"{f['name']}: the rise in a year {k_['p10']:,.0f} to {k_['p90']:,.0f} psi"
                 + (f"; past the friction line ({f['pressure_to_slip_psi']:,.0f} psi to slip, worst declared case) in {f['past_line_now_share'] * 100:.0f}% of the kept runs now and {f['past_line_1y_share'] * 100:.0f}% in a year - a share of runs, not a probability" if f.get('pressure_to_slip_psi') is not None else '') + '.')
    for w in fc['wells']:
        if w.get('arrival_day') is not None or w.get('arrives_share_of_kept'):
            a = w.get('arrival_day')
            L.append(f"{w['display']} ({w['distance_ft']:,.0f} ft): the injected water arrives in {w['arrives_share_of_kept'] * 100:.0f}% of the kept runs" + (f", between day {a['p10']:,.0f} and day {a['p90']:,.0f}" if a else '')
                     + (f"; its share now {w['share_now']['kept']['p10'] * 100:.0f} to {w['share_now']['kept']['p90'] * 100:.0f}%" if w.get('share_now') and w['share_now']['kept'] else '') + '.')
    useful = [c for c in res['next'] if not c['tells_nothing']]
    if useful:
        c = useful[0]
        L.append(f"What to measure next: {c['what']} - {res['next_basis']} disagree about it by {c['spread_in_sigmas']:g} times its error ({c['sigma']}); it would narrow the area of review by about {c['narrows_aor'] * 100:.0f}% and the front by about {c['narrows_front'] * 100:.0f}%."
                 + (f" Then: {useful[1]['what']} ({useful[1]['narrows_aor'] * 100:.0f}% and {useful[1]['narrows_front'] * 100:.0f}%)." if len(useful) > 1 else ''))
    nothing = [c['what'] for c in res['next'] if c['tells_nothing']]
    if nothing:
        L.append(f"Would tell nothing new ({res['next_basis']} agree about it within a quarter of its error): " + '; '.join(nothing) + '.')
    for q in res['earthquakes'][:5]:
        L.append(f"M {q['magnitude']:g} on {q['time'][:10]}, {q['distance_km']:g} km from {q['fault']}: the kept runs put the rise on that fault at {q['rise_psi']['p10']:,.0f} to {q['rise_psi']['p90']:,.0f} psi that month"
                 + (f" ({q['pressure_to_slip_psi']:,.0f} psi to slip in the worst declared case)" if q.get('pressure_to_slip_psi') is not None else '') + ' - a coincidence in the model\'s terms, not a cause.')
    L.append('A kept run is one the measurements do not contradict on uniform rock, not the truth; the grids are coarser than the pages\'.')
    return L


# --------------------------------------------------------------------------------------------------------------
# the workspace: the inputs' hash, the run, the page, the report
# --------------------------------------------------------------------------------------------------------------
def _inputs(ws) -> Tuple[str, dict]:
    st = settings(ws); pst = PM.settings(ws); fst = FR.settings(ws)
    fp = FL.page(ws); d = FL.load(ws)
    try:
        stress = ws.stress()
    except Exception:
        stress = None
    try:
        from . import op_seismicity as OS
        cats = [(c.get('file'), c.get('sha256')) for c in OS.catalogs(ws)]
    except Exception:
        cats = []
    key = hashlib.sha256(json.dumps([st, {k_: pst.get(k_) for k_ in ('threshold_psi', 'sealing_layers', 'radius_km')}, fst.get('projection_days'), d['formations'], stress, cats,
                                     [(r_['id'], r_.get('kind')) for r_ in ws.layers()],
                                     [{k_: v for k_, v in w.items() if k_ in ('well', 'role', 'formation', 'days', 'surface', 'disposal')} for w in fp['wells']]],
                                    sort_keys=True, default=str).encode()).hexdigest()
    return key, {'st': st, 'pst': pst, 'fst': fst, 'fp': fp, 'd': d, 'stress': stress}


def _formations(inp: dict) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    for wl in inp['fp']['wells']:
        if wl.get('formation') and wl.get('days'):
            by.setdefault(wl['formation'], []).append(wl)
    return {k_: v for k_, v in by.items() if any(w.get('role') == 'injection' for w in v)}


def _scene_for(ws, name: str, wells: List[dict], inp: dict) -> dict:
    from . import slip as SL
    fm = dict(inp['d']['formations'].get(name, {}))
    main = next(w for w in wells if w.get('role') == 'injection')
    if (main.get('surface') or {}).get('lat') is None:
        raise CalibrationError('the injection well has no declared surface position')
    lat0, lon0 = PM._wgs(main['surface'])
    frame = PM._frame(lat0, lon0)
    faults = PM.fault_segments(ws, frame, lat0, lon0, float(inp['pst'].get('radius_km', 25.0)), [])
    faults = [f for f in faults if f.get('segments')]
    # the pressure to slip of each fault at ambient pressure, worst declared case
    slip = {}
    disp = main.get('disposal') or {}
    stress, az, _src = PM._stress_for(ws, lat0, lon0)
    if stress is not None and az is not None and disp.get('top_interval_ft') is not None:
        t_, b_ = disp.get('top_interval_ft'), disp.get('base_interval_ft')
        depth = (float(t_) + float(b_)) / 2.0 if b_ is not None else float(t_)
        state = SL.stress_at(depth, stress['sv_psi_ft'], stress['shmin_psi_ft'], stress['shmax_psi_ft'], stress.get('pp_psi_ft', SL.PSI_PER_FT_HYDROSTATIC))
        dips = tuple(stress.get('dips_deg', [60.0, 75.0, 90.0])); mus = tuple(stress.get('mus', [0.6]))
        for f in faults:
            own = (f.get('attrs') or {}).get('dip_deg')
            slip[f['name']] = round(min(SL.slip_tendency(f['strike_deg'], d_, state, az, m_, 0.0)['pressure_to_slip_psi'] for d_ in ((own,) if own is not None else dips) for m_ in mus), 1)
    sc = make_scene(name, fm, wells, faults, float(inp['pst'].get('threshold_psi', PM.DEFAULT_THRESHOLD_PSI)), int(inp['fst'].get('projection_days', FR.DEFAULT_PROJECTION_DAYS)), slip)
    # the catalogue's events near a mapped fault, inside the record
    try:
        from . import op_seismicity as OS
        d0 = datetime.strptime(sc['start'], '%Y-%m-%d')
        seen = set()
        for c in OS.catalogs(ws):
            try:
                cat = OS._read_catalog(OS._dir(ws, 'catalogs', c['file']))
            except Exception:
                continue
            for e in cat['events']:
                if e['event_id'] in seen or e.get('lat') is None:
                    continue
                seen.add(e['event_id'])
                t = datetime.strptime(str(e['time'])[:10], '%Y-%m-%d')
                di = (t - d0).days
                if not 0 <= di < sc['n_days']:
                    continue
                x, y = PM._xy_ft(frame, float(e['lat']), float(e['lon']))
                best = None
                for fi, f in enumerate(faults):
                    for x0, y0, x1, y1 in f['segments']:
                        L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
                        tt = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / L2))
                        dist = math.hypot(x - (x0 + tt * (x1 - x0)), y - (y0 + tt * (y1 - y0)))
                        if best is None or dist < best[0]:
                            best = (dist, fi)
                if best and best[0] <= 5000.0 / 0.3048:
                    sc['events'].append({'event_id': e['event_id'], 'time': str(e['time']), 'magnitude': e.get('magnitude'), 'day': di, 'fault_index': best[1], 'distance_km': round(best[0] * 0.3048 / 1000.0, 2)})
        sc['events'].sort(key=lambda e_: -(e_['magnitude'] or 0.0))
    except Exception:
        pass
    return sc


def run(ws, actor: str = 'system', formation: Optional[str] = None) -> dict:
    """Run the ensemble for every formation with an injection ledger (or one), keep the result with the hash of what it
    was computed from, and return it."""
    import time
    t0 = time.time()
    key, inp = _inputs(ws)
    st = inp['st']
    fms = _formations(inp)
    if formation:
        if formation not in fms:
            raise CalibrationError(f'no formation {formation!r} with an injection well in the ledger')
        fms = {formation: fms[formation]}
    if not fms:
        raise CalibrationError('nothing to calibrate: the ledger has no injection well with a formation and a volume record (the Fluid balance tab)')
    out = []
    for name in sorted(fms):
        try:
            sc = _scene_for(ws, name, fms[name], inp)
            ranges = parameter_ranges(sc['fm'], (st.get('ranges') or {}).get(name))
            smp = sample(ranges, sc['layers'], list(inp['pst'].get('sealing_layers', [])), int(st['runs']), int(st['seed']), bool(st.get('sample_sealing', True)))
            res = calibrate(sc, ranges, smp, float(st['sigma_psi']), float(st['sigma_share']), float(st['well_radius_ft']))
            res.update(formation=name, main=sc['main'], main_display=sc['main_display'], period={'start': sc['start'], 'end': sc['end'], 'days': sc['n_days']}, threshold_psi=sc['threshold_psi'],
                       front_projection_days=sc['front_projection_days'], ranges=ranges, label='SIMULATION', lines=summary_lines(sc, res, float(st['sigma_psi']), float(st['sigma_share'])))
        except CalibrationError as e:
            res = {'formation': name, 'status': 'NOT RUN', 'lines': [f'{name}: NOT RUN - {e}'], 'label': 'SIMULATION'}
        out.append(res)
    result = {'key': key, 'generated_utc': _utc(), 'by': actor, 'seconds': round(time.time() - t0, 1), 'settings': st, 'formations': out, 'label': 'SIMULATION'}
    with _LOCK:
        _write(_result_path(ws), result)
    ws.audit(actor, 'calibration.run', {'formations': [(f['formation'], f['status'], f.get('n_kept'), f.get('best_misfit')) for f in out], 'runs': st['runs'], 'seconds': result['seconds']})
    return result


def page(ws) -> dict:
    """The stored result, whether everything it was computed from is still the same, the ranges as they stand, and
    what a run would have to work with."""
    key, inp = _inputs(ws)
    st = inp['st']
    fms = _formations(inp)
    ranges = {}
    for name in sorted(fms):
        fm = inp['d']['formations'].get(name, {})
        if all(fm.get(k_) is not None for k_ in ('k_md', 'thickness_ft', 'porosity')):
            ranges[name] = parameter_ranges(fm, (st.get('ranges') or {}).get(name))
    res = None
    if os.path.isfile(_result_path(ws)):
        with open(_result_path(ws), 'r', encoding='utf-8') as f:
            res = json.load(f)
    status = 'EMPTY' if not fms else ('NOT RUN' if res is None else ('OK' if res.get('key') == key else 'STALE'))
    lines = []
    if status == 'EMPTY':
        lines.append('Nothing to calibrate: the ledger has no injection well with a formation and a volume record (the Fluid balance tab is where they are declared).')
    elif status == 'NOT RUN':
        lines.append(f"Not run yet: {st['runs']} runs for {', '.join(sorted(fms))} (Run the calibration; it takes a fraction of a second a run).")
    else:
        if status == 'STALE':
            lines.append(f"STALE: the ledger, the declarations or the settings have changed since this was run on {res['generated_utc']} - run it again before leaning on it.")
        for f in res['formations']:
            lines.extend(f['lines'])
    return {'status': status, 'result': res, 'settings': st, 'ranges': ranges, 'formations': sorted(fms), 'label': 'SIMULATION', 'lines': lines,
            'parameters': [{'parameter': k_, 'words': v} for k_, v in PARAM_WORDS.items()]}


def text(p: dict) -> str:
    out = ['Calibration (SIMULATION)', '=' * 24]
    out.extend(p['lines'])
    res = p.get('result')
    if res:
        for f in res['formations']:
            if f['status'] in ('NOT RUN',):
                continue
            out.append(f"  {f['formation']}: the uncertain numbers (all runs P10-P90 -> kept P10-P90)")
            for q in f['parameters']:
                a, k_ = q['all'], q['kept']
                out.append(f"    {q['words']:36s} {a['p10']:>12g} - {a['p90']:<12g} -> {k_['p10']:>12g} - {k_['p90']:<12g}" + (f"  narrowed {max(q['narrowed'], 0) * 100:3.0f}%" if q.get('narrowed') is not None else '')
                           + (f"  [{q['range_kind']}]" if q.get('range_kind') else ''))
            out.append(f"  {f['formation']}: the best runs")
            for r in f['runs'][:5]:
                out.append(f"    run {r['run']:3d}{' (declared)' if r['declared_case'] else '':11s} misfit {r['misfit'] if r['misfit'] is not None else '-':>7}  k {r['k_md']:8.3g} md  h {r['thickness_ft']:6.3g} ft  phi {r['porosity']:.3f}  c_t {r['c_t_per_psi']:.2e}"
                           + (f"  sealing {', '.join(r['sealing'])}" if r['sealing'] else '') + ('  kept' if r['kept'] else ''))
            if f['next']:
                out.append(f"  {f['formation']}: what to measure next")
                for c in f['next']:
                    out.append(f"    {c['what']}: spread {c['spread_in_sigmas']:g} x its error; narrows the area of review {c['narrows_aor'] * 100:.0f}%, the front {c['narrows_front'] * 100:.0f}%" + ('  (tells nothing new)' if c['tells_nothing'] else ''))
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    if p['result'] is None:
        raise CalibrationError('nothing to write: the calibration has not been run')
    d = ws.dir('reports', 'calibration')
    p['program_version'] = program_version
    with open(os.path.join(d, 'calibration.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'calibration.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Calibration (SIMULATION)\n\nRun {p['result']['generated_utc']} by {p['result']['by']}; written by GEA-Program {program_version}." + (' STALE: the inputs have changed since.' if p['status'] == 'STALE' else '')
                + '\n\n' + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n' + text(p) + '\n')
    files = ['calibration.json', 'calibration.md']
    ws.audit(actor, 'calibration.report', {'files': files, 'status': p['status']})
    return {'folder': d, 'files': files, 'page': p}


# --------------------------------------------------------------------------------------------------------------
# the labelled scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_wells(days: int = 180, q: float = 8000.0, seed: int = 3) -> List[dict]:
    """An injector, an observation well 3,000 ft east and a producer 500 ft north, as the ledger shapes them; no
    measurement yet. SIMULATION."""
    rng = np.random.default_rng(seed)
    dates = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
    ft = 0.3048 / 111195.0
    inj = {'well': 'SWD-1', 'display': 'SWD 1', 'role': 'injection', 'formation': 'F', 'surface': {'lat': 31.96, 'lon': -102.24, 'datum': 'WGS84'},
           'days': [{'date': d, 'volume_bbl': q * (1 + 0.25 * math.sin(i / 9.0)) + float(rng.normal(0, 200)), 'bhp_psi': None, 'tds_mgl': 120000.0, 'sg': 1.0812, 'density_from': 'declared'} for i, d in enumerate(dates)]}
    obs = {'well': 'OBS-1', 'display': 'OBS 1', 'role': 'observation', 'formation': 'F', 'surface': {'lat': 31.96, 'lon': -102.24 + 3000 * ft / math.cos(math.radians(31.96)), 'datum': 'WGS84'},
           'days': [{'date': d, 'volume_bbl': None, 'bhp_psi': None, 'tds_mgl': None, 'sg': None, 'density_from': ''} for d in dates]}
    prod = {'well': 'P-7H', 'display': 'P 7H', 'role': 'production', 'formation': 'F', 'surface': {'lat': 31.96 + 500 * ft, 'lon': -102.24, 'datum': 'WGS84'},
            'days': [{'date': d, 'volume_bbl': -2000.0, 'bhp_psi': None, 'tds_mgl': None, 'sg': None, 'density_from': ''} for d in dates]}
    return [inj, obs, prod]


def measure_truth(wells: List[dict], fm: dict, truth: dict, seed: int = 1, gauge_noise_psi: float = 5.0, bhp_noise_psi: float = 10.0, tds_noise: float = 1000.0) -> dict:
    """Write into the wells what a formation of the `truth` numbers would have had them measure: the gauge, the injector's
    bottomhole pressure, the producer's TDS every twentieth day. Returns the truth's own forecasts."""
    sc = make_scene('F', fm, wells, [], front_projection_days=365)
    pl = run_plume(sc, truth['k_md'], truth['thickness_ft'], truth['porosity'], truth['c_t_per_psi'], frozenset())
    fr = run_front(sc, truth['k_md'], truth['thickness_ft'], truth['porosity'], frozenset())
    rng = np.random.default_rng(seed)
    by = {w['well']: w for w in wells}
    for i, d in enumerate(by['OBS-1']['days']):
        d['bhp_psi'] = 3000.0 + float(pl['cell']['OBS-1'][i]) + float(rng.normal(0, gauge_noise_psi))
    for i, d in enumerate(by['SWD-1']['days']):
        d['bhp_psi'] = 3400.0 + float(pl['pwf']['SWD-1'][i]) + float(rng.normal(0, bhp_noise_psi))
    for i, d in enumerate(by['P-7H']['days']):
        if i % 20 == 10:
            d['tds_mgl'] = float(round(60000.0 + 60000.0 * float(fr['share']['P-7H'][i]) + float(rng.normal(0, tds_noise)))); d['density_from'] = 'the report'
    return {'aor_acres': pl['aor_acres'], 'aor1_acres': pl['aor1_acres'], 'half_now_ft': fr['half_now_ft'], 'half_end_ft': fr['half_end_ft'], 'share_now': fr['share_now']['P-7H'],
            'k_h': truth['k_md'] * truth['thickness_ft'], 'h_phi': truth['thickness_ft'] * truth['porosity']}


def selftest(runs: int = 24) -> dict:
    """A formation whose true numbers are not the declared ones: the measurements it would give are written into a
    labelled ledger, and the calibration is asked to find it."""
    checks = []
    fm = {'k_md': 50.0, 'thickness_ft': 100.0, 'porosity': 0.12, 'mu_cp': 0.6, 'water_tds_mgl': 60000.0, 'water_sg': 1.0414}
    truth = {'k_md': 110.0, 'thickness_ft': 80.0, 'porosity': 0.10, 'c_t_per_psi': 7e-6}
    rg = parameter_ranges(fm)
    smp = sample(rg, [], [], runs, 7, True)
    checks.append({'check': 'the sample: run 0 the declared case, every other inside its range, each range covered end to end, the same seed the same runs',
                   'value': {'runs': len(smp), 'k': [_sig(min(s_['k_md'] for s_ in smp[1:])), _sig(max(s_['k_md'] for s_ in smp[1:]))]},
                   'pass': smp[0]['declared_case'] and smp[0]['k_md'] == 50.0 and all(rg[n_]['low'] <= s_[n_] <= rg[n_]['high'] for s_ in smp[1:] for n_ in rg)
                           and min(s_['k_md'] for s_ in smp[1:]) < rg['k_md']['low'] * 1.15 and max(s_['k_md'] for s_ in smp[1:]) > rg['k_md']['high'] / 1.15 and sample(rg, [], [], runs, 7, True) == smp})
    # nothing measured: every run kept, the ranges the declarations' own
    w0 = synthetic_wells()
    r0 = calibrate(make_scene('F', fm, w0, [], front_projection_days=365), rg, smp)
    checks.append({'check': 'nothing measured: NO MEASUREMENT, every run kept, a spread and something to measure', 'value': {'status': r0['status'], 'kept': r0['n_kept'], 'aor_1y': [r0['forecasts']['aor_1y_acres']['all']['p10'], r0['forecasts']['aor_1y_acres']['all']['p90']]},
                   'pass': r0['status'] == 'NO MEASUREMENT' and r0['n_kept'] == r0['n_ok'] == runs and r0['forecasts']['aor_1y_acres']['all']['p90'] > 1.5 * r0['forecasts']['aor_1y_acres']['all']['p10']
                           and any(not c_['tells_nothing'] for c_ in r0['next'])})
    # the truth's measurements written in: the calibration finds it
    w1 = synthetic_wells()
    tr = measure_truth(w1, fm, truth)
    sc1 = make_scene('F', fm, w1, [], front_projection_days=365)
    r1 = calibrate(sc1, rg, smp)
    by = {p_['parameter']: p_ for p_ in r1['parameters']}
    checks.append({'check': 'three measurements read: the injector\'s pressure, the gauge, the producer\'s water', 'value': [m_['key'] for m_ in r1['measurements']], 'pass': {m_['key'] for m_ in r1['measurements']} == {'p:SWD-1', 'p:OBS-1', 'w:P-7H'}})
    checks.append({'check': 'CALIBRATED: some runs kept, not all; the declared case (not the truth) not among them', 'value': {'status': r1['status'], 'kept': r1['n_kept'], 'best': r1['best_misfit'], 'declared': r1['declared_misfit']},
                   'pass': r1['status'] == 'CALIBRATED' and 1 <= r1['n_kept'] < runs / 2 and r1['best_misfit'] < 1.5 and r1['declared_misfit'] > 2.0 and not r1['declared_kept']})
    kh, hp = by['k_h'], by['h_phi']
    checks.append({'check': 'the transmissivity found: the truth inside the kept runs, the range narrowed by a third or more', 'value': {'truth': tr['k_h'], 'kept': [kh['kept']['lo'], kh['kept']['hi']], 'narrowed': kh['narrowed']},
                   'pass': kh['kept']['lo'] * 0.85 <= tr['k_h'] <= kh['kept']['hi'] * 1.15 and kh['narrowed'] >= 0.33})
    checks.append({'check': 'the pore thickness found from the water: the truth inside the kept runs, narrowed', 'value': {'truth': tr['h_phi'], 'kept': [hp['kept']['lo'], hp['kept']['hi']], 'narrowed': hp['narrowed']},
                   'pass': hp['kept']['lo'] * 0.85 <= tr['h_phi'] <= hp['kept']['hi'] * 1.15 and hp['narrowed'] >= 0.25})
    f1 = r1['forecasts']
    checks.append({'check': 'the forecasts: the truth\'s area of review in a year and its front inside the kept runs', 'value': {'aor_1y_truth': tr['aor1_acres'], 'kept': [f1['aor_1y_acres']['kept']['lo'], f1['aor_1y_acres']['kept']['hi']], 'front_truth': tr['half_end_ft'], 'front_kept': [f1['front_half_end_ft']['kept']['lo'], f1['front_half_end_ft']['kept']['hi']]},
                   'pass': f1['aor_1y_acres']['kept']['lo'] * 0.8 <= tr['aor1_acres'] <= f1['aor_1y_acres']['kept']['hi'] * 1.2 and f1['front_half_end_ft']['kept']['lo'] * 0.9 <= tr['half_end_ft'] <= f1['front_half_end_ft']['kept']['hi'] * 1.1})
    # a measurement no run can make: the gauge of another formation altogether
    w2 = synthetic_wells()
    measure_truth(w2, fm, truth)
    for i, d in enumerate(w2[1]['days']):
        d['bhp_psi'] = 3000.0 - 4.0 * i                             # falling steadily while the well injects
    r2 = calibrate(make_scene('F', fm, w2, [], front_projection_days=365), rg, smp)
    checks.append({'check': 'a gauge falling while the well injects: NO RUN FITS', 'value': {'status': r2['status'], 'best': r2['best_misfit']}, 'pass': r2['status'] == 'NO RUN FITS' and r2['best_misfit'] > NO_FIT})
    # the narrowing estimate: a reading that is the target itself narrows it wholly but for its error; an unrelated one not at all
    rng = np.random.default_rng(2); t_ = rng.normal(0, 1, 400)
    n_self = _narrowing(t_, t_, 0.1); n_none = _narrowing(rng.normal(0, 1, 400), t_, 0.1)
    checks.append({'check': 'the narrowing estimate: the target itself read to a tenth narrows it nine tenths, an unrelated reading hardly', 'value': {'itself': round(n_self, 3), 'unrelated': round(n_none, 3)},
                   'pass': abs(n_self - (1.0 - math.sqrt(1.0 - float(np.var(t_)) / (float(np.var(t_)) + 0.01)))) < 1e-9 and n_self > 0.85 and n_none < 0.02})
    checks.append({'check': 'significant figures and percentiles', 'value': [_sig(7.123456e-6), _sig(123456.0)], 'pass': _sig(7.123456e-6) == 7.123e-06 and _sig(123456.0) == 123500.0 and _pct([1, 2, 3, 4, 5])['p50'] == 3.0 and _pct([]) is None})
    return {'ok': all(c_['pass'] for c_ in checks), 'checks': checks, 'label': 'SIMULATION', 'runs': runs}
