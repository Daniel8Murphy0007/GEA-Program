"""Torque and drag: what the drill string should weigh at the hook and take to turn, against what the rig measured.

The soft-string model (Johancsik, Friesen and Dawson, "Torque and Drag in Directional Wells - Prediction and
Measurement", SPE-11380-PA, JPT 36(6), 1984) takes the string as a cable lying on the low side of the hole (or pulled
against the high side of a build), element by element from the bit up. For each element between two survey points,
with W its buoyed weight, theta its mean inclination, d_theta and d_alpha its changes of inclination and azimuth, and
F the axial tension below it:

    normal force   N  = sqrt( (F d_alpha sin theta)^2 + (W sin theta - F d_theta)^2 )
    tension above  F' = F + W cos theta + mu N   (pulling out)    F - ... - mu N (running in)    F + W cos theta (rotating)
    torque         M' = M + mu N r                (rotating; r the radius the string turns on - the tool joint)

The buoyed weight is the string's weight in air times (1 - mud density / 65.5 ppg, the density of steel). The friction
factor mu is the operator's: one for cased hole (above the deepest casing shoe set) and one for open hole. The program
gives, at each bit depth from the surface to TD, the hook load pulling out (pick-up), running in (slack-off) and
rotating off bottom, and the surface torque rotating off bottom - for the declared friction factors and a fan of
others (0.1 to 0.5): the "broomstick". The hook load is the tension at the surface plus the travelling block's weight.

From the drilling record it takes what the rig measured - the hook load while tripping out and in with the pumps off,
and the hook load and torque while rotating off bottom - as the median of each 100 ft of bit depth, and finds the
friction factor that best fits each, open and cased hole apart. The string is the operator's declaration: each
section from the bit up with its length, its outside diameter, its weight per foot in air (with its tool joints) and
the outside diameter it turns on.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

STEEL_PPG = 65.5
FT = 0.3048
DEFAULTS = {'mud_ppg': 10.0, 'mu_cased': 0.25, 'mu_open': 0.30, 'step_ft': 100.0, 'block_klbf': None}
FAN = (0.1, 0.2, 0.3, 0.4, 0.5)
MODES = ('out', 'in', 'rotate')
NOT_A_MEASUREMENT = ['the friction factor: it is fitted, a number that makes the model match the rig, not a property measured in the hole',
                     'where the string touches: the soft-string model lays the string on the hole as a cable, without its stiffness or the '
                     'hole\'s clearance', 'buckling: a string pushed past its buckling load in compression is not modelled']


def buoyancy(mud_ppg: float) -> float:
    return 1.0 - float(mud_ppg) / STEEL_PPG


def soft_string(md_ft, inc_deg, azi_deg, w_lb_ft, r_in, mu, mode: str, bottom_lbf: float = 0.0, bottom_torque_ftlb: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """Tension (lbf, positive in tension) and torque (ft.lbf) at each point, from the bit (the last point) up.
    w_lb_ft, r_in, mu: one per element between points (n - 1). mode: 'out', 'in' or 'rotate'. bottom_lbf: the axial
    force at the bit (negative: weight on bit)."""
    md = np.asarray(md_ft, dtype=np.float64)
    I = np.radians(np.asarray(inc_deg, dtype=np.float64))
    A = np.radians(np.asarray(azi_deg, dtype=np.float64))
    n = len(md)
    F = np.zeros(n); M = np.zeros(n)
    F[-1], M[-1] = float(bottom_lbf), float(bottom_torque_ftlb)
    for k in range(n - 2, -1, -1):
        L = md[k + 1] - md[k]
        W = float(w_lb_ft[k]) * L
        ib = 0.5 * (I[k] + I[k + 1])
        dI = I[k + 1] - I[k]
        dA = (A[k + 1] - A[k] + math.pi) % (2.0 * math.pi) - math.pi
        Ft = F[k + 1]
        N = math.hypot(Ft * dA * math.sin(ib), W * math.sin(ib) - Ft * dI)
        if mode == 'out':
            F[k] = Ft + W * math.cos(ib) + mu[k] * N; M[k] = M[k + 1]
        elif mode == 'in':
            F[k] = Ft + W * math.cos(ib) - mu[k] * N; M[k] = M[k + 1]
        elif mode == 'rotate':
            F[k] = Ft + W * math.cos(ib); M[k] = M[k + 1] + mu[k] * N * float(r_in[k]) / 12.0
        else:
            raise ValueError(f'no mode {mode!r}')
    return F, M


# -- the string and the hole ---------------------------------------------------------------------------------------------
def check_string(sections: List[dict]) -> List[dict]:
    """Sections from the bit up: {what, length_ft (None for the last: to the surface), od_in, weight_lb_ft (in air,
    with tool joints), contact_od_in (the diameter it turns on; the od when not given)}."""
    if not sections:
        raise ValueError('a string needs at least one section')
    out = []
    for i, s in enumerate(sections):
        what = str(s.get('what') or '').strip() or f'section {i + 1}'
        L = s.get('length_ft')
        if L in (None, ''):
            if i != len(sections) - 1:
                raise ValueError('only the top section may run to the surface')
            L = None
        else:
            L = float(L)
            if not (math.isfinite(L) and 0 < L < 60000):
                raise ValueError(f'{what}: the length is in feet, more than zero')
        od = float(s.get('od_in') or 0)
        w = float(s.get('weight_lb_ft') or 0)
        if not (1.0 <= od <= 30.0):
            raise ValueError(f'{what}: the outside diameter is in inches, 1 to 30')
        if not (1.0 <= w <= 500.0):
            raise ValueError(f'{what}: the weight is in pounds per foot in air, 1 to 500')
        c = float(s.get('contact_od_in') or od)
        if not (od <= c <= 30.0):
            raise ValueError(f'{what}: the contact diameter is at least the outside diameter')
        out.append({'what': what, 'length_ft': L, 'od_in': od, 'weight_lb_ft': w, 'contact_od_in': c})
    if out[-1]['length_ft'] is not None:
        out[-1]['length_ft'] = None
    return out


def path_ft(md_m, inc, azi, step_ft: float = 30.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The survey every `step_ft` of measured depth (and at each station), in feet, its direction on the arcs."""
    from . import well3d as W3
    from . import wellplan as WPL
    p = W3.Path(md_m, inc, azi, 0.0, 0.0, 0.0, 0.0)
    md_f = np.asarray(p.md) / FT
    grid = np.unique(np.round(np.concatenate([md_f, np.arange(0.0, md_f[-1], step_ft), [md_f[-1]]]), 3))
    dirs = [WPL._interp_dir(p, m * FT) for m in grid]
    return grid, np.array([d[0] for d in dirs]), np.array([d[1] for d in dirs])


def at_depth(md_f, inc, azi, bit_ft: float, string: List[dict], mud_ppg: float, shoe_ft: Optional[float], mu_cased: float, mu_open: float):
    """The points from the surface to the bit and, per element, the buoyed weight, the contact radius and mu."""
    keep = md_f < bit_ft
    m = np.concatenate([md_f[keep], [bit_ft]])
    i = np.concatenate([inc[keep], [np.interp(bit_ft, md_f, inc)]])
    a = np.concatenate([azi[keep], [np.interp(bit_ft, md_f, np.degrees(np.unwrap(np.radians(azi))) ) % 360.0]])
    # the sections from the bit up: section j covers [bit - top_j, bit - bottom_j]
    bounds, below = [], 0.0
    for s in string:
        top = below + (s['length_ft'] if s['length_ft'] is not None else 1e9)
        bounds.append((bit_ft - top, bit_ft - below, s)); below = top
    # split elements at section boundaries
    cuts = sorted(set([b for b, _, _ in bounds if 0 < b < bit_ft]))
    if cuts:
        m2 = np.unique(np.concatenate([m, cuts]))
        i = np.interp(m2, m, i); a = np.degrees(np.unwrap(np.interp(m2, m, np.unwrap(np.radians(a))))) % 360.0
        m = m2
    mid = 0.5 * (m[1:] + m[:-1])
    bf = buoyancy(mud_ppg)
    w = np.zeros(len(mid)); r = np.zeros(len(mid)); mu = np.zeros(len(mid))
    for top, bot, s in bounds:
        sel = (mid >= top) & (mid < bot)
        w[sel] = s['weight_lb_ft'] * bf; r[sel] = s['contact_od_in'] / 2.0
    mu[:] = mu_open
    if shoe_ft:
        mu[mid <= shoe_ft] = mu_cased
    return m, i, a, w, r, mu


def model(md_f, inc, azi, string, mud_ppg, shoe_ft, mu_cased, mu_open, depths_ft, block_klbf: float = 0.0, modes=MODES) -> Dict[str, list]:
    """Hook load (klbf) and off-bottom torque (kft.lbf) at each bit depth."""
    out = {'depth_ft': [], 'out': [], 'in': [], 'rotate': [], 'torque': []}
    for D in depths_ft:
        m, i, a, w, r, mu = at_depth(md_f, inc, azi, float(D), string, mud_ppg, shoe_ft, mu_cased, mu_open)
        out['depth_ft'].append(round(float(D), 1))
        for mode in modes:
            F, M = soft_string(m, i, a, w, r, mu, mode)
            out[mode].append(round(F[0] / 1000.0 + block_klbf, 3))
            if mode == 'rotate':
                out['torque'].append(round(M[0] / 1000.0, 4))
    return out


# -- the record ------------------------------------------------------------------------------------------------------------
def measured(ws, well_id: str, block_klbf: Optional[float] = None, bin_ft: float = 100.0) -> Optional[dict]:
    """From the drilling record: the hook load tripping out and in (pumps off) and rotating off bottom, and the torque
    rotating off bottom, as the median of each bin of bit depth."""
    from . import rig_state as RS
    from . import series_cache as SCH
    try:
        c = SCH.ensure(ws, well_id)
    except Exception:
        return None
    if c is None or c.meta.get('index_kind') != 'time_s':
        return None
    names = {x['name'] for x in c.meta['channels'] if x.get('present')}
    if not {'Hook Load', 'Bit Depth', 'Hole Depth'} <= names:
        return None
    g = lambda n: np.asarray(c.channel(n), dtype=np.float64) if n in names else None
    t = np.asarray(c.index, dtype=np.float64)
    drl = c.extra('drilling')
    drl = np.asarray(drl, dtype=bool) if drl is not None else np.zeros(len(t), bool)
    ch = {'hookload': g('Hook Load'), 'block': g('Block Height'), 'bit': g('Bit Depth'), 'hole': g('Hole Depth'), 'flow': g('Total Pump Output'),
          'spp': g('Standpipe Pressure'), 'rpm': g('Rotary RPM')}
    cl = RS.classify(t, ch, drl, block_klbf)
    st = cl['state']
    bit, hole, hl = ch['bit'], ch['hole'], ch['hookload']
    tq = g('Convertible Torque')
    off = (hole - bit) > 5.0
    sets = {'out': st == RS.TOUT, 'in': st == RS.TIN, 'rotate': (st == RS.CIRC) & cl['rot'] & off}
    out = {'block_klbf': cl['block_weight_klbf'], 'block_estimated': cl['block_weight_estimated']}
    for k, sel in sets.items():
        sel = sel & np.isfinite(hl) & np.isfinite(bit)
        if sel.sum() < 30:
            out[k] = {'depth_ft': [], 'klbf': [], 'n': []}
            continue
        b = np.floor(bit[sel] / bin_ft)
        dd, vv, nn, tt = [], [], [], []
        for u in np.unique(b):
            m_ = b == u
            if m_.sum() < 10:
                continue
            dd.append(round(float(np.median(bit[sel][m_])), 1)); vv.append(round(float(np.median(hl[sel][m_])), 2)); nn.append(int(m_.sum()))
            if k == 'rotate' and tq is not None:
                tt.append(round(float(np.nanmedian(tq[sel][m_])), 3))
        out[k] = {'depth_ft': dd, 'klbf': vv, 'n': nn}
        if k == 'rotate':
            out[k]['torque_kftlb'] = tt if tq is not None else []
    return out


def fit_mu(md_f, inc, azi, string, mud_ppg, shoe_ft, meas: dict, mode: str, block_klbf: float, which: str, other_mu: float) -> Optional[dict]:
    """The friction factor (open hole when which='open', cased when 'cased', the other held at other_mu) that best fits
    the measured hook loads of `mode`, over the bit depths in that hole."""
    m = meas.get('rotate' if mode == 'torque' else mode) or {}
    vals = m.get('torque_kftlb') if mode == 'torque' else m.get('klbf')
    if not m.get('depth_ft') or not vals or len(vals) != len(m['depth_ft']):
        return None
    D = np.asarray(m['depth_ft']); H = np.asarray(vals, dtype=np.float64)
    sel = (D > shoe_ft) if (which == 'open' and shoe_ft) else (D <= shoe_ft) if (which == 'cased' and shoe_ft) else np.ones(len(D), bool)
    if sel.sum() < 3:
        return None
    key = 'torque' if mode == 'torque' else mode
    run = 'rotate' if mode == 'torque' else mode

    def err(mu):
        mc, mo = (mu, other_mu) if which == 'cased' else (other_mu, mu)
        mod = model(md_f, inc, azi, string, mud_ppg, shoe_ft, mc, mo, D[sel], block_klbf, modes=(run,))[key]
        return float(np.sqrt(np.mean((np.asarray(mod) - H[sel]) ** 2)))
    coarse = [(float(mu), err(float(mu))) for mu in np.round(np.arange(0.0, 0.81, 0.05), 2)]
    c0 = min(coarse, key=lambda x: x[1])[0]
    fine = [(float(mu), err(float(mu))) for mu in np.round(np.arange(max(0.0, c0 - 0.05), min(0.8, c0 + 0.05) + 1e-9, 0.01), 2)]
    best = min(coarse + fine, key=lambda x: x[1])
    return {'mu': best[0], 'rms': round(best[1], 3), 'points': int(sel.sum())}


# -- the workspace ---------------------------------------------------------------------------------------------------------
def _store_path(ws) -> str:
    return os.path.join(ws.path, 'drillstring', 'torque_drag.json')


def _load(ws) -> dict:
    try:
        with open(_store_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wells': {}}


def well_config(ws, well_id: str) -> dict:
    return {**DEFAULTS, 'string': None, **((_load(ws).get('wells') or {}).get(well_id) or {})}


def set_well(ws, actor: str, well_id: str, source: str = '', string: Optional[List[dict]] = None, mud_ppg=None, mu_cased=None, mu_open=None,
             block_klbf=None) -> dict:
    """Declare a well's string (sections from the bit up), mud density and friction factors."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    d = _load(ws)
    cur = dict((d.get('wells') or {}).get(well_id) or {})
    changed = False
    if string is not None:
        cur['string'] = check_string([dict(x) for x in string]); changed = True
    for key, v, lo, hi in (('mud_ppg', mud_ppg, 6.0, 22.0), ('mu_cased', mu_cased, 0.0, 0.8), ('mu_open', mu_open, 0.0, 0.8), ('block_klbf', block_klbf, 0.0, 200.0)):
        if v in (None, ''):
            continue
        x = float(v)
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{key} must be between {lo:g} and {hi:g}')
        cur[key] = x; changed = True
    if changed:
        if not (source or '').strip():
            raise ValueError('say where the string and its figures came from (e.g. "BHA report run 3, pipe tally")')
        cur['source'] = source.strip()
    cur['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'); cur['by'] = actor
    d.setdefault('wells', {})[well_id] = cur
    os.makedirs(os.path.dirname(_store_path(ws)), exist_ok=True)
    with open(_store_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(_store_path(ws) + '.tmp', _store_path(ws))
    ws.audit(actor, 'torque_drag.declare', {'well': well_id, **{k: v for k, v in cur.items() if k not in ('utc', 'by')}})
    return well_config(ws, well_id)


def parse_string(text: str) -> List[dict]:
    """'what:length_ft:od_in:weight_lb_ft[:contact_od_in]; ...' from the bit up, the last section's length blank."""
    out = []
    for part in [p.strip() for p in (text or '').split(';') if p.strip()]:
        f = [x.strip() for x in part.split(':')]
        if len(f) < 4:
            raise ValueError(f'a section is what:length_ft:od_in:weight_lb_ft[:contact_od_in], not {part!r}')
        out.append({'what': f[0], 'length_ft': f[1] or None, 'od_in': f[2], 'weight_lb_ft': f[3], 'contact_od_in': f[4] if len(f) > 4 and f[4] else None})
    return check_string(out)


def analyse(md_m, inc, azi, cfg: dict, shoe_ft: Optional[float], meas: Optional[dict] = None, td_ft: Optional[float] = None) -> dict:
    md_f, I, A = path_ft(md_m, inc, azi, 30.0)
    td = float(td_ft or md_f[-1])
    step = float(cfg.get('step_ft') or DEFAULTS['step_ft'])
    depths = np.unique(np.concatenate([np.arange(step, td, step), [td]]))
    s = cfg['string']
    block = float(cfg.get('block_klbf') or (meas or {}).get('block_klbf') or 0.0)
    decl = model(md_f, I, A, s, cfg['mud_ppg'], shoe_ft, cfg['mu_cased'], cfg['mu_open'], depths, block)
    fan = {f'{mu:.1f}': model(md_f, I, A, s, cfg['mud_ppg'], shoe_ft, mu, mu, depths[::2] if len(depths) > 60 else depths, block, modes=('out', 'in'))
           for mu in FAN}
    air = sum((x['length_ft'] if x['length_ft'] is not None else max(0.0, td - sum(y['length_ft'] for y in s[:-1]))) * x['weight_lb_ft'] for x in s) / 1000.0
    fits = {}
    if meas:
        for mode in ('out', 'in', 'torque'):                 # cased hole first (the bit inside it sees no open hole), then open hole with it
            mu_c = cfg['mu_cased']
            if shoe_ft:
                f_ = fit_mu(md_f, I, A, s, cfg['mud_ppg'], shoe_ft, meas, mode, block, 'cased', cfg['mu_open'])
                if f_:
                    fits[f'{mode}:cased'] = f_
                    mu_c = f_['mu']
            f_ = fit_mu(md_f, I, A, s, cfg['mud_ppg'], shoe_ft, meas, mode, block, 'open', mu_c)
            if f_:
                fits[f'{mode}:open'] = f_
    return {'declared': decl, 'fan': fan, 'measured': meas, 'fits': fits, 'block_klbf': round(block, 2), 'td_ft': round(td, 1), 'shoe_ft': shoe_ft,
            'buoyancy': round(buoyancy(cfg['mud_ppg']), 4), 'string_air_klbf': round(air, 1), 'string_buoyed_klbf': round(air * buoyancy(cfg['mud_ppg']), 1),
            'max_inc_deg': round(float(np.max(I)), 2), 'config': cfg}


def example() -> dict:
    """The worked example: Volve 15/9-F-10's survey (Equinor's released data) to its 5,300 m TD with a string declared
    here for the example - not the string Volve ran - and the example's friction factors."""
    from . import well3d as W3
    sc = W3.volve_scene()
    w = next(x for x in sc['wells'] if x['name'] == '15/9-F-10')
    st = w['stations']
    string = check_string([{'what': '8-1/2 in BHA (example)', 'length_ft': 400, 'od_in': 6.75, 'weight_lb_ft': 100.0, 'contact_od_in': 6.75},
                           {'what': '5 in heavy-weight pipe (example)', 'length_ft': 900, 'od_in': 5.0, 'weight_lb_ft': 50.0, 'contact_od_in': 6.5},
                           {'what': '5 in drill pipe (example)', 'length_ft': None, 'od_in': 5.0, 'weight_lb_ft': 22.6, 'contact_od_in': 6.625}])
    cfg = {**DEFAULTS, 'string': string, 'mud_ppg': 10.5, 'mu_cased': 0.20, 'mu_open': 0.28, 'block_klbf': 50.0,
           'source': 'a string, mud and friction factors declared for the example, not from Volve; the survey is Equinor\'s'}
    shoe_ft = 2500.0 / FT                                   # an example shoe at 2,500 m MD
    r = analyse(st['md'], st['incl'], st['azi'], cfg, shoe_ft)
    r.update({'well': '15/9-F-10', 'display': 'Worked example: Volve 15/9-F-10 with an example string',
              'attribution': 'Survey: ' + (w.get('source', '') or "Equinor's released trajectory").rstrip('. ') + '. Contains data from the Volve field released by Equinor ASA and the former Volve licence partners; the data are not sold here.',
              'how': 'the survey as released; the string, mud, shoe (2,500 m) and friction factors declared for the example'})
    return r


def page(ws, scene: str = 'workspace', well: Optional[str] = None) -> dict:
    from . import rig_state as RS
    from . import well3d as W3
    out = {'scene': scene, 'scenes': [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'example', 'title': 'Worked example: Volve 15/9-F-10'}],
           'defaults': DEFAULTS, 'fan': list(FAN), 'not_a_measurement': NOT_A_MEASUREMENT, 'wells': [], 'well': None, 'result': None, 'detail': ''}
    if scene == 'example' or ws is None:
        out['scene'] = 'example'
        out['result'] = example()
        return out
    if scene != 'workspace':
        raise KeyError(f'no scene {scene!r}: workspace or example')
    for w in ws.wells():
        out['wells'].append({'id': w['id'], 'display': w.get('display') or w['id'], 'survey': bool(w.get('trajectory')), 'config': well_config(ws, w['id'])})
    choice = well if any(w['id'] == well for w in out['wells']) else next((w['id'] for w in out['wells'] if w['survey'] and w['config'].get('string')),
                                                                          next((w['id'] for w in out['wells'] if w['survey']), out['wells'][0]['id'] if out['wells'] else None))
    out['well'] = choice
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    cfg = well_config(ws, choice)
    tj = W3.trajectory_of(ws, choice) if ws.well(choice).get('trajectory') else None
    if not tj:
        out['detail'] = 'this well has no directional survey attached'
        return out
    if not cfg.get('string'):
        out['detail'] = 'declare the drill string (sections from the bit up)'
        return out
    shoes = RS.casing(ws).get(choice) or []
    shoe_ft = max(s['shoe_md_ft'] for s in shoes) if shoes else None
    meas = measured(ws, choice, cfg.get('block_klbf'))
    st = tj['stations']
    td_ft = None
    if meas and any((meas.get(k) or {}).get('depth_ft') for k in ('out', 'in', 'rotate')):
        td_ft = max(max((meas.get(k) or {}).get('depth_ft') or [0]) for k in ('out', 'in', 'rotate'))
    md_end = st[-1]['md'] / FT
    r = analyse([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], cfg, shoe_ft, meas, min(td_ft, md_end) if td_ft else None)
    r.update({'well': choice, 'display': next(w['display'] for w in out['wells'] if w['id'] == choice),
              'how': f"the survey ({tj['meta']['source']}); the string as declared ({cfg.get('source')}); "
                     + (f"cased to {shoe_ft:,.0f} ft (the deepest shoe declared)" if shoe_ft else 'no casing declared: all open hole')})
    out['result'] = r
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return f"Torque and drag: {p.get('detail', '')}"
    d = r['declared']
    L = [f"Torque and drag - {r['display']}: TD {r['td_ft']:,.0f} ft, mud {r['config']['mud_ppg']:g} ppg (buoyancy {r['buoyancy']}), mu cased {r['config']['mu_cased']:g}, open {r['config']['mu_open']:g}, block {r['block_klbf']:g} klbf",
         f"  string {r['string_air_klbf']:,.1f} klbf in air, {r['string_buoyed_klbf']:,.1f} buoyed; at TD: pick-up {d['out'][-1]:,.1f}, slack-off {d['in'][-1]:,.1f}, "
         f"rotating {d['rotate'][-1]:,.1f} klbf; off-bottom torque {d['torque'][-1]:,.2f} kft.lbf"]
    for k, f_ in r['fits'].items():
        mode, which = k.split(':')
        L.append(f"  measured {({'out': 'pick-up', 'in': 'slack-off', 'torque': 'off-bottom torque'})[mode]} fits mu {f_['mu']:.2f} in {which} hole "
                 f"({f_['points']} bins, rms {f_['rms']:.2f} {'kft.lbf' if mode == 'torque' else 'klbf'})")
    return '\n'.join(L)


def build_report(ws, version: str, well: Optional[str] = None, scene: str = 'workspace'):
    from . import client_reports as CR
    p = page(ws, scene, well)
    r = p['result']
    if not r:
        raise ValueError(p.get('detail') or 'nothing to model')
    now = datetime.now(timezone.utc)
    rid = f"TND-{now.strftime('%Y%m%dT%H%M%SZ')}"
    d = r['declared']
    front = [['Report ID', rid], ['Well', r['display']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['At TD', f"pick-up {d['out'][-1]:,.1f} klbf, slack-off {d['in'][-1]:,.1f} klbf, rotating {d['rotate'][-1]:,.1f} klbf, torque {d['torque'][-1]:,.2f} kft.lbf"],
             ['Friction factors', f"cased {r['config']['mu_cased']:g}, open {r['config']['mu_open']:g}" + ('; fitted: ' + ', '.join(f"{k.replace(':', ' ')} {v['mu']:.2f}" for k, v in r['fits'].items()) if r['fits'] else '')]]
    s1 = CR.Section('1', 'The model and the string', [f"Soft-string model (Johancsik et al., SPE-11380-PA). {r.get('how', '')}. Mud {r['config']['mud_ppg']:g} ppg, buoyancy factor {r['buoyancy']}; "
                                                       f"travelling block {r['block_klbf']:g} klbf added to the surface tension."], [])
    s1.tables.append(CR.Table(['Section (bit up)', 'Length (ft)', 'OD (in)', 'Weight (lb/ft, air)', 'Turns on (in)'],
                              [[x['what'], 'to surface' if x['length_ft'] is None else f"{x['length_ft']:,.0f}", f"{x['od_in']:g}", f"{x['weight_lb_ft']:g}", f"{x['contact_od_in']:g}"]
                               for x in r['config']['string']]))
    k = max(1, len(d['depth_ft']) // 50)
    s2 = CR.Section('2', 'By bit depth', ['Hook loads in klbf (the tension at the surface and the block), torque rotating off bottom in kft.lbf, at the declared friction factors.'], [])
    s2.tables.append(CR.Table(['Bit depth (ft)', 'Pick-up', 'Slack-off', 'Rotating', 'Torque'],
                              [[f"{d['depth_ft'][i]:,.0f}", f"{d['out'][i]:,.1f}", f"{d['in'][i]:,.1f}", f"{d['rotate'][i]:,.1f}", f"{d['torque'][i]:,.2f}"]
                               for i in list(range(0, len(d['depth_ft']), k)) + ([len(d['depth_ft']) - 1] if (len(d['depth_ft']) - 1) % k else [])]))
    paras = [f"{k_.replace(':', ' in ').replace('out', 'pick-up').replace('in in', 'slack-off in')}: mu {v['mu']:.2f} over {v['points']} depth bins (rms {v['rms']:.2f})" for k_, v in r['fits'].items()]
    s3 = CR.Section('3', 'Against the rig', paras or ['No trip or off-bottom rotation in the drilling record to compare.'], [])
    s4 = CR.Section('4', 'What this will not call a measurement', ['; '.join(p['not_a_measurement']) + '.'], [])
    return CR.Document(f"Torque and drag - {r['display']}", rid, front, [s1, s2, s3, s4], footer='Soft-string model: Johancsik, Friesen and Dawson, SPE-11380-PA (1984).',
                       data={'report_id': rid, **{k_: v for k_, v in r.items() if k_ != 'fan'}})


def write_report(ws, version: str, well: Optional[str] = None, actor: str = 'system', scene: str = 'workspace') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well, scene)
    if scene == 'example':
        dd, base = ws.reports_dir, 'torque_drag_example'
    else:
        dd, base = os.path.join(ws.reports_dir, 'wells', doc.data['well']), 'torque_drag'
    os.makedirs(dd, exist_ok=True)
    paths = CR.write(doc, dd, base)
    ws.audit(actor, 'report.torque_drag', {'well': doc.data.get('well'), 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


# -- a labelled record ------------------------------------------------------------------------------------------------------
def selftest_files(tmp: str, mu_cased: float = 0.18, mu_open: float = 0.24, block: float = 40.0, seed: int = 3) -> dict:
    """A survey (vertical to 3,000 ft, built to 90 degrees by 5,250 ft, a lateral to 9,000 ft) and a drilling record of a
    trip out from 9,000 ft and back in, stand by stand (90 ft at 1.5 ft/s, a minute in the slips between), with the
    pumps off, and the string rotated 30 ft off bottom at 100 rpm with the pumps on for five minutes every 900 ft on the
    way in - its hook loads and torque made by this model with the declared friction factors (cased 0.18 above a shoe at
    4,000 ft, open 0.24), a 40 klbf block and 0.3 klbf of noise."""
    import csv
    from datetime import timedelta
    rng = np.random.default_rng(seed)
    os.makedirs(tmp, exist_ok=True)
    md = np.arange(0.0, 9000.01, 100.0)
    inc = np.clip((md - 3000.0) / (5250.0 - 3000.0) * 90.0, 0.0, 90.0)
    sp = os.path.join(tmp, 'td_survey.csv')
    with open(sp, 'w', encoding='utf-8', newline='') as f:
        f.write('MD (ft),Inc (deg),Azi (deg)\n' + ''.join(f'{m:.1f},{i:.3f},45.0\n' for m, i in zip(md, inc)))
    string = check_string([{'what': 'BHA', 'length_ft': 300, 'od_in': 6.75, 'weight_lb_ft': 95.0, 'contact_od_in': 6.75},
                           {'what': 'drill pipe', 'length_ft': None, 'od_in': 5.0, 'weight_lb_ft': 22.6, 'contact_od_in': 6.625}])
    md_f, I, A = path_ft(md * FT, inc, np.full(len(md), 45.0), 30.0)
    mud, shoe = 10.0, 4000.0
    stands = np.arange(9000.0, 990.0, -90.0)
    cache = {}

    def hk(D, mode):
        key = (round(D, 1), mode)
        if key not in cache:
            mm = model(md_f, I, A, string, mud, shoe, mu_cased, mu_open, [D], block, modes=(mode,))
            cache[key] = (mm[mode][0], mm['torque'][0] if mode == 'rotate' else 0.0)
        return cache[key]
    ep = os.path.join(tmp, 'td_record.csv')
    t = datetime(2026, 9, 10, 0, 0, 0)
    rows = []

    def row(bit, hl, flow=0.0, rpm=0.0, tq=0.0, block_h=50.0):
        nonlocal t
        rows.append([t.strftime('%Y/%m/%d'), t.strftime('%H:%M:%S'), '9000.0', f'{bit:.2f}', '0.00', f'{hl + rng.normal(0, 0.3):.2f}', f'{block_h:.2f}',
                     f'{flow:.1f}', '150.0' if flow else '0.0', f'{rpm:.1f}', f'{tq + (rng.normal(0, 0.05) if rpm else 0):.3f}', '0'])
        t += timedelta(seconds=1)
    for a in stands:                                       # out: pull a stand, then the slips
        b = max(a - 90.0, 1000.0)
        for k in range(int((a - b) / 1.5)):
            bit = a - 1.5 * k
            row(bit, hk(round(bit / 10) * 10, 'out')[0], block_h=5 + 1.5 * k)
        for _ in range(60):
            row(b, block, block_h=95.0)
    ins = stands[::-1]
    for a in ins:                                         # in: run a stand, then the slips; rotate off bottom every 1,800 ft
        b = min(a + 90.0, 9000.0)
        if b <= a:
            continue
        for k in range(int((b - a) / 1.5)):
            bit = a + 1.5 * k
            row(bit, hk(round(bit / 10) * 10, 'in')[0], block_h=95 - 1.5 * k)
        if int(b) % 900 == 0:
            h_, q_ = hk(b - 30.0, 'rotate')
            for _ in range(300):
                row(b - 30.0, h_, flow=500.0, rpm=100.0, tq=q_, block_h=60.0)
        for _ in range(60):
            row(b, block, block_h=5.0)
    with open(ep, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Hook Load (klbs)', 'Block Height (feet)',
                    'Total Pump Output (gal_per_min)', 'Standpipe Pressure (psi)', 'Rotary RPM (RPM)', 'Convertible Torque (kft_lb)', 'On Bottom (unitless)'])
        w.writerows(rows)
    return {'survey': sp, 'record': ep, 'string': string, 'mud_ppg': mud, 'shoe_ft': shoe, 'mu_cased': mu_cased, 'mu_open': mu_open, 'block_klbf': block, 'rows': len(rows)}
