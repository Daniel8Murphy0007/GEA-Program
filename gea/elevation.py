# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""elevation - the ground elevation of a position from the USGS 3D Elevation Program (3DEP).

The Elevation Point Query Service (EPQS) answers one position with the ground elevation from the best 3DEP
raster it holds, the raster it came from and that raster's resolution: a public, documented, no-key service at
https://epqs.nationalmap.gov/v1/json?x=<lon>&y=<lat>&wkid=4326&units=Feet . The elevation is orthometric
(NAVD88 in the conterminous United States) - sea level for the purposes of a subsea depth - and it is the
GROUND, not the kelly bushing: a depth measured from the KB is deeper by the KB height, which the completion
report states and this service does not. The program stores what the service answered, with its raster and
the time of the query, as the reference elevation's source; nothing is fetched unless asked.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

EPQS_URL = 'https://epqs.nationalmap.gov/v1/json'
NO_DATA = -1000000.0


def epqs(lat: float, lon: float, url: str = EPQS_URL, timeout: float = 20.0, units: str = 'Feet') -> dict:
    """One query to the EPQS: the ground elevation at (lat, lon), the raster, its resolution, the query time."""
    q = urllib.parse.urlencode({'x': f'{float(lon):.6f}', 'y': f'{float(lat):.6f}', 'wkid': '4326', 'units': units, 'includeDate': 'false'})
    full = url + ('&' if '?' in url else '?') + q
    req = urllib.request.Request(full, headers={'Accept': 'application/json', 'User-Agent': 'gea-program'})
    t0 = datetime.now(timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:                                                   # noqa: BLE001 - every failure is reported the same way
        return {'status': 'UNREACHABLE', 'detail': f'{type(e).__name__}: {e}', 'url': full, 'queried_utc': t0.isoformat()}
    try:
        d = json.loads(raw.decode('utf-8'))
    except Exception:                                                        # noqa: BLE001
        return {'status': 'NOT JSON', 'detail': raw[:120].decode('utf-8', 'replace'), 'url': full, 'queried_utc': t0.isoformat()}
    v = d.get('value')
    try:
        val = float(v)
    except (TypeError, ValueError):
        return {'status': 'NO VALUE', 'detail': f'the service answered without an elevation: {str(d)[:200]}', 'url': full, 'queried_utc': t0.isoformat()}
    if val <= NO_DATA + 1 or val < -1500 or val > 30000:
        return {'status': 'NO DATA', 'detail': f'the service holds no elevation at this position (value {val:g})', 'url': full, 'queried_utc': t0.isoformat()}
    res = d.get('resolution')
    out = {'status': 'OK', 'elev_ft': round(val, 2) if units == 'Feet' else round(val * 3.280839895, 2), 'units_answered': units,
           'raster': d.get('rasterSource') or d.get('raster') or '', 'resolution_m': (float(res) if isinstance(res, (int, float, str)) and str(res).replace('.', '', 1).isdigit() else None),
           'vertical_datum': 'NAVD88 (orthometric; the ground, not the kelly bushing)', 'url': full, 'queried_utc': t0.isoformat()}
    out['source'] = f"USGS 3DEP EPQS, {out['queried_utc'][:10]}" + (f", {out['resolution_m']:g} m raster" if out['resolution_m'] else '') + (f" ({out['raster']})" if out['raster'] else '')
    return out


def as_reference(res: dict) -> Optional[dict]:
    """The EPQS answer as a well's reference elevation record (datum GL, the source stated), or None."""
    if res.get('status') != 'OK':
        return None
    return {'ft': res['elev_ft'], 'datum': 'GL', 'source': res['source'], 'vertical_datum': 'NAVD88', 'raster': res.get('raster', ''), 'queried_utc': res['queried_utc']}
