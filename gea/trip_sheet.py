# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Trip sheet: the mud the hole took or gave on every trip, against the pipe that was moved.

Pulling pipe takes steel out of the hole, and the hole must be given that volume of mud to stay full; running pipe
puts steel in, and the hole gives the same volume back. A crew keeps a trip sheet: stand by stand, the mud measured
at surface beside the mud the pipe accounts for. A hole that takes less than the steel pulled is being filled by
something else - the first sign of a swabbed kick; one that takes more, or gives back less, is losing mud.

This module writes that sheet from the drilling record:

- the trips are the rig-time page's own (`rig_state`): each run of tripping and of the slips between its stands,
  the pumps off, that moves the bit 1,000 ft or more;
- the volume is read from the trip tank where the record carries one that moves with the pipe, and otherwise from
  the pits - many rigs fill the hole from the active system, and a trip tank whose sensor reads nothing is common in
  a recorder's export. Which one each trip was read from is said with it. Where neither moved with the pipe the trip
  is NOT READ: the hole was filled from a tank this record does not carry, and a blank is not a short fill;
- only the change while the pipe was being handled is counted (moving, or in the slips with the pumps off). A
  change of STEP_BBL or more inside half a minute is mud moved at surface - a tank refilled or emptied - and is taken
  out and reported; the change during a break inside the trip (circulating, filling pipe) is reported and not
  counted. A trip in which more was moved at surface than the pipe accounts for is not judged;
- what the pipe accounts for is computed from the pipe the operator declares for the well - its outside and inside
  diameters and, where known, its weight with tool joints - and how it is tripped: pulled dry (the steel alone) or
  wet (the steel and what the pipe holds), run open (the pipe fills: the steel alone) or closed against a float
  (the steel and the pipe's bore). Steel is taken at 2,748 lb a barrel. With no pipe declared, each trip is set
  against the well's own other trips in the same direction read from the same tank: their median barrels per 100 ft
  and their scatter - the well against itself, which finds the trip that differs and cannot find a pipe that is
  wrong on every trip;
- a trip departs when the measured volume differs from the expected by more than the tolerance: with a pipe, 5 bbl
  or a quarter of the expected, whichever is more; against the well's own trips, 5 bbl or three scatters. Pulling
  out: SHORT FILL (the hole took less than the pipe pulled) or TOOK MORE (losses). Running in: GAVE MORE (more came
  back than the pipe run) or SHORT RETURN (losses). Within the trip, each 500 ft is set against the same rate, and
  the stretch where most of the departure lies is named.

What it will not call a measurement: a kick or a loss - a departure is a place in the record to look at, and the
flow check, the daily report and the driller's own sheet say what it was; the pit volume as the hole's alone - mud
is mixed, moved and lost at surface during a trip, and only the fast moves are taken out; the pipe - it is the
operator's declaration, and a string of several sizes is not one pipe (the heavy-weight pipe and the collars come
through the table last and displace more: the last stands of a trip out and the first of a trip in read high); a
trip read from the pits against a trip read from a trip tank - they are not compared with one another.
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

STEEL_LB_PER_BBL = 2748.0        # steel at 489.5 lb/ft3
BBL_PER_FT_IN2 = 1.0 / 1029.4    # barrels per foot of a cylinder one inch across, squared
STEP_BBL = 5.0                   # a change this large inside STEP_S is mud moved at surface
STEP_S = 30.0
MIN_RATE = 0.15                  # bbl per 100 ft: under this, the volume did not move with the pipe
INTERVAL_FT = 500.0
FINGERPRINT_MIN = 4              # like trips needed before a trip is set against the well's own
MIN_BBL = 5.0
SHARE = 0.25
SIGMA = 3.0
SCATTER_FLOOR = 0.05             # bbl per 100 ft
SPREAD_MAX = 0.6                 # like trips whose scatter is more than this share of their median do not agree: no basis
READ_SHARE = 0.5                 # a trip is judged when at least this share of its footage could be read
SMOOTH_S = 20.0                  # the volume is read through a running median this long: a pit sloshes
CURVE_POINTS = 120
TANK_NAMES = ('trip tank mud volume', 'trip tank volume', 'trip volume active', 'trip tank 1 volume', 'trip tank')
PIT_NAMES = ('total mud volume', 'pit volume active', 'active pit volume', 'pit total', 'pvt total mud volume', 'total active volume')
PULL = {'dry': 'pulled dry: the steel alone', 'wet': 'pulled wet: the steel and what the pipe holds'}
RUN = {'open': 'run open, the pipe filling: the steel alone', 'closed': 'run closed against a float: the steel and the bore'}
STATUS = {'AS EXPECTED': 'the mud at surface is what the pipe accounts for',
          'SHORT FILL': 'pulling out, the hole took less mud than the pipe pulled: something else is filling it',
          'TOOK MORE': 'pulling out, the hole took more mud than the pipe pulled: losses',
          'GAVE MORE': 'running in, more mud came back than the pipe run: the hole is giving',
          'SHORT RETURN': 'running in, less mud came back than the pipe run: losses',
          'NOT READ': 'neither the trip tank nor the pits moved with the pipe: the hole was filled from a tank this record does not carry',
          'TRANSFER': 'more mud was moved at surface inside the trip than the pipe accounts for: not judged',
          'LITTLE READ': 'less than half of the trip was run with the pumps off and no mud being moved at surface: not judged',
          'NO BASIS': 'no pipe is declared and the well has too few like trips to set this one against'}
DEPARTED = ('SHORT FILL', 'TOOK MORE', 'GAVE MORE', 'SHORT RETURN')
NOT_A_MEASUREMENT = [
    'a kick or a loss: a departure is a place in the record to look at; the flow check, the daily report and the driller\'s own trip sheet say what it was',
    'the pit volume as the hole\'s alone: mud is mixed, moved and lost at surface during a trip, and only a move of 5 bbl inside half a minute is taken out',
    'the pipe: it is the operator\'s declaration; a string of several sizes is not one pipe, and the heavy pipe and the collars displace more as they come through the table',
    'the well against its own trips as a check of the pipe: it finds the trip that differs from the others and cannot find what is wrong on every trip',
    'a trip marked NOT READ as a hole that took nothing: the record carries no volume that moved with the pipe',
    'a trip read from the pits beside one read from a trip tank: they are not compared with one another',
]
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


class TripSheetError(ValueError):
    pass


def _utc(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws) -> str:
    return os.path.join(ws.path, 'records', 'trip_sheet.json')


def settings(ws) -> dict:
    try:
        with open(_path(ws), encoding='utf-8') as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault('pipes', {})
    return s


def _save(ws, s: dict) -> None:
    os.makedirs(os.path.dirname(_path(ws)), exist_ok=True)
    tmp = _path(ws) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, _path(ws))


# ---------------------------------------------------------------------------------------------------------------
# the pipe
# ---------------------------------------------------------------------------------------------------------------
def displacement(pipe: dict) -> dict:
    """What a foot of the declared pipe displaces, in barrels: the steel (from its weight with tool joints where
    given, else from its body's diameters), the bore, and both; and from them the volume a trip of 100 ft moves at
    surface - negative pulling out (the tank falls), positive running in."""
    od, idm = float(pipe['od_in']), float(pipe['id_in'])
    wt = pipe.get('weight_lb_ft')
    steel = float(wt) / STEEL_LB_PER_BBL if wt else (od * od - idm * idm) * BBL_PER_FT_IN2
    bore = idm * idm * BBL_PER_FT_IN2
    out = -(steel + (bore if pipe.get('pull') == 'wet' else 0.0))
    inn = steel + (bore if pipe.get('run') == 'closed' else 0.0)
    return {'steel_bbl_ft': steel, 'bore_bbl_ft': bore, 'out_bbl_100ft': out * 100.0, 'in_bbl_100ft': inn * 100.0,
            'steel_from': 'its weight with tool joints' if wt else 'its body\'s diameters (tool joints not counted: a few percent more)'}


def pipe_record(well_id: str, od_in, id_in, source: str, weight_lb_ft=None, pull: str = 'dry', run: str = 'open') -> dict:
    try:
        od, idm = float(od_in), float(id_in)
        wt = float(weight_lb_ft) if weight_lb_ft not in (None, '') else None
    except (TypeError, ValueError):
        raise TripSheetError('the diameters and the weight are numbers: inches and pounds a foot')
    if not (2.0 <= od <= 8.0):
        raise TripSheetError('the outside diameter is between 2 and 8 inches')
    if not (1.0 <= idm < od):
        raise TripSheetError('the inside diameter is an inch or more and less than the outside diameter')
    body = (od * od - idm * idm) * BBL_PER_FT_IN2 * STEEL_LB_PER_BBL
    if wt is not None and not (0.9 * body <= wt <= 1.6 * body):
        raise TripSheetError(f'a pipe of these diameters weighs {body:.1f} lb/ft in its body; a weight with tool joints is between {0.9 * body:.1f} and {1.6 * body:.1f} lb/ft')
    if pull not in PULL:
        raise TripSheetError('pulled dry or wet: ' + ', '.join(PULL))
    if run not in RUN:
        raise TripSheetError('run open or closed: ' + ', '.join(RUN))
    if not str(source or '').strip():
        raise TripSheetError('say where the pipe\'s figures came from: the tally, the pipe\'s data sheet')
    return {'well': well_id, 'od_in': round(od, 4), 'id_in': round(idm, 4), 'weight_lb_ft': (round(wt, 3) if wt is not None else None), 'pull': pull, 'run': run, 'source': str(source).strip()}


def declare_pipe(ws, actor: str, well_id: str, od_in, id_in, source: str, weight_lb_ft=None, pull: str = 'dry', run: str = 'open') -> dict:
    if well_id not in [w_['id'] for w_ in ws.wells()]:
        raise KeyError(f'no well {well_id} in this workspace')
    rec = pipe_record(well_id, od_in, id_in, source, weight_lb_ft, pull, run)
    rec['declared_utc'] = _utc(); rec['by'] = actor
    with _LOCK:
        s = settings(ws)
        s['pipes'][well_id] = rec
        _save(ws, s)
        _MEMO.clear()
    ws.audit(actor, 'trip_sheet.pipe', {k: rec[k] for k in ('well', 'od_in', 'id_in', 'weight_lb_ft', 'pull', 'run', 'source')})
    return rec


def remove_pipe(ws, actor: str, well_id: str) -> dict:
    with _LOCK:
        s = settings(ws)
        if well_id not in s['pipes']:
            raise KeyError(f'no pipe is declared for {well_id}')
        rec = s['pipes'].pop(well_id)
        _save(ws, s)
        _MEMO.clear()
    ws.audit(actor, 'trip_sheet.pipe_removed', {'well': well_id})
    return rec


# ---------------------------------------------------------------------------------------------------------------
# the reading, on arrays
# ---------------------------------------------------------------------------------------------------------------
def _filled(v: np.ndarray) -> np.ndarray:
    """A volume with its blanks carried forward from the last reading (and the first reading carried back)."""
    v = np.asarray(v, dtype=np.float64)
    ok = np.isfinite(v)
    if not ok.any():
        return v
    idx = np.where(ok, np.arange(len(v)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = v[idx]
    out[:int(np.argmax(ok))] = v[int(np.argmax(ok))]
    return out


def read_volume(t: np.ndarray, v: np.ndarray, handling: np.ndarray, moved_ft: Optional[np.ndarray] = None) -> dict:
    """One trip's volume, sample by sample: the change kept (the pipe being handled, no fast move), the change taken
    out as mud moved at surface (STEP_BBL or more inside STEP_S), and the change during the breaks. With the footage
    the pipe moved in each sample, the footage the kept change belongs to - a stand run while a tank was being
    filled is not read, and is not expected of the reading either."""
    v = _filled(v)
    n = len(v)
    if n < 2 or not np.isfinite(v).any():
        return {'ok': False}
    dt = float(np.median(np.diff(t))) if n > 2 else 1.0
    k = max(1, int(SMOOTH_S / max(dt, 0.5))) | 1
    if k >= 3 and n > k:
        pad = np.concatenate([np.full(k // 2, v[0]), v, np.full(k // 2, v[-1])])
        v = np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)
    d = np.diff(v, prepend=v[0])
    cs = np.cumsum(d)
    j = np.searchsorted(t, t - STEP_S, side='left')              # the first sample inside the last half minute
    win = cs - np.where(j > 0, cs[np.maximum(j - 1, 0)], 0.0)
    moved = np.zeros(n, dtype=bool)
    for i in np.flatnonzero(np.abs(win) >= STEP_BBL):
        moved[max(int(j[i]), 0):i + 1] = True
    keep = handling & ~moved
    kept = np.where(keep, d, 0.0)
    ft = np.where(keep, moved_ft, 0.0) if moved_ft is not None else np.zeros(n)
    return {'ok': True, 'kept': kept, 'kept_ft': ft, 'read_ft': float(ft.sum()), 'measured': float(kept.sum()), 'transfers': float(np.abs(d[moved]).sum()), 'transfers_net': float(d[moved].sum()),
            'breaks': float(d[~handling & ~moved].sum()), 'span': float(np.nanmax(v) - np.nanmin(v))}


def _mad_sigma(x: List[float]) -> Tuple[float, float]:
    a = np.asarray(x, dtype=np.float64)
    m = float(np.median(a))
    return m, max(SCATTER_FLOOR, 1.4826 * float(np.median(np.abs(a - m))))


def analyse_arrays(t: np.ndarray, bit: np.ndarray, state: np.ndarray, trips: List[dict], volumes: Dict[str, Optional[np.ndarray]], pipe: Optional[dict] = None) -> dict:
    """Every trip of a record read: `volumes` holds the trip tank's and the pits' arrays (either may be None), `state`
    the rig-time page's state of each sample, `trips` its trips."""
    from . import rig_state as RS
    t = np.asarray(t, dtype=np.float64)
    b = np.nan_to_num(np.asarray(bit, dtype=np.float64))
    handling_all = np.isin(state, (RS.TIN, RS.TOUT, RS.SLIPS))
    disp = displacement(pipe) if pipe else None
    out: List[dict] = []
    for n_, tr in enumerate(trips, 1):
        ia = int(np.searchsorted(t, tr['t_start'], side='left')); iz = int(np.searchsorted(t, tr['t_end'], side='right')) - 1
        row = {'n': n_, 'direction': tr['direction'], 'start': tr.get('start'), 'end': tr.get('end'), 't_start': tr['t_start'], 't_end': tr['t_end'],
               'from_ft': tr['from_ft'], 'to_ft': tr['to_ft'], 'feet': float(tr['feet']), 'hours': tr.get('elapsed_hours'), 'source': None, 'measured_bbl': None, 'rate_bbl_100ft': None,
               'transfers_bbl': 0.0, 'breaks_bbl': 0.0, 'expected_bbl': None, 'basis': None, 'departure_bbl': None, 'tolerance_bbl': None, 'status': 'NOT READ', 'intervals': [], 'worst': None,
               'curve': None}
        if iz - ia < 3 or row['feet'] <= 0:
            out.append(row); continue
        ts, hs = t[ia:iz + 1], handling_all[ia:iz + 1]
        gone_ = np.maximum.accumulate(np.abs(b[ia:iz + 1] - b[ia]))            # the trip's own progress: pipe worked up and down is not footage
        mft = np.diff(gone_, prepend=0.0)
        reads = {}
        for src in ('trip tank', 'pits'):
            v = volumes.get(src)
            if v is None:
                continue
            r = read_volume(ts, np.asarray(v[ia:iz + 1], dtype=np.float64), hs, mft)
            if r['ok']:
                reads[src] = r
        sign = -1.0 if tr['direction'] == 'OUT' else 1.0
        chosen = next((s for s in ('trip tank', 'pits') if s in reads and reads[s]['read_ft'] > 0 and abs(reads[s]['measured']) / reads[s]['read_ft'] * 100.0 >= MIN_RATE), None)
        if chosen is None:
            # nothing moved with the pipe; say what was moved at surface, for the reader
            row['transfers_bbl'] = round(max((r['transfers'] for r in reads.values()), default=0.0), 1)
            out.append(row); continue
        r = reads[chosen]
        row.update(source=chosen, measured_bbl=round(r['measured'], 2), read_ft=round(r['read_ft'], 0), rate_bbl_100ft=round(r['measured'] / r['read_ft'] * 100.0, 3),
                   transfers_bbl=round(r['transfers'], 1), breaks_bbl=round(r['breaks'], 1), _kept=r['kept'], _kept_ft=r['kept_ft'], _ia=ia, _sign=sign)
        row['status'] = 'PENDING'
        out.append(row)
    # what each trip is set against: the declared pipe, else the well's own like trips read from the same tank
    prints: Dict[str, dict] = {}
    for src in ('trip tank', 'pits'):
        for dr in ('OUT', 'IN'):
            like = [x for x in out if x['status'] == 'PENDING' and x['source'] == src and x['direction'] == dr and (x.get('read_ft') or 0.0) >= READ_SHARE * x['feet']
                    and x['transfers_bbl'] <= max(10.0, abs(x['measured_bbl'] or 0.0))]
            if len(like) >= 2:
                m, s = _mad_sigma([x['rate_bbl_100ft'] for x in like])
                prints[f'{src}|{dr}'] = {'source': src, 'direction': dr, 'n': len(like), 'median_bbl_100ft': round(m, 3), 'scatter_bbl_100ft': round(s, 3),
                                         'agree': len(like) >= FINGERPRINT_MIN and s <= SPREAD_MAX * abs(m)}
    for row in out:
        if row['status'] != 'PENDING':
            continue
        kept, kept_ft, ia = row.pop('_kept'), row.pop('_kept_ft'), row.pop('_ia'); row.pop('_sign')
        feet = row['feet']; read_ft = row['read_ft']
        if disp is not None:
            rate = disp['out_bbl_100ft'] if row['direction'] == 'OUT' else disp['in_bbl_100ft']
            exp = rate * read_ft / 100.0
            tol = max(MIN_BBL, SHARE * abs(exp))
            row['basis'] = 'pipe'
        else:
            like = [x['rate_bbl_100ft'] for x in out if x is not row and x['source'] == row['source'] and x['direction'] == row['direction'] and x['rate_bbl_100ft'] is not None
                    and x['status'] not in ('NOT READ',) and x['transfers_bbl'] <= max(10.0, abs(x['measured_bbl'] or 0.0)) and (x.get('read_ft') or 0.0) >= READ_SHARE * x['feet']]
            rate, sc = _mad_sigma(like) if len(like) >= FINGERPRINT_MIN else (None, None)
            group = prints.get(f"{row['source']}|{row['direction']}")
            if rate is not None and group is not None and not group['agree']:
                sc = float('inf')                               # the like trips as a whole do not agree: one left out does not make them
            if rate is None or sc > SPREAD_MAX * abs(rate):
                row['status'] = 'NO BASIS'
                row['why'] = ('the well has too few like trips' if rate is None
                              else f"its {len(like)} like trips do not agree with one another ({abs(rate):.2f} bbl per 100 ft at the median" + (f", scatter {group['scatter_bbl_100ft']:.2f})" if group else ')'))
                rate = None
            else:
                exp = rate * read_ft / 100.0
                tol = max(MIN_BBL, SIGMA * sc * read_ft / 100.0)
                row['basis'] = 'own trips'; row['like_trips'] = len(like)
        if read_ft < READ_SHARE * feet:
            row['status'] = 'LITTLE READ'
        elif row['transfers_bbl'] > max(10.0, abs(exp) if rate is not None else abs(row['measured_bbl'])):
            row['status'] = 'TRANSFER'
        if rate is not None:
            dep = row['measured_bbl'] - exp
            row.update(expected_bbl=round(exp, 2), expected_rate_bbl_100ft=round(rate, 3), departure_bbl=round(dep, 2), tolerance_bbl=round(tol, 2))
            if row['status'] == 'PENDING':
                if abs(dep) <= tol:
                    row['status'] = 'AS EXPECTED'
                elif row['direction'] == 'OUT':
                    row['status'] = 'SHORT FILL' if dep > 0 else 'TOOK MORE'
                else:
                    row['status'] = 'GAVE MORE' if dep > 0 else 'SHORT RETURN'
        # along the trip: the footage gone and the mud kept, each INTERVAL_FT against the same rate
        seg_b = b[ia:ia + len(kept)]
        gone = np.abs(seg_b - seg_b[0])
        gone = np.maximum.accumulate(gone)
        cum = np.cumsum(kept); cft = np.cumsum(kept_ft)
        edges = np.arange(0.0, feet + 1e-6, INTERVAL_FT)
        if len(edges) < 2 or feet - edges[-1] > 0.4 * INTERVAL_FT:
            edges = np.append(edges, feet)
        else:
            edges[-1] = feet
        at = np.searchsorted(gone, edges, side='left').clip(0, len(gone) - 1)
        at[-1] = len(gone) - 1
        ivs = []
        for k in range(len(edges) - 1):
            ft = float(edges[k + 1] - edges[k])
            mv = float(cum[at[k + 1]] - (cum[at[k]] if k else 0.0))
            rf = float(cft[at[k + 1]] - (cft[at[k]] if k else 0.0))
            d0 = float(seg_b[at[k]]); d1 = float(seg_b[at[k + 1]])
            iv = {'from_ft': round(d0, 0), 'to_ft': round(d1, 0), 'feet': round(ft, 0), 'read_ft': round(rf, 0), 'measured_bbl': round(mv, 2)}
            if rate is not None:
                iv['expected_bbl'] = round(rate * rf / 100.0, 2); iv['departure_bbl'] = round(mv - rate * rf / 100.0, 2)
            ivs.append(iv)
        row['intervals'] = ivs
        if row['status'] in DEPARTED and ivs:
            same = [iv for iv in ivs if (iv['departure_bbl'] > 0) == (row['departure_bbl'] > 0)]
            if same:
                row['worst'] = max(same, key=lambda iv: abs(iv['departure_bbl']))
        step = max(1, len(gone) // CURVE_POINTS)
        row['curve'] = {'feet': [round(float(x), 0) for x in gone[::step]] + [round(float(gone[-1]), 0)], 'bbl': [round(float(x), 2) for x in cum[::step]] + [round(float(cum[-1]), 2)]}
        if row['status'] == 'PENDING':
            row['status'] = 'NO BASIS'
    for row in out:
        for k in ('_kept', '_kept_ft', '_ia', '_sign'):
            row.pop(k, None)
        row['reads'] = describe(row)
    counts = {k: sum(1 for x in out if x['status'] == k) for k in STATUS}
    return {'trips': out, 'counts': counts, 'fingerprints': list(prints.values()), 'displacement': ({k: round(v, 5) if isinstance(v, float) else v for k, v in disp.items()} if disp else None)}


def describe(x: dict) -> str:
    """One trip in a sentence."""
    way = 'pulling out' if x['direction'] == 'OUT' else 'running in'
    head = f"{way} {x['feet']:,.0f} ft, {x['from_ft']:,.0f} to {x['to_ft']:,.0f} ft"
    if x['status'] == 'NOT READ':
        return head + ': not read - neither the trip tank nor the pits moved with the pipe' + (f" ({x['transfers_bbl']:,.0f} bbl was moved at surface)" if x.get('transfers_bbl') else '')
    if (x['measured_bbl'] < 0) == (x['direction'] == 'OUT'):
        s = head + f": the hole {'took' if x['direction'] == 'OUT' else 'gave'} {abs(x['measured_bbl']):,.1f} bbl by the {x['source']} ({abs(x['rate_bbl_100ft']):.2f} bbl per 100 ft)"
    else:                                                       # the volume went the other way to the pipe
        s = head + f": the {x['source']} {'rose' if x['measured_bbl'] > 0 else 'fell'} {abs(x['measured_bbl']):,.1f} bbl, the other way to the pipe ({abs(x['rate_bbl_100ft']):.2f} bbl per 100 ft)"
    if x.get('expected_bbl') is not None:
        s += f", against {abs(x['expected_bbl']):,.1f} {'taken' if x['direction'] == 'OUT' else 'given'} " + ('for the declared pipe' if x['basis'] == 'pipe' else f"at the median of its {x.get('like_trips', '')} like trips")
        if x['status'] in DEPARTED:
            s += f": {abs(x['departure_bbl']):,.1f} bbl {'short' if x['status'] in ('SHORT FILL', 'SHORT RETURN') else 'more'} (the tolerance is {x['tolerance_bbl']:,.1f})"
            if x.get('worst'):
                s += f"; most of it between {x['worst']['from_ft']:,.0f} and {x['worst']['to_ft']:,.0f} ft"
    elif x['status'] == 'NO BASIS':
        s += '; no pipe is declared and ' + (x.get('why') or 'the well has too few like trips') + ': nothing to set it against'
    if x['status'] == 'TRANSFER':
        s += f"; not judged - {x['transfers_bbl']:,.0f} bbl was moved at surface inside it"
    elif x['status'] == 'LITTLE READ':
        s += f"; not judged - only {x.get('read_ft', 0):,.0f} ft of it was run with the pumps off and no mud being moved at surface"
    elif x.get('transfers_bbl'):
        s += f"; {x['transfers_bbl']:,.0f} bbl moved at surface taken out"
    if x.get('breaks_bbl') and abs(x['breaks_bbl']) >= 1.0:
        s += f"; {x['breaks_bbl']:+,.0f} bbl during the breaks not counted"
    return s


# ---------------------------------------------------------------------------------------------------------------
# the workspace
# ---------------------------------------------------------------------------------------------------------------
def _find(c, names: Tuple[str, ...]) -> Optional[str]:
    low = {n.lower(): n for n in c._ch}
    for want in names:
        if want in low:
            return low[want]
    for want in names:
        for l, n in low.items():
            if l.startswith(want + ' '):
                return n
    return None


def well(ws, well_id: str) -> dict:
    from . import drill_string as DS
    from . import rig_state as RS
    c = DS._cache(ws, well_id, build=True)
    pipe = settings(ws)['pipes'].get(well_id)
    key = (os.path.abspath(ws.path), well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'), json.dumps(pipe, sort_keys=True))
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
    w = ws.well(well_id)
    names = {'trip tank': _find(c, TANK_NAMES), 'pits': _find(c, PIT_NAMES)}
    base = {'well': well_id, 'display': w.get('display') or well_id, 'channels': {k: v for k, v in names.items() if v}, 'pipe': pipe, 'source_mtime': c.meta.get('source_mtime'), 'notes': []}
    rs = RS.well(ws, well_id)
    if not any(names.values()):
        r = {**base, 'status': 'NO VOLUME', 'why': 'the record has neither a trip tank volume nor a pit volume: there is nothing to read a trip from', 'trips': [], 'counts': {}, 'n_trips': len(rs['trips'])}
    elif not rs['trips']:
        r = {**base, 'status': 'NO TRIPS', 'why': 'the record has no trip of 1,000 ft or more with the pumps off', 'trips': [], 'counts': {}, 'n_trips': 0}
    else:
        vols: Dict[str, Optional[np.ndarray]] = {}
        for k, n in names.items():
            if not n:
                vols[k] = None; continue
            v = np.array(c.channel(n), dtype=np.float64)
            if (c.unit(n) or '').lower() in ('m3', 'm^3'):
                v = v * 6.289811
            vols[k] = v
        a = analyse_arrays(np.array(rs['_t'], dtype=np.float64), np.array(c.channel('Bit Depth'), dtype=np.float64), rs['_state'], rs['trips'], vols, pipe)
        r = {**base, 'status': 'OK', **a, 'n_trips': len(rs['trips']), 'record_start': rs.get('t0'),
             'record_end': (datetime.fromisoformat(rs['t0']) + timedelta(seconds=float(rs['_t'][-1]))).isoformat(timespec='seconds')}
        used = {x['source'] for x in a['trips'] if x['source']}
        if names['trip tank'] and 'trip tank' not in used:
            r['notes'].append(f"the trip tank in this record ({names['trip tank']}) does not move with the pipe in any trip: " + ('the pits are read' if 'pits' in used else 'nothing is read'))
        if a['counts'].get('NOT READ'):
            r['notes'].append(f"{a['counts']['NOT READ']} trip(s) not read: no volume in the record moved with the pipe, so the hole was filled from a tank the record does not carry")
        if not pipe:
            r['notes'].append('no pipe is declared: each trip is set against the well\'s own like trips, which finds the trip that differs and cannot check the pipe')
    with _LOCK:
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = r
    return r


def page(ws, well_id: Optional[str] = None, now: Optional[datetime] = None, full: bool = True) -> dict:
    """Every well's trips counted, and the chosen well's sheet. With `full` off only the chosen well keeps its trips'
    intervals and curves (for a screen)."""
    wells = []
    for w in ws.wells():
        try:
            wells.append(well(ws, w['id']))
        except Exception as e:
            if type(e).__name__ in ('EdrError', 'FileNotFoundError', 'KeyError', 'WorkspaceError', 'ValueError'):
                continue
            raise
    out: Dict[str, Any] = {'status': 'OK' if wells else 'NO DRILLING RECORD', 'generated_utc': _utc(now), 'title': ws.manifest.get('name') or 'this workspace', 'statuses': STATUS,
                           'pull': PULL, 'run': RUN, 'not_a_measurement': NOT_A_MEASUREMENT,
                           'rules': {'step_bbl': STEP_BBL, 'step_s': STEP_S, 'min_rate_bbl_100ft': MIN_RATE, 'interval_ft': INTERVAL_FT, 'min_bbl': MIN_BBL, 'share': SHARE, 'sigma': SIGMA,
                                     'like_trips_min': FINGERPRINT_MIN, 'steel_lb_per_bbl': STEEL_LB_PER_BBL}}
    rows, lines = [], []
    for r in wells:
        c = r.get('counts') or {}
        row = {k: r.get(k) for k in ('well', 'display', 'status', 'why', 'channels', 'pipe', 'notes', 'n_trips', 'record_start', 'record_end', 'fingerprints', 'displacement')}
        row['counts'] = c
        if r['status'] == 'OK':
            row['trips'] = r['trips']
            dep = sum(c.get(k, 0) for k in DEPARTED)
            read = sum(c.get(k, 0) for k in ('AS EXPECTED',) + DEPARTED)
            src = sorted({x['source'] for x in r['trips'] if x['source']})
            lines.append(f"{r['display']}: {r['n_trips']} trip(s), {read} read" + (f" from the {' and the '.join(src)}" if src else '') + (' against the declared pipe' if r.get('pipe') else ' against the well\'s own trips' if read else '')
                         + f": {c.get('AS EXPECTED', 0)} as expected, {c.get('SHORT FILL', 0)} short fill, {c.get('GAVE MORE', 0)} gave more, {c.get('TOOK MORE', 0) + c.get('SHORT RETURN', 0)} with losses"
                         + f"; {c.get('NOT READ', 0)} not read, {c.get('TRANSFER', 0) + c.get('LITTLE READ', 0)} not judged, {c.get('NO BASIS', 0)} with nothing to set them against" + ('.' if dep else '.'))
        else:
            lines.append(f"{r['display']}: not read - {r.get('why')}.")
        rows.append(row)
    out['wells'] = rows
    if not wells:
        lines.append('No well of this site has a drilling recorder export attached.')
    else:
        lines.append('A departure is a place in the record to look at: the flow check, the daily report and the driller\'s own trip sheet say what it was.')
    out['lines'] = lines
    cur = next((r for r in rows if r['well'] == well_id), None) or next((r for r in rows if r['status'] == 'OK' and any(x['source'] for x in r['trips'])), None) \
        or next((r for r in rows if r['status'] == 'OK'), None)
    if well_id and not any(r['well'] == well_id for r in rows):
        raise KeyError(f'no well {well_id} with a drilling record')
    out['well'] = cur['well'] if cur else None
    if not full:
        for r in rows:
            if r['well'] != out['well'] and r['status'] == 'OK':
                r['trips'] = [{k: v for k, v in x.items() if k not in ('intervals', 'curve')} for x in r['trips'] if x['status'] in DEPARTED]
    try:
        from . import public_pad as PP
        cr = PP.credit(ws)
        if cr and wells:
            out['public_source'] = cr
            out['lines'].append(cr['line'])
    except Exception:
        pass
    return out


def slim(p: dict) -> dict:
    """The page without each trip's curve, for a reader that is not drawing it."""
    out = dict(p)
    out['wells'] = [dict(r, trips=[{k: v for k, v in x.items() if k != 'curve'} for x in r.get('trips') or []]) if r.get('trips') else r for r in p.get('wells') or []]
    return out


def text(p: dict) -> str:
    L = ['Trip sheet: the mud the hole took or gave on each trip, against the pipe', '=' * 72] + list(p.get('lines') or [])
    for r in p.get('wells') or []:
        if r['status'] != 'OK':
            continue
        pipe = r.get('pipe')
        if pipe:
            d = r.get('displacement') or {}
            L.append(f"  {r['display']}: the pipe {pipe['od_in']:g} x {pipe['id_in']:g} in" + (f", {pipe['weight_lb_ft']:g} lb/ft" if pipe.get('weight_lb_ft') else '')
                     + f" ({pipe['source']}); {PULL[pipe['pull']]}: {abs(d.get('out_bbl_100ft', 0)):.3f} bbl per 100 ft; {RUN[pipe['run']]}: {d.get('in_bbl_100ft', 0):.3f} bbl per 100 ft")
        for f in r.get('fingerprints') or []:
            L.append(f"  {r['display']}: {'pulling out' if f['direction'] == 'OUT' else 'running in'}, by the {f['source']}: {abs(f['median_bbl_100ft']):.2f} bbl per 100 ft at the median of {f['n']} trip(s), scatter {f['scatter_bbl_100ft']:.2f}" + ('' if f.get('agree') else (' - too few to set a trip against' if f['n'] < FINGERPRINT_MIN else ' - they do not agree well enough to set a trip against')))
        shown = [x for x in r.get('trips') or [] if x['status'] in DEPARTED]
        for x in shown:
            L.append(f"  {r['display'][:18]:<19} {(x.get('start') or '')[:16].replace('T', ' ')}  {x['status'].lower():<13} {x['reads']}")
        for n in r.get('notes') or []:
            L.append(f"  note: {r['display']}: {n}")
    return '\n'.join(L)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'trip_sheet')
    slim = json.loads(json.dumps(p, default=str))
    with open(os.path.join(d, 'trip_sheet.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **slim}, f, indent=1)
    with open(os.path.join(d, 'trip_sheet.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Trip sheet\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines'])
                + '\n\n```\n' + text(p) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'trips.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'n', 'start', 'end', 'direction', 'from_ft', 'to_ft', 'feet', 'read_from', 'read_ft', 'measured_bbl', 'bbl_per_100ft', 'expected_bbl', 'set_against', 'departure_bbl', 'tolerance_bbl', 'status',
                     'moved_at_surface_bbl', 'during_breaks_bbl'])
        for r in p['wells']:
            for x in r.get('trips') or []:
                wr.writerow([r['display'], x['n'], x.get('start') or '', x.get('end') or '', x['direction'], x['from_ft'], x['to_ft'], x['feet'], x['source'] or '', x.get('read_ft', ''), '' if x['measured_bbl'] is None else x['measured_bbl'],
                             '' if x['rate_bbl_100ft'] is None else x['rate_bbl_100ft'], '' if x['expected_bbl'] is None else x['expected_bbl'], x['basis'] or '',
                             '' if x['departure_bbl'] is None else x['departure_bbl'], '' if x['tolerance_bbl'] is None else x['tolerance_bbl'], x['status'], x['transfers_bbl'], x['breaks_bbl']])
    with open(os.path.join(d, 'intervals.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['well', 'trip', 'direction', 'from_ft', 'to_ft', 'feet', 'measured_bbl', 'expected_bbl', 'departure_bbl'])
        for r in p['wells']:
            for x in r.get('trips') or []:
                for iv in x.get('intervals') or []:
                    wr.writerow([r['display'], x['n'], x['direction'], iv['from_ft'], iv['to_ft'], iv['feet'], iv['measured_bbl'], iv.get('expected_bbl', ''), iv.get('departure_bbl', '')])
    files = ['trip_sheet.json', 'trip_sheet.md', 'trips.csv', 'intervals.csv']
    ws.audit(actor, 'trip_sheet.report', {'files': files, 'wells': [r['well'] for r in p['wells']]})
    return {'folder': d, 'files': files, 'page': p}


# ---------------------------------------------------------------------------------------------------------------
# a labelled record
# ---------------------------------------------------------------------------------------------------------------
SYNTHETIC_PIPE = {'od_in': 5.0, 'id_in': 4.276, 'weight_lb_ft': 22.6, 'pull': 'dry', 'run': 'open'}


def synthetic_edr(path: str, trip_ft: float = 2000.0) -> dict:
    """A one-second recorder export with seven trips of `trip_ft` whose mud is known, for the pipe SYNTHETIC_PIPE
    (0.8224 bbl of steel per 100 ft): 1 out and 2 in, read from a trip tank, each what the pipe accounts for; 3 out,
    the hole taking nothing for 800 ft (6.58 bbl short); 4 in, with 15 bbl moved into the pits in 20 s; 5 out, with
    five minutes circulating in it while the pits fall 12 bbl; 6 in, half the mud coming back (losses); 7 out, with
    no volume moving at all. From trip 3 on the trip tank stands still and the pits are what moves. SIMULATION."""
    steel = SYNTHETIC_PIPE['weight_lb_ft'] / STEEL_LB_PER_BBL
    hdr = ['YYYY/MM/DD', 'HH:MM:SS', 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Hook Load (klbs)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'On Bottom (unitless)', 'Total Mud Volume (barrels)', 'Trip Tank Mud Volume (barrels)']
    t = [datetime(2026, 8, 1, 6, 0, 0)]
    rows: List[list] = []
    S = {'hole': 10000.0, 'bit': 10000.0, 'block': 105.0, 'pit': 500.0, 'tank': 40.0}

    def row(rop, rpm, pump, hl, on):
        rows.append([t[0].strftime('%Y/%m/%d'), t[0].strftime('%H:%M:%S'), f"{S['hole']:.2f}", f"{S['bit']:.2f}", f'{rop:.1f}', f'{20.0 if on else 0.0:.1f}', f'{rpm:.1f}',
                     f'{2500.0 if pump else 0.0:.0f}', f'{hl:.1f}', f'{550.0 if pump else 0.0:.0f}', f"{S['block']:.2f}", str(on), f"{S['pit']:.3f}", f"{S['tank']:.3f}"])
        t[0] += timedelta(seconds=1)

    def drill(minutes, rate=190.0):
        for _ in range(int(minutes * 60)):
            S['hole'] += rate / 3600.0; S['bit'] = S['hole']; S['block'] -= rate / 3600.0
            row(rate, 120.0, True, 180.0, 1)

    def pick_up(seconds, ft):
        for _ in range(int(seconds)):
            S['bit'] -= ft / seconds; S['block'] += ft / seconds
            row(0.0, 0.0, True, 200.0, 0)

    def slips(seconds, block_to):
        b0 = S['block']
        for i in range(int(seconds)):
            S['block'] = b0 + (block_to - b0) * min(1.0, (i + 1) / max(1.0, seconds * 0.6))
            row(0.0, 0.0, False, 40.0, 0)

    def still(minutes, pump=False, pit_bbl=0.0):
        n = int(minutes * 60)
        for _ in range(n):
            S['pit'] += pit_bbl / n
            row(0.0, 0.0, pump, 200.0, 0)

    def stand(direction, where, share=1.0, move_s=60, slips_s=60, ft=100.0):
        sgn = -1.0 if direction == 'OUT' else 1.0
        if direction == 'IN':
            slips(slips_s, S['block'] + ft)
        for _ in range(int(move_s)):
            S['bit'] += sgn * ft / move_s; S['block'] -= sgn * ft / move_s
            if where:
                S[where] += sgn * steel * share * ft / move_s
            row(0.0, 0.0, False, 200.0, 0)
        if direction == 'OUT':
            slips(slips_s, S['block'] - ft)

    n_st = int(trip_ft / 100.0)
    drill(20)
    still(8, pump=True)
    pick_up(120, 100.0)                                         # pulled a stand off bottom with the pumps on, then the trips
    for _ in range(n_st): stand('OUT', 'tank')                  # 1
    still(15)
    for _ in range(n_st): stand('IN', 'tank')                   # 2
    still(15)
    for k in range(n_st): stand('OUT', 'pit', 0.0 if 6 <= k < 14 else 1.0)      # 3: nothing taken over 800 ft
    still(15)
    for k in range(n_st):                                       # 4: 15 bbl moved into the pits in 20 s
        stand('IN', 'pit')
        if k == n_st // 2:
            for _ in range(20):
                S['pit'] += 0.75
                row(0.0, 0.0, False, 40.0, 0)
    still(15)
    for k in range(n_st):                                       # 5: five minutes circulating in it, the pits falling 12 bbl
        stand('OUT', 'pit')
        if k == n_st // 2:
            still(5, pump=True, pit_bbl=-12.0)
    still(15)
    for _ in range(n_st): stand('IN', 'pit', 0.5)               # 6: half the mud comes back
    still(15)
    for _ in range(n_st): stand('OUT', None)                    # 7: no volume moves
    still(10, pump=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f); wr.writerow(hdr); wr.writerows(rows)
    per = steel * trip_ft
    return {'path': path, 'label': 'SIMULATION', 'pipe': dict(SYNTHETIC_PIPE), 'steel_bbl_100ft': steel * 100.0, 'trip_ft': trip_ft, 'per_trip_bbl': per,
            'expect': [('OUT', 'trip tank', 'AS EXPECTED', -per), ('IN', 'trip tank', 'AS EXPECTED', per), ('OUT', 'pits', 'SHORT FILL', -per * (1 - 800.0 / trip_ft)), ('IN', 'pits', 'AS EXPECTED', per),
                       ('OUT', 'pits', 'AS EXPECTED', -per), ('IN', 'pits', 'SHORT RETURN', per * 0.5), ('OUT', None, 'NOT READ', None)],
            'short_ft': (600.0, 1400.0)}


def selftest() -> dict:
    """The reading against volumes that are known. SIMULATION."""
    from . import rig_state as RS
    checks = []
    # the pipe's arithmetic
    d = displacement(SYNTHETIC_PIPE)
    body = displacement({'od_in': 5.0, 'id_in': 4.276})
    wet = displacement({**SYNTHETIC_PIPE, 'pull': 'wet', 'run': 'closed'})
    checks.append({'name': 'a pipe\'s steel from its weight (22.6 lb/ft: 0.8224 bbl per 100 ft) and from its body (5 x 4.276 in: 0.6524), its bore (1.7762); wet and closed add the bore',
                   'pass': abs(d['out_bbl_100ft'] + 0.82242) < 1e-4 and abs(d['in_bbl_100ft'] - 0.82242) < 1e-4 and abs(body['in_bbl_100ft'] - 0.65238) < 1e-4
                           and abs(wet['out_bbl_100ft'] + 2.59861) < 1e-4 and abs(wet['in_bbl_100ft'] - 2.59861) < 1e-4})
    # arrays: ten trips in at 2.0 bbl per 100 ft, one of them giving 3.5; a refill taken out; a break not counted
    t = np.arange(0.0, 3000.0, 1.0)
    handling = np.ones(len(t), dtype=bool); handling[1000:1300] = False
    v = np.zeros(len(t)); v[:1000] = np.linspace(0.0, 10.0, 1000); v[1000:1300] = np.linspace(10.0, 4.0, 300); v[1300:] = 4.0 + np.linspace(0.0, 17.0, 1700)
    v[2000:] += 30.0                                            # 30 bbl in one second
    r = read_volume(t, v, handling)
    checks.append({'name': 'a volume read over a trip: 27 bbl with the pipe, 6 bbl in a break not counted, 30 bbl moved in a second taken out',
                   'pass': abs(r['measured'] - 27.0) < 1.5 and abs(r['breaks'] + 6.0) < 0.5 and abs(r['transfers'] - 30.0) < 1.5})
    n = 11
    tt = np.arange(0.0, n * 2000.0, 1.0); bit = np.zeros(len(tt)); vol = np.zeros(len(tt)); st = np.full(len(tt), RS.STAT); trips = []
    rates = [2.0, 2.1, 1.9, 2.05, 1.95, 3.5, 2.0, 2.02, 1.98, 2.1, 1.9]
    acc = 0.0
    for k in range(n):
        a = k * 2000; z = a + 1000
        bit[a:z] = np.linspace(0.0, 2000.0, 1000); bit[z:a + 2000] = 2000.0
        vol[a:z] = acc + np.linspace(0.0, rates[k] * 20.0, 1000); acc += rates[k] * 20.0; vol[z:a + 2000] = acc
        st[a:z] = RS.TIN
        trips.append({'t_start': float(a), 't_end': float(z - 1), 'direction': 'IN', 'from_ft': 0.0, 'to_ft': 2000.0, 'feet': 2000.0})
    a_ = analyse_arrays(tt, bit, st, trips, {'trip tank': None, 'pits': vol})
    odd = [x for x in a_['trips'] if x['status'] != 'AS EXPECTED']
    checks.append({'name': 'with no pipe declared, eleven like trips: the one that gave 3.5 bbl per 100 ft where the others gave 2.0 is the one named, 30 bbl more',
                   'pass': len(odd) == 1 and odd[0]['n'] == 6 and odd[0]['status'] == 'GAVE MORE' and odd[0]['basis'] == 'own trips' and abs(odd[0]['departure_bbl'] - 30.0) < 1.0
                           and a_['fingerprints'][0]['n'] == 11})
    few = analyse_arrays(tt[:6000], bit[:6000], st[:6000], trips[:3], {'trip tank': None, 'pits': vol[:6000]})
    checks.append({'name': 'three trips and no pipe: nothing to set them against, and it says so', 'pass': [x['status'] for x in few['trips']] == ['NO BASIS'] * 3})
    flat = analyse_arrays(tt[:6000], bit[:6000], st[:6000], trips[:3], {'trip tank': np.full(6000, 39.7), 'pits': np.full(6000, 500.0)}, SYNTHETIC_PIPE)
    checks.append({'name': 'a trip tank standing at 39.7 bbl and pits that do not move: the trips are not read, not called short', 'pass': [x['status'] for x in flat['trips']] == ['NOT READ'] * 3})
    bad = []
    for kw in ({'od_in': 5.0, 'id_in': 5.5, 'source': 'x'}, {'od_in': 5.0, 'id_in': 4.276, 'source': ''}, {'od_in': 5.0, 'id_in': 4.276, 'source': 'x', 'weight_lb_ft': 60.0},
               {'od_in': 5.0, 'id_in': 4.276, 'source': 'x', 'pull': 'damp'}, {'od_in': 'five', 'id_in': 4.276, 'source': 'x'}):
        try:
            pipe_record('W', **kw); bad.append(False)
        except TripSheetError:
            bad.append(True)
    checks.append({'name': 'a pipe that cannot be: its bore wider than itself, no source, a weight its diameters cannot have, a way of pulling that is none, a diameter in words', 'pass': all(bad)})
    return {'ok': all(c['pass'] for c in checks), 'label': 'SIMULATION', 'checks': checks}
