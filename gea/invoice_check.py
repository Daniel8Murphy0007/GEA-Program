"""The invoice cross-check: a drilling contractor's daywork invoice set, day by day, against the daily drilling report
and the drilling record.

Three accounts of the same days, from three hands:

- the invoice - what the contractor bills: each line a day (or a span of days) at a rate (operating, standby,
  repair, moving, force majeure), a quantity, a unit price and an amount;
- the daily drilling report (the IADC-API Daily Drilling Report, or a form the operator accepts) - what the rig
  says it did: each line a time span with its IADC operation code (2 drilling, 5 circulate and condition, 6 trips,
  8 repair rig, ...) and the depths;
- the drilling record - what the recorder measured, second by second: the pumps, the rotary, the bit moving, the
  bit on bottom making hole, and the depth.

And the contract's terms, declared by the operator with their source: the report day's start hour on the rig's
clock, the day rates by kind, and the repair allowance (the hours of rig repair per job and per calendar month
paid at the operating rate; past it, the repair rate - IADC daywork contract, paragraph 4.4).

The checks, per report day:

- the report's own day: its lines add up to 24 hours, and none overlaps another;
- the report against the record: hours reported drilling (code 2) against the record's hours drilling; the depth
  at the end of the day and the footage against the record's; a line coded for work that moves pipe or fluid
  (drilling, reaming, circulating, trips) over a span when the record shows the pumps off, the rotary still and
  the bit still; a repair line over a span when the record shows the rig working;
- the invoice against the report and the contract: hours billed in a day (no more than 24), billed twice, billed
  with no report; the operating rate billed for hours the report puts on standby (waiting on the operator); rig
  repair past the allowance billed at the operating rate; a unit price that is not the contract's rate for its
  kind; quantity times price that is not the amount;
- the invoice against the record: a day billed for which the record holds nothing, and a day billed at the
  operating rate on which the record shows the rig quiet six hours or more beyond the hours billed at standby,
  repair or force majeure (a QUESTION, not an error: waiting on cement is operating time and quiet on the recorder).

Each day is AGREES, QUESTION or DIFFERS, and each finding carries the dollars it puts in question at the
contract's rates. Nothing here is a ruling on what is owed: the contract and the parties decide; this lays the
three accounts side by side, with every number traceable to its line.

The files are read as an Excel workbook (its first sheet) or CSV, by their column headings: `gea invoice-check --template
ddr` and `--template invoice` print the headings it reads. The times are the rig's clock - the report's and the
recorder's are taken to be the same clock, as both are kept on the rig.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from .workspace import WorkspaceError as _WsErr

KINDS = ('operating', 'standby', 'repair', 'moving', 'force_majeure')
ACTIVE_CODES = {2: 'drilling', 3: 'reaming', 5: 'circulate and condition', 6: 'trips'}
IADC = {1: 'rig up / tear down / rig move', 2: 'drilling', 3: 'reaming', 4: 'coring', 5: 'circulate and condition mud', 6: 'trips',
        7: 'service / maintain rig', 8: 'repair rig', 9: 'replace drill line', 10: 'deviation survey', 11: 'wireline logs',
        12: 'run casing and cement', 13: 'wait on cement', 14: 'rig up / down BOP', 15: 'test BOP', 16: 'drill stem test', 17: 'plug back',
        18: 'squeeze cement', 19: 'fishing', 20: 'directional work', 21: 'other', 22: 'other', 23: 'other', 31: 'run / retrieve riser',
        32: 'surface testing', 33: 'operating status', 34: 'safety', 35: 'well control', 36: 'coiled tubing', 37: 'completion', 38: 'subsea'}
IADC_SOURCE = 'IADC DDR operation code definitions (iadc.org, Definitions-DDR-Codes, 2019-10-07)'
CONTRACT_SOURCE = 'IADC U.S. Daywork Drilling Contract (2013 sample): 4.3 operating rate, 4.4 repair time, 4.5 standby, 4.7 force majeure, 8.4 daily report'
STANDBY_WORDS = ('standby', 'stand-by', 'stand by', 'waiting on orders', 'wait on orders', 'waiting on operator', 'wait on operator', 'awaiting orders',
                 "waiting on operator's", 'waiting on company', 'wait on company')
FLOW_FLOOR_GPM = 50.0
RPM_FLOOR = 5.0
BIT_MOVE_FT_PER_MIN = 5.0
TOL = {'drilling_h': 1.0, 'drilling_frac': 0.2, 'footage_ft': 30.0, 'footage_frac': 0.10, 'depth_ft': 30.0, 'day_h': 0.25,
       'quiet_question_h': 6.0, 'line_active_frac': 0.5, 'money': 0.51}
_LOCK = threading.Lock()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'economics', 'invoices', *p)


def _well(ws, well_id: str) -> dict:
    if not well_id or not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    return ws.well(well_id)


def _load(p: str, default):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _store(p: str, obj) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(p + '.tmp', p)


# -- the contract's terms ------------------------------------------------------------------------------------------------
def terms(ws) -> dict:
    return _load(_dir(ws, 'terms.json'), {})


def _num(v, what: str, lo: float = 0.0, allow_none: bool = True) -> Optional[float]:
    if v in (None, ''):
        if allow_none:
            return None
        raise ValueError(f'{what} is needed')
    try:
        x = float(str(v).replace(',', '').replace('$', '').strip())
    except ValueError:
        raise ValueError(f'{what} must be a number: {v!r}')
    if not math.isfinite(x) or x < lo:
        raise ValueError(f'{what} must be at least {lo:g}')
    return x


def terms_record(source: str, day_start: str = '06:00', report_date: str = 'start', rates: Optional[dict] = None,
                 repair_hours_per_job=None, repair_hours_per_month=None) -> dict:
    """The contract's terms checked, not written. `rates`: {kind: USD per day} for the kinds in KINDS (operating needed)."""
    if not (source or '').strip():
        raise ValueError('the terms need where they came from (e.g. "daywork contract with the drilling contractor, 2026-03-01, Exhibit A")')
    m = re.match(r'^(\d{1,2}):(\d{2})$', (day_start or '').strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError('the report day starts at HH:MM on the rig\'s clock, e.g. 06:00')
    if report_date not in ('start', 'end'):
        raise ValueError("the report's date is the day it starts ('start') or the day it ends ('end')")
    r = {}
    for k, v in (rates or {}).items():
        if k not in KINDS:
            raise ValueError(f'a rate is one of {", ".join(KINDS)}: {k!r}')
        x = _num(v, f'the {k} rate')
        if x is not None:
            r[k] = round(x, 2)
    if 'operating' not in r:
        raise ValueError('the terms need the operating day rate')
    t = {'source': source.strip(), 'day_start': f'{int(m.group(1)):02d}:{m.group(2)}', 'report_date': report_date, 'rates_usd_per_day': r,
         'repair_hours_per_job': _num(repair_hours_per_job, 'the repair hours per job'),
         'repair_hours_per_month': _num(repair_hours_per_month, 'the repair hours per month')}
    return t


def set_terms(ws, actor: str, source: str, day_start: str = '06:00', report_date: str = 'start', rates: Optional[dict] = None,
              repair_hours_per_job=None, repair_hours_per_month=None) -> dict:
    """Declare the contract's terms. `rates`: {kind: USD per day} for the kinds in KINDS (operating needed)."""
    t = {**terms_record(source, day_start, report_date, rates, repair_hours_per_job, repair_hours_per_month), 'by': actor, 'utc': _utc()}
    _store(_dir(ws, 'terms.json'), t)
    ws.audit(actor, 'invoice.terms', {k: t[k] for k in ('source', 'day_start', 'report_date', 'rates_usd_per_day', 'repair_hours_per_job', 'repair_hours_per_month')})
    return t


# -- reading the files ---------------------------------------------------------------------------------------------------
def _norm(h: str) -> str:
    h = re.sub(r'\(.*?\)|\[.*?\]', ' ', str(h or '').lower())
    return re.sub(r'[^a-z0-9#]+', ' ', h).strip()


def _find(heads: List[str], names: Tuple[str, ...], used: set) -> Optional[int]:
    nh = [_norm(h) for h in heads]
    for n in names:                                   # an exact heading first, then one that starts with it
        for i, h in enumerate(nh):
            if i not in used and h == n:
                return i
    for n in names:
        for i, h in enumerate(nh):
            if i not in used and (h.startswith(n + ' ') or h.endswith(' ' + n)):
                return i
    return None


def _rows(text: str) -> Tuple[List[str], List[List[str]]]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith('#')]
    if not lines:
        raise ValueError('the file is empty')
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    rd = list(csv.reader(io.StringIO('\n'.join(lines)), dialect))
    return [h.strip() for h in rd[0]], [[c.strip() for c in r] for r in rd[1:] if any(c.strip() for c in r)]


def _date(v: str) -> Optional[date]:
    v = (v or '').strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%m/%d/%y', '%d-%b-%Y', '%d-%b-%y', '%b %d, %Y', '%Y%m%d'):
        try:
            return datetime.strptime(v[:len(datetime(2026, 12, 31).strftime(fmt)) + 2].strip() if fmt != '%b %d, %Y' else v, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v[:10]).date()
    except ValueError:
        return None


def _hm(v: str) -> Optional[float]:
    """A time of day as hours: 06:00, 0600, 6:30, 24:00."""
    v = (v or '').strip().replace('.', ':')
    m = re.match(r'^(\d{1,2}):?(\d{2})(?::\d{2})?$', v)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59 or (h == 24 and mi):
        return None
    return h + mi / 60.0


DDR_COLS = {'date': ('date', 'report date', 'day', 'rpt date'), 'from': ('from', 'start', 'time from', 'start time', 'from time'),
            'to': ('to', 'end', 'time to', 'end time', 'to time'), 'hours': ('hours', 'hrs', 'duration', 'hr'),
            'code': ('code', 'iadc code', 'op code', 'iadc', 'operation code', 'activity code'),
            'activity': ('activity', 'description', 'operation', 'details', 'remarks', 'summary', 'operations'),
            'depth_from': ('depth from', 'start depth', 'md from', 'from depth', 'depth start'),
            'depth_to': ('depth to', 'end depth', 'md to', 'to depth', 'depth end', 'depth')}
INV_COLS = {'date': ('date', 'service date', 'day', 'work date'), 'from': ('from', 'period from', 'start date', 'date from'),
            'to': ('to', 'period to', 'end date', 'date to'), 'kind': ('rate type', 'kind', 'charge type', 'item', 'description', 'rate', 'charge'),
            'quantity': ('quantity', 'qty', 'days', 'hours'), 'unit': ('unit', 'uom', 'units'),
            'price': ('unit price', 'price', 'unit rate', 'day rate', 'rate usd', 'rate'), 'amount': ('amount', 'total', 'extended', 'line total', 'ext amount')}


def template(kind: str) -> str:
    if kind == 'ddr':
        return ('# a daily drilling report, one row per line of its time breakdown; the date is the report\'s date\n'
                'Date,From,To,Hours,Code,Activity,Depth From (ft),Depth To (ft)\n'
                '2026-07-01,06:00,18:00,12,2,Drilling 8-3/4 in lateral,9500,10100\n'
                '2026-07-01,18:00,06:00,12,6,Trip out for bit,10100,10100\n')
    if kind == 'invoice':
        return ('# a daywork invoice, one row per charge; Rate Type is operating, standby, repair, moving or force majeure (anything else is listed, not checked)\n'
                'Date,Rate Type,Quantity,Unit,Unit Price,Amount\n'
                '2026-07-01,Operating day rate,1,day,30000,30000\n'
                '2026-07-02,Standby with crews,12,hour,25000,12500\n')
    raise ValueError("a template is 'ddr' or 'invoice'")


def _kind(text: str) -> str:
    t = (text or '').lower()
    if 'force majeure' in t:
        return 'force_majeure'
    if any(w in t for w in ('standby', 'stand-by', 'stand by')):
        return 'standby'
    if 'repair' in t:
        return 'repair'
    if any(w in t for w in ('mobiliz', 'demobiliz', 'moving', 'rig move', 'move rate', 'rig up', 'rig down')):
        return 'moving'
    if any(w in t for w in ('operating', 'daywork', 'day work', 'day rate', 'dayrate', 'drilling rate')):
        return 'operating'
    return 'other'


def read_ddr(text: str, day_start_h: float = 6.0) -> dict:
    heads, rows = _rows(text)
    used: set = set()
    col = {}
    for k in ('date', 'from', 'to', 'hours', 'code', 'activity', 'depth_from', 'depth_to'):
        i = _find(heads, DDR_COLS[k], used)
        if i is not None:
            col[k] = i; used.add(i)
    missing = [k for k in ('date',) if k not in col]
    if missing or not ('hours' in col or ('from' in col and 'to' in col)) or not ('code' in col or 'activity' in col):
        raise ValueError('not a daily drilling report this program reads: it needs a Date column, Hours (or From and To) and a Code or Activity '
                         f"column (found: {', '.join(heads)}); `gea invoice-check --template ddr` prints the headings")
    lines, errors = [], []
    for n, r in enumerate(rows, start=2):
        g = lambda k: r[col[k]] if k in col and col[k] < len(r) else ''
        d = _date(g('date'))
        if d is None:
            errors.append(f'row {n}: the date {g("date")!r} does not read'); continue
        a, b = _hm(g('from')), _hm(g('to'))
        h = None
        if g('hours'):
            try:
                h = float(g('hours'))
            except ValueError:
                errors.append(f'row {n}: hours {g("hours")!r} is not a number'); continue
        if a is not None and b is not None:
            span = (b - a) % 24.0 or (24.0 if b != a or (h and h >= 23.99) else 0.0)
            if h is None:
                h = span
        if h is None:
            errors.append(f'row {n}: no hours, and no From and To'); continue
        code = None
        cm = re.match(r'^\s*(\d{1,2})', g('code'))
        if cm:
            code = int(cm.group(1))
        act = g('activity')
        df_, dt_ = (lambda v: float(v.replace(',', '')) if re.match(r'^-?[\d,]+(\.\d+)?$', v or '') else None)(g('depth_from')), \
                   (lambda v: float(v.replace(',', '')) if re.match(r'^-?[\d,]+(\.\d+)?$', v or '') else None)(g('depth_to'))
        standby = code == 33 or any(w in (act or '').lower() for w in STANDBY_WORDS)
        lines.append({'row': n, 'date': d.isoformat(), 'from_h': a, 'to_h': b, 'hours': round(h, 3), 'code': code,
                      'code_name': IADC.get(code, '') if code is not None else '', 'activity': act, 'depth_from_ft': df_, 'depth_to_ft': dt_,
                      'standby': standby})
    return {'columns': {k: heads[i] for k, i in col.items()}, 'lines': lines, 'errors': errors}


def read_invoice(text: str) -> dict:
    heads, rows = _rows(text)
    used: set = set()
    col = {}
    for k in ('date', 'from', 'to', 'quantity', 'unit', 'amount', 'price', 'kind'):
        i = _find(heads, INV_COLS[k], used)
        if i is not None:
            # a 'rate' heading over numbers is the price; over words it is the kind
            if k == 'price' and _norm(heads[i]) == 'rate' and rows and not re.match(r'^[\d$,.\s]+$', rows[0][i] if i < len(rows[0]) else ''):
                continue
            col[k] = i; used.add(i)
    if not ('date' in col or ('from' in col and 'to' in col)) or 'quantity' not in col or not ('amount' in col or 'price' in col):
        raise ValueError('not an invoice this program reads: it needs a Date (or From and To), a Quantity, and an Amount or Unit Price '
                         f"(found: {', '.join(heads)}); `gea invoice-check --template invoice` prints the headings")
    lines, errors = [], []
    for n, r in enumerate(rows, start=2):
        g = lambda k: r[col[k]] if k in col and col[k] < len(r) else ''
        d0 = _date(g('date')) if 'date' in col else _date(g('from'))
        d1 = _date(g('to')) if ('to' in col and 'date' not in col) else d0
        if d0 is None or d1 is None or d1 < d0:
            errors.append(f'row {n}: the date does not read'); continue
        try:
            q = float(g('quantity').replace(',', ''))
        except ValueError:
            errors.append(f'row {n}: the quantity {g("quantity")!r} is not a number'); continue
        price = _num(g('price'), f'row {n} unit price') if g('price') else None
        amount = _num(g('amount'), f'row {n} amount') if g('amount') else None
        unit = (g('unit') or ('hour' if _norm(heads[col['quantity']]) == 'hours' else 'day')).lower()
        unit = 'hour' if unit.startswith(('h', 'hr')) else 'day'
        desc = g('kind')
        lines.append({'row': n, 'from': d0.isoformat(), 'to': d1.isoformat(), 'kind': _kind(desc), 'description': desc, 'quantity': q, 'unit': unit,
                      'price': price, 'amount': amount, 'hours': round(q * (24.0 if unit == 'day' else 1.0), 3)})
    return {'columns': {k: heads[i] for k, i in col.items()}, 'lines': lines, 'errors': errors}


def files(ws, well_id: Optional[str] = None) -> List[dict]:
    fs = _load(_dir(ws, 'files.json'), {'files': []})['files']
    return [f for f in fs if well_id is None or f['well_id'] == well_id]


def attach(ws, actor: str, well_id: str, kind: str, filename: str, content: bytes, source: str) -> dict:
    _well(ws, well_id)
    if kind not in ('ddr', 'invoice'):
        raise ValueError("attach a 'ddr' (daily drilling report) or an 'invoice'")
    if not (source or '').strip():
        raise ValueError('say where the file came from (e.g. "contractor invoice 4471, received 2026-08-03")')
    from . import xlsx_read as XR
    try:
        text = XR.text_of(content)
    except (KeyError, ValueError, zipfile.BadZipFile) as e:
        raise ValueError(f'the workbook does not read: {e}')
    t = terms(ws)
    parsed = read_ddr(text) if kind == 'ddr' else read_invoice(text)
    if not parsed['lines']:
        raise ValueError('no line of the file reads: ' + '; '.join(parsed['errors'][:5]))
    import hashlib
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename or kind + '.csv'))
    stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + name
    p = _dir(ws, well_id, stem)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(content)
    rec = {'file': f'{well_id}/{stem}', 'well_id': well_id, 'kind': kind, 'name': filename, 'source': source.strip(), 'sha256': hashlib.sha256(content).hexdigest(),
           'lines': len(parsed['lines']), 'errors': parsed['errors'][:20], 'columns': parsed['columns'], 'by': actor, 'utc': _utc()}
    with _LOCK:
        d = _load(_dir(ws, 'files.json'), {'schema': 1, 'files': []})
        d['files'].append(rec)
        _store(_dir(ws, 'files.json'), d)
    ws.audit(actor, 'invoice.attach', {k: rec[k] for k in ('file', 'well_id', 'kind', 'source', 'sha256', 'lines')})
    try:                                                  # the alert check reads the new file at its next tick
        from . import alerts as AL
        st = AL.state(ws); st.setdefault('last', {}).pop('invoices', None); AL.save_state(ws, st)
    except OSError:
        pass
    return rec


def remove(ws, actor: str, file: str) -> dict:
    with _LOCK:
        d = _load(_dir(ws, 'files.json'), {'schema': 1, 'files': []})
        if not any(f['file'] == file for f in d['files']):
            raise KeyError(f'no file {file}')
        d['files'] = [f for f in d['files'] if f['file'] != file]
        _store(_dir(ws, 'files.json'), d)
    ws.audit(actor, 'invoice.remove', {'file': file})
    return {'removed': file}


def _read_attached(ws, well_id: str, kind: str) -> Tuple[List[dict], List[str]]:
    from . import xlsx_read as XR
    out, errs = [], []
    for f in files(ws, well_id):
        if f['kind'] != kind:
            continue
        with open(_dir(ws, *f['file'].split('/')), 'rb') as fh:
            text = XR.text_of(fh.read())
        r = read_ddr(text) if kind == 'ddr' else read_invoice(text)
        for ln in r['lines']:
            out.append({**ln, 'file': f['name']})
        errs += [f"{f['name']}: {e}" for e in r['errors']]
    return out, errs


# -- the record, by report day -----------------------------------------------------------------------------------------
def _record_days(ws, well_id: str, day_start_h: float) -> Optional[dict]:
    """Per report day of the record: hours recorded, active, quiet, drilling; the bottom of the hole at the day's end;
    and a function giving the active share of any span."""
    from . import drill_string as DS
    try:
        c = DS._cache(ws, well_id, build=True)
    except (KeyError, OSError, ValueError, _WsErr):
        return None
    t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
    idx = np.asarray(c.index, dtype=np.float64)
    if len(idx) < 2:
        return None
    dt = np.diff(idx, append=idx[-1] + (idx[-1] - idx[-2]))
    dt = np.where(dt > 60.0, np.median(np.diff(idx)), dt)
    drl = np.asarray(c.extra('drilling'), dtype=bool)
    flow, rpm, bit = DS._ch(c, 'Total Pump Output'), DS._ch(c, 'Rotary RPM'), DS._ch(c, 'Bit Depth')
    act = drl.copy()
    if flow is not None:
        act |= np.nan_to_num(flow) > FLOW_FLOOR_GPM
    if rpm is not None:
        act |= np.nan_to_num(rpm) > RPM_FLOOR
    if bit is not None:
        b = np.nan_to_num(bit)
        j = np.searchsorted(idx, idx - 60.0)                      # the bit's travel over the minute before
        act |= np.abs(b - b[j]) > BIT_MOVE_FT_PER_MIN
    channels = [n for n, a in (('drilling flag', True), ('pump output', flow is not None), ('rotary', rpm is not None), ('bit depth', bit is not None)) if a]
    bottom = c.extra('bottom')
    bottom = np.asarray(bottom, dtype=np.float64) if bottom is not None else None
    secs_from_day = (idx + (t0 - datetime(t0.year, t0.month, t0.day)).total_seconds() - day_start_h * 3600.0)
    day_no = np.floor(secs_from_day / 86400.0).astype(np.int64)
    base = datetime(t0.year, t0.month, t0.day) + timedelta(hours=day_start_h)
    days = {}
    for k in np.unique(day_no):
        m = day_no == k
        start = base + timedelta(days=int(k))
        rec = {'start': start.isoformat(timespec='minutes'), 'recorded_h': float((dt * m).sum() / 3600.0), 'active_h': float((dt * (m & act)).sum() / 3600.0),
               'drilling_h': float((dt * (m & drl)).sum() / 3600.0)}
        rec['quiet_h'] = rec['recorded_h'] - rec['active_h']
        if bottom is not None:
            bb = bottom[m]; prior = bottom[:int(np.flatnonzero(m)[0]) + 1]
            prior = prior[np.isfinite(prior)]
            fin = bb[np.isfinite(bb)]
            if len(fin):
                rec['bottom_start_ft'] = float(prior[-1]) if len(prior) else float(fin[0])
                rec['bottom_end_ft'] = float(fin[-1])
                rec['footage_ft'] = max(0.0, float(np.nanmax(bb)) - rec['bottom_start_ft'])
        days[start.date().isoformat()] = rec

    def share(a: datetime, b: datetime) -> Optional[Tuple[float, float]]:
        s0, s1 = (a - t0).total_seconds(), (b - t0).total_seconds()
        i0, i1 = int(np.searchsorted(idx, s0, 'left')), int(np.searchsorted(idx, s1, 'left'))
        if i1 <= i0:
            return None
        rec_h = float(dt[i0:i1].sum() / 3600.0)
        return rec_h, float((dt[i0:i1] * act[i0:i1]).sum() / 3600.0)
    return {'days': days, 'share': share, 'first': (t0 + timedelta(seconds=float(idx[0]))).isoformat(timespec='minutes'),
            'last': (t0 + timedelta(seconds=float(idx[-1]))).isoformat(timespec='minutes'), 'channels': channels}


# -- the cross-check ----------------------------------------------------------------------------------------------------
def _r(v, n=2):
    return None if v is None else round(float(v), n)


def check(ws, well_id: str) -> dict:
    w = _well(ws, well_id)
    t = terms(ws)
    out = {'well_id': well_id, 'well': w.get('display') or well_id, 'terms': t or None, 'days': [], 'findings': [], 'other_lines': [],
           'files': files(ws, well_id), 'iadc_source': IADC_SOURCE, 'contract_source': CONTRACT_SOURCE}
    if not t:
        out['status'] = 'NO TERMS'
        out['detail'] = "declare the contract's terms first: the report day's start, the day rates and the repair allowance, with their source"
        return out
    hh, mm = (int(x) for x in t['day_start'].split(':'))
    ds_h = hh + mm / 60.0
    ddr, e1 = _read_attached(ws, well_id, 'ddr')
    inv, e2 = _read_attached(ws, well_id, 'invoice')
    out['read_errors'] = e1 + e2
    if not ddr and not inv:
        out['status'] = 'NO FILES'
        out['detail'] = 'attach the daily drilling reports and the invoice for this well'
        return out
    shift = 0 if t.get('report_date', 'start') == 'start' else -1          # the report day a report date names
    rec = _record_days(ws, well_id, ds_h)
    out['record'] = {k: rec[k] for k in ('first', 'last', 'channels')} if rec else None
    rates = t['rates_usd_per_day']
    op = rates['operating']
    by_day: Dict[str, dict] = {}

    def day(k: str) -> dict:
        return by_day.setdefault(k, {'date': k, 'ddr': [], 'billed': [], 'findings': []})

    def find(dk: str, kind: str, severity: str, detail: str, usd: float = 0.0, ref: str = ''):
        f = {'date': dk, 'check': kind, 'severity': severity, 'detail': detail, 'usd_in_question': round(usd, 2), 'ref': ref}
        day(dk)['findings'].append(f)
        out['findings'].append(f)
        out['findings'].sort(key=lambda x: x['date'])
    for ln in ddr:
        dk = (date.fromisoformat(ln['date']) + timedelta(days=shift)).isoformat()
        day(dk)['ddr'].append(ln)
    for ln in inv:
        d0, d1 = date.fromisoformat(ln['from']), date.fromisoformat(ln['to'])
        n = (d1 - d0).days + 1
        if ln['kind'] == 'other':
            out['other_lines'].append(ln)
            continue
        for i in range(n):
            dk = (d0 + timedelta(days=i)).isoformat()
            day(dk)['billed'].append({**ln, 'hours_day': ln['hours'] / n, 'amount_day': (ln['amount'] / n) if ln['amount'] is not None else None})
    # the invoice lines themselves: the price and the arithmetic
    for ln in inv:
        if ln['kind'] == 'other':
            continue
        ref = f"{ln['file']} row {ln['row']}"
        cr = rates.get(ln['kind'])
        if ln['price'] is not None and cr is not None:
            per_day = ln['price'] * (24.0 if ln['unit'] == 'hour' else 1.0)
            if abs(per_day - cr) > TOL['money']:
                find(ln['from'], 'RATE', 'DIFFERS', f"{ln['description'] or ln['kind']}: billed at {per_day:,.2f} USD/day; the contract's {ln['kind'].replace('_', ' ')} rate is {cr:,.2f}",
                     (per_day - cr) * ln['hours'] / 24.0, ref)
        if ln['price'] is not None and ln['amount'] is not None and abs(ln['quantity'] * ln['price'] - ln['amount']) > TOL['money']:
            find(ln['from'], 'ARITHMETIC', 'DIFFERS', f"{ln['quantity']:g} {ln['unit']}(s) at {ln['price']:,.2f} is {ln['quantity'] * ln['price']:,.2f}, billed {ln['amount']:,.2f}",
                 ln['amount'] - ln['quantity'] * ln['price'], ref)
    # repair: jobs (contiguous repair lines) and the month's running total
    month_used: Dict[str, float] = {}
    job_h, prev_end = 0.0, None
    repair_paid_excess: Dict[str, float] = {}
    for dk in sorted(by_day):
        for ln in by_day[dk]['ddr']:
            if ln['code'] != 8:
                job_h, prev_end = 0.0, None
                continue
            job_h += ln['hours']
            mk = dk[:7]
            before_m = month_used.get(mk, 0.0)
            month_used[mk] = before_m + ln['hours']
            over = 0.0
            if t.get('repair_hours_per_job') is not None:
                over = max(over, min(ln['hours'], job_h - t['repair_hours_per_job']))
            if t.get('repair_hours_per_month') is not None:
                over = max(over, min(ln['hours'], month_used[mk] - t['repair_hours_per_month']))
            if over > 0:
                repair_paid_excess[dk] = repair_paid_excess.get(dk, 0.0) + over
    # day by day
    for dk in sorted(by_day):
        D = by_day[dk]
        rd = rec['days'].get(dk) if rec else None
        D['record'] = {k: _r(v, 2 if k.endswith('_h') else 1) for k, v in rd.items() if k != 'start'} if rd else None
        rep_h = sum(x['hours'] for x in D['ddr'])
        billed = {k: sum(b['hours_day'] for b in D['billed'] if b['kind'] == k) for k in KINDS}
        D['ddr_hours'] = round(rep_h, 2)
        D['billed_hours'] = {k: round(v, 2) for k, v in billed.items() if v}
        D['billed_usd'] = round(sum(b['amount_day'] for b in D['billed'] if b['amount_day'] is not None), 2)
        tot_b = sum(billed.values())
        if D['ddr'] and abs(rep_h - 24.0) > TOL['day_h']:
            find(dk, 'REPORT DAY', 'QUESTION', f"the report's lines add up to {rep_h:g} h, not 24")
        spans = sorted((x['from_h'], x['to_h']) for x in D['ddr'] if x['from_h'] is not None and x['to_h'] is not None)
        if tot_b > 24.0 + TOL['day_h']:
            find(dk, 'OVER 24 H', 'DIFFERS', f"{tot_b:g} h billed in one day ({', '.join(f'{k} {v:g} h' for k, v in billed.items() if v)})",
                 (tot_b - 24.0) / 24.0 * op)
        rows_by_key: Dict[tuple, int] = {}
        for b in D['billed']:
            k = (b['kind'], b['from'], b['to'])
            rows_by_key[k] = rows_by_key.get(k, 0) + 1
        for k, n in rows_by_key.items():
            if n > 1:
                find(dk, 'BILLED TWICE', 'DIFFERS', f"{n} lines bill {k[0].replace('_', ' ')} for this day")
        if D['billed'] and not D['ddr']:
            find(dk, 'NO REPORT', 'QUESTION', 'billed, and no daily drilling report for the day is attached')
        if D['billed'] and rec and not rd:
            find(dk, 'NOT IN THE RECORD', 'QUESTION', f"billed, and the drilling record holds nothing for this day (it runs {rec['first']} to {rec['last']}); a move or rig-up day is off the recorder")
        # standby hours billed at the operating rate
        sb = sum(x['hours'] for x in D['ddr'] if x['standby'])
        if sb and billed['operating'] > 0:
            paid_sb = billed['standby']
            at_op = max(0.0, min(sb - paid_sb, billed['operating']))
            if at_op > TOL['day_h']:
                sbr = rates.get('standby')
                find(dk, 'STANDBY AT OPERATING', 'DIFFERS',
                     f"the report puts {sb:g} h on standby ({'; '.join(x['activity'] or x['code_name'] for x in D['ddr'] if x['standby'])}); {billed['operating']:g} h billed at the operating rate"
                     + ('' if sbr is not None else '; the standby rate is not declared, so the difference is not priced'),
                     at_op / 24.0 * (op - sbr) if sbr is not None else 0.0)
        # repair past the allowance at the operating rate
        ex = repair_paid_excess.get(dk, 0.0)
        if ex > 0 and billed['operating'] > 0 and billed['repair'] < ex - TOL['day_h']:
            rr = rates.get('repair', 0.0)
            allow = ', '.join(x for x in ((f"{t['repair_hours_per_job']:g} h a job" if t.get('repair_hours_per_job') is not None else ''),
                                           (f"{t['repair_hours_per_month']:g} h a month" if t.get('repair_hours_per_month') is not None else '')) if x)
            find(dk, 'REPAIR OVER ALLOWANCE', 'DIFFERS',
                 f"{ex:g} h of rig repair (code 8) past the allowance ({allow}) and the day billed at the operating rate; past it the repair rate is {rr:,.2f} USD/day",
                 (ex - billed['repair']) / 24.0 * (op - rr))
        # the report against the record
        if rd:
            r_drl = sum(x['hours'] for x in D['ddr'] if x['code'] == 2)
            if D['ddr'] and abs(r_drl - rd['drilling_h']) > max(TOL['drilling_h'], TOL['drilling_frac'] * max(r_drl, rd['drilling_h'])):
                find(dk, 'DRILLING HOURS', 'DIFFERS', f"reported drilling (code 2) {r_drl:g} h; the record shows {rd['drilling_h']:.1f} h on bottom making hole")
            ends = [x['depth_to_ft'] for x in D['ddr'] if x['depth_to_ft'] is not None]
            starts = [x['depth_from_ft'] for x in D['ddr'] if x['depth_from_ft'] is not None]
            if ends and rd.get('bottom_end_ft') is not None:
                if abs(max(ends) - rd['bottom_end_ft']) > TOL['depth_ft']:
                    find(dk, 'DEPTH', 'DIFFERS', f"reported depth at the day's end {max(ends):,.0f} ft; the record's hole bottom {rd['bottom_end_ft']:,.0f} ft")
                if starts:
                    rf = max(ends) - min(starts)
                    if abs(rf - rd.get('footage_ft', 0.0)) > max(TOL['footage_ft'], TOL['footage_frac'] * max(rf, rd.get('footage_ft', 0.0))):
                        find(dk, 'FOOTAGE', 'DIFFERS', f"reported footage {rf:,.0f} ft; the record's {rd.get('footage_ft', 0.0):,.0f} ft of new hole")
            unbilled_quiet = rd['quiet_h'] - billed['standby'] - billed['repair'] - billed['force_majeure']
            if billed['operating'] and unbilled_quiet >= TOL['quiet_question_h']:
                find(dk, 'QUIET ON THE RECORD', 'QUESTION',
                     f"{billed['operating']:g} h billed at the operating rate; the record shows the pumps off, the rotary still and the bit still for {rd['quiet_h']:.1f} h of "
                     f"{rd['recorded_h']:.1f} recorded, {unbilled_quiet:.1f} h more than the hours billed at standby, repair or force majeure "
                     "(waiting on cement, rigging up and repairs within the allowance are operating time and quiet on the recorder)")
            if rd['recorded_h'] < 23.0 and D['billed']:
                D['record_partial'] = True
            # each timed line against the record over its own span
            d0 = datetime.fromisoformat(dk) + timedelta(hours=ds_h)
            for x in D['ddr']:
                if x['from_h'] is None or x['to_h'] is None or not (x['code'] in ACTIVE_CODES or x['code'] == 8):
                    continue
                off_a = (x['from_h'] - ds_h) % 24.0
                a = d0 + timedelta(hours=off_a)
                b = a + timedelta(hours=x['hours'])
                sh = rec['share'](a, b)
                if not sh or sh[0] < 0.5 * x['hours']:
                    continue
                frac = sh[1] / sh[0]
                when = f"{a.strftime('%H:%M')}-{b.strftime('%H:%M')}"
                if x['code'] in ACTIVE_CODES and frac < TOL['line_active_frac']:
                    find(dk, 'LINE VS RECORD', 'DIFFERS', f"{when} reported as {ACTIVE_CODES[x['code']]} (code {x['code']}); the record shows the rig working {100 * frac:.0f} % of that span",
                         ref=f"{x['file']} row {x['row']}")
                if x['code'] == 8 and frac > TOL['line_active_frac']:
                    find(dk, 'LINE VS RECORD', 'QUESTION', f"{when} reported as rig repair (code 8); the record shows the rig working {100 * frac:.0f} % of that span",
                         ref=f"{x['file']} row {x['row']}")
        sev = {f['severity'] for f in D['findings']}
        D['status'] = 'DIFFERS' if 'DIFFERS' in sev else 'QUESTION' if 'QUESTION' in sev else 'AGREES'
        D['usd_in_question'] = round(sum(f['usd_in_question'] for f in D['findings']), 2)
        out['days'].append(D)
    try:                                                  # the time lost against the pad's targets, beside each day (not a finding: daywork pays it)
        from . import rig_state as RS
        lost = RS.lost_by_day(ws, well_id)
    except Exception:
        lost = {}
    for D in out['days']:
        D['lost_time'] = lost.get(D['date'])
    out['totals'] = {'days': len(out['days']), 'agrees': sum(d['status'] == 'AGREES' for d in out['days']),
                     'question': sum(d['status'] == 'QUESTION' for d in out['days']), 'differs': sum(d['status'] == 'DIFFERS' for d in out['days']),
                     'billed_usd': round(sum(ln['amount'] or 0.0 for ln in inv), 2),
                     'billed_checked_usd': round(sum(ln['amount'] or 0.0 for ln in inv if ln['kind'] != 'other'), 2),
                     'usd_in_question': round(sum(f['usd_in_question'] for f in out['findings']), 2)}
    out['status'] = 'DIFFERS' if out['totals']['differs'] else 'QUESTION' if out['totals']['question'] else 'AGREES'
    out['how'] = (f"report days start at {t['day_start']} on the rig's clock and a report is dated by the day it {'starts' if t.get('report_date', 'start') == 'start' else 'ends'}; "
                  f"the record counts the rig working when the drilling flag is set, the pumps put out more than {FLOW_FLOOR_GPM:g} gal/min, the rotary turns above "
                  f"{RPM_FLOOR:g} rpm or the bit moves more than {BIT_MOVE_FT_PER_MIN:g} ft in a minute (of the channels this record carries: {', '.join((rec or {}).get('channels') or ['none'])}); "
                  "dollars in question are at the contract's declared rates")
    return out


# -- the report --------------------------------------------------------------------------------------------------------
def build_report(ws, well_id: str, version: str):
    from . import client_reports as CR
    r = check(ws, well_id)
    now = datetime.now(timezone.utc)
    rid = f"INV-{re.sub(r'[^A-Za-z0-9]+', '-', r['well'])}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    T = r.get('totals') or {}
    front = [['Report ID', rid], ['Well', r['well']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['Outcome', f"{r['status']}: {T.get('agrees', 0)} day(s) agree, {T.get('question', 0)} in question, {T.get('differs', 0)} differ; "
                        f"{T.get('usd_in_question', 0):,.2f} USD in question of {T.get('billed_checked_usd', 0):,.2f} billed at day rates" if T else r['status']]]
    t = r['terms'] or {}
    s1 = CR.Section('1', 'The terms and the files', [], [])
    if t:
        s1.paragraphs.append(f"Contract terms declared {t.get('utc', '')} by {t.get('by', '')} from: {t['source']}. Report day from {t['day_start']} on the rig's clock; "
                             f"repair allowance: {(format(t['repair_hours_per_job'], 'g') + ' h a job') if t.get('repair_hours_per_job') is not None else 'none stated a job'}, "
                             f"{(format(t['repair_hours_per_month'], 'g') + ' h a month') if t.get('repair_hours_per_month') is not None else 'none stated a month'}.")
        s1.tables.append(CR.Table(['Rate', 'USD per day'], [[k.replace('_', ' '), f'{v:,.2f}'] for k, v in t['rates_usd_per_day'].items()]))
    s1.tables.append(CR.Table(['File', 'Kind', 'Lines', 'Source', 'SHA-256'], [[f['name'], f['kind'], f['lines'], f['source'], f['sha256'][:16] + '...'] for f in r['files']]))
    if r.get('record'):
        s1.paragraphs.append(f"The drilling record runs {r['record']['first']} to {r['record']['last']} (the recorder's clock).")
    hw = r.get('how') or r.get('detail') or ''
    s1.paragraphs.append(hw[:1].upper() + hw[1:] + '.')
    s2 = CR.Section('2', 'Day by day', [], [])
    s2.tables.append(CR.Table(['Day', 'Result', 'Billed (h)', 'Billed (USD)', 'Report (h)', 'Record: working / quiet / drilling (h)', 'Time lost vs targets (h)', 'USD in question'],
                              [[d['date'], d['status'], ', '.join(f"{k.replace('_', ' ')} {v:g}" for k, v in d['billed_hours'].items()) or '-', f"{d['billed_usd']:,.2f}",
                                f"{d['ddr_hours']:g}", (f"{d['record']['active_h']:.1f} / {d['record']['quiet_h']:.1f} / {d['record']['drilling_h']:.1f}" if d['record'] else 'not in the record'),
                                (f"{d['lost_time']['lost_h']:.2f}" if d.get('lost_time') else '-'), f"{d['usd_in_question']:,.2f}"] for d in r['days']]))
    if any(d.get('lost_time') for d in r['days']):
        s2.paragraphs.append('Time lost vs targets: the drilling connections\' weight-to-weight time and the trips\' pipe-handling time past the pad\'s targets '
                             '(the Rig time tab). Under daywork the operator pays for it; it is shown beside the bill, not counted in question.')
    s3 = CR.Section('3', 'What differs, and what to ask', [], [])
    if r['findings']:
        s3.tables.append(CR.Table(['Day', 'Check', 'Result', 'What the three accounts say', 'USD', 'Line'],
                                  [[f['date'], f['check'], f['severity'], f['detail'], f"{f['usd_in_question']:,.2f}", f['ref']] for f in sorted(r['findings'], key=lambda f: f['date'])]))
    else:
        s3.paragraphs.append('Nothing differs.')
    if r['other_lines']:
        s3.paragraphs.append('Charges other than the day rates are listed and not cross-checked: '
                             + '; '.join(f"{x['description']} {x['amount'] or 0:,.2f}" for x in r['other_lines'][:20]) + '.')
    s3.paragraphs.append('This lays the invoice, the daily drilling reports and the drilling record side by side at the contract terms as declared; it is not a '
                         'ruling on what is owed - the contract and the parties decide.')
    doc = CR.Document(f"Invoice cross-check - {r['well']}", rid, front, [s1, s2, s3],
                      footer=f"Operation codes: {IADC_SOURCE}. Rate kinds: {CONTRACT_SOURCE}.",
                      data={'report_id': rid, **{k: v for k, v in r.items() if k not in ('files',)}, 'files': r['files']})
    return doc, r


def write_report(ws, well_id: str, version: str, actor: str = 'system') -> dict:
    from . import client_reports as CR
    doc, r = build_report(ws, well_id, version)
    out = os.path.join(ws.reports_dir, 'wells', well_id)
    paths = CR.write(doc, out, 'invoice_check')
    ws.audit(actor, 'report.invoice_check', {'well': well_id, 'report_id': doc.report_id, 'status': r['status'], 'totals': r.get('totals')})
    return {'report_id': doc.report_id, 'paths': paths, 'status': r['status'], 'totals': r.get('totals')}


def page(ws, well_id: Optional[str] = None) -> dict:
    wells = [{'id': w['id'], 'display': w.get('display') or w['id']} for w in ws.wells()]
    wid = well_id or (wells[0]['id'] if wells else None)
    res = {'wells': wells, 'well_id': wid, 'terms': terms(ws) or None, 'kinds': list(KINDS)}
    if wid:
        res['check'] = check(ws, wid)
        p = os.path.join(ws.reports_dir, 'wells', wid, 'invoice_check.html')
        res['report'] = f'wells/{wid}/invoice_check' if os.path.isfile(p) else None
    return res


def text(r: dict) -> str:
    L = [f"Invoice cross-check - {r['well']}: {r['status']}"]
    if r.get('detail'):
        L.append('  ' + r['detail'])
    T = r.get('totals')
    if T:
        L.append(f"  {T['days']} day(s): {T['agrees']} agree, {T['question']} in question, {T['differs']} differ; {T['usd_in_question']:,.2f} USD in question "
                 f"of {T['billed_checked_usd']:,.2f} billed at day rates")
    for d in r.get('days', []):
        L.append(f"  {d['date']}  {d['status']:<9} billed {', '.join(f'{k} {v:g} h' for k, v in d['billed_hours'].items()) or '-':<28} report {d['ddr_hours']:g} h"
                 + (f"  record working {d['record']['active_h']:.1f} h, quiet {d['record']['quiet_h']:.1f} h, drilling {d['record']['drilling_h']:.1f} h" if d['record'] else '  not in the record'))
        for f in d['findings']:
            L.append(f"      {f['severity']:<8} {f['check']}: {f['detail']}" + (f"  [{f['usd_in_question']:,.2f} USD]" if f['usd_in_question'] else '') + (f"  ({f['ref']})" if f['ref'] else ''))
    for e in r.get('read_errors') or []:
        L.append('  not read: ' + e)
    return '\n'.join(L)


# -- a labelled scene for the gate ------------------------------------------------------------------------------------
def selftest_files(tmp: str) -> dict:
    """A recorder export over five report days (06:00 to 06:00, one sample every 10 s), its daily drilling reports and
    an invoice with seven planted differences - each one a check this module must make:

    day 1 (07-01): 18 h drilling 5,000 -> 5,900 ft, 6 h circulating; report and record agree - and the invoice bills
                   the day twice (BILLED TWICE, OVER 24 H);
    day 2 (07-02): 6 h drilling to 6,200 ft, circulating, the trip out and in, and a 4 h rig repair 20:00-24:00 with the
                   rig quiet; the allowance is 2 h a month, so 2 h of repair are billed at the operating rate (REPAIR OVER
                   ALLOWANCE); the report calls 14:00-16:00 a trip when the record shows the rig still (LINE VS RECORD);
    day 3 (07-03): 20 h quiet that the report puts on standby, waiting on the operator's orders, billed at the
                   operating rate (STANDBY AT OPERATING; QUIET ON THE RECORD); 4 h circulating;
    day 4 (07-04): 24 h drilling 6,200 -> 7,400 ft; the report says 7,600 (DEPTH, FOOTAGE); the invoice's amount for
                   one day at 30,000 is 31,000 (ARITHMETIC);
    day 5 (07-05): billed, with no report and nothing in the record (NO REPORT, NOT IN THE RECORD);
    and a fuel line, listed and not checked."""
    p_edr = os.path.join(tmp, 'inv_edr.csv')
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'On Bottom (unitless)',
           'Total Pump Output (gal_per_min)', 'Rotary RPM (rpm)']
    t = datetime(2026, 7, 1, 6, 0, 0)
    step = 10
    rows = []
    hole = [5000.0]

    def seg(hours, mode, rate=0.0, bit_to=None):
        n = int(hours * 3600 / step)
        b0 = rows_bit[0]
        for i in range(n):
            nonlocal_t[0] += timedelta(seconds=step)
            if mode == 'drill':
                hole[0] += rate * step / 3600.0; bit = hole[0]; flow, rpm, on, rop = 600.0, 120.0, 1, rate
            elif mode == 'circ':
                bit = rows_bit[0]; flow, rpm, on, rop = 600.0, 60.0, 0, 0.0
            elif mode == 'trip':
                bit = b0 + (bit_to - b0) * (i + 1) / n; flow, rpm, on, rop = 0.0, 0.0, 0, 0.0
            else:
                bit = rows_bit[0]; flow, rpm, on, rop = 0.0, 0.0, 0, 0.0
            rows_bit[0] = bit
            tt = nonlocal_t[0]
            rows.append([tt.strftime('%Y/%m/%d'), tt.strftime('%H:%M:%S'), f'{hole[0]:.1f}', f'{bit:.1f}', f'{rop:.1f}', str(on), f'{flow:.0f}', f'{rpm:.0f}'])
    nonlocal_t = [t - timedelta(seconds=step)]
    rows_bit = [5000.0]
    seg(18, 'drill', 50.0); seg(6, 'circ')                                         # day 1
    seg(6, 'drill', 50.0); seg(2, 'circ'); seg(2, 'quiet'); seg(4, 'trip', bit_to=0.0); seg(4, 'quiet'); seg(2, 'trip', bit_to=6200.0); seg(4, 'circ')   # day 2
    seg(20, 'quiet'); seg(4, 'circ')                                               # day 3
    seg(24, 'drill', 50.0)                                                         # day 4
    with open(p_edr, 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f); wr.writerow(hdr); wr.writerows(rows)
    ddr = ('Date,From,To,Hours,Code,Activity,Depth From (ft),Depth To (ft)\n'
           '2026-07-01,06:00,24:00,18,2,Drilling lateral,5000,5900\n'
           '2026-07-01,00:00,06:00,6,5,Circulate and condition,5900,5900\n'
           '2026-07-02,06:00,12:00,6,2,Drilling lateral,5900,6200\n'
           '2026-07-02,12:00,14:00,2,5,Circulate bottoms up,6200,6200\n'
           '2026-07-02,14:00,16:00,2,6,Trip out,6200,6200\n'
           '2026-07-02,16:00,20:00,4,6,Trip out,6200,6200\n'
           '2026-07-02,20:00,24:00,4,8,Repair top drive,6200,6200\n'
           '2026-07-02,00:00,02:00,2,6,Trip in,6200,6200\n'
           '2026-07-02,02:00,06:00,4,5,Circulate,6200,6200\n'
           "2026-07-03,06:00,02:00,20,21,Waiting on operator's orders,6200,6200\n"
           '2026-07-03,02:00,06:00,4,5,Circulate and condition,6200,6200\n'
           '2026-07-04,06:00,06:00,24,2,Drilling lateral,6200,7600\n')
    inv = ('Date,Rate Type,Quantity,Unit,Unit Price,Amount\n'
           '2026-07-01,Operating day rate,1,day,30000,30000\n'
           '2026-07-01,Operating day rate,1,day,30000,30000\n'
           '2026-07-02,Operating day rate,1,day,30000,30000\n'
           '2026-07-03,Operating day rate,1,day,30000,30000\n'
           '2026-07-04,Operating day rate,1,day,30000,31000\n'
           '2026-07-05,Operating day rate,1,day,30000,30000\n'
           '2026-07-04,Fuel (diesel),1,lot,4200,4200\n')
    return {'edr': p_edr, 'ddr': ddr, 'invoice': inv}
