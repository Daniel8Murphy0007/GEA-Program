# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""geometry - the directional driller's arithmetic in three dimensions, shared by the twin, the advisor, the screen
and shadow mode so every one of them places the bit the same way: minimum curvature between two stations (the
inclination and the azimuth at each end, the length between), the dogleg of the step, and a lateral's own frame -
distance along the plan's azimuth, offset to the right of the plan line, true vertical depth - in which the layer
is a surface and the corridor is two lines."""
from __future__ import annotations

import math
from typing import Tuple


def min_curvature(inc0: float, az0: float, inc1: float, az1: float, length: float) -> Tuple[float, float, float, float]:
    """One step between two stations: (dN, dE, dV, dogleg_deg). Degrees in, feet out, azimuths measured clockwise
    from north - or from the plan's azimuth, when the caller passes azimuths relative to it; the result's N is then
    along the plan and E to its right."""
    i0, i1 = math.radians(inc0), math.radians(inc1); a0, a1 = math.radians(az0), math.radians(az1)
    cb = math.cos(i1 - i0) - math.sin(i0) * math.sin(i1) * (1.0 - math.cos(a1 - a0))
    beta = math.acos(max(-1.0, min(1.0, cb)))
    rf = 1.0 if beta < 1e-9 else (2.0 / beta) * math.tan(beta / 2.0)
    half = length / 2.0 * rf
    dn = half * (math.sin(i0) * math.cos(a0) + math.sin(i1) * math.cos(a1))
    de = half * (math.sin(i0) * math.sin(a0) + math.sin(i1) * math.sin(a1))
    dv = half * (math.cos(i0) + math.cos(i1))
    return dn, de, dv, math.degrees(beta)


def dls_of(build_deg_100: float, turn_deg_100: float, inc_deg: float) -> float:
    """The dogleg severity, degrees per 100 ft, of a build and a turn asked together at this inclination: the turn
    counts by the sine of the inclination (a turn in a near-vertical hole is nearly nothing)."""
    return math.sqrt(build_deg_100 ** 2 + (turn_deg_100 * math.sin(math.radians(inc_deg))) ** 2)


def toolface_of(build_deg_100: float, turn_deg_100: float, inc_deg: float) -> float:
    """The toolface a build and a turn amount to: 0 is straight up, 90 to the right, 180 down, 270 to the left."""
    if abs(build_deg_100) < 1e-9 and abs(turn_deg_100) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(turn_deg_100 * math.sin(math.radians(inc_deg)), build_deg_100)) % 360.0


def wrap180(deg: float) -> float:
    """An angle into (-180, 180]."""
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def apparent_dip(dip_along_deg: float, cross_dip_deg: float, az_rel_deg: float) -> float:
    """The dip a hole sees along itself when it runs `az_rel_deg` off the plan: the plan-wise dip by the cosine and
    the cross dip by the sine, as the slopes add."""
    a = math.radians(az_rel_deg)
    return math.degrees(math.atan(math.tan(math.radians(dip_along_deg)) * math.cos(a) + math.tan(math.radians(cross_dip_deg)) * math.sin(a)))
