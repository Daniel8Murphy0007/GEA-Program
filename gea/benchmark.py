# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""benchmark - the wells of a pad against one another, and each against the best the pad has done.

Every well with a drilling record is measured the same way, from its own record: how deep, in how many days, at
what rate on bottom, how long its connections took, how fast it tripped. The wells are then ranked on each count,
and each is set against the PAD'S BEST: the well it would have been had every stretch of it been drilled at the
rate of whichever well was fastest through that stretch, and every connection made in the pad's best quarter.

- THE COUNTS, per well: the footage and the on-bottom hours (hole a sidetrack abandoned left out); the on-bottom
  rate, rotary and slide apart, and the slide share of the footage; the days from the first stand drilled to the
  last and the feet per day over them; the share of those days spent on bottom; the drilling connections with
  their median and quartiles weight to weight; the median speed of the trips in and out.
- THE RANKS: 1 is the best of the pad on that count (the fastest, the shortest, the most), ties sharing a rank;
  a count a well's record cannot give is left blank and unranked.
- THE PAD'S BEST. The hole is cut into the roadmap's stretches (1,000 ft of TVD above the heel, 1,000 ft along
  the lateral). In each stretch every well has its own rate there - its footage over its on-bottom hours - and
  the best is the fastest well's with 300 ft or more drilled in the stretch. A well's COMPOSITE on-bottom time
  is its footage in each stretch at that stretch's best rate, summed; the GAP is its own on-bottom time less
  that. For connections, the gap is each connection's time beyond the pad's 25th-percentile connection, summed.
  The two gaps are hours the pad has already shown can be saved - on some well, in that stretch - not a forecast.
- THE CURVES: depth against days since the first stand, for every well on one chart.

What it will not call a measurement: a cause - a well can be fastest through a stretch for its bit, its motor,
its mud or its hole size, none of which is in the record; a like-for-like comparison where the wells differ in
diameter or direction (the page gives each well's year and deviation where the record has them, and no more);
the gap as money or as a promise; and days, where a record has gaps - the days are the recorder's clock from
the first stand to the last, whatever happened between.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

MIN_STRETCH_FT = 300.0
COUNTS = (('feet_per_day', 'feet per day, first stand to last', 'ft/day', True), ('rop_ft_h', 'on-bottom rate', 'ft/h', True), ('rotary_rop_ft_h', 'rotary rate', 'ft/h', True),
          ('slide_rop_ft_h', 'slide rate', 'ft/h', True), ('on_bottom_share', 'share of the days spent on bottom', '', True), ('w2w_median_min', 'median connection, weight to weight', 'min', False),
          ('trip_in_ft_h', 'median trip in', 'ft/h', True), ('trip_out_ft_h', 'median trip out', 'ft/h', True), ('gap_share', 'on-bottom time beyond the pad\'s best', '', False))


class BenchmarkError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def well_counts(rs: dict, ws_: dict) -> dict:
    """One well's counts from its rig time (`rig_state.well`) and its stands (`roadmap.well_stands`)."""
    st = ws_['stands']
    feet = float(st['feet'].sum()); hours = float(st['hours'].sum())
    out: Dict[str, Any] = {'well': ws_['well'], 'display': ws_['display'], 'stands': int(st['n']), 'drilled_ft': round(feet, 0), 'on_bottom_h': round(hours, 1),
                           'rop_ft_h': round(feet / hours, 1) if hours > 0 else None, 'md_from_ft': None, 'md_to_ft': None, 'days': None, 'feet_per_day': None, 'on_bottom_share': None}
    if st['n']:
        t_first = float(st['t_end'][0]) - float(st['hours'][0]) * 3600.0
        days = (float(st['t_end'].max()) - t_first) / 86400.0
        out.update(md_from_ft=round(float(st['md'][0] - st['stand_ft'] / 2), 0), md_to_ft=round(float(st['md'][-1] + st['stand_ft'] / 2), 0), days=round(days, 2),
                   feet_per_day=round(feet / days, 0) if days > 0 else None, on_bottom_share=round(hours / (days * 24.0), 3) if days > 0 else None)
    f = rs.get('footage') or {}
    tot = (f.get('rotary_ft') or 0.0) + (f.get('slide_ft') or 0.0)
    out.update(rotary_rop_ft_h=f.get('rotary_rop_ft_h'), slide_rop_ft_h=(f.get('slide_rop_ft_h') if (f.get('slide_ft') or 0.0) >= 100.0 else None),
               slide_share=round((f.get('slide_ft') or 0.0) / tot, 3) if tot > 0 else None)
    w2w = np.asarray([c['w2w_min'] for c in rs.get('connections') or []], dtype=np.float64)
    out.update(connections=int(len(w2w)), w2w_median_min=round(float(np.median(w2w)), 2) if len(w2w) >= 5 else None, w2w_p25_min=round(float(np.percentile(w2w, 25)), 2) if len(w2w) >= 5 else None,
               w2w_p75_min=round(float(np.percentile(w2w, 75)), 2) if len(w2w) >= 5 else None, _w2w=w2w)
    for d, key in (('IN', 'trip_in_ft_h'), ('OUT', 'trip_out_ft_h')):
        v = [x['speed_ft_h'] for x in rs.get('trips') or [] if x.get('direction') == d and x.get('speed_ft_h')]
        out[key] = round(float(np.median(v)), 0) if v else None
        out[key.replace('_ft_h', '_n')] = len(v)
    out['tvd'] = ws_.get('tvd_how') or 'none in the record'
    out['heel_md_ft'] = ws_.get('heel_md_ft')
    return out


def by_stretch(stands: Dict[str, dict]) -> Dict[tuple, Dict[str, dict]]:
    """Each well's footage, on-bottom hours and rate in each stretch (every stand, whatever its mode)."""
    out: Dict[tuple, Dict[str, dict]] = {}
    for wid, w in stands.items():
        st = w['stands']
        for i, key in enumerate(w['keys']):
            d = out.setdefault(key, {}).setdefault(wid, {'feet': 0.0, 'hours': 0.0})
            d['feet'] += float(st['feet'][i]); d['hours'] += float(st['hours'][i])
    for key, d in out.items():
        for wid, x in d.items():
            x['rop_ft_h'] = x['feet'] / x['hours'] if x['hours'] > 0 else None
    return out


def composite(stretches: Dict[tuple, Dict[str, dict]]) -> Tuple[Dict[tuple, dict], Dict[str, dict]]:
    """The best rate in each stretch (the fastest well with 300 ft or more there) and each well's on-bottom time at the
    best rates against its own."""
    best: Dict[tuple, dict] = {}
    for key, d in stretches.items():
        cand = [(x['rop_ft_h'], wid) for wid, x in d.items() if x['rop_ft_h'] and x['feet'] >= MIN_STRETCH_FT]
        if cand:
            r, wid = max(cand)
            best[key] = {'rop_ft_h': r, 'well': wid, 'wells': len(cand)}
    wells: Dict[str, dict] = {}
    for key, d in stretches.items():
        for wid, x in d.items():
            w = wells.setdefault(wid, {'actual_h': 0.0, 'composite_h': 0.0, 'feet': 0.0, 'alone_ft': 0.0, 'best_in': 0})
            b = best.get(key)
            if b is None or not x['rop_ft_h']:
                continue                                      # a stretch nobody drilled 300 ft of: no best to set it against
            w['actual_h'] += x['hours']; w['feet'] += x['feet']
            w['composite_h'] += x['feet'] / max(b['rop_ft_h'], x['rop_ft_h'])
            if b['wells'] == 1:
                w['alone_ft'] += x['feet']
            if b['well'] == wid:
                w['best_in'] += 1
    for w in wells.values():
        w['gap_h'] = max(0.0, w['actual_h'] - w['composite_h'])
        w['gap_share'] = w['gap_h'] / w['actual_h'] if w['actual_h'] > 0 else None
    return best, wells


def ranks(rows: List[dict]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for key, _, _, high in COUNTS:
        vals = sorted({r[key] for r in rows if r.get(key) is not None}, reverse=high)
        out[key] = {r['well']: vals.index(r[key]) + 1 for r in rows if r.get(key) is not None}
    return out


def page(ws) -> dict:
    from . import rig_state as RS
    from . import roadmap as RM
    out: Dict[str, Any] = {'status': 'OK', 'wells': [], 'ranks': {}, 'stretches': [], 'curves': {}, 'notes': [], 'lines': [], 'generated_utc': _utc(),
                           'counts': [{'key': k, 'words': w, 'unit': u, 'higher_is_better': h} for k, w, u, h in COUNTS], 'constants': {'min_stretch_ft': MIN_STRETCH_FT}}
    stands: Dict[str, dict] = {}; rows: List[dict] = []
    for wid in RS._wells_with_record(ws):
        try:
            sd = RM.well_stands(ws, wid)
            if sd['stands']['n'] == 0:
                out['notes'].append(f"{sd['display']}: no complete stand drilled in its record"); continue
            rows.append(well_counts(RS.well(ws, wid), sd)); stands[wid] = sd
        except (KeyError, ValueError, OSError) as e:
            out['notes'].append(f'{wid}: {e}')
    if not rows:
        out['status'] = 'EMPTY'
        out['lines'].append('Nothing to set side by side: no well in the workspace has a drilling record with stands drilled (add the wells\' drilling recorder exports on the Wells tab).')
        return out
    str_ = by_stretch(stands)
    best, comp = composite(str_)
    p25 = None
    allw = np.concatenate([r['_w2w'] for r in rows]) if any(len(r['_w2w']) for r in rows) else np.zeros(0)
    if len(allw) >= 8:
        p25 = float(np.percentile(allw, 25))
    for r in rows:
        c = comp.get(r['well'], {})
        w2w = r.pop('_w2w')
        r.update(composite_h=round(c.get('composite_h', 0.0), 1), compared_h=round(c.get('actual_h', 0.0), 1), gap_h=round(c.get('gap_h', 0.0), 1),
                 gap_share=(round(c['gap_share'], 3) if c.get('gap_share') is not None else None), compared_ft=round(c.get('feet', 0.0), 0), alone_ft=round(c.get('alone_ft', 0.0), 0),
                 best_in_stretches=int(c.get('best_in', 0)), connection_gap_h=(round(float(np.clip(w2w - p25, 0, None).sum()) / 60.0, 1) if p25 is not None and len(w2w) else None))
        sd = stands[r['well']]['stands']
        t0 = float(sd['t_end'][0]) - float(sd['hours'][0]) * 3600.0
        k = max(1, int(np.ceil(sd['n'] / 300.0)))
        out['curves'][r['well']] = [[round((float(sd['t_end'][i]) - t0) / 86400.0, 3), round(float(sd['md'][i] + sd['stand_ft'] / 2), 0)] for i in list(range(0, sd['n'], k)) + ([sd['n'] - 1] if (sd['n'] - 1) % k else [])]
    out['wells'] = rows
    out['ranks'] = ranks(rows) if len(rows) > 1 else {}
    out['pad_best_connection_min'] = round(p25, 2) if p25 is not None else None
    names = {r['well']: r['display'] for r in rows}
    for key in sorted(str_, key=RM._order):
        d = str_[key]; b = best.get(key)
        out['stretches'].append({'stretch': list(key), 'label': RM.stretch_label(key), 'best_well': (b or {}).get('well'), 'best_rop_ft_h': (round(b['rop_ft_h'], 1) if b else None),
                                 'by_well': {wid: {'feet': round(x['feet'], 0), 'hours': round(x['hours'], 2), 'rop_ft_h': (round(x['rop_ft_h'], 1) if x['rop_ft_h'] else None),
                                                   'of_best': (round(x['rop_ft_h'] / b['rop_ft_h'], 3) if b and x['rop_ft_h'] else None)} for wid, x in d.items()}})
    L = out['lines']
    L.append(f"{len(rows)} well(s) with a drilling record: " + '; '.join(f"{r['display']} {r['drilled_ft']:,.0f} ft in {r['days']:g} days ({r['feet_per_day']:,.0f} ft/day, {r['rop_ft_h']:g} ft/h on bottom)" for r in rows) + '.')
    if len(rows) > 1:
        for key, words, unit, high in COUNTS[:2] + COUNTS[5:6]:
            have = [r for r in rows if r.get(key) is not None]
            if len(have) > 1:
                b = (max if high else min)(have, key=lambda r: r[key]); w_ = (min if high else max)(have, key=lambda r: r[key])
                L.append(f"{words[0].upper() + words[1:]}: best {b['display']} at {b[key]:,g} {unit}, last {w_['display']} at {w_[key]:,g} {unit}.".replace(' .', '.'))
        tot_gap = sum(r['gap_h'] for r in rows); tot_h = sum(r['compared_h'] for r in rows)
        if tot_h > 0:
            L.append(f"Against the pad's best - each stretch at the rate of the fastest well through it - the wells spent {tot_gap:,.0f} of their {tot_h:,.0f} on-bottom hours beyond it ({tot_gap / tot_h * 100:.0f}%): "
                     + '; '.join(f"{r['display']} {r['gap_h']:,.0f} h ({(r['gap_share'] or 0) * 100:.0f}%), fastest in {r['best_in_stretches']} stretch(es)" for r in rows) + '.')
        if p25 is not None:
            L.append(f"Connections: the pad's best quarter is {p25:.1f} min weight to weight; the time beyond it is "
                     + '; '.join(f"{r['display']} {r['connection_gap_h']:g} h over {r['connections']}" for r in rows if r['connection_gap_h'] is not None) + '.')
        alone = [r for r in rows if r['alone_ft'] > 0]
        if alone:
            out['notes'].append('drilled where no other well drilled 300 ft of the same stretch, and so set against itself there: ' + '; '.join(f"{r['display']} {r['alone_ft']:,.0f} ft" for r in alone))
    else:
        out['notes'].append('one well with a record: its counts, and nothing to rank it against')
    L.append('The gaps are hours the pad has already shown can be saved, on some well in that stretch - not a forecast, and not a cause: the bit, the motor, the mud and the hole size are not in the record.')
    out['names'] = names
    from . import public_pad as _PP
    cr = _PP.credit(ws)
    if cr:
        out['public_source'] = cr
        L.append(cr['line'])
        if cr['reconstructed']:
            out['notes'].append(', '.join(cr['reconstructed']) + ': the bit depth of this record is reconstructed from the block and the slips, not recorded - exact on bottom, approximate over a trip')
    return out


def text(p: dict) -> str:
    out = ['Benchmark: the wells against one another', '=' * 40]
    out.extend(p['lines'])
    if p['wells']:
        out.append(f"  {'well':18s} {'drilled ft':>10s} {'days':>7s} {'ft/day':>7s} {'ft/h':>7s} {'rotary':>7s} {'slide':>7s} {'on btm':>7s} {'conn min':>9s} {'trip in':>8s} {'trip out':>8s} {'gap h':>7s} {'gap':>5s}")
        f = lambda v, d=0: ('-' if v is None else f'{v:,.{d}f}')
        for r in p['wells']:
            out.append(f"  {r['display'][:18]:18s} {f(r['drilled_ft']):>10s} {f(r['days'], 1):>7s} {f(r['feet_per_day']):>7s} {f(r['rop_ft_h'], 1):>7s} {f(r['rotary_rop_ft_h'], 1):>7s} {f(r['slide_rop_ft_h'], 1):>7s} "
                       f"{('-' if r['on_bottom_share'] is None else f'{r_pct(r):.0f}%'):>7s} {f(r['w2w_median_min'], 1):>9s} {f(r['trip_in_ft_h']):>8s} {f(r['trip_out_ft_h']):>8s} {f(r['gap_h']):>7s} "
                       f"{('-' if r['gap_share'] is None else f'{r_gap(r):.0f}%'):>5s}")
    for n in p.get('notes', []):
        out.append('  note: ' + n)
    return '\n'.join(out)


def r_pct(r: dict) -> float:
    return (r['on_bottom_share'] or 0.0) * 100.0


def r_gap(r: dict) -> float:
    return (r['gap_share'] or 0.0) * 100.0


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'benchmark')
    p['program_version'] = program_version
    with open(os.path.join(d, 'benchmark.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'benchmark.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Benchmark: the wells against one another\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p) + '\n```\n')
    with open(os.path.join(d, 'benchmark.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        keys = ['display', 'drilled_ft', 'days', 'feet_per_day', 'on_bottom_h', 'rop_ft_h', 'rotary_rop_ft_h', 'slide_rop_ft_h', 'slide_share', 'on_bottom_share', 'connections', 'w2w_p25_min', 'w2w_median_min',
                'w2w_p75_min', 'trip_in_ft_h', 'trip_out_ft_h', 'compared_h', 'composite_h', 'gap_h', 'gap_share', 'connection_gap_h', 'best_in_stretches']
        wr.writerow(keys)
        for r in p['wells']:
            wr.writerow([r.get(k, '') if r.get(k) is not None else '' for k in keys])
    with open(os.path.join(d, 'stretches.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        ids = [r['well'] for r in p['wells']]
        wr.writerow(['stretch', 'best_well', 'best_rop_ft_h'] + [f"{p['names'][w]}_{k}" for w in ids for k in ('feet', 'hours', 'rop_ft_h')])
        for s in p['stretches']:
            wr.writerow([s['label'], p['names'].get(s['best_well'], ''), s['best_rop_ft_h'] if s['best_rop_ft_h'] is not None else '']
                        + [((s['by_well'].get(w) or {}).get(k, '') if (s['by_well'].get(w) or {}).get(k) is not None else '') for w in ids for k in ('feet', 'hours', 'rop_ft_h')])
    files = ['benchmark.json', 'benchmark.md', 'benchmark.csv', 'stretches.csv']
    ws.audit(actor, 'benchmark.report', {'files': files, 'wells': [r['well'] for r in p['wells']]})
    return {'folder': d, 'files': files, 'page': p}


def selftest() -> dict:
    checks = []
    mk = lambda feet, hours: {'feet': feet, 'hours': hours, 'rop_ft_h': feet / hours}
    s = {('TVD', 1): {'A': mk(1000.0, 5.0), 'B': mk(1000.0, 10.0)}, ('TVD', 2): {'A': mk(1000.0, 20.0), 'B': mk(1000.0, 10.0)}, ('TVD', 3): {'A': mk(200.0, 1.0), 'B': mk(1000.0, 10.0)},
         ('LAT', 0): {'B': mk(1000.0, 8.0)}, ('LAT', 1): {'B': mk(100.0, 2.0)}}
    best, comp = composite(s)
    checks.append({'check': 'the best of each stretch is the fastest well with 300 ft there: a fast 200 ft does not set it', 'value': {str(k): (v['well'], v['rop_ft_h']) for k, v in best.items()},
                   'pass': best[('TVD', 1)]['well'] == 'A' and best[('TVD', 2)]['well'] == 'B' and best[('TVD', 3)]['well'] == 'B' and best[('TVD', 3)]['rop_ft_h'] == 100.0 and ('LAT', 1) not in best})
    checks.append({'check': 'a well\'s composite time is its footage at each stretch\'s best rate; a well faster than the best over a short stretch is not charged for it; a stretch with no best is left out of both sides',
                   'value': {k: (round(v['actual_h'], 2), round(v['composite_h'], 2), round(v['gap_h'], 2)) for k, v in comp.items()},
                   'pass': abs(comp['A']['actual_h'] - 26.0) < 1e-9 and abs(comp['A']['composite_h'] - (5.0 + 10.0 + 1.0)) < 1e-9 and abs(comp['B']['actual_h'] - 38.0) < 1e-9
                           and abs(comp['B']['composite_h'] - (5.0 + 10.0 + 10.0 + 8.0)) < 1e-9 and comp['B']['alone_ft'] == 2000.0 and comp['A']['alone_ft'] == 200.0 and comp['A']['best_in'] == 1 and comp['B']['best_in'] == 3})
    rk = ranks([{'well': 'A', 'rop_ft_h': 100.0, 'w2w_median_min': 6.0, 'feet_per_day': None}, {'well': 'B', 'rop_ft_h': 150.0, 'w2w_median_min': 6.0}, {'well': 'C', 'rop_ft_h': 80.0, 'w2w_median_min': 9.0}])
    checks.append({'check': 'the ranks: the fastest first, the shortest connection first, a tie sharing a rank, a count a record cannot give unranked', 'value': [rk['rop_ft_h'], rk['w2w_median_min'], rk['feet_per_day']],
                   'pass': rk['rop_ft_h'] == {'B': 1, 'A': 2, 'C': 3} and rk['w2w_median_min'] == {'A': 1, 'B': 1, 'C': 2} and rk['feet_per_day'] == {}})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SELF-TEST'}
