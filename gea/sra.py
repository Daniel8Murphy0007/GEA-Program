# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""sra - the Seismicity Response Area packet: what an operator inside an SRA has to put in front of the
Railroad Commission, built from what this program already holds.

The Railroad Commission of Texas declares a Seismicity Response Area around a cluster of felt earthquakes
and expects the operators inside it to run an operator-led response plan. The plans on file - Gardendale,
Northern Culberson-Reeves, Stanton - have the same shape, and the Commission's December 2023 Notice to
Operators on disposal-well monitoring in the Permian Basin names the data. That shape is what this module
builds to:

- an area: a centre, a radius (Gardendale is 9.08 km, "100 square miles"), a plan date, the magnitude
  threshold the plan is written against (M 3.5 in every plan on file), the goal (no earthquake at or above the
  threshold for 18 months), the response (the operator-led response group meets within 48 hours of one), and
  the checkpoint cadence with Commission staff (quarterly);
- which of a site's disposal wells and seismic stations fall inside it, by geodesic distance on a stated
  datum, and each well's depth tier - shallow or deep by the base of a named formation (the Wolfcamp in the
  Gardendale plan), because the plans act on the two tiers differently;
- the four daily parameters the Notice names, in its own words: maximum surface injection pressure (psi),
  average surface injection pressure (psi), injection volume (barrels per day), maximum injection rate
  (barrels per minute) - recorded daily and reported monthly through the TexNet injection reporting tool;
- bottomhole pressure by one of the Notice's three methods: calculated (surface pressure plus the hydrostatic
  column to the top of the disposal interval, quarterly), dip-in (quarterly), or a permanent probe (daily,
  reported monthly) - and a downhole gauge is a permanent probe, which is what this program was built around;
- the seismicity: the TexNet catalogue's events inside the area over the period, the largest, the count at or
  above the threshold, the days since the last one, whether the 48-hour response was triggered and by what,
  and where the 18-month clock stands;
- the schedule: the checkpoints with Commission staff from the plan date.

What this module will not do, and says so in every packet:

- it does not decide whether an earthquake is an aftershock. The Gardendale plan exempts aftershocks of the
  triggering event for six months; which events those are is a seismologist's call and is recorded here as
  the operator's declaration, never computed;
- it does not read a disposal well's volume off a pressure gauge. A well with no rate channel has no volume
  and no maximum rate, and the packet says NOT RECORDED for those rather than leaving the column blank;
- it does not place a well in a depth tier from its depth alone. The tier is stratigraphic - above or below the
  base of a formation - and a well whose completion the operator has not declared is UNCLASSIFIED;
- it does not know the TexNet tool's own upload template. The export carries the Notice's parameter names as
  its column headers, with the well's API and UIC numbers, and the mapping onto the tool's template is the
  operator's step and is named as such;
- it does not fetch the catalogue. The catalogue is a file the operator exports from TexNet and hands in,
  with its export date recorded, because a packet that quietly fetched a catalogue nobody saw is a packet
  nobody can check.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import geodesy as G

# The four daily parameters, in the Notice's own words (RRC NTO, December 2023, "Disposal Well Monitoring and
# Reporting Requirements in the Permian Basin"). The keys are this program's; the labels are the Commission's.
NTO_PARAMETERS: Tuple[Tuple[str, str], ...] = (
    ('max_surface_injection_pressure_psi', 'Maximum surface injection pressure (pounds per square inch)'),
    ('avg_surface_injection_pressure_psi', 'Average surface injection pressure (pounds per square inch)'),
    ('injection_volume_bbl', 'Injection volume (barrels per day)'),
    ('max_injection_rate_bbl_min', 'Maximum injection rate (barrels per minute)'),
)
# The Notice's three bottomhole-pressure methods and how often each is reported.
BHP_METHODS: Dict[str, dict] = {
    'calculated': {'label': 'Calculated BHP method', 'reported': 'quarterly',
                   'how': 'surface pressure plus the hydrostatic column to the shallowest open depth of the disposal interval'},
    'dip_in': {'label': 'Dip-in BHP measurement method', 'reported': 'quarterly',
               'how': 'a gauge run in the well with gradient stops no more than 1,000 ft apart to within 500 ft of the top perforation'},
    'probe': {'label': 'Permanent BHP probe method', 'reported': 'monthly, from daily measurements',
              'how': 'a surface-readout downhole gauge installed before first injection; a downhole gauge this program monitors is one'},
}
DEPTH_TIERS = ('shallow', 'deep')          # above / below the base of the named formation
RATE_UNITS = {'bbl/min': 1.0, 'bbl/d': 1.0 / 1440.0, 'bbl/day': 1.0 / 1440.0, 'bpm': 1.0, 'bpd': 1.0 / 1440.0}
SECONDS_PER_DAY = 86400.0


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_dt(s: Any) -> Optional[datetime]:
    from .permits import parse_date
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return parse_date(str(s)) if s is not None else None


def _months_later(d: datetime, months: int) -> datetime:
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return d.replace(year=y, month=m, day=day)


# --------------------------------------------------------------------------------------------------------------
# the area
# --------------------------------------------------------------------------------------------------------------
def define(name: str, lat: float, lon: float, radius_km: float, plan_date: Any, datum: str = 'WGS84',
           threshold_m: float = 3.5, goal_months: int = 18, response_hours: int = 48, checkpoint_months: int = 3,
           formation_boundary: str = 'base of the Wolfcamp', aftershock_months: int = 6,
           triggering_event: str = '', note: str = '') -> dict:
    """An SRA as the plans on file define one. Every number here is a declaration, not a measurement: the
    radius is the Commission's, the threshold and the goal are the plan's, and they are recorded as given."""
    if not name.strip():
        raise ValueError('an SRA needs a name')
    if not (radius_km > 0):
        raise ValueError('the radius must be positive, in kilometres (Gardendale: 9.08)')
    pd = _parse_dt(plan_date)
    if pd is None:
        raise ValueError(f'the plan date is not a date: {plan_date!r}')
    d = G.datum_name(datum)
    if d == 'UNKNOWN':
        raise ValueError('the centre needs a datum (WGS84, NAD83 or NAD27): an area whose datum is unknown is not an area')
    c = G.to_wgs84(float(lat), float(lon), 0.0, d)
    return {'protocol': 'sra.define/1', 'name': name.strip(), 'centre': {'lat': float(lat), 'lon': float(lon), 'datum': d},
            'centre_wgs84': {'lat': c['lat'], 'lon': c['lon'], 'shift_m': c.get('shift_m', 0.0)},
            'radius_km': float(radius_km), 'area_km2': round(math.pi * float(radius_km) ** 2, 1),
            'plan_date': pd.strftime('%Y-%m-%d'), 'threshold_m': float(threshold_m), 'goal_months': int(goal_months),
            'response_hours': int(response_hours), 'checkpoint_months': int(checkpoint_months),
            'aftershock_months': int(aftershock_months), 'formation_boundary': formation_boundary,
            'triggering_event': triggering_event, 'note': note,
            'basis': ('the shape of the operator-led response plans the Commission has on file (Gardendale, Northern '
                      'Culberson-Reeves, Stanton) and the December 2023 Notice to Operators on disposal-well monitoring'),
            'not_a_measurement': ['the radius, threshold, goal and cadence: declarations copied from the plan, never derived here']}


def checkpoints(sra: dict, until: Optional[datetime] = None, n_max: int = 12) -> List[dict]:
    """The meetings with Commission staff: the first one about ninety days after the plan date, then every
    checkpoint_months, with the goal date beside them."""
    pd = _parse_dt(sra['plan_date'])
    goal = _months_later(pd, sra['goal_months'])
    until = until or max(goal, datetime.now(timezone.utc))
    out, k, d = [], 1, _months_later(pd, sra['checkpoint_months'])
    while d <= until and len(out) < n_max:
        out.append({'n': k, 'date': d.strftime('%Y-%m-%d'), 'what': 'checkpoint with Commission staff: seismicity level and trend, '
                                                                   'pressure monitoring, effectiveness of the plan'})
        k += 1
        d = _months_later(pd, sra['checkpoint_months'] * k)
    out.append({'n': 'goal', 'date': goal.strftime('%Y-%m-%d'),
                'what': f"{sra['goal_months']} months from the plan date: if no earthquake at or above M {sra['threshold_m']:.1f} has occurred "
                        f"inside the area, the response group meets to decide next steps"})
    return out


# --------------------------------------------------------------------------------------------------------------
# membership: what of the site is inside the area
# --------------------------------------------------------------------------------------------------------------
def inside(sra: dict, lat: float, lon: float, datum: str = 'WGS84') -> dict:
    """Is a position inside the area - by geodesic distance from the centre, both positions on WGS84."""
    p = G.to_wgs84(float(lat), float(lon), 0.0, G.datum_name(datum))
    g = G.geodesic_m(sra['centre_wgs84']['lat'], sra['centre_wgs84']['lon'], p['lat'], p['lon'])
    km = g['distance_m'] / 1000.0
    return {'distance_km': round(km, 3), 'inside': km <= sra['radius_km'], 'datum': G.datum_name(datum),
            'datum_shift_m': round(p.get('shift_m', 0.0), 1)}


def well_membership(sra: dict, well: dict) -> dict:
    """One well against the area: its distance, its tier, and what the operator has and has not declared."""
    disp = well.get('disposal') or {}
    surf = well.get('surface') or {}
    out = {'id': well['id'], 'display': well.get('display'), 'api_number': disp.get('api_number') or '',
           'uic_number': disp.get('uic_number') or '', 'role': disp.get('role') or 'undeclared',
           'depth_tier': disp.get('depth_tier') if disp.get('depth_tier') in DEPTH_TIERS else 'UNCLASSIFIED',
           'formation_basis': disp.get('formation_basis') or '', 'bhp_method': disp.get('bhp_method') or '',
           'gaps': []}
    if surf.get('lat') is None or surf.get('lon') is None:
        out.update({'distance_km': None, 'inside': None, 'datum': None})
        out['gaps'].append('no surface position declared: membership cannot be decided')
    else:
        m = inside(sra, surf['lat'], surf['lon'], surf.get('datum') or 'UNKNOWN')
        out.update(m)
        if m['datum'] == 'UNKNOWN':
            out['gaps'].append('surface position has no datum: the distance assumes the centre\'s datum and may be off by tens of metres')
    if out['depth_tier'] == 'UNCLASSIFIED':
        out['gaps'].append(f"depth tier not declared (shallow or deep by the {sra.get('formation_boundary', 'formation boundary')})")
    if not out['api_number']:
        out['gaps'].append('no API number')
    if not out['uic_number']:
        out['gaps'].append('no UIC permit number')
    if not disp.get('channel_pressure'):
        out['gaps'].append('no surface injection pressure channel declared: the two pressure parameters cannot be recorded')
    if not disp.get('channel_rate'):
        out['gaps'].append('no injection rate channel declared: volume and maximum rate cannot be recorded')
    if out['bhp_method'] not in BHP_METHODS:
        out['gaps'].append('no bottomhole pressure method declared (calculated, dip_in or probe)')
    elif out['bhp_method'] == 'calculated' and (disp.get('top_interval_ft') is None or disp.get('gradient_psi_ft') is None):
        out['gaps'].append('calculated BHP needs the top of the disposal interval (ft) and the fluid gradient (psi/ft)')
    elif out['bhp_method'] == 'probe' and not disp.get('channel_bhp'):
        out['gaps'].append('probe BHP needs the downhole gauge channel declared')
    return out


def membership(sra: dict, wells: Sequence[dict], stations: Sequence[dict]) -> dict:
    """The site's wells and stations against the area."""
    w_rows = [well_membership(sra, w) for w in wells]
    s_rows = []
    for st in stations:
        lat, lon = st.get('lat'), st.get('lon')
        if lat is None or lon is None:
            s_rows.append({'id': st['id'], 'display': st.get('display'), 'distance_km': None, 'inside': None, 'gaps': ['no position']})
            continue
        m = inside(sra, lat, lon, st.get('datum') or 'UNKNOWN')
        s_rows.append({'id': st['id'], 'display': st.get('display'), 'kind': st.get('kind'), **m, 'gaps': []})
    return {'wells': w_rows, 'stations': s_rows,
            'n_wells_inside': sum(1 for r in w_rows if r['inside']), 'n_stations_inside': sum(1 for r in s_rows if r['inside']),
            'n_undecided': sum(1 for r in w_rows + s_rows if r['inside'] is None)}


# --------------------------------------------------------------------------------------------------------------
# the daily record: the Notice's four parameters and the bottomhole pressure
# --------------------------------------------------------------------------------------------------------------
def daily_records(stream, disposal: dict, start: Optional[datetime] = None, end: Optional[datetime] = None) -> dict:
    """Per calendar day (UTC): the four parameters from the well's own channels, and the bottomhole pressure by
    the declared method. A parameter whose channel is not declared is NOT RECORDED, named, not blank.

    The record must carry an absolute time. A stream whose index is elapsed seconds with no start time has no
    calendar, and a daily report without a calendar is not a daily report: it is refused."""
    t0s = stream.meta.get('start_time') if getattr(stream, 'meta', None) else None
    t0 = _parse_dt(t0s) if t0s else None
    if stream.index_kind != 'time_s' or t0 is None:
        return {'status': 'REFUSED', 'detail': 'the record carries no absolute time (no start time, or a depth index); a daily '
                                              'report needs a calendar', 'days': [], 'parameters': {}}
    idx = np.asarray(stream.index, dtype=float)
    p_ch = disposal.get('channel_pressure'); q_ch = disposal.get('channel_rate'); b_ch = disposal.get('channel_bhp')
    method = disposal.get('bhp_method') or ''
    unit = str(disposal.get('rate_unit') or 'bbl/min').lower()
    rate_to_bpm = RATE_UNITS.get(unit)
    params = {}
    for key, label in NTO_PARAMETERS:
        need = p_ch if 'pressure' in key else q_ch
        params[key] = {'label': label, 'status': 'RECORDED' if (need and need in stream.channels) else 'NOT RECORDED',
                       'channel': need if (need and need in stream.channels) else None}
    if q_ch and q_ch in stream.channels and rate_to_bpm is None:
        for key in ('injection_volume_bbl', 'max_injection_rate_bbl_min'):
            params[key].update({'status': 'NOT RECORDED', 'channel': None,
                                'why': f'rate unit {unit!r} is not one this program converts (bbl/min, bbl/d)'})
    bhp = {'method': method, 'label': BHP_METHODS.get(method, {}).get('label', 'not declared'),
           'reported': BHP_METHODS.get(method, {}).get('reported', '-'), 'status': 'NOT RECORDED'}
    if method == 'probe' and b_ch in stream.channels:
        bhp['status'] = 'RECORDED'; bhp['channel'] = b_ch
    elif method == 'calculated' and p_ch in stream.channels and disposal.get('top_interval_ft') is not None and disposal.get('gradient_psi_ft') is not None:
        bhp['status'] = 'RECORDED'
        bhp['hydrostatic_psi'] = round(float(disposal['top_interval_ft']) * float(disposal['gradient_psi_ft']), 1)
        bhp['how'] = f"surface pressure + {disposal['gradient_psi_ft']} psi/ft x {disposal['top_interval_ft']} ft = surface + {bhp['hydrostatic_psi']} psi"
    elif method == 'dip_in':
        bhp['status'] = 'BY SURVEY'; bhp['how'] = 'a gauge run in the well each quarter; the survey is attached, not computed here'
    # the days
    abs_t = np.array([t0.timestamp() + s for s in idx])
    day_of = np.floor(abs_t / SECONDS_PER_DAY).astype(np.int64)
    lo = int(math.floor(start.timestamp() / SECONDS_PER_DAY)) if start else int(day_of.min())
    hi = int(math.floor(end.timestamp() / SECONDS_PER_DAY)) if end else int(day_of.max())
    P = stream.channels[p_ch].values if params['max_surface_injection_pressure_psi']['status'] == 'RECORDED' else None
    Q = stream.channels[q_ch].values * rate_to_bpm if params['injection_volume_bbl']['status'] == 'RECORDED' else None
    B = stream.channels[b_ch].values if bhp.get('channel') else None
    days = []
    for d in range(lo, hi + 1):
        sel = day_of == d
        n = int(sel.sum())
        row = {'date': datetime.fromtimestamp(d * SECONDS_PER_DAY, timezone.utc).strftime('%Y-%m-%d'), 'samples': n}
        if n == 0:
            row['status'] = 'NO DATA'
            days.append(row); continue
        row['status'] = 'RECORDED'
        if P is not None:
            pv = P[sel]; pv = pv[~np.isnan(pv)]
            row['max_surface_injection_pressure_psi'] = round(float(pv.max()), 1) if pv.size else None
            row['avg_surface_injection_pressure_psi'] = round(float(pv.mean()), 1) if pv.size else None
        if Q is not None:
            t = abs_t[sel]; qv = Q[sel]
            ok = ~np.isnan(qv)
            if ok.sum() >= 2:
                tt, qq = t[ok], qv[ok]
                # each sample holds for its own interval, the last one for the median interval: the way a
                # totalizer counts, so a day of 144 ten-minute samples is 24 hours, not 23 h 50 min
                dt = np.diff(tt); dt = np.append(dt, float(np.median(dt)))
                row['injection_volume_bbl'] = round(float(np.sum(qq * dt / 60.0)), 1)    # bbl/min x minutes
                row['max_injection_rate_bbl_min'] = round(float(qq.max()), 3)
            elif ok.sum() == 1:
                row['injection_volume_bbl'] = None; row['max_injection_rate_bbl_min'] = round(float(qv[ok][0]), 3)
            else:
                row['injection_volume_bbl'] = None; row['max_injection_rate_bbl_min'] = None
        if B is not None:
            bv = B[sel]; bv = bv[~np.isnan(bv)]
            row['bhp_psi'] = round(float(bv.mean()), 1) if bv.size else None
        elif bhp['status'] == 'RECORDED' and row.get('avg_surface_injection_pressure_psi') is not None:
            row['bhp_psi'] = round(row['avg_surface_injection_pressure_psi'] + bhp['hydrostatic_psi'], 1)
        days.append(row)
    n_rec = sum(1 for r in days if r['status'] == 'RECORDED')
    return {'status': 'RECORDED' if n_rec else 'NO DATA', 'days': days, 'n_days': len(days), 'n_recorded': n_rec,
            'n_missing': len(days) - n_rec, 'parameters': params, 'bhp': bhp,
            'period': {'start': days[0]['date'] if days else None, 'end': days[-1]['date'] if days else None},
            'detail': (f"{n_rec} of {len(days)} day(s) carry a record; "
                       + ', '.join(f"{k.replace('_', ' ')}: {v['status']}" for k, v in params.items())
                       + f"; bottomhole pressure: {bhp['status']} ({bhp['label']})")}


def monthly_summary(daily: dict) -> List[dict]:
    """The monthly roll-up the tool is fed: per month, the days recorded and missing, the monthly maximum
    and mean of the daily parameters, and the total volume."""
    by: Dict[str, List[dict]] = {}
    for r in daily.get('days', []):
        by.setdefault(r['date'][:7], []).append(r)
    out = []
    for m in sorted(by):
        rows = by[m]; rec = [r for r in rows if r['status'] == 'RECORDED']
        def col(k, f):
            v = [r[k] for r in rec if r.get(k) is not None]
            return round(f(v), 1) if v else None
        out.append({'month': m, 'days': len(rows), 'recorded': len(rec), 'missing': len(rows) - len(rec),
                    'max_surface_injection_pressure_psi': col('max_surface_injection_pressure_psi', max),
                    'avg_surface_injection_pressure_psi': col('avg_surface_injection_pressure_psi', lambda v: sum(v) / len(v)),
                    'injection_volume_bbl': col('injection_volume_bbl', sum),
                    'max_injection_rate_bbl_min': col('max_injection_rate_bbl_min', max),
                    'bhp_psi': col('bhp_psi', lambda v: sum(v) / len(v))})
    return out


# --------------------------------------------------------------------------------------------------------------
# the seismicity: the TexNet catalogue, as a file the operator hands in
# --------------------------------------------------------------------------------------------------------------
CATALOG_FIELDS = {
    'event_id': ['eventid', 'event id', 'id', 'event'],
    'origin_time': ['origin time', 'origin date time', 'time utc', 'origin datetime', 'datetime', 'time'],
    'origin_date': ['origin date', 'date'],
    'magnitude': ['local magnitude', 'magnitude', 'ml', 'mag', 'magnitude ml'],
    'lat': ['latitude', 'lat', 'latitude wgs84'],
    'lon': ['longitude', 'lon', 'long', 'longitude wgs84'],
    'depth_km': ['depth km', 'depth', 'depth of hypocenter km'],
}


def read_catalog(path: str, mapping: Optional[Dict[str, str]] = None) -> dict:
    """A TexNet earthquake-catalogue export (CSV). The columns are found by name; a mapping wins. Every event
    keeps its own id so a claim in the packet can be traced to the catalogue line it came from."""
    from .permits import read_table, _float
    header, rows = read_table(path)
    cols = _detect(header, mapping)
    events, skipped = [], 0
    for r in rows:
        t = None
        if cols.get('origin_time'):
            t = _parse_dt(r.get(cols['origin_time']))
        if t is None and cols.get('origin_date'):
            t = _parse_dt((r.get(cols['origin_date']) or '') + (' ' + (r.get(cols['origin_time']) or '') if cols.get('origin_time') else ''))
            if t is None:
                t = _parse_dt(r.get(cols['origin_date']))
        m = _float(r.get(cols['magnitude'])) if cols.get('magnitude') else None
        la = _float(r.get(cols['lat'])) if cols.get('lat') else None
        lo = _float(r.get(cols['lon'])) if cols.get('lon') else None
        if t is None or m is None or la is None or lo is None:
            skipped += 1; continue
        events.append({'event_id': (r.get(cols['event_id']) if cols.get('event_id') else '') or f'row{len(events) + skipped + 1}',
                       'time': _utc(t), 'magnitude': m, 'lat': la, 'lon': lo,
                       'depth_km': _float(r.get(cols['depth_km'])) if cols.get('depth_km') else None})
    return {'path': os.path.abspath(path), 'export_mtime_utc': _utc(datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)),
            'columns': cols, 'n_rows': len(rows), 'n_events': len(events), 'n_skipped': skipped, 'events': events,
            'datum': 'WGS84', 'basis': 'the TexNet earthquake catalogue as exported by the operator; positions WGS84, magnitudes ML'}


def _detect(header: Sequence[str], mapping: Optional[Dict[str, str]]) -> Dict[str, Optional[str]]:
    from .permits import _norm
    norm = {_norm(h): h for h in header}
    out: Dict[str, Optional[str]] = {}
    for f, cands in CATALOG_FIELDS.items():
        if mapping and f in mapping and mapping[f] in header:
            out[f] = mapping[f]; continue
        hit = next((norm[c] for c in cands if c in norm), None)
        if hit is None:
            hit = next((h for n, h in norm.items() for c in cands if c in n and len(c) > 2), None)
        out[f] = hit
    # 'time' must not steal the date column
    if out.get('origin_time') and out.get('origin_date') and out['origin_time'] == out['origin_date']:
        out['origin_time'] = None
    return out


def seismicity(sra: dict, catalog: dict, as_of: Optional[datetime] = None, aftershocks: Sequence[str] = ()) -> dict:
    """The catalogue against the area and the plan: what happened inside it, the largest, the count at or above
    the threshold, the response trigger, and the goal clock. Aftershocks are the operator's declaration (a list
    of event ids) and are shown as exempt, never decided here."""
    now = as_of or datetime.now(timezone.utc)
    pd = _parse_dt(sra['plan_date'])
    thr = float(sra['threshold_m'])
    ex = set(aftershocks or [])
    inside_ev = []
    for e in catalog.get('events', []):
        m = inside(sra, e['lat'], e['lon'], catalog.get('datum', 'WGS84'))
        if m['inside']:
            t = _parse_dt(e['time'])
            inside_ev.append({**e, 'distance_km': m['distance_km'], 'after_plan': bool(t and t >= pd),
                              'at_or_above': e['magnitude'] >= thr, 'exempt': e['event_id'] in ex})
    inside_ev.sort(key=lambda e: e['time'])
    above = [e for e in inside_ev if e['at_or_above']]
    counted = [e for e in above if e['after_plan'] and not e['exempt']]
    largest = max(inside_ev, key=lambda e: e['magnitude']) if inside_ev else None
    last = counted[-1] if counted else None
    last_t = _parse_dt(last['time']) if last else None
    clock_start = last_t or pd
    goal_date = _months_later(clock_start, sra['goal_months'])
    days_quiet = (now - clock_start).days
    if counted:
        trigger = {'status': 'TRIGGERED', 'event_id': last['event_id'], 'magnitude': last['magnitude'], 'time': last['time'],
                   'respond_by': _utc(last_t + timedelta(hours=sra['response_hours'])),
                   'detail': (f"M {last['magnitude']:.1f} on {last['time'][:10]} inside the area, after the plan date and not declared an "
                              f"aftershock: the response group meets within {sra['response_hours']} hours")}
    else:
        trigger = {'status': 'NOT TRIGGERED', 'detail': f'no earthquake at or above M {thr:.1f} inside the area since the plan date that is not a declared aftershock'}
    goal = {'clock_started': clock_start.strftime('%Y-%m-%d'), 'goal_date': goal_date.strftime('%Y-%m-%d'), 'days_quiet': days_quiet,
            'status': 'MET' if now >= goal_date else 'RUNNING',
            'detail': (f"{days_quiet} day(s) without an earthquake at or above M {thr:.1f} since {clock_start.strftime('%Y-%m-%d')}; "
                       + (f"the {sra['goal_months']}-month goal was reached on {goal_date.strftime('%Y-%m-%d')}" if now >= goal_date
                          else f"the {sra['goal_months']}-month goal falls on {goal_date.strftime('%Y-%m-%d')}"))}
    # the trend: events per month inside the area, all magnitudes
    per_month: Dict[str, int] = {}
    for e in inside_ev:
        per_month[e['time'][:7]] = per_month.get(e['time'][:7], 0) + 1
    return {'as_of': _utc(now), 'n_inside': len(inside_ev), 'n_at_or_above': len(above), 'n_counted': len(counted),
            'n_exempt': sum(1 for e in above if e['exempt']),
            'largest': ({'event_id': largest['event_id'], 'magnitude': largest['magnitude'], 'time': largest['time'],
                         'distance_km': largest['distance_km']} if largest else None),
            'events': inside_ev, 'trigger': trigger, 'goal': goal, 'per_month': per_month,
            'catalog': {k: catalog[k] for k in ('path', 'export_mtime_utc', 'n_events', 'n_skipped', 'basis') if k in catalog},
            'not_a_measurement': ['which events are aftershocks: a declaration by the operator, listed as exempt, never decided here',
                                  'magnitudes and locations: the catalogue\'s, as exported, not re-estimated',
                                  'the catalogue\'s completeness: events after its export date are not in it']}


# --------------------------------------------------------------------------------------------------------------
# the export the tool is fed
# --------------------------------------------------------------------------------------------------------------
def export_daily_csv(rows: List[dict], out_path: str) -> dict:
    """One line per well per day, the Notice's four parameters under the Notice's own names, the bottomhole
    pressure and its method, and the well's identifiers. The mapping onto the TexNet tool's own template is
    the operator's step."""
    cols = ['API number', 'UIC permit number', 'Well', 'Date'] + [lbl for _, lbl in NTO_PARAMETERS] + \
           ['Bottomhole pressure (pounds per square inch)', 'BHP method', 'Samples', 'Status']
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    n = 0
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(cols)
        for r in rows:
            w.writerow([r.get('api_number', ''), r.get('uic_number', ''), r.get('well', ''), r['date']]
                       + [('' if r.get(k) is None else r[k]) if r.get('status') == 'RECORDED' else 'NOT RECORDED' for k, _ in NTO_PARAMETERS]
                       + ['' if r.get('bhp_psi') is None else r['bhp_psi'], r.get('bhp_method', ''), r.get('samples', 0), r.get('status', '')])
            n += 1
    return {'path': os.path.abspath(out_path), 'rows': n, 'columns': cols,
            'note': 'column headers are the Notice\'s parameter names; map them onto the TexNet tool\'s template before upload'}


# --------------------------------------------------------------------------------------------------------------
# the packet
# --------------------------------------------------------------------------------------------------------------
def packet(sra: dict, member: dict, wells_daily: Dict[str, dict], seis: Optional[dict], as_of: Optional[datetime] = None,
           site: Optional[dict] = None, association: Optional[dict] = None, wells: Optional[List[dict]] = None) -> dict:
    """Everything the packet says, in one record: the area, who is inside it, the daily record per well rolled
    up by month, the seismicity against the plan, the schedule, and every gap by name."""
    now = as_of or datetime.now(timezone.utc)
    gaps: List[str] = []
    for w in member['wells']:
        for g in w['gaps']:
            gaps.append(f"well {w['id']}: {g}")
    for s in member['stations']:
        for g in s.get('gaps', []):
            gaps.append(f"station {s['id']}: {g}")
    # the well named the way the operator's master data names it (v0.13.0): the US Well Number taken apart and
    # the "What is a Well" components the site can name; the identity rides on the well row and its gaps are the
    # identity's own, named in that table and not counted against the packet
    from . import ppdm as _P
    full = {w['id']: w for w in (wells or [])}
    identities = {}
    for w in member['wells']:
        rec = full.get(w['id'])
        if rec is not None:
            ident = _P.well_identity(rec, site)
            identities[w['id']] = {'status': ident['status'], 'us_well_number': ident['components']['well']['identifier'],
                                   'identifies': (ident['us_well_number'] or {}).get('identifies'),
                                   'state': ((ident['us_well_number'] or {}).get('state') or {}).get('name'),
                                   'wellbore': ident['components']['wellbore'].get('identifier'),
                                   'origin_datum': ((ident['components']['well_origin'] or {}).get('position') or {}).get('datum'),
                                   'aliases': [f"{a['name']} [{a['type']}]" for a in ident['aliases']], 'gaps': ident['gaps']}
    wells_out = []
    for w in member['wells']:
        d = wells_daily.get(w['id'])
        w = {**w, 'identity': identities.get(w['id'])}
        if d is None:
            wells_out.append({**w, 'daily_status': 'NOT RUN', 'months': []}); continue
        if d['status'] == 'REFUSED':
            gaps.append(f"well {w['id']}: {d['detail']}")
        for k, p in d.get('parameters', {}).items():
            if p['status'] != 'RECORDED' and w['inside']:
                gaps.append(f"well {w['id']}: {p['label']} - NOT RECORDED" + (f" ({p['why']})" if p.get('why') else ''))
        if d.get('bhp', {}).get('status') == 'NOT RECORDED' and w['inside']:
            gaps.append(f"well {w['id']}: bottomhole pressure not recorded ({d['bhp']['label']})")
        wells_out.append({**w, 'daily_status': d['status'], 'daily_detail': d.get('detail'), 'period': d.get('period'),
                          'n_recorded': d.get('n_recorded', 0), 'n_missing': d.get('n_missing', 0),
                          'parameters': d.get('parameters', {}), 'bhp': d.get('bhp', {}), 'months': monthly_summary(d)})
    if seis is None:
        gaps.append('no catalogue export handed in: the seismicity section is empty and the goal clock is not stated')
    assoc_out = None
    if association:
        a = association.get('association') or {}
        inside_ev = []
        for e in a.get('events', []):
            m = inside(sra, e['lat'], e['lon'], e.get('datum', 'WGS84'))
            inside_ev.append({**e, 'distance_km': m['distance_km'], 'inside': m['inside']})
        assoc_out = {'generated_utc': association.get('generated_utc'), 'n_stations': len([k for k, v in (association.get('stations') or {}).items() if v.get('status') == 'PICKED']),
                     'n_picks': a.get('n_picks', 0), 'n_events': a.get('n_events', 0), 'n_inside': sum(1 for e in inside_ev if e['inside']),
                     'events': inside_ev, 'refused': a.get('refused', []), 'model': a.get('model'), 'catalogue': association.get('catalogue')}
    status = 'COMPLETE' if not gaps else 'INCOMPLETE'
    return {'protocol': 'sra.packet/1', 'generated_utc': _utc(now), 'status': status, 'sra': sra,
            'site': ({'id': site['id'], 'display': site.get('display'), 'client': site.get('client')} if site else None),
            'membership': {k: member[k] for k in ('n_wells_inside', 'n_stations_inside', 'n_undecided')},
            'wells': wells_out, 'stations': member['stations'], 'seismicity': seis, 'association': assoc_out, 'checkpoints': checkpoints(sra),
            'gaps': gaps,
            'basis': ('membership by geodesic distance on WGS84; daily parameters from each well\'s own channels, named as the Notice names them; '
                      'seismicity from the catalogue export handed in; the schedule from the plan date'),
            'not_a_measurement': ['an aftershock: the operator\'s declaration, shown as exempt',
                                  'a volume read off a pressure gauge: a well with no rate channel has no volume',
                                  'a depth tier from depth alone: the tier is stratigraphic and is declared',
                                  'the TexNet tool\'s own template: the export carries the Notice\'s names and the mapping is the operator\'s',
                                  'a catalogue nobody handed in: the catalogue is a file with an export date']}


# --------------------------------------------------------------------------------------------------------------
# the synthetic scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_scene(days: int = 40, seed: int = 7) -> dict:
    """A labelled scene: one deep disposal well inside the area with a surface pressure, a rate and a downhole
    probe, one shallow well inside with no rate channel, one well outside, and a catalogue with a triggering
    event, two aftershocks, an M 3.8 after the plan date, and small events outside the area."""
    from .ports import LiveStream, StreamChannel
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    n = days * 24 * 6                                         # 10-minute samples
    idx = np.arange(n) * 600.0
    day = idx / SECONDS_PER_DAY
    p = 1800.0 + 60.0 * np.sin(2 * np.pi * day / 7.0) + rng.normal(0, 4.0, n)
    q = np.clip(6.0 + 1.5 * np.sin(2 * np.pi * day / 3.0) + rng.normal(0, 0.2, n), 0.0, None)   # bbl/min, ~8,600 bbl/d
    q[(day >= 10) & (day < 12)] = 0.0                          # two days shut in
    b = p + 0.465 * 9800.0 + rng.normal(0, 3.0, n)             # a probe at 9,800 ft, brine gradient
    def stream(name, chans):
        return LiveStream(name=name, source_format='synthetic', index_kind='time_s', index=idx.copy(), channels=chans,
                          meta={'start_time': _utc(t0)})
    deep = stream('deep', {'P_surf_psi': StreamChannel('P_surf_psi', 'psi', p.copy()), 'Q_bpm': StreamChannel('Q_bpm', 'bbl/min', q.copy()),
                           'P_bh_psi': StreamChannel('P_bh_psi', 'psi', b.copy())})
    shallow = stream('shallow', {'P_surf_psi': StreamChannel('P_surf_psi', 'psi', p * 0.6)})
    sra = define('Synthetic SRA', 31.95, -102.25, 9.08, '2026-01-10', 'WGS84', triggering_event='syn-000')
    wells = [
        {'id': 'deep-1', 'display': 'Deep disposal 1', 'surface': {'lat': 31.97, 'lon': -102.23, 'datum': 'WGS84'},
         'disposal': {'role': 'disposal', 'api_number': '42-329-00001', 'uic_number': '000001', 'depth_tier': 'deep',
                      'formation_basis': 'completed below the base of the Wolfcamp', 'channel_pressure': 'P_surf_psi',
                      'channel_rate': 'Q_bpm', 'rate_unit': 'bbl/min', 'bhp_method': 'probe', 'channel_bhp': 'P_bh_psi'}},
        {'id': 'shallow-1', 'display': 'Shallow disposal 1', 'surface': {'lat': 31.93, 'lon': -102.27, 'datum': 'NAD27'},
         'disposal': {'role': 'disposal', 'api_number': '42-329-00002', 'uic_number': '000002', 'depth_tier': 'shallow',
                      'formation_basis': 'completed above the base of the Wolfcamp', 'channel_pressure': 'P_surf_psi',
                      'bhp_method': 'calculated', 'top_interval_ft': 5200.0, 'gradient_psi_ft': 0.465}},
        {'id': 'far-1', 'display': 'Far well', 'surface': {'lat': 32.30, 'lon': -102.25, 'datum': 'WGS84'},
         'disposal': {'role': 'disposal', 'depth_tier': 'deep'}},
    ]
    stations = [{'id': 'arr-a', 'display': 'Array A', 'kind': 'array', 'lat': 31.96, 'lon': -102.20, 'datum': 'WGS84'},
                {'id': 'node-far', 'display': 'Far node', 'kind': 'station', 'lat': 32.40, 'lon': -102.00, 'datum': 'WGS84'}]
    events = [
        {'event_id': 'syn-000', 'time': '2025-12-16T03:12:00Z', 'magnitude': 5.2, 'lat': 31.95, 'lon': -102.25, 'depth_km': 7.0},   # triggering, before plan
        {'event_id': 'syn-001', 'time': '2026-01-12T10:00:00Z', 'magnitude': 3.6, 'lat': 31.96, 'lon': -102.24, 'depth_km': 6.5},   # aftershock (declared)
        {'event_id': 'syn-002', 'time': '2026-01-20T22:40:00Z', 'magnitude': 2.1, 'lat': 31.94, 'lon': -102.26, 'depth_km': 6.0},
        {'event_id': 'syn-003', 'time': '2026-02-03T01:05:00Z', 'magnitude': 3.8, 'lat': 31.98, 'lon': -102.22, 'depth_km': 7.5},   # counts: triggers
        {'event_id': 'syn-004', 'time': '2026-02-05T14:30:00Z', 'magnitude': 4.1, 'lat': 32.45, 'lon': -102.60, 'depth_km': 8.0},   # outside
        {'event_id': 'syn-005', 'time': '2026-02-07T09:00:00Z', 'magnitude': 1.4, 'lat': 31.95, 'lon': -102.25, 'depth_km': 5.0},
    ]
    catalog = {'path': 'synthetic', 'export_mtime_utc': _utc(t0 + timedelta(days=days)), 'n_events': len(events), 'n_skipped': 0,
               'events': events, 'datum': 'WGS84', 'basis': 'SYNTHETIC catalogue, labelled'}
    return {'sra': sra, 'wells': wells, 'stations': stations, 'streams': {'deep-1': deep, 'shallow-1': shallow}, 'catalog': catalog,
            'aftershocks': ['syn-001'], 'as_of': t0 + timedelta(days=days),
            'truth': {'inside_wells': ['deep-1', 'shallow-1'], 'outside_wells': ['far-1'], 'inside_stations': ['arr-a'],
                      'trigger_event': 'syn-003', 'largest_inside': 'syn-000', 'n_inside': 5, 'shut_in_days': ['2026-01-11', '2026-01-12'],
                      'deep_volume_per_day_bbl': 8640.0, 'probe_offset_psi': 0.465 * 9800.0}, 'label': 'SIMULATION_SELF_TEST'}


def selftest() -> dict:
    sc = synthetic_scene()
    sra, truth = sc['sra'], sc['truth']
    mem = membership(sra, sc['wells'], sc['stations'])
    ins = sorted(w['id'] for w in mem['wells'] if w['inside']); outs = sorted(w['id'] for w in mem['wells'] if w['inside'] is False)
    daily = {wid: daily_records(st, next(w for w in sc['wells'] if w['id'] == wid)['disposal']) for wid, st in sc['streams'].items()}
    seis = seismicity(sra, sc['catalog'], as_of=sc['as_of'], aftershocks=sc['aftershocks'])
    pk = packet(sra, mem, daily, seis, as_of=sc['as_of'], wells=sc['wells'])
    deep = daily['deep-1']; shallow = daily['shallow-1']
    full = [d for d in deep['days'] if d['status'] == 'RECORDED' and d['date'] not in truth['shut_in_days'] and d['samples'] == 144]
    vols = [d['injection_volume_bbl'] for d in full]
    shut = [d for d in deep['days'] if d['date'] in truth['shut_in_days']]
    probe_off = [d['bhp_psi'] - d['avg_surface_injection_pressure_psi'] for d in full if d.get('bhp_psi') is not None]
    checks = {
        'membership': ins == sorted(truth['inside_wells']) and outs == truth['outside_wells']
                      and [s['id'] for s in mem['stations'] if s['inside']] == truth['inside_stations'],
        'nad27_shift_named': any(w['id'] == 'shallow-1' and w.get('datum_shift_m', 0) > 10 for w in mem['wells']),
        'deep_four_parameters': all(p['status'] == 'RECORDED' for p in deep['parameters'].values()) and deep['bhp']['status'] == 'RECORDED',
        'shallow_refuses_volume': shallow['parameters']['injection_volume_bbl']['status'] == 'NOT RECORDED'
                                  and shallow['parameters']['max_surface_injection_pressure_psi']['status'] == 'RECORDED'
                                  and shallow['bhp']['status'] == 'RECORDED' and abs(shallow['bhp']['hydrostatic_psi'] - 5200 * 0.465) < 0.1,
        'volume_integrates': bool(vols) and abs(float(np.mean(vols)) - truth['deep_volume_per_day_bbl']) / truth['deep_volume_per_day_bbl'] < 0.03,
        'shut_in_days_zero': len(shut) == 2 and all((d['injection_volume_bbl'] or 0.0) < 50.0 for d in shut),
        'probe_offset': bool(probe_off) and abs(float(np.mean(probe_off)) - truth['probe_offset_psi']) < 15.0,
        'seismicity': seis['n_inside'] == truth['n_inside'] and seis['largest']['event_id'] == truth['largest_inside']
                      and seis['trigger']['status'] == 'TRIGGERED' and seis['trigger']['event_id'] == truth['trigger_event']
                      and seis['n_exempt'] == 1 and seis['goal']['status'] == 'RUNNING' and seis['goal']['clock_started'] == '2026-02-03',
        'packet_names_gaps': pk['status'] == 'INCOMPLETE' and any('far-1' in g and 'API' in g for g in pk['gaps'])
                             and any('shallow-1' in g and 'NOT RECORDED' in g for g in pk['gaps']),
        'checkpoints': pk['checkpoints'][0]['date'] == '2026-04-10' and pk['checkpoints'][-1]['date'] == '2027-07-10',
    }
    return {'label': sc['label'], 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks,
            'deep_mean_volume_bbl_day': round(float(np.mean(vols)), 1) if vols else None,
            'probe_offset_psi': round(float(np.mean(probe_off)), 1) if probe_off else None, 'packet': pk}


def report_text(pk: dict) -> str:
    s = pk['sra']; m = pk['membership']; se = pk.get('seismicity')
    lines = [f"SRA packet: {s['name']}  [{pk['status']}]",
             f"  area: {s['radius_km']} km around {s['centre']['lat']:.4f}, {s['centre']['lon']:.4f} ({s['centre']['datum']}); plan date {s['plan_date']}; "
             f"threshold M {s['threshold_m']:.1f}; goal {s['goal_months']} months; response within {s['response_hours']} h",
             f"  inside: {m['n_wells_inside']} well(s), {m['n_stations_inside']} station(s); undecided: {m['n_undecided']}"]
    for w in pk['wells']:
        where = 'inside' if w['inside'] else ('outside' if w['inside'] is False else 'undecided')
        dist = '' if w['distance_km'] is None else f" at {w['distance_km']:.1f} km"
        lines.append(f"  well {w['id']}: {where}{dist}; tier {w['depth_tier']}; {w.get('daily_detail') or w.get('daily_status')}")
    if se:
        lines.append(f"  seismicity: {se['n_inside']} inside, {se['n_at_or_above']} at/above threshold, {se['n_exempt']} exempt; "
                     f"largest M {se['largest']['magnitude']:.1f} ({se['largest']['event_id']})" if se['largest'] else '  seismicity: none inside')
        lines.append(f"  response: {se['trigger']['status']} - {se['trigger']['detail']}")
        lines.append(f"  goal: {se['goal']['status']} - {se['goal']['detail']}")
    if pk['gaps']:
        lines.append(f"  gaps ({len(pk['gaps'])}):")
        lines += [f"    - {g}" for g in pk['gaps']]
    return '\n'.join(lines)
