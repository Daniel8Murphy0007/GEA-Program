"""Operations and seismicity: each well's operations set against the earthquakes around it, in one frame.

An operator who drills, completes or disposes near recorded seismicity is asked three questions: what happened near
the well while it worked, would that have reached a regulator's protocol, and does the earthquake rate around the
well differ while it works from the rest of the record. This module answers each from what the workspace holds and
says what it cannot:

- the operations: each well's declared windows - drilling, completion, disposal, injection or other, with their
  source - and, for a well with a drilling record, the record's own span as a drilling window and the rig's state
  at an event's time (drilling, circulating, not pumping), with the hole's depth then;
- the catalogue: one or more earthquake catalogue exports (USGS ComCat or any FDSN event service text, a TexNet
  CSV), each with the window, the minimum magnitude and the circle it was queried for - the program cannot reach a
  catalogue service itself, so it gives the query URL to run elsewhere;
- the frame: every event within the screening distance of a well (20 km by default, the screening radius Atkinson
  et al. 2016 used), its distance from the well - for a well with a directional survey attached, from the nearest
  point of the surveyed path in plan (the lateral, not the pad), with the measured depth of that point and the
  straight-line distance to the hypocentre beside it; from the surface position for a well without one - and its
  relation to the well's operations:
  DURING one, AFTER one (within the association window that follows - 90 days by default, about the three months
  of the same study), or OUTSIDE;
- the protocol: for an event within the association radius during or after an operation the protocol covers, the
  levels it reaches and their actions, from `reference/seismicity_protocols.json` (British Columbia, Alberta,
  Oklahoma, Ohio, Texas), each with its document;
- the rate comparison: the events at or above the stated completeness within the association radius, counted
  inside the operation windows (with their association windows) and outside them over the catalogue's span. Under
  the hypothesis of one constant rate, the count inside is binomial in the total with the share of time inside;
  the probability of a count at least as large is the exact conditional test for two Poisson rates. The ratio of
  the two rates is given, or, when the background holds no event, its 95 % upper bound (3 events over its time).

What this will not call a measurement: a cause. A coincidence in time and place is what the record shows; the
test treats events as independent (an aftershock sequence counts as many), the catalogue's completeness is
declared, not measured, and a well without a survey is measured from its surface location, not its lateral.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

REF = os.path.join(os.path.dirname(__file__), 'reference')
FIX = os.path.join(os.path.dirname(__file__), 'fixtures')
KINDS = ('drilling', 'completion', 'disposal', 'injection', 'other')
DEFAULTS = {'protocol': 'bc-ksmma-2021', 'radius_km': 5.0, 'screen_km': 20.0, 'post_days': 90.0, 'mc': None}
DEFAULT_SOURCES = {
    'screen': 'Atkinson et al. (2016), Hydraulic fracturing and seismicity in the Western Canada Sedimentary Basin, Seismological Research Letters 87(3): events screened within 20 km of each well, in a window from the start of treatment to three months after',
}
_LOCK = threading.Lock()
_PROT = None


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dt(s) -> Optional[datetime]:
    if s in (None, ''):
        return None
    t = str(s).strip().replace('Z', '').replace(' ', 'T', 1)
    try:
        d = datetime.fromisoformat(t[:19]) if len(t) > 10 else datetime.fromisoformat(t[:10])
    except ValueError:
        return None
    return d.replace(tzinfo=None)


def _when(v, what: str) -> Optional[str]:
    if v in (None, ''):
        return None
    d = _dt(v)
    if d is None:
        raise ValueError(f'{what} is not a date (YYYY-MM-DD or YYYY-MM-DDTHH:MM, UTC): {v!r}')
    return d.isoformat(timespec='minutes') if len(str(v).strip()) > 10 else d.date().isoformat()


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# -- the lateral: the surveyed path in plan, around the well's surface position ------------------------------------
LATERAL_STEP_M = 25.0
_LAT_CACHE: Dict[tuple, dict] = {}


def lateral_of(ws, w: dict, lat: float, lon: float) -> Optional[dict]:
    """The well's surveyed path in a transverse Mercator frame on its own surface position (metres east and north of
    it, depth below sea level when the reference elevation is declared), a point every 25 m of measured depth on the
    minimum-curvature arc; the survey's azimuths turned to that frame's north (true, grid with its convergence, or
    magnetic with the declared declination). None for a well without a survey."""
    if not w.get('trajectory'):
        return None
    from . import well3d as W3
    t = w['trajectory']
    fp = os.path.join(ws.path, 'wells', w['id'], 'trajectory', t.get('file', ''))
    try:
        key = (fp, os.path.getmtime(fp), round(lat, 7), round(lon, 7), json.dumps(t, sort_keys=True, default=str),
               json.dumps((w.get('surface') or {}).get('elevation'), sort_keys=True, default=str))
    except OSError:
        return None
    if key in _LAT_CACHE:
        return _LAT_CACHE[key]
    try:
        tj = W3.trajectory_of(ws, w['id'])
    except (OSError, ValueError, KeyError):
        return None
    st = [x for x in (tj or {}).get('stations') or [] if x.get('incl') is not None and x.get('azi') is not None]
    if len(st) < 2:
        return None
    rot, how = W3._rot_to_local(tj['meta'], lat, lon, lon)
    el = ((w.get('surface') or {}).get('elevation') or {}).get('ft')
    elev_m = float(el) * W3.FT if el not in (None, '') else None
    p = W3.Path([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], 0.0, 0.0, elev_m or 0.0, rot)
    dn = p.dense(LATERAL_STEP_M)
    reach = max(math.hypot(x, y) for x, y in zip(dn['x'], dn['y']))
    if len(_LAT_CACHE) > 64:
        _LAT_CACHE.clear()
    _LAT_CACHE[key] = {'lat0': lat, 'lon0': lon, 'x': dn['x'], 'y': dn['y'], 'z': dn['z'] if elev_m is not None else None, 'md_m': dn['md'],
            'reach_km': round(reach / 1000.0, 3), 'td_md_ft': round(float(p.md[-1]) / W3.FT, 1), 'azimuth': how,
            'source': tj['meta'].get('source') or tj['meta'].get('file') or '', 'n_stations': len(st)}
    return _LAT_CACHE[key]


def _seg_nearest(px, py, x, y):
    """The nearest point of the polyline (x, y) to (px, py): (distance, segment index, fraction along it)."""
    import numpy as np
    ax, ay = np.asarray(x[:-1]), np.asarray(y[:-1])
    dx, dy = np.asarray(x[1:]) - ax, np.asarray(y[1:]) - ay
    L2 = dx * dx + dy * dy
    t = np.where(L2 > 0, ((px - ax) * dx + (py - ay) * dy) / np.where(L2 > 0, L2, 1.0), 0.0).clip(0.0, 1.0)
    d = np.hypot(ax + t * dx - px, ay + t * dy - py)
    i = int(np.argmin(d))
    return float(d[i]), i, float(t[i])


def from_lateral(lat_: dict, lat: float, lon: float, depth_km=None) -> dict:
    """An event's distance from the lateral: in plan to the nearest point of the path, the measured depth there, and
    (with the event's depth and the well's elevation) the straight-line distance to the nearest point of the path."""
    from . import geodesy as GD
    import numpy as np
    fr = GD._tm(lat_['lat0'], lat_['lon0'], 1.0, 0.0, 0.0, 'WGS84')
    q = GD.project(lat, lon, fr)
    px, py = q['easting_m'], q['northing_m']
    x, y, md = lat_['x'], lat_['y'], lat_['md_m']
    if len(x) < 2:
        d = math.hypot(px - x[0], py - y[0]); i, f = 0, 0.0
    else:
        d, i, f = _seg_nearest(px, py, x, y)
    m = md[i] + f * (md[min(i + 1, len(md) - 1)] - md[i])
    out = {'plan_km': d / 1000.0, 'md_ft': round(m / 0.3048, 0)}
    if lat_.get('z') is not None and depth_km is not None:
        z = np.asarray(lat_['z']); X, Y = np.asarray(x), np.asarray(y)
        h = np.sqrt((X - px) ** 2 + (Y - py) ** 2 + (z - float(depth_km) * 1000.0) ** 2)
        out['hypocentral_km'] = round(float(h.min()) / 1000.0, 2)
    return out


# -- the protocols ---------------------------------------------------------------------------------------
def protocols() -> dict:
    global _PROT
    if _PROT is None:
        with open(os.path.join(REF, 'seismicity_protocols.json'), encoding='utf-8') as f:
            _PROT = json.load(f)
    return _PROT


def protocol(pid: str) -> dict:
    for p in protocols()['protocols']:
        if p['id'] == pid:
            return p
    raise KeyError(f"no protocol {pid!r}: one of {', '.join(p['id'] for p in protocols()['protocols'])}")


# -- the workspace's declarations ------------------------------------------------------------------------
def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'seismicity', *p)


def _load(ws, name: str, default):
    p = _dir(ws, name)
    if not os.path.isfile(p):
        return default
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _store(ws, name: str, obj) -> None:
    p = _dir(ws, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(p + '.tmp', p)


def operations(ws, well_id: Optional[str] = None) -> List[dict]:
    ops = _load(ws, 'operations.json', {'operations': []})['operations']
    return [o for o in ops if well_id is None or o['well_id'] == well_id]


def operation_record(ws, well_id: str, kind: str, start, end, source: str, note: str = '', actor: str = 'system', op_id: Optional[str] = None) -> dict:
    """Validate an operation window without writing it (the declarations sheet plans with this)."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    k = (kind or '').strip().lower()
    if k not in KINDS:
        raise ValueError(f'an operation is one of {", ".join(KINDS)}, not {kind!r}')
    a, b = _when(start, 'the start'), _when(end, 'the end')
    if not a:
        raise ValueError('an operation needs its start (UTC)')
    if b and _dt(b) <= _dt(a):
        raise ValueError('the operation ends before it starts')
    if not (source or '').strip():
        raise ValueError('an operation needs its source (the completion report, the injection record, the daily drilling reports)')
    return {'id': op_id or uuid.uuid4().hex[:10], 'well_id': well_id, 'kind': k, 'start': a, 'end': b, 'source': source.strip(),
            'note': (note or '').strip(), 'declared_by': actor, 'declared_utc': _utc()}


def save_operation(ws, actor: str, well_id: str, kind: str, start, end, source: str, note: str = '', op_id: Optional[str] = None) -> dict:
    rec = operation_record(ws, well_id, kind, start, end, source, note, actor, op_id)
    with _LOCK:
        ops = operations(ws)
        if op_id and not any(o['id'] == op_id for o in ops):
            raise KeyError(f'no operation {op_id}')
        old = next((o for o in ops if o['id'] == rec['id']), None)
        ops = [o for o in ops if o['id'] != rec['id']] + [rec]
        ops.sort(key=lambda o: (o['well_id'], o['start']))
        _store(ws, 'operations.json', {'schema': 1, 'operations': ops})
    ws.audit(actor, 'seismicity.operation.' + ('replace' if old else 'declare'), {k: rec[k] for k in ('id', 'well_id', 'kind', 'start', 'end', 'source')})
    return rec


def remove_operation(ws, actor: str, op_id: str) -> dict:
    with _LOCK:
        ops = operations(ws)
        gone = next((o for o in ops if o['id'] == op_id), None)
        if gone is None:
            raise KeyError(f'no operation {op_id}')
        _store(ws, 'operations.json', {'schema': 1, 'operations': [o for o in ops if o['id'] != op_id]})
    ws.audit(actor, 'seismicity.operation.remove', {'id': op_id, 'well_id': gone['well_id']})
    return {'removed': op_id}


def catalogs(ws) -> List[dict]:
    return _load(ws, 'catalogs.json', {'catalogs': []})['catalogs']


def _read_catalog(path: str) -> dict:
    from . import sra as SR
    return SR.read_catalog(path)


def catalog_record(filename: str, content: bytes, start, end, min_magnitude, lat, lon, radius_km, source: str) -> dict:
    """Check a catalogue export and what it was queried for, without keeping it."""
    import tempfile
    a, b = _when(start, 'the catalogue start'), _when(end, 'the catalogue end')
    if not a or not b or _dt(b) <= _dt(a):
        raise ValueError('a catalogue needs the window it was queried for: its start and end')
    try:
        mm = float(min_magnitude)
    except (TypeError, ValueError):
        raise ValueError('a catalogue needs the minimum magnitude it was queried for')
    try:
        la, lo, r = float(lat), float(lon), float(radius_km)
    except (TypeError, ValueError):
        raise ValueError('a catalogue needs the circle it was queried for: centre latitude, longitude and radius (km)')
    if not (-90 <= la <= 90 and -180 <= lo <= 180 and r > 0):
        raise ValueError('the catalogue circle is not a position and a positive radius')
    if not (source or '').strip():
        raise ValueError('a catalogue needs its source (e.g. "USGS ComCat, queried 2026-10-18")')
    fn = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename or '')) or 'catalog.txt'
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, fn)
        with open(p, 'wb') as f:
            f.write(content)
        try:
            cat = _read_catalog(p)
        except Exception as e:
            raise ValueError(f'the file is not a catalogue this program reads (FDSN text or a TexNet CSV): {e}')
    if not cat['n_events'] and cat['n_rows']:
        raise ValueError(f"none of the file's {cat['n_rows']} rows is an event with a time, magnitude and position")
    return {'file': fn, 'sha256': hashlib.sha256(content).hexdigest(), 'start': a, 'end': b, 'min_magnitude': mm,
            'circle': {'lat': la, 'lon': lo, 'radius_km': r}, 'source': source.strip(), 'n_events': cat['n_events'], 'n_skipped': cat['n_skipped']}


def attach_catalog(ws, actor: str, filename: str, content: bytes, start, end, min_magnitude, lat, lon, radius_km, source: str) -> dict:
    rec = catalog_record(filename, content, start, end, min_magnitude, lat, lon, radius_km, source)
    rec.update({'attached_by': actor, 'attached_utc': _utc()})
    with _LOCK:
        os.makedirs(_dir(ws, 'catalogs'), exist_ok=True)
        with open(_dir(ws, 'catalogs', rec['file']), 'wb') as f:
            f.write(content)
        cs = [c for c in catalogs(ws) if c['file'] != rec['file']] + [rec]
        _store(ws, 'catalogs.json', {'schema': 1, 'catalogs': cs})
    ws.audit(actor, 'seismicity.catalog.attach', {k: rec[k] for k in ('file', 'sha256', 'start', 'end', 'min_magnitude', 'n_events', 'source')})
    return rec


def remove_catalog(ws, actor: str, file: str) -> dict:
    with _LOCK:
        cs = catalogs(ws)
        if not any(c['file'] == file for c in cs):
            raise KeyError(f'no catalogue {file}')
        _store(ws, 'catalogs.json', {'schema': 1, 'catalogs': [c for c in cs if c['file'] != file]})
    ws.audit(actor, 'seismicity.catalog.remove', {'file': file})
    return {'removed': file}


def settings(ws) -> dict:
    s = dict(DEFAULTS)
    if ws is not None:
        s.update(_load(ws, 'settings.json', {}))
    return s


def set_settings(ws, actor: str, protocol_id: Optional[str] = None, radius_km=None, screen_km=None, post_days=None, mc=None) -> dict:
    s = settings(ws)
    if protocol_id:
        protocol(protocol_id)
        s['protocol'] = protocol_id
    for k, v, lo in (('radius_km', radius_km, 0.1), ('screen_km', screen_km, 0.1), ('post_days', post_days, 0.0)):
        if v not in (None, ''):
            x = float(v)
            if not math.isfinite(x) or x < lo:
                raise ValueError(f'{k} must be at least {lo}')
            s[k] = x
    if mc not in (None, ''):
        s['mc'] = float(mc)
    if s['screen_km'] < s['radius_km']:
        raise ValueError('the screening distance must be at least the association radius')
    _store(ws, 'settings.json', s)
    ws.audit(actor, 'seismicity.settings', s)
    return s


def query_url(lat: float, lon: float, radius_km: float, start: str, end: str, min_magnitude: float = 1.0) -> str:
    from . import sra as SR
    return SR.comcat_url(lat, lon, radius_km, start, end, min_magnitude)


# -- the frame and the arithmetic ------------------------------------------------------------------------
def binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed exactly in logs."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq for i in range(k, n + 1)]
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))


def _union(spans: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    out: List[Tuple[datetime, datetime]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _days(spans) -> float:
    return sum((b - a).total_seconds() for a, b in spans) / 86400.0


def analyse(scene: dict) -> dict:
    """The frame for a scene: {wells: [{id, display, lat, lon, operations: [{kind, start, end, source}], state_at?}], events,
    catalog: {start, end, min_magnitude, circle}, settings}. Pure: the same scene gives the same answer."""
    st = {**DEFAULTS, **(scene.get('settings') or {})}
    R, SCR, POST = float(st['radius_km']), float(st['screen_km']), float(st['post_days'])
    cat = scene.get('catalog') or {}
    c0, c1 = _dt(cat.get('start')), _dt(cat.get('end'))
    mc = st['mc'] if st.get('mc') is not None else cat.get('min_magnitude')
    prot = protocol(st['protocol']) if st.get('protocol') else None
    events = sorted(scene.get('events') or [], key=lambda e: e['time'])
    wells_out = []
    for w in scene.get('wells') or []:
        row = {'id': w['id'], 'display': w.get('display') or w['id'], 'placed': w.get('lat') is not None, 'operations': [], 'events': [],
               'notes': [], 'position_source': w.get('position_source')}
        if not row['placed']:
            row['notes'].append("no surface position declared: declare it (Setup, or the declarations sheet) to set the well in the frame")
            wells_out.append(row)
            continue
        ops = []
        for o in w.get('operations') or []:
            a = _dt(o['start'])
            b = _dt(o.get('end')) or c1 or datetime.utcnow()
            ops.append({**o, '_a': a, '_b': b, '_post': b + timedelta(days=POST)})
        row['operations'] = [{k: v for k, v in o.items() if not k.startswith('_')} for o in ops]
        # coverage: the association circle inside the catalogue's query circle
        circ = cat.get('circle')
        if circ:
            d0 = km(w['lat'], w['lon'], circ['lat'], circ['lon'])
            row['coverage'] = {'distance_from_centre_km': round(d0, 2), 'inside': d0 + R <= circ['radius_km'] + 1e-9,
                               'screen_inside': d0 + SCR <= circ['radius_km'] + 1e-9}
            if not row['coverage']['inside']:
                row['notes'].append(f"the {R:g} km association circle reaches outside the catalogue's {circ['radius_km']:g} km query circle: events there are not in it")
            elif not row['coverage']['screen_inside']:
                row['notes'].append(f"the {SCR:g} km screening circle reaches outside the catalogue's query circle: the screen is partial there")
        L = w.get('lateral')
        reach = (L or {}).get('reach_km') or 0.0
        if L:
            row['distance_from'] = {'basis': 'lateral', 'detail': f"the nearest point of the surveyed path in plan ({L['n_stations']} stations to {L['td_md_ft']:,.0f} ft MD, {L['azimuth']}; {L['source']})",
                                    'reach_km': L['reach_km']}
        else:
            row['distance_from'] = {'basis': 'surface', 'detail': 'the surface position: no directional survey attached, so a lateral is not known'}
        for e in events:
            d0 = km(w['lat'], w['lon'], e['lat'], e['lon'])
            if d0 > SCR + reach:
                continue
            fl = from_lateral(L, e['lat'], e['lon'], e.get('depth_km')) if L else None
            d = fl['plan_km'] if fl else d0
            if d > SCR:
                continue
            t = _dt(e['time'])
            rel, op, lag = 'OUTSIDE', None, None
            for o in ops:
                if o['_a'] <= t <= o['_b']:
                    rel, op = 'DURING', o
                    break
            if rel == 'OUTSIDE':
                for o in ops:
                    if o['_b'] < t <= o['_post']:
                        rel, op, lag = 'AFTER', o, round((t - o['_b']).total_seconds() / 86400.0, 1)
                        break
            ev = {'event_id': e['event_id'], 'time': e['time'], 'magnitude': e['magnitude'], 'mag_type': e.get('mag_type') or '',
                  'contributor': e.get('contributor') or '', 'lat': e['lat'], 'lon': e['lon'], 'depth_km': e.get('depth_km'),
                  'place': e.get('place') or '', 'distance_km': round(d, 2), 'within_radius': d <= R, 'relation': rel,
                  'distance_basis': 'lateral' if fl else 'surface', 'distance_surface_km': round(d0, 2),
                  'operation': ({'kind': op['kind'], 'start': op['start'], 'end': op.get('end')} if op else None), 'days_after_end': lag,
                  'counted': d <= R and (mc is None or e['magnitude'] >= mc - 1e-9)}
            if fl:
                ev['nearest_md_ft'] = fl['md_ft']
                if 'hypocentral_km' in fl:
                    ev['hypocentral_km'] = fl['hypocentral_km']
            if w.get('state_at') and rel == 'DURING':
                try:
                    ev['rig'] = w['state_at'](t)
                except Exception:
                    ev['rig'] = None
            if prot and ev['within_radius'] and op is not None and op['kind'] in prot['applies_to']:
                pr = prot.get('radius_km')
                inside_p = pr is None or d <= pr
                reached = [lv for lv in prot['levels'] if e['magnitude'] >= lv['m'] - 1e-9]
                plain = [lv for lv in reached if not lv.get('condition')]
                if plain and inside_p:
                    ev['protocol'] = {'id': prot['id'], 'levels': reached, 'highest': plain[-1],
                                      'conditional': [lv for lv in reached if lv.get('condition') and lv['m'] > plain[-1]['m']],
                                      'distance_basis': (f"within the protocol's {pr:g} km" if pr is not None else f"the protocol names no distance; within the {R:g} km association radius")}
            row['events'].append(ev)
        # the rate comparison
        if c0 and c1 and c1 > c0:
            win = _union([(max(o['_a'], c0), min(o['_post'], c1)) for o in ops if o['_a'] < c1 and o['_post'] > c0])
            t_op = _days(win)
            t_all = (c1 - c0).total_seconds() / 86400.0
            t_bg = max(0.0, t_all - t_op)
            counted = [e for e in row['events'] if e['counted'] and c0 <= _dt(e['time']) <= c1]
            n_op = sum(1 for e in counted if e['relation'] in ('DURING', 'AFTER'))
            n_bg = len(counted) - n_op
            share = t_op / t_all if t_all else 0.0
            rate = {'radius_km': R, 'mc': mc, 'n_operations': n_op, 'n_background': n_bg, 'days_operations': round(t_op, 2), 'days_background': round(t_bg, 2),
                    'share_of_time': round(share, 6),
                    'rate_operations_per_year': round(n_op / t_op * 365.25, 4) if t_op else None,
                    'rate_background_per_year': round(n_bg / t_bg * 365.25, 4) if t_bg else None}
            if not ops or t_op <= 0:
                rate['status'] = 'NO OPERATIONS'
                rate['detail'] = 'no operation window falls inside the catalogue\'s span'
            elif t_bg <= 0:
                rate['status'] = 'NO BACKGROUND'
                rate['detail'] = (f"the catalogue covers only the operations ({t_op:,.0f} day(s)), so there is no time outside them to compare with: "
                                  f"attach a longer catalogue (the ComCat query below covers years)")
            elif n_op + n_bg == 0:
                rate['status'] = 'NO EVENTS'
                rate['detail'] = f'no event at or above M {mc:g} within {R:g} km in the catalogue\'s span'
            else:
                p = binom_tail(n_op, n_op + n_bg, share)
                rate['p_value'] = p
                if n_bg:
                    rate['ratio'] = round((n_op / t_op) / (n_bg / t_bg), 3) if t_op and t_bg else None
                else:
                    rate['ratio'] = None
                    rate['background_upper_95_per_year'] = round(3.0 / t_bg * 365.25, 4) if t_bg else None
                rate['status'] = 'HIGHER DURING OPERATIONS' if p < 0.01 and n_op / max(t_op, 1e-9) > n_bg / max(t_bg, 1e-9) else 'NOT DISTINGUISHED'
                rate['detail'] = (f"{n_op} event(s) in {t_op:,.0f} day(s) of operations and their {POST:g}-day association windows against {n_bg} in "
                                  f"{t_bg:,.0f} day(s) outside them; if the rate were one, a count of {n_op} or more inside would come by chance with "
                                  f"probability {p:.2g}")
            row['rate'] = rate
        else:
            row['rate'] = {'status': 'NO CATALOGUE WINDOW', 'detail': 'the catalogue\'s query window is not declared'}
        # the cluster rule of a protocol that has one
        if prot and prot.get('cluster'):
            cl = prot['cluster']
            big = [e for e in row['events'] if e['within_radius'] and e['magnitude'] >= cl['m'] - 1e-9 and e['relation'] in ('DURING', 'AFTER')]
            hits = []
            for i, e in enumerate(big):
                t0 = _dt(e['time'])
                grp = [x for x in big if t0 <= _dt(x['time']) <= t0 + timedelta(hours=cl['hours'])]
                if len(grp) >= cl['n']:
                    hits.append({'from': e['time'], 'events': [x['event_id'] for x in grp]})
                    break
            row['cluster'] = hits
        row['largest'] = max(row['events'], key=lambda e: e['magnitude']) if row['events'] else None
        row['associated'] = [e for e in row['events'] if e['within_radius'] and e['relation'] in ('DURING', 'AFTER')]
        row['protocol_events'] = [e for e in row['events'] if e.get('protocol')]
        wells_out.append(row)
    # an event during or after more than one well's operation is named on each, with the others beside it
    by_ev: Dict[str, List[dict]] = {}
    for w in wells_out:
        for e in w.get('events', []):
            if e['within_radius'] and e['relation'] in ('DURING', 'AFTER'):
                by_ev.setdefault(e['event_id'], []).append({'well': w['display'], 'relation': e['relation'], 'kind': e['operation']['kind'], 'distance_km': e['distance_km']})
    for w in wells_out:
        for e in w.get('events', []):
            e['also'] = [x for x in by_ev.get(e['event_id'], []) if x['well'] != w['display']]
    return {'title': scene.get('title') or '', 'settings': {**st, 'mc': mc}, 'protocol': prot, 'catalog': {k: v for k, v in cat.items() if k != 'path'},
            'wells': wells_out, 'n_events': len(events), 'notes': scene.get('notes') or [],
            'sources': {'screen': DEFAULT_SOURCES['screen']},
            'not_a_measurement': ['a cause: a coincidence in time and place is what the record shows, not that an operation caused an earthquake',
                                  "the catalogue's completeness: the minimum magnitude is the query's (or the site's declared value), not measured, and a sparse network misses small events",
                                  'independence: the test counts every event, so an aftershock sequence weighs as many events',
                                  ("the distance: from the nearest point of each surveyed path in plan; the path is as surveyed, so it is as good as the survey and its azimuth reference"
                                   if all((w.get('distance_from') or {}).get('basis') == 'lateral' for w in wells_out if w.get('placed'))
                                   and any(w.get('placed') for w in wells_out) else
                                   "the distance: from the surveyed path in plan for a well with a survey attached, from the surface position for one without; a lateral's toe can be kilometres from its pad"),
                                  "a protocol's verdict: the levels an event reaches by the catalogue's magnitude, which may differ from the regulator's magnitude of record"]}


# -- the scenes ------------------------------------------------------------------------------------------
def example_scene(settings_over: Optional[dict] = None) -> dict:
    from . import sra as SR
    with open(os.path.join(REF, 'mahoning_example.json'), encoding='utf-8') as f:
        ex = json.load(f)
    cat = SR.read_catalog(os.path.join(FIX, ex['catalog']['file']))
    return {'id': 'mahoning', 'title': ex['title'], 'how': ex['how'], 'notes': ex['notes'],
            'wells': [{**{k: w[k] for k in ('id', 'display', 'lat', 'lon', 'position_source', 'api_number', 'operator', 'detail')},
                       'operations': w['operations']} for w in ex['wells']],
            'events': cat['events'], 'catalog': {**ex['catalog'], 'n_events': cat['n_events']},
            'settings': {**DEFAULTS, 'protocol': ex.get('protocol', 'ohio-odnr-2014'), **(settings_over or {})}}


def _edr_state(ws, wid):
    """The rig's state at a time from the drilling record, and the record's span - or None for a well without one."""
    try:
        from . import drill_string as DS
        import numpy as np
        c = DS._cache(ws, wid, build=True)
    except Exception:
        return None
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    idx = c.index
    if not len(idx):
        return None
    drl = np.asarray(c.extra('drilling'), dtype=bool)
    fl = DS._ch(c, 'Total Pump Output')
    bottom = c.extra('bottom')

    def at(t: datetime):
        s = (t - t0).total_seconds()
        i = int(np.searchsorted(idx, s))
        i = min(max(i, 0), len(idx) - 1)
        if abs(idx[i] - s) > 120:
            return {'state': 'outside the record', 'bottom_ft': None}
        state = 'drilling' if drl[i] else ('circulating' if fl is not None and np.nan_to_num(fl[i]) > DS.FLOW_FLOOR_GPM else 'not pumping')
        return {'state': state, 'bottom_ft': round(float(bottom[i]), 1) if bottom is not None and bottom[i] == bottom[i] else None}
    return {'start': t0, 'end': t0 + timedelta(seconds=float(idx[-1])), 'at': at}


def workspace_scene(ws) -> dict:
    from . import geodesy as GD
    cs = catalogs(ws)
    events, seen = [], set()
    for c in cs:
        try:
            cat = _read_catalog(_dir(ws, 'catalogs', c['file']))
        except Exception:
            continue
        for e in cat['events']:
            if e['event_id'] not in seen:
                seen.add(e['event_id']); events.append(e)
    # the union of the catalogues' windows and their smallest minimum magnitude; the circle of the first (each is listed)
    cat = None
    if cs:
        cat = {'start': min(c['start'] for c in cs), 'end': max(c['end'] for c in cs), 'min_magnitude': max(c['min_magnitude'] for c in cs),
               'circle': cs[0]['circle'] if len(cs) == 1 else None, 'files': [c['file'] for c in cs], 'source': '; '.join(c['source'] for c in cs)}
    wells = []
    for w in ws.wells():
        s = w.get('surface') or {}
        row = {'id': w['id'], 'display': w.get('display') or w['id'], 'lat': None, 'lon': None, 'operations': []}
        if s.get('lat') is not None:
            p = GD.to_wgs84(s['lat'], s['lon'], 0.0, s.get('datum') or 'WGS84')
            row.update({'lat': p['lat'], 'lon': p['lon'], 'position_source': f"declared, {s.get('datum')}"})
            row['lateral'] = lateral_of(ws, w, p['lat'], p['lon'])
        row['operations'] = [{'kind': o['kind'], 'start': o['start'], 'end': o['end'], 'source': o['source'], 'id': o['id']} for o in operations(ws, w['id'])]
        e = _edr_state(ws, w['id'])
        if e:
            row['operations'].append({'kind': 'drilling', 'start': e['start'].isoformat(timespec='minutes'), 'end': e['end'].isoformat(timespec='minutes'),
                                      'source': 'the drilling record\'s own span', 'id': None})
            row['state_at'] = e['at']
        wells.append(row)
    return {'id': 'workspace', 'title': ws.manifest.get('name') or 'this workspace', 'wells': wells, 'events': events, 'catalog': cat or {},
            'settings': settings(ws)}


def scenes(ws) -> List[dict]:
    out = [{'id': 'mahoning', 'title': 'Worked example: the Mahoning Valley, Ohio (public record)'}]
    if ws is not None:
        out.insert(0, {'id': 'workspace', 'title': (ws.manifest.get('name') or 'This workspace') + ' - your wells'})
    return out


def page(ws, scene_id: str = 'workspace', settings_over: Optional[dict] = None) -> dict:
    if scene_id == 'mahoning' or ws is None:
        sc = example_scene(settings_over)
    elif scene_id == 'workspace':
        sc = workspace_scene(ws)
        if settings_over:
            sc['settings'] = {**sc['settings'], **settings_over}
    else:
        raise KeyError(f'no scene {scene_id!r}')
    res = analyse(sc)
    res['scene'] = sc['id']
    res['scenes'] = scenes(ws)
    res['how'] = sc.get('how') or ''
    res['protocols'] = protocols()['protocols']
    res['protocols_how'] = protocols()['how']
    if sc['id'] == 'workspace':
        res['catalogs'] = catalogs(ws)
        res['operations_declared'] = operations(ws)
        placed = [w for w in sc['wells'] if w['lat'] is not None]
        if placed:
            la = sum(w['lat'] for w in placed) / len(placed)
            lo = sum(w['lon'] for w in placed) / len(placed)
            spread = max(km(la, lo, w['lat'], w['lon']) + ((w.get('lateral') or {}).get('reach_km') or 0.0) for w in placed)
            today = datetime.utcnow().date().isoformat()
            starts = [o['start'] for w in sc['wells'] for o in w['operations']]
            st0 = (min(starts)[:4] + '-01-01') if starts else '2000-01-01'
            res['query'] = {'url': query_url(la, lo, round(spread + sc['settings']['screen_km'], 1), '1990-01-01', today, 1.0),
                            'centre': [round(la, 4), round(lo, 4)], 'radius_km': round(spread + sc['settings']['screen_km'], 1),
                            'start': '1990-01-01', 'end': today, 'min_magnitude': 1.0, 'operations_from': st0}
    for w in res['wells']:
        w.pop('state_at', None)
        w.pop('lateral', None)
    if sc['id'] == 'workspace':
        from . import seismic_watch as SW
        res['watch'] = SW.status(ws)
    return res


# -- the words -------------------------------------------------------------------------------------------
def text(res: dict) -> str:
    st = res['settings']
    L = [res['title'] or 'Operations and seismicity',
         f"association within {st['radius_km']:g} km and {st['post_days']:g} days after an operation; screened to {st['screen_km']:g} km; "
         f"completeness M {st['mc'] if st['mc'] is not None else '?'}; protocol: {res['protocol']['name'] if res['protocol'] else 'none'}"]
    c = res.get('catalog') or {}
    if c:
        L.append(f"catalogue: {c.get('source', '')} ({c.get('start')} to {c.get('end')}, M >= {c.get('min_magnitude')}); {res['n_events']} event(s)")
    for w in res['wells']:
        L.append('')
        L.append(f"{w['display']}")
        if not w['placed']:
            L += ['  ' + n for n in w['notes']]
            continue
        if w.get('distance_from'):
            L.append(f"  distances from {w['distance_from']['detail']}")
        for o in w['operations']:
            L.append(f"  {o['kind']:<10} {o['start']} to {o.get('end') or 'open'} - {o['source']}")
        r = w.get('rate') or {}
        L.append(f"  rate: {r.get('status')}: {r.get('detail', '')}")
        if r.get('ratio') is not None:
            L.append(f"        ratio {r['ratio']:g} ({r['rate_operations_per_year']:g} against {r['rate_background_per_year']:g} per year)")
        elif r.get('background_upper_95_per_year') is not None:
            L.append(f"        no background event: {r['rate_operations_per_year']:g} per year against below {r['background_upper_95_per_year']:g} per year (95 %)")
        for e in w['events']:
            if not e['within_radius'] and e['relation'] == 'OUTSIDE':
                continue
            pr = e.get('protocol')
            L.append(f"  {e['time'][:16]}  M {e['magnitude']:.2f} {e['mag_type']:<3} {e['distance_km']:5.2f} km  {e['relation']:<7}"
                     + (f" {e['operation']['kind']}" if e['operation'] else '')
                     + (f"  (lateral, nearest at {e['nearest_md_ft']:,.0f} ft MD)" if e.get('distance_basis') == 'lateral' else '')
                     + (f" +{e['days_after_end']:g} d" if e['days_after_end'] is not None else '')
                     + (f"  rig: {e['rig']['state']}" + (f" at {e['rig']['bottom_ft']:g} ft" if e['rig'].get('bottom_ft') else '') if e.get('rig') else '')
                     + ('' if e['within_radius'] else f"  (beyond {st['radius_km']:g} km)")
                     + ''.join(f"  [also {x['relation'].lower()} {x['kind']} of {x['well']}, {x['distance_km']:g} km]" for x in e.get('also', []))
                     + (f"  -> M {pr['highest']['m']:g}: {pr['highest']['action']}" if pr else '')
                     + ''.join(f"; {c['condition']}, M {c['m']:g}: {c['action']}" for c in (pr or {}).get('conditional', [])))
        for cl in w.get('cluster') or []:
            L.append(f"  cluster from {cl['from']}: {len(cl['events'])} events - {res['protocol']['cluster']['action']}")
        L += ['  note: ' + n for n in w['notes']]
    if res.get('notes'):
        L.append('')
        L += ['note: ' + n for n in res['notes']]
    return '\n'.join(L)


def synthetic_scene(seed: int = 11) -> dict:
    """A labelled scene for the gate: a background of one event in two years around a pad for twenty years, and a
    completion that brings twelve events within 2 km over its ten days, the largest M 3.1 on its sixth day."""
    import random
    rng = random.Random(seed)
    lat0, lon0 = 32.0, -102.0
    c0, c1 = datetime(2006, 1, 1), datetime(2026, 1, 1)
    ev = []
    for i in range(10):                               # background: ten events, 20 years, spread over 15 km
        t = c0 + timedelta(days=rng.uniform(0, (c1 - c0).days))
        r, a = rng.uniform(0.5, 15.0), rng.uniform(0, 2 * math.pi)
        ev.append({'event_id': f'bg{i}', 'time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'magnitude': round(rng.uniform(1.2, 2.2), 1),
                   'lat': lat0 + r * math.cos(a) / 111.2, 'lon': lon0 + r * math.sin(a) / (111.2 * math.cos(math.radians(lat0))), 'depth_km': 5.0})
    fs = datetime(2024, 5, 1)
    mags = [1.6, 1.8, 2.0, 1.7, 2.3, 3.1, 2.6, 2.7, 2.5, 2.6, 2.8, 1.9]
    for i, m in enumerate(mags):
        t = fs + timedelta(days=1 + i * 0.75, hours=3)
        r, a = 0.4 + 0.12 * i, 0.3 * i
        ev.append({'event_id': f'op{i}', 'time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'magnitude': m,
                   'lat': lat0 + r * math.cos(a) / 111.2, 'lon': lon0 + r * math.sin(a) / (111.2 * math.cos(math.radians(lat0))), 'depth_km': 3.2})
    ev.append({'event_id': 'far', 'time': '2024-05-04T00:00:00Z', 'magnitude': 3.6, 'lat': lat0 + 30 / 111.2, 'lon': lon0, 'depth_km': 6.0})
    return {'id': 'synthetic', 'title': 'SYNTHETIC pad', 'wells': [
        {'id': 'pad-1', 'display': 'SYNTHETIC pad 1', 'lat': lat0, 'lon': lon0, 'operations': [
            {'kind': 'drilling', 'start': '2024-03-01', 'end': '2024-04-10', 'source': 'synthetic'},
            {'kind': 'completion', 'start': '2024-05-01', 'end': '2024-05-11', 'source': 'synthetic'}]},
        {'id': 'unplaced', 'display': 'SYNTHETIC unplaced', 'lat': None, 'lon': None, 'operations': []}],
        'events': ev, 'catalog': {'start': '2006-01-01', 'end': '2026-01-01', 'min_magnitude': 1.0, 'circle': {'lat': lat0, 'lon': lon0, 'radius_km': 25.0}},
        'settings': {**DEFAULTS, 'protocol': 'bc-ksmma-2021'}}
