"""Pore pressure and fracture gradient: the mud-weight window, from a well's logs or its drilling record.

The rock's pores hold fluid at a pressure; where it is higher than a column of brine would give (overpressure), the
shale was buried faster than its water could leave and is less compacted than its depth says. The methods here read
that from the log of a compaction indicator against its normal trend and turn it into a pressure:

- the OVERBURDEN - the weight of everything above: the sea (offshore) and the rock, its density from the bulk density
  log where there is one, else from the sonic by Gardner's relation (Gardner, Gardner and Gregory, "Formation velocity
  and density - the diagnostic basics for stratigraphic traps", Geophysics 39(6), 1974: rho = 0.23 V^0.25, g/cm3 with
  V in ft/s), else the declared mean density above the first log value;
- the NORMAL COMPACTION TREND - the indicator in shale (gamma ray at or above the declared cut-off) over a depth
  interval the operator declares normally pressured, fitted as a straight line in its logarithm against true vertical
  depth: sonic transit time falls with depth, resistivity and the corrected d-exponent rise;
- the PORE PRESSURE by Eaton's ratio method (Eaton, "The equation for geopressure prediction from well logs", SPE-5544,
  1975): Pp = S - (S - Ph) (X / Xn)^n, with S the overburden, Ph the normal (hydrostatic) pressure, X the indicator and
  Xn its trend at that depth taken so that a less compacted shale gives a higher pressure - sonic (Xn/X)^3.0,
  resistivity (X/Xn)^1.2, corrected d-exponent (X/Xn)^1.2 - the exponents Eaton published, the operator's to change;
- the corrected d-exponent from the drilling record (Jorden and Shirley, SPE-1407, 1966; Rehm and McClendon, SPE-3601,
  1971): d = log10(R / 60N) / log10(12W / 10^6 D), R the rate of penetration (ft/h), N the rotary speed (rpm), W the
  weight on bit (lbf), D the bit diameter (in); dc = d x (normal pore gradient / mud density);
- the FRACTURE GRADIENT by Eaton's relation (Eaton, "Fracture gradient prediction and its application in oilfield
  operations", JPT 21(10), 1969): F = (nu / (1 - nu)) (S - Pp) + Pp, nu Poisson's ratio (declared); and the Hubbert and
  Willis minimum (Hubbert and Willis, "Mechanics of hydraulic fracturing", Trans. AIME 210, 1957): (S - Pp) / 3 + Pp.

Every pressure is also given as an equivalent mud weight (ppg = psi / (0.052 x TVD ft)). Declared pressure
measurements - formation tests, kicks, leak-off and formation-integrity tests - are drawn beside the curves, and the
Eaton exponent that best fits the formation tests is given. The worked example is the Dutch offshore well L07-01
(NLOG open data), its sonic and gamma ray from 358 to 3,916 m: a pressure read from a trend is only as good as the
trend, and the example shows the trend beside the log so the choice can be judged.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
G = 9.80665
PSI_PER_PA = 1.0 / 6894.757293168
PPG_PER_PSI_FT = 1.0 / 0.052
INDICATORS = {
    'sonic': {'names': ('DT', 'DTC', 'AC', 'DTCO', 'DT4P', 'DTCOMP', 'SONIC'), 'units': ('us/ft', 'us/f', 'usec/ft'), 'exponent': 3.0, 'direction': -1},
    'resistivity': {'names': ('RDEP', 'ILD', 'RT', 'RD', 'LLD', 'RILD', 'RT_HRLT', 'AT90', 'RDEEP', 'P40H', 'RT_ARC'), 'units': ('ohmm', 'ohm.m', 'ohm-m'),
                    'exponent': 1.2, 'direction': 1},
    'dexponent': {'names': (), 'units': (), 'exponent': 1.2, 'direction': 1},
}
DENSITY_NAMES = ('RHOB', 'DEN', 'ROBB', 'RHOZ', 'DENS', 'ZDEN', 'DLDN', 'RHO8')
GR_NAMES = ('GR', 'GRMA', 'GAM', 'GAMN', 'SGR', 'CGR', 'GR_EDTC', 'GRC', 'GSGR', 'IDGR', 'HSGR')
DEFAULTS = {'indicator': 'auto', 'exponent': None, 'nct_top_m': None, 'nct_bottom_m': None, 'gr_cutoff': None, 'normal_gradient_psi_ft': 0.465,
            'poisson': 0.25, 'density_above_gcc': 2.0, 'water_depth_m': 0.0, 'air_gap_m': 0.0, 'water_density_gcc': 1.03, 'smooth_m': 15.0,
            'bit_in': None, 'source': ''}
NOT_A_MEASUREMENT = ['the pore pressure where nothing was measured: it is computed from a log against its trend, and the trend is the '
                     'operator\'s choice of a normally pressured interval',
                     'the fracture gradient: a relation with a declared Poisson\'s ratio, not a leak-off test',
                     'the pressure in sand and carbonate: the method reads shale (the gamma ray at or above its cut-off)']
EXAMPLE = os.path.join(os.path.dirname(__file__), 'reference', 'l0701_sonic_gr.las.gz')


# -- the arithmetic -------------------------------------------------------------------------------------------------------
def gardner(dt_us_ft: np.ndarray) -> np.ndarray:
    """Bulk density (g/cm3) from compressional slowness (us/ft): 0.23 V^0.25, V in ft/s."""
    with np.errstate(divide='ignore', invalid='ignore'):
        return 0.23 * (1.0e6 / dt_us_ft) ** 0.25


def overburden_psi(tvd_m: np.ndarray, rho_gcc: np.ndarray, water_depth_m: float = 0.0, air_gap_m: float = 0.0,
                   water_density_gcc: float = 1.03, density_above_gcc: float = 2.0) -> np.ndarray:
    """The vertical stress (psi) at each true vertical depth below the rig floor: the sea from the air gap to the
    seabed, then the rock - the given densities between samples, the declared mean density above the first one."""
    z = np.asarray(tvd_m, dtype=np.float64)
    r = np.asarray(rho_gcc, dtype=np.float64).copy()
    ok = np.isfinite(r)
    if ok.any():
        r[~ok] = np.interp(z[~ok], z[ok], r[ok])
    else:
        r[:] = density_above_gcc
    mud = air_gap_m + water_depth_m                       # below this, rock
    S = np.zeros_like(z)
    sea = max(0.0, min(z[0], mud) - air_gap_m) * water_density_gcc * 1000.0 * G
    first = z[0] - mud
    rock0 = max(0.0, first) * density_above_gcc * 1000.0 * G
    S[0] = sea + rock0
    dz = np.diff(z)
    seg = 0.5 * (r[1:] + r[:-1]) * 1000.0 * G * dz
    below = z[1:] > mud
    seg = np.where(below, seg, dz * water_density_gcc * 1000.0 * G)
    S[1:] = S[0] + np.cumsum(seg)
    return S * PSI_PER_PA


def ppg(psi: np.ndarray, tvd_m: np.ndarray) -> np.ndarray:
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.asarray(psi) / (0.052 * np.asarray(tvd_m) / FT)


def dexponent(rop_ft_h, rpm, wob_lbf, bit_in) -> np.ndarray:
    """Jorden and Shirley's d-exponent."""
    R, N, W = (np.asarray(x, dtype=np.float64) for x in (rop_ft_h, rpm, wob_lbf))
    with np.errstate(divide='ignore', invalid='ignore'):
        d = np.log10(R / (60.0 * N)) / np.log10(12.0 * W / (1.0e6 * float(bit_in)))
    return np.where((R > 0) & (N > 0) & (W > 0), d, np.nan)


def dc(d, normal_ppg: float, mud_ppg) -> np.ndarray:
    """Rehm and McClendon's correction for the mud density."""
    return np.asarray(d, dtype=np.float64) * float(normal_ppg) / np.asarray(mud_ppg, dtype=np.float64)


def fit_trend(tvd_m: np.ndarray, x: np.ndarray) -> Optional[Tuple[float, float, int]]:
    """log10(x) = a + b z by least squares; None with fewer than 20 points."""
    ok = np.isfinite(x) & (x > 0) & np.isfinite(tvd_m)
    if ok.sum() < 20:
        return None
    b, a = np.polyfit(tvd_m[ok], np.log10(x[ok]), 1)
    return float(a), float(b), int(ok.sum())


def eaton(S, Ph, x, xn, n: float, direction: int) -> np.ndarray:
    """Eaton's ratio: Pp = S - (S - Ph) r^n, r = x/xn for an indicator that rises with compaction (resistivity, dc),
    xn/x for one that falls (sonic)."""
    with np.errstate(divide='ignore', invalid='ignore'):
        r = (np.asarray(x) / np.asarray(xn)) if direction > 0 else (np.asarray(xn) / np.asarray(x))
        return np.asarray(S) - (np.asarray(S) - np.asarray(Ph)) * r ** n


def eaton_fg(S, Pp, nu: float) -> np.ndarray:
    return nu / (1.0 - nu) * (np.asarray(S) - np.asarray(Pp)) + np.asarray(Pp)


def hubbert_willis(S, Pp) -> np.ndarray:
    return (np.asarray(S) - np.asarray(Pp)) / 3.0 + np.asarray(Pp)


def fit_exponent(S, Ph, x, xn, direction: int, measured) -> Optional[float]:
    """The Eaton exponent that best matches measured pore pressures (least squares over 0.3 to 6)."""
    S, Ph, x, xn, m = (np.asarray(v, dtype=np.float64) for v in (S, Ph, x, xn, measured))
    ok = np.isfinite(S) & np.isfinite(x) & np.isfinite(xn) & np.isfinite(m)
    if ok.sum() < 1:
        return None
    grid = np.arange(0.3, 6.0001, 0.01)
    err = [float(np.sum((eaton(S[ok], Ph[ok], x[ok], xn[ok], n, direction) - m[ok]) ** 2)) for n in grid]
    return round(float(grid[int(np.argmin(err))]), 2)


# -- the analysis of one log ----------------------------------------------------------------------------------------------
def _pick(chans: Dict[str, Tuple[np.ndarray, str]], names, units=()) -> Optional[str]:
    up = {k.upper(): k for k in chans}
    for n in names:
        if n.upper() in up:
            return up[n.upper()]
    for k, (_, u) in chans.items():
        if units and (u or '').lower().replace(' ', '') in units:
            return k
    return None


def analyse_log(md_m: np.ndarray, tvd_m: np.ndarray, chans: Dict[str, Tuple[np.ndarray, str]], cfg: dict,
                measured: Optional[List[dict]] = None, mud: Optional[Tuple[np.ndarray, np.ndarray]] = None) -> dict:
    """One well's log: md and tvd (m, rig floor), channels {name: (values, unit)}; cfg as DEFAULTS; measured: declared
    pressures [{tvd_m, psi, kind}]; mud: (tvd_m, ppg) of the mud weight used."""
    cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
    kind = cfg['indicator']
    ind = None
    if kind in ('auto', 'sonic'):
        ind = _pick(chans, INDICATORS['sonic']['names'], INDICATORS['sonic']['units'])
        kind = 'sonic' if ind else kind
    if ind is None and cfg['indicator'] in ('auto', 'resistivity'):
        ind = _pick(chans, INDICATORS['resistivity']['names'])
        kind = 'resistivity' if ind else kind
    if ind is None and cfg['indicator'] in ('auto', 'dexponent') and 'dc' in chans:
        ind, kind = 'dc', 'dexponent'
    if ind is None:
        raise ValueError('no compaction indicator in this log: a sonic, a deep resistivity or the d-exponent')
    spec = INDICATORS[kind]
    n = float(cfg['exponent'] or spec['exponent'])
    x = np.asarray(chans[ind][0], dtype=np.float64)
    if kind == 'sonic' and (chans[ind][1] or '').lower() in ('us/m', 'usec/m'):
        x = x * FT
    den_n = _pick(chans, DENSITY_NAMES)
    gr_n = _pick(chans, GR_NAMES)
    rho = np.asarray(chans[den_n][0], dtype=np.float64) if den_n else np.full(len(md_m), np.nan)
    rho = np.where((rho > 1.2) & (rho < 3.2), rho, np.nan)
    rho_src = np.where(np.isfinite(rho), 'density log', '')
    if kind == 'sonic':
        gd = gardner(x)
        use = ~np.isfinite(rho) & np.isfinite(gd) & (gd > 1.2) & (gd < 3.2)
        rho = np.where(use, gd, rho)
        rho_src = np.where(use, 'Gardner from the sonic', rho_src)
    order = np.argsort(tvd_m)
    md_m, tvd_m, x, rho = md_m[order], tvd_m[order], x[order], rho[order]
    gr = np.asarray(chans[gr_n][0], dtype=np.float64)[order] if gr_n else None
    S = overburden_psi(tvd_m, rho, cfg['water_depth_m'], cfg['air_gap_m'], cfg['water_density_gcc'], cfg['density_above_gcc'])
    Ph = cfg['normal_gradient_psi_ft'] * np.maximum(tvd_m - cfg['air_gap_m'], 0.0) / FT
    cut = cfg['gr_cutoff']
    if gr is not None and cut is None:
        cut = float(np.nanpercentile(gr, 60)) if np.isfinite(gr).any() else None
    shale = np.isfinite(x) & (x > 0) & ((gr >= cut) if (gr is not None and cut is not None) else True)
    if gr is not None:
        shale &= np.isfinite(gr)
    top = cfg['nct_top_m'] if cfg['nct_top_m'] is not None else float(np.nanmin(tvd_m[np.isfinite(x)])) if np.isfinite(x).any() else 0.0
    bot = cfg['nct_bottom_m'] if cfg['nct_bottom_m'] is not None else top + 0.4 * (float(np.nanmax(tvd_m[np.isfinite(x)])) - top) if np.isfinite(x).any() else 0.0
    in_nct = shale & (tvd_m >= top) & (tvd_m <= bot)
    tr = fit_trend(tvd_m[in_nct], x[in_nct])
    if tr is None:
        raise ValueError(f'fewer than 20 shale points between {top:,.0f} and {bot:,.0f} m TVD to fit the normal compaction trend')
    a, b, npts = tr
    xn = 10 ** (a + b * tvd_m)
    if spec['direction'] < 0 and b >= 0 or spec['direction'] > 0 and b <= 0:
        trend_note = 'the fitted trend runs the wrong way for compaction: the interval is not normally compacted'
    else:
        trend_note = ''
    # smoothed shale indicator on a regular depth grid
    step = max(1.0, float(cfg['smooth_m']) / 3.0)
    zs = np.arange(math.floor(np.nanmin(tvd_m[shale]) if shale.any() else tvd_m[0]), np.nanmax(tvd_m) + step, step) if shale.any() else np.array([])
    xs = np.full(len(zs), np.nan)
    half = float(cfg['smooth_m']) / 2.0
    zsh, xsh = tvd_m[shale], x[shale]
    for i, z in enumerate(zs):
        lo, hi = np.searchsorted(zsh, z - half), np.searchsorted(zsh, z + half)
        if hi - lo >= 1:
            xs[i] = float(np.median(xsh[lo:hi]))
    Sg = np.interp(zs, tvd_m, S) if len(zs) else zs
    Phg = cfg['normal_gradient_psi_ft'] * np.maximum(zs - cfg['air_gap_m'], 0.0) / FT
    xng = 10 ** (a + b * zs)
    Pp = eaton(Sg, Phg, xs, xng, n, spec['direction'])
    below = np.isfinite(Pp) & (Pp < 0.9 * Phg)              # far faster than the trend: not a shale of this compaction
    n_below = int(below.sum())
    Pp = np.where(below, np.nan, Pp)
    FG = eaton_fg(Sg, Pp, float(cfg['poisson']))
    HW = hubbert_willis(Sg, Pp)
    meas = []
    for m_ in measured or []:
        z = float(m_['tvd_m'])
        meas.append({**m_, 'ppg': round(float(ppg(m_['psi'], z)), 3), 'hydrostatic_psi': round(cfg['normal_gradient_psi_ft'] * max(z - cfg['air_gap_m'], 0) / FT, 1)})
    pore_meas = [m_ for m_ in meas if m_.get('kind', 'pore') in ('pore', 'rft', 'mdt', 'dst', 'kick', 'formation test')]
    fit_n = None
    if pore_meas and len(zs):
        zz = np.array([m_['tvd_m'] for m_ in pore_meas])
        fit_n = fit_exponent(np.interp(zz, zs, Sg), cfg['normal_gradient_psi_ft'] * np.maximum(zz - cfg['air_gap_m'], 0.0) / FT,
                             np.interp(zz, zs, xs), 10 ** (a + b * zz), spec['direction'], [m_['psi'] for m_ in pore_meas])
        for m_ in pore_meas:
            m_['computed_psi'] = round(float(np.interp(m_['tvd_m'], zs, Pp)), 1) if np.isfinite(np.interp(m_['tvd_m'], zs, Pp)) else None
    flags = []
    if mud is not None and len(zs):
        mz, mw = (np.asarray(v, dtype=np.float64) for v in mud)
        if len(mz):
            mwg = np.interp(zs, mz, mw, left=np.nan, right=np.nan)
            ppp, fgp = ppg(Pp, zs), ppg(FG, zs)
            for lab, mask in (('below the pore pressure', mwg < ppp), ('above the fracture gradient', mwg > fgp)):
                mask = mask & np.isfinite(mwg)
                if mask.any():
                    flags.append({'what': f'the mud weight {lab}', 'from_tvd_m': round(float(zs[mask][0]), 1), 'to_tvd_m': round(float(zs[mask][-1]), 1),
                                  'samples': int(mask.sum())})
    keep = np.isfinite(Pp)
    over0 = keep & (ppg(Pp, zs) > ppg(Phg, zs) + 0.5)
    run = max(1, int(round(50.0 / step)))                    # half a ppg over hydrostatic held for 50 m
    over = np.zeros_like(over0)
    for i in range(len(zs) - run + 1):
        if over0[i:i + run].all():
            over[i] = True
            break
    k = max(1, int(len(zs) / 700))
    r1 = lambda v, d=2: [None if not np.isfinite(q) else round(float(q), d) for q in v[::k]]
    kd = max(1, int(len(tvd_m) / 1500))
    return {'indicator': kind, 'channel': ind, 'exponent': n, 'density': den_n, 'gamma': gr_n, 'gr_cutoff': None if cut is None else round(cut, 1),
            'trend': {'a': round(a, 6), 'b_per_m': round(b, 8), 'points': npts, 'top_tvd_m': round(top, 1), 'bottom_tvd_m': round(bot, 1), 'note': trend_note},
            'density_from': sorted(set(str(s) for s in rho_src if s)) + ([f"{cfg['density_above_gcc']:g} g/cm3 declared above the first value"]),
            'overburden_td_psi': round(float(S[-1]), 1), 'overburden_td_ppg': round(float(ppg(S[-1], tvd_m[-1])), 3), 'td_tvd_m': round(float(tvd_m[-1]), 1),
            'max_pp_ppg': round(float(np.nanmax(ppg(Pp, zs))), 2) if keep.any() else None,
            'max_pp_at_tvd_m': round(float(zs[keep][int(np.nanargmax(ppg(Pp, zs)[keep]))]), 1) if keep.any() else None,
            'overpressure_from_tvd_m': round(float(zs[over][0]), 1) if over.any() else None,
            'fit_exponent': fit_n, 'measured': meas, 'flags': flags,
            'not_read': n_below and f'{n_below} depth step(s) read below 90 % of hydrostatic - rock faster than the shale trend (salt, carbonate, cemented sand) - left blank',
            'curves': {'tvd_m': r1(zs, 1), 'pp_ppg': r1(ppg(Pp, zs)), 'fg_ppg': r1(ppg(FG, zs)), 'hw_ppg': r1(ppg(HW, zs)), 'ob_ppg': r1(ppg(Sg, zs)),
                       'hyd_ppg': r1(ppg(Phg, zs)), 'x': r1(xs, 3), 'xn': r1(xng, 3), 'pp_psi': r1(Pp, 0), 'fg_psi': r1(FG, 0), 'ob_psi': r1(Sg, 0)},
            'points': {'tvd_m': [round(float(v), 1) for v in tvd_m[::kd]], 'x': [None if not np.isfinite(v) else round(float(v), 3) for v in x[::kd]],
                       'shale': [bool(v) for v in shale[::kd]]},
            'mud': None if mud is None else {'tvd_m': [round(float(v), 1) for v in mud[0]], 'ppg': [round(float(v), 2) for v in mud[1]]}}


# -- the workspace --------------------------------------------------------------------------------------------------------
def _store_path(ws) -> str:
    return os.path.join(ws.path, 'geomech', 'pore_pressure.json')


def _load(ws) -> dict:
    try:
        with open(_store_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wells': {}, 'measured': {}}


def _save(ws, d: dict) -> None:
    os.makedirs(os.path.dirname(_store_path(ws)), exist_ok=True)
    with open(_store_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(_store_path(ws) + '.tmp', _store_path(ws))


LIMITS = {'exponent': (0.3, 6.0), 'nct_top_m': (0.0, 15000.0), 'nct_bottom_m': (0.0, 15000.0), 'gr_cutoff': (0.0, 1000.0),
          'normal_gradient_psi_ft': (0.40, 0.52), 'poisson': (0.05, 0.49), 'density_above_gcc': (1.5, 2.8), 'water_depth_m': (0.0, 4000.0),
          'air_gap_m': (0.0, 200.0), 'water_density_gcc': (1.0, 1.1), 'smooth_m': (1.0, 200.0), 'bit_in': (2.0, 40.0)}


def well_config(ws, well_id: str) -> dict:
    return {**DEFAULTS, **((_load(ws).get('wells') or {}).get(well_id) or {})}


def set_well(ws, actor: str, well_id: str, source: str = '', **cfg) -> dict:
    """Declare how a well's pore pressure is read: the indicator, Eaton's exponent, the normally pressured interval,
    the shale cut-off, the normal gradient, Poisson's ratio, the density above the logs, the sea and the bit size."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    d = _load(ws)
    cur = dict((d.get('wells') or {}).get(well_id) or {})
    changed = False
    for k, v in cfg.items():
        if k == 'indicator' and v not in (None, ''):
            if v not in ('auto', 'sonic', 'resistivity', 'dexponent'):
                raise ValueError('the indicator is auto, sonic, resistivity or dexponent')
            cur[k] = v; changed = True
            continue
        if k not in LIMITS:
            continue
        if v in (None, ''):
            if k in cur:
                cur.pop(k); changed = True
            continue
        x = float(v)
        lo, hi = LIMITS[k]
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{k} must be between {lo:g} and {hi:g}')
        cur[k] = x; changed = True
    if cur.get('nct_top_m') is not None and cur.get('nct_bottom_m') is not None and cur['nct_bottom_m'] <= cur['nct_top_m']:
        raise ValueError('the normally pressured interval must end deeper than it starts')
    if changed:
        if not (source or '').strip():
            raise ValueError('say where these values came from (e.g. "offset well RFTs, 2019 drilling program")')
        cur['source'] = source.strip()
    cur['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'); cur['by'] = actor
    d.setdefault('wells', {})[well_id] = cur
    _save(ws, d)
    ws.audit(actor, 'pore_pressure.settings', {'well': well_id, **{k: v for k, v in cur.items() if k not in ('utc', 'by')}})
    return {**DEFAULTS, **cur}


def add_measurement(ws, actor: str, well_id: str, depth_m, value, unit: str = 'psi', kind: str = 'pore', depth_kind: str = 'tvd',
                    source: str = '') -> dict:
    """A measured pressure: a formation test or a kick (kind 'pore'), or a leak-off or formation-integrity test (kind
    'lot' / 'fit'); its depth (m, true vertical or measured) and its value (psi, or ppg equivalent at that depth)."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    if kind not in ('pore', 'kick', 'lot', 'fit'):
        raise ValueError('a measurement is a pore pressure (pore), a kick, a leak-off test (lot) or a formation-integrity test (fit)')
    if unit not in ('psi', 'ppg'):
        raise ValueError('the value is in psi or ppg')
    if depth_kind not in ('tvd', 'md'):
        raise ValueError('the depth is tvd or md')
    z, v = float(depth_m), float(value)
    if not (0 < z < 15000 and v > 0 and math.isfinite(v)):
        raise ValueError('a measurement needs a depth (m) and a positive value')
    if not (source or '').strip():
        raise ValueError('say where the measurement came from (e.g. "MDT run 2, station 14")')
    d = _load(ws)
    rec = {'depth_m': z, 'depth_kind': depth_kind, 'value': v, 'unit': unit, 'kind': kind, 'source': source.strip(), 'by': actor,
           'utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    d.setdefault('measured', {}).setdefault(well_id, []).append(rec)
    _save(ws, d)
    ws.audit(actor, 'pore_pressure.measurement', {'well': well_id, **{k: rec[k] for k in ('depth_m', 'depth_kind', 'value', 'unit', 'kind', 'source')}})
    return rec


def remove_measurement(ws, actor: str, well_id: str, index: int) -> dict:
    d = _load(ws)
    lst = (d.get('measured') or {}).get(well_id) or []
    if not 0 <= int(index) < len(lst):
        raise KeyError(f'{well_id} has no measurement {index}')
    rec = lst.pop(int(index))
    _save(ws, d)
    ws.audit(actor, 'pore_pressure.measurement_removed', {'well': well_id, **{k: rec[k] for k in ('depth_m', 'value', 'unit', 'kind')}})
    return rec


def _cache(ws, well_id: str):
    """The well's series cache for a time record; None for a depth log (read from its file)."""
    from . import series_cache as SCH
    try:
        c = SCH.ensure(ws, well_id)
    except Exception:
        return None
    return c if (c is not None and c.meta.get('index_kind') == 'time_s') else None


def _source_kind(ws, well_id: str) -> Optional[str]:
    w = ws.well(well_id)
    names = [str(f).lower() for f in (w.get('files') or [])]
    if any(n.endswith(('.las', '.dlis', '.las.gz')) for n in names):
        return 'log'
    c = _cache(ws, well_id)
    if c is not None and any(x['name'] == 'Rate Of Penetration' and x.get('present') for x in c.meta['channels']):
        return 'drilling record'
    return None


def _tvd_of(ws, well_id: str):
    """md (m) -> tvd (m) by the well's survey, or None (the well taken as vertical)."""
    from . import well3d as W3
    try:
        tj = W3.trajectory_of(ws, well_id)
    except Exception:
        tj = None
    if not tj or len(tj['stations']) < 2:
        return None
    st = tj['stations']
    p = W3.Path([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], 0.0, 0.0, 0.0, 0.0)
    return lambda md: np.interp(md, p.md, p.tvd)


def inputs(ws, well_id: str, cfg: dict) -> Tuple[np.ndarray, np.ndarray, Dict[str, Tuple[np.ndarray, str]], str, Optional[tuple]]:
    """A well's depth log, or its drilling record turned into a depth log of the corrected d-exponent and gamma ray:
    (md m, tvd m, channels, how, mud (tvd, ppg) or None)."""
    f_tvd = _tvd_of(ws, well_id)
    c = _cache(ws, well_id)
    if c is None:
        st = ws.well_stream(well_id)
        if st.index_kind != 'depth':
            raise ValueError('the record could not be read')
        unit = ((st.meta or {}).get('index_unit') or 'M').upper()
        md = np.asarray(st.index, dtype=np.float64) * (FT if unit.startswith('F') else 1.0)
        ch = {k: (np.asarray(v.values, dtype=np.float64), v.unit or '') for k, v in st.channels.items()}
        how = f"the depth log ({len(md):,} samples, depths in {unit.lower()})"
        tvd = f_tvd(md) if f_tvd else md.copy()
        return md, tvd, ch, how + ('; true vertical depth by the survey' if f_tvd else '; taken as vertical (no survey)'), None
    names = {x['name']: x for x in c.meta['channels'] if x.get('present')}
    need = ('Hole Depth', 'Rate Of Penetration', 'Rotary RPM', 'Weight on Bit')
    if not all(n in names for n in need):
        raise ValueError('neither a depth log nor a drilling record with hole depth, rate of penetration, rotary speed and weight on bit')
    drill = c.extra('drilling')
    sel = np.asarray(drill, dtype=bool) if drill is not None else np.ones(c.meta['n'], dtype=bool)
    hd = np.asarray(c.channel('Hole Depth'), dtype=np.float64)[sel]
    fd = FT if (c.unit('Hole Depth') or 'ft').lower().startswith('f') else 1.0
    rop = np.asarray(c.channel('Rate Of Penetration'), dtype=np.float64)[sel] * (1.0 if (c.unit('Rate Of Penetration') or 'ft/h').lower().startswith('f') else 1 / FT)
    rpm = np.asarray(c.channel('Rotary RPM'), dtype=np.float64)[sel]
    wu = (c.unit('Weight on Bit') or 'klbf').lower()
    wob = np.asarray(c.channel('Weight on Bit'), dtype=np.float64)[sel] * (1000.0 if wu.startswith('k') and 'lb' in wu else 224.809 if wu == 'kn' else 1.0)
    bit = cfg.get('bit_in')
    if not bit:
        raise ValueError('the d-exponent needs the bit size: declare it (inches)')
    d = dexponent(rop, rpm, wob, bit)
    mud_ch = next((n for n in names if 'mud' in n.lower() and ('weight' in n.lower() or 'density' in n.lower())), None)
    norm_ppg = cfg['normal_gradient_psi_ft'] * PPG_PER_PSI_FT
    mw = np.asarray(c.channel(mud_ch), dtype=np.float64)[sel] if mud_ch else None
    dcx = dc(d, norm_ppg, mw) if mw is not None else d
    md_s = hd * fd
    edges = np.arange(np.nanmin(md_s), np.nanmax(md_s) + 3.0, 3.0) if np.isfinite(md_s).any() else np.array([0.0, 1.0])
    k = np.digitize(md_s, edges)
    out_md, out_dc, out_gr, out_mw = [], [], [], []
    gr = np.asarray(c.channel('Gamma'), dtype=np.float64)[sel] if 'Gamma' in names else None
    for i in np.unique(k):
        m_ = (k == i) & np.isfinite(dcx)
        if m_.sum() < 3:
            continue
        out_md.append(float(np.median(md_s[m_]))); out_dc.append(float(np.median(dcx[m_])))
        out_gr.append(float(np.nanmedian(gr[m_])) if gr is not None else np.nan)
        out_mw.append(float(np.nanmedian(mw[m_])) if mw is not None else np.nan)
    md = np.asarray(out_md)
    if 'Interpolated TVD' in names and not f_tvd:
        tv = np.asarray(c.channel('Interpolated TVD'), dtype=np.float64)[sel] * fd
        okv = np.isfinite(tv) & np.isfinite(md_s)
        o = np.argsort(md_s[okv])
        tvd = np.interp(md, md_s[okv][o], tv[okv][o]) if okv.sum() > 1 else md.copy()
        tsrc = "the recorder's interpolated TVD"
    else:
        tvd = f_tvd(md) if f_tvd else md.copy()
        tsrc = 'the survey' if f_tvd else 'taken as vertical (no survey)'
    ch = {'dc': (np.asarray(out_dc), ''), 'GR': (np.asarray(out_gr), 'gAPI')}
    how = (f"the drilling record's d-exponent (bit {bit:g} in), " + (f"corrected by its {mud_ch}" if mud_ch else 'not corrected (no mud density channel)')
           + f", the drilling samples' median every 3 m of hole depth; true vertical depth by {tsrc}")
    mud = (tvd, np.asarray(out_mw)) if mud_ch else None
    return md, tvd, ch, how, mud


def _measured(ws, well_id: str, f_tvd) -> List[dict]:
    out = []
    for i, m_ in enumerate((_load(ws).get('measured') or {}).get(well_id) or []):
        z = float(m_['depth_m'])
        tvd = float(f_tvd(np.array([z]))[0]) if (m_['depth_kind'] == 'md' and f_tvd) else z
        psi = m_['value'] if m_['unit'] == 'psi' else m_['value'] * 0.052 * tvd / FT
        out.append({'index': i, 'tvd_m': round(tvd, 2), 'psi': round(psi, 1), 'kind': 'pore' if m_['kind'] in ('pore', 'kick') else m_['kind'],
                    'label': m_['kind'], 'source': m_['source']})
    return out


def example() -> dict:
    """The worked example: L07-01 (Dutch offshore, NLOG open data), its sonic and gamma ray, rotary table 37 m above
    sea level, 36 m of water; the normal trend fitted from 450 to 2,950 m."""
    from . import ports
    import tempfile
    with gzip.open(EXAMPLE, 'rb') as f:
        raw = f.read()
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'l0701.las')
        with open(p, 'wb') as f:
            f.write(raw)
        st = ports.read_las(p)
    md = np.asarray(st.index, dtype=np.float64)
    ch = {k: (np.asarray(v.values, dtype=np.float64), v.unit or '') for k, v in st.channels.items()}
    cfg = {**DEFAULTS, 'water_depth_m': 36.0, 'air_gap_m': 37.0, 'nct_top_m': 450.0, 'nct_bottom_m': 2950.0,
           'source': "the well's header (rotary table 37 m above mean sea level, ground level 36 m below); the trend interval - the Tertiary "
                     "and the Jurassic-Triassic shales, 450 to 2,950 m - chosen here for the example, not from any pressure measured in the well"}
    r = analyse_log(md, md.copy(), ch, cfg)
    r.update({'well': 'L07-01', 'display': 'Worked example: L07-01, Dutch offshore (NLOG open data)', 'how': 'the composite log as released (taken as vertical)',
              'config': cfg, 'attribution': 'Well L07-01 (UBID 7264), NLOG - Dutch Oil and Gas Portal open data; the LAS as redistributed in A. McDonald\'s public notebooks (MIT).'})
    return r


def page(ws, scene: str = 'workspace', well: Optional[str] = None) -> dict:
    out = {'scene': scene, 'scenes': [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'example', 'title': 'Worked example: L07-01 (public data)'}],
           'defaults': DEFAULTS, 'not_a_measurement': NOT_A_MEASUREMENT, 'wells': [], 'well': None, 'result': None, 'detail': ''}
    if scene == 'example' or ws is None:
        out['result'] = example()
        out['scene'] = 'example'
        return out
    if scene != 'workspace':
        raise KeyError(f'no scene {scene!r}: workspace or example')
    for w in ws.wells():
        try:
            kind = _source_kind(ws, w['id'])
        except Exception:
            kind = None
        out['wells'].append({'id': w['id'], 'display': w.get('display') or w['id'], 'source': kind, 'config': well_config(ws, w['id']),
                             'measured': (_load(ws).get('measured') or {}).get(w['id']) or []})
    usable = [w for w in out['wells'] if w['source']]
    choice = well if any(w['id'] == well for w in out['wells']) else (usable[0]['id'] if usable else None)
    out['well'] = choice
    if choice is None:
        out['detail'] = 'no well has a depth log or a drilling record to read'
        return out
    cfg = well_config(ws, choice)
    try:
        md, tvd, ch, how, mud = inputs(ws, choice, cfg)
        r = analyse_log(md, tvd, ch, cfg, _measured(ws, choice, _tvd_of(ws, choice)), mud)
        r.update({'how': how, 'config': cfg, 'well': choice, 'display': next(w['display'] for w in out['wells'] if w['id'] == choice)})
        out['result'] = r
    except ValueError as e:
        out['detail'] = str(e)
    return out


def text(r: dict) -> str:
    x = r.get('result')
    if not x:
        return f"Pore pressure: {r.get('detail', 'nothing to read')}"
    L = [f"Pore pressure - {x.get('display', x.get('well'))}: {x['indicator']} ({x['channel']}), Eaton exponent {x['exponent']:g}; {x.get('how', '')}",
         f"  normal trend: log10 = {x['trend']['a']:.4f} {x['trend']['b_per_m']:+.3e} x TVD(m), {x['trend']['points']:,} shale points from "
         f"{x['trend']['top_tvd_m']:,.0f} to {x['trend']['bottom_tvd_m']:,.0f} m (gamma ray >= {x['gr_cutoff']})" + (f"; {x['trend']['note']}" if x['trend']['note'] else ''),
         f"  overburden at {x['td_tvd_m']:,.0f} m TVD: {x['overburden_td_psi']:,.0f} psi ({x['overburden_td_ppg']:.2f} ppg); density from {', '.join(x['density_from'])}",
         f"  highest pore pressure {x['max_pp_ppg']} ppg at {x['max_pp_at_tvd_m']} m TVD" + (f"; overpressure (half a ppg over hydrostatic) from {x['overpressure_from_tvd_m']:,.0f} m" if x['overpressure_from_tvd_m'] else '; no overpressure read')]
    if x.get('not_read'):
        L.append('  ' + x['not_read'])
    for m_ in x['measured']:
        L.append(f"  measured {m_['label']} at {m_['tvd_m']:,.0f} m TVD: {m_['psi']:,.0f} psi ({m_['ppg']:.2f} ppg)" + (f", computed {m_['computed_psi']:,.0f}" if m_.get('computed_psi') else ''))
    if x.get('fit_exponent'):
        L.append(f"  the exponent that fits the measured pore pressures: {x['fit_exponent']:g}")
    for f_ in x['flags']:
        L.append(f"  {f_['what']}: {f_['from_tvd_m']:,.0f} to {f_['to_tvd_m']:,.0f} m TVD")
    return '\n'.join(L)


def build_report(ws, version: str, well: Optional[str] = None, scene: str = 'workspace'):
    from . import client_reports as CR
    r = page(ws, scene, well)
    x = r['result']
    if not x:
        raise ValueError(r.get('detail') or 'nothing to read')
    now = datetime.now(timezone.utc)
    rid = f"PPF-{now.strftime('%Y%m%dT%H%M%SZ')}"
    front = [['Report ID', rid], ['Well', x.get('display', '')], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'Downhole Gauge Monitoring build {version}'], ['Indicator', f"{x['indicator']} ({x['channel']}), Eaton exponent {x['exponent']:g}"],
             ['Highest pore pressure', f"{x['max_pp_ppg']} ppg at {x['max_pp_at_tvd_m']} m TVD"],
             ['Overburden at TD', f"{x['overburden_td_ppg']:.2f} ppg ({x['overburden_td_psi']:,.0f} psi) at {x['td_tvd_m']:,.0f} m TVD"]]
    cfg = x['config']
    s1 = CR.Section('1', 'The method and what was declared', [
        f"Read from {x.get('how', '')}. Overburden: the sea ({cfg['water_depth_m']:g} m of water at {cfg['water_density_gcc']:g} g/cm3 below an air gap of {cfg['air_gap_m']:g} m) "
        f"and the rock, its density from {', '.join(x['density_from'])}. Normal compaction trend in shale (gamma ray >= {x['gr_cutoff']}) from {x['trend']['top_tvd_m']:,.0f} to "
        f"{x['trend']['bottom_tvd_m']:,.0f} m TVD: log10 = {x['trend']['a']:.4f} {x['trend']['b_per_m']:+.3e} z, {x['trend']['points']:,} points. Normal gradient "
        f"{cfg['normal_gradient_psi_ft']:g} psi/ft. Pore pressure by Eaton (SPE-5544), exponent {x['exponent']:g}; fracture gradient by Eaton (1969) with Poisson's ratio "
        f"{cfg['poisson']:g}, and the Hubbert-Willis minimum. Declared by: {cfg.get('source') or 'the defaults'}."], [])
    c = x['curves']
    k = max(1, len(c['tvd_m']) // 60)
    s2 = CR.Section('2', 'By depth', ['Equivalent mud weights (ppg) every few tens of metres; blank where the log is not shale or not read.'], [])
    s2.tables.append(CR.Table(['TVD (m)', 'Hydrostatic', 'Pore pressure', 'Fracture (Eaton)', 'Fracture (H-W min)', 'Overburden'],
                              [[f"{c['tvd_m'][i]:,.0f}", f"{c['hyd_ppg'][i]:.2f}" if c['hyd_ppg'][i] is not None else '-',
                                f"{c['pp_ppg'][i]:.2f}" if c['pp_ppg'][i] is not None else '-', f"{c['fg_ppg'][i]:.2f}" if c['fg_ppg'][i] is not None else '-',
                                f"{c['hw_ppg'][i]:.2f}" if c['hw_ppg'][i] is not None else '-', f"{c['ob_ppg'][i]:.2f}" if c['ob_ppg'][i] is not None else '-']
                               for i in range(0, len(c['tvd_m']), k)]))
    paras = [f"{m_['label']} at {m_['tvd_m']:,.0f} m TVD: {m_['psi']:,.0f} psi ({m_['ppg']:.2f} ppg) - {m_['source']}" + (f"; computed {m_['computed_psi']:,.0f} psi" if m_.get('computed_psi') else '') for m_ in x['measured']]
    if x.get('fit_exponent'):
        paras.append(f"The Eaton exponent that best fits the measured pore pressures: {x['fit_exponent']:g}.")
    paras += [f"{f_['what']} from {f_['from_tvd_m']:,.0f} to {f_['to_tvd_m']:,.0f} m TVD." for f_ in x['flags']]
    s3 = CR.Section('3', 'Measured pressures and the mud', paras or ['No pressure measurement declared; no mud weight in the record.'], [])
    s4 = CR.Section('4', 'What this will not call a measurement', ['; '.join(r['not_a_measurement']) + '.'], [])
    return CR.Document(f"Pore pressure and fracture gradient - {x.get('display', '')}", rid, front, [s1, s2, s3, s4],
                       footer='Eaton SPE-5544 (1975), JPT 21(10) (1969); Gardner et al., Geophysics 39(6) (1974); Jorden & Shirley SPE-1407; Rehm & McClendon SPE-3601; Hubbert & Willis (1957).',
                       data={'report_id': rid, **{k_: v for k_, v in x.items() if k_ not in ('points',)}})


def write_report(ws, version: str, well: Optional[str] = None, actor: str = 'system', scene: str = 'workspace') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well, scene)
    if scene == 'example':
        d, base = ws.reports_dir, 'pore_pressure_example'
    else:
        d, base = os.path.join(ws.reports_dir, 'wells', doc.data['well']), 'pore_pressure'
    os.makedirs(d, exist_ok=True)
    paths = CR.write(doc, d, base)
    ws.audit(actor, 'report.pore_pressure', {'well': doc.data.get('well'), 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


# -- a labelled example ---------------------------------------------------------------------------------------------------
def truth(z_m: np.ndarray, rho_gcc: float = 2.3, gradient: float = 0.465, zone=(2200.0, 2600.0), frac: float = 0.4) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The overburden, normal and pore pressure (psi) built into the labelled files: rock of one density from the rig
    floor, a normal gradient, and between the zone's top and bottom a pore pressure `frac` of the way from hydrostatic
    to the overburden."""
    z = np.asarray(z_m, dtype=np.float64)
    S = rho_gcc * 1000.0 * G * z * PSI_PER_PA
    Ph = gradient * z / FT
    Pp = np.where((z >= zone[0]) & (z <= zone[1]), Ph + frac * (S - Ph), Ph)
    return S, Ph, Pp


def selftest_files(tmp: str) -> dict:
    """A depth log (sonic, deep resistivity, gamma ray and density, 500 to 3,500 m every 0.5 m) and a drilling record
    (1,000 to 3,000 m, a sample every 10 s) built so that Eaton's method returns a known pore pressure: shale on a
    normal compaction trend - sonic 10^(2.25 - 1.2e-4 z) us/ft, resistivity 10^(-0.2 + 1.5e-4 z) ohm.m, corrected
    d-exponent 10^(-0.1 + 1.0e-4 z) - and an overpressured zone from 2,200 to 2,600 m where each departs from its trend
    by exactly Eaton's ratio for a pore pressure 40 % of the way from hydrostatic to the overburden; density 2.3 g/cm3,
    a normal gradient of 0.465 psi/ft, mud of 9.5 ppg."""
    import csv
    from datetime import timedelta
    os.makedirs(tmp, exist_ok=True)
    z = np.arange(500.0, 3500.01, 0.5)
    S, Ph, Pp = truth(z)
    ratio = (S - Pp) / (S - Ph)
    dt = 10 ** (2.25 - 1.2e-4 * z) / ratio ** (1 / 3.0)
    rt = 10 ** (-0.2 + 1.5e-4 * z) * ratio ** (1 / 1.2)
    lp = os.path.join(tmp, 'pp_selftest.las')
    with open(lp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('~Version Information\nVERS.   2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0\nWRAP.   NO : ONE LINE PER DEPTH STEP\n'
                '~Well Information\nSTRT.M  500.0 :\nSTOP.M  3500.0 :\nSTEP.M  0.5 :\nNULL.  -999.25 :\nWELL.   PP-SELFTEST :\n'
                '~Curve Information\nDEPT.M  : depth\nDT  .US/F : sonic\nRDEP.OHMM : deep resistivity\nGR  .GAPI : gamma ray\nRHOB.G/C3 : bulk density\n~ASCII\n')
        for i in range(len(z)):
            f.write(f'{z[i]:.1f} {dt[i]:.4f} {rt[i]:.5f} 100.0 2.3000\n')
    norm = 0.465 * PPG_PER_PSI_FT
    zd = np.arange(1000.0, 3000.01, 0.25)
    Sd, Phd, Ppd = truth(zd)
    dcv = 10 ** (-0.1 + 1.0e-4 * zd) * ((Sd - Ppd) / (Sd - Phd)) ** (1 / 1.2)
    d = dcv * 9.5 / norm
    W, N, D = 60000.0, 150.0, 8.5
    R = 60.0 * N * 10 ** (d * math.log10(12.0 * W / (1.0e6 * D)))      # ft/h
    ep = os.path.join(tmp, 'pp_selftest_edr.csv')
    t = datetime(2026, 9, 1, 0, 0, 0)
    with open(ep, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)',
                    'On Bottom (unitless)', 'Gamma (api)', 'Mud Weight In (ppg)', 'Interpolated TVD (feet)'])
        hole = zd[0] / FT
        k = 0
        while hole < zd[-1] / FT:
            r = float(np.interp(hole * FT, zd, R))
            w.writerow([t.strftime('%Y/%m/%d'), t.strftime('%H:%M:%S'), f'{hole:.2f}', f'{hole:.2f}', f'{r:.3f}', '60.0', '150.0', '1', '100.0', '9.50', f'{hole:.2f}'])
            hole += r * 10.0 / 3600.0
            t += timedelta(seconds=10)
            k += 1
    return {'las': lp, 'edr': ep, 'zone_m': [2200.0, 2600.0], 'frac': 0.4, 'rows': k}
