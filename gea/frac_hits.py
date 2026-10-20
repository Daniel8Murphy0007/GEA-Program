"""Frac hits: what each fracturing stage did to the pressure in the wells around it.

When a well is fractured stage by stage, a neighbouring well's pressure gauge answers in one of three ways (Seth,
Elliott and Sharma, "Rapid Analysis of Offset Well Pressure Response during Fracturing: Distinguishing between
Poroelastic, Hydraulic, and Frac-Hit Responses in Field Data Using Pattern Recognition", URTeC 2020-3129):

- POROELASTIC - the rock around the monitored well is squeezed by the growing fracture, with no fluid reaching it:
  the pressure rises while the stage pumps and falls as soon as the stage stops (a few psi to about a hundred);
- MIXED - a hydraulic connection as well as the squeeze: the pressure keeps rising, or holds, after the stage stops
  (tens to hundreds of psi);
- FRAC HIT - the stage's fluid and pressure reach the monitored well directly: hundreds to thousands of psi, and it
  does not fall when the stage stops.

For each stage of each treatment well (the stage table as the completion report gives it: stage, start and end of
pumping, the perforated interval, the fluid and proppant) and each monitored well (a well with a pressure channel in
its record), this module fits the monitored pressure's trend over the half hour before the stage, takes the
pressure's departure from that trend while the stage pumps and for half an hour after it stops, and reads:

- the first response - the first moment the departure exceeds the threshold (five times the trend's own noise, and
  not less than 5 psi) and stays above it for a minute; with it, the fluid pumped by then (the stage's volume in
  proportion to its pumping time: the volume to first response of sealed-wellbore pressure monitoring, Haustveit et
  al., SPE-199731-MS, 2020);
- the peak departure, and what the pressure did after the stage stopped - fell, held or kept rising;
- the type, by the rule above: no response below the threshold; FRAC HIT at 250 psi or more without falling; MIXED
  when it holds or rises after the stage stops; POROELASTIC when it falls. The thresholds are the operator's to
  declare, with their source.

With both wells surveyed, the distance from the stage's mid-point to the monitored well's nearest point is given; with
an earthquake catalogue attached and the stage clock's offset from UTC declared, the earthquakes during the stage are
counted beside it. The stage times and the gauge record must be on the same clock: the record's own, as written.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {'pre_min': 30.0, 'post_min': 30.0, 'k_sigma': 5.0, 'min_psi': 5.0, 'sustain_s': 60.0, 'hit_psi': 250.0, 'fall_frac': 0.10}
RULE_SOURCE = ('the three responses of Seth, Elliott and Sharma (URTeC 2020-3129): poroelastic falls when the stage stops; mixed and a direct '
               'frac hit hold or rise after it, a direct hit at hundreds to thousands of psi')
TYPES = ('NONE', 'POROELASTIC', 'MIXED', 'FRAC HIT')
COLUMNS = {
    'stage': ('stage', 'stage no', 'stage number', 'stage #', 'stg', 'stage_no'),
    'start': ('start', 'start time', 'pump start', 'start date/time', 'start datetime', 'begin', 'pumping start'),
    'end': ('end', 'end time', 'pump end', 'end date/time', 'end datetime', 'shut in', 'shut-in', 'pumping end', 'stop'),
    'top': ('top perf', 'top perf md', 'top md', 'perf top', 'top', 'top perforation'),
    'bottom': ('bottom perf', 'bottom perf md', 'bottom md', 'perf bottom', 'bottom', 'btm perf', 'bottom perforation'),
    'volume': ('clean volume', 'clean fluid', 'fluid', 'total fluid', 'slurry volume', 'volume', 'fluid volume'),
    'proppant': ('proppant', 'total proppant', 'sand', 'proppant mass'),
}
NOT_A_MEASUREMENT = ['where the fracture went: a pressure response says the stage reached the monitored well\'s rock or its wellbore, not '
                     'the fracture\'s shape', 'the volume to first response: the stage\'s volume in proportion to its pumping time, not a '
                     'metered volume at that second', 'a cause for an earthquake: a count beside a stage is a coincidence in time and place']


def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'completion', 'frac', *p)


def _load(ws, name: str, default):
    try:
        with open(_dir(ws, name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _store(ws, name: str, obj) -> None:
    os.makedirs(_dir(ws), exist_ok=True)
    p = _dir(ws, name)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(p + '.tmp', p)


def settings(ws) -> dict:
    s = {**DEFAULTS, 'monitors': {}, 'rule_source': '', 'utc_offset_h': {}}
    if ws is not None:
        s.update(_load(ws, 'settings.json', {}))
    return s


def set_settings(ws, actor: str, source: str = '', monitor: Optional[Tuple[str, Optional[str]]] = None, **rule) -> dict:
    s = settings(ws)
    lims = {'pre_min': (5, 600), 'post_min': (5, 600), 'k_sigma': (1, 50), 'min_psi': (0.1, 1000), 'sustain_s': (0, 3600),
            'hit_psi': (1, 20000), 'fall_frac': (0.0, 1.0)}
    changed = False
    for k, v in rule.items():
        if k not in lims or v in (None, ''):
            continue
        x = float(v)
        lo, hi = lims[k]
        if not (math.isfinite(x) and lo <= x <= hi):
            raise ValueError(f'{k} must be between {lo:g} and {hi:g}')
        s[k] = x
        changed = True
    if changed:
        if not (source or '').strip():
            raise ValueError("the rule's values need where they came from (e.g. \"completions engineer, pad 3 frac plan\")")
        s['rule_source'] = source.strip()
    if monitor:
        wid, ch = monitor
        if not any(w['id'] == wid for w in ws.wells()):
            raise KeyError(f'no well {wid!r}')
        if ch in (None, ''):
            s['monitors'].pop(wid, None)
        else:
            s['monitors'][wid] = {'channel': str(ch)}
    s['utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    s['by'] = actor
    _store(ws, 'settings.json', s)
    ws.audit(actor, 'frac.settings', {k: s.get(k) for k in list(DEFAULTS) + ['monitors', 'rule_source']})
    return s


# -- the stage table ------------------------------------------------------------------------------------------------------
def _norm(h: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'\(.*?\)|\[.*?\]', '', h or '').strip().lower().replace('_', ' '))


def _unit_of(h: str) -> str:
    m = re.search(r'\(([^)]*)\)|\[([^\]]*)\]', h or '')
    return (m.group(1) or m.group(2) or '').strip().lower() if m else ''


def _time(s: str) -> datetime:
    s = (s or '').strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M',
                '%m/%d/%y %H:%M', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M'):
        try:
            return datetime.strptime(s[:19] if 'T' in s or '-' in s[:5] else s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        raise ValueError(f'{s!r} is not a date and time') from None


def read_stages(content: bytes, depth_unit: str = 'ft') -> List[dict]:
    """The stage table: one row per stage with its pumping start and end; the perforated interval, fluid and proppant
    when given. Columns are found by their names (a unit in brackets is read)."""
    from . import xlsx_read as XR
    text = XR.to_csv(content) if XR.is_xlsx(content) else content.decode('utf-8-sig', errors='replace')
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError('the stage table has no rows')
    head = rows[0]
    col: Dict[str, int] = {}
    for key, names in COLUMNS.items():
        for j, h in enumerate(head):
            if _norm(h) in names and key not in col:
                col[key] = j
    for need in ('stage', 'start', 'end'):
        if need not in col:
            raise ValueError(f"the stage table needs a {need} column (found: {', '.join(head)})")
    du = _unit_of(head[col['top']]) if 'top' in col else ''
    fdepth = 0.3048 if (du or depth_unit).startswith('f') else 1.0
    vu = _unit_of(head[col['volume']]) if 'volume' in col else 'bbl'
    fvol = {'bbl': 1.0, 'gal': 1 / 42.0, 'm3': 6.28981}.get(vu or 'bbl', 1.0)
    out, seen = [], set()
    for i, r in enumerate(rows[1:], start=2):
        g = lambda k: (r[col[k]].strip() if k in col and col[k] < len(r) else '')
        st = g('stage')
        if not st:
            continue
        try:
            a, b = _time(g('start')), _time(g('end'))
        except ValueError as e:
            raise ValueError(f'row {i}: {e}') from None
        if b <= a:
            raise ValueError(f'row {i}: stage {st} ends before it starts')
        if st in seen:
            raise ValueError(f'row {i}: stage {st} is listed twice')
        seen.add(st)
        f = lambda k, s=1.0: (float(g(k).replace(',', '')) * s if g(k) else None)
        try:
            rec = {'stage': st, 'start': a.isoformat(), 'end': b.isoformat(), 'top_md_m': f('top', fdepth), 'bottom_md_m': f('bottom', fdepth),
                   'volume_bbl': f('volume', fvol), 'proppant_lb': f('proppant')}
        except ValueError as e:
            raise ValueError(f'row {i}: {e}') from None
        out.append(rec)
    out.sort(key=lambda x: x['start'])
    return out


def stages(ws) -> Dict[str, dict]:
    return _load(ws, 'stages.json', {})


def attach_stages(ws, well_id: str, filename: str, content: bytes, actor: str, source: str, depth_unit: str = 'ft',
                  utc_offset_h=None) -> dict:
    """Keep a treatment well's stage table verbatim beside the program and record how to read it."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    if not (source or '').strip():
        raise ValueError('say where the stage table came from (e.g. "frac service company\'s post-job report")')
    st = read_stages(content, depth_unit)
    off = None
    if utc_offset_h not in (None, ''):
        off = float(utc_offset_h)
        if not -14 <= off <= 14:
            raise ValueError('the stage clock\'s offset from UTC is in hours, between -14 and 14')
    fn = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename)) or 'stages.csv'
    d = _dir(ws, well_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fn), 'wb') as f:
        f.write(content)
    allr = stages(ws)
    allr[well_id] = {'file': fn, 'sha256': hashlib.sha256(content).hexdigest(), 'source': source.strip(), 'depth_unit': depth_unit,
                     'utc_offset_h': off, 'stages': len(st), 'first': st[0]['start'], 'last': st[-1]['end'], 'by': actor,
                     'utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    _store(ws, 'stages.json', allr)
    ws.audit(actor, 'frac.stages', {'well': well_id, 'file': fn, 'stages': len(st), 'source': source.strip()})
    return {**allr[well_id], 'well_id': well_id}


def remove_stages(ws, well_id: str, actor: str) -> dict:
    allr = stages(ws)
    rec = allr.pop(well_id, None)
    if not rec:
        raise KeyError(f'{well_id} has no stage table')
    _store(ws, 'stages.json', allr)
    ws.audit(actor, 'frac.stages_removed', {'well': well_id, 'file': rec['file'], 'sha256': rec['sha256']})
    return rec


def stage_rows(ws, well_id: str) -> List[dict]:
    rec = stages(ws).get(well_id)
    if not rec:
        return []
    with open(_dir(ws, well_id, rec['file']), 'rb') as f:
        return read_stages(f.read(), rec.get('depth_unit') or 'ft')


# -- the monitored pressure -----------------------------------------------------------------------------------------------
def pressure_channel(ws, well_id: str, declared: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """The well's pressure channel: the declared one, else the first in psi or named for pressure (not the pump's)."""
    from . import series_cache as SCH
    try:
        c = SCH.ensure(ws, well_id)
    except Exception:
        return None
    if c.meta.get('index_kind') != 'time_s' or not c.meta.get('start_time'):
        return None
    chans = [x for x in c.meta['channels'] if x.get('present')]
    if declared:
        x = next((x for x in chans if x['name'] == declared), None)
        return (x['name'], x['unit']) if x else None
    skip = ('standpipe', 'pump', 'spp', 'differential', 'casing pressure', 'annul')
    for x in chans:
        n = x['name'].lower()
        if any(s in n for s in skip):
            continue
        if (x['unit'] or '').lower() in ('psi', 'psia', 'psig') or 'press' in n or n.endswith('_psi'):
            return x['name'], x['unit'] or 'psi'
    return None


def _series(ws, well_id: str, ch: str) -> Tuple[np.ndarray, np.ndarray, datetime]:
    from . import series_cache as SCH
    c = SCH.ensure(ws, well_id)
    t = np.asarray(c.index, dtype=np.float64)
    v = np.asarray(c.channel(ch), dtype=np.float64)
    ok = np.isfinite(v) & np.isfinite(t)
    return t[ok], v[ok], datetime.fromisoformat(c.meta['start_time'][:19])


def respond(t: np.ndarray, p: np.ndarray, a: float, b: float, rule: dict) -> dict:
    """One stage (pumping from a to b, seconds on the record's index) read on one monitored pressure."""
    pre = (t >= a - rule['pre_min'] * 60.0) & (t < a)
    win = (t >= a) & (t <= b + rule['post_min'] * 60.0)
    if pre.sum() < 10 or win.sum() < 5:
        return {'type': None, 'why': 'the record does not cover the stage and the half hour before it'}
    tp, pp = t[pre], p[pre]
    A = np.vstack([np.ones_like(tp), tp - a]).T
    coef, *_ = np.linalg.lstsq(A, pp, rcond=None)
    res = pp - A @ coef
    noise = float(1.4826 * np.median(np.abs(res - np.median(res))))
    thr = max(rule['min_psi'], rule['k_sigma'] * noise)
    tw, pw = t[win], p[win]
    d = pw - (coef[0] + coef[1] * (tw - a))
    pump = tw <= b
    first = None
    above = d > thr
    for i in np.flatnonzero(above & pump):
        j = i
        while j + 1 < len(tw) and above[j + 1]:
            j += 1
        if tw[j] - tw[i] >= rule['sustain_s'] or j == len(tw) - 1:
            first = float(tw[i]); break
    peak_i = int(np.argmax(d))
    peak = float(d[peak_i])
    end_m = (tw >= b - 120.0) & (tw <= b)
    post_m = tw >= b + rule['post_min'] * 60.0 - 120.0
    at_end = float(np.median(d[end_m])) if end_m.any() else float(d[pump][-1]) if pump.any() else peak
    after = float(np.median(d[post_m])) if post_m.any() else float(d[-1])
    change = after - at_end
    if first is None or peak < thr:
        kind = 'NONE'
    elif peak >= rule['hit_psi'] and change > -rule['fall_frac'] * peak:
        kind = 'FRAC HIT'
    elif change > -rule['fall_frac'] * max(at_end, thr):
        kind = 'MIXED'
    else:
        kind = 'POROELASTIC'
    after_s = 'fell' if change < -rule['fall_frac'] * max(abs(at_end), thr) else 'rose' if change > rule['fall_frac'] * max(abs(at_end), thr) else 'held'
    return {'type': kind, 'threshold_psi': round(thr, 2), 'noise_psi': round(noise, 3), 'trend_psi_per_h': round(float(coef[1]) * 3600.0, 3),
            'first_response_s': None if first is None else round(first - a, 1), 'peak_psi': round(peak, 2), 'peak_at_s': round(float(tw[peak_i]) - a, 1),
            'at_end_psi': round(at_end, 2), 'after_psi': round(after, 2), 'after_stop': after_s,
            'trace': {'s': [round(float(x) - a, 1) for x in np.r_[tp, tw][::max(1, (len(tp) + len(tw)) // 300)]],
                      'psi': [round(float(x), 2) for x in np.r_[res, d][::max(1, (len(tp) + len(tw)) // 300)]]}}


# -- the analysis ---------------------------------------------------------------------------------------------------------
def _positions(ws) -> Dict[str, dict]:
    from . import well3d as W3
    try:
        sc = W3.workspace_scene(ws)
    except Exception:
        return {}
    out = {}
    for w in sc.get('wells') or []:
        out[w['id']] = W3.Path(w['stations']['md'], w['stations']['incl'], w['stations']['azi'], w['slot'][0], w['slot'][1], w['ref_elev_m'], 0.0)
    return out


def _nearest(path, q) -> Tuple[float, float]:
    d = path.dense(5.0)
    P = np.stack([np.asarray(d['x']), np.asarray(d['y']), np.asarray(d['z'])], axis=1)
    k = int(np.argmin(np.linalg.norm(P - np.asarray(q), axis=1)))
    return float(np.linalg.norm(P[k] - np.asarray(q))), float(d['md'][k])


def _quakes(ws, start: datetime, end: datetime, lat: float, lon: float, radius_km: float = 5.0) -> Optional[List[dict]]:
    from . import op_seismicity as OS
    try:
        sc = OS.workspace_scene(ws)
    except Exception:
        return None
    if not sc.get('catalog'):
        return None
    out = []
    for e in sc['events']:
        try:
            te = datetime.fromisoformat(str(e['time']).replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, KeyError):
            continue
        if start <= te <= end and OS.km(lat, lon, e['lat'], e['lon']) <= radius_km:
            out.append({'time': e['time'], 'magnitude': e.get('magnitude'), 'event_id': e.get('event_id')})
    return out


def analyse(ws) -> dict:
    s = settings(ws)
    rule = {k: float(s[k]) for k in DEFAULTS}
    names = {w['id']: (w.get('display') or w['id']) for w in ws.wells()}
    treat = {wid: rec for wid, rec in stages(ws).items() if wid in names}
    monitors = []
    for wid in names:
        decl = (s['monitors'].get(wid) or {}).get('channel')
        ch = pressure_channel(ws, wid, decl)
        if ch:
            monitors.append({'id': wid, 'display': names[wid], 'channel': ch[0], 'unit': ch[1], 'declared': bool(decl)})
    pos = _positions(ws)
    series = {}
    for m in monitors:
        try:
            series[m['id']] = _series(ws, m['id'], m['channel'])
        except Exception as e:
            m['why'] = f'unreadable: {type(e).__name__}'
    rows = []
    for wid, rec in treat.items():
        for st in stage_rows(ws, wid):
            a_dt, b_dt = datetime.fromisoformat(st['start']), datetime.fromisoformat(st['end'])
            mid = None
            if wid in pos and st['top_md_m'] is not None and st['bottom_md_m'] is not None:
                mid = pos[wid].at(0.5 * (st['top_md_m'] + st['bottom_md_m']))
            q = None
            if rec.get('utc_offset_h') is not None:
                try:
                    from . import geodesy as GD
                    sf = ws.well(wid).get('surface') or {}
                    g = GD.to_wgs84(float(sf['lat']), float(sf['lon']), 0.0, sf.get('datum') or 'WGS84')
                    off = timedelta(hours=float(rec['utc_offset_h']))
                    q = _quakes(ws, a_dt - off, b_dt - off + timedelta(minutes=rule['post_min']), g['lat'], g['lon'])
                except Exception:
                    q = None
            row = {'well_id': wid, 'well': names[wid], **st, 'pump_min': round((b_dt - a_dt).total_seconds() / 60.0, 1), 'responses': [],
                   'earthquakes': q}
            for m in monitors:
                if m['id'] == wid or m['id'] not in series:
                    continue
                t, p, t0 = series[m['id']]
                a = (a_dt - t0).total_seconds(); b = (b_dt - t0).total_seconds()
                r = respond(t, p, a, b, rule)
                r['monitor_id'], r['monitor'] = m['id'], m['display']
                if r.get('first_response_s') is not None and st.get('volume_bbl'):
                    r['vfr_bbl'] = round(st['volume_bbl'] * min(1.0, r['first_response_s'] / max(1.0, b - a)), 1)
                if mid is not None and m['id'] in pos:
                    dd, mdn = _nearest(pos[m['id']], mid)
                    r['distance_m'], r['nearest_md_m'] = round(dd, 1), round(mdn, 1)
                row['responses'].append(r)
            rows.append(row)
    count = {k: sum(1 for r in rows for x in r['responses'] if x.get('type') == k) for k in TYPES}
    return {'rule': rule, 'rule_source': s.get('rule_source') or RULE_SOURCE, 'treatment': [{'well_id': k, 'well': names[k], **v} for k, v in treat.items()],
            'monitors': monitors, 'stages': rows, 'count': count, 'not_a_measurement': NOT_A_MEASUREMENT,
            'hits': [{'well_id': r['well_id'], 'well': r['well'], 'stage': r['stage'], 'start': r['start'], 'monitor_id': x['monitor_id'],
                      'monitor': x['monitor'], 'peak_psi': x['peak_psi']}
                     for r in rows for x in r['responses'] if x.get('type') == 'FRAC HIT']}


def page(ws) -> dict:
    r = analyse(ws)
    r['title'] = ws.manifest.get('name') or 'this workspace'
    r['wells'] = [{'id': w['id'], 'display': w.get('display') or w['id']} for w in ws.wells()]
    r['detail'] = ('' if r['treatment'] and r['monitors'] else
                   'attach a stage table to a treatment well' if not r['treatment'] else 'no well has a pressure channel in its record to monitor')
    return r


def text(r: dict) -> str:
    L = [f"Frac hits - {r.get('title', '')}: {len(r['stages'])} stage(s), {len(r['monitors'])} monitored well(s); "
         + ', '.join(f"{k} {v}" for k, v in r['count'].items())]
    if r.get('detail'):
        L.append('  ' + r['detail'])
    for s_ in r['stages']:
        for x in s_['responses']:
            if x.get('type') in (None, 'NONE'):
                continue
            L.append(f"  {s_['well']} stage {s_['stage']} ({s_['start'][:16]}) -> {x['monitor']}: {x['type']}, peak {x['peak_psi']:,.1f} psi, "
                     f"first at {x['first_response_s'] / 60:.1f} min" + (f" ({x['vfr_bbl']:,.0f} bbl)" if x.get('vfr_bbl') is not None else '')
                     + f", after the stop it {x['after_stop']}" + (f"; {x['distance_m']:,.0f} m apart" if x.get('distance_m') is not None else ''))
    return '\n'.join(L)


def build_report(ws, version: str):
    from . import client_reports as CR
    r = page(ws)
    now = datetime.now(timezone.utc)
    rid = f"FDI-{now.strftime('%Y%m%dT%H%M%SZ')}"
    front = [['Report ID', rid], ['Scope', r['title']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Stages read', str(len(r['stages']))], ['Monitored wells', ', '.join(f"{m['display']} ({m['channel']})" for m in r['monitors']) or '-'],
             ['Responses', ', '.join(f"{k.lower()} {v}" for k, v in r['count'].items())]]
    s1 = CR.Section('1', 'The rule', [f"Trend over the {r['rule']['pre_min']:g} min before each stage; threshold the larger of {r['rule']['k_sigma']:g} times the trend's noise "
                                      f"and {r['rule']['min_psi']:g} psi, held {r['rule']['sustain_s']:g} s; a frac hit at {r['rule']['hit_psi']:g} psi or more that does not fall; "
                                      f"falling means losing more than {100 * r['rule']['fall_frac']:g} % in the {r['rule']['post_min']:g} min after the stage stops. {r['rule_source']}."], [])
    s2 = CR.Section('2', 'Stage by stage', [], [])
    s2.tables.append(CR.Table(['Well', 'Stage', 'Pumping', 'Monitor', 'Type', 'Peak (psi)', 'First (min)', 'VFR (bbl)', 'After the stop', 'Apart (m)'],
                              [[s_['well'], s_['stage'], f"{s_['start'][:16].replace('T', ' ')} +{s_['pump_min']:g} min", x['monitor'], x.get('type') or '-',
                                f"{x['peak_psi']:,.1f}" if x.get('peak_psi') is not None else '-',
                                f"{x['first_response_s'] / 60:.1f}" if x.get('first_response_s') is not None else '-',
                                f"{x['vfr_bbl']:,.0f}" if x.get('vfr_bbl') is not None else '-', x.get('after_stop') or x.get('why', '-'),
                                f"{x['distance_m']:,.0f}" if x.get('distance_m') is not None else '-'] for s_ in r['stages'] for x in s_['responses']]))
    s3 = CR.Section('3', 'What this will not call a measurement', ['; '.join(r['not_a_measurement']) + '.'], [])
    return CR.Document(f"Frac hits - {r['title']}", rid, front, [s1, s2, s3],
                       footer='Responses after Seth, Elliott and Sharma, URTeC 2020-3129; volume to first response after Haustveit et al., SPE-199731-MS.',
                       data={'report_id': rid, **{k: v for k, v in r.items() if k != 'stages'},
                             'stages': [{**s_, 'responses': [{k: v for k, v in x.items() if k != 'trace'} for x in s_['responses']]} for s_ in r['stages']]})


def write_report(ws, version: str, actor: str = 'system') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version)
    paths = CR.write(doc, ws.reports_dir, 'frac_hits')
    ws.audit(actor, 'report.frac_hits', {'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


# -- a labelled example ---------------------------------------------------------------------------------------------------
def selftest_files(tmp: str, seed: int = 7) -> dict:
    """A treatment well's six-stage table and a monitored well's gauge record (one sample every 10 s) in which the
    stages answer, by construction: stage 1 nothing, 2 poroelastic (25 psi, falling at the stop), 3 mixed (60 psi,
    rising after the stop), 4 a frac hit (900 psi, holding), 5 poroelastic (8 psi), 6 nothing; each response fading
    40 minutes after its stage stops; a slow trend and noise of 0.5 psi throughout."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 8, 3, 6, 0, 0)
    plan = [(1, 0, 'NONE', 0.0), (2, 1, 'POROELASTIC', 25.0), (3, 2, 'MIXED', 60.0), (4, 3, 'FRAC HIT', 900.0), (5, 4, 'POROELASTIC', 8.0), (6, 5, 'NONE', 0.0)]
    pump_s, gap_s = 2 * 3600, 3 * 3600
    rows, t = [], np.arange(0, 6 * (pump_s + gap_s) + 3600, 10.0)
    p = 3200.0 + 2.0 * t / 3600.0 + rng.normal(0.0, 0.5, len(t))
    for st, k, kind, mag in plan:
        a = 3600.0 + k * (pump_s + gap_s); b = a + pump_s
        rows.append((st, t0 + timedelta(seconds=a), t0 + timedelta(seconds=b), 15000 - 250 * st, 15200 - 250 * st, 9000 + 100 * st, 400000))
        if kind == 'NONE':
            continue
        on = (t >= a + 1200)                                # the first response 20 minutes in
        ramp = np.clip((t - (a + 1200)) / (pump_s - 1200), 0, 1)
        if kind == 'POROELASTIC':
            shape = np.where(t <= b, ramp, np.exp(-(t - b) / 600.0))
        elif kind == 'MIXED':
            shape = np.where(t <= b, ramp, 1.0 + 0.5 * np.clip((t - b) / 3600.0, 0, 1))
        else:
            shape = np.where(t <= b, np.clip((t - (a + 1200)) / 300.0, 0, 1), 1.0)
        fade = np.where(t > b + 2400.0, np.exp(-(t - (b + 2400.0)) / 480.0), 1.0)    # gone before the next stage's trend window
        p = p + mag * shape * on * fade
    os.makedirs(tmp, exist_ok=True)
    sp = os.path.join(tmp, 'frac_stages.csv')
    with open(sp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Stage', 'Start Time', 'End Time', 'Top Perf (ft)', 'Bottom Perf (ft)', 'Clean Volume (bbl)', 'Proppant (lb)'])
        for st, a, b, top, bot, vol, prop in rows:
            w.writerow([st, a.strftime('%Y-%m-%d %H:%M:%S'), b.strftime('%Y-%m-%d %H:%M:%S'), top, bot, vol, prop])
    gp = os.path.join(tmp, 'monitor_gauge.csv')
    with open(gp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'P_gauge_psi', 'T_gauge_F'])
        for ti, pi in zip(t, p):
            w.writerow([(t0 + timedelta(seconds=float(ti))).strftime('%Y-%m-%dT%H:%M:%S'), f'{pi:.2f}', '182.0'])
    return {'stages': sp, 'gauge': gp, 'expected': {str(st): kind for st, _, kind, _ in plan}}
