"""structure - the mapped faults and folds around a well, from a layer the site brought.

The first geological statement this program makes about a position: how far, and in which direction, the
nearest mapped fault is; the nearest fold; what else the layer holds within a radius. The layer is a shapefile
the site added - a public structure map (USGS, a state survey, BEG/TexNet) or the operator's own - read by
`shapefile.py` and kept in the workspace with its hash and its own metadata. The distances are from the point
to the nearest segment of each line, in a local plane (accurate to well under a metre at the distances this
is used for), with the reported distance and bearing to the nearest point recomputed on the ellipsoid.

What it will not call a measurement: a fault the layer does not hold (a position outside the layer's extent is
OUTSIDE LAYER EXTENT, never "no faults"); a fault's depth, dip or activity (the layer's attributes are
carried as the layer states them, and nothing more); a hazard (distance to a mapped trace is a fact of the
map, not a judgement); the map's own accuracy (a compiled regional map is not a site survey, and the metadata
beside the layer is where its scale and sources are stated).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .shapefile import Layer, Feature

R_EARTH_M = 6371008.8
FAULT_WORDS = ('fault', 'thrust', 'shear', 'rupture')
FOLD_WORDS = ('anticline', 'syncline', 'fold', 'arch', 'dome', 'monocline', 'basin axis')


def classify(attrs: dict, type_attr: Optional[str], layer_name: str = '') -> str:
    """fault | fold | other, by the type attribute's words - or, when the layer has no type attribute, by the
    layer's own name (EIA's Faults_Permian carries only 'fault' / 'dotted fault' in LONG_DESC, and its name)."""
    t = str(attrs.get(type_attr, '') if type_attr else '').lower()
    if not t:
        t = layer_name.lower().replace('_', ' ')
    if any(w in t for w in FAULT_WORDS):
        return 'fault'
    if any(w in t for w in FOLD_WORDS):
        return 'fold'
    return 'other'


CONTOUR_WORDS = ('elev', 'zlevel', 'z_level', 'contour', 'depth', 'thick', 'isopach', 'top_ft', 'elevation')


def guess_attrs(layer: Layer) -> Tuple[Optional[str], Optional[str]]:
    """Which attribute names the structure type and which its name, by the field names the layer carries."""
    names = [f['name'] for f in layer.fields]
    low = [n.lower() for n in names]
    type_attr = next((names[i] for i, n in enumerate(low) if n in ('type', 'kind', 'ftype', 'feature', 'class', 'struct_typ', 'structtype', 'long_desc', 'desc', 'descriptio')), None)
    if type_attr is None:
        type_attr = next((names[i] for i, n in enumerate(low) if 'type' in n or 'kind' in n or 'desc' in n), None)
    name_attr = next((names[i] for i, n in enumerate(low) if n in ('name', 'fname', 'label', 'struct_nam', 'structname')), None)
    if name_attr is None:
        name_attr = next((names[i] for i, n in enumerate(low) if 'name' in n), None)
    return type_attr, name_attr


def guess_value_attr(layer: Layer) -> Optional[str]:
    """For a contour layer: the numeric field that carries the contour's value (ElevTop_ft, ZLEVEL, Thick_ft)."""
    for f in layer.fields:
        n = f['name'].lower()
        if f['type'] in ('N', 'F') and any(w in n for w in CONTOUR_WORDS) and 'shape' not in n:
            return f['name']
    return None


def layer_kind(layer: Layer) -> str:
    """structure (fault/fold traces) | contours (lines carrying a value) | areas (polygons) | points."""
    st = layer.shape_type
    if st.startswith('Polygon'):
        return 'areas'
    if st.startswith('Point') or st.startswith('MultiPoint'):
        return 'points'
    if guess_value_attr(layer) is not None:
        return 'contours'
    return 'structure'


def _local(lat0: float, lon0: float):
    k = math.cos(math.radians(lat0))

    def to_xy(lon: float, lat: float) -> Tuple[float, float]:
        return (math.radians(lon - lon0) * k * R_EARTH_M, math.radians(lat - lat0) * R_EARTH_M)
    return to_xy


def _seg_nearest(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> Tuple[float, float, float]:
    """Nearest point on segment AB to P: (distance, x, y)."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return math.hypot(px - ax, py - ay), ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    nx, ny = ax + t * dx, ay + t * dy
    return math.hypot(px - nx, py - ny), nx, ny


def inside_extent(layer: Layer, lat: float, lon: float, margin_deg: float = 0.0) -> bool:
    x0, y0, x1, y1 = layer.bbox
    return (x0 - margin_deg) <= lon <= (x1 + margin_deg) and (y0 - margin_deg) <= lat <= (y1 + margin_deg)


def nearest(layer: Layer, lat: float, lon: float, radius_km: float = 50.0, type_attr: Optional[str] = None, name_attr: Optional[str] = None,
            n_list: int = 10) -> dict:
    """The nearest fault, the nearest fold, and the n nearest features of any kind within the radius, with the
    distance (km) and bearing (deg, from the position to the nearest point of the feature) of each."""
    from . import geodesy as GD
    if type_attr is None or name_attr is None:
        ta, na = guess_attrs(layer)
        type_attr = type_attr or ta
        name_attr = name_attr or na
    out = {'lat': lat, 'lon': lon, 'layer': layer.name, 'radius_km': radius_km, 'type_attr': type_attr, 'name_attr': name_attr,
           'status': 'OK', 'nearest_fault': None, 'nearest_fold': None, 'within': [], 'n_considered': 0}
    if not inside_extent(layer, lat, lon, margin_deg=radius_km / 111.0):      # the extent, widened by the radius asked about
        out['status'] = 'OUTSIDE LAYER EXTENT'
        out['note'] = (f'the position ({lat:.4f}, {lon:.4f}) is outside the layer\'s extent (lon {layer.bbox[0]:.3f}..{layer.bbox[2]:.3f}, '
                       f'lat {layer.bbox[1]:.3f}..{layer.bbox[3]:.3f}): the layer says nothing about this ground')
        return out
    to_xy = _local(lat, lon)
    rad_m = radius_km * 1000.0
    deg_lat = radius_km / 111.0
    deg_lon = deg_lat / max(math.cos(math.radians(lat)), 0.01)
    hits: List[dict] = []
    for i, f in enumerate(layer.features):
        if f.bbox is not None:
            x0, y0, x1, y1 = f.bbox
            if x1 < lon - deg_lon or x0 > lon + deg_lon or y1 < lat - deg_lat or y0 > lat + deg_lat:
                continue
        out['n_considered'] += 1
        best = (float('inf'), 0.0, 0.0)
        for part in f.parts:
            pts = [to_xy(x, y) for (x, y) in part]
            if len(pts) == 1:
                d = math.hypot(*pts[0]); cand = (d, pts[0][0], pts[0][1])
                if cand[0] < best[0]:
                    best = cand
                continue
            for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
                d, nx, ny = _seg_nearest(0.0, 0.0, ax, ay, bx, by)
                if d < best[0]:
                    best = (d, nx, ny)
        if best[0] <= rad_m:
            # the nearest point back to lon/lat, then the ellipsoidal distance and bearing to it
            k = math.cos(math.radians(lat))
            nlon = lon + math.degrees(best[1] / (k * R_EARTH_M)); nlat = lat + math.degrees(best[2] / R_EARTH_M)
            g = GD.geodesic_m(lat, lon, nlat, nlon)
            hits.append({'index': i, 'kind': classify(f.attrs, type_attr, layer.name), 'type': str(f.attrs.get(type_attr, '')) if type_attr else '',
                         'name': str(f.attrs.get(name_attr, '')) if name_attr else '', 'distance_km': round(g['distance_m'] / 1000.0, 3),
                         'bearing_deg': round(g['azimuth_deg'] % 360.0, 1), 'nearest_lat': round(nlat, 6), 'nearest_lon': round(nlon, 6),
                         'attrs': {k_: v for k_, v in f.attrs.items()}})
    hits.sort(key=lambda h: h['distance_km'])
    out['nearest_fault'] = next((h for h in hits if h['kind'] == 'fault'), None)
    out['nearest_fold'] = next((h for h in hits if h['kind'] == 'fold'), None)
    out['within'] = hits[:n_list]
    out['n_within'] = len(hits)
    out['counts_within'] = {k: sum(1 for h in hits if h['kind'] == k) for k in ('fault', 'fold', 'other')}
    return out


def _unit_of(attr: str) -> str:
    a = attr.lower()
    return 'ft' if 'ft' in a or 'feet' in a else ('m' if a.endswith('_m') or a.endswith('m') and 'elev' in a else '')


def contour_at(layer: Layer, lat: float, lon: float, value_attr: Optional[str] = None, radius_km: float = 30.0) -> dict:
    """A contour map read at a position: the nearest contour of each value within the radius, the two nearest distinct
    values (the contours the position lies between, or the one it sits on), with their distances. A contour map
    is read, not interpolated: the answer is the bracket, never a number between the lines."""
    value_attr = value_attr or guess_value_attr(layer)
    out = {'lat': lat, 'lon': lon, 'layer': layer.name, 'kind': 'contours', 'value_attr': value_attr, 'unit': _unit_of(value_attr or ''), 'radius_km': radius_km,
           'status': 'OK', 'nearest': [], 'between': None, 'on': None}
    if value_attr is None:
        out.update({'status': 'NO VALUE ATTRIBUTE', 'note': 'the layer has no numeric field naming the contour value'})
        return out
    if not inside_extent(layer, lat, lon, margin_deg=radius_km / 111.0):
        out['status'] = 'OUTSIDE LAYER EXTENT'
        out['note'] = f"the position is outside the layer's extent (lon {layer.bbox[0]:.3f}..{layer.bbox[2]:.3f}, lat {layer.bbox[1]:.3f}..{layer.bbox[3]:.3f})"
        return out
    to_xy = _local(lat, lon)
    rad_m = radius_km * 1000.0
    deg_lat = radius_km / 111.0
    deg_lon = deg_lat / max(math.cos(math.radians(lat)), 0.01)
    best_by_value: Dict[float, float] = {}
    for f in layer.features:
        v = f.attrs.get(value_attr)
        if v is None:
            continue
        if f.bbox is not None:
            x0, y0, x1, y1 = f.bbox
            if x1 < lon - deg_lon or x0 > lon + deg_lon or y1 < lat - deg_lat or y0 > lat + deg_lat:
                continue
        best = float('inf')
        for part in f.parts:
            pts = [to_xy(x, y) for (x, y) in part]
            for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
                d, _, _ = _seg_nearest(0.0, 0.0, ax, ay, bx, by)
                if d < best:
                    best = d
        if best <= rad_m and best < best_by_value.get(float(v), float('inf')):
            best_by_value[float(v)] = best
    near = sorted(((d / 1000.0, v) for v, d in best_by_value.items()))
    out['nearest'] = [{'value': v, 'distance_km': round(d, 3)} for d, v in near[:6]]
    if not near:
        out['status'] = 'NO CONTOUR WITHIN RADIUS'
        return out
    d0, v0 = near[0]
    if d0 < 0.25:
        out['on'] = v0
    if len(near) >= 2:
        d1, v1 = near[1]
        # the bracket holds only when the two nearest values are one contour interval apart: a position between two
        # adjacent contours is nearer to them than to any other pair; a pair two intervals apart means the lines do
        # not resolve the bracket here (a wrap, a gap in the map), and the answer is the nearest contour alone
        values = sorted(set(float(f.attrs[value_attr]) for f in layer.features if f.attrs.get(value_attr) is not None))
        steps = [b - a for a, b in zip(values[:-1], values[1:]) if b - a > 0]
        interval = min(steps) if steps else None
        out['contour_interval'] = interval
        if interval is not None and abs(abs(v1 - v0) - interval) < 1e-6:
            out['between'] = sorted([v0, v1])
            out['between_distances_km'] = {str(v0): round(d0, 3), str(v1): round(d1, 3)}
        else:
            out['note'] = 'the two nearest contours are not one interval apart: the bracket is not resolved by the lines here'
    return out


def within_areas(layer: Layer, lat: float, lon: float) -> dict:
    """Which polygons of an area layer contain the position (ray casting on the plan coordinates; holes as the
    specification has them - inner rings counted by parity)."""
    ta, na = guess_attrs(layer)
    out = {'lat': lat, 'lon': lon, 'layer': layer.name, 'kind': 'areas', 'status': 'OK', 'inside': [], 'name_attr': na}
    if not inside_extent(layer, lat, lon):
        out['status'] = 'OUTSIDE LAYER EXTENT'
        return out
    for f in layer.features:
        if f.kind != 'Polygon':
            continue
        if f.bbox is not None:
            x0, y0, x1, y1 = f.bbox
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
        crossings = 0
        for ring in f.parts:
            n = len(ring)
            for i in range(n):
                (x1_, y1_), (x2_, y2_) = ring[i], ring[(i + 1) % n]
                if (y1_ > lat) != (y2_ > lat):
                    xi = x1_ + (lat - y1_) * (x2_ - x1_) / (y2_ - y1_)
                    if xi > lon:
                        crossings += 1
        if crossings % 2 == 1:
            out['inside'].append({'name': str(f.attrs.get(na, '')) if na else '', 'attrs': dict(f.attrs)})
    return out


def nearest_points(layer: Layer, lat: float, lon: float, radius_km: float = 10.0, n_list: int = 5) -> dict:
    """The nearest points of a point layer (picked wells, stations) with their attributes and distances."""
    from . import geodesy as GD
    out = {'lat': lat, 'lon': lon, 'layer': layer.name, 'kind': 'points', 'radius_km': radius_km, 'status': 'OK', 'points': [], 'n_within': 0}
    if not inside_extent(layer, lat, lon, margin_deg=radius_km / 111.0):
        out['status'] = 'OUTSIDE LAYER EXTENT'
        return out
    deg_lat = radius_km / 111.0
    deg_lon = deg_lat / max(math.cos(math.radians(lat)), 0.01)
    hits = []
    for f in layer.features:
        for part in f.parts:
            for (x, y) in part:
                if abs(x - lon) > deg_lon or abs(y - lat) > deg_lat:
                    continue
                g = GD.geodesic_m(lat, lon, y, x)
                if g['distance_m'] <= radius_km * 1000.0:
                    hits.append({'distance_km': round(g['distance_m'] / 1000.0, 3), 'bearing_deg': round(g['azimuth_deg'] % 360.0, 1), 'lat': y, 'lon': x, 'attrs': dict(f.attrs)})
    hits.sort(key=lambda h: h['distance_km'])
    out['points'] = hits[:n_list]
    out['n_within'] = len(hits)
    return out


def describe(layer: Layer, lat: float, lon: float, radius_km: float = 50.0, type_attr: Optional[str] = None, name_attr: Optional[str] = None) -> dict:
    """One layer's answer for a position, by what kind of layer it is."""
    k = layer_kind(layer)
    if k == 'contours':
        r = contour_at(layer, lat, lon, radius_km=radius_km)
    elif k == 'areas':
        r = within_areas(layer, lat, lon)
    elif k == 'points':
        r = nearest_points(layer, lat, lon, radius_km=min(radius_km, 10.0))
    else:
        r = nearest(layer, lat, lon, radius_km, type_attr, name_attr)
        r['kind'] = 'structure'
    return r


def compass(bearing_deg: float) -> str:
    names = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return names[int((bearing_deg % 360.0) / 22.5 + 0.5) % 16]


def lines(res: dict) -> List[str]:
    """The statement as the well page and the SRA packet print it, by the layer's kind."""
    if res['status'] != 'OK':
        return [f"Layer {res['layer']}: {res['status']}" + (f" - {res['note']}" if res.get('note') else '') + '.']
    kind = res.get('kind', 'structure')
    if kind == 'contours':
        u = res.get('unit') or ''
        fmtv = lambda v: f"{v:,.0f} {u}".strip()
        if res.get('on') is not None:
            head = f"On the {fmtv(res['on'])} contour of {res['layer']} ({res['value_attr']})"
        elif res.get('between'):
            a, b = res['between']
            head = f"Between the {fmtv(a)} and {fmtv(b)} contours of {res['layer']} ({res['value_attr']})"
        else:
            head = f"Nearest contour of {res['layer']}: {fmtv(res['nearest'][0]['value'])} at {res['nearest'][0]['distance_km']:.2f} km"
        near = ', '.join(f"{fmtv(n['value'])} at {n['distance_km']:.2f} km" for n in res['nearest'][:4])
        return [head + f"; nearest contours: {near}.", 'A contour map is read, not interpolated: the bracket is the answer, and a pick at the well is the log\'s, not the map\'s.']
    if kind == 'areas':
        if res['inside']:
            return [f"Inside {', '.join(x['name'] or 'an unnamed area' for x in res['inside'])} ({res['layer']})."]
        return [f"Inside no area of {res['layer']} (the position is within the layer's extent but in none of its polygons)."]
    if kind == 'points':
        if not res['points']:
            return [f"No point of {res['layer']} within {res['radius_km']:g} km."]
        p = res['points'][0]
        keep = {k: v for k, v in p['attrs'].items() if not k.lower().startswith('shape') and k.lower() not in ('latitude', 'longitude', 'row_labels')}
        vals = ', '.join(f"{k} {v}" for k, v in list(keep.items())[:6])
        return [f"Nearest point of {res['layer']}: {p['distance_km']:.2f} km to the {compass(p['bearing_deg'])} - {vals}; {res['n_within']} within {res['radius_km']:g} km.",
                'A neighbouring pick is that well\'s, at that well; it is not a pick at this one.']
    out = []
    nf = res['nearest_fault']
    if nf:
        out.append(f"Nearest mapped fault: {nf['distance_km']:.2f} km to the {compass(nf['bearing_deg'])} ({nf['bearing_deg']:.0f} deg)"
                   + (f" - {nf['name']}" if nf['name'] and nf['name'].lower() not in ('not compiled', 'unnamed', '') else '')
                   + (f" ({nf['type']})" if nf['type'] else '') + f", from layer {res['layer']}.")
    else:
        out.append(f"No mapped fault within {res['radius_km']:g} km in layer {res['layer']} ({res['n_considered']} features considered).")
    nfo = res['nearest_fold']
    if nfo:
        out.append(f"Nearest mapped fold: {nfo['distance_km']:.2f} km to the {compass(nfo['bearing_deg'])}"
                   + (f" - {nfo['name']}" if nfo['name'] and nfo['name'].lower() not in ('not compiled', 'unnamed', '') else '') + (f" ({nfo['type']})" if nfo['type'] else '') + '.')
    c = res.get('counts_within', {})
    out.append(f"Within {res['radius_km']:g} km: {c.get('fault', 0)} fault trace(s), {c.get('fold', 0)} fold axis/axes, {c.get('other', 0)} other feature(s).")
    out.append('A distance to a mapped trace is a fact of the map, not a hazard judgement; the map\'s scale and sources are in its own metadata.')
    return out
