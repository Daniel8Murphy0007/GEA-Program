# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Wellbore stability: the mud-weight window along a well, with the hole's own direction in it.

The Pore pressure tab gives two sides of the window - the pore pressure a mud must hold back and the fracture
gradient it must stay under - and they are the same for a vertical hole and a horizontal one. The third side is not:
the rock at the wall of a hole carries the stress the removed rock carried, and where that is more than the rock's
strength the wall fails in shear and falls in (breakout, collapse). How much mud that takes depends on the stresses,
the rock, and which way the hole points through them. This module works that out.

- THE STRESSES at a depth are three, taken as one vertical and two horizontal (the usual case away from salt and
  steep structure): the overburden Sv, the least horizontal stress Shmin and the greatest SHmax with its azimuth.
  Sv and Shmin come from the Pore pressure tab where it reads the well (its overburden, and its fracture gradient as
  the least stress) or are declared as gradients; SHmax is declared - as a gradient, or as how far it stands from
  Shmin toward Sv - because nothing in a drilling record measures it. Undeclared, it is taken equal to Shmin and the
  page says so.
- AT THE WALL the stresses are Kirsch's for a hole in an elastic rock (Kirsch, Z. VDI 42, 1898), in the form for a
  hole at any angle to the three stresses (Hiramatsu and Oka, Int. J. Rock Mech. Min. Sci. 5, 1968; Fjaer et al.,
  Petroleum Related Rock Mechanics, 2nd ed., 2008, section 4.3): the far stresses are turned into the hole's own
  axes, and at each point round the wall the hoop stress, the stress along the hole and the shear between them give
  two principal stresses, the mud's pressure being the third.
- COLLAPSE is Mohr-Coulomb's shear failure on the effective stresses (total less pore pressure; an impermeable
  wall): the wall fails where s1' > UCS + s3' tan^2(45 + phi/2), UCS the rock's unconfined strength and phi its
  friction angle, both declared. The collapse pressure is the least mud pressure at which no point of the wall fails
  - or, where the operator accepts breakout, at which no more than the declared width of each side has failed.
- BREAKDOWN is the mud pressure at which the least effective stress round the wall falls to minus the tensile
  strength: the wall parts. In a vertical hole that is Hubbert and Willis's 3 Shmin - SHmax - Pp (Trans. AIME 210,
  1957). The mud is LOST when its pressure passes the least of the three stresses, which opens a fracture and keeps
  it open; the window's top is that least stress, and the breakdown is given beside it.
- THE WINDOW at each depth is from the greater of the pore pressure and the collapse pressure up to the least
  stress, each as an equivalent mud weight (ppg = psi / (0.052 x TVD ft)). Against it are set the mud weight (static:
  pumps off) and, where the Hydraulics tab has the mud and the string, the equivalent circulating density at that
  depth (pumps on).

What it will not call a measurement: any of it. The stresses and the strength are declared or estimated; the model
is elastic, the wall impermeable, the rock the same in every direction, and time, temperature and the mud's chemistry
are not in it. A caliper log, an image log and what came over the shakers are the measurements of whether a hole
stood."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
PSI_FT_PER_PPG = 0.052
GAP_FT = 250.0                  # the Pore pressure tab's curves are not carried across a stretch longer than this that it left blank
THETA = np.radians(np.arange(0.0, 180.0, 1.0))           # the wall, a degree at a time: the far half is the near half again
_C2, _S2, _SN, _CS = np.cos(2 * THETA), np.sin(2 * THETA), np.sin(THETA), np.cos(THETA)
DEFAULTS = {'sv_ppg': None, 'pp_ppg': None, 'shmin_ppg': None, 'shmax_ppg': None, 'shmax_k': None, 'shmax_azimuth_deg': None, 'ucs_psi': None, 'friction_deg': 30.0,
            'tensile_psi': 0.0, 'poisson': 0.25, 'breakout_deg': 0.0, 'mw_ppg': None, 'step_ft': 100.0, 'top_md_ft': None, 'source': ''}
LIMITS = {'sv_ppg': (14.0, 24.0), 'pp_ppg': (6.0, 20.0), 'shmin_ppg': (8.0, 24.0), 'shmax_ppg': (8.0, 40.0), 'shmax_k': (0.0, 2.5), 'shmax_azimuth_deg': (0.0, 360.0),
          'ucs_psi': (100.0, 60000.0), 'friction_deg': (5.0, 55.0), 'tensile_psi': (0.0, 5000.0), 'poisson': (0.05, 0.49), 'breakout_deg': (0.0, 90.0),
          'mw_ppg': (6.0, 22.0), 'step_ft': (10.0, 1000.0), 'top_md_ft': (0.0, 40000.0)}
WORDS = {'sv_ppg': 'the overburden, as a gradient in ppg', 'pp_ppg': 'the pore pressure, ppg', 'shmin_ppg': 'the least horizontal stress, ppg (a leak-off test is its measurement)',
         'shmax_ppg': 'the greatest horizontal stress, ppg', 'shmax_k': 'the greatest horizontal stress as a share of the way from the least toward the overburden (0 equal to the least, 1 equal to the overburden)',
         'shmax_azimuth_deg': 'the azimuth of the greatest horizontal stress, degrees from north', 'ucs_psi': "the rock's unconfined compressive strength, psi",
         'friction_deg': "the rock's internal friction angle, degrees", 'tensile_psi': "the rock's tensile strength, psi", 'poisson': "Poisson's ratio",
         'breakout_deg': 'the breakout accepted on each side of the hole, degrees of its wall (0: none)', 'mw_ppg': 'the mud weight, ppg - used only where the Hydraulics tab has no mud declared for the well',
         'step_ft': 'the step along the hole, ft', 'top_md_ft': 'where the open hole starts, ft MD (the deepest casing shoe where the casing program gives one)'}
SOURCES = ('Kirsch (1898); Hiramatsu and Oka (1968); Fjaer, Holt, Horsrud, Raaen and Risnes, Petroleum Related Rock Mechanics, 2nd ed. (2008), chapter 4; '
           'Zoback, Reservoir Geomechanics (2007), chapters 6 and 8; Hubbert and Willis (1957)')
NOT_A_MEASUREMENT = [
    'the stresses: the overburden is integrated from a density and the least horizontal stress is a fracture gradient from a relation or a declared figure - a leak-off or extended leak-off test is its measurement; the greatest horizontal stress is declared, and nothing in a drilling record measures it',
    "the rock's strength: one declared strength and friction angle for the whole open hole, where the rock changes bed by bed; a core test is the measurement",
    'the collapse pressure: an elastic, impermeable, isotropic wall failing at the first point to reach its strength - shale that weakens with time in a water-based mud, bedding planes, and a mud that does not seal the wall all move it, and a hole can stand below this figure or fall in above it',
    'the breakdown: the wall parting, with no fluid getting into the rock; the mud is lost at the least stress, which is the top of the window here',
    'whether a hole stood: the caliper, the image log and the cavings over the shakers say that',
]
_LOCK = threading.Lock()
_MEMO: Dict[str, dict] = {}


class StabilityError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _r(v, n: int = 2):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


# -- the wall -------------------------------------------------------------------------------------------------------------

def hole_stress(sv: float, sH: float, sh: float, az_H_deg: float, inc_deg: float, az_deg: float) -> Dict[str, float]:
    """The far stresses in the hole's own axes: z along the hole (downward), x across it in the vertical plane through
    it (toward its low side's normal), y across it and horizontal. Angles in degrees; azimuths from north, eastward."""
    aH = math.radians(az_H_deg); i = math.radians(inc_deg); a = math.radians(az_deg)
    eH = np.array([math.cos(aH), math.sin(aH), 0.0]); eh = np.array([-math.sin(aH), math.cos(aH), 0.0]); ev = np.array([0.0, 0.0, 1.0])
    S = sH * np.outer(eH, eH) + sh * np.outer(eh, eh) + sv * np.outer(ev, ev)          # north, east, down
    x = np.array([math.cos(i) * math.cos(a), math.cos(i) * math.sin(a), -math.sin(i)])
    y = np.array([-math.sin(a), math.cos(a), 0.0])
    z = np.array([math.sin(i) * math.cos(a), math.sin(i) * math.sin(a), math.cos(i)])
    f = lambda p, q: float(p @ S @ q)
    return {'xx': f(x, x), 'yy': f(y, y), 'zz': f(z, z), 'xy': f(x, y), 'xz': f(x, z), 'yz': f(y, z)}


def wall(s: Dict[str, float], pw, nu: float):
    """The two principal total stresses in the wall's own plane at each degree round it, for a mud pressure (or a
    column of them); the third is the mud's pressure itself."""
    pw = np.asarray(pw, dtype=np.float64)[..., None]
    tt = s['xx'] + s['yy'] - 2.0 * (s['xx'] - s['yy']) * _C2 - 4.0 * s['xy'] * _S2 - pw
    zz = s['zz'] - nu * (2.0 * (s['xx'] - s['yy']) * _C2 + 4.0 * s['xy'] * _S2)
    tz = 2.0 * (-s['xz'] * _SN + s['yz'] * _CS)
    m = 0.5 * (tt + zz); r = np.sqrt(0.25 * (tt - zz) ** 2 + tz ** 2)
    return m + r, m - r


def shear(s: Dict[str, float], pw, pp: float, ucs: float, q: float, nu: float) -> np.ndarray:
    """Mohr-Coulomb round the wall: positive where it has failed in shear. A point in tension is the tensile rule's
    to judge, so the confining stress is not taken below zero here."""
    t1, t2 = wall(s, pw, nu)
    pw_ = np.asarray(pw, dtype=np.float64)[..., None]
    s1 = np.maximum(t1, pw_); s3 = np.minimum(t2, pw_)
    return (s1 - pp) - ucs - q * np.maximum(s3 - pp, 0.0)


def failed_deg(s: Dict[str, float], pw: float, pp: float, ucs: float, phi_deg: float, nu: float) -> int:
    """How many degrees of each side of the wall have failed in shear at a mud pressure."""
    q = math.tan(math.radians(45.0 + phi_deg / 2.0)) ** 2
    return int((shear(s, pw, pp, ucs, q, nu) > 0).sum())


def collapse(s: Dict[str, float], pp: float, ucs: float, phi_deg: float, nu: float, width_deg: float = 0.0, hi: Optional[float] = None) -> Optional[float]:
    """The least mud pressure at which no more than `width_deg` of each side of the wall has failed in shear; None
    where no pressure up to `hi` (twice the greatest stress) does it."""
    q = math.tan(math.radians(45.0 + phi_deg / 2.0)) ** 2
    hi = float(hi) if hi is not None else 2.0 * max(s['xx'], s['yy'], s['zz'])
    grid = np.linspace(0.0, hi, 81)
    good = (shear(s, grid, pp, ucs, q, nu) > 0).sum(axis=1) <= width_deg
    if not good.any():
        return None
    k = int(np.argmax(good))
    if k == 0:
        return 0.0
    a, b = float(grid[k - 1]), float(grid[k])
    for _ in range(34):
        m = 0.5 * (a + b)
        if int((shear(s, m, pp, ucs, q, nu) > 0).sum()) <= width_deg:
            b = m
        else:
            a = m
    return b


def breakdown(s: Dict[str, float], pp: float, t0: float, nu: float, hi: Optional[float] = None) -> Optional[float]:
    """The mud pressure at which the wall first parts: the least effective stress round it down to minus the tensile
    strength. None where it has not parted by `hi` (three times the greatest stress)."""
    hi = float(hi) if hi is not None else 3.0 * max(s['xx'], s['yy'], s['zz'])
    g = lambda pw: float((wall(s, pw, nu)[1] - pp).min() + t0)
    if g(0.0) <= 0.0:
        return 0.0
    if g(hi) > 0.0:
        return None
    a, b = 0.0, hi
    for _ in range(46):
        m = 0.5 * (a + b)
        if g(m) > 0.0:
            a = m
        else:
            b = m
    return a


def at_depth(tvd_ft: float, inc_deg: float, az_deg: Optional[float], sv_ppg: float, pp_ppg: float, shmin_ppg: float, shmax_ppg: float, az_H_deg: Optional[float],
             ucs_psi: float, phi_deg: float, t0_psi: float, nu: float, width_deg: float = 0.0) -> dict:
    """The window at one point of a hole, every figure an equivalent mud weight in ppg."""
    k = PSI_FT_PER_PPG * float(tvd_ft)
    sv, pp, sh, sH = (float(x) * k for x in (sv_ppg, pp_ppg, shmin_ppg, shmax_ppg))
    s = hole_stress(sv, sH, sh, float(az_H_deg or 0.0), float(inc_deg), float(az_deg or 0.0))
    c0 = collapse(s, pp, ucs_psi, phi_deg, nu, 0.0)
    cw = collapse(s, pp, ucs_psi, phi_deg, nu, width_deg) if width_deg > 0 else c0
    bd = breakdown(s, pp, t0_psi, nu)
    least = min(sv, sh, sH)
    lower = max(pp, cw) if cw is not None else None
    e = lambda p: None if p is None else p / k
    return {'collapse0_ppg': e(c0), 'collapse_ppg': e(cw), 'breakdown_ppg': e(bd), 'least_ppg': e(least), 'lower_ppg': e(lower), 'upper_ppg': e(least),
            'width_ppg': (e(least) - e(lower)) if lower is not None else None, 'set_by': None if lower is None else ('collapse' if cw > pp else 'pore pressure')}


# -- a hole ---------------------------------------------------------------------------------------------------------------

def _need(cfg: dict) -> List[str]:
    out = []
    if cfg.get('ucs_psi') is None:
        out.append("the rock's unconfined compressive strength")
    return out


def shmax_of(cfg: dict, sv: float, sh: float) -> Tuple[float, str]:
    """The greatest horizontal stress at a depth (ppg), and how it was come by."""
    if cfg.get('shmax_ppg') is not None:
        return float(cfg['shmax_ppg']), 'declared as a gradient'
    if cfg.get('shmax_k') is not None:
        return sh + float(cfg['shmax_k']) * (sv - sh), f"declared as {float(cfg['shmax_k']):g} of the way from the least stress to the overburden"
    return sh, 'not declared: taken equal to the least horizontal stress'


def analyse(md_ft: np.ndarray, tvd_ft: np.ndarray, inc_deg: np.ndarray, azi_deg: Optional[np.ndarray], cfg: dict, stress, mw_ppg: Optional[float] = None,
            ecd: Optional[dict] = None) -> dict:
    """The window at every station given. `stress(tvd_ft)` returns (sv, pp, shmin) in ppg, any of them None where it
    is not known at that depth. `ecd` is {md_ft, ecd_ppg} from the Hydraulics tab."""
    need = _need(cfg)
    if need:
        raise StabilityError('declare ' + ', '.join(need))
    aniso = cfg.get('shmax_ppg') is not None or (cfg.get('shmax_k') or 0.0) != 0.0
    if aniso and cfg.get('shmax_azimuth_deg') is None:
        raise StabilityError('the two horizontal stresses are declared unequal: declare the azimuth of the greatest')
    if aniso and azi_deg is None:
        raise StabilityError("the two horizontal stresses are declared unequal and the hole's azimuth is not known: attach its directional survey on the 3D tab")
    cols = ('md_ft', 'tvd_ft', 'inc_deg', 'azi_deg', 'sv_ppg', 'pp_ppg', 'shmin_ppg', 'shmax_ppg', 'collapse0_ppg', 'collapse_ppg', 'breakdown_ppg', 'lower_ppg', 'upper_ppg',
            'width_ppg', 'set_by', 'ecd_ppg')
    P: Dict[str, list] = {c: [] for c in cols}
    how_H = ''; skipped = 0
    for i in range(len(md_ft)):
        z = float(tvd_ft[i])
        sv, pp, sh = stress(z) if z > 100.0 else (None, None, None)
        if None in (sv, pp, sh):
            skipped += 1
            continue
        sH, how_H = shmax_of(cfg, sv, sh)
        a = at_depth(z, float(inc_deg[i]), None if azi_deg is None else float(azi_deg[i]), sv, pp, sh, sH, cfg.get('shmax_azimuth_deg'), float(cfg['ucs_psi']),
                     float(cfg['friction_deg']), float(cfg['tensile_psi']), float(cfg['poisson']), float(cfg['breakout_deg']))
        e = float(np.interp(float(md_ft[i]), ecd['md_ft'], ecd['ecd_ppg'])) if ecd and ecd['md_ft'][0] <= md_ft[i] <= ecd['md_ft'][-1] else None
        row = {'md_ft': _r(md_ft[i], 1), 'tvd_ft': _r(z, 1), 'inc_deg': _r(inc_deg[i], 2), 'azi_deg': None if azi_deg is None else _r(azi_deg[i], 1), 'sv_ppg': _r(sv, 3),
               'pp_ppg': _r(pp, 3), 'shmin_ppg': _r(sh, 3), 'shmax_ppg': _r(sH, 3), **{k_: (_r(a[k_], 3) if k_ != 'set_by' else a[k_]) for k_ in a if k_ != 'least_ppg'}, 'ecd_ppg': _r(e, 3)}
        for c in cols:
            P[c].append(row[c])
    n = len(P['md_ft'])
    if not n:
        raise StabilityError('the overburden, the pore pressure and the least horizontal stress are not known at any depth of this hole: declare them, or let the Pore pressure tab read the well')
    ix = [i for i in range(n) if P['width_ppg'][i] is not None]
    none_ = [i for i in range(n) if P['width_ppg'][i] is None]
    closed = [i for i in ix if P['width_ppg'][i] <= 0.0]
    nar = min(ix, key=lambda i: P['width_ppg'][i]) if ix else None
    at = lambda i: {'md_ft': P['md_ft'][i], 'tvd_ft': P['tvd_ft'][i], 'inc_deg': P['inc_deg'][i], 'lower_ppg': P['lower_ppg'][i], 'upper_ppg': P['upper_ppg'][i],
                    'width_ppg': P['width_ppg'][i], 'set_by': P['set_by'][i]}
    out = {'profile': P, 'stations': n, 'skipped': skipped, 'shmax_how': how_H, 'anisotropic': bool(aniso),
           'narrowest': at(nar) if nar is not None else None, 'closed': {'stations': len(closed) + len(none_), 'from_md_ft': P['md_ft'][min(closed + none_)] if closed or none_ else None},
           'collapse_sets_pct': _r(100.0 * sum(1 for i in ix if P['set_by'][i] == 'collapse') / len(ix), 0) if ix else None,
           'highest_lower': (lambda i: {'ppg': P['lower_ppg'][i], 'md_ft': P['md_ft'][i], 'set_by': P['set_by'][i]})(max(ix, key=lambda i: P['lower_ppg'][i])) if ix else None,
           'lowest_upper': (lambda i: {'ppg': P['upper_ppg'][i], 'md_ft': P['md_ft'][i]})(min(range(n), key=lambda i: P['upper_ppg'][i])),
           'mud': None, 'config': cfg}
    if mw_ppg is not None and ix:
        st = [(mw_ppg - P['lower_ppg'][i], i) for i in ix]
        m_lo, i_lo = min(st)
        below = [i for d, i in st if d < 0]
        mud = {'mw_ppg': _r(mw_ppg, 2), 'static': {'margin_ppg': _r(m_lo, 3), **at(i_lo)}, 'below_at': len(below), 'below_from_md_ft': P['md_ft'][min(below)] if below else None,
               'static_over_upper_at': sum(1 for i in range(n) if mw_ppg > P['upper_ppg'][i]), 'circulating': None, 'ecd_over_at': 0}
        ev = [(P['upper_ppg'][i] - P['ecd_ppg'][i], i) for i in range(n) if P['ecd_ppg'][i] is not None]
        if ev:
            m_hi, i_hi = min(ev)
            over = [i for d, i in ev if d < 0]
            mud['circulating'] = {'margin_ppg': _r(m_hi, 3), 'ecd_ppg': P['ecd_ppg'][i_hi], **at(i_hi)}
            mud['ecd_over_at'] = len(over); mud['ecd_over_from_md_ft'] = P['md_ft'][min(over)] if over else None
        out['mud'] = mud
    out['status'] = ('NO WINDOW' if (closed or none_) else 'MUD BELOW THE WINDOW' if out['mud'] and out['mud']['below_at'] else
                     'ABOVE THE WINDOW' if out['mud'] and (out['mud']['ecd_over_at'] or out['mud']['static_over_upper_at']) else 'INSIDE' if out['mud'] else 'NO MUD WEIGHT')
    return out


def polar(tvd_ft: float, sv_ppg: float, pp_ppg: float, shmin_ppg: float, shmax_ppg: float, az_H_deg: Optional[float], cfg: dict,
          incs=(0, 15, 30, 45, 60, 75, 90), az_step: int = 15) -> dict:
    """The collapse mud weight at one depth for a hole pointing every way: what the hole's direction is worth."""
    azs = list(range(0, 360, az_step))
    rows = []
    for inc in incs:
        row = []
        for az in (azs if inc else azs[:1]):
            a = at_depth(tvd_ft, inc, az, sv_ppg, pp_ppg, shmin_ppg, shmax_ppg, az_H_deg, float(cfg['ucs_psi']), float(cfg['friction_deg']), float(cfg['tensile_psi']),
                         float(cfg['poisson']), float(cfg['breakout_deg']))
            row.append(_r(a['collapse_ppg'], 2))
        rows.append(row * len(azs) if not inc else row)
    flat = [v for r in rows for v in r if v is not None]
    best = min(((rows[i][j], incs[i], azs[j]) for i in range(len(incs)) for j in range(len(azs)) if rows[i][j] is not None), default=None)
    worst = max(((rows[i][j], incs[i], azs[j]) for i in range(len(incs)) for j in range(len(azs)) if rows[i][j] is not None), default=None)
    return {'tvd_ft': _r(tvd_ft, 1), 'inc_deg': list(incs), 'azi_deg': azs, 'collapse_ppg': rows, 'min_ppg': min(flat) if flat else None, 'max_ppg': max(flat) if flat else None,
            'easiest': None if best is None else {'ppg': best[0], 'inc_deg': best[1], 'azi_deg': best[2]}, 'hardest': None if worst is None else {'ppg': worst[0], 'inc_deg': worst[1], 'azi_deg': worst[2]},
            'stresses_ppg': {'sv': _r(sv_ppg, 2), 'pp': _r(pp_ppg, 2), 'shmin': _r(shmin_ppg, 2), 'shmax': _r(shmax_ppg, 2), 'shmax_azimuth_deg': az_H_deg}}


# -- the workspace ----------------------------------------------------------------------------------------------------------

def _store_path(ws) -> str:
    return os.path.join(ws.path, 'geomech', 'stability.json')


def _load(ws) -> dict:
    try:
        with open(_store_path(ws), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {'wells': {}}
    except (OSError, ValueError):
        return {'wells': {}}


def well_config(ws, well_id: str) -> dict:
    return {**DEFAULTS, **((_load(ws).get('wells') or {}).get(well_id) or {})}


def set_well(ws, actor: str, well_id: str, source: str = '', **cfg) -> dict:
    """Declare a well's stresses, its rock's strength and the breakout accepted, with where they came from. An empty
    value takes a declaration off."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    with _LOCK:
        d = _load(ws)
        cur = dict((d.get('wells') or {}).get(well_id) or {})
        changed = False
        for k, v in cfg.items():
            if k not in LIMITS:
                continue
            if v in (None, ''):
                if k in cur:
                    cur.pop(k); changed = True
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise StabilityError(f'{WORDS[k]}: a number')
            lo, hi = LIMITS[k]
            if not (math.isfinite(x) and lo <= x <= hi):
                raise StabilityError(f'{WORDS[k]}: between {lo:g} and {hi:g}')
            if cur.get(k) != x:
                cur[k] = x; changed = True
        if cur.get('shmax_ppg') is not None and cur.get('shmax_k') is not None:
            raise StabilityError('the greatest horizontal stress is declared once: as a gradient, or as its place between the least stress and the overburden')
        if changed:
            if not (source or '').strip():
                raise StabilityError('say where these values came from (e.g. "offset well leak-off tests and core, 2024 drilling program")')
            cur['source'] = source.strip()
        cur['utc'] = _utc(); cur['by'] = actor
        d.setdefault('wells', {})[well_id] = cur
        os.makedirs(os.path.dirname(_store_path(ws)), exist_ok=True)
        with open(_store_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
        os.replace(_store_path(ws) + '.tmp', _store_path(ws))
    ws.audit(actor, 'stability.settings', {'well': well_id, **{k: v for k, v in cur.items() if k not in ('utc', 'by')}})
    return {**DEFAULTS, **cur}


def _unwrap_interp(x: np.ndarray, xs: np.ndarray, deg: np.ndarray) -> np.ndarray:
    return np.mod(np.interp(x, xs, np.degrees(np.unwrap(np.radians(deg)))), 360.0)


def _path(ws, well_id: str) -> Optional[dict]:
    """The hole's direction by depth: its survey, else the drilling recorder's own depth table (no azimuth)."""
    from . import hydraulics as H
    from . import well3d as W3
    try:
        tj = W3.trajectory_of(ws, well_id) if ws.well(well_id).get('trajectory') else None
    except Exception:
        tj = None
    if tj and len(tj['stations']) >= 2:
        st = tj['stations']
        md = np.array([x['md'] for x in st], dtype=np.float64) / FT
        f, md_end = H._tvd_function([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st])
        return {'from': 'survey', 'source': f"the survey ({(tj.get('meta') or {}).get('source') or 'as attached'})", 'md_end': md_end, 'tvd': f,
                'inc': lambda m: np.interp(m, md, np.array([x['incl'] for x in st], dtype=np.float64)),
                'azi': lambda m: _unwrap_interp(m, md, np.array([x['azi'] for x in st], dtype=np.float64))}
    rp = H.recorder_path(ws, well_id)
    if rp:
        gm, gt, gi = np.asarray(rp['md_ft']), np.asarray(rp['tvd_ft']), np.asarray(rp['inc_deg'])
        return {'from': 'recorder', 'source': rp['source'], 'md_end': float(gm[-1]), 'tvd': lambda m: float(np.interp(float(m), gm, gt)), 'inc': lambda m: np.interp(m, gm, gi), 'azi': None}
    return None


def _stress_source(ws, well_id: str, cfg: dict):
    """(sv, pp, shmin) in ppg by TVD ft: each from its declaration where there is one, else the Pore pressure tab."""
    curves = None; how_pp = None
    try:
        from . import pore_pressure as PPM
        r = (PPM.page(ws, well=well_id) or {}).get('result')
        if r and r.get('well') == well_id:
            c = r['curves']
            keep = [i for i in range(len(c['tvd_m'])) if None not in (c['pp_ppg'][i], c['fg_ppg'][i], c['ob_ppg'][i])]
            if len(keep) >= 2:
                curves = {'z': np.array([c['tvd_m'][i] / FT for i in keep]), 'pp': np.array([c['pp_ppg'][i] for i in keep]), 'fg': np.array([c['fg_ppg'][i] for i in keep]),
                          'ob': np.array([c['ob_ppg'][i] for i in keep])}
                how_pp = 'the Pore pressure tab'
    except Exception:                                        # a well the Pore pressure tab cannot read is a well whose stresses are declared
        curves = None
    how = {}
    for key, name, cv in (('sv_ppg', 'the overburden', 'ob'), ('pp_ppg', 'the pore pressure', 'pp'), ('shmin_ppg', 'the least horizontal stress', 'fg')):
        how[key] = 'declared' if cfg.get(key) is not None else (f"{how_pp}{' (its fracture gradient, by Eaton)' if cv == 'fg' else ''}" if curves is not None else None)

    def stress(z: float):
        out = []
        for key, cv in (('sv_ppg', 'ob'), ('pp_ppg', 'pp'), ('shmin_ppg', 'fg')):
            if cfg.get(key) is not None:
                out.append(float(cfg[key]))
            elif curves is not None and curves['z'][0] <= z <= curves['z'][-1]:
                j = int(np.searchsorted(curves['z'], z))             # between two depths the Pore pressure tab read - and not across a stretch it left blank
                near = j == 0 or curves['z'][j] - curves['z'][j - 1] <= GAP_FT
                out.append(float(np.interp(z, curves['z'], curves[cv])) if near else None)
            else:
                out.append(None)
        return tuple(out)
    missing = [n for (k, n, _c) in (('sv_ppg', 'the overburden', 0), ('pp_ppg', 'the pore pressure', 0), ('shmin_ppg', 'the least horizontal stress', 0)) if how[k] is None]
    return stress, how, missing


def _hydraulics(ws, well_id: str) -> Tuple[Optional[float], Optional[dict], Optional[float], str]:
    """The mud weight, the ECD by depth and the deepest casing shoe, where the Hydraulics tab and the casing program have them."""
    from . import hydraulics as H
    mw = None; ecd = None; shoe = None; how = 'the Hydraulics tab has no mud and string declared for this well: no circulating density'
    try:
        cfg = H.program_config(ws, well_id)
        if (H.well_config(ws, well_id) or {}).get('source'):
            mw = float(cfg['mw_ppg'])
        shoes = [float(h['bottom_md_ft']) for h in (cfg.get('hole') or []) if h.get('bottom_md_ft') is not None]
        shoe = max(shoes) if shoes else None
        r = (H.page(ws, 'workspace', well_id) or {}).get('result')
        if r and r.get('well') == well_id and r.get('ecd'):
            ecd = {'md_ft': list(r['ecd']['md_ft']), 'ecd_ppg': list(r['ecd']['ecd_ppg']), 'q_gpm': r['circulating']['q_gpm']}
            mw = float(r['config']['mw_ppg'])
            how = f"the Hydraulics tab: {mw:g} ppg mud at {r['circulating']['q_gpm']:g} gal/min"
    except Exception:
        pass
    return mw, ecd, shoe, how


def _memo(key_obj, make):
    key = hashlib.sha256(json.dumps(key_obj, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    r = make()
    with _LOCK:
        if len(_MEMO) > 24:
            _MEMO.clear()
        _MEMO[key] = r
    return r


def well(ws, well_id: str) -> dict:
    """One well's window: {status, ...}. Statuses that are not a result say what is missing."""
    w = next((x for x in ws.wells() if x['id'] == well_id), None)
    if w is None:
        raise KeyError(f'no well {well_id}')
    cfg = well_config(ws, well_id)
    base = {'well': well_id, 'display': w.get('display') or well_id, 'config': cfg}
    path = _path(ws, well_id)
    if path is None:
        return {**base, 'status': 'NO PATH', 'why': "this well has no directional survey attached (the 3D tab) and no drilling record with the recorder's own TVD"}
    stress, how, missing = _stress_source(ws, well_id, cfg)
    need = _need(cfg) + missing
    if need:
        return {**base, 'status': 'NOT DECLARED', 'why': 'declare ' + ', '.join(need) + (' - or let the Pore pressure tab read the well, for the stresses' if missing else ''), 'path_from': path['from']}
    mw_h, ecd, shoe, how_mud = _hydraulics(ws, well_id)
    mw = mw_h if mw_h is not None else (float(cfg['mw_ppg']) if cfg.get('mw_ppg') is not None else None)      # the Hydraulics tab's mud is the one its circulating density was worked out with
    top = float(cfg['top_md_ft']) if cfg.get('top_md_ft') is not None else (shoe or 0.0)
    step = float(cfg['step_ft'])
    md = np.unique(np.concatenate([np.arange(max(top, step), path['md_end'], step), [path['md_end']]]))
    md = md[md >= top]
    tvd = np.array([path['tvd'](m) for m in md]); inc = np.asarray(path['inc'](md), dtype=np.float64); azi = None if path['azi'] is None else np.asarray(path['azi'](md), dtype=np.float64)
    key = [cfg, md.round(1).tolist(), tvd.round(1).tolist(), inc.round(2).tolist(), None if azi is None else azi.round(1).tolist(), [stress(float(z)) for z in tvd], mw, ecd]
    try:
        r = _memo(key, lambda: analyse(md, tvd, inc, azi, cfg, stress, mw, ecd))
    except StabilityError as e:
        return {**base, 'status': 'NOT DECLARED', 'why': str(e), 'path_from': path['from']}
    r = dict(r)
    nar = r['narrowest']
    pl = None
    if nar is not None:
        sv, pp, sh = stress(nar['tvd_ft'])
        sH, _h = shmax_of(cfg, sv, sh)
        pl = _memo(['polar', cfg, nar['tvd_ft'], sv, pp, sh, sH], lambda: polar(nar['tvd_ft'], sv, pp, sh, sH, cfg.get('shmax_azimuth_deg'), cfg))
        pl = {**pl, 'hole': {'inc_deg': nar['inc_deg'], 'azi_deg': None if azi is None else _r(float(path['azi'](np.array([nar['md_ft']]))[0]), 1)}}
    r.update({**base, 'path_from': path['from'], 'top_md_ft': _r(top, 1), 'top_from': 'declared' if cfg.get('top_md_ft') is not None else ('the deepest casing shoe' if shoe else 'the surface: no casing is declared'),
              'td_md_ft': _r(float(md[-1]), 1), 'polar': pl, 'stress_how': how, 'mud_how': (how_mud if mw_h is not None else 'declared here') if mw is not None else 'no mud weight is declared, here or on the Hydraulics tab',
              'ecd_how': how_mud if ecd else 'the Hydraulics tab has no mud and string declared for this well: no circulating density',
              'how': f"{path['source']}; the stresses - overburden {how['sv_ppg']}, pore pressure {how['pp_ppg']}, least horizontal stress {how['shmin_ppg']}, greatest {r['shmax_how']}; "
                     f"the strength as declared ({cfg.get('source') or 'no source given'})"})
    r['lines'] = _lines(r)
    return r


def _lines(r: dict) -> List[str]:
    out = []; nar = r.get('narrowest'); cfg = r['config']
    out.append(f"The open hole from {r['top_md_ft']:,.0f} to {r['td_md_ft']:,.0f} ft MD ({r['top_from']}), {r['stations']} stations: the window is from the greater of the pore pressure and the collapse "
               f"pressure up to the least stress, as mud weights.")
    if r.get('skipped'):
        out.append(f"{r['skipped']} more station(s) of the open hole are left out: the overburden, the pore pressure or the least horizontal stress is not known at their depth "
                   f"(the Pore pressure tab does not read there, and none is declared).")
    if r['closed']['stations']:
        out.append(f"At {r['closed']['stations']} station(s), from {r['closed']['from_md_ft']:,.0f} ft, there is no window: by these stresses and this strength the mud that holds the wall up is more than the mud the rock "
                   f"takes without loss. Either the declarations are off, or this hole is drilled with some breakout accepted.")
    if nar:
        out.append(f"It is narrowest at {nar['md_ft']:,.0f} ft MD ({nar['tvd_ft']:,.0f} ft TVD, {nar['inc_deg']:g} deg): {nar['lower_ppg']:.2f} to {nar['upper_ppg']:.2f} ppg, {nar['width_ppg']:.2f} ppg wide, "
                   f"its floor set by the {nar['set_by']}.")
    if r.get('collapse_sets_pct') is not None:
        out.append(f"The collapse pressure, not the pore pressure, sets the floor at {r['collapse_sets_pct']:g}% of the stations"
                   + (f"; the highest floor is {r['highest_lower']['ppg']:.2f} ppg at {r['highest_lower']['md_ft']:,.0f} ft." if r.get('highest_lower') else '.'))
    m = r.get('mud')
    if m:
        s_ = m['static']
        out.append(f"The mud, {m['mw_ppg']:g} ppg ({r['mud_how']}): with the pumps off it is {abs(s_['margin_ppg']):.2f} ppg {'above' if s_['margin_ppg'] >= 0 else 'BELOW'} the floor at its nearest, "
                   f"{s_['md_ft']:,.0f} ft" + (f"; below the floor at {m['below_at']} station(s), from {m['below_from_md_ft']:,.0f} ft." if m['below_at'] else '.'))
        c_ = m.get('circulating')
        if c_:
            out.append(f"Circulating, the ECD is {abs(c_['margin_ppg']):.2f} ppg {'under' if c_['margin_ppg'] >= 0 else 'OVER'} the least stress at its nearest, {c_['md_ft']:,.0f} ft ({c_['ecd_ppg']:.2f} against "
                       f"{c_['upper_ppg']:.2f} ppg)" + (f"; over it at {m['ecd_over_at']} station(s), from {m['ecd_over_from_md_ft']:,.0f} ft." if m['ecd_over_at'] else '.'))
        else:
            out.append(f"No circulating density: {r['ecd_how']}.")
    else:
        out.append(f"No mud weight to set against the window: {r['mud_how']}.")
    pl = r.get('polar')
    if pl and pl.get('easiest') and r.get('anisotropic'):
        out.append(f"At the narrowest depth a hole pointing {pl['easiest']['azi_deg']:g} deg at {pl['easiest']['inc_deg']:g} deg of inclination needs the least mud against collapse ({pl['easiest']['ppg']:.2f} ppg), and one "
                   f"pointing {pl['hardest']['azi_deg']:g} deg at {pl['hardest']['inc_deg']:g} deg the most ({pl['hardest']['ppg']:.2f} ppg).")
    elif pl and pl.get('easiest'):
        out.append(f"With the two horizontal stresses equal the azimuth does not matter; by inclination the collapse mud weight at the narrowest depth runs from {pl['min_ppg']:.2f} to {pl['max_ppg']:.2f} ppg.")
    out.append(f"The greatest horizontal stress: {r['shmax_how']}. Breakout accepted: {cfg['breakout_deg']:g} deg a side.")
    return out


def example() -> dict:
    """The worked example: Volve 15/9-F-10's survey (Equinor's released data) with stresses, a strength and a mud
    declared for the example - not Volve's."""
    from . import well3d as W3
    sc = W3.volve_scene()
    w = next(x for x in sc['wells'] if x['name'] == '15/9-F-10')
    st = w['stations']
    cfg = {**DEFAULTS, 'sv_ppg': 18.5, 'pp_ppg': 9.0, 'shmin_ppg': 14.5, 'shmax_k': 0.5, 'shmax_azimuth_deg': 60.0, 'ucs_psi': 4500.0, 'friction_deg': 30.0, 'mw_ppg': 12.3,
           'breakout_deg': 0.0, 'top_md_ft': 8200.0, 'source': "stresses, strength and mud declared for the example, not from Volve; the survey is Equinor's"}
    from . import hydraulics as H
    f, md_end = H._tvd_function(st['md'], st['incl'], st['azi'])
    mdf = np.asarray(st['md'], dtype=np.float64) / FT
    md = np.unique(np.concatenate([np.arange(cfg['top_md_ft'], md_end, 100.0), [md_end]]))
    tvd = np.array([f(m) for m in md]); inc = np.interp(md, mdf, np.asarray(st['incl'], dtype=np.float64)); azi = _unwrap_interp(md, mdf, np.asarray(st['azi'], dtype=np.float64))
    stress = lambda z: (cfg['sv_ppg'], cfg['pp_ppg'], cfg['shmin_ppg'])
    r = dict(analyse(md, tvd, inc, azi, cfg, stress, cfg['mw_ppg'], None))
    nar = r['narrowest']
    sH, _h = shmax_of(cfg, cfg['sv_ppg'], cfg['shmin_ppg'])
    pl = polar(nar['tvd_ft'], cfg['sv_ppg'], cfg['pp_ppg'], cfg['shmin_ppg'], sH, cfg['shmax_azimuth_deg'], cfg)
    pl['hole'] = {'inc_deg': nar['inc_deg'], 'azi_deg': _r(float(_unwrap_interp(np.array([nar['md_ft']]), mdf, np.asarray(st['azi'], dtype=np.float64))[0]), 1)}
    r.update({'well': '15/9-F-10', 'display': 'Worked example: Volve 15/9-F-10 with example stresses and strength', 'path_from': 'survey', 'top_md_ft': cfg['top_md_ft'], 'top_from': 'declared',
              'td_md_ft': _r(float(md[-1]), 1), 'polar': pl, 'stress_how': {'sv_ppg': 'declared', 'pp_ppg': 'declared', 'shmin_ppg': 'declared'}, 'mud_how': 'declared here',
              'ecd_how': 'the example declares no string: no circulating density',
              'attribution': 'Survey: ' + (w.get('source', '') or "Equinor's released trajectory").rstrip('. ') + '. Contains data from the Volve field released by Equinor ASA and the former Volve licence partners; the data are not sold here.',
              'how': 'the survey as released; the stresses, the strength and the mud declared for the example'})
    r['lines'] = _lines(r)
    return r


def page(ws, scene: str = 'workspace', well_id: Optional[str] = None) -> dict:
    out = {'scene': scene, 'scenes': [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'example', 'title': 'Worked example: Volve 15/9-F-10'}], 'defaults': DEFAULTS,
           'limits': {k: list(v) for k, v in LIMITS.items()}, 'words': WORDS, 'not_a_measurement': NOT_A_MEASUREMENT, 'sources': SOURCES, 'wells': [], 'well': None, 'result': None, 'detail': '',
           'generated_utc': _utc()}
    if scene == 'example' or ws is None:
        out['scene'] = 'example'; out['result'] = example()
        return out
    if scene != 'workspace':
        raise KeyError(f'no scene {scene!r}: workspace or example')
    for w in ws.wells():
        c_ = well_config(ws, w['id'])
        out['wells'].append({'id': w['id'], 'display': w.get('display') or w['id'], 'survey': bool(w.get('trajectory')), 'declared': bool(c_.get('source')), 'config': c_})
    if well_id is not None and not any(w['id'] == well_id for w in out['wells']):
        raise KeyError(f'no well {well_id}')
    choice = well_id or next((w['id'] for w in out['wells'] if w['declared']), next((w['id'] for w in out['wells'] if w['survey']), out['wells'][0]['id'] if out['wells'] else None))
    out['well'] = choice
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    r = well(ws, choice)
    if 'profile' in r:
        out['result'] = r
    else:
        out['detail'] = r.get('why') or ''; out['status'] = r['status']
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return f"Wellbore stability: {p.get('detail') or 'nothing to show'}"
    P = r['profile']
    out = [f"Wellbore stability - {r['display']} - {r['status']}"] + ['  ' + l for l in r['lines']]
    out.append(f"  {'MD ft':>8s} {'TVD ft':>8s} {'inc':>5s} {'pore':>6s} {'collapse':>8s} {'floor':>6s} {'least':>6s} {'width':>6s} {'ECD':>6s}  floor set by")
    f = lambda v, w, n=2: (f'{v:>{w}.{n}f}' if v is not None else f"{'-':>{w}s}")
    k = max(1, len(P['md_ft']) // 24)
    for i in list(range(0, len(P['md_ft']), k)) + ([len(P['md_ft']) - 1] if (len(P['md_ft']) - 1) % k else []):
        out.append(f"  {P['md_ft'][i]:>8,.0f} {P['tvd_ft'][i]:>8,.0f} {P['inc_deg'][i]:>5.1f} {f(P['pp_ppg'][i], 6)} {f(P['collapse_ppg'][i], 8)} {f(P['lower_ppg'][i], 6)} {f(P['upper_ppg'][i], 6)} "
                   f"{f(P['width_ppg'][i], 6)} {f(P['ecd_ppg'][i], 6)}  {P['set_by'][i] or 'no mud holds it'}")
    return '\n'.join(out)


def write_report(ws, program_version: str, well_id: Optional[str] = None, actor: str = 'system', scene: str = 'workspace') -> dict:
    import csv
    p = page(ws, scene, well_id)
    r = p.get('result')
    if not r:
        raise StabilityError(p.get('detail') or 'nothing to write')
    d = ws.dir('reports', 'stability', r['well'].replace('/', '-'))
    with open(os.path.join(d, 'stability.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, 'generated_utc': p['generated_utc'], 'result': json.loads(json.dumps(r, default=str)), 'not_a_measurement': NOT_A_MEASUREMENT, 'sources': SOURCES}, f, indent=1)
    with open(os.path.join(d, 'stability.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Wellbore stability - {r['display']}\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in r['lines'])
                + f"\n\nHow it was come by: {r['how']}.\n\n```\n" + text(p) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + f'\n\nSources: {SOURCES}.\n')
    P = r['profile']; cols = list(P)
    with open(os.path.join(d, 'window.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for i in range(len(P['md_ft'])):
            wr.writerow(['' if P[c][i] is None else P[c][i] for c in cols])
    files = ['stability.json', 'stability.md', 'window.csv']
    ws.audit(actor, 'stability.report', {'well': r['well'], 'files': files, 'status': r['status']})
    return {'folder': d, 'files': files, 'page': p}


# -- the rig now: the record still being written, against the window ------------------------------------------------------------

EVENTS = {'ecd': 'stability.ecd', 'mud': 'stability.mud'}
LIVE_WINDOW_S = 300.0           # the pump rate now is its middle value over the record's last five minutes
PUMPS_ON_GPM = 50.0
BUCKET_FT = 500.0               # one alert for each 500 ft of hole a condition is first met in
FLOW_NAMES = ('total pump output', 'flow in', 'pump output', 'flow in rate')


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


def now_from_arrays(r: dict, t: np.ndarray, flow: Optional[np.ndarray], bit: Optional[np.ndarray], hole: Optional[np.ndarray], ecd_at=None) -> dict:
    """Where the rig is against a well's window, from the end of its record: the bit and the hole depth, the pump rate
    over the last five minutes, the mud weight against the floor down to the hole depth, and - `ecd_at(bit_ft, q_gpm)`
    giving {md_ft, ecd_ppg} - the circulating density at that rate against the least stress down to the bit."""
    P = r['profile']; mud = r.get('mud')
    fin = lambda a: None if a is None else next((float(v) for v in np.asarray(a[-600:], dtype=np.float64)[::-1] if math.isfinite(float(v))), None)
    bit_ft = fin(bit); hole_ft = fin(hole)
    q = None
    if flow is not None and len(t):
        seg = np.asarray(flow[np.searchsorted(t, float(t[-1]) - LIVE_WINDOW_S):], dtype=np.float64); seg = seg[np.isfinite(seg)]
        q = float(np.median(seg)) if len(seg) else None
    out = {'bit_ft': _r(bit_ft, 0), 'hole_ft': _r(hole_ft, 0), 'flow_gpm': _r(q, 0), 'pumps': None if q is None else ('on' if q >= PUMPS_ON_GPM else 'off'), 'mud_below': None, 'ecd': None}
    deep = hole_ft if hole_ft is not None else bit_ft
    if mud and deep is not None:
        low = [i for i in range(len(P['md_ft'])) if P['md_ft'][i] <= deep + 1.0 and P['lower_ppg'][i] is not None and mud['mw_ppg'] < P['lower_ppg'][i]]
        if low:
            i = max(low, key=lambda j: P['lower_ppg'][j])
            out['mud_below'] = {'stations': len(low), 'from_md_ft': P['md_ft'][min(low)], 'worst_md_ft': P['md_ft'][i], 'floor_ppg': P['lower_ppg'][i], 'mw_ppg': mud['mw_ppg'],
                                'by_ppg': _r(P['lower_ppg'][i] - mud['mw_ppg'], 2), 'set_by': P['set_by'][i]}
    if ecd_at is not None and q is not None and q >= PUMPS_ON_GPM and bit_ft is not None and bit_ft > P['md_ft'][0]:
        e = ecd_at(bit_ft, q)
        if e and len(e['md_ft']) >= 2:
            pts = [(P['upper_ppg'][i] - float(np.interp(P['md_ft'][i], e['md_ft'], e['ecd_ppg'])), i) for i in range(len(P['md_ft'])) if e['md_ft'][0] <= P['md_ft'][i] <= bit_ft + 1.0]
            if pts:
                m, i = min(pts)
                out['ecd'] = {'at_bit_ppg': _r(float(e['ecd_ppg'][-1]), 2), 'margin_ppg': _r(m, 2), 'md_ft': P['md_ft'][i], 'ecd_ppg': _r(P['upper_ppg'][i] - m, 2), 'upper_ppg': P['upper_ppg'][i],
                              'over': bool(m < 0), 'over_stations': sum(1 for d, _i in pts if d < 0)}
    return out


def now(ws, well_id: str) -> dict:
    """The rig against the window, from the end of the well's drilling record."""
    r = well(ws, well_id)
    if 'profile' not in r:
        return {'status': r['status'], 'why': r.get('why')}
    from . import drill_string as DS
    from . import hydraulics as H
    try:
        c = DS._cache(ws, well_id, build=True)
    except Exception as e:
        return {'status': 'NO RECORD', 'why': f"this well has no drilling record to read the rig's place from ({type(e).__name__})"}
    fn, bn, hn = _find(c, FLOW_NAMES), _find(c, ('bit depth',)), _find(c, ('hole depth',))
    bottom = c.extra('bottom')
    ecd_at = None
    try:
        cfg = H.program_config(ws, well_id)
        if cfg.get('string') and cfg.get('hole') and (cfg.get('fann') or {}).get('600') and (H.well_config(ws, well_id) or {}).get('source'):
            rheo = H.rheology(cfg['fann']); path = _path(ws, well_id)
            ecd_at = lambda bit_ft, q: H.ecd_profile(cfg, rheo, float(bit_ft), float(q), path['tvd'], float(r['config']['step_ft']))
    except Exception:
        ecd_at = None
    out = now_from_arrays(r, np.asarray(c.index, dtype=np.float64), c.channel(fn) if fn else None, c.channel(bn) if bn else None,
                          bottom if bottom is not None else (c.channel(hn) if hn else None), ecd_at)
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    from datetime import timedelta
    out.update({'status': 'OK', 'record_end': (t0 + timedelta(seconds=float(c.index[-1]))).isoformat(timespec='seconds'), 'source_mtime': c.meta.get('source_mtime'),
                'ecd_how': 'the Hydraulics tab\'s mud and string at the pump rate of the record\'s last five minutes' if ecd_at else 'the Hydraulics tab has no mud and string declared: no circulating density'})
    return out


def live_events(ws, now_utc: Optional[datetime] = None) -> List[dict]:
    """What an alert is raised for, on a record still being written: the mud weight below the window's floor somewhere
    in the hole drilled so far, and the circulating density over the least stress somewhere above the bit - each once
    for every 500 ft of hole it is first met in."""
    from . import dysfunction as DY
    from . import series_cache as SCH
    out = []
    ts = (now_utc or datetime.now(timezone.utc)).timestamp()
    for w in ws.wells():
        wid = w['id']
        if not (well_config(ws, wid) or {}).get('source'):
            continue
        try:
            if ts - os.path.getmtime(SCH.source_path(ws, wid)) > DY.LIVE_SOURCE_S:
                continue
            n = now(ws, wid)
        except Exception:
            continue
        if n.get('status') != 'OK' or not DY.is_live(n, now_utc):
            continue
        disp = w.get('display') or wid
        mb = n.get('mud_below')
        if mb:
            out.append({'event': EVENTS['mud'], 'key': f"{EVENTS['mud']}:{wid}:{int(mb['from_md_ft'] // BUCKET_FT)}", 'well_id': wid, 'well': disp, 'time': n['record_end'], 'depth_ft': mb['worst_md_ft'],
                        'detail': f"the {mb['mw_ppg']:g} ppg mud is {mb['by_ppg']:.2f} ppg below the window's floor at {mb['worst_md_ft']:,.0f} ft ({mb['floor_ppg']:.2f} ppg, set by the {mb['set_by']}); "
                                  f"below it at {mb['stations']} station(s) from {mb['from_md_ft']:,.0f} ft"})
        e = n.get('ecd')
        if e and e['over']:
            out.append({'event': EVENTS['ecd'], 'key': f"{EVENTS['ecd']}:{wid}:{int(e['md_ft'] // BUCKET_FT)}", 'well_id': wid, 'well': disp, 'time': n['record_end'], 'depth_ft': e['md_ft'],
                        'detail': f"at {n['flow_gpm']:,.0f} gal/min the circulating density is {e['ecd_ppg']:.2f} ppg at {e['md_ft']:,.0f} ft, {abs(e['margin_ppg']):.2f} ppg over the least stress there "
                                  f"({e['upper_ppg']:.2f} ppg); the bit at {n['bit_ft']:,.0f} ft"})
    return out


# -- the self-test: the cases with an answer in closed form -------------------------------------------------------------------

def selftest() -> dict:
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    q = lambda phi: math.tan(math.radians(45.0 + phi / 2.0)) ** 2
    near = lambda a, b, tol=0.5: a is not None and abs(a - b) <= tol
    sv, sH, sh, pp, ucs, phi, nu = 10000.0, 8500.0, 7000.0, 4500.0, 3000.0, 30.0, 0.25
    v = hole_stress(sv, sH, sh, 30.0, 0.0, 0.0)
    ck("a vertical hole, the horizontal stresses unequal: collapse at (3 SH - Sh - UCS + Pp (q - 1)) / (1 + q)", near(collapse(v, pp, ucs, phi, nu), (3 * sH - sh - ucs + pp * (q(phi) - 1)) / (1 + q(phi))))
    ck("the same hole parts at 3 Sh - SH - Pp, and at that plus the tensile strength (Hubbert and Willis)",
       near(breakdown(v, pp, 0.0, nu), 3 * sh - sH - pp) and near(breakdown(v, pp, 500.0, nu), 3 * sh - sH - pp + 500.0))
    v2 = hole_stress(sv, sh, sh, 0.0, 0.0, 0.0)
    ck("a vertical hole, the horizontal stresses equal, the overburden the greatest stress at the wall: collapse at (Sv - UCS + Pp (q - 1)) / q", near(collapse(v2, pp, ucs, phi, nu), (sv - ucs + pp * (q(phi) - 1)) / q(phi)))
    v3 = hole_stress(8000.0, sh, sh, 0.0, 0.0, 0.0)
    ck("the same with a lighter overburden, the hoop stress the greatest: collapse at (2 Sh - UCS + Pp (q - 1)) / (1 + q)", near(collapse(v3, pp, ucs, phi, nu), (2 * sh - ucs + pp * (q(phi) - 1)) / (1 + q(phi))))
    h1 = hole_stress(sv, sH, sh, 30.0, 90.0, 120.0)
    ck("a horizontal hole along the least stress has the overburden and the greatest stress across it: collapse at (3 Sv - SH - UCS + Pp (q - 1)) / (1 + q)",
       near(collapse(h1, pp, ucs, phi, nu), (3 * sv - sH - ucs + pp * (q(phi) - 1)) / (1 + q(phi))) and near(h1['xx'], sv, 1e-6) and near(h1['yy'], sH, 1e-6) and near(h1['zz'], sh, 1e-6))
    h2 = hole_stress(sv, sH, sh, 30.0, 90.0, 30.0)
    ck("a horizontal hole along the greatest stress needs more mud than it can take: collapse above breakdown, no window",
       near(collapse(h2, pp, ucs, phi, nu), (3 * sv - sh - ucs + pp * (q(phi) - 1)) / (1 + q(phi))) and near(breakdown(h2, pp, 0.0, nu), 3 * sh - sv - pp) and collapse(h2, pp, ucs, phi, nu) > breakdown(h2, pp, 0.0, nu))
    a1 = collapse(hole_stress(sv, sH, sh, 30.0, 55.0, 70.0), pp, ucs, phi, nu); a2 = collapse(hole_stress(sv, sH, sh, 30.0, 55.0, 250.0), pp, ucs, phi, nu)
    iso = [collapse(hole_stress(sv, sh, sh, 0.0, 60.0, a), pp, ucs, phi, nu) for a in (0.0, 45.0, 133.0)]
    ck("a hole and the hole pointing the opposite way are the same hole; with equal horizontal stresses the azimuth does not matter", near(a1, a2, 1e-6) and max(iso) - min(iso) < 1e-6)
    # the breakout's width in a vertical hole at a mud pressure, where the hoop stress is the greatest at the wall (Zoback, eq. 6.8's form)
    b = hole_stress(7600.0, 8200.0, 7000.0, 0.0, 0.0, 0.0); pw = 4800.0
    cs = (8200.0 + 7000.0 - pw - pp - ucs - q(phi) * (pw - pp)) / (2.0 * (8200.0 - 7000.0))
    width = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, cs))))
    ck("the breakout's width at a mud pressure is the closed form's, to a degree of the wall", abs(failed_deg(b, pw, pp, ucs, phi, nu) - width) <= 1.5 and 20.0 < width < 160.0)
    w = [collapse(v, pp, ucs, phi, nu, x) for x in (0.0, 30.0, 60.0, 90.0)]
    ck("accepting more breakout takes less mud, and never more", all(w[i] > w[i + 1] for i in range(3)) and near(w[0], 6125.0))
    d = at_depth(10000.0, 0.0, 0.0, sv / 520.0, pp / 520.0, sh / 520.0, sH / 520.0, 30.0, ucs, phi, 0.0, nu)
    ck("as mud weights at 10,000 ft: the floor is the collapse pressure, the top the least stress, the width their difference",
       near(d['collapse_ppg'], 6125.0 / 520.0, 0.002) and near(d['upper_ppg'], sh / 520.0, 1e-9) and d['set_by'] == 'collapse' and near(d['width_ppg'], (sh - 6125.0) / 520.0, 0.002))
    strong = at_depth(10000.0, 0.0, 0.0, sv / 520.0, pp / 520.0, sh / 520.0, sh / 520.0, 0.0, 20000.0, phi, 0.0, nu)
    ck("a rock strong enough needs no mud against collapse: the pore pressure sets the floor", strong['set_by'] == 'pore pressure' and near(strong['lower_ppg'], pp / 520.0, 1e-9))
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'the cases with an answer in closed form'}
