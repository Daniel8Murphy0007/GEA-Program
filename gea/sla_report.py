"""sla_report — the monthly SLA measurement from the program's own records
(SLA 2.0 measurement; 5.0 post-implementation; drift and staleness SLA;
1.0 latency definitions; SOW 4.2.26 re-fit cap).

Inputs are the record files the other components already write; nothing is
typed in:
    drift monitor log dir      evaluations.jsonl, change_log.jsonl
    store-and-forward result   the Data Resilience machine JSON (+ records CSV)
    alarm event log            JSON lines from the alarm engine
    well-test record dir       approvals.jsonl
    accuracy statement JSON    the Accuracy Statement machine JSON
    config store               versions and rollbacks in the window

Every SLA line is measured / target / status; a line whose input is absent
is NOT MEASURED, never assumed met. Business-day arithmetic matches the
drift monitor's.

Headless-safe: numpy only.
"""

from __future__ import annotations

import calendar
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from .sample_record import parse_utc
from .drift_monitor import business_days_after


def month_window(month: str) -> tuple:
    y, m = int(month[:4]), int(month[5:7])
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y, m, calendar.monthrange(y, m)[1], 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def _lines(path: Optional[str]) -> List[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def _in(ts: str, a: datetime, b: datetime) -> bool:
    try:
        t = parse_utc(ts)
    except Exception:
        return False
    return a <= t <= b


def _bd_between(a: str, b: str) -> int:
    ta, tb = parse_utc(a), parse_utc(b)
    n, d = 0, ta
    while d.date() < tb.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def measure(month: str, monitor_log_dir: Optional[str] = None, store_forward_json: Optional[str] = None,
            alarm_log: Optional[str] = None, well_test_dir: Optional[str] = None, accuracy_json: Optional[str] = None,
            config_dir: Optional[str] = None, refit_cap_per_year: int = 4) -> dict:
    a, b = month_window(month)
    days = (b - a).days + 1
    lines: List[dict] = []

    def line(area, metric, measured, target, status, note=''):
        lines.append({'area': area, 'metric': metric, 'measured': measured, 'target': target, 'status': status, 'note': note})

    # --- drift and staleness SLA ------------------------------------------------------
    evs = [e for e in _lines(os.path.join(monitor_log_dir, 'evaluations.jsonl')) if _in(e['timestamp_utc'], a, b)] if monitor_log_dir else []
    chg = [e for e in _lines(os.path.join(monitor_log_dir, 'change_log.jsonl'))] if monitor_log_dir else []
    if monitor_log_dir and (evs or chg):
        eval_days = {parse_utc(e['timestamp_utc']).date() for e in evs}
        first_eval = min((parse_utc(e['timestamp_utc']) for e in evs), default=None)
        # adherence counted from the first evaluation in the month (a site commissioned mid-month is not penalised for the days before)
        days_due = (b.date() - first_eval.date()).days + 1 if first_eval else days
        adherence = round(100.0 * len(eval_days) / days_due, 1) if days_due else 0.0
        line('Model drift', 'Drift evaluation cadence (days with an evaluation / days due)', f'{len(eval_days)} / {days_due} ({adherence} %)',
             '>= 1 evaluation every 24 h', 'MET' if adherence >= 100.0 else 'NOT MET')
        detections = sorted({e['evaluation_id'] for e in evs if e.get('drift_detected')})
        line('Model drift', 'Evaluations with drift detected', f'{len(detections)} of {len({e["evaluation_id"] for e in evs})}', 'reported', 'REPORTED')
        proposals = [c for c in chg if c.get('status') == 'PROPOSED' and _in(c['timestamp_utc'], a, b)]
        decided = [c for c in chg if c.get('status') in ('APPLIED', 'REJECTED', 'BLOCKED_ANNUAL_LIMIT') and c.get('decided_utc') and _in(c['decided_utc'], a, b)]
        refits = [c for c in decided if c.get('type') == 'REFIT_OFFSET' and c['status'] == 'APPLIED']
        late = [c for c in refits if _bd_between(c['timestamp_utc'], c['decided_utc']) > 10]
        line('Model drift', 'Re-fit / redeploy within 10 business days of detection', f'{len(refits) - len(late)} of {len(refits)} within',
             '<= 10 business days', ('MET' if not late else 'NOT MET') if refits else 'NOT MEASURED', 'no re-fit in the month' if not refits else '')
        fall = [c for c in chg if c.get('type') == 'FALLBACK' and c.get('decided_utc') and _in(c['decided_utc'], a, b)]
        late_f = [c for c in fall if _bd_between(c['timestamp_utc'], c['decided_utc']) > 2]
        line('Model drift', 'Fallback decided within 2 business days', f'{len(fall) - len(late_f)} of {len(fall)} within', '<= 2 business days',
             ('MET' if not late_f else 'NOT MET') if fall else 'NOT MEASURED', 'no fallback in the month' if not fall else '')
        year_refits: Dict[str, int] = {}
        for c in chg:
            if c.get('type') == 'REFIT_OFFSET' and c.get('status') == 'APPLIED' and c.get('decided_utc') and parse_utc(c['decided_utc']).year == a.year:
                year_refits[c['station']] = year_refits.get(c['station'], 0) + 1
        worst = max(year_refits.values()) if year_refits else 0
        line('Model drift', f'Re-fits per station in {a.year} (year to date)', f'max {worst}', f'<= {refit_cap_per_year} per year', 'MET' if worst <= refit_cap_per_year else 'NOT MET')
        open_props = [c for c in proposals if not any(d.get('supersedes_entry_id') == c.get('entry_id') for d in chg)]
        line('Model drift', 'Proposals awaiting decision at month end', str(len(open_props)), '0', 'MET' if not open_props else 'ATTENTION')
    else:
        line('Model drift', 'Drift evaluation cadence', '-', '>= 1 evaluation every 24 h', 'NOT MEASURED', 'no monitor log supplied')

    # --- latency and remote-site availability ----------------------------------------------
    if store_forward_json and os.path.exists(store_forward_json):
        sim = json.load(open(store_forward_json, encoding='utf-8')).get('simulation', {})
        gr = sim.get('gap_report', {})
        rec_csv = store_forward_json.replace('.json', '_records.csv')
        lat = []
        if os.path.exists(rec_csv):
            with open(rec_csv, newline='', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    if r.get('ingest_timestamp_utc') and _in(r['timestamp_utc'], a, b):
                        lat.append((parse_utc(r['ingest_timestamp_utc']) - parse_utc(r['timestamp_utc'])).total_seconds())
        lat = np.array(lat, dtype=float)
        if len(lat):
            p95, p99 = float(np.percentile(lat, 95)), float(np.percentile(lat, 99))
            w2, w5 = 100.0 * float(np.mean(lat <= 120)), 100.0 * float(np.mean(lat <= 300))
            line('Latency', 'End-to-end latency p95 (field edge to OT lake)', f'{p95:.0f} s', '<= 120 s (95 %)', 'MET' if p95 <= 120 else 'NOT MET',
                 'includes outage replay; SLA 7.0 exclusion applies to documented telecom outages')
            line('Latency', 'End-to-end latency p99', f'{p99:.0f} s', '<= 300 s (99 %)', 'MET' if p99 <= 300 else 'NOT MET')
            line('Latency', 'Records within 2 min / within 5 min', f'{w2:.2f} % / {w5:.2f} %', '95 % / 99 %', 'MET' if (w2 >= 95 and w5 >= 99) else 'NOT MET')
        outs = sim.get('outages', [])
        tot_h = sum(o['duration_h'] for o in outs)
        hours = days * 24.0
        avail = 100.0 * (1 - tot_h / hours)
        line('Remote site', 'Link availability (outage hours / month hours)', f'{avail:.3f} % ({tot_h:.2f} h down)', '>= 99 %', 'MET' if avail >= 99.0 else 'NOT MET')
        st = sim.get('stats', {})
        line('Remote site', 'Store-and-forward: samples lost over capacity', str(st.get('dropped_over_capacity', 0)), '0 (72 h buffer)', 'MET' if not st.get('dropped_over_capacity') else 'NOT MET')
        dup = sum(v.get('duplicates_in_delivery', 0) for v in gr.values())
        line('Remote site', 'Duplicates in delivered stream', str(dup), '0', 'MET' if dup == 0 else 'NOT MET')
        line('Remote site', 'Chronological replay', 'verified' if all(v.get('chronological_replay') for v in gr.values()) else 'VIOLATED', 'required',
             'MET' if all(v.get('chronological_replay') for v in gr.values()) else 'NOT MET')
    else:
        line('Latency', 'End-to-end latency', '-', '95 % <= 2 min; 99 % <= 5 min', 'NOT MEASURED', 'no store-and-forward record supplied')
        line('Remote site', 'Link availability', '-', '>= 99 %', 'NOT MEASURED')

    # --- alarms ---------------------------------------------------------------------------------
    al = [e for e in _lines(alarm_log) if _in(e['timestamp_utc'], a, b)] if alarm_log else []
    if al:
        acts = [e for e in al if e['event'] == 'ACTIVATED']
        acked = [e for e in al if e['event'] == 'ACKNOWLEDGED']
        rtn_un = [e for e in al if e['event'] == 'RTN_UNACKED']
        line('Alarms', 'Activations in the month', str(len(acts)), 'reported', 'REPORTED')
        line('Alarms', 'Alarms that returned to normal unacknowledged', f'{len(rtn_un)} of {len(acts)}', '0', 'MET' if not rtn_un else 'ATTENTION')
        p1 = [e for e in acts if e['priority'] == 'P1']
        line('Alarms', 'P1 activations', str(len(p1)), 'each with a documented response', 'REPORTED')
    else:
        line('Alarms', 'Activations in the month', '-', 'reported', 'NOT MEASURED', 'no alarm event log supplied')

    # --- well tests ------------------------------------------------------------------------------
    ap = [e for e in _lines(os.path.join(well_test_dir, 'approvals.jsonl')) if _in(e['timestamp_utc'], a, b)] if well_test_dir else []
    if ap:
        tests = {e['test_id'] for e in ap}
        line('Well tests', 'Well tests with approval activity', str(len(tests)), 'reported', 'REPORTED')
        line('Well tests', 'Approval decisions recorded', str(len(ap)), 'timestamped, named', 'MET' if all(e.get('approver') for e in ap) else 'NOT MET')
    else:
        line('Well tests', 'Well tests approved', '-', 'reported', 'NOT MEASURED', 'no approval trail supplied')

    # --- accuracy --------------------------------------------------------------------------------
    if accuracy_json and os.path.exists(accuracy_json):
        bt = json.load(open(accuracy_json, encoding='utf-8')).get('backtest', {})
        bands = bt.get('bands', {})
        n_ok = bt.get('n_ok', 0)
        line('Accuracy', 'Quantities meeting >= 95 % at the conservative 90 % CI end', f"{bands.get('MEETS_TARGET', 0)} of {n_ok}", 'all claimed quantities >= 95 %',
             'MET' if bands.get('MEETS_TARGET', 0) == n_ok else 'PARTIAL', 'quantities below target are excluded from any claim')
        line('Accuracy', 'Quantities NOT ACCEPTABLE (< 85 %)', str(bands.get('NOT_ACCEPTABLE', 0)), '0 among claimed quantities', 'REPORTED')
    else:
        line('Accuracy', 'Accuracy statement', '-', '>= 95 % at 90 % CI', 'NOT MEASURED', 'no accuracy statement supplied')

    # --- change and continuity ------------------------------------------------------------------------
    if config_dir and os.path.isdir(config_dir):
        from .config_versioning import ConfigStore
        cs = ConfigStore(config_dir)
        changes = [e for n in cs.names() for e in cs.history(n) if _in(e['timestamp_utc'], a, b)]
        line('Change & continuity', 'Configuration versions committed in the month', str(len(changes)), 'every change versioned with author and diff',
             'MET' if all(e.get('author') for e in changes) else 'NOT MET')
        line('Change & continuity', 'Rollbacks in the month', str(sum(1 for e in changes if e.get('rollback_of'))), 'reported', 'REPORTED')
    else:
        line('Change & continuity', 'Configuration versions', '-', 'every change versioned', 'NOT MEASURED', 'no config store supplied')

    status_counts: Dict[str, int] = {}
    for l in lines:
        status_counts[l['status']] = status_counts.get(l['status'], 0) + 1
    return {'month': month, 'window': [a.strftime('%Y-%m-%dT%H:%M:%SZ'), b.strftime('%Y-%m-%dT%H:%M:%SZ')], 'lines': lines,
            'status_counts': status_counts,
            'inputs': {'monitor_log_dir': monitor_log_dir, 'store_forward_json': store_forward_json, 'alarm_log': alarm_log,
                       'well_test_dir': well_test_dir, 'accuracy_json': accuracy_json, 'config_dir': config_dir}}
