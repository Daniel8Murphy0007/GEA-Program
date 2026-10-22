"""Casing design and kick tolerance: each casing string's burst, collapse and tension against the loads the hole can put
on it, and how big a gas kick each open hole can take and still be shut in without breaking down the shoe.

The pipe's own strength is API's (API TR 5C3 / ISO/TR 10400, the formulas behind the API 5CT tables), from the
outside diameter D, the wall t and the grade's minimum yield Yp:

- internal yield (burst) P = 0.875 (2 Yp t / D), the 0.875 the wall's -12.5 % manufacturing tolerance;
- collapse in four regimes by D/t - yield 2 Yp ((D/t - 1) / (D/t)^2), plastic Yp (A / (D/t) - B) - C, transition
  Yp (F / (D/t) - G) and elastic 46.95e6 / ((D/t)(D/t - 1)^2) - with A, B, C, F, G and the D/t boundaries the
  polynomials in Yp the bulletin gives;
- under axial tension the collapse is read at the reduced yield Ypa = Yp (sqrt(1 - 0.75 (Sa/Yp)^2) - 0.5 Sa/Yp) (the
  ellipse of plasticity), Sa the axial stress;
- the pipe body's yield in tension Yp pi/4 (D^2 - d^2).

The loads are the textbook ones (Bourgoyne et al., Applied Drilling Engineering, ch. 7; Rabia, Well Engineering and
Construction), on the true vertical depth z:

- burst: inside, gas to the surface from the deepest pore pressure the hole below the shoe reaches, no more than the
  fracture pressure at the shoe less the gas column above it when there is open hole below; outside, a backup of
  formation water (0.465 psi/ft unless declared). The gas is a real gas: its density p M / (z R T) at each depth's
  pressure and temperature (the surface temperature and the geothermal gradient), z by Dranchuk and Abou-Kassem's fit
  of the Standing-Katz chart on Sutton's pseudocritical properties for the declared gas gravity, the column integrated
  up the well; a gas gradient declared in psi/ft replaces it;
- collapse: outside, the mud the string was run in; inside, the string emptied (or emptied to a declared fraction of
  its depth) with the next hole's mud below the level;
- tension: the string's buoyed weight hanging from its top (the buoyancy factor 1 - MW/65.5), checked against the
  body yield with the design factor and with a declared overpull.

Each check gives the smallest safety factor (rating over load) along the string and where it falls, against the
declared design factors (1.1 burst, 1.125 collapse, 1.6 tension unless declared).

Kick tolerance, for each open hole below a shoe (a single gas bubble, the method the IADC teaches): the shoe can take
MAASP = 0.052 (FG - MW) D_shoe on top of the mud; a kick of intensity KI at the bottom shuts in at SIDPP = 0.052 KI
D_bottom; the tallest gas column the shoe can hold is H = (MAASP - SIDPP) / (mud gradient - gas gradient), no more
than the open hole. Its volume taken at the bottom (around the BHA, then the drill pipe) and at the shoe (around the
drill pipe, carried back to the bottom by Boyle's law, P_frac V_shoe = P_bottom V_bottom) - the smaller is the kick
tolerance, in barrels.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

GRADES: Dict[str, float] = {'H40': 40000.0, 'J55': 55000.0, 'K55': 55000.0, 'M65': 65000.0, 'N80': 80000.0, 'L80': 80000.0, 'C90': 90000.0,
                            'R95': 95000.0, 'T95': 95000.0, 'C110': 110000.0, 'P110': 110000.0, 'Q125': 125000.0}
# the wall (in) of the common API 5CT sizes by outside diameter (in) and nominal weight (lb/ft, threaded and coupled)
WALLS: Dict[float, Dict[float, float]] = {
    4.5: {9.5: 0.205, 10.5: 0.224, 11.6: 0.250, 13.5: 0.290, 15.1: 0.337},
    5.0: {11.5: 0.220, 13.0: 0.253, 15.0: 0.296, 18.0: 0.362, 21.4: 0.437},
    5.5: {14.0: 0.244, 15.5: 0.275, 17.0: 0.304, 20.0: 0.361, 23.0: 0.415},
    7.0: {17.0: 0.231, 20.0: 0.272, 23.0: 0.317, 26.0: 0.362, 29.0: 0.408, 32.0: 0.453, 35.0: 0.498, 38.0: 0.540},
    7.625: {26.4: 0.328, 29.7: 0.375, 33.7: 0.430, 39.0: 0.500},
    8.625: {24.0: 0.264, 28.0: 0.304, 32.0: 0.352, 36.0: 0.400, 40.0: 0.450, 44.0: 0.500, 49.0: 0.557},
    9.625: {32.3: 0.312, 36.0: 0.352, 40.0: 0.395, 43.5: 0.435, 47.0: 0.472, 53.5: 0.545},
    10.75: {32.75: 0.279, 40.5: 0.350, 45.5: 0.400, 51.0: 0.450, 55.5: 0.495},
    13.375: {48.0: 0.330, 54.5: 0.380, 61.0: 0.430, 68.0: 0.480, 72.0: 0.514},
    16.0: {65.0: 0.375, 75.0: 0.438, 84.0: 0.495},
    18.625: {87.5: 0.435},
    20.0: {94.0: 0.438, 106.5: 0.500, 133.0: 0.635},
}
DEFAULTS = {'strings': [], 'td_md_ft': None, 'td_hole_in': None, 'td_mw_ppg': None, 'pp_ppg': None, 'fg_ppg': None, 'kick_intensity_ppg': 0.5,
            'gas_gradient_psi_ft': None, 'gas_sg': 0.65, 'surface_temp_f': 70.0, 'geothermal_f_100ft': 1.5, 'backup_gradient_psi_ft': 0.465, 'evacuation': 1.0, 'df_burst': 1.1, 'df_collapse': 1.125, 'df_tension': 1.6,
            'overpull_klbf': 100.0, 'bha_od_in': None, 'bha_length_ft': None, 'dp_od_in': None, 'step_ft': 100.0}
LIMITS = {'td_md_ft': (100.0, 40000.0), 'td_hole_in': (2.0, 40.0), 'td_mw_ppg': (6.0, 22.0), 'pp_ppg': (6.0, 22.0), 'fg_ppg': (6.0, 25.0),
          'kick_intensity_ppg': (0.0, 5.0), 'gas_gradient_psi_ft': (0.0, 0.3), 'gas_sg': (0.55, 1.5), 'surface_temp_f': (-20.0, 150.0),
          'geothermal_f_100ft': (0.5, 4.0), 'backup_gradient_psi_ft': (0.0, 0.6), 'evacuation': (0.0, 1.0),
          'df_burst': (1.0, 2.0), 'df_collapse': (0.8, 2.0), 'df_tension': (1.0, 3.0), 'overpull_klbf': (0.0, 1000.0), 'bha_od_in': (2.0, 20.0),
          'bha_length_ft': (0.0, 5000.0), 'dp_od_in': (2.0, 10.0), 'step_ft': (20.0, 1000.0)}
NOT_A_MEASUREMENT = [
    "the ratings: API's formulas on the declared size, weight, wall and grade (the pipe body's; not the connection's, not a mill's test, not a "
    "string worn by drilling)",
    "the loads: the textbook cases on the declared mud and the pore pressure and fracture gradient (the Pore pressure tab's, or declared), "
    "not a well's measured pressures; the temperature and the cement are not in them",
    "tension: the buoyed weight on the measured length, hanging straight; the drag of a deviated hole and the bending in a dogleg are not added",
    "kick tolerance: a single bubble of gas, a sweet natural gas of the declared gravity (no correction for CO2, H2S or nitrogen), at the "
    "declared geothermal temperature, not the circulating one; the volume a well can take, not one it has taken",
]
BBL_PER_IN2_FT = 1.0 / 1029.4          # annular capacity: (D^2 - d^2) / 1029.4 bbl per ft


# -- the pipe's strength ---------------------------------------------------------------------------------------------------
def size_in(x) -> float:
    """An outside diameter in inches: 9.625, '9.625', '9-5/8', '9 5/8', '13-3/8 in'."""
    s = str(x).strip().lower().replace('"', '').replace('in', '').strip()
    m = re.fullmatch(r'(\d+)(?:[-\s]+(\d+)/(\d+))?', s)
    if m and m.group(2):
        return round(int(m.group(1)) + int(m.group(2)) / int(m.group(3)), 4)
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f'a diameter is inches, 9.625 or 9-5/8, not {x!r}') from None
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f'a diameter is inches, 9.625 or 9-5/8, not {x!r}')
    return v


def wall_for(od_in: float, weight_lbft: float) -> Optional[float]:
    for od, tab in WALLS.items():
        if abs(od - od_in) < 0.002:
            for w, t in tab.items():
                if abs(w - weight_lbft) < 0.01:
                    return t
    return None


def coefficients(yp: float) -> dict:
    """API TR 5C3's empirical collapse factors for a minimum yield Yp (psi), and the D/t boundaries of the regimes."""
    A = 2.8762 + 0.10679e-5 * yp + 0.21301e-10 * yp ** 2 - 0.53132e-16 * yp ** 3
    B = 0.026233 + 0.50609e-6 * yp
    C = -465.93 + 0.030867 * yp - 0.10483e-7 * yp ** 2 + 0.36989e-13 * yp ** 3
    ba = B / A
    r = 3 * ba / (2 + ba)
    F = 46.95e6 * r ** 3 / (yp * (r - ba) * (1 - r) ** 2)
    G = F * B / A
    dt_yp = (math.sqrt((A - 2) ** 2 + 8 * (B + C / yp)) + (A - 2)) / (2 * (B + C / yp))
    dt_pt = yp * (A - F) / (C + yp * (B - G))
    dt_te = (2 + ba) / (3 * ba)
    return {'A': A, 'B': B, 'C': C, 'F': F, 'G': G, 'dt_yp': dt_yp, 'dt_pt': dt_pt, 'dt_te': dt_te}


def collapse(od_in: float, wall_in: float, yp: float) -> Tuple[float, str]:
    """The collapse resistance (psi) and the regime it falls in."""
    k = coefficients(yp)
    dt = od_in / wall_in
    if dt <= k['dt_yp']:
        return 2 * yp * (dt - 1) / dt ** 2, 'yield'
    if dt <= k['dt_pt']:
        return yp * (k['A'] / dt - k['B']) - k['C'], 'plastic'
    if dt <= k['dt_te']:
        return yp * (k['F'] / dt - k['G']), 'transition'
    return 46.95e6 / (dt * (dt - 1) ** 2), 'elastic'


def burst(od_in: float, wall_in: float, yp: float) -> float:
    return 0.875 * 2 * yp * wall_in / od_in


def area_in2(od_in: float, wall_in: float) -> float:
    return math.pi / 4 * (od_in ** 2 - (od_in - 2 * wall_in) ** 2)


def body_yield_lbf(od_in: float, wall_in: float, yp: float) -> float:
    return yp * area_in2(od_in, wall_in)


def reduced_yield(yp: float, sa_psi: float) -> float:
    """The yield collapse is read at under an axial tension stress Sa (the ellipse of plasticity); compression is not credited."""
    q = max(0.0, sa_psi) / yp
    return yp * (math.sqrt(max(0.0, 1 - 0.75 * q * q)) - 0.5 * q)


def collapse_with_tension(od_in: float, wall_in: float, yp: float, sa_psi: float) -> float:
    ya = reduced_yield(yp, sa_psi)
    return collapse(od_in, wall_in, ya)[0] if ya > 1000.0 else 0.0


def _r10(x: float) -> int:
    """To the nearest 10 psi, as the API tables give a rating."""
    return int(math.floor(x / 10.0 + 0.5)) * 10


# -- the declared strings ----------------------------------------------------------------------------------------------------
def check_strings(rows: List[dict]) -> List[dict]:
    """Each string: what, od_in, weight_lbft, grade, wall_in (or from the API table), top_md_ft, shoe_md_ft, hole_in, mw_ppg."""
    out = []
    for i, r in enumerate(rows):
        n = f"string {i + 1} ({r.get('what') or 'unnamed'})"
        what = str(r.get('what') or '').strip()
        if not what:
            raise ValueError(f'{n}: name it (e.g. "9-5/8 in intermediate")')
        try:
            od = size_in(r.get('od_in'))
            ppf = float(r.get('weight_lbft'))
            top = float(r.get('top_md_ft') or 0.0)
            shoe = float(r.get('shoe_md_ft'))
            hole = size_in(r.get('hole_in'))
            mw = float(r.get('mw_ppg'))
        except (TypeError, ValueError) as e:
            raise ValueError(f'{n}: the outside diameter, the weight (lb/ft), the top and the shoe (ft MD), the hole (in) and the mud (ppg) are numbers'
                             + (f' - {e}' if 'diameter' in str(e) else '')) from None
        grade = str(r.get('grade') or '').strip().upper().replace('-', '')
        if grade not in GRADES:
            raise ValueError(f"{n}: the grade is one of {', '.join(GRADES)}, not {r.get('grade')!r}")
        given = r.get('wall_in') not in (None, '') and r.get('wall_from') != 'the API table'     # a checked row passed again keeps where its wall came from
        wall = float(r['wall_in']) if given else wall_for(od, ppf)
        if wall is None:
            raise ValueError(f'{n}: {od:g} in {ppf:g} lb/ft is not in the program\'s table of API sizes - give the wall (in) from the pipe\'s data sheet')
        if not (2.0 <= od <= 30.0 and 0.1 <= wall <= od / 4 and 2.0 <= ppf <= 250.0):
            raise ValueError(f'{n}: the size, weight or wall is out of range')
        plain = 10.69 * (od - wall) * wall
        if abs(plain - ppf) / ppf > 0.12:
            raise ValueError(f'{n}: a {od:g} in pipe with a {wall:g} in wall weighs about {plain:.1f} lb/ft plain-end, not {ppf:g} - check the weight and the wall')
        if not (0.0 <= top < shoe <= 40000.0):
            raise ValueError(f'{n}: the top must be above the shoe, both in ft MD (0 for a string to the surface)')
        if not (od < hole <= 40.0):
            raise ValueError(f'{n}: the hole ({hole:g} in) must be larger than the pipe ({od:g} in)')
        if not (6.0 <= mw <= 22.0):
            raise ValueError(f'{n}: the mud it was run in is 6 to 22 ppg')
        yp = GRADES[grade]
        cp, regime = collapse(od, wall, yp)
        out.append({'what': what, 'od_in': od, 'weight_lbft': ppf, 'grade': grade, 'wall_in': wall, 'id_in': round(od - 2 * wall, 3),
                    'top_md_ft': top, 'shoe_md_ft': shoe, 'hole_in': hole, 'mw_ppg': mw, 'yield_psi': yp,
                    'burst_psi': _r10(burst(od, wall, yp)), 'collapse_psi': _r10(cp), 'collapse_regime': regime,
                    'body_yield_klbf': round(body_yield_lbf(od, wall, yp) / 1000.0, 1), 'd_over_t': round(od / wall, 2),
                    'wall_from': 'declared' if given else 'the API table'})
    out.sort(key=lambda x: x['shoe_md_ft'])
    for a, b in zip(out, out[1:]):
        if b['od_in'] >= a['id_in']:
            raise ValueError(f"{b['what']} ({b['od_in']:g} in) does not pass through {a['what']} (bore {a['id_in']:g} in)")
        if b['shoe_md_ft'] <= a['shoe_md_ft']:
            raise ValueError(f"two shoes at the same depth ({a['shoe_md_ft']:g} ft)")
    return out


def parse_strings(text: str) -> List[dict]:
    """'what:od:weight:grade:top-shoe:hole:mw[:wall]; ...' e.g. 'surface:13-3/8:54.5:J55:0-1500:17.5:9.0; intermediate:9-5/8:47:N80:0-7000:12.25:10'."""
    rows = []
    for part in [p.strip() for p in (text or '').split(';') if p.strip()]:
        f = [x.strip() for x in part.split(':')]
        if len(f) not in (7, 8):
            raise ValueError(f'a string is what:od:weight:grade:top-shoe:hole:mw[:wall] (e.g. surface:13-3/8:54.5:J55:0-1500:17.5:9.0), not {part!r}')
        top, _, shoe = f[4].partition('-')
        if not shoe:
            top, shoe = '0', f[4]
        rows.append({'what': f[0], 'od_in': f[1], 'weight_lbft': f[2], 'grade': f[3], 'top_md_ft': top, 'shoe_md_ft': shoe, 'hole_in': f[5], 'mw_ppg': f[6],
                     'wall_in': f[7] if len(f) == 8 and f[7] else None})
    return check_strings(rows)


def strings_text(strings: List[dict]) -> str:
    def g(v):
        return f'{v:g}'
    return '; '.join(f"{s['what']}:{g(s['od_in'])}:{g(s['weight_lbft'])}:{s['grade']}:{g(s['top_md_ft'])}-{g(s['shoe_md_ft'])}:{g(s['hole_in'])}:{g(s['mw_ppg'])}"
                     + (f":{g(s['wall_in'])}" if s.get('wall_from') == 'declared' else '') for s in strings)


# -- the store ------------------------------------------------------------------------------------------------------------------
def _store_path(ws) -> str:
    return os.path.join(ws.path, 'drillstring', 'casing_design.json')


def _load(ws) -> dict:
    try:
        with open(_store_path(ws), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wells': {}}


def well_config(ws, well_id: str) -> dict:
    return {**DEFAULTS, **((_load(ws).get('wells') or {}).get(well_id) or {})}


def set_well(ws, actor: str, well_id: str, source: str = '', strings=None, **vals) -> dict:
    """Declare a well's casing program (the strings, shallowest shoe first) and the loads' terms."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    d = _load(ws)
    cur = dict((d.get('wells') or {}).get(well_id) or {})
    changed = False
    if strings is not None:
        cur['strings'] = parse_strings(strings) if isinstance(strings, str) else check_strings([dict(x) for x in strings]); changed = True
    for key, v in vals.items():
        if key not in LIMITS:
            raise ValueError(f'unknown setting {key}')
        if v in (None, ''):
            continue
        if str(v).strip().lower() == 'none' and DEFAULTS.get(key) is None:
            cur[key] = None; changed = True
            continue
        x = size_in(v) if key in ('td_hole_in', 'bha_od_in', 'dp_od_in') else float(v)
        lo, hi = LIMITS[key]
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{key} must be between {lo:g} and {hi:g}')
        cur[key] = x; changed = True
    if changed:
        if not (source or '').strip():
            raise ValueError('say where the casing program came from (e.g. "drilling program rev 2, casing tally")')
        cur['source'] = source.strip()
        if strings is not None:
            cur['strings_source'] = source.strip()
    cur['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'); cur['by'] = actor
    d.setdefault('wells', {})[well_id] = cur
    os.makedirs(os.path.dirname(_store_path(ws)), exist_ok=True)
    with open(_store_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(_store_path(ws) + '.tmp', _store_path(ws))
    ws.audit(actor, 'casing_design.declare', {'well': well_id, **{k: v for k, v in cur.items() if k not in ('utc', 'by')}})
    return well_config(ws, well_id)


# -- the gas: a real gas at the well's pressure and temperature -----------------------------------------------------------------
SUTTON_TPC = (169.2, 349.5, -74.0)        # deg R: Tpc = a + b sg + c sg^2 (Sutton, SPE 14265, 1985)
SUTTON_PPC = (756.8, -131.0, -3.6)        # psia
DAK = (0.3265, -1.0700, -0.5339, 0.01569, -0.05165, 0.5475, -0.7361, 0.1844, 0.1056, 0.6134, 0.7210)   # Dranchuk and Abou-Kassem, 1975
R_GAS = 10.7316                           # psia ft3 / (lb-mol deg R)
M_AIR = 28.97                             # lb / lb-mol
ATM = 14.696                              # psi


def pseudo_critical(sg: float) -> Tuple[float, float]:
    """Sutton's pseudocritical temperature (deg R) and pressure (psia) of a sweet natural gas of specific gravity sg."""
    return (SUTTON_TPC[0] + SUTTON_TPC[1] * sg + SUTTON_TPC[2] * sg * sg, SUTTON_PPC[0] + SUTTON_PPC[1] * sg + SUTTON_PPC[2] * sg * sg)


def z_factor(tpr: float, ppr: float) -> float:
    """The gas deviation factor by Dranchuk and Abou-Kassem's fit of the Standing-Katz chart: the reduced density rho_r
    solving 0 = 1 + C1 rho + C2 rho^2 - C3 rho^5 + C4 (1 + A11 rho^2) rho^2 exp(-A11 rho^2) - 0.27 Ppr / (Tpr rho),
    found by Newton's method from the ideal gas; z = 0.27 Ppr / (Tpr rho_r)."""
    if ppr <= 1e-9:
        return 1.0
    A = DAK
    c1 = A[0] + A[1] / tpr + A[2] / tpr ** 3 + A[3] / tpr ** 4 + A[4] / tpr ** 5
    c2 = A[5] + A[6] / tpr + A[7] / tpr ** 2
    c3 = A[8] * (A[6] / tpr + A[7] / tpr ** 2)
    c4 = A[9] / tpr ** 3

    def f(r):
        e = math.exp(-A[10] * r * r)
        return 1 + c1 * r + c2 * r * r - c3 * r ** 5 + c4 * (1 + A[10] * r * r) * r * r * e - 0.27 * ppr / (tpr * r)

    def df(r):
        e = math.exp(-A[10] * r * r)
        return (c1 + 2 * c2 * r - 5 * c3 * r ** 4 + 2 * c4 * r * e * (1 + 2 * A[10] * r * r - A[10] * r * r * (1 + A[10] * r * r))
                + 0.27 * ppr / (tpr * r * r))
    r = 0.27 * ppr / tpr
    for _ in range(100):
        step = f(r) / df(r)
        nr = r - step
        if nr <= 0:
            nr = r / 2
        if abs(nr - r) < 1e-12:
            r = nr
            break
        r = nr
    if not (math.isfinite(r) and r > 0 and abs(f(r)) < 1e-8):
        lo, hi = 1e-6, 3.0                      # the gas root lies where f rises through zero; bisect on it
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
        r = 0.5 * (lo + hi)
    return 0.27 * ppr / (tpr * r)


def gas_state(p_psig: float, t_f: float, sg: float) -> dict:
    """The gas at a gauge pressure and a temperature: z, its density (lb/ft3, ppg) and its gradient (psi/ft)."""
    tpc, ppc = pseudo_critical(sg)
    pa, tr = max(0.0, p_psig) + ATM, t_f + 459.67
    z = z_factor(tr / tpc, pa / ppc)
    rho = pa * M_AIR * sg / (z * R_GAS * tr)
    return {'z': z, 'rho_lb_ft3': rho, 'ppg': rho / 7.48052, 'psi_ft': rho / 144.0, 'p_psia': pa, 't_r': tr}


def temperature_f(cfg: dict, tvd_ft: float) -> float:
    return float(cfg['surface_temp_f']) + float(cfg['geothermal_f_100ft']) * max(0.0, tvd_ft) / 100.0


def real_gas(cfg: dict) -> bool:
    return cfg.get('gas_gradient_psi_ft') in (None, '')


def gas_column(cfg: dict, p0_psig: float, z0_ft: float, z_ft: float, step_ft: float = 50.0) -> float:
    """The pressure at z_ft in a column of gas whose pressure at z0_ft is p0_psig: the declared gradient, or the real gas's
    own weight integrated along the geothermal temperature (fourth-order Runge-Kutta, 50 ft steps)."""
    if not real_gas(cfg):
        return p0_psig - float(cfg['gas_gradient_psi_ft']) * (z0_ft - z_ft)
    sg = float(cfg['gas_sg'])
    n = max(1, int(math.ceil(abs(z0_ft - z_ft) / step_ft)))
    h = (z_ft - z0_ft) / n

    def g(z, p):
        return gas_state(p, temperature_f(cfg, z), sg)['psi_ft']
    z, p = z0_ft, p0_psig
    for _ in range(n):
        k1 = g(z, p); k2 = g(z + h / 2, p + h * k1 / 2); k3 = g(z + h / 2, p + h * k2 / 2); k4 = g(z + h, p + h * k3)
        p += h * (k1 + 2 * k2 + 2 * k3 + k4) / 6
        z += h
    return p


def _bubble(cfg: dict, gm: float, maasp: float, sidpp: float, p_anchor: float, z_anchor: float, down: bool) -> dict:
    """A gas bubble whose top (down=True: at the shoe, the anchor) or bottom (down=False: at the bottom of the hole) is at the
    anchor pressure: its height H = (MAASP - SIDPP) / (mud gradient - gas gradient), the gas gradient taken at the bubble's
    middle, found by fixed point; its middle's pressure, temperature and z."""
    gg = float(cfg['gas_gradient_psi_ft']) if not real_gas(cfg) else 0.1
    st = None
    for _ in range(30):
        h = (maasp - sidpp) / (gm - gg) if gm > gg else -1.0
        hm = max(0.0, h) / 2
        zm = z_anchor + hm if down else z_anchor - hm
        pm = p_anchor + gg * hm if down else p_anchor - gg * hm
        if not real_gas(cfg):
            break
        st = gas_state(pm, temperature_f(cfg, zm), float(cfg['gas_sg']))
        if abs(st['psi_ft'] - gg) < 1e-7:
            gg = st['psi_ft']
            break
        gg = st['psi_ft']
    h = (maasp - sidpp) / (gm - gg) if gm > gg else -1.0
    return {'h': h, 'gg': gg, 'mid_p_psig': pm, 'mid_tvd_ft': zm, 'state': st}


# -- the loads -------------------------------------------------------------------------------------------------------------------
def _section_below(cfg: dict, i: int) -> Optional[dict]:
    """The hole drilled below string i's shoe: to the next string's shoe (its hole, its mud), or for the deepest to the
    declared TD; None when nothing is drilled below."""
    st = cfg['strings']
    if i + 1 < len(st):
        n = st[i + 1]
        return {'bottom_md_ft': n['shoe_md_ft'], 'hole_in': n['hole_in'], 'mw_ppg': n['mw_ppg'], 'what': f"the {n['hole_in']:g} in hole for {n['what']}"}
    td = cfg.get('td_md_ft')
    if td and td > st[i]['shoe_md_ft'] + 1.0:
        return {'bottom_md_ft': float(td), 'hole_in': cfg.get('td_hole_in'), 'mw_ppg': cfg.get('td_mw_ppg') or st[i]['mw_ppg'], 'what': 'the hole to TD'}
    return None


def _pore(win, cfg, tvd: float, mw: float) -> Tuple[float, str]:
    from .hydraulics import _at
    pp, _ = _at(win, tvd)
    if pp is not None:
        return pp, 'the window'
    return mw + float(cfg['kick_intensity_ppg']), f"the hole's mud plus the kick intensity ({cfg['kick_intensity_ppg']:g} ppg)"


def _fg(win, tvd: float) -> Optional[float]:
    from .hydraulics import _at
    return _at(win, tvd)[1]


def gas_how(cfg: dict) -> str:
    if real_gas(cfg):
        return (f"a natural gas of specific gravity {float(cfg['gas_sg']):g} as a real gas (z by Dranchuk and Abou-Kassem, pseudocritical properties by "
                f"Sutton) at the well's temperature, {float(cfg['surface_temp_f']):g} F at the surface rising {float(cfg['geothermal_f_100ft']):g} F per 100 ft")
    return f"a gas gradient of {float(cfg['gas_gradient_psi_ft']):g} psi/ft as declared"


def _column_at(cfg: dict, p0: float, z0: float, zs: List[float]) -> List[float]:
    """The gas column's pressure at each of the TVDs zs (shallower than z0), from p0 at z0: integrated once, upward, through them."""
    order = sorted(range(len(zs)), key=lambda k: -zs[k])
    out = [0.0] * len(zs)
    z, p = z0, p0
    for k in order:
        p = gas_column(cfg, p, z, min(zs[k], z)) if zs[k] < z else p
        z = min(zs[k], z)
        out[k] = p if zs[k] <= z0 else p0
    return out


def string_loads(cfg: dict, i: int, tvd_of, win) -> dict:
    s = cfg['strings'][i]
    gb = float(cfg['backup_gradient_psi_ft'])
    below = _section_below(cfg, i)
    ds = tvd_of(s['shoe_md_ft'])
    open_below = below is not None
    if open_below:
        dres = tvd_of(below['bottom_md_ft'])
        pp, pp_how = _pore(win, cfg, dres, below['mw_ppg'])
        fg_shoe = _fg(win, ds)
    else:
        dres = ds
        pp, pp_how = _pore(win, cfg, dres, s['mw_ppg'])
        fg_shoe = None
    p_res = 0.052 * pp * dres
    p_frac = 0.052 * fg_shoe * ds if fg_shoe is not None else None
    mw_in = below['mw_ppg'] if open_below else s['mw_ppg']
    bf = 1.0 - s['mw_ppg'] / 65.5
    area = area_in2(s['od_in'], s['wall_in'])
    step = float(cfg.get('step_ft') or 100.0)
    mds = sorted(set([s['top_md_ft'], s['shoe_md_ft']] + [float(x) for x in np.arange(math.ceil(s['top_md_ft'] / step) * step, s['shoe_md_ft'], step)]))
    rows = []
    level = float(cfg['evacuation']) * ds
    zs = [tvd_of(md) for md in mds]
    col_res = _column_at(cfg, p_res, dres, zs)
    col_frac = _column_at(cfg, p_frac, ds, zs) if p_frac is not None else None
    for k, md in enumerate(mds):
        z = zs[k]
        pin_b = col_res[k]
        limited = False
        if col_frac is not None and col_frac[k] < pin_b:
            pin_b, limited = col_frac[k], True
        pout_b = gb * z
        net_b = pin_b - pout_b
        pout_c = 0.052 * s['mw_ppg'] * z
        pin_c = 0.052 * mw_in * max(0.0, z - level)
        net_c = pout_c - pin_c
        axial = bf * s['weight_lbft'] * (s['shoe_md_ft'] - md)
        sa = axial / area
        c_rating = collapse_with_tension(s['od_in'], s['wall_in'], s['yield_psi'], sa)
        rows.append({'md_ft': round(md, 1), 'tvd_ft': round(z, 1), 'burst_in_psi': round(pin_b), 'burst_out_psi': round(pout_b), 'burst_net_psi': round(net_b),
                     'burst_limited_by_shoe': limited, 'collapse_out_psi': round(pout_c), 'collapse_in_psi': round(pin_c), 'collapse_net_psi': round(net_c),
                     'axial_klbf': round(axial / 1000.0, 1), 'axial_stress_psi': round(sa), 'collapse_rating_psi': round(c_rating),
                     'sf_burst': round(s['burst_psi'] / net_b, 3) if net_b > 1.0 else None,
                     'sf_collapse': round(c_rating / net_c, 3) if net_c > 1.0 else None})

    def least(key):
        v = [(r[key], r['md_ft']) for r in rows if r[key] is not None]
        return {'sf': min(v)[0], 'md_ft': min(v)[1]} if v else None
    top_load = bf * s['weight_lbft'] * (s['shoe_md_ft'] - s['top_md_ft'])
    by = s['body_yield_klbf'] * 1000.0
    need = max(float(cfg['df_tension']) * top_load, top_load + float(cfg['overpull_klbf']) * 1000.0)
    lb, lc = least('sf_burst'), least('sf_collapse')
    sf_t = round(by / top_load, 3) if top_load > 0 else None
    checks = {
        'burst': {'sf': lb['sf'] if lb else None, 'md_ft': lb['md_ft'] if lb else None, 'df': cfg['df_burst'],
                  'ok': (lb is None) or lb['sf'] >= cfg['df_burst']},
        'collapse': {'sf': lc['sf'] if lc else None, 'md_ft': lc['md_ft'] if lc else None, 'df': cfg['df_collapse'],
                     'ok': (lc is None) or lc['sf'] >= cfg['df_collapse']},
        'tension': {'sf': sf_t, 'md_ft': s['top_md_ft'], 'df': cfg['df_tension'], 'load_klbf': round(top_load / 1000.0, 1),
                    'needed_klbf': round(need / 1000.0, 1), 'overpull_klbf': cfg['overpull_klbf'], 'ok': by >= need},
    }
    return {'string': s, 'below': below, 'shoe_tvd_ft': round(ds, 1), 'reservoir_tvd_ft': round(dres, 1), 'pore_ppg': round(pp, 3), 'pore_how': pp_how,
            'fg_shoe_ppg': round(fg_shoe, 3) if fg_shoe is not None else None, 'mud_inside_ppg': mw_in, 'buoyancy_factor': round(bf, 4),
            'rows': rows, 'checks': checks, 'ok': all(c['ok'] for c in checks.values()),
            'gas': gas_how(cfg),
            'burst_case': ('gas to the surface from the pore pressure at the bottom of the hole below (' + pp_how + ')'
                           + (', no more than the fracture pressure at the shoe less the gas above it' if p_frac is not None else '')
                           if open_below else 'gas to the surface from the pore pressure at the shoe (' + pp_how + ')'),
            'collapse_case': (f"the {s['mw_ppg']:g} ppg mud it was run in outside; inside emptied "
                              + ('to the shoe' if cfg['evacuation'] >= 1.0 else f"to {cfg['evacuation'] * 100:.0f} % of its vertical depth, {mw_in:g} ppg mud below"))}


def _capacity_profile(cfg: dict, hole_in: float, bottom_md: float):
    """Annular capacity (bbl/ft) at a measured depth: around the BHA over its length above the bit, the drill pipe above."""
    bha, L, dp = cfg.get('bha_od_in'), cfg.get('bha_length_ft'), cfg.get('dp_od_in')
    if not (bha and L is not None and dp and hole_in):
        return None

    def cap(md):
        od = bha if md >= bottom_md - L else dp
        return max(0.0, hole_in ** 2 - od ** 2) * BBL_PER_IN2_FT
    return cap


def _volume(cap, tvd_of, md_from: float, md_to: float, n: int = 400) -> float:
    if md_to <= md_from:
        return 0.0
    m = np.linspace(md_from, md_to, n + 1)
    c = np.array([cap(x) for x in m])
    return float(np.trapezoid(c, m) if hasattr(np, 'trapezoid') else np.trapz(c, m))


def _md_at_tvd(tvd_of, z: float, lo: float, hi: float) -> float:
    """The measured depth between lo and hi at which the path reaches a TVD (bisection; TVD never falls along MD)."""
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if tvd_of(mid) < z:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def kick_tolerance(cfg: dict, i: int, tvd_of, win) -> Optional[dict]:
    s = cfg['strings'][i]
    below = _section_below(cfg, i)
    if below is None:
        return None
    ds, md_s = tvd_of(s['shoe_md_ft']), s['shoe_md_ft']
    md_b = below['bottom_md_ft']
    dt = tvd_of(md_b)
    mw = float(below['mw_ppg'])
    fg = _fg(win, ds)
    from .hydraulics import _at
    pp_win, _ = _at(win, dt)
    ki = float(cfg['kick_intensity_ppg'])
    pp = max(mw + ki, pp_win) if pp_win is not None else mw + ki
    out = {'shoe_what': s['what'], 'shoe_md_ft': md_s, 'shoe_tvd_ft': round(ds, 1), 'bottom_md_ft': md_b, 'bottom_tvd_ft': round(dt, 1), 'mw_ppg': mw,
           'kick_ppg': round(pp, 3), 'kick_how': ('the window\'s pore pressure at the bottom' if pp_win is not None and pp_win > mw + ki
                                                    else f'the mud plus a kick intensity of {ki:g} ppg'),
           'hole_in': below['hole_in'], 'fg_shoe_ppg': round(fg, 3) if fg is not None else None, 'status': 'OK'}
    if fg is None:
        out.update({'status': 'NOT KNOWN', 'detail': 'the fracture gradient at the shoe is not known (the Pore pressure tab, or declare one)'})
        return out
    gm = 0.052 * mw
    maasp = 0.052 * (fg - mw) * ds
    sidpp = 0.052 * (pp - mw) * dt
    p_frac, p_bot = 0.052 * fg * ds, 0.052 * pp * dt
    out.update({'maasp_psi': round(maasp), 'sidpp_psi': round(sidpp), 'gas': gas_how(cfg)})
    bot = _bubble(cfg, gm, maasp, sidpp, p_bot, dt, down=False)        # the bubble on bottom, as it is shut in
    sho = _bubble(cfg, gm, maasp, sidpp, p_frac, ds, down=True)        # the bubble circulated up to the shoe
    out.update({'gas_gradient_bottom_psi_ft': round(bot['gg'], 6), 'gas_gradient_shoe_psi_ft': round(sho['gg'], 6)})
    if real_gas(cfg):
        out.update({'z_bottom': round(bot['state']['z'], 4), 'z_shoe': round(sho['state']['z'], 4),
                    'temp_bottom_f': round(temperature_f(cfg, bot['mid_tvd_ft']), 1), 'temp_shoe_f': round(temperature_f(cfg, sho['mid_tvd_ft']), 1)})
    if gm <= max(bot['gg'], sho['gg']):
        out.update({'status': 'NOT KNOWN', 'detail': 'the mud is no heavier than the gas'})
        return out
    open_h = dt - ds
    hb, hs = min(bot['h'], open_h), min(sho['h'], open_h)
    out['height_ft'] = round(max(0.0, hb), 1)
    out['height_shoe_ft'] = round(max(0.0, hs), 1)
    out['height_capped'] = bot['h'] > open_h
    if bot['h'] <= 0:
        out.update({'volume_bbl': 0.0, 'detail': 'the shoe cannot hold the shut-in pressure of a kick of this intensity: no kick tolerance'})
        return out
    cap = _capacity_profile(cfg, below['hole_in'] or 0.0, md_b)
    if cap is None:
        out.update({'status': 'HEIGHT ONLY', 'detail': 'declare the BHA (outside diameter, length) and the drill pipe (outside diameter) for the volume'})
        return out
    md_top_bottom = _md_at_tvd(tvd_of, dt - hb, md_s, md_b)
    v_bottom = _volume(cap, tvd_of, md_top_bottom, md_b)
    md_low_shoe = _md_at_tvd(tvd_of, ds + max(0.0, hs), md_s, md_b)
    v_shoe = _volume(cap, tvd_of, md_s, md_low_shoe)
    if real_gas(cfg):                                  # the same moles of gas: p V / (z T) at the shoe's middle and at the bottom's
        sb, ss = bot['state'], sho['state']
        v_shoe_at_bottom = v_shoe * (ss['p_psia'] / (ss['z'] * ss['t_r'])) / (sb['p_psia'] / (sb['z'] * sb['t_r']))
        law = "the real-gas law (p V / z T) from the shoe's conditions to the bottom's"
    else:
        v_shoe_at_bottom = v_shoe * p_frac / p_bot
        law = "Boyle's law (p V) from the fracture pressure at the shoe to the pressure at the bottom"
    out.update({'volume_at_bottom_bbl': round(v_bottom, 1), 'volume_at_shoe_bbl': round(v_shoe, 1), 'volume_shoe_as_bottom_bbl': round(v_shoe_at_bottom, 1),
                'volume_bbl': round(min(v_bottom, v_shoe_at_bottom), 1), 'carried_by': law,
                'governs': 'the bubble at the bottom' if v_bottom <= v_shoe_at_bottom else 'the bubble at the shoe'})
    return out


def analyse(cfg: dict, tvd_of, win, path_how: str = '', window_how: str = '') -> dict:
    if not cfg.get('strings'):
        raise ValueError('declare the casing strings: what:od:weight:grade:top-shoe:hole:mw for each, shallowest shoe first')
    strings = [string_loads(cfg, i, tvd_of, win) for i in range(len(cfg['strings']))]
    kicks = [k for k in (kick_tolerance(cfg, i, tvd_of, win) for i in range(len(cfg['strings']))) if k]
    if float(cfg['kick_intensity_ppg']) > 0:          # a swabbed kick: gas pulled in with no underbalance
        sw = {k['shoe_md_ft']: k for k in (kick_tolerance({**cfg, 'kick_intensity_ppg': 0.0}, i, tvd_of, win) for i in range(len(cfg['strings']))) if k}
        for k in kicks:
            s_ = sw.get(k['shoe_md_ft']) or {}
            k['swabbed'] = {x: s_.get(x) for x in ('kick_ppg', 'kick_how', 'sidpp_psi', 'height_ft', 'volume_bbl', 'status', 'detail', 'governs')}
    return {'strings': strings, 'kicks': kicks, 'ok': all(s['ok'] for s in strings), 'config': cfg, 'path_how': path_how, 'window_how': window_how}


# -- the example, the page, the report --------------------------------------------------------------------------------------------------
EXAMPLE_WINDOW = {'tvd_ft': [0.0, 4000.0, 7000.0, 10000.0], 'pp_ppg': [8.6, 8.6, 9.4, 11.0], 'fg_ppg': [12.5, 14.2, 15.0, 15.8]}


def example() -> dict:
    """A vertical 10,000 ft well with a casing program and a window declared for the example (not a real well's)."""
    cfg = {**DEFAULTS, 'strings': check_strings([
        {'what': '13-3/8 in surface (example)', 'od_in': '13-3/8', 'weight_lbft': 54.5, 'grade': 'J55', 'top_md_ft': 0, 'shoe_md_ft': 1500, 'hole_in': 17.5, 'mw_ppg': 9.0},
        {'what': '9-5/8 in intermediate (example)', 'od_in': '9-5/8', 'weight_lbft': 47, 'grade': 'N80', 'top_md_ft': 0, 'shoe_md_ft': 7000, 'hole_in': 12.25, 'mw_ppg': 9.8},
        {'what': '7 in production (example)', 'od_in': 7, 'weight_lbft': 29, 'grade': 'P110', 'top_md_ft': 0, 'shoe_md_ft': 10000, 'hole_in': 8.5, 'mw_ppg': 11.5}]),
        'bha_od_in': 6.5, 'bha_length_ft': 600.0, 'dp_od_in': 5.0,
        'source': 'a casing program and window declared for the example, a vertical well; not a real well\'s'}
    r = analyse(cfg, lambda md: float(md), {**EXAMPLE_WINDOW}, 'a vertical well (TVD = MD), declared for the example',
                'declared for the example: pore pressure 8.6 ppg to 4,000 ft rising to 11.0 ppg at 10,000 ft; fracture gradient 12.5 to 15.8 ppg')
    r.update({'well': 'example', 'display': 'Worked example: a vertical 10,000 ft well with three strings', 'path_from': 'vertical'})
    return r


def page(ws, scene: str = 'workspace', well: Optional[str] = None) -> dict:
    from . import hydraulics as HY
    out = {'scene': scene, 'scenes': [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'example', 'title': 'Worked example: three strings, 10,000 ft'}],
           'defaults': DEFAULTS, 'grades': GRADES, 'not_a_measurement': NOT_A_MEASUREMENT, 'wells': [], 'well': None, 'result': None, 'detail': ''}
    if scene == 'example' or ws is None:
        out['scene'] = 'example'
        out['result'] = example()
        return out
    if scene != 'workspace':
        raise KeyError(f'no scene {scene!r}: workspace or example')
    for w in ws.wells():
        c = well_config(ws, w['id'])
        out['wells'].append({'id': w['id'], 'display': w.get('display') or w['id'], 'config': c, 'strings_text': strings_text(c.get('strings') or [])})
    choice = well if any(w['id'] == well for w in out['wells']) else next((w['id'] for w in out['wells'] if w['config'].get('strings')),
                                                                          out['wells'][0]['id'] if out['wells'] else None)
    out['well'] = choice
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    cfg = well_config(ws, choice)
    if not cfg.get('strings'):
        out['detail'] = 'declare the casing strings for this well (below, or the [casing_design] table of the declarations sheet)'
        return out
    path = HY.depth_path(ws, choice, plan_too=True)
    if path:
        tvd_of = (lambda md: HY.tvd_at(path, md))
        path_how = path['source'] + ('; deeper than it, on along its last slope' if cfg['strings'][-1]['shoe_md_ft'] > path['md_ft'][-1] + 1 or
                                     (cfg.get('td_md_ft') or 0) > path['md_ft'][-1] + 1 else '')
    else:
        tvd_of = (lambda md: float(md))
        path_how = 'no survey, drilling record or plan: the well taken as vertical (TVD = MD)'
    win, how_w = HY._window(ws, choice, cfg)
    if win is None:
        how_w = 'none: the pore pressure is the mud plus the kick intensity, and the fracture gradient is not known'
    try:
        r = analyse(cfg, tvd_of, win, path_how, how_w)
    except ValueError as e:
        out['detail'] = str(e)
        return out
    r.update({'well': choice, 'display': next(w['display'] for w in out['wells'] if w['id'] == choice), 'path_from': path['from'] if path else 'vertical'})
    out['result'] = r
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return f"Casing design: {p.get('detail', '')}"
    L = [f"Casing design - {r['display']} ({r['path_how']}; window: {r['window_how']})", f"  the gas: {gas_how(r['config'])}"]
    for s in r['strings']:
        st, c = s['string'], s['checks']
        L.append(f"  {st['what']}: {st['od_in']:g} in {st['weight_lbft']:g} lb/ft {st['grade']} to {st['shoe_md_ft']:,.0f} ft MD - burst {st['burst_psi']:,} psi, "
                 f"collapse {st['collapse_psi']:,} psi ({st['collapse_regime']}), body {st['body_yield_klbf']:,.0f} klbf")
        L.append('    ' + '; '.join(f"{k} SF {('%.2f' % v['sf']) if v['sf'] is not None else '-'} (design {v['df']:g}) {'OK' if v['ok'] else 'BELOW'}" for k, v in c.items()))
    for k in r['kicks']:
        if k.get('volume_bbl') is not None:
            sw = k.get('swabbed') or {}
            L.append(f"  kick tolerance below {k['shoe_what']}: {k['volume_bbl']:g} bbl at {k['kick_ppg']:.2f} ppg ({k['governs'] if k.get('governs') else k.get('detail', '')}); "
                     f"MAASP {k['maasp_psi']:,} psi" + (f"; swabbed (no underbalance) {sw['volume_bbl']:g} bbl" if sw.get('volume_bbl') is not None else ''))
        else:
            L.append(f"  kick tolerance below {k['shoe_what']}: {k['status']} - {k.get('detail', '')}" + (f"; MAASP {k['maasp_psi']:,} psi, height {k.get('height_ft', 0):,.0f} ft" if k.get('maasp_psi') is not None else ''))
    return '\n'.join(L)


def build_report(ws, version: str, well: Optional[str] = None, scene: str = 'workspace'):
    from . import client_reports as CR
    p = page(ws, scene, well)
    r = p['result']
    if not r:
        raise ValueError(p.get('detail') or 'nothing to check')
    now = datetime.now(timezone.utc)
    rid = f"CSG-{now.strftime('%Y%m%dT%H%M%SZ')}"
    cfg = r['config']
    below = [s for s in r['strings'] if not s['ok']]
    front = [['Report ID', rid], ['Well', r['display']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Strings', f"{len(r['strings'])}: " + ('every check at or above its design factor' if not below else f"{len(below)} with a check below its design factor")],
             ['Design factors', f"burst {cfg['df_burst']:g}, collapse {cfg['df_collapse']:g}, tension {cfg['df_tension']:g} with {cfg['overpull_klbf']:g} klbf overpull"]]
    for k in r['kicks']:
        if k.get('volume_bbl') is not None:
            front.append([f"Kick tolerance below {k['shoe_what']}", f"{k['volume_bbl']:g} bbl at {k['kick_ppg']:.2f} ppg ({k['kick_how']})"])
    s1 = CR.Section('1', 'The casing program and its ratings', [
        f"Read on {r['path_how']}. Window: {r['window_how']}. Ratings from API TR 5C3 (ISO/TR 10400) on the declared size, weight, wall and grade: "
        f"internal yield 0.875 (2 Yp t / D); collapse by its D/t regime; body yield in tension. {cfg.get('source') or ''}"], [])
    s1.tables.append(CR.Table(['String', 'OD (in)', 'lb/ft', 'Grade', 'Wall (in)', 'Top-shoe (ft MD)', 'Hole (in)', 'Mud (ppg)', 'Burst (psi)', 'Collapse (psi)', 'Body (klbf)'],
                              [[s_['what'], f"{s_['od_in']:g}", f"{s_['weight_lbft']:g}", s_['grade'], f"{s_['wall_in']:g}", f"{s_['top_md_ft']:,.0f}-{s_['shoe_md_ft']:,.0f}",
                                f"{s_['hole_in']:g}", f"{s_['mw_ppg']:g}", f"{s_['burst_psi']:,}", f"{s_['collapse_psi']:,} ({s_['collapse_regime']})", f"{s_['body_yield_klbf']:,.0f}"]
                               for s_ in cfg['strings']]))
    s2 = CR.Section('2', 'Each string against its loads', [], [])
    s2.tables.append(CR.Table(['String', 'Burst SF (at ft MD)', 'Collapse SF (at ft MD)', 'Tension SF (load klbf)', 'Result'],
                              [[s['string']['what'],
                                '-' if s['checks']['burst']['sf'] is None else f"{s['checks']['burst']['sf']:.2f} ({s['checks']['burst']['md_ft']:,.0f})",
                                '-' if s['checks']['collapse']['sf'] is None else f"{s['checks']['collapse']['sf']:.2f} ({s['checks']['collapse']['md_ft']:,.0f})",
                                '-' if s['checks']['tension']['sf'] is None else f"{s['checks']['tension']['sf']:.2f} ({s['checks']['tension']['load_klbf']:,.0f})",
                                'every check at or above its design factor' if s['ok'] else 'BELOW: ' + ', '.join(k for k, v in s['checks'].items() if not v['ok'])]
                               for s in r['strings']]))
    for s in r['strings']:
        s2.paragraphs.append(f"{s['string']['what']}: burst - {s['burst_case']} (pore pressure {s['pore_ppg']:.2f} ppg at {s['reservoir_tvd_ft']:,.0f} ft TVD"
                             + (f", fracture gradient at the shoe {s['fg_shoe_ppg']:.2f} ppg" if s['fg_shoe_ppg'] is not None else '') + f"), backup {cfg['backup_gradient_psi_ft']:g} psi/ft; "
                             f"collapse - {s['collapse_case']}, the rating reduced for the tension at each depth; tension - buoyancy factor {s['buoyancy_factor']:.3f}.")
    secs = [s1, s2]
    for n, s in enumerate(r['strings']):
        k_ = max(1, len(s['rows']) // 25)
        idx = list(range(0, len(s['rows']), k_)) + ([len(s['rows']) - 1] if (len(s['rows']) - 1) % k_ else [])
        sec = CR.Section(f'3.{n + 1}', f"{s['string']['what']}: the loads by depth", [], [])
        sec.tables.append(CR.Table(['MD (ft)', 'TVD (ft)', 'Burst load (psi)', 'Burst SF', 'Collapse load (psi)', 'Collapse rating (psi)', 'Collapse SF', 'Axial (klbf)'],
                                   [[f"{s['rows'][i]['md_ft']:,.0f}", f"{s['rows'][i]['tvd_ft']:,.0f}", f"{s['rows'][i]['burst_net_psi']:,}",
                                     '-' if s['rows'][i]['sf_burst'] is None else f"{s['rows'][i]['sf_burst']:.2f}", f"{s['rows'][i]['collapse_net_psi']:,}",
                                     f"{s['rows'][i]['collapse_rating_psi']:,}", '-' if s['rows'][i]['sf_collapse'] is None else f"{s['rows'][i]['sf_collapse']:.2f}",
                                     f"{s['rows'][i]['axial_klbf']:,.0f}"] for i in idx]))
        secs.append(sec)
    s4 = CR.Section('4', 'Kick tolerance', [f"A single gas bubble: {gas_how(cfg)}. MAASP = 0.052 (FG - MW) D_shoe; SIDPP = 0.052 (kick - MW) D_bottom; "
                                             f"the tallest bubble H = (MAASP - SIDPP) / (mud gradient - gas gradient), the gas's gradient taken at the bubble's middle on bottom "
                                             f"and at the shoe; its volume at the bottom and at the shoe "
                                             f"({'the real-gas law, p V / z T' if real_gas(cfg) else 'Boyle' + chr(39) + 's law'} back to the bottom), the smaller governing; "
                                             f"and the same for a swabbed kick, gas pulled in with no underbalance. Annulus: BHA {cfg.get('bha_od_in') or '-'} in over "
                                             f"{cfg.get('bha_length_ft') or '-'} ft, drill pipe {cfg.get('dp_od_in') or '-'} in."], [])
    s4.tables.append(CR.Table(['Below', 'Open hole (ft MD)', 'Mud (ppg)', 'Kick (ppg)', 'FG at shoe (ppg)', 'MAASP (psi)', 'Gas (psi/ft) bottom / shoe', 'Height (ft)',
                               'Kick tolerance (bbl)', 'Swabbed (bbl)'],
                              [[k['shoe_what'], f"{k['shoe_md_ft']:,.0f}-{k['bottom_md_ft']:,.0f}", f"{k['mw_ppg']:g}", f"{k['kick_ppg']:.2f}",
                                '-' if k['fg_shoe_ppg'] is None else f"{k['fg_shoe_ppg']:.2f}", '-' if k.get('maasp_psi') is None else f"{k['maasp_psi']:,}",
                                '-' if k.get('gas_gradient_bottom_psi_ft') is None else f"{k['gas_gradient_bottom_psi_ft']:.3f} / {k['gas_gradient_shoe_psi_ft']:.3f}",
                                '-' if k.get('height_ft') is None else f"{k['height_ft']:,.0f}", f"{k['volume_bbl']:g}" if k.get('volume_bbl') is not None else k['status'],
                                f"{(k.get('swabbed') or {}).get('volume_bbl'):g}" if (k.get('swabbed') or {}).get('volume_bbl') is not None else '-']
                               for k in r['kicks']]))
    secs += [s4, CR.Section('5', 'What this will not call a measurement', ['; '.join(p['not_a_measurement']) + '.'], [])]
    return CR.Document(f"Casing design and kick tolerance - {r['display']}", rid, front, secs,
                       footer='Ratings after API TR 5C3 / ISO/TR 10400; loads after Bourgoyne et al., Applied Drilling Engineering (SPE, 1986), ch. 7.',
                       data={'report_id': rid, **r})


def write_report(ws, version: str, well: Optional[str] = None, actor: str = 'system', scene: str = 'workspace') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well, scene)
    if scene == 'example':
        dd, base = ws.reports_dir, 'casing_design_example'
    else:
        dd, base = os.path.join(ws.reports_dir, 'wells', doc.data['well']), 'casing_design'
    os.makedirs(dd, exist_ok=True)
    paths = CR.write(doc, dd, base)
    ws.audit(actor, 'report.casing_design', {'well': doc.data.get('well'), 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}
