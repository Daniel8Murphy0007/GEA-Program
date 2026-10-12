# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""slip - slip tendency on the mapped faults, from a declared stress state: the fault slip potential method, stated.

The question after "how far is the nearest mapped fault" is "how close to slipping is it, under this
injection". The open method (Morris et al. 1996 slip tendency; Walsh & Zoback 2016 fault slip potential) is
arithmetic on a stress state and a fault plane: resolve the vertical stress and the two horizontal stresses onto
the plane, subtract the pore pressure, and compare the shear stress to the friction times the effective
normal stress. This module does that arithmetic and nothing more; every input is declared by the site with
its source, and the output says which inputs it rests on:

- the stress state at the depth of interest: the vertical stress gradient (overburden), the minimum and maximum
  horizontal stress gradients, the azimuth of the maximum horizontal stress - from the World Stress Map's
  records near the site (`wsm_nearby`, read from the WSM database CSV the site downloads), from a published
  regional study, or from the operator's own measurements, named either way;
- the pore pressure: the ambient gradient (hydrostatic 0.433 psi/ft unless declared otherwise) and the
  increase the injection is to be judged at (a declared number, or the surface injection pressure the well
  records);
- the fault: its strike from the mapped trace (the layer the site brought), its dip declared (a map carries
  no dip; a basement-rooted fault in the Permian is steep, and the statement is given for the dip the site
  declares, bracketed if a range is declared), and the friction coefficient (Byerlee's 0.6 unless declared),
  printed beside the measured envelope the catalogue carries (`friction_priors`), so the reader sees how far a
  law can sit from a measurement.

The output per fault segment: the normal and shear stress on the plane, the effective normal stress at ambient
and at the injection pore pressure, the slip tendency Ts = tau / sigma_n_eff, the pore pressure increase that
would bring the plane to the friction line (the "pressure to slip", negative when the plane is already past
it under these inputs), and the sensitivity to the declared dip and friction. A statement, never a verdict: the
output rests on declared gradients and a declared dip, and it says so on every line.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Dict, List, Optional, Tuple

PSI_PER_FT_HYDROSTATIC = 0.433
BYERLEE = 0.6
REGIMES = ('normal', 'strike-slip', 'reverse')


# ---- the stress state --------------------------------------------------------------------------------------------------------
def stress_at(depth_ft: float, sv_psi_ft: float, shmin_psi_ft: float, shmax_psi_ft: float, pp_psi_ft: float = PSI_PER_FT_HYDROSTATIC) -> dict:
    """Total stresses and ambient pore pressure at a true vertical depth, from declared gradients (psi/ft)."""
    return {'depth_ft': float(depth_ft), 'Sv': sv_psi_ft * depth_ft, 'Shmin': shmin_psi_ft * depth_ft, 'SHmax': shmax_psi_ft * depth_ft, 'Pp': pp_psi_ft * depth_ft}


def regime_of(sv: float, shmin: float, shmax: float) -> str:
    """Anderson's classification from the ordering of the three stresses."""
    if sv >= shmax >= shmin:
        return 'normal'
    if shmax >= sv >= shmin:
        return 'strike-slip'
    return 'reverse'


def resolve(strike_deg: float, dip_deg: float, shmax_azimuth_deg: float, sv: float, shmin: float, shmax: float) -> Tuple[float, float]:
    """Normal stress and shear stress on a plane (strike by the right-hand rule, dip to the right of strike) in a stress field
    with principal axes vertical and horizontal, SHmax at the given azimuth. Standard Cauchy resolution; compression positive."""
    s, d, a = math.radians(strike_deg), math.radians(dip_deg), math.radians(shmax_azimuth_deg)
    # unit normal of the plane in (north, east, down)
    n = (-math.sin(d) * math.sin(s), math.sin(d) * math.cos(s), -math.cos(d))
    # stress tensor in (north, east, down): rotate the horizontal principal stresses by the SHmax azimuth
    ca, sa = math.cos(a), math.sin(a)
    sxx = shmax * ca * ca + shmin * sa * sa
    syy = shmax * sa * sa + shmin * ca * ca
    sxy = (shmax - shmin) * ca * sa
    szz = sv
    t = (sxx * n[0] + sxy * n[1], sxy * n[0] + syy * n[1], szz * n[2])          # traction on the plane
    sn = t[0] * n[0] + t[1] * n[1] + t[2] * n[2]
    tt = math.sqrt(max(t[0] ** 2 + t[1] ** 2 + t[2] ** 2 - sn ** 2, 0.0))
    return sn, tt


def slip_tendency(strike_deg: float, dip_deg: float, state: dict, shmax_azimuth_deg: float, mu: float = BYERLEE, delta_pp: float = 0.0) -> dict:
    """The plane against the stress state: Ts at the ambient pore pressure and at the injection increase, the pressure to slip."""
    sn, tau = resolve(strike_deg, dip_deg, shmax_azimuth_deg, state['Sv'], state['Shmin'], state['SHmax'])
    pp0 = state['Pp']
    sn_eff0 = sn - pp0
    sn_eff1 = sn - (pp0 + delta_pp)
    ts0 = tau / sn_eff0 if sn_eff0 > 0 else float('inf')
    ts1 = tau / sn_eff1 if sn_eff1 > 0 else float('inf')
    p_slip = sn_eff0 - tau / mu                      # the pore-pressure increase that brings the plane to tau = mu * sigma_n_eff
    return {'strike_deg': round(strike_deg, 1), 'dip_deg': dip_deg, 'sigma_n_psi': round(sn, 1), 'tau_psi': round(tau, 1), 'sigma_n_eff_ambient_psi': round(sn_eff0, 1),
            'sigma_n_eff_injection_psi': round(sn_eff1, 1), 'Ts_ambient': round(ts0, 3), 'Ts_injection': round(ts1, 3), 'mu': mu,
            'pressure_to_slip_psi': round(p_slip, 1), 'margin_after_injection_psi': round(p_slip - delta_pp, 1),
            'status': ('PAST THE FRICTION LINE' if p_slip - delta_pp <= 0 else 'BELOW THE FRICTION LINE')}


def segment_strikes(feature) -> List[Tuple[float, float, float, float, float]]:
    """(strike_deg, length_km, mid_lat, mid_lon, bearing) per segment of a polyline feature in geographic degrees."""
    out = []
    for part in feature.parts:
        for (x0, y0), (x1, y1) in zip(part[:-1], part[1:]):
            latm = (y0 + y1) / 2.0
            dx = (x1 - x0) * 111.195 * math.cos(math.radians(latm))
            dy = (y1 - y0) * 111.195
            L = math.hypot(dx, dy)
            if L <= 0:
                continue
            b = math.degrees(math.atan2(dx, dy)) % 360.0
            out.append((b % 180.0, L, latm, (x0 + x1) / 2.0, b))
    return out


def faults_in_radius(layer, lat: float, lon: float, radius_km: float, type_attr: Optional[str] = None, name_attr: Optional[str] = None, max_faults: int = 50) -> List[dict]:
    """The mapped fault traces within the radius, each with its length-weighted mean strike and its segments."""
    from . import structure as ST
    r = ST.nearest(layer, lat, lon, radius_km, type_attr, name_attr, n_list=max_faults)
    out = []
    if r.get('status') != 'OK':
        return out
    for w in r.get('within', []):
        if w.get('kind') != 'fault':
            continue
        f = layer.features[w['index']] if 'index' in w and w['index'] < len(layer.features) else None
        if f is None:
            continue
        segs = segment_strikes(f)
        if not segs:
            continue
        # length-weighted mean of a direction (mod 180): average the doubled angle
        sx = sum(L * math.cos(math.radians(2 * s)) for s, L, _, _, _ in segs)
        sy = sum(L * math.sin(math.radians(2 * s)) for s, L, _, _, _ in segs)
        mean_strike = (math.degrees(math.atan2(sy, sx)) / 2.0) % 180.0
        row = {'index': w['index'], 'name': w.get('name', ''), 'type': w.get('type', ''), 'distance_km': w['distance_km'], 'bearing_deg': w.get('bearing_deg'),
               'strike_deg': round(mean_strike, 1), 'length_km': round(sum(L for _, L, _, _, _ in segs), 2), 'n_segments': len(segs),
               'strike_spread_deg': round(max(abs(((s - mean_strike + 90) % 180) - 90) for s, _, _, _, _ in segs), 1), 'attrs': {}}
        # what the layer itself states about the fault: a dip, a published pressure-to-slip, a confidence
        for k, v in f.attrs.items():
            kl = k.lower()
            if kl == 'dip' and _f(v) is not None:
                row['attrs']['dip_deg'] = float(v)
            elif kl.startswith('pp2s') and _f(v) is not None:
                row['attrs']['published_pressure_to_slip_psi'] = round(float(v) * (145.037738 if 'mpa' in kl else 1.0), 1)
                row['attrs']['published_pressure_to_slip_field'] = k
            elif kl in ('note', 'confidence', 'source', 'interprete', 'orientatio', 'comment') and str(v).strip():
                row['attrs'][kl] = str(v).strip()
        out.append(row)
    return out


def statement(faults: List[dict], depth_ft: float, stress: dict, shmax_azimuth_deg: float, dips_deg: Tuple[float, ...] = (60.0, 75.0, 90.0),
              mus: Tuple[float, ...] = (BYERLEE,), delta_pp_psi: float = 0.0, sources: Optional[Dict[str, str]] = None) -> dict:
    """Every fault within the radius against the declared stress state, for each declared dip and friction coefficient."""
    state = stress_at(depth_ft, stress['sv_psi_ft'], stress['shmin_psi_ft'], stress['shmax_psi_ft'], stress.get('pp_psi_ft', PSI_PER_FT_HYDROSTATIC))
    regime = regime_of(state['Sv'], state['Shmin'], state['SHmax'])
    rows = []
    for f in faults:
        own_dip = (f.get('attrs') or {}).get('dip_deg')
        dips = (own_dip,) if own_dip is not None else tuple(dips_deg)        # the layer's own dip when it states one, else the declared range
        cases = []
        for dip in dips:
            for mu in mus:
                cases.append(slip_tendency(f['strike_deg'], dip, state, shmax_azimuth_deg, mu, delta_pp_psi))
        worst = min(cases, key=lambda c: c['margin_after_injection_psi'])
        best = max(cases, key=lambda c: c['margin_after_injection_psi'])
        row = {**f, 'cases': cases, 'worst': worst, 'best': best, 'dip_source': ('the layer' if own_dip is not None else 'declared'),
               'angle_to_shmax_deg': round(min(abs(((f['strike_deg'] - shmax_azimuth_deg + 90) % 180) - 90), 90.0), 1)}
        pub = (f.get('attrs') or {}).get('published_pressure_to_slip_psi')
        if pub is not None:
            ours = cases[0]['pressure_to_slip_psi'] if len(cases) == 1 else None
            row['published'] = {'pressure_to_slip_psi': pub, 'field': f['attrs'].get('published_pressure_to_slip_field'),
                                'ours_at_ambient_psi': ours, 'difference_psi': (round(ours - pub, 1) if ours is not None else None)}
        rows.append(row)
    rows.sort(key=lambda r: r['worst']['margin_after_injection_psi'])
    out = {'depth_ft': depth_ft, 'state': {k: round(v, 1) for k, v in state.items()}, 'regime': regime, 'shmax_azimuth_deg': shmax_azimuth_deg, 'dips_deg': list(dips_deg),
           'mus': list(mus), 'delta_pp_psi': delta_pp_psi, 'sources': dict(sources or {}), 'faults': rows,
           'n_past_line': sum(1 for r in rows if r['worst']['status'] == 'PAST THE FRICTION LINE'),
           'n_past_line_best_case': sum(1 for r in rows if r['best']['status'] == 'PAST THE FRICTION LINE')}
    out['lines'] = lines(out)
    return out


def lines(res: dict) -> List[str]:
    st = res['state']
    src = res.get('sources', {})
    out = [f"Stress state at {res['depth_ft']:,.0f} ft TVD, from declared gradients: Sv {st['Sv']:,.0f}, SHmax {st['SHmax']:,.0f} at {res['shmax_azimuth_deg']:g} deg, Shmin {st['Shmin']:,.0f}, "
           f"ambient pore pressure {st['Pp']:,.0f} psi ({res['regime']} regime by their order)" + (f"; SHmax azimuth from {src['shmax_azimuth']}" if src.get('shmax_azimuth') else '')
           + (f"; gradients from {src['gradients']}" if src.get('gradients') else '') + '.']
    out.append(f"Judged at a pore-pressure increase of {res['delta_pp_psi']:,.0f} psi, for dips {', '.join(f'{d:g}' for d in res['dips_deg'])} deg where the layer states no dip "
               f"(declared; a map carries no dip) and friction {', '.join(f'{m:g}' for m in res['mus'])}.")
    if not res['faults']:
        out.append('No mapped fault trace within the radius.')
        return out
    out.append(f"{len(res['faults'])} mapped fault trace(s) within the radius; {res['n_past_line']} past the friction line in the worst declared case, "
               f"{res['n_past_line_best_case']} in the best.")
    for r in res['faults'][:10]:
        w, b = r['worst'], r['best']
        out.append(f"{r['name'] or r['type'] or 'unnamed trace'} ({r['distance_km']:.2f} km, strike {r['strike_deg']:g} deg, {r['angle_to_shmax_deg']:g} deg to SHmax, {r['length_km']:g} km"
                   + (f", dip {w['dip_deg']:g} from the layer" if r.get('dip_source') == 'the layer' else '') + '): '
                   f"Ts {b['Ts_injection']:.2f} to {w['Ts_injection']:.2f}; pressure to slip {w['margin_after_injection_psi']:,.0f} to {b['margin_after_injection_psi']:,.0f} psi "
                   f"after the increase; {w['status']} in the worst case (dip {w['dip_deg']:g}, mu {w['mu']:g})."
                   + (f" The layer publishes {r['published']['pressure_to_slip_psi']:,.0f} psi to slip ({r['published']['field']}) under its authors' stress model; this arithmetic on the declared "
                      f"gradients gives {r['published']['ours_at_ambient_psi']:,.0f} psi at ambient pressure ({r['published']['difference_psi']:+,.0f} psi)." if r.get('published') and r['published'].get('ours_at_ambient_psi') is not None else ''))
    out.append('A statement on declared gradients, a declared dip and a declared friction; the map gives the strike and nothing else. Not a probability, not a verdict.')
    return out


# ---- the World Stress Map ------------------------------------------------------------------------------------------------------
def read_wsm(src) -> List[dict]:
    """The WSM database CSV (2016 release): every record with its latitude, longitude, SHmax azimuth, quality, regime, type and depth.
    Columns are found by name (LAT, LON, AZI, QUALITY, REGIME, TYPE, DEPTH, ID, LOCALITY), so a release with more columns still reads."""
    if isinstance(src, (bytes, bytearray)):
        text = bytes(src).decode('utf-8', 'replace')
    elif hasattr(src, 'read'):
        text = src.read()
    else:
        with open(src, encoding='utf-8', errors='replace') as f:
            text = f.read()
    lines_ = [ln for ln in text.splitlines() if ln.strip()]
    if not lines_:
        raise ValueError('WSM: empty file')
    delim = ',' if lines_[0].count(',') >= lines_[0].count(';') else ';'
    rd = csv.DictReader(io.StringIO('\n'.join(lines_)), delimiter=delim)
    cols = {c.strip().upper(): c for c in (rd.fieldnames or [])}
    need = {'LAT', 'LON', 'AZI'}
    if not need <= set(cols):
        raise ValueError(f"WSM: columns {sorted(need - set(cols))} not found (header: {', '.join((rd.fieldnames or [])[:12])})")
    out = []
    for r in rd:
        try:
            lat, lon, azi = float(r[cols['LAT']]), float(r[cols['LON']]), float(r[cols['AZI']])
        except (TypeError, ValueError):
            continue
        if azi == 999 or azi < 0:
            continue
        out.append({'id': r.get(cols.get('ID', ''), ''), 'lat': lat, 'lon': lon, 'azimuth': azi % 180.0, 'quality': (r.get(cols.get('QUALITY', ''), '') or '').strip().upper(),
                    'regime': (r.get(cols.get('REGIME', ''), '') or '').strip().upper(), 'type': (r.get(cols.get('TYPE', ''), '') or '').strip(),
                    'depth_km': _f(r.get(cols.get('DEPTH', ''), '')), 'locality': (r.get(cols.get('LOCALITY', ''), '') or '').strip()})
    return out


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def wsm_nearby(records: List[dict], lat: float, lon: float, radius_km: float = 100.0, qualities: Tuple[str, ...] = ('A', 'B', 'C')) -> dict:
    """The WSM records within the radius of the acceptable qualities: their count by quality and regime, the circular
    median SHmax azimuth (axial, mod 180) with the spread - the stated source for a site's SHmax azimuth."""
    from . import geodesy as GD
    near = []
    for r in records:
        if r['quality'] not in qualities:
            continue
        dlat = abs(r['lat'] - lat); dlon = abs(r['lon'] - lon) * math.cos(math.radians(lat))
        if math.hypot(dlat, dlon) * 111.195 > radius_km * 1.5:
            continue
        try:
            dm = GD.geodesic_m(lat, lon, r['lat'], r['lon'])['distance_m']
            dist = (dm if dm is not None else math.hypot(dlat, dlon) * 111195.0) / 1000.0
        except Exception:                                      # noqa: BLE001 - a fallback for the degenerate pairs
            dist = math.hypot(dlat, dlon) * 111.195
        if dist <= radius_km:
            near.append({**r, 'distance_km': round(dist, 2)})
    if not near:
        return {'n': 0, 'radius_km': radius_km, 'qualities': list(qualities), 'azimuth_median': None, 'records': []}
    sx = sum(math.cos(math.radians(2 * r['azimuth'])) for r in near); sy = sum(math.sin(math.radians(2 * r['azimuth'])) for r in near)
    mean = (math.degrees(math.atan2(sy, sx)) / 2.0) % 180.0
    R = math.hypot(sx, sy) / len(near)
    devs = sorted(abs(((r['azimuth'] - mean + 90) % 180) - 90) for r in near)
    by_q: Dict[str, int] = {}; by_reg: Dict[str, int] = {}
    for r in near:
        by_q[r['quality']] = by_q.get(r['quality'], 0) + 1
        by_reg[r['regime'] or '?'] = by_reg.get(r['regime'] or '?', 0) + 1
    near.sort(key=lambda r: r['distance_km'])
    return {'n': len(near), 'radius_km': radius_km, 'qualities': list(qualities), 'azimuth_mean': round(mean, 1), 'azimuth_median': round(mean, 1),
            'spread_deg_median': round(devs[len(devs) // 2], 1), 'concentration': round(R, 3), 'by_quality': by_q, 'by_regime': by_reg, 'nearest_km': near[0]['distance_km'],
            'records': near[:50],
            'statement': f"{len(near)} World Stress Map record(s) of quality {'/'.join(qualities)} within {radius_km:g} km: SHmax {mean:.0f} deg (axial mean; median deviation "
                         f"{devs[len(devs) // 2]:.0f} deg; concentration {R:.2f}); regimes {', '.join(f'{k} {v}' for k, v in sorted(by_reg.items()))}."}
