# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""sticking - the friction factor of the hole at each connection, and whether it is climbing.

The Torque & drag page fits ONE friction factor to a whole well: the number that makes the declared string weigh, in
the model, what the rig measured. This takes the same model (the soft string of Johancsik, Friesen and Dawson, SPE
11380) and the same declarations - the string from the bit up, the mud, the cased-hole factor, the shoe, the block's
weight, the well's path - and asks it at EVERY connection: what open-hole friction factor makes the string weigh
what the hook read as it was picked up, with the string not rotating? And the same of the slack-off weight. That
number takes the depth and the shape of the hole out of the weight: a pick-up weight that climbs because the hole
is longer leaves the factor where it was; one that climbs because the hole is holding the pipe moves it.

The rule: at each connection the median factor of the last five is set against the median of the twenty before
them (ten at least). A rise of 0.05 or more, and of three of their own scatters or more (the scatter from one
connection to the next: 1.48 x the median absolute step over root two, 0.01 at least), is FLAGGED; an event runs from the first flagged connection until three running are not.
Because the twenty roll forward, a hole that settles at a new, higher factor stops being flagged: the event says
the factor ROSE, and the chart shows where it stands. The 0.05 is a setting (`records/dysfunction.json`).

It is the tight-hole rule's companion, not its copy. That rule sets each weight against a line through the fifteen
connections before it, so it sees a jump and follows a slow climb without a word; this one holds the weight against
the model, so it sees the slow climb - and needs the string declared to do it.

What it will not call a measurement: stuck pipe, or its forecast - no rule knows the pipe will stick, this one says
the friction factor that fits the weights has risen, which is cuttings left in the hole, a wall cake building, a
ledge or a dogleg, the mud losing its slickness, or a string that is no longer the one declared; the friction
factor itself - fitted, a number that makes the model match the rig; and the factor in a straight hole - where the
hole is too near vertical for friction to move the weight by 10 klbf between no friction and 0.8, no factor is read.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional

import numpy as np

DEFAULTS = {'stick_rise': 0.05}
BOUNDS = {'stick_rise': (0.02, 0.5)}
WORDS = {'stick_rise': 'the rise in the open-hole friction factor of the last five connections over the twenty before them that flags the hole holding the pipe'}
GRID = np.round(np.arange(0.0, 0.8001, 0.04), 2)
DEPTH_STEP_FT = 200.0
PATH_STEP_FT = 60.0
LAST, BASE, BASE_MIN = 5, 20, 10
SIGMA, SCATTER_FLOOR = 3.0, 0.01
CLOSE = 3
MIN_SPAN_KLBF = 10.0
NO_FIT_SHARE = 0.25
KINDS = (('out', 'pickup_still', 'pick-up'), ('in', 'slackoff_still', 'slack-off'))
NOT_A_MEASUREMENT = ['stuck pipe, or its forecast: the friction factor that fits the weights has risen - cuttings, a wall cake, a ledge, the mud, or a string no longer the one declared',
                     'the friction factor: fitted, a number that makes the model match the rig, not a property measured in the hole',
                     'the factor in a straight hole: where friction cannot move the weight by 10 klbf, none is read']


def setup(ws, well_id: str) -> dict:
    """What the model needs for one well, as the Torque & drag page takes it: the path (the survey, else the
    recorder's own), the declared string and mud, the cased-hole factor, the deepest shoe."""
    from . import rig_state as RS
    from . import torque_drag as TD
    from . import well3d as W3
    cfg = TD.well_config(ws, well_id)
    if not cfg.get('string'):
        return {'status': 'NO STRING', 'why': 'the drill string is not declared for this well (the Torque & drag tab: the sections from the bit up, the mud, the block)'}
    tj = W3.trajectory_of(ws, well_id) if ws.well(well_id).get('trajectory') else None
    if tj:
        st = tj['stations']
        md_m, inc, azi = [x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st]
        path_how = f"the survey ({tj['meta']['source']})"
    else:
        from . import hydraulics as HYD
        rp = HYD.recorder_path(ws, well_id)
        if not rp:
            return {'status': 'NO PATH', 'why': 'this well has no directional survey attached (and no drilling record with the recorder\'s TVD)'}
        md_m, inc, azi = [m * TD.FT for m in rp['md_ft']], rp['inc_deg'], [0.0] * len(rp['md_ft'])
        path_how = rp['source'] + '; the path taken in one vertical plane, so the drag of a turn in azimuth is not in the model'
    md_f, I, A = TD.path_ft(md_m, inc, azi, PATH_STEP_FT)
    shoes = RS.casing(ws).get(well_id) or []
    shoe_ft = max(s['shoe_md_ft'] for s in shoes) if shoes else None
    return {'status': 'OK', 'md_f': md_f, 'inc': I, 'azi': A, 'string': cfg['string'], 'mud_ppg': float(cfg['mud_ppg']), 'mu_cased': float(cfg['mu_cased']),
            'mu_open_declared': float(cfg['mu_open']), 'block_klbf': cfg.get('block_klbf'), 'shoe_ft': shoe_ft,
            'how': f"{path_how}; the string as declared ({cfg.get('source')}); mud {float(cfg['mud_ppg']):g} ppg; "
                   + (f"cased to {shoe_ft:,.0f} ft at a factor of {float(cfg['mu_cased']):g}" if shoe_ft else 'no casing declared: all open hole')}


def signature(ws, well_id: str) -> str:
    """Everything `setup` reads that can change without the record changing (the memo's key)."""
    from . import rig_state as RS
    from . import torque_drag as TD
    try:
        return json.dumps([TD.well_config(ws, well_id), RS.casing(ws).get(well_id), ws.well(well_id).get('trajectory')], sort_keys=True, default=str)
    except Exception as e:
        return f'{type(e).__name__}: {e}'


def hook_fan(su: dict, depth_ft: float, mode: str) -> np.ndarray:
    """The string's tension at the surface (klbf, the block not in it) with the bit at `depth_ft`, for every open-hole
    factor of GRID at once: torque_drag.soft_string's own recursion, the open-hole factor a vector."""
    from . import torque_drag as TD
    m, i, a, w, r, mu = TD.at_depth(su['md_f'], su['inc'], su['azi'], float(depth_ft), su['string'], su['mud_ppg'], su['shoe_ft'], su['mu_cased'], -1.0)
    I = np.radians(i); A = np.radians(a)
    sgn = 1.0 if mode == 'out' else -1.0
    F = np.zeros(len(GRID))
    for k in range(len(m) - 2, -1, -1):
        L = m[k + 1] - m[k]
        W = float(w[k]) * L
        ib = 0.5 * (I[k] + I[k + 1])
        dI = I[k + 1] - I[k]
        dA = (A[k + 1] - A[k] + math.pi) % (2.0 * math.pi) - math.pi
        N = np.hypot(F * dA * math.sin(ib), W * math.sin(ib) - F * dI)
        F = F + W * math.cos(ib) + sgn * (GRID if mu[k] < 0 else mu[k]) * N
    return F / 1000.0


def factors(su: dict, readings: List[dict], block_klbf: float) -> dict:
    """The open-hole friction factor that fits each connection's pick-up and slack-off weight (string not rotating)."""
    ds = [r['depth_ft'] for r in readings if r.get('depth_ft') is not None and any(r.get(k) is not None for _, k, _ in KINDS)]
    rows: List[dict] = []
    counts = {'weighed': 0, 'read': 0, 'cased': 0, 'straight': 0, 'no_fit': 0, 'weights': 0, 'weights_no_fit': 0}
    if not ds:
        return {'rows': rows, 'counts': counts}
    top = su['md_f'][-1]
    lo, hi = max(min(ds), 2.0 * PATH_STEP_FT), min(max(ds), top)
    grid_d = np.unique(np.r_[np.arange(lo, hi, DEPTH_STEP_FT), hi]) if hi > lo else np.asarray([lo])
    fan = {mode: np.asarray([hook_fan(su, d, mode) for d in grid_d]) for mode, _, _ in KINDS}

    def at(mode: str, d: float) -> np.ndarray:
        f = fan[mode]
        if len(grid_d) == 1:
            return f[0]
        j = int(np.clip(np.searchsorted(grid_d, d) - 1, 0, len(grid_d) - 2))
        x = (d - grid_d[j]) / (grid_d[j + 1] - grid_d[j])
        return f[j] * (1.0 - x) + f[j + 1] * x
    for r in readings:
        d = r.get('depth_ft')
        if d is None or not any(r.get(k) is not None for _, k, _ in KINDS):
            continue
        counts['weighed'] += 1
        row = {'t': r['t'], 'time': r.get('time'), 'depth_ft': d, 'mu_out': None, 'mu_in': None}
        if d > top + DEPTH_STEP_FT:
            row['why'] = 'beyond the end of the path'
        elif su['shoe_ft'] and d <= su['shoe_ft']:
            row['why'] = 'inside the casing'; counts['cased'] += 1
        else:
            for mode, key, _ in KINDS:
                v = r.get(key)
                if v is None:
                    continue
                f = at(mode, min(d, top)) + block_klbf
                if abs(f[-1] - f[0]) < MIN_SPAN_KLBF:
                    row['why'] = 'the hole is too straight here for friction to move the weight'; continue
                x, y = (f, GRID) if mode == 'out' else (f[::-1], GRID[::-1])
                counts['weights'] += 1
                if not (x[0] <= v <= x[-1]):
                    row[f'{mode}_no_fit'] = round(float(v - (x[0] if v < x[0] else x[-1])), 1); counts['weights_no_fit'] += 1; continue
                row[f'mu_{mode}'] = round(float(np.interp(v, x, y)), 3)
            if row['mu_out'] is None and row['mu_in'] is None:
                counts['straight' if row.get('why') else 'no_fit'] += 1
            else:
                counts['read'] += 1
        rows.append(row)
    return {'rows': rows, 'counts': counts}


def trend(rows: List[dict], trips: List[dict], P: dict) -> List[dict]:
    """The rule: the last five connections' median factor against the twenty before them."""
    outs = sorted(x['t_start'] for x in trips if x.get('direction') == 'OUT')
    events: List[dict] = []
    for mode, _, name in KINDS:
        key = f'mu_{mode}'
        seq = [r for r in rows if r.get(key) is not None]
        ev: Optional[dict] = None
        quiet = 0

        def close(why: str):
            nonlocal ev
            if ev is not None:
                ev['ended'] = why
                nxt = [o for o in outs if o > ev['t_last']]
                if nxt and nxt[0] - ev['t_last'] <= 86400.0:
                    ev['trip_out_after_h'] = round((nxt[0] - ev['t_last']) / 3600.0, 2)
                events.append(ev)
            ev = None
        for j, r in enumerate(seq):
            if j + 1 < LAST + BASE_MIN:
                continue
            cur = float(np.median([x[key] for x in seq[j + 1 - LAST:j + 1]]))
            b = np.asarray([x[key] for x in seq[max(0, j + 1 - LAST - BASE):j + 1 - LAST]])
            base = float(np.median(b))
            sc = max(1.4826 * float(np.median(np.abs(np.diff(b)))) / math.sqrt(2.0), SCATTER_FLOOR)      # from one connection to the next: a climb in the twenty is not their scatter
            lim = max(P['stick_rise'], SIGMA * sc)
            fl = (cur - base) >= lim
            r[f'{mode}_now'] = round(cur, 3); r[f'{mode}_before'] = round(base, 3); r[f'{mode}_limit'] = round(lim, 3); r[f'{mode}_flagged'] = bool(fl)
            if ev is None:
                if fl:
                    ev = {'kind': 'sticking', 'which': name, 't': r['t'], 'time': r.get('time'), 'depth_ft': r['depth_ft'], 'depth_to_ft': r['depth_ft'], 'connections': 1, 'mu_from': round(base, 2),
                          'mu_to': round(cur, 2), 't_last': r['t'], 'ended': None}
                    quiet = 0
                continue
            if fl:
                ev['connections'] += 1 + quiet; quiet = 0
                ev['t_last'] = r['t']; ev['depth_to_ft'] = r['depth_ft']; ev['mu_to'] = max(ev['mu_to'], round(cur, 2))
            else:
                quiet += 1
                if quiet >= CLOSE:
                    close('no longer above the connections before it')
        if ev is not None:
            if quiet == 0:
                ev['open'] = True
                close('open at the end of the record')
            else:
                close('no longer above the connections before it')
    merged: List[dict] = []                              # the pick-up and the slack-off speaking of the same stretch of hole are one event
    for e in sorted(events, key=lambda e: e['t']):
        m = merged[-1] if merged else None
        if m is not None and m['which'] != e['which'] and ' and ' not in m['which'] and e['t'] <= m['t_last']:
            first = m if m['which'] == 'pick-up' else e
            m.update(which='pick-up and slack-off', mu_from=first['mu_from'], mu_to=first['mu_to'], connections=max(m['connections'], e['connections']), t_last=max(m['t_last'], e['t_last']),
                     depth_to_ft=max(m['depth_to_ft'], e['depth_to_ft']))
            if e.get('open') or m.get('open'):
                m['open'] = True; m['ended'] = 'open at the end of the record'
            if e.get('trip_out_after_h') is not None:
                m.setdefault('trip_out_after_h', e['trip_out_after_h'])
        else:
            merged.append(e)
    return merged


def analyse(su: dict, readings: List[dict], trips: List[dict], block_klbf: Optional[float], P: dict) -> dict:
    if su.get('status') != 'OK':
        return {'status': su.get('status') or 'NO STRING', 'why': su.get('why'), 'events': [], 'readings': []}
    block = float(su['block_klbf'] if su.get('block_klbf') is not None else (block_klbf or 0.0))
    f = factors(su, readings, block)
    c = f['counts']
    out = {'status': 'OK', 'events': [], 'readings': f['rows'], 'counts': c, 'block_klbf': round(block, 1), 'declared': su['mu_open_declared'], 'how': su['how']
           + f"; the block {block:g} klbf (" + ('declared' if su.get('block_klbf') is not None else 'as the rig-time page takes it') + ')'}
    if c['weighed'] == 0:
        out.update(status='NO READINGS', why='no connection has a pick-up or slack-off weight with the string not rotating')
    elif c['weights_no_fit'] > NO_FIT_SHARE * max(c['weights'], 1):
        out.update(status='DOES NOT FIT', why=f"{c['weights_no_fit']} of the {c['weights']} pick-up and slack-off weights are what no friction factor from 0 to 0.8 gives the declared string: "
                                              'the string, the mud or the block\'s weight are not as declared')
    elif c['read'] < LAST + BASE_MIN:
        out.update(status='TOO FEW READINGS', why=f"a factor could be read at {c['read']} of {c['weighed']} connections ({c['cased']} inside the casing, {c['straight']} where the hole is too straight for "
                                                  f"friction to move the weight, {c['no_fit']} that no factor fits); the rule needs {LAST + BASE_MIN}")
    else:
        out['events'] = trend(f['rows'], trips, P)
    for k, nm in (('mu_out', 'median_out'), ('mu_in', 'median_in')):
        v = [r[k] for r in f['rows'] if r.get(k) is not None]
        out[nm] = round(float(np.median(v)), 3) if v else None
    return out


def describe(e: dict) -> str:
    return (f"the friction factor that fits the {e['which']} weight up from {e['mu_from']:.2f} to {e['mu_to']:.2f} over {e['connections']} connection(s) "
            f"from {e['depth_ft']:,.0f} ft")


# --------------------------------------------------------------------------------------------------------------
# the made hole and the self-test
# --------------------------------------------------------------------------------------------------------------
def made_hole(td_ft: float = 16500.0) -> dict:
    """A path (vertical to 3,000 ft, built to 60 degrees by 4,500 ft, held to `td_ft`), a string and a shoe at 4,000 ft,
    as the model takes them. SIMULATION."""
    from . import torque_drag as TD
    md = np.arange(0.0, td_ft + 0.01, 100.0)
    inc = np.clip((md - 3000.0) / (4500.0 - 3000.0) * 60.0, 0.0, 60.0)
    string = TD.check_string([{'what': 'BHA', 'length_ft': 300, 'od_in': 6.75, 'weight_lb_ft': 95.0, 'contact_od_in': 6.75},
                              {'what': 'drill pipe', 'length_ft': None, 'od_in': 5.0, 'weight_lb_ft': 22.6, 'contact_od_in': 6.625}])
    md_f, I, A = TD.path_ft(md * TD.FT, inc, np.full(len(md), 45.0), PATH_STEP_FT)
    return {'status': 'OK', 'md_f': md_f, 'inc': I, 'azi': A, 'string': string, 'mud_ppg': 10.0, 'mu_cased': 0.18, 'mu_open_declared': 0.24, 'block_klbf': 40.0, 'shoe_ft': 4000.0,
            'how': 'a made hole', 'survey': {'md_ft': md, 'inc_deg': inc, 'azi_deg': np.full(len(md), 45.0)}}


def made_weight(su: dict, depth_ft: float, mu_open: float, mode: str) -> float:
    """The hook load (klbf, the block in it) the model gives at one depth for one open-hole factor."""
    return float(np.interp(mu_open, GRID, hook_fan(su, depth_ft, mode))) + float(su['block_klbf'])


def made_factor(k: int, rise_from: Optional[int] = 40, per: float = 0.008, top: float = 0.45) -> float:
    """The open-hole factor of made connection k: 0.24, climbing `per` a connection from `rise_from`."""
    return 0.24 if (rise_from is None or k < rise_from) else min(top, 0.24 + per * (k - rise_from + 1))


def selftest() -> dict:
    checks = []
    from . import torque_drag as TD
    su = made_hole()
    # the vector recursion is the page's own model
    d0 = 12000.0
    ref = TD.model(su['md_f'], su['inc'], su['azi'], su['string'], su['mud_ppg'], su['shoe_ft'], su['mu_cased'], 0.32, [d0], su['block_klbf'], modes=('out', 'in'))
    mine = {m: made_weight(su, d0, 0.32, m) for m in ('out', 'in')}
    checks.append({'check': 'the hook load for every factor at once is the Torque & drag page\'s own model', 'value': [ref['out'][0], round(mine['out'], 3), ref['in'][0], round(mine['in'], 3)],
                   'pass': abs(ref['out'][0] - mine['out']) < 0.05 and abs(ref['in'][0] - mine['in']) < 0.05})
    rng = np.random.default_rng(7)

    def readings(rise_from, n=60, noise=0.5):
        out = []
        for k in range(n):
            d = 10000.0 + 95.0 * k
            mu = made_factor(k, rise_from)
            out.append({'t': 3000.0 * k, 'time': None, 'depth_ft': d, 'pickup_still': made_weight(su, d, mu, 'out') + rng.normal(0, noise), 'slackoff_still': made_weight(su, d, mu, 'in') + rng.normal(0, noise)})
        return out
    P = dict(DEFAULTS)
    flat = analyse(su, readings(None), [], None, P)
    mu = [r['mu_out'] for r in flat['readings']]
    checks.append({'check': 'a hole of one friction factor: the factor read at every connection is that factor, the hole 5,700 ft longer by the end, and no event',
                   'value': [flat['status'], flat['counts'], round(min(mu), 3), round(max(mu), 3), len(flat['events'])],
                   'pass': flat['status'] == 'OK' and flat['counts']['read'] == 60 and all(abs(x - 0.24) <= 0.02 for x in mu) and not flat['events']})
    rise = analyse(su, readings(40), [{'direction': 'OUT', 't_start': 3000.0 * 62}], None, P)
    ev = rise['events']
    checks.append({'check': 'the factor climbing 0.008 a connection from the 41st: one event, both weights in it, within ten connections of the start, open at the end, a trip out after it noted',
                   'value': [(e['which'], e['depth_ft'], e['connections'], e['mu_from'], e['mu_to'], e['ended'], e.get('trip_out_after_h')) for e in ev],
                   'pass': len(ev) == 1 and ev[0]['which'] == 'pick-up and slack-off' and all(10000 + 95 * 41 <= e['depth_ft'] <= 10000 + 95 * 50 and e.get('open') and abs(e['mu_from'] - 0.24) <= 0.02
                                                                                                         and e['mu_to'] >= 0.34 and e.get('trip_out_after_h') for e in ev)})
    step = analyse(su, [dict(r, pickup_still=made_weight(su, r['depth_ft'], 0.24 if k < 20 else 0.33, 'out'), slackoff_still=None) for k, r in enumerate(readings(None))], [], None, P)
    es = step['events']
    checks.append({'check': 'a step up to a new level: flagged as it rises, and ended once the connections before it are at the new level',
                   'value': [(e['depth_ft'], e['connections'], e['mu_from'], e['mu_to'], e['ended']) for e in es],
                   'pass': len(es) == 1 and not es[0].get('open') and es[0]['ended'].startswith('no longer above') and 0.31 <= es[0]['mu_to'] <= 0.35 and 8 <= es[0]['connections'] <= 20})
    strict = analyse(su, readings(40), [], None, {'stick_rise': 0.3})
    checks.append({'check': 'the setting is the rule: at 0.30 the same climb is no event', 'value': len(strict['events']), 'pass': not strict['events']})
    wrong = analyse({**su, 'block_klbf': 140.0}, readings(None), [], None, P)
    checks.append({'check': 'a block declared 100 klbf too heavy: no factor fits the weights, and the page says the declarations are not the rig\'s', 'value': [wrong['status'], wrong.get('why')],
                   'pass': wrong['status'] == 'DOES NOT FIT' and 'not as declared' in wrong['why']})
    vert = made_hole(); vert['inc'] = np.zeros(len(vert['inc']))
    v = analyse(vert, [{'t': k * 3000.0, 'depth_ft': 5000.0 + 95.0 * k, 'pickup_still': 150.0 + k, 'slackoff_still': 149.0 + k} for k in range(30)], [], None, P)
    checks.append({'check': 'a vertical hole: friction cannot move the weight, so no factor is read and none is invented', 'value': [v['status'], v['counts']],
                   'pass': v['status'] == 'TOO FEW READINGS' and v['counts']['read'] == 0 and v['counts']['straight'] == 30})
    none = analyse({'status': 'NO STRING', 'why': 'the drill string is not declared'}, readings(None), [], None, P)
    checks.append({'check': 'no string declared: not judged, and says what to declare', 'value': none['status'], 'pass': none['status'] == 'NO STRING' and not none['events']})
    return {'ok': all(x['pass'] for x in checks), 'checks': checks, 'label': 'SIMULATION'}
