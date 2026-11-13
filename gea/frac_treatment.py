"""Frac treatment: what each stage's own pumping record says - the pressures, what was pumped, the shut-in, the friction,
and the fall-off after it.

A fracturing stage is recorded second by second at the surface: the treating pressure, the slurry rate, the proppant
concentration. From that record alone, with nothing fitted to a model of the fracture, this module reads:

- THE PUMPING - when the stage pumped, the slurry volume (the rate summed over time) and the proppant (the
  concentration times the clean rate, summed) beside the record's own totalisers where it has them; the average and
  the greatest treating pressure and rate;
- THE BREAKDOWN - the first pressure peak the rate did not cause: a peak followed by a fall while the rate holds or
  rises. A stage with no such peak is said to have none;
- THE SHUT-IN - the instantaneous shut-in pressure (ISIP): the pressure the formation holds the moment the pumps
  stop, with the friction gone. The pressure rings for some seconds after the pumps stop (the water hammer), so the
  ISIP is not a sample: it is a straight line fitted to the pressure after the ringing has died down, read back at
  the moment the rate reached zero - fitted over three windows, and the spread between them given as its range.
  With the depth of the perforations and the fluid's gradient declared, the bottomhole ISIP and its gradient;
- THE FRICTION - the treating pressure just before the shut-in, less the ISIP: everything the rate added between
  the pump and the fracture. Where the rate was stepped down before the shut-in (three plateaus or more), each
  plateau's friction is fitted as a part that goes with the square of the rate (the pipe and the perforations
  together - the surface record cannot part them) and a part that goes with its square root (the tortuous path just
  outside the wellbore), after the step-down test of Cramer (SPE-30506) and Wright et al.;
- THE FALL-OFF - with enough record after the shut-in, the pressure at 5, 10 and 15 minutes, and the G-function of
  Nolte (SPE-8341): the pressure against G, and G dP/dG. A closure pick is offered where the G dP/dG curve leaves
  its straight line through the origin (the tangent method, Barree et al., SPE-107877) - a candidate to be looked
  at on the plot, never a measurement by itself;
- A STEP-RATE TEST - where the rate was stepped up through four plateaus or more, the pressure at the end of each
  against its rate, fitted as two straight lines: where they meet is the fracture extension pressure. A test whose
  points lie on one line is said to show no break.

The treatment records are read as the service company wrote them: a spreadsheet or CSV with a header of stacked rows
and a units row, a frac van's data-acquisition export, or a plain CSV with a time, a pressure and a rate.
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

# -- reading a treatment record ------------------------------------------------------------------------------------------
CHANNELS = {
    'pressure': ('primary treating pressure', 'treating pressure', 'wellhead pressure', 'surface treating pressure', 'surface pressure', 'tubing pressure',
                 'casing pressure', 'treatment pressure', 'wh pressure', 'whp', 'stp', 'pressure'),
    'pressure2': ('secondary treating pressure', 'backside pressure', 'annulus pressure', 'annular pressure', 'line pressure'),
    'bottomhole': ('bottomhole pressure', 'bottom hole pressure', 'bhp', 'downhole pressure', 'calculated bottomhole pressure'),
    'rate': ('slurry rate', 'pump rate', 'injection rate', 'treating rate', 'total rate', 'rate'),
    'clean_rate': ('clean rate', 'clean fluid rate', 'calculated clean rate'),
    'conc': ('prop conc', 'proppant concentration', 'proppant conc', 'blender conc', 'inline conc 1', 'sand concentration', 'sand conc', 'prop concentration',
             'slurry proppant conc', 'concentration'),
    'slurry_total': ('slurry stage total', 'stage slurry volume', 'slurry total', 'job slurry total', 'slurry volume', 'total slurry'),
    'clean_total': ('clean stage total', 'stage clean volume', 'clean total', 'job clean total', 'clean volume', 'total clean'),
    'prop_total': ('prop stage total', 'stage prop total', 'proppant total', 'prop total', 'job prop total', 'total proppant'),
}
TIME_NAMES = ('timestamp', 'date time', 'datetime', 'date/time', 'time')
UNIT = {  # to psi, bbl/min, lbm of proppant added per gallon of clean fluid, bbl, lbm
    'psi': 1.0, 'psig': 1.0, 'psia': 1.0, 'kpa': 0.1450377, 'mpa': 145.0377, 'bar': 14.50377,
    'bpm': 1.0, 'bbl/min': 1.0, 'gpm': 1.0 / 42.0, 'gal/min': 1.0 / 42.0, 'm3/min': 6.289811, 'm3/m': 6.289811, 'l/min': 0.006289811, 'lpm': 0.006289811,
    'ppg': 1.0, 'ppa': 1.0, 'lb/gal': 1.0, 'lbm/gal': 1.0, 'kg/m3': 1.0 / 119.8264,
    'bbl': 1.0, 'gal': 1.0 / 42.0, 'm3': 6.289811, 'lbm': 1.0, 'lb': 1.0, 'lbs': 1.0, 'kg': 2.2046226, 'klb': 1000.0,
}
BBL_GAL = 42.0


class TreatmentError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[_\-.:]+', ' ', str(s or '')).strip().lower())


def _split_unit(name: str) -> Tuple[str, str]:
    m = re.search(r'[\(\[]\s*([^\)\]]+?)\s*[\)\]]\s*$', name.strip())
    if m:
        return name[:m.start()].strip(), m.group(1).strip().lower().replace(' ', '')
    return name.strip(), ''


def _num(s: str) -> float:
    try:
        return float(str(s).replace(',', ''))
    except ValueError:
        return float('nan')


def _when(s: str) -> Optional[datetime]:
    s = str(s).strip()
    for f in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d %H:%M:%S.%f', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %I:%M:%S %p',
              '%Y-%m-%d %I:%M:%S %p', '%Y/%m/%d %H:%M:%S', '%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _table(content: bytes, filename: str = '', sheet: Optional[str] = None) -> List[List[str]]:
    """The rows of a spreadsheet's largest sheet, or of a CSV, as text."""
    from . import xlsx_read as X
    if X.is_xlsx(content):
        best: List[List[str]] = []
        for s in ([sheet] if sheet else X.sheets(content)):
            r = X.rows(content, s)
            if len(r) > len(best):
                best = r
        return best
    text = content.decode('utf-8-sig', 'replace')
    first = text[:4000]
    if first.lstrip().startswith('*') or (',' not in first and '\t' not in first):
        return [ln.split() for ln in text.splitlines()]
    delim = '\t' if first.count('\t') > first.count(',') else ','
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _pick(names: List[str], units: List[str]) -> Dict[str, int]:
    """Which column is which: a column whose name is one of a channel's names, in the order they are written; failing
    that, one whose name ends or begins with it and is not another channel's own name."""
    normed = [_norm(n) for n in names]
    every = {a: key for key, al in CHANNELS.items() for a in al}
    out: Dict[str, int] = {}
    for key, aliases in CHANNELS.items():
        for a in aliases:
            hit = [i for i, n in enumerate(normed) if n == a and i not in out.values()]
            if hit:
                out[key] = hit[0]
                break
    for key, aliases in CHANNELS.items():
        if key in out:
            continue
        for a in aliases:
            hit = [i for i, n in enumerate(normed) if (n.endswith(' ' + a) or n.startswith(a + ' ')) and i not in out.values() and every.get(n, key) == key
                   and not any(n.endswith(' ' + b) and len(b) > len(a) for b, k2 in every.items() if k2 != key)]
            if hit:
                out[key] = hit[0]
                break
    return out


def _to(v: np.ndarray, unit: str, what: str, notes: List[str]) -> np.ndarray:
    u = (unit or '').lower().replace(' ', '')
    if not u:
        return v
    if u not in UNIT:
        notes.append(f'the unit "{unit}" of the {what} is not one this page converts: read as written')
        return v
    return v * UNIT[u]


def _read_dataacq(rows: List[List[str]]) -> dict:
    """A frac van's data-acquisition export: lines that begin with * describe it, the channel names follow one a line
    (each ending in the acquisition system's own code), then the samples, the first channel the minutes elapsed."""
    lines = [' '.join(c for c in r if c != '').strip() for r in rows]
    start = None; step_min = None; names: List[str] = []; i = 0; n = len(lines); after = 0
    while i < n:
        ln = lines[i]
        low = ln.lower()
        if low.startswith('*start date and time'):
            start = _when(ln.split(':', 1)[1].strip()) if ':' in ln else None
            if start is None:
                m = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2}:\d{2})\s*([AP]M)?', ln)
                if m:
                    start = _when(' '.join(x for x in m.groups() if x))
        elif low.startswith('*step'):
            j = i + 1
            while j < n and not lines[j]:
                j += 1
            if j < n:
                step_min = _num(lines[j].split()[0])
        elif low.startswith('*names of data channels'):
            j = i + 1
            while j < n and not lines[j]:
                j += 1
            while j < n and lines[j] and not lines[j].startswith('*') and not re.fullmatch(r'[-+0-9.eE\s]+', lines[j]):
                names.append(lines[j]); j += 1
            i = j - 1
            after = j
        i += 1
    if not names:
        raise TreatmentError('a data-acquisition export with no channel names in it')
    clean = []
    for nm in names:
        parts = nm.split()
        if len(parts) > 1 and re.fullmatch(r'[A-Za-z]{2}\d{2}|TIME', parts[-1]):
            parts = parts[:-1]
        if parts and re.fullmatch(r'[A-Z]{1,2}\d{1,2}:', parts[0]):
            parts = parts[1:]
        clean.append(' '.join(parts))
    data = []
    for r in rows[after:]:
        cells = [c for c in r if c != '']
        if len(cells) >= max(3, len(names) - 2) and re.fullmatch(r'[-+0-9.eE]+', cells[0] or 'x'):
            data.append(cells[:len(names)])
    if not data:
        raise TreatmentError('a data-acquisition export with no samples in it')
    width = min(len(d) for d in data)
    a = np.array([[_num(c) for c in d[:width]] for d in data], dtype=np.float64)
    units = [''] * width
    for k, nm in enumerate(clean[:width]):
        base, u = _split_unit(nm)
        clean[k] = base; units[k] = u
    t = a[:, 0] * 60.0 if 'min' in (units[0] or 'min') else a[:, 0]
    return {'layout': 'data-acquisition export', 'names': clean[:width], 'units': units, 'data': a, 't_rel': t, 'start': start, 'step_s': (step_min * 60.0 if step_min else None),
            'notes': ['the export names no units: read as psi, bbl/min and lbm/gal, the units of the van that wrote it']}


def _read_table(rows: List[List[str]]) -> dict:
    """A header of one or several stacked rows (the words of each column's name one under another), perhaps a row of
    units in brackets, then the samples."""
    def numeric(r) -> float:
        cells = [c for c in r if c != '']
        if not cells:
            return 0.0
        return sum(1 for c in cells if not math.isnan(_num(c)) or _when(c) is not None or re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?(\s*[AP]M)?|\d{4}-\d{2}-\d{2}', c)) / len(cells)
    first = None
    for i in range(min(len(rows), 400)):
        if len([c for c in rows[i] if c != '']) >= 2 and numeric(rows[i]) >= 0.8 and all(numeric(r) >= 0.8 for r in rows[i + 1:i + 4]):
            first = i
            break
    if first is None or first == 0:
        raise TreatmentError('no header followed by rows of numbers was found in the file')
    head = []
    j = first - 1
    while j >= 0 and len(head) < 6 and any(c != '' for c in rows[j]):
        head.insert(0, rows[j]); j -= 1
    width = max(len(r) for r in head + rows[first:first + 5])
    head = [list(r) + [''] * (width - len(r)) for r in head]
    units = [''] * width
    last = head[-1]
    filled = [c for c in last if c != '']
    if len(head) > 1 and filled and sum(1 for c in filled if re.fullmatch(r'[\(\[].*[\)\]]', c.strip())) >= 0.6 * len(filled):
        units = [c.strip().strip('()[]').strip().lower().replace(' ', '') for c in last]
        head = head[:-1]
    names = []
    for k in range(width):
        nm = ' '.join(r[k].strip() for r in head if r[k].strip())
        base, u = _split_unit(nm)
        names.append(base)
        if u and not units[k]:
            units[k] = u
    body = [list(r) + [''] * (width - len(r)) for r in rows[first:] if any(c != '' for c in r)]
    normed = [_norm(x) for x in names]
    start = None; t_rel = None
    di = next((k for k, x in enumerate(normed) if x == 'date'), None)
    ti = next((k for k, x in enumerate(normed) if x in ('time of day', 'time', 'clock time') and k != di), None)
    if di is not None and ti is not None:
        ts = [_when(f'{r[di]} {r[ti]}') for r in body]
    else:
        k = next((k for k, x in enumerate(normed) if x in TIME_NAMES), None)
        ts = [_when(r[k]) for r in body] if k is not None else [None]
        if k is not None and ts and ts[0] is None:
            v = np.array([_num(r[k]) for r in body])
            u = units[k]
            t_rel = v * (60.0 if u.startswith('min') else 3600.0 if u in ('h', 'hr', 'hours') else 1.0)
            ts = []
        elif k is None:
            k = next((k for k, x in enumerate(normed) if x.startswith('elapsed') or x.startswith('job time')), None)
            if k is None:
                raise TreatmentError('no time column was found (a Date and a Time of Day, a timestamp, or an elapsed time)')
            v = np.array([_num(r[k]) for r in body])
            t_rel = v * (60.0 if units[k].startswith('min') else 3600.0 if units[k] in ('h', 'hr', 'hours') else 1.0)
            ts = []
    if ts:
        keep = [i for i, x in enumerate(ts) if x is not None]
        if len(keep) < 10:
            raise TreatmentError('the time column could not be read as dates and times')
        body = [body[i] for i in keep]
        start = ts[keep[0]]
        t_rel = np.array([(ts[i] - start).total_seconds() for i in keep])
    a = np.array([[_num(c) for c in r] for r in body], dtype=np.float64)
    return {'layout': 'table with a stacked header' if len(head) > 1 else 'table', 'names': names, 'units': units, 'data': a, 't_rel': t_rel, 'start': start, 'step_s': None}


def read_treatment(content: bytes, filename: str = '', start: Optional[str] = None) -> dict:
    """One stage's pumping record: seconds from its first sample, the treating pressure (psi), the slurry rate
    (bbl/min), the proppant concentration (lbm added per gallon) and what else the file has, with what was read as
    what. `start` is the record's first moment, for a file that only has elapsed time."""
    rows = _table(content, filename)
    if not rows:
        raise TreatmentError('the file is empty')
    head = ' '.join(' '.join(r) for r in rows[:3]).lower()
    raw = _read_dataacq(rows) if head.lstrip().startswith('*') else _read_table(rows)
    notes: List[str] = list(raw.get('notes') or [])
    cols = _pick(raw['names'], raw['units'])
    if 'pressure' not in cols or 'rate' not in cols:
        raise TreatmentError('the file needs a treating pressure and a slurry rate; its columns are ' + ', '.join(n for n in raw['names'] if n)[:400])
    t = np.asarray(raw['t_rel'], dtype=np.float64)
    ok = ~np.isnan(t)
    order = np.argsort(t[ok], kind='stable')
    t = t[ok][order]
    out: Dict[str, Optional[np.ndarray]] = {}
    what = {'pressure': 'treating pressure', 'pressure2': 'second pressure', 'bottomhole': 'bottomhole pressure', 'rate': 'slurry rate', 'clean_rate': 'clean rate',
            'conc': 'proppant concentration', 'slurry_total': 'slurry total', 'clean_total': 'clean total', 'prop_total': 'proppant total'}
    read_as = {}
    for key in CHANNELS:
        if key not in cols:
            out[key] = None
            continue
        k = cols[key]
        v = _to(raw['data'][ok][order, k], raw['units'][k], what[key], notes)
        if key not in ('pressure', 'rate') and (np.isnan(v).all() or float(np.nanmax(np.abs(v))) == 0.0):
            out[key] = None
            continue
        out[key] = v
        read_as[key] = raw['names'][k] + (f" ({raw['units'][k]})" if raw['units'][k] else '')
    st = raw['start'] or (_when(start) if start else None)
    if st is None:
        notes.append('the file has elapsed time only and no start was given: the times are from its first sample')
    dt = np.diff(t)
    cad = float(np.median(dt)) if len(dt) else 1.0
    return {'t': t - (t[0] if len(t) else 0.0), 'start': st.strftime('%Y-%m-%dT%H:%M:%S') if st else None, 'cadence_s': cad, 'samples': int(len(t)), 'layout': raw['layout'],
            'read_as': read_as, 'notes': notes, **out}


# -- the arithmetic ------------------------------------------------------------------------------------------------------
RULE = {'on_bpm': 0.3, 'on_share': 0.02, 'merge_s': 30.0, 'brief_s': 60.0, 'surface_share': 0.05, 'quiet_s': 60.0, 'windows': ((15.0, 60.0), (20.0, 90.0), (30.0, 120.0)),
        'wide_share': 0.05, 'plateau_s': 8.0, 'plateau_share': 0.03, 'plateau_bpm': 0.25, 'treating_s': 60.0, 'stepdown_points': 3, 'falloff_s': 300.0,
        'steprate_points': 4, 'steprate_s': 30.0, 'conc_max': 12.0, 'breakdown_psi': 100.0, 'breakdown_share': 0.03, 'line_tol': 0.07, 'line_share': 0.2}
NOT_A_MEASUREMENT = [
    'the ISIP as one number where the pressure was still falling fast when the ringing stopped: it is a line read back to the shut-in, and its range is what the windows disagree by',
    'a closure pressure: the G-function pick is where one curve leaves one straight line, to be looked at on the plot and set beside a second method before anyone uses it',
    'the perforation friction alone: the part that goes with the square of the rate is the pipe and the perforations together, and a surface record cannot part them',
    'the bottomhole pressure: the surface pressure plus a declared column of fluid, with no friction left in it only after the pumps have stopped',
    'a breakdown as the rock breaking: a pressure peak followed by a fall at a held rate is also what friction reducer reaching the perforations, or a ball seating, looks like',
    'a step-rate break in a job\'s own ramp-up as the extension pressure: there it is as likely the perforations opening',
    'the fracture: its height, length, width and where the proppant went are not in a pressure and a rate',
]


def _dt(t: np.ndarray) -> np.ndarray:
    n = len(t)
    if n < 2:
        return np.ones(n)
    d = np.diff(t, append=t[-1] + (t[-1] - t[-2]))
    cad = float(np.median(d))
    return np.where((d <= 0) | (d > max(10.0, 10 * cad)), cad, d)


def _med(v: np.ndarray, k: int = 5) -> np.ndarray:
    """A running median over k samples: a sensor's one- and two-second spikes are not the rate."""
    n = len(v)
    if n < k:
        return v.copy()
    h = k // 2
    pad = np.concatenate([np.full(h, v[0]), v, np.full(h, v[-1])])
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)


def segments(t: np.ndarray, p: np.ndarray, q: np.ndarray) -> List[dict]:
    """When the stage pumped. A stretch of rate with gaps under half a minute closed; a stretch under a minute is
    BRIEF; one pumped at under a twentieth of the record's greatest pressure is SURFACE work (priming, a line test) -
    neither is injection."""
    qs = _med(np.nan_to_num(q))
    qmax = float(qs.max()) if len(qs) else 0.0
    on = qs > max(RULE['on_bpm'], RULE['on_share'] * qmax)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(np.int8), [0]])))
    raw = [(int(edges[i]), int(edges[i + 1]) - 1) for i in range(0, len(edges), 2)]
    merged: List[List[int]] = []
    for a, b in raw:
        if merged and t[a] - t[merged[-1][1]] < RULE['merge_s']:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    dt = _dt(t)
    pmax = float(np.nanmax(p)) if len(p) and not np.isnan(p).all() else 0.0
    out = []
    for a, b in merged:
        dur = float(t[b] - t[a] + dt[b])
        vol = float(np.sum(np.nan_to_num(q[a:b + 1]) * dt[a:b + 1]) / 60.0)
        pm = float(np.nanmedian(p[a:b + 1])) if not np.isnan(p[a:b + 1]).all() else float('nan')
        kind = 'BRIEF' if dur < RULE['brief_s'] else 'SURFACE' if not pm > RULE['surface_share'] * pmax else 'INJECTION'
        out.append({'a': a, 'b': b, 'start_s': float(t[a]), 'end_s': float(t[b] + dt[b]), 'minutes': round(dur / 60.0, 2), 'volume_bbl': round(vol, 1),
                    'rate_bpm': round(float(np.median(qs[a:b + 1])), 2), 'pressure_psi': None if math.isnan(pm) else round(pm), 'kind': kind})
    return out


def plateaus(t: np.ndarray, q: np.ndarray, a: int, b: int, min_s: float) -> List[Tuple[int, int, float]]:
    """Stretches of [a, b] over which the rate held: within 3 % (or a quarter of a barrel a minute) for min_s or more."""
    out = []
    i = a
    while i <= b:
        lo = hi = q[i]
        j = i
        while j + 1 <= b:
            v = q[j + 1]
            nlo, nhi = min(lo, v), max(hi, v)
            if nhi - nlo > max(RULE['plateau_bpm'], RULE['plateau_share'] * abs(0.5 * (nlo + nhi))) * 2:
                break
            lo, hi = nlo, nhi
            j += 1
        if t[j] - t[i] >= min_s:
            out.append((i, j, float(np.median(q[i:j + 1]))))
            i = j + 1
        else:
            i += 1
    return out


def _line(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    xm, ym = float(x.mean()), float(y.mean())
    sxx = float(((x - xm) ** 2).sum())
    m = float(((x - xm) * (y - ym)).sum()) / sxx if sxx > 0 else 0.0
    return m, ym - m * xm


def shut_in(t: np.ndarray, p: np.ndarray, q: np.ndarray, seg: dict, quiet_until: float) -> dict:
    """The shut-in that ends a stretch of injection: the ISIP, the friction, the ringing, a step-down if there was one."""
    qs = _med(np.nan_to_num(q))
    b = seg['b']
    t0 = seg['end_s']
    quiet = quiet_until - t0
    out: Dict[str, object] = {'at_s': round(t0, 1), 'quiet_s': round(quiet, 1)}
    if quiet < RULE['quiet_s']:
        out.update(status='TOO SHORT', why=f"the pumps came back on {quiet:.0f} s after they stopped: under a minute of pressure to read")
        return out
    est = []
    for w0, w1 in RULE['windows']:
        if w1 > quiet:
            continue
        m = (t >= t0 + w0) & (t <= t0 + w1) & ~np.isnan(p)
        if int(m.sum()) < 10:
            continue
        sl, ic = _line(t[m] - t0, p[m])
        est.append({'from_s': w0, 'to_s': w1, 'isip_psi': round(ic, 1), 'slope_psi_s': round(sl, 3)})
    if not est:
        out.update(status='TOO SHORT', why='too few samples in the minute after the pumps stopped')
        return out
    vals = sorted(e['isip_psi'] for e in est)
    isip = float(np.median(vals))
    out.update(status='OK', isip_psi=round(isip), isip_low_psi=round(vals[0]), isip_high_psi=round(vals[-1]), windows=est)
    out['wide'] = bool(vals[-1] - vals[0] > RULE['wide_share'] * isip)
    # the rates the stage held on its way down to the shut-in: the last plateau, and each higher one before it
    a0 = max(seg['a'], int(np.searchsorted(t, t0 - 1800.0)))
    steps_: List[Tuple[int, int, float]] = []
    for x in plateaus(t, qs, a0, b, RULE['plateau_s']):
        if x[2] <= 2 * RULE['on_bpm']:
            continue
        if steps_ and t[x[0]] - t[steps_[-1][1]] <= 5.0 and abs(x[2] - steps_[-1][2]) <= 0.05 * steps_[-1][2]:   # one step, drifting
            i_, _, _ = steps_[-1]
            steps_[-1] = (i_, x[1], float(np.median(qs[i_:x[1] + 1])))
        else:
            steps_.append(x)
    pl = steps_
    if pl:
        seq = [pl[-1]]
        for x in reversed(pl[:-1]):
            if x[2] >= seq[0][2] * 1.05 and t[seq[0][0]] - t[x[1]] < 300.0:
                seq.insert(0, x)
            elif t[x[1]] - t[x[0]] >= RULE['treating_s'] and x[2] < seq[0][2] * 0.95:   # the rate had been lower for a minute: the staircase starts after it
                break
        while len(seq) > 1 and t[seq[0][1]] - t[seq[0][0]] < RULE['treating_s']:   # the top of the staircase is a rate the stage was treated at, held a minute or more
            seq.pop(0)

        def at(x, whole=False):
            i, j, qv = x
            k = max(i, int(np.searchsorted(t, t[j] - 10.0))) if whole else (i + j + 1) // 2
            return qv, float(np.nanmedian(p[k:j + 1])), float(t0 - t[j])
        top = at(seq[0], True)
        if t0 - t[seq[0][1]] <= 1800.0:
            out['last_rate_bpm'] = round(top[0], 2)
            out['last_pressure_psi'] = round(top[1])
            out['last_before_s'] = round(top[2])
            out['friction_psi'] = round(top[1] - isip)
            steps = [top] + [at(x) for x in seq[1:]]
            out['step_down'] = step_down(steps, isip) if len(steps) >= RULE['stepdown_points'] else None
    # the ringing after the pumps stop
    last = est[-1]
    m = (t >= t0 + 1.0) & (t <= t0 + min(45.0, quiet)) & ~np.isnan(p)
    if int(m.sum()) >= 12:
        x = t[m] - t0
        res = p[m] - np.polyval(np.polyfit(x, p[m], 2), x)
        pp = float(res.max() - res.min())
        if pp >= 30.0:
            sg = np.sign(res)
            cross = x[1:][(sg[1:] != sg[:-1]) & (sg[1:] != 0)]
            per = float(2.0 * np.median(np.diff(cross))) if len(cross) >= 3 else None
            out['ringing'] = {'peak_to_peak_psi': round(pp), 'period_s': None if per is None else round(per, 1)}
    return out


def step_down(steps: List[Tuple[float, float, float]], isip: float) -> dict:
    """Each plateau's friction (its pressure less the ISIP) as a q-squared part and a root-q part, neither negative."""
    qv = np.array([s[0] for s in steps]); f = np.array([s[1] for s in steps]) - isip
    A = np.column_stack([qv ** 2, np.sqrt(qv)])
    coef, *_ = np.linalg.lstsq(A, f, rcond=None)
    a, b_ = float(coef[0]), float(coef[1])
    if a < 0 or b_ < 0:
        if a < 0:
            a = 0.0; b_ = max(0.0, float((np.sqrt(qv) * f).sum() / qv.sum()))
        else:
            b_ = 0.0; a = max(0.0, float((qv ** 2 * f).sum() / (qv ** 4).sum()))
    fit = a * qv ** 2 + b_ * np.sqrt(qv)
    sq, rt = a * qv[0] ** 2, b_ * math.sqrt(qv[0])
    tot = sq + rt
    return {'points': [{'rate_bpm': round(float(x), 2), 'pressure_psi': round(float(s[1])), 'friction_psi': round(float(y)), 'fitted_psi': round(float(z))} for x, s, y, z in zip(qv, steps, f, fit)],
            'square_psi': round(sq), 'root_psi': round(rt), 'at_rate_bpm': round(float(qv[0]), 2), 'square_coef': a, 'root_coef': b_,
            'misfit_psi': round(float(np.sqrt(np.mean((fit - f) ** 2))), 1),
            'reads': ('no friction above the ISIP to part' if tot <= 0 else
                      'mostly the part that goes with the square of the rate: the pipe and the perforations' if sq >= 0.67 * tot else
                      'mostly the part that goes with the root of the rate: the path just outside the wellbore' if rt >= 0.67 * tot else
                      'both parts: the pipe and perforations, and the path just outside the wellbore')}


def g_function(dt_s: np.ndarray, tp_s: float) -> np.ndarray:
    """Nolte's G for low fluid efficiency (alpha = 1): G = 4/pi (g(x) - g(0)), g(x) = 4/3 ((1 + x)^1.5 - x^1.5), x = dt / tp."""
    x = np.asarray(dt_s, dtype=np.float64) / tp_s
    return 4.0 / math.pi * (4.0 / 3.0 * ((1.0 + x) ** 1.5 - x ** 1.5) - 4.0 / 3.0)


def falloff(t: np.ndarray, p: np.ndarray, t0: float, t_end: float, tp_s: float) -> dict:
    """The pressure after the last shut-in: at 5, 10 and 15 minutes; against G; and where G dP/dG leaves its line."""
    m0 = (t >= t0 + 60.0) & (t <= t_end) & ~np.isnan(p)
    bled = None
    if int(m0.sum()) > 12:
        ts, ps = t[m0], _med(p[m0], 3)
        drop = ps[:-5] - ps[5:]
        k = np.flatnonzero(drop >= np.maximum(100.0, 0.03 * ps[:-5]))
        if len(k):                                                                   # the well opened, or the gauge isolated: the fall-off is over
            bled = float(ts[k[0]])
            t_end = bled - 5.0
    dur = t_end - t0
    out: Dict[str, object] = {'minutes': round(dur / 60.0, 1), 'pumping_min': round(tp_s / 60.0, 1), 'at': {}}
    if bled is not None:
        out['bled_off_min'] = round((bled - t0) / 60.0, 1)
    for mn in (5, 10, 15, 30, 60):
        m = (t >= t0 + 60.0 * mn - 2.5) & (t <= t0 + 60.0 * mn + 2.5) & ~np.isnan(p)
        if m.any() and 60.0 * mn + 2.5 <= dur:
            out['at'][str(mn)] = round(float(np.median(p[m])))
    if dur < RULE['falloff_s'] or tp_s <= 0:
        out.update(status='TOO SHORT', why=f"{dur / 60:.1f} min of pressure after the shut-in: five are needed for a G-function")
        return out
    m = (t >= t0 + 30.0) & (t <= t_end) & ~np.isnan(p)
    tt, pp = t[m] - t0, _med(p[m], 5)
    G = g_function(tt, tp_s)
    n = 120
    grid = np.linspace(G[0], G[-1], n)
    pg = np.interp(grid, G, pp)
    h = 3
    dp = np.full(n, np.nan)
    for i in range(n):
        lo, hi = max(0, i - h), min(n, i + h + 1)
        dp[i] = _line(grid[lo:hi], pg[lo:hi])[0]
    y = -grid * dp                                                                   # G dP/dG, positive while the pressure falls
    ratio = np.where(grid > 0, y / np.maximum(grid, 1e-9), np.nan)
    need = max(8, int(RULE['line_share'] * n))
    best = None
    i = 0
    while i < n - need:
        j = i + need
        seg_ = ratio[i:j]
        med = float(np.median(seg_))
        if med <= 0 or np.abs(seg_ / med - 1.0).max() > RULE['line_tol']:
            i += 1
            continue
        while j < n and abs(ratio[j] / med - 1.0) <= RULE['line_tol']:
            j += 1
            med = float(np.median(ratio[i:j]))
        best = (i, j, med)                                                           # the latest such run is kept
        i = j
    out.update(status='OK', g=[round(float(x), 4) for x in grid], pressure=[round(float(x), 1) for x in pg], gdpdg=[round(float(x), 1) for x in y],
               minutes_at_g=[round(float(np.interp(x, G, tt)) / 60.0, 2) for x in grid])
    if best is None:
        out['pick'] = None
        out['reads'] = 'G dP/dG has no straight stretch through the origin in this record - the pressure is not a straight line in G anywhere in it: no closure is offered'
        return out
    i, j, med = best
    out['line'] = {'slope_psi': round(med, 1), 'from_g': round(float(grid[i]), 3), 'to_g': round(float(grid[min(j, n) - 1]), 3)}
    # the pressure itself is a straight line in G over that stretch; a closure is where it stays above that line (the decline slowing)
    k1 = max(i + 2, min(j, n) - h)
    sl, ic = _line(grid[i:k1], pg[i:k1])
    resid = pg - (ic + sl * grid)
    thr = max(5.0, 4.0 * float(np.std(resid[i:k1])))
    kd = None
    for k in range(k1, n - 4):
        if resid[k] > thr and float((resid[k:] > thr).mean()) >= 0.8:
            kd = k
            break
    if kd is None:
        out['pick'] = None
        if float((resid[k1:] < -thr).mean()) > 0.5 if n > k1 else False:
            out['reads'] = 'the pressure falls away below its straight line in G - faster, not slower: that is not a closure, and none is offered'
        else:
            out['reads'] = (f"the pressure was still on its straight line in G when the record ended, {dur / 60:.0f} min after the shut-in: no closure within the record - "
                            f"if it closes, it is below {pg[-1]:,.0f} psi")
        return out
    gc = float(grid[kd - 1])
    if grid[-1] < 1.5 * gc:
        out['pick'] = None
        out['reads'] = (f"the pressure leaves its straight line in G at G = {gc:.2f}, {float(np.interp(gc, G, tt)) / 60.0:.1f} min after the shut-in, but the record ends too soon after it "
                        f"(G = {grid[-1]:.2f}) to tell a closure from a pause in the decline: none is offered")
        return out
    out['pick'] = {'g': round(gc, 3), 'minutes': round(float(np.interp(gc, G, tt)) / 60.0, 1), 'pressure_psi': round(float(ic + sl * gc)), 'efficiency': round(gc / (2.0 + gc), 3)}
    out['reads'] = (f"the pressure leaves its straight line in G at G = {gc:.2f}, {out['pick']['minutes']:g} min after the shut-in, at {out['pick']['pressure_psi']:,} psi, and G dP/dG leaves its "
                    f"line through the origin there - a closure candidate (tangent method), to be looked at on the plot")
    return out


def step_rate(t: np.ndarray, p: np.ndarray, q: np.ndarray) -> List[dict]:
    """Runs of four or more plateaus, each a higher rate than the last: the pressure at the end of each against its
    rate, as two straight lines - where they meet, the fracture extension pressure."""
    qs = _med(np.nan_to_num(q))
    pl = [x for x in plateaus(t, qs, 0, len(t) - 1, RULE['steprate_s']) if x[2] > RULE['on_bpm']]
    runs: List[List[Tuple[int, int, float]]] = []
    for x in pl:
        if runs and x[2] > runs[-1][-1][2] * 1.08 and t[x[0]] - t[runs[-1][-1][1]] < 120.0:
            runs[-1].append(x)
        else:
            runs.append([x])
    out = []
    for run in runs:
        if len(run) < RULE['steprate_points']:
            continue
        qv = np.array([x[2] for x in run])
        pv = np.array([float(np.nanmedian(p[max(x[0], x[1] - 4):x[1] + 1])) for x in run])
        n = len(qv)
        if np.isnan(pv).any() or (np.diff(pv) <= 0).any():                              # a test is rate put up and pressure following: anything else is a job being pumped
            continue
        m1, c1 = _line(qv, pv)
        sse1 = float(((m1 * qv + c1 - pv) ** 2).sum())
        best = None
        for k in range(1, n - 2):                                                    # the break between point k and k + 1; two points or more on each side
            ma, ca = _line(qv[:k + 1], pv[:k + 1]); mb, cb = _line(qv[k + 1:], pv[k + 1:])
            sse = float(((ma * qv[:k + 1] + ca - pv[:k + 1]) ** 2).sum() + ((mb * qv[k + 1:] + cb - pv[k + 1:]) ** 2).sum())
            if best is None or sse < best[0]:
                best = (sse, k, ma, ca, mb, cb)
        r = {'start_s': float(t[run[0][0]]), 'end_s': float(t[run[-1][1]]), 'points': [{'rate_bpm': round(float(a), 2), 'pressure_psi': round(float(b))} for a, b in zip(qv, pv)]}
        if best is not None and best[2] > 0 and best[4] < 0.6 * best[2] and best[0] < 0.5 * sse1 + 1e-9 and abs(best[2] - best[4]) > 1e-9:
            _, k, ma, ca, mb, cb = best
            qx = (cb - ca) / (ma - mb)
            if qv[0] <= qx <= qv[-1]:
                r.update(status='BREAK', extension_psi=round(ma * qx + ca), at_rate_bpm=round(qx, 2), slopes=[round(ma, 1), round(mb, 1)],
                         reads=(f"the pressure rises {ma:,.0f} psi per bbl/min up to {qx:.1f} bbl/min and {mb:,.0f} beyond it, bending at {ma * qx + ca:,.0f} psi (surface) - "
                                f"in a test begun below the fracturing pressure that is the extension pressure; in a job's own ramp-up it is as likely the perforations opening"))
                out.append(r)
                continue
        r.update(status='NO BREAK', reads=f"the points lie on one line ({m1:,.0f} psi per bbl/min): either every step was already above the extension pressure, or none reached it")
        out.append(r)
    return out


def breakdown(t: np.ndarray, p: np.ndarray, q: np.ndarray, seg: dict) -> Optional[dict]:
    """The first pressure peak the rate did not cause: a peak in the opening of the injection after which the pressure
    fell by 100 psi and 3 % while the rate held or rose."""
    qs = _med(np.nan_to_num(q))
    a, b = seg['a'], seg['b']
    target = 0.8 * seg['rate_bpm']
    reach = next((i for i in range(a, b + 1) if qs[i] >= target), b)
    end = min(b, int(np.searchsorted(t, min(t[reach] + 120.0, t[a] + 900.0))))
    if end - a < 10:
        return None
    ps = _med(np.nan_to_num(p[a:end + 1]), 3)
    best = None
    for k in range(1, len(ps) - 5):
        if ps[k] < ps[k - 1] or ps[k] < np.max(ps[:k + 1]):
            continue
        hi = min(len(ps), k + 121)
        j = k + int(np.argmin(ps[k:hi]))
        fall = float(ps[k] - ps[j])
        if j > k and fall >= max(RULE['breakdown_psi'], RULE['breakdown_share'] * ps[k]) and qs[a + j] >= 0.98 * qs[a + k]:
            if best is None or ps[k] > best['pressure_psi']:
                best = {'pressure_psi': round(float(ps[k])), 'at_s': round(float(t[a + k]), 1), 'rate_bpm': round(float(qs[a + k]), 2), 'fell_psi': round(fall), 'in_s': round(float(t[a + j] - t[a + k]), 1)}
    return best


def analyse(rec: dict, declared: Optional[dict] = None) -> dict:
    """Everything this page reads from one stage's record."""
    t, p, q = rec['t'], np.asarray(rec['pressure'], dtype=np.float64), np.nan_to_num(np.asarray(rec['rate'], dtype=np.float64))
    dt = _dt(t)
    segs = segments(t, p, q)
    inj = [s for s in segs if s['kind'] == 'INJECTION']
    out: Dict[str, object] = {'samples': int(len(t)), 'cadence_s': rec.get('cadence_s'), 'start': rec.get('start'), 'minutes': round(float(t[-1] - t[0]) / 60.0, 1) if len(t) else 0.0,
                              'segments': [{k: v for k, v in s.items() if k not in ('a', 'b')} for s in segs], 'notes': list(rec.get('notes') or []),
                              'not_injection': {'brief': sum(1 for s in segs if s['kind'] == 'BRIEF'), 'surface': sum(1 for s in segs if s['kind'] == 'SURFACE')}}
    if not inj:
        out.update(status='NO INJECTION', why='the record has no stretch of a minute or more pumped against pressure')
        return out
    on = np.zeros(len(t), bool)
    for s in inj:
        on[s['a']:s['b'] + 1] = True
    pump_s = float(dt[on].sum())
    vol = float((q[on] * dt[on]).sum() / 60.0)
    main = max(inj, key=lambda s: s['volume_bbl'])
    full = on & (q >= 0.5 * main['rate_bpm'])
    w = dt[full]
    pumping = {'injection_min': round(pump_s / 60.0, 1), 'slurry_bbl': round(vol), 'stretches': len(inj),
               'pressure_avg_psi': round(float((np.nan_to_num(p[full]) * w).sum() / w.sum())) if w.sum() > 0 else None,
               'pressure_max_psi': round(float(np.nanmax(p[on]))), 'rate_avg_bpm': round(float((q[full] * w).sum() / w.sum()), 1) if w.sum() > 0 else None,
               'rate_max_bpm': round(float(_med(q)[on].max()), 1)}
    tot = rec.get('slurry_total')
    if tot is not None:
        d = np.diff(np.nan_to_num(np.asarray(tot, dtype=np.float64)))
        pumping['slurry_totaliser_bbl'] = round(float(d[(d > 0) & (d < 50.0)].sum()))
    cr = rec.get('clean_rate')
    if cr is not None:
        cr = np.nan_to_num(np.asarray(cr, dtype=np.float64))
        pumping['clean_bbl'] = round(float((cr[on] * dt[on]).sum() / 60.0))
    conc = rec.get('conc')
    if conc is not None:
        c = np.asarray(conc, dtype=np.float64)
        bad = np.isnan(c) | (c < 0) | (c > RULE['conc_max'])
        cc = np.where(bad, 0.0, c)
        base = cr if cr is not None else q
        mass = float((cc[on] * base[on] * BBL_GAL * dt[on]).sum() / 60.0)
        pumping['proppant_lbm'] = round(mass, -2) if mass >= 100.0 else None
        pumping['conc_max_ppg'] = round(float(np.percentile(cc[on], 99.5)), 2) if on.any() and mass >= 100.0 else None
        if int((bad & on & ~np.isnan(c)).sum()):
            out['notes'].append(f"{int((bad & on & ~np.isnan(c)).sum())} s of proppant concentration above {RULE['conc_max']:g} lbm/gal or below zero left out: a densitometer does that when it is being flushed")
        if cr is None:
            out['notes'].append('no clean rate in the record: the proppant is the concentration times the slurry rate, a little high')
    pt = rec.get('prop_total')
    if pt is not None:
        d = np.diff(np.nan_to_num(np.asarray(pt, dtype=np.float64)))
        pumping['proppant_totaliser_lbm'] = round(float(d[d > 0].sum()), -2) if float(d[d > 0].sum()) >= 100.0 else None
    out['pumping'] = pumping
    out['breakdown'] = breakdown(t, p, q, main)
    shut = []
    for i, s in enumerate(inj):
        nxt = next((x['start_s'] for x in segs if x['start_s'] > s['end_s'] - 1e-6 and x['kind'] != 'BRIEF'), float(t[-1]))
        r = shut_in(t, p, q, s, nxt)
        r['after_bbl'] = s['volume_bbl']
        shut.append(r)
    out['shut_ins'] = shut
    good = [r for r in shut if r.get('status') == 'OK']
    last = shut[-1]
    big = [i for i, s in enumerate(inj) if s['volume_bbl'] >= 0.1 * vol]              # a shut-in after a tenth of the stage or more: not a line test or a ball being seated
    fi = big[-1] if big else len(inj) - 1
    final = shut[fi] if shut[fi].get('status') == 'OK' else None
    out['final'] = final
    out['final_index'] = fi if final is not None else None
    dec = dict(declared or {})
    if final is not None and dec.get('tvd_ft') and dec.get('fluid_psi_ft') is not None:
        hyd = float(dec['fluid_psi_ft']) * float(dec['tvd_ft'])
        bh = final['isip_psi'] + hyd
        out['bottomhole'] = {'tvd_ft': float(dec['tvd_ft']), 'fluid_psi_ft': float(dec['fluid_psi_ft']), 'hydrostatic_psi': round(hyd), 'isip_psi': round(bh),
                             'gradient_psi_ft': round(bh / float(dec['tvd_ft']), 3), 'low_psi_ft': round((final['isip_low_psi'] + hyd) / float(dec['tvd_ft']), 3),
                             'high_psi_ft': round((final['isip_high_psi'] + hyd) / float(dec['tvd_ft']), 3), 'source': dec.get('source') or ''}
    if final is not None:
        t0 = inj[fi]['end_s']
        end = next((x['start_s'] for x in segs if x['start_s'] > t0 and x['kind'] != 'BRIEF'), float(t[-1]))
        before = np.zeros(len(t), bool)
        for s in inj[:fi + 1]:
            before[s['a']:s['b'] + 1] = True
        out['falloff'] = falloff(t, p, t0, end, float(dt[before].sum()))
    out['step_rate'] = step_rate(t, p, q)
    out['status'] = 'OK' if good else 'NO SHUT-IN READ'
    if not good:
        out['why'] = last.get('why')
    return out


# -- the workspace: a stage's record kept beside the program ----------------------------------------------------------------
KEYS = ('pressure', 'rate', 'conc', 'clean_rate', 'slurry_total', 'clean_total', 'prop_total', 'pressure2', 'bottomhole')
WATER_PSI_FT = 0.433
_MEMO: Dict[tuple, dict] = {}


def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'completion', 'frac', 'treatment', *p)


def _safe(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(s).strip()) or 'x'


def _index(ws) -> Dict[str, Dict[str, dict]]:
    try:
        with open(_dir(ws, 'treatments.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _store(ws, idx: dict) -> None:
    os.makedirs(_dir(ws), exist_ok=True)
    p = _dir(ws, 'treatments.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(idx, f, indent=1)
    os.replace(p + '.tmp', p)


def stage_key(s: str):
    """Stages in the order a crew counts them: 3R before 4, 6A before 6B, 9 before 10."""
    m = re.match(r'\s*(\d+)\s*(.*)$', str(s))
    return (0, int(m.group(1)), m.group(2).strip().upper()) if m else (1, 0, str(s).upper())


def attach(ws, well_id: str, stage: str, filename: str, content: bytes, actor: str, source: str, start: Optional[str] = None) -> dict:
    """Keep one stage's pumping record verbatim, and beside it the channels this page reads from it."""
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    stage = str(stage or '').strip()
    if not stage:
        raise TreatmentError('name the stage (e.g. 1, 3R, 6A)')
    if not (source or '').strip():
        raise TreatmentError('say where the record came from (e.g. "frac service company\'s post-job data")')
    rec = read_treatment(content, filename, start)
    d = _dir(ws, _safe(well_id), _safe(stage))
    os.makedirs(d, exist_ok=True)
    fn = _safe(os.path.basename(filename)) or 'treatment.csv'
    with open(os.path.join(d, fn), 'wb') as f:
        f.write(content)
    tmp = os.path.join(d, 'channels.tmp.npz')
    np.savez_compressed(tmp, t=rec['t'], **{k: np.asarray(rec[k], dtype=np.float32) for k in KEYS if rec.get(k) is not None})
    os.replace(tmp, os.path.join(d, 'channels.npz'))
    idx = _index(ws)
    old = (idx.get(well_id) or {}).get(stage) or {}
    idx.setdefault(well_id, {})[stage] = {
        'file': fn, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'source': source.strip(), 'layout': rec['layout'], 'start': rec['start'],
        'samples': rec['samples'], 'cadence_s': rec['cadence_s'], 'read_as': rec['read_as'], 'notes': rec['notes'], 'declared': old.get('declared'), 'by': actor,
        'utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    _store(ws, idx)
    ws.audit(actor, 'frac_treatment.attach', {'well': well_id, 'stage': stage, 'file': fn, 'sha256': idx[well_id][stage]['sha256'], 'samples': rec['samples'], 'source': source.strip()})
    return {**idx[well_id][stage], 'well_id': well_id, 'stage': stage}


def remove(ws, well_id: str, stage: str, actor: str) -> dict:
    idx = _index(ws)
    rec = (idx.get(well_id) or {}).pop(str(stage), None)
    if rec is None:
        raise KeyError(f'{well_id} has no treatment record for stage {stage}')
    if not idx.get(well_id):
        idx.pop(well_id, None)
    _store(ws, idx)
    ws.audit(actor, 'frac_treatment.removed', {'well': well_id, 'stage': stage, 'file': rec['file'], 'sha256': rec['sha256']})
    return rec


def declare(ws, well_id: str, actor: str, source: str, tvd_ft, fluid_psi_ft=None, stage: Optional[str] = None) -> dict:
    """The depth of the perforations and the gradient of the fluid in the well at the shut-in - what turns a surface
    ISIP into a bottomhole one. For one stage, or for every stage of the well that has none of its own."""
    idx = _index(ws)
    if well_id not in idx:
        raise KeyError(f'{well_id} has no treatment record')
    if not (source or '').strip():
        raise TreatmentError('say where the depth and the gradient came from (e.g. "perforation record; fresh water")')
    if tvd_ft in (None, ''):
        raise TreatmentError('give the true vertical depth of the perforations, in feet')
    tvd = float(tvd_ft)
    grad = WATER_PSI_FT if fluid_psi_ft in (None, '') else float(fluid_psi_ft)
    if not (math.isfinite(tvd) and 100.0 <= tvd <= 40000.0):
        raise TreatmentError('the true vertical depth of the perforations is in feet, between 100 and 40,000')
    if not (math.isfinite(grad) and 0.3 <= grad <= 1.2):
        raise TreatmentError('the fluid gradient is in psi/ft, between 0.3 and 1.2 (fresh water is 0.433)')
    dec = {'tvd_ft': tvd, 'fluid_psi_ft': grad, 'source': source.strip(), 'by': actor, 'utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    stages_ = [str(stage)] if stage not in (None, '') else list(idx[well_id])
    for s in stages_:
        if s not in idx[well_id]:
            raise KeyError(f'{well_id} has no treatment record for stage {s}')
        idx[well_id][s]['declared'] = dec
    _store(ws, idx)
    ws.audit(actor, 'frac_treatment.declared', {'well': well_id, 'stages': stages_, 'tvd_ft': tvd, 'fluid_psi_ft': grad, 'source': source.strip()})
    return {'well_id': well_id, 'stages': stages_, **dec}


def load(ws, well_id: str, stage: str) -> dict:
    meta = (_index(ws).get(well_id) or {}).get(str(stage))
    if meta is None:
        raise KeyError(f'{well_id} has no treatment record for stage {stage}')
    with np.load(_dir(ws, _safe(well_id), _safe(stage), 'channels.npz')) as z:
        rec = {k: np.array(z[k], dtype=np.float64) for k in z.files}
    for k in KEYS:
        rec.setdefault(k, None)
    rec.update(start=meta.get('start'), cadence_s=meta.get('cadence_s'), notes=list(meta.get('notes') or []))
    return rec


def result(ws, well_id: str, stage: str) -> dict:
    meta = (_index(ws).get(well_id) or {}).get(str(stage))
    if meta is None:
        raise KeyError(f'{well_id} has no treatment record for stage {stage}')
    key = (os.path.abspath(ws.path), well_id, str(stage), meta['sha256'], json.dumps(meta.get('declared'), sort_keys=True))
    if key not in _MEMO:
        if len(_MEMO) > 200:
            _MEMO.clear()
        _MEMO[key] = analyse(load(ws, well_id, stage), meta.get('declared'))
    return _MEMO[key]


def _thin(x: np.ndarray, y: np.ndarray, n: int = 700) -> Tuple[List[float], List[float]]:
    """A curve for a chart: n bins, each bin's own greatest and least kept in the order they came."""
    if len(x) <= 2 * n:
        return [round(float(v), 1) for v in x], [None if math.isnan(v) else round(float(v), 2) for v in y]
    edges = np.linspace(0, len(x), n + 1).astype(int)
    xs: List[float] = []; ys: List[float] = []
    for a, b in zip(edges[:-1], edges[1:]):
        seg_ = y[a:b]
        if b <= a or np.isnan(seg_).all():
            continue
        i, j = a + int(np.nanargmin(seg_)), a + int(np.nanargmax(seg_))
        for k in sorted({i, j}):
            xs.append(round(float(x[k]), 1)); ys.append(round(float(y[k]), 2))
    return xs, ys


def traces(ws, well_id: str, stage: str) -> dict:
    """What the stage's charts are drawn from: the whole record thinned, and the final shut-in second by second."""
    rec = load(ws, well_id, stage)
    r = result(ws, well_id, stage)
    t = rec['t']
    out = {'pressure': dict(zip(('t', 'v'), _thin(t, rec['pressure']))), 'rate': dict(zip(('t', 'v'), _thin(t, np.nan_to_num(rec['rate']))))}
    if rec.get('conc') is not None:
        c = np.where((rec['conc'] < 0) | (rec['conc'] > RULE['conc_max']), np.nan, rec['conc'])
        if not np.isnan(c).all():
            out['conc'] = dict(zip(('t', 'v'), _thin(t, c)))
    f = r.get('final')
    if f:
        t0 = f['at_s']
        m = (t >= t0 - 180.0) & (t <= t0 + 240.0)
        out['shut_in'] = {'t': [round(float(x - t0), 1) for x in t[m]], 'pressure': [None if math.isnan(x) else round(float(x), 1) for x in rec['pressure'][m]],
                          'rate': [round(float(x), 2) for x in np.nan_to_num(rec['rate'][m])]}
    return out


def summary_row(well: dict, stage: str, meta: dict, r: dict) -> dict:
    pu = r.get('pumping') or {}; f = r.get('final') or {}; fo = r.get('falloff') or {}; bh = r.get('bottomhole') or {}; sd = f.get('step_down') or {}
    return {'well': well['id'], 'display': well.get('display') or well['id'], 'stage': stage, 'start': meta.get('start'), 'status': r.get('status'), 'why': r.get('why'),
            'minutes': pu.get('injection_min'), 'slurry_bbl': pu.get('slurry_bbl'), 'slurry_totaliser_bbl': pu.get('slurry_totaliser_bbl'), 'clean_bbl': pu.get('clean_bbl'),
            'proppant_lbm': pu.get('proppant_lbm'), 'proppant_totaliser_lbm': pu.get('proppant_totaliser_lbm'), 'rate_avg_bpm': pu.get('rate_avg_bpm'), 'rate_max_bpm': pu.get('rate_max_bpm'),
            'pressure_avg_psi': pu.get('pressure_avg_psi'), 'pressure_max_psi': pu.get('pressure_max_psi'), 'breakdown_psi': (r.get('breakdown') or {}).get('pressure_psi'),
            'isip_psi': f.get('isip_psi'), 'isip_low_psi': f.get('isip_low_psi'), 'isip_high_psi': f.get('isip_high_psi'), 'isip_wide': f.get('wide'),
            'friction_psi': f.get('friction_psi'), 'friction_rate_bpm': f.get('last_rate_bpm'), 'square_psi': sd.get('square_psi'), 'root_psi': sd.get('root_psi'),
            'shut_ins': len([x for x in r.get('shut_ins') or [] if x.get('status') == 'OK']), 'falloff_min': fo.get('minutes'), 'p5_psi': (fo.get('at') or {}).get('5'),
            'p10_psi': (fo.get('at') or {}).get('10'), 'p15_psi': (fo.get('at') or {}).get('15'), 'closure_psi': (fo.get('pick') or {}).get('pressure_psi'),
            'gradient_psi_ft': bh.get('gradient_psi_ft'), 'bottomhole_isip_psi': bh.get('isip_psi'), 'declared': meta.get('declared'), 'source': meta.get('source'), 'file': meta.get('file'),
            'layout': meta.get('layout'), 'step_rate': [x['status'] for x in r.get('step_rate') or []]}


def page(ws, well_id: Optional[str] = None, stage: Optional[str] = None) -> dict:
    """Every stage with a treatment record, one row each; and, for the stage asked for, everything read from it."""
    idx = _index(ws)
    wells = {w['id']: w for w in ws.wells()}
    rows = []
    for wid in sorted(idx, key=lambda x: (wells.get(x, {}).get('display') or x)):
        if wid not in wells:
            continue
        for st in sorted(idx[wid], key=stage_key):
            try:
                rows.append(summary_row(wells[wid], st, idx[wid][st], result(ws, wid, st)))
            except (OSError, ValueError, KeyError) as e:
                rows.append({'well': wid, 'display': wells[wid].get('display') or wid, 'stage': st, 'status': 'UNREADABLE', 'why': str(e)[:200], 'start': idx[wid][st].get('start')})
    ok = [r for r in rows if r.get('isip_psi') is not None]
    out = {'status': 'OK' if rows else 'NO TREATMENT RECORD', 'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'title': ws.manifest.get('name') or 'this workspace',
           'wells': [{'id': w['id'], 'display': w.get('display') or w['id']} for w in ws.wells()], 'stages': rows, 'not_a_measurement': NOT_A_MEASUREMENT, 'water_psi_ft': WATER_PSI_FT,
           'totals': {'stages': len(rows), 'read': len(ok), 'slurry_bbl': round(sum(r.get('slurry_bbl') or 0 for r in rows)), 'proppant_lbm': round(sum(r.get('proppant_lbm') or 0 for r in rows)),
                      'isip_low_psi': min((r['isip_psi'] for r in ok), default=None), 'isip_high_psi': max((r['isip_psi'] for r in ok), default=None),
                      'wide': sum(1 for r in ok if r.get('isip_wide')), 'step_downs': sum(1 for r in ok if r.get('square_psi') is not None)}}
    lines = []
    if not rows:
        lines.append('No stage has a treatment record yet: attach one below - the stage\'s pressure, rate and proppant, second by second, as the frac service company gave it.')
    else:
        t_ = out['totals']
        lines.append(f"{len(rows)} stage(s) on {len({r['well'] for r in rows})} well(s): {t_['slurry_bbl']:,} bbl of slurry and {t_['proppant_lbm']:,} lbm of proppant pumped, by the records' own rates.")
        if ok:
            lines.append(f"The ISIP was read at {len(ok)} stage(s): {t_['isip_low_psi']:,} to {t_['isip_high_psi']:,} psi at the surface"
                         + (f"; at {t_['wide']} the pressure was still falling fast after the ringing and the ISIP is a wide range" if t_['wide'] else '') + '.')
        grads = [r['gradient_psi_ft'] for r in ok if r.get('gradient_psi_ft') is not None]
        if grads:
            lines.append(f"With the perforation depth declared at {len(grads)} stage(s), the ISIP gradient is {min(grads):.2f} to {max(grads):.2f} psi/ft.")
        else:
            lines.append('No perforation depth is declared: the ISIPs are surface pressures, with no gradient.')
        sdr = [r for r in ok if r.get('square_psi') is not None]
        if sdr:
            lines.append(f"{len(sdr)} stage(s) were stepped down to the shut-in: " + '; '.join(
                f"{r['display']} stage {r['stage']} {r['friction_psi']:,} psi at {r['friction_rate_bpm']:g} bbl/min ({r['square_psi']:,} with the rate squared, {r['root_psi']:,} with its root)" for r in sdr[:6])
                         + (f"; and {len(sdr) - 6} more in the table" if len(sdr) > 6 else '') + '.')
        picks = [r for r in ok if r.get('closure_psi') is not None]
        lines.append((f"A closure candidate is offered at {len(picks)} stage(s): " + '; '.join(f"{r['display']} stage {r['stage']} {r['closure_psi']:,} psi" for r in picks) + '.') if picks else
                     'No stage\'s fall-off shows a closure within its record: the G dP/dG curves do not leave a straight line through the origin before the records end.')
    out['lines'] = lines
    if rows:
        cur = next((r for r in rows if r['well'] == well_id and r['stage'] == str(stage)), None) if well_id else None
        cur = cur or next((r for r in rows if r.get('status') == 'OK'), rows[0])
        out['well'], out['stage'] = cur['well'], cur['stage']
        if cur.get('status') != 'UNREADABLE':
            out['detail'] = {**result(ws, cur['well'], cur['stage']), 'meta': idx[cur['well']][cur['stage']], 'traces': traces(ws, cur['well'], cur['stage'])}
    try:
        from . import public_pad as PP
        cr = PP.credit(ws)
        if cr and rows:
            out['public_source'] = cr
            out['lines'].append(cr['line'])
    except Exception:
        pass
    return out


def stage_table(ws, well_id: str) -> str:
    """The stages of a well as the Frac hits tab reads a stage table: each stage's first and last moment of injection,
    its clean volume and its proppant, from its own record."""
    idx = _index(ws).get(well_id) or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['Stage', 'Start Time', 'End Time', 'Clean Volume (bbl)', 'Proppant (lb)'])
    for st in sorted(idx, key=stage_key):
        r = result(ws, well_id, st)
        inj = [s for s in r.get('segments') or [] if s['kind'] == 'INJECTION']
        if not inj or not idx[st].get('start'):
            continue
        t0 = datetime.strptime(idx[st]['start'], '%Y-%m-%dT%H:%M:%S')
        pu = r.get('pumping') or {}
        w.writerow([st, (t0 + timedelta(seconds=inj[0]['start_s'])).strftime('%Y-%m-%d %H:%M:%S'), (t0 + timedelta(seconds=inj[-1]['end_s'])).strftime('%Y-%m-%d %H:%M:%S'),
                    pu.get('clean_bbl') if pu.get('clean_bbl') is not None else pu.get('slurry_bbl') or '', pu.get('proppant_lbm') or ''])
    return buf.getvalue()


def to_frac_hits(ws, well_id: str, actor: str) -> dict:
    """Give the Frac hits tab this well's stage times, read from the treatment records."""
    from . import frac_hits as FH
    table = stage_table(ws, well_id)
    if table.count('\n') < 2:
        raise TreatmentError('no stage of this well has a start time and an injection to give the Frac hits tab')
    return FH.attach_stages(ws, well_id, 'stages_from_treatment_records.csv', table.encode('utf-8'), actor, 'the stages\' own treatment records (the Frac treatment tab)')


def text(p: dict) -> str:
    L = ['Frac treatment: each stage from its own pumping record', '=' * 54] + list(p.get('lines') or [])
    rows = p.get('stages') or []
    if rows:
        L.append(f"  {'well':<14}{'stage':<7}{'start':<17}{'min':>6}{'slurry bbl':>12}{'prop lbm':>11}{'bpm':>6}{'avg psi':>9}{'max psi':>9}{'ISIP psi':>10}{'range':>13}{'friction':>10}{'at bpm':>8}{'5 min':>8}{'15 min':>8}{'psi/ft':>8}")
        f = lambda v, fmt='{:,.0f}': '-' if v is None else fmt.format(v)
        for r in rows:
            if r.get('status') == 'UNREADABLE':
                L.append(f"  {r['display']:<14}{r['stage']:<7}unreadable: {r.get('why')}")
                continue
            rng = '-' if r.get('isip_low_psi') is None else f"{r['isip_low_psi']:,}-{r['isip_high_psi']:,}"
            L.append(f"  {r['display'][:13]:<14}{r['stage']:<7}{(r.get('start') or '-')[:16].replace('T', ' '):<17}{f(r.get('minutes'), '{:,.0f}'):>6}{f(r.get('slurry_bbl')):>12}{f(r.get('proppant_lbm')):>11}"
                     f"{f(r.get('rate_avg_bpm'), '{:.0f}'):>6}{f(r.get('pressure_avg_psi')):>9}{f(r.get('pressure_max_psi')):>9}{f(r.get('isip_psi')):>10}{rng:>13}{f(r.get('friction_psi')):>10}"
                     f"{f(r.get('friction_rate_bpm'), '{:.0f}'):>8}{f(r.get('p5_psi')):>8}{f(r.get('p15_psi')):>8}{f(r.get('gradient_psi_ft'), '{:.3f}'):>8}")
    d = p.get('detail')
    if d and d.get('status') == 'OK':
        L.append(f"Stage {p.get('stage')} of {next((w['display'] for w in p['wells'] if w['id'] == p.get('well')), p.get('well'))}:")
        if d.get('breakdown'):
            b = d['breakdown']
            L.append(f"  first pressure peak {b['pressure_psi']:,} psi at {b['rate_bpm']:g} bbl/min, {b['at_s'] / 60:.1f} min in; it fell {b['fell_psi']:,} psi in {b['in_s']:.0f} s with the rate held")
        for s in d.get('shut_ins') or []:
            if s.get('status') != 'OK':
                L.append(f"  shut-in at {s['at_s'] / 60:.1f} min: {s.get('why')}")
                continue
            L.append(f"  shut-in at {s['at_s'] / 60:.1f} min after {s['after_bbl']:,.0f} bbl: ISIP {s['isip_psi']:,} psi ({s['isip_low_psi']:,} to {s['isip_high_psi']:,})"
                     + (f"; friction {s['friction_psi']:,} psi at {s['last_rate_bpm']:g} bbl/min" if s.get('friction_psi') is not None else '')
                     + (f"; step-down over {len(s['step_down']['points'])} rates: {s['step_down']['reads']}" if s.get('step_down') else ''))
        fo = d.get('falloff') or {}
        if fo:
            L.append(f"  fall-off, {fo.get('minutes')} min: " + (fo.get('reads') or fo.get('why') or ''))
        for sr in d.get('step_rate') or []:
            L.append(f"  step-rate at {sr['start_s'] / 60:.1f} min over {len(sr['points'])} rates: {sr['reads']}")
        for n in d.get('notes') or []:
            L.append(f"  note: {n}")
    return '\n'.join(L)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'frac_treatment')
    idx = _index(ws)
    full = {'program_version': program_version, **{k: v for k, v in p.items() if k != 'detail'},
            'by_stage': {f"{r['well']}|{r['stage']}": {k: v for k, v in result(ws, r['well'], r['stage']).items()} for r in p['stages'] if r.get('status') != 'UNREADABLE'},
            'records': idx}
    with open(os.path.join(d, 'frac_treatment.json'), 'w', encoding='utf-8') as f:
        json.dump(full, f, indent=1, default=str)
    with open(os.path.join(d, 'frac_treatment.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Frac treatment: each stage from its own pumping record\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines'])
                + '\n\n```\n' + text({**p, 'detail': None}) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    keys = ['display', 'stage', 'start', 'status', 'minutes', 'slurry_bbl', 'slurry_totaliser_bbl', 'clean_bbl', 'proppant_lbm', 'proppant_totaliser_lbm', 'rate_avg_bpm', 'rate_max_bpm',
            'pressure_avg_psi', 'pressure_max_psi', 'breakdown_psi', 'isip_psi', 'isip_low_psi', 'isip_high_psi', 'friction_psi', 'friction_rate_bpm', 'square_psi', 'root_psi',
            'falloff_min', 'p5_psi', 'p10_psi', 'p15_psi', 'closure_psi', 'bottomhole_isip_psi', 'gradient_psi_ft', 'file', 'source']
    with open(os.path.join(d, 'stages.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(keys)
        for r in p['stages']:
            wr.writerow(['' if r.get(k) is None else r.get(k) for k in keys])
    files = ['frac_treatment.json', 'frac_treatment.md', 'stages.csv']
    for wid in idx:
        tb = stage_table(ws, wid)
        if tb.count('\n') >= 2:
            fn = f'stage_table_{_safe(wid)}.csv'
            with open(os.path.join(d, fn), 'w', encoding='utf-8', newline='') as f:
                f.write(tb)
            files.append(fn)
    ws.audit(actor, 'frac_treatment.report', {'files': files, 'stages': len(p['stages'])})
    return {'folder': d, 'files': files, 'page': p}


# -- a labelled stage ---------------------------------------------------------------------------------------------------
def synthetic(isip: float = 4200.0, a: float = 0.30, b: float = 110.0, rate: float = 60.0, pump_min: float = 40.0, steps: Tuple[float, ...] = (45.0, 30.0, 15.0), closure_g: Optional[float] = 0.9,
              leak_psi: float = 520.0, falloff_min: float = 90.0, ring_psi: float = 140.0, breakdown_psi: float = 900.0, noise: float = 2.0, seed: int = 3, step_up: bool = True) -> dict:
    """One stage, second by second, built so that every number this page reads has a known answer: an ISIP, a friction
    of a x q-squared + b x root-q, a step-down through `steps`, a ringing of `ring_psi` that dies in fifteen seconds,
    a fall-off that is a straight line in G (slope `leak_psi`) down to a closure at G = `closure_g` and a third as steep
    after it, a breakdown peak in the opening, and before the stage a step-rate test whose pressure bends at 3,000 psi."""
    rng = np.random.default_rng(seed)
    q: List[float] = []
    pres: List[float] = []
    fr = lambda x: a * x * x + b * math.sqrt(max(x, 0.0))
    if step_up:                                                                      # the test: six rates of a minute each, the pressure bending at 3,000 psi
        for k, x in enumerate((1.0, 2.0, 3.0, 4.0, 5.0, 6.0)):
            pv = 1800.0 + 400.0 * x if x <= 3.0 else 3000.0 + 60.0 * (x - 3.0)
            q += [x] * 60; pres += [pv] * 60
        q += [0.0] * 240; pres += list(2600.0 - 300.0 * (1.0 - np.exp(-np.arange(240) / 80.0)))
    t_start = len(q)
    n_pump = int(pump_min * 60)
    for i in range(n_pump):
        x = rate * min(1.0, i / 120.0)
        base = isip - 250.0 + 250.0 * min(1.0, i / n_pump * 3.0)
        bump = breakdown_psi * math.exp(-((i - 150.0) / 25.0) ** 2) if i < 400 else 0.0
        q.append(x); pres.append(base + fr(x) + bump)
    for x in steps:
        q += [x] * 20; pres += [isip + fr(x)] * 20
    tp = float(len(q) - t_start)
    t0 = len(q)
    n_fall = int(falloff_min * 60)
    dt_ = np.arange(1, n_fall + 1, dtype=np.float64)
    G = g_function(dt_, tp)
    if closure_g is None:
        fall = isip - leak_psi * G
    else:
        fall = np.where(G <= closure_g, isip - leak_psi * G, isip - leak_psi * closure_g - leak_psi / 3.0 * (G - closure_g))
    fall = fall + ring_psi * np.exp(-dt_ / 5.0) * np.cos(2 * math.pi * dt_ / 6.0)
    q += [0.0] * n_fall; pres += list(fall)
    qa = np.array(q); pa = np.array(pres) + rng.normal(0.0, noise, len(q))
    conc = np.where((np.arange(len(q)) > t_start + 0.25 * n_pump) & (np.arange(len(q)) < t0 - 60 - 20 * len(steps)), 1.5, 0.0)
    t = np.arange(len(q), dtype=np.float64)
    return {'t': t, 'pressure': pa, 'rate': qa, 'conc': conc, 'clean_rate': qa / (1.0 + conc * 0.0456), 'slurry_total': np.cumsum(qa) / 60.0, 'clean_total': None, 'prop_total': None,
            'pressure2': None, 'bottomhole': None, 'start': '2026-08-03T06:00:00', 'cadence_s': 1.0, 'notes': [],
            'expected': {'isip': isip, 'friction': fr(rate), 'square': a * rate * rate, 'root': b * math.sqrt(rate), 'closure_g': closure_g,
                         'closure_psi': None if closure_g is None else isip - leak_psi * closure_g, 'extension_psi': 3000.0, 'shut_in_s': float(t0), 'pump_s': tp,
                         'slurry_bbl': float(qa.sum() / 60.0), 'breakdown_psi': isip - 250.0 + 250.0 * (150 / n_pump * 3.0) + fr(rate) + breakdown_psi}}


def write_csv(path: str, rec: dict, stacked: bool = False) -> str:
    """A labelled stage as a file: one header row, or the stacked header of a service company's export."""
    t0 = datetime.strptime(rec['start'], '%Y-%m-%dT%H:%M:%S')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        if stacked:
            w.writerow(['', 'Time of', 'Treating', 'Slurry', 'Clean', 'Prop', 'Slurry Stage'])
            w.writerow(['Date', 'Day', 'Pressure', 'Rate', 'Rate', 'Conc', 'Total'])
            w.writerow(['(mm:dd:yyyy)', '(hh:mm:ss)', '(psi)', '(bpm)', '(bpm)', '(ppg)', '(bbl)'])
        else:
            w.writerow(['Timestamp', 'Treating Pressure (psi)', 'Slurry Rate (bpm)', 'Clean Rate (bpm)', 'Prop Conc (ppg)', 'Slurry Total (bbl)'])
        for i in range(len(rec['t'])):
            ts = t0 + timedelta(seconds=float(rec['t'][i]))
            vals = [f"{rec['pressure'][i]:.1f}", f"{rec['rate'][i]:.2f}", f"{rec['clean_rate'][i]:.3f}", f"{rec['conc'][i]:.2f}", f"{rec['slurry_total'][i]:.2f}"]
            w.writerow(([ts.strftime('%Y-%m-%d'), ts.strftime('%H:%M:%S')] if stacked else [ts.strftime('%Y-%m-%d %H:%M:%S')]) + vals)
    return path


def write_dataacq(path: str, rec: dict) -> str:
    """The same stage as a frac van's data-acquisition export."""
    t0 = datetime.strptime(rec['start'], '%Y-%m-%dT%H:%M:%S')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('*File was generated by DataAcq ver 1\n\n*Start date and time: ' + t0.strftime('%Y-%m-%d %I:%M:%S %p') + '\n\n*Step, Start and Depth flag:\n\n0.0166667 0 0\n\n'
                '*Total number of Data Channels:\n\n5\n\n*Base column included\n\n*Names of Data Channels:\n\nTime (min) TIME\nA1: Treating Pressure UA46\nA2: Line Pressure UB46\n'
                'Slurry Rate UP50\nBlender Conc Ul53\n\n*Data:\n\n')
        for i in range(len(rec['t'])):
            f.write(f" {rec['t'][i] / 60.0:.5f} {rec['pressure'][i]:.1f} {rec['pressure'][i] + 12.0:.1f} {rec['rate'][i]:.2f} {rec['conc'][i]:.2f}\n")
    return path


def selftest() -> dict:
    """Every number this page reads, on a stage built with the answer known."""
    import tempfile
    checks = []
    rec = synthetic()
    e = rec['expected']
    r = analyse(rec, {'tvd_ft': 8000.0, 'fluid_psi_ft': WATER_PSI_FT, 'source': 'self-test'})
    f = r['final']; fo = r['falloff']; sd = f['step_down']
    checks.append({'check': 'the ISIP is read back through the ringing to within 1 % of the truth, and the truth is inside its range widened by 15 psi', 'value': [f['isip_psi'], f['isip_low_psi'], f['isip_high_psi'], e['isip']],
                   'pass': abs(f['isip_psi'] - e['isip']) <= 0.01 * e['isip'] and f['isip_low_psi'] - 15 <= e['isip'] <= f['isip_high_psi'] + 15 and not f['wide']})
    checks.append({'check': 'the friction at the treating rate, and its two parts from the step-down', 'value': [f['friction_psi'], round(e['friction']), sd['square_psi'], round(e['square']), sd['root_psi'], round(e['root'])],
                   'pass': abs(f['friction_psi'] - e['friction']) <= 0.04 * e['friction'] and abs(sd['square_psi'] - e['square']) <= 0.15 * e['square'] and abs(sd['root_psi'] - e['root']) <= 0.15 * e['root']
                         and len(sd['points']) == 4 and f['last_rate_bpm'] == 60.0})
    fo1 = analyse(synthetic(step_up=False))['falloff']                                # one stretch of pumping, so that G here is G as the stage was built
    pk = fo1.get('pick') or {}
    checks.append({'check': 'the fall-off is a line in G down to the closure, and the pick is where it leaves it', 'value': [pk.get('g'), e['closure_g'], pk.get('pressure_psi'), round(e['closure_psi']), (fo.get('pick') or {}).get('pressure_psi')],
                   'pass': bool(pk) and abs(pk['g'] - e['closure_g']) <= 0.06 and abs(pk['pressure_psi'] - e['closure_psi']) <= 40.0 and abs(fo1['line']['slope_psi'] - 520.0) <= 40.0
                         and abs(((fo.get('pick') or {}).get('pressure_psi') or 0) - e['closure_psi']) <= 40.0 and abs(pk['efficiency'] - pk['g'] / (2 + pk['g'])) < 1e-3})
    r_open = analyse(synthetic(closure_g=None, falloff_min=60.0))
    checks.append({'check': 'a fall-off that never leaves its line is said not to have closed, and no pick is offered', 'value': (r_open['falloff'].get('reads') or '')[:60],
                   'pass': r_open['falloff'].get('pick') is None and 'still on its straight line' in (r_open['falloff'].get('reads') or '')})
    sr = r['step_rate']
    checks.append({'check': 'the step-rate test before the stage bends at the extension pressure; the stage\'s own ramp is not a test', 'value': [[x['status'] for x in sr], sr[0].get('extension_psi') if sr else None],
                   'pass': len(sr) == 1 and sr[0]['status'] == 'BREAK' and abs(sr[0]['extension_psi'] - e['extension_psi']) <= 60.0})
    pu = r['pumping']
    checks.append({'check': 'the volume is the rate summed, and agrees with the record\'s totaliser; the proppant is the concentration times the clean rate', 'value': [pu['slurry_bbl'], pu.get('slurry_totaliser_bbl'), pu.get('proppant_lbm')],
                   'pass': abs(pu['slurry_bbl'] - pu['slurry_totaliser_bbl']) <= 2 and pu['proppant_lbm'] > 50000 and len(r['shut_ins']) == 2})
    bd = r['breakdown'] or {}
    checks.append({'check': 'the breakdown is the peak the rate did not cause', 'value': [bd.get('pressure_psi'), round(e['breakdown_psi'])],
                   'pass': bool(bd) and abs(bd['pressure_psi'] - e['breakdown_psi']) <= 60.0 and bd['fell_psi'] >= 600})
    bh = r['bottomhole']
    checks.append({'check': 'the bottomhole ISIP is the surface one plus the declared column, and its gradient', 'value': [bh['isip_psi'], bh['gradient_psi_ft']],
                   'pass': abs(bh['isip_psi'] - (f['isip_psi'] + 0.433 * 8000.0)) <= 1 and abs(bh['gradient_psi_ft'] - bh['isip_psi'] / 8000.0) < 1e-3})
    fast = synthetic(ring_psi=0.0, step_up=False, steps=(), falloff_min=20.0)
    k = int(fast['expected']['shut_in_s'])
    fast['pressure'][k:] = fast['pressure'][k:] - 1500.0 * (1.0 - np.exp(-np.arange(len(fast['t']) - k) / 45.0))
    r_fast = analyse(fast)
    checks.append({'check': 'a pressure still falling fast after the pumps stop gives a wide ISIP, said to be wide', 'value': [r_fast['final']['isip_low_psi'], r_fast['final']['isip_high_psi']],
                   'pass': r_fast['final']['wide'] and r_fast['final']['isip_high_psi'] - r_fast['final']['isip_low_psi'] > 200})
    with tempfile.TemporaryDirectory() as tmp:
        small = synthetic(pump_min=12.0, falloff_min=8.0, step_up=False)
        got = []
        for fn, wr in (('plain.csv', lambda p_: write_csv(p_, small)), ('stacked.csv', lambda p_: write_csv(p_, small, True)), ('van.txt', lambda p_: write_dataacq(p_, small))):
            pth = wr(os.path.join(tmp, fn))
            with open(pth, 'rb') as fh:
                rr = read_treatment(fh.read(), fn)
            a_ = analyse(rr)
            got.append((rr['layout'], rr['samples'], a_['final']['isip_psi'], rr['start']))
        checks.append({'check': 'the same stage read from a plain CSV, a stacked header and a van\'s export gives the same ISIP and the same start', 'value': got,
                       'pass': len({g[2] for g in got}) == 1 and len({g[1] for g in got}) == 1 and len({g[3] for g in got}) == 1 and got[2][0] == 'data-acquisition export'
                             and got[1][0] == 'table with a stacked header'})
        try:
            read_treatment(('Timestamp,Depth (ft),Gamma (api)\n' + ''.join(f'2026-08-03 06:00:{k:02d},{100 + k},{50 + k % 3}\n' for k in range(20))).encode(), 'log.csv')
            no = False
        except TreatmentError as ex:
            no = 'treating pressure' in str(ex)
        checks.append({'check': 'a file with no pressure and rate is turned away, with its columns named', 'value': no, 'pass': no})
    checks.append({'check': 'stages sort as a crew counts them', 'value': sorted(['10', '3R', '6B', '4', '6A', '9'], key=stage_key), 'pass': sorted(['10', '3R', '6B', '4', '6A', '9'], key=stage_key) == ['3R', '4', '6A', '6B', '9', '10']})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION: stages built with every answer known, in three file layouts'}
