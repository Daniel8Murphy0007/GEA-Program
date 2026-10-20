"""The Earth's magnetic field at a well: the World Magnetic Model 2025.

A magnetic survey's azimuths are measured from magnetic north, and its error model weighs each magnetometer error by
the field's strength and dip; the declination turns a magnetic azimuth to true north. A survey file usually states its
declination; the strength and the dip it often does not. This module computes all three from the World Magnetic
Model (WMM2025, the model of the U.S. National Centers for Environmental Information and the British Geological
Survey, valid from 2025.0 to 2030.0; its coefficients are in `reference/WMM2025.COF` as published): the field's
spherical-harmonic expansion to degree and order 12 with its yearly change, evaluated at the latitude, longitude and
height above the WGS84 ellipsoid and the decimal year.

It answers with the declination (degrees east of true north), the dip (degrees below horizontal), the total field
and its components (nT), and whether the date falls inside the model's five years - outside them it still answers,
and says the value is extrapolated. It does not model the crustal field or a disturbance (a magnetic storm, the rig's
steel), which is what the survey's error model's declination and field terms allow for.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime
from typing import Dict, Optional, Tuple

REF = os.path.join(os.path.dirname(__file__), 'reference', 'WMM2025.COF')
A_REF = 6371200.0                          # the geomagnetic reference radius, m
WGS84_A, WGS84_F = 6378137.0, 1.0 / 298.257223563
NMAX = 12
_COEF: Optional[Tuple[float, str, Dict[Tuple[int, int], Tuple[float, float, float, float]]]] = None


def coefficients():
    global _COEF
    if _COEF is None:
        with open(REF, encoding='ascii') as f:
            head = f.readline().split()
            epoch, name = float(head[0]), head[1]
            c = {}
            for line in f:
                p = line.split()
                if not p or p[0].startswith('9999'):
                    break
                n, m = int(p[0]), int(p[1])
                c[(n, m)] = tuple(float(x) for x in p[2:6])
        _COEF = (epoch, name, c)
    return _COEF


def decimal_year(d) -> float:
    if isinstance(d, (int, float)):
        return float(d)
    if isinstance(d, str):
        d = datetime.fromisoformat(d.strip()[:19]) if len(d.strip()) > 10 else datetime.fromisoformat(d.strip()[:10])
    if isinstance(d, datetime):
        start, end = datetime(d.year, 1, 1), datetime(d.year + 1, 1, 1)
        return d.year + (d - start).total_seconds() / (end - start).total_seconds()
    if isinstance(d, date):
        return decimal_year(datetime(d.year, d.month, d.day))
    raise ValueError(f'not a date: {d!r}')


def field(lat_deg: float, lon_deg: float, height_m: float = 0.0, when=None) -> dict:
    """The main field at a place and time (geodetic latitude and longitude on WGS84, height above the ellipsoid)."""
    epoch, name, c = coefficients()
    t = decimal_year(when) if when is not None else decimal_year(datetime.utcnow())
    dt = t - epoch
    phi, lam = math.radians(lat_deg), math.radians(lon_deg)
    e2 = WGS84_F * (2.0 - WGS84_F)
    sphi = math.sin(phi)
    rc = WGS84_A / math.sqrt(1.0 - e2 * sphi * sphi)
    p = (rc + height_m) * math.cos(phi)
    z = (rc * (1.0 - e2) + height_m) * sphi
    r = math.hypot(p, z)
    phic = math.asin(z / r)                                  # geocentric latitude
    theta = math.pi / 2.0 - phic
    st, ct = math.sin(theta), math.cos(theta)
    st = st if abs(st) > 1e-12 else 1e-12
    # Gauss-normalised associated Legendre functions and their derivatives in colatitude
    P = [[0.0] * (NMAX + 1) for _ in range(NMAX + 1)]
    dP = [[0.0] * (NMAX + 1) for _ in range(NMAX + 1)]
    P[0][0] = 1.0
    for n in range(1, NMAX + 1):
        for m in range(0, n + 1):
            if n == m:
                P[n][m] = st * P[n - 1][m - 1]
                dP[n][m] = st * dP[n - 1][m - 1] + ct * P[n - 1][m - 1]
            elif n == 1 or m == n - 1:
                P[n][m] = ct * P[n - 1][m]
                dP[n][m] = ct * dP[n - 1][m] - st * P[n - 1][m]
            else:
                k = ((n - 1) ** 2 - m * m) / ((2 * n - 1) * (2 * n - 3))
                P[n][m] = ct * P[n - 1][m] - k * P[n - 2][m]
                dP[n][m] = ct * dP[n - 1][m] - st * P[n - 1][m] - k * dP[n - 2][m]
    # Schmidt semi-normalisation
    S = [[0.0] * (NMAX + 1) for _ in range(NMAX + 1)]
    S[0][0] = 1.0
    for n in range(1, NMAX + 1):
        S[n][0] = S[n - 1][0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            S[n][m] = S[n][m - 1] * math.sqrt((n - m + 1) * (2 if m == 1 else 1) / (n + m))
    br = bt = bp = 0.0
    ar = A_REF / r
    for n in range(1, NMAX + 1):
        f = ar ** (n + 2)
        for m in range(0, n + 1):
            g, h, gd, hd = c[(n, m)]
            g, h = g + dt * gd, h + dt * hd
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            pnm, dpnm = S[n][m] * P[n][m], S[n][m] * dP[n][m]
            br += f * (n + 1) * (g * cm + h * sm) * pnm
            bt -= f * (g * cm + h * sm) * dpnm
            bp += f * m * (g * sm - h * cm) * pnm
    bp /= st
    xs, ys, zs = -bt, bp, -br                                 # north, east, down in the geocentric frame
    d = phic - phi
    X = xs * math.cos(d) - zs * math.sin(d)
    Z = xs * math.sin(d) + zs * math.cos(d)
    Y = ys
    H = math.hypot(X, Y)
    F = math.hypot(H, Z)
    return {'declination_deg': math.degrees(math.atan2(Y, X)), 'dip_deg': math.degrees(math.atan2(Z, H)), 'total_nT': F,
            'north_nT': X, 'east_nT': Y, 'down_nT': Z, 'horizontal_nT': H, 'model': name, 'decimal_year': round(t, 4),
            'in_range': epoch <= t <= epoch + 5.0,
            'note': ('' if epoch <= t <= epoch + 5.0 else f'{t:.2f} is outside {name}\'s years ({epoch:g} to {epoch + 5:g}): extrapolated')}
