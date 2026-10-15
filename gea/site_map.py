# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""site_map - everything in a workspace that has a position, on one map.

The map's frame is geographic WGS84. Every source arrives on its own datum and is brought into the frame once,
with the shift stated: a structure layer on NAD27 is moved by the datum shift at its own centre (it varies by
less than a metre across a basin); NAD83 and WGS84 are taken as the same ground to within about two metres, as
the rest of the program takes them. A well is placed only where its surface position was declared, on the datum
it was declared on; a well without one is listed as NOT PLACED and is never put anywhere.

What the map draws:
  * the outline of the US states and counties (`reference/us_outline.json`, from the US Census Bureau's 2017
    cartographic boundary files by way of the us-atlas package - shipped, so the map needs no network);
  * every structure layer the site brought - faults and folds, formation contours, areas, points - each drawn at
    the detail the view needs: a vertex that would land in the same screen cell as the one before it is dropped
    (`tol`, in degrees, is the page's pixel), never moved;
  * the World Stress Map records in view, as SHmax ticks, best quality first;
  * the seismic stations, the Seismicity Response Areas, and the wells.

"What is here" answers a click: each layer's own statement for that position (the nearest fault and its
distance, the contour value, the area it falls in, the nearest points), and the distance to every placed well.

Headless-safe: numpy and the standard library.
"""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

OUTLINE_FILE = os.path.join(os.path.dirname(__file__), 'reference', 'us_outline.json')
FRAME = 'WGS84 (NAD83 taken as the same ground to within about 2 m)'
_OUTLINE: Optional[dict] = None
_CACHE: Dict[str, Tuple[float, dict]] = {}
_GUARD = threading.Lock()


def outline_data() -> dict:
    global _OUTLINE
    if _OUTLINE is None:
        with open(OUTLINE_FILE, encoding='utf-8') as f:
            d = json.load(f)
        for a in d['arcs']:
            a['p'] = np.asarray(a['p'], dtype=np.float64)
        _OUTLINE = d
    return _OUTLINE


def _shift_for(datum: Optional[str], bbox) -> Tuple[float, float, float]:
    """(dlat, dlon, metres) that bring a layer on `datum` onto WGS84, from the shift at the layer's centre."""
    from . import geodesy as GD
    d = GD.datum_name(datum)
    if d in ('WGS84', 'NAD83', 'UNKNOWN'):
        return 0.0, 0.0, 0.0
    lat0, lon0 = (bbox[1] + bbox[3]) / 2.0, (bbox[0] + bbox[2]) / 2.0
    r = GD.to_wgs84(lat0, lon0, 0.0, d)
    return r['lat'] - lat0, r['lon'] - lon0, float(r.get('shift_m') or 0.0)


def _layer(ws, layer_id: str) -> dict:
    """The layer read once per process (and again when its record changes): its features in the frame, as arrays."""
    from . import structure as ST
    p = os.path.join(ws.path, 'layers', layer_id, 'layer.json')
    mt = os.path.getmtime(p)
    key = os.path.abspath(p)
    with _GUARD:
        hit = _CACHE.get(key)
        if hit and hit[0] == mt:
            return hit[1]
    rec = ws.layer(layer_id)
    L = ws.load_layer(layer_id)
    dlat, dlon, shift_m = _shift_for((rec.get('crs') or {}).get('datum'), L.bbox)
    kind = rec.get('kind') or ST.layer_kind(L)
    feats = []
    for k, f in enumerate(L.features):
        parts = [np.asarray(pp, dtype=np.float64) + np.array([dlon, dlat]) for pp in f.parts if len(pp)]
        if not parts:
            continue
        allp = np.vstack(parts)
        a = f.attrs
        item = {'i': k, 'parts': parts, 'bbox': (float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max()))}
        if kind == 'structure':
            item['cls'] = L.declared_kind or ST.classify(a, rec.get('type_attr'), rec.get('display') or layer_id)
        if rec.get('name_attr') and a.get(rec['name_attr']) not in (None, ''):
            item['name'] = str(a.get(rec['name_attr']))[:60]
        if rec.get('value_attr') and a.get(rec['value_attr']) not in (None, ''):
            try:
                item['value'] = float(a.get(rec['value_attr']))
            except (TypeError, ValueError):
                pass
        feats.append(item)
    bb = (L.bbox[0] + dlon, L.bbox[1] + dlat, L.bbox[2] + dlon, L.bbox[3] + dlat)
    data = {'id': layer_id, 'rec': rec, 'kind': kind, 'feats': feats, 'bbox': bb, 'shift_m': shift_m, 'L': L, 'dlat': dlat, 'dlon': dlon,
            'n_points': int(sum(len(pp) for f in feats for pp in f['parts']))}
    with _GUARD:
        _CACHE[key] = (mt, data)
    return data


def _thin(pts: np.ndarray, tol: float, coslat: float) -> np.ndarray:
    """Drop a vertex that lands in the same screen cell as the vertex before it; keep the ends. Nothing is moved."""
    if tol <= 0 or len(pts) <= 2:
        return pts
    q = np.floor(pts / np.array([tol / max(coslat, 0.05), tol])).astype(np.int64)
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(q[1:] != q[:-1], axis=1)
    keep[-1] = True
    return pts[keep]


def _hits(bb, view) -> bool:
    return not (bb[2] < view[0] or bb[0] > view[2] or bb[3] < view[1] or bb[1] > view[3])


def _r(a: np.ndarray) -> list:
    return np.round(a, 5).tolist()             # 1e-5 degree: about a metre - below any screen pixel the map draws


def wells(ws) -> List[dict]:
    from . import geodesy as GD
    out = []
    for w in ws.wells():
        s = w.get('surface') or {}
        rec = {'id': w['id'], 'display': w.get('display') or w['id'], 'placed': False}
        if s.get('lat') is not None and s.get('lon') is not None:
            g = GD.to_wgs84(float(s['lat']), float(s['lon']), 0.0, s.get('datum') or 'UNKNOWN')
            rec.update({'placed': True, 'lat': g['lat'], 'lon': g['lon'], 'declared': {'lat': s['lat'], 'lon': s['lon'], 'datum': s.get('datum') or 'UNKNOWN'},
                        'shift_m': g.get('shift_m') or 0.0, 'datum_status': g.get('status')})
        out.append(rec)
    return out


def default_view(ws, wl: List[dict], layers: List[dict]) -> dict:
    """Where the map opens: the placed wells with a margin; else the Seismicity Response Areas; else the area most of the
    site's layers cover (the layers whose centres lie within five degrees of their median centre)."""
    placed = [w for w in wl if w['placed']]
    if placed:
        lats = [w['lat'] for w in placed]; lons = [w['lon'] for w in placed]
        m = max(0.12, (max(lats) - min(lats)) * 0.4, (max(lons) - min(lons)) * 0.4)
        return {'bbox': [min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m], 'why': 'the placed wells'}
    for s in ws.sites():
        sra = s.get('sra')
        if sra and sra.get('centre_wgs84'):
            c = sra['centre_wgs84']; rd = float(sra.get('radius_km') or 10.0) / 111.0 * 1.5
            return {'bbox': [c['lon'] - rd / math.cos(math.radians(c['lat'])), c['lat'] - rd, c['lon'] + rd / math.cos(math.radians(c['lat'])), c['lat'] + rd],
                    'why': f"the Seismicity Response Area of {s['display']}"}
    if layers:
        cx = np.median([(l['bbox'][0] + l['bbox'][2]) / 2 for l in layers]); cy = np.median([(l['bbox'][1] + l['bbox'][3]) / 2 for l in layers])
        near = [l for l in layers if abs((l['bbox'][0] + l['bbox'][2]) / 2 - cx) <= 5 and abs((l['bbox'][1] + l['bbox'][3]) / 2 - cy) <= 5] or layers
        # the median of each edge: one regional layer (a fault map of half the continent) does not widen the view
        return {'bbox': [float(np.median([l['bbox'][i] for l in near])) for i in range(4)],
                'why': f'the area most of the layers cover (the median extent of {len(near)} of the {len(layers)})'}
    return {'bbox': [-106.7, 25.8, -93.5, 36.5], 'why': 'nothing in the workspace has a position yet: Texas'}


def overview(ws) -> dict:
    """The map's contents: the layers (without their geometry), the wells placed and not, the stations, the areas, the frame."""
    layers = []
    for rec in ws.layers():
        try:
            d = _layer(ws, rec['id'])
        except (ValueError, OSError) as e:
            layers.append({'id': rec['id'], 'display': rec.get('display'), 'kind': rec.get('kind'), 'status': 'UNREADABLE', 'note': str(e), 'bbox': rec.get('bbox')})
            continue
        layers.append({'id': rec['id'], 'display': rec.get('display') or rec['id'], 'kind': d['kind'], 'formation': rec.get('formation') or '', 'role': rec.get('role') or '',
                       'n_features': len(d['feats']), 'n_points': d['n_points'], 'bbox': [round(x, 5) for x in d['bbox']],
                       'datum': (rec.get('crs') or {}).get('datum') or 'UNKNOWN', 'shift_m': round(d['shift_m'], 1), 'citation': rec.get('citation') or '',
                       'value_attr': rec.get('value_attr') or '', 'status': 'OK',
                       'classes': sorted({f.get('cls') for f in d['feats'] if f.get('cls')})})
    wl = wells(ws)
    stations = []
    for s in ws.seismic_stations():
        if s.get('lat') is not None and s.get('lon') is not None:
            stations.append({'id': s['id'], 'display': s.get('display') or s['id'], 'lat': float(s['lat']), 'lon': float(s['lon']), 'kind': s.get('kind', '')})
    areas = []
    for s in ws.sites():
        sra = s.get('sra')
        if sra and sra.get('centre_wgs84'):
            areas.append({'site': s['id'], 'name': sra.get('name') or s['display'], 'lat': sra['centre_wgs84']['lat'], 'lon': sra['centre_wgs84']['lon'],
                          'radius_km': float(sra.get('radius_km') or 0.0)})
    sm = ws.stress_map()
    return {'frame': FRAME, 'layers': layers, 'wells': wl, 'stations': stations, 'areas': areas,
            'stress_map': ({'n_records': sm.get('n_records'), 'citation': sm.get('citation', '')} if sm else None),
            'view': default_view(ws, wl, [l for l in layers if l.get('status') == 'OK']),
            'outline_source': 'US Census Bureau cartographic boundary files, 2017, by way of the us-atlas package (public domain data)'}


def layer_view(ws, layer_id: str, view: List[float], tol: float, max_points: int = 4000) -> dict:
    """One layer's features in the view, thinned to the view's pixel; points sampled evenly past `max_points`."""
    d = _layer(ws, layer_id)
    coslat = math.cos(math.radians((view[1] + view[3]) / 2.0))
    feats, n_in = [], 0
    if d['kind'] == 'points':
        pts = [(f['parts'][0][0], f) for f in d['feats'] if _hits(f['bbox'], view)]
        n_in = len(pts)
        step = max(1, math.ceil(n_in / max_points))
        for p, f in pts[::step]:
            feats.append({'i': f['i'], 'p': [round(float(p[0]), 5), round(float(p[1]), 5)], **({'name': f['name']} if 'name' in f else {})})
        return {'id': layer_id, 'kind': 'points', 'features': feats, 'in_view': n_in, 'shown': len(feats)}
    nv = 0
    for f in d['feats']:
        if not _hits(f['bbox'], view):
            continue
        n_in += 1
        parts = []
        for pp in f['parts']:
            t = _thin(pp, tol, coslat)
            if len(t) >= 2 or (d['kind'] == 'areas' and len(t) >= 3):
                parts.append(_r(t)); nv += len(t)
        if parts:
            item = {'i': f['i'], 'parts': parts}
            for k in ('cls', 'name', 'value'):
                if k in f:
                    item[k] = f[k]
            feats.append(item)
    return {'id': layer_id, 'kind': d['kind'], 'features': feats, 'in_view': n_in, 'shown': len(feats), 'vertices': nv}


def outline_view(view: List[float], tol: float) -> dict:
    o = outline_data()
    coslat = math.cos(math.radians((view[1] + view[3]) / 2.0))
    span = max(view[2] - view[0], view[3] - view[1])
    arcs = []
    for a in o['arcs']:
        if not _hits(a['b'], view):
            continue
        if not a['s'] and span > 12:                 # county lines only once the view is closer than a dozen degrees
            continue
        t = _thin(a['p'], tol, coslat)
        if len(t) >= 2:
            arcs.append({'s': a['s'], 'p': _r(t)})
    labels = []
    if span <= 6:
        for c in o['counties']:
            lb = c['label']
            if lb and view[0] <= lb[0] <= view[2] and view[1] <= lb[1] <= view[3]:
                labels.append({'name': c['name'], 'state': o['states'].get(c['state'], c['state']), 'p': lb})
    return {'arcs': arcs, 'labels': labels, 'source': 'US Census Bureau cartographic boundary files, 2017 (us-atlas 3.0.1)'}


def stress_view(ws, view: List[float], limit: int = 2500) -> dict:
    from . import slip as SL
    sm = ws.stress_map()
    if not sm:
        return {'records': [], 'n_in_view': 0}
    key = ('wsm', ws.path)
    with _GUARD:
        hit = _CACHE.get(str(key))
    p = os.path.join(ws.path, 'layers', '_stress_map', sm['file'])
    mt = os.path.getmtime(p)
    if not hit or hit[0] != mt:
        recs = SL.read_wsm(p)
        with _GUARD:
            _CACHE[str(key)] = (mt, {'recs': recs})
    else:
        recs = hit[1]['recs']
    inv = [r for r in recs if view[0] <= r['lon'] <= view[2] and view[1] <= r['lat'] <= view[3]]
    order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    inv.sort(key=lambda r: order.get(r['quality'], 5))
    return {'records': [{'lat': r['lat'], 'lon': r['lon'], 'azimuth': r['azimuth'], 'quality': r['quality'], 'regime': r['regime'], 'type': r['type']}
                        for r in inv[:limit]], 'n_in_view': len(inv), 'citation': sm.get('citation', '')}


def what_here(ws, lat: float, lon: float, radius_km: float = 25.0) -> dict:
    """Each layer's own statement for a position in the frame, and the distance to every placed well."""
    from . import structure as ST
    from . import geodesy as GD
    out = []
    for rec in ws.layers():
        try:
            d = _layer(ws, rec['id'])
            r = ST.describe(d['L'], lat - d['dlat'], lon - d['dlon'], radius_km, rec.get('type_attr'), rec.get('name_attr'))
        except (ValueError, OSError) as e:
            r = {'status': 'UNREADABLE', 'note': str(e)}
        r['layer_id'] = rec['id']; r['layer_display'] = rec.get('display') or rec['id']; r['layer_kind'] = rec.get('kind') or r.get('kind', 'structure')
        try:
            r['lines'] = ST.lines(r)
        except Exception:
            r['lines'] = []
        out.append({k: r.get(k) for k in ('layer_id', 'layer_display', 'layer_kind', 'status', 'lines')})
    near = []
    for w in wells(ws):
        if w['placed']:
            g = GD.geodesic_m(lat, lon, w['lat'], w['lon'])
            near.append({'id': w['id'], 'display': w['display'], 'distance_km': round((g['distance_m'] or 0.0) / 1000.0, 3),
                         'bearing_deg': round(g['azimuth_deg'], 1) if g.get('azimuth_deg') is not None else None})
    near.sort(key=lambda x: x['distance_km'])
    answered = [x for x in out if x['status'] == 'OK']
    return {'lat': lat, 'lon': lon, 'frame': FRAME, 'radius_km': radius_km, 'layers': out, 'answered': len(answered), 'wells': near}
