"""Plan against actual: where a well is against its plan, where the bit is heading, and how to get back.

A directional plan is a trajectory like a survey - measured depth, inclination and azimuth - attached beside the well
with its azimuth reference stated (the same reader and the same rules as a survey). Both are put in the 3D tab's frame
and compared station by station:

- at each surveyed station, the closest point of the plan (in three dimensions, on the plan's arcs) and the distance
  to it, split in the plan's own frame there: HIGH (+) or LOW (-) of the plan in the vertical plane through its
  direction, RIGHT (+) or LEFT (-) across it, and the inclination and azimuth against the plan's there;
- each split against the survey's own uncertainty in that direction (the well's survey program weighed by its tool
  models, `wellpos`): a well off its plan by more than two standard deviations is off by more than the survey can
  explain;
- the bit projected below the last survey by the survey-to-bit offset, holding the last interval's build and turn
  rates (the "constant build and turn" projection);
- from the projected bit, the one arc that reaches the plan a set distance ahead: its dogleg severity and the
  toolface to set (0 high side, 90 right, 180 low side, 270 left) - the steering a directional driller reads.

The survey program - which tool surveyed which depths - and the projection's settings are declared per well. A tool
model names one of the ISCWSA / OWSG models in `wellpos` (MWD+SRGM, the ISCWSA MWD Rev 5.11 standard, by default).
"""
from __future__ import annotations

import base64
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {'bit_offset_m': 15.0, 'lookahead_m': 30.0}
FT = 0.3048
NOT_A_MEASUREMENT = ['where the bit is: below the last survey the position is projected, not surveyed',
                     'the arc back to the plan: one arc to a point of the plan, not a full re-plan with the plan\'s direction there',
                     'a tool\'s accuracy beyond its model: each survey is weighed by the tool model its program names']


def _path(ws) -> str:
    return os.path.join(ws.path, 'wellpath', 'survey_program.json')


def settings(ws) -> dict:
    s = {'wells': {}}
    if ws is None:
        return s
    try:
        with open(_path(ws), encoding='utf-8') as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def well_settings(ws, well_id: str) -> dict:
    from . import wellpos as WP
    w = (settings(ws).get('wells') or {}).get(well_id) or {}
    return {'program': w.get('program') or [{'to_md_m': None, 'model': WP.DEFAULT_MODEL}],
            'program_declared': bool(w.get('program')), 'bit_offset_m': float(w.get('bit_offset_m', DEFAULTS['bit_offset_m'])),
            'lookahead_m': float(w.get('lookahead_m', DEFAULTS['lookahead_m'])), 'source': w.get('source') or '',
            'utc': w.get('utc'), 'by': w.get('by')}


def parse_program(text: str) -> List[dict]:
    """'MWD+SRGM' or '0-1500:MWD+SRGM, 1500-:MWD+IFR1' (metres) into a survey program."""
    from . import wellpos as WP
    out = []
    for part in [p.strip() for p in (text or '').split(',') if p.strip()]:
        if ':' in part and '-' in part.split(':', 1)[0]:
            rng, name = part.split(':', 1)
            hi = rng.split('-', 1)[1].strip()
            out.append({'to_md_m': float(hi) if hi else None, 'model': name.strip()})
        else:
            out.append({'to_md_m': None, 'model': part})
    check_program(out)
    return out


def check_program(program: List[dict]) -> List[dict]:
    from . import wellpos as WP
    if not program:
        raise ValueError('a survey program needs at least one tool model')
    last = -1.0
    for i, s in enumerate(program):
        if s.get('model') not in WP.models():
            raise ValueError(f"no tool model {s.get('model')!r}: one of {', '.join(WP.models())}")
        to = s.get('to_md_m')
        if to in (None, ''):
            if i != len(program) - 1:
                raise ValueError('only the last run of a survey program may run to the end of the well')
            s['to_md_m'] = None
            continue
        to = float(to)
        if not math.isfinite(to) or to <= last:
            raise ValueError('each run of a survey program must end deeper than the one before')
        s['to_md_m'] = last = to
    return program


def set_well(ws, actor: str, well_id: str, program: Optional[List[dict]] = None, bit_offset_m=None, lookahead_m=None,
             source: str = '') -> dict:
    """Declare a well's survey program and the projection's settings."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    s = settings(ws)
    cur = dict((s.get('wells') or {}).get(well_id) or {})
    if program is not None:
        if not (source or '').strip():
            raise ValueError('say where the survey program came from (e.g. "directional company\'s survey program, rev 1")')
        cur['program'] = check_program([dict(p) for p in program])
        cur['source'] = source.strip()
    for key, v, lo, hi in (('bit_offset_m', bit_offset_m, 0.0, 100.0), ('lookahead_m', lookahead_m, 5.0, 1000.0)):
        if v in (None, ''):
            continue
        x = float(v)
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{key} is in metres, between {lo:g} and {hi:g}')
        cur[key] = x
    cur['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    cur['by'] = actor
    s.setdefault('wells', {})[well_id] = cur
    os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
    with open(_path(ws) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)
    os.replace(_path(ws) + '.tmp', _path(ws))
    ws.audit(actor, 'survey_program.declare', {'well': well_id, **{k: cur.get(k) for k in ('program', 'bit_offset_m', 'lookahead_m', 'source')}})
    return well_settings(ws, well_id)


def attach(ws, well_id: str, filename: str, content: bytes, actor: str, azimuth_reference: str, md_unit: str = 'ft',
           grid: Optional[str] = None, declination_deg: Optional[float] = None, source: str = '') -> dict:
    """Attach a well's plan: a table of measured depth, inclination and azimuth (CSV or an Excel workbook) or a WITSML
    trajectory, its azimuth reference stated."""
    from . import well3d as W3
    from . import xlsx_read as XR
    if XR.is_xlsx(content):
        content = XR.to_csv(content).encode('utf-8')
        filename = os.path.splitext(filename)[0] + '.csv'
    return W3.attach_trajectory(ws, well_id, filename, content, actor, azimuth_reference, md_unit, None, grid, declination_deg, source, kind='plan')


def remove(ws, well_id: str, actor: str) -> dict:
    from . import well3d as W3
    return W3.remove_plan(ws, well_id, actor)


# -- the geometry -------------------------------------------------------------------------------------------------------
def _frame(inc_deg: float, az_deg: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The direction of the hole (north, east, down), its high side and its right, at an inclination and azimuth."""
    i, a = math.radians(inc_deg), math.radians(az_deg)
    t = np.array([math.sin(i) * math.cos(a), math.sin(i) * math.sin(a), math.cos(i)])
    h = np.array([math.cos(i) * math.cos(a), math.cos(i) * math.sin(a), -math.sin(i)])
    r = np.array([-math.sin(a), math.cos(a), 0.0])
    return t, h, r


def _nev(p) -> np.ndarray:
    """A Path's position (east, north, depth) as north, east, down."""
    return np.array([p[1], p[0], p[2]])


def _interp_dir(path, md: float) -> Tuple[float, float]:
    """The inclination and azimuth on the arc at a measured depth."""
    from . import well3d as W3
    md = min(max(md, float(path.md[0])), float(path.md[-1]))
    k = min(int(np.searchsorted(path.md, md, 'right')) - 1, len(path.md) - 2)
    t1, t2 = W3._tangent(path.inc[k], path.azi[k]), W3._tangent(path.inc[k + 1], path.azi[k + 1])
    b = math.acos(max(-1.0, min(1.0, float(np.dot(t1, t2)))))
    f = (md - path.md[k]) / max(path.md[k + 1] - path.md[k], 1e-9)
    t = t1 if b < 1e-9 else (math.sin((1 - f) * b) * t1 + math.sin(f * b) * t2) / math.sin(b)
    inc = math.degrees(math.acos(max(-1.0, min(1.0, float(t[2])))))
    az = math.degrees(math.atan2(float(t[1]), float(t[0]))) % 360.0
    return inc, az


def _closest(plan, dense: np.ndarray, dmd: np.ndarray, q: np.ndarray) -> Tuple[float, np.ndarray]:
    """The plan's measured depth closest to a point (NEV), on the segments between its dense points."""
    d = dense[1:] - dense[:-1]
    L2 = np.einsum('ij,ij->i', d, d)
    t = np.where(L2 > 0, np.einsum('ij,ij->i', q - dense[:-1], d) / np.where(L2 > 0, L2, 1.0), 0.0).clip(0.0, 1.0)
    Q = dense[:-1] + t[:, None] * d
    j = int(np.argmin(np.linalg.norm(Q - q, axis=1)))
    m = float(dmd[j] + t[j] * (dmd[j + 1] - dmd[j]))
    return m, _nev(plan.at(m))


def arc_to(p0: np.ndarray, t0: np.ndarray, h0: np.ndarray, r0: np.ndarray, q: np.ndarray) -> dict:
    """The one circular arc from p0 heading t0 that reaches q: its dogleg, length, severity (deg/30 m) and toolface."""
    d = q - p0
    L = float(np.linalg.norm(d))
    if L < 1e-6:
        return {'dogleg_deg': 0.0, 'length_m': 0.0, 'dls_deg_30m': 0.0, 'toolface_deg': None}
    u = d / L
    phi = math.acos(max(-1.0, min(1.0, float(np.dot(t0, u)))))
    theta = 2.0 * phi
    arc = L if phi < 1e-9 else L * phi / math.sin(phi)
    perp = u - float(np.dot(u, t0)) * t0
    tf = None if float(np.linalg.norm(perp)) < 1e-9 else math.degrees(math.atan2(float(np.dot(perp, r0)), float(np.dot(perp, h0)))) % 360.0
    return {'dogleg_deg': round(math.degrees(theta), 3), 'length_m': round(arc, 2), 'dls_deg_30m': round(math.degrees(theta) / arc * 30.0, 3) if arc > 0 else 0.0,
            'toolface_deg': None if tf is None else round(tf, 1)}


def compare(actual: dict, plan: dict, field: dict, program: List[dict], bit_offset_m: float = 15.0, lookahead_m: float = 30.0) -> dict:
    """actual and plan: {'md', 'incl', 'azi' (the frame's azimuths, degrees), 'slot' [x, y], 'ref_elev_m'}; field:
    {declination_deg, dip_deg, total_nT}; the azimuths are taken as true (the frame is true north at its centre)."""
    from . import well3d as W3
    from . import wellpos as WP
    A = W3.Path(actual['md'], actual['incl'], actual['azi'], actual['slot'][0], actual['slot'][1], actual['ref_elev_m'], 0.0)
    P = W3.Path(plan['md'], plan['incl'], plan['azi'], plan['slot'][0], plan['slot'][1], plan['ref_elev_m'], 0.0)
    dn = P.dense(1.0)
    dense = np.stack([np.asarray(dn['y']), np.asarray(dn['x']), np.asarray(dn['z'])], axis=1)
    dmd = np.asarray(dn['md'], dtype=np.float64)
    u = WP.program_uncertainty(A.md, A.inc, A.azi, A.tvd, field['declination_deg'], field['dip_deg'], field['total_nT'], program)
    rows = []
    for k in range(len(A.md)):
        a = np.array([A.y[k], A.x[k], A.z[k]])
        m, q = _closest(P, dense, dmd, a)
        pi_, pa_ = _interp_dir(P, m)
        t, h, r = _frame(pi_, pa_)
        d = a - q
        C = u['cov'][k]
        sh, sr = math.sqrt(max(float(h @ C @ h), 0.0)), math.sqrt(max(float(r @ C @ r), 0.0))
        hi, ri = float(d @ h), float(d @ r)
        rows.append({'md_m': round(float(A.md[k]), 2), 'inc_deg': round(float(A.inc[k]), 3), 'azi_deg': round(float(A.azi[k]), 3),
                     'tvd_m': round(float(A.tvd[k]), 2), 'dls_deg_30m': round(float(A.dls[k]), 3),
                     'plan_md_m': round(m, 2), 'plan_inc_deg': round(pi_, 3), 'plan_azi_deg': round(pa_, 3),
                     'distance_m': round(float(np.linalg.norm(d)), 3), 'high_m': round(hi, 3), 'right_m': round(ri, 3),
                     'sigma_high_m': round(sh, 3), 'sigma_right_m': round(sr, 3),
                     'n_sigma': round(math.hypot(hi / sh if sh > 1e-9 else 0.0, ri / sr if sr > 1e-9 else 0.0), 2),
                     'd_inc_deg': round(float(A.inc[k]) - pi_, 3), 'd_azi_deg': round((float(A.azi[k]) - pa_ + 180.0) % 360.0 - 180.0, 3)})
    # the bit: the last interval's build and turn held for the survey-to-bit offset
    n = len(A.md)
    dm = float(A.md[-1] - A.md[-2]) if n > 1 else 0.0
    br = (float(A.inc[-1] - A.inc[-2]) / dm) if dm > 0 else 0.0
    tr = (((float(A.azi[-1] - A.azi[-2]) + 180.0) % 360.0 - 180.0) / dm) if dm > 0 else 0.0
    md_b = float(A.md[-1]) + bit_offset_m
    inc_b = min(max(float(A.inc[-1]) + br * bit_offset_m, 0.0), 180.0)
    az_b = (float(A.azi[-1]) + tr * bit_offset_m) % 360.0
    B = W3.Path(list(A.md) + [md_b], list(A.inc) + [inc_b], list(A.azi) + [az_b], actual['slot'][0], actual['slot'][1], actual['ref_elev_m'], 0.0)
    b = np.array([B.y[-1], B.x[-1], B.z[-1]])
    mb, qb = _closest(P, dense, dmd, b)
    pib, pab = _interp_dir(P, mb)
    tb_, hb_, rb_ = _frame(pib, pab)
    db = b - qb
    t0, h0, r0 = _frame(inc_b, az_b)
    m_t = min(mb + lookahead_m, float(P.md[-1]))
    if m_t - mb < 1.0:                                   # nothing of the plan left ahead of the bit
        steer = {'dogleg_deg': None, 'length_m': None, 'dls_deg_30m': None, 'toolface_deg': None,
                 'note': "the bit is at or past the plan's TD: no plan ahead to steer back to"}
    else:
        steer = arc_to(b, t0, h0, r0, _nev(P.at(m_t)))
        steer['note'] = ''
    steer.update({'to_plan_md_m': round(m_t, 2), 'lookahead_m': round(m_t - mb, 2)})
    bit = {'md_m': round(md_b, 2), 'inc_deg': round(inc_b, 3), 'azi_deg': round(az_b, 3), 'tvd_m': round(float(B.tvd[-1]), 2),
           'build_deg_30m': round(br * 30.0, 3), 'turn_deg_30m': round(tr * 30.0, 3), 'offset_m': bit_offset_m,
           'plan_md_m': round(mb, 2), 'distance_m': round(float(np.linalg.norm(db)), 3), 'high_m': round(float(db @ hb_), 3), 'right_m': round(float(db @ rb_), 3),
           'past_plan_td': mb >= float(P.md[-1]) - 0.5}
    worst = max(rows, key=lambda x: x['distance_m']) if rows else None
    return {'rows': rows, 'last': rows[-1] if rows else None, 'bit': bit, 'steer': steer, 'worst': worst,
            'worst_sigma': max(rows, key=lambda x: x['n_sigma']) if rows else None,
            'plan_td_md_m': round(float(P.md[-1]), 2), 'actual_td_md_m': round(float(A.md[-1]), 2), 'model': u['model'],
            'views': {'plan': {'n': [round(float(v), 2) for v in dense[::10, 0]], 'e': [round(float(v), 2) for v in dense[::10, 1]], 'z': [round(float(v), 2) for v in dense[::10, 2]]},
                      'actual': {'n': [round(float(v), 2) for v in A.y], 'e': [round(float(v), 2) for v in A.x], 'z': [round(float(v), 2) for v in A.z]},
                      'bit': {'n': round(float(b[0]), 2), 'e': round(float(b[1]), 2), 'z': round(float(b[2]), 2)},
                      'slot': {'n': round(float(actual['slot'][1]), 2), 'e': round(float(actual['slot'][0]), 2)}}}


# -- the page -----------------------------------------------------------------------------------------------------------
def _field_at(ws, w) -> Tuple[dict, str]:
    from . import geomag as GM
    s = w.get('surface') or {}
    when = None
    try:
        from . import drill_string as DS
        when = DS._cache(ws, w['id'], build=False).meta.get('start_time')
    except Exception:
        pass
    f = GM.field(float(s.get('lat')), float(s.get('lon')), 0.0, when or datetime.now(timezone.utc).date())
    return ({'declination_deg': round(f['declination_deg'], 3), 'dip_deg': round(f['dip_deg'], 3), 'total_nT': round(f['total_nT'], 1)},
            f"WMM2025 at the well for {f['decimal_year']:.2f}" + (f" ({f['note']})" if f['note'] else ''))


def page(ws, well: Optional[str] = None) -> dict:
    from . import well3d as W3
    from . import wellpos as WP
    sc = W3.workspace_scene(ws)
    act = {w['id']: w for w in sc['wells']}
    pln = {w['id']: w for w in sc['planned']}
    wl = []
    for w in ws.wells():
        st = well_settings(ws, w['id'])
        wl.append({'id': w['id'], 'display': w.get('display') or w['id'], 'survey': bool(w.get('trajectory')), 'plan': w.get('plan'),
                   'placed': w['id'] in act or w['id'] in pln, 'program': st['program'], 'program_declared': st['program_declared'],
                   'program_source': st['source'], 'bit_offset_m': st['bit_offset_m'], 'lookahead_m': st['lookahead_m']})
    choice = well if well in {x['id'] for x in wl} else next((x['id'] for x in wl if x['id'] in act and x['id'] in pln), wl[0]['id'] if wl else None)
    out = {'title': ws.manifest.get('name') or 'this workspace', 'wells': wl, 'well': choice, 'models': WP.model_list(), 'defaults': DEFAULTS,
           'not_a_measurement': NOT_A_MEASUREMENT, 'comparison': None, 'detail': ''}
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    why = next((x.get('why') for x in sc['not_placed'] if x['id'] == choice), None)
    if choice not in act or choice not in pln:
        out['detail'] = why or ('this well has no plan attached' if choice in act else 'this well has no survey yet: the plan alone is drawn in Collision')
        return out
    st = well_settings(ws, choice)
    fld, note = _field_at(ws, ws.well(choice))
    a, p = act[choice], pln[choice]
    c = compare({'md': a['stations']['md'], 'incl': a['stations']['incl'], 'azi': a['stations']['azi'], 'slot': a['slot'], 'ref_elev_m': a['ref_elev_m']},
                {'md': p['stations']['md'], 'incl': p['stations']['incl'], 'azi': p['stations']['azi'], 'slot': p['slot'], 'ref_elev_m': p['ref_elev_m']},
                fld, st['program'], st['bit_offset_m'], st['lookahead_m'])
    c.update({'field': fld, 'field_note': note, 'survey_source': a['source'], 'plan_source': p['source'], 'azimuth_note': a['azimuth']['applied'],
              'plan_azimuth_note': p['azimuth']['applied']})
    out['comparison'] = c
    return out


def text(r: dict) -> str:
    c = r.get('comparison')
    L = [f"Plan against actual - {r['title']}, well {r.get('well')}"]
    if not c:
        L.append('  ' + (r.get('detail') or 'nothing to compare'))
        return '\n'.join(L)
    la, b, s = c['last'], c['bit'], c['steer']
    L.append(f"  survey to {c['actual_td_md_m']:,.1f} m MD, plan to {c['plan_td_md_m']:,.1f} m MD; tools: {c['model']}")
    L.append(f"  at the last survey ({la['md_m']:,.1f} m): {la['distance_m']:.2f} m from the plan, {abs(la['high_m']):.2f} m {'high' if la['high_m'] >= 0 else 'low'}, "
             f"{abs(la['right_m']):.2f} m {'right' if la['right_m'] >= 0 else 'left'} ({la['n_sigma']:.1f} sigma)")
    L.append(f"  the bit projected to {b['md_m']:,.1f} m (build {b['build_deg_30m']:+.2f}, turn {b['turn_deg_30m']:+.2f} deg/30 m): {b['distance_m']:.2f} m from the plan, "
             f"{abs(b['high_m']):.2f} m {'high' if b['high_m'] >= 0 else 'low'}, {abs(b['right_m']):.2f} m {'right' if b['right_m'] >= 0 else 'left'}")
    if s['dls_deg_30m'] is None:
        L.append('  ' + s['note'])
    else:
        L.append(f"  back to the plan {s['lookahead_m']:.0f} m ahead: {s['dls_deg_30m']:.2f} deg/30 m over {s['length_m']:.1f} m" +
                 (f", toolface {s['toolface_deg']:.0f}" if s['toolface_deg'] is not None else ''))
    w = c['worst']
    L.append(f"  furthest from the plan: {w['distance_m']:.2f} m at {w['md_m']:,.1f} m MD")
    return '\n'.join(L)


def build_report(ws, version: str, well: str):
    from . import client_reports as CR
    r = page(ws, well)
    c = r['comparison']
    if not c:
        raise ValueError(r.get('detail') or 'nothing to compare')
    now = datetime.now(timezone.utc)
    rid = f"PVA-{now.strftime('%Y%m%dT%H%M%SZ')}"
    disp = next(x['display'] for x in r['wells'] if x['id'] == well)
    la, b, s = c['last'], c['bit'], c['steer']
    front = [['Report ID', rid], ['Well', disp], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['At the last survey', f"{la['md_m']:,.1f} m MD: {la['distance_m']:.2f} m from the plan ({la['n_sigma']:.1f} sigma)"],
             ['The bit, projected', f"{b['md_m']:,.1f} m MD: {b['distance_m']:.2f} m from the plan"],
             ['Back to the plan', s['note'] if s['dls_deg_30m'] is None else
              f"{s['dls_deg_30m']:.2f} deg/30 m" + (f" at toolface {s['toolface_deg']:.0f}" if s['toolface_deg'] is not None else '')
              + f" to reach it {s['lookahead_m']:.0f} m ahead"]]
    s1 = CR.Section('1', 'The survey and the plan', [f"Survey: {c['survey_source']} ({c['azimuth_note']}). Plan: {c['plan_source']} ({c['plan_azimuth_note']}). "
                                                     f"Survey tools: {c['model']}. Field: {c['field']['declination_deg']:+.2f} deg declination, {c['field']['dip_deg']:.2f} deg dip, "
                                                     f"{c['field']['total_nT']:,.0f} nT ({c['field_note']})."], [])
    s2 = CR.Section('2', 'Station by station', ['High (+) / low (-) and right (+) / left (-) are in the plan\'s own frame at its closest point; sigma is the survey\'s '
                                                'standard deviation in that direction.'], [])
    s2.tables.append(CR.Table(['MD (m)', 'Inc', 'Azi', 'TVD (m)', 'Plan MD (m)', 'Distance (m)', 'High/low (m)', 'Right/left (m)', 'Sigma h / r (m)', 'Sigmas'],
                              [[f"{x['md_m']:,.1f}", f"{x['inc_deg']:.2f}", f"{x['azi_deg']:.2f}", f"{x['tvd_m']:,.1f}", f"{x['plan_md_m']:,.1f}", f"{x['distance_m']:.2f}",
                                f"{x['high_m']:+.2f}", f"{x['right_m']:+.2f}", f"{x['sigma_high_m']:.2f} / {x['sigma_right_m']:.2f}", f"{x['n_sigma']:.1f}"] for x in c['rows']]))
    s3 = CR.Section('3', 'The bit and the way back', [
        f"Projected from the last survey by {b['offset_m']:g} m holding the last interval's build ({b['build_deg_30m']:+.2f} deg/30 m) and turn "
        f"({b['turn_deg_30m']:+.2f} deg/30 m): {b['inc_deg']:.2f} deg inclination, {b['azi_deg']:.2f} deg azimuth, {b['tvd_m']:,.1f} m TVD; "
        f"{b['distance_m']:.2f} m from the plan. " + (s['note'] + '.' if s['dls_deg_30m'] is None else
        f"One arc reaches the plan at {s['to_plan_md_m']:,.1f} m MD: {s['dogleg_deg']:.2f} deg over {s['length_m']:.1f} m, "
        f"{s['dls_deg_30m']:.2f} deg/30 m" + (f", toolface {s['toolface_deg']:.0f} deg from high side." if s['toolface_deg'] is not None else '.'))], [])
    s4 = CR.Section('4', 'What this will not call a measurement', ['; '.join(r['not_a_measurement']) + '.'], [])
    return CR.Document(f"Plan against actual - {disp}", rid, front, [s1, s2, s3, s4],
                       footer='Error models: ISCWSA MWD (Rev 4, Rev 5.11) and the OWSG tool-code sets. Field: WMM2025 (NCEI/BGS).',
                       data={'report_id': rid, 'well': well, **{k: v for k, v in c.items() if k != 'views'}})


def write_report(ws, version: str, well: str, actor: str = 'system') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well)
    d = os.path.join(ws.reports_dir, 'wells', well)
    os.makedirs(d, exist_ok=True)
    paths = CR.write(doc, d, 'plan_vs_actual')
    ws.audit(actor, 'report.plan_vs_actual', {'well': well, 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


def decode_content(body: dict) -> bytes:
    if body.get('content_b64'):
        return base64.b64decode(body['content_b64'])
    return str(body.get('content') or '').encode('utf-8')
