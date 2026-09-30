# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""forward_model - Part 2 of the subsurface surveying tool: THE SENSING
KERNEL (the buoyancy column first).

The forward model answers: given a strata column, what does the gravity
channel read? - and its inverse-lite answers: given measured borehole
gravity, what density column does the ground imply? Every constant in the
chain is a standard value with its source named:

    g   = 9.80665 m/s^2 (standard gravity)
    G   = 6.67430e-11 m^3/kg/s^2 (CODATA 2018)
    R   = 6371.0088 km (IUGG mean radius)
    free-air gradient F = 2*g/R = 0.30785 mGal/m       (composed from the above)

THE ENVELOPE: the interstation borehole-gravity relation
dg = (F - 4*pi*G*rho)*dz is the classical Bouguer-slab form (Hammer 1950;
Telford, Geldart and Sheriff 1990 ch. 2), with the Earth itself as the
reference body and every constant entering it a standard value.

HONESTY
    - The KTB validation is partly circular BY THE ARCHIVE'S NATURE: BHGM
      density is itself derived from measured gravity by the vendor's own
      inversion, so agreement measures how closely the constant chain
      {g, G, R} reproduces the vendor's constants - a CONSTANTS test
      (still falsifiable: a wrong g, G or R shows up directly), not an
      independent strata test. Stated here and in the returned report.
    - Archive null stations (RHO recorded as 0.00 g/cc) are excluded from
      filtered statistics WITH the exclusion counted and disclosed; raw
      statistics are reported alongside - nothing silently dropped.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional

from .profile_catalog import CATALOG

# Standard constants, cited
G_STD = 6.67430e-11                # CODATA 2018 Newtonian constant of gravitation, m^3 kg^-1 s^-2
G_SURFACE_STD = 9.80665            # standard acceleration of gravity, ISO 80000-3 / CGPM 1901, m s^-2
R_EARTH_M = 6371008.8              # IUGG mean Earth radius (R1), m
FREE_AIR_STD = 2.0 * G_SURFACE_STD / R_EARTH_M      # s^-2
MGAL = 1.0e5                       # 1 m/s^2 = 1e5 mGal

RHO_NULL_GCC = 0.5                 # below this the archive cell is a null, not rock


def predict_delta_g_mgal(rho_gcc: float, dz_m: float) -> float:
    """Forward kernel: interstation gravity change for a slab of density
    rho [g/cc] over dz [m], GEA constants throughout."""
    rho = rho_gcc * 1000.0
    return (FREE_AIR_STD - 4.0 * math.pi * G_STD * rho) * dz_m * MGAL


def implied_density_gcc(dg_mgal: float, dz_m: float) -> float:
    """Inverse-lite: the density the ground implies for a measured
    interstation gravity change - the sensing direction."""
    return (FREE_AIR_STD - dg_mgal / MGAL / dz_m) / (4.0 * math.pi * G_STD) / 1000.0


def forward_gravity_profile(depths_m: List[float], rho_gcc: List[float]) -> List[float]:
    """Predicted gravity profile (mGal, relative to the first station) from a
    density column, GEA constants throughout."""
    g = [0.0]
    for i in range(1, len(depths_m)):
        mid = 0.5 * (rho_gcc[i] + rho_gcc[i - 1])
        g.append(g[-1] + predict_delta_g_mgal(mid, depths_m[i] - depths_m[i - 1]))
    return g


def ktb_gravity_test(entry: str = 'ktb_hb_bhgm_density') -> Dict:
    """The K2 validation against REAL borehole gravimetry (KTB BHGM, 197
    stations to 8,400 m): predict interstation gravity from the tool's
    density column with GEA constants; invert measured gravity back to an
    implied density column; report raw AND null-filtered statistics with the
    circularity caveat stated in the result itself."""
    st = CATALOG[entry].stream()
    z = [float(v) for v in st.index]
    grav = [float(v) for v in st.channels['GRAV'].values]
    rho = [float(v) for v in st.channels['RHO'].values]
    raw, filt = [], []
    for i in range(1, len(z)):
        dz = z[i] - z[i - 1]
        if dz <= 0:
            continue
        rho_mid = 0.5 * (rho[i] + rho[i - 1])
        rec = {
            'depth_m': z[i], 'dz_m': dz, 'rho_gcc': rho_mid,
            'dg_pred_mgal': predict_delta_g_mgal(rho_mid, dz),
            'dg_meas_mgal': grav[i] - grav[i - 1],
        }
        rec['residual_mgal'] = rec['dg_pred_mgal'] - rec['dg_meas_mgal']
        rec['rho_implied_gcc'] = implied_density_gcc(rec['dg_meas_mgal'], dz)
        raw.append(rec)
        if rho[i] > RHO_NULL_GCC and rho[i - 1] > RHO_NULL_GCC:
            filt.append(rec)

    def _stats(rows):
        if len(rows) < 3:
            return {'n': len(rows), 'status': 'REFUSED_THIN_DATA'}
        p = [r['dg_pred_mgal'] for r in rows]
        m = [r['dg_meas_mgal'] for r in rows]
        res = [r['residual_mgal'] for r in rows]
        dr = [r['rho_implied_gcc'] - r['rho_gcc'] for r in rows]
        mp, mm = statistics.mean(p), statistics.mean(m)
        num = sum((a - mp) * (b - mm) for a, b in zip(p, m))
        den = math.sqrt(sum((a - mp) ** 2 for a in p) * sum((b - mm) ** 2 for b in m))
        return {
            'n': len(rows),
            'correlation': num / den if den else float('nan'),
            'mean_residual_mgal': statistics.mean(res),
            'stdev_residual_mgal': statistics.pstdev(res),
            'worst_residual_mgal': max(abs(r) for r in res),
            'implied_density_mean_offset_gcc': statistics.mean(dr),
            'implied_density_stdev_gcc': statistics.pstdev(dr),
        }

    return {
        'entry': entry,
        'constants': {'G_STD': G_STD, 'g_std': G_SURFACE_STD,
                      'R_earth_m': R_EARTH_M,
                      'free_air_std_mgal_per_m': FREE_AIR_STD * MGAL},
        'raw': _stats(raw),
        'null_filtered': _stats(filt),
        'null_stations_excluded': len(raw) - len(filt),
        'intervals': raw,
        'circularity_caveat': (
            'BHGM density is itself gravity-derived by the vendor inversion; '
            'this test uses standard constants (CODATA 2018 G, standard gravity, '
            'IUGG mean radius) against the vendor loop - a constants test, not an '
            'independent strata test. It remains falsifiable: a wrong g, G or R '
            'appears directly as bias here.'),
    }
