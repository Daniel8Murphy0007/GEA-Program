# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""edr - the rig's electronic drilling recorder export, read as delivered.

An EDR system exports one row per second with the date and the time in two columns, then the WITS record-1
channels with their units in parentheses - `Hole Depth (feet)`, `Rate Of Penetration (ft_per_hr)`,
`Weight on Bit (klbs)`, `On Bottom (unitless)`, `Interpolated TVD (feet)` and so on. A value the recorder
did not have is `-999.25` (the WITS sentinel) or an empty cell; the file stops when the rig is off the
well and starts again when it is back, so one well's file is several sections separated by gaps of days.

This port reads that layout and nothing else (the historian port takes a timestamp in one column; a LAS or
DLIS is by depth). It streams the file - a 300 MB export is three million rows - into numeric arrays, turns
every sentinel and blank into a missing value and counts them per channel, names the sections, and keeps
the two time columns' origin so every sample has its wall-clock stamp back.

From the stream it writes the DRILLING RECORD: per section, the span, the hole depth drilled, the hours on
bottom and rotating, the rate of penetration while on bottom, the connections, the peaks; for the well, the
total depth and the recorder's own TVD at it; the table of hole depth against the recorder's interpolated
TVD, reduced to depth steps - a table the interval band can convert a measured depth with, labelled as the
recorder's interpolation and not a survey. Every number is the file's arithmetic. The record does not say
what formation was drilled, whether the well was drilled well, or anything the recorder did not write.
"""
from __future__ import annotations

import csv
import json
import math
import os
from array import array
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .ports import LiveStream, StreamChannel

SENTINEL = -999.25
GAP_SECONDS = 60.0                       # a break longer than this starts a new section (one second is the recorder's cadence)
DATE_COL, TIME_COL = 'YYYY/MM/DD', 'HH:MM:SS'
UNITS = {'feet': 'ft', 'ft': 'ft', 'ft_per_hr': 'ft/h', 'klbs': 'klbf', 'rpm': 'rpm', 'psi': 'psi', 'gal_per_min': 'gal/min',
         'kft_lb': 'kft.lbf', 'unitless': '', 'api': 'gAPI', 'spm': 'spm', 'barrels': 'bbl', 'degf': 'degF', 'ppg': 'ppg', 'm': 'm',
         'm_per_hr': 'm/h', 'kn': 'kN', 'kpa': 'kPa', 'l_per_min': 'L/min', 'knm': 'kN.m', 'm3': 'm3', 'degc': 'degC'}
# the channels the record reads by name (the header's name without its unit); a file may carry more or fewer
CH = {'hole': 'Hole Depth', 'bit': 'Bit Depth', 'rop': 'Rate Of Penetration', 'wob': 'Weight on Bit', 'rpm': 'Rotary RPM',
      'spp': 'Standpipe Pressure', 'hkld': 'Hook Load', 'dp': 'Differential Pressure', 'flow': 'Total Pump Output',
      'block': 'Block Height', 'torque': 'Convertible Torque', 'onb': 'On Bottom', 'gamma': 'Gamma', 'spm': 'Pump Total Strokes Rate',
      'trip': 'Trip Tank Mud Volume', 'tvd': 'Interpolated TVD'}


def is_edr(path: str) -> bool:
    """The layout and nothing more: the first two header cells are the date and the time."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            first = f.readline()
    except OSError:
        return False
    cells = [c.strip() for c in first.split(',')]
    return len(cells) >= 3 and cells[0].upper() == DATE_COL and cells[1].upper() == TIME_COL


def split_header(h: str) -> Tuple[str, str]:
    """`Hole Depth (feet)` -> ('Hole Depth', 'ft'); a header without a unit keeps its name and gets ''."""
    h = h.strip()
    if h.endswith(')') and '(' in h:
        name, unit = h[:h.rfind('(')].strip(), h[h.rfind('(') + 1:-1].strip()
        return name, UNITS.get(unit.lower(), unit)
    return h, ''


def _stamp(d: str, t: str) -> datetime:
    return datetime.strptime(d.strip() + ' ' + t.strip(), '%Y/%m/%d %H:%M:%S')


def read(path: str) -> LiveStream:
    """The file as a LiveStream: elapsed seconds from the first stamp, one channel per column after the two
    time columns, sentinels and blanks missing and counted. Refuses a file that is not this layout."""
    p = Path(path)
    if not is_edr(str(p)):
        raise ValueError(f'{p.name}: not an EDR export (the first two columns must be {DATE_COL} and {TIME_COL})')
    with p.open('r', newline='', encoding='utf-8', errors='replace') as f:
        rd = csv.reader(f)
        header = next(rd)
        names_units = [split_header(h) for h in header[2:]]
        ncol = len(names_units)
        cols = [array('d') for _ in range(ncol)]
        secs = array('d')
        sentinel = [0] * ncol; blank = [0] * ncol; bad = [0] * ncol
        t0 = None; prev = None; n = 0; out_of_order = 0; duplicate = 0
        bounds: List[int] = [0]
        for row in rd:
            if len(row) < 2 or not row[0].strip():
                continue
            try:
                t = _stamp(row[0], row[1])
            except ValueError:
                continue                                    # a row with no readable stamp is not a sample
            if t0 is None:
                t0 = t
            el = (t - t0).total_seconds()
            if prev is not None:
                d = el - prev
                if d < 0:
                    out_of_order += 1
                elif d == 0:
                    duplicate += 1
                elif d > GAP_SECONDS:
                    bounds.append(n)
            prev = el
            secs.append(el)
            for j in range(ncol):
                cell = row[j + 2].strip() if j + 2 < len(row) else ''
                if not cell:
                    blank[j] += 1; cols[j].append(math.nan); continue
                try:
                    x = float(cell)
                except ValueError:
                    bad[j] += 1; cols[j].append(math.nan); continue
                if x == SENTINEL:
                    sentinel[j] += 1; cols[j].append(math.nan); continue
                cols[j].append(x)
            n += 1
    if n == 0 or t0 is None:
        raise ValueError(f'{p.name}: no readable rows')
    index = np.frombuffer(secs, dtype=np.float64).copy()
    channels: Dict[str, StreamChannel] = {}
    counts = {}
    for j, (name, unit) in enumerate(names_units):
        channels[name] = StreamChannel(name=name, unit=unit, values=np.frombuffer(cols[j], dtype=np.float64).copy())
        counts[name] = {'sentinel': sentinel[j], 'blank': blank[j], 'bad': bad[j]}
    bounds.append(n)
    sections = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    meta = {'path': str(p), 'columns': str(len(header)), 'start_time': t0.isoformat(), 'end_time': (t0 + timedelta(seconds=float(index[-1]))).isoformat(),
            'rows': str(n), 'sections': json.dumps(sections), 'counts': json.dumps(counts), 'out_of_order': str(out_of_order), 'duplicate_stamps': str(duplicate),
            'layout': f'EDR one-second export: {DATE_COL} and {TIME_COL} columns, {ncol} channels with units in the header, {SENTINEL} and blank cells missing'}
    return LiveStream(name=p.stem, source_format='edr', index_kind='time_s', index=index, channels=channels, meta=meta)


# -- the drilling record ---------------------------------------------------------------------------------------------
def _ch(stream: LiveStream, key: str) -> Optional[np.ndarray]:
    c = stream.channels.get(CH[key])
    return c.values if c is not None else None


def _hours(seconds: float) -> float:
    return round(seconds / 3600.0, 2)


def _span(idx: np.ndarray, a: int, b: int) -> float:
    """Seconds covered by samples a..b-1 (the samples are one second apart; the span counts the samples, not the clock)."""
    return float(b - a)


# -- what the hole depth channel can be trusted for ----------------------------------------------------------------
DROP_FT = 10.0                     # a fall in hole depth larger than this is an event, never drilling
REREF_RATE_FT_H = 1000.0           # depth regained faster than this is the recorder counting pipe, not new hole
SIDETRACK_MIN_DROP_FT = 500.0      # a sidetrack abandons at least this much hole
TIMEDRILL_ROP_FT_H = 20.0          # the first six hours after a kickoff are time-drilled slower than this
EVENT_TYPES = ('STALE OPENING', 'RE-REFERENCE', 'SIDETRACK', 'CORRECTION', 'UNRESOLVED')


def depth_events(stream: LiveStream) -> dict:
    """Read the hole depth channel for what it can be trusted for, before anything is computed from it.

    A recorder's hole depth is the depth it has been told and the depth the bit has passed; four things make it lie,
    and each leaves a shape this pass recognises and names:

    - STALE OPENING: a section opens on the depth the recorder was showing when it was moved - the last well's
      depth - and holds it until someone sets it; the run is excluded (it is left by a jump of more than DROP_FT,
      or it differs from where this file left off and holds to the section's end, and nothing was drilled in it).
    - RE-REFERENCE: the depth is set back to a shallow value and the recorder then counts pipe running in as new
      hole, with the on-bottom flag set; it is regained faster than any bit drills (> REREF_RATE_FT_H), or with the
      recorder's own rate of penetration at zero - within the same section; a depth not regained before the
      recording stops is never a re-reference. The window is excluded.
    - SIDETRACK: the depth is set back by SIDETRACK_MIN_DROP_FT or more and the next six hours on bottom are
      time-drilled (median rate under TIMEDRILL_ROP_FT_H): a new borehole from that depth; the hole below it,
      drilled before, is abandoned, counted in the footage drilled and kept out of the depth tables and logs.
    - CORRECTION: a smaller fall regained at drilling rates - the depth was overstated and has been corrected;
      the overstated footage is taken back. A depth set forward by more than DROP_FT with the string off bottom is
      a correction the other way: it had been understated, and the footage is added. Without a sidetrack the
      footage drilled is therefore exactly total depth less the first depth on bottom.

    A fall that is none of these is UNRESOLVED, handled as a correction, and said to be so. The daily drilling
    reports decide every one; this pass reads the shape and says which shape it read."""
    hole, rop, onb = _ch(stream, 'hole'), _ch(stream, 'rop'), _ch(stream, 'onb')
    n = len(stream.index); idx = stream.index
    t0 = datetime.fromisoformat(stream.meta['start_time'])
    T = lambda i: (t0 + timedelta(seconds=float(idx[i]))).isoformat()
    excluded = np.zeros(n, dtype=bool)
    out = {'events': [], 'excluded': excluded, 'kickoffs': [], 'running_bottom': None, 'increments': None}
    if hole is None:
        return out
    on = (onb == 1.0) if onb is not None else np.zeros(n, dtype=bool)
    ropv = np.nan_to_num(rop, nan=0.0) if rop is not None else np.zeros(n)
    sections = json.loads(stream.meta['sections'])
    # 1. stale openings
    prev_last = None
    for a, b in sections:
        pres = np.flatnonzero(~np.isnan(hole[a:b]))
        if not len(pres):
            continue
        a0 = a + int(pres[0]); v = float(hole[a0])
        k = a0
        while k < b and (hole[k] != hole[k] or abs(hole[k] - v) <= 0.05):
            k += 1
        jumps = k < b and abs(hole[k] - v) > DROP_FT                                    # left by a jump, not by drilling on from it
        differs = prev_last is not None and abs(v - prev_last) > DROP_FT
        drilled = bool(np.any(on[a0:k] & (ropv[a0:k] > 0)))
        if (jumps or (differs and k >= b)) and not drilled and not excluded[a0]:
            excluded[a0:k] = True
            out['events'].append({'type': 'STALE OPENING', 'at': T(a0), 'from_ft': round(v, 1), 'to_ft': (round(float(hole[k]), 1) if k < b else None),
                                  'hours': round(float(idx[k - 1] - idx[a0]) / 3600.0, 2),
                                  'effect': f"the section opened on {v:,.1f} ft - " + ("not where this file left off" if differs else "a value the recorder then left by a jump")
                                            + f" - and held it for {float(idx[k - 1] - idx[a0]) / 60.0:,.0f} min with nothing drilled: a depth carried over from where the recorder was before; excluded"})
        last = np.flatnonzero(~np.isnan(hole[a:b]) & ~excluded[a:b])                    # where the file left off: the last depth it can be trusted for
        prev_last = float(hole[a + int(last[-1])]) if len(last) else prev_last
    # 2. falls, in time order, each classified by how the depth was regained
    falls = []; lastv = None
    for i in range(n):
        if hole[i] != hole[i] or excluded[i]:
            continue
        if lastv is not None and hole[lastv] - hole[i] > DROP_FT:
            falls.append((lastv, i))
        lastv = i
    six = 6 * 3600.0
    sec_end = np.zeros(n, dtype=int)
    for a, b in sections:
        sec_end[a:b] = b
    at_event = {}
    for p, i in falls:
        if excluded[i]:
            continue
        pre, post = float(hole[p]), float(hole[i]); drop = pre - post
        e_sec = sec_end[i]                                                               # a depth is regained within its own section, or not at all
        later = np.flatnonzero((hole[i:e_sec] >= pre - 0.5) & ~excluded[i:e_sec])
        j = i + int(later[0]) if len(later) else None
        dur_h = (float(idx[j] - idx[i]) / 3600.0) if j is not None else None
        rate = (drop / dur_h) if dur_h else None
        win6 = (idx >= idx[i]) & (idx < idx[i] + six)
        f6 = ropv[win6 & on & ~excluded]
        rop6 = float(np.median(f6)) if len(f6) else None
        if j is not None:
            rr = ropv[i:j][on[i:j] & ~excluded[i:j]]
            rop_reg = float(np.median(rr)) if len(rr) else None
        else:
            rop_reg = None
        ev = {'at': T(i), 'from_ft': round(pre, 1), 'to_ft': round(post, 1), 'regained_after_h': round(dur_h, 2) if dur_h is not None else None,
              'regain_ft_per_h': round(rate) if rate is not None else None, 'rop_median_first_6h_ft_h': round(rop6, 1) if rop6 is not None else None}
        if j is not None and (rate is None or rate > REREF_RATE_FT_H or (rop_reg is not None and rop_reg == 0.0)):
            ev['type'] = 'RE-REFERENCE'
            excluded[i:j] = True
            ev['effect'] = (f"the depth was set back to {post:,.1f} ft and regained {drop:,.0f} ft in {dur_h * 60:,.0f} min ({rate:,.0f} ft/h)"
                            + (", with the recorder's own rate of penetration at zero" if rop_reg == 0.0 else '') + ": pipe counted as new hole; the window is excluded"
                            + f" ({float(on[i:j].sum()) / 3600.0:,.1f} h the on-bottom flag was set in it)")
        elif drop >= SIDETRACK_MIN_DROP_FT and rop6 is not None and rop6 < TIMEDRILL_ROP_FT_H:
            ev['type'] = 'SIDETRACK'
            ev['effect'] = (f"the depth was set back {drop:,.0f} ft to {post:,.1f} ft and the next six hours on bottom ran at a median {rop6:,.1f} ft/h - time-drilling a kickoff: "
                            f"a new borehole from {post:,.1f} ft; the hole drilled before from {post:,.0f} to {pre:,.0f} ft is abandoned")
        elif drop < SIDETRACK_MIN_DROP_FT:
            ev['type'] = 'CORRECTION'
            ev['effect'] = f"the depth was {drop:,.1f} ft too deep and was corrected to {post:,.1f} ft; the overstated footage is taken back"
        else:
            ev['type'] = 'UNRESOLVED'
            ev['effect'] = (f"the depth was set back {drop:,.0f} ft to {post:,.1f} ft and neither regained like a re-reference nor time-drilled like a kickoff; "
                            "handled as a correction - the daily drilling report decides")
        out['events'].append(ev)
        if ev['type'] != 'RE-REFERENCE':
            at_event[i] = ev
    out['events'].sort(key=lambda e: e['at'])
    # 3. the running bottom of the current borehole and the footage each sample adds
    M = np.nan; Marr = np.full(n, np.nan); inc = np.zeros(n)
    for k in range(n):
        h = hole[k]
        if h != h or excluded[k]:
            Marr[k] = M; continue
        ev = at_event.get(k)
        if ev is not None:
            if ev['type'] == 'SIDETRACK':
                out['kickoffs'].append({'index': k, 'at': ev['at'], 'kickoff_ft': round(float(h), 1), 'abandoned_deepest_ft': (round(float(M), 1) if M == M else None)})
                M = float(h)
            else:
                if M == M and M > h:
                    inc[k] -= (M - h)
                M = float(h)
            Marr[k] = M; continue
        if M != M:
            M = float(h)
        elif h > M:
            if not on[k] and h - M > DROP_FT:                                           # set forward off bottom: the depth was understated
                out['events'].append({'type': 'CORRECTION', 'at': T(k), 'from_ft': round(float(M), 1), 'to_ft': round(float(h), 1),
                                      'effect': f"the depth was set forward {h - M:,.1f} ft to {float(h):,.1f} ft off bottom - it had been understated; the footage is added"})
            inc[k] = h - M                                                               # every foot the bottom moves down is footage; the falls take back what was overstated
            M = float(h)
        Marr[k] = M
    out['events'].sort(key=lambda e: e['at'])
    out['running_bottom'] = Marr; out['increments'] = inc
    return out


IDLE_FLAG_S = 1800                 # the on-bottom flag set this long with the depth still and no rate of penetration is not drilling


def _idle_flag(drilling: np.ndarray, hole: Optional[np.ndarray], rop: Optional[np.ndarray]) -> np.ndarray:
    """Stretches of IDLE_FLAG_S or longer in which the on-bottom flag is set at the bottom of the hole but the depth
    does not move and the rate of penetration is zero or missing: the flag left on, not drilling."""
    n = len(drilling); idle = np.zeros(n, dtype=bool)
    if hole is None:
        return idle
    ropz = (np.nan_to_num(rop, nan=0.0) <= 0.0) if rop is not None else np.ones(n, dtype=bool)
    cand = drilling & ropz & ~np.isnan(hole)
    hv = np.nan_to_num(hole, nan=-1.0)
    brk = np.ones(n, dtype=bool)
    brk[1:] = ~cand[1:] | ~cand[:-1] | (np.abs(hv[1:] - hv[:-1]) > 0.05)
    starts = np.flatnonzero(brk & cand)
    for st in starts:
        e = st + 1
        while e < n and cand[e] and not brk[e]:
            e += 1
        if e - st >= IDLE_FLAG_S:
            idle[st:e] = True
    return idle


def _keep_masks(stream: LiveStream, de: dict) -> Tuple[np.ndarray, np.ndarray]:
    """Samples a depth table may use: not excluded, and not in a borehole abandoned by a later sidetrack
    (by hole depth for the TVD table, by bit depth for the gamma log)."""
    n = len(stream.index)
    hole, bit = _ch(stream, 'hole'), _ch(stream, 'bit')
    keep_h = ~de['excluded'].copy(); keep_b = ~de['excluded'].copy()
    for ko in de['kickoffs']:
        before = np.arange(n) < ko['index']
        if hole is not None:
            keep_h &= ~(before & (np.nan_to_num(hole, nan=-1) > ko['kickoff_ft']))
        if bit is not None:
            keep_b &= ~(before & (np.nan_to_num(bit, nan=-1) > ko['kickoff_ft']))
    return keep_h, keep_b


def record(stream: LiveStream, de: Optional[dict] = None) -> dict:
    """The drilling record from an EDR stream: the file's own arithmetic, per section and for the well, computed
    after `depth_events` has said which hole depths can be trusted."""
    if stream.source_format != 'edr':
        raise ValueError('a drilling record is written from an EDR stream')
    de = de if de is not None else depth_events(stream)
    t0 = datetime.fromisoformat(stream.meta['start_time'])
    idx = stream.index; n = len(idx)
    T = lambda i: (t0 + timedelta(seconds=float(idx[i]))).isoformat()
    sections = json.loads(stream.meta['sections'])
    counts = json.loads(stream.meta['counts'])
    hole, bit, rop, wob, rpm, spp, onb, tvd, torque, hkld, flow = (_ch(stream, k) for k in ('hole', 'bit', 'rop', 'wob', 'rpm', 'spp', 'onb', 'tvd', 'torque', 'hkld', 'flow'))
    missing = [CH[k] for k in ('hole', 'onb') if _ch(stream, k) is None]
    out = {'well': stream.name, 'source': os.path.basename(stream.meta['path']), 'layout': stream.meta['layout'], 'rows': int(n),
           'start': stream.meta['start_time'], 'end': stream.meta['end_time'], 'span_days': round(float(idx[-1]) / 86400.0, 2),
           'seconds_recorded': int(n), 'hours_recorded': _hours(n), 'n_sections': len(sections), 'sections': [], 'channels': [], 'notes': [], 'lines': [],
           'depth_events': [{k: v for k, v in e.items()} for e in de['events']]}
    if missing:
        out['notes'].append('channels this record needs are not in the file: ' + ', '.join(missing))
    on_flag = (onb == 1.0) if onb is not None else np.zeros(n, dtype=bool)
    excl = de['excluded']; M = de['running_bottom']; inc = de['increments']
    if hole is not None and M is not None:
        drilling = on_flag & ~excl & ~np.isnan(hole) & (hole >= np.nan_to_num(M, nan=np.inf) - 10.0)
    else:
        drilling = on_flag & ~excl
    idle = _idle_flag(drilling, hole, rop)
    drilling = drilling & ~idle
    rot = drilling & (rpm > 0) if rpm is not None else drilling
    prev_end = None
    for si, (a, b) in enumerate(sections, 1):
        sec = {'n': si, 'start': T(a), 'end': T(b - 1), 'rows': int(b - a), 'hours': _hours(_span(idx, a, b)),
               'gap_before_hours': (_hours(float(idx[a] - idx[prev_end - 1])) if prev_end else None)}
        d = drilling[a:b]
        if hole is not None and d.any():
            mm = np.flatnonzero(~np.isnan(M[a:b])); m0 = a + int(mm[0])
            sec['hole_depth_at_start_ft'] = round(float(M[m0] - inc[m0]), 1)              # the running bottom when this section opened
            sec['hole_depth_to_ft'] = round(float(np.max(hole[a:b][d])), 1)
            sec['feet_drilled'] = round(float(inc[a:b].sum()), 1)                           # new hole, after resets, corrections and stale depths
        evs = [e for e in de['events'] if sec['start'] <= e['at'] <= sec['end']]
        if evs:
            sec['depth_events'] = [e['type'] for e in evs]
        sec['on_bottom_hours'] = _hours(float(d.sum())); sec['rotating_hours'] = _hours(float(rot[a:b].sum()))
        sec['on_bottom_flag_hours'] = _hours(float(on_flag[a:b].sum()))
        if rop is not None:
            v = rop[a:b][d]; v = v[~np.isnan(v)]
            sec['rop_on_bottom_mean_ft_h'] = round(float(v.mean()), 1) if len(v) else None
            sec['rop_on_bottom_median_ft_h'] = round(float(np.median(v)), 1) if len(v) else None
        if sec.get('feet_drilled') is not None and sec['on_bottom_hours']:
            sec['feet_per_on_bottom_hour'] = round(sec['feet_drilled'] / sec['on_bottom_hours'], 1)
        for key, nm in (('wob', 'wob_max_klbf'), ('rpm', 'rpm_max'), ('spp', 'spp_max_psi'), ('torque', 'torque_max_kft_lbf'), ('hkld', 'hookload_max_klbf'), ('flow', 'flow_max_gal_min')):
            arr = _ch(stream, key)
            if arr is not None:
                v = arr[a:b][~excl[a:b]]; v = v[~np.isnan(v)]
                sec[nm] = round(float(v.max()), 2) if len(v) else None
        # a connection: the string comes off bottom and goes back within the section (drilling stops, then resumes)
        if len(d) > 1:
            sec['connections'] = int(np.sum((~d[1:]) & d[:-1]))
        out['sections'].append(sec)
        prev_end = b
    if hole is not None and drilling.any():
        last_ko = de['kickoffs'][-1]['index'] if de['kickoffs'] else 0
        final = drilling & (np.arange(n) >= last_ko)
        td = float(np.max(hole[final])) if final.any() else float(np.max(hole[drilling]))
        out['total_depth_md_ft'] = round(td, 1)
        i_td = int(np.flatnonzero(final & (hole >= td - 0.5))[0]) if (final & (hole >= td - 0.5)).any() else int(np.flatnonzero(drilling)[-1])
        d0 = int(np.flatnonzero(drilling)[0])
        out['first_hole_depth_ft'] = round(float(M[d0] - inc[d0]), 1)
        out['reached_td'] = T(i_td)
        out['reached_td_how'] = 'the first sample drilling at total depth in the final borehole'
        raw_td = float(np.nanmax(hole))
        if raw_td > td + 0.5:
            out['notes'].append(f"the hole depth channel's largest value is {raw_td:,.1f} ft, which the record does not take as total depth: "
                                "it lies in a stale opening, a re-referenced window or an abandoned borehole")
        out['boreholes'] = []
        start_ft = out['first_hole_depth_ft']
        for ko in de['kickoffs']:
            out['boreholes'].append({'from_ft': start_ft, 'deepest_ft': ko['abandoned_deepest_ft'], 'status': 'ABANDONED below the next kickoff',
                                     'abandoned_interval_ft': [ko['kickoff_ft'], ko['abandoned_deepest_ft']]})
            start_ft = ko['kickoff_ft']
        out['boreholes'].append({'from_ft': start_ft, 'deepest_ft': out['total_depth_md_ft'], 'status': 'FINAL', 'kicked_off_at': (de['kickoffs'][-1]['at'] if de['kickoffs'] else None)})
        keep_h, _ = _keep_masks(stream, de)
        if tvd is not None:
            both = keep_h & ~np.isnan(tvd) & ~np.isnan(hole)
            if both.any():
                j = np.flatnonzero(both)[int(np.argmax(hole[both]))]
                out['tvd_at_td_ft'] = round(float(tvd[j]), 1); out['tvd_at_td_md_ft'] = round(float(hole[j]), 1)
                if td - float(hole[j]) > 1.0:
                    out['notes'].append(f"the recorder's TVD stops at {float(hole[j]):,.1f} ft MD, {td - float(hole[j]):,.1f} ft short of total depth; the TVD at TD is the last one it wrote")
                out['tvd_max_ft'] = round(float(np.max(tvd[both])), 1)
    out['on_bottom_hours'] = _hours(float(drilling.sum())); out['rotating_hours'] = _hours(float(rot.sum()))
    out['on_bottom_flag_hours'] = _hours(float(on_flag.sum()))
    out['excluded_hours'] = _hours(float(excl.sum()))
    out['flag_idle_hours'] = _hours(float(idle.sum()))
    if out.get('total_depth_md_ft') is not None:
        out['feet_drilled'] = round(float(inc.sum()), 1)
        if out['on_bottom_hours']:
            out['feet_per_on_bottom_hour'] = round(out['feet_drilled'] / out['on_bottom_hours'], 1)
    for name, ch in stream.channels.items():
        v = ch.values; ok = ~np.isnan(v)
        c = counts.get(name, {})
        out['channels'].append({'channel': name, 'unit': ch.unit, 'present': int(ok.sum()), 'coverage_pct': round(100.0 * ok.sum() / len(v), 2),
                                'sentinel': c.get('sentinel', 0), 'blank': c.get('blank', 0), 'not_numeric': c.get('bad', 0),
                                'min': (round(float(v[ok].min()), 3) if ok.any() else None), 'max': (round(float(v[ok].max()), 3) if ok.any() else None)})
    keep_h, keep_b = _keep_masks(stream, de)
    out['md_tvd'] = md_tvd_table(stream, keep=keep_h)
    if out['md_tvd']['status'] == 'OK':
        out['lateral'] = lateral_from_table(out['md_tvd']['rows'])
    out['observations'] = observations(stream)
    gl = gamma_log(stream, keep=keep_b)
    if gl is not None:
        out['gamma'] = {'status': 'OK', 'bins': int(len(gl.index)), 'depth_from_ft': float(gl.index[0]), 'depth_to_ft': float(gl.index[-1]),
                        'bins_covered_pct': float(gl.meta['bins_covered_pct']), 'samples': int(gl.meta['samples']), 'how': gl.meta['how']}
        try:
            out['gamma']['lithology'] = gamma_lithology(gl)
        except ValueError as ex:
            out['gamma']['lithology'] = {'status': 'NOT ESTIMATED', 'why': str(ex)}
    else:
        out['gamma'] = {'status': 'NO GAMMA ON BOTTOM', 'how': 'the file carries no gamma value while the on-bottom flag is set'}
    out['lines'] = lines(out)
    return out


def md_tvd_table(stream: LiveStream, step_ft: float = 100.0, keep: Optional[np.ndarray] = None) -> dict:
    """Hole depth against the recorder's interpolated TVD, one row per `step_ft` of hole depth (the deepest sample
    in each step, the last time the hole was that deep). The recorder interpolates its TVD from the directional
    surveys it was given; this table is that interpolation and is labelled so - it is not a survey."""
    hole, tvd = _ch(stream, 'hole'), _ch(stream, 'tvd')
    if hole is None or tvd is None:
        return {'status': 'NO CHANNEL', 'rows': [], 'note': f'needs {CH["hole"]} and {CH["tvd"]}'}
    ok = ~np.isnan(hole) & ~np.isnan(tvd)
    if keep is not None:
        ok &= keep                                      # not a stale or re-referenced depth, not an abandoned borehole
    if not ok.any():
        return {'status': 'NO DATA', 'rows': [], 'note': 'no sample carries both a hole depth and a TVD'}
    h, t = hole[ok], tvd[ok]
    steps = np.floor(h / step_ft).astype(int)
    rows = []
    for s in np.unique(steps):
        m = steps == s
        i = np.flatnonzero(m)[-1]                       # the last sample in the step: the deepest the hole got there
        rows.append({'md_ft': round(float(h[i]), 1), 'tvd_ft': round(float(t[i]), 2)})
    rows.sort(key=lambda r: r['md_ft'])
    non_mono = sum(1 for a, b in zip(rows, rows[1:]) if b['tvd_ft'] < a['tvd_ft'] - 0.5)
    return {'status': 'OK', 'rows': rows, 'step_ft': step_ft, 'n': len(rows), 'md_min_ft': rows[0]['md_ft'], 'md_max_ft': rows[-1]['md_ft'],
            'tvd_max_ft': round(max(r['tvd_ft'] for r in rows), 2), 'tvd_decreasing_steps': non_mono,
            'source': f"the recorder's '{CH['tvd']}' channel against '{CH['hole']}', one row per {step_ft:.0f} ft of hole depth; an interpolation the recorder "
                      "made from the directional surveys it was given, not a survey, and not a measurement by this program",
            'samples_with_both': int(ok.sum()), 'samples_without_tvd': int(np.isnan(tvd).sum())}


def lateral_from_table(rows: List[dict]) -> dict:
    """Where the hole stopped going down: the first table row at which TVD is within 1% of its maximum, read as the
    start of the lateral; the lateral length is total MD minus that MD. Arithmetic on the table, named as such."""
    tmax = max(r['tvd_ft'] for r in rows)
    first = next((r for r in rows if r['tvd_ft'] >= 0.99 * tmax), rows[-1])
    return {'tvd_max_ft': round(tmax, 1), 'lateral_start_md_ft': first['md_ft'], 'lateral_length_ft': round(rows[-1]['md_ft'] - first['md_ft'], 1),
            'how': 'the first 100-ft step at which the recorder\'s TVD is within 1% of its maximum; a landing point is the directional survey\'s to give'}


# -- the gamma log the recorder carries -------------------------------------------------------------------------------
GAMMA_BIN_FT = 1.0
ROP_SPIKE_FT_H = 1000.0


def gamma_log(stream: LiveStream, bin_ft: float = GAMMA_BIN_FT, keep: Optional[np.ndarray] = None) -> Optional[LiveStream]:
    """The recorder's gamma channel as a log by depth: the on-bottom samples that carry a gamma value, binned to
    `bin_ft` of bit depth, the median per bin and the sample count per bin. Indexed to BIT depth: the gamma sensor
    sits some distance behind the bit on the drilling assembly, a distance the recorder does not state, so the log
    is shallow by that offset and says so. None when the file carries no gamma on bottom."""
    g, bd, onb = _ch(stream, 'gamma'), _ch(stream, 'bit'), _ch(stream, 'onb')
    if g is None or bd is None:
        return None
    on = (onb == 1.0) if onb is not None else np.ones(len(g), dtype=bool)
    m = on & ~np.isnan(g) & ~np.isnan(bd)
    if keep is not None:
        m &= keep                                       # the final borehole only, and no re-referenced depth
    if not m.any():
        return None
    b = np.floor(bd[m] / bin_ft).astype(np.int64)
    gv = g[m]
    order = np.argsort(b, kind='stable')
    b, gv = b[order], gv[order]
    edges = np.flatnonzero(np.diff(b)) + 1
    starts = np.concatenate([[0], edges]); ends = np.concatenate([edges, [len(b)]])
    depth = (b[starts] * bin_ft).astype(np.float64)
    med = np.array([np.median(gv[a:e]) for a, e in zip(starts, ends)])
    cnt = (ends - starts).astype(np.float64)
    lo, hi = int(b[starts][0]), int(b[starts][-1])
    meta = {'path': stream.meta['path'], 'index_channel': 'DEPT', 'index_units': 'ft', 'bin_ft': str(bin_ft), 'bins': str(len(depth)),
            'bins_covered_pct': f'{100.0 * len(depth) / (hi - lo + 1):.1f}', 'samples': str(int(m.sum())),
            'how': f"the recorder's '{CH['gamma']}' while the on-bottom flag is set, binned to {bin_ft:g} ft of '{CH['bit']}': the median per bin; "
                   f"indexed to bit depth, so shallow by the sensor's distance behind the bit, which the recorder does not state",
            'source_format': 'edr'}
    return LiveStream(name=stream.name + '_gamma', source_format='edr_gamma', index_kind='depth', index=depth,
                      channels={'GR': StreamChannel(name='GR', unit='gAPI', values=med), 'GR_N': StreamChannel(name='GR_N', unit='', values=cnt)}, meta=meta)


def write_las(log: LiveStream, path: str, well: str, source: str) -> None:
    """A LAS 2.0 file of a depth-indexed stream (the form `gea dlis --to-las` writes)."""
    idx = log.index
    with open(path, 'w', encoding='utf-8') as f:
        f.write('~VERSION INFORMATION\n VERS.   2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0\n WRAP.   NO  : ONE LINE PER DEPTH STEP\n')
        f.write('~WELL INFORMATION\n')
        f.write(f" STRT.ft     {float(idx[0]):.4f} : START\n STOP.ft     {float(idx[-1]):.4f} : STOP\n STEP.ft     {float(log.meta.get('bin_ft', 0.0)):.4f} : STEP (bins; a missing bin is a gap, not a step)\n NULL.       -999.25 : NULL VALUE\n")
        f.write(f" WELL.       {well} : WELL (the recorder's job number until the operator declares the well)\n SRC .       EDR {source} : SOURCE (gea edr; {log.meta.get('how', '')})\n")
        f.write('~CURVE INFORMATION\n DEPT    .ft       : INDEX (bit depth bin)\n')
        for c in log.channels:
            f.write(f" {c:<8s}.{log.channels[c].unit:<8s} : {('gamma, median of the bin' if c == 'GR' else 'samples in the bin')}\n")
        f.write('~A\n')
        for i in range(len(idx)):
            f.write(f"{float(idx[i]):.4f}" + ''.join(f" {(-999.25 if log.channels[c].values[i] != log.channels[c].values[i] else float(log.channels[c].values[i])):.4f}" for c in log.channels) + '\n')


def gamma_lithology(log: LiveStream, cutoff: float = 0.5) -> dict:
    """The lithology-from-GR arithmetic this program already applies to a catalogue log, on the recorder's gamma log."""
    from .gamma import shale_volume, formation_flags, METHOD_LABEL
    g = log.channels['GR'].values
    vsh, meta = shale_volume(g)
    iv = formation_flags(log.index, vsh, cutoff=cutoff)
    fin = g[~np.isnan(g)]
    return {'status': 'OK', 'bins': int(len(g)), 'depth_from_ft': float(log.index[0]), 'depth_to_ft': float(log.index[-1]),
            'gr_api': {'min': round(float(fin.min()), 1), 'max': round(float(fin.max()), 1), 'p50': round(float(np.median(fin)), 1)},
            'vsh': meta, 'cutoff': cutoff, 'n_intervals': len(iv), 'sand_ft': round(sum(x['base_depth'] - x['top_depth'] for x in iv if x['flag'] == 'SAND'), 1),
            'shale_ft': round(sum(x['base_depth'] - x['top_depth'] for x in iv if x['flag'] == 'SHALE'), 1),
            'intervals': iv[:400], 'intervals_truncated': max(0, len(iv) - 400),
            'how': f"{METHOD_LABEL}; picks from the log's own 5th and 95th percentiles; a sand/shale flag per bin at a {cutoff:g} cutoff - a gamma index, "
                   "not a formation name, on a log indexed to bit depth"}


# the quantities a recorder cannot sensibly write below zero, and the one it writes negative by design
_NON_NEGATIVE = ('rop', 'wob', 'rpm', 'spp', 'hkld', 'flow', 'block', 'torque', 'spm', 'trip', 'hole', 'bit')


def observations(stream: LiveStream) -> List[dict]:
    """What the channels carry that a reader should see before trusting a number: values below zero where the quantity
    cannot be, rate-of-penetration spikes, the differential pressure's negative stretches (the recorder's reference,
    not a fault). Counted and named; nothing is altered or removed."""
    out = []
    for key in _NON_NEGATIVE:
        v = _ch(stream, key)
        if v is None:
            continue
        n = int(np.nansum(v < 0))
        if n:
            out.append({'channel': CH[key], 'what': 'below zero', 'samples': n, 'min': round(float(np.nanmin(v)), 2),
                        'note': 'a block height or depth below zero is a calibration offset in the recorder; the value is carried as written' if key in ('block', 'hole', 'bit') else 'carried as written'})
    rop = _ch(stream, 'rop')
    if rop is not None:
        n = int(np.nansum(rop > ROP_SPIKE_FT_H))
        if n:
            out.append({'channel': CH['rop'], 'what': f'above {ROP_SPIKE_FT_H:,.0f} ft/h', 'samples': n, 'max': round(float(np.nanmax(rop)), 1),
                        'note': 'instantaneous rate spikes at a connection or a depth correction; the on-bottom mean and median in the sections are the rates to read'})
    dp = _ch(stream, 'dp')
    if dp is not None:
        n = int(np.nansum(dp < 0))
        if n:
            out.append({'channel': CH['dp'], 'what': 'below zero', 'samples': n, 'min': round(float(np.nanmin(dp)), 1),
                        'note': "the recorder's differential pressure is standpipe pressure less an off-bottom reference; it is negative with the pumps off by design"})
    return out


def lines(rec: dict) -> List[str]:
    L = [f"Drilling record for {rec['well']} from {rec['source']}: {rec['rows']:,} one-second rows, {rec['start'][:16]} to {rec['end'][:16]} "
         f"({rec['span_days']} days on the clock, {rec['hours_recorded']:,} hours recorded in {rec['n_sections']} section(s))."]
    if rec.get('total_depth_md_ft') is not None:
        L.append(f"Total depth {rec['total_depth_md_ft']:,} ft MD reached {rec['reached_td'][:16]}" + (f", the recorder's TVD there {rec['tvd_at_td_ft']:,} ft" if rec.get('tvd_at_td_ft') else '')
                 + f"; {rec.get('feet_drilled', 0):,} ft drilled in {rec['on_bottom_hours']:,} hours on bottom ({rec['rotating_hours']:,} rotating)"
                 + (f", {rec['feet_per_on_bottom_hour']:,} ft per on-bottom hour" if rec.get('feet_per_on_bottom_hour') else '') + '.')
    for e in rec.get('depth_events', []):
        L.append(f"Depth event, {e['type']} at {e['at'][:16]}: {e['effect']}.")
    if len(rec.get('boreholes', [])) > 1:
        L.append('Boreholes: ' + '; '.join(f"from {b['from_ft']:,} to {b['deepest_ft']:,} ft ({b['status'].lower()})" for b in rec['boreholes'])
                 + f". Feet drilled counts every borehole ({rec.get('feet_drilled', 0):,} ft); total depth is the final borehole's.")
    if rec.get('excluded_hours') or rec.get('flag_idle_hours'):
        L.append(f"Excluded from the arithmetic: {rec.get('excluded_hours', 0):,} h of stale or re-referenced depth"
                 + (f" and {rec['flag_idle_hours']:,} h with the on-bottom flag set and nothing drilled (depth still, rate of penetration zero, {IDLE_FLAG_S // 60} min or longer)" if rec.get('flag_idle_hours') else '')
                 + f"; the recorder's on-bottom flag was set for {rec['on_bottom_flag_hours']:,} h, of which {rec['on_bottom_hours']:,} h were drilling at the bottom of the hole.")
    for s in rec['sections']:
        L.append(f"Section {s['n']}: {s['start'][:16]} to {s['end'][:16]}, {s['hours']:,} h" + (f" after a {s['gap_before_hours']:,} h gap" if s.get('gap_before_hours') else '')
                 + (f"; hole {s['hole_depth_at_start_ft']:,} to {s['hole_depth_to_ft']:,} ft ({s['feet_drilled']:,} ft)" if s.get('feet_drilled') is not None else '')
                 + f"; on bottom {s['on_bottom_hours']:,} h" + (f", ROP on bottom mean {s['rop_on_bottom_mean_ft_h']:,} ft/h" if s.get('rop_on_bottom_mean_ft_h') is not None else '')
                 + (f"; {s['connections']} connections" if s.get('connections') is not None else '') + '.')
    if rec.get('lateral'):
        la = rec['lateral']
        L.append(f"Lateral: the recorder's TVD reaches within 1% of its {la['tvd_max_ft']:,} ft maximum at {la['lateral_start_md_ft']:,} ft MD; {la['lateral_length_ft']:,} ft of hole beyond it. {la['how']}.")
    mt = rec.get('md_tvd') or {}
    if mt.get('status') == 'OK':
        L.append(f"MD-TVD table: {mt['n']} rows at {mt['step_ft']:.0f} ft steps, {mt['md_min_ft']:,} to {mt['md_max_ft']:,} ft MD; {mt['samples_without_tvd']:,} samples carry no TVD. {mt['source']}.")
    ga = rec.get('gamma') or {}
    if ga.get('status') == 'OK':
        li = ga.get('lithology') or {}
        L.append(f"Gamma log: {ga['bins']:,} one-foot bins from {ga['depth_from_ft']:,.0f} to {ga['depth_to_ft']:,.0f} ft bit depth ({ga['bins_covered_pct']:.1f}% of the span covered) from "
                 f"{ga['samples']:,} on-bottom samples - written as gamma_log.las; {ga['how']}."
                 + (f" Lithology from GR: picks {li['vsh']['gr_clean_api']} / {li['vsh']['gr_shale_api']} gAPI, {li['n_intervals']} intervals, {li['sand_ft']:,} ft flagged SAND and {li['shale_ft']:,} ft SHALE at the {li['cutoff']:g} cutoff - a gamma index, not a formation name."
                    if li.get('status') == 'OK' else (f" Lithology from GR: NOT ESTIMATED - {li.get('why', '')}." if li else '')))
    elif ga:
        L.append(f"Gamma log: {ga['status']} - {ga.get('how', '')}.")
    for o in rec.get('observations', []):
        L.append(f"Observation: {o['channel']} {o['what']} in {o['samples']:,} samples" + (f" (min {o['min']:,})" if 'min' in o else '') + (f" (max {o['max']:,})" if 'max' in o else '') + f" - {o['note']}.")
    gaps = [c for c in rec['channels'] if c['sentinel'] or c['blank'] or c['not_numeric']]
    if gaps:
        L.append('Missing values: ' + '; '.join(f"{c['channel']} {c['sentinel']:,} sentinel, {c['blank']:,} blank" + (f", {c['not_numeric']:,} not numeric" if c['not_numeric'] else '') for c in gaps) + '.')
    for n in rec['notes']:
        L.append('Note: ' + n + '.')
    L.append("Every number above is the recorder's own, read as delivered; this record does not say what was drilled through, how the well was drilled, or anything the recorder did not write.")
    return L


def document(rec: dict, program_version: str):
    """The drilling record as a client report."""
    from .client_reports import Document, Section, Table, PROGRAM_NAME
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    rid = f"EDR-{rec['well']}-{now.replace('-', '').replace(':', '')}"
    front = [['Report ID', rid], ['Well', rec['well']], ['Source file', rec['source']], ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}'],
             ['Rows', f"{rec['rows']:,}"], ['Recorded', f"{rec['start'][:19]} to {rec['end'][:19]}"]]
    secs = [Section('1', 'The well', [l for l in rec['lines'][:2]]),
            Section('2', 'Sections', [rec['lines'][i] for i in range(2, 2 + len(rec['sections']))],
                    [Table(['#', 'Start', 'End', 'Hours', 'Gap before (h)', 'Hole at start (ft)', 'Hole deepest (ft)', 'Feet drilled', 'On bottom (h)', 'Rotating (h)', 'ROP on bottom mean (ft/h)', 'Connections', 'WOB max (klbf)', 'RPM max', 'SPP max (psi)'],
                           [[s['n'], s['start'][:16], s['end'][:16], s['hours'], s.get('gap_before_hours'), s.get('hole_depth_at_start_ft'), s.get('hole_depth_to_ft'), s.get('feet_drilled'), s['on_bottom_hours'], s['rotating_hours'],
                             s.get('rop_on_bottom_mean_ft_h'), s.get('connections'), s.get('wob_max_klbf'), s.get('rpm_max'), s.get('spp_max_psi')] for s in rec['sections']], 'One row per section of the file')])]
    mt = rec.get('md_tvd') or {}
    if mt.get('status') == 'OK':
        secs.append(Section('3', 'Hole depth against the recorder\'s TVD', [l for l in rec['lines'] if l.startswith('Lateral:') or l.startswith('MD-TVD table:')],
                            [Table(['MD (ft)', 'TVD (ft)'], [[r['md_ft'], r['tvd_ft']] for r in mt['rows']], mt['source'])]))
    ga = rec.get('gamma') or {}
    if ga.get('status') == 'OK':
        li = ga.get('lithology') or {}
        secs.append(Section(str(len(secs) + 1), "The recorder's gamma log", [l for l in rec['lines'] if l.startswith('Gamma log:')],
                            ([Table(['Top (ft)', 'Base (ft)', 'Flag', 'Bins', 'Mean Vsh'], [[x['top_depth'], x['base_depth'], x['flag'], x['n_samples'], x['mean_vsh']] for x in li['intervals'][:120]],
                                    f"the first {min(120, len(li['intervals']))} of {li['n_intervals']} intervals; the whole list is in drilling_record.json")] if li.get('status') == 'OK' else [])))
    obs = rec.get('observations') or []
    secs.append(Section(str(len(secs) + 1), 'Channels', [l for l in rec['lines'] if l.startswith('Missing values:')],
                        ([Table(['Channel', 'What', 'Samples', 'Note'], [[o['channel'], o['what'], o['samples'], o['note']] for o in obs], 'Counted and named; nothing altered')] if obs else []) +
                        [Table(['Channel', 'Unit', 'Present', 'Coverage %', 'Sentinel', 'Blank', 'Not numeric', 'Min', 'Max'],
                               [[c['channel'], c['unit'], c['present'], c['coverage_pct'], c['sentinel'], c['blank'], c['not_numeric'], c['min'], c['max']] for c in rec['channels']],
                               f'A sentinel is {SENTINEL}, the value the recorder writes when it has none')]))
    secs.append(Section(str(len(secs) + 1), 'What this record will not say', [rec['lines'][-1]]))
    return Document(title=f'{PROGRAM_NAME} - Drilling Record', report_id=rid, front=front, sections=secs,
                    footer="The recorder's own numbers, read as delivered.",
                    data={k: v for k, v in rec.items() if k != 'lines'} | {'_records': [], '_catalogue_obj': None})


def write_record(stream: LiveStream, out_dir: str, program_version: str) -> dict:
    """The record beside the well's other reports: drilling_record.json / .md / .html, and the MD-TVD table as CSV."""
    from .client_reports import write
    de = depth_events(stream)
    rec = record(stream, de=de)
    os.makedirs(out_dir, exist_ok=True)
    paths = write(document(rec, program_version), out_dir, basename='drilling_record')
    with open(os.path.join(out_dir, 'drilling_record.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1, default=str)
    gl = gamma_log(stream, keep=_keep_masks(stream, de)[1])
    if gl is not None:
        lp = os.path.join(out_dir, 'gamma_log.las')
        write_las(gl, lp, stream.name, os.path.basename(stream.meta['path']))
        paths['gamma_las'] = lp
    if rec['md_tvd']['status'] == 'OK':
        tp = os.path.join(out_dir, 'md_tvd.csv')
        with open(tp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['md_ft', 'tvd_ft', 'source'])
            for r in rec['md_tvd']['rows']:
                w.writerow([r['md_ft'], r['tvd_ft'], "EDR interpolated TVD (not a survey)"])
        paths['md_tvd_csv'] = tp
    paths['record'] = rec
    return paths


def selftest(tmp: str, rows_per_section: int = 3000) -> dict:
    """A synthetic EDR export in this layout - three sections with gaps, a sentinel gamma, blank TVD, a lateral -
    written, read back and recorded. The gate runs this; the port is proven on a real export on the machine that holds one."""
    p = os.path.join(tmp, 'edr_selftest.csv')
    t = datetime(2026, 6, 1, 0, 0, 0)
    hdr = [DATE_COL, TIME_COL, 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Hook Load (klbs)', 'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'Convertible Torque (kft_lb)', 'On Bottom (unitless)',
           'Gamma (api)', 'Pump Total Strokes Rate (SPM)', 'Trip Tank Mud Volume (barrels)', 'Interpolated TVD (feet)']
    depth = 1500.0
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(hdr)
        for s in range(3):
            if s:
                t += timedelta(days=2)                         # the rig is off the well for two days between sections
            for i in range(rows_per_section):
                on = 1 if (i % 120) < 100 else 0                # a 20-second connection every 120 seconds
                if on:
                    depth += 600.0 / 3600.0                     # 600 ft/h while on bottom (a self-test cadence, not a rig's)
                tvd = depth if depth < 2000.0 else 2000.0 + (depth - 2000.0) * 0.02   # a lateral past 2,000 ft
                row = [t.strftime('%Y/%m/%d'), t.strftime('%H:%M:%S'), f'{depth:.1f}', f'{depth - (0 if on else 30):.1f}', '600.00' if on else '0.00',
                       '20.0' if on else '0.0', '60.00' if on else '0.00', '2500.00', '150.0', '400.00', '500.00', '50.00', '10.000', str(on),
                       '-999.25' if s == 0 else f'{(45.0 if depth < 2300.0 else 115.0) + (i % 7):.2f}', '100', '0.00' if i % 500 == 0 else '45.00', '' if (i % 10 == 0) else f'{tvd:.2f}']
                w.writerow(row); t += timedelta(seconds=1)
    st = read(p)
    rec = record(st)
    return {'path': p, 'stream': st.summary(), 'record': rec}


def selftest_flag(tmp: str) -> dict:
    """A synthetic export for the smaller shapes: the on-bottom flag left on for 2,000 s with the depth still and no
    rate of penetration (not drilling), a depth set forward 20 ft off bottom (understated, added), a 12 ft fall at the
    end of a section regained only in the next section (a correction - never a re-reference), and the next section
    opening on the corrected-back depth and drilling on from it (not a stale opening); then three restarts on another
    well's depth - two held to their section's end, the third left by a jump - each a stale opening, the second judged
    against the last depth the file can be trusted for, not the stale one before it. Without a sidetrack the footage
    must be total depth less the first depth on bottom: 1,230.0 - 1,000.1 = 229.9 ft, in 0.58 h drilling."""
    p = os.path.join(tmp, 'edr_flag.csv')
    hdr = [DATE_COL, TIME_COL, 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'On Bottom (unitless)']
    rows = []
    t = [datetime(2026, 8, 1, 0, 0, 0)]

    def row(hole, rop, on):
        rows.append([t[0].strftime('%Y/%m/%d'), t[0].strftime('%H:%M:%S'), f'{hole:.1f}', f'{hole:.1f}', f'{rop:.2f}', str(on)])
        t[0] += timedelta(seconds=1)

    d = 1000.0
    for _ in range(600):                                    # 1,000 -> 1,060 at 360 ft/h
        d += 0.1; row(d, 360.0, 1)
    for _ in range(2000):                                   # the flag left on: depth still, ROP zero
        row(d, 0.0, 1)
    for _ in range(60):
        row(d, 0.0, 0)
    d += 20.0                                               # set forward 20 ft off bottom
    for _ in range(60):
        row(d, 0.0, 0)
    for _ in range(600):                                    # 1,080 -> 1,140
        d += 0.1; row(d, 360.0, 1)
    for _ in range(300):                                    # set back 12 ft off bottom at the end of the section
        row(d - 12.0, 0.0, 0)
    t[0] += timedelta(days=1)
    for _ in range(60):                                     # the next section opens on 1,140 and drills on from it
        row(d, 0.0, 0)
    for _ in range(600):                                    # 1,140 -> 1,200
        d += 0.1; row(d, 360.0, 1)
    for held in (True, True, False):                        # three restarts on another well's depth: two held to the section's end, one left by a jump
        t[0] += timedelta(hours=2)
        for _ in range(300 if held else 60):
            row(9999.0, 0.0, 0)
    for _ in range(300):                                    # 1,200 -> 1,230
        d += 0.1; row(d, 360.0, 1)
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(rows)
    return {'path': p, 'record': record(read(p))}


def selftest_events(tmp: str) -> dict:
    """A synthetic export carrying one of each depth event, in order: a section that opens on another well's depth
    (STALE OPENING), a depth set back to 2,000 ft and regained by running pipe at 3,600 ft/h with the recorder's
    ROP at zero (RE-REFERENCE), a depth set back 700 ft and time-drilled at 5 ft/h for six hours (SIDETRACK), and a
    50 ft overstated depth corrected (CORRECTION). The record must find each, take TD from the final borehole
    (5,750 ft), count 1,450 ft of new hole including the 700 ft abandoned, and keep the abandoned hole out of the logs."""
    p = os.path.join(tmp, 'edr_events.csv')
    hdr = [DATE_COL, TIME_COL, 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'On Bottom (unitless)', 'Gamma (api)', 'Interpolated TVD (feet)']
    rows = []
    t = [datetime(2026, 7, 1, 0, 0, 0)]

    def row(hole, bit, rop, on, gamma=60.0):
        rows.append([t[0].strftime('%Y/%m/%d'), t[0].strftime('%H:%M:%S'), f'{hole:.1f}', f'{bit:.1f}', f'{rop:.2f}', str(on), f'{gamma:.2f}', f'{4000.0 + 0.1 * (hole - 4000.0):.2f}'])
        t[0] += timedelta(seconds=1)

    d = 5000.0
    for _ in range(1200):                                   # section A: 5,000 -> 5,200 at 600 ft/h
        d += 600.0 / 3600.0; row(d, d, 600.0, 1)
    t[0] += timedelta(days=2)
    for _ in range(300):                                    # section B opens on 9,999 ft, another well's depth, off bottom
        row(9999.0, 9999.0, 0.0, 0)
    d = 5200.0
    for _ in range(1200):                                   # then the true depth, drilled to 5,400
        d += 600.0 / 3600.0; row(d, d, 600.0, 1)
    d = 2000.0
    while d < 5400.0:                                       # re-reference: set back to 2,000 and pipe counted at 3,600 ft/h, ROP zero, flag on
        row(d, d, 0.0, 1); d += 1.0
    d = 5400.0
    for _ in range(600):                                    # real drilling 5,400 -> 5,500
        d += 600.0 / 3600.0; row(d, d, 600.0, 1)
    for _ in range(600):                                    # off bottom, pulling back
        row(d, 4900.0, 0.0, 0)
    d = 4800.0
    for _ in range(6 * 3600):                               # sidetrack: kicked off at 4,800, time-drilled at 5 ft/h for six hours
        d += 5.0 / 3600.0; row(d, d, 5.0, 1, gamma=110.0)
    while d < 5700.0:                                       # then drilled at 600 ft/h past the old depth to 5,700
        d += 600.0 / 3600.0; row(d, d, 600.0, 1, gamma=110.0)
    d = 5650.0                                              # a 50 ft overstated depth, corrected
    for _ in range(600):
        d += 600.0 / 3600.0; row(d, d, 600.0, 1, gamma=110.0)
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(rows)
    st = read(p)
    de = depth_events(st)
    return {'path': p, 'record': record(st, de=de), 'gamma': gamma_log(st, keep=_keep_masks(st, de)[1])}

