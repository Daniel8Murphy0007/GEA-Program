"""Hydraulics and ECD: the pressures the mud makes while it circulates, against the window the rock allows.

The mud's flow curve is the operator's six-speed viscometer readings (the dial at 600, 300, 200, 100, 6 and 3 rpm). As
API RP 13D lays it out, the fluid is a power-law fluid, tau = K gamma^n, read separately for the two places it flows:
inside the pipe from the 600 and 300 readings (the shear there is high), in the annulus from the 100 and 3 (it is low).

    n_p = log(R600 / R300) / log 2              K_p = 0.511 R600 / 1022^n_p     (Pa s^n; one dial degree is 0.511 Pa)
    n_a = log(R100 / R3) / log(100 / 3)         K_a = 0.511 R100 / 170.2^n_a

Inside a pipe of bore D at mean velocity V the effective viscosity is the Metzner-Reed one,
mu = K ((3n+1)/4n)^n (8V/D)^(n-1); in the annulus between a hole D2 and a pipe D1 the flow is a slot,
mu = K ((2n+1)/3n)^n (12V/(D2-D1))^(n-1). The Reynolds number is rho V D / mu (D2 - D1 in the annulus). The Fanning
friction factor joins three regimes the way the API method and Zamora et al. (AADE-05-NTCE-27) do (Churchill's
blending): laminar 16/Re in the pipe and 24/Re in the annulus; transitional, rising from the laminar value at the
critical Reynolds number 3470 - 1370 n; turbulent a/Re^b with a = (log10 n + 3.93)/50 and b = (1.75 - log10 n)/7
(Dodge and Metzner); f_int = (f_trans^-8 + f_turb^-8)^(-1/8), f = (f_lam^12 + f_int^12)^(1/12). The pressure lost
over a length L is 2 f rho V^2 L / D.

The pressure at the standpipe is the sum of the surface lines (declared at a rate and scaled as the 1.86th power of
the rate, turbulent flow in smooth lines), the string, the tools that take pressure (motor, MWD: declared), the bit
(rho Q^2 / (2 Cd^2 A^2), Cd 0.95, A the nozzles' total flow area) and the annulus. The equivalent circulating density
at a depth is the mud weight plus the annular friction above it over 0.052 times its true vertical depth. Tripping
pipe pushes or pulls mud through the annulus: with the string closed at the bottom, the mud moves at the pipe's speed
times (Kc + d^2 / (D^2 - d^2)), Kc = 0.45 the clinging constant (Burkhardt, JPT 1961), at the pipe's peak speed (1.5
times the stand's average); surge adds that pressure below the pipe, swab takes it away. Against the pore pressure
and fracture gradient (from the Pore pressure tab, or declared), the program gives the margin at every depth and the
fastest a stand may be run or pulled at each bit depth.

From the drilling record it takes the standpipe pressure and the pump rate while the pumps run, as the median of each
250 ft of bit depth, and sets the model's standpipe pressure at the same rate and depth against it.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
IN = 0.0254
GPM = 6.30901964e-5                     # m3/s per US gal/min
PPG = 119.826427                        # kg/m3 per lb/gal
PSI = 6894.757293                       # Pa per psi
DIAL_PA = 0.511                         # Pa per viscometer dial degree (the standard R1-B1-F1 spring)
RATES = {600: 1022.0, 300: 511.0, 200: 340.7, 100: 170.2, 6: 10.22, 3: 5.11}     # 1/s
CD = 0.95
DEFAULTS = {'mw_ppg': 10.0, 'fann': {}, 'flow_gpm': 500.0, 'nozzles_32': [], 'bit_in': None, 'string': None, 'hole': None,
            'surface_psi': 0.0, 'surface_ref_gpm': None, 'tools_psi': 0.0, 'trip_ft_min': 90.0, 'peak_factor': 1.5, 'clinging': 0.45,
            'pp_ppg': None, 'fg_ppg': None, 'step_ft': 100.0, 'source': ''}
NOT_A_MEASUREMENT = ['the equivalent circulating density: computed from the declared mud and geometry, not read by a downhole pressure '
                     'tool', 'the cuttings in the annulus: their weight is not added to the mud', 'the pipe centred in the hole and its tool '
                     'joints left out: an eccentric pipe loses less in laminar flow, tool joints more', 'surge and swab: the steady-flow '
                     'estimate at the pipe\'s peak speed, not the mud\'s gel or its inertia when the pipe starts and stops']


# -- the mud ------------------------------------------------------------------------------------------------------------
def rheology(fann: Dict) -> dict:
    """The power-law n and K (Pa s^n) for the pipe (600/300) and the annulus (100/3, or 600/300 when either is
    missing), and the Bingham plastic viscosity and yield point for the record."""
    r = {int(k): float(v) for k, v in (fann or {}).items() if v not in (None, '')}
    if 600 not in r or 300 not in r:
        raise ValueError('the viscometer readings at 600 and 300 rpm are needed (the dial, as read)')
    for k, v in r.items():
        if k not in RATES:
            raise ValueError(f'a viscometer speed is 600, 300, 200, 100, 6 or 3 rpm, not {k}')
        if not (0.0 < v <= 330.0):
            raise ValueError(f'the dial at {k} rpm reads between 0 and 300, not {v:g}')
    speeds = sorted(r)
    if any(r[a] > r[b] for a, b in zip(speeds, speeds[1:])):
        raise ValueError('the dial rises with the speed: a faster speed reads at least the slower one')
    n_p = math.log(r[600] / r[300]) / math.log(2.0)
    if not (0.05 <= n_p <= 1.0):
        raise ValueError(f'the 600 and 300 readings give n = {n_p:.3f}: a drilling fluid thins with shear (0 < n <= 1)')
    K_p = DIAL_PA * r[600] / RATES[600] ** n_p
    if 100 in r and 3 in r and r[100] > r[3]:
        n_a = math.log(r[100] / r[3]) / math.log(RATES[100] / RATES[3])
        K_a = DIAL_PA * r[100] / RATES[100] ** n_a
        ann_from = '100 and 3 rpm'
    else:
        n_a, K_a, ann_from = n_p, K_p, '600 and 300 rpm (no 100 and 3 reading)'
    pv = r[600] - r[300]
    yp = r[300] - pv
    out = {'n_pipe': round(n_p, 4), 'K_pipe': K_p, 'n_ann': round(n_a, 4), 'K_ann': K_a, 'annulus_from': ann_from,
           'pv_cp': round(pv, 2), 'yp_lbf_100ft2': round(yp, 2), 'readings': {str(k): r[k] for k in speeds[::-1]}}
    if 6 in r and 3 in r:
        out['tau0_lbf_100ft2'] = round(2.0 * r[3] - r[6], 2)
    return out


def friction(Re: float, n: float, laminar: float) -> Tuple[float, str]:
    """Fanning friction factor (laminar = 16 in a pipe, 24 in the annulus) and the regime."""
    Re = max(float(Re), 1e-9)
    Rc = 3470.0 - 1370.0 * n
    f_lam = laminar / Re
    f_trans = laminar * Re / Rc ** 2
    a, b = (math.log10(n) + 3.93) / 50.0, (1.75 - math.log10(n)) / 7.0
    f_turb = a / Re ** b
    f_int = (f_trans ** -8 + f_turb ** -8) ** (-1.0 / 8.0)
    f = (f_lam ** 12 + f_int ** 12) ** (1.0 / 12.0)
    regime = 'laminar' if Re < Rc else ('transitional' if Re < Rc + 800.0 else 'turbulent')
    return f, regime


def pipe_loss(q_gpm: float, d_in: float, length_ft: float, rho_ppg: float, n: float, K: float) -> dict:
    Q, D, L, rho = q_gpm * GPM, d_in * IN, length_ft * FT, rho_ppg * PPG
    V = Q / (math.pi * D * D / 4.0)
    mu = K * ((3 * n + 1) / (4 * n)) ** n * (8.0 * V / D) ** (n - 1) if V > 0 else float('inf')
    Re = rho * V * D / mu if V > 0 else 0.0
    f, reg = friction(Re, n, 16.0) if V > 0 else (0.0, 'no flow')
    dp = 2.0 * f * rho * V * V * L / D / PSI
    return {'psi': dp, 'v_ft_min': V / FT * 60.0, 're': Re, 'regime': reg, 'f': f, 'mu_cp': mu * 1000.0}


def annulus_loss(q_gpm: Optional[float], dh_in: float, dp_in: float, length_ft: float, rho_ppg: float, n: float, K: float,
                 v_ft_min: Optional[float] = None) -> dict:
    """The annular loss at a pump rate, or at a mud velocity given directly (surge and swab)."""
    D2, D1, L, rho = dh_in * IN, dp_in * IN, length_ft * FT, rho_ppg * PPG
    if D2 <= D1:
        raise ValueError(f'the hole ({dh_in:g} in) must be wider than the pipe in it ({dp_in:g} in)')
    V = (v_ft_min * FT / 60.0) if v_ft_min is not None else q_gpm * GPM / (math.pi * (D2 * D2 - D1 * D1) / 4.0)
    Dh = D2 - D1
    if V <= 0:
        return {'psi': 0.0, 'v_ft_min': 0.0, 're': 0.0, 'regime': 'no flow', 'f': 0.0, 'mu_cp': None}
    mu = K * ((2 * n + 1) / (3 * n)) ** n * (12.0 * V / Dh) ** (n - 1)
    Re = rho * V * Dh / mu
    f, reg = friction(Re, n, 24.0)
    dp = 2.0 * f * rho * V * V * L / Dh / PSI
    return {'psi': dp, 'v_ft_min': V / FT * 60.0, 're': Re, 'regime': reg, 'f': f, 'mu_cp': mu * 1000.0}


def bit(q_gpm: float, rho_ppg: float, nozzles_32: List[float], bit_in: Optional[float], cd: float = CD) -> dict:
    """The bit's pressure drop, jet velocity, hydraulic horsepower, horsepower per square inch and jet impact force."""
    if not nozzles_32:
        return {'psi': 0.0, 'tfa_in2': None, 'note': 'no nozzles declared: the bit\'s pressure drop is left out'}
    A = sum(math.pi / 4.0 * (float(d) / 32.0) ** 2 for d in nozzles_32)        # in2
    Q, rho, Am = q_gpm * GPM, rho_ppg * PPG, A * IN * IN
    dp = rho * Q * Q / (2.0 * cd * cd * Am * Am) / PSI
    vn = Q / Am / FT                                                           # ft/s
    hhp = dp * q_gpm / 1714.0
    out = {'psi': dp, 'tfa_in2': round(A, 4), 'jet_ft_s': round(vn, 1), 'hhp': round(hhp, 1),
           'impact_lbf': round(0.01823 * cd * q_gpm * math.sqrt(rho_ppg * dp), 1)}
    if bit_in:
        out['hsi'] = round(hhp / (math.pi / 4.0 * float(bit_in) ** 2), 2)
    return out


# -- the string and the hole ---------------------------------------------------------------------------------------------
def check_string(sections: List[dict]) -> List[dict]:
    """Sections from the bit up: {what, length_ft (None for the top: to the surface), od_in, id_in}."""
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
        od, idd = float(s.get('od_in') or 0), float(s.get('id_in') or 0)
        if not (1.0 <= od <= 30.0):
            raise ValueError(f'{what}: the outside diameter is in inches, 1 to 30')
        if not (0.5 <= idd < od):
            raise ValueError(f'{what}: the bore is in inches, at least 0.5 and less than the outside diameter')
        out.append({'what': what, 'length_ft': L, 'od_in': od, 'id_in': idd})
    out[-1]['length_ft'] = None
    return out


def check_hole(sections: List[dict]) -> List[dict]:
    """Sections from the surface down: {what, bottom_md_ft (None for the last: to TD), id_in} - each casing string's
    inside diameter to its shoe, then the open hole."""
    if not sections:
        raise ValueError('the hole needs at least one section (the open hole)')
    out, prev = [], 0.0
    for i, s in enumerate(sections):
        what = str(s.get('what') or '').strip() or f'section {i + 1}'
        b = s.get('bottom_md_ft')
        if b in (None, ''):
            if i != len(sections) - 1:
                raise ValueError('only the last (open) section may run to TD')
            b = None
        else:
            b = float(b)
            if not (math.isfinite(b) and b > prev):
                raise ValueError(f'{what}: the bottom is a measured depth in feet, deeper than the section above')
            prev = b
        d = float(s.get('id_in') or 0)
        if not (2.0 <= d <= 40.0):
            raise ValueError(f'{what}: the inside diameter is in inches, 2 to 40')
        out.append({'what': what, 'bottom_md_ft': b, 'id_in': d})
    out[-1]['bottom_md_ft'] = None
    return out


def parse_sections(text: str, kind: str) -> List[dict]:
    """string: 'what:length_ft:od_in:id_in; ...' from the bit up, the top section's length blank.
    hole: 'what:bottom_md_ft:id_in; ...' from the surface down, the open hole's bottom blank."""
    out = []
    for part in [p.strip() for p in (text or '').split(';') if p.strip()]:
        f = [x.strip() for x in part.split(':')]
        if kind == 'string':
            if len(f) != 4:
                raise ValueError(f'a string section is what:length_ft:od_in:id_in, not {part!r}')
            out.append({'what': f[0], 'length_ft': f[1] or None, 'od_in': f[2], 'id_in': f[3]})
        else:
            if len(f) != 3:
                raise ValueError(f'a hole section is what:bottom_md_ft:id_in, not {part!r}')
            out.append({'what': f[0], 'bottom_md_ft': f[1] or None, 'id_in': f[2]})
    return check_string(out) if kind == 'string' else check_hole(out)


def _segments(bit_ft: float, string: List[dict], hole: List[dict]) -> List[dict]:
    """The annulus from the surface to the bit, cut wherever the string or the hole changes: [{top, bottom, hole_in, od_in}]."""
    s_bounds, below = [], 0.0
    for s in string:
        top = below + (s['length_ft'] if s['length_ft'] is not None else 1e9)
        s_bounds.append((max(0.0, bit_ft - top), bit_ft - below, s)); below = top
    h_bounds, top = [], 0.0
    for h in hole:
        bot = h['bottom_md_ft'] if h['bottom_md_ft'] is not None else 1e9
        h_bounds.append((top, bot, h)); top = bot
    cuts = sorted({0.0, bit_ft} | {x for a, b, _ in s_bounds for x in (a, b) if 0 < x < bit_ft} | {x for a, b, _ in h_bounds for x in (a, b) if 0 < x < bit_ft})
    segs = []
    for a, b in zip(cuts, cuts[1:]):
        m = 0.5 * (a + b)
        s = next(x for lo, hi, x in s_bounds if lo <= m < hi)
        h = next(x for lo, hi, x in h_bounds if lo <= m < hi)
        segs.append({'top_ft': a, 'bottom_ft': b, 'hole_in': h['id_in'], 'hole': h['what'], 'od_in': s['od_in'], 'pipe': s['what']})
    return segs


def _pipe_lengths(bit_ft: float, string: List[dict]) -> List[Tuple[dict, float]]:
    out, used = [], 0.0
    for s in string:
        L = s['length_ft'] if s['length_ft'] is not None else max(0.0, bit_ft - used)
        L = min(L, max(0.0, bit_ft - used))
        out.append((s, L)); used += L
    return out


def surface_psi(cfg: dict, q: float) -> float:
    s, ref = float(cfg.get('surface_psi') or 0.0), cfg.get('surface_ref_gpm')
    if not s:
        return 0.0
    return s * (q / float(ref)) ** 1.86 if ref else s


def circulate(cfg: dict, rheo: dict, bit_ft: float, q_gpm: float, tvd_of=None) -> dict:
    """Every pressure at a pump rate with the bit at a depth: the breakdown of the standpipe pressure, the annulus
    section by section, and the bottom-hole ECD."""
    rho = float(cfg['mw_ppg'])
    pipe = [{'what': s['what'], 'length_ft': round(L, 1), 'id_in': s['id_in'], **pipe_loss(q_gpm, s['id_in'], L, rho, rheo['n_pipe'], rheo['K_pipe'])}
            for s, L in _pipe_lengths(bit_ft, cfg['string']) if L > 0]
    ann = []
    for g in _segments(bit_ft, cfg['string'], cfg['hole']):
        a = annulus_loss(q_gpm, g['hole_in'], g['od_in'], g['bottom_ft'] - g['top_ft'], rho, rheo['n_ann'], rheo['K_ann'])
        ann.append({**g, **a})
    b = bit(q_gpm, rho, cfg.get('nozzles_32') or [], cfg.get('bit_in'))
    parts = {'surface': surface_psi(cfg, q_gpm), 'string': sum(p['psi'] for p in pipe), 'tools': float(cfg.get('tools_psi') or 0.0),
             'bit': b['psi'], 'annulus': sum(a['psi'] for a in ann)}
    tvd = tvd_of(bit_ft) if tvd_of else bit_ft
    ecd = rho + parts['annulus'] / (0.052 * tvd) if tvd > 0 else rho
    return {'bit_ft': round(bit_ft, 1), 'q_gpm': q_gpm, 'parts': {k: round(v, 1) for k, v in parts.items()}, 'spp_psi': round(sum(parts.values()), 1),
            'pipe': pipe, 'annulus': ann, 'bit': b, 'ecd_bit_ppg': round(ecd, 3), 'tvd_bit_ft': round(tvd, 1),
            'bit_share': round(parts['bit'] / sum(parts.values()), 3) if sum(parts.values()) > 0 else None}


def ecd_profile(cfg: dict, rheo: dict, bit_ft: float, q_gpm: float, tvd_of, step_ft: float = 100.0) -> Dict[str, list]:
    """The ECD at every depth from the surface to the bit: the mud weight plus the annular friction above that depth."""
    c = circulate(cfg, rheo, bit_ft, q_gpm, tvd_of)
    ann = c['annulus']
    depths = np.unique(np.concatenate([np.arange(step_ft, bit_ft, step_ft), [bit_ft]]))
    out = {'md_ft': [], 'tvd_ft': [], 'ecd_ppg': []}
    for z in depths:
        above = sum(a['psi'] * max(0.0, min(z, a['bottom_ft']) - a['top_ft']) / (a['bottom_ft'] - a['top_ft']) for a in ann if a['top_ft'] < z)
        tvd = tvd_of(z)
        out['md_ft'].append(round(float(z), 1)); out['tvd_ft'].append(round(tvd, 1))
        out['ecd_ppg'].append(round(cfg['mw_ppg'] + above / (0.052 * tvd), 3) if tvd > 1.0 else cfg['mw_ppg'])
    return out


def surge_swab(cfg: dict, rheo: dict, bit_ft: float, v_pipe_ft_min: float, tvd_of) -> dict:
    """The surge (running in) and swab (pulling out) pressure below the pipe with the bit at a depth, the string closed
    at the bottom, at the pipe's peak speed (peak_factor times v_pipe)."""
    kc = float(cfg.get('clinging') or 0.45)
    vp = float(v_pipe_ft_min) * float(cfg.get('peak_factor') or 1.5)
    dp = 0.0
    for g in _segments(bit_ft, cfg['string'], cfg['hole']):
        d2, d1 = g['hole_in'], g['od_in']
        v_mud = vp * (kc + d1 * d1 / (d2 * d2 - d1 * d1))
        dp += annulus_loss(None, d2, d1, g['bottom_ft'] - g['top_ft'], cfg['mw_ppg'], rheo['n_ann'], rheo['K_ann'], v_ft_min=v_mud)['psi']
    tvd = tvd_of(bit_ft)
    return {'psi': round(dp, 1), 'surge_ppg': round(cfg['mw_ppg'] + dp / (0.052 * tvd), 3), 'swab_ppg': round(cfg['mw_ppg'] - dp / (0.052 * tvd), 3),
            'peak_ft_min': round(vp, 1)}


def max_trip_speed(cfg: dict, rheo: dict, bit_ft: float, tvd_of, pp: Optional[float], fg: Optional[float]) -> dict:
    """The fastest average stand speed (ft/min) that keeps the surge EMW at the bit at or below the fracture gradient and
    the swab EMW at or above the pore pressure."""
    def ok(v, which):
        s = surge_swab(cfg, rheo, bit_ft, v, tvd_of)
        return s['surge_ppg'] <= fg if which == 'run' else s['swab_ppg'] >= pp
    out = {}
    for which, lim in (('run', fg), ('pull', pp)):
        if lim is None:
            out[which] = None
            continue
        if not ok(1.0, which):
            out[which] = 0.0
            continue
        lo, hi = 1.0, 2000.0
        if ok(hi, which):
            out[which] = hi
            continue
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if ok(mid, which) else (lo, mid)
        out[which] = round(lo, 0)
    return out


# -- the record ------------------------------------------------------------------------------------------------------------
def measured(ws, well_id: str, bin_ft: float = 250.0) -> Optional[dict]:
    """The standpipe pressure and pump rate while the pumps run, as the median of each bin of bit depth."""
    from . import rig_state as RS
    from . import series_cache as SCH
    try:
        c = SCH.ensure(ws, well_id)
    except Exception:
        return None
    if c is None or c.meta.get('index_kind') != 'time_s':
        return None
    names = {x['name'] for x in c.meta['channels'] if x.get('present')}
    if not {'Standpipe Pressure', 'Total Pump Output', 'Bit Depth'} <= names:
        return None
    g = lambda n: np.asarray(c.channel(n), dtype=np.float64) if n in names else None
    t = np.asarray(c.index, dtype=np.float64)
    drl = c.extra('drilling')
    drl = np.asarray(drl, dtype=bool) if drl is not None else np.zeros(len(t), bool)
    ch = {'hookload': g('Hook Load'), 'block': g('Block Height'), 'bit': g('Bit Depth'), 'hole': g('Hole Depth'), 'flow': g('Total Pump Output'),
          'spp': g('Standpipe Pressure'), 'rpm': g('Rotary RPM')}
    cl = RS.classify(t, ch, drl, None)
    st = cl['state']
    spp, q, b = ch['spp'], ch['flow'], ch['bit']
    sel = np.isin(st, [RS.ROT, RS.SLIDE, RS.CIRC, RS.WASH, RS.REAM]) & np.isfinite(spp) & np.isfinite(q) & np.isfinite(b) & (q > 50.0) & (b > 0)
    if sel.sum() < 60:
        return {'depth_ft': [], 'spp_psi': [], 'q_gpm': [], 'n': []}
    k = np.floor(b[sel] / bin_ft)
    out = {'depth_ft': [], 'spp_psi': [], 'q_gpm': [], 'n': []}
    for u in np.unique(k):
        m = k == u
        if m.sum() < 30:
            continue
        out['depth_ft'].append(round(float(np.median(b[sel][m])), 1)); out['spp_psi'].append(round(float(np.median(spp[sel][m])), 1))
        out['q_gpm'].append(round(float(np.median(q[sel][m])), 1)); out['n'].append(int(m.sum()))
    return out


def against_record(cfg: dict, rheo: dict, meas: dict, tvd_of) -> Optional[dict]:
    if not meas or len(meas.get('depth_ft') or []) < 2:
        return None
    rows = []
    for d, p, q, n in zip(meas['depth_ft'], meas['spp_psi'], meas['q_gpm'], meas['n']):
        m = circulate(cfg, rheo, d, q, tvd_of)['spp_psi']
        rows.append({'depth_ft': d, 'q_gpm': q, 'measured_psi': p, 'model_psi': m, 'difference_psi': round(p - m, 1), 'samples': n})
    diff = np.array([r['difference_psi'] for r in rows])
    ratio = np.array([r['measured_psi'] / r['model_psi'] for r in rows if r['model_psi'] > 0])
    return {'rows': rows, 'median_difference_psi': round(float(np.median(diff)), 1), 'spread_psi': round(float(np.percentile(diff, 90) - np.percentile(diff, 10)), 1),
            'median_ratio': round(float(np.median(ratio)), 3) if len(ratio) else None, 'bins': len(rows)}


# -- the workspace ---------------------------------------------------------------------------------------------------------
def _store_path(ws) -> str:
    return os.path.join(ws.path, 'drillstring', 'hydraulics.json')


def _load(ws) -> dict:
    try:
        with open(_store_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wells': {}}


def well_config(ws, well_id: str) -> dict:
    return {**DEFAULTS, **((_load(ws).get('wells') or {}).get(well_id) or {})}


def program_config(ws, well_id: str) -> dict:
    """The well's hydraulics with its hole read from the casing program (each depth's innermost string's inside diameter,
    then the bit) when the program gives one; the hole declared for hydraulics otherwise."""
    from . import casing_design as CSG
    cfg = well_config(ws, well_id)
    pr = CSG.program(ws, well_id)
    oh = cfg.get('bit_in') or CSG.well_config(ws, well_id).get('td_hole_in')
    hole, why = CSG.hole_sections(pr['strings'], oh)
    if hole:
        cfg['hole'], cfg['hole_from'] = check_hole(hole), 'from the casing program'
    elif cfg.get('hole'):
        cfg['hole_from'] = 'as declared for hydraulics' + (f' (the casing program cannot give it: {why})' if pr['strings'] else '')
    else:
        cfg['hole_from'], cfg['hole_missing'] = None, why
    return cfg


LIMITS = {'mw_ppg': (6.0, 22.0), 'flow_gpm': (10.0, 3000.0), 'bit_in': (2.0, 40.0), 'surface_psi': (0.0, 2000.0), 'surface_ref_gpm': (10.0, 3000.0),
          'tools_psi': (0.0, 3000.0), 'trip_ft_min': (1.0, 1000.0), 'peak_factor': (1.0, 3.0), 'clinging': (0.0, 1.0), 'pp_ppg': (6.0, 22.0),
          'fg_ppg': (6.0, 25.0), 'step_ft': (20.0, 1000.0)}


def set_well(ws, actor: str, well_id: str, source: str = '', string=None, hole=None, fann=None, nozzles_32=None, **vals) -> dict:
    """Declare a well's hydraulics: the string (bit up, with its bores), the hole (surface down: casing IDs to their
    shoes, the open hole), the mud (weight and viscometer readings), the pump rate, the nozzles and the rest."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    d = _load(ws)
    cur = dict((d.get('wells') or {}).get(well_id) or {})
    changed = False
    if string is not None:
        cur['string'] = check_string([dict(x) for x in string]) if not isinstance(string, str) else parse_sections(string, 'string'); changed = True
    if hole is not None:
        from . import casing_design as CSG
        if CSG.hole_sections(CSG.program(ws, well_id)['strings'], cur.get('bit_in') or vals.get('bit_in') or CSG.well_config(ws, well_id).get('td_hole_in'))[0]:
            raise ValueError("this well's hole comes from its casing program (the Casing tab, or the [casing] table): change it there, and give the bit here")
        cur['hole'] = check_hole([dict(x) for x in hole]) if not isinstance(hole, str) else parse_sections(hole, 'hole'); changed = True
    if fann is not None:
        f = {str(int(k)): float(v) for k, v in dict(fann).items() if v not in (None, '')}
        rheology(f)
        cur['fann'] = f; changed = True
    if nozzles_32 is not None:
        nz = [float(x) for x in (nozzles_32.replace(',', ' ').split() if isinstance(nozzles_32, str) else nozzles_32)]
        if any(not (6.0 <= x <= 32.0) for x in nz) or len(nz) > 12:
            raise ValueError('nozzles are in 32nds of an inch, 6 to 32 each, at most 12')
        cur['nozzles_32'] = nz; changed = True
    for key, v in vals.items():
        if key not in LIMITS:
            raise ValueError(f'unknown setting {key}')
        if v in (None, ''):
            continue
        if str(v).strip().lower() == 'none' and key in ('pp_ppg', 'fg_ppg', 'surface_ref_gpm', 'bit_in'):
            cur[key] = None; changed = True
            continue
        x = float(v)
        lo, hi = LIMITS[key]
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{key} must be between {lo:g} and {hi:g}')
        cur[key] = x; changed = True
    if changed:
        if not (source or '').strip():
            raise ValueError('say where the figures came from (e.g. "mud report 2026-06-14, BHA report run 3")')
        cur['source'] = source.strip()
    cur['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'); cur['by'] = actor
    d.setdefault('wells', {})[well_id] = cur
    os.makedirs(os.path.dirname(_store_path(ws)), exist_ok=True)
    with open(_store_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(_store_path(ws) + '.tmp', _store_path(ws))
    ws.audit(actor, 'hydraulics.declare', {'well': well_id, **{k: v for k, v in cur.items() if k not in ('utc', 'by')}})
    return well_config(ws, well_id)


def recorder_path(ws, well_id: str) -> Optional[dict]:
    """The drilling recorder's own MD-TVD table for a well with no survey attached (its 'Interpolated TVD' against the
    hole depth, written with the drilling record): {md_ft, tvd_ft, inc_deg, source}, or None. The inclination of each
    step is the one whose cosine is the TVD it adds over the MD; the azimuth is not in the record."""
    p = os.path.join(ws.reports_dir, 'wells', well_id, 'drilling_record.json')
    try:
        with open(p, encoding='utf-8') as f:
            mt = (json.load(f) or {}).get('md_tvd') or {}
    except (OSError, ValueError):
        return None
    rows = [r for r in (mt.get('rows') or []) if r.get('md_ft') is not None and r.get('tvd_ft') is not None]
    if mt.get('status') != 'OK' or len(rows) < 3:
        return None
    md = [0.0] + [float(r['md_ft']) for r in rows]
    tv = [0.0] + [float(r['tvd_ft']) for r in rows]
    keep_md, keep_tv = [md[0]], [tv[0]]
    for m, t in zip(md[1:], tv[1:]):
        if m > keep_md[-1] + 1.0:
            keep_md.append(m); keep_tv.append(min(t, keep_tv[-1] + (m - keep_md[-2])))
    inc = [0.0]
    for i in range(1, len(keep_md)):
        c = (keep_tv[i] - keep_tv[i - 1]) / (keep_md[i] - keep_md[i - 1])
        inc.append(math.degrees(math.acos(max(-1.0, min(1.0, c)))))
    return {'md_ft': keep_md, 'tvd_ft': keep_tv, 'inc_deg': inc,
            'source': "the drilling recorder's Interpolated TVD against the hole depth (no survey attached): an interpolation the recorder made, "
                      "not a survey; the azimuth is not in it"}


def depth_path(ws, well_id: str, plan_too: bool = False) -> Optional[dict]:
    """A well's MD to TVD table in feet: its survey (every 30 ft along it), else the drilling recorder's own Interpolated
    TVD, else (with plan_too) its directional plan. {md_ft, tvd_ft, from: survey|recorder|plan, source}, or None."""
    from . import well3d as W3
    for kind in (('trajectory', 'plan') if plan_too else ('trajectory',)):
        try:
            tj = W3.trajectory_of(ws, well_id, kind) if ws.well(well_id).get(kind) else None
        except Exception:
            tj = None
        if tj and len(tj['stations']) >= 2:
            st = tj['stations']
            f, md_end = _tvd_function([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st])
            grid = sorted(set([0.0] + [float(x) for x in np.arange(30.0, md_end, 30.0)] + [md_end]))
            name = 'survey' if kind == 'trajectory' else 'plan'
            return {'md_ft': grid, 'tvd_ft': [f(m) for m in grid], 'from': name,
                    'source': f"the {'survey' if name == 'survey' else 'directional plan'} ({(tj.get('meta') or {}).get('source') or 'as attached'})"}
        if kind == 'trajectory':
            rp = recorder_path(ws, well_id)
            if rp:
                return {'md_ft': rp['md_ft'], 'tvd_ft': rp['tvd_ft'], 'from': 'recorder', 'source': rp['source']}
    return None


def tvd_at(path: dict, md_ft: float) -> float:
    """The TVD at a measured depth on a depth path; past its last station, on along the last station's slope."""
    m, t = path['md_ft'], path['tvd_ft']
    md_ft = float(md_ft)
    if md_ft <= m[-1]:
        return float(np.interp(md_ft, m, t))
    k = len(m) - 1
    j = max(0, k - 1)
    while j > 0 and m[k] - m[j] < 30.0:
        j -= 1
    slope = (t[k] - t[j]) / (m[k] - m[j]) if m[k] > m[j] else 1.0
    return float(t[k] + max(0.0, min(1.0, slope)) * (md_ft - m[k]))


def _tvd_function(md_m, inc, azi):
    from . import well3d as W3
    p = W3.Path(md_m, inc, azi, 0.0, 0.0, 0.0, 0.0)
    grid = np.unique(np.concatenate([np.asarray(p.md), np.arange(0.0, float(p.md[-1]), 30.0 * FT)]))
    tv = np.array([p.at(m)[2] for m in grid])
    gf, tf = grid / FT, tv / FT
    return lambda md_ft: float(np.interp(float(md_ft), gf, tf)), float(gf[-1])


def _window(ws, well_id: Optional[str], cfg: dict) -> Tuple[Optional[dict], str]:
    """The pore pressure and fracture gradient by TVD (ft): the Pore pressure tab's for the well, else the declared."""
    if ws is not None and well_id:
        try:
            from . import pore_pressure as PPM
            r = (PPM.page(ws, well=well_id) or {}).get('result')
        except Exception:
            r = None
        if r and r.get('well') == well_id:
            c = r['curves']
            z = [t / FT for t, a, b in zip(c['tvd_m'], c['pp_ppg'], c['fg_ppg']) if a is not None and b is not None]
            pp = [a for a, b in zip(c['pp_ppg'], c['fg_ppg']) if a is not None and b is not None]
            fg = [b for a, b in zip(c['pp_ppg'], c['fg_ppg']) if a is not None and b is not None]
            if len(z) >= 2:
                return {'tvd_ft': z, 'pp_ppg': pp, 'fg_ppg': fg}, 'the Pore pressure tab (Eaton)'
    if cfg.get('pp_ppg') or cfg.get('fg_ppg'):
        return {'pp_const': cfg.get('pp_ppg'), 'fg_const': cfg.get('fg_ppg')}, 'declared (one pore pressure and one fracture gradient for the hole)'
    return None, 'none: neither the Pore pressure tab nor a declared window'


def _at(win: Optional[dict], tvd_ft: float) -> Tuple[Optional[float], Optional[float]]:
    if not win:
        return None, None
    if 'tvd_ft' in win:
        z = win['tvd_ft']
        if tvd_ft < z[0] or tvd_ft > z[-1]:
            return None, None
        return float(np.interp(tvd_ft, z, win['pp_ppg'])), float(np.interp(tvd_ft, z, win['fg_ppg']))
    return win.get('pp_const'), win.get('fg_const')


def analyse(md_m, inc, azi, cfg: dict, td_ft: Optional[float] = None, window: Optional[dict] = None, window_how: str = '',
            meas: Optional[dict] = None, recorder: Optional[dict] = None) -> dict:
    if not cfg.get('string'):
        raise ValueError('declare the drill string with its bores (sections from the bit up)')
    if not cfg.get('hole'):
        raise ValueError('declare the hole: each casing string\'s inside diameter to its shoe, then the open hole')
    rheo = rheology(cfg.get('fann') or {})
    if recorder:
        gm, gt = np.asarray(recorder['md_ft']), np.asarray(recorder['tvd_ft'])
        tvd_of, md_end = (lambda md_ft: float(np.interp(float(md_ft), gm, gt))), float(gm[-1])
    else:
        tvd_of, md_end = _tvd_function(md_m, inc, azi)
    td = float(td_ft or md_end)
    q = float(cfg['flow_gpm'])
    c = circulate(cfg, rheo, td, q, tvd_of)
    prof = ecd_profile(cfg, rheo, td, q, tvd_of, float(cfg.get('step_ft') or 100.0))
    prof['pp_ppg'], prof['fg_ppg'], prof['margin_fg_ppg'], prof['margin_pp_ppg'] = [], [], [], []
    for z, e in zip(prof['tvd_ft'], prof['ecd_ppg']):
        pp, fg = _at(window, z)
        prof['pp_ppg'].append(round(pp, 3) if pp is not None else None); prof['fg_ppg'].append(round(fg, 3) if fg is not None else None)
        prof['margin_fg_ppg'].append(round(fg - e, 3) if fg is not None else None)
        prof['margin_pp_ppg'].append(round(cfg['mw_ppg'] - pp, 3) if pp is not None else None)
    fgm = [(m, d) for m, d in zip(prof['margin_fg_ppg'], prof['md_ft']) if m is not None]
    ppm = [(m, d) for m, d in zip(prof['margin_pp_ppg'], prof['md_ft']) if m is not None]
    trips = {'depth_ft': [], 'surge_ppg': [], 'swab_ppg': [], 'run_max_ft_min': [], 'pull_max_ft_min': []}
    step = max(500.0, float(cfg.get('step_ft') or 100.0))
    for d in np.unique(np.concatenate([np.arange(step, td, step), [td]])):
        s = surge_swab(cfg, rheo, float(d), float(cfg['trip_ft_min']), tvd_of)
        pp, fg = _at(window, tvd_of(float(d)))
        mx = max_trip_speed(cfg, rheo, float(d), tvd_of, pp, fg) if (pp is not None or fg is not None) else {'run': None, 'pull': None}
        trips['depth_ft'].append(round(float(d), 1)); trips['surge_ppg'].append(s['surge_ppg']); trips['swab_ppg'].append(s['swab_ppg'])
        trips['run_max_ft_min'].append(mx['run']); trips['pull_max_ft_min'].append(mx['pull'])
    rates = sorted({max(50.0, round(q * f / 10.0) * 10.0) for f in (0.5, 0.75, 1.0, 1.25)})
    by_rate = [{'q_gpm': r_, **{k: v for k, v in circulate(cfg, rheo, td, r_, tvd_of).items() if k in ('spp_psi', 'ecd_bit_ppg', 'parts')}} for r_ in rates]
    rec = against_record(cfg, rheo, meas, tvd_of) if meas else None
    return {'td_ft': round(td, 1), 'tvd_td_ft': round(tvd_of(td), 1), 'rheology': rheo, 'circulating': c, 'ecd': prof, 'trips': trips, 'by_rate': by_rate,
            'window_how': window_how, 'min_fg_margin': ({'ppg': min(fgm)[0], 'md_ft': min(fgm)[1]} if fgm else None),
            'min_pp_margin': ({'ppg': min(ppm)[0], 'md_ft': min(ppm)[1]} if ppm else None), 'record': rec, 'measured': meas, 'config': cfg}


def example() -> dict:
    """The worked example: Volve 15/9-F-10's survey (Equinor's released data) with a mud, string, hole, nozzles and
    window declared for the example - not what Volve ran."""
    from . import well3d as W3
    sc = W3.volve_scene()
    w = next(x for x in sc['wells'] if x['name'] == '15/9-F-10')
    st = w['stations']
    cfg = {**DEFAULTS, 'mw_ppg': 10.5, 'fann': {'600': 56.0, '300': 36.0, '200': 28.0, '100': 20.0, '6': 6.0, '3': 5.0}, 'flow_gpm': 550.0,
           'nozzles_32': [13, 13, 13, 13, 13, 13], 'bit_in': 8.5, 'surface_psi': 100.0, 'surface_ref_gpm': 550.0, 'tools_psi': 350.0,
           'string': check_string([{'what': '6-3/4 in BHA (example)', 'length_ft': 400, 'od_in': 6.75, 'id_in': 2.81},
                                   {'what': '5 in heavy-weight pipe (example)', 'length_ft': 900, 'od_in': 5.0, 'id_in': 3.0},
                                   {'what': '5 in 19.5 lb/ft drill pipe (example)', 'length_ft': None, 'od_in': 5.0, 'id_in': 4.276}]),
           'hole': check_hole([{'what': '9-5/8 in 47 lb/ft casing (example)', 'bottom_md_ft': round(2500.0 / FT, 1), 'id_in': 8.681},
                               {'what': '8-1/2 in open hole (example)', 'bottom_md_ft': None, 'id_in': 8.5}]),
           'pp_ppg': 9.0, 'fg_ppg': 14.0,
           'source': 'a mud, string, hole, nozzles, rate and window declared for the example, not from Volve; the survey is Equinor\'s'}
    r = analyse(st['md'], st['incl'], st['azi'], cfg, None, {'pp_const': 9.0, 'fg_const': 14.0}, 'declared for the example')
    r.update({'well': '15/9-F-10', 'display': 'Worked example: Volve 15/9-F-10 with an example mud and string',
              'attribution': 'Survey: ' + (w.get('source', '') or "Equinor's released trajectory").rstrip('. ') + '. Contains data from the Volve field released by Equinor ASA and the former Volve licence partners; the data are not sold here.',
              'how': 'the survey as released; the mud, string, hole, nozzles, rate and window declared for the example'})
    return r


def page(ws, scene: str = 'workspace', well: Optional[str] = None) -> dict:
    from . import well3d as W3
    out = {'scene': scene, 'scenes': [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'example', 'title': 'Worked example: Volve 15/9-F-10'}],
           'defaults': DEFAULTS, 'not_a_measurement': NOT_A_MEASUREMENT, 'wells': [], 'well': None, 'result': None, 'detail': ''}
    if scene == 'example' or ws is None:
        out['scene'] = 'example'
        out['result'] = example()
        return out
    if scene != 'workspace':
        raise KeyError(f'no scene {scene!r}: workspace or example')
    for w in ws.wells():
        c_ = program_config(ws, w['id'])
        out['wells'].append({'id': w['id'], 'display': w.get('display') or w['id'], 'survey': bool(w.get('trajectory')), 'config': c_,
                             'hole_from': c_.get('hole_from')})
    choice = well if any(w['id'] == well for w in out['wells']) else next((w['id'] for w in out['wells'] if w['survey'] and w['config'].get('string')),
                                                                          next((w['id'] for w in out['wells'] if w['survey']), out['wells'][0]['id'] if out['wells'] else None))
    out['well'] = choice
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    cfg = program_config(ws, choice)
    tj = W3.trajectory_of(ws, choice) if ws.well(choice).get('trajectory') else None
    rec_path = None if tj else recorder_path(ws, choice)
    if not tj and not rec_path:
        out['detail'] = 'this well has no directional survey attached (and no drilling record with the recorder\'s TVD)'
        return out
    missing = [w for w, ok in (('the drill string with its bores', cfg.get('string')),
                               ('the hole: the casing program (the Casing tab) and the bit' + (f" - {cfg['hole_missing']}" if cfg.get('hole_missing') else ''), cfg.get('hole')),
                               ('the viscometer readings', (cfg.get('fann') or {}).get('600'))) if not ok]
    if missing:
        out['detail'] = 'declare ' + ', '.join(missing)
        return out
    meas = measured(ws, choice)
    td = max(meas['depth_ft']) if meas and meas.get('depth_ft') else None
    win, how_w = _window(ws, choice, cfg)
    try:
        if tj:
            st = tj['stations']
            md_end = st[-1]['md'] / FT
            r = analyse([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], cfg, min(td, md_end) if td else None, win, how_w, meas)
        else:
            md_end = rec_path['md_ft'][-1]
            r = analyse(None, None, None, cfg, min(td, md_end) if td else None, win, how_w, meas, recorder=rec_path)
    except ValueError as e:
        out['detail'] = str(e)
        return out
    path_how = f"the survey ({tj['meta']['source']})" if tj else rec_path['source']
    r.update({'well': choice, 'display': next(w['display'] for w in out['wells'] if w['id'] == choice), 'path_from': 'survey' if tj else 'recorder',
              'hole_from': cfg.get('hole_from'),
              'how': f"{path_how}; the mud and string as declared ({cfg.get('source')}); the hole {cfg.get('hole_from')}"})
    out['result'] = r
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return f"Hydraulics: {p.get('detail', '')}"
    c, rh, cfg = r['circulating'], r['rheology'], r['config']
    pt = c['parts']
    L = [f"Hydraulics - {r['display']}: bit at {r['td_ft']:,.0f} ft MD ({r['tvd_td_ft']:,.0f} ft TVD), {c['q_gpm']:g} gpm, mud {cfg['mw_ppg']:g} ppg "
         f"(PV {rh['pv_cp']:g} cP, YP {rh['yp_lbf_100ft2']:g}; n pipe {rh['n_pipe']:.3f}, annulus {rh['n_ann']:.3f})",
         f"  standpipe {c['spp_psi']:,.0f} psi = surface {pt['surface']:,.0f} + string {pt['string']:,.0f} + tools {pt['tools']:,.0f} + bit {pt['bit']:,.0f} "
         f"+ annulus {pt['annulus']:,.0f}; ECD at the bit {c['ecd_bit_ppg']:.2f} ppg"]
    b = c['bit']
    if b.get('tfa_in2'):
        L.append(f"  bit: TFA {b['tfa_in2']:.3f} in2, jet {b['jet_ft_s']:g} ft/s, {b['hhp']:g} hhp" + (f", {b['hsi']:g} hp/in2" if b.get('hsi') else '')
                 + f", impact {b['impact_lbf']:,.0f} lbf, {100 * (c['bit_share'] or 0):.0f} % of the standpipe pressure")
    if r['min_fg_margin']:
        L.append(f"  window ({r['window_how']}): smallest margin to the fracture gradient {r['min_fg_margin']['ppg']:+.2f} ppg at {r['min_fg_margin']['md_ft']:,.0f} ft MD")
    t = r['trips']
    if t['depth_ft']:
        L.append(f"  tripping at {cfg['trip_ft_min']:g} ft/min (peak x{cfg['peak_factor']:g}): at TD surge {t['surge_ppg'][-1]:.2f} ppg, swab {t['swab_ppg'][-1]:.2f} ppg"
                 + (f"; fastest at TD: run {t['run_max_ft_min'][-1]:g}, pull {t['pull_max_ft_min'][-1]:g} ft/min" if t['run_max_ft_min'][-1] is not None else ''))
    if r.get('record'):
        rc = r['record']
        L.append(f"  against the record: {rc['bins']} depth bins, measured - model median {rc['median_difference_psi']:+,.0f} psi (10-90 % spread {rc['spread_psi']:,.0f} psi)")
    return '\n'.join(L)


def build_report(ws, version: str, well: Optional[str] = None, scene: str = 'workspace'):
    from . import client_reports as CR
    p = page(ws, scene, well)
    r = p['result']
    if not r:
        raise ValueError(p.get('detail') or 'nothing to model')
    now = datetime.now(timezone.utc)
    rid = f"HYD-{now.strftime('%Y%m%dT%H%M%SZ')}"
    c, rh, cfg = r['circulating'], r['rheology'], r['config']
    pt = c['parts']
    front = [['Report ID', rid], ['Well', r['display']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Standpipe pressure', f"{c['spp_psi']:,.0f} psi at {c['q_gpm']:g} gpm with the bit at {r['td_ft']:,.0f} ft"],
             ['ECD at the bit', f"{c['ecd_bit_ppg']:.2f} ppg (mud {cfg['mw_ppg']:g} ppg)"]]
    if r['min_fg_margin']:
        front.append(['Smallest margin to the fracture gradient', f"{r['min_fg_margin']['ppg']:+.2f} ppg at {r['min_fg_margin']['md_ft']:,.0f} ft MD ({r['window_how']})"])
    s1 = CR.Section('1', 'The mud, the string and the hole', [
        f"{r.get('how', '')}. Viscometer {', '.join(f'{k}: {v:g}' for k, v in rh['readings'].items())}; plastic viscosity {rh['pv_cp']:g} cP, yield point "
        f"{rh['yp_lbf_100ft2']:g} lbf/100 ft2. Power law (API RP 13D): pipe n {rh['n_pipe']:.3f} from 600 and 300 rpm, annulus n {rh['n_ann']:.3f} from {rh['annulus_from']}."], [])
    s1.tables.append(CR.Table(['String (bit up)', 'Length (ft)', 'OD (in)', 'Bore (in)'],
                              [[x['what'], 'to surface' if x['length_ft'] is None else f"{x['length_ft']:,.0f}", f"{x['od_in']:g}", f"{x['id_in']:g}"] for x in cfg['string']]))
    s1.tables.append(CR.Table(['Hole (surface down)', 'To (ft MD)', 'Inside diameter (in)'],
                              [[x['what'], 'TD' if x['bottom_md_ft'] is None else f"{x['bottom_md_ft']:,.0f}", f"{x['id_in']:g}"] for x in cfg['hole']]))
    s2 = CR.Section('2', 'Where the pressure goes', [f"At {c['q_gpm']:g} gpm with the bit at {r['td_ft']:,.0f} ft MD: surface lines {pt['surface']:,.0f} psi, "
                                                     f"string {pt['string']:,.0f}, tools {pt['tools']:,.0f}, bit {pt['bit']:,.0f}, annulus {pt['annulus']:,.0f}; "
                                                     f"standpipe {c['spp_psi']:,.0f} psi."], [])
    s2.tables.append(CR.Table(['Annulus section', 'From (ft)', 'To (ft)', 'Hole x pipe (in)', 'Velocity (ft/min)', 'Regime', 'Loss (psi)'],
                              [[f"{a['hole']} / {a['pipe']}", f"{a['top_ft']:,.0f}", f"{a['bottom_ft']:,.0f}", f"{a['hole_in']:g} x {a['od_in']:g}", f"{a['v_ft_min']:,.0f}",
                                a['regime'], f"{a['psi']:,.0f}"] for a in c['annulus']]))
    s2.tables.append(CR.Table(['Rate (gpm)', 'Standpipe (psi)', 'ECD at the bit (ppg)'],
                              [[f"{x['q_gpm']:g}", f"{x['spp_psi']:,.0f}", f"{x['ecd_bit_ppg']:.2f}"] for x in r['by_rate']]))
    e = r['ecd']
    k = max(1, len(e['md_ft']) // 40)
    idx = list(range(0, len(e['md_ft']), k)) + ([len(e['md_ft']) - 1] if (len(e['md_ft']) - 1) % k else [])
    s3 = CR.Section('3', 'ECD against the window', [f"Window: {r['window_how']}."], [])
    s3.tables.append(CR.Table(['MD (ft)', 'TVD (ft)', 'ECD (ppg)', 'Pore pressure (ppg)', 'Fracture gradient (ppg)', 'Margin to FG (ppg)'],
                              [[f"{e['md_ft'][i]:,.0f}", f"{e['tvd_ft'][i]:,.0f}", f"{e['ecd_ppg'][i]:.2f}", '-' if e['pp_ppg'][i] is None else f"{e['pp_ppg'][i]:.2f}",
                                '-' if e['fg_ppg'][i] is None else f"{e['fg_ppg'][i]:.2f}", '-' if e['margin_fg_ppg'][i] is None else f"{e['margin_fg_ppg'][i]:+.2f}"] for i in idx]))
    t = r['trips']
    s4 = CR.Section('4', 'Tripping: surge and swab', [f"The string closed at the bottom, clinging constant {cfg['clinging']:g}, the pipe's peak speed {cfg['peak_factor']:g} times "
                                                      f"the stand's average of {cfg['trip_ft_min']:g} ft/min. The fastest speeds keep the surge at or below the fracture gradient "
                                                      f"and the swab at or above the pore pressure at the bit."], [])
    s4.tables.append(CR.Table(['Bit depth (ft)', 'Surge EMW (ppg)', 'Swab EMW (ppg)', 'Fastest run (ft/min)', 'Fastest pull (ft/min)'],
                              [[f"{t['depth_ft'][i]:,.0f}", f"{t['surge_ppg'][i]:.2f}", f"{t['swab_ppg'][i]:.2f}",
                                '-' if t['run_max_ft_min'][i] is None else f"{t['run_max_ft_min'][i]:g}", '-' if t['pull_max_ft_min'][i] is None else f"{t['pull_max_ft_min'][i]:g}"]
                               for i in range(len(t['depth_ft']))]))
    secs = [s1, s2, s3, s4]
    rc = r.get('record')
    s5 = CR.Section('5', 'Against the rig', [f"Standpipe pressure measured while pumping (the median of each 250 ft of bit depth) against the model at the same rate and depth: "
                                             f"median difference {rc['median_difference_psi']:+,.0f} psi, 10-90 % spread {rc['spread_psi']:,.0f} psi over {rc['bins']} bins."
                                             if rc else 'No pumping in the drilling record to compare.'], [])
    if rc:
        s5.tables.append(CR.Table(['Bit depth (ft)', 'Rate (gpm)', 'Measured (psi)', 'Model (psi)', 'Difference (psi)'],
                                  [[f"{x['depth_ft']:,.0f}", f"{x['q_gpm']:g}", f"{x['measured_psi']:,.0f}", f"{x['model_psi']:,.0f}", f"{x['difference_psi']:+,.0f}"] for x in rc['rows']]))
    secs += [s5, CR.Section('6', 'What this will not call a measurement', ['; '.join(p['not_a_measurement']) + '.'], [])]
    return CR.Document(f"Hydraulics and ECD - {r['display']}", rid, front, secs,
                       footer='Power-law hydraulics after API RP 13D; friction blending after Zamora et al., AADE-05-NTCE-27; surge and swab after Burkhardt, JPT 1961.',
                       data={'report_id': rid, **{k_: v for k_, v in r.items() if k_ not in ('measured',)}})


def write_report(ws, version: str, well: Optional[str] = None, actor: str = 'system', scene: str = 'workspace') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well, scene)
    if scene == 'example':
        dd, base = ws.reports_dir, 'hydraulics_example'
    else:
        dd, base = os.path.join(ws.reports_dir, 'wells', doc.data['well']), 'hydraulics'
    os.makedirs(dd, exist_ok=True)
    paths = CR.write(doc, dd, base)
    ws.audit(actor, 'report.hydraulics', {'well': doc.data.get('well'), 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


# -- a labelled record ------------------------------------------------------------------------------------------------------
def selftest_files(tmp: str, offset_psi: float = 120.0, seed: int = 5) -> dict:
    """A survey (vertical to 3,000 ft, built to 60 degrees by 6,000 ft, held to 10,000 ft), a declared mud, string and
    hole, and a drilling record of drilling from 4,000 to 10,000 ft at 500 gpm (and circulating at 350 gpm every 1,000
    ft) whose standpipe pressure is this model's plus a constant `offset_psi` (what the declarations left out) and 15 psi
    of noise."""
    import csv
    from datetime import timedelta
    rng = np.random.default_rng(seed)
    os.makedirs(tmp, exist_ok=True)
    md = np.arange(0.0, 10000.01, 100.0)
    inc = np.clip((md - 3000.0) / 3000.0 * 60.0, 0.0, 60.0)
    sp = os.path.join(tmp, 'hyd_survey.csv')
    with open(sp, 'w', encoding='utf-8', newline='') as f:
        f.write('MD (ft),Inc (deg),Azi (deg)\n' + ''.join(f'{m:.1f},{i:.3f},120.0\n' for m, i in zip(md, inc)))
    cfg = {**DEFAULTS, 'mw_ppg': 11.0, 'fann': {'600': 60.0, '300': 38.0, '100': 21.0, '6': 7.0, '3': 6.0}, 'flow_gpm': 500.0, 'nozzles_32': [14, 14, 14, 14, 14],
           'bit_in': 8.75, 'tools_psi': 400.0, 'surface_psi': 0.0,
           'string': check_string([{'what': 'BHA', 'length_ft': 350, 'od_in': 6.75, 'id_in': 2.875}, {'what': 'drill pipe', 'length_ft': None, 'od_in': 5.0, 'id_in': 4.276}]),
           'hole': check_hole([{'what': '9-5/8 casing', 'bottom_md_ft': 3500.0, 'id_in': 8.681}, {'what': 'open hole', 'bottom_md_ft': None, 'id_in': 8.75}])}
    tvd_of, _ = _tvd_function(md * FT, inc, np.full(len(md), 120.0))
    rheo = rheology(cfg['fann'])
    ep = os.path.join(tmp, 'hyd_record.csv')
    t = datetime(2026, 9, 12, 0, 0, 0)
    rows = []
    cache = {}

    def spp(d, q):
        key = (round(d / 25.0) * 25.0, q)
        if key not in cache:
            cache[key] = circulate(cfg, rheo, key[0], q, tvd_of)['spp_psi']
        return cache[key]
    d = 4000.0
    while d < 10000.0:
        for _ in range(120):                                  # drilling: one foot every two seconds
            d += 0.5
            rows.append((t, d, d, 500.0, spp(d, 500.0) + offset_psi + rng.normal(0, 15.0), 120.0, 1))
            t += timedelta(seconds=1)
        if int(d) % 1000 == 0:
            for _ in range(300):
                rows.append((t, d, d - 20.0, 350.0, spp(d - 20.0, 350.0) + offset_psi + rng.normal(0, 15.0), 0.0, 0))
                t += timedelta(seconds=1)
    with open(ep, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Hook Load (klbs)', 'Block Height (feet)',
                    'Total Pump Output (gal_per_min)', 'Standpipe Pressure (psi)', 'Rotary RPM (RPM)', 'Convertible Torque (kft_lb)', 'On Bottom (unitless)',
                    'Interpolated TVD (feet)'])
        for (tt, hole, bitd, q, p, rpm, onb) in rows:
            w.writerow([tt.strftime('%Y/%m/%d'), tt.strftime('%H:%M:%S'), f'{hole:.2f}', f'{bitd:.2f}', '1800.0' if onb else '0.0', '180.0', '40.0',
                        f'{q:.1f}', f'{p:.1f}', f'{rpm:.1f}', '8.0' if rpm else '0.0', str(onb), f'{tvd_of(hole):.2f}'])
    return {'survey': sp, 'record': ep, 'config': cfg, 'offset_psi': offset_psi, 'rows': len(rows)}
