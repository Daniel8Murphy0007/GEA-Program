# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gravity_reference - THE CITED EXTERNAL GRAVITY REFERENCE LAYER.

Until now the only gravity numbers the surveying chain could check a live
G6-class stream against were its own - the loopback fixture. This module
gives the chain an EXTERNAL, CITED, PUBLIC reference: the WGS84 normal
gravity closed form plus the standard free-air correction, evaluated at a
surveyed site's latitude and elevation. It is an OBSERVATIONAL REFERENCE
STANDARD - a comparison target, labeled as such, never an input to the
program's own model. Program predictions are compared AGAINST it; it does
not seed them.

CITATIONS (all public standards):
  * WGS84 Somigliana normal gravity: NGA (NIMA) TR8350.2, 3rd ed., 2000,
    "Department of Defense World Geodetic System 1984", eq. 4-1 with the
    defining constants of Table 3.4 (gamma_e = 9.7803253359 m/s^2,
    k = 0.00193185265241, e^2 = 0.00669437999013).
  * Free-air gradient 0.3086 mGal/m: standard geodetic value (Heiskanen &
    Moritz, "Physical Geodesy", 1967, sec. 3-2; used by BGI/IGSN71 practice).
  * KTB main-hole site: Windischeschenbach, Germany, 49.8156 N, 12.1204 E,
    ground elevation ~513.6 m - ICDP/KTB published site description
    (GFZ ICDP KTB operational dataset; the same source family as the
    library's KTB log excerpts).

The register-map rule is UNCHANGED: G6 register maps remain user-supplied
and citation-mandatory (modbus.py). What this module removes is the
self-reference - a loaded stream's gravity values can now be checked
against a number the repository did not generate.
"""

from __future__ import annotations

import math
from typing import Dict

# WGS84 defining constants - NGA TR8350.2 Table 3.4 (cited above)
GAMMA_EQUATOR_MS2 = 9.7803253359
SOMIGLIANA_K = 0.00193185265241
WGS84_E2 = 0.00669437999013
FREE_AIR_MGAL_PER_M = 0.3086          # Heiskanen & Moritz 1967 (cited above)
MGAL_PER_MS2 = 1e5

# KTB main-hole site - ICDP/KTB published description (cited above)
KTB_LAT_DEG = 49.8156
KTB_LON_DEG = 12.1204
KTB_ELEV_M = 513.6


def somigliana_normal_gravity_ms2(lat_deg: float) -> float:
    """WGS84 normal gravity on the ellipsoid at geodetic latitude [m/s^2].

    gamma = gamma_e * (1 + k sin^2 phi) / sqrt(1 - e^2 sin^2 phi)
    (NGA TR8350.2 eq. 4-1; closed form, no approximation).
    """
    s2 = math.sin(math.radians(lat_deg)) ** 2
    return (GAMMA_EQUATOR_MS2 * (1.0 + SOMIGLIANA_K * s2)
            / math.sqrt(1.0 - WGS84_E2 * s2))


def free_air_correction_ms2(elev_m: float) -> float:
    """Standard free-air reduction from ellipsoid/ground elevation [m/s^2]."""
    return -FREE_AIR_MGAL_PER_M * elev_m / MGAL_PER_MS2


def site_reference_gravity(lat_deg: float, elev_m: float) -> Dict:
    """Cited external surface-gravity reference for a surveyed site."""
    gamma0 = somigliana_normal_gravity_ms2(lat_deg)
    fac = free_air_correction_ms2(elev_m)
    return {
        'lat_deg': lat_deg,
        'elev_m': elev_m,
        'normal_gravity_ms2': gamma0,
        'free_air_correction_ms2': fac,
        'reference_gravity_ms2': gamma0 + fac,
        'kind': 'OBSERVATIONAL_REFERENCE_STANDARD',
        'citation': ('WGS84 Somigliana (NGA TR8350.2 eq. 4-1) + free-air '
                     '0.3086 mGal/m (Heiskanen & Moritz 1967); NOT a GEA '
                     'derivation - the external comparison target'),
        'note': ('local terrain/Bouguer/anomaly effects are NOT modeled '
                 'here; agreement is expected at the ~100 mGal (1e-3 m/s^2) '
                 'band, not exactly - the reference bounds sanity, it does '
                 'not replace a site gravity survey'),
    }


def ktb_site_reference() -> Dict:
    """The KTB main-hole site reference - the library's anchor well."""
    out = site_reference_gravity(KTB_LAT_DEG, KTB_ELEV_M)
    out['site'] = ('KTB Hauptbohrung, Windischeschenbach (49.8156 N, '
                   '12.1204 E, ~513.6 m) - ICDP/KTB published site '
                   'description')
    return out


def check_stream_gravity(measured_ms2: float, lat_deg: float,
                         elev_m: float, band_ms2: float = 1.5e-3) -> Dict:
    """QC a measured surface gravity against the cited external reference.

    band_ms2 default 1.5e-3 m/s^2 (~150 mGal) spans normal regional
    anomaly + instrument-datum offsets; a live stream outside the band is
    flagged for datum/units review, never silently accepted."""
    ref = site_reference_gravity(lat_deg, elev_m)
    delta = measured_ms2 - ref['reference_gravity_ms2']
    return {
        'reference': ref,
        'measured_ms2': measured_ms2,
        'delta_ms2': delta,
        'delta_mgal': delta * MGAL_PER_MS2,
        'within_band': abs(delta) <= band_ms2,
        'verdict': ('CONSISTENT_WITH_CITED_REFERENCE' if abs(delta) <= band_ms2
                    else 'DATUM_OR_UNITS_REVIEW - measured value outside the '
                         'regional-anomaly band of the cited reference'),
    }
