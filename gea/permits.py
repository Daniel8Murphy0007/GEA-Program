# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""permits - a drilling-permit export (a regulator's query result, an operator's schedule) to the rigs CSV the leg judges against.

The detectability test, the array test and the track verdict all take ground truth as a rigs CSV:
`source_id, lat, lon, start_utc, end_utc[, kind, note]`. Nobody writes that by hand for a hundred
permits. This module reads a permit export - the Texas RRC drilling-permit query's CSV, or any
table with a well identifier, a surface position and a date - finds the columns by name (a
mapping file overrides the guesses), and writes the rigs CSV beside an import note that says
exactly which columns were used, how many rows went in, how many were dropped and why, and what
was assumed. The usual assumption: a permit says when drilling may start, rarely when it stopped,
so the working window ends `default_days` after it starts, and every such row's note says so.

Nothing here is a measurement, and nothing here knows a permit's datum: positions are copied as
the export gives them, and the note says that too.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

# the fields the rigs CSV needs, and the column names a permit export is likely to call them
DEFAULT_CANDIDATES: Dict[str, List[str]] = {
    'id': ['api', 'api no', 'api no.', 'api number', 'api_no', 'apino', 'permit', 'permit no', 'permit no.', 'permit number', 'permit_no', 'drilling permit', 'well id', 'uwi', 'id', 'source_id'],
    'name': ['lease name', 'lease', 'well name', 'well', 'well no', 'well no.', 'well number', 'name'],
    'lat': ['surface latitude', 'surface lat', 'shl latitude', 'shl lat', 'sh_lat', 'latitude', 'lat', 'lat_dd', 'y'],
    'lon': ['surface longitude', 'surface long', 'surface lon', 'shl longitude', 'shl long', 'sh_lon', 'longitude', 'long', 'lon', 'lon_dd', 'x'],
    'start': ['spud date', 'spud', 'spud_date', 'approved date', 'approval date', 'permit date', 'permit approved', 'issued', 'issue date', 'date approved', 'start', 'start_utc', 'start date'],
    'end': ['rig release', 'rig release date', 'completion date', 'completed', 'td date', 'end', 'end_utc', 'end date', 'plugged date', 'release date'],
    'kind': ['well type', 'wellbore profile', 'profile', 'type', 'kind', 'purpose'],
    'operator': ['operator', 'operator name', 'company', 'operator_name'],
    'county': ['county', 'county name'],
}
REQUIRED = ('lat', 'lon', 'start')

DATE_FORMATS = ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y', '%m/%d/%y',
                '%d-%b-%Y', '%d %b %Y', '%b %d, %Y', '%Y%m%d', '%m-%d-%Y')


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(s).strip().lower()).strip()


def detect_columns(header: Sequence[str], mapping: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    """Which column holds each field. A mapping {field: column name} wins; otherwise the first candidate that
    matches a header exactly (normalised), then the first header that contains a candidate."""
    norm = {_norm(h): h for h in header}
    out: Dict[str, Optional[str]] = {}
    for field, cands in DEFAULT_CANDIDATES.items():
        if mapping and mapping.get(field):
            col = mapping[field]
            if col not in header:
                raise ValueError(f"mapping names column '{col}' for {field}, which is not in the file: {list(header)}")
            out[field] = col
            continue
        found = None
        for c in cands:
            if _norm(c) in norm:
                found = norm[_norm(c)]
                break
        if found is None:
            for c in cands:
                for nh, h in norm.items():
                    if len(_norm(c)) >= 4 and _norm(c) in nh and h not in out.values():
                        found = h
                        break
                if found:
                    break
        out[field] = found
    # a second start column (an approval date beside a spud date): used for a row whose start column is empty
    if out.get('start'):
        for c in DEFAULT_CANDIDATES['start']:
            h = norm.get(_norm(c))
            if h and h != out['start'] and h not in out.values():
                out['start_fallback'] = h
                break
    return out


def parse_date(s: str) -> Optional[datetime]:
    s = str(s or '').strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _float(s) -> Optional[float]:
    try:
        v = float(str(s).strip().replace(',', ''))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def read_table(path: str) -> Tuple[List[str], List[dict]]:
    """A CSV or TSV (the delimiter is sniffed) with a header row."""
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        rd = csv.DictReader(f, dialect=dialect)
        rows = [r for r in rd]
        header = list(rd.fieldnames or [])
    return header, rows


def import_permits(path: str, out_csv: str, mapping: Optional[Dict[str, str]] = None, default_days: float = 30.0,
                   within: Optional[Tuple[float, float, float]] = None, kind: str = 'rig') -> dict:
    """The export at `path` to the rigs CSV at `out_csv`, with `out_csv + '.import.json'` beside it. Rows without a
    position or a start date are dropped and counted; a missing end date is assumed start + default_days and the row's
    note says so; `within` = (lat, lon, km) keeps only rigs inside that radius."""
    from .seismic_detect import haversine_km
    header, rows = read_table(path)
    if not header:
        raise ValueError('the file has no header row')
    cols = detect_columns(header, mapping)
    missing = [f for f in REQUIRED if not cols.get(f)]
    if missing:
        raise ValueError(f"no column found for {', '.join(missing)}; the file's columns are {header}. Give a mapping file: {{\"lat\": \"<column>\", ...}}")
    out_rows = []
    dropped = {'no_position': 0, 'no_start_date': 0, 'outside_radius': 0, 'duplicate_id': 0, 'end_before_start': 0}
    assumed_end = 0
    fell_back = 0
    seen = set()
    for i, r in enumerate(rows):
        lat, lon = _float(r.get(cols['lat'])), _float(r.get(cols['lon']))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            dropped['no_position'] += 1
            continue
        start = parse_date(r.get(cols['start']))
        note_bits = []
        if start is None and cols.get('start_fallback'):
            start = parse_date(r.get(cols['start_fallback']))
            if start is not None:
                note_bits.append(f"start from {cols['start_fallback']} (no {cols['start']})")
        if start is None:
            dropped['no_start_date'] += 1
            continue
        end = parse_date(r.get(cols['end'])) if cols.get('end') else None
        assumed = False
        if end is None:
            end = start + timedelta(days=default_days)
            assumed = True
            note_bits.append(f'end assumed start + {default_days:g} d')
        if end < start:
            dropped['end_before_start'] += 1
            continue
        if within:
            if haversine_km(lat, lon, within[0], within[1]) > within[2]:
                dropped['outside_radius'] += 1
                continue
        sid = str(r.get(cols['id']) or '').strip() if cols.get('id') else ''
        name = str(r.get(cols['name']) or '').strip() if cols.get('name') else ''
        if not sid:
            sid = name or f'row{i + 1}'
        sid = re.sub(r'[^A-Za-z0-9._-]+', '-', sid).strip('-') or f'row{i + 1}'
        if sid in seen:
            dropped['duplicate_id'] += 1
            continue
        seen.add(sid)
        assumed_end += int(assumed)
        fell_back += int(any(b.startswith('start from') for b in note_bits))
        op = str(r.get(cols['operator']) or '').strip() if cols.get('operator') else ''
        county = str(r.get(cols['county']) or '').strip() if cols.get('county') else ''
        k = (str(r.get(cols['kind']) or '').strip() if cols.get('kind') else '') or kind
        note = '; '.join([b for b in [f'permit export {os.path.basename(path)}', name and f'name {name}', op and f'operator {op}', county and f'county {county}'] + note_bits if b])
        out_rows.append({'source_id': sid, 'lat': round(lat, 6), 'lon': round(lon, 6), 'start_utc': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                         'end_utc': end.strftime('%Y-%m-%dT%H:%M:%SZ'), 'kind': k, 'note': note})
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['source_id', 'lat', 'lon', 'start_utc', 'end_utc', 'kind', 'note'])
        w.writeheader()
        for row in out_rows:
            w.writerow(row)
    note = {'protocol': 'permits.import_permits/1', 'source': os.path.abspath(path), 'rows_in': len(rows), 'rows_out': len(out_rows), 'dropped': dropped,
            'columns_used': {k: v for k, v in cols.items() if v}, 'columns_not_found': [k for k, v in cols.items() if not v],
            'end_assumed_rows': assumed_end, 'start_fallback_rows': fell_back, 'default_days': default_days, 'within': ({'lat': within[0], 'lon': within[1], 'km': within[2]} if within else None),
            'out': os.path.abspath(out_csv), 'imported_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'assumptions': ['positions are copied as the export gives them; the datum is the export\'s and is not converted',
                            f'a row without an end date works for {default_days:g} days from its start date (counted above)',
                            'the start date is whatever the chosen column holds (a spud date when there is one, else an approval date - which is not a spud date)'],
            'not_a_measurement': ['a rig\'s working window: a permit date is a permission, not a drilling log']}
    with open(out_csv + '.import.json', 'w', encoding='utf-8') as f:
        json.dump(note, f, indent=1)
    return note


def report_text(note: dict) -> str:
    lines = [f"permits: {note['rows_out']} of {note['rows_in']} rows -> {note['out']}",
             '  columns: ' + ', '.join(f'{k}={v}' for k, v in note['columns_used'].items())]
    if note['columns_not_found']:
        lines.append('  not found: ' + ', '.join(note['columns_not_found']))
    d = {k: v for k, v in note['dropped'].items() if v}
    if d:
        lines.append('  dropped: ' + ', '.join(f'{k} {v}' for k, v in d.items()))
    if note['end_assumed_rows']:
        lines.append(f"  end assumed (start + {note['default_days']:g} d) for {note['end_assumed_rows']} row(s)")
    if note.get('start_fallback_rows'):
        lines.append(f"  start taken from {note['columns_used'].get('start_fallback')} for {note['start_fallback_rows']} row(s) whose {note['columns_used'].get('start')} was empty")
    if note['within']:
        lines.append(f"  kept within {note['within']['km']:g} km of {note['within']['lat']}, {note['within']['lon']}")
    lines.append('  ' + '; '.join(note['assumptions']))
    return '\n'.join(lines)
