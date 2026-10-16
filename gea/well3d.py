"""The well in 3D: directional surveys made into well paths, set in one frame with the mapped formations, and
the drill string replayed from the record.

What is drawn, and from what:

- a well path from its directional survey (measured depth, inclination, azimuth at each station) by the
  minimum-curvature method; between stations the path follows the same arc, never a straight chord. The
  survey's own azimuth reference is applied (grid, true, or magnetic with its declination); a correction a
  scene makes to a file is recorded beside the well with the evidence for it;
- depth below sea level from the well's declared reference elevation; without one a path is not set among
  the formations, because it has nothing to stand on;
- the formation surfaces as they were delivered: grid points of an interpreted horizon (never re-gridded or
  smoothed) or the contour lines of a formation-top map at their elevations;
- the formation picks at their measured depths, and how far each surface sits from each pick;
- the replay: the bit's measured depth against time - from a drilling recorder's bit depth (every sample,
  by stride, never averaged), or from the time stamps of the survey stations measured while drilling (a
  station copied in from an earlier section carries the time it was loaded, not measured, and is not used);
- colours along the path from what was measured there: inclination and dogleg severity at the stations, or a
  drilling recorder's channels binned by bit depth over the drilling samples.

What it will not draw: a well without a survey (its picks may be shown, as points); a fault trace with no
depth; an isopach as a surface (a thickness is not a position); a rock or fluid property between wells.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference')
FT = 0.3048
MEASURED_TYPES_EXCLUDED = ('unknown', 'tie in point', '')
DLS_PER = 30.0                         # dogleg severity per 30 m (the metric convention; 100 ft is 30.48 m)


# -- reading surveys -------------------------------------------------------------------------------
def _angle(v: float, uom: str) -> float:
    u = (uom or 'dega').lower()
    if u in ('rad', 'radian', 'radians'):
        return math.degrees(v)
    if u in ('gon', 'grad'):
        return v * 0.9
    return v


def _len(v: float, uom: str) -> float:
    u = (uom or 'm').lower()
    return v * FT if u in ('ft', 'feet', 'foot', 'usft', 'ft[us]') else v


def read_witsml_trajectory(text: str) -> List[dict]:
    """WITSML 1.3/1.4 trajectory objects: the header and every station, angles in degrees and lengths in metres
    whatever units the file used. Nothing is computed here; the file's own TVD and displacements are kept."""
    out = []
    for t in re.findall(r'<trajectory\b.*?</trajectory>', text, re.S):
        def g(p, s=t):
            m = re.search(p, s, re.S)
            return m.group(1).strip() if m else None
        hdr = re.sub(r'<trajectoryStation\b.*', '', t, flags=re.S)
        rec = {'well': g(r'<nameWell>(.*?)</nameWell>'), 'wellbore': g(r'<nameWellbore>(.*?)</nameWellbore>'),
               'name': (g(r'<name>(.*?)</name>', hdr) or '').replace('&amp;', '&'), 'azi_ref': (g(r'<aziRef>(.*?)</aziRef>', hdr) or '').lower(),
               'service_company': g(r'<serviceCompany>(.*?)</serviceCompany>', hdr), 'start': g(r'<dTimTrajStart>(.*?)</dTimTrajStart>', hdr),
               'end': g(r'<dTimTrajEnd>(.*?)</dTimTrajEnd>', hdr), 'grid_cor_deg': None, 'mag_decl_deg': None, 'stations': []}
        for k, tag in (('grid_cor_deg', 'gridCorUsed'), ('mag_decl_deg', 'magDeclUsed')):
            m = re.search(rf'<{tag} uom="([^"]*)">([-\d.Ee+]+)<', hdr)
            if m:
                rec[k] = _angle(float(m.group(2)), m.group(1))
        for st in re.findall(r'<trajectoryStation\b.*?</trajectoryStation>', t, re.S):
            def num(tag, kind):
                m = re.search(rf'<{tag} uom="([^"]*)">([-\d.Ee+]+)<', st)
                if not m:
                    return None
                v = float(m.group(2))
                return _angle(v, m.group(1)) if kind == 'a' else _len(v, m.group(1))
            md = num('md', 'l')
            if md is None:
                continue
            rec['stations'].append({'md': md, 'incl': num('incl', 'a'), 'azi': num('azi', 'a'), 'tvd': num('tvd', 'l'),
                                    'dispNs': num('dispNs', 'l'), 'dispEw': num('dispEw', 'l'),
                                    'time': g(r'<dTimStn>(.*?)</dTimStn>', st), 'type': (g(r'<typeTrajStation>(.*?)</typeTrajStation>', st) or '')})
        rec['stations'].sort(key=lambda s: s['md'])
        out.append(rec)
    return out


SURVEY_COLUMNS = {'md': ('md', 'measured depth', 'depth', 'mdepth', 'm.d.'), 'incl': ('inc', 'incl', 'inclination', 'angle'),
                  'azi': ('azi', 'azim', 'azimuth', 'az', 'direction', 'dir')}


def read_survey_csv(text: str, md_unit: str = 'ft') -> List[dict]:
    """A survey table (CSV or whitespace columns) with measured depth, inclination and azimuth columns, found by their
    header names; inclination and azimuth in degrees. Every row must carry all three, or the table is refused."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith('#')]
    if not lines:
        raise ValueError('the survey file is empty')
    dialect = ',' if ',' in lines[0] else ('\t' if '\t' in lines[0] else None)
    rows = [next(csv.reader([ln], delimiter=dialect)) if dialect else ln.split() for ln in lines]
    hdr = [h.strip().lower().strip('"') for h in rows[0]]

    def col(k):
        for i, h in enumerate(hdr):
            base = re.sub(r'[\(\[].*', '', h).strip()
            if base in SURVEY_COLUMNS[k] or any(base.startswith(w + ' ') for w in SURVEY_COLUMNS[k]):
                return i
        return None
    ci = {k: col(k) for k in SURVEY_COLUMNS}
    missing = [k for k, v in ci.items() if v is None]
    if missing:
        raise ValueError(f"the survey header names no {', '.join(missing)} column (found: {', '.join(hdr)}); expected MD, Inc, Azi")
    st = []
    for n, r in enumerate(rows[1:], start=2):
        try:
            md, inc, az = (float(r[ci['md']]), float(r[ci['incl']]), float(r[ci['azi']]))
        except (ValueError, IndexError):
            raise ValueError(f'survey row {n} is not three numbers: {r}')
        if not (0 <= inc <= 180 and -360 <= az <= 360):
            raise ValueError(f'survey row {n}: inclination {inc} or azimuth {az} is out of range')
        st.append({'md': _len(md, md_unit), 'incl': inc, 'azi': az % 360.0, 'tvd': None, 'dispNs': None, 'dispEw': None, 'time': None, 'type': 'table'})
    st.sort(key=lambda s: s['md'])
    if len(st) < 2:
        raise ValueError('a survey needs at least two stations')
    if any(b['md'] <= a['md'] for a, b in zip(st, st[1:])):
        raise ValueError('the survey repeats a measured depth')
    return st


# -- the minimum-curvature method ------------------------------------------------------------------
def _tangent(inc_deg, azi_deg):
    i, a = np.radians(inc_deg), np.radians(azi_deg)
    return np.stack([np.sin(i) * np.cos(a), np.sin(i) * np.sin(a), np.cos(i)], axis=-1)      # north, east, down


def min_curvature(md: Sequence[float], inc: Sequence[float], azi: Sequence[float]) -> Dict[str, np.ndarray]:
    """North, east and vertical depth below the first station, and the dogleg severity, by the minimum-curvature method."""
    md, inc, azi = (np.asarray(x, dtype=np.float64) for x in (md, inc, azi))
    t = _tangent(inc, azi)
    dm = np.diff(md)
    cosb = np.clip(np.sum(t[:-1] * t[1:], axis=1), -1.0, 1.0)
    b = np.arccos(cosb)
    rf = np.where(b > 1e-9, 2.0 / np.where(b > 1e-9, b, 1.0) * np.tan(b / 2.0), 1.0)
    step = (dm * rf / 2.0)[:, None] * (t[:-1] + t[1:])
    p = np.vstack([np.zeros(3), np.cumsum(step, axis=0)])
    dls = np.concatenate([[0.0], np.where(dm > 0, np.degrees(b) / np.where(dm > 0, dm, 1.0) * DLS_PER, 0.0)])
    return {'n': p[:, 0], 'e': p[:, 1], 'v': p[:, 2], 'dls': dls}


class Path:
    """A well path: stations with positions in a projected frame (east, north) and depth below sea level, metres."""

    def __init__(self, md, inc, azi, x0: float, y0: float, ref_elev_m: float, azi_rot_deg: float = 0.0):
        md, inc, azi = (list(map(float, v)) for v in (md, inc, azi))
        self.tied_at_surface = md[0] > 0
        if md[0] > 0:
            # a table that starts below the reference: the hole above its first station is taken as vertical, a tie-in at
            # the surface - the survey's own implication, recorded on the path
            md, inc, azi = [0.0] + md, [0.0] + inc, [azi[0]] + azi
        self.md = np.asarray(md, dtype=np.float64)
        self.inc = np.asarray(inc, dtype=np.float64)
        self.azi = (np.asarray(azi, dtype=np.float64) + azi_rot_deg) % 360.0
        mc = min_curvature(self.md, self.inc, self.azi)
        self.x = x0 + mc['e']; self.y = y0 + mc['n']
        self.tvd = mc['v']
        self.z = self.tvd - ref_elev_m                 # below mean sea level, positive down
        self.dls = mc['dls']
        self.ref_elev_m = ref_elev_m

    def at(self, md: float) -> Tuple[float, float, float]:
        """The position at a measured depth on the arc between the stations around it (not a chord)."""
        md = float(md)
        if md <= self.md[0]:
            return float(self.x[0]), float(self.y[0]), float(self.z[0])
        if md >= self.md[-1]:
            return float(self.x[-1]), float(self.y[-1]), float(self.z[-1])
        k = int(np.searchsorted(self.md, md, 'right')) - 1
        t1, t2 = _tangent(self.inc[k], self.azi[k]), _tangent(self.inc[k + 1], self.azi[k + 1])
        b = math.acos(max(-1.0, min(1.0, float(np.dot(t1, t2)))))
        f = (md - self.md[k]) / (self.md[k + 1] - self.md[k])
        if b < 1e-9:
            t = t1
        else:
            t = (math.sin((1 - f) * b) * t1 + math.sin(f * b) * t2) / math.sin(b)
        bf = f * b
        rf = 2.0 / bf * math.tan(bf / 2.0) if bf > 1e-9 else 1.0
        d = (md - self.md[k]) * rf / 2.0 * (t1 + t)
        return float(self.x[k] + d[1]), float(self.y[k] + d[0]), float(self.z[k] + d[2])

    def dense(self, step_m: float = 10.0) -> Dict[str, list]:
        """The path for drawing: every station and a point every `step_m` of measured depth between them, on the arc."""
        mds = sorted(set(np.round(np.concatenate([self.md, np.arange(self.md[0], self.md[-1], step_m)]), 3).tolist()))
        pts = [self.at(m) for m in mds]
        return {'md': [round(m, 2) for m in mds], 'x': [round(p[0], 2) for p in pts], 'y': [round(p[1], 2) for p in pts],
                'z': [round(p[2], 2) for p in pts]}


def file_position_miss(stations: List[dict]) -> Optional[float]:
    """The largest difference, metres, between the minimum-curvature positions computed here from a file's measured depths,
    inclinations and azimuths, and the positions the file itself carries (its TVD and displacements, against its first
    station) - the method checked against the service company's own arithmetic. None when the file carries none."""
    st = [s for s in stations if s['tvd'] is not None and s['dispNs'] is not None and s['dispEw'] is not None]
    if len(st) < 2 or len(st) != len(stations):
        return None
    raw = min_curvature([s['md'] for s in st], [s['incl'] for s in st], [s['azi'] for s in st])
    d = np.maximum.reduce([np.abs(raw['v'] + st[0]['tvd'] - np.array([s['tvd'] for s in st])),
                           np.abs(raw['n'] + st[0]['dispNs'] - np.array([s['dispNs'] for s in st])),
                           np.abs(raw['e'] + st[0]['dispEw'] - np.array([s['dispEw'] for s in st]))])
    return round(float(d.max()), 4)


def measured_times(trajs: List[dict]) -> List[Tuple[str, float]]:
    """(time, measured depth) of the stations measured while drilling - each station's own time stamp. A station whose
    type is unknown or a tie-in carries the time it was loaded into the file, not measured, and is left out."""
    seen = {}
    for tr in trajs:
        for s in tr['stations']:
            if s.get('time') and (s.get('type') or '').strip().lower() not in MEASURED_TYPES_EXCLUDED:
                seen[round(s['md'], 2)] = s['time']
    out = sorted(((t, md) for md, t in seen.items()), key=lambda r: (r[0], r[1]))
    return out


def replay_from_stations(trajs: List[dict]) -> Tuple[Optional[dict], str]:
    """The drilling progress the survey stations' own times describe, or why there is none: fewer than three distinct
    times (a resurvey run in one go stamps every station alike) is not a record of drilling."""
    times = measured_times(trajs)
    distinct = len({t for t, _ in times})
    if distinct < 3:
        return None, (f'no replay: the stations measured while drilling carry {distinct} distinct time(s) - a survey run in one go '
                      f'(a resurvey) stamps every station alike, which is not a record of the hole being drilled')
    return ({'kind': 'survey stations', 'how': "each station's own time stamp, measured while drilling (stations copied in from an "
                                               "earlier section carry the time they were loaded and are left out)",
             't': [t for t, _ in times], 'md': [round(m, 2) for _, m in times]}, '')


# -- surfaces --------------------------------------------------------------------------------------
def surface_grid(s: dict, origin: Tuple[float, float]) -> dict:
    """An interpreted horizon delivered as inline/crossline points, sent as the grid it is: the inline and crossline
    numbers, the affine map from them to easting and northing (fitted to the points and its worst miss reported),
    and depth at each node, empty where the horizon was not picked."""
    il, xl = np.asarray(s['il'], dtype=np.int64), np.asarray(s['xl'], dtype=np.int64)
    x, y, z = (np.asarray(s[k], dtype=np.float64) for k in ('x', 'y', 'z'))
    A = np.column_stack([np.ones(len(il)), il, xl])
    cx, *_ = np.linalg.lstsq(A, x, rcond=None)
    cy, *_ = np.linalg.lstsq(A, y, rcond=None)
    miss = float(max(np.abs(A @ cx - x).max(), np.abs(A @ cy - y).max()))
    st = int(s.get('step') or 1)
    i0, j0 = int(il.min()), int(xl.min())
    ni, nj = int((il.max() - i0) // st) + 1, int((xl.max() - j0) // st) + 1
    grid = [None] * (ni * nj)
    for a, b, v in zip(((il - i0) // st).tolist(), ((xl - j0) // st).tolist(), z.tolist()):
        grid[a * nj + b] = round(v, 2)
    return {'id': s['id'], 'name': s['name'], 'pick': s.get('pick'), 'ni': ni, 'nj': nj, 'il0': i0, 'xl0': j0, 'step': st,
            'affine_x': [round(cx[0] + cx[1] * i0 + cx[2] * j0 - origin[0], 4), round(cx[1] * st, 6), round(cx[2] * st, 6)],
            'affine_y': [round(cy[0] + cy[1] * i0 + cy[2] * j0 - origin[1], 4), round(cy[1] * st, 6), round(cy[2] * st, 6)],
            'affine_miss_m': round(miss, 3), 'z': grid, 'n_points': int(len(il)), 'n_source_points': s.get('n_source_points'),
            'z_range': [round(float(z.min()), 1), round(float(z.max()), 1)]}


def surface_at(s: dict, e: float, n: float) -> Optional[float]:
    """The horizon's depth at the grid node nearest a position, when one lies within a bin's diagonal; else None."""
    x, y, z = (np.asarray(s[k], dtype=np.float64) for k in ('x', 'y', 'z'))
    d = np.hypot(x - e, y - n)
    i = int(d.argmin())
    return float(z[i]) if d[i] <= 12.5 * (s.get('step') or 1) * math.sqrt(2) / 2 + 1e-6 else None


# -- the shipped Volve excerpt ---------------------------------------------------------------------
VOLVE_WELLS = [
    {'id': 'F-4', 'picks': 'NO 15/9-F-4', 'files': ['volve_traj_F-4.xml.gz'], 'path_file': 'volve_traj_F-4.xml.gz',
     'azimuth': 'as grid', 'why': "the file says true north, but the field's own picks on this well (Equinor's, with eastings and northings) "
                                  "agree with its stations to 0.1 m read as grid azimuths and miss by up to 14 m with the 0.95 degree grid "
                                  "convergence applied; the stations are read as grid azimuths and this is recorded"},
    {'id': 'F-5', 'picks': 'NO 15/9-F-5', 'files': ['volve_traj_F-5_36in.xml.gz', 'volve_traj_F-5_17.5in.xml.gz', 'volve_traj_F-5_12.25in.xml.gz',
                                                    'volve_traj_F-5_8.5in.xml.gz'], 'path_file': 'volve_traj_F-5_8.5in.xml.gz', 'azimuth': 'declared'},
    {'id': 'F-10', 'picks': 'NO 15/9-F-10', 'files': ['volve_traj_F-10.xml.gz'], 'path_file': 'volve_traj_F-10.xml.gz', 'azimuth': 'declared'},
]


def _ref(name: str) -> bytes:
    with open(os.path.join(REF_DIR, name), 'rb') as f:
        b = f.read()
    return gzip.decompress(b) if name.endswith('.gz') else b


def volve_picks() -> List[dict]:
    return list(csv.DictReader(io.StringIO(_ref('volve_well_picks.csv').decode('utf-8'))))


def volve_surfaces() -> dict:
    return json.loads(_ref('volve_surfaces.json.gz').decode('utf-8'))


def _f(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, '') else None
    except ValueError:
        return None


_VOLVE: Dict[str, dict] = {}


def volve_scene() -> dict:
    """The shipped Volve excerpt as one scene, built once per process (the files ship with the program and do not change)."""
    if 'scene' not in _VOLVE:
        _VOLVE['scene'] = _volve_scene()
    return _VOLVE['scene']


def _volve_scene() -> dict:
    """The shipped Volve excerpt as one scene (UTM zone 31N on ED50, as delivered; depths below mean sea level)."""
    picks = volve_picks()
    by_well: Dict[str, List[dict]] = {}
    for p in picks:
        by_well.setdefault(p['WELL'], []).append(p)
    surf = volve_surfaces()
    wells, slots = [], []
    for spec in VOLVE_WELLS:
        pk = by_well[spec['picks']]
        sb = next(p for p in pk if p['PICKS'] == 'Seabed')
        x0, y0 = float(sb['EASTING']), float(sb['NORTHING'])
        # the reference elevation from the picks themselves: TVD below the rig floor minus depth below sea level
        kb = float(np.median([float(p['TVD']) + float(p['TVDSS']) for p in pk if _f(p['TVD']) is not None and _f(p['TVDSS']) is not None]))
        trajs = [t for fn in spec['files'] for t in read_witsml_trajectory(_ref(fn).decode('utf-8'))]
        main = read_witsml_trajectory(_ref(spec['path_file']).decode('utf-8'))[0]
        st = [s for s in main['stations'] if s['incl'] is not None and s['azi'] is not None]
        rot = 0.0
        if spec['azimuth'] == 'declared' and main['azi_ref'].startswith('true'):
            rot = -(main.get('grid_cor_deg') or 0.0)
        p = Path([s['md'] for s in st], [s['incl'] for s in st], [s['azi'] for s in st], x0, y0, kb, rot)
        wells.append({'spec': spec, 'path': p, 'stations': st, 'main': main, 'trajs': trajs, 'picks': pk, 'kb': kb})
        slots.append((x0, y0))
    origin = (round(float(np.mean([s[0] for s in slots])), 1), round(float(np.mean([s[1] for s in slots])), 1))
    s_by_pick = {s['pick']: s for s in surf['surfaces']}
    out_wells = []
    for w in wells:
        p, spec = w['path'], w['spec']
        # the file's own positions against the minimum-curvature path (the method checked against the service company's)
        own_miss = file_position_miss(w['stations'])
        pk_out, miss = [], []
        for q in w['picks']:
            md = _f(q['DEPTH'])
            if md is None or md > p.md[-1] + 1e-6:
                continue
            x, y, z = p.at(md)
            item = {'name': q['PICKS'], 'md': md, 'x': round(x - origin[0], 2), 'y': round(y - origin[1], 2), 'z': round(z, 2),
                    'tvdss_pick': -_f(q['TVDSS']) if _f(q['TVDSS']) is not None else None}
            if _f(q['EASTING']) is not None:
                item['miss_m'] = round(math.sqrt((x - float(q['EASTING'])) ** 2 + (y - float(q['NORTHING'])) ** 2 + (z + float(q['TVDSS'])) ** 2), 2)
                miss.append(item['miss_m'])
            s = s_by_pick.get(q['PICKS'])
            if s is not None:
                zs = surface_at(s, float(q['EASTING']), float(q['NORTHING'])) if _f(q['EASTING']) is not None else None
                item['surface'] = s['id']
                item['surface_minus_pick_m'] = (round(zs + float(q['TVDSS']), 2) if zs is not None else None)
            pk_out.append(item)
        dn = p.dense(10.0)
        dn['x'] = [round(v - origin[0], 2) for v in dn['x']]; dn['y'] = [round(v - origin[1], 2) for v in dn['y']]
        replay, replay_note = replay_from_stations(w['trajs'])
        out_wells.append({
            'id': spec['id'], 'name': w['main']['well'] or spec['id'], 'wellbore': w['main']['wellbore'], 'slot': [round(p.x[0] - origin[0], 2), round(p.y[0] - origin[1], 2)],
            'ref_elev_m': round(w['kb'], 2), 'ref': 'the rig floor (kelly bushing), from the picks: TVD minus depth below sea level',
            'td_md': round(float(p.md[-1]), 2), 'td_z': round(float(p.z[-1]), 2), 'n_stations': int(len(p.md)), 'path': dn,
            'stations': {'md': [round(float(v), 2) for v in p.md], 'incl': [round(float(v), 3) for v in p.inc], 'azi': [round(float(v), 3) for v in p.azi],
                         'dls': [round(float(v), 3) for v in p.dls]},
            'azimuth': {'file_reference': w['main']['azi_ref'], 'applied': spec['azimuth'], 'note': spec.get('why', '')},
            'source': f"{w['main']['name']} ({w['main']['service_company'] or 'service company not stated'}), {len(w['trajs'])} trajectory file(s)",
            'file_position_miss_m': own_miss, 'picks': pk_out, 'pick_miss_max_m': (round(max(miss), 2) if miss else None),
            'replay': replay, 'replay_note': replay_note,
            'channels': {'Inclination': {'unit': 'deg', 'md': [round(float(v), 2) for v in p.md], 'v': [round(float(v), 3) for v in p.inc], 'how': 'at the survey stations'},
                         'Dogleg severity': {'unit': 'deg/30 m', 'md': [round(float(v), 2) for v in p.md], 'v': [round(float(v), 3) for v in p.dls],
                                             'how': 'between the survey stations'}},
        })
    others = []
    for wname, pk in sorted(by_well.items()):
        if any(wname == s['picks'] for s in VOLVE_WELLS):
            continue
        pts = [{'name': q['PICKS'], 'md': _f(q['DEPTH']), 'x': round(float(q['EASTING']) - origin[0], 2), 'y': round(float(q['NORTHING']) - origin[1], 2),
                'z': -float(q['TVDSS'])} for q in pk if _f(q['EASTING']) is not None and _f(q['TVDSS']) is not None]
        if pts:
            others.append({'well': wname, 'picks': pts})
    surfaces = []
    for s in surf['surfaces']:
        g = surface_grid(s, origin)
        ties = []
        for q in picks:
            if q['PICKS'] == s['pick'] and _f(q['EASTING']) is not None and _f(q['TVDSS']) is not None:
                zs = surface_at(s, float(q['EASTING']), float(q['NORTHING']))
                if zs is not None:
                    ties.append({'well': q['WELL'], 'dz': round(zs + float(q['TVDSS']), 2)})
        adz = sorted(abs(t['dz']) for t in ties)
        g['ties'] = ties
        g['tie_median_m'] = round(float(np.median(adz)), 2) if adz else None
        g['tie_max_m'] = round(float(max(adz)), 2) if adz else None
        surfaces.append(g)
    return {'id': 'volve', 'title': 'Volve field, Norwegian North Sea (Equinor open data)', 'kind': 'catalogue',
            'frame': {'crs': 'UTM zone 31N on ED50 (International 1924 ellipsoid), as delivered', 'origin': list(origin), 'unit': 'm',
                      'z': 'depth below mean sea level, metres, positive down',
                      'check': "the slot eastings and northings unprojected on ED50 zone 31N land within 0.1 m of the latitudes and longitudes "
                               "in the operator's own log headers"},
            'wells': out_wells, 'picks_only': others, 'surfaces': surfaces, 'layers': [],
            'attribution': "Contains data from the Volve field released by Equinor ASA and the former Volve licence partners ExxonMobil Exploration "
                           "and Production Norway AS and Bayerngas Norge AS, under Equinor's terms and conditions for the licence to data - Volve. "
                           "The data are not sold here. Equinor and the partners do not endorse this program.",
            'not_drawn': ['wells without a directional survey in this excerpt: their picks are shown as points',
                          'the seabed: the delivered file mixes a depth block with a two-way-time block',
                          'rock and fluid properties between wells: nothing here measures them']}


# -- a workspace's own wells ------------------------------------------------------------------------
def trajectory_of(ws, well_id: str) -> Optional[dict]:
    w = ws.well(well_id)
    t = w.get('trajectory')
    if not t:
        return None
    p = os.path.join(ws.path, 'wells', well_id, 'trajectory', t['file'])
    with open(p, encoding='utf-8', errors='replace') as f:
        text = f.read()
    if t['format'] == 'witsml':
        tr = [x for x in read_witsml_trajectory(text) if x['stations']]
        st = tr[0]['stations'] if tr else []
        st = [s for s in st if s['incl'] is not None and s['azi'] is not None]
        trajs = tr
    else:
        st = read_survey_csv(text, t.get('md_unit') or 'ft')
        trajs = []
    return {'meta': t, 'stations': st, 'trajs': trajs}


def attach_trajectory(ws, well_id: str, filename: str, content: bytes, actor: str, azimuth_reference: str, md_unit: str = 'ft',
                      depth_reference: Optional[str] = None, grid: Optional[str] = None, declination_deg: Optional[float] = None,
                      source: str = '') -> dict:
    """Keep a well's directional survey verbatim beside it and record how to read it. The azimuth reference is the
    operator's statement, never guessed: grid (with the grid named, so the convergence can be computed), true, or
    magnetic (with the declination)."""
    import hashlib
    ws.well(well_id)
    ref = (azimuth_reference or '').lower().strip()
    if ref not in ('grid', 'true', 'magnetic'):
        raise ValueError('state the survey\'s azimuth reference: grid, true or magnetic')
    if ref == 'grid':
        from . import geodesy as GD
        if not grid or grid not in GD.ZONES and not re.match(r'^UTM\d{1,2}[NS]$', grid or ''):
            raise ValueError(f'a grid azimuth reference needs its grid named: one of {", ".join(GD.ZONES)} or UTM<zone><N|S>')
    if ref == 'magnetic' and declination_deg is None:
        raise ValueError('a magnetic azimuth reference needs the declination used (degrees, east positive)')
    if not source.strip():
        raise ValueError('say where the survey came from (e.g. "RRC imaged records, W-1 directional survey" or "operator MWD report")')
    text = content.decode('utf-8', errors='replace')
    fmt = 'witsml' if '<trajectory' in text[:4000] else 'table'
    if fmt == 'witsml':
        tr = read_witsml_trajectory(text)
        st = [s for t in tr[:1] for s in t['stations'] if s['incl'] is not None and s['azi'] is not None]
        if len(st) < 2:
            raise ValueError('the WITSML file holds no trajectory with at least two stations carrying inclination and azimuth')
        md_unit = 'm'
    else:
        st = read_survey_csv(text, md_unit)
    w = ws.well(well_id)
    elev = (w.get('surface') or {}).get('elevation') or {}
    dref = (depth_reference or elev.get('datum') or '').upper()
    if elev and dref and elev.get('datum') and dref != elev['datum'].upper():
        raise ValueError(f"the survey's depths are from {dref} but the well's declared reference elevation is {elev['datum']}: declare the "
                         f"elevation of the survey's reference first")
    d = os.path.join(ws.path, 'wells', well_id, 'trajectory')
    os.makedirs(d, exist_ok=True)
    fn = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename)) or 'survey.csv'
    with open(os.path.join(d, fn), 'wb') as f:
        f.write(content)
    rec = {'file': fn, 'format': fmt, 'md_unit': md_unit, 'azimuth_reference': ref, 'grid': grid if ref == 'grid' else None,
           'declination_deg': declination_deg if ref == 'magnetic' else None, 'depth_reference': dref or None, 'source': source.strip(),
           'sha256': hashlib.sha256(content).hexdigest(), 'stations': len(st), 'md_range_m': [round(st[0]['md'], 2), round(st[-1]['md'], 2)],
           'attached_by': actor, 'attached_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    w['trajectory'] = rec
    _write_well(ws, w)
    ws.audit(actor, 'well.trajectory', {'well': well_id, 'file': fn, 'stations': len(st), 'azimuth_reference': ref, 'source': rec['source']})
    return rec


def _write_well(ws, w: dict) -> None:
    p = os.path.join(ws.path, 'wells', w['id'], 'well.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(w, f, indent=1)
    os.replace(p + '.tmp', p)


def _rot_to_local(meta: dict, lat: float, lon: float, frame_lon0: float) -> Tuple[float, str]:
    """Degrees to add to the survey's azimuths to bring them to the scene's local grid (a transverse Mercator on the
    centre of the wells, so its north is true north at the centre)."""
    from . import geodesy as GD
    ref = meta['azimuth_reference']
    loc = GD.project(lat, lon, GD._tm(0.0, frame_lon0, 1.0, 0.0, 0.0, 'WGS84'))['convergence_deg']
    if ref == 'true':
        return -loc, f'true north, turned {-loc:+.3f} degrees to the scene grid'
    if ref == 'magnetic':
        dec = float(meta['declination_deg'])
        return dec - loc, f'magnetic north with the declination {dec:+.2f} degrees, turned {dec - loc:+.3f} to the scene grid'
    g = meta['grid']
    if g in GD.ZONES:
        zone = GD.ZONES[g]
    else:
        m = re.match(r'^UTM(\d{1,2})([NS])$', g)
        zone = GD.utm(int(m.group(1)), m.group(2) == 'N')
    conv = GD.project(lat, lon, zone)['convergence_deg']
    return conv - loc, f'{g} grid north, the convergence {conv:+.3f} degrees there, turned {conv - loc:+.3f} to the scene grid'


def workspace_scene(ws, margin_km: float = 8.0, layer_tol_m: float = 25.0) -> dict:
    """The workspace's wells that have a surface position, a reference elevation and a directional survey, in a local
    frame (metres east and north of the wells' centre, depth below sea level), with the formation-top contour layers
    at their elevations and each drilling recorder's bit depth for the replay."""
    from . import geodesy as GD
    from . import site_map as SM
    from . import series_cache as SCH
    wl = []
    listed = []
    for w in ws.wells():
        s = w.get('surface') or {}
        why = None
        if s.get('lat') is None:
            why = 'no surface position declared'
        elif not (s.get('elevation') or {}).get('ft') and (s.get('elevation') or {}).get('ft') != 0:
            why = 'no reference elevation declared'
        elif not w.get('trajectory'):
            why = 'no directional survey attached'
        if why:
            listed.append({'id': w['id'], 'display': w.get('display') or w['id'], 'why': why})
            continue
        g = GD.to_wgs84(float(s['lat']), float(s['lon']), 0.0, s.get('datum') or 'UNKNOWN')
        wl.append((w, g))
    lat0 = float(np.mean([g['lat'] for _, g in wl])) if wl else None
    lon0 = float(np.mean([g['lon'] for _, g in wl])) if wl else None
    out_wells = []
    if wl:
        frame = GD._tm(lat0, lon0, 1.0, 0.0, 0.0, 'WGS84')
        for w, g in wl:
            tj = trajectory_of(ws, w['id'])
            pr = GD.project(g['lat'], g['lon'], frame)
            rot, how = _rot_to_local(tj['meta'], g['lat'], g['lon'], lon0)
            elev_m = float(w['surface']['elevation']['ft']) * FT
            st = tj['stations']
            p = Path([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], pr['easting_m'], pr['northing_m'], elev_m, rot)
            dn = p.dense(10.0)
            rec = {'id': w['id'], 'name': w.get('display') or w['id'], 'slot': [round(p.x[0], 2), round(p.y[0], 2)], 'ref_elev_m': round(elev_m, 2),
                   'ref': f"{w['surface']['elevation']['datum']} at {w['surface']['elevation']['ft']:,} ft ({w['surface']['elevation'].get('source', '')})",
                   'td_md': round(float(p.md[-1]), 2), 'td_z': round(float(p.z[-1]), 2), 'n_stations': int(len(p.md)), 'path': dn,
                   'stations': {'md': [round(float(v), 2) for v in p.md], 'incl': [round(float(v), 3) for v in p.inc], 'azi': [round(float(v), 3) for v in p.azi],
                                'dls': [round(float(v), 3) for v in p.dls]},
                   'azimuth': {'file_reference': tj['meta']['azimuth_reference'], 'applied': how, 'note': ''},
                   'source': tj['meta']['source'], 'picks': [], 'replay': None,
                   'channels': {'Inclination': {'unit': 'deg', 'md': [round(float(v), 2) for v in p.md], 'v': [round(float(v), 3) for v in p.inc], 'how': 'at the survey stations'},
                                'Dogleg severity': {'unit': 'deg/30 m', 'md': [round(float(v), 2) for v in p.md], 'v': [round(float(v), 3) for v in p.dls],
                                                    'how': 'between the survey stations'}}}
            try:
                c = SCH.load(SCH.cache_dir(ws, w['id']), SCH.source_path(ws, w['id']))
            except Exception:
                c = None
            if c is not None and c.meta.get('start_time'):
                names = {x['name']: x for x in c.meta['channels'] if x.get('present')}
                if 'Bit Depth' in names:
                    idx = np.asarray(c.index, dtype=np.float64)
                    bd = np.asarray(c.channel('Bit Depth'), dtype=np.float64)
                    k = max(1, len(idx) // 4000)
                    sel = np.arange(0, len(idx), k)
                    sel = sel[~np.isnan(bd[sel])]
                    t0 = datetime.fromisoformat(c.meta['start_time'])
                    f = FT if (c.unit('Bit Depth') or 'ft').lower().startswith('f') else 1.0
                    rec['replay'] = {'kind': 'drilling recorder', 'how': f'the recorder\'s bit depth, every {k}th sample (each a sample, none averaged)',
                                     # the recorder's own clock, as written in its file: no time zone is added to it
                                     't': [(t0 + timedelta(seconds=float(idx[i]))).isoformat()[:19] for i in sel],
                                     'md': [round(float(bd[i]) * f, 2) for i in sel]}
                    chans = [n for n in ('Gamma', 'Rate Of Penetration', 'Weight on Bit', 'Rotary RPM', 'Differential Pressure', 'Total Gas') if n in names]
                    if chans and c.extra('drilling') is not None:
                        dw = SCH.depth_window(c, chans, points=1500)
                        for n in chans:
                            ch = dw['channels'][n]
                            rec['channels'][n] = {'unit': ch['unit'], 'md': [round(v * f, 2) if v is not None else None for v in dw['x']], 'v': ch['mean'],
                                                  'how': "the drilling samples' mean in each depth bin of the recorder's bit depth"}
            out_wells.append(rec)
    layers, skipped = [], []
    tol_m = layer_tol_m
    if wl:
        xs = [w['slot'][0] for w in out_wells]; ys = [w['slot'][1] for w in out_wells]
        r = margin_km * 1000.0
        lo = GD.unproject(min(xs) - r, min(ys) - r, frame); hi = GD.unproject(max(xs) + r, max(ys) + r, frame)
        view = [lo[1], lo[0], hi[1], hi[0]]
        why_view = f'the placed wells and {margin_km:g} km around them'
    elif any(r_.get('kind') == 'contours' for r_ in ws.layers()):
        # no well can be placed yet: the formation tops alone, over the area the map opens on when no well is placed
        dv = SM.overview(ws)['view']
        view, why_view = dv['bbox'], dv['why']
        lat0, lon0 = (view[1] + view[3]) / 2.0, (view[0] + view[2]) / 2.0
        frame = GD._tm(lat0, lon0, 1.0, 0.0, 0.0, 'WGS84')
        span_m = max((view[2] - view[0]) * 111000.0 * math.cos(math.radians(lat0)), (view[3] - view[1]) * 111000.0)
        tol_m = max(layer_tol_m, span_m / 3000.0)
    else:
        view = None
    if view is not None:
        for rec in ws.layers():
            kind, role, va = rec.get('kind'), (rec.get('role') or '').lower(), rec.get('value_attr') or ''
            nm = rec.get('display') or rec['id']
            if kind != 'contours':
                if kind == 'structure':
                    skipped.append({'id': rec['id'], 'display': nm, 'why': 'fault and fold traces carry no depth: they are on the Map, not in 3D'})
                continue
            if role not in ('top', 'base'):
                skipped.append({'id': rec['id'], 'display': nm, 'why': f"its contours are a {role or 'map with no declared role'} ({va or 'no value field'}), not the elevation of a top or base"})
                continue
            if 'elev' not in va.lower():
                skipped.append({'id': rec['id'], 'display': nm, 'why': f"its value field {va or '(none)'} does not say it is an elevation, nor in what unit"})
                continue
            unit = FT if 'ft' in va.lower() or 'feet' in va.lower() else (1.0 if va.lower().endswith('m') else None)
            if unit is None:
                skipped.append({'id': rec['id'], 'display': nm, 'why': f'the value field {va} names no unit (ft or m)'})
                continue
            try:
                lv = SM.layer_view(ws, rec['id'], view, tol_m / 111000.0)
            except (ValueError, OSError) as e:
                skipped.append({'id': rec['id'], 'display': nm, 'why': f'unreadable: {e}'})
                continue
            lines = []
            for f_ in lv['features']:
                if f_.get('value') is None:
                    continue
                z = -float(f_['value']) * unit
                for part in f_['parts']:
                    pts = [GD.project(la, lo_, frame) for lo_, la in part]
                    lines.append({'v': f_['value'], 'z': round(z, 1), 'x': [round(q['easting_m'], 1) for q in pts], 'y': [round(q['northing_m'], 1) for q in pts]})
            if lines:
                layers.append({'id': rec['id'], 'name': nm, 'formation': rec.get('formation') or '', 'role': role, 'value_attr': va, 'lines': lines,
                               'citation': rec.get('citation') or '', 'vertices': sum(len(l_['x']) for l_ in lines)})
    return {'id': 'workspace', 'title': ws.manifest.get('name') or 'This workspace', 'kind': 'workspace',
            'frame': ({'crs': f'local transverse Mercator on WGS84 centred at {lat0:.5f}, {lon0:.5f} (north is true north at the centre)', 'origin': [0.0, 0.0],
                       'unit': 'm', 'z': 'depth below mean sea level, metres, positive down', 'check': '', 'area': why_view,
                       'layer_detail_m': round(tol_m, 1)} if view is not None else None),
            'wells': out_wells, 'not_placed': listed, 'picks_only': [], 'surfaces': [], 'layers': layers, 'layers_not_drawn': skipped,
            'attribution': '', 'not_drawn': ['a well without a surface position, a reference elevation and a directional survey (listed with what it needs)',
                                             'fault and fold traces (no depth)', 'isopachs (a thickness is not a position)',
                                             'rock and fluid properties between wells']}


def scenes(ws=None) -> List[dict]:
    out = [{'id': 'volve', 'title': 'Volve field, Norwegian North Sea (Equinor open data)', 'kind': 'catalogue', 'wells': len(VOLVE_WELLS)}]
    if ws is not None:
        n = sum(1 for w in ws.wells() if w.get('trajectory'))
        out.insert(0, {'id': 'workspace', 'title': ws.manifest.get('name') or 'This workspace', 'kind': 'workspace', 'wells': n})
    return out


def scene(ws, scene_id: str) -> dict:
    if scene_id == 'volve':
        return volve_scene()
    if scene_id == 'workspace':
        return workspace_scene(ws)
    raise KeyError(f'no 3D scene named {scene_id}')


def text(sc: dict) -> str:
    """The scene in words, for the terminal."""
    fr = sc.get('frame') or {}
    L = [sc['title'], f"  frame: {fr.get('crs', 'none - no well can be placed yet and no formation-top layer to draw')}" + (f"; the area: {fr['area']}" if fr.get('area') else '')]
    for w in sc['wells']:
        L.append(f"  {w['name']}: {w['n_stations']} stations to {w['td_md']:,.1f} m MD, {w['td_z']:,.1f} m below sea level; azimuths {w['azimuth']['applied']}"
                 + (f"; the file's own positions reproduced to {w['file_position_miss_m']} m" if w.get('file_position_miss_m') is not None else '')
                 + (f"; the picks to {w['pick_miss_max_m']} m" if w.get('pick_miss_max_m') is not None else ''))
        if w.get('replay'):
            L.append(f"      replay: {len(w['replay']['t'])} steps, {w['replay']['t'][0]} to {w['replay']['t'][-1]} ({w['replay']['kind']})")
        elif w.get('replay_note'):
            L.append(f"      {w['replay_note']}")
    for s in sc.get('surfaces', []):
        L.append(f"  surface {s['name']}: {s['n_points']:,} of {s['n_source_points']:,} points, {s['z_range'][0]:,.0f} to {s['z_range'][1]:,.0f} m; "
                 f"against {len(s['ties'])} picks the median miss {s['tie_median_m']} m, the largest {s['tie_max_m']} m")
    for l in sc.get('layers', []):
        L.append(f"  layer {l['name']}: {len(l['lines'])} contour lines at their elevations ({l['vertices']:,} vertices)")
    for n in sc.get('not_placed', []):
        L.append(f"  not drawn: {n['display']} - {n['why']}")
    for n in sc.get('layers_not_drawn', []):
        L.append(f"  layer not drawn: {n['display']} - {n['why']}")
    if sc.get('attribution'):
        L.append('  ' + sc['attribution'])
    return '\n'.join(L)
