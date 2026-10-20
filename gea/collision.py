"""Well collision checks: how close each well's path comes to its neighbours, against how well both are known.

Two wells drilled from one pad start metres apart. Each well's position is known only as well as its survey: the
error model (`wellpos`: the tool models of the well's survey program, ISCWSA MWD Rev 5.11 unless declared) gives each
station's covariance, growing with depth. A well not yet drilled is checked along its plan (`wellplan`), weighed as if
surveyed every 30 m; a well being drilled along its survey and then its plan below it. This module scans every
well against every other in three dimensions and answers, along the reference well, with the separation factor of the
ISCWSA collision-avoidance rule (Sawaryn et al., "Well-Collision-Avoidance Separation Rule", SPE Drilling &
Completion 34(1), 2019):

    SF = (D - R_r - R_o - S_m) / (k * sqrt(sigma_S^2 + sigma_pa^2))

- D: the centre-to-centre distance from a point on the reference well to the closest point of the offset well (the
  3D closest-approach scan the ISCWSA recommends);
- R_r, R_o: the two holes' radii; S_m: the surface margin (0.3 m by default); sigma_pa: the project-ahead uncertainty
  of the well being drilled (0.5 m by default) - the values the rule recommends;
- sigma_S: the standard deviation of the separation along the line between the two points - the two positions'
  covariances projected on it, less the part they share: an error that is the same in every survey of the field
  (the declination, the field strength, the depth stretch - the model's GLOBAL sources) moves both wells together
  and does not change the distance between them;
- k: the scaling factor the operator's standard sets (3.5 by default; the operator's own value is declared).

The levels are the ISCWSA's in the absence of the operator's own (ISCWSA eBook, Separation Rules): every section
below 4.0 is reported; below 1.5 is the approach limit (the neighbour may have to be shut in); below 1.25 the plan
should be changed, for it leaves too little room to steer; below 1.0 drilling needs the operator's dispensation.

The field at each well - declination, dip and strength - comes from the World Magnetic Model (`geomag`) at the well's
position and its survey's date. The worked example is the Volve field's template: three wells whose slots sit two
metres apart.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {'k': 3.5, 'surface_margin_m': 0.3, 'sigma_pa_m': 0.5, 'hole_in': 8.5, 'step_m': 10.0, 'report_below': 4.0}
LEVELS = ((1.0, 'BELOW 1.0', "below 1.0: drilling on needs the operator's dispensation (ISCWSA: a last resort, for a risk that is fiscal only)"),
          (1.25, 'REVISE THE PLAN', 'below 1.25: too little room to steer - change the plan'),
          (1.5, 'APPROACH LIMIT', 'below 1.5: the approach limit - the neighbour may have to be shut in while drilling'),
          (4.0, 'REPORTED', 'below 4.0: reported, as every section under 4.0 is'))
RULE_SOURCE = ('the ISCWSA separation rule (Sawaryn et al., Well-Collision-Avoidance Separation Rule, SPE Drilling & Completion 34(1), 2019; '
               'surface margin 0.3 m and project-ahead 0.5 m as recommended) and the ISCWSA eBook\'s levels (Separation Rules: report below 4.0, '
               'approach limit 1.5, revise below 1.25, dispensation below 1.0)')
_LOCK = threading.Lock()
TC_NORTH_BELOW_DEG = 5.0          # the travelling cylinder's angle from north while the reference is within this of vertical
PLANNED_STATION_M = 30.0          # a plan weighed as if surveyed every 30 m (one stand)


def _path(ws) -> str:
    return os.path.join(ws.path, 'wellpath', 'collision.json')


def settings(ws) -> dict:
    s = {**DEFAULTS, 'holes_in': {}}
    if ws is None:
        return s
    try:
        with open(_path(ws), encoding='utf-8') as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def set_settings(ws, actor: str, k=None, surface_margin_m=None, sigma_pa_m=None, hole: Optional[Tuple[str, object]] = None, source: str = '') -> dict:
    s = settings(ws)
    for key, v, lo in (('k', k, 0.5), ('surface_margin_m', surface_margin_m, 0.0), ('sigma_pa_m', sigma_pa_m, 0.0)):
        if v in (None, ''):
            continue
        x = float(v)
        if not math.isfinite(x) or x < lo:
            raise ValueError(f'{key} must be at least {lo:g}')
        s[key] = x
    if any(v not in (None, '') for v in (k, surface_margin_m, sigma_pa_m)):
        if not (source or '').strip():
            raise ValueError("the rule's values need where they came from (e.g. \"company anti-collision standard, rev 3\")")
        s['rule_source'] = source.strip()
    if hole:
        wid, v = hole
        if not any(w['id'] == wid for w in ws.wells()):
            raise KeyError(f'no well {wid!r}')
        if v in (None, ''):
            s['holes_in'].pop(wid, None)
        else:
            x = float(v)
            if not 2.0 <= x <= 40.0:
                raise ValueError('the hole size is in inches, between 2 and 40')
            s['holes_in'][wid] = x
    s['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    s['by'] = actor
    os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
    with open(_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump({k_: v_ for k_, v_ in s.items() if k_ not in DEFAULTS or s[k_] != DEFAULTS[k_] or k_ in ('k', 'surface_margin_m', 'sigma_pa_m')}, f, indent=1)
    os.replace(_path(ws) + '.tmp', _path(ws))
    ws.audit(actor, 'collision.settings', {k_: s.get(k_) for k_ in ('k', 'surface_margin_m', 'sigma_pa_m', 'holes_in', 'rule_source')})
    return s


# -- one well: positions and covariances along its path ---------------------------------------------------------------
def prepare(w: dict, step_m: float = 10.0) -> dict:
    """w: {id, display, md (m), inc (deg), az (deg, the frame's north), az_true_offset_deg (true = frame + this), x0, y0
    (m in the frame), ref_elev_m, field {declination_deg, dip_deg, total_nT}, hole_in}. Adds the stations' positions and
    covariances in the frame, and a point every step_m with them interpolated."""
    from . import well3d as W3
    from . import wellpos as WP
    md = np.asarray(w['md'], dtype=np.float64)
    inc = np.asarray(w['inc'], dtype=np.float64)
    az = np.asarray(w['az'], dtype=np.float64)
    if md[0] > 0:                                      # tie the survey at the surface, vertical above its first station
        md, inc, az = np.r_[0.0, md], np.r_[0.0, inc], np.r_[az[0], az]
    p = W3.Path(md, inc, az, w['x0'], w['y0'], w.get('ref_elev_m') or 0.0, 0.0)
    az_true = (az + w.get('az_true_offset_deg', 0.0)) % 360.0
    f = w['field']
    u = WP.program_uncertainty(md, inc, az_true, p.tvd, f['declination_deg'], f['dip_deg'], f['total_nT'], w.get('program'))
    r = math.radians(-w.get('az_true_offset_deg', 0.0))  # true north -> the frame's north
    R = np.array([[math.cos(r), -math.sin(r), 0.0], [math.sin(r), math.cos(r), 0.0], [0.0, 0.0, 1.0]])
    C = np.einsum('ij,kjl,ml->kim', R, u['cov'], R)
    G = {k: u['global'][k] @ R.T for k in u['global']}
    grid = np.unique(np.r_[md, np.arange(md[0], md[-1], step_m), md[-1]])
    pts = np.array([p.at(m) for m in grid])           # east, north, depth below sea level
    pos = np.stack([pts[:, 1], pts[:, 0], pts[:, 2]], axis=1)          # north, east, down
    j = np.clip(np.searchsorted(md, grid, 'right') - 1, 0, len(md) - 2)
    t = ((grid - md[j]) / np.where(md[j + 1] > md[j], md[j + 1] - md[j], 1.0)).clip(0.0, 1.0)
    Ci = C[j] * (1 - t)[:, None, None] + C[j + 1] * t[:, None, None]
    Gi = {k: v[j] * (1 - t)[:, None] + v[j + 1] * t[:, None] for k, v in G.items()}
    tng = np.gradient(pos, grid, axis=0) if len(grid) > 1 else np.array([[0.0, 0.0, 1.0]])
    tng = tng / np.maximum(np.linalg.norm(tng, axis=1), 1e-12)[:, None]
    inc_g = np.degrees(np.arccos(np.clip(tng[:, 2], -1.0, 1.0)))
    az_g = np.arctan2(tng[:, 1], tng[:, 0])
    hs = np.stack([np.cos(np.radians(inc_g)) * np.cos(az_g), np.cos(np.radians(inc_g)) * np.sin(az_g), -np.sin(np.radians(inc_g))], axis=1)
    rt = np.stack([-np.sin(az_g), np.cos(az_g), np.zeros(len(grid))], axis=1)
    return {**w, 'grid_md': grid, 'pos': pos, 'cov': Ci, 'glob': Gi, 'radius_m': w.get('hole_in', DEFAULTS['hole_in']) * 0.0254 / 2.0,
            'grid_inc': inc_g, 'hs': hs, 'rt': rt, 'model': u['model'],
            'stations_cov': C, 'stations_md': md, 'td_ellipse': WP.ellipse(C[-1], 1.0)}


def level(sf: float) -> Tuple[str, str]:
    for lim, name, why in LEVELS:
        if sf < lim:
            return name, why
    return 'CLEAR', 'at or above 4.0'


def scan(ref: dict, off: dict, rule: dict) -> dict:
    """The reference well scanned against the offset: at each point of the reference, the closest point of the offset
    (on the segments between its points), the distance, sigma along it and the separation factor."""
    A, B = ref['pos'], off['pos']
    P0, P1 = B[:-1], B[1:]
    d = P1 - P0
    L2 = np.einsum('ij,ij->i', d, d)
    out = []
    k, sm, spa = float(rule['k']), float(rule['surface_margin_m']), float(rule['sigma_pa_m'])
    for i in range(len(A)):
        a = A[i]
        tt = np.where(L2 > 0, np.einsum('ij,ij->i', a - P0, d) / np.where(L2 > 0, L2, 1.0), 0.0).clip(0.0, 1.0)
        Q = P0 + tt[:, None] * d
        dist = np.linalg.norm(Q - a, axis=1)
        jj = int(np.argmin(dist))
        D = float(dist[jj]); q = Q[jj]; f = float(tt[jj])
        n = (q - a) / D if D > 1e-9 else np.array([0.0, 0.0, 1.0])
        Co = off['cov'][jj] * (1 - f) + off['cov'][jj + 1] * f
        Cr = ref['cov'][i]
        X = np.zeros((3, 3))
        for g_, sr in ref['glob'].items():                   # what both wells share moves them together
            if g_ not in off['glob']:
                continue
            so = off['glob'][g_][jj] * (1 - f) + off['glob'][g_][jj + 1] * f
            X += np.outer(sr[i], so)
        var = float(n @ (Cr + Co - X - X.T) @ n)
        sig = math.sqrt(max(var, 0.0))
        sf = (D - ref['radius_m'] - off['radius_m'] - sm) / (k * math.sqrt(sig * sig + spa * spa))
        md_o = float(off['grid_md'][jj] * (1 - f) + off['grid_md'][jj + 1] * f)
        if ref['grid_inc'][i] < TC_NORTH_BELOW_DEG:              # near vertical: the bearing from north
            ang = math.degrees(math.atan2(float(n[1]), float(n[0]))) % 360.0
        else:                                                     # clockwise from the reference's high side
            ang = math.degrees(math.atan2(float(n @ ref['rt'][i]), float(n @ ref['hs'][i]))) % 360.0
        out.append((float(ref['grid_md'][i]), float(a[2]), md_o, D, sig, sf, ang))
    arr = np.array(out)
    i_min = int(np.argmin(arr[:, 5]))
    low = arr[:, 5] < float(rule.get('report_below', 4.0))
    sections = []
    for s_, e_ in _runs(low):
        seg = arr[s_:e_]
        m = int(np.argmin(seg[:, 5]))
        sections.append({'from_md_m': round(float(seg[0, 0]), 1), 'to_md_m': round(float(seg[-1, 0]), 1), 'min_sf': round(float(seg[m, 5]), 2),
                         'at_md_m': round(float(seg[m, 0]), 1), 'level': level(float(seg[m, 5]))[0]})
    mn = arr[i_min]
    lv, why = level(float(mn[5]))
    return {'reference': ref['id'], 'offset': off['id'], 'reference_name': ref['display'], 'offset_name': off['display'],
            'min_sf': round(float(mn[5]), 3), 'at_md_m': round(float(mn[0]), 1), 'at_depth_m': round(float(mn[1]), 1), 'offset_md_m': round(float(mn[2]), 1),
            'distance_m': round(float(mn[3]), 3), 'sigma_m': round(float(mn[4]), 3), 'level': lv, 'why': why, 'sections': sections,
            'closest_m': round(float(arr[:, 3].min()), 3), 'closest_at_md_m': round(float(arr[int(np.argmin(arr[:, 3])), 0]), 1),
            'profile': {'md_m': [round(float(x), 1) for x in arr[:, 0]], 'sf': [round(float(min(x, 99.0)), 3) for x in arr[:, 5]],
                        'distance_m': [round(float(x), 2) for x in arr[:, 3]], 'sigma_m': [round(float(x), 3) for x in arr[:, 4]],
                        'offset_md_m': [round(float(x), 1) for x in arr[:, 2]], 'tc_deg': [round(float(x), 1) for x in arr[:, 6]],
                        'tc_north': [bool(x < TC_NORTH_BELOW_DEG) for x in ref['grid_inc']]}}


def _runs(mask):
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def analyse(wells: List[dict], rule: dict, title: str = '') -> dict:
    """Every well against every other (each as the reference in turn)."""
    prep = [prepare(w, rule.get('step_m', DEFAULTS['step_m'])) for w in wells]
    pairs = []
    for a in prep:
        for b in prep:
            if a['id'] != b['id']:
                pairs.append(scan(a, b, rule))
    pairs.sort(key=lambda p: p['min_sf'])
    wl = [{'id': w['id'], 'display': w['display'], 'td_md_m': round(float(w['stations_md'][-1]), 1), 'n_stations': int(len(w['stations_md'])),
           'hole_in': w.get('hole_in', DEFAULTS['hole_in']), 'field': w['field'], 'td_ellipse_1sigma': w['td_ellipse'],
           'slot': [round(float(w['x0']), 2), round(float(w['y0']), 2)], 'field_note': w.get('field_note', ''), 'azimuth_note': w.get('azimuth_note', ''),
           'model': w['model'], 'basis': w.get('basis', 'survey'), 'basis_note': w.get('basis_note', '')}
          for w in prep]
    worst = pairs[0] if pairs else None
    return {'title': title, 'rule': {k: rule[k] for k in ('k', 'surface_margin_m', 'sigma_pa_m') if k in rule}, 'rule_source': rule.get('rule_source') or RULE_SOURCE,
            'wells': wl, 'pairs': pairs, 'worst': {k: v for k, v in (worst or {}).items() if k != 'profile'} or None,
            'levels': [{'below': lim, 'level': name, 'meaning': why} for lim, name, why in LEVELS],
            'model': 'each well by its survey program\'s tool models (ISCWSA MWD Rev 5.11, MWD+SRGM, unless declared)',
            'not_a_measurement': ['the wells\' true positions: an uncertainty is what the survey tool\'s published error model allows, not a measured miss',
                                  'a probability of collision: the separation factor is a rule\'s ratio, and the operator\'s standard says what it permits',
                                  'the hole size when it is not declared (8-1/2 in by default), and the casing beyond it',
                                  'a plan\'s position: a well not yet drilled is checked along its plan, weighed as if surveyed every 30 m by its program\'s tools']}


# -- the scenes -----------------------------------------------------------------------------------------------------------
def volve_wells() -> Tuple[List[dict], str]:
    """The Volve template's three wells from the 3D scene: their stations in the field's grid (UTM zone 31N on ED50),
    the field from WMM2025 at the template for 2025.0 (the wells were surveyed 2008-2016; the field then differs by a
    degree or so of declination, which only weighs the magnetometer terms)."""
    from . import geodesy as GD
    from . import geomag as GM
    from . import well3d as W3
    sc = W3.volve_scene()
    ox, oy = sc['frame']['origin']
    zone = GD.utm(31, True, 'INTL1924')
    lat, lon = GD.unproject(ox, oy, zone)
    conv = GD.project(lat, lon, zone)['convergence_deg']
    f = GM.field(lat, lon, 0.0, 2025.0)
    fld = {'declination_deg': round(f['declination_deg'], 3), 'dip_deg': round(f['dip_deg'], 3), 'total_nT': round(f['total_nT'], 1)}
    out = []
    for w in sc['wells']:
        st = w['stations']
        out.append({'id': w['id'], 'display': w['name'], 'md': st['md'], 'inc': st['incl'], 'az': st['azi'], 'az_true_offset_deg': conv,
                    'x0': float(w['slot'][0]), 'y0': float(w['slot'][1]), 'ref_elev_m': float(w['ref_elev_m']), 'field': fld, 'hole_in': 8.5,
                    'field_note': f"WMM2025 at the template ({lat:.4f}, {lon:.4f}) for 2025.0", 'azimuth_note': f'grid azimuths, the grid convergence {conv:+.3f} deg'})
    return out, f"Volve, the template of 15/9-F: {', '.join(w['display'] for w in out)} (Equinor's released data)"


def workspace_wells(ws) -> Tuple[List[dict], List[dict]]:
    """The workspace's wells with a surface position, a reference elevation and a survey, in the 3D tab's frame; and
    those left out, with why."""
    from . import geomag as GM
    from . import well3d as W3
    sc = W3.workspace_scene(ws)
    s = settings(ws)
    left = list(sc.get('not_placed') or [])
    from . import wellplan as WPL
    out = []
    plans = {p['id']: p for p in sc.get('planned') or []}
    entries = [(w, 'survey') for w in sc['wells']] + [(p, 'plan') for p in plans.values() if not p['surveyed']]
    for w, basis in entries:
        wid = w['id']
        wr = ws.well(wid)
        surf = wr.get('surface') or {}
        when = None
        try:
            from . import drill_string as DS
            c = DS._cache(ws, wid, build=False)
            when = c.meta.get('start_time')
        except Exception:
            pass
        f = GM.field(float(surf.get('lat')), float(surf.get('lon')), 0.0, when or datetime.now(timezone.utc).date())
        st = dict(w['stations'])
        note = ''
        if basis == 'plan':                                # a well not yet drilled: its plan, weighed as surveyed every stand
            st = _densify(st, PLANNED_STATION_M)
            note = f'the plan ({w["source"]}), weighed as surveyed every {PLANNED_STATION_M:g} m'
        elif wid in plans and plans[wid]['td_md'] > float(st['md'][-1]) + 1.0:    # drilling: the survey, then the plan below it
            pl = _densify(plans[wid]['stations'], PLANNED_STATION_M)
            below = [i for i, m in enumerate(pl['md']) if m > float(st['md'][-1]) + 1.0]
            st = {k: list(st[k]) + [pl[k][i] for i in below] for k in ('md', 'incl', 'azi')}
            basis = 'survey + plan'
            note = f'surveyed to {w["stations"]["md"][-1]:,.1f} m, the plan below it'
        prog = WPL.well_settings(ws, wid)['program']
        out.append({'id': wid, 'display': w['name'] + (' (plan)' if basis == 'plan' else ''), 'md': st['md'], 'inc': st['incl'], 'az': st['azi'], 'az_true_offset_deg': 0.0,
                    'program': prog, 'basis': basis, 'basis_note': note,
                    'x0': float(w['slot'][0]), 'y0': float(w['slot'][1]), 'ref_elev_m': float(w['ref_elev_m']),
                    'field': {'declination_deg': round(f['declination_deg'], 3), 'dip_deg': round(f['dip_deg'], 3), 'total_nT': round(f['total_nT'], 1)},
                    'hole_in': s['holes_in'].get(wid, DEFAULTS['hole_in']),
                    'field_note': f"WMM2025 at the well for {f['decimal_year']:.2f}" + (f" ({f['note']})" if f['note'] else ''),
                    'azimuth_note': w['azimuth']['applied']})
    placed = {x['id'] for x in out}
    left = [x for x in left if x['id'] not in placed]
    return out, left


def _densify(st: dict, step: float) -> dict:
    """A plan's stations with one added every `step` metres between them, on its arcs (as a survey would see it)."""
    from . import well3d as W3
    from . import wellplan as WPL
    p = W3.Path(st['md'], st['incl'], st['azi'], 0.0, 0.0, 0.0, 0.0)
    mds = sorted(set(np.round(np.concatenate([p.md, np.arange(p.md[0], p.md[-1], step)]), 3).tolist()))
    dirs = [WPL._interp_dir(p, m) for m in mds]
    return {'md': mds, 'incl': [d[0] for d in dirs], 'azi': [d[1] for d in dirs]}


def page(ws, scene: str = 'workspace') -> dict:
    rule = settings(ws)
    if scene == 'volve' or ws is None:
        wells, title = volve_wells()
        res = analyse(wells, {**DEFAULTS, **{k: rule[k] for k in ('k', 'surface_margin_m', 'sigma_pa_m') if k in rule}, 'rule_source': rule.get('rule_source')}, title)
        res['left_out'] = []
    elif scene == 'workspace':
        wells, left = workspace_wells(ws)
        res = analyse(wells, rule, ws.manifest.get('name') or 'this workspace') if len(wells) >= 2 else {
            'title': ws.manifest.get('name') or 'this workspace', 'wells': [], 'pairs': [], 'worst': None, 'rule': {k: rule[k] for k in ('k', 'surface_margin_m', 'sigma_pa_m')},
            'rule_source': rule.get('rule_source') or RULE_SOURCE, 'levels': [{'below': lim, 'level': name, 'meaning': why} for lim, name, why in LEVELS],
            'model': 'each well by its survey program\'s tool models', 'not_a_measurement': [],
            'detail': f"{len(wells)} well(s) have a surface position, a reference elevation and a survey or a plan; a check needs two"}
        res['left_out'] = left
    else:
        raise KeyError(f'no scene {scene!r}: workspace or volve')
    res['scene'] = scene
    res['scenes'] = [{'id': 'workspace', 'title': 'Your wells'}, {'id': 'volve', 'title': 'Worked example: the Volve template (public data)'}]
    res['settings'] = {k: rule.get(k) for k in ('k', 'surface_margin_m', 'sigma_pa_m', 'holes_in', 'rule_source')}
    return res


# -- the report -----------------------------------------------------------------------------------------------------------
def build_report(ws, version: str, scene: str = 'workspace'):
    from . import client_reports as CR
    r = page(ws, scene)
    now = datetime.now(timezone.utc)
    rid = f"COL-{now.strftime('%Y%m%dT%H%M%SZ')}"
    w = r.get('worst')
    front = [['Report ID', rid], ['Scope', r['title']], ['Wells', str(len(r['wells']))], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Closest approach', (f"separation factor {w['min_sf']:.2f} ({w['level']}): {w['reference_name']} at {w['at_md_m']:,.0f} m MD against {w['offset_name']}, "
                                   f"{w['distance_m']:.2f} m apart" if w else r.get('detail', 'no pair to check'))]]
    s1 = CR.Section('1', 'The rule', [f"SF = (D - R_r - R_o - S_m) / (k x sqrt(sigma_S^2 + sigma_pa^2)) with k = {r['rule']['k']:g}, S_m = {r['rule']['surface_margin_m']:g} m, "
                                      f"sigma_pa = {r['rule']['sigma_pa_m']:g} m; {r['rule_source']}. Each well's uncertainty: {r['model']}."], [])
    s1.tables.append(CR.Table(['Below', 'Level', 'Meaning'], [[f"{x['below']:g}", x['level'], x['meaning']] for x in r['levels']]))
    s2 = CR.Section('2', 'The wells', [], [])
    s2.tables.append(CR.Table(['Well', 'TD (m MD)', 'Stations', 'Hole (in)', 'Field (dec / dip / total)', 'Ellipse at TD, 1 sigma (major / minor / vertical, m)'],
                              [[x['display'], f"{x['td_md_m']:,.1f}", x['n_stations'], f"{x['hole_in']:g}",
                                f"{x['field']['declination_deg']:+.2f} / {x['field']['dip_deg']:.2f} / {x['field']['total_nT']:,.0f} nT",
                                f"{x['td_ellipse_1sigma']['major_m']:.2f} / {x['td_ellipse_1sigma']['minor_m']:.2f} / {x['td_ellipse_1sigma']['vertical_m']:.2f}"] for x in r['wells']]))
    if r.get('left_out'):
        s2.paragraphs.append('Not checked: ' + '; '.join(f"{x.get('display', x.get('id'))} ({x.get('why', '')})" for x in r['left_out']) + '.')
    s3 = CR.Section('3', 'Every pair', [], [])
    s3.tables.append(CR.Table(['Reference', 'Offset', 'Minimum SF', 'Level', 'At (m MD)', 'Distance (m)', 'Sigma (m)', 'Sections below 4.0'],
                              [[p['reference_name'], p['offset_name'], f"{p['min_sf']:.2f}", p['level'], f"{p['at_md_m']:,.0f}", f"{p['distance_m']:.2f}", f"{p['sigma_m']:.2f}",
                                '; '.join(f"{s_['from_md_m']:,.0f}-{s_['to_md_m']:,.0f} m (min {s_['min_sf']:.2f})" for s_ in p['sections'][:6]) or '-'] for p in r['pairs']]))
    s4 = CR.Section('4', 'What this will not call a measurement', ['; '.join(r.get('not_a_measurement') or []) + '.'], [])
    doc = CR.Document(f"Well collision check - {r['title']}", rid, front, [s1, s2, s3, s4],
                      footer='Error model: ISCWSA MWD Rev 4 (SPE-67616-PA). Field: WMM2025 (NCEI/BGS). ' + RULE_SOURCE + '.',
                      data={'report_id': rid, **{k: v for k, v in r.items() if k != 'pairs'}, 'pairs': [{k: v for k, v in p.items() if k != 'profile'} for p in r['pairs']]})
    return doc


def write_report(ws, version: str, actor: str = 'system', scene: str = 'workspace') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, scene)
    paths = CR.write(doc, ws.reports_dir, 'collision' if scene == 'workspace' else f'collision_{scene}')
    ws.audit(actor, 'report.collision', {'scene': scene, 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


def text(r: dict) -> str:
    L = [f"Well collision check - {r['title']}: rule k {r['rule']['k']:g}, Sm {r['rule']['surface_margin_m']:g} m, sigma_pa {r['rule']['sigma_pa_m']:g} m"]
    if r.get('detail'):
        L.append('  ' + r['detail'])
    for w in r['wells']:
        e = w['td_ellipse_1sigma']
        L.append(f"  {w['display']:<14} TD {w['td_md_m']:>8,.1f} m MD, {w['n_stations']} stations, hole {w['hole_in']:g} in; at TD 1-sigma {e['major_m']:.2f} x {e['minor_m']:.2f} m, "
                 f"vertical {e['vertical_m']:.2f} m; field dec {w['field']['declination_deg']:+.2f}, dip {w['field']['dip_deg']:.2f}, {w['field']['total_nT']:,.0f} nT")
    for p in r['pairs']:
        L.append(f"  {p['reference_name']:>12} vs {p['offset_name']:<12} SF {p['min_sf']:6.2f} {p['level']:<15} at {p['at_md_m']:>7,.0f} m MD, {p['distance_m']:.2f} m apart, sigma {p['sigma_m']:.2f} m"
                 + (f"; below 4.0: " + ', '.join(f"{s_['from_md_m']:,.0f}-{s_['to_md_m']:,.0f} m" for s_ in p['sections'][:4]) if p['sections'] else ''))
    for x in r.get('left_out') or []:
        L.append(f"  not checked: {x.get('display', x.get('id'))} - {x.get('why', '')}")
    return '\n'.join(L)
