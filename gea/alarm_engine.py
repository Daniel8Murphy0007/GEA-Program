# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""alarm_engine — alarm definitions, the alarm state machine, the event log
and the alarm-management KPIs (SOW 4.2.4; ISA-18.2 / IEC 62682 practice).

Definitions are engineering configuration: each alarm names its tag, kind,
setpoint, deadband (return-to-normal hysteresis in engineering units),
on-delay (seconds the condition must persist before the alarm activates),
priority (P1 highest .. P5 lowest) and the BASIS of the setpoint (a
datasheet, a client setting, the record layer). Nothing here invents a
setpoint: `defaults_from_catalogue` derives only instrument over-range
alarms from the engineering range the tag catalogue already carries, plus a
quality alarm per tag; every other setpoint is loaded from the client's
definitions file.

State machine per alarm (ISA-18.2 states, simplified):

    NORMAL --condition true for >= on_delay--> ACTIVE_UNACKED
    ACTIVE_UNACKED --acknowledge--> ACTIVE_ACKED
    ACTIVE_* --condition false past the deadband--> NORMAL (event CLEARED;
              an unacknowledged alarm that clears is logged RTN_UNACKED)
    any --shelve--> SHELVED (suppressed, logged) --unshelve--> NORMAL

Kinds: HIGH, HIGH_HIGH, LOW, LOW_LOW (value vs setpoint with deadband),
RATE (|dv/dt| vs setpoint per second), QUALITY (sample flag not GOOD).

Event log: one line per transition - timestamp, alarm, tag, kind, priority,
event (ACTIVATED / ACKNOWLEDGED / CLEARED / RTN_UNACKED / SHELVED /
UNSHELVED), value, operator, note. Append-only JSON lines when a path is
given; always kept in memory.

KPIs over a window (targets as commonly stated in ISA-18.2 practice, printed
as targets, not as this program's claims): average alarm rate per 10 min
(target <= 1, manageable <= 2), peak 10-min rate, alarm floods (> 10 alarms
in 10 min) and time in flood, standing alarms (active longer than 24 h),
chattering alarms (>= 5 activations in 10 min), priority distribution
(target about 80 / 15 / 5 for P3 / P2 / P1 when three priorities are used),
top-10 most frequent alarms.

Headless-safe: numpy only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np

from .sample_record import SampleRecord, TagCatalogue, parse_utc

KINDS = ('HIGH', 'HIGH_HIGH', 'LOW', 'LOW_LOW', 'RATE', 'QUALITY')
PRIORITIES = ('P1', 'P2', 'P3', 'P4', 'P5')
EVENTS = ('ACTIVATED', 'ACKNOWLEDGED', 'CLEARED', 'RTN_UNACKED', 'SHELVED', 'UNSHELVED')


@dataclass
class AlarmDefinition:
    alarm_id: str
    tag_id: str
    kind: str
    priority: str = 'P3'
    setpoint: Optional[float] = None
    deadband: float = 0.0
    on_delay_s: float = 0.0
    description: str = ''
    basis: str = 'client setting'
    enabled: bool = True

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f'unknown alarm kind {self.kind}; kinds {KINDS}')
        if self.priority not in PRIORITIES:
            raise ValueError(f'unknown priority {self.priority}; priorities {PRIORITIES}')
        if self.kind != 'QUALITY' and self.setpoint is None:
            raise ValueError(f'{self.alarm_id}: {self.kind} needs a setpoint')

    def row(self) -> dict:
        return asdict(self)


def load_alarm_definitions(path) -> List[AlarmDefinition]:
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    return [AlarmDefinition(**x) for x in (d['alarms'] if isinstance(d, dict) else d)]


def write_alarm_definitions(defs: Iterable[AlarmDefinition], path) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'alarms': [d.row() for d in defs]}, f, indent=1)
    return str(path)


def defaults_from_catalogue(catalogue: TagCatalogue, quality_priority: str = 'P4',
                            range_priority: str = 'P2') -> List[AlarmDefinition]:
    """Instrument over-range alarms at the tag's engineering range (basis:
    the catalogue / datasheet) and one QUALITY alarm per tag. No process
    setpoints - those come from the client's definitions file."""
    out: List[AlarmDefinition] = []
    for r in catalogue.rows():
        tag = r['tag_id']
        lo, hi = r['eng_range_lo'], r['eng_range_hi']
        unit = r['unit']
        if hi != '':
            out.append(AlarmDefinition(alarm_id=f'{tag}.HH_RANGE', tag_id=tag, kind='HIGH_HIGH', priority=range_priority,
                                       setpoint=float(hi), deadband=abs(float(hi)) * 0.005,
                                       description=f'{tag} above instrument range {hi:g} {unit}',
                                       basis=f"engineering range upper bound ({r['limits_basis'].split(';')[0]})"))
        if lo != '':
            out.append(AlarmDefinition(alarm_id=f'{tag}.LL_RANGE', tag_id=tag, kind='LOW_LOW', priority=range_priority,
                                       setpoint=float(lo), deadband=max(abs(float(hi)) * 0.005 if hi != '' else 0.0, 0.0),
                                       description=f'{tag} below instrument range {lo:g} {unit}',
                                       basis=f"engineering range lower bound ({r['limits_basis'].split(';')[0]})"))
        out.append(AlarmDefinition(alarm_id=f'{tag}.QUALITY', tag_id=tag, kind='QUALITY', priority=quality_priority,
                                   description=f'{tag} sample quality not GOOD', basis='record layer quality rules'))
    return out


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass
class _State:
    state: str = 'NORMAL'
    cond_since: Optional[datetime] = None
    active_since: Optional[datetime] = None
    activations: List[datetime] = field(default_factory=list)
    last_value: Optional[float] = None
    last_time: Optional[datetime] = None
    shelved_until: Optional[datetime] = None
    shelved_by: str = ''
    shelved_note: str = ''


class AlarmEngine:
    """Feeds SampleRecords through every enabled definition on their tag."""

    def __init__(self, definitions: Iterable[AlarmDefinition], event_log_path: Optional[str] = None,
                 operator_positions: int = 1):
        self.defs: Dict[str, AlarmDefinition] = {d.alarm_id: d for d in definitions}
        self.by_tag: Dict[str, List[AlarmDefinition]] = {}
        for d in self.defs.values():
            self.by_tag.setdefault(d.tag_id, []).append(d)
        self.states: Dict[str, _State] = {a: _State() for a in self.defs}
        self.events: List[dict] = []
        self.log_path = event_log_path
        self.operator_positions = max(1, int(operator_positions))
        self.watermark: Optional[datetime] = None      # last sample time already processed into this log
        if event_log_path and os.path.exists(event_log_path):
            with open(event_log_path, encoding='utf-8') as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.events = [e for e in lines if e.get('event') != 'PROCESSED']    # run markers stay in the file only
            self._replay(lines)

    def _replay(self, lines: List[dict]) -> None:
        """Rebuild the alarm states from the event log, so a second run over the same
        stream adds nothing, an acknowledgement made yesterday still stands today, and
        only samples newer than the log's watermark are processed."""
        for e in lines:
            if e.get('event') == 'PROCESSED':
                self.watermark = parse_utc(e['timestamp_utc'])
                continue
            st = self.states.get(e.get('alarm_id'))
            if st is None:
                continue
            t = parse_utc(e['timestamp_utc'])
            ev = e['event']
            if ev == 'ACTIVATED':
                st.state, st.active_since = 'ACTIVE_UNACKED', t
                st.activations.append(t)
            elif ev == 'ACKNOWLEDGED':
                if st.state == 'ACTIVE_UNACKED':
                    st.state = 'ACTIVE_ACKED'
            elif ev in ('CLEARED', 'RTN_UNACKED'):
                st.state, st.active_since = 'NORMAL', None
            elif ev == 'SHELVED':
                st.state, st.cond_since = 'SHELVED', None
                st.shelved_until = parse_utc(e['until']) if e.get('until') else None
                st.shelved_by, st.shelved_note = e.get('operator', ''), e.get('note', '')
            elif ev == 'UNSHELVED':
                st.state, st.shelved_until = 'NORMAL', None
            if e.get('value') is not None and ev in ('ACTIVATED', 'CLEARED', 'RTN_UNACKED'):
                st.last_value, st.last_time = e['value'], t

    # -- events -----------------------------------------------------------------
    def _event(self, now: datetime, d: AlarmDefinition, event: str, value=None, operator: str = '', note: str = '',
               until: Optional[datetime] = None) -> dict:
        e = {'timestamp_utc': _iso(now), 'alarm_id': d.alarm_id, 'tag_id': d.tag_id, 'kind': d.kind,
             'priority': d.priority, 'event': event, 'value': (None if value is None else float(value)),
             'setpoint': d.setpoint, 'operator': operator, 'note': note}
        if until is not None:
            e['until'] = _iso(until)
        self.events.append(e)
        if self.log_path:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(e, sort_keys=True) + '\n')
        return e

    # -- condition evaluation ------------------------------------------------------
    @staticmethod
    def _condition(d: AlarmDefinition, rec: SampleRecord, st: _State, now: datetime) -> Optional[bool]:
        """True = in alarm, False = clearly normal (past deadband), None = inside the deadband (hold)."""
        if d.kind == 'QUALITY':
            return rec.quality_flag != 'GOOD'
        v = rec.value
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        sp, db = float(d.setpoint), float(d.deadband)
        if d.kind in ('HIGH', 'HIGH_HIGH'):
            return True if v >= sp else (False if v < sp - db else None)
        if d.kind in ('LOW', 'LOW_LOW'):
            return True if v <= sp else (False if v > sp + db else None)
        if d.kind == 'RATE':
            if st.last_value is None or st.last_time is None:
                return False
            dt = (now - st.last_time).total_seconds()
            if dt <= 0:
                return None
            r = abs((v - st.last_value) / dt)
            return True if r >= sp else (False if r < max(sp - db, 0.0) else None)
        return None

    def process(self, records: Iterable[SampleRecord]) -> List[dict]:
        """Process records in time order per tag. Returns the events raised."""
        start = len(self.events)
        recs = sorted(records, key=lambda r: (r.timestamp_utc, r.tag_id))
        if self.watermark is not None:
            recs = [r for r in recs if parse_utc(r.timestamp_utc) > self.watermark]
        last_time: Optional[datetime] = None
        for rec in recs:
            last_time = parse_utc(rec.timestamp_utc)
            for d in self.by_tag.get(rec.tag_id, []):
                if not d.enabled:
                    continue
                st = self.states[d.alarm_id]
                now = parse_utc(rec.timestamp_utc)
                if st.state == 'SHELVED':
                    st.last_value, st.last_time = rec.value, now
                    if st.shelved_until is not None and now >= st.shelved_until:
                        self._expire_shelf(d, st, now)          # the shelf ran out: back to NORMAL, evaluated from here
                    else:
                        continue
                cond = self._condition(d, rec, st, now)
                if cond is True:
                    if st.cond_since is None:
                        st.cond_since = now
                    if st.state == 'NORMAL' and (now - st.cond_since).total_seconds() >= d.on_delay_s:
                        st.state = 'ACTIVE_UNACKED'
                        st.active_since = now
                        st.activations.append(now)
                        self._event(now, d, 'ACTIVATED', rec.value, note=(rec.rule_fired if d.kind == 'QUALITY' else ''))
                elif cond is False:
                    st.cond_since = None
                    if st.state in ('ACTIVE_UNACKED', 'ACTIVE_ACKED'):
                        self._event(now, d, 'CLEARED' if st.state == 'ACTIVE_ACKED' else 'RTN_UNACKED', rec.value)
                        st.state = 'NORMAL'
                        st.active_since = None
                # cond None: inside the deadband - hold the current state
                if rec.value is not None and not (isinstance(rec.value, float) and np.isnan(rec.value)):
                    st.last_value, st.last_time = rec.value, now
        if last_time is not None:
            self.watermark = last_time
            if self.log_path:
                marker = {'timestamp_utc': _iso(last_time), 'alarm_id': '', 'tag_id': '', 'kind': 'PROCESSED', 'priority': '',
                          'event': 'PROCESSED', 'value': None, 'setpoint': None, 'operator': '', 'note': f'{len(recs)} samples processed'}
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(marker, sort_keys=True) + '\n')
        return self.events[start:]

    # -- operator actions -------------------------------------------------------------
    def acknowledge(self, alarm_id: str, operator: str, now: datetime, note: str = '') -> dict:
        if alarm_id not in self.states:
            raise ValueError(f'unknown alarm id {alarm_id}')
        st, d = self.states[alarm_id], self.defs[alarm_id]
        if st.state != 'ACTIVE_UNACKED':
            raise ValueError(f'{alarm_id} is {st.state}, nothing to acknowledge')
        st.state = 'ACTIVE_ACKED'
        return self._event(now, d, 'ACKNOWLEDGED', st.last_value, operator, note)

    def acknowledge_all(self, operator: str, now: datetime, priority: Optional[str] = None, note: str = '') -> List[dict]:
        """Acknowledge every ACTIVE_UNACKED alarm (optionally one priority only). Each one is its own event."""
        out = []
        for a, s in list(self.states.items()):
            if s.state == 'ACTIVE_UNACKED' and (priority is None or self.defs[a].priority == priority):
                out.append(self.acknowledge(a, operator, now, note))
        return out

    def shelve(self, alarm_id: str, operator: str, now: datetime, note: str = '', hours: Optional[float] = None) -> dict:
        """Suppress an alarm. With `hours`, the shelf expires on its own the first time a sample at or after
        the expiry is processed (an UNSHELVED event with operator 'expiry' is logged). Without, it stays
        shelved until someone unshelves it. Shelving is never silent: the event carries who, why, and until when."""
        if alarm_id not in self.states:
            raise ValueError(f'unknown alarm id {alarm_id}')
        if hours is not None and hours <= 0:
            raise ValueError('shelve hours must be positive')
        st, d = self.states[alarm_id], self.defs[alarm_id]
        st.state = 'SHELVED'
        st.cond_since = None
        st.active_since = None
        st.shelved_until = (now + timedelta(hours=hours)) if hours else None
        st.shelved_by, st.shelved_note = operator, note
        return self._event(now, d, 'SHELVED', st.last_value, operator, note, until=st.shelved_until)

    def unshelve(self, alarm_id: str, operator: str, now: datetime, note: str = '') -> dict:
        if alarm_id not in self.states:
            raise ValueError(f'unknown alarm id {alarm_id}')
        st, d = self.states[alarm_id], self.defs[alarm_id]
        if st.state != 'SHELVED':
            raise ValueError(f'{alarm_id} is {st.state}, not shelved')
        st.state, st.shelved_until = 'NORMAL', None
        return self._event(now, d, 'UNSHELVED', st.last_value, operator, note)

    def _expire_shelf(self, d: AlarmDefinition, st: _State, now: datetime) -> None:
        st.state, st.shelved_until = 'NORMAL', None
        self._event(now, d, 'UNSHELVED', st.last_value, 'expiry', 'shelf expired')

    def shelved(self) -> List[dict]:
        return [{'alarm_id': a, 'state': s.state, 'priority': self.defs[a].priority, 'tag_id': self.defs[a].tag_id,
                 'shelved_until_utc': _iso(s.shelved_until) if s.shelved_until else None, 'shelved_by': s.shelved_by,
                 'note': s.shelved_note, 'last_value': s.last_value}
                for a, s in self.states.items() if s.state == 'SHELVED']

    def active(self) -> List[dict]:
        return [{'alarm_id': a, 'state': s.state, 'priority': self.defs[a].priority, 'tag_id': self.defs[a].tag_id,
                 'active_since_utc': _iso(s.active_since) if s.active_since else None, 'last_value': s.last_value}
                for a, s in self.states.items() if s.state.startswith('ACTIVE')]

    # -- KPIs -------------------------------------------------------------------------
    def kpis(self, window_start: Optional[datetime] = None, window_end: Optional[datetime] = None,
             standing_hours: float = 24.0) -> dict:
        acts = [e for e in self.events if e['event'] == 'ACTIVATED']
        if not acts:
            return {'n_activations': 0, 'window': None, 'note': 'no activations in the window'}
        times = [parse_utc(e['timestamp_utc']) for e in acts]
        ws = window_start or min(times)
        we = window_end or max(times)
        span_min = max((we - ws).total_seconds() / 60.0, 10.0)
        acts_w = [(t, e) for t, e in zip(times, acts) if ws <= t <= we]
        n = len(acts_w)
        # 10-minute bins
        nb = int(np.ceil(span_min / 10.0))
        bins = np.zeros(nb, dtype=int)
        for t, _ in acts_w:
            bins[min(int((t - ws).total_seconds() // 600), nb - 1)] += 1
        per_pos = bins / self.operator_positions
        flood_bins = int(np.sum(per_pos > 10))
        # chattering: >= 5 activations of one alarm within any 10-minute span
        chatter = []
        for a, s in self.states.items():
            ts = sorted(s.activations)
            for i in range(len(ts)):
                if i + 4 < len(ts) and (ts[i + 4] - ts[i]).total_seconds() <= 600:
                    chatter.append(a)
                    break
        # standing alarms
        standing = [a for a, s in self.states.items() if s.active_since and (we - s.active_since).total_seconds() > standing_hours * 3600]
        # priority distribution
        pr = {p: 0 for p in PRIORITIES}
        for _, e in acts_w:
            pr[e['priority']] += 1
        counts: Dict[str, int] = {}
        for _, e in acts_w:
            counts[e['alarm_id']] = counts.get(e['alarm_id'], 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        unacked = [a for a, s in self.states.items() if s.state == 'ACTIVE_UNACKED']
        return {
            'window': [_iso(ws), _iso(we)], 'window_hours': round(span_min / 60.0, 2),
            'operator_positions': self.operator_positions,
            'n_activations': n, 'per_day': round(n / (span_min / 1440.0), 1),
            'avg_per_10min_per_position': round(float(per_pos.mean()), 3),
            'peak_per_10min_per_position': round(float(per_pos.max()), 2),
            'flood_10min_bins': flood_bins, 'pct_time_in_flood': round(100.0 * flood_bins / nb, 2),
            'standing_alarms_over_24h': standing, 'chattering_alarms': chatter,
            'active_unacknowledged': unacked,
            'priority_distribution': pr,
            'priority_distribution_pct': {p: round(100.0 * c / n, 1) for p, c in pr.items()} if n else pr,
            'top_alarms': [{'alarm_id': a, 'activations': c, 'pct_of_total': round(100.0 * c / n, 1)} for a, c in top],
            'targets': {'avg_per_10min': '<= 1 acceptable, <= 2 manageable', 'per_day': '<= 150 acceptable, <= 300 manageable',
                        'flood': '> 10 alarms in 10 min per operator position', 'chattering': '>= 5 activations in 10 min',
                        'standing': f'active > {standing_hours:g} h', 'priority_split': 'about 80 / 15 / 5 (P3 / P2 / P1) for a three-priority scheme',
                        'basis': 'targets as commonly stated in ISA-18.2 / IEC 62682 alarm-management practice; printed as targets, not as results'},
        }


def records_from_series(tag_id: str, times: List[str], values: List[float], unit: str = '',
                        flags: Optional[List[str]] = None) -> List[SampleRecord]:
    """Convenience for tests and for feeding a single series."""
    return [SampleRecord(tag_id=tag_id, timestamp_utc=t, value=v, unit=unit,
                         quality_flag=(flags[i] if flags else 'GOOD')) for i, (t, v) in enumerate(zip(times, values))]
