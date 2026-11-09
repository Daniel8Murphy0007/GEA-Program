# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""fluid - the fluid ledger and the material balance of a formation: every barrel that went into or came out
of a formation through the site's wells, by day, with its density and its dissolved solids, against the room the
formation has for it.

The question a disposal operator, a neighbour and a regulator share is where the water went. This module answers
the part of it that arithmetic can answer, from what the program already holds, and names the rest as a
projection:

- THE LEDGER. Per well, per calendar day: the volume in (a disposal or injection well) or out (a producer), the
  surface pressure and the bottomhole pressure where they are recorded. The record comes from one of two places,
  and the page says which: the well's own measured stream when its injection channels are declared (`sra`'s
  four daily parameters, the same arithmetic the TexNet packet uses), or the operator's own injection or
  production report attached as a CSV (a date column, a volume column, and whatever pressure, density or TDS
  columns the report carries). The density of what went in is declared with its source (a water analysis), as
  specific gravity or as total dissolved solids; from TDS the density is McCain's (1991) correlation, named.
  Each day's barrels become a mass of water (tonnes) and a mass of dissolved solids (tonnes): the concentration
  side of the ledger.
- THE FORMATION. Each well is assigned to a formation by declaration - and the page shows the `interval`
  statement beside it (the declared disposal interval against the mapped tops) so a declaration the map
  contradicts is seen. A formation is declared with its porosity, net thickness, area and compressibilities
  (water and pore volume), each with a source; pore volume is 7,758 x area x thickness x porosity barrels, and
  the room it has for a pressure rise is the storage c_t x V_p, barrels per psi.
- THE BALANCE. For a slightly compressible single-phase formation, the net volume kept raises the pressure by
  dV / (c_t V_p). The ledger has the volume; the wells' bottomhole pressures have the pressure; a least-squares
  line of pressure against cumulative net volume gives the storage the pressure SAYS the formation has. Against
  the storage DECLARED: near one and the formation behaves as the declared closed volume; much larger and the
  fluid is finding room the declaration does not have - an aquifer, leak-off, a larger connected volume - and
  the balance names the barrels it cannot place at the observed pressure (the UNACCOUNTED volume, with its
  tonnes of solids) as the outflow it is looking for; much smaller and the declared volume is too large or the
  well is poorly connected to it. The Hall plot (Hall 1963; cumulative (p_wf - p_e) x days against cumulative
  volume) is drawn for each injection well: a rising slope is plugging or a filling formation, a falling one an
  opened path.
- THE NEIGHBOURS. Every other well's distance from each injection well (geodesic, surface to surface, on the
  declared datum - no survey is used, and the page says so) and, from the declared permeability, viscosity and
  storage, two projections labelled SIMULATION: the day the pressure front (the radius of investigation,
  r = 0.0325 sqrt(k t / (phi mu c_t)), field units) reaches it and the pressure rise there by the line-source
  solution at the well's mean rate; and the swept radius of the injected water itself (piston displacement in a
  cylinder of the net thickness: r = sqrt(V / (pi h phi)), the TDS front) with the volume it would take to reach
  the neighbour, and the density difference that makes the real front sink or float.

What it will not do: invent a volume from a pressure (a well with no rate channel and no report has NO VOLUME
RECORD, named), a density from nothing (undeclared density leaves the mass columns empty), a formation from a
depth (the assignment is a declaration; the interval statement stands beside it), or a plume from a point (the
grid, the faults and the time-stepped pressure field are the next module; this one is the ledger they will be
fed from). Every number carries the record or the declaration it came from.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import geodesy as G

BBL_PER_ACRE_FT = 7758.0
FT3_PER_BBL = 5.614583
M3_PER_BBL = 0.158987
C_WATER_PER_PSI = 3.0e-6        # brine, reservoir conditions; stated as the default, a declaration overrides
C_PORE_PER_PSI = 4.0e-6         # pore-volume compressibility, consolidated sandstone (Hall 1953 range 3 to 10 x 1e-6)
MU_WATER_CP = 0.6               # brine at ~150 F
ROLES = ('injection', 'production')
STATUS_CLOSED = 'CLOSED AS DECLARED'
STATUS_MORE = 'MORE ROOM THAN DECLARED'
STATUS_LESS = 'LESS ROOM THAN DECLARED'
STATUS_NOFIT = 'NOT FITTED'
RATIO_LO, RATIO_HI = 0.5, 2.0
MIN_FIT_DAYS = 10

_LOCK = threading.Lock()

DATE_COLS = ('date', 'day', 'report_date', 'report date', 'injection date', 'production date', 'yyyy-mm-dd')
VOL_IN_COLS = ('injection_volume_bbl', 'injection volume (bbl)', 'injection volume', 'injected_bbl', 'volume_bbl', 'volume (bbl)',
               'volume', 'bbl', 'barrels', 'water_bbl', 'water (bbl)', 'water', 'bwpd', 'bbl/d', 'bbl_per_day')
OIL_COLS = ('oil_bbl', 'oil (bbl)', 'oil', 'bopd')
GAS_COLS = ('gas_mcf', 'gas (mcf)', 'gas', 'mcfd')
PAVG_COLS = ('avg_surface_injection_pressure_psi', 'average surface injection pressure (psi)', 'avg_pressure_psi', 'avg pressure', 'average pressure',
             'tubing_pressure_psi', 'tubing pressure', 'thp', 'avg_thp_psi', 'wellhead_pressure_psi', 'pressure_psi', 'pressure')
PMAX_COLS = ('max_surface_injection_pressure_psi', 'maximum surface injection pressure (psi)', 'max_pressure_psi', 'max pressure', 'maximum pressure')
BHP_COLS = ('bhp_psi', 'bottomhole_pressure_psi', 'bottomhole pressure (psi)', 'bhp', 'gauge_psi', 'downhole_pressure_psi')
SG_COLS = ('density_sg', 'sg', 'specific_gravity', 'specific gravity')
TDS_COLS = ('tds_mgl', 'tds (mg/l)', 'tds', 'tds_mg_l', 'salinity_mgl', 'total dissolved solids')
PPG_COLS = ('density_ppg', 'ppg', 'lb/gal', 'density (lb/gal)')


class FluidError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _well(ws, well_id: str) -> dict:
    try:
        return ws.well(well_id)
    except Exception as e:                     # the workspace's own error, as the ledger's
        raise FluidError(str(e))


# --------------------------------------------------------------------------------------------------------------
# density and dissolved solids
# --------------------------------------------------------------------------------------------------------------
def density_from_tds(tds_mgl: float) -> dict:
    """Brine density at standard conditions from total dissolved solids, by McCain (1991): rho (lb/ft3) =
    62.368 + 0.438603 S + 1.60074e-3 S^2, S the salinity in weight percent. TDS in mg/L is a mass per volume of
    brine, so S = TDS / (10,000 rho_gcc); two passes settle it. Returns the specific gravity and the steps."""
    tds = float(tds_mgl)
    if tds < 0 or tds > 400000:
        raise FluidError(f'TDS {tds:g} mg/L is outside 0 to 400,000 mg/L')
    sg = 1.0
    for _ in range(4):
        S = tds / (10000.0 * sg)
        rho_lbft3 = 62.368 + 0.438603 * S + 1.60074e-3 * S * S
        sg = rho_lbft3 / 62.368
    return {'sg': round(sg, 4), 'salinity_wt_pct': round(S, 3), 'rho_lb_ft3': round(rho_lbft3, 3), 'rho_ppg': round(rho_lbft3 / 7.4805, 3),
            'how': 'McCain (1991), rho = 62.368 + 0.438603 S + 0.00160074 S^2 lb/ft3 at standard conditions, S in weight percent'}


def tds_from_sg(sg: float) -> float:
    """The TDS (mg/L) McCain's correlation gives this specific gravity: the quadratic solved for S, then S x 10,000 x sg."""
    rho = float(sg) * 62.368
    a, b, c = 1.60074e-3, 0.438603, 62.368 - rho
    disc = b * b - 4 * a * c
    if disc < 0:
        return 0.0
    S = max(0.0, (-b + math.sqrt(disc)) / (2 * a))
    return round(S * 10000.0 * float(sg), 0)


def density_declaration(sg: Optional[float] = None, tds_mgl: Optional[float] = None, ppg: Optional[float] = None) -> dict:
    """What the operator declared of the fluid, with the other forms derived and said so."""
    if ppg is not None and sg is None:
        sg = float(ppg) / 8.3378
    if sg is None and tds_mgl is None:
        raise FluidError('a fluid density needs --density-sg, --density-ppg or --tds-mgl')
    out: Dict[str, Any] = {}
    if sg is not None:
        sg = float(sg)
        if not 0.9 <= sg <= 1.35:
            raise FluidError(f'specific gravity {sg:g} is outside 0.9 to 1.35; brines are')
        out.update(sg=round(sg, 4), sg_how='declared' if ppg is None else f'declared as {float(ppg):g} lb/gal (sg = ppg / 8.3378)')
        if tds_mgl is None:
            out.update(tds_mgl=tds_from_sg(sg), tds_how="from the specific gravity by McCain (1991), as the correlation's inverse")
    if tds_mgl is not None:
        d = density_from_tds(tds_mgl)
        out.update(tds_mgl=round(float(tds_mgl), 0), tds_how='declared')
        if sg is None:
            out.update(sg=d['sg'], sg_how=d['how'])
        else:
            out['sg_by_tds'] = d['sg']
            if abs(d['sg'] - sg) > 0.02:
                out['note'] = f"the declared specific gravity {sg:g} and the TDS's {d['sg']:g} (McCain) differ by more than 0.02: one of them is not this brine"
    return out


def mass_of(volume_bbl: float, sg: Optional[float], tds_mgl: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """Tonnes of brine and tonnes of dissolved solids in a volume: V (m3) x rho (t/m3); solids = V x TDS (mg/L = g/m3) / 1e6."""
    if volume_bbl is None:
        return None, None
    m3 = float(volume_bbl) * M3_PER_BBL
    t_w = m3 * float(sg) if sg is not None else None
    t_s = m3 * float(tds_mgl) / 1.0e6 if tds_mgl is not None else None
    return (round(t_w, 2) if t_w is not None else None), (round(t_s, 3) if t_s is not None else None)


# --------------------------------------------------------------------------------------------------------------
# the store: records/fluid.json
# --------------------------------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'fluid.json')


def load(ws) -> dict:
    p = _path(ws)
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
    else:
        d = {}
    d.setdefault('schema', 1); d.setdefault('wells', {}); d.setdefault('formations', {})
    return d


def _save(ws, d: dict) -> None:
    tmp = _path(ws) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, default=str)
    os.replace(tmp, _path(ws))


def _f(v, what: str, lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise FluidError(f'{what} must be a number, got {v!r}')
    if lo is not None and x < lo or hi is not None and x > hi:
        raise FluidError(f'{what} {x:g} is outside {lo:g} to {hi:g}')
    return x


def declare_well(ws, actor: str, well_id: str, role: Optional[str] = None, formation: Optional[str] = None, sg=None, tds_mgl=None, ppg=None,
                 start: Optional[str] = None, end: Optional[str] = None, source: str = '') -> dict:
    """What the ledger needs declared of a well: its role (injection or production), the formation it is completed in,
    and the density of what it injects (sg, lb/gal or TDS) for a period, each with its source."""
    _well(ws, well_id)
    if not (role or formation or sg is not None or tds_mgl is not None or ppg is not None):
        raise FluidError('nothing to declare: --role, --formation, or a density (--density-sg, --density-ppg, --tds-mgl)')
    if not (source or '').strip():
        raise FluidError('a declaration needs its source (--source "completion report W-2" or "water analysis 2026-03-14")')
    with _LOCK:
        d = load(ws)
        rec = d['wells'].setdefault(well_id, {'densities': [], 'reports': []})
        if role:
            if role not in ROLES:
                raise FluidError(f'the role must be one of {ROLES}, got {role!r}')
            rec['role'] = role; rec['role_source'] = source
        if formation:
            rec['formation'] = str(formation).strip(); rec['formation_source'] = source
        if sg is not None or tds_mgl is not None or ppg is not None:
            dens = density_declaration(sg, tds_mgl, ppg)
            dens.update({'from': _day(start) if start else None, 'to': _day(end) if end else None, 'source': source, 'declared_by': actor, 'declared_utc': _utc()})
            rec['densities'] = [x for x in rec.get('densities', []) if not (x.get('from') == dens['from'] and x.get('to') == dens['to'])] + [dens]
            rec['densities'].sort(key=lambda x: x.get('from') or '')
        rec['declared_by'] = actor; rec['declared_utc'] = _utc()
        _save(ws, d)
    ws.audit(actor, 'fluid.declare', {'well': well_id, 'role': rec.get('role'), 'formation': rec.get('formation'), 'densities': len(rec.get('densities', [])), 'source': source})
    return rec


def declare_formation(ws, actor: str, name: str, source: str = '', **vals) -> dict:
    """A formation's room and conductivity, declared: porosity (fraction), net thickness (ft), area (acres), water and
    pore-volume compressibilities (1/psi; defaults stated when blank), permeability (md), viscosity (cp), initial
    pressure (psi) and the formation water's density or TDS."""
    name = (name or '').strip()
    if not name:
        raise FluidError('a formation needs a name')
    if not (source or '').strip():
        raise FluidError('a formation declaration needs its source (--source "petrophysics summary 2025" ...)')
    keys = {'porosity': (0.001, 0.5), 'thickness_ft': (0.1, 20000.0), 'area_acres': (0.01, 1.0e7), 'c_water_per_psi': (1e-7, 1e-4), 'c_pore_per_psi': (1e-7, 1e-3),
            'k_md': (1e-4, 1e5), 'mu_cp': (0.1, 10.0), 'p_initial_psi': (0.0, 30000.0), 'water_sg': (0.9, 1.35), 'water_tds_mgl': (0.0, 400000.0)}
    with _LOCK:
        d = load(ws)
        rec = d['formations'].setdefault(name, {})
        n = 0
        for k, (lo, hi) in keys.items():
            v = vals.get(k)
            if v is not None and v != '':
                rec[k] = _f(v, k, lo, hi); n += 1
        if not n:
            raise FluidError('nothing to declare for the formation: give --porosity, --thickness-ft, --area-acres, --k-md, --mu-cp, --p-initial-psi, --water-sg or --water-tds-mgl')
        if rec.get('water_tds_mgl') is not None and vals.get('water_sg') in (None, ''):
            rec['water_sg'] = density_from_tds(rec['water_tds_mgl'])['sg']; rec['water_sg_how'] = 'McCain (1991) from the TDS'
        elif rec.get('water_sg') is not None and vals.get('water_sg') not in (None, ''):
            rec['water_sg_how'] = 'declared'
        rec['source'] = source; rec['declared_by'] = actor; rec['declared_utc'] = _utc()
        _save(ws, d)
    ws.audit(actor, 'fluid.formation', {'formation': name, **{k: rec.get(k) for k in keys}, 'source': source})
    return rec


def remove(ws, actor: str, well_id: Optional[str] = None, report: Optional[str] = None, formation: Optional[str] = None) -> dict:
    with _LOCK:
        d = load(ws)
        if formation:
            if formation not in d['formations']:
                raise FluidError(f'no formation {formation!r} declared')
            del d['formations'][formation]; what = {'formation': formation}
        elif well_id and report:
            rec = d['wells'].get(well_id) or {}
            if not any(r['file'] == report for r in rec.get('reports', [])):
                raise FluidError(f'{well_id} has no report {report}')
            rec['reports'] = [r for r in rec['reports'] if r['file'] != report]; what = {'well': well_id, 'report': report}
        elif well_id:
            if well_id not in d['wells']:
                raise FluidError(f'{well_id} has no fluid declaration')
            del d['wells'][well_id]; what = {'well': well_id}
        else:
            raise FluidError('say what to remove: --well, --well with --remove-report, or --remove-formation')
        _save(ws, d)
    ws.audit(actor, 'fluid.remove', what)
    return what


# --------------------------------------------------------------------------------------------------------------
# the operator's report, as a CSV
# --------------------------------------------------------------------------------------------------------------
def _day(s: Any) -> str:
    s = str(s).strip()
    cands = [s, s[:10], s.split('T')[0], s.split(' ')[0]]
    for c in cands:
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d.%m.%Y', '%Y%m%d', '%d-%b-%Y', '%b %d, %Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(c, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
    raise FluidError(f'{s!r} is not a date this program reads (YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY, DD.MM.YYYY)')


def _col(header: List[str], names: Tuple[str, ...]) -> Optional[str]:
    low = {h.strip().lower(): h for h in header}
    for n in names:
        if n in low:
            return low[n]
    for h in header:                                     # a unit in brackets after the name
        base = re.sub(r'\s*[\(\[].*$', '', h.strip().lower())
        if base in names:
            return h
    return None


def parse_report(text: str) -> dict:
    """The rows of an injection or production report: a date column and a volume column at least; pressures, BHP,
    density or TDS when the report has them. The volume is barrels for the day (a rate in bbl/d is the same number)."""
    if text.startswith('﻿'):
        text = text[1:]
    sample = text[:4096]
    delim = ';' if sample.count(';') > sample.count(',') else ('\t' if sample.count('\t') > sample.count(',') else ',')
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r) and not (r[0].strip().startswith('#'))]
    if len(rows) < 2:
        raise FluidError('the report has no rows under its header')
    header = [h.strip() for h in rows[0]]
    cols = {'date': _col(header, DATE_COLS), 'volume': _col(header, VOL_IN_COLS), 'oil': _col(header, OIL_COLS), 'gas': _col(header, GAS_COLS),
            'p_avg': _col(header, PAVG_COLS), 'p_max': _col(header, PMAX_COLS), 'bhp': _col(header, BHP_COLS),
            'sg': _col(header, SG_COLS), 'tds': _col(header, TDS_COLS), 'ppg': _col(header, PPG_COLS)}
    if not cols['date']:
        raise FluidError(f'no date column (one of {", ".join(DATE_COLS[:4])}) in the header: {", ".join(header[:8])}')
    if not cols['volume'] and not cols['oil']:
        raise FluidError(f'no volume column (one of {", ".join(VOL_IN_COLS[:6])}) in the header: {", ".join(header[:8])}')
    idx = {k: header.index(v) for k, v in cols.items() if v}
    out: List[dict] = []
    bad = 0
    for r in rows[1:]:
        if len(r) <= idx['date']:
            bad += 1; continue
        try:
            day = _day(r[idx['date']])
        except FluidError:
            bad += 1; continue
        row: Dict[str, Any] = {'date': day}
        for k in ('volume', 'oil', 'gas', 'p_avg', 'p_max', 'bhp', 'sg', 'tds', 'ppg'):
            if k in idx and len(r) > idx[k]:
                s = r[idx[k]].strip().replace(',', '')
                try:
                    row[k] = float(s) if s not in ('', '-', 'NA', 'n/a', 'NaN') else None
                except ValueError:
                    row[k] = None
            else:
                row[k] = None
        if row.get('ppg') is not None and row.get('sg') is None:
            row['sg'] = row['ppg'] / 8.3378
        out.append(row)
    if not out:
        raise FluidError(f'no row carried a readable date ({bad} skipped)')
    out.sort(key=lambda x: x['date'])
    days = [x['date'] for x in out]
    dup = len(days) - len(set(days))
    return {'rows': out, 'columns': {k: v for k, v in cols.items() if v}, 'n_rows': len(out), 'skipped': bad, 'duplicate_days': dup,
            'start': days[0], 'end': days[-1], 'delimiter': delim}


def attach_report(ws, actor: str, well_id: str, filename: str, content: bytes, source: str = '') -> dict:
    """The operator's report attached to the well: kept under wells/<id>/records/fluid/ with its SHA-256, read now
    to be sure it reads; the ledger reads it again each time."""
    _well(ws, well_id)
    if not (source or '').strip():
        raise FluidError('a report needs its source (--source "H-10 filed 2026-04-02", "operator production report")')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')
    p = parse_report(text)
    fn = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename) or 'report.csv')
    d_ = os.path.join(ws.well_dir(well_id), 'records', 'fluid')
    os.makedirs(d_, exist_ok=True)
    with open(os.path.join(d_, fn), 'wb') as f:
        f.write(content)
    rec = {'file': fn, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'n_rows': p['n_rows'], 'start': p['start'], 'end': p['end'],
           'columns': p['columns'], 'skipped': p['skipped'], 'duplicate_days': p['duplicate_days'], 'source': source, 'attached_by': actor, 'attached_utc': _utc()}
    with _LOCK:
        d = load(ws)
        w = d['wells'].setdefault(well_id, {'densities': [], 'reports': []})
        w['reports'] = [r for r in w.get('reports', []) if r['file'] != fn] + [rec]
        _save(ws, d)
    ws.audit(actor, 'fluid.report.attach', {k: rec[k] for k in ('file', 'sha256', 'n_rows', 'start', 'end', 'source')}, inputs=[os.path.join(d_, fn)])
    return rec


def _read_reports(ws, well_id: str, recs: List[dict]) -> Tuple[Dict[str, dict], List[str]]:
    """The days of every attached report, later files overriding earlier ones day by day; the notes say what was read."""
    days: Dict[str, dict] = {}
    notes = []
    for r in recs:
        p = os.path.join(ws.well_dir(well_id), 'records', 'fluid', r['file'])
        if not os.path.isfile(p):
            notes.append(f"{r['file']}: the file is missing from the well's records"); continue
        with open(p, 'rb') as f:
            content = f.read()
        if hashlib.sha256(content).hexdigest() != r['sha256']:
            notes.append(f"{r['file']}: the file changed since it was attached (SHA-256 differs); not read"); continue
        try:
            parsed = parse_report(content.decode('utf-8-sig', 'replace'))
        except FluidError as e:
            notes.append(f"{r['file']}: {e}"); continue
        for row in parsed['rows']:
            cur = days.setdefault(row['date'], {'date': row['date'], 'source': r['file']})
            for k, v in row.items():
                if k != 'date' and v is not None:
                    if k == 'volume' and cur.get('volume') is not None and cur.get('source') == r['file']:
                        cur['volume'] += v                                  # two rows of one report on one day add up
                    else:
                        cur[k] = v
            cur['source'] = r['file']
        notes.append(f"{r['file']}: {parsed['n_rows']} day(s), {parsed['start']} to {parsed['end']} ({r['source']})")
    return days, notes


# --------------------------------------------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------------------------------------------
def _density_for(day: str, densities: List[dict], row: dict) -> Tuple[Optional[float], Optional[float], str]:
    """The density that applies to a day: the report's own column first, else the declaration whose period holds the day."""
    if row.get('sg') is not None or row.get('tds') is not None:
        sg = row.get('sg'); tds = row.get('tds')
        if sg is None and tds is not None:
            sg = density_from_tds(tds)['sg']
        if tds is None and sg is not None:
            tds = tds_from_sg(sg)
        return sg, tds, 'the report'
    for d in densities:
        if (d.get('from') is None or d['from'] <= day) and (d.get('to') is None or day <= d['to']):
            return d.get('sg'), d.get('tds_mgl'), d.get('source', 'declared')
    return None, None, ''


def well_ledger(ws, well_id: str, decl: dict) -> dict:
    """One well's days: where each came from, the volume signed by the role, the pressures, the masses."""
    w = _well(ws, well_id)
    role = decl.get('role') or ('injection' if w.get('disposal') else None)
    out: Dict[str, Any] = {'well': well_id, 'display': w.get('display', well_id), 'role': role, 'role_how': ('declared' if decl.get('role') else ('a disposal well by its declaration' if role else 'not declared')),
                           'formation': decl.get('formation'), 'formation_source': decl.get('formation_source'), 'days': [], 'notes': [], 'record': 'NO VOLUME RECORD',
                           'surface': w.get('surface'), 'disposal': w.get('disposal')}
    days: Dict[str, dict] = {}
    if decl.get('reports'):
        days, notes = _read_reports(ws, well_id, decl['reports'])
        out['notes'].extend(notes)
        if days:
            out['record'] = "the operator's report"
    disp = w.get('disposal') or {}
    if not days and (disp.get('channel_rate') or disp.get('channel_pressure')):
        from . import sra as SR
        try:
            dr = SR.daily_records(ws.well_stream(well_id), disp)
        except Exception as e:                                               # a stream that does not read is a note, not a crash
            dr = {'status': 'NOT READ', 'detail': f'{type(e).__name__}: {e}', 'days': []}
        if dr.get('status') == 'RECORDED':
            for r in dr['days']:
                if r.get('status') != 'RECORDED':
                    continue
                days[r['date']] = {'date': r['date'], 'volume': r.get('injection_volume_bbl'), 'p_avg': r.get('avg_surface_injection_pressure_psi'),
                                   'p_max': r.get('max_surface_injection_pressure_psi'), 'bhp': r.get('bhp_psi'), 'source': 'the measured stream'}
            out['record'] = "the well's measured stream (the declared injection channels)"
            out['notes'].append(dr.get('detail', ''))
        else:
            out['notes'].append(f"the measured stream: {dr.get('status')} - {dr.get('detail', '')}")
    if not days:
        if role is None:
            out['notes'].append('no role declared (injection or production), no report attached, no injection channels declared: this well is outside the ledger')
        else:
            out['notes'].append('no report attached and no injection channels declared: the well has days but no volumes')
        return out
    sign = -1.0 if role == 'production' else 1.0
    dens = decl.get('densities', [])
    cum = 0.0; cum_w = 0.0; cum_s = 0.0; n_dens = 0
    for day in sorted(days):
        r = days[day]
        vol = r.get('volume')
        if role == 'production':
            liq = (vol or 0.0) + (r.get('oil') or 0.0)
            vol = liq if (vol is not None or r.get('oil') is not None) else None
        sg, tds, how = _density_for(day, dens, r)
        t_w, t_s = mass_of(vol, sg, tds) if vol is not None else (None, None)
        if vol is not None:
            cum += sign * vol
            if t_w is not None:
                cum_w += sign * t_w; n_dens += 1
            if t_s is not None:
                cum_s += sign * t_s
        out['days'].append({'date': day, 'volume_bbl': (round(sign * vol, 1) if vol is not None else None), 'cum_bbl': round(cum, 1),
                            'p_avg_psi': r.get('p_avg'), 'p_max_psi': r.get('p_max'), 'bhp_psi': r.get('bhp'), 'sg': (round(sg, 4) if sg is not None else None),
                            'tds_mgl': (round(tds, 0) if tds is not None else None), 'density_from': how, 'water_t': (round(sign * t_w, 2) if t_w is not None else None),
                            'solids_t': (round(sign * t_s, 3) if t_s is not None else None), 'gas_mcf': r.get('gas'), 'source': r.get('source')})
    vols = [d_['volume_bbl'] for d_ in out['days'] if d_['volume_bbl'] is not None]
    out.update(n_days=len(out['days']), n_with_volume=len(vols), cum_bbl=round(cum, 1), cum_water_t=(round(cum_w, 1) if n_dens else None), cum_solids_t=(round(cum_s, 2) if n_dens else None),
               days_with_density=n_dens, mean_bbl_per_day=(round(sum(vols) / len(vols), 1) if vols else None), max_bbl_per_day=(round(max(vols, key=abs), 1) if vols else None),
               start=out['days'][0]['date'], end=out['days'][-1]['date'],
               n_bhp=sum(1 for d_ in out['days'] if d_['bhp_psi'] is not None), n_p_avg=sum(1 for d_ in out['days'] if d_['p_avg_psi'] is not None))
    if n_dens == 0:
        out['notes'].append('no density declared for these days: the water and solids columns stay empty (gea fluid --declare --well ... --tds-mgl or --density-sg, with the analysis as source)')
    elif n_dens < len(vols):
        out['notes'].append(f'{len(vols) - n_dens} day(s) with a volume have no density that applies')
    return out


# --------------------------------------------------------------------------------------------------------------
# the material balance
# --------------------------------------------------------------------------------------------------------------
def storage_declared(fm: dict) -> dict:
    """Pore volume and storage from the declaration, with each default named when it stands in."""
    out: Dict[str, Any] = {'status': 'OK', 'defaults': []}
    for k in ('porosity', 'thickness_ft', 'area_acres'):
        if fm.get(k) is None:
            out['status'] = 'NOT DECLARED'
    cw = fm.get('c_water_per_psi'); cf = fm.get('c_pore_per_psi')
    if cw is None:
        cw = C_WATER_PER_PSI; out['defaults'].append(f'water compressibility {C_WATER_PER_PSI:g}/psi (default)')
    if cf is None:
        cf = C_PORE_PER_PSI; out['defaults'].append(f'pore-volume compressibility {C_PORE_PER_PSI:g}/psi (default, consolidated sandstone)')
    out['c_t_per_psi'] = cw + cf
    if out['status'] == 'OK':
        vp = BBL_PER_ACRE_FT * fm['area_acres'] * fm['thickness_ft'] * fm['porosity']
        out.update(pore_volume_bbl=round(vp, 0), storage_bbl_per_psi=round(vp * (cw + cf), 2),
                   how=f"V_p = 7,758 x {fm['area_acres']:g} acres x {fm['thickness_ft']:g} ft x {fm['porosity']:g} = {vp:,.0f} bbl; c_t = {cw:g} + {cf:g} = {cw + cf:g}/psi; storage c_t V_p = {vp * (cw + cf):,.1f} bbl/psi")
    else:
        out['how'] = 'pore volume needs porosity, net thickness and area declared (gea fluid --formation NAME --porosity --thickness-ft --area-acres --source ...)'
    return out


def fit_storage(cum_bbl: np.ndarray, p_psi: np.ndarray) -> dict:
    """Pressure against cumulative net volume, least squares: slope 1/S psi per bbl -> S bbl/psi, intercept the
    pressure at zero net volume, r^2, n. Needs MIN_FIT_DAYS days and a volume range."""
    ok = ~(np.isnan(cum_bbl) | np.isnan(p_psi))
    x = cum_bbl[ok]; y = p_psi[ok]
    if x.size < MIN_FIT_DAYS:
        return {'status': STATUS_NOFIT, 'n': int(x.size), 'why': f'{x.size} day(s) with both a volume and a bottomhole pressure; {MIN_FIT_DAYS} are needed'}
    if np.ptp(x) <= 0:
        return {'status': STATUS_NOFIT, 'n': int(x.size), 'why': 'the cumulative volume does not change over the days with a pressure'}
    A = np.vstack([x, np.ones_like(x)]).T
    (m, b), res, _, _ = np.linalg.lstsq(A, y, rcond=None)
    m, b = float(m), float(b)
    yhat = m * x + b
    ss_res = float(np.sum((y - yhat) ** 2)); ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if m <= 0:
        return {'status': STATUS_NOFIT, 'n': int(x.size), 'slope_psi_per_bbl': float(m), 'r2': round(r2, 3),
                'why': 'the pressure does not rise with the volume kept (slope <= 0): the formation is not storing it as a closed volume would, or the pressures are not the formation\'s'}
    return {'status': 'FITTED', 'n': int(x.size), 'slope_psi_per_bbl': float(m), 'slope_psi_per_mbbl': round(m * 1000.0, 3), 'storage_bbl_per_psi': round(1.0 / m, 2),
            'p_zero_psi': round(float(b), 1), 'r2': round(r2, 3), 'dp_psi': round(float(yhat[-1] - yhat[0]), 1), 'dv_bbl': round(float(x[-1] - x[0]), 1)}


def hall_plot(days: List[dict], p_e_psi: Optional[float]) -> dict:
    """Hall (1963): cumulative (p_wf - p_e) x days against cumulative injected volume; the slope of the first and the
    second half of the record, and what their ratio says. p_wf is the bottomhole pressure when recorded, else the
    average surface pressure (said so); p_e the formation's initial pressure declared, else the first pressure."""
    pts = [(d['cum_bbl'], (d['bhp_psi'] if d['bhp_psi'] is not None else d['p_avg_psi'])) for d in days if d['volume_bbl'] is not None and (d['bhp_psi'] is not None or d['p_avg_psi'] is not None)]
    if len(pts) < MIN_FIT_DAYS:
        return {'status': 'NOT DRAWN', 'why': f'{len(pts)} day(s) with a volume and a pressure; {MIN_FIT_DAYS} are needed'}
    uses_bhp = any(d['bhp_psi'] is not None for d in days)
    pe = p_e_psi if p_e_psi is not None else pts[0][1]
    x = []; hy = []; acc = 0.0
    for cum, p in pts:
        acc += (p - pe) * 1.0                              # one day each
        x.append(cum); hy.append(acc)
    X = np.asarray(x); Y = np.asarray(hy)
    half = len(pts) // 2
    def slope(a, b):
        xs, ys = X[a:b], Y[a:b]
        if xs.size < 3 or np.ptp(xs) <= 0:
            return None
        return float(np.polyfit(xs, ys, 1)[0])
    s1, s2 = slope(0, half), slope(half, len(pts))
    out = {'status': 'DRAWN', 'n': len(pts), 'p_e_psi': round(float(pe), 1), 'p_e_how': 'declared initial pressure' if p_e_psi is not None else 'the first recorded pressure (no initial pressure declared)',
           'pressure': 'bottomhole' if uses_bhp else 'average surface (no bottomhole pressure recorded)', 'points': [[round(float(a), 1), round(float(b), 1)] for a, b in zip(X[:: max(1, len(pts) // 200)], Y[:: max(1, len(pts) // 200)])],
           'slope_first_half_psi_d_per_bbl': (round(s1, 5) if s1 is not None else None), 'slope_second_half_psi_d_per_bbl': (round(s2, 5) if s2 is not None else None)}
    if s1 and s2 and s1 > 0:
        ratio = s2 / s1
        out['slope_ratio'] = round(ratio, 2)
        out['reading'] = ('the slope steepened: more pressure per barrel - plugging of the face, or a filling formation' if ratio > 1.3 else
                          'the slope eased: less pressure per barrel - an opened path (a fracture, a thief zone) or an outflow' if ratio < 0.77 else
                          'the slope holds: the well takes its water as it did')
    else:
        out['reading'] = 'the slopes could not both be drawn'
    return out


def radius_of_investigation_ft(k_md: float, t_hours: float, phi: float, mu_cp: float, ct_per_psi: float) -> float:
    """r_inv = 0.0325 sqrt(k t / (phi mu c_t)), field units (Lee 1982): md, hours, fraction, cp, 1/psi -> ft."""
    return 0.0325 * math.sqrt(k_md * t_hours / (phi * mu_cp * ct_per_psi))


def _e1(x: float) -> float:
    """The exponential integral E1(x) for x > 0: the series for small x, the continued fraction beyond."""
    if x <= 0:
        return float('inf')
    if x < 1.0:
        s = -0.5772156649015329 - math.log(x); term = 1.0; k = 1
        while k < 60:
            term *= -x / k
            s -= term / k
            if abs(term / k) < 1e-15:
                break
            k += 1
        return s
    # Lentz continued fraction
    b = x + 1.0; c = 1e300; d = 1.0 / b; h = d
    for i in range(1, 200):
        a = -i * i; b += 2.0
        d = 1.0 / (a * d + b); c = b + a / c; de = c * d; h *= de
        if abs(de - 1.0) < 1e-14:
            break
    return h * math.exp(-x)


def line_source_dp_psi(q_bbl_d: float, k_md: float, h_ft: float, phi: float, mu_cp: float, ct_per_psi: float, r_ft: float, t_hours: float, B: float = 1.0) -> float:
    """Pressure rise at r after t of steady injection at q, infinite uniform formation (Theis / the line source), field
    units: dp = 70.6 q mu B / (k h) x E1(948 phi mu c_t r^2 / (k t))."""
    if t_hours <= 0 or r_ft <= 0:
        return 0.0
    return 70.6 * q_bbl_d * mu_cp * B / (k_md * h_ft) * _e1(948.0 * phi * mu_cp * ct_per_psi * r_ft * r_ft / (k_md * t_hours))


def swept_radius_ft(volume_bbl: float, h_ft: float, phi: float) -> float:
    """Piston displacement in a cylinder: r = sqrt(V / (pi h phi)), V in ft3."""
    if volume_bbl <= 0:
        return 0.0
    return math.sqrt(volume_bbl * FT3_PER_BBL / (math.pi * h_ft * phi))


def volume_to_reach_bbl(r_ft: float, h_ft: float, phi: float) -> float:
    return math.pi * r_ft * r_ft * h_ft * phi / FT3_PER_BBL


def formation_balance(name: str, fm: dict, wells: List[dict], as_of: Optional[str] = None) -> dict:
    """One formation: its wells' days summed into a ledger, the declared storage, the storage the pressure says, the
    unaccounted volume at the end, and the words."""
    decl = storage_declared(fm)
    out: Dict[str, Any] = {'formation': name, 'declared': fm, 'storage': decl, 'wells': [w['well'] for w in wells], 'days': [], 'lines': []}
    by_day: Dict[str, dict] = {}
    for w in wells:
        for d in w.get('days', []):
            r = by_day.setdefault(d['date'], {'date': d['date'], 'in_bbl': 0.0, 'out_bbl': 0.0, 'water_t': 0.0, 'solids_t': 0.0, 'n_mass': 0, 'bhp': [], 'wells': 0})
            v = d['volume_bbl']
            if v is not None:
                r['wells'] += 1
                if v >= 0:
                    r['in_bbl'] += v
                else:
                    r['out_bbl'] += -v
                if d['water_t'] is not None:
                    r['water_t'] += d['water_t']; r['n_mass'] += 1
                if d['solids_t'] is not None:
                    r['solids_t'] += d['solids_t']
            if d['bhp_psi'] is not None:
                r['bhp'].append(d['bhp_psi'])
    if not by_day:
        out.update(status='NO RECORD', lines=[f'{name}: no well with a volume record is assigned to it'])
        return out
    cum = 0.0; cum_w = 0.0; cum_s = 0.0
    xs = []; ps = []
    for day in sorted(by_day):
        r = by_day[day]
        if as_of and day > as_of:
            break
        net = r['in_bbl'] - r['out_bbl']; cum += net; cum_w += r['water_t']; cum_s += r['solids_t']
        p = (sum(r['bhp']) / len(r['bhp'])) if r['bhp'] else None
        row = {'date': day, 'in_bbl': round(r['in_bbl'], 1), 'out_bbl': round(r['out_bbl'], 1), 'net_bbl': round(net, 1), 'cum_net_bbl': round(cum, 1),
               'cum_water_t': round(cum_w, 1), 'cum_solids_t': round(cum_s, 2), 'bhp_psi': (round(p, 1) if p is not None else None), 'wells': r['wells']}
        if decl['status'] == 'OK':
            row['dp_closed_psi'] = round(cum / decl['storage_bbl_per_psi'], 2)         # the rise a closed, declared volume would show
        out['days'].append(row)
        xs.append(cum); ps.append(p if p is not None else np.nan)
    last = out['days'][-1]
    out.update(status='LEDGER', start=out['days'][0]['date'], end=last['date'], n_days=len(out['days']), cum_in_bbl=round(sum(d['in_bbl'] for d in out['days']), 1),
               cum_out_bbl=round(sum(d['out_bbl'] for d in out['days']), 1), cum_net_bbl=last['cum_net_bbl'], cum_water_t=last['cum_water_t'], cum_solids_t=last['cum_solids_t'])
    fit = fit_storage(np.asarray(xs, dtype=float), np.asarray(ps, dtype=float))
    out['fit'] = fit
    if decl['status'] == 'OK':
        out['pore_volume_fraction'] = round(out['cum_net_bbl'] / decl['pore_volume_bbl'], 4)
        out['dp_closed_psi'] = last['dp_closed_psi']
    verdict = STATUS_NOFIT
    if fit['status'] == 'FITTED' and decl['status'] == 'OK':
        ratio = fit['storage_bbl_per_psi'] / decl['storage_bbl_per_psi']
        out['storage_ratio'] = round(ratio, 2)
        verdict = STATUS_CLOSED if RATIO_LO <= ratio <= RATIO_HI else (STATUS_MORE if ratio > RATIO_HI else STATUS_LESS)
        # the barrels the declared volume cannot hold at the observed rise: cum_net - S_decl x (p_last - p_zero)
        dp_obs = fit['slope_psi_per_bbl'] * (xs[-1] - xs[0])
        held = decl['storage_bbl_per_psi'] * dp_obs
        un = out['cum_net_bbl'] - held
        out['unaccounted_bbl'] = round(un, 0)
        out['held_by_declared_volume_bbl'] = round(held, 0)
        out['observed_rise_psi'] = round(dp_obs, 1)
        if out['cum_net_bbl'] and out.get('cum_solids_t') is not None and last['cum_water_t']:
            frac = un / out['cum_net_bbl']
            out['unaccounted_solids_t'] = round(out['cum_solids_t'] * frac, 1)
            out['unaccounted_water_t'] = round(out['cum_water_t'] * frac, 0)
    out['verdict'] = verdict
    # the words
    L = out['lines']
    L.append(f"{name}: {out['n_days']} day(s) from {out['start']} to {out['end']}, {out['cum_in_bbl']:,.0f} bbl in and {out['cum_out_bbl']:,.0f} bbl out through {len(wells)} well(s); net {out['cum_net_bbl']:,.0f} bbl kept"
             + (f", {out['cum_water_t']:,.0f} t of brine carrying {out['cum_solids_t']:,.0f} t of dissolved solids" if last['cum_water_t'] else ', no density declared so no mass'))
    if decl['status'] == 'OK':
        L.append(f"Declared room: {decl['how']}. The net volume is {out['pore_volume_fraction'] * 100:.2f}% of the pore volume; closed, it would have raised the pressure {out['dp_closed_psi']:,.0f} psi.")
    else:
        L.append(f"Declared room: {decl['how']}.")
    if fit['status'] == 'FITTED':
        L.append(f"The pressure says: {fit['slope_psi_per_mbbl']:g} psi per thousand barrels over {fit['n']} day(s) with a bottomhole pressure (r^2 {fit['r2']:.2f}), a storage of {fit['storage_bbl_per_psi']:,.1f} bbl/psi"
                 + (f" - {out['storage_ratio']:g} x the declared {decl['storage_bbl_per_psi']:,.1f} bbl/psi: {verdict}." if 'storage_ratio' in out else '.'))
        if 'unaccounted_bbl' in out:
            if verdict == STATUS_MORE:
                L.append(f"Of the {out['cum_net_bbl']:,.0f} bbl kept, the declared volume holds {out['held_by_declared_volume_bbl']:,.0f} at the observed rise of {out['observed_rise_psi']:,.0f} psi; "
                         f"{out['unaccounted_bbl']:,.0f} bbl" + (f" ({out.get('unaccounted_water_t', 0):,.0f} t of brine, {out.get('unaccounted_solids_t', 0):,.0f} t of solids)" if 'unaccounted_solids_t' in out else '')
                         + " are UNACCOUNTED: they found room the declaration does not have - an aquifer, leak-off through a fault or the confining layer, or a larger connected volume. That is the outflow to look for.")
            elif verdict == STATUS_LESS:
                L.append(f"The pressure rose more than the declared volume allows for {out['cum_net_bbl']:,.0f} bbl: the connected volume is smaller than declared, or the well is poorly connected to it (skin, a compartment). "
                         f"At the observed rise the declared volume would have taken {out['held_by_declared_volume_bbl']:,.0f} bbl.")
            else:
                L.append(f"The formation behaves as the declared closed volume within a factor of two; {abs(out['unaccounted_bbl']):,.0f} bbl of the balance is within that tolerance.")
    else:
        L.append(f"The pressure says nothing yet: {fit.get('why', fit['status'])}.")
    return out


# --------------------------------------------------------------------------------------------------------------
# the neighbours
# --------------------------------------------------------------------------------------------------------------
def neighbours(inj: dict, others: List[dict], fm: Optional[dict], decl_storage: dict) -> dict:
    """Every other well with a surface position, by distance from the injection well, with the two projections."""
    surf = inj.get('surface') or {}
    out: Dict[str, Any] = {'well': inj['well'], 'rows': [], 'status': 'OK', 'notes': []}
    if surf.get('lat') is None:
        out.update(status='NO SURFACE POSITION', notes=['the injection well has no declared surface position (declare it in the wells table of the declarations sheet)'])
        return out
    have_k = fm is not None and fm.get('k_md') is not None and fm.get('thickness_ft') is not None and fm.get('porosity') is not None
    mu = (fm or {}).get('mu_cp') or MU_WATER_CP
    ct = decl_storage.get('c_t_per_psi', C_WATER_PER_PSI + C_PORE_PER_PSI)
    days = inj.get('n_with_volume') or 0
    q = inj.get('mean_bbl_per_day') or 0.0
    cum = inj.get('cum_bbl') or 0.0
    out['projection'] = {'k_md': (fm or {}).get('k_md'), 'h_ft': (fm or {}).get('thickness_ft'), 'porosity': (fm or {}).get('porosity'), 'mu_cp': mu, 'c_t_per_psi': ct,
                         'q_bbl_d': q, 'days': days, 'cum_bbl': cum, 'label': 'SIMULATION: a line source in an infinite, uniform, single-phase formation with the declared k, h, porosity, viscosity and compressibility, '
                                                                                                          'at the well\'s mean rate; piston displacement for the swept radius. A projection, not a measurement.'}
    if have_k and days:
        out['r_inv_ft'] = round(radius_of_investigation_ft(fm['k_md'], days * 24.0, fm['porosity'], mu, ct), 0)
        out['swept_ft'] = round(swept_radius_ft(max(cum, 0.0), fm['thickness_ft'], fm['porosity']), 0)
    inj_sg = None
    for d in reversed(inj.get('days', [])):
        if d.get('sg') is not None:
            inj_sg = d['sg']; break
    fw_sg = (fm or {}).get('water_sg')
    if inj_sg is not None and fw_sg is not None:
        out['density_difference'] = {'injected_sg': inj_sg, 'formation_water_sg': fw_sg, 'delta_sg': round(inj_sg - fw_sg, 4),
                                     'reading': ('the injected water is denser than the formation water: the front under-rides, running ahead along the base' if inj_sg - fw_sg > 0.005 else
                                                 'the injected water is lighter than the formation water: the front over-rides, running ahead along the top' if fw_sg - inj_sg > 0.005 else
                                                 'the densities match within 0.005: the front stays close to a piston')}
    for o in others:
        s2 = o.get('surface') or {}
        if o['well'] == inj['well'] or s2.get('lat') is None:
            continue
        try:
            g = G.geodesic_m(float(surf['lat']), float(surf['lon']), float(s2['lat']), float(s2['lon']))
            dist_m = g['distance_m'] if isinstance(g, dict) else float(g)
        except Exception as e:
            out['notes'].append(f"{o['well']}: distance not computed ({e})"); continue
        if (surf.get('datum') or 'WGS84') != (s2.get('datum') or 'WGS84'):
            out['notes'].append(f"{o['well']}: the two surface positions are on different datums ({surf.get('datum')} vs {s2.get('datum')}); the distance is on the first")
        row: Dict[str, Any] = {'well': o['well'], 'display': o.get('display', o['well']), 'role': o.get('role'), 'formation': o.get('formation'), 'distance_ft': round(dist_m / 0.3048, 0), 'distance_km': round(dist_m / 1000.0, 3),
                               'same_formation': (o.get('formation') == inj.get('formation')) if inj.get('formation') else None}
        if row['same_formation'] is False:
            row['front'] = 'OTHER FORMATION'
            row['swept'] = 'in another formation: no projection - a path between formations is a fault or a leaking confining layer, which the map must show'
        elif have_k and days:
            r = dist_m / 0.3048
            t_arr_h = (r / 0.0325) ** 2 * fm['porosity'] * mu * ct / fm['k_md']
            row['front_arrival_day'] = round(t_arr_h / 24.0, 1)
            row['front'] = 'ARRIVED' if t_arr_h <= days * 24.0 else 'PROJECTED'
            row['dp_now_psi'] = round(line_source_dp_psi(q, fm['k_md'], fm['thickness_ft'], fm['porosity'], mu, ct, r, days * 24.0), 2) if q > 0 else None
            row['dp_1yr_psi'] = round(line_source_dp_psi(q, fm['k_md'], fm['thickness_ft'], fm['porosity'], mu, ct, r, (days + 365) * 24.0), 2) if q > 0 else None
            vreach = volume_to_reach_bbl(r, fm['thickness_ft'], fm['porosity'])
            row['volume_to_reach_bbl'] = round(vreach, 0)
            row['swept'] = 'INSIDE THE SWEPT RADIUS' if cum >= vreach else f'{(vreach - cum) / q / 365.0:.1f} more years at the mean rate' if q > 0 else 'not at this rate'
        out['rows'].append(row)
    out['rows'].sort(key=lambda r: r['distance_ft'])
    if not have_k:
        out['notes'].append('no permeability, thickness and porosity declared for the formation: distances only, no front or pressure projection (gea fluid --formation NAME --k-md --thickness-ft --porosity ...)')
    return out


# --------------------------------------------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------------------------------------------
def page(ws, as_of: Optional[str] = None) -> dict:
    d = load(ws)
    wells = []
    for w in ws.wells():
        decl = d['wells'].get(w['id'], {})
        if not decl and not w.get('disposal'):
            continue
        wells.append(well_ledger(ws, w['id'], decl))
    # the interval statement beside each assignment
    for wl in wells:
        if wl.get('disposal'):
            try:
                iv = ws.well_interval(wl['well'], actor='fluid', write=False)
                wl['interval'] = {'status': iv.get('status'), 'tier': (iv.get('tier') or {}).get('status'), 'lines': iv.get('lines', [])[:4]}
            except Exception as e:
                wl['interval'] = {'status': 'NOT READ', 'lines': [str(e)]}
    by_fm: Dict[str, List[dict]] = {}
    named = set()
    for wl in wells:
        if wl.get('formation'):
            named.add(wl['formation'])
            if wl.get('days'):
                by_fm.setdefault(wl['formation'], []).append(wl)
    formations = []
    for name in sorted(named | set(d['formations'])):
        formations.append(formation_balance(name, d['formations'].get(name, {}), by_fm.get(name, []), as_of))
    for wl in wells:
        if wl.get('role') == 'injection' and wl.get('days'):
            fm = d['formations'].get(wl.get('formation') or '')
            wl['hall'] = hall_plot(wl['days'], (fm or {}).get('p_initial_psi'))
            wl['neighbours'] = neighbours(wl, wells, fm, storage_declared(fm or {}))
    unassigned = [wl['well'] for wl in wells if wl.get('days') and not wl.get('formation')]
    out = {'status': 'OK' if wells else 'EMPTY', 'wells': wells, 'formations': formations, 'unassigned': unassigned, 'as_of': as_of, 'generated_utc': _utc(),
           'constants': {'bbl_per_acre_ft': BBL_PER_ACRE_FT, 'c_water_default_per_psi': C_WATER_PER_PSI, 'c_pore_default_per_psi': C_PORE_PER_PSI, 'mu_default_cp': MU_WATER_CP},
           'lines': []}
    if not wells:
        out['lines'].append('No well is in the ledger: declare a role and a formation for the disposal well and its neighbours (gea fluid --declare --well ID --role injection --formation NAME --source ...), '
                            'then attach each well\'s injection or production report (--attach report.csv) or declare the disposal well\'s injection channels (gea workspace --action set-well --channel-rate ...).')
    for wl in wells:
        out['lines'].append(f"{wl['display']}: {wl['role'] or 'no role'}" + (f" {'from' if wl['role'] == 'production' else 'into'} {wl['formation']}" if wl.get('formation') else ', no formation assigned')
                            + (f"; {wl['n_with_volume']} day(s) with a volume from {wl['record']}, {wl['cum_bbl']:,.0f} bbl net ({wl['start']} to {wl['end']})" if wl.get('days') else f"; {wl['record']}")
                            + (f", {wl['cum_water_t']:,.0f} t of brine, {wl['cum_solids_t']:,.1f} t of solids" if wl.get('cum_water_t') else ''))
    for fm in formations:
        out['lines'].extend(fm['lines'])
    if unassigned:
        out['lines'].append(f"Not in any balance (no formation declared): {', '.join(unassigned)}.")
    out['lines'].append('Distances are surface to surface on the declared datum; the front and pressure projections are SIMULATION on the declared formation and the mean rate; the measured volumes are the record, the inferred locations are not.')
    return out


def text(p: dict) -> str:
    lines = ['Fluid ledger and material balance', '=' * 36]
    lines.extend(p['lines'])
    for wl in p['wells']:
        if wl.get('hall') and wl['hall'].get('status') == 'DRAWN':
            h = wl['hall']
            lines.append(f"  Hall plot, {wl['display']}: {h['n']} day(s), p_e {h['p_e_psi']:g} psi ({h['p_e_how']}), {h['pressure']} pressure; {h['reading']}"
                         + (f" (slope ratio {h['slope_ratio']})" if h.get('slope_ratio') is not None else ''))
        nb = wl.get('neighbours')
        if nb and nb.get('rows'):
            lines.append(f"  Neighbours of {wl['display']}" + (f" (front at {nb['r_inv_ft']:,.0f} ft, swept {nb['swept_ft']:,.0f} ft)" if nb.get('r_inv_ft') is not None else '') + ':')
            for r in nb['rows']:
                lines.append(f"    {r['display']}: {r['distance_ft']:,.0f} ft" + (f", {r['role']}" if r.get('role') else '') + (f" in {r['formation']}" if r.get('formation') else '')
                             + (f"; front {r['front']} (day {r['front_arrival_day']:g}), dp now {r['dp_now_psi']} psi, in a year {r['dp_1yr_psi']} psi; {r['swept']}" if 'front_arrival_day' in r else (f"; {r['swept']}" if 'swept' in r else '')))
            if nb.get('density_difference'):
                lines.append(f"    {nb['density_difference']['reading']} (sg {nb['density_difference']['injected_sg']} vs {nb['density_difference']['formation_water_sg']})")
    return '\n'.join(lines)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    """The page written under reports/fluid/: fluid_balance.json and .md, and the ledger CSVs."""
    p = page(ws)
    d = ws.dir('reports', 'fluid')
    p['program_version'] = program_version
    with open(os.path.join(d, 'fluid_balance.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    md = ['# Fluid ledger and material balance', '', f"Generated {p['generated_utc']} by GEA-Program {program_version}.", '']
    md.extend(f'- {l}' for l in p['lines'])
    md.append('')
    with open(os.path.join(d, 'fluid_balance.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n' + text(p) + '\n')
    files = ['fluid_balance.json', 'fluid_balance.md']
    for fm in p['formations']:
        if fm.get('days'):
            fn = 'ledger_' + re.sub(r'[^A-Za-z0-9_-]+', '_', fm['formation']) + '.csv'
            with open(os.path.join(d, fn), 'w', encoding='utf-8', newline='') as f:
                wr = csv.DictWriter(f, fieldnames=list(fm['days'][0].keys()))
                wr.writeheader(); wr.writerows(fm['days'])
            files.append(fn)
    for wl in p['wells']:
        if wl.get('days'):
            fn = 'well_' + re.sub(r'[^A-Za-z0-9_-]+', '_', wl['well']) + '.csv'
            with open(os.path.join(d, fn), 'w', encoding='utf-8', newline='') as f:
                wr = csv.DictWriter(f, fieldnames=list(wl['days'][0].keys()))
                wr.writeheader(); wr.writerows(wl['days'])
            files.append(fn)
    ws.audit(actor, 'fluid.report', {'files': files, 'wells': len(p['wells']), 'formations': [(fm['formation'], fm.get('verdict')) for fm in p['formations']]})
    return {'folder': d, 'files': files, 'page': p}


# --------------------------------------------------------------------------------------------------------------
# the labelled scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_scene(days: int = 240, leak_fraction: float = 0.0, seed: int = 11, storage_bbl_per_psi: float = 600.0, q_bbl_d: float = 8000.0,
                    p0_psi: float = 3000.0, noise_psi: float = 3.0) -> dict:
    """A closed tank (or one that loses leak_fraction of each day's water): the report rows an operator would file,
    the truth beside them. SIMULATION."""
    rng = np.random.default_rng(seed)
    rows = []; kept = 0.0
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(days):
        q = q_bbl_d * (1.0 + 0.1 * math.sin(i / 17.0)) + rng.normal(0, 150.0)
        kept += q * (1.0 - leak_fraction)
        p = p0_psi + kept / storage_bbl_per_psi + rng.normal(0, noise_psi)
        rows.append({'date': (t0 + timedelta(days=i)).strftime('%Y-%m-%d'), 'injection_volume_bbl': round(q, 1), 'bhp_psi': round(p, 1),
                     'avg_surface_injection_pressure_psi': round(p - 2600.0, 1), 'tds_mgl': 120000})
    csv_text = 'date,injection_volume_bbl,avg_surface_injection_pressure_psi,bhp_psi,tds_mgl\n' + '\n'.join(
        f"{r['date']},{r['injection_volume_bbl']},{r['avg_surface_injection_pressure_psi']},{r['bhp_psi']},{r['tds_mgl']}" for r in rows) + '\n'
    cum = sum(r['injection_volume_bbl'] for r in rows)
    return {'csv': csv_text, 'rows': rows, 'truth': {'storage_bbl_per_psi': storage_bbl_per_psi, 'leak_fraction': leak_fraction, 'cum_bbl': round(cum, 1),
                                                     'leaked_bbl': round(cum * leak_fraction, 1), 'p0_psi': p0_psi}, 'label': 'SIMULATION'}


def selftest() -> dict:
    """Run on labelled scenes: the storage the fit recovers from a closed tank, the leak it names on a leaking one, the
    density correlation, the front and Hall arithmetic."""
    checks = []
    d = density_from_tds(100000.0)
    checks.append({'check': 'McCain density of 100,000 mg/L brine', 'value': d['sg'], 'pass': 1.060 <= d['sg'] <= 1.075})
    rt = tds_from_sg(d['sg'])
    checks.append({'check': 'TDS from sg round-trips', 'value': rt, 'pass': abs(rt - 100000.0) < 500.0})
    # the closed tank, through the ledger arithmetic without a workspace
    sc = synthetic_scene()
    rows = parse_report(sc['csv'])['rows']
    days = []; cum = 0.0
    for r in rows:
        cum += r['volume']; t_w, t_s = mass_of(r['volume'], density_from_tds(r['tds'])['sg'], r['tds'])
        days.append({'date': r['date'], 'volume_bbl': r['volume'], 'cum_bbl': cum, 'p_avg_psi': r['p_avg'], 'p_max_psi': None, 'bhp_psi': r['bhp'], 'sg': None, 'tds_mgl': r['tds'], 'water_t': t_w, 'solids_t': t_s})
    # a declaration that matches the truth: S = c_t V_p with c_t 7e-6 -> V_p = 600/7e-6 bbl = 85.7 MMbbl; area from h 100 ft, phi 0.12
    vp = sc['truth']['storage_bbl_per_psi'] / (C_WATER_PER_PSI + C_PORE_PER_PSI)
    fm = {'porosity': 0.12, 'thickness_ft': 100.0, 'area_acres': vp / (BBL_PER_ACRE_FT * 100.0 * 0.12), 'k_md': 50.0, 'mu_cp': 0.6, 'p_initial_psi': 3000.0, 'water_tds_mgl': 60000.0, 'water_sg': 1.041}
    fb = formation_balance('Tank', fm, [{'well': 'INJ-1', 'days': days}])
    fit = fb['fit']
    checks.append({'check': 'closed tank: storage fitted within 5% of the truth', 'value': fit.get('storage_bbl_per_psi'), 'truth': sc['truth']['storage_bbl_per_psi'],
                   'pass': fit['status'] == 'FITTED' and abs(fit['storage_bbl_per_psi'] / sc['truth']['storage_bbl_per_psi'] - 1.0) < 0.05})
    checks.append({'check': 'closed tank: verdict CLOSED AS DECLARED', 'value': fb['verdict'], 'pass': fb['verdict'] == STATUS_CLOSED})
    checks.append({'check': 'closed tank: solids mass follows the TDS', 'value': fb['cum_solids_t'], 'pass': abs(fb['cum_solids_t'] - fb['cum_net_bbl'] * M3_PER_BBL * 120000.0 / 1e6) < 1.0})
    # the leaking tank: six of every ten barrels leave; the pressure says two and a half times the room
    sl = synthetic_scene(leak_fraction=0.6, seed=5)
    rows = parse_report(sl['csv'])['rows']
    days = []; cum = 0.0
    for r in rows:
        cum += r['volume']
        days.append({'date': r['date'], 'volume_bbl': r['volume'], 'cum_bbl': cum, 'p_avg_psi': r['p_avg'], 'p_max_psi': None, 'bhp_psi': r['bhp'], 'sg': None, 'tds_mgl': None, 'water_t': None, 'solids_t': None})
    fl = formation_balance('Leaky', fm, [{'well': 'INJ-1', 'days': days}])
    checks.append({'check': 'leaking tank: verdict MORE ROOM THAN DECLARED', 'value': fl['verdict'], 'ratio': fl.get('storage_ratio'), 'pass': fl['verdict'] == STATUS_MORE})
    checks.append({'check': 'leaking tank: unaccounted volume within 10% of the leak', 'value': fl.get('unaccounted_bbl'), 'truth': sl['truth']['leaked_bbl'],
                   'pass': fl.get('unaccounted_bbl') is not None and abs(fl['unaccounted_bbl'] / sl['truth']['leaked_bbl'] - 1.0) < 0.10})
    hp = hall_plot(days, 3000.0)
    # a filling tank's Hall slope steepens: (p_wf - p_e) grows with every barrel kept
    checks.append({'check': 'Hall plot drawn, slope steepens on a filling tank', 'value': hp.get('slope_ratio'), 'pass': hp['status'] == 'DRAWN' and hp.get('slope_ratio') is not None and hp['slope_ratio'] > 1.3})
    # the front: r_inv at 1 day for k 50 md, phi 0.12, mu 0.6, ct 7e-6 = 0.0325 sqrt(50 x 24 / (0.12 x 0.6 x 7e-6)) = 1585 ft
    r1 = radius_of_investigation_ft(50.0, 24.0, 0.12, 0.6, 7e-6)
    checks.append({'check': 'radius of investigation, 1 day', 'value': round(r1, 0), 'pass': abs(r1 - 1585.0) < 5.0})
    dp = line_source_dp_psi(8000.0, 50.0, 100.0, 0.12, 0.6, 7e-6, 2000.0, 240 * 24.0)
    e1 = _e1(1.0)
    checks.append({'check': 'E1(1) = 0.21938', 'value': round(e1, 5), 'pass': abs(e1 - 0.219384) < 1e-5})
    checks.append({'check': 'line-source dp at 2,000 ft after 240 days is positive and finite', 'value': round(dp, 2), 'pass': 0.0 < dp < 5000.0})
    sw = swept_radius_ft(1.0e6, 100.0, 0.12)
    checks.append({'check': 'swept radius of 1 MMbbl in 100 ft at 12% porosity', 'value': round(sw, 0), 'pass': abs(sw - math.sqrt(1.0e6 * FT3_PER_BBL / (math.pi * 12.0))) < 1.0})
    # a report with no date column is not accepted
    try:
        parse_report('volume_bbl,bhp_psi\n100,3000\n'); bad = False
    except FluidError:
        bad = True
    checks.append({'check': 'a report without a date column is not accepted', 'pass': bad})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
