"""The economics of a well: what its drilling cost by the operator's own rates, against what the market was doing.

Two kinds of number, never mixed:

- the well's time and footage, from its drilling record (the recorder's own clock): days on the record, feet
  drilled, hours on bottom - measured;
- its cost, from rates and items the operator declares (a spread rate in dollars per day over stated dates, a
  bit, a cement job, an AFE), each with where it came from - declared, not measured. Cost is the declared rates
  integrated over the record's time, plus the items. Where the rates leave part of the time uncovered, the cost
  says so and no cost per foot is given: a partial cost divided by the whole footage would understate it.

Beside them, the market: daily spot prices published by the U.S. Energy Information Administration (WTI at
Cushing, Henry Hub gas) shipped with the program and refreshable, and any other series the workspace imports
(a rig count, a regional price) with its source stated. Prices are context: the page shows what they were while
each well was drilled; it does not say they moved the well's cost. Across wells, a rank correlation between
cost per foot and price is computed only when ten or more wells have a complete cost - below that it is noise.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference')
BUILTIN = ('wti', 'henry_hub')
MIN_WELLS_FOR_CORRELATION = 10
WINDOW_PAD_DAYS = 60
_LOCK = threading.Lock()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _prov() -> dict:
    with open(os.path.join(REF_DIR, 'eia_prices.provenance.json'), encoding='utf-8') as f:
        return json.load(f)


def _dir(ws, *parts) -> str:
    return os.path.join(ws.path, 'economics', *parts)


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', (s or '').lower()).strip('_')[:40]


def _dt(s) -> datetime:
    """A naive date-time (the recorder's clock, or a declared date taken at midnight)."""
    t = str(s).strip().replace('Z', '')
    d = datetime.fromisoformat(t[:19]) if len(t) > 10 else datetime.fromisoformat(t[:10])
    return d.replace(tzinfo=None)


# -- price series ------------------------------------------------------------------------------------
def _parse_series(text: str, date_col: Optional[str] = None, value_col: Optional[str] = None) -> Tuple[List[str], List[float], dict]:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if len(rows) < 3:
        raise ValueError('a series needs a header and at least two rows')
    hdr = [h.strip() for h in rows[0]]
    low = [h.lower() for h in hdr]
    if date_col:
        if date_col.lower() not in low:
            raise ValueError(f'no column named {date_col!r} (the header: {", ".join(hdr)})')
        di = low.index(date_col.lower())
    else:
        di = next((i for i, h in enumerate(low) if h in ('date', 'day', 'week', 'period', 'month')), 0)
    if value_col:
        if value_col.lower() not in low:
            raise ValueError(f'no column named {value_col!r} (the header: {", ".join(hdr)})')
        vi = low.index(value_col.lower())
    else:
        vi = next((i for i, h in enumerate(low) if i != di and h in ('price', 'value', 'close', 'count', 'rigs')), 1 if di == 0 else 0)
    dates, vals = [], []
    for n, r in enumerate(rows[1:], start=2):
        try:
            d = date.fromisoformat(r[di].strip()[:10]).isoformat()
        except (ValueError, IndexError):
            raise ValueError(f'row {n}: {r[di] if di < len(r) else ""!r} is not a date (YYYY-MM-DD)')
        cell = r[vi].strip() if vi < len(r) else ''
        if cell == '':
            continue                                   # a day the publisher left blank: no value, never a guess
        try:
            v = float(cell.replace(',', ''))
        except ValueError:
            raise ValueError(f'row {n}: {cell!r} is not a number')
        dates.append(d); vals.append(v)
    if len(set(dates)) != len(dates):
        raise ValueError('the series repeats a date')
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    dates = [dates[i] for i in order]; vals = [vals[i] for i in order]
    return dates, vals, {'date_column': hdr[di], 'value_column': hdr[vi], 'rows': len(dates)}


def series_list(ws=None) -> List[dict]:
    """Every series the page can show: the two shipped EIA series (or the workspace's refreshed copies) and any imported."""
    p = _prov()
    out = []
    for key in BUILTIN:
        s = p['series'][key]
        meta = {'id': key, 'name': s['name'], 'unit': s['unit'], 'source': f"U.S. Energy Information Administration, series {s['eia_series']} ({s['eia_url']})",
                'kind': 'shipped', 'as_of': s['last'], 'rows': s['rows']}
        if ws is not None and os.path.isfile(_dir(ws, 'series', key + '.json')):
            with open(_dir(ws, 'series', key + '.json'), encoding='utf-8') as f:
                meta.update(json.load(f))
        out.append(meta)
    if ws is not None and os.path.isdir(_dir(ws, 'series')):
        for fn in sorted(os.listdir(_dir(ws, 'series'))):
            if fn.endswith('.json') and fn[:-5] not in BUILTIN:
                with open(_dir(ws, 'series', fn), encoding='utf-8') as f:
                    out.append(json.load(f))
    return out


def read_series(ws, sid: str) -> Tuple[List[str], List[float], dict]:
    meta = next((m for m in series_list(ws) if m['id'] == sid), None)
    if meta is None:
        raise KeyError(f'no price series {sid}')
    if meta.get('kind') == 'shipped':
        with open(os.path.join(REF_DIR, _prov()['series'][sid]['file']), 'rb') as f:
            text = gzip.decompress(f.read()).decode('utf-8')
    else:
        with open(_dir(ws, 'series', meta['file']), encoding='utf-8-sig', errors='replace') as f:
            text = f.read()
    d, v, _ = _parse_series(text, meta.get('date_column'), meta.get('value_column'))
    return d, v, meta


def refresh_prices(ws, actor: str, fetch=None) -> List[dict]:
    """Fetch the latest copy of each shipped EIA series into the workspace. A copy that does not parse, or that ends earlier
    than the one already held, is refused and the one held is kept. `fetch(url) -> bytes` is replaceable for tests."""
    p = _prov()
    out = []
    fetch = fetch or (lambda u: urllib.request.urlopen(u, timeout=60).read())
    os.makedirs(_dir(ws, 'series'), exist_ok=True)
    for key in BUILTIN:
        s = p['series'][key]
        try:
            raw = fetch(s['refresh_url'])
            d, v, info = _parse_series(raw.decode('utf-8'))
        except Exception as e:
            out.append({'id': key, 'status': 'NOT REFRESHED', 'why': f'{type(e).__name__}: {e}'})
            continue
        held = next((m for m in series_list(ws) if m['id'] == key), {})
        if held.get('as_of') and d[-1] < held['as_of']:
            out.append({'id': key, 'status': 'NOT REFRESHED', 'why': f"the fetched copy ends {d[-1]}, earlier than the {held['as_of']} already held"})
            continue
        fn = key + '.csv'
        with open(_dir(ws, 'series', fn), 'wb') as f:
            f.write(raw)
        meta = {'id': key, 'name': s['name'], 'unit': s['unit'], 'kind': 'refreshed', 'file': fn, 'as_of': d[-1], 'rows': len(d),
                'source': f"U.S. Energy Information Administration, series {s['eia_series']}, via {s['refresh_url']}",
                'fetched_utc': _utc(), 'sha256': hashlib.sha256(raw).hexdigest(), **info}
        with open(_dir(ws, 'series', key + '.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=1)
        ws.audit(actor, 'economics.prices.refresh', {'series': key, 'as_of': d[-1], 'rows': len(d)})
        out.append({'id': key, 'status': 'REFRESHED', 'as_of': d[-1], 'rows': len(d)})
    return out


def import_series(ws, actor: str, name: str, unit: str, source: str, content: bytes, date_col: Optional[str] = None,
                  value_col: Optional[str] = None) -> dict:
    """Keep a series the workspace brings (a rig count, a regional price), verbatim, with its name, unit and source."""
    name, unit, source = (name or '').strip(), (unit or '').strip(), (source or '').strip()
    if not name or not unit or not source:
        raise ValueError('an imported series needs its name, its unit and where it came from')
    sid = _slug(name)
    if not sid or sid in BUILTIN:
        raise ValueError(f'choose another name than {name!r}')
    d, v, info = _parse_series(content.decode('utf-8-sig', errors='replace'), date_col, value_col)
    os.makedirs(_dir(ws, 'series'), exist_ok=True)
    fn = sid + '.csv'
    with open(_dir(ws, 'series', fn), 'wb') as f:
        f.write(content)
    meta = {'id': sid, 'name': name, 'unit': unit, 'source': source, 'kind': 'imported', 'file': fn, 'as_of': d[-1], 'first': d[0], 'rows': len(d),
            'imported_by': actor, 'imported_utc': _utc(), 'sha256': hashlib.sha256(content).hexdigest(), **info}
    with open(_dir(ws, 'series', sid + '.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1)
    ws.audit(actor, 'economics.series.import', {'series': sid, 'name': name, 'unit': unit, 'source': source, 'rows': len(d)})
    return meta


# -- the cost basis: what the operator declares ---------------------------------------------------------
def costs(ws) -> dict:
    p = _dir(ws, 'costs.json')
    if not os.path.isfile(p):
        return {'schema': 1, 'currency': 'USD', 'wells': {}}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _save_costs(ws, c: dict) -> None:
    os.makedirs(_dir(ws), exist_ok=True)
    p = _dir(ws, 'costs.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(c, f, indent=1)
    os.replace(p + '.tmp', p)


def _money(v, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{what} must be a number of dollars')
    if not math.isfinite(x) or x <= 0:
        raise ValueError(f'{what} must be more than zero')
    return round(x, 2)


def set_rate(ws, well_id: str, actor: str, usd_per_day, what: str, source: str, start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """A day rate over a stated period (open at either end when not given). Periods of one well's rates may not overlap:
    two rates for the same hour would be counted twice."""
    ws.well(well_id)
    if not (source or '').strip() or not (what or '').strip():
        raise ValueError('a rate needs what it pays for (e.g. "rig spread") and where it came from (e.g. "drilling contract, schedule B")')
    a = _dt(start) if start else None
    b = _dt(end) if end else None
    if a and b and b <= a:
        raise ValueError('the rate period must end after it starts')
    with _LOCK:
        c = costs(ws)
        w = c['wells'].setdefault(well_id, {'rates': [], 'items': [], 'afe': None})
        for r in w['rates']:
            ra = _dt(r['from']) if r['from'] else datetime.min
            rb = _dt(r['to']) if r['to'] else datetime.max
            if (a or datetime.min) < rb and (b or datetime.max) > ra:
                raise ValueError(f"this period overlaps the {r['usd_per_day']:,.2f} USD/day {r['what']} rate already declared "
                                 f"({r['from'] or 'open'} to {r['to'] or 'open'}); remove it first")
        rec = {'usd_per_day': _money(usd_per_day, 'the day rate'), 'from': a.isoformat() if a else None, 'to': b.isoformat() if b else None,
               'what': what.strip(), 'source': source.strip(), 'by': actor, 'utc': _utc()}
        w['rates'].append(rec)
        w['rates'].sort(key=lambda r: r['from'] or '')
        _save_costs(ws, c)
    ws.audit(actor, 'economics.rate', {'well': well_id, **{k: rec[k] for k in ('usd_per_day', 'from', 'to', 'what', 'source')}})
    return rec


def add_item(ws, well_id: str, actor: str, usd, what: str, source: str, section: Optional[int] = None) -> dict:
    ws.well(well_id)
    if not (source or '').strip() or not (what or '').strip():
        raise ValueError('an item needs what it is (e.g. "8.5 in PDC bit") and where its price came from')
    rec = {'usd': _money(usd, 'the item'), 'what': what.strip(), 'source': source.strip(), 'section': int(section) if section not in (None, '') else None,
           'by': actor, 'utc': _utc()}
    with _LOCK:
        c = costs(ws)
        c['wells'].setdefault(well_id, {'rates': [], 'items': [], 'afe': None})['items'].append(rec)
        _save_costs(ws, c)
    ws.audit(actor, 'economics.item', {'well': well_id, **{k: rec[k] for k in ('usd', 'what', 'source', 'section')}})
    return rec


def set_afe(ws, well_id: str, actor: str, usd, source: str) -> dict:
    ws.well(well_id)
    if not (source or '').strip():
        raise ValueError('an AFE needs where it came from (e.g. "AFE 26-114, approved 2026-05-02")')
    rec = {'usd': _money(usd, 'the AFE'), 'source': source.strip(), 'by': actor, 'utc': _utc()}
    with _LOCK:
        c = costs(ws)
        c['wells'].setdefault(well_id, {'rates': [], 'items': [], 'afe': None})['afe'] = rec
        _save_costs(ws, c)
    ws.audit(actor, 'economics.afe', {'well': well_id, 'usd': rec['usd'], 'source': rec['source']})
    return rec


def remove(ws, well_id: str, actor: str, kind: str, index: int = 0) -> dict:
    with _LOCK:
        c = costs(ws)
        w = c['wells'].get(well_id)
        if not w:
            raise KeyError(f'no cost basis for {well_id}')
        if kind == 'afe':
            gone, w['afe'] = w.get('afe'), None
        elif kind in ('rate', 'item'):
            lst = w['rates' if kind == 'rate' else 'items']
            if not 0 <= int(index) < len(lst):
                raise KeyError(f'no {kind} {index} for {well_id}')
            gone = lst.pop(int(index))
        else:
            raise ValueError('remove a rate, an item or the afe')
        _save_costs(ws, c)
    ws.audit(actor, 'economics.remove', {'well': well_id, 'kind': kind, 'removed': gone})
    return {'removed': gone}


# -- the arithmetic ------------------------------------------------------------------------------------
def _days(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 86400.0)


def _cost_over(rates: List[dict], a: datetime, b: datetime) -> Tuple[float, float]:
    """(dollars, days covered) of the declared rates over [a, b]."""
    usd = cov = 0.0
    for r in rates:
        ra = _dt(r['from']) if r['from'] else datetime.min
        rb = _dt(r['to']) if r['to'] else datetime.max
        lo, hi = max(a, ra), min(b, rb)
        if hi > lo:
            d = _days(lo, hi)
            usd += d * r['usd_per_day']; cov += d
    return usd, cov


def _window(dates: List[str], vals: List[float], a: date, b: date) -> Optional[dict]:
    pts = [(d, v) for d, v in zip(dates, vals) if a.isoformat() <= d <= b.isoformat()]
    if not pts:
        return None
    vs = [v for _, v in pts]
    return {'n': len(vs), 'mean': round(sum(vs) / len(vs), 3), 'min': min(vs), 'max': max(vs), 'first': pts[0], 'last': pts[-1]}


def _spearman(x: List[float], y: List[float]) -> float:
    def rank(a):
        o = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0] * len(a); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and a[o[j + 1]] == a[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((p - mx) * (q - my) for p, q in zip(rx, ry))
    den = math.sqrt(sum((p - mx) ** 2 for p in rx) * sum((q - my) ** 2 for q in ry))
    return num / den if den else float('nan')


def _record(ws, well_id: str) -> Optional[dict]:
    p = os.path.join(ws.reports_dir, 'wells', well_id, 'drilling_record.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def well_economics(ws, w: dict, basis: dict, series: Dict[str, Tuple[List[str], List[float], dict]]) -> dict:
    rec = _record(ws, w['id'])
    out = {'well_id': w['id'], 'display': w.get('display') or w['id'], 'record': rec is not None}
    if rec is None:
        out['why'] = 'no drilling record: refresh the reports once a drilling-recorder export is attached'
        return out
    a, b = _dt(rec['start']), _dt(rec['end'])
    days = _days(a, b)
    ft = rec.get('feet_drilled')
    obh = rec.get('on_bottom_hours')
    out.update({'start': rec['start'], 'end': rec['end'], 'days': round(days, 2), 'feet_drilled': ft, 'td_md_ft': rec.get('total_depth_md_ft'),
                'on_bottom_hours': obh, 'ft_per_day': round(ft / days, 1) if ft and days else None,
                'ft_per_on_bottom_hour': rec.get('feet_per_on_bottom_hour'), 'clock': "the drilling recorder's own clock"})
    rates, items, afe = basis.get('rates', []), basis.get('items', []), basis.get('afe')
    out['basis'] = {'rates': rates, 'items': items, 'afe': afe}
    if rates or items:
        usd_t, cov = _cost_over(rates, a, b)
        usd_i = sum(i['usd'] for i in items)
        full = rates and abs(cov - days) < 1e-6
        cost = round(usd_t + usd_i, 2)
        out['cost'] = {'usd': cost, 'time_usd': round(usd_t, 2), 'items_usd': round(usd_i, 2), 'days_covered': round(cov, 3),
                       'complete': bool(full),
                       'per_ft': round(cost / ft, 2) if full and ft else None, 'per_day': round(cost / days, 2) if full and days else None,
                       'per_on_bottom_hour': round(cost / obh, 2) if full and obh else None,
                       'why_incomplete': (None if full else (f'the rates declared cover {cov:,.2f} of the {days:,.2f} days on the record' if rates
                                                              else 'no day rate declared: the items alone are not the cost of the well'))}
        if afe:
            out['cost']['afe_usd'] = afe['usd']
            out['cost']['vs_afe_pct'] = round(100.0 * (cost - afe['usd']) / afe['usd'], 1) if full else None
    secs = []
    for s in rec.get('sections', []):
        sa, sb = _dt(s['start']), _dt(s['end'])
        sd = _days(sa, sb)
        row = {'n': s['n'], 'start': s['start'], 'end': s['end'], 'days': round(sd, 3), 'feet_drilled': s.get('feet_drilled'),
               'on_bottom_hours': s.get('on_bottom_hours'), 'from_ft': s.get('hole_depth_at_start_ft'), 'to_ft': s.get('hole_depth_to_ft')}
        if rates:
            u, c_ = _cost_over(rates, sa, sb)
            u += sum(i['usd'] for i in items if i.get('section') == s['n'])
            row['cost_usd'] = round(u, 2)
            row['complete'] = abs(c_ - sd) < 1e-6
            row['cost_per_ft'] = round(u / s['feet_drilled'], 2) if row['complete'] and s.get('feet_drilled') else None
        secs.append(row)
    out['sections'] = secs
    out['market'] = {sid: _window(d, v, a.date(), b.date()) for sid, (d, v, m) in series.items()}
    return out


def economics(ws) -> dict:
    """The page: every well's time, footage and declared cost, the market while each was drilled, the series for the charts."""
    series = {}
    for m in series_list(ws):
        try:
            series[m['id']] = read_series(ws, m['id'])
        except (OSError, ValueError, KeyError):
            continue
    basis = costs(ws)['wells']
    wells = [well_economics(ws, w, basis.get(w['id'], {}), series) for w in ws.wells()]
    drilled = [w for w in wells if w.get('record')]
    chart = None
    if drilled:
        a = min(_dt(w['start']) for w in drilled).date() - timedelta(days=WINDOW_PAD_DAYS)
        b = max(_dt(w['end']) for w in drilled).date() + timedelta(days=WINDOW_PAD_DAYS)
        chart = {'from': a.isoformat(), 'to': b.isoformat(), 'series': {}}
        for sid, (d, v, m) in series.items():
            pts = [(x, y) for x, y in zip(d, v) if a.isoformat() <= x <= b.isoformat()]
            chart['series'][sid] = {'name': m['name'], 'unit': m['unit'], 'dates': [p[0] for p in pts], 'values': [p[1] for p in pts]}
        chart['wells'] = [{'id': w['well_id'], 'display': w['display'], 'from': w['start'][:10], 'to': w['end'][:10]} for w in drilled]
    complete = [w for w in drilled if (w.get('cost') or {}).get('per_ft') is not None and (w['market'].get('wti') or {}).get('mean') is not None]
    corr = {'n_wells': len(complete), 'needed': MIN_WELLS_FOR_CORRELATION}
    if len(complete) >= MIN_WELLS_FOR_CORRELATION:
        corr['spearman_cost_per_ft_vs_wti'] = round(_spearman([w['cost']['per_ft'] for w in complete], [w['market']['wti']['mean'] for w in complete]), 3)
        corr['note'] = 'a rank correlation across wells, not a cause: depth, formation, rig and crew move cost per foot too'
    else:
        corr['note'] = (f"{len(complete)} well(s) have a complete cost; a correlation between cost per foot and price is reported from "
                        f"{MIN_WELLS_FOR_CORRELATION} - below that it is noise")
    return {'generated_utc': _utc(), 'currency': 'USD', 'series': [{k: m.get(k) for k in ('id', 'name', 'unit', 'source', 'kind', 'as_of', 'rows', 'fetched_utc')}
                                                                    for m in series_list(ws)],
            'latest': {sid: {'date': d[-1], 'value': v[-1]} for sid, (d, v, m) in series.items() if d},
            'wells': wells, 'chart': chart, 'correlation': corr,
            'attribution': _prov()['attribution'],
            'rule': 'Time and footage are measured, from each drilling record on the recorder\'s clock. Cost is declared: the operator\'s rates '
                    'over that time plus the items, and only a cost whose rates cover all of it is divided by the footage. Prices are context.'}


def text(e: dict) -> str:
    L = ['Economics', '  ' + e['rule']]
    for sid, x in e['latest'].items():
        m = next(s for s in e['series'] if s['id'] == sid)
        L.append(f"  {m['name']}: {x['value']:,} {m['unit']} on {x['date']} ({m['kind']})")
    for w in e['wells']:
        if not w.get('record'):
            L.append(f"  {w['display']}: {w['why']}")
            continue
        L.append(f"  {w['display']}: {w['start'][:16]} to {w['end'][:16]}, {w['days']:,.1f} days, {w['feet_drilled'] or 0:,.0f} ft drilled"
                 + (f", {w['ft_per_day']:,.0f} ft/day" if w.get('ft_per_day') else ''))
        c = w.get('cost')
        if c:
            L.append(f"      cost {c['usd']:,.0f} USD" + (f", {c['per_ft']:,.2f} USD/ft, {c['per_day']:,.0f} USD/day" if c['complete'] else f" - incomplete: {c['why_incomplete']}")
                     + (f"; AFE {c['afe_usd']:,.0f} USD" + (f" ({c['vs_afe_pct']:+.1f} %)" if c.get('vs_afe_pct') is not None else '') if c.get('afe_usd') else ''))
        else:
            L.append('      no cost declared')
        for sid, mk in w['market'].items():
            if mk:
                m = next(s for s in e['series'] if s['id'] == sid)
                L.append(f"      {m['name']} while drilling: mean {mk['mean']:,} {m['unit']} ({mk['min']:,} to {mk['max']:,}, {mk['n']} days)")
    L.append('  ' + e['correlation']['note'])
    L.append('  ' + e['attribution'])
    return '\n'.join(L)
