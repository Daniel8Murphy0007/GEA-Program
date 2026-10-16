"""The client well report: one document per well, and one for the pad, assembled from what the workspace already
holds - the drilling record, the depth events and who confirmed them, the well's declared identity and position,
what the mapped structure says at that position, the well path from its directional survey, and the declared cost
against the market while it was drilled. Nothing is computed here that the program does not compute elsewhere; this
module gathers, draws and states where each number came from. Where a part is missing, the report says what would
supply it instead of leaving a blank that reads as zero.

Every drawing is SVG made from the numbers in the report's own tables, written beside it and inline in the HTML,
with a caption and the words a reader gets when the drawing cannot be seen. The rendered report passes the same
vocabulary guard as every other client report.
"""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# the light steps of the categorical palette, in their fixed order, and the neutral inks - a printed page is white
SLOTS = ('#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300')
INK, INK2, MUTED, GRID = '#141412', '#3d3c37', '#77756e', '#e3e1d8'
FONT = 'font-family="Segoe UI,Arial,sans-serif"'


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _nice(v: float) -> float:
    if not v or not math.isfinite(v):
        return 1.0
    p = 10 ** math.floor(math.log10(abs(v)))
    f = abs(v) / p
    return (1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10) * p


def _ticks(lo: float, hi: float, n: int = 5) -> List[float]:
    st = _nice((hi - lo) / n)
    t = math.ceil(lo / st) * st
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10)); t += st
    return out


def _tl(t: float, ticks: List[float]) -> str:
    st = (ticks[1] - ticks[0]) if len(ticks) > 1 else 1.0
    return f'{t:,.{0 if st >= 1 else 1 if st >= 0.1 else 2}f}'


def _f(v, d=0) -> str:
    return '—' if v is None else f'{v:,.{d}f}'


# -- the drawings ----------------------------------------------------------------------------------
def svg_progress(lines: List[dict], title: str) -> str:
    """Depth against days: one line per well, depth increasing downward. `lines`: [{'label', 'days': [], 'depth': []}]."""
    W, H, L, R, T, B = 760, 340, 70, 130, 30, 42
    xs = [x for l in lines for x in l['days'] if x is not None]; ys = [y for l in lines for y in l['depth'] if y is not None]
    x0, x1 = 0.0, max(xs) if xs else 1.0
    y0, y1 = min(ys + [0.0]), max(ys) if ys else 1.0
    sx = lambda x: L + (x - x0) / ((x1 - x0) or 1) * (W - L - R)
    sy = lambda y: T + (y - y0) / ((y1 - y0) or 1) * (H - T - B)
    g = [f'<text x="{L}" y="18" {FONT} font-size="13" font-weight="600" fill="{INK}">{_e(title)}</text>']
    for t in _ticks(y0, y1):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="{GRID}"/><text x="{L - 6}" y="{sy(t) + 4:.1f}" {FONT} font-size="11" fill="{INK2}" text-anchor="end">{t:,.0f}</text>')
    tx = _ticks(x0, x1)
    for t in tx:
        g.append(f'<text x="{sx(t):.1f}" y="{H - 22}" {FONT} font-size="11" fill="{INK2}" text-anchor="middle">{_tl(t, tx)}</text>')
    g.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" {FONT} font-size="11" fill="{MUTED}" text-anchor="middle">days since the first sample</text>')
    g.append(f'<text x="14" y="{(T + H - B) / 2:.0f}" {FONT} font-size="11" fill="{MUTED}" transform="rotate(-90 14 {(T + H - B) / 2:.0f})" text-anchor="middle">measured depth, ft</text>')
    for i, l in enumerate(lines):
        c = SLOTS[i % len(SLOTS)]
        pts, d = [(x, y) for x, y in zip(l['days'], l['depth']) if x is not None and y is not None], ''
        for k, (x, y) in enumerate(pts):
            d += ('L' if k else 'M') + f'{sx(x):.1f} {sy(y):.1f}'
        if pts:
            g.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2" stroke-linejoin="round"/>')
            ex, ey = pts[-1]
            g.append(f'<circle cx="{sx(ex):.1f}" cy="{sy(ey):.1f}" r="3.5" fill="{c}"/>')
        # the key: one row per well beside the plot, in the line's colour (end labels would collide on a pad)
        g.append(f'<rect x="{W - R + 14}" y="{T + 4 + i * 18}" width="14" height="3" fill="{c}"/>'
                 f'<text x="{W - R + 34}" y="{T + 9 + i * 18}" {FONT} font-size="11" fill="{INK}">{_e(l["label"])}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(g)}</svg>'


def svg_plan(e: List[float], n: List[float], title: str, unit: str = 'ft') -> str:
    """The plan view: east against north from the slot, one scale for both, north up."""
    W, H, M = 380, 380, 52
    e0, e1, n0, n1 = min(min(e), 0.0), max(max(e), 0.0), min(min(n), 0.0), max(max(n), 0.0)
    span = max(e1 - e0, n1 - n0, 1.0) * 1.16                     # one scale for both axes, each centred on its own range
    ce, cn = (e0 + e1) / 2, (n0 + n1) / 2
    k_ = (W - 2 * M) / span
    sx = lambda v: W / 2 + (v - ce) * k_
    sy = lambda v: H / 2 - (v - cn) * k_
    g = [f'<text x="{M}" y="18" {FONT} font-size="13" font-weight="600" fill="{INK}">{_e(title)}</text>']
    tx, ty = _ticks(ce - span / 2, ce + span / 2, 4), _ticks(cn - span / 2, cn + span / 2, 4)
    for t in tx:
        g.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{M}" y2="{H - M}" stroke="{GRID}"/>'
                 f'<text x="{sx(t):.1f}" y="{H - M + 14}" {FONT} font-size="10" fill="{INK2}" text-anchor="middle">{_tl(t, tx)}</text>')
    for t in ty:
        g.append(f'<line x1="{M}" x2="{W - M}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="{GRID}"/>'
                 f'<text x="{M - 5}" y="{sy(t) + 3:.1f}" {FONT} font-size="10" fill="{INK2}" text-anchor="end">{_tl(t, ty)}</text>')
    d = ''.join(('L' if k else 'M') + f'{sx(x):.1f} {sy(y):.1f}' for k, (x, y) in enumerate(zip(e, n)))
    g.append(f'<path d="{d}" fill="none" stroke="{SLOTS[0]}" stroke-width="2"/>')
    g.append(f'<circle cx="{sx(0):.1f}" cy="{sy(0):.1f}" r="4" fill="{INK}"/><text x="{sx(0) + 6:.1f}" y="{sy(0) - 6:.1f}" {FONT} font-size="10" fill="{INK}">slot</text>')
    g.append(f'<circle cx="{sx(e[-1]):.1f}" cy="{sy(n[-1]):.1f}" r="4" fill="{SLOTS[0]}"/><text x="{sx(e[-1]) + 6:.1f}" y="{sy(n[-1]) + 12:.1f}" {FONT} font-size="10" fill="{INK}">TD</text>')
    g.append(f'<text x="{W - M}" y="{H - 8}" {FONT} font-size="10" fill="{MUTED}" text-anchor="end">east, {unit}</text><text x="10" y="{M - 8}" {FONT} font-size="10" fill="{MUTED}">north, {unit}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(g)}</svg>'


def svg_section(vs: List[float], tvd: List[float], title: str, azimuth: float, bands: List[dict], unit: str = 'ft') -> str:
    """The vertical section: displacement along one azimuth against true vertical depth (down), with the mapped tops at
    the surface location drawn as bands where the map brackets them."""
    W, H, L, R, T, B = 560, 380, 64, 150, 30, 40
    x0, x1 = min(vs + [0.0]), max(vs + [0.0]); y0, y1 = 0.0, max(tvd + [b['to'] for b in bands] + [1.0])
    pad = (x1 - x0) * 0.06 or 1.0; x0 -= pad; x1 += pad
    sx = lambda v: L + (v - x0) / ((x1 - x0) or 1) * (W - L - R)
    sy = lambda v: T + (v - y0) / ((y1 - y0) or 1) * (H - T - B)
    g = [f'<text x="{L}" y="18" {FONT} font-size="13" font-weight="600" fill="{INK}">{_e(title)}</text>']
    for i, b in enumerate(bands):
        g.append(f'<rect x="{L}" y="{sy(b["from"]):.1f}" width="{W - L - R}" height="{max(1.5, sy(b["to"]) - sy(b["from"])):.1f}" fill="{SLOTS[(i + 1) % len(SLOTS)]}" opacity=".22"/>'
                 f'<text x="{W - R + 6}" y="{sy((b["from"] + b["to"]) / 2) + 4:.1f}" {FONT} font-size="10" fill="{INK}">{_e(b["label"])}</text>')
    for t in _ticks(y0, y1):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="{GRID}"/><text x="{L - 6}" y="{sy(t) + 4:.1f}" {FONT} font-size="10" fill="{INK2}" text-anchor="end">{t:,.0f}</text>')
    for t in _ticks(x0, x1, 4):
        g.append(f'<text x="{sx(t):.1f}" y="{H - 22}" {FONT} font-size="10" fill="{INK2}" text-anchor="middle">{t:,.0f}</text>')
    d = ''.join(('L' if k else 'M') + f'{sx(x):.1f} {sy(y):.1f}' for k, (x, y) in enumerate(zip(vs, tvd)))
    g.append(f'<path d="{d}" fill="none" stroke="{SLOTS[0]}" stroke-width="2"/>')
    g.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" {FONT} font-size="10" fill="{MUTED}" text-anchor="middle">displacement along {azimuth:.1f}°, {unit}</text>'
             f'<text x="12" y="{(T + H - B) / 2:.0f}" {FONT} font-size="10" fill="{MUTED}" transform="rotate(-90 12 {(T + H - B) / 2:.0f})" text-anchor="middle">true vertical depth, {unit}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(g)}</svg>'


def svg_market(dates: List[str], values: List[float], name: str, unit: str, windows: List[dict]) -> str:
    W, H, L, R, T, B = 760, 220, 60, 16, 30, 34
    from datetime import date as _d
    t = [_d.fromisoformat(x).toordinal() for x in dates]
    t0, t1 = min(t), max(t)
    lo, hi = min(values), max(values); p = (hi - lo) * 0.08 or 1.0; lo -= p; hi += p
    sx = lambda v: L + (v - t0) / ((t1 - t0) or 1) * (W - L - R)
    sy = lambda v: T + (1 - (v - lo) / ((hi - lo) or 1)) * (H - T - B)
    g = [f'<text x="{L}" y="18" {FONT} font-size="13" font-weight="600" fill="{INK}">{_e(name)} ({_e(unit)}), daily</text>']
    for i, w in enumerate(windows):
        a, b = sx(_d.fromisoformat(w['from']).toordinal()), sx(_d.fromisoformat(w['to']).toordinal())
        g.append(f'<rect x="{a:.1f}" y="{T}" width="{max(2.0, b - a):.1f}" height="{H - T - B}" fill="{INK}" opacity=".08"/>'
                 f'<text x="{(a + b) / 2:.1f}" y="{H - B - 6 - (i % 3) * 12}" {FONT} font-size="10" fill="{INK2}" text-anchor="middle">{_e(w["label"])}</text>')
    for v in _ticks(lo, hi, 4):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" stroke="{GRID}"/><text x="{L - 6}" y="{sy(v) + 4:.1f}" {FONT} font-size="10" fill="{INK2}" text-anchor="end">{v:,.2f}</text>')
    seen = set()
    for x, d in zip(t, dates):
        if d[:7] not in seen and d[8:10] <= '07':
            seen.add(d[:7])
            g.append(f'<text x="{sx(x):.1f}" y="{H - 12}" {FONT} font-size="10" fill="{INK2}" text-anchor="middle">{d[:7]}</text>')
    d, prev = '', None
    for k, (x, v) in enumerate(zip(t, values)):
        d += ('M' if (k == 0 or x - prev > 6) else 'L') + f'{sx(x):.1f} {sy(v):.1f}'; prev = x
    g.append(f'<path d="{d}" fill="none" stroke="{SLOTS[0]}" stroke-width="2"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(g)}</svg>'


# -- gathering -------------------------------------------------------------------------------------
def _record(ws, wid: str) -> Optional[dict]:
    p = os.path.join(ws.reports_dir, 'wells', wid, 'drilling_record.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _progress_line(ws, wid: str, rec: dict) -> Tuple[dict, str]:
    """Depth against days for the drawing: the running bottom from the chart cache when it is built, else the sections'
    opening and deepest depths (the record's own table)."""
    from . import series_cache as SCH
    try:
        c = SCH.load(SCH.cache_dir(ws, wid), SCH.source_path(ws, wid))
        p = SCH.progress(c) if c is not None else None
    except Exception:
        p = None
    if p:
        return {'days': p['days'], 'depth': p['bottom_max']}, 'the running bottom of the hole from the depth pass (stale and re-referenced depths excluded)'
    t0 = datetime.fromisoformat(rec['start'])
    days, depth = [], []
    for s in rec.get('sections', []):
        if s.get('hole_depth_at_start_ft') is None:
            continue
        days += [(datetime.fromisoformat(s['start']) - t0).total_seconds() / 86400.0, (datetime.fromisoformat(s['end']) - t0).total_seconds() / 86400.0]
        depth += [s['hole_depth_at_start_ft'], s['hole_depth_to_ft']]
    return {'days': days, 'depth': depth}, "each section's opening and deepest depth from the drilling record (the chart cache, which draws every sample, is not built yet: a refresh builds it)"


def _site_of(ws, wid: str) -> Optional[dict]:
    for s in ws.sites():
        if wid in (s.get('wells') or []):
            return s
    return None


def _path(ws, wid: str) -> Optional[dict]:
    from . import well3d as W3
    try:
        tj = W3.trajectory_of(ws, wid)
    except Exception:
        return None
    if not tj or len(tj['stations']) < 2:
        return None
    st = tj['stations']
    p = W3.Path([x['md'] for x in st], [x['incl'] for x in st], [x['azi'] for x in st], 0.0, 0.0, 0.0, 0.0)
    ft = 1 / 0.3048
    e, n, tvd = [v * ft for v in p.x], [v * ft for v in p.y], [v * ft for v in p.tvd]
    az = math.degrees(math.atan2(e[-1], n[-1])) % 360.0 if (e[-1] or n[-1]) else 0.0
    vs = [x * math.sin(math.radians(az)) + y * math.cos(math.radians(az)) for x, y in zip(e, n)]
    return {'meta': tj['meta'], 'md_ft': [v * ft for v in p.md], 'e': e, 'n': n, 'tvd': tvd, 'vs': vs, 'azimuth': az,
            'incl': [float(v) for v in p.inc], 'azi': [float(v) for v in p.azi], 'dls': [float(v) for v in p.dls]}


def _tops_at(ws, lat: float, lon: float, ref_elev_ft: Optional[float]) -> List[dict]:
    """The formation-top maps' brackets at the surface location, as depth bands below the declared reference (a map read,
    not interpolated: the top lies between the two contours, never at a number between them)."""
    from . import site_map as SM
    from . import structure as ST
    out = []
    if ref_elev_ft is None:
        return out
    for rec in ws.layers():
        if rec.get('kind') != 'contours' or (rec.get('role') or '').lower() not in ('top', 'base') or 'elev' not in (rec.get('value_attr') or '').lower() \
                or 'ft' not in (rec.get('value_attr') or '').lower():
            continue
        try:
            d = SM._layer(ws, rec['id'])
            r = ST.contour_at(d['L'], lat - d['dlat'], lon - d['dlon'], rec.get('value_attr'))
        except Exception:
            continue
        if r.get('status') != 'OK':
            continue
        if r.get('on') is not None:
            a = b = float(r['on'])
        elif r.get('between'):
            a, b = (float(v) for v in r['between'][:2])
        else:
            continue
        top, bot = sorted((ref_elev_ft - a, ref_elev_ft - b))
        out.append({'label': rec.get('formation') or rec.get('display') or rec['id'], 'from': top, 'to': bot, 'elev_bracket_ft': sorted((a, b)),
                    'layer': rec.get('display') or rec['id'], 'citation': rec.get('citation') or ''})
    out.sort(key=lambda b: b['from'])
    return out


def build_well(ws, well_id: str, program_version: str):
    """One well's report."""
    from .client_reports import Document, Section, Table, Figure, PROGRAM_NAME
    from . import economics as EC
    from . import setup_checklist as SC
    from . import site_map as SM
    w = ws.well(well_id)
    name = w.get('display') or well_id
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rid = f"WELL-{well_id}-{now.replace('-', '').replace(':', '')}"
    rec = _record(ws, well_id)
    site = _site_of(ws, well_id)
    surf = w.get('surface') or {}
    elev = surf.get('elevation') or {}
    placed = next((x for x in SM.wells(ws) if x['id'] == well_id and x['placed']), None)
    from . import ppdm as P
    api_in = P.declared_api(w)
    api = P.parse_well_number(api_in) if api_in else None
    api_txt = ('not declared' if api is None else api['api10'] + (f" ({api['state']['name']})" if (api.get('state') or {}).get('name') else '') if api['status'] == 'VALID'
               else f"{api_in} as declared - not a valid US Well Number: " + '; '.join(api['problems']))
    front = [['Report ID', rid], ['Well', name], ['US Well Number (API)', api_txt],
             ['Site', site['display'] if site else 'not assigned to a site'], ['Client', (site or {}).get('client') or 'not declared'],
             ['Surface location', (f"{surf['lat']:.6f}, {surf['lon']:.6f} ({surf.get('datum') or 'datum not stated'}), as declared" if surf.get('lat') is not None else 'not declared')],
             ['Reference elevation', (f"{elev['datum']} {elev['ft']:,.1f} ft ({elev.get('source', '')})" if elev.get('ft') is not None else 'not declared')],
             ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}']]
    secs: List = []
    missing: List[str] = []
    data = {'well_id': well_id, 'report_id': rid, 'generated_utc': now}
    if rec is None:
        secs.append(Section('1', 'The drilling record', ['There is no drilling record for this well: attach its drilling recorder export and refresh the '
                                                         'reports. The report says nothing about the drilling until then.']))
        missing.append('the drilling record')
    else:
        front += [['Source file', rec['source']], ['Recorded', f"{rec['start'][:16]} to {rec['end'][:16]} (the recorder's own clock)"]]
        days = (datetime.fromisoformat(rec['end']) - datetime.fromisoformat(rec['start'])).total_seconds() / 86400.0
        kv = [['Total depth (MD)', f"{_f(rec.get('total_depth_md_ft'), 1)} ft" + (f", reached {rec['reached_td'][:16]}" if rec.get('reached_td') else '')],
              ['TVD at total depth (the recorder\'s)', f"{_f(rec.get('tvd_at_td_ft'), 1)} ft" if rec.get('tvd_at_td_ft') is not None else 'not written by the recorder'],
              ['Feet drilled', f"{_f(rec.get('feet_drilled'), 1)} ft"], ['Days on the record', f'{days:,.1f}'],
              ['Feet per day', _f(rec.get('feet_drilled') / days if rec.get('feet_drilled') and days else None, 0)],
              ['Hours on bottom', _f(rec.get('on_bottom_hours'), 2)], ['Feet per hour on bottom', _f(rec.get('feet_per_on_bottom_hour'), 1)],
              ['Sections', str(rec.get('n_sections'))], ['Boreholes', '; '.join(f"{b['from_ft']:,.0f} to {b['deepest_ft']:,.0f} ft, {b['status'].lower()}" for b in rec.get('boreholes', [])) or '—']]
        secs.append(Section('1', 'The well in numbers', [l for l in rec.get('lines', [])[:2]], [Table(['Measure', 'Value'], kv, "From the drilling record: the recorder's own numbers, read as delivered")]))
        line, how = _progress_line(ws, well_id, rec)
        fig = Figure('well_report_progress', svg_progress([{'label': name, **line}], f'{name}: depth against days'),
                     f'Measured depth against days since the first sample: {how}.',
                     f"{name} drilled from {rec.get('first_hole_depth_ft') or 0:,.0f} to {rec.get('total_depth_md_ft') or 0:,.0f} ft measured depth over {days:,.1f} days on the record")
        secs.append(Section('2', 'Drilling progress', [], [Table(['#', 'Start', 'End', 'Hours', 'From (ft)', 'To (ft)', 'Feet drilled', 'On bottom (h)', 'ROP on bottom, mean (ft/h)', 'Connections'],
                                                                 [[s['n'], s['start'][:16], s['end'][:16], _f(s['hours'], 2), _f(s.get('hole_depth_at_start_ft'), 1), _f(s.get('hole_depth_to_ft'), 1), _f(s.get('feet_drilled'), 1),
                                                                   _f(s['on_bottom_hours'], 2), _f(s.get('rop_on_bottom_mean_ft_h'), 1), s.get('connections')] for s in rec.get('sections', [])],
                                                                 'One row per section of the recording')], [fig]))
        ev = rec.get('depth_events') or []
        conf = SC.confirmations(ws).get(f'events:{well_id}')
        para = [f"{len(ev)} place(s) where the recorder's hole depth could not be taken at its word; the last column says what the drilling "
                f"record did with each, and the footage above is counted after it." if ev else
                "The recorder's hole depth was taken at its word throughout: no stale opening, re-reference, sidetrack or correction was found."]
        para.append(f"Checked by {conf['by']} on {conf['utc'][:10]} against: {conf['note']}." if conf else
                    'Not yet checked by a person against the daily drilling reports (the Setup checklist asks for it).')
        if not conf and ev:
            missing.append('a person\'s check of the depth events')
        secs.append(Section('3', 'Depth events', para, [Table(['Type', 'At', 'From (ft)', 'To (ft)', 'What the record did'],
                                                              [[x.get('type'), str(x.get('at', ''))[:16], _f(x.get('from_ft'), 1), _f(x.get('to_ft'), 1), x.get('effect') or '']
                                                               for x in ev], "From the drilling record, on the recorder's clock")] if ev else []))
    # location and structure
    n = len(secs) + 1
    if placed:
        wh = SM.what_here(ws, placed['lat'], placed['lon'])
        lines = [f"Drawn at {placed['lat']:.6f}, {placed['lon']:.6f} (WGS84)" + (f", moved {placed['shift_m']:.0f} m from the declared {placed['declared']['datum']} by its datum shift" if placed.get('shift_m') else '') + '.']
        rows = [[l['layer_display'], ' '.join(l['lines'] or [])] for l in wh['layers'] if l['status'] == 'OK' and l['lines']]
        near = [[x['display'], f"{x['distance_km']:,.2f} km", f"{x['bearing_deg']:.0f}°" if x.get('bearing_deg') is not None else '—'] for x in wh['wells'] if x['id'] != well_id]
        tabs = [Table(['Layer', 'What it says at the surface location'], rows, 'Each mapped layer\'s own statement; a contour map is read as the two contours the location lies between')] if rows else []
        if near:
            tabs.append(Table(['Placed well', 'Distance', 'Bearing'], near, 'From this well\'s surface location'))
        secs.append(Section(str(n), 'Location and the mapped structure', lines, tabs))
        data['what_here'] = wh
    else:
        secs.append(Section(str(n), 'Location and the mapped structure', ['The surface location is not declared, so the well is not placed and the mapped '
                                                                            'structure is not read at it (Setup declares it, with its datum).']))
        missing.append('the surface location')
    # the well path
    n = len(secs) + 1
    pth = _path(ws, well_id)
    if pth:
        m = pth['meta']
        bands = _tops_at(ws, placed['lat'], placed['lon'], elev.get('ft')) if placed else []
        figs = [Figure('well_report_plan', svg_plan(pth['e'], pth['n'], f'{name}: plan view'),
                       f"East and north of the slot, on the survey's {m['azimuth_reference']} north{(' (' + m['grid'] + ')') if m.get('grid') else ''}, by the minimum-curvature method.",
                       f"The path ends {math.hypot(pth['e'][-1], pth['n'][-1]):,.0f} ft from the slot toward {pth['azimuth']:.0f} degrees"),
                Figure('well_report_section', svg_section(pth['vs'], pth['tvd'], f'{name}: vertical section', pth['azimuth'], bands),
                       'True vertical depth against displacement toward the bottom hole' + ('; the bands are the mapped tops at the surface location, between the '
                                                                                           'two contours the location lies between, below the declared reference.' if bands else '.'),
                       f"The path reaches {pth['tvd'][-1]:,.0f} ft true vertical depth")]
        secs.append(Section(str(n), 'The well path', [f"From the directional survey attached on {m['attached_utc'][:10]} by {m['attached_by']} ({m['source']}): "
                                                      f"{m['stations']} stations, azimuths {m['azimuth_reference']}" + (f" on {m['grid']}" if m.get('grid') else '') + '.'],
                            [Table(['MD (ft)', 'Inclination (°)', 'Azimuth (°)', 'TVD (ft)', 'North (ft)', 'East (ft)', 'Dogleg (°/30 m)'],
                                   [[_f(a, 1), _f(b, 2), _f(c, 2), _f(d, 1), _f(e_, 1), _f(f_, 1), _f(g_, 2)]
                                    for a, b, c, d, e_, f_, g_ in zip(pth['md_ft'], pth['incl'], pth['azi'], pth['tvd'], pth['n'], pth['e'], pth['dls'])],
                                   'Minimum curvature from the survey stations')] +
                            ([Table(['Mapped top', 'Depth below the reference (ft)', 'Contours it lies between (ft, elevation)', 'Layer'],
                                    [[b['label'], f"{b['from']:,.0f} to {b['to']:,.0f}", f"{b['elev_bracket_ft'][0]:,.0f} and {b['elev_bracket_ft'][1]:,.0f}", b['layer']] for b in bands],
                                    'At the surface location; a well that deviates meets each top elsewhere')] if bands else []), figs))
    else:
        secs.append(Section(str(n), 'The well path', ['No directional survey is attached, so the path is not drawn. The Railroad Commission\'s imaged well '
                                                      'records are the public place a Texas survey is filed; attach it with gea trajectory.']))
        missing.append('the directional survey')
    # economics
    n = len(secs) + 1
    if rec is not None:
        ser = {}
        for mm in EC.series_list(ws):
            if mm['id'] in EC.BUILTIN:
                try:
                    ser[mm['id']] = EC.read_series(ws, mm['id'])
                except Exception:
                    pass
        we = EC.well_economics(ws, w, EC.costs(ws)['wells'].get(well_id, {}), ser)
        para, tabs, figs = [], [], []
        c = we.get('cost')
        if c:
            para.append('The cost below is the operator\'s declaration - the day rates over the recorded time plus the items - not a measurement.'
                        + ('' if c['complete'] else f" It is incomplete: {c['why_incomplete']}, so it is not divided by the footage."))
            tabs.append(Table(['Cost', 'USD'], [['Cost (declared)', f"${c['usd']:,.0f}"], ['Per foot', f"${c['per_ft']:,.2f}" if c['per_ft'] else '—'],
                                         ['Per day', f"${c['per_day']:,.0f}" if c['per_day'] else '—'], ['AFE', f"${c['afe_usd']:,.0f}" if c.get('afe_usd') else 'not declared'],
                                         ['Against the AFE', f"{c['vs_afe_pct']:+.1f} %" if c.get('vs_afe_pct') is not None else '—']], 'The cost basis and its sources are in the workspace\'s audit log'))
            tabs.append(Table(['Declared', 'Source'], [[f"${r['usd_per_day']:,.2f}/day {r['what']}, " + (f"{r['from'] or 'the start'} to {r['to'] or 'the end'}" if (r.get('from') or r.get('to')) else 'over the whole record'), r['source']] for r in we['basis']['rates']] +
                              [[f"${i['usd']:,.2f} {i['what']}" + (f" (section {i['section']})" if i.get('section') else ''), i['source']] for i in we['basis']['items']], 'Every rate and item, as declared'))
        else:
            para.append('No cost basis is declared for this well, so no cost is reported.')
            missing.append('the cost basis (day rates)')
        mk = []
        for sid, (d, v, meta) in ser.items():
            x = we['market'].get(sid)
            if x:
                mk.append([meta['name'], meta['unit'], f"{x['mean']:,.2f}", f"{x['min']:,.2f} to {x['max']:,.2f}", x['n']])
                a, b = rec['start'][:10], rec['end'][:10]
                from datetime import date as _d, timedelta as _td
                lo, hi = (_d.fromisoformat(a) - _td(days=45)).isoformat(), (_d.fromisoformat(b) + _td(days=45)).isoformat()
                pts = [(dd, vv) for dd, vv in zip(d, v) if lo <= dd <= hi]
                if len(pts) > 2:
                    figs.append(Figure(f'well_report_{sid}', svg_market([p[0] for p in pts], [p[1] for p in pts], meta['name'], meta['unit'], [{'from': a, 'to': b, 'label': 'drilling'}]),
                                       f"{meta['name']}, daily, with the drilling record shaded; context, not a cause of the cost. {meta['source']}.",
                                       f"{meta['name']} averaged {x['mean']:,.2f} {meta['unit']} over the {x['n']} trading days of the drilling record"))
        if mk:
            tabs.append(Table(['Series', 'Unit', 'Mean while drilling', 'Range', 'Trading days'], mk, 'U.S. Energy Information Administration spot prices'))
        secs.append(Section(str(n), 'Cost and the market', para, tabs, figs))
        data['economics'] = we
    n = len(secs) + 1
    secs.append(Section(str(n), 'What this report will not say',
                        ['A declared number - the location, the elevation, the well number, a rate, an item, the AFE - is reported as declared, with '
                         'where it came from, and is not measured here. A well path between survey stations follows the minimum-curvature arc. A mapped top '
                         'is read as the two contours the location lies between, never as a number between them. A price is context, not a cause.'] +
                        ([f"Not in this report yet, and what would supply it: {', '.join(missing)}."] if missing else [])))
    data['missing'] = missing
    return Document(title=f'{PROGRAM_NAME} - Well Report', report_id=rid, front=front, sections=secs,
                    footer='Assembled from the workspace as it stands; every number traceable to the drilling record, the survey or a declaration.',
                    data=data | {'_records': [], '_catalogue_obj': None})


def build_pad(ws, program_version: str):
    """The pad: every well's numbers side by side, their progress on one chart, the cost table, and what each still lacks."""
    from .client_reports import Document, Section, Table, Figure, PROGRAM_NAME
    from . import economics as EC
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    site = ws.manifest.get('name') or 'the workspace'
    rid = f"PAD-{now.replace('-', '').replace(':', '')}"
    e = EC.economics(ws)
    rows, lines, cost_rows = [], [], []
    for w in ws.wells():
        rec = _record(ws, w['id'])
        if rec is None:
            rows.append([w.get('display') or w['id'], '—', '—', '—', '—', '—', 'no drilling record'])
            continue
        days = (datetime.fromisoformat(rec['end']) - datetime.fromisoformat(rec['start'])).total_seconds() / 86400.0
        rows.append([w.get('display') or w['id'], rec['start'][:10], _f(rec.get('total_depth_md_ft'), 0), _f(rec.get('feet_drilled'), 0), f'{days:,.1f}',
                     _f(rec['feet_drilled'] / days if rec.get('feet_drilled') and days else None, 0), _f(rec.get('feet_per_on_bottom_hour'), 1)])
        line, _ = _progress_line(ws, w['id'], rec)
        lines.append({'label': w.get('display') or w['id'], **line})
    lacks = []
    for w in ws.wells():
        try:
            m = build_well(ws, w['id'], program_version).data.get('missing') or []
        except Exception as ex:                                  # one well that cannot be assembled does not stop the pad
            m = [f'the report could not be assembled ({type(ex).__name__})']
        lacks.append([w.get('display') or w['id'], '; '.join(m) or 'nothing'])
    for we in e['wells']:
        if we.get('record'):
            c = we.get('cost') or {}
            cost_rows.append([we['display'], f"${c['usd']:,.0f}" if c else 'not declared', f"${c['per_ft']:,.2f}" if c.get('per_ft') else '—',
                              f"${c['per_day']:,.0f}" if c.get('per_day') else '—', (f"{we['market']['wti']['mean']:,.2f}" if (we['market'].get('wti') or {}).get('mean') is not None else '—')])
    figs = [Figure('pad_report_progress', svg_progress(lines, f'{site}: depth against days'), 'Measured depth against days since each well\'s first sample, one line per well.',
                   'Drilling progress of ' + ', '.join(f"{l['label']} to {max([d for d in l['depth'] if d is not None] or [0]):,.0f} ft" for l in lines))] if lines else []
    secs = [Section('1', 'The wells', [], [Table(['Well', 'First sample', 'Total depth (ft)', 'Feet drilled', 'Days on the record', 'Feet per day', 'Feet per hour on bottom'], rows,
                                                  'From each well\'s drilling record')], figs),
            Section('2', 'Cost and the market', ['Costs are the operator\'s declarations (day rates over each well\'s recorded time plus the items), not measurements; '
                                                 + e['correlation']['note'] + '.'],
                    [Table(['Well', 'Cost (declared)', 'Per foot', 'Per day', 'WTI while drilling (USD/bbl, mean)'], cost_rows, e['attribution'])]),
            Section('3', 'What each well\'s report still lacks', ['Each well\'s own report carries its sources, its depth events, its location and path; '
                                                                 'what it cannot say yet, and what would supply it, is listed here.'],
                    [Table(['Well', 'Not in its report yet'], lacks, 'Supplied through Setup, the well page and Economics')]),
            Section('4', 'What this report will not say', ['A declared number is reported as declared, with where it came from. A price is context, not a cause.'])]
    return Document(title=f'{PROGRAM_NAME} - Pad Report', report_id=rid,
                    front=[['Report ID', rid], ['Site', site], ['Wells', str(len(ws.wells()))], ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}']],
                    sections=secs, footer='Assembled from the workspace as it stands.',
                    data={'report_id': rid, 'generated_utc': now, 'wells': [r[0] for r in rows], 'lacks': {r[0]: r[1] for r in lacks}, '_records': [], '_catalogue_obj': None})


def write_well(ws, well_id: str, program_version: str, actor: str = 'system') -> dict:
    from .client_reports import write
    doc = build_well(ws, well_id, program_version)
    paths = write(doc, os.path.join(ws.reports_dir, 'wells', well_id), basename='well_report')
    ws.audit(actor, 'report.well', {'well': well_id, 'report_id': doc.report_id, 'missing': doc.data.get('missing', [])})
    return {'report_id': doc.report_id, 'paths': paths, 'missing': doc.data.get('missing', [])}


def write_pad(ws, program_version: str, actor: str = 'system') -> dict:
    from .client_reports import write
    doc = build_pad(ws, program_version)
    paths = write(doc, ws.reports_dir, basename='pad_report')
    ws.audit(actor, 'report.pad', {'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}
