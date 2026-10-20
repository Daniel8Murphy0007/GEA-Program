"""Wellbore position uncertainty: the ISCWSA MWD error model (Revision 4), station by station.

A directional survey gives each station's measured depth, inclination and azimuth; each is measured with errors of
known kinds - the depth counter, the accelerometers and magnetometers (their biases and scale factors), the
declination and the field's strength, the drill string's interference, the tool's misalignment and sag. The ISCWSA
model (Williamson, "Accuracy prediction for directional measurement while drilling", SPE Drilling & Completion 15(4),
2000, SPE-67616-PA; the Industry Steering Committee on Wellbore Survey Accuracy's MWD model at its fourth revision)
gives each error source a magnitude, a propagation mode and a weighting function - its effect on the station's depth,
inclination and azimuth - and carries those effects into the position of every station below it:

- the position effect of an error at station k reaches the two survey legs that meet there (the leg ending at k and
  the leg starting at k), by the balanced-tangential derivatives of each leg's displacement; at the station of
  interest K only the leg ending there counts;
- a RANDOM source is independent from station to station: its variances add; a SYSTEMATIC source is the same error
  at every station of a survey: its effects add before they are squared; a GLOBAL source is the same error in every
  survey of the field (declination, the field strength, the depth stretch) - within one well as systematic, and
  between two wells correlated, which a collision check uses;
- at a vertical station the azimuth is undefined, and the three sources whose weighting divides by the inclination
  (ABXY-TI2S, XYM3, XYM4) take the model's own limiting form there.

The magnitudes are the model's published ones (ISCWSA MWD Rev 4; the same as OWSG MWD Rev 2); a tool of another
grade is another model. The result at each station is the 3x3 covariance of its north, east and vertical position,
in metres squared, and the semi-axes of its ellipsoid. This module is checked in the gate against the ISCWSA's own
example (test well 1, 60 degrees north, every source at four depths).
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

SOURCE = ('ISCWSA MWD error model, Revision 4 (Williamson, SPE-67616-PA, 2000, with the ISCWSA revisions; the same terms as '
          'OWSG MWD Rev 2): every error source with its magnitude, propagation and weighting function')
G_DEFAULT = 9.80665
VERTICAL_LIMIT_RAD = math.radians(1e-4)
D2R = math.pi / 180.0
# code: (weighting, magnitude in SI or radians, propagation)
MWD_REV4: Dict[str, Tuple[str, float, str]] = {
    'DRFR': ('DREF', 0.35, 'random'),
    'DSFS': ('DSF', 0.00056, 'systematic'),
    'DSTG': ('DST', 2.5e-7, 'global'),
    'ABXY-TI1S': ('ABXY_TI1', 0.004, 'systematic'),
    'ABXY-TI2S': ('ABXY_TI2', 0.004, 'systematic'),
    'ABZ': ('ABZ', 0.004, 'systematic'),
    'ASXY-TI1S': ('ASXY_TI1', 0.0005, 'systematic'),
    'ASXY-TI2S': ('ASXY_TI2', 0.0005, 'systematic'),
    'ASXY-TI3S': ('ASXY_TI3', 0.0005, 'systematic'),
    'ASZ': ('ASZ', 0.0005, 'systematic'),
    'MBXY-TI1S': ('MBXY_TI1', 70.0, 'systematic'),
    'MBXY-TI2S': ('MBXY_TI2', 70.0, 'systematic'),
    'MBZ': ('MBZ', 70.0, 'systematic'),
    'MSXY-TI1S': ('MSXY_TI1', 0.0016, 'systematic'),
    'MSXY-TI2S': ('MSXY_TI2', 0.0016, 'systematic'),
    'MSXY-TI3S': ('MSXY_TI3', 0.0016, 'systematic'),
    'MSZ': ('MSZ', 0.0016, 'systematic'),
    'DECG': ('AZ', 0.36 * D2R, 'global'),
    'DECR': ('AZ', 0.1 * D2R, 'random'),
    'DBHG': ('DBH', 5000.0 * D2R, 'global'),
    'DBHR': ('DBH', 3000.0 * D2R, 'random'),
    'AMIL': ('AMIL', 220.0, 'systematic'),
    'SAG': ('SAG', 0.2 * D2R, 'systematic'),
    'XYM1': ('XYM1', 0.1 * D2R, 'systematic'),
    'XYM2': ('XYM2', 0.1 * D2R, 'systematic'),
    'XYM3': ('XYM3', 0.1 * D2R, 'systematic'),
    'XYM4': ('XYM4', 0.1 * D2R, 'systematic'),
}
SINGULAR = ('ABXY_TI2', 'XYM3', 'XYM4')


def weights(kind: str, md, inc, az_true, az_mag, tvd, dip: float, B: float, G: float) -> np.ndarray:
    """dp/de: the effect of a unit error of this kind on each station's (depth, inclination, azimuth)."""
    n = len(md)
    w = np.zeros((n, 3))
    si, ci = np.sin(inc), np.cos(inc)
    sa, ca = np.sin(az_mag), np.cos(az_mag)
    tD = math.tan(dip)
    BcD = B * math.cos(dip)
    with np.errstate(divide='ignore', invalid='ignore'):
        if kind == 'DREF':
            w[:, 0] = 1.0
        elif kind == 'DSF':
            w[:, 0] = md
        elif kind == 'DST':
            w[:, 0] = md * tvd
        elif kind == 'ABXY_TI1':
            w[:, 1] = -ci / G
            w[:, 2] = ci * tD * sa / G
        elif kind == 'ABXY_TI2':
            w[:, 2] = np.where(inc > VERTICAL_LIMIT_RAD, (ci / np.where(si == 0, 1.0, si) - tD * ca) / G, 0.0)
        elif kind == 'ABZ':
            w[:, 1] = -si / G
            w[:, 2] = si * tD * sa / G
        elif kind == 'ASXY_TI1':
            w[:, 1] = si * ci / math.sqrt(2.0)
            w[:, 2] = -si * tD * ci * sa / math.sqrt(2.0)
        elif kind == 'ASXY_TI2':
            w[:, 1] = si * ci / 2.0
            w[:, 2] = -si * tD * ci * sa / 2.0
        elif kind == 'ASXY_TI3':
            w[:, 2] = (si * tD * ca - ci) / 2.0
        elif kind == 'ASZ':
            w[:, 1] = -si * ci
            w[:, 2] = si * tD * ci * sa
        elif kind == 'MBXY_TI1':
            w[:, 2] = -ci * sa / BcD
        elif kind == 'MBXY_TI2':
            w[:, 2] = ca / BcD
        elif kind == 'MBZ':
            w[:, 2] = -si * sa / BcD
        elif kind == 'MSXY_TI1':
            w[:, 2] = si * sa * (tD * ci + si * ca) / math.sqrt(2.0)
        elif kind == 'MSXY_TI2':
            w[:, 2] = sa * (tD * si * ci - ci * ci * ca - ca) / 2.0
        elif kind == 'MSXY_TI3':
            w[:, 2] = (ci * ca * ca - ci * sa * sa - tD * si * ca) / 2.0
        elif kind == 'MSZ':
            w[:, 2] = -(si * ca + tD * ci) * si * sa
        elif kind == 'AZ':
            w[:, 2] = 1.0
        elif kind == 'DBH':
            w[:, 2] = 1.0 / BcD
        elif kind == 'AMIL':
            w[:, 2] = -si * sa / BcD
        elif kind == 'SAG':
            w[:, 1] = si
        elif kind == 'XYM1':
            w[:, 1] = np.abs(si)
        elif kind == 'XYM2':
            w[:, 2] = -1.0
        elif kind == 'XYM3':
            w[:, 1] = np.abs(ci) * np.cos(az_true)
            w[:, 2] = np.where(inc > VERTICAL_LIMIT_RAD, -np.abs(ci) * np.sin(az_true) / np.where(si == 0, 1.0, si), 0.0)
        elif kind == 'XYM4':
            w[:, 1] = np.abs(ci) * np.sin(az_true)
            w[:, 2] = np.where(inc > VERTICAL_LIMIT_RAD, np.abs(ci) * np.cos(az_true) / np.where(si == 0, 1.0, si), 0.0)
        else:
            raise KeyError(f'no weighting {kind!r}')
    return w


def _drdp(md, inc, az) -> Tuple[np.ndarray, np.ndarray]:
    """The balanced-tangential derivatives of the legs' displacements (north, east, vertical) by each station's depth,
    inclination and azimuth: A[k] for the leg ending at station k, Bp[k] for the leg starting at k. Each (n, 3, 3):
    [k, NEV, DIA]."""
    n = len(md)
    si, ci, sa, ca = np.sin(inc), np.cos(inc), np.sin(az), np.cos(az)
    t = np.stack([si * ca, si * sa, ci], axis=1)
    dti = np.stack([ci * ca, ci * sa, -si], axis=1)
    dta = np.stack([-si * sa, si * ca, np.zeros(n)], axis=1)
    A = np.zeros((n, 3, 3)); Bp = np.zeros((n, 3, 3))
    h = 0.5 * np.diff(md)
    A[1:, :, 0] = 0.5 * (t[:-1] + t[1:])
    A[1:, :, 1] = h[:, None] * dti[1:]
    A[1:, :, 2] = h[:, None] * dta[1:]
    Bp[:-1, :, 0] = -0.5 * (t[:-1] + t[1:])
    Bp[:-1, :, 1] = h[:, None] * dti[:-1]
    Bp[:-1, :, 2] = h[:, None] * dta[:-1]
    if md[0] == 0:                                   # a survey from the datum: the first station's own depth error
        A[0, :, 0] = t[0]
    return A, Bp


def model_terms(md_m, inc_deg, az_true_deg, tvd_m, declination_deg: float, dip_deg: float, total_nT: float,
                G: float = G_DEFAULT, model: Optional[Dict[str, Tuple[str, float, str]]] = None) -> Dict[str, dict]:
    """Each error source's position vectors at every station: {'code': {'e': (n,3) the station's effect on both legs,
    'e_star': (n,3) its effect on its own leg, 'prop': propagation}}. Depths in metres, angles in degrees."""
    model = model or MWD_REV4
    md = np.asarray(md_m, dtype=np.float64)
    inc = np.radians(np.asarray(inc_deg, dtype=np.float64))
    azt = np.radians(np.asarray(az_true_deg, dtype=np.float64))
    tvd = np.asarray(tvd_m, dtype=np.float64)
    azm = azt - math.radians(declination_deg)
    dip, B = math.radians(dip_deg), float(total_nT)
    A, Bp = _drdp(md, inc, azt)
    out = {}
    for code, (kind, mag, prop) in model.items():
        dpde = weights(kind, md, inc, azt, azm, tvd, dip, B, G) * mag
        e = np.einsum('kij,kj->ki', A + Bp, dpde)
        e[0] = 0.0
        es = np.einsum('kij,kj->ki', A, dpde)
        if kind in SINGULAR:
            sing = np.flatnonzero(inc < VERTICAL_LIMIT_RAD)
            for k in sing:
                if k == 0 or k == len(md) - 1:
                    continue
                dd, d1 = md[k + 1] - md[k - 1], md[k] - md[k - 1]
                a2 = azt[k]
                if kind == 'ABXY_TI2':
                    e[k] = [0.5 * dd * -math.sin(a2) * mag / G, 0.5 * dd * math.cos(a2) * mag / G, 0.0]
                    es[k] = [0.5 * d1 * -math.sin(a2) * mag / G, 0.5 * d1 * math.cos(a2) * mag / G, 0.0]
                elif kind == 'XYM3':
                    e[k] = [0.5 * dd * mag, 0.0, 0.0]
                    es[k] = [0.5 * d1 * mag, 0.0, 0.0]
                else:
                    e[k] = [0.0, 0.5 * dd * mag, 0.0]
                    es[k] = [0.0, 0.5 * d1 * mag, 0.0]
        out[code] = {'e': e, 'e_star': es, 'prop': prop}
    return out


def covariances(terms: Dict[str, dict]) -> Tuple[Dict[str, np.ndarray], np.ndarray, Dict[str, np.ndarray]]:
    """Per source and in total, each station's NEV covariance (n,3,3); and each global source's correlated vector sum
    (n,3) for the correlation between wells."""
    per, total, glob = {}, None, {}
    for code, t in terms.items():
        e, es = t['e'], t['e_star']
        if t['prop'] == 'random':
            run = np.cumsum(np.einsum('ki,kj->kij', e, e), axis=0)
            prev = np.concatenate([np.zeros((1, 3, 3)), run[:-1]])
            C = prev + np.einsum('ki,kj->kij', es, es)
        else:
            run = np.cumsum(e, axis=0)
            s = np.concatenate([np.zeros((1, 3)), run[:-1]]) + es
            C = np.einsum('ki,kj->kij', s, s)
            if t['prop'] == 'global':
                glob[code] = s
        per[code] = C
        total = C if total is None else total + C
    return per, total, glob


def ellipse(C: np.ndarray, k: float = 1.0) -> dict:
    """The semi-axes of a covariance's ellipsoid at k standard deviations: the horizontal major and minor and its
    azimuth, and the vertical standard deviation."""
    h = C[:2, :2]
    vals, vecs = np.linalg.eigh(h)
    vals = np.clip(vals, 0.0, None)
    major = vecs[:, 1]
    return {'major_m': round(float(k * math.sqrt(vals[1])), 3), 'minor_m': round(float(k * math.sqrt(vals[0])), 3),
            'major_azimuth_deg': round(float(math.degrees(math.atan2(major[1], major[0])) % 180.0), 1),
            'vertical_m': round(float(k * math.sqrt(max(C[2, 2], 0.0))), 3)}


def well_uncertainty(md_m, inc_deg, az_true_deg, tvd_m, declination_deg: float, dip_deg: float, total_nT: float,
                     G: float = G_DEFAULT) -> dict:
    terms = model_terms(md_m, inc_deg, az_true_deg, tvd_m, declination_deg, dip_deg, total_nT, G)
    per, total, glob = covariances(terms)
    return {'cov': total, 'per_source': per, 'global': glob, 'model': 'ISCWSA MWD Rev 4', 'source': SOURCE,
            'field': {'declination_deg': declination_deg, 'dip_deg': dip_deg, 'total_nT': total_nT}}


def min_curve_tvd(md_m, inc_deg, az_deg) -> np.ndarray:
    """The true vertical depth of each station below the first, by minimum curvature (for the stretch term)."""
    from . import well3d as W3
    mc = W3.min_curvature(np.asarray(md_m, float), np.asarray(inc_deg, float), np.asarray(az_deg, float))
    return mc['v']


def sigmas(C: np.ndarray) -> Tuple[float, float, float, float, float, float]:
    """(NN, EE, VV, NE, NV, EV) variances and covariances, in the order the ISCWSA example tabulates them."""
    return (float(C[0, 0]), float(C[1, 1]), float(C[2, 2]), float(C[0, 1]), float(C[0, 2]), float(C[1, 2]))


EXAMPLE = os.path.join(os.path.dirname(__file__), 'reference', 'iscwsa_rev4_example.json')


def check_example(atol: float = 5e-5, rtol: float = 1e-3) -> dict:
    """Recompute the ISCWSA's own MWD Rev 4 example (reference/iscwsa_rev4_example.json) and compare every row: each
    error source's covariance and the total at four depths. A row agrees when every one of its six terms is within
    `atol` m^2 or `rtol` of the published value."""
    with open(EXAMPLE, encoding='utf-8') as f:
        d = json.load(f)
    h, s, vd = d['header'], d['survey'], d['vd']
    md = np.array(s['md'], float); inc = np.array(s['inc'], float); az = np.array(s['azi'], float)
    tvd = min_curve_tvd(md, inc, az)
    r = well_uncertainty(md, inc, az, tvd, math.degrees(h['declination']), math.degrees(h['dip']), h['b_total'], h['G'])
    rows, bad, worst = 0, [], 0.0
    for k in range(len(vd['md'])):
        m, src = vd['md'][k], vd['source'][k]
        i = int(np.flatnonzero(md == m)[0])
        C = r['cov'][i] if src in ('Totals', 'TOTAL') else r['per_source'][src][i]
        got = np.array(sigmas(C), float)
        ref = np.array([vd[c][k] for c in ('nn', 'ee', 'vv', 'ne', 'nv', 'ev')], float)
        err = np.abs(got - ref)
        rel = np.where(np.abs(ref) > 0, err / np.maximum(np.abs(ref), 1e-300), 0.0)
        worst = max(worst, float(np.max(np.where(err > atol, rel, 0.0))))
        rows += 1
        if ((err > atol) & (rel > rtol)).any():
            bad.append({'md_m': m, 'source': src, 'published': ref.round(6).tolist(), 'computed': got.round(6).tolist()})
    tot = {m: sigmas(r['cov'][int(np.flatnonzero(md == m)[0])]) for m in sorted(set(vd['md']))}
    return {'rows': rows, 'sources': len(set(vd['source'])) - 1, 'depths_m': sorted(set(vd['md'])), 'disagree': bad,
            'worst_relative': worst, 'totals': {str(m): [round(x, 4) for x in v] for m, v in tot.items()},
            'source': d['source'], 'license': d['license']}
