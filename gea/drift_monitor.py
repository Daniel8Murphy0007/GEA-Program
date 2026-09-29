# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""drift_monitor — drift evaluation as a scheduled job with a log.

The reconciler classifies; this module runs it on a cadence, keeps the
record, ages the result into a staleness state, and turns a
CALIBRATION_OFFSET into a change-log entry with before/after coefficients
that a named approver applies - or that the annual re-fit cap blocks.

State lives in one directory as append-only JSON lines plus one small JSON
state file, so the client can read every evaluation and every change with
any text tool:

    <log_dir>/evaluations.jsonl   one line per station per evaluation
    <log_dir>/change_log.jsonl    PROPOSED / APPLIED / REJECTED / BLOCKED entries
    <log_dir>/state.json          applied offset corrections per station,
                                  last evaluation, re-fit count per year

Clock: every public method takes `now` (aware UTC datetime) so a scheduled
run, a replay and an acceptance test are the same code path with an injected
clock. Nothing here reads the wall clock unless `now` is omitted.

SLA clocks (business days from the evaluation that detected drift):
notify 1, fallback 2, re-fit/redeploy 10; evaluation cadence 24 h; safety
models revert immediately (not modelled here - no safety model in scope).
Re-fits per station per calendar year are capped (default 4, SOW 4.2.26);
a proposal beyond the cap is logged BLOCKED_ANNUAL_LIMIT, never applied.

Headless-safe: numpy only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from .reconciler import Reconciler, ReconcilerConfig
from .ports import LiveStream, StreamChannel

EVALUATE_EVERY_H = 24.0
NOTIFY_BD, FALLBACK_BD, REFIT_BD = 1, 2, 10
MAX_REFITS_PER_YEAR = 4
DRIFT_CLASSES = ('CALIBRATION_OFFSET', 'UNEXPLAINED_OFFSET', 'UNEXPLAINED_TREND')


def _utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(s: str) -> datetime:
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1]
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def business_days_after(start: datetime, n: int) -> datetime:
    d, added = start, 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


@dataclass
class MonitorConfig:
    evaluate_every_h: float = EVALUATE_EVERY_H
    max_refits_per_year: int = MAX_REFITS_PER_YEAR
    notify_bd: int = NOTIFY_BD
    fallback_bd: int = FALLBACK_BD
    refit_bd: int = REFIT_BD


class DriftMonitor:
    """One well's drift evaluations, on a cadence, with the record."""

    def __init__(self, well_config, log_dir: str, config: MonitorConfig | None = None,
                 reconciler_config: ReconcilerConfig | None = None, well_name: str = 'well'):
        self.well = well_config
        self.well_name = well_name
        self.cfg = config or MonitorConfig()
        self.rcfg = reconciler_config
        self.log_dir = str(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
        self.state_path = os.path.join(self.log_dir, 'state.json')
        self.eval_path = os.path.join(self.log_dir, 'evaluations.jsonl')
        self.change_path = os.path.join(self.log_dir, 'change_log.jsonl')
        self.state = self._load_state()

    # -- state ----------------------------------------------------------------
    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding='utf-8') as f:
                return json.load(f)
        return {'well': self.well_name, 'corrections_psi': {}, 'last_evaluation': None,
                'evaluation_count': 0, 'refits_applied': {}, 'next_entry_seq': 1}

    def _save_state(self) -> None:
        with open(self.state_path, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=1, sort_keys=True)

    def _append(self, path: str, entry: dict) -> None:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + '\n')

    def _read(self, path: str) -> List[dict]:
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]

    def _entry_id(self, prefix: str, now: datetime) -> str:
        seq = self.state['next_entry_seq']
        self.state['next_entry_seq'] = seq + 1
        return f"{prefix}-{now.strftime('%Y%m%dT%H%M%SZ')}-{seq:04d}"

    # -- corrections applied to the live leg ------------------------------------
    def corrected_stream(self, stream: LiveStream) -> LiveStream:
        """The live stream with every APPLIED offset correction subtracted
        from its station channel (the correction is logged; the raw file is
        never rewritten)."""
        corr = self.state.get('corrections_psi', {})
        if not corr:
            return stream
        channels: Dict[str, StreamChannel] = {}
        for name, ch in stream.channels.items():
            c = float(corr.get(name, 0.0))
            channels[name] = replace(ch, values=np.asarray(ch.values, dtype=float) - c) if c else ch
        meta = dict(stream.meta)
        meta['corrections_applied_psi'] = json.dumps({k: v for k, v in corr.items() if k in stream.channels})
        return LiveStream(name=stream.name, source_format=stream.source_format, index_kind=stream.index_kind,
                          index=stream.index, channels=channels, meta=meta)

    # -- scheduling ---------------------------------------------------------------
    def next_due(self) -> Optional[datetime]:
        last = self.state.get('last_evaluation')
        if not last:
            return None
        return _parse(last['timestamp_utc']) + timedelta(hours=self.cfg.evaluate_every_h)

    def staleness(self, now: Optional[datetime] = None) -> dict:
        now = _utc(now)
        last = self.state.get('last_evaluation')
        if not last:
            return {'status': 'NEVER_EVALUATED', 'age_h': None, 'cadence_h': self.cfg.evaluate_every_h,
                    'next_due_utc': None, 'as_of_utc': _iso(now)}
        age_h = (now - _parse(last['timestamp_utc'])).total_seconds() / 3600.0
        due = self.next_due()
        return {'status': 'CURRENT' if age_h <= self.cfg.evaluate_every_h else 'STALE',
                'age_h': round(age_h, 2), 'cadence_h': self.cfg.evaluate_every_h,
                'last_evaluation_utc': last['timestamp_utc'], 'last_evaluation_id': last['evaluation_id'],
                'next_due_utc': _iso(due) if due else None,
                'overdue_h': round(max(0.0, age_h - self.cfg.evaluate_every_h), 2), 'as_of_utc': _iso(now)}

    def run_scheduled(self, stream: LiveStream, station_map: Optional[Dict[str, float]] = None,
                      now: Optional[datetime] = None, force: bool = False) -> dict:
        """The scheduled-job entry point: evaluate when due (or forced), else
        record that the run was not due and return the staleness state."""
        now = _utc(now)
        due = self.next_due()
        if force or due is None or now >= due:
            return self.evaluate(stream, station_map, now=now)
        return {'action': 'SKIPPED_NOT_DUE', 'as_of_utc': _iso(now), 'next_due_utc': _iso(due),
                'staleness': self.staleness(now)}

    # -- the evaluation -------------------------------------------------------------
    def evaluate(self, stream: LiveStream, station_map: Optional[Dict[str, float]] = None,
                 now: Optional[datetime] = None) -> dict:
        now = _utc(now)
        eval_id = self._entry_id('EVAL', now)
        live = self.corrected_stream(stream)
        rep = Reconciler(self.well, self.rcfg).reconcile(live, station_map=station_map)
        drift_stations = [s for s in rep['stations'] if s['classification'] in DRIFT_CLASSES]
        clocks = None
        if drift_stations:
            clocks = {'notify_due': business_days_after(now, self.cfg.notify_bd).strftime('%Y-%m-%d'),
                      'fallback_due': business_days_after(now, self.cfg.fallback_bd).strftime('%Y-%m-%d'),
                      'refit_due': business_days_after(now, self.cfg.refit_bd).strftime('%Y-%m-%d')}
        proposals = []
        for s in rep['stations']:
            entry = {'evaluation_id': eval_id, 'timestamp_utc': _iso(now), 'well': self.well_name,
                     'station': s['channel'], 'md_ft': s.get('md_ft'), 'classification': s['classification'],
                     'n': s.get('n'), 'span_years': s.get('span_years'), 'bias_psi': s.get('bias_psi'),
                     'slope_psi_yr': s.get('slope_psi_yr'), 'noise_sigma_psi': s.get('noise_sigma_psi'),
                     'transient_count': s.get('transient_count'),
                     'drift_envelope_psi_yr': s.get('drift_envelope_psi_yr'),
                     'correction_in_force_psi': float(self.state['corrections_psi'].get(s['channel'], 0.0)),
                     'drift_detected': s['classification'] in DRIFT_CLASSES}
            self._append(self.eval_path, entry)
            if s['classification'] == 'CALIBRATION_OFFSET':
                proposals.append(self.propose_refit(s['channel'], float(s['bias_psi']), eval_id, now))
            elif s['classification'] in ('UNEXPLAINED_OFFSET', 'UNEXPLAINED_TREND'):
                proposals.append(self._log_change({'type': 'FALLBACK', 'station': s['channel'],
                                                   'evaluation_id': eval_id, 'timestamp_utc': _iso(now),
                                                   'status': 'PROPOSED', 'classification': s['classification'],
                                                   'bias_psi': s.get('bias_psi'), 'slope_psi_yr': s.get('slope_psi_yr'),
                                                   'detail': 'hold model output for this station; investigate'}, now))
        self.state['last_evaluation'] = {'evaluation_id': eval_id, 'timestamp_utc': _iso(now),
                                         'drift_detected': bool(drift_stations),
                                         'classification_counts': rep['classification_counts']}
        self.state['evaluation_count'] = int(self.state.get('evaluation_count', 0)) + 1
        self._save_state()
        return {'action': 'EVALUATED', 'evaluation_id': eval_id, 'timestamp_utc': _iso(now),
                'evaluation': rep, 'drift_detected': bool(drift_stations), 'sla_clocks': clocks,
                'proposals': proposals, 'staleness': self.staleness(now)}

    # -- change log -----------------------------------------------------------------
    def _log_change(self, entry: dict, now: datetime) -> dict:
        entry = dict(entry)
        entry.setdefault('entry_id', self._entry_id('CHG', now))
        self._append(self.change_path, entry)
        self._save_state()
        return entry

    def _refits_this_year(self, station: str, year: int) -> int:
        return int(self.state.get('refits_applied', {}).get(station, {}).get(str(year), 0))

    def propose_refit(self, station: str, bias_psi: float, evaluation_id: str,
                      now: Optional[datetime] = None) -> dict:
        """CALIBRATION_OFFSET -> PROPOSED offset correction with before/after.
        Blocked (logged, not applied) beyond the annual cap."""
        now = _utc(now)
        before = float(self.state['corrections_psi'].get(station, 0.0))
        after = round(before + bias_psi, 3)
        used = self._refits_this_year(station, now.year)
        entry = {'type': 'REFIT_OFFSET', 'station': station, 'evaluation_id': evaluation_id,
                 'timestamp_utc': _iso(now), 'coefficient': 'offset_correction_psi',
                 'before': before, 'after': after, 'measured_bias_psi': round(bias_psi, 3),
                 'refits_applied_this_year': used, 'annual_cap': self.cfg.max_refits_per_year}
        if used >= self.cfg.max_refits_per_year:
            entry.update({'status': 'BLOCKED_ANNUAL_LIMIT',
                          'detail': f'{used} re-fits already applied to {station} in {now.year}; cap {self.cfg.max_refits_per_year}'})
        else:
            entry.update({'status': 'PROPOSED', 'detail': 'awaiting approval'})
        return self._log_change(entry, now)

    def approve(self, entry_id: str, approver: str, now: Optional[datetime] = None,
                decision: str = 'APPLIED', note: str = '') -> dict:
        """Apply (or reject) a PROPOSED entry. Applying a REFIT_OFFSET writes
        the correction into state and counts against the annual cap."""
        now = _utc(now)
        entries = self._read(self.change_path)
        src = next((e for e in entries if e.get('entry_id') == entry_id), None)
        if src is None:
            raise KeyError(f'no change-log entry {entry_id}')
        if src.get('status') != 'PROPOSED':
            raise ValueError(f"entry {entry_id} is {src.get('status')}, not PROPOSED")
        if decision not in ('APPLIED', 'REJECTED'):
            raise ValueError('decision must be APPLIED or REJECTED')
        if decision == 'APPLIED' and src.get('type') == 'REFIT_OFFSET':
            st = src['station']
            used = self._refits_this_year(st, now.year)
            if used >= self.cfg.max_refits_per_year:
                decision = 'BLOCKED_ANNUAL_LIMIT'
            else:
                self.state['corrections_psi'][st] = float(src['after'])
                self.state.setdefault('refits_applied', {}).setdefault(st, {})[str(now.year)] = used + 1
        out = dict(src)
        out.update({'status': decision, 'approver': approver, 'decided_utc': _iso(now),
                    'note': note, 'supersedes_entry_id': entry_id})
        out['entry_id'] = self._entry_id('CHG', now)
        return self._log_change(out, now)

    # -- views ------------------------------------------------------------------------
    def history(self, station: Optional[str] = None, last: int = 50) -> List[dict]:
        rows = self._read(self.eval_path)
        if station:
            rows = [r for r in rows if r.get('station') == station]
        return rows[-last:]

    def change_log(self, last: int = 50) -> List[dict]:
        return self._read(self.change_path)[-last:]

    def open_proposals(self) -> List[dict]:
        entries = self._read(self.change_path)
        superseded = {e.get('supersedes_entry_id') for e in entries if e.get('supersedes_entry_id')}
        return [e for e in entries if e.get('status') == 'PROPOSED' and e.get('entry_id') not in superseded]

    def status(self, now: Optional[datetime] = None) -> dict:
        now = _utc(now)
        return {'well': self.well_name, 'as_of_utc': _iso(now), 'staleness': self.staleness(now),
                'last_evaluation': self.state.get('last_evaluation'),
                'evaluation_count': self.state.get('evaluation_count', 0),
                'corrections_psi': dict(self.state.get('corrections_psi', {})),
                'refits_applied': self.state.get('refits_applied', {}),
                'open_proposals': self.open_proposals(),
                'log_dir': self.log_dir,
                'log_sha256': {os.path.basename(p): _sha(p) for p in (self.eval_path, self.change_path) if os.path.exists(p)}}


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:16]
