# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""geodesy - which datum a position is on, and what it costs to assume.

Every position in this program before this module was a bare pair of numbers: a station at
31.0, -102.0; a rig from a permit export; a sensor in an array CSV; a position on a track.
None of them said which datum they were on, and the program compared them as if that question
had an obvious answer. It does not. A regulator's drilling-permit export in Texas may be on
NAD27; a network's station metadata is on WGS84; a modern survey is on NAD83. Those are three
different sets of numbers for the same ground, tens of metres apart - the program computes the
separation at the site rather than quoting one, because it varies across the country.

That distance matters here because of what the leg claims. A track's position ellipse is
hundreds of metres across on a good day. A datum error of the same order does not look like an
error: the arithmetic is right, the ellipse is honest, and the whole picture sits in the wrong
place on the map. Nothing in the program could have noticed.

So: a position carries its CRS, every conversion carries the accuracy of the conversion, and a
position whose datum nobody stated is UNKNOWN - comparable with others only under an assumption
that is written down and printed.

What this module will not call a measurement:

- a position whose datum was not stated: assuming it matches is an assumption, not a conversion;
- a three-parameter datum shift as a survey-grade transformation - NAD27 to WGS84 here is the
  published CONUS-mean shift, good to several metres, where a grid transformation (NADCON) is
  good to a few centimetres and is not in this program;
- NAD83 and WGS84 as the same datum: no shift is applied between them, and the metre or two
  that costs is carried as the accuracy of saying so, not hidden;
- a projected grid distance as a ground distance: the scale factor is printed beside it;
- an elevation as an orthometric height: heights here are ellipsoidal, and the geoid separation
  (tens of metres in Texas) is not in this program;
- a projection to better than a centimetre: the transverse Mercator and Lambert series here close
  on themselves to a few millimetres inside a zone, which is four orders below anything this
  program claims about a position, and is not survey software.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Sequence, Tuple

# a, 1/f
ELLIPSOIDS: Dict[str, Tuple[float, float]] = {
    'WGS84': (6378137.0, 298.257223563),
    'GRS80': (6378137.0, 298.257222101),
    'CLARKE1866': (6378206.4, 294.9786982),
}

# datum -> ellipsoid, the three-parameter shift to WGS84 (metres, geocentric), and what that shift is worth
DATUMS: Dict[str, dict] = {
    'WGS84': {'ellipsoid': 'WGS84', 'to_wgs84': (0.0, 0.0, 0.0), 'accuracy_m': 0.0,
              'note': 'the reference frame this program converts into'},
    'NAD83': {'ellipsoid': 'GRS80', 'to_wgs84': (0.0, 0.0, 0.0), 'accuracy_m': 2.0,
              'note': 'NAD83 and WGS84 agree to within about a metre or two in the lower 48 at the epochs this program sees; no shift is '
                      'applied and that metre or two is carried as the accuracy of saying so'},
    'NAD27': {'ellipsoid': 'CLARKE1866', 'to_wgs84': (-8.0, 160.0, 176.0), 'accuracy_m': 10.0,
              'note': 'the published CONUS-mean three-parameter shift (DMA TR8350.2). A grid transformation (NADCON) is better by several '
                      'metres and is not in this program; this is good to roughly ten'},
    'UNKNOWN': {'ellipsoid': 'WGS84', 'to_wgs84': (0.0, 0.0, 0.0), 'accuracy_m': None,
                'note': 'nobody said which datum these numbers are on. They are used as given, which is an assumption, not a conversion'},
}

ALIASES = {'WGS 84': 'WGS84', 'WGS-84': 'WGS84', 'EPSG:4326': 'WGS84', '4326': 'WGS84', 'WGS1984': 'WGS84',
           'NAD 83': 'NAD83', 'NAD-83': 'NAD83', 'EPSG:4269': 'NAD83', '4269': 'NAD83', 'NAD83(2011)': 'NAD83', 'GRS80': 'NAD83',
           'NAD 27': 'NAD27', 'NAD-27': 'NAD27', 'EPSG:4267': 'NAD27', '4267': 'NAD27', 'CLARKE1866': 'NAD27',
           '': 'UNKNOWN', 'NONE': 'UNKNOWN', 'UNSTATED': 'UNKNOWN', 'UNKNOWN': 'UNKNOWN'}

US_SURVEY_FOOT = 1200.0 / 3937.0            # exactly, by definition
INTERNATIONAL_FOOT = 0.3048                 # exactly, by definition
LENGTH_UNITS = {'m': 1.0, 'metre': 1.0, 'meter': 1.0, 'meters': 1.0, 'metres': 1.0,
                'usft': US_SURVEY_FOOT, 'us_survey_foot': US_SURVEY_FOOT, 'ussurveyfoot': US_SURVEY_FOOT, 'sft': US_SURVEY_FOOT,
                'ft': INTERNATIONAL_FOOT, 'foot': INTERNATIONAL_FOOT, 'feet': INTERNATIONAL_FOOT, 'intlft': INTERNATIONAL_FOOT}


def datum_name(s: Optional[str]) -> str:
    """Whatever the file called it -> one of DATUMS. Anything unrecognised is UNKNOWN, never guessed."""
    k = str(s or '').strip().upper().replace('_', '').replace('  ', ' ')
    if k in DATUMS:
        return k
    if k in ALIASES:
        return ALIASES[k]
    k2 = k.replace(' ', '').replace('-', '')
    for a, v in ALIASES.items():
        if a.replace(' ', '').replace('-', '') == k2:
            return v
    return 'UNKNOWN'


def length_unit(s: Optional[str]) -> Tuple[float, str]:
    """A unit name -> (metres per unit, the canonical name). The US survey foot and the international foot
    differ by two parts per million - 0.6 m over 300 km, which is more than a position ellipse - so they are
    never treated as the same thing."""
    k = str(s or 'm').strip().lower().replace(' ', '').replace('-', '')
    if k not in LENGTH_UNITS:
        raise ValueError(f"unknown length unit '{s}'; known: " + ', '.join(sorted(set(LENGTH_UNITS))))
    v = LENGTH_UNITS[k]
    name = 'm' if v == 1.0 else ('usft' if v == US_SURVEY_FOOT else 'ft')
    return v, name


# ---------------------------------------------------------------------------
# the ellipsoid: geodetic <-> geocentric, and distance on it
# ---------------------------------------------------------------------------
def _ell(name: str) -> Tuple[float, float, float]:
    a, inv_f = ELLIPSOIDS[name]
    f = 1.0 / inv_f
    return a, f, f * (2.0 - f)              # a, f, e^2


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h_m: float, ellipsoid: str = 'WGS84') -> Tuple[float, float, float]:
    a, f, e2 = _ell(ellipsoid)
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    s, c = math.sin(lat), math.cos(lat)
    N = a / math.sqrt(1.0 - e2 * s * s)
    return ((N + h_m) * c * math.cos(lon), (N + h_m) * c * math.sin(lon), (N * (1.0 - e2) + h_m) * s)


def ecef_to_geodetic(x: float, y: float, z: float, ellipsoid: str = 'WGS84') -> Tuple[float, float, float]:
    """Bowring's method, then two Newton steps: better than a millimetre everywhere this program works."""
    a, f, e2 = _ell(ellipsoid)
    b = a * (1.0 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    if p < 1e-9:
        return (90.0 if z >= 0 else -90.0, 0.0, abs(z) - b)
    th = math.atan2(z * a, p * b)
    lat = math.atan2(z + ep2 * b * math.sin(th) ** 3, p - e2 * a * math.cos(th) ** 3)
    for _ in range(2):
        s = math.sin(lat)
        N = a / math.sqrt(1.0 - e2 * s * s)
        h = p / math.cos(lat) - N
        lat = math.atan2(z, p * (1.0 - e2 * N / (N + h)))
    s = math.sin(lat)
    N = a / math.sqrt(1.0 - e2 * s * s)
    h = p / math.cos(lat) - N
    return (math.degrees(lat), math.degrees(math.atan2(y, x)), h)


def geodesic_m(lat1: float, lon1: float, lat2: float, lon2: float, ellipsoid: str = 'WGS84') -> dict:
    """Vincenty's inverse formula: the distance along the ellipsoid and the azimuths at both ends. The
    spherical formula the rest of the leg uses is half a percent out; at 100 km that is 500 m, which is a
    larger error than anything else in a bearing crossing."""
    a, f, _ = _ell(ellipsoid)
    b = a * (1.0 - f)
    L = math.radians(lon2 - lon1)
    U1, U2 = math.atan((1 - f) * math.tan(math.radians(lat1))), math.atan((1 - f) * math.tan(math.radians(lat2)))
    sU1, cU1, sU2, cU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    sin_sig = cos_sig = sig = cos2a = cos2sm = 0.0
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        sin_sig = math.hypot(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)
        if sin_sig == 0.0:
            return {'distance_m': 0.0, 'azimuth_deg': 0.0, 'reverse_azimuth_deg': 0.0, 'ellipsoid': ellipsoid, 'converged': True}
        cos_sig = sU1 * sU2 + cU1 * cU2 * cl
        sig = math.atan2(sin_sig, cos_sig)
        sin_a = cU1 * cU2 * sl / sin_sig
        cos2a = max(1.0 - sin_a * sin_a, 1e-18)
        cos2sm = cos_sig - 2.0 * sU1 * sU2 / cos2a
        C = f / 16.0 * cos2a * (4.0 + f * (4.0 - 3.0 * cos2a))
        prev = lam
        lam = L + (1 - C) * f * sin_a * (sig + C * sin_sig * (cos2sm + C * cos_sig * (-1 + 2 * cos2sm * cos2sm)))
        if abs(lam - prev) < 1e-12:
            break
    else:
        return {'distance_m': None, 'azimuth_deg': None, 'reverse_azimuth_deg': None, 'ellipsoid': ellipsoid, 'converged': False,
                'detail': 'Vincenty did not converge (nearly antipodal points); no distance is claimed'}
    u2 = cos2a * (a * a - b * b) / (b * b)
    A = 1 + u2 / 16384.0 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024.0 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    d_sig = B * sin_sig * (cos2sm + B / 4.0 * (cos_sig * (-1 + 2 * cos2sm ** 2) - B / 6.0 * cos2sm * (-3 + 4 * sin_sig ** 2) * (-3 + 4 * cos2sm ** 2)))
    sl, cl = math.sin(lam), math.cos(lam)
    return {'distance_m': b * A * (sig - d_sig),
            'azimuth_deg': math.degrees(math.atan2(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)) % 360.0,
            'reverse_azimuth_deg': math.degrees(math.atan2(cU1 * sl, -sU1 * cU2 + cU1 * sU2 * cl)) % 360.0,
            'ellipsoid': ellipsoid, 'converged': True}


# ---------------------------------------------------------------------------
# datum shifts
# ---------------------------------------------------------------------------
def to_wgs84(lat: float, lon: float, h: float = 0.0, datum: str = 'WGS84') -> dict:
    """A position on `datum` -> the same ground on WGS84, with what the conversion is worth."""
    d = datum_name(datum)
    spec = DATUMS[d]
    dx, dy, dz = spec['to_wgs84']
    if d in ('WGS84', 'NAD83', 'UNKNOWN'):
        out = {'lat': float(lat), 'lon': float(lon), 'h_m': float(h), 'from': d, 'shift_m': 0.0,
               'accuracy_m': spec['accuracy_m'], 'applied': False, 'note': spec['note']}
    else:
        x, y, z = geodetic_to_ecef(lat, lon, h, spec['ellipsoid'])
        la, lo, hh = ecef_to_geodetic(x + dx, y + dy, z + dz, 'WGS84')
        g = geodesic_m(lat, lon, la, lo)
        out = {'lat': la, 'lon': lo, 'h_m': hh, 'from': d, 'shift_m': (round(g['distance_m'], 3) if g['distance_m'] is not None else None),
               'shift_bearing_deg': (round(g['azimuth_deg'], 1) if g['azimuth_deg'] is not None else None),
               'accuracy_m': spec['accuracy_m'], 'applied': True, 'note': spec['note'],
               'parameters_m': {'dx': dx, 'dy': dy, 'dz': dz}}
    if d == 'UNKNOWN':
        out['status'] = 'UNKNOWN_DATUM'
        out['detail'] = ('the datum of this position was not stated, so it is used as given. If it is on NAD27 the ground it names is tens of '
                         'metres from where this program puts it')
    else:
        out['status'] = 'OK'
    return out


def transform(lat: float, lon: float, h: float, src: str, dst: str) -> dict:
    """Between any two datums here, through WGS84. The accuracies add."""
    a = to_wgs84(lat, lon, h, src)
    s, d = datum_name(src), datum_name(dst)
    if d in ('WGS84', 'UNKNOWN') or d == s:
        out = dict(a)
        out.update({'to': d if d != 'UNKNOWN' else s})
        return out
    spec = DATUMS[d]
    dx, dy, dz = spec['to_wgs84']
    x, y, z = geodetic_to_ecef(a['lat'], a['lon'], a['h_m'], 'WGS84')
    la, lo, hh = ecef_to_geodetic(x - dx, y - dy, z - dz, spec['ellipsoid'])
    g = geodesic_m(lat, lon, la, lo)
    acc = [v for v in (a.get('accuracy_m'), spec['accuracy_m']) if v is not None]
    return {'lat': la, 'lon': lo, 'h_m': hh, 'from': s, 'to': d, 'applied': True,
            'shift_m': (round(g['distance_m'], 3) if g['distance_m'] is not None else None),
            'shift_bearing_deg': (round(g['azimuth_deg'], 1) if g['azimuth_deg'] is not None else None),
            'accuracy_m': (round(math.sqrt(sum(v * v for v in acc)), 2) if acc else None),
            'status': 'OK' if s != 'UNKNOWN' else 'UNKNOWN_DATUM',
            'note': f"{DATUMS[s]['note']}; then {spec['note']}"}


def datum_separation_m(lat: float, lon: float, a_datum: str = 'NAD27', b_datum: str = 'WGS84') -> dict:
    """How far apart the two datums put the same ground, here. This is the number that matters: it is not a
    constant, and quoting one from memory is how a position ends up in the wrong place."""
    pa, pb = to_wgs84(lat, lon, 0.0, a_datum), to_wgs84(lat, lon, 0.0, b_datum)
    g = geodesic_m(pa['lat'], pa['lon'], pb['lat'], pb['lon'])
    return {'at': {'lat': lat, 'lon': lon}, 'datums': [datum_name(a_datum), datum_name(b_datum)],
            'separation_m': (round(g['distance_m'], 2) if g['distance_m'] is not None else None),
            'bearing_deg': (round(g['azimuth_deg'], 1) if g['azimuth_deg'] is not None else None),
            'basis': 'the same numbers read on each datum, both converted to WGS84, and the ground distance between the results',
            'not_a_measurement': ['this separation anywhere but here: it varies across the country']}


# ---------------------------------------------------------------------------
# projections: transverse Mercator (UTM) and Lambert conformal conic (state plane)
# ---------------------------------------------------------------------------
def utm_zone(lon_deg: float) -> int:
    return int(math.floor((lon_deg + 180.0) / 6.0) % 60) + 1


def _tm(lat0, lon0, k0, fe, fn, ellipsoid):
    return {'kind': 'tm', 'lat0': lat0, 'lon0': lon0, 'k0': k0, 'fe': fe, 'fn': fn, 'ellipsoid': ellipsoid}


def _lcc(lat0, lon0, lat1, lat2, fe, fn, ellipsoid):
    return {'kind': 'lcc', 'lat0': lat0, 'lon0': lon0, 'lat1': lat1, 'lat2': lat2, 'fe': fe, 'fn': fn, 'ellipsoid': ellipsoid}


def utm(zone: int, north: bool = True, ellipsoid: str = 'WGS84') -> dict:
    return _tm(0.0, -183.0 + 6.0 * zone, 0.9996, 500000.0, 0.0 if north else 10000000.0, ellipsoid)


# Texas state plane, NAD83, metres (the false northings step by a million per zone, which is how you
# tell at a glance which zone a northing came from)
ZONES: Dict[str, dict] = {
    'TX_NORTH': _lcc(34.0, -101.5, 34.65, 36.1833333333, 200000.0, 1000000.0, 'GRS80'),
    'TX_NORTH_CENTRAL': _lcc(31.6666666667, -98.5, 32.1333333333, 33.9666666667, 600000.0, 2000000.0, 'GRS80'),
    'TX_CENTRAL': _lcc(29.6666666667, -100.3333333333, 30.1166666667, 31.8833333333, 700000.0, 3000000.0, 'GRS80'),
    'TX_SOUTH_CENTRAL': _lcc(27.8333333333, -99.0, 28.3833333333, 30.2833333333, 600000.0, 4000000.0, 'GRS80'),
    'TX_SOUTH': _lcc(25.6666666667, -98.5, 26.1666666667, 27.8333333333, 300000.0, 5000000.0, 'GRS80'),
}


def _m_arc(lat, a, e2):
    """Meridian arc from the equator."""
    e4, e6 = e2 * e2, e2 * e2 * e2
    return a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
                - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
                + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
                - (35 * e6 / 3072) * math.sin(6 * lat))


def project(lat_deg: float, lon_deg: float, zone: dict) -> dict:
    """Geodetic -> grid easting/northing, with the point's scale factor and grid convergence beside it."""
    a, f, e2 = _ell(zone['ellipsoid'])
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    lon0 = math.radians(zone['lon0'])
    if zone['kind'] == 'tm':
        k0 = zone['k0']
        ep2 = e2 / (1 - e2)
        s, c, t = math.sin(lat), math.cos(lat), math.tan(lat)
        N = a / math.sqrt(1 - e2 * s * s)
        T, C = t * t, ep2 * c * c
        A = (lon - lon0) * c
        M = _m_arc(lat, a, e2)
        M0 = _m_arc(math.radians(zone['lat0']), a, e2)
        E = zone['fe'] + k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120)
        Nn = zone['fn'] + k0 * (M - M0 + N * t * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
                                                 + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720))
        k = k0 * (1 + (1 + C) * A * A / 2 + (5 - 4 * T + 42 * C + 13 * C * C - 28 * ep2) * A ** 4 / 24)
        conv = math.degrees(math.atan(t * math.sin(lon - lon0)))
    else:
        e = math.sqrt(e2)

        def m(p):
            return math.cos(p) / math.sqrt(1 - e2 * math.sin(p) ** 2)

        def t_(p):
            return math.tan(math.pi / 4 - p / 2) / ((1 - e * math.sin(p)) / (1 + e * math.sin(p))) ** (e / 2)

        p1, p2, p0 = math.radians(zone['lat1']), math.radians(zone['lat2']), math.radians(zone['lat0'])
        m1, m2 = m(p1), m(p2)
        t1, t2, t0, tt = t_(p1), t_(p2), t_(p0), t_(lat)
        n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2)) if abs(p1 - p2) > 1e-12 else math.sin(p1)
        F = m1 / (n * t1 ** n)
        r0, r = a * F * t0 ** n, a * F * tt ** n
        th = n * (lon - lon0)
        E = zone['fe'] + r * math.sin(th)
        Nn = zone['fn'] + r0 - r * math.cos(th)
        k = (m1 / m(lat)) * (tt / t1) ** n if abs(math.cos(lat)) > 1e-12 else float('nan')
        conv = math.degrees(th)
    return {'easting_m': E, 'northing_m': Nn, 'scale_factor': k, 'convergence_deg': conv, 'zone': zone,
            'not_a_measurement': ['a grid distance as a ground distance: multiply by the scale factor printed here']}


def unproject(E: float, N: float, zone: dict) -> Tuple[float, float]:
    """Grid easting/northing -> geodetic."""
    a, f, e2 = _ell(zone['ellipsoid'])
    lon0 = math.radians(zone['lon0'])
    if zone['kind'] == 'tm':
        k0 = zone['k0']
        e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
        ep2 = e2 / (1 - e2)
        M = _m_arc(math.radians(zone['lat0']), a, e2) + (N - zone['fn']) / k0
        mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
        p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
              + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
        s, c, t = math.sin(p1), math.cos(p1), math.tan(p1)
        C1, T1 = ep2 * c * c, t * t
        N1 = a / math.sqrt(1 - e2 * s * s)
        R1 = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
        D = (E - zone['fe']) / (N1 * k0)
        lat = p1 - (N1 * t / R1) * (D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D ** 4 / 24
                                    + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D ** 6 / 720)
        lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
                      + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D ** 5 / 120) / c
    else:
        e = math.sqrt(e2)

        def m(p):
            return math.cos(p) / math.sqrt(1 - e2 * math.sin(p) ** 2)

        def t_(p):
            return math.tan(math.pi / 4 - p / 2) / ((1 - e * math.sin(p)) / (1 + e * math.sin(p))) ** (e / 2)

        p1_, p2_, p0 = math.radians(zone['lat1']), math.radians(zone['lat2']), math.radians(zone['lat0'])
        m1, m2 = m(p1_), m(p2_)
        t1, t2, t0 = t_(p1_), t_(p2_), t_(p0)
        n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2)) if abs(p1_ - p2_) > 1e-12 else math.sin(p1_)
        F = m1 / (n * t1 ** n)
        r0 = a * F * t0 ** n
        dE, dN = E - zone['fe'], r0 - (N - zone['fn'])
        r = math.copysign(math.hypot(dE, dN), n)
        th = math.atan2(dE, dN)
        tt = (r / (a * F)) ** (1.0 / n)
        lat = math.pi / 2 - 2 * math.atan(tt)
        for _ in range(12):
            s = e * math.sin(lat)
            lat = math.pi / 2 - 2 * math.atan(tt * ((1 - s) / (1 + s)) ** (e / 2))
        lon = th / n + lon0
    return math.degrees(lat), math.degrees(lon)


def grid_to_wgs84(E: float, N: float, zone_name: str, datum: str = 'NAD83', unit: str = 'm') -> dict:
    """A projected coordinate in a permit export -> WGS84 lat/lon, saying what was assumed on the way."""
    if zone_name not in ZONES and not zone_name.upper().startswith('UTM'):
        raise ValueError(f"unknown zone '{zone_name}'; known: " + ', '.join(sorted(ZONES)) + ', UTM<n>N, UTM<n>S')
    if zone_name in ZONES:
        if datum_name(datum) == 'NAD27':
            raise ValueError(f"the {zone_name} definition here is the NAD83 one. NAD27 state plane in Texas is a different grid - different false "
                             'origins, and feet rather than metres - so reading NAD27 coordinates on it would be wrong by thousands of metres. '
                             'Convert the export to NAD83 or give the positions as latitude and longitude')
        z = ZONES[zone_name]
    else:
        q = zone_name.upper().replace('UTM', '')
        north = not q.endswith('S')
        z = utm(int(q.rstrip('NS')), north, 'GRS80' if datum_name(datum) == 'NAD83' else ('CLARKE1866' if datum_name(datum) == 'NAD27' else 'WGS84'))
    scale, uname = length_unit(unit)
    lat, lon = unproject(E * scale, N * scale, z)
    out = to_wgs84(lat, lon, 0.0, datum)
    out.update({'zone': zone_name, 'unit': uname, 'grid': {'easting_m': E * scale, 'northing_m': N * scale},
                'on_zone_datum': {'lat': lat, 'lon': lon}})
    return out


# ---------------------------------------------------------------------------
# what a position is, and whether a set of them may be compared
# ---------------------------------------------------------------------------
@dataclass
class Position:
    lat: float
    lon: float
    datum: str = 'UNKNOWN'
    h_m: float = 0.0
    label: str = ''

    def to_dict(self) -> dict:
        return asdict(self)

    def wgs84(self) -> dict:
        return to_wgs84(self.lat, self.lon, self.h_m, self.datum)


def check_set(positions: Sequence[Position], assume: Optional[str] = None) -> dict:
    """May these positions be compared with each other? They may when they are all on one datum, or when
    every datum is stated and the ones that differ have been converted. The answer when some are UNKNOWN is
    not 'no' - it is 'only under an assumption', and the assumption is printed with what it would cost if it
    is wrong, computed here rather than quoted."""
    if not positions:
        return {'status': 'EMPTY', 'datums': [], 'n': 0}
    names = [datum_name(p.datum) for p in positions]
    uniq = sorted(set(names))
    known = [n for n in uniq if n != 'UNKNOWN']
    n_unknown = names.count('UNKNOWN')
    lat0 = sum(p.lat for p in positions) / len(positions)
    lon0 = sum(p.lon for p in positions) / len(positions)
    out = {'protocol': 'geodesy.check_set', 'n': len(positions), 'datums': uniq, 'n_unknown': n_unknown,
           'at': {'lat': round(lat0, 5), 'lon': round(lon0, 5)},
           'basis': 'the datums the positions carry, and the ground separation between them computed at the centre of the set',
           'not_a_measurement': ['a position whose datum was not stated as being on any particular datum',
                                 'the separation between two datums anywhere but here']}
    seps = []
    for i in range(len(known)):
        for j in range(i + 1, len(known)):
            s = datum_separation_m(lat0, lon0, known[i], known[j])
            seps.append({'datums': [known[i], known[j]], 'separation_m': s['separation_m'], 'bearing_deg': s['bearing_deg']})
    out['separations'] = seps
    worst = max([s['separation_m'] for s in seps if s['separation_m'] is not None] or [0.0])
    if n_unknown:
        risk = datum_separation_m(lat0, lon0, 'NAD27', 'WGS84')
        out['if_wrong'] = risk
        if n_unknown == len(positions):
            out['status'] = 'ALL_UNKNOWN'
            out['assumed'] = datum_name(assume) if assume else None
            out['detail'] = (f'none of the {len(positions)} position(s) says which datum it is on. They are consistent with each other whatever it '
                             f'is, but the ground they name moves by up to {risk["separation_m"]:.0f} m here if it is NAD27 rather than WGS84'
                             + (f'; this run assumes {datum_name(assume)}' if assume else ', and nothing has been assumed'))
        else:
            out['status'] = 'MIXED_UNKNOWN'
            out['detail'] = (f'{n_unknown} of {len(positions)} position(s) say no datum while {len(known)} say ' + ', '.join(known) +
                             f'. If the unstated ones are not on the same datum they are out by up to {risk["separation_m"]:.0f} m here, which is '
                             'the size of a position ellipse')
    elif len(known) == 1:
        out['status'] = 'OK'
        out['detail'] = f'every position is on {known[0]}'
    else:
        out['status'] = 'MIXED'
        out['detail'] = ('positions on ' + ', '.join(known) + f' are being used together; they are up to {worst:.0f} m apart here until they are '
                         'converted to one datum')
    out['worst_separation_m'] = round(worst, 2)
    return out


def report_text(res: dict) -> str:
    p = res.get('protocol', '')
    lines = []
    if p.endswith('check_set'):
        lines.append(f"datum check [{res['status']}]: {res['n']} position(s) on " + ', '.join(res['datums'])
                     + (f", {res['n_unknown']} unstated" if res['n_unknown'] else ''))
        for s in res.get('separations', []):
            lines.append(f"  {' vs '.join(s['datums'])}: {s['separation_m']:.1f} m apart here, bearing {s['bearing_deg']:.0f} deg")
        if res.get('if_wrong'):
            lines.append(f"  if the unstated ones are NAD27 rather than WGS84: {res['if_wrong']['separation_m']:.1f} m")
        lines.append('  ' + res.get('detail', ''))
    for nm in res.get('not_a_measurement', []):
        lines.append('  not a measurement: ' + nm)
    return '\n'.join(lines)


def selftest() -> dict:
    """SELF_TEST against values that do not come from this program: the WGS84 meridian arc to 45 deg N, a
    degree of latitude and a degree of longitude at the equator, the defining foot ratios, and the grid
    identities every projection must satisfy at its own origin. Then round trips."""
    a, f, e2 = _ell('WGS84')
    arc45 = _m_arc(math.radians(45.0), a, e2)                       # published: 4 984 944.378 m
    deg_lat = geodesic_m(0.0, 0.0, 1.0, 0.0)['distance_m']          # published: 110 574.389 m
    deg_lon = geodesic_m(0.0, 0.0, 0.0, 1.0)['distance_m']          # published: 111 319.491 m
    # at a transverse Mercator central meridian the easting is the false easting exactly and the scale is k0
    z13 = utm(13)
    cm = project(40.0, z13['lon0'], z13)
    # at a Lambert zone's own origin the easting is the false easting and the northing the false northing
    tc = ZONES['TX_CENTRAL']
    org = project(tc['lat0'], tc['lon0'], tc)
    # and at its standard parallels the scale factor is exactly one
    k_sp = project(tc['lat1'], tc['lon0'] + 1.0, tc)['scale_factor']
    # round trips
    rt = []
    for name, z in (('UTM13N', z13), ('TX_CENTRAL', tc), ('TX_NORTH', ZONES['TX_NORTH'])):
        for la, lo in ((31.0, -102.0), (32.5, -101.0), (29.9, -100.3), (35.0, -101.6)):
            pr = project(la, lo, z)
            la2, lo2 = unproject(pr['easting_m'], pr['northing_m'], z)
            g = geodesic_m(la, lo, la2, lo2)
            rt.append((name, la, lo, g['distance_m']))
    rt_worst = max(x[3] for x in rt)
    rt_where = max(rt, key=lambda x: x[3])
    # a datum round trip, and the separation this program must not quote from memory
    back = transform(*[to_wgs84(31.0, -102.0, 0.0, 'NAD27')[k] for k in ('lat', 'lon', 'h_m')], 'WGS84', 'NAD27')
    rt_datum = geodesic_m(31.0, -102.0, back['lat'], back['lon'])['distance_m']
    sep = datum_separation_m(31.0, -102.0, 'NAD27', 'WGS84')
    # a grid coordinate in US survey feet must not be read as international feet
    ft_gap = abs(project(31.0, -102.0, ZONES['TX_CENTRAL'])['northing_m'] / US_SURVEY_FOOT
                 - project(31.0, -102.0, ZONES['TX_CENTRAL'])['northing_m'] / INTERNATIONAL_FOOT) * US_SURVEY_FOOT
    chk = check_set([Position(31.0, -102.0, 'WGS84'), Position(31.1, -102.1, 'NAD27'), Position(31.2, -102.2)])
    ok = bool(abs(arc45 - 4984944.378) < 1.0
              and abs(deg_lat - 110574.389) < 0.5 and abs(deg_lon - 111319.491) < 0.5
              and abs(cm['easting_m'] - z13['fe']) < 1e-6 and abs(cm['scale_factor'] - z13['k0']) < 1e-12
              and abs(org['easting_m'] - tc['fe']) < 1e-6 and abs(org['northing_m'] - tc['fn']) < 1e-6
              and abs(k_sp - 1.0) < 1e-9
              and rt_worst < 0.02 and rt_datum < 0.001
              and sep['separation_m'] is not None and 5.0 < sep['separation_m'] < 200.0
              and ft_gap > 5.0
              and chk['status'] == 'MIXED_UNKNOWN' and chk['n_unknown'] == 1
              and datum_name('EPSG:4267') == 'NAD27' and datum_name('nad 83') == 'NAD83' and datum_name('whatever') == 'UNKNOWN'
              and length_unit('usft')[0] != length_unit('ft')[0])
    return {'status': 'SELF_TEST', 'ok': ok,
            'against_published': {'meridian_arc_to_45N_m': round(arc45, 3), 'published_m': 4984944.378,
                                  'degree_of_latitude_at_equator_m': round(deg_lat, 3), 'published_lat_m': 110574.389,
                                  'degree_of_longitude_at_equator_m': round(deg_lon, 3), 'published_lon_m': 111319.491},
            'grid_identities': {'tm_central_meridian_easting_m': round(cm['easting_m'], 6), 'tm_scale_at_cm': cm['scale_factor'],
                                'lcc_origin_easting_m': round(org['easting_m'], 6), 'lcc_origin_northing_m': round(org['northing_m'], 6),
                                'lcc_scale_at_standard_parallel': round(k_sp, 12)},
            'round_trip_worst_m': round(rt_worst, 6), 'round_trip_worst_at': {'zone': rt_where[0], 'lat': rt_where[1], 'lon': rt_where[2]},
            'round_trip_tolerance_m': 0.02, 'datum_round_trip_m': round(rt_datum, 6),
            'nad27_to_wgs84_at_31N_102W': sep,
            'us_survey_vs_international_foot_m_at_this_northing': round(ft_gap, 2),
            'check_set': chk}
