# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""interval - the declared disposal interval against the mapped formation tops.

The second geological statement this program makes about a well, and the one a regulator reviewing a
disposal well in a seismic response area asks first: where does the injection interval sit against the
geology? The operator declares the interval (top and base, feet, true vertical depth below a reference
elevation the operator states - the kelly bushing, the ground, the derrick floor - with where that elevation
came from); the layers the site brought give the formation tops as contour brackets at the well's position
(`structure`: read, not interpolated). This module puts the two side by side:

- the interval is converted to subsea elevation through the declared reference: subsea = reference - depth,
  so a depth of 6,000 ft from a kelly bushing at 2,950 ft is -3,050 ft subsea; a measured depth is refused
  (this program converts true vertical depth only; a deviated well's MD needs its survey);
- for each formation-top layer with a bracket at the well, the interval is ABOVE the mapped top (its base is
  shallower than the shallowest the top can be), BELOW it (its top is deeper than the deepest the top can be),
  or NOT RESOLVED at the map's resolution (the bracket overlaps the interval - the map cannot place the top
  on one side of it);
- a formation with a top layer and an isopach layer gets a base bracket by arithmetic the report shows:
  base is between (top_low - thickness_high) and (top_high - thickness_low);
- the declared depth tier (shallow or deep by the base of the formation the SRA names) is CONSISTENT with the
  map, INCONSISTENT with it, or NOT RESOLVED - and that is the statement the Commission's review turns on.

What it will not do: interpolate a top between two contours; add a thickness the map does not state; take a
kelly-bushing depth from a ground elevation without saying the offset is unstated; call the tier right - it
says whether the declaration and the map agree, and the operator's completion report is the record.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

REF_DATUMS = ('KB', 'GL', 'DF')        # kelly bushing, ground level, derrick floor
DEPTH_REFS = ('TVD', 'MD')
LAYER_ROLES = ('top', 'isopach', 'base')


def subsea_ft(depth_ft: float, ref_elev_ft: float) -> float:
    """Subsea elevation (ft, negative below sea level) of a true vertical depth below the reference elevation."""
    return round(float(ref_elev_ft) - float(depth_ft), 1)


def bracket_of(res: dict) -> Optional[Tuple[float, float]]:
    """The (low, high) elevation bracket a contour reading gives: the two contours the position lies between,
    or the one it sits on (a bracket of zero width). None when the reading gives neither."""
    if not res or res.get('status') != 'OK':
        return None
    if res.get('on') is not None:
        v = float(res['on'])
        return (v, v)
    if res.get('between'):
        a, b = res['between']
        return (min(float(a), float(b)), max(float(a), float(b)))
    return None


def compare(top_ss: float, base_ss: float, bracket: Tuple[float, float], what: str = 'top') -> dict:
    """Where the interval (subsea top and base, top the shallower) sits against a formation-top bracket."""
    lo, hi = bracket
    top_ss, base_ss = max(top_ss, base_ss), min(top_ss, base_ss)
    if base_ss > hi:
        return {'relation': 'ABOVE', 'detail': f'the interval base ({base_ss:,.0f} ft) is shallower than the shallowest the mapped {what} can be ({hi:,.0f} ft)'}
    if top_ss < lo:
        return {'relation': 'BELOW', 'detail': f'the interval top ({top_ss:,.0f} ft) is deeper than the deepest the mapped {what} can be ({lo:,.0f} ft)'}
    return {'relation': 'NOT RESOLVED', 'detail': f'the mapped {what} lies between {lo:,.0f} and {hi:,.0f} ft and the interval ({top_ss:,.0f} to {base_ss:,.0f} ft) '
                                               f'overlaps that bracket - the map cannot place the {what} on one side of the interval'}


def base_bracket(top: Tuple[float, float], thickness: Tuple[float, float]) -> Tuple[float, float]:
    """The base of a formation from its top bracket and its thickness bracket: the arithmetic the report shows."""
    return (round(top[0] - thickness[1], 1), round(top[1] - thickness[0], 1))


def tier_check(tier: Optional[str], top_ss: float, base_ss: float, base_br: Tuple[float, float]) -> dict:
    """The declared tier against the mapped base: 'deep' means completed below the base of the formation,
    'shallow' above it. Subsea elevations: deeper is more negative."""
    lo, hi = base_br
    top_ss, base_ss = max(top_ss, base_ss), min(top_ss, base_ss)
    if tier not in ('shallow', 'deep'):
        return {'status': 'NOT DECLARED', 'detail': 'no depth tier declared (set-well --depth-tier shallow|deep)'}
    whole_below = top_ss < lo            # every foot of the interval deeper than the deepest the base can be
    whole_above = base_ss > hi           # every foot shallower than the shallowest the base can be
    if tier == 'deep':
        if whole_below:
            return {'status': 'CONSISTENT', 'detail': f'declared deep; the whole interval is below the mapped base bracket ({lo:,.0f} to {hi:,.0f} ft)'}
        if whole_above:
            return {'status': 'INCONSISTENT', 'detail': f'declared deep, but the whole interval ({top_ss:,.0f} to {base_ss:,.0f} ft) is above the mapped base bracket ({lo:,.0f} to {hi:,.0f} ft)'}
    else:
        if whole_above:
            return {'status': 'CONSISTENT', 'detail': f'declared shallow; the whole interval is above the mapped base bracket ({lo:,.0f} to {hi:,.0f} ft)'}
        if whole_below:
            return {'status': 'INCONSISTENT', 'detail': f'declared shallow, but the whole interval ({top_ss:,.0f} to {base_ss:,.0f} ft) is below the mapped base bracket ({lo:,.0f} to {hi:,.0f} ft)'}
    return {'status': 'NOT RESOLVED', 'detail': f'declared {tier}; the mapped base bracket ({lo:,.0f} to {hi:,.0f} ft) overlaps the interval ({top_ss:,.0f} to {base_ss:,.0f} ft) - the map cannot say'}


def formation_named(boundary: str, formations: List[str]) -> Optional[str]:
    """Which declared formation the SRA's boundary text names ('base of the Wolfcamp' -> 'Wolfcamp')."""
    b = (boundary or '').lower()
    for f in sorted(formations, key=len, reverse=True):
        if f and re.search(r'\b' + re.escape(f.lower()) + r'\b', b):
            return f
    return None


def statement(well: dict, structure: dict, layer_records: List[dict], formation_boundary: Optional[str] = None) -> dict:
    """The interval against the mapped tops for one well. `structure` is the well's structure result (every layer's
    reading at the position); `layer_records` the workspace's layer records (role and formation per layer)."""
    disp = well.get('disposal') or {}
    surf = well.get('surface') or {}
    elev = surf.get('elevation') or {}
    out = {'well': well.get('id'), 'status': 'OK', 'declared': {}, 'tops': [], 'formations': [], 'tier': None, 'notes': [], 'lines': []}
    top_ft, base_ft = disp.get('top_interval_ft'), disp.get('base_interval_ft')
    ref = disp.get('depth_reference')
    missing = []
    if top_ft is None or base_ft is None:
        missing.append('the interval (set-well --top-interval-ft --base-interval-ft)')
    if elev.get('ft') is None:
        missing.append('the reference elevation (set-well --ref-elev-ft --ref-elev-datum KB|GL|DF --ref-elev-source ..., or --elev-3dep)')
    if ref not in DEPTH_REFS:
        missing.append('the depth reference (set-well --depth-ref TVD|MD)')
    if missing:
        out['status'] = 'NOT DECLARED'
        out['notes'].append('not declared: ' + '; '.join(missing))
        out['lines'].append('Interval against the mapped tops: NOT DECLARED - ' + '; '.join(missing) + '.')
        return out
    if ref == 'MD':
        out['status'] = 'NOT RESOLVED'
        out['notes'].append('the interval is declared in measured depth; this program converts true vertical depth only - a deviated well needs its survey, a vertical one may be declared TVD')
        out['lines'].append('Interval against the mapped tops: NOT RESOLVED - the interval is declared in measured depth; declare true vertical depth (set-well --depth-ref TVD) or convert it with the survey first.')
        return out
    top_ss, base_ss = subsea_ft(top_ft, elev['ft']), subsea_ft(base_ft, elev['ft'])
    out['declared'] = {'top_ft': float(top_ft), 'base_ft': float(base_ft), 'depth_reference': ref, 'ref_elev_ft': float(elev['ft']),
                       'ref_datum': elev.get('datum'), 'ref_source': elev.get('source', ''), 'top_subsea_ft': top_ss, 'base_subsea_ft': base_ss,
                       'depth_tier': disp.get('depth_tier')}
    if elev.get('datum') == 'GL':
        out['notes'].append('the reference is the ground elevation; a depth measured from the kelly bushing is deeper by the KB height, which is not stated - '
                            'declare the KB elevation from the completion report when the brackets are close')
    if structure.get('status') != 'OK':
        out['status'] = structure.get('status', 'NO LAYER')
        out['notes'].append(structure.get('note', ''))
        out['lines'].append(f"Interval against the mapped tops: {out['status']} - {structure.get('note', '')}")
        return out
    recs = {r['id']: r for r in layer_records}
    by_formation: Dict[str, dict] = {}
    for L in structure.get('layers', []):
        rec = recs.get(L.get('layer_id'), {})
        if (rec.get('kind') or L.get('layer_kind')) != 'contours':
            continue
        br = bracket_of(L)
        role = rec.get('role') or 'top'
        fm = rec.get('formation') or ''
        row = {'layer_id': L.get('layer_id'), 'layer': L.get('layer_display') or L.get('layer'), 'formation': fm, 'role': role, 'unit': L.get('unit') or 'ft',
               'bracket': list(br) if br else None, 'status': L.get('status')}
        if br is None:
            row['relation'] = 'NO BRACKET'
            row['detail'] = (L.get('lines') or [L.get('note', '')])[0]
        elif role == 'isopach':
            row['relation'] = 'THICKNESS'
            row['detail'] = f'thickness between {br[0]:,.0f} and {br[1]:,.0f} {row["unit"]}'
        else:
            row.update(compare(top_ss, base_ss, br))
        out['tops'].append(row)
        if fm and br is not None:
            by_formation.setdefault(fm, {})[role] = br
    for fm, parts in by_formation.items():
        f = {'formation': fm, 'top': list(parts['top']) if 'top' in parts else None, 'thickness': list(parts['isopach']) if 'isopach' in parts else None, 'base': None}
        if 'base' in parts:
            f['base'] = list(parts['base']); f['base_how'] = 'a base layer'
        elif 'top' in parts and 'isopach' in parts:
            bb = base_bracket(parts['top'], parts['isopach'])
            f['base'] = list(bb)
            f['base_how'] = (f"top ({parts['top'][0]:,.0f} to {parts['top'][1]:,.0f}) minus thickness ({parts['isopach'][0]:,.0f} to {parts['isopach'][1]:,.0f}): "
                             f"base between {bb[0]:,.0f} and {bb[1]:,.0f} ft")
        if f['base']:
            f['interval_vs_base'] = compare(top_ss, base_ss, tuple(f['base']), what='base')
        out['formations'].append(f)
    named = formation_named(formation_boundary or '', list(by_formation))
    if formation_boundary:
        fm = next((f for f in out['formations'] if f['formation'] == named), None)
        if fm and fm.get('base'):
            out['tier'] = {'formation': named, 'boundary': formation_boundary, **tier_check(disp.get('depth_tier'), top_ss, base_ss, tuple(fm['base']))}
        else:
            out['tier'] = {'formation': named, 'boundary': formation_boundary, 'status': 'NO MAPPED BASE',
                           'detail': (f"no base bracket for the {named} at this well (a top layer and an isopach layer, or a base layer, declared with add-layer --formation-name --role)"
                                      if named else f"no layer is declared for the formation the boundary names ({formation_boundary})")}
    # the lines as the well page and the packet print them
    d = out['declared']
    out['lines'].append(f"Declared interval {d['top_ft']:,.0f} to {d['base_ft']:,.0f} ft {ref} below the {d['ref_datum']} at {d['ref_elev_ft']:,.0f} ft"
                        + (f" ({d['ref_source']})" if d['ref_source'] else '') + f": {top_ss:,.0f} to {base_ss:,.0f} ft subsea.")
    for row in out['tops']:
        if row['relation'] in ('ABOVE', 'BELOW'):
            out['lines'].append(f"{row['relation']} the mapped {row['formation'] + ' ' if row['formation'] else ''}top of {row['layer']}: {row['detail']}.")
        elif row['relation'] == 'NOT RESOLVED':
            out['lines'].append(f"NOT RESOLVED against {row['layer']}: {row['detail']}.")
        elif row['relation'] == 'THICKNESS':
            out['lines'].append(f"{row['layer']} ({row['formation']} isopach): {row['detail']}.")
        else:
            out['lines'].append(f"{row['layer']}: no bracket at this position - {row['detail']}")
    for f in out['formations']:
        if f.get('base'):
            out['lines'].append(f"{f['formation']} base: {f['base_how']}; the interval is {f['interval_vs_base']['relation']} it - {f['interval_vs_base']['detail']}.")
    if out['tier']:
        out['lines'].append(f"Depth tier against the map ({out['tier']['boundary']}): {out['tier']['status']} - {out['tier']['detail']}.")
    for n in out['notes']:
        out['lines'].append('Note: ' + n + '.')
    out['lines'].append('A contour map is read, not interpolated: every bracket above is two contours, and the completion report is the record of the interval.')
    if not out['tops']:
        out['status'] = 'NO TOPS'
        out['lines'].insert(1, 'No formation-top layer gives a bracket at this well (a contours layer inside whose extent the well lies).')
    return out


def events_vs_faults(events: List[dict], layer, type_attr: Optional[str] = None, name_attr: Optional[str] = None, radius_km: float = 20.0) -> dict:
    """Each catalogue event against the mapped faults of one structure layer: the nearest mapped fault trace to the
    epicentre, its distance and bearing; the counts within 1, 2 and 5 km of any mapped fault; the trace with the most
    events within 2 km. A surface trace against an epicentre: the hypocentral depth is not used, and a distance to a
    mapped trace at the map's scale is a fact of the map, not an attribution."""
    from . import structure as ST
    rows, near = [], {}
    for e in events:
        la, lo = e.get('lat'), e.get('lon')
        if la is None or lo is None:
            rows.append({'event_id': e.get('event_id'), 'time': e.get('time'), 'magnitude': e.get('magnitude'), 'status': 'NO POSITION'})
            continue
        r = ST.nearest(layer, float(la), float(lo), radius_km, type_attr, name_attr, n_list=1)
        nf = r.get('nearest_fault')
        row = {'event_id': e.get('event_id'), 'time': e.get('time'), 'magnitude': e.get('magnitude'), 'lat': la, 'lon': lo, 'depth_km': e.get('depth_km'),
               'status': r.get('status'), 'fault': None}
        if nf:
            row['fault'] = {'name': nf.get('name'), 'type': nf.get('type'), 'distance_km': nf.get('distance_km'), 'bearing_deg': nf.get('bearing_deg')}
            key = nf.get('name') or nf.get('type') or 'unnamed'
            if nf['distance_km'] <= 2.0:
                near[key] = near.get(key, 0) + 1
        rows.append(row)
    dists = [r['fault']['distance_km'] for r in rows if r.get('fault')]
    out = {'layer': layer.name, 'radius_km': radius_km, 'n_events': len(events), 'n_placed': len(dists),
           'within_1km': sum(1 for d in dists if d <= 1.0), 'within_2km': sum(1 for d in dists if d <= 2.0), 'within_5km': sum(1 for d in dists if d <= 5.0),
           'no_fault_within_radius': sum(1 for r in rows if r.get('status') == 'OK' and not r.get('fault')),
           'outside_extent': sum(1 for r in rows if r.get('status') == 'OUTSIDE LAYER EXTENT'),
           'most_events_within_2km': (max(near.items(), key=lambda kv: kv[1]) if near else None), 'events': rows}
    out['lines'] = [f"{out['n_placed']} of {out['n_events']} events have a mapped fault within {radius_km:g} km in {layer.name}: {out['within_1km']} within 1 km, "
                    f"{out['within_2km']} within 2 km, {out['within_5km']} within 5 km"
                    + (f"; {out['no_fault_within_radius']} with none within the radius" if out['no_fault_within_radius'] else '')
                    + (f"; {out['outside_extent']} outside the layer's extent" if out['outside_extent'] else '') + '.']
    if out['most_events_within_2km']:
        k, n = out['most_events_within_2km']
        out['lines'].append(f"The trace with the most events within 2 km: {k} ({n}).")
    out['lines'].append('An epicentre against a surface trace at the map\'s scale: the depth is not used, and proximity is not attribution.')
    return out
