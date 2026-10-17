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
    'penetrators': {'cols': ('well', 'penetrator', 'installed', 'installer', 'certified', 'certificate', 'motor', 'gauge_distance_in', 'ir_a_megohm',
                             'ir_b_megohm', 'ir_c_megohm', 'ir_test_volts', 'ir_min_megohm', 'ir_min_source', 'void_test_psi', 'void_test_minutes',
                             'void_held', 'note'),
                    'need': ('well', 'penetrator'),
                    'about': "the ESP wellhead penetrator installed: its catalogue or fleet id (`gea penetrator`), the date, the installer and whether the maker certified them, the motor (PMM or induction), the gauge distance, the insulation readings per conductor and the test voltage, the void test"},
    'esp_drives': {'cols': ('well', 'maker', 'model', 'max_volts', 'max_amps', 'freq_min_hz', 'freq_max_hz', 'phases', 'motor_volts', 'motor_amps', 'area',
                            'gas_group', 'temp_class', 'ambient_min_f', 'ambient_max_f', 'source'),
                   'need': ('well', 'source'),
                   'about': "the ESP drive's output limits and the motor nameplate, the wellhead's hazardous-area classification (e.g. Class I, Division 1), gas group, temperature class and the site's ambient range"},
}
ORDER = ('wells', 'fleet', 'rates', 'items', 'afe', 'surveys', 'runs', 'components', 'penetrators', 'esp_drives')
APPLY_ORDER = ('wells', 'fleet', 'rates', 'items', 'afe', 'surveys', 'runs', 'penetrators', 'esp_drives')


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
    return buf.getvalue()
