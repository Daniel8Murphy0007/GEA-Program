# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""earth_model - Part 1 of the subsurface surveying tool: THE EARTH MODEL.

v1.74.0 (Daniel's 2026-08-29 gap analysis: "we are making a geological
subsurface surveying tool" - this is the container that holds the map).

WHAT THIS IS
    Every catalogue entry so far has been a 1-D column at a scattered site.
    This module registers those sites into ONE geographic frame:

      - sites are grouped by ARCHIVE-DECLARED coordinates (rounded to 0.01
        deg, ~1.1 km - the grouping resolution, disclosed) so that multiple
        entries drilled into the same ground become one Site with the union
        of their measured properties;
      - every value is placed on a COMMON VERTICAL FRAME where the archive
        supplies a reference elevation: z_ref_m = site elevation - depth
        (metres relative to sea level; marine sites carry negative seafloor
        elevations, the Dome C ice sheet +3233 m, Retama's KB +227.4 m);
      - per-value UNCERTAINTY is carried where the archive supplies it (a
        channel whose name extends the property's with 'std'), and is None
        - never invented - everywhere else;
      - entries whose archives declare no coordinates are listed in
        `unregistered` with the reason, never placed by guesswork.

HONESTY RULES
    - Coordinates come from the archives themselves (PANGAEA/IODP headers,
      the Retama drag-report well block). Nothing is geolocated by memory.
    - Sites without a reference elevation get vertical_frame
      'DEPTH_ONLY_NO_DATUM' and their values stay in native depth.
    - The model never interpolates between sites; it registers, measures
      distances (haversine, WGS-84 mean radius), and reports.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .profile_catalog import CATALOG

EARTH_RADIUS_KM = 6371.0088          # IUGG mean Earth radius
GROUP_DECIMALS = 2                   # site-grouping resolution ~1.1 km (disclosed)
FT_TO_M = 0.3048                     # exact definition


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class PropertyRecord:
    """One measured property series at a site, vertically registered."""
    property: str
    unit: str
    entry: str
    depths_m: List[float]                 # native depth (m unless noted)
    values: List[float]
    z_ref_m: Optional[List[float]]        # elevation-referenced (m rel. sea level) or None
    sigma: Optional[List[float]]          # archive-supplied uncertainty or None
    depth_unit: str = "m"


@dataclass
class Site:
    key: Tuple[float, float]
    latitude: float
    longitude: float
    elevation_m: Optional[float]          # reference elevation (seafloor/surface/KB)
    elevation_datum: str                  # e.g. 'archive elevation', 'KB (drag report)'
    vertical_frame: str                   # 'ELEVATION_REFERENCED' | 'DEPTH_ONLY_NO_DATUM'
    entries: List[str] = field(default_factory=list)
    records: List[PropertyRecord] = field(default_factory=list)

    def properties(self) -> List[str]:
        return sorted({r.property for r in self.records})


def _retama_coords() -> Optional[Tuple[float, float, float]]:
    """Parse the Retama coordinates from the drag report's VERBATIM well
    block (archive-sourced, not remembered). Returns (lat, lon, kb_m)."""
    if 'retama_403h_drag_report' not in CATALOG:
        return None
    meta = CATALOG['retama_403h_drag_report'].stream().meta.get('well', '')
    m = re.search(r'Latitude\s+([\-0-9.]+).*?Longitude\s+([\-0-9.]+)'
                  r'.*?KB Elevation \(ft\)\s+([\-0-9.]+)', meta)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3)) * FT_TO_M


def _sigma_for(channels, name):
    """Archive-supplied uncertainty channel for `name`, if one exists."""
    for cn, ch in channels.items():
        if cn != name and cn.startswith(name) and 'std' in cn.lower():
            return [float(v) for v in ch.values]
    return None


class EarthModel:
    """The registered library: sites in one frame, refusals with reasons."""

    def __init__(self):
        self.sites: Dict[Tuple[float, float], Site] = {}
        self.unregistered: List[Tuple[str, str]] = []
        self._build()

    # -- construction ------------------------------------------------------
    def _add_records(self, site: Site, entry_name: str, st, depth_unit="m",
                     depth_scale=1.0, md_indexed=False) -> None:
        """md_indexed=True marks DEVIATED wells whose index is MEASURED depth:
        vertical registration then comes from the entry's own row-aligned TVD
        channel (never from MD - a horizontal well's MD is not a height), and
        entries without a TVD channel stay unregistered vertically (z=None).
        Near-vertical scientific boreholes use index depth directly; that
        approximation (depth-below-surface ~ true vertical depth) is the
        model's stated assumption for hole inclinations < a few degrees."""
        site.entries.append(entry_name)
        if st.index_kind != 'depth':
            return                       # ordinal/time entries register the site only
        depths = [float(d) * depth_scale for d in st.index]
        tvd_m = None
        if md_indexed:
            tvd_ch = next((ch for cn, ch in st.channels.items()
                           if cn.upper().startswith('TVD')), None)
            if tvd_ch is not None:
                tvd_m = [float(v) * depth_scale for v in tvd_ch.values]
        for cn, ch in st.channels.items():
            if 'std' in cn.lower():
                continue                 # uncertainty channels attach to their property
            vals = [float(v) for v in ch.values]
            if site.elevation_m is None:
                z = None
            elif md_indexed:
                z = ([site.elevation_m - t for t in tvd_m]
                     if tvd_m is not None else None)
            else:
                z = [site.elevation_m - d for d in depths]
            site.records.append(PropertyRecord(
                property=cn, unit=ch.unit, entry=entry_name,
                depths_m=depths, values=vals, z_ref_m=z,
                sigma=_sigma_for(st.channels, cn), depth_unit=depth_unit))

    def _site_for(self, lat: float, lon: float, elev, datum: str) -> Site:
        key = (round(lat, GROUP_DECIMALS), round(lon, GROUP_DECIMALS))
        if key not in self.sites:
            self.sites[key] = Site(
                key=key, latitude=lat, longitude=lon,
                elevation_m=(float(elev) if elev is not None else None),
                elevation_datum=datum,
                vertical_frame=('ELEVATION_REFERENCED' if elev is not None
                                else 'DEPTH_ONLY_NO_DATUM'))
        return self.sites[key]

    def _build(self) -> None:
        retama = _retama_coords()
        for name, e in sorted(CATALOG.items()):
            if name.startswith('retama_403h'):
                if retama is None:
                    self.unregistered.append((name, 'retama drag-report well block absent'))
                    continue
                lat, lon, kb = retama
                site = self._site_for(lat, lon, kb, 'KB (drag-report well block, 746 ft)')
                # operator survey depths are in FEET
                self._add_records(site, name, e.stream(), depth_unit='ft->m',
                                  depth_scale=FT_TO_M, md_indexed=True)
                continue
            try:
                st = e.stream()
            except Exception as ex:
                self.unregistered.append((name, 'non-stream entry: %s' % type(ex).__name__))
                continue
            lat, lon = st.meta.get('latitude'), st.meta.get('longitude')
            if not (lat and lon):
                self.unregistered.append((name, 'archive declares no coordinates'))
                continue
            site = self._site_for(float(lat), float(lon), st.meta.get('elevation_m'),
                                  'archive elevation')
            self._add_records(site, name, st)

    # -- geography ---------------------------------------------------------
    def distance_km(self, a: Tuple[float, float], b: Tuple[float, float]) -> float:
        sa, sb = self.sites[a], self.sites[b]
        return haversine_km(sa.latitude, sa.longitude, sb.latitude, sb.longitude)

    def nearest(self, lat: float, lon: float, k: int = 3):
        ranked = sorted(self.sites.values(),
                        key=lambda s: haversine_km(lat, lon, s.latitude, s.longitude))
        return [(s.key, round(haversine_km(lat, lon, s.latitude, s.longitude), 1))
                for s in ranked[:k]]

    def span_km(self) -> Tuple[float, Tuple, Tuple]:
        keys = list(self.sites)
        best = (0.0, None, None)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = self.distance_km(a, b)
                if d > best[0]:
                    best = (d, a, b)
        return best

    # -- reporting ---------------------------------------------------------
    def census(self) -> dict:
        n_rec = sum(len(s.records) for s in self.sites.values())
        n_unc = sum(1 for s in self.sites.values() for r in s.records
                    if r.sigma is not None)
        span, a, b = self.span_km()
        lats = [s.latitude for s in self.sites.values()]
        return {
            'sites': len(self.sites),
            'registered_entries': sum(len(s.entries) for s in self.sites.values()),
            'unregistered_entries': len(self.unregistered),
            'property_records': n_rec,
            'records_with_archive_uncertainty': n_unc,
            'multi_entry_sites': sum(1 for s in self.sites.values() if len(s.entries) > 1),
            'elevation_referenced_sites': sum(1 for s in self.sites.values()
                                              if s.vertical_frame == 'ELEVATION_REFERENCED'),
            'latitude_span_deg': round(max(lats) - min(lats), 2),
            'great_circle_span_km': round(span, 1),
            'span_endpoints': (a, b),
            'grouping_resolution_deg': 10 ** -GROUP_DECIMALS,
        }
