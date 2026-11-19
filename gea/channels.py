# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Channels: what a well's drilling record carries, how good each channel is, and who reads it.

Every tab that reads a drilling record reads it through a few channels - the hook load, the pump rate, the pit
volume - and says nothing, or says "not read", when one is not there or is not what its name says. This module
looks at the record itself, channel by channel, and answers four questions a rig-site engineer asks before
trusting a page:

SOURCE. The file the record came from, its layout, when it was last written; and each channel as the file names it,
with its unit.

QUALITY. For each channel: how many samples carry a reading and how many are the recorder's "no value"; the middle
value and the range nineteen in twenty fall in; whether it reads one value throughout (a channel that is wired but
dead - a trip tank at 0.0 for a whole well); readings dropped for a second or two between two that read; and single
readings far from both neighbours while the neighbours agree (spikes). And for the record as a whole: its cadence,
its gaps, time going backwards, the bit deeper than the hole, the hole depth stepping back - and the pump rate
reading on after the pumps have stopped, told from the standpipe pressure, which a recorder that works its rate out
from the strokes does for ten seconds to a minute.

LATENCY. How far behind the record's end each channel's last reading is; and for a record still being written, how
long ago the file was last written. That is the file's delay, read from this computer's clock and the file's own
time: the delay between the sensor and the recorder is not in a file.

USERS. Which tabs and which alert rules read each channel, by the names each of them looks for; and for each of
them, whether this well's record has what it reads. Where a rule or a tab says itself that it cannot read a record
without a channel, that is said here as CANNOT READ, with the channel it is missing.

What it will not call a measurement: a calibration. A channel that reads steadily and plausibly can still be wrong
by a constant, and nothing in a record says so. A spike here is a reading unlike both its neighbours; some are real.
"""
from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import rig_state as RS

DROPOUT_S = 3.0                 # a reading that drops out for this long or less, between two that read
SPIKE_SHARE = 0.5               # a single reading this share of the channel's range from both neighbours ...
AGREE_SHARE = 0.05              # ... while the neighbours are within this share of it of each other
COVER_BINS = 48                 # a channel's readings are shown in this many stretches of the record, first sample to last
GAP_S = 60.0                    # a break in the record longer than this is a gap
BIT_BELOW_FT = 1.0              # the bit this far below the hole's bottom is a depth that does not add up
STEP_BACK_FT = 1.0              # the hole depth falling by more than this is the depth re-set
HELD_MIN_S = 5.0                # the pump rate reading on this long after the pressure came down is counted ...
HELD_MARK_S = 30.0              # ... and marked to look at when the middle case is this long
LOOK_SHARE = 0.01               # a check says LOOK when it touches more than one sample in a hundred ...
MISSING_LOOK = 0.2              # ... and a channel with more than this share of its samples missing is marked
NOT_A_MEASUREMENT = [
    "a calibration: a channel that reads steadily and plausibly can still be wrong by a constant - a hook load not zeroed, a depth off by a joint - and nothing in a record says so",
    "a spike as an error: it is one reading unlike both its neighbours while they agree with each other; a pressure pulse or a bit taking weight can be exactly that",
    "the delay between the sensor and the recorder: what is given is how far behind the record's end a channel's last reading is, and when the file was last written by this computer's clock",
    "which channel is right when two disagree: the bit below the hole's bottom says the two depths do not add up, not which one moved",
    "a tab's result: READS means the record has the channels the tab looks for by name; whether those hold enough for the tab to judge is the tab's own to say",
    "a channel under another name: each tab looks for the names it knows, and a channel the file calls something else is not found by it - it is listed here with no reader",
]
_LOCK = threading.Lock()
_MEMO: Dict[tuple, dict] = {}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _r(v, n: int = 2):
    return None if v is None or not np.isfinite(v) else round(float(v), n)


# -- one channel ---------------------------------------------------------------------------------------------------------------------

def quality(t: np.ndarray, v: np.ndarray) -> dict:
    """What one channel's own samples say of it. `t` in seconds, rising; `v` with NaN where the recorder had no value."""
    t = np.asarray(t, dtype=np.float64); x = np.asarray(v, dtype=np.float64)
    n = len(x); ok = np.isfinite(x); m = int(ok.sum())
    out = {'samples': n, 'readings': m, 'missing': n - m, 'readings_pct': _r(100.0 * m / n, 1) if n else None, 'first_s': None, 'last_s': None, 'behind_s': None,
           'min': None, 'p025': None, 'median': None, 'p975': None, 'max': None, 'constant': None, 'dropouts': 0, 'spikes': 0, 'cover': []}
    if n:
        edges = np.unique(np.linspace(0, n, min(COVER_BINS, n) + 1).astype(np.int64))
        out['cover'] = [round(float(s), 2) for s in np.add.reduceat(ok.astype(np.int64), edges[:-1]) / np.diff(edges)]
    if not m:
        return out
    idx = np.flatnonzero(ok)
    out.update(first_s=float(t[idx[0]]), last_s=float(t[idx[-1]]), behind_s=float(t[-1] - t[idx[-1]]))
    g = x[idx]
    lo, p0, md, p1, hi = (float(q) for q in np.percentile(g, (0.0, 2.5, 50.0, 97.5, 100.0)))
    out.update(min=_r(lo, 3), p025=_r(p0, 3), median=_r(md, 3), p975=_r(p1, 3), max=_r(hi, 3))
    if hi == lo:
        out['constant'] = _r(lo, 3)
        return out
    scale = p1 - p0
    if n >= 3 and scale > 0:
        z = np.where(ok, x, 0.0)                                             # a missing reading and a zero are both a reading that is not there
        floor = 0.1 * p1 if p1 > 0 else None
        if floor is not None:
            good = ok & (x > floor); gone = ~good & (z <= 0.0)
            near = (t[2:] - t[:-2]) <= DROPOUT_S + 1e-9
            one = gone[1:-1] & good[:-2] & good[2:] & near
            out['dropouts'] = int(one.sum())
            if n >= 4:
                near2 = (t[3:] - t[:-3]) <= DROPOUT_S + 1e-9
                out['dropouts'] += int((gone[1:-2] & gone[2:-1] & good[:-3] & good[3:] & near2).sum())
        a, b, c = x[:-2], x[1:-1], x[2:]
        tri = ok[:-2] & ok[1:-1] & ok[2:] & ((t[2:] - t[:-2]) <= GAP_S)
        with np.errstate(invalid='ignore'):
            mid = 0.5 * (a + c)
            sp = tri & (np.abs(b - mid) > SPIKE_SHARE * scale) & (np.abs(a - c) < AGREE_SHARE * scale)
            if floor is not None:
                sp &= ~((b <= 0.0) & (a > floor) & (c > floor))               # a reading dropped to nothing is a dropout, counted above
        out['spikes'] = int(sp.sum())
    return out


def record_checks(t: np.ndarray, ch: Dict[str, Optional[np.ndarray]]) -> List[dict]:
    """What the channels say of each other and of the clock. `ch`: hole, bit, flow, spp, each an array or None."""
    from . import procedures as PR
    t = np.asarray(t, dtype=np.float64); n = len(t)
    out = []
    d = np.diff(t) if n > 1 else np.zeros(0)
    cad = float(np.median(d)) if len(d) else None
    back = int((d <= 0).sum()); gaps = d[d > GAP_S]
    out.append({'id': 'clock', 'words': "the record's clock", 'status': 'LOOK' if back else 'OK', 'cadence_s': _r(cad, 2), 'backwards': back, 'gaps': int(len(gaps)), 'gap_hours': _r(float(gaps.sum()) / 3600.0, 2),
                'detail': (f"a sample every {cad:g} s" if cad is not None else 'one sample') + (f"; {len(gaps)} break(s) longer than a minute, {float(gaps.sum()) / 3600.0:,.1f} h in all" if len(gaps) else '; no break longer than a minute')
                          + (f"; the time goes backwards or stands still at {back:,} sample(s)" if back else '')})
    hole, bit = ch.get('hole'), ch.get('bit')
    if hole is None or bit is None:
        out.append({'id': 'depths', 'words': 'the bit against the hole', 'status': 'NOT READ', 'detail': 'needs the hole depth and the bit depth'})
    else:
        h = np.asarray(hole, dtype=np.float64); b = np.asarray(bit, dtype=np.float64)
        both = np.isfinite(h) & np.isfinite(b)
        below = both & (b > h + BIT_BELOW_FT)
        share = float(below.sum()) / max(int(both.sum()), 1)
        worst = float(np.max((b - h)[below])) if below.any() else 0.0
        out.append({'id': 'depths', 'words': 'the bit against the hole', 'status': 'LOOK' if share > LOOK_SHARE else 'OK', 'samples': int(below.sum()), 'share_pct': _r(100.0 * share, 2), 'worst_ft': _r(worst, 1),
                    'detail': (f"the bit reads deeper than the hole by more than {BIT_BELOW_FT:g} ft at {int(below.sum()):,} sample(s) ({100.0 * share:.2f}%), by {worst:,.1f} ft at most" if below.any()
                               else 'the bit never reads deeper than the hole')})
    if hole is None:
        out.append({'id': 'hole_steps', 'words': 'the hole depth stepping back', 'status': 'NOT READ', 'detail': 'needs the hole depth'})
    else:
        h = np.asarray(hole, dtype=np.float64); g = h[np.isfinite(h)]
        dh = np.diff(g) if len(g) > 1 else np.zeros(0)
        steps = dh[dh < -STEP_BACK_FT]
        out.append({'id': 'hole_steps', 'words': 'the hole depth stepping back', 'status': 'LOOK' if len(steps) else 'OK', 'steps': int(len(steps)), 'largest_ft': _r(float(-steps.min()), 1) if len(steps) else None,
                    'detail': (f"the hole depth falls by more than {STEP_BACK_FT:g} ft {len(steps)} time(s), by {float(-steps.min()):,.1f} ft at most: a depth re-set, a sidetrack or a spike; the pages read a hole depth with those taken out"
                               if len(steps) else 'the hole depth never steps back')})
    flow, spp = ch.get('flow'), ch.get('spp')
    if flow is None or spp is None:
        out.append({'id': 'held_rate', 'words': 'the pump rate after the pumps stop', 'status': 'NOT READ', 'detail': 'needs the pump rate and the standpipe pressure'})
    else:
        raw = PR._rate_on(t, flow); on = PR.pumps_on(t, flow, spp)
        held = raw & ~on
        dtv = RS._dt(t) if n > 1 else np.ones(n)
        runs = [float(dtv[a:b].sum()) for a, b in RS._runs(held)]
        runs = [s for s in runs if s >= HELD_MIN_S]
        stops = int(np.count_nonzero(raw[:-1] & ~raw[1:])) if n > 1 else 0
        med = float(np.median(runs)) if runs else None
        out.append({'id': 'held_rate', 'words': 'the pump rate after the pumps stop', 'status': 'LOOK' if stops and len(runs) > 0.2 * stops and med >= HELD_MARK_S else 'OK', 'holds': len(runs), 'stops': stops,
                    'median_s': _r(med, 0),
                    'detail': (f"at {len(runs):,} of {stops:,} stop(s) the pump rate went on reading after the standpipe pressure had come down to the level it holds with the pumps off, for {med:,.0f} s in the "
                               "middle case, as a rate worked out from the strokes does; the Procedures tab takes the pumps as stopped by the pressure" if runs
                               else 'the pump rate stops reading when the standpipe pressure comes down')})
    return out


# -- who reads what ------------------------------------------------------------------------------------------------------------------

def _match(names: Tuple[str, ...], have: List[str]) -> Optional[str]:
    """The channel a reader finds, looking for `names` as the tabs do: the name itself, whatever its case, then a name that starts with it."""
    low = {n.lower(): n for n in have}
    for want in names:
        if want.lower() in low:
            return low[want.lower()]
    tight = {n.lower().replace(' ', ''): n for n in have}                    # 'Tool Face' is a toolface
    for want in names:
        if want.lower().replace(' ', '') in tight:
            return tight[want.lower().replace(' ', '')]
    for want in names:
        for l, n in low.items():
            if l.startswith(want.lower() + ' '):
                return n
    return None


def readers() -> List[dict]:
    """Every tab and every alert rule that reads a drilling record, with the channels each looks for - taken from the
    modules' own lists of names where they keep one. `needs`: without it the reader says itself that it cannot read
    the record. `reads`: read when it is there."""
    from . import daily_report as DR
    from . import dysfunction as DY
    from . import edr as EDR
    from . import kick as KD
    from . import roadmap as RM
    from . import stability as SB
    from . import stickslip as VB
    from . import trip_sheet as TS
    C = RS._CH
    hole, bit, hkld, block, flow, spp, rpm = (('the hole depth', (C['hole'],)), ('the bit depth', (C['bit'],)), ('the hook load', (C['hookload'],)), ('the block height', (C['block'],)),
                                               ('the pump rate', (C['flow'],)), ('the standpipe pressure', (C['spp'],)), ('the rotary speed', (C['rpm'],)))
    diff = ('the differential pressure', DY._NAMES['diff']); wob = ('the weight on bit', DY._NAMES['wob']); torque = ('the surface torque', VB.TORQUE_NAMES)
    rop = ('the rate of penetration', ('Rate Of Penetration',)); gamma = ('the gamma ray', ('Gamma',)); tvd = ('the recorder\'s TVD', ('Interpolated TVD',))
    pit = ('the active pit volume', KD.NAMES['pit']); out_ = ('the return flow', KD.NAMES['out']); tank = ('the trip tank', TS.TANK_NAMES); kq = ('the pump rate', KD.NAMES['q'])
    R = lambda id_, title, tab, module, needs=(), reads=(), event=None, either=(), what='': {'id': id_, 'title': title, 'tab': tab, 'module': module, 'needs': list(needs), 'reads': list(reads),
                                                                                             'either': list(either), 'event': event, 'kind': 'alert rule' if event else 'tab', 'what': what}
    E = EDR.CH
    return [
        R('record', 'The drilling record itself', '#/wells', 'edr', reads=(hole, bit, rop, ('the weight on bit', (E['wob'],)), rpm, spp, hkld, ('the differential pressure', (E['dp'],)), flow, block,
                                                                        ('the surface torque', (E['torque'],)), ('the on-bottom flag', (E['onb'],)), gamma, ('the pump strokes', (E['spm'],)),
                                                                        ('the trip tank', (E['trip'],)), tvd), what='its sections, the on-bottom and drilling flags every tab reads, the depth table'),
        R('rigtime', 'Rig time', '#/rigtime', 'rig_state', reads=(hkld, block, bit, hole, flow, spp, rpm), what='the hours by rig state, the connections and the trips'),
        R('benchmark', 'Benchmark', '#/benchmark', 'rig_state', reads=(hkld, block, bit, hole, flow, spp, rpm), what='each well measured the same way from its own record'),
        R('roadmap', 'Roadmap', '#/roadmap', 'roadmap', reads=(hole, ('the weight on bit', RM._NAMES['wob']), ('the rotary speed', RM._NAMES['rpm']), ('the pump rate', RM._NAMES['flow']),
                                                              ('the differential pressure', RM._NAMES['diff']), ('the standpipe pressure', RM._NAMES['spp']), ('the surface torque', RM._NAMES['torque']),
                                                              ('the recorder\'s TVD', RM._NAMES['tvd'])), what='what the fastest stands were drilled with'),
        R('daily', 'Daily report', '#/daily', 'daily_report', reads=(hole, bit, hkld, block) + tuple(('the ' + lab.lower(), names) for _k, lab, names in DR.PARAMS + DR.MUD), what="the rig's morning sheet"),
        R('procedures', 'Procedures', '#/procedures', 'procedures', reads=(flow, spp, rpm, bit, hole), what='how each connection was made and each trip started'),
        R('drillstring', 'Drill string', '#/drillstring', 'drill_string', reads=(rop, ('the weight on bit', ('Weight on Bit',)), rpm, ('the differential pressure', ('Differential Pressure',)), flow,
                                                                                ('the surface torque', ('Convertible Torque',))), what="each component's usage and the motor's statistics"),
        R('slides', 'Slide sheet', '#/slides', 'slide_sheet', reads=(bit, ('the weight on bit', ('Weight on Bit',)), rpm, spp, ('the differential pressure', ('Differential Pressure',)), flow,
                                                                    ('the gravity toolface', ('gravity toolface', 'gravity toolface fresh', 'gtf', 'toolface')),
                                                                    ('the magnetic toolface', ('magnetic toolface', 'magnetic toolface fresh', 'mtf'))),
          what='each slide and what the assembly builds rotating'),
        R('torquedrag', 'Torque & drag', '#/torquedrag', 'torque_drag', needs=(hkld, bit, hole), reads=(block, flow, spp, rpm, ('the surface torque', ('Convertible Torque',))),
          what="the model's weights against the record's"),
        R('hydraulics', 'Hydraulics & ECD', '#/hydraulics', 'hydraulics', needs=(spp, flow, bit), reads=(hole, hkld, block, rpm, tvd), what="the model's standpipe pressure against the record's"),
        R('stability', 'Wellbore stability', '#/stability', 'stability', reads=(('the pump rate', SB.FLOW_NAMES), bit, hole), what='the rig at the end of its record against the window'),
        R('hardline', 'Hard line', '#/hardline', 'hardline', reads=(hole,), event='hardline.warn, hardline.setback, hardline.crossed', what='the bit against a declared boundary'),
        R('kick', 'Kick & loss', '#/kick', 'kick', needs=(kq, pit), reads=(out_, ('the hole depth', KD.NAMES['hole']), ('the bit depth', KD.NAMES['bit'])), event='kick.flowback, kick.gain, kick.loss',
          what='flowbacks against their fingerprint, and the pits while circulating'),
        R('trips', 'Trip sheet', '#/trips', 'trip_sheet', needs=(bit,), either=(tank, ('the pits', TS.PIT_NAMES)), event='trip.fill, trip.loss, trip.unread', what='the mud the hole took or gave on every trip'),
        R('washout', 'Drilling alerts: off-bottom pressure falling', '#/dysfunction', 'dysfunction', needs=(spp, flow, bit, hole), reads=(diff,), event='drilling.washout', what='the off-bottom pressure at each connection'),
        R('stall', 'Drilling alerts: motor stall', '#/dysfunction', 'dysfunction', needs=(diff, bit, hole), reads=(flow, rpm), event='drilling.stall', what='the differential pressure against its own last five minutes'),
        R('tight', 'Drilling alerts: pick-up weight off its trend', '#/dysfunction', 'dysfunction', needs=(hkld, bit), reads=(rpm,), event='drilling.tight', what='the pick-up and slack-off weight at each connection'),
        R('stickslip', 'Drilling alerts: stick-slip', '#/dysfunction', 'stickslip', needs=(torque,), reads=(rpm,), event='drilling.stickslip', what='the surface torque swinging with one period'),
        R('bounce', 'Drilling alerts: bit bounce', '#/dysfunction', 'stickslip', needs=(hkld,), reads=(wob, hole), event='drilling.bounce', what='the hook load jumping from second to second'),
        R('sticking', 'Drilling alerts: friction factor rising', '#/dysfunction', 'sticking', needs=(hkld, bit), reads=(rpm,), event='drilling.sticking', what="the friction factor that fits the connections' weights"),
        R('porepressure', 'Pore pressure', '#/porepressure', 'pore_pressure', reads=(hole, rop, ('the weight on bit', ('Weight on Bit',)), rpm, gamma, tvd), what='the drilling exponent, where no sonic log is attached'),
        R('invoices', 'Invoice check', '#/invoices', 'invoice_check', reads=(flow, rpm, bit), what="the motor's hours against the invoice's"),
        R('opseis', 'Operations & seismicity', '#/opseis', 'op_seismicity', reads=(flow,), what='the pumping against the earthquakes'),
        R('3d', '3D', '#/3d', 'well3d', reads=(bit, gamma, rop, ('the weight on bit', ('Weight on Bit',)), rpm, ('the differential pressure', ('Differential Pressure',)), ('the total gas', ('Total Gas',))),
          what='a channel drawn along the well path'),
    ]


def read_by(names_in_record: List[str], constant: Optional[Dict[str, float]] = None) -> Tuple[List[dict], Dict[str, List[str]]]:
    """Each reader against a record's channel names: what it finds, what it does not, and its verdict; and, per channel,
    the readers that read it."""
    constant = constant or {}
    users: Dict[str, List[str]] = {n: [] for n in names_in_record}
    out = []
    for r in readers():
        def look(pairs):
            got = []
            for label, names in pairs:
                n = _match(tuple(names), names_in_record)
                got.append({'what': label, 'channel': n, 'constant': constant.get(n) if n else None, 'looks_for': list(names)[:6]})
                if n and r['title'] not in users[n]:
                    users[n].append(r['title'])
            return got
        needs, reads, either = look(r['needs']), look(r['reads']), look(r['either'])
        bare = lambda s: s[4:] if s.startswith('the ') else s
        missing = [bare(x['what']) for x in needs if not x['channel']]
        if either and not any(x['channel'] for x in either):
            missing.append(' or '.join(bare(x['what']) for x in either))
        dead = [x['what'] for x in needs + either if x['channel'] and x['constant'] is not None]
        nothing = [x['what'] for x in needs if x['channel'] and x['constant'] is not None]              # a channel it cannot do without, reading one value throughout
        live_either = [x for x in either if x['channel'] and x['constant'] is None]
        if either and any(x['channel'] for x in either) and not live_either:
            nothing.append(' and '.join(x['what'] for x in either if x['channel']))
        found = sum(1 for x in needs + reads + either if x['channel']); total = len(needs) + len(reads) + len(either)
        verdict = 'CANNOT READ' if missing else ('NOTHING TO READ' if (found == 0 or nothing) else 'READS')
        out.append({'id': r['id'], 'title': r['title'], 'tab': r['tab'], 'kind': r['kind'], 'event': r['event'], 'what': r['what'], 'verdict': verdict, 'found': found, 'of': total, 'missing': missing,
                    'dead': dead, 'needs': needs, 'either': either, 'reads': reads,
                    'words': (f"cannot read this record: it has no {', no '.join(missing)}" if missing
                              else 'none of the channels it reads is in this record' if found == 0
                              else f"finds its channels, but {', '.join(nothing)} read{'s' if len(nothing) == 1 and ' and ' not in nothing[0] else ''} one value throughout: there is nothing in {'it' if len(nothing) == 1 and ' and ' not in nothing[0] else 'them'} to read" if nothing
                              else f"reads {found} of the {total} channel(s) it looks for" + (f"; {', '.join(dead)} read{'s' if len(dead) == 1 else ''} one value throughout" if dead else ''))})
    return out, users


# -- the workspace -------------------------------------------------------------------------------------------------------------------

def _cache(ws, well_id: str):
    from . import drill_string as DS
    return DS._cache(ws, well_id, build=True)


def well(ws, well_id: str, now: Optional[datetime] = None) -> dict:
    """One well's drilling record, channel by channel (kept until the record changes; whether it is still being written is asked each time)."""
    from . import dysfunction as DY
    c = _cache(ws, well_id)
    key = (os.path.abspath(ws.path), well_id, c.meta.get('built_utc'), c.meta.get('source_mtime'), c.meta.get('source_size'))
    with _LOCK:
        m = _MEMO.get(key)
    if m is None:
        t = np.array(c.index, dtype=np.float64)
        t0 = datetime.fromisoformat(c.meta['start_time']).replace(tzinfo=None)
        iso = lambda s: None if s is None else (t0 + timedelta(seconds=float(s))).isoformat(timespec='seconds')
        chans = []; constant = {}
        for rec in c.meta['channels']:
            name = rec['name']
            q = quality(t, np.asarray(c.channel(name), dtype=np.float64))
            fl = c.flags(name)
            q.update({'name': name, 'unit': rec.get('unit') or '', 'flagged': int(np.count_nonzero(fl)) if fl is not None else 0, 'first': iso(q.pop('first_s')), 'last': iso(q['last_s'])})
            if q['constant'] is not None:
                constant[name] = q['constant']
            q['mark'] = ('NO READINGS' if not q['readings'] else 'ONE VALUE' if q['constant'] is not None else 'LOOK' if (q['missing'] > MISSING_LOOK * q['samples'] or q['behind_s'] > max(GAP_S, 0.001 * (t[-1] - t[0]))
                                                                                                              or q['dropouts'] + q['spikes'] > LOOK_SHARE * q['readings']) else 'OK')
            chans.append(q)
        names = [x['name'] for x in chans]
        g = lambda n: (np.asarray(c.channel(n), dtype=np.float64) if n else None)
        checks = record_checks(t, {'hole': g(_match((RS._CH['hole'],), names)), 'bit': g(_match((RS._CH['bit'],), names)), 'flow': g(_match((RS._CH['flow'],), names)), 'spp': g(_match((RS._CH['spp'],), names))})
        rd, users = read_by(names, constant)
        for q in chans:
            q['readers'] = users.get(q['name'], [])
        src = c.meta.get('source') or ''
        m = {'channels': chans, 'checks': checks, 'readers': rd, 'record': {'start': iso(0.0) if len(t) else None, 'end': iso(t[-1]) if len(t) else None, 'samples': int(len(t)),
                                                                           'hours': _r(float(t[-1] - t[0]) / 3600.0, 1) if len(t) > 1 else 0.0, 'sections': len(c.meta.get('sections') or []) or 1,
                                                                           'clock': "the drilling recorder's own clock"},
             'source': {'file': os.path.basename(src) if src else None, 'layout': {'edr': "a drilling recorder's export, a row a sample"}.get(c.meta.get('source_format'), c.meta.get('source_format') or 'not known'),
                        'size_mb': _r((c.meta.get('source_size') or 0) / 1e6, 1), 'mtime': c.meta.get('source_mtime')}}
        with _LOCK:
            if len(_MEMO) > 16:
                _MEMO.clear()
            _MEMO[key] = m
    w = ws.well(well_id)
    n_ = now or datetime.now(timezone.utc)
    mt = m['source']['mtime']
    live = DY.is_live({'source_mtime': mt, 'record_end': m['record']['end']}, n_) if mt and m['record']['end'] else False
    ago = max(0.0, n_.timestamp() - float(mt)) if mt else None
    out = {**m, 'well': well_id, 'display': w.get('display') or well_id, 'status': 'OK',
           'source': {**{k: v for k, v in m['source'].items() if k != 'mtime'}, 'written_utc': datetime.fromtimestamp(float(mt), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if mt else None,
                      'written_ago_s': _r(ago, 0), 'being_written': bool(live)}}
    return out


def _wells_with_record(ws) -> List[str]:
    return RS._wells_with_record(ws)


def page(ws, well_id: Optional[str] = None) -> dict:
    """The tab: one well's record channel by channel. Reading it writes nothing. Only the well shown is read."""
    out = {'generated_utc': _utc(), 'status': 'OK', 'wells': [], 'well': None, 'result': None, 'lines': [], 'not_a_measurement': list(NOT_A_MEASUREMENT),
           'limits': {'dropout_s': DROPOUT_S, 'gap_s': GAP_S, 'bit_below_ft': BIT_BELOW_FT, 'step_back_ft': STEP_BACK_FT}}
    if well_id is not None and well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    ids = _wells_with_record(ws)
    out['wells'] = [{'well': i, 'display': ws.well(i).get('display') or i} for i in ids]
    if not ids:
        out.update(status='NO DRILLING RECORD', lines=["No well of this workspace has a drilling recorder's export attached: attach one on the Wells tab, and its channels are read here."])
        return out
    pick = well_id if well_id in ids else ids[0]
    if well_id is not None and well_id not in ids:
        out['lines'] = [f"{ws.well(well_id).get('display') or well_id} has no drilling record; {ws.well(pick).get('display') or pick} is shown."]
    out['well'] = pick
    r = well(ws, pick)
    out['result'] = r
    out['lines'] = out['lines'] + _lines(r)
    return out


def _lines(r: dict) -> List[str]:
    ch = r['channels']; s = r['source']; rec = r['record']
    dead = [x['name'] for x in ch if x['mark'] == 'ONE VALUE']; none = [x['name'] for x in ch if x['mark'] == 'NO READINGS']; look = [x['name'] for x in ch if x['mark'] == 'LOOK']
    unread = [x['name'] for x in ch if not x['readers']]
    cannot = [x for x in r['readers'] if x['verdict'] == 'CANNOT READ']; empty = [x for x in r['readers'] if x['verdict'] == 'NOTHING TO READ']
    out = [f"{r['display']}: {len(ch)} channels in {s['file'] or 'the record'} ({s['layout']}), {rec['samples']:,} samples over {rec['hours']:,} h from {str(rec['start']).replace('T', ' ')} to {str(rec['end']).replace('T', ' ')} on the recorder's clock."]
    if s['being_written']:
        out.append(f"The record is still being written: the file was last written {s['written_ago_s']:,.0f} s ago by this computer's clock.")
    else:
        out.append(f"The record is not being written now: the file was last written {str(s['written_utc']).replace('T', ' ').replace('Z', ' UTC')}." if s['written_utc'] else "When the file was last written is not known.")
    if dead:
        out.append(f"{len(dead)} channel(s) read one value throughout and carry nothing: {', '.join(dead)}.")
    if none:
        out.append(f"{len(none)} channel(s) have a column and no reading: {', '.join(none)}.")
    if look:
        out.append(f"{len(look)} channel(s) to look at - missing samples, dropped readings, spikes, or ending before the record does: {', '.join(look)}.")
    for c in r['checks']:
        if c['status'] == 'LOOK':
            out.append(f"{c['words'][0].upper() + c['words'][1:]}: {c['detail']}.")
    if cannot:
        out.append("Cannot read this record: " + '; '.join(f"{x['title']} (no {', no '.join(x['missing'])})" for x in cannot) + '.')
    else:
        out.append("Every tab and alert rule that says what it cannot do without finds it in this record.")
    if empty:
        out.append("Nothing in this record for: " + '; '.join(f"{x['title']} ({x['words']})" for x in empty) + '.')
    if unread:
        out.append(f"{len(unread)} channel(s) no tab looks for by that name: {', '.join(unread)}.")
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return '\n'.join(p['lines'] or ['Channels: nothing to read'])
    out = [f"Channels - {r['display']}"] + ['  ' + l for l in p['lines']] + ['', f"  {'channel':<28s} {'unit':<8s} {'readings':>9s} {'middle':>11s} {'19 in 20 between':>26s} {'dropped':>8s} {'spikes':>7s}  mark         read by"]
    f = lambda v: '-' if v is None else f'{v:,.6g}'
    for x in r['channels']:
        out.append(f"  {x['name'][:28]:<28s} {x['unit'][:8]:<8s} {('-' if x['readings_pct'] is None else format(x['readings_pct'], '.1f') + '%'):>9s} {f(x['median']):>11s} {(f(x['p025']) + ' to ' + f(x['p975'])):>26s} "
                   f"{x['dropouts']:>8,d} {x['spikes']:>7,d}  {x['mark']:<12s} {len(x['readers'])} reader(s)")
    out += ['', '  The record as a whole:'] + [f"  {c['status']:<9s} {c['words']}: {c['detail']}" for c in r['checks']]
    out += ['', '  Who reads it:'] + [f"  {x['verdict']:<16s} {x['title']:<46s} {x['words']}" for x in r['readers']]
    return '\n'.join(out)


def write_report(ws, program_version: str, well_id: Optional[str] = None, actor: str = 'system') -> dict:
    p = page(ws, well_id)
    r = p.get('result')
    if not r:
        raise ValueError((p['lines'] or ['nothing to write'])[0])
    d = ws.dir('reports', 'channels', r['well'])
    with open(os.path.join(d, 'channels.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, 'generated_utc': p['generated_utc'], 'result': json.loads(json.dumps(r, default=str)), 'not_a_measurement': NOT_A_MEASUREMENT}, f, indent=1)
    with open(os.path.join(d, 'channels.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Channels - {r['display']}\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p)
                + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'channels.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        cols = ['name', 'unit', 'samples', 'readings', 'missing', 'flagged', 'readings_pct', 'first', 'last', 'behind_s', 'min', 'p025', 'median', 'p975', 'max', 'constant', 'dropouts', 'spikes', 'mark']
        wr.writerow(cols + ['read_by'])
        for x in r['channels']:
            wr.writerow([('' if x.get(k) is None else x.get(k)) for k in cols] + ['; '.join(x['readers'])])
    files = ['channels.json', 'channels.md', 'channels.csv']
    ws.audit(actor, 'channels.report', {'well': r['well'], 'files': files, 'channels': len(r['channels'])})
    return {'folder': d, 'files': files, 'page': p}


# -- the self-test -------------------------------------------------------------------------------------------------------------------

def selftest() -> dict:
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    t = np.arange(4000.0)
    base = 600.0 + 20.0 * np.sin(t / 50.0)
    q0 = quality(t, base)
    ck("a channel that reads throughout: every sample a reading, no dropout, no spike, its last reading at the record's end", q0['readings'] == 4000 and q0['missing'] == 0 and q0['dropouts'] == 0 and q0['spikes'] == 0
       and q0['behind_s'] == 0.0 and q0['constant'] is None and abs(q0['median'] - 600.0) < 5.0)
    x = base.copy(); x[[100, 900]] = 0.0; x[1500:1502] = np.nan; x[2000:2010] = 0.0
    q1 = quality(t, x)
    ck("two readings dropped for a second and one for two seconds are three dropouts; ten seconds of nothing is not one", q1['dropouts'] == 3 and q1['missing'] == 2 and q1['spikes'] == 0)
    y = base.copy(); y[[300, 1200, 2500]] = 2000.0; y[3000:3200] = 900.0
    q2 = quality(t, y)
    ck("three single readings far from both neighbours are three spikes; a step that stays is none", q2['spikes'] == 3 and q2['dropouts'] == 0)
    q3 = quality(t, np.full(4000, 39.7)); q4 = quality(t, np.full(4000, np.nan))
    ck("a channel at 39.7 throughout reads one value; a column of no values has no reading", q3['constant'] == 39.7 and q3['readings'] == 4000 and q4['readings'] == 0 and q4['median'] is None and q4['constant'] is None)
    z = base.copy(); z[3400:] = np.nan
    q5 = quality(t, z)
    ck("a channel that stops six hundred seconds before the record does is that far behind its end, and its last stretches of the record are empty", q5['behind_s'] == 600.0 and q5['readings'] == 3400
       and len(q5['cover']) == COVER_BINS and q5['cover'][0] == 1.0 and q5['cover'][-1] == 0.0 and abs(sum(q5['cover']) / COVER_BINS - 0.85) < 0.01 and q0['cover'] == [1.0] * COVER_BINS)
    q6 = quality(t[::10], x[::10])
    ck("at a sample every ten seconds nothing is short enough to be a dropout", q6['dropouts'] == 0)
    flow = np.where((t % 1000) < 700, 600.0, 0.0); spp = flow * 4.5
    held = flow.copy()
    for k in range(4):
        held[k * 1000 + 700:k * 1000 + 740] = 600.0                              # the rate reads on for forty seconds after each stop
    hole = 9000.0 + np.cumsum(np.where(flow > 0, 0.05, 0.0)); bit = np.minimum(hole, hole - np.where(flow > 0, 0.0, 30.0))
    c0 = {x_['id']: x_ for x_ in record_checks(t, {'hole': hole, 'bit': bit, 'flow': flow, 'spp': spp})}
    c1 = {x_['id']: x_ for x_ in record_checks(t, {'hole': hole, 'bit': bit, 'flow': held, 'spp': spp})}
    brief = flow.copy()
    for k in range(4):
        brief[k * 1000 + 700:k * 1000 + 712] = 600.0                             # ... and for twelve
    c1b = {x_['id']: x_ for x_ in record_checks(t, {'hole': hole, 'bit': bit, 'flow': brief, 'spp': spp})}
    ck("a pump rate that stops with the pressure is OK; one that reads on for forty seconds after each of four stops is found at all four, forty seconds each, and marked; one that reads on for twelve is counted and not marked",
       c0['held_rate']['status'] == 'OK' and c0['held_rate']['holds'] == 0 and c1['held_rate']['status'] == 'LOOK' and c1['held_rate']['holds'] == 4 and abs(c1['held_rate']['median_s'] - 40.0) <= 2.0
       and c1b['held_rate']['status'] == 'OK' and c1b['held_rate']['holds'] == 4 and abs(c1b['held_rate']['median_s'] - 12.0) <= 2.0)
    stepped = hole.copy(); stepped[2000:] -= 40.0
    bad_bit = np.minimum(stepped, stepped - np.where(flow > 0, 0.0, 30.0)); bad_bit[500:700] = stepped[500:700] + 12.0
    tg = t.copy(); tg[2000:] += 7200.0; tb = t.copy(); tb[3000] = tb[2999]
    c2 = {x_['id']: x_ for x_ in record_checks(tg, {'hole': stepped, 'bit': bad_bit, 'flow': None, 'spp': None})}
    c3 = {x_['id']: x_ for x_ in record_checks(tb, {'hole': None, 'bit': None, 'flow': None, 'spp': None})}
    ck("the bit twelve feet below the hole for two hundred samples, a hole depth stepping back forty feet and a two-hour break are each found and sized",
       c2['depths']['status'] == 'LOOK' and c2['depths']['samples'] == 200 and c2['depths']['worst_ft'] == 12.0 and c2['hole_steps']['steps'] == 1 and c2['hole_steps']['largest_ft'] == 40.0
       and c2['clock']['gaps'] == 1 and c2['clock']['gap_hours'] == 2.0 and c0['depths']['status'] == 'OK' and c0['hole_steps']['status'] == 'OK' and c0['clock']['status'] == 'OK')
    ck("a clock that stands still is found; with no channels the checks that need them say NOT READ", c3['clock']['status'] == 'LOOK' and c3['clock']['backwards'] == 1
       and [c3[k]['status'] for k in ('depths', 'hole_steps', 'held_rate')] == ['NOT READ'] * 3 and c2['held_rate']['status'] == 'NOT READ')
    full = ['Hole Depth', 'Bit Depth', 'Hook Load', 'Block Height', 'Total Pump Output', 'Standpipe Pressure', 'Rotary RPM', 'Differential Pressure', 'Convertible Torque', 'Weight on Bit',
            'Rate Of Penetration', 'Pit Volume Active', 'Return Flow', 'Trip Volume Active', 'Gamma', 'Interpolated TVD']
    rd, users = read_by(full); V = {x_['id']: x_ for x_ in rd}
    ck("a record with every channel is read by every tab and alert rule", all(x_['verdict'] == 'READS' for x_ in rd) and len(rd) >= 20 and all(users[n] for n in full))
    rd2, users2 = read_by([n for n in full if n not in ('Pit Volume Active', 'Trip Volume Active', 'Convertible Torque', 'Differential Pressure')] + ['Mud Motor Temp']); V2 = {x_['id']: x_ for x_ in rd2}
    ck("without a pit volume, a trip tank, a torque and a differential pressure: Kick & loss, the Trip sheet, stick-slip and the motor stall cannot read it and say what is missing; the rest still read",
       [V2[k]['verdict'] for k in ('kick', 'trips', 'stickslip', 'stall')] == ['CANNOT READ'] * 4 and V2['kick']['missing'] == ['active pit volume'] and V2['trips']['missing'] == ['trip tank or pits']
       and V2['stall']['missing'] == ['differential pressure'] and V2['kick']['words'] == 'cannot read this record: it has no active pit volume' and all(V2[k]['verdict'] == 'READS' for k in ('rigtime', 'washout', 'tight', 'bounce', 'procedures', 'hydraulics', 'torquedrag'))
       and users2['Mud Motor Temp'] == [])
    rd3, _u = read_by(['hole depth', 'BIT DEPTH', 'Pit Volume Active', 'Flow In', 'Trip Tank 1 Volume'], {'Trip Tank 1 Volume': 0.0}); V3 = {x_['id']: x_ for x_ in rd3}
    rd4, _u = read_by(['Bit Depth', 'Trip Tank 1 Volume'], {'Trip Tank 1 Volume': 0.0}); V4 = {x_['id']: x_ for x_ in rd4}
    ck("a trip tank at one value throughout with no pits beside it: the Trip sheet finds its channels and has nothing to read", V4['trips']['verdict'] == 'NOTHING TO READ' and 'one value throughout' in V4['trips']['words'])
    ck("a tab finds a channel by the names it knows, whatever their case: Kick & loss finds a pump rate called Flow In, Rig time does not; a trip tank at one value throughout beside pits that move is found and said to be dead",
       V3['kick']['verdict'] == 'READS' and next(x_ for x_ in V3['kick']['needs'] if x_['what'] == 'the pump rate')['channel'] == 'Flow In'
       and next(x_ for x_ in V3['rigtime']['reads'] if x_['what'] == 'the pump rate')['channel'] is None and V3['trips']['verdict'] == 'READS' and V3['trips']['dead'] == ['the trip tank'] and V3['record']['found'] == 2)
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'made channels with known faults, and made lists of channel names'}
