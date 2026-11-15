"""hardline - how near each well runs to a boundary the site has declared, and where its bit is heading.

A lease line, a unit boundary or the edge of a project area is a line on the map; the rule that goes with it is a
distance to keep from it - the setback. The hard line is the boundary moved in by that distance (or, for an area to
keep out of, moved out by it). This module takes the boundary the site declares - a polygon of a layer the site
brought, or corners typed in - and each well's surveyed path, and says for every well: how far from the hard line it
runs at its nearest, where along the hole that is, how much of the hole is within the warning distance, inside the
setback or over the boundary; where the bit is now (the bottom of the hole from the drilling record, carried beyond
the last survey station on that station's direction), how far it is from the hard line, and - held on that direction -
how much more hole reaches it. On a record still being written an alert is raised once when the bit comes within the
warning distance, once when it is inside the setback, and once when it is over the boundary.

The distance is in plan: a boundary is a vertical surface, and depth does not enter. It is measured in the 3D tab's
frame (a local transverse Mercator centred on the wells, north true at the centre), from points every two metres
along the path to the nearest point of the boundary. The distance to the hard line is that distance less the setback,
which is exact: the hard line is the set of points one setback from the boundary.

What it will not call a measurement: the boundary (it is what the site declared, from the file or the corners it
gave, on the datum they carry - a plat or a survey of the ground decides where a lease line is); the setback and the
warning distance (the rule is the regulator's and the operator's, and is the site's statement); the well's position
to better than its survey (the survey's one-sigma across the line is printed beside each distance and is not taken
off it); the bit beyond the last survey station (projected on that station's direction - the next survey decides);
a well with no surface position, elevation and survey (it is listed, never drawn).
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
STEP_M = 2.0                       # the path is sampled this often
AHEAD_FT = 500.0                   # how far ahead of the bit the heading is carried for the page
REACH_M = 3000.0                   # ... and how far it is carried to look for the hard line
MAX_BEYOND_FT = 500.0              # a hole this far beyond its last survey station is not surveyed there: the bit is not projected so far
KINDS = ('stay_inside', 'keep_out')
KIND_WORDS = {'stay_inside': 'a boundary to stay inside (a lease, a unit, a project area): the hard line is the setback inside it',
              'keep_out': 'an area to keep out of: the hard line is the setback outside it'}
EVENTS = {'warn': 'hardline.warn', 'setback': 'hardline.setback', 'crossed': 'hardline.crossed'}
NOT_A_MEASUREMENT = [
    'the boundary: it is the polygon the site declared, from the file or the corners it gave on the datum they carry - a plat or a survey of the ground decides where the line is',
    'the setback and the warning distance: the rule is the regulator\'s and the operator\'s, and both are the site\'s statement here',
    'the well\'s position to better than its survey: the survey\'s one-sigma across the line is printed beside the distance and is not taken off it',
    'the bit beyond the last survey station: it is projected on that station\'s inclination and azimuth, and the next survey decides',
    'a distance in depth: the boundary is taken as a vertical surface, and the distance is in plan',
    'a well with no surface position, reference elevation and directional survey: it is listed with what it needs and is not judged',
]


class HardLineError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'hardline.json')


def settings(ws) -> dict:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault('boundaries', [])
    return s


def _save(ws, s: dict) -> None:
    tmp = _path(ws) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, _path(ws))


def parse_corners(text: str) -> List[Tuple[float, float]]:
    """Corners typed in: a latitude and a longitude on each line (or separated by semicolons), decimal degrees."""
    out = []
    for ln in str(text or '').replace(';', '\n').splitlines():
        t = [x for x in ln.replace(',', ' ').split() if x]
        if not t:
            continue
        try:
            lat, lon = float(t[0]), float(t[1])
        except (ValueError, IndexError):
            raise HardLineError(f'a corner is a latitude and a longitude in decimal degrees: {ln.strip()!r} is not')
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HardLineError(f'a corner is a latitude then a longitude: {ln.strip()!r} is out of range')
        out.append((lat, lon))
    if len(out) >= 2 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3:
        raise HardLineError('a boundary needs three corners or more')
    return out


def polygon_layers(ws) -> List[dict]:
    """The layers of the workspace that hold polygons, with each polygon's label: what a boundary can be chosen from."""
    out = []
    for rec in ws.layers():
        if 'polygon' not in str(rec.get('shape_type', '')).lower():
            continue
        try:
            L = ws.load_layer(rec['id'])
        except Exception:
            continue
        feats = []
        for i, f in enumerate(L.features):
            lab = next((str(v).strip() for k, v in f.attrs.items() if isinstance(v, str) and v.strip()), '') or f'polygon {i + 1}'
            feats.append({'index': i, 'label': lab, 'corners': sum(max(0, len(p) - 1) for p in f.parts)})
        out.append({'id': rec['id'], 'display': rec.get('display') or rec['id'], 'datum': (rec.get('crs') or {}).get('datum') or '', 'features': feats, 'citation': rec.get('citation') or ''})
    return out


def declare(ws, actor: str, name: str, kind: str, setback_ft, warn_ft, source: str, layer: Optional[str] = None, feature=None, corners: Optional[str] = None,
            datum: str = 'WGS84') -> dict:
    """A boundary and its rule, declared: a polygon of a layer (by its number or its label) or corners typed in; whether
    the wells stay inside it or keep out of it; the setback, the warning distance, and where the rule came from."""
    from . import geodesy as GD
    name = str(name or '').strip()
    if not name:
        raise HardLineError('name the boundary (the lease, the unit, the area)')
    if kind not in KINDS:
        raise HardLineError(f"a boundary is one of {', '.join(KINDS)}")
    try:
        sb = float(setback_ft); wn = float(warn_ft if warn_ft not in (None, '') else 0.0)
    except (TypeError, ValueError):
        raise HardLineError('the setback and the warning distance are numbers of feet')
    if not (0.0 <= sb <= 10000.0) or not (0.0 <= wn <= 10000.0):
        raise HardLineError('the setback and the warning distance are between 0 and 10,000 ft')
    if not str(source or '').strip():
        raise HardLineError('say where the boundary and its setback came from (the lease, the field rule, the permit)')
    b: Dict[str, Any] = {'name': name, 'kind': kind, 'setback_ft': sb, 'warn_ft': wn, 'source': str(source).strip(), 'declared_by': actor, 'declared_utc': _utc()}
    if layer:
        rec = next((x for x in polygon_layers(ws) if str(layer).strip().lower() in (x['id'].lower(), x['display'].lower())), None)
        if rec is None:
            raise HardLineError(f'no layer of polygons named {layer!r} in this workspace')
        f = None
        if feature not in (None, ''):
            f = next((x for x in rec['features'] if str(x['index']) == str(feature) or x['label'].lower() == str(feature).strip().lower()), None)
        elif len(rec['features']) == 1:
            f = rec['features'][0]
        if f is None:
            raise HardLineError(f"say which polygon of {rec['display']}: " + '; '.join(f"{x['index']} ({x['label']})" for x in rec['features'][:12]))
        b.update(layer=rec['id'], feature=f['index'], feature_label=f['label'], datum=rec['datum'] or 'UNKNOWN')
    elif corners:
        if str(datum or '').upper() not in GD.DATUMS:
            raise HardLineError(f"the corners' datum is one of {', '.join(sorted(GD.DATUMS))}")
        b.update(corners=[[la, lo] for la, lo in parse_corners(corners)], datum=str(datum).upper())
    else:
        raise HardLineError('a boundary is a polygon of a layer, or corners typed in')
    s = settings(ws)
    ids = {x['id'] for x in s['boundaries']}
    base = ''.join(c.lower() if c.isalnum() else '-' for c in name).strip('-') or 'boundary'
    bid, k = base, 2
    while bid in ids:
        bid = f'{base}-{k}'; k += 1
    b['id'] = bid
    rings(ws, b)                                                     # it must read before it is kept
    s['boundaries'].append(b)
    _save(ws, s)
    ws.audit(actor, 'hardline.declared', {k_: v for k_, v in b.items() if k_ != 'corners'} | ({'corners': len(b['corners'])} if b.get('corners') else {}))
    return b


def remove(ws, actor: str, boundary_id: str) -> dict:
    s = settings(ws)
    b = next((x for x in s['boundaries'] if x['id'] == boundary_id), None)
    if b is None:
        raise KeyError(f'no boundary {boundary_id}')
    s['boundaries'] = [x for x in s['boundaries'] if x['id'] != boundary_id]
    _save(ws, s)
    ws.audit(actor, 'hardline.removed', {'id': boundary_id, 'name': b['name']})
    return b


def rings(ws, b: dict) -> List[List[Tuple[float, float]]]:
    """The boundary's rings as (latitude, longitude) on WGS84."""
    from . import geodesy as GD
    if b.get('layer'):
        try:
            L = ws.load_layer(b['layer'])
            f = L.features[int(b['feature'])]
        except Exception as e:
            raise HardLineError(f"the boundary's polygon is not there any more ({b['layer']}, polygon {b.get('feature')}): {e}")
        raw = [[(la, lo) for lo, la in part] for part in f.parts]
    else:
        raw = [[(float(la), float(lo)) for la, lo in b['corners']]]
    out = []
    for r in raw:
        if len(r) >= 2 and r[0] == r[-1]:
            r = r[:-1]
        if len(r) < 3:
            continue
        g = [GD.to_wgs84(la, lo, 0.0, b.get('datum') or 'UNKNOWN') for la, lo in r]
        out.append([(x['lat'], x['lon']) for x in g])
    if not out:
        raise HardLineError('the boundary has no ring of three corners or more')
    return out


# ---------------------------------------------------------------------------------------------------------------
# the geometry, in a plane
# ---------------------------------------------------------------------------------------------------------------
def boundary_distance(px: np.ndarray, py: np.ndarray, rings_xy: List[np.ndarray]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """For each point: its distance to the nearest point of the boundary, that point, the ring and edge it is on, and
    whether the point is inside (even-odd over the rings)."""
    n = len(px)
    best = np.full(n, np.inf); bx = np.zeros(n); by = np.zeros(n); be = np.zeros(n, dtype=int)
    inside = np.zeros(n, dtype=bool)
    e = 0
    for ring in rings_xy:
        m = len(ring)
        for i in range(m):
            ax, ay = ring[i]; cx, cy = ring[(i + 1) % m]
            dx, dy = cx - ax, cy - ay
            L2 = dx * dx + dy * dy
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0.0, 1.0) if L2 > 0 else np.zeros(n)
            qx, qy = ax + t * dx, ay + t * dy
            d = np.hypot(px - qx, py - qy)
            k = d < best
            best[k] = d[k]; bx[k] = qx[k]; by[k] = qy[k]; be[k] = e
            cross = ((ay > py) != (cy > py))
            with np.errstate(divide='ignore', invalid='ignore'):
                xi = ax + (py - ay) * dx / np.where(dy != 0, dy, np.nan)
            inside ^= cross & (px < xi)
            e += 1
    return best, bx, by, be, inside


def analyse_path(md, x, y, z, rings_xy: List[np.ndarray], kind: str, setback_m: float, warn_m: float, bit_md: Optional[float] = None, ahead_m: float = AHEAD_FT * FT) -> dict:
    """One well's path (metres along hole, east, north, depth) against one boundary. The margin is the distance to the
    hard line: positive on the allowed side of it."""
    md = np.asarray(md, float); x = np.asarray(x, float); y = np.asarray(y, float); z = np.asarray(z, float)
    g = np.unique(np.r_[np.arange(md[0], md[-1], STEP_M), md[-1]])
    gx, gy, gz = np.interp(g, md, x), np.interp(g, md, y), np.interp(g, md, z)
    sign = 1.0 if kind == 'stay_inside' else -1.0

    def margin_of(px, py):
        d, qx, qy, e, ins = boundary_distance(px, py, rings_xy)
        signed = np.where(ins, d, -d) * sign                           # positive on the side the well may be on
        return signed, signed - setback_m, qx, qy, e
    signed, mar, qx, qy, e = margin_of(gx, gy)
    k = int(np.argmin(mar))
    step = np.diff(g, prepend=g[0])
    out: Dict[str, Any] = {
        'min_margin_m': float(mar[k]), 'min_distance_m': float(signed[k]), 'at_md_m': float(g[k]), 'at_z_m': float(gz[k]), 'at_xy': [float(gx[k]), float(gy[k])],
        'nearest_xy': [float(qx[k]), float(qy[k])], 'edge': int(e[k]), 'surface_margin_m': float(mar[0]), 'td_margin_m': float(mar[-1]),
        'warn_m': float(step[(mar < warn_m)].sum()), 'setback_m': float(step[(mar < 0)].sum()), 'over_m': float(step[(signed < 0)].sum()),
        'first_warn_md_m': (float(g[np.argmax(mar < warn_m)]) if (mar < warn_m).any() else None),
        'first_setback_md_m': (float(g[np.argmax(mar < 0)]) if (mar < 0).any() else None),
        'first_over_md_m': (float(g[np.argmax(signed < 0)]) if (signed < 0).any() else None)}
    out['status'] = ('OVER THE BOUNDARY' if out['over_m'] > 0 else 'INSIDE THE SETBACK' if out['setback_m'] > 0 else 'WITHIN THE WARNING DISTANCE' if out['warn_m'] > 0 else 'CLEAR')
    # the bit: the bottom of the hole, carried beyond the last station on its direction
    j = max(0, len(md) - 2)
    while j > 0 and md[-1] - md[j] < 5.0:
        j -= 1
    seg = max(1e-9, md[-1] - md[j])
    ux, uy, uz = (x[-1] - x[j]) / seg, (y[-1] - y[j]) / seg, (z[-1] - z[j]) / seg
    bm = md[-1] if bit_md is None else float(bit_md)
    beyond = max(0.0, bm - md[-1])
    if bm <= md[-1]:
        bx_, by_, bz_ = float(np.interp(bm, md, x)), float(np.interp(bm, md, y)), float(np.interp(bm, md, z))
    else:
        bx_, by_, bz_ = float(x[-1] + ux * beyond), float(y[-1] + uy * beyond), float(z[-1] + uz * beyond)
    s_b, m_b, qbx, qby, _ = margin_of(np.array([bx_]), np.array([by_]))
    horiz = math.hypot(ux, uy)
    bit: Dict[str, Any] = {'md_m': float(bm), 'beyond_survey_m': float(beyond), 'xy': [bx_, by_], 'z_m': bz_, 'margin_m': float(m_b[0]), 'distance_m': float(s_b[0]),
                           'nearest_xy': [float(qbx[0]), float(qby[0])], 'inclination_deg': float(math.degrees(math.acos(max(-1.0, min(1.0, uz))))),
                           'azimuth_deg': (float(math.degrees(math.atan2(ux, uy)) % 360.0) if horiz > 1e-6 else None)}
    bit['status'] = 'OVER THE BOUNDARY' if s_b[0] < 0 else 'INSIDE THE SETBACK' if m_b[0] < 0 else 'WITHIN THE WARNING DISTANCE' if m_b[0] < warn_m else 'CLEAR'
    if horiz > 1e-3:
        tt = np.arange(0.0, REACH_M + STEP_M, STEP_M)
        _, m_r, _, _, _ = margin_of(bx_ + ux * tt, by_ + uy * tt)
        _, m_a, _, _, _ = margin_of(np.array([bx_ + ux * ahead_m]), np.array([by_ + uy * ahead_m]))
        bit['ahead_m'] = float(ahead_m); bit['ahead_margin_m'] = float(m_a[0])
        hit = np.flatnonzero(m_r < 0)
        bit['reach_m'] = None
        if len(hit) and m_b[0] >= 0:                                     # where the margin passes through nought, between two samples
            i = int(hit[0])
            bit['reach_m'] = float(tt[i - 1] + (tt[i] - tt[i - 1]) * m_r[i - 1] / (m_r[i - 1] - m_r[i])) if i > 0 and m_r[i - 1] > m_r[i] else float(tt[i])
        closing = float(m_r[min(5, len(m_r) - 1)] - m_r[0]) < -1e-6
        bit['closing'] = bool(closing)
    else:
        bit.update(ahead_m=float(ahead_m), ahead_margin_m=float(m_b[0]), reach_m=None, closing=False)
    out['bit'] = bit
    return out


# ---------------------------------------------------------------------------------------------------------------
# the workspace
# ---------------------------------------------------------------------------------------------------------------
def _bit_md_m(ws, wid: str) -> Tuple[Optional[float], Optional[str], Optional[float]]:
    """The bottom of the hole from the well's drilling record (metres), when it has one; the end of the record; the
    age of its file in seconds."""
    try:
        from . import series_cache as SCH
        src = SCH.source_path(ws, wid)
        c = SCH.load(SCH.cache_dir(ws, wid), src)
        if c is None:
            return None, None, None
        bo = c.extra('bottom')
        if bo is None:
            return None, None, None
        bo = np.asarray(bo, dtype=np.float64)
        ok = np.isfinite(bo)
        if not ok.any():
            return None, None, None
        unit = (c.unit('Hole Depth') or 'ft').lower()
        end = None
        if c.meta.get('start_time'):
            from datetime import timedelta
            end = (datetime.fromisoformat(c.meta['start_time']) + timedelta(seconds=float(np.asarray(c.index)[-1]))).isoformat()[:19]
        age = datetime.now(timezone.utc).timestamp() - os.path.getmtime(src)
        return float(np.max(bo[ok])) * (FT if unit.startswith('f') else 1.0), end, age
    except Exception:
        return None, None, None


def _sigma_across(co: Optional[dict], md_m: float, at_xy, near_xy) -> Optional[float]:
    """The survey's one-sigma, at that depth, along the line from the well to the nearest point of the boundary."""
    if co is None:
        return None
    try:
        k = int(np.clip(np.searchsorted(co['grid_md'], md_m), 0, len(co['grid_md']) - 1))
        de, dn = near_xy[0] - at_xy[0], near_xy[1] - at_xy[1]
        h = math.hypot(de, dn)
        if h < 1e-6:
            return None
        u = np.array([dn / h, de / h, 0.0])                             # north, east, down
        return float(math.sqrt(max(0.0, float(u @ co['cov'][k] @ u))))
    except Exception:
        return None


def page(ws, now: Optional[datetime] = None) -> dict:
    from . import geodesy as GD
    from . import well3d as W3
    s = settings(ws)
    sc = W3.workspace_scene(ws)
    wells = sc.get('wells') or []
    out: Dict[str, Any] = {'generated_utc': _utc(), 'title': ws.manifest.get('name') or '', 'boundaries': [], 'rows': [], 'wells': [], 'not_placed': sc.get('not_placed') or [],
                           'layers': polygon_layers(ws), 'kinds': KIND_WORDS, 'not_a_measurement': NOT_A_MEASUREMENT, 'lines': [], 'ahead_ft': AHEAD_FT}
    frame = None
    if wells and (sc.get('frame') or {}).get('lat0') is not None:
        frame = GD._tm(sc['frame']['lat0'], sc['frame']['lon0'], 1.0, 0.0, 0.0, 'WGS84')
        out['frame'] = sc['frame']['crs']
    co_by: Dict[str, dict] = {}
    if wells and s['boundaries']:
        try:
            from . import collision as CO
            cw, _ = CO.workspace_wells(ws)
            for w in cw:
                if w.get('basis') == 'survey':
                    co_by[w['id']] = CO.prepare(w)
        except Exception:
            co_by = {}
    for w in wells:
        bit_m, rec_end, age = _bit_md_m(ws, w['id'])
        p = w['path']
        out['wells'].append({'id': w['id'], 'name': w['name'], 'td_md_ft': round(w['td_md'] / FT, 1), 'bit_md_ft': (round(bit_m / FT, 1) if bit_m is not None else None), 'record_end': rec_end,
                             'live': bool(age is not None and age <= 7200.0), 'x': [round(v, 1) for v in p['x'][::3]] + [round(p['x'][-1], 1)], 'y': [round(v, 1) for v in p['y'][::3]] + [round(p['y'][-1], 1)],
                             '_bit_m': bit_m})
    for b in s['boundaries']:
        rec = dict(b)
        try:
            rl = rings(ws, b)
        except HardLineError as e:
            rec['error'] = str(e); out['boundaries'].append(rec); continue
        rec['corners_n'] = sum(len(r) for r in rl)
        rec['kind_words'] = KIND_WORDS[b['kind']]
        if frame is None:
            rec['rings_ll'] = [[[round(la, 7), round(lo, 7)] for la, lo in r] for r in rl]
            out['boundaries'].append(rec); continue
        rxy = [np.array([[q['easting_m'], q['northing_m']] for q in (GD.project(la, lo, frame) for la, lo in r)]) for r in rl]
        rec['rings_xy'] = [[[round(float(a), 1), round(float(c), 1)] for a, c in r] for r in rxy]
        rec['rings_ll'] = [[[round(la, 7), round(lo, 7)] for la, lo in r] for r in rl]
        out['boundaries'].append(rec)
        for w, wr in zip(wells, out['wells']):
            p = w['path']
            uncovered = (wr['_bit_m'] - w['td_md']) if wr['_bit_m'] is not None and wr['_bit_m'] - w['td_md'] > MAX_BEYOND_FT * FT else None
            a = analyse_path(p['md'], p['x'], p['y'], p['z'], rxy, b['kind'], b['setback_ft'] * FT, b['warn_ft'] * FT, None if uncovered is not None else wr['_bit_m'])
            sig = _sigma_across(co_by.get(w['id']), a['at_md_m'], a['at_xy'], a['nearest_xy'])
            nla, nlo = GD.unproject(a['nearest_xy'][0], a['nearest_xy'][1], frame)[:2]
            near = {'lat': float(nla), 'lon': float(nlo)}
            bt = a['bit']
            row = {'boundary': b['id'], 'boundary_name': b['name'], 'well_id': w['id'], 'well': w['name'], 'status': a['status'],
                   'min_margin_ft': round(a['min_margin_m'] / FT, 1), 'min_distance_ft': round(a['min_distance_m'] / FT, 1), 'at_md_ft': round(a['at_md_m'] / FT, 1),
                   'at_below_sea_level_ft': round(a['at_z_m'] / FT, 1), 'nearest_lat': round(near['lat'], 7), 'nearest_lon': round(near['lon'], 7), 'edge': a['edge'] + 1,
                   'sigma_ft': (round(sig / FT, 1) if sig is not None else None), 'surface_margin_ft': round(a['surface_margin_m'] / FT, 1), 'td_margin_ft': round(a['td_margin_m'] / FT, 1),
                   'warn_ft_of_hole': round(a['warn_m'] / FT), 'setback_ft_of_hole': round(a['setback_m'] / FT), 'over_ft_of_hole': round(a['over_m'] / FT),
                   'first_warn_md_ft': (round(a['first_warn_md_m'] / FT) if a['first_warn_md_m'] is not None else None),
                   'first_setback_md_ft': (round(a['first_setback_md_m'] / FT) if a['first_setback_md_m'] is not None else None),
                   'first_over_md_ft': (round(a['first_over_md_m'] / FT) if a['first_over_md_m'] is not None else None),
                   'at_xy': [round(v, 1) for v in a['at_xy']], 'nearest_xy': [round(v, 1) for v in a['nearest_xy']],
                   'bit': {'md_ft': round(bt['md_m'] / FT, 1), 'beyond_survey_ft': round(bt['beyond_survey_m'] / FT, 1), 'margin_ft': round(bt['margin_m'] / FT, 1), 'distance_ft': round(bt['distance_m'] / FT, 1),
                           'status': bt['status'], 'inclination_deg': round(bt['inclination_deg'], 1), 'azimuth_deg': (round(bt['azimuth_deg'], 1) if bt['azimuth_deg'] is not None else None),
                           'ahead_ft': round(bt['ahead_m'] / FT), 'ahead_margin_ft': round(bt['ahead_margin_m'] / FT, 1), 'reach_ft': (round(bt['reach_m'] / FT) if bt.get('reach_m') is not None else None),
                           'closing': bt['closing'], 'xy': [round(v, 1) for v in bt['xy']], 'from_record': wr['_bit_m'] is not None and uncovered is None,
                           'uncovered_ft': (round(uncovered / FT) if uncovered is not None else None)}}
            out['rows'].append(row)
    for wr in out['wells']:
        wr.pop('_bit_m', None)
    out['status'] = 'NO BOUNDARY' if not s['boundaries'] else 'NO WELLS' if not wells else 'OK'
    order = {'OVER THE BOUNDARY': 0, 'INSIDE THE SETBACK': 1, 'WITHIN THE WARNING DISTANCE': 2, 'CLEAR': 3}
    out['rows'].sort(key=lambda r: (order[r['status']], r['min_margin_ft']))
    out['worst'] = out['rows'][0] if out['rows'] else None
    out['lines'] = _lines(out)
    try:
        from . import public_pad as PP
        cr = PP.credit(ws)
        if cr:
            out['public_source'] = {'name': cr['name'], 'licence': cr['licence'], 'credit': cr['credit']}
            out['lines'].append(cr['line'])
    except Exception:
        pass
    return out


def describe(r: dict) -> str:
    b = r['bit']
    side = 'over the boundary by' if r['min_distance_ft'] < 0 else 'from the hard line'
    t = (f"{r['well']} against {r['boundary_name']}: {r['status'].lower()} - at its nearest, {r['at_md_ft']:,.0f} ft MD, "
         + (f"{abs(r['min_distance_ft']):,.0f} ft over the boundary" if r['min_distance_ft'] < 0 else f"{abs(r['min_margin_ft']):,.0f} ft {'inside the setback' if r['min_margin_ft'] < 0 else side}")
         + (f" (the survey's one-sigma across the line there {r['sigma_ft']:,.0f} ft)" if r.get('sigma_ft') is not None else ''))
    if b.get('uncovered_ft'):
        t += f"; the drilling record's hole is {b['uncovered_ft']:,.0f} ft deeper than the last survey station - not surveyed there, and not projected that far: what follows is for the last station"
    t += (f"; the bit at {b['md_ft']:,.0f} ft MD" + (f", {b['beyond_survey_ft']:,.0f} ft beyond the last survey station and projected on its direction," if b['beyond_survey_ft'] > 1 else '')
          + f" is {abs(b['margin_ft']):,.0f} ft " + ('past the hard line' if b['margin_ft'] < 0 else 'from the hard line'))
    if b.get('reach_ft') is not None:
        t += f"; held on {b['azimuth_deg']:.0f} degrees at {b['inclination_deg']:.0f} degrees of inclination it reaches the hard line in {b['reach_ft']:,.0f} ft more hole"
    elif b['margin_ft'] >= 0:
        t += '; held on its direction it does not reach the hard line' + ('' if b['azimuth_deg'] is not None else ' (the hole is vertical there)')
    return t


def _lines(p: dict) -> List[str]:
    if p['status'] == 'NO BOUNDARY':
        return ['No boundary is declared for this site: declare a lease line, a unit boundary or an area to keep out of - a polygon of a layer the site brought, or corners typed in - with its setback, '
                'and every well with a survey is measured against it.'] + (
            [f"{len(p['not_placed'])} well(s) have no surface position, elevation and survey and would not be judged: " + '; '.join(f"{x['display']} ({x['why']})" for x in p['not_placed'][:8])] if p['not_placed'] else [])
    L = []
    for b in p['boundaries']:
        if b.get('error'):
            L.append(f"{b['name']}: {b['error']}")
            continue
        L.append(f"{b['name']}: {KIND_WORDS[b['kind']].split(':')[0]}, {b.get('corners_n', 0)} corners"
                 + (f" (polygon {b['feature_label']!r} of the layer {b['layer']})" if b.get('layer') else ' (corners typed in)') + f", on {b.get('datum')}; setback {b['setback_ft']:,.0f} ft, "
                 f"warning within {b['warn_ft']:,.0f} ft of the hard line. Source: {b['source']}.")
    if p['status'] == 'NO WELLS':
        L.append('No well of this site has a surface position, a reference elevation and a directional survey, so none is measured against it.')
    L.extend(describe(r) + '.' for r in p['rows'])
    if p['not_placed']:
        L.append('Not judged: ' + '; '.join(f"{x['display']} ({x['why']})" for x in p['not_placed'][:12]) + '.')
    L.append('The distance is in plan, from points every 2 m along the surveyed path to the nearest point of the boundary, less the setback; the survey\'s own uncertainty is printed and not taken off.')
    return L


def text(p: dict) -> str:
    out = ['Hard line: each well against the declared boundary', '=' * 50]
    out.extend(p['lines'])
    if p['rows']:
        out.append(f"  {'well':16s} {'boundary':22s} {'status':28s} {'nearest ft':>10s} {'at MD ft':>9s} {'sigma ft':>8s} {'bit ft':>8s} {'ahead ft':>9s} {'reach ft':>9s}")
        for r in p['rows']:
            b = r['bit']
            out.append(f"  {r['well'][:16]:16s} {r['boundary_name'][:22]:22s} {r['status']:28s} {r['min_margin_ft']:>10,.0f} {r['at_md_ft']:>9,.0f} {(r['sigma_ft'] if r['sigma_ft'] is not None else float('nan')):>8,.0f} "
                       f"{b['margin_ft']:>8,.0f} {b['ahead_margin_ft']:>9,.0f} {(str(b['reach_ft']) if b['reach_ft'] is not None else '-'):>9s}")
        out.append('  nearest, bit and ahead are distances to the hard line (the boundary less the setback), negative past it; reach is how much more hole, held on the bit\'s direction, comes to the hard line.')
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'hardline')
    slim = {k: v for k, v in p.items() if k != 'wells'} | {'wells': [{k: v for k, v in w.items() if k not in ('x', 'y')} for w in p['wells']]}
    with open(os.path.join(d, 'hardline.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **slim}, f, indent=1, default=str)
    with open(os.path.join(d, 'hardline.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Hard line: each well against the declared boundary\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines'])
                + '\n\n```\n' + text(p) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'hardline.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'boundary', 'status', 'nearest_to_hard_line_ft', 'nearest_to_boundary_ft', 'at_md_ft', 'nearest_lat', 'nearest_lon', 'survey_sigma_across_ft', 'hole_within_warning_ft',
                     'hole_inside_setback_ft', 'hole_over_boundary_ft', 'bit_md_ft', 'bit_beyond_survey_ft', 'bit_to_hard_line_ft', 'bit_azimuth_deg', 'bit_inclination_deg', 'ahead_ft', 'ahead_to_hard_line_ft',
                     'reach_more_hole_ft'])
        for r in p['rows']:
            b = r['bit']
            wr.writerow([r['well'], r['boundary_name'], r['status'], r['min_margin_ft'], r['min_distance_ft'], r['at_md_ft'], r['nearest_lat'], r['nearest_lon'], '' if r['sigma_ft'] is None else r['sigma_ft'],
                         r['warn_ft_of_hole'], r['setback_ft_of_hole'], r['over_ft_of_hole'], b['md_ft'], b['beyond_survey_ft'], b['margin_ft'], '' if b['azimuth_deg'] is None else b['azimuth_deg'],
                         b['inclination_deg'], b['ahead_ft'], b['ahead_margin_ft'], '' if b['reach_ft'] is None else b['reach_ft']])
    files = ['hardline.json', 'hardline.md', 'hardline.csv']
    ws.audit(actor, 'hardline.report', {'rows': len(p['rows']), 'boundaries': len(p['boundaries'])})
    return {'folder': d, 'files': files}


def live_events(ws, now: Optional[datetime] = None) -> List[dict]:
    """What an alert is raised for: a well whose drilling record is still being written, with its bit within the warning
    distance of a hard line, inside the setback, or over the boundary. Each is raised once a well, a boundary and a level."""
    p = page(ws, now)
    live = {w['id'] for w in p['wells'] if w.get('live')}
    out = []
    for r in p['rows']:
        b = r['bit']
        if r['well_id'] not in live or not b.get('from_record') or b['status'] == 'CLEAR':
            continue
        lvl = 'crossed' if b['status'] == 'OVER THE BOUNDARY' else 'setback' if b['status'] == 'INSIDE THE SETBACK' else 'warn'
        out.append({'event': EVENTS[lvl], 'key': f"{EVENTS[lvl]}:{r['well_id']}:{r['boundary']}", 'well_id': r['well_id'], 'well': r['well'], 'depth_ft': b['md_ft'],
                    'time': next((w['record_end'] for w in p['wells'] if w['id'] == r['well_id']), '') or '', 'detail': describe(r)})
    return out


# ---------------------------------------------------------------------------------------------------------------
# the self-test: paths and boundaries with every distance known
# ---------------------------------------------------------------------------------------------------------------
def selftest() -> dict:
    checks = []
    sq = [np.array([[-1000.0, -1000.0], [1000.0, -1000.0], [1000.0, 1000.0], [-1000.0, 1000.0]])]
    # a vertical hole at the centre of a 2 km square
    a = analyse_path([0, 1000, 2000], [0, 0, 0], [0, 0, 0], [0, 1000, 2000], sq, 'stay_inside', 100.0, 50.0)
    checks.append({'check': 'a vertical hole at the centre of a square 2,000 m on a side, setback 100 m: 900 m from the hard line all the way down, clear, and on no heading to reach it',
                   'value': [round(a['min_margin_m'], 3), a['status'], a['bit']['reach_m']], 'pass': abs(a['min_margin_m'] - 900.0) < 1e-6 and a['status'] == 'CLEAR' and a['bit']['reach_m'] is None})
    # a lateral due east from the centre, 800 m long at 1,500 m depth
    md = [0.0, 1500.0, 2300.0]; x = [0.0, 0.0, 800.0]; y = [0.0, 0.0, 0.0]; z = [0.0, 1500.0, 1500.0]
    a = analyse_path(md, x, y, z, sq, 'stay_inside', 100.0, 150.0)
    checks.append({'check': 'a lateral 800 m due east from the centre: nearest the hard line at its toe, 100 m; the last 50 m of it within a 150 m warning; the bit heading 090, reaching the hard line 100 m on',
                   'value': [round(a['min_margin_m'], 2), round(a['at_md_m'], 1), round(a['warn_m'], 1), a['status'], a['bit']['azimuth_deg'], a['bit']['reach_m'], round(a['bit']['ahead_margin_m'], 1)],
                   'pass': abs(a['min_margin_m'] - 100.0) < 1e-6 and a['at_md_m'] == 2300.0 and abs(a['warn_m'] - 50.0) <= 2.0 and a['status'] == 'WITHIN THE WARNING DISTANCE' and abs(a['bit']['azimuth_deg'] - 90.0) < 1e-6
                           and abs(a['bit']['reach_m'] - 100.0) < 1e-6 and abs(a['bit']['ahead_margin_m'] - (100.0 - AHEAD_FT * FT)) < 1e-6 and a['bit']['closing']})
    # the same lateral carried on by the drilling record: the bit 150 m beyond the last station
    a2 = analyse_path(md, x, y, z, sq, 'stay_inside', 100.0, 150.0, bit_md=2450.0)
    a3 = analyse_path(md, x, y, z, sq, 'stay_inside', 100.0, 150.0, bit_md=2550.0)
    checks.append({'check': 'the bit 150 m beyond the last survey station, projected on its direction: 50 m inside the setback; 250 m beyond: 50 m over the boundary',
                   'value': [round(a2['bit']['margin_m'], 2), a2['bit']['status'], round(a3['bit']['distance_m'], 2), a3['bit']['status'], a['status']],
                   'pass': abs(a2['bit']['margin_m'] + 50.0) < 1e-6 and a2['bit']['status'] == 'INSIDE THE SETBACK' and abs(a3['bit']['distance_m'] + 50.0) < 1e-6 and a3['bit']['status'] == 'OVER THE BOUNDARY'
                           and abs(a2['bit']['beyond_survey_m'] - 150.0) < 1e-9})
    # a lateral that runs out through the east side
    a = analyse_path([0.0, 1500.0, 2700.0], [0.0, 0.0, 1200.0], y, z, sq, 'stay_inside', 100.0, 150.0)
    checks.append({'check': 'a lateral 1,200 m east: within the warning from 750 m out, inside the setback from 900 m, over the boundary from 1,000 m, 200 m of hole over it',
                   'value': [a['status'], a['first_warn_md_m'], a['first_setback_md_m'], a['first_over_md_m'], round(a['over_m'], 1), round(a['min_distance_m'], 1)],
                   'pass': a['status'] == 'OVER THE BOUNDARY' and abs(a['first_warn_md_m'] - 2250.0) <= 2.0 and abs(a['first_setback_md_m'] - 2400.0) <= 2.0 and abs(a['first_over_md_m'] - 2500.0) <= 2.0
                           and abs(a['over_m'] - 200.0) <= 2.0 and abs(a['min_distance_m'] + 200.0) < 1e-6})
    # an area to keep out of, 300 m east of a vertical hole; and a point nearest a corner
    box = [np.array([[300.0, -200.0], [700.0, -200.0], [700.0, 200.0], [300.0, 200.0]])]
    a = analyse_path([0, 1000], [0, 0], [0, 0], [0, 1000], box, 'keep_out', 100.0, 50.0)
    c = analyse_path([0, 1000], [0, 0], [-500.0, -500.0], [0, 1000], box, 'keep_out', 0.0, 50.0)
    i = analyse_path([0, 1000], [500.0, 500.0], [0.0, 0.0], [0, 1000], box, 'keep_out', 0.0, 50.0)
    checks.append({'check': 'an area to keep out of: 300 m from it with a 100 m setback is 200 m from the hard line; nearest a corner the distance is to the corner (424.26 m); a hole inside the area is over it',
                   'value': [round(a['min_margin_m'], 2), round(c['min_margin_m'], 2), i['status'], round(i['min_distance_m'], 1)],
                   'pass': abs(a['min_margin_m'] - 200.0) < 1e-6 and abs(c['min_margin_m'] - math.hypot(300.0, 300.0)) < 1e-6 and i['status'] == 'OVER THE BOUNDARY' and abs(i['min_distance_m'] + 200.0) < 1e-6})
    # a ring with a hole in it, and an L: inside by even-odd
    L_ = [np.array([[0.0, 0.0], [400.0, 0.0], [400.0, 200.0], [200.0, 200.0], [200.0, 400.0], [0.0, 400.0]])]
    d, _, _, _, ins = boundary_distance(np.array([100.0, 300.0, 300.0, 210.0]), np.array([100.0, 100.0, 300.0, 210.0]), L_)
    donut = [np.array([[-500.0, -500.0], [500.0, -500.0], [500.0, 500.0], [-500.0, 500.0]]), np.array([[-100.0, -100.0], [100.0, -100.0], [100.0, 100.0], [-100.0, 100.0]])]
    d2, _, _, _, ins2 = boundary_distance(np.array([0.0, 300.0]), np.array([0.0, 0.0]), donut)
    checks.append({'check': 'an L-shaped boundary and one with a hole in it: inside and outside told by the rings, the distance to the nearest edge (10 m in the notch of the L)',
                   'value': [ins.tolist(), [round(float(v), 2) for v in d], ins2.tolist(), [round(float(v), 1) for v in d2]],
                   'pass': ins.tolist() == [True, True, False, False] and abs(d[2] - 100.0) < 1e-9 and abs(d[3] - 10.0) < 1e-9 and ins2.tolist() == [False, True] and d2.tolist() == [100.0, 200.0]})
    errs = {}
    for name, fn in (('two', lambda: parse_corners('31.0,-102.0\n31.1,-102.0')), ('words', lambda: parse_corners('north, west\n1,2\n3,4')), ('range', lambda: parse_corners('131,-102\n31,-102\n31,-103'))):
        try:
            fn(); errs[name] = ''
        except HardLineError as e:
            errs[name] = str(e)
    cs = parse_corners('31.0, -102.0; 31.0 -101.9\n31.1,-101.9\n31.0,-102.0')
    checks.append({'check': 'corners typed in: commas, spaces, lines or semicolons; a closing corner dropped; two corners, words and a latitude of 131 each stopped', 'value': [len(cs), sorted(k for k, v in errs.items() if v)],
                   'pass': len(cs) == 3 and all(errs.values())})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
