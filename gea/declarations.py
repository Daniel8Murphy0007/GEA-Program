"""The declarations sheet: everything the operator declares about a pad, in one file, checked before anything is written.

The program measures what its records hold; the rest the operator declares - who each well is and where, its
reference elevation, its directional survey, the rates and the AFE, the tools in the fleet, the drill-string runs,
the ESP penetrator and drive. Each has its own command and form; this module takes them all from one sheet a site
can keep in a spreadsheet and save as CSV:

- the sheet holds several small tables, one after another. A table starts with a header row whose first cell is the
  table's name in brackets (`[wells]`, `[rates]`, `[runs]` ...) and whose other cells name its columns; its rows
  leave the first cell empty. A row whose first cell starts with `#` is a comment. Blank rows are ignored;
- every row is checked against the workspace before anything is written: the well must exist (by its id or its
  name), every figure must parse, every declaration must carry its source, and nothing may contradict what is already
  declared (an overlapping rate, a fleet tool of the same id with another datasheet). One error and nothing is
  written;
- each row is planned as NEW, CHANGE (it replaces what is declared) or SAME (already declared exactly so: nothing to
  do), so the same sheet can be imported again and again;
- `template(ws)` writes the sheet for a workspace with every table's header, a comment saying what it takes, and
  what is already declared, so a site fills in the blanks; importing the template unchanged plans nothing but SAME;
- the import adds and replaces; it never removes. Removing is done where the declaration lives.

Each declaration is written through the same function its own command uses, so it is audited the same way, under the
actor who imported the sheet.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

MOTOR_COLS = ('motor_rev_per_gal', 'motor_flow_min_gpm', 'motor_flow_max_gpm', 'motor_max_operating_diff_psi', 'motor_max_diff_psi',
              'motor_max_torque_ft_lb', 'motor_lobes', 'motor_stages', 'motor_source')
TABLES: Dict[str, dict] = {
    'wells': {'cols': ('well', 'api_number', 'well_name', 'operator', 'lat', 'lon', 'datum', 'elevation_ft', 'elevation_datum', 'elevation_source'),
              'need': ('well',),
              'about': "who each well is and where: its API number, permit name and operator; its surface position (lat, lon, datum: NAD83, NAD27 or WGS84); its reference elevation (ft, KB/GL/DF, and where it came from)"},
    'rates': {'cols': ('well', 'what', 'usd_per_day', 'from', 'to', 'source'), 'need': ('well', 'what', 'usd_per_day', 'source'),
              'about': "day rates: what the rate pays for, USD per day, the period (YYYY-MM-DD or YYYY-MM-DDTHH:MM on the recorder's clock; blank is open) and the contract or invoice it came from"},
    'items': {'cols': ('well', 'what', 'usd', 'section', 'source'), 'need': ('well', 'what', 'usd', 'source'),
              'about': 'one-off costs: what it is, USD, the hole section number if it belongs to one, where the price came from'},
    'afe': {'cols': ('well', 'usd', 'source'), 'need': ('well', 'usd', 'source'), 'about': "each well's AFE in USD and its number or approval"},
    'surveys': {'cols': ('well', 'file', 'azimuth_reference', 'grid', 'declination_deg', 'md_unit', 'depth_reference', 'source'),
                'need': ('well', 'file', 'azimuth_reference', 'source'),
                'about': "directional surveys: the file (a path beside this sheet: a table with MD, Inc and Azi columns, or a WITSML trajectory), its azimuth reference (grid with the grid named, true, or magnetic with the declination), the MD unit (ft or m), the depth reference (KB/GL/DF) and where it came from"},
    'fleet': {'cols': ('class_id', 'maker', 'model', 'serial', 'od_in', 'datasheet', 'note', 'ratings'), 'need': ('class_id', 'maker', 'model', 'datasheet'),
              'about': "the operator's own tools: the tool-library class (`gea tools --action classes`), maker, model, serial, OD, the datasheet that rates it, and its ratings as key=value pairs separated by ; (lists with /)"},
    'runs': {'cols': ('well', 'run', 'start', 'end', 'depth_in_ft', 'depth_out_ft', 'note'), 'need': ('well', 'run'),
             'about': "drill-string runs: the run's name, its start and end on the recorder's clock, depth in and out"},
    'components': {'cols': ('well', 'run', 'class_id', 'maker', 'model', 'serial', 'od_in', 'length_ft', 'connection', 'motor_id', 'service_limit',
                            'service_basis', 'service_source', 'fleet_id', 'note') + MOTOR_COLS,
                   'need': ('well', 'run', 'class_id'),
                   'about': "each run's components from the bit up, in order: the class, maker, model, serial, OD, length, the motor's catalogue id (`gea tools --action motors`) or - for a motor not in the catalogue - its own figures (motor_*) with their source, the service interval with its basis and source"},
    'operations': {'cols': ('well', 'kind', 'start', 'end', 'source', 'note'), 'need': ('well', 'kind', 'start', 'source'),
                   'about': "operation windows for Operations & Seismicity: drilling, completion, disposal, injection or other, the start and end in UTC (blank end: still going), and the record it comes from"},
    'catalogs': {'cols': ('file', 'start', 'end', 'min_magnitude', 'lat', 'lon', 'radius_km', 'source'),
                 'need': ('file', 'start', 'end', 'min_magnitude', 'lat', 'lon', 'radius_km', 'source'),
                 'about': "earthquake catalogue exports (USGS ComCat text or a TexNet CSV, a path beside this sheet) with the window, minimum magnitude and circle they were queried for"},
    'penetrators': {'cols': ('well', 'penetrator', 'installed', 'installer', 'certified', 'certificate', 'motor', 'gauge_distance_in', 'ir_a_megohm',
                             'ir_b_megohm', 'ir_c_megohm', 'ir_test_volts', 'ir_min_megohm', 'ir_min_source', 'void_test_psi', 'void_test_minutes',
                             'void_held', 'note'),
                    'need': ('well', 'penetrator'),
                    'about': "the ESP wellhead penetrator installed: its catalogue or fleet id (`gea penetrator`), the date, the installer and whether the maker certified them, the motor (PMM or induction), the gauge distance, the insulation readings per conductor and the test voltage, the void test"},
    'esp_drives': {'cols': ('well', 'maker', 'model', 'max_volts', 'max_amps', 'freq_min_hz', 'freq_max_hz', 'phases', 'motor_volts', 'motor_amps', 'area',
                            'gas_group', 'temp_class', 'ambient_min_f', 'ambient_max_f', 'source'),
                   'need': ('well', 'source'),
                   'about': "the ESP drive's output limits and the motor nameplate, the wellhead's hazardous-area classification (e.g. Class I, Division 1), gas group, temperature class and the site's ambient range"},
    'contract': {'cols': ('day_start', 'report_date', 'operating', 'standby', 'repair', 'moving', 'force_majeure', 'repair_hours_per_job',
                          'repair_hours_per_month', 'source'),
                 'need': ('operating', 'source'),
                 'about': "the drilling contract's terms for the invoice check: the hour the report day starts on the rig's clock (06:00), whether a report is dated by the day it starts or ends (start/end), the day rates in USD (operating, and standby, repair, moving and force majeure where the contract has them), the repair allowance in hours a job and a month, and the contract it came from"},
    'rig_time': {'cols': ('w2w_min', 'trip_in_ft_h', 'trip_out_ft_h', 'source'), 'need': ('source',),
                 'about': "the rig-time targets from the drilling program: weight-to-weight connection minutes and tripping speeds in and out (ft/h); blank keeps the pad's own best quarter"},
    'block_weights': {'cols': ('well', 'block_weight_klbf'), 'need': ('well', 'block_weight_klbf'),
                      'about': "a well's travelling block weight in klbf, where the rig-time estimate from the hook load is wrong"},
    'casing': {'cols': ('well', 'size_in', 'shoe_md_ft', 'set', 'source'), 'need': ('well', 'size_in', 'shoe_md_ft', 'source'),
               'about': "casing shoes: the size in inches as written (13-3/8, 9-5/8, 7), the shoe's measured depth in ft, when it was set (the recorder's clock; blank: when the hole is 50 ft past it) and the tally or report it came from"},
    'settings': {'cols': ('setting', 'value'), 'need': ('setting', 'value'),
                 'about': "the program's settings: watch_enabled (yes/no), watch_every_min, watch_mode (operations/always), summary_enabled (yes/no), summary_at (HH:MM UTC), summary_send (yes/no)"},
}
ORDER = ('wells', 'fleet', 'rates', 'items', 'afe', 'surveys', 'runs', 'components', 'penetrators', 'esp_drives', 'operations', 'catalogs',
         'contract', 'rig_time', 'block_weights', 'casing', 'settings')
APPLY_ORDER = ('wells', 'fleet', 'rates', 'items', 'afe', 'surveys', 'runs', 'penetrators', 'esp_drives', 'operations', 'catalogs',
               'contract', 'rig_time', 'block_weights', 'casing', 'settings')
SETTINGS = ('watch_enabled', 'watch_every_min', 'watch_mode', 'summary_enabled', 'summary_at', 'summary_send')


class SheetError(ValueError):
    pass


# -- reading the sheet ---------------------------------------------------------------------------------
def parse(text: str) -> Tuple[Dict[str, List[dict]], List[str]]:
    """The tables in a sheet: {table: [row dict with '_row' its line number]}, and the errors found reading it."""
    if text.startswith('﻿'):
        text = text[1:]
    sample = text[:4096]
    delim = ';' if sample.count(';') > sample.count(',') and '[' in sample else ','
    out: Dict[str, List[dict]] = {}
    errs: List[str] = []
    cur, cols = None, []
    headless = 0
    for n, cells in enumerate(csv.reader(io.StringIO(text), delimiter=delim), start=1):
        cells = [c.strip() for c in cells]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        first = cells[0]
        if first.startswith('#'):
            continue
        m = re.fullmatch(r'\[\s*([A-Za-z_]+)\s*\]', first)
        if m:
            name = m.group(1).lower()
            if name not in TABLES:
                errs.append(f'row {n}: no table [{name}]; the tables are ' + ', '.join(f'[{t}]' for t in ORDER))
                cur = None
                continue
            cols = [c.lower().replace(' ', '_') for c in cells[1:]]
            bad = [c for c in cols if c and c not in TABLES[name]['cols']]
            miss = [c for c in TABLES[name]['need'] if c not in cols]
            if bad:
                errs.append(f"row {n}: [{name}] has no column {', '.join(bad)}; its columns are {', '.join(TABLES[name]['cols'])}")
            if miss:
                errs.append(f"row {n}: [{name}] needs the column {', '.join(miss)}")
            cur = name
            out.setdefault(name, [])
            continue
        if cur is None and not out:
            headless += 1
            continue
        if first:
            errs.append(f'row {n}: the first column holds a table name in brackets or is left empty, not {first!r}')
            continue
        if cur is None:
            errs.append(f'row {n}: a row before any table header (start a table with a row like [wells],well,api_number,...)')
            continue
        vals = cells[1:]
        if len(vals) > len(cols):
            extra = [v for v in vals[len(cols):] if v]
            if extra:
                errs.append(f'row {n}: [{cur}] has {len(cols)} columns but the row has more: {extra}')
        rec = {c: (vals[i] if i < len(vals) else '') for i, c in enumerate(cols) if c}
        rec['_row'] = n
        out[cur].append(rec)
    if not out and headless:
        errs.insert(0, f'no table header in {headless} row(s): this is not a declarations sheet - each table starts with a row like '
                       '[wells],well,api_number,... (gea declare --template writes one)')
    elif headless:
        errs.insert(0, f'row 1: {headless} row(s) before the first table header')
    return out, errs


def _f(v, what: str, positive: bool = True) -> Optional[float]:
    if v in (None, ''):
        return None
    try:
        x = float(str(v).replace(',', '').replace('$', ''))
    except ValueError:
        raise SheetError(f'{what} is not a number: {v!r}')
    if x != x or (positive and x <= 0):
        raise SheetError(f'{what} must be more than zero, not {v!r}')
    return x


def _when(v, what: str) -> Optional[str]:
    if v in (None, ''):
        return None
    t = str(v).strip().replace('Z', '').replace(' ', 'T', 1)
    try:
        d = datetime.fromisoformat(t[:19]) if len(t) > 10 else datetime.fromisoformat(t[:10])
    except ValueError:
        raise SheetError(f'{what} is not a date (YYYY-MM-DD or YYYY-MM-DDTHH:MM): {v!r}')
    return d.isoformat()


def _ratings(s: str) -> dict:
    out = {}
    for part in [p for p in (s or '').split(';') if p.strip()]:
        if '=' not in part:
            raise SheetError(f'ratings are key=value pairs separated by ;, not {part.strip()!r}')
        k, v = part.split('=', 1)
        k, v = k.strip().lower().replace(' ', '_'), v.strip()
        try:
            out[k] = float(v) if re.fullmatch(r'-?\d+(\.\d+)?', v) else v
        except ValueError:
            out[k] = v
        if isinstance(out[k], float) and out[k].is_integer():
            out[k] = int(out[k])
    return out


# -- planning ------------------------------------------------------------------------------------------
def _well_id(ws, ref: str, wells: List[dict]) -> str:
    r = (ref or '').strip()
    for w in wells:
        if w['id'] == r:
            return w['id']
    hits = [w for w in wells if (w.get('display') or '').strip().lower() == r.lower()]
    if len(hits) == 1:
        return hits[0]['id']
    if len(hits) > 1:
        raise SheetError(f'{r!r} names {len(hits)} wells; use the well id ({", ".join(w["id"] for w in hits)})')
    raise SheetError(f'no well {r!r} in this workspace (the import declares about wells already added; add the well first)')


def _fleet_add(ws, TX, cid, rec, od, ratings, actor) -> dict:
    r = TX.fleet_add(ws.path, cid, rec['maker'], rec['model'], rec['datasheet'], rec.get('serial', ''), od, rec.get('note', ''), actor, ratings or None)
    ws.audit(actor, 'fleet.add', {'id': r['id'], 'class': r['class_id'], 'datasheet': r['datasheet']})
    return r


def _norm(d: dict, drop=('declared_by', 'declared_utc', 'by', 'utc')) -> dict:
    return {k: v for k, v in d.items() if k not in drop}


def plan(ws, text: str, base_dir: str = '') -> dict:
    """Read and check a sheet against the workspace: every row planned as NEW, CHANGE or SAME, or an error. Nothing is
    written."""
    from . import drill_string as DS
    from . import economics as EC
    from . import geodesy as GD
    from . import penetrator as PN
    from . import tool_taxonomy as TX
    from . import well3d as W3
    from .interval import REF_DATUMS
    tables, errs = parse(text)
    wells = ws.wells()
    steps: List[dict] = []

    def add(table, row, wid, action, what, fn: Optional[Callable] = None):
        steps.append({'table': table, 'row': row, 'well': wid, 'action': action, 'what': what, '_fn': fn})

    def guard(table, rec, body):
        try:
            body()
        except (SheetError, ValueError, KeyError) as e:
            errs.append(f"row {rec['_row']}: [{table}] " + str(e.args[0] if e.args else e))

    # wells
    for rec in tables.get('wells', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            w = next(x for x in wells if x['id'] == wid)
            cur_i, cur_s = w.get('identity') or {}, w.get('surface') or {}
            identity = {k: rec.get(k, '').strip() for k in ('api_number', 'well_name', 'operator') if rec.get(k, '').strip()}
            if identity.get('api_number') and not re.fullmatch(r'[0-9][0-9-]{8,17}', identity['api_number']):
                raise SheetError(f"the API number is digits and dashes (e.g. 42-317-41234), not {identity['api_number']!r}")
            changes = [f"{k} {cur_i.get(k) or '-'} -> {v}" for k, v in identity.items() if cur_i.get(k) != v]
            surface = {}
            lat, lon = _f(rec.get('lat'), 'the latitude', False), _f(rec.get('lon'), 'the longitude', False)
            if (lat is None) != (lon is None):
                raise SheetError('a surface position needs both lat and lon')
            if lat is not None:
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise SheetError(f'the position {lat}, {lon} is not a latitude and longitude in degrees')
                d = GD.datum_name(rec.get('datum'))
                if d == 'UNKNOWN':
                    raise SheetError(f"the position needs its datum (NAD83, NAD27 or WGS84), not {rec.get('datum')!r}")
                surface.update({'lat': lat, 'lon': lon, 'datum': d})
                if (cur_s.get('lat'), cur_s.get('lon'), cur_s.get('datum')) != (lat, lon, d):
                    changes.append(f"position {cur_s.get('lat', '-')}, {cur_s.get('lon', '-')} {cur_s.get('datum', '')} -> {lat}, {lon} {d}".replace('  ', ' '))
            ef = _f(rec.get('elevation_ft'), 'the reference elevation (ft)', False)
            if ef is not None:
                ed = (rec.get('elevation_datum') or '').strip().upper()
                if ed not in REF_DATUMS:
                    raise SheetError(f'the elevation datum is one of {", ".join(REF_DATUMS)} (kelly bushing, ground level, derrick floor), not {ed!r}')
                src = (rec.get('elevation_source') or '').strip()
                if not src:
                    raise SheetError('a reference elevation needs its source (e.g. "completion report W-2")')
                if not surface and not cur_s.get('lat'):
                    raise SheetError('a reference elevation needs the surface position (lat, lon, datum) first')
                surface['elevation'] = {'ft': ef, 'datum': ed, 'source': src}
                ce = cur_s.get('elevation') or {}
                if (ce.get('ft'), ce.get('datum'), ce.get('source')) != (ef, ed, src):
                    changes.append(f"elevation {(str(ce.get('ft')) + ' ' + ce.get('datum', '')) if ce else '-'} -> {ef:g} ft {ed} ({src})")
            if not identity and not surface:
                add('wells', rec['_row'], wid, 'SAME', 'nothing declared on this row')
                return
            fn = (lambda actor, wid=wid, s=surface, i=identity: ws.set_well(wid, surface=s or None, identity=i or None, actor=actor))
            add('wells', rec['_row'], wid, 'CHANGE' if changes and (cur_i or cur_s) else ('NEW' if changes else 'SAME'),
                '; '.join(changes) or 'already declared', fn if changes else None)
        guard('wells', rec, b)

    # fleet
    planned_fleet: Dict[str, dict] = {}
    for rec in tables.get('fleet', []):
        def b(rec=rec):
            cid = rec['class_id'].strip()
            if cid not in TX.TAXONOMY:
                raise SheetError(f'no tool class {cid!r} (`gea tools --action classes`)')
            for k in ('maker', 'model', 'datasheet'):
                if not rec.get(k, '').strip():
                    raise SheetError(f'a fleet tool needs its {k}')
            ratings = _ratings(rec.get('ratings', ''))
            tid = TX._slug(f"{rec['maker']} {rec['model']} {rec.get('serial', '')}".strip())
            old = next((t for t in TX.fleet_list(ws.path) if t['id'] == tid), None)
            od = _f(rec.get('od_in'), 'the outside diameter (in)')
            planned_fleet[tid] = {'class_id': cid, 'ratings': ratings}
            if old:
                same = (old['class_id'], old['datasheet'], old.get('od_in'), old.get('ratings') or {}) == (cid, rec['datasheet'].strip(), od, ratings)
                if not same:
                    raise SheetError(f'{tid} is already in the fleet with another class, datasheet, OD or ratings: remove it from the fleet first, then import')
                add('fleet', rec['_row'], None, 'SAME', f'{tid} already in the fleet')
                return
            add('fleet', rec['_row'], None, 'NEW', f"{tid}: {TX.TAXONOMY[cid]['name']} ({rec['datasheet'].strip()})",
                lambda actor, rec=rec, cid=cid, od=od, ratings=ratings: _fleet_add(ws, TX, cid, rec, od, ratings, actor))
        guard('fleet', rec, b)

    costs = EC.costs(ws)['wells']
    # rates
    new_rates: Dict[str, List[Tuple[datetime, datetime, int]]] = {}
    for rec in tables.get('rates', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            usd = _f(rec['usd_per_day'], 'the day rate (USD)')
            a, z = _when(rec.get('from'), 'the start'), _when(rec.get('to'), 'the end')
            if a and z and z <= a:
                raise SheetError('the rate period must end after it starts')
            what, src = rec['what'].strip(), rec['source'].strip()
            if not what or not src:
                raise SheetError('a rate needs what it pays for and where it came from')
            have = (costs.get(wid) or {}).get('rates', [])
            if any(abs(r['usd_per_day'] - round(usd, 2)) < 0.005 and r['what'] == what and r['from'] == a and r['to'] == z for r in have):
                add('rates', rec['_row'], wid, 'SAME', f'{usd:,.2f} USD/day {what} already declared')
                return
            lo, hi = datetime.fromisoformat(a) if a else datetime.min, datetime.fromisoformat(z) if z else datetime.max
            for r in have:
                ra = datetime.fromisoformat(r['from']) if r['from'] else datetime.min
                rb = datetime.fromisoformat(r['to']) if r['to'] else datetime.max
                if lo < rb and hi > ra:
                    raise SheetError(f"the period overlaps the {r['usd_per_day']:,.2f} USD/day {r['what']} rate already declared "
                                     f"({r['from'] or 'open'} to {r['to'] or 'open'}); remove it first (gea economics --remove rate)")
            for (ra, rb, n) in new_rates.get(wid, []):
                if lo < rb and hi > ra:
                    raise SheetError(f'the period overlaps the rate on row {n}')
            new_rates.setdefault(wid, []).append((lo, hi, rec['_row']))
            add('rates', rec['_row'], wid, 'NEW', f"{usd:,.2f} USD/day {what}, {a or 'open'} to {z or 'open'} ({src})",
                lambda actor, wid=wid, usd=usd, what=what, src=src, a=a, z=z: EC.set_rate(ws, wid, actor, usd, what, src, a, z))
        guard('rates', rec, b)

    for rec in tables.get('items', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            usd = _f(rec['usd'], 'the item (USD)')
            what, src = rec['what'].strip(), rec['source'].strip()
            if not what or not src:
                raise SheetError('an item needs what it is and where its price came from')
            sec = rec.get('section', '').strip()
            if sec and not sec.isdigit():
                raise SheetError(f'the section is the hole section number (1, 2, ...), not {sec!r}')
            sec_i = int(sec) if sec else None
            have = (costs.get(wid) or {}).get('items', [])
            if any(abs(r['usd'] - round(usd, 2)) < 0.005 and r['what'] == what and r['source'] == src and r.get('section') == sec_i for r in have):
                add('items', rec['_row'], wid, 'SAME', f'{what} already declared')
                return
            add('items', rec['_row'], wid, 'NEW', f'{usd:,.2f} USD {what} ({src})',
                lambda actor, wid=wid, usd=usd, what=what, src=src, sec_i=sec_i: EC.add_item(ws, wid, actor, usd, what, src, sec_i))
        guard('items', rec, b)

    for rec in tables.get('afe', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            usd, src = _f(rec['usd'], 'the AFE (USD)'), rec['source'].strip()
            if not src:
                raise SheetError('an AFE needs where it came from')
            old = (costs.get(wid) or {}).get('afe')
            if old and abs(old['usd'] - round(usd, 2)) < 0.005 and old['source'] == src:
                add('afe', rec['_row'], wid, 'SAME', 'AFE already declared')
                return
            add('afe', rec['_row'], wid, 'CHANGE' if old else 'NEW', (f"{old['usd']:,.2f} -> " if old else '') + f'{usd:,.2f} USD ({src})',
                lambda actor, wid=wid, usd=usd, src=src: EC.set_afe(ws, wid, actor, usd, src))
        guard('afe', rec, b)

    for rec in tables.get('surveys', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            p = rec['file'].strip()
            path = p if os.path.isabs(p) else os.path.join(base_dir or '.', p)
            if not os.path.isfile(path):
                raise SheetError(f'no survey file {p!r} beside the sheet' + ('' if base_dir else ' (a sheet sent through the browser carries no files: attach surveys on the well page or from the command line)'))
            with open(path, 'rb') as f:
                content = f.read()
            ref = rec['azimuth_reference'].strip().lower()
            if ref not in ('grid', 'true', 'magnetic'):
                raise SheetError("the azimuth reference is grid, true or magnetic")
            dec = _f(rec.get('declination_deg'), 'the declination (degrees)', False)
            if ref == 'magnetic' and dec is None:
                raise SheetError('a magnetic azimuth reference needs the declination used')
            if ref == 'grid' and not rec.get('grid', '').strip():
                raise SheetError('a grid azimuth reference needs its grid named (e.g. TX-C or UTM14N)')
            unit = (rec.get('md_unit') or 'ft').strip().lower()
            if unit not in ('ft', 'm'):
                raise SheetError('the MD unit is ft or m')
            txt = content.decode('utf-8', errors='replace')
            if '<trajectory' not in txt[:4000]:
                W3.read_survey_csv(txt, unit)
            sha = hashlib.sha256(content).hexdigest()
            old = ws.well(wid).get('trajectory') or {}
            if old.get('sha256') == sha and old.get('azimuth_reference') == ref and old.get('source') == rec['source'].strip():
                add('surveys', rec['_row'], wid, 'SAME', f'{os.path.basename(p)} already attached')
                return
            add('surveys', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{os.path.basename(p)}, azimuths {ref} ({rec['source'].strip()})",
                lambda actor, wid=wid, p=p, content=content, ref=ref, dec=dec, unit=unit, rec=rec:
                    W3.attach_trajectory(ws, wid, os.path.basename(p), content, actor, ref, unit, rec.get('depth_reference', '').strip() or None,
                                         rec.get('grid', '').strip() or None, dec, rec['source'].strip()))
        guard('surveys', rec, b)

    # runs with their components
    comps: Dict[Tuple[str, str], List[dict]] = {}
    for rec in tables.get('components', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            comps.setdefault((wid, rec['run'].strip().lower()), []).append(rec)
        guard('components', rec, b)
    used = set()
    existing = DS.runs(ws)
    for rec in tables.get('runs', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            name = rec['run'].strip()
            key = (wid, name.lower())
            if key in used:
                raise SheetError(f'run {name!r} on {wid} is on the sheet twice')
            used.add(key)
            cs = comps.get(key) or []
            if not cs:
                raise SheetError(f'run {name!r} has no rows in [components]: list its components from the bit up')
            clist = []
            for c in cs:
                d = {k: c.get(k, '').strip() for k in TABLES['components']['cols'] if k not in ('well', 'run') and k not in MOTOR_COLS}
                d = {k: v for k, v in d.items() if v}
                mot = {k[len('motor_'):]: c.get(k, '').strip() for k in MOTOR_COLS if c.get(k, '').strip()}
                if mot:
                    if d.get('motor_id'):
                        raise SheetError(f"component on row {c['_row']}: give the motor's catalogue id or its own figures, not both")
                    d['motor'] = mot
                try:
                    DS._component(d, len(clist))
                except (ValueError, KeyError) as e:
                    raise SheetError(f"component on row {c['_row']}: " + str(e.args[0] if e.args else e))
                clist.append(d)
            a, z = _when(rec.get('start'), 'the start'), _when(rec.get('end'), 'the end')
            if a and z and z <= a:
                raise SheetError('the run ends before it starts')
            din, dout = _f(rec.get('depth_in_ft'), 'the depth in', False), _f(rec.get('depth_out_ft'), 'the depth out', False)
            old = next((r for r in existing if r['well_id'] == wid and r['name'].lower() == name.lower()), None)
            new_c = [DS._component(d, i) for i, d in enumerate(clist)]
            if old:
                same = ((old['start'], old['end'], old.get('depth_in_ft'), old.get('depth_out_ft'), old.get('note') or '') ==
                        (DS._when(a, ''), DS._when(z, ''), din, dout, rec.get('note', '').strip())
                        and [_norm(c) for c in old['components']] == [_norm(c) for c in new_c])
                if same:
                    add('runs', rec['_row'], wid, 'SAME', f'{name}: {len(new_c)} component(s), already declared')
                    return
            add('runs', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{name}: {len(new_c)} component(s) from the bit up ({', '.join(c['class_id'] for c in new_c)})",
                lambda actor, wid=wid, name=name, clist=clist, a=a, z=z, din=din, dout=dout, rec=rec, rid=(old or {}).get('id'):
                    DS.save_run(ws, actor, wid, name, clist, a, z, din, dout, rec.get('note', '').strip(), rid))
        guard('runs', rec, b)
    for key, cs in comps.items():
        if key not in used:
            errs.append(f"row {cs[0]['_row']}: [components] name run {cs[0]['run']!r} on {key[0]}, which is not in [runs]")

    pen_recs = PN.records(ws)
    for rec in tables.get('penetrators', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            pid = rec['penetrator'].strip()
            pen = None
            if pid in planned_fleet and not any(p['id'] == pid for p in PN.penetrators()):
                if planned_fleet[pid]['class_id'] != 'wellhead_penetrator':
                    raise SheetError(f'{pid} is not a wellhead penetrator in [fleet]')
                pen = {'id': pid, 'origin': 'fleet'}
            kw = dict(installed=rec.get('installed'), installer=rec.get('installer', ''), certified=rec.get('certified'),
                      certificate=rec.get('certificate', ''), motor_type=rec.get('motor', ''), gauge_distance_in=rec.get('gauge_distance_in'),
                      insulation={ph: rec.get(f'ir_{ph.lower()}_megohm') for ph in ('A', 'B', 'C') if rec.get(f'ir_{ph.lower()}_megohm')},
                      ir_test_volts=rec.get('ir_test_volts'), ir_min_megohm=rec.get('ir_min_megohm'), ir_min_source=rec.get('ir_min_source', ''),
                      void_test_psi=rec.get('void_test_psi'), void_test_minutes=rec.get('void_test_minutes'), void_held=rec.get('void_held'),
                      note=rec.get('note', ''))
            new = PN.install_record(ws, wid, pid, pen=pen, **kw)
            old = (pen_recs.get(wid) or {}).get('install')
            if old and _norm(old) == _norm(new):
                add('penetrators', rec['_row'], wid, 'SAME', f'{pid} installation already recorded')
                return
            add('penetrators', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{pid} installed {new['installed'] or '(date not given)'}"
                + (f", {new['motor_type']}" if new['motor_type'] else ''),
                lambda actor, wid=wid, pid=pid, kw=kw: PN.save_install(ws, actor, wid, pid, **kw))
        guard('penetrators', rec, b)

    for rec in tables.get('esp_drives', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            kw = {k: rec.get(k, '') for k in TABLES['esp_drives']['cols'] if k != 'well'}
            new = PN.drive_record(ws, wid, **kw)
            old = (pen_recs.get(wid) or {}).get('drive')
            if old and _norm(old) == _norm(new):
                add('esp_drives', rec['_row'], wid, 'SAME', 'drive already declared')
                return
            add('esp_drives', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{(new['maker'] + ' ' + new['model']).strip() or 'drive'} ({new['source']})",
                lambda actor, wid=wid, kw=kw: PN.save_drive(ws, actor, wid, **kw))
        guard('esp_drives', rec, b)

    from . import op_seismicity as OS
    ops_have = OS.operations(ws)
    seen_ops = set()
    for rec in tables.get('operations', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            new = OS.operation_record(ws, wid, rec.get('kind', ''), rec.get('start'), rec.get('end'), rec.get('source', ''), rec.get('note', ''))
            key = (wid, new['kind'], new['start'])
            if key in seen_ops:
                raise SheetError(f"the {new['kind']} operation starting {new['start']} on {wid} is on the sheet twice")
            seen_ops.add(key)
            old = next((o for o in ops_have if (o['well_id'], o['kind'], o['start']) == key), None)
            if old and (old['end'], old['source'], old['note']) == (new['end'], new['source'], new['note']):
                add('operations', rec['_row'], wid, 'SAME', f"{new['kind']} from {new['start']} already declared")
                return
            add('operations', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{new['kind']} {new['start']} to {new['end'] or 'open'} ({new['source']})",
                lambda actor, wid=wid, rec=rec, oid=(old or {}).get('id'): OS.save_operation(ws, actor, wid, rec.get('kind', ''), rec.get('start'), rec.get('end'),
                                                                                              rec.get('source', ''), rec.get('note', ''), oid))
        guard('operations', rec, b)
    cats_have = {c['file']: c for c in OS.catalogs(ws)}
    for rec in tables.get('catalogs', []):
        def b(rec=rec):
            p = rec['file'].strip()
            path = p if os.path.isabs(p) else os.path.join(base_dir or '.', p)
            if not os.path.isfile(path):
                raise SheetError(f'no catalogue file {p!r} beside the sheet' + ('' if base_dir else ' (a sheet sent through the browser carries no files: attach catalogues on the Operations & Seismicity tab or from the command line)'))
            with open(path, 'rb') as f:
                content = f.read()
            new = OS.catalog_record(os.path.basename(p), content, rec['start'], rec['end'], rec['min_magnitude'], rec['lat'], rec['lon'], rec['radius_km'], rec['source'])
            old = cats_have.get(new['file'])
            if old and all(old.get(k) == new[k] for k in ('sha256', 'start', 'end', 'min_magnitude', 'circle', 'source')):
                add('catalogs', rec['_row'], None, 'SAME', f"{new['file']} already attached")
                return
            add('catalogs', rec['_row'], None, 'CHANGE' if old else 'NEW', f"{new['file']}: {new['n_events']} event(s), {new['start']} to {new['end']}, M >= {new['min_magnitude']:g}",
                lambda actor, p=p, content=content, rec=rec: OS.attach_catalog(ws, actor, os.path.basename(p), content, rec['start'], rec['end'], rec['min_magnitude'],
                                                                               rec['lat'], rec['lon'], rec['radius_km'], rec['source']))
        guard('catalogs', rec, b)

    # the contract, rig time, casing and settings
    from . import invoice_check as IC
    from . import rig_state as RS
    from . import seismic_watch as SW
    from . import pad_summary as PS
    for rec in tables.get('contract', [])[:1]:
        def b(rec=rec):
            rates = {k: rec.get(k) for k in IC.KINDS if (rec.get(k) or '').strip()}
            new = IC.terms_record(rec.get('source', ''), (rec.get('day_start') or '06:00').strip(), (rec.get('report_date') or 'start').strip(), rates,
                                  rec.get('repair_hours_per_job'), rec.get('repair_hours_per_month'))
            old = IC.terms(ws)
            keys = ('source', 'day_start', 'report_date', 'rates_usd_per_day', 'repair_hours_per_job', 'repair_hours_per_month')
            if old and all(old.get(k) == new[k] for k in keys):
                add('contract', rec['_row'], None, 'SAME', 'the contract terms already declared')
                return
            add('contract', rec['_row'], None, 'CHANGE' if old else 'NEW',
                f"operating {new['rates_usd_per_day']['operating']:,.2f} USD/day, report day from {new['day_start']} ({new['source']})",
                lambda actor, rec=rec, rates=rates: IC.set_terms(ws, actor, rec.get('source', ''), (rec.get('day_start') or '06:00').strip(),
                                                                (rec.get('report_date') or 'start').strip(), rates, rec.get('repair_hours_per_job'),
                                                                rec.get('repair_hours_per_month')))
        guard('contract', rec, b)
    if len(tables.get('contract', [])) > 1:
        errs.append(f"row {tables['contract'][1]['_row']}: [contract] the contract is one row")
    for rec in tables.get('rig_time', [])[:1]:
        def b(rec=rec):
            vals = {k: _f(rec.get(k), k) for k in ('w2w_min', 'trip_in_ft_h', 'trip_out_ft_h')}
            if not any(v is not None for v in vals.values()):
                raise SheetError('give at least one target')
            cur = RS.settings(ws).get('targets', {})
            want = {k: v for k, v in vals.items() if v is not None}
            if all(cur.get(k) == v for k, v in want.items()) and RS.settings(ws).get('targets_source') == rec['source'].strip():
                add('rig_time', rec['_row'], None, 'SAME', 'the targets already declared')
                return
            add('rig_time', rec['_row'], None, 'CHANGE' if cur else 'NEW', ', '.join(f"{k.replace('_', ' ')} {v:g}" for k, v in want.items()) + f" ({rec['source'].strip()})",
                lambda actor, vals=vals, rec=rec: RS.set_settings(ws, actor, rec['source'], vals['w2w_min'], vals['trip_in_ft_h'], vals['trip_out_ft_h']))
        guard('rig_time', rec, b)
    bw_have = RS.settings(ws).get('block_weight_klbf', {})
    for rec in tables.get('block_weights', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            v = _f(rec.get('block_weight_klbf'), 'the block weight')
            if bw_have.get(wid) == v:
                add('block_weights', rec['_row'], wid, 'SAME', f'{v:g} klbf already declared')
                return
            add('block_weights', rec['_row'], wid, 'CHANGE' if wid in bw_have else 'NEW', f'block weight {v:g} klbf',
                lambda actor, wid=wid, v=v: RS.set_settings(ws, actor, '', None, None, None, (wid, v)))
        guard('block_weights', rec, b)
    cs_have = RS.casing(ws)
    for rec in tables.get('casing', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            new = RS.casing_record(ws, wid, rec.get('size_in', ''), rec.get('shoe_md_ft'), rec.get('source', ''), rec.get('set') or None)
            old = next((x for x in cs_have.get(wid, []) if x['size'] == new['size']), None)
            if old == new:
                add('casing', rec['_row'], wid, 'SAME', f"{new['size']} in shoe already declared")
                return
            add('casing', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{new['size']} in shoe at {new['shoe_md_ft']:,.0f} ft ({new['source']})",
                lambda actor, wid=wid, rec=rec: RS.set_casing(ws, actor, wid, rec.get('size_in', ''), rec.get('shoe_md_ft'), rec.get('source', ''), rec.get('set') or None))
        guard('casing', rec, b)
    sw_now, ps_now = SW.settings(ws), PS.settings(ws)
    yes = lambda v: str(v).strip().lower() in ('yes', 'y', 'true', '1', 'on')
    for rec in tables.get('settings', []):
        def b(rec=rec):
            k, v = rec['setting'].strip(), rec['value'].strip()
            if k not in SETTINGS:
                raise SheetError(f"no setting {k!r}: one of {', '.join(SETTINGS)}")
            if k.endswith(('_enabled', '_send')) and v.lower() not in ('yes', 'no', 'y', 'n', 'true', 'false', '1', '0', 'on', 'off'):
                raise SheetError(f'{k} is yes or no, not {v!r}')
            if k == 'watch_every_min':
                x = _f(v, k)
                cur, want, fn = sw_now['every_min'], x, (lambda actor, x=x: SW.set_settings(ws, actor, every_min=x))
            elif k == 'watch_mode':
                if v not in ('operations', 'always'):
                    raise SheetError("watch_mode is 'operations' or 'always'")
                cur, want, fn = sw_now['mode'], v, (lambda actor, v=v: SW.set_settings(ws, actor, mode=v))
            elif k == 'watch_enabled':
                cur, want, fn = sw_now['enabled'], yes(v), (lambda actor, v=v: SW.set_settings(ws, actor, enabled=yes(v)))
            elif k == 'summary_enabled':
                cur, want, fn = ps_now['enabled'], yes(v), (lambda actor, v=v: PS.set_settings(ws, actor, enabled=yes(v)))
            elif k == 'summary_send':
                cur, want, fn = ps_now['send'], yes(v), (lambda actor, v=v: PS.set_settings(ws, actor, send=yes(v)))
            else:
                if not re.fullmatch(r'([01]?\d|2[0-3]):[0-5]\d', v):
                    raise SheetError('summary_at is HH:MM, 24-hour, UTC')
                want = f"{int(v.split(':')[0]):02d}:{v.split(':')[1]}"
                cur, fn = ps_now['at'], (lambda actor, w_=want: PS.set_settings(ws, actor, at=w_))
            if cur == want:
                add('settings', rec['_row'], None, 'SAME', f'{k} already {_g(want)}')
                return
            add('settings', rec['_row'], None, 'CHANGE', f'{k} {_g(cur)} -> {_g(want)}', fn)
        guard('settings', rec, b)

    _plan_engineering(ws, tables, wells, add, guard, base_dir)
    steps.sort(key=lambda s: (APPLY_ORDER.index(s['table']), s['row']))
    counts = {k: sum(1 for s in steps if s['action'] == k) for k in ('NEW', 'CHANGE', 'SAME')}
    return {'steps': steps, 'errors': sorted(errs, key=lambda e: int(re.match(r'row (\d+)', e).group(1)) if re.match(r'row (\d+)', e) else 0),
            'counts': counts, 'tables': {k: len(v) for k, v in tables.items()}}


def apply(ws, text: str, actor: str, base_dir: str = '', write: bool = False) -> dict:
    """Plan the sheet; with write, and only when it has no error, write every NEW and CHANGE in order."""
    p = plan(ws, text, base_dir)
    p['written'] = []
    p['applied'] = False
    if write and not p['errors']:
        for s in p['steps']:
            if s['_fn'] is None:
                continue
            try:
                s['_fn'](actor)
            except Exception as e:                     # a write the plan did not foresee: stop, and say what was written
                p['errors'].append(f"row {s['row']}: [{s['table']}] while writing: {e.args[0] if e.args else e}")
                p['stopped_at'] = s['row']
                break
            p['written'].append(s['row'])
        p['applied'] = not p['errors']
        ws.audit(actor, 'declarations.import', {'rows_written': len(p['written']), 'counts': p['counts'], 'complete': p['applied']})
    for s in p['steps']:
        s.pop('_fn', None)
    return p


def text(p: dict) -> str:
    L = []
    for s in p['steps']:
        L.append(f"  row {s['row']:>4}  {s['action']:<6} [{s['table']}] {(s['well'] + ': ') if s['well'] else ''}{s['what']}")
    c = p['counts']
    head = f"{c['NEW']} new, {c['CHANGE']} change(s), {c['SAME']} already declared"
    if p['errors']:
        L.append('')
        L.append(f"{len(p['errors'])} error(s) - nothing was written:" if not p.get('written') else f"{len(p['errors'])} error(s):")
        L += ['  ' + e for e in p['errors']]
    if p.get('applied'):
        tail = f"written: {len(p['written'])} row(s)."
    elif p['errors']:
        tail = 'fix the rows above and import again.' if not p.get('written') else f"stopped after writing {len(p['written'])} row(s); the rest were not written."
    elif c['NEW'] + c['CHANGE']:
        tail = 'nothing written yet: import again with --apply to write the NEW and CHANGE rows.'
    else:
        tail = 'nothing to write: the workspace already holds every row.'
    return head + '\n' + '\n'.join(L) + ('\n' if L else '') + tail


# -- the template --------------------------------------------------------------------------------------
def _g(v) -> str:
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v)


def template(ws) -> str:
    """The sheet for this workspace: every table with its header and what it takes, filled with what is already
    declared (so importing it unchanged writes nothing)."""
    from . import drill_string as DS
    from . import economics as EC
    from . import penetrator as PN
    from . import tool_taxonomy as TX
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['# GEA declarations sheet for ' + (ws.manifest.get('name') or 'this workspace') + '. Fill in the blanks, save as CSV, then: gea declare --workspace <folder> --file <this sheet> (add --apply to write).'])
    w.writerow(['# A table starts with its name in brackets in the first column; its rows leave the first column empty. Lines starting with # are comments.'])
    wells = ws.wells()
    costs = EC.costs(ws)['wells']
    pen = PN.records(ws)

    def head(t):
        w.writerow([])
        w.writerow(['# ' + TABLES[t]['about']])
        w.writerow([f'[{t}]'] + list(TABLES[t]['cols']))

    head('wells')
    for x in wells:
        i, s = x.get('identity') or {}, x.get('surface') or {}
        e = s.get('elevation') or {}
        w.writerow(['', x['id'], i.get('api_number', ''), i.get('well_name', ''), i.get('operator', ''), _g(s.get('lat')), _g(s.get('lon')),
                    s.get('datum', '') if s.get('lat') is not None else '', _g(e.get('ft')), e.get('datum', ''), e.get('source', '')])
    head('fleet')
    for t in TX.fleet_list(ws.path):
        r = t.get('ratings') or {}
        w.writerow(['', t['class_id'], t['maker'], t['model'], t.get('serial', ''), _g(t.get('od_in')), t['datasheet'], t.get('note', ''),
                    '; '.join(f"{k}={'/'.join(map(_g, v)) if isinstance(v, list) else _g(v)}" for k, v in r.items())])
    head('rates')
    for wid, c in costs.items():
        for r in c.get('rates', []):
            w.writerow(['', wid, r['what'], _g(r['usd_per_day']), r['from'] or '', r['to'] or '', r['source']])
    head('items')
    for wid, c in costs.items():
        for r in c.get('items', []):
            w.writerow(['', wid, r['what'], _g(r['usd']), _g(r.get('section')), r['source']])
    head('afe')
    for wid, c in costs.items():
        if c.get('afe'):
            w.writerow(['', wid, _g(c['afe']['usd']), c['afe']['source']])
    head('surveys')
    w.writerow(['# a survey already attached is kept beside its well; list it here only to replace it'])
    head('runs')
    rs = DS.runs(ws)
    for r in rs:
        w.writerow(['', r['well_id'], r['name'], r['start'] or '', r['end'] or '', _g(r.get('depth_in_ft')), _g(r.get('depth_out_ft')), r.get('note', '')])
    head('components')
    for r in rs:
        for c in r['components']:
            sv = c.get('service') or {}
            m = c.get('motor') or {}
            own = bool(m) and not m.get('id')
            fl = m.get('flow_gpm') or [None, None]
            w.writerow(['', r['well_id'], r['name'], c['class_id'], c['maker'], c['model'], c.get('serial', ''), _g(c.get('od_in')), _g(c.get('length_ft')),
                        c.get('connection', ''), m.get('id') or '', _g(sv.get('limit')), sv.get('basis', '') if sv else '', sv.get('source', ''),
                        c.get('fleet_id') or '', c.get('note', '')]
                       + ([_g(m.get('rev_per_gal')), _g(fl[0]) if fl[0] else '', _g(fl[1]) if fl[1] else '', _g(m.get('max_operating_diff_psi')),
                           _g(m.get('max_diff_psi')), _g(m.get('max_torque_ft_lb')),
                           f"{m['rotor_lobes']}/{m['stator_lobes']}" if m.get('rotor_lobes') else '', _g(m.get('stages')), m.get('source', '')] if own else []))
    head('penetrators')
    for wid, rec in pen.items():
        i = rec.get('install')
        if i:
            ir = i.get('insulation_megohm') or {}
            w.writerow(['', wid, i['penetrator'], i.get('installed') or '', i.get('installer', ''), _g(i.get('installer_certified')), i.get('certificate', ''),
                        i.get('motor_type') or '', _g(i.get('gauge_distance_in')), _g(ir.get('A')), _g(ir.get('B')), _g(ir.get('C')),
                        _g(i.get('ir_test_volts')), _g(i.get('ir_min_megohm')), i.get('ir_min_source', ''), _g(i.get('void_test_psi')),
                        _g(i.get('void_test_minutes')), _g(i.get('void_held')), i.get('note', '')])
    head('esp_drives')
    for wid, rec in pen.items():
        d = rec.get('drive')
        if d:
            w.writerow(['', wid] + [_g(d.get(k)) if d.get(k) is not None else '' for k in TABLES['esp_drives']['cols'][1:]])
    from . import op_seismicity as OS
    head('operations')
    for o in OS.operations(ws):
        w.writerow(['', o['well_id'], o['kind'], o['start'], o['end'] or '', o['source'], o.get('note', '')])
    head('catalogs')
    for c in OS.catalogs(ws):
        w.writerow(['# ' + c['file'] + ' is attached (' + str(c['n_events']) + ' events); list it here only to replace it'])
    from . import invoice_check as IC
    from . import rig_state as RS
    from . import seismic_watch as SW
    from . import pad_summary as PS
    head('contract')
    t = IC.terms(ws)
    if t:
        r = t['rates_usd_per_day']
        w.writerow(['', t['day_start'], t.get('report_date', 'start')] + [_g(r.get(k)) for k in IC.KINDS]
                   + [_g(t.get('repair_hours_per_job')), _g(t.get('repair_hours_per_month')), t['source']])
    head('rig_time')
    rs_ = RS.settings(ws)
    if rs_.get('targets'):
        tg = rs_['targets']
        w.writerow(['', _g(tg.get('w2w_min')), _g(tg.get('trip_in_ft_h')), _g(tg.get('trip_out_ft_h')), rs_.get('targets_source', '')])
    head('block_weights')
    for wid, v in rs_.get('block_weight_klbf', {}).items():
        w.writerow(['', wid, _g(v)])
    head('casing')
    for wid, lst in RS.casing(ws).items():
        for c in lst:
            w.writerow(['', wid, c['size'], _g(c['shoe_md_ft']), c.get('set') or '', c['source']])
    _template_engineering(ws, w, head)
    head('settings')
    sw, ps = SW.settings(ws), PS.settings(ws)
    for k, v in (('watch_enabled', _g(bool(sw['enabled']))), ('watch_every_min', _g(float(sw['every_min']))), ('watch_mode', sw['mode']),
                 ('summary_enabled', _g(bool(ps['enabled']))), ('summary_at', ps['at']), ('summary_send', _g(bool(ps['send'])))):
        w.writerow(['', k, v])
    return buf.getvalue()


# -- the engineering declarations (v0.59): plans and survey programs, pore pressure, torque and drag, hydraulics, frac
# stages, production. Each row is written through its own module's function, as its own tab or command writes it.
ENG_TABLES: Dict[str, dict] = {
    'survey_program': {'cols': ('well', 'program', 'source'), 'need': ('well', 'program', 'source'),
                       'about': "the survey tools by depth for the collision check and plan vs actual: one tool model ('MWD+SRGM') or runs by measured depth in metres ('0-1500:MWD+SRGM, 1500-:MWD+IFR1'), and the survey program it came from (`gea plan --models` lists the models)"},
    'plans': {'cols': ('well', 'file', 'azimuth_reference', 'grid', 'declination_deg', 'md_unit', 'source'), 'need': ('well', 'file', 'azimuth_reference', 'source'),
              'about': "directional plans: the file (a path beside this sheet: MD, Inc and Azi columns, an Excel table or a WITSML trajectory), its azimuth reference (grid with the grid named, true, or magnetic with the declination), the MD unit and the plan's revision"},
    'pore_pressure': {'cols': ('well', 'indicator', 'exponent', 'nct_top_m', 'nct_bottom_m', 'gr_cutoff', 'normal_gradient_psi_ft', 'poisson',
                               'density_above_gcc', 'water_depth_m', 'air_gap_m', 'smooth_m', 'bit_in', 'source'),
                      'need': ('well', 'source'),
                      'about': "how a well's pore pressure is read: the indicator (auto, sonic, resistivity, dexponent), Eaton's exponent, the normally pressured interval (m TVD), the shale cut-off (gAPI), the normal gradient (psi/ft), Poisson's ratio, the density above the logs, the sea, and the bit size (in) the d-exponent needs; blank keeps the default"},
    'pressures': {'cols': ('well', 'depth_m', 'depth_kind', 'value', 'unit', 'kind', 'source'), 'need': ('well', 'depth_m', 'value', 'kind', 'source'),
                  'about': "measured pressures: the depth in metres (tvd or md), the value in psi or ppg, the kind (pore: a formation test; kick; lot: a leak-off test; fit: a formation-integrity test) and the test it came from"},
    'torque_drag': {'cols': ('well', 'string', 'mud_ppg', 'mu_cased', 'mu_open', 'block_klbf', 'source'), 'need': ('well', 'source'),
                    'about': "the string for torque and drag, from the bit up: what:length_ft:od_in:lb_ft_in_air:contact_od_in sections separated by ; (the last with no length: to the surface), the mud (ppg), the friction factors cased and open, the block weight (klbf; blank: estimated from the record)"},
    'hydraulics': {'cols': ('well', 'string', 'hole', 'mw_ppg', 'fann', 'flow_gpm', 'nozzles_32', 'bit_in', 'surface_psi', 'surface_ref_gpm', 'tools_psi',
                            'trip_ft_min', 'pp_ppg', 'fg_ppg', 'source'), 'need': ('well', 'source'),
                   'about': "the hydraulics: the string from the bit up (what:length_ft:od_in:bore_in; ...), the hole from the surface down (what:bottom_md_ft:inside_diameter_in; ..., the open hole last with no bottom), the mud weight, the viscometer dial (600=60 300=38 200=30 100=21 6=7 3=6), the pump rate, the nozzles in 32nds (14 14 14 14 14), the bit (in), the surface lines' loss (psi) at a rate, the motor and MWD (psi), the trip speed, and a pore pressure and fracture gradient for the hole when the Pore pressure tab has none"},
    'casing_design': {'cols': ('well', 'what', 'od_in', 'weight_lbft', 'grade', 'top_md_ft', 'shoe_md_ft', 'hole_in', 'mw_ppg', 'wall_in', 'source'),
                      'need': ('well', 'what', 'od_in', 'weight_lbft', 'grade', 'shoe_md_ft', 'hole_in', 'mw_ppg', 'source'),
                      'about': "the casing program for casing design and kick tolerance: a row per string - its outside diameter (9.625 or 9-5/8), nominal weight (lb/ft), grade (H40 to Q125), top (0 to the surface) and shoe (ft MD), the hole it was run in (in), the mud it was run in (ppg), the wall (in) for a size not in the program's table; a well's rows together replace its program"},
    'casing_loads': {'cols': ('well', 'td_md_ft', 'td_hole_in', 'td_mw_ppg', 'pp_ppg', 'fg_ppg', 'kick_intensity_ppg', 'gas_gradient_psi_ft', 'gas_sg',
                              'surface_temp_f', 'geothermal_f_100ft', 'backup_gradient_psi_ft', 'evacuation', 'df_burst', 'df_collapse', 'df_tension', 'overpull_klbf', 'bha_od_in', 'bha_length_ft', 'dp_od_in', 'source'),
                     'need': ('well', 'source'),
                     'about': "the casing loads' terms: the hole below the deepest string (TD ft MD, hole in, mud ppg), a pore pressure and fracture gradient when the Pore pressure tab has none, the kick intensity (ppg), the gas (its gravity, the surface temperature in F and the geothermal gradient in F per 100 ft; or a fixed gradient in psi/ft in place of the real gas), the backup gradient (psi/ft), the evacuation (0 to 1), the design factors, the overpull (klbf), and the BHA and drill pipe for the kick volume; blank keeps the default"},
    'frac_stages': {'cols': ('well', 'file', 'depth_unit', 'utc_offset_h', 'source'), 'need': ('well', 'file', 'source'),
                    'about': "a treatment well's stage table (a path beside this sheet: CSV or Excel, one row per stage with its start and end of pumping), its depth unit (ft or m), the stage clock's hours from UTC, and the report it came from"},
    'frac_monitors': {'cols': ('well', 'channel'), 'need': ('well', 'channel'),
                      'about': "a monitored well's pressure channel for the frac-hit check, as the record names it (blank: the first pressure channel)"},
    'production': {'cols': ('well', 'file', 'source'), 'need': ('well', 'file', 'source'),
                   'about': "a well's monthly production (a path beside this sheet: CSV or Excel with the month, oil, gas, water and producing days) and where it came from"},
    'decline_terms': {'cols': ('well', 'terminal_decline', 'b_max', 'start', 'price_basis', 'oil_price', 'gas_price', 'nri', 'severance', 'opex_month',
                               'discount', 'gas_mmbtu_per_mcf', 'source'), 'need': ('well', 'source'),
                      'about': "the decline fit's and the value's terms: the terminal decline (0.08 = 8 % a year), the largest b, the month to fit from (YYYY-MM), the price basis (sec or flat) and flat prices, the net revenue interest, severance tax, operating cost a month (USD), the discount rate and the gas heat content"},
}
TABLES.update(ENG_TABLES)
ENG_ORDER = ('survey_program', 'plans', 'pore_pressure', 'pressures', 'torque_drag', 'hydraulics', 'casing_design', 'casing_loads', 'frac_stages', 'frac_monitors', 'production', 'decline_terms')
ORDER = ORDER[:-1] + ENG_ORDER + ('settings',)
APPLY_ORDER = APPLY_ORDER[:-1] + ENG_ORDER + ('settings',)


def _same_vals(cur: dict, want: dict) -> bool:
    for k, v in want.items():
        c = cur.get(k)
        if isinstance(v, float) or isinstance(c, float):
            try:
                if c is None or abs(float(c) - float(v)) > 1e-9:
                    return False
            except (TypeError, ValueError):
                return False
        elif c != v:
            return False
    return True


def _file_bytes(p: str, base_dir: str, what: str, where: str) -> bytes:
    path = p if os.path.isabs(p) else os.path.join(base_dir or '.', p)
    if not os.path.isfile(path):
        raise SheetError(f'no {what} file {p!r} beside the sheet' + ('' if base_dir else f' (a sheet sent through the browser carries no files: attach it on {where} or from the command line)'))
    with open(path, 'rb') as f:
        return f.read()


def _plan_engineering(ws, tables, wells, add, guard, base_dir: str) -> None:
    from . import decline as DCA
    from . import frac_hits as FH
    from . import hydraulics as HY
    from . import pore_pressure as PPF
    from . import torque_drag as TND
    from . import wellplan as WPL
    from . import xlsx_read as XR

    for rec in tables.get('survey_program', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            prog = WPL.parse_program(rec['program'])
            cur = WPL.well_settings(ws, wid)
            src = rec['source'].strip()
            if cur['program_declared'] and cur['program'] == prog and cur['source'] == src:
                add('survey_program', rec['_row'], wid, 'SAME', 'the survey program already declared')
                return
            txt = ', '.join((f"to {p['to_md_m']:g} m: " if p['to_md_m'] is not None else ('then ' if i else '')) + p['model'] for i, p in enumerate(prog))
            add('survey_program', rec['_row'], wid, 'CHANGE' if cur['program_declared'] else 'NEW', f'{txt} ({src})',
                lambda actor, wid=wid, prog=prog, src=src: WPL.set_well(ws, actor, wid, prog, source=src))
        guard('survey_program', rec, b)

    for rec in tables.get('plans', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            p = rec['file'].strip()
            content = _file_bytes(p, base_dir, 'plan', 'the Plan vs actual tab')
            ref = rec['azimuth_reference'].strip().lower()
            if ref not in ('grid', 'true', 'magnetic'):
                raise SheetError('the azimuth reference is grid, true or magnetic')
            dec = _f(rec.get('declination_deg'), 'the declination (degrees)', False)
            if ref == 'magnetic' and dec is None:
                raise SheetError('a magnetic azimuth reference needs the declination used')
            if ref == 'grid' and not rec.get('grid', '').strip():
                raise SheetError('a grid azimuth reference needs its grid named (e.g. TX-C or UTM14N)')
            unit = (rec.get('md_unit') or 'ft').strip().lower()
            if unit not in ('ft', 'm'):
                raise SheetError('the MD unit is ft or m')
            body = XR.to_csv(content) if XR.is_xlsx(content) else content.decode('utf-8', errors='replace')
            if '<trajectory' not in body[:4000]:
                from . import well3d as W3
                W3.read_survey_csv(body, unit)
            sha = hashlib.sha256(XR.to_csv(content).encode('utf-8') if XR.is_xlsx(content) else content).hexdigest()
            old = ws.well(wid).get('plan') or {}
            if old.get('sha256') == sha and old.get('azimuth_reference') == ref and old.get('source') == rec['source'].strip():
                add('plans', rec['_row'], wid, 'SAME', f'{os.path.basename(p)} already attached')
                return
            add('plans', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"plan {os.path.basename(p)}, azimuths {ref} ({rec['source'].strip()})",
                lambda actor, wid=wid, p=p, content=content, ref=ref, dec=dec, unit=unit, rec=rec:
                    WPL.attach(ws, wid, os.path.basename(p), content, actor, ref, unit, rec.get('grid', '').strip() or None, dec, rec['source'].strip()))
        guard('plans', rec, b)

    for rec in tables.get('pore_pressure', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            want = {}
            ind = (rec.get('indicator') or '').strip().lower()
            if ind:
                if ind not in ('auto', 'sonic', 'resistivity', 'dexponent'):
                    raise SheetError('the indicator is auto, sonic, resistivity or dexponent')
                want['indicator'] = ind
            for k in PPF.LIMITS:
                v = _f(rec.get(k), k, False)
                if v is not None:
                    lo, hi = PPF.LIMITS[k]
                    if not lo <= v <= hi:
                        raise SheetError(f'{k} must be between {lo:g} and {hi:g}')
                    want[k] = v
            if not want:
                raise SheetError('give at least one value (blank keeps the default)')
            cur = PPF.well_config(ws, wid)
            src = rec['source'].strip()
            if _same_vals(cur, want) and cur.get('source') == src:
                add('pore_pressure', rec['_row'], wid, 'SAME', 'already declared')
                return
            declared = bool(((PPF._load(ws).get('wells') or {}).get(wid) or {}).get('source'))
            add('pore_pressure', rec['_row'], wid, 'CHANGE' if declared else 'NEW', ', '.join(f'{k} {_g(v)}' for k, v in want.items()) + f' ({src})',
                lambda actor, wid=wid, want=want, src=src: PPF.set_well(ws, actor, wid, src, **want))
        guard('pore_pressure', rec, b)

    have_m = PPF._load(ws).get('measured') or {}
    for rec in tables.get('pressures', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            z, v = _f(rec['depth_m'], 'the depth (m)'), _f(rec['value'], 'the value')
            unit = (rec.get('unit') or 'psi').strip().lower()
            dk = (rec.get('depth_kind') or 'tvd').strip().lower()
            kind = rec['kind'].strip().lower()
            if kind not in ('pore', 'kick', 'lot', 'fit'):
                raise SheetError('the kind is pore, kick, lot or fit')
            if unit not in ('psi', 'ppg') or dk not in ('tvd', 'md'):
                raise SheetError('the unit is psi or ppg; the depth kind tvd or md')
            src = rec['source'].strip()
            if any(abs(m['depth_m'] - z) < 1e-6 and abs(m['value'] - v) < 1e-6 and (m['unit'], m['kind'], m['depth_kind'], m['source']) == (unit, kind, dk, src)
                   for m in have_m.get(wid, [])):
                add('pressures', rec['_row'], wid, 'SAME', f'{kind} {v:g} {unit} at {z:g} m {dk} already declared')
                return
            add('pressures', rec['_row'], wid, 'NEW', f'{kind} {v:g} {unit} at {z:g} m {dk} ({src})',
                lambda actor, wid=wid, z=z, v=v, unit=unit, kind=kind, dk=dk, src=src: PPF.add_measurement(ws, actor, wid, z, v, unit, kind, dk, src))
        guard('pressures', rec, b)

    for rec in tables.get('torque_drag', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            string = TND.parse_string(rec['string']) if (rec.get('string') or '').strip() else None
            vals = {k: _f(rec.get(k), k, k != 'mud_ppg') for k in ('mud_ppg', 'mu_cased', 'mu_open', 'block_klbf')}
            for k, (lo, hi) in (('mud_ppg', (6.0, 22.0)), ('mu_cased', (0.0, 0.8)), ('mu_open', (0.0, 0.8)), ('block_klbf', (0.0, 200.0))):
                if vals[k] is not None and not lo <= vals[k] <= hi:
                    raise SheetError(f'{k} must be between {lo:g} and {hi:g}')
            if string is None and all(v is None for v in vals.values()):
                raise SheetError('give the string or at least one value')
            cur = TND.well_config(ws, wid)
            src = rec['source'].strip()
            want = {k: v for k, v in vals.items() if v is not None}
            if (string is None or cur.get('string') == string) and _same_vals(cur, want) and cur.get('source') == src:
                add('torque_drag', rec['_row'], wid, 'SAME', 'already declared')
                return
            what = (f"{len(string)} section(s) from the bit up; " if string else '') + ', '.join(f'{k} {_g(v)}' for k, v in want.items())
            add('torque_drag', rec['_row'], wid, 'CHANGE' if cur.get('source') else 'NEW', f'{what} ({src})'.replace('; (', ' ('),
                lambda actor, wid=wid, string=string, vals=vals, src=src: TND.set_well(ws, actor, wid, src, string, vals['mud_ppg'], vals['mu_cased'],
                                                                                         vals['mu_open'], vals['block_klbf']))
        guard('torque_drag', rec, b)

    for rec in tables.get('hydraulics', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            string = HY.parse_sections(rec['string'], 'string') if (rec.get('string') or '').strip() else None
            hole = HY.parse_sections(rec['hole'], 'hole') if (rec.get('hole') or '').strip() else None
            fann = None
            if (rec.get('fann') or '').strip():
                fann = {}
                for part in rec['fann'].replace(',', ' ').split():
                    k, _, v = part.partition('=')
                    if not v:
                        raise SheetError(f'the viscometer is SPEED=DIAL pairs (600=60 300=38 ...), not {part!r}')
                    fann[str(int(float(k)))] = float(v)
                HY.rheology(fann)
            nz = None
            if (rec.get('nozzles_32') or '').strip():
                nz = [float(x) for x in rec['nozzles_32'].replace(',', ' ').split()]
                if any(not 6.0 <= x <= 32.0 for x in nz) or len(nz) > 12:
                    raise SheetError('nozzles are in 32nds of an inch, 6 to 32 each, at most 12')
            vals = {}
            for k in ('mw_ppg', 'flow_gpm', 'bit_in', 'surface_psi', 'surface_ref_gpm', 'tools_psi', 'trip_ft_min', 'pp_ppg', 'fg_ppg'):
                v = _f(rec.get(k), k, False)
                if v is not None:
                    lo, hi = HY.LIMITS[k]
                    if not lo <= v <= hi:
                        raise SheetError(f'{k} must be between {lo:g} and {hi:g}')
                    vals[k] = v
            if string is None and hole is None and fann is None and nz is None and not vals:
                raise SheetError('give at least one of the string, the hole, the mud or a value')
            cur = HY.well_config(ws, wid)
            src = rec['source'].strip()
            same = ((string is None or cur.get('string') == string) and (hole is None or cur.get('hole') == hole)
                    and (fann is None or {k: float(v) for k, v in (cur.get('fann') or {}).items()} == fann)
                    and (nz is None or cur.get('nozzles_32') == nz) and _same_vals(cur, vals) and cur.get('source') == src)
            if same:
                add('hydraulics', rec['_row'], wid, 'SAME', 'already declared')
                return
            parts = ([f'string {len(string)} section(s)'] if string else []) + ([f'hole {len(hole)} section(s)'] if hole else []) \
                + (['viscometer ' + ' '.join(f'{k}={_g(v)}' for k, v in fann.items())] if fann else []) + ([f"nozzles {' '.join(_g(x) for x in nz)}"] if nz else []) \
                + [f'{k} {_g(v)}' for k, v in vals.items()]
            add('hydraulics', rec['_row'], wid, 'CHANGE' if cur.get('source') else 'NEW', '; '.join(parts) + f' ({src})',
                lambda actor, wid=wid, string=string, hole=hole, fann=fann, nz=nz, vals=vals, src=src: HY.set_well(ws, actor, wid, src, string, hole, fann, nz, **vals))
        guard('hydraulics', rec, b)

    from . import casing_design as CSG
    groups: Dict[str, list] = {}
    for rec in tables.get('casing_design', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            row = {k: rec.get(k) for k in ('what', 'od_in', 'weight_lbft', 'grade', 'top_md_ft', 'shoe_md_ft', 'hole_in', 'mw_ppg', 'wall_in')}
            row['wall_in'] = row['wall_in'] if (row.get('wall_in') or '').strip() else None
            row['top_md_ft'] = row['top_md_ft'] if (row.get('top_md_ft') or '').strip() else 0
            CSG.check_strings([row])
            groups.setdefault(wid, []).append((rec, row))
        guard('casing_design', rec, b)
    for wid, items in groups.items():
        first = items[0][0]

        def b(wid=wid, items=items, first=first):
            strings = CSG.check_strings([r for _, r in items])
            srcs = {rec['source'].strip() for rec, _ in items}
            src = '; '.join(sorted(srcs))
            cur = CSG.well_config(ws, wid)
            if cur.get('strings') == strings and (cur.get('strings_source') or cur.get('source')) == src:
                add('casing_design', first['_row'], wid, 'SAME', f'{len(strings)} string(s) already declared')
                return
            add('casing_design', first['_row'], wid, 'CHANGE' if cur.get('strings') else 'NEW',
                f"{len(strings)} string(s): " + ', '.join(f"{x['what']} {x['od_in']:g} in {x['weight_lbft']:g} lb/ft {x['grade']} to {x['shoe_md_ft']:,.0f} ft" for x in strings) + f' ({src})',
                lambda actor, wid=wid, strings=strings, src=src: CSG.set_well(ws, actor, wid, src, strings))
        guard('casing_design', first, b)

    for rec in tables.get('casing_loads', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            want = {}
            for k in CSG.LIMITS:
                if k == 'step_ft' or not (rec.get(k) or '').strip():
                    continue
                if rec[k].strip().lower() == 'none' and CSG.DEFAULTS.get(k) is None:
                    want[k] = None
                    continue
                v = CSG.size_in(rec[k]) if k in ('td_hole_in', 'bha_od_in', 'dp_od_in') else _f(rec.get(k), k, False)
                lo, hi = CSG.LIMITS[k]
                if not lo <= v <= hi:
                    raise SheetError(f'{k} must be between {lo:g} and {hi:g}')
                want[k] = v
            if not want:
                raise SheetError('give at least one value (blank keeps the default)')
            cur = CSG.well_config(ws, wid)
            src = rec['source'].strip()
            if _same_vals(cur, want) and cur.get('source') == src:
                add('casing_loads', rec['_row'], wid, 'SAME', 'already declared')
                return
            declared = bool(((CSG._load(ws).get('wells') or {}).get(wid) or {}).get('source'))
            add('casing_loads', rec['_row'], wid, 'CHANGE' if declared else 'NEW', ', '.join(f'{k} {_g(v)}' for k, v in want.items()) + f' ({src})',
                lambda actor, wid=wid, want=want, src=src: CSG.set_well(ws, actor, wid, src, None, **want))
        guard('casing_loads', rec, b)

    st_have = FH.stages(ws)
    for rec in tables.get('frac_stages', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            p = rec['file'].strip()
            content = _file_bytes(p, base_dir, 'stage table', 'the Frac hits tab')
            unit = (rec.get('depth_unit') or 'ft').strip().lower()
            if unit not in ('ft', 'm'):
                raise SheetError('the depth unit is ft or m')
            off = _f(rec.get('utc_offset_h'), "the stage clock's hours from UTC", False)
            if off is not None and not -14 <= off <= 14:
                raise SheetError("the stage clock's offset from UTC is in hours, between -14 and 14")
            st = FH.read_stages(content, unit)
            src = rec['source'].strip()
            old = st_have.get(wid) or {}
            if old.get('sha256') == hashlib.sha256(content).hexdigest() and old.get('source') == src and old.get('depth_unit') == unit and old.get('utc_offset_h') == off:
                add('frac_stages', rec['_row'], wid, 'SAME', f'{os.path.basename(p)} already attached')
                return
            add('frac_stages', rec['_row'], wid, 'CHANGE' if old else 'NEW', f"{os.path.basename(p)}: {len(st)} stage(s) ({src})",
                lambda actor, wid=wid, p=p, content=content, src=src, unit=unit, off=off: FH.attach_stages(ws, wid, os.path.basename(p), content, actor, src, unit, off))
        guard('frac_stages', rec, b)

    mon_have = FH.settings(ws).get('monitors') or {}
    for rec in tables.get('frac_monitors', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            ch = rec['channel'].strip()
            if (mon_have.get(wid) or {}).get('channel') == ch:
                add('frac_monitors', rec['_row'], wid, 'SAME', f'monitored on {ch} already')
                return
            add('frac_monitors', rec['_row'], wid, 'CHANGE' if wid in mon_have else 'NEW', f'monitored on {ch}',
                lambda actor, wid=wid, ch=ch: FH.set_settings(ws, actor, '', (wid, ch)))
        guard('frac_monitors', rec, b)

    prod_have = DCA._load(ws).get('wells') or {}
    for rec in tables.get('production', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            p = rec['file'].strip()
            content = _file_bytes(p, base_dir, 'production', 'the Decline curves tab')
            prod = DCA.read_production(content)
            src = rec['source'].strip()
            old = prod_have.get(wid) or {}
            if old.get('sha256') == hashlib.sha256(content).hexdigest() and old.get('source') == src:
                add('production', rec['_row'], wid, 'SAME', f'{os.path.basename(p)} already attached')
                return
            add('production', rec['_row'], wid, 'CHANGE' if old.get('file') else 'NEW', f"{os.path.basename(p)}: {len(prod)} month(s), {prod[0]['month']} to {prod[-1]['month']} ({src})",
                lambda actor, wid=wid, p=p, content=content, src=src: DCA.attach(ws, wid, os.path.basename(p), content, actor, src))
        guard('production', rec, b)

    for rec in tables.get('decline_terms', []):
        def b(rec=rec):
            wid = _well_id(ws, rec['well'], wells)
            want = {}
            for k in DCA.LIMITS:
                v = _f(rec.get(k), k, False)
                if v is not None:
                    lo, hi = DCA.LIMITS[k]
                    if not lo <= v <= hi:
                        raise SheetError(f'{k} must be between {lo:g} and {hi:g}')
                    want[k] = v
            pb = (rec.get('price_basis') or '').strip().lower()
            if pb:
                if pb not in ('sec', 'flat'):
                    raise SheetError('the price basis is sec or flat')
                want['price_basis'] = pb
            if (rec.get('start') or '').strip():
                if not re.fullmatch(r'\d{4}-\d{2}', rec['start'].strip()[:7]):
                    raise SheetError('the month to fit from is YYYY-MM')
                want['start'] = rec['start'].strip()[:7]
            if not want:
                raise SheetError('give at least one term')
            cur = DCA.well_config(ws, wid)
            src = rec['source'].strip()
            if _same_vals(cur, want) and cur.get('source') == src:
                add('decline_terms', rec['_row'], wid, 'SAME', 'already declared')
                return
            add('decline_terms', rec['_row'], wid, 'CHANGE' if ((prod_have.get(wid) or {}).get('config')) else 'NEW',
                ', '.join(f'{k} {_g(v)}' for k, v in want.items()) + f' ({src})',
                lambda actor, wid=wid, want=want, src=src: DCA.set_config(ws, actor, wid, src, **want))
        guard('decline_terms', rec, b)


def _template_engineering(ws, w, head) -> None:
    from . import decline as DCA
    from . import frac_hits as FH
    from . import hydraulics as HY
    from . import pore_pressure as PPF
    from . import torque_drag as TND
    from . import wellplan as WPL
    wells = ws.wells()
    head('survey_program')
    for x in wells:
        s = WPL.well_settings(ws, x['id'])
        if s['program_declared']:
            w.writerow(['', x['id'], ', '.join((f"{a:g}-{p['to_md_m']:g}:" if p['to_md_m'] is not None else (f'{a:g}-:' if i else '')) + p['model']
                                               for i, (p, a) in enumerate(zip(s['program'], [0.0] + [q['to_md_m'] or 0.0 for q in s['program']]))), s['source']])
    head('plans')
    w.writerow(['# a plan already attached is kept beside its well; list it here only to replace it'])
    head('pore_pressure')
    for wid, c in ((PPF._load(ws).get('wells') or {})).items():
        if c.get('source'):
            w.writerow(['', wid] + [_g(c.get(k)) if c.get(k) is not None else '' for k in TABLES['pore_pressure']['cols'][1:-1]] + [c['source']])
    head('pressures')
    for wid, lst in (PPF._load(ws).get('measured') or {}).items():
        for m in lst:
            w.writerow(['', wid, _g(m['depth_m']), m['depth_kind'], _g(m['value']), m['unit'], m['kind'], m['source']])
    head('torque_drag')
    for wid, c in ((TND._load(ws).get('wells') or {})).items():
        if c.get('source'):
            st = '; '.join(f"{x['what']}:{_g(x['length_ft']) if x['length_ft'] is not None else ''}:{_g(x['od_in'])}:{_g(x['weight_lb_ft'])}:{_g(x['contact_od_in'])}"
                           for x in (c.get('string') or []))
            w.writerow(['', wid, st, _g(c.get('mud_ppg')), _g(c.get('mu_cased')), _g(c.get('mu_open')), _g(c.get('block_klbf')), c['source']])
    head('hydraulics')
    for wid, c in ((HY._load(ws).get('wells') or {})).items():
        if c.get('source'):
            st = '; '.join(f"{x['what']}:{_g(x['length_ft']) if x['length_ft'] is not None else ''}:{_g(x['od_in'])}:{_g(x['id_in'])}" for x in (c.get('string') or []))
            ho = '; '.join(f"{x['what']}:{_g(x['bottom_md_ft']) if x['bottom_md_ft'] is not None else ''}:{_g(x['id_in'])}" for x in (c.get('hole') or []))
            w.writerow(['', wid, st, ho, _g(c.get('mw_ppg')), ' '.join(f'{k}={_g(float(v))}' for k, v in (c.get('fann') or {}).items()), _g(c.get('flow_gpm')),
                        ' '.join(_g(float(x)) for x in (c.get('nozzles_32') or [])), _g(c.get('bit_in')), _g(c.get('surface_psi')), _g(c.get('surface_ref_gpm')),
                        _g(c.get('tools_psi')), _g(c.get('trip_ft_min')), _g(c.get('pp_ppg')), _g(c.get('fg_ppg')), c['source']])
    from . import casing_design as CSG
    head('casing_design')
    for wid, c in ((CSG._load(ws).get('wells') or {})).items():
        for x in (c.get('strings') or []):
            w.writerow(['', wid, x['what'], _g(x['od_in']), _g(x['weight_lbft']), x['grade'], _g(x['top_md_ft']), _g(x['shoe_md_ft']), _g(x['hole_in']),
                        _g(x['mw_ppg']), _g(x['wall_in']) if x.get('wall_from') == 'declared' else '', c.get('strings_source') or c.get('source') or ''])
    head('casing_loads')
    for wid, c in ((CSG._load(ws).get('wells') or {})).items():
        if c.get('source') and any(k in c for k in CSG.LIMITS):
            w.writerow(['', wid] + [_g(c.get(k)) if c.get(k) is not None else '' for k in TABLES['casing_loads']['cols'][1:-1]] + [c['source']])
    head('frac_stages')
    for wid, s in FH.stages(ws).items():
        w.writerow([f"# {wid}: {s['file']} is attached ({s['stages']} stages); list it here only to replace it"])
    head('frac_monitors')
    for wid, m in (FH.settings(ws).get('monitors') or {}).items():
        w.writerow(['', wid, m.get('channel', '')])
    head('production')
    for wid, p in (DCA._load(ws).get('wells') or {}).items():
        if p.get('file'):
            w.writerow([f"# {wid}: {p['file']} is attached ({p['months']} months); list it here only to replace it"])
    head('decline_terms')
    for wid, p in (DCA._load(ws).get('wells') or {}).items():
        c = p.get('config') or {}
        if c.get('source'):
            w.writerow(['', wid] + [_g(c.get(k)) if c.get(k) is not None else '' for k in TABLES['decline_terms']['cols'][1:-1]] + [c['source']])


def template_xlsx(ws, path: str) -> None:
    """The same sheet as an Excel workbook (one sheet; the import reads its first sheet)."""
    from . import xlsx_read as XR
    rows_ = list(csv.reader(io.StringIO(template(ws))))
    XR.write_simple(path, [[c for c in r] for r in rows_])
