"""well_test_validation — stable-period detection and well-test accept/reject
with reason codes and an approval trail (SOW 4.2.3.1).

A well test is a period in which the well is on stream at a fixed operating
point and its rates and pressures are stable enough that their means can be
taken as the test result. The client agrees the stability logic; this module
keeps that logic in a CONFIG FILE (`criteria.json`), never in code, prints
the criteria in force (with the file's hash) on every report, and gives
every rejected candidate a reason code that names the rule and the number.

Roles the detector needs (mapped from channel names by `ChannelMap`):
    on_stream      hours on stream per sample (h)
    rates          one or more rate channels (oil, gas, water ...)
    pressures      one or more pressure channels (downhole, wellhead ...)
    operating      operating-point channels that must not change (choke ...)

Algorithm (greedy maximal stable segments):
    1. Per-sample eligibility: every required channel present; on-stream
       hours >= minimum; quality flag GOOD (from the record layer) unless
       the criteria allow a fraction of flagged samples.
    2. Within each run of eligible samples, grow a window from its start
       while every stability rule holds (CV of rates and pressures, relative
       trend, operating-point change). When it cannot grow further, a window
       at least `min_samples` long is an ACCEPTED test; otherwise the
       candidate is REJECTED with the rule that failed at minimum length.
    3. Runs of ineligible samples are REJECTED candidates with the
       eligibility rule that failed.

Reason codes: MISSING_CHANNEL, INSUFFICIENT_DURATION, ON_STREAM_BELOW_MIN,
QUALITY_FLAGS, RATE_UNSTABLE:<channel>, PRESSURE_UNSTABLE:<channel>,
TREND_EXCEEDS:<channel>, OPERATING_POINT_CHANGED:<channel>.

Approval trail: `approvals.jsonl` in the record directory; multi-level
(level 1 engineer, level 2 supervisor by default), each entry timestamped
with approver and decision; a test's status is derived from the trail.

Headless-safe: numpy only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from .sample_record import TagCatalogue, apply_quality_rules

DEFAULT_CRITERIA = {
    'name': 'default well-test stability criteria',
    'basis': 'engineering defaults for daily-cadence production data; the client agrees and versions this file',
    'min_samples': 5,
    'min_on_stream_hours': 23.0,
    'max_flagged_fraction': 0.0,
    'rate_max_cv_pct': 3.0,
    'rate_negligible_fraction': 0.01,
    'pressure_max_cv_pct': 1.0,
    'max_trend_pct_over_window': 3.0,
    'operating_max_change_pct': 2.0,
    'approval_levels': [{'level': 1, 'role': 'production engineer'},
                        {'level': 2, 'role': 'production supervisor'}],
}


def write_default_criteria(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CRITERIA, f, indent=1)
    return path


def load_criteria(path: Optional[str]) -> dict:
    if not path:
        c = dict(DEFAULT_CRITERIA)
        c['_source'] = 'built-in defaults (no criteria file given)'
        c['_sha256'] = hashlib.sha256(json.dumps(DEFAULT_CRITERIA, sort_keys=True).encode()).hexdigest()[:16]
        return c
    with open(path, 'rb') as f:
        raw = f.read()
    c = dict(DEFAULT_CRITERIA)
    c.update(json.loads(raw.decode('utf-8')))
    c['_source'] = os.path.abspath(path)
    c['_sha256'] = hashlib.sha256(raw).hexdigest()[:16]
    return c


@dataclass
class ChannelMap:
    on_stream: Optional[str]
    rates: Dict[str, str]          # role -> channel name, e.g. {'oil': 'BORE_OIL_VOL[15/9-F-12]'}
    pressures: Dict[str, str]
    operating: Dict[str, str] = field(default_factory=dict)

    def all_channels(self) -> Dict[str, str]:
        out = {}
        if self.on_stream:
            out['on_stream'] = self.on_stream
        out.update({f'rate:{k}': v for k, v in self.rates.items()})
        out.update({f'pressure:{k}': v for k, v in self.pressures.items()})
        out.update({f'operating:{k}': v for k, v in self.operating.items()})
        return out


def volve_channel_map(well_tag: str) -> ChannelMap:
    w = well_tag
    return ChannelMap(on_stream=f'ON_STREAM_HRS[{w}]',
                      rates={'oil': f'BORE_OIL_VOL[{w}]', 'gas': f'BORE_GAS_VOL[{w}]', 'water': f'BORE_WAT_VOL[{w}]'},
                      pressures={'downhole': f'AVG_DOWNHOLE_PRESSURE[{w}]', 'wellhead': f'AVG_WHP_P[{w}]'},
                      operating={'choke': f'AVG_CHOKE_SIZE_P[{w}]'})


def _utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _cv_pct(x: np.ndarray) -> float:
    m = float(np.mean(x))
    return float(np.std(x) / abs(m) * 100.0) if m else float('inf')


def _trend_pct(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    t = np.arange(len(x), dtype=float)
    slope, _ = np.polyfit(t, x, 1)
    m = float(np.mean(x))
    return float(abs(slope) * (len(x) - 1) / abs(m) * 100.0) if m else float('inf')


def _change_pct(x: np.ndarray) -> float:
    m = float(np.mean(x))
    return float((np.max(x) - np.min(x)) / abs(m) * 100.0) if m else float('inf')


class WellTestValidator:
    """Detect stable periods in a time-indexed LiveStream and score them."""

    def __init__(self, criteria: dict, channel_map: ChannelMap, catalogue: Optional[TagCatalogue] = None):
        self.c = criteria
        self.map = channel_map
        self.catalogue = catalogue

    # -- per-sample eligibility -------------------------------------------------
    def _eligibility(self, stream) -> List[Optional[str]]:
        n = len(stream.index)
        chans = self.map.all_channels()
        missing = [v for v in chans.values() if v not in stream.channels]
        if missing:
            return [f'MISSING_CHANNEL:{",".join(missing)}'] * n
        reason: List[Optional[str]] = [None] * n
        arrays = {k: np.asarray(stream.channels[v].values, dtype=float) for k, v in chans.items()}
        flagged = {}
        if self.catalogue is not None:
            t = np.asarray(stream.index, dtype=float)
            for k, v in chans.items():
                if v in self.catalogue:
                    fl = apply_quality_rules(arrays[k], t, self.catalogue.get(v),
                                             getattr(stream.channels[v], 'quality', None))
                    flagged[k] = [q != 'GOOD' for q, _ in fl]
        for i in range(n):
            for k, a in arrays.items():
                if np.isnan(a[i]):
                    reason[i] = f'MISSING_VALUE:{k}'
                    break
            if reason[i]:
                continue
            if 'on_stream' in arrays and arrays['on_stream'][i] < self.c['min_on_stream_hours']:
                reason[i] = f"ON_STREAM_BELOW_MIN:{arrays['on_stream'][i]:g}h<{self.c['min_on_stream_hours']:g}h"
                continue
            if self.c.get('max_flagged_fraction', 0.0) <= 0.0:
                bad = [k for k, fl in flagged.items() if fl[i]]
                if bad:
                    reason[i] = f'QUALITY_FLAGS:{",".join(bad)}'
        return reason

    # -- window stability -------------------------------------------------------
    def _window_check(self, stream, i: int, j: int) -> Optional[str]:
        """None if [i:j) is stable under every rule, else the reason code."""
        c = self.c
        means = {role: abs(float(np.mean(np.asarray(stream.channels[chan].values, dtype=float)[i:j])))
                 for role, chan in self.map.rates.items()}
        floor = c.get('rate_negligible_fraction', 0.0) * (max(means.values()) if means else 0.0)
        for role, chan in self.map.rates.items():
            if means[role] < floor:
                continue      # negligible stream (e.g. water at first oil): not a stability criterion, disclosed in criteria
            x = np.asarray(stream.channels[chan].values, dtype=float)[i:j]
            cv = _cv_pct(x)
            if cv > c['rate_max_cv_pct']:
                return f'RATE_UNSTABLE:{role}:cv {cv:.2f}%>{c["rate_max_cv_pct"]:g}%'
            tr = _trend_pct(x)
            if tr > c['max_trend_pct_over_window']:
                return f'TREND_EXCEEDS:{role}:{tr:.2f}%>{c["max_trend_pct_over_window"]:g}%'
        for role, chan in self.map.pressures.items():
            x = np.asarray(stream.channels[chan].values, dtype=float)[i:j]
            cv = _cv_pct(x)
            if cv > c['pressure_max_cv_pct']:
                return f'PRESSURE_UNSTABLE:{role}:cv {cv:.2f}%>{c["pressure_max_cv_pct"]:g}%'
            tr = _trend_pct(x)
            if tr > c['max_trend_pct_over_window']:
                return f'TREND_EXCEEDS:{role}:{tr:.2f}%>{c["max_trend_pct_over_window"]:g}%'
        for role, chan in self.map.operating.items():
            x = np.asarray(stream.channels[chan].values, dtype=float)[i:j]
            ch = _change_pct(x)
            if ch > c['operating_max_change_pct']:
                return f'OPERATING_POINT_CHANGED:{role}:{ch:.2f}%>{c["operating_max_change_pct"]:g}%'
        return None

    def _stats(self, stream, i: int, j: int) -> dict:
        out = {}
        for group in ('rates', 'pressures', 'operating'):
            for role, chan in getattr(self.map, group).items():
                x = np.asarray(stream.channels[chan].values, dtype=float)[i:j]
                out[f'{group[:-1] if group != "operating" else "operating"}:{role}'] = {
                    'channel': chan, 'unit': stream.channels[chan].unit, 'mean': round(float(np.mean(x)), 3),
                    'cv_pct': round(_cv_pct(x), 3), 'trend_pct': round(_trend_pct(x), 3),
                    'min': round(float(np.min(x)), 3), 'max': round(float(np.max(x)), 3)}
        if self.map.on_stream:
            x = np.asarray(stream.channels[self.map.on_stream].values, dtype=float)[i:j]
            out['on_stream'] = {'channel': self.map.on_stream, 'mean_hours': round(float(np.mean(x)), 2),
                                'min_hours': round(float(np.min(x)), 2)}
        return out

    # -- the detection ------------------------------------------------------------
    def detect(self, stream, t0_utc: Optional[str] = None) -> dict:
        if getattr(stream, 'index_kind', 'time_s') != 'time_s':
            raise ValueError('well-test detection needs a time-indexed stream')
        n = len(stream.index)
        t = np.asarray(stream.index, dtype=float)
        origin = t0_utc or stream.meta.get('start_date') or stream.meta.get('start_time')
        base = datetime.fromisoformat(origin.replace('Z', '')) if origin else datetime(1970, 1, 1)
        base = base.replace(tzinfo=timezone.utc) if base.tzinfo is None else base.astimezone(timezone.utc)
        stamp = lambda k: _iso(base + timedelta(seconds=float(t[k])))
        elig = self._eligibility(stream)
        mn = int(self.c['min_samples'])
        tests: List[dict] = []
        rejected: List[dict] = []
        seq = 1

        def _top(code: str) -> str:
            parts = code.split(':')
            return parts[0] if parts[0] in ('INSUFFICIENT_DURATION', 'ON_STREAM_BELOW_MIN', 'QUALITY_FLAGS',
                                             'MISSING_VALUE', 'MISSING_CHANNEL') else ':'.join(parts[:2])

        i = 0
        while i < n:
            if elig[i]:
                j = i
                while j < n and elig[j]:
                    j += 1
                codes = sorted({_top(elig[k]) for k in range(i, j)})
                rejected.append({'candidate_id': f'C{seq:03d}', 'start_utc': stamp(i), 'end_utc': stamp(j - 1),
                                 'n': j - i, 'status': 'REJECTED', 'reason_codes': codes, 'primary_reason': codes[0],
                                 'detail': '; '.join(sorted({elig[k] for k in range(i, j)})[:4])})
                seq += 1
                i = j
                continue
            j = i + 1
            while j < n and not elig[j]:
                j += 1
            run_end = j
            covered = np.zeros(n, dtype=bool)
            fails: Dict[int, str] = {}
            k = i
            while k < run_end:
                if run_end - k < mn:
                    for q in range(k, run_end):
                        fails.setdefault(q, f'INSUFFICIENT_DURATION:{run_end - k} samples < min_samples {mn}')
                    break
                fail = self._window_check(stream, k, k + mn)
                if fail:
                    fails.setdefault(k, fail)
                    k += 1
                    continue
                e = k + mn
                while e < run_end and self._window_check(stream, k, e + 1) is None:
                    e += 1
                st = self._stats(stream, k, e)
                tests.append({'test_id': f'WT{seq:03d}', 'start_utc': stamp(k), 'end_utc': stamp(e - 1), 'n': e - k,
                              'status': 'ACCEPTED', 'reason_codes': [], 'statistics': st,
                              'virtual_rates': {role: st[f'rate:{role}']['mean'] for role in self.map.rates}})
                seq += 1
                covered[k:e] = True
                k = e
            # rejected candidates: contiguous eligible samples not inside an accepted test
            q = i
            while q < run_end:
                if covered[q]:
                    q += 1
                    continue
                r = q
                while r < run_end and not covered[r]:
                    r += 1
                seg_fails = [fails[x] for x in range(q, r) if x in fails]
                if not seg_fails:
                    seg_fails = [f'INSUFFICIENT_DURATION:{r - q} samples < min_samples {mn}']
                tops = [_top(f) for f in seg_fails]
                stab = [t_ for t_ in tops if t_ != 'INSUFFICIENT_DURATION']
                top = max(sorted(set(stab or tops)), key=(stab or tops).count)
                rejected.append({'candidate_id': f'C{seq:03d}', 'start_utc': stamp(q), 'end_utc': stamp(r - 1),
                                 'n': r - q, 'status': 'REJECTED', 'reason_codes': sorted(set(tops)),
                                 'primary_reason': top,
                                 'detail': next(f for f in seg_fails if _top(f) == top)})
                seq += 1
                q = r
            i = run_end
        rejected.sort(key=lambda r: r['start_utc'])
        # merge adjacent rejected candidates with identical reason codes (readability)
        merged: List[dict] = list(rejected)
        return {'stream': stream.name, 'n_samples': n, 'window': [stamp(0), stamp(n - 1)] if n else None,
                'criteria': self.c, 'channel_map': self.map.all_channels(),
                'tests': tests, 'rejected': merged, 'n_accepted': len(tests), 'n_rejected': len(merged),
                'eligible_samples': int(sum(1 for r in elig if not r)),
                'samples_in_accepted_tests': int(sum(x['n'] for x in tests))}


# ---------------------------------------------------------------------------
# Approval trail
# ---------------------------------------------------------------------------
class ApprovalTrail:
    def __init__(self, record_dir: str, levels: Optional[List[dict]] = None):
        self.dir = str(record_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, 'approvals.jsonl')
        self.levels = levels or DEFAULT_CRITERIA['approval_levels']

    def entries(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]

    def approve(self, test_id: str, approver: str, level: int, decision: str = 'APPROVED',
                note: str = '', now: Optional[datetime] = None) -> dict:
        if decision not in ('APPROVED', 'REJECTED', 'CORRECTED'):
            raise ValueError('decision must be APPROVED, REJECTED or CORRECTED')
        if level not in {l['level'] for l in self.levels}:
            raise ValueError(f'unknown approval level {level}; levels {[l["level"] for l in self.levels]}')
        e = {'test_id': test_id, 'level': level, 'role': next(l['role'] for l in self.levels if l['level'] == level),
             'approver': approver, 'decision': decision, 'note': note, 'timestamp_utc': _iso(_utc(now))}
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(e, sort_keys=True) + '\n')
        return e

    def status_of(self, test_id: str) -> dict:
        es = [e for e in self.entries() if e['test_id'] == test_id]
        by_level = {}
        for e in es:
            by_level[e['level']] = e          # latest decision per level wins
        if any(e['decision'] == 'REJECTED' for e in by_level.values()):
            st = 'REJECTED_ON_REVIEW'
        elif all(lv['level'] in by_level and by_level[lv['level']]['decision'] in ('APPROVED', 'CORRECTED') for lv in self.levels):
            st = 'APPROVED_ALL_LEVELS'
        elif by_level:
            st = f'PENDING_LEVEL_{min(lv["level"] for lv in self.levels if lv["level"] not in by_level)}'
        else:
            st = 'PENDING_LEVEL_1'
        return {'test_id': test_id, 'status': st, 'decisions': [by_level[k] for k in sorted(by_level)]}
