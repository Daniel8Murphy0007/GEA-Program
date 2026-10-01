# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""client_reports — the client-facing report family, in the outline and
vocabulary of a production-operations scope of work.

The engines in this package return dicts. This module turns them into the
reports a client engineer expects to open, numbered the way a scope of work
numbers them, and says nothing in the program's internal register. Every
number is recomputed at generation time from the same engines; every
threshold in force is printed; every flag names the rule that fired.

Reports (this module grows one report at a time; each is a function that
returns a `Document`):

    gauge_drift_report(...)   Gauge Drift & Reconciliation Report
                              (SOW 4.2.10 drift / re-fit; SLA 1.0 Model
                              Drift & Model Staleness; 4.2.2 data quality;
                              4.2.1.2 tag catalogue; 4.2.1.4 configuration)

Rendering: `render_markdown(doc)`, `render_html(doc)`, `write(doc, out_dir)`.

Vocabulary gate: `forbidden_terms(text)` returns the internal-register words
found in a rendered report; the acceptance suite requires an empty list.

Headless-safe: numpy only.
"""

from __future__ import annotations

import html as _html
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from .sample_record import (TagCatalogue, records_from_stream, quality_summary,
                            write_records_csv, write_catalogue_csv, parse_utc)
from .accuracy_statement import Z90

PROGRAM_NAME = 'Downhole Gauge Monitoring'

# SLA clocks (business days) for a detected model drift / staleness event.
SLA_EVALUATE_EVERY_H = 24.0
SLA_NOTIFY_BD = 1
SLA_FALLBACK_BD = 2
SLA_REFIT_BD = 10

# Words that never reach a client report: internal working vocabulary, and the
# names of other programs (this tuple is the only place in the package that
# may spell them - it is what keeps them out).
FORBIDDEN_TERMS = ('uqff', 'star-magic', 'star magic', 'primitive', 'doctrine',      # standalone-check: allow
                   'pinned_awaiting', 'refus', 'aether', 'dpm', 'rule 7', 'honest',   # standalone-check: allow
                   'landmark', 'lattice')                                          # standalone-check: allow

CLASS_KEY: Dict[str, dict] = {
    'IN_FAMILY': dict(
        meaning='Residual within the gauge noise band; live data and model agree.',
        status='NO DRIFT', action='ACCEPT - no action.'),
    'CALIBRATION_OFFSET': dict(
        meaning='Constant bias above the noise band and within instrument scale.',
        status='DRIFT DETECTED', action='RE-FIT - apply the offset correction and record it in the change log.'),
    'DRIFT_CONSISTENT': dict(
        meaning='Trend inside the instrument aging envelope at station conditions.',
        status='AGING WITHIN ENVELOPE', action='MONITOR - schedule recalibration per the maintenance plan.'),
    'TRANSIENTS': dict(
        meaning='Clustered short excursions consistent with well events, not an instrument fault.',
        status='NO DRIFT', action='REVIEW - confirm the excursions against the operations log.'),
    'UNEXPLAINED_OFFSET': dict(
        meaning='Bias larger than a calibration correction can account for.',
        status='DRIFT DETECTED', action='FALLBACK - hold the model output for this station; investigate gauge and completion; SLA clocks start.'),
    'UNEXPLAINED_TREND': dict(
        meaning='Trend outside the instrument aging envelope; a process change (for example drawdown) or an instrument fault.',
        status='DRIFT DETECTED', action='FALLBACK - hold the model output for this station; investigate process change versus instrument; SLA clocks start.'),
    'INSUFFICIENT_DATA': dict(
        meaning='Fewer than 8 valid samples in the window.',
        status='PENDING', action='PENDING - evaluate again when the window fills.'),
}


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------
@dataclass
class Table:
    columns: List[str]
    rows: List[List[object]]
    caption: str = ''


@dataclass
class Section:
    number: str
    title: str
    paragraphs: List[str] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)


@dataclass
class Document:
    title: str
    report_id: str
    front: List[List[str]]                  # key/value rows
    sections: List[Section]
    footer: str = ''
    data: dict = field(default_factory=dict)  # machine copy of what was rendered


def _fmt(v) -> str:
    if v is None:
        return '-'
    if isinstance(v, float):
        if v != v:                       # NaN
            return '-'
        if v == int(v) and abs(v) < 1e9:
            return f'{int(v):,}'
        return f'{v:,.2f}' if abs(v) < 1e6 else f'{v:.3e}'
    return str(v)


def _business_days_after(start: datetime, n: int) -> datetime:
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Report 1 - Gauge Drift & Reconciliation
# ---------------------------------------------------------------------------
def gauge_drift_report(evaluation: dict, stream, catalogue: Optional[TagCatalogue] = None,
                       well_name: str = '', evaluated_at: Optional[datetime] = None,
                       program_version: str = '', gauge_spec=None,
                       roc_limits: Optional[Dict[str, float]] = None,
                       monitor_status: Optional[dict] = None) -> Document:
    """Build the report from a reconciler evaluation dict and the live stream
    it was evaluated on. Nothing is recomputed here except summaries; the
    engine's numbers are the record. With `gauge_spec` the tag limits come
    from the datasheet and the report cites it."""
    now = evaluated_at or _utc_now()
    if catalogue is None:
        catalogue = TagCatalogue.from_stream(stream, gauge_spec=gauge_spec, roc_limits=roc_limits)
    records = records_from_stream(stream, catalogue)
    dq = quality_summary(records)
    stations = evaluation.get('stations', [])
    counts = evaluation.get('classification_counts', {})
    n_st = len(stations)
    drift_stations = [s for s in stations if CLASS_KEY.get(s['classification'], {}).get('status') == 'DRIFT DETECTED']
    drift_detected = bool(drift_stations)
    window_days = max((s.get('span_years', 0.0) or 0.0) for s in stations) * 365.25 if stations else 0.0
    first = min(v['first_utc'] for v in dq.values()) if dq else '-'
    last = max(v['last_utc'] for v in dq.values()) if dq else '-'
    well = well_name or evaluation.get('stream', 'well')
    report_id = f"GDR-{well.replace('/', '-').replace(' ', '_')[:24]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    meta = dict(getattr(stream, 'meta', {}) or {})

    # 1. Summary ---------------------------------------------------------------
    parts = [f'{n_st} gauge station{"s" if n_st != 1 else ""} evaluated against the well model over a '
             f'{window_days:.0f}-day window ({first} to {last}).']
    if counts:
        parts.append('Classification: ' + ', '.join(f'{k} x{v}' for k, v in sorted(counts.items())) + '.')
    if drift_detected:
        parts.append(f'MODEL DRIFT DETECTED at {len(drift_stations)} station'
                     f'{"s" if len(drift_stations) != 1 else ""}: '
                     + '; '.join(f"{s['channel']} ({s['classification']}, bias {_fmt(s.get('bias_psi'))} psi"
                                 + (f", trend {_fmt(s.get('slope_psi_yr'))} psi/yr" if s.get('slope_psi_yr') is not None else '')
                                 + ')' for s in drift_stations)
                     + '. SLA clocks for notification, fallback and re-fit start at the evaluation timestamp (section 5).')
    else:
        parts.append('No model drift detected; no SLA clock started.')
    summary = Section('1', 'Summary', [' '.join(parts)])

    # 2. Tag catalogue (SOW 4.2.1.2) --------------------------------------------
    cat_rows = [[r['tag_id'], r['description'][:70], r['unit'], r['owner'], r['tag_class'],
                 f"{_fmt(r['eng_range_lo'])} to {_fmt(r['eng_range_hi'])}",
                 r['cadence_s'] or '-', r['source_layer'], r['source'][:50]] for r in catalogue.rows()]
    tag_sec = Section('2', 'Tag catalogue and data model (SOW 4.2.1.2)',
                      ['One definition per tag: owner, engineering unit, engineering range, cadence and '
                       'source layer. Quality rules in force per tag are listed in section 6.'],
                      [Table(['Tag', 'Description', 'Unit', 'Owner', 'Class', 'Engineering range',
                             'Cadence (s)', 'Source layer', 'Source'], cat_rows)])

    # 3. Data quality (SOW 4.2.2) -----------------------------------------------
    dq_rows = []
    for tag, v in dq.items():
        c = v['counts']
        dq_rows.append([tag, v['n'], f"{v['pct_good']:.1f}", c['RANGE'], c['ROC'], c['FLATLINE'],
                        c['SPIKE'], c['STALE'], c['GAP'],
                        f"{v['longest_gap_samples']} ({v['longest_gap_s']/3600:.1f} h)" if v['longest_gap_samples'] else '0',
                        _fmt(v['latency_p95_s']) if v['latency_p95_s'] is not None else 'not stamped'])
    dq_par = ['Per-sample quality flags after the range, rate-of-change, flatline, spike and staleness '
              'rules. Every flagged sample carries the rule and limit that fired (records CSV).']
    ex = {tag: v['rule_examples'] for tag, v in dq.items() if v['rule_examples']}
    if ex:
        dq_par.append('Rules that fired: ' + '; '.join(
            f"{tag}: " + ', '.join(f'{f} ({r})' for f, r in d.items()) for tag, d in ex.items()) + '.')
    if meta.get('nan_days_dropped') not in (None, '0'):
        dq_par.append(f"Source records without a value in the evaluated channel: {meta['nan_days_dropped']} "
                      f"(excluded from the evaluation window by the ingest adapter and counted here).")
    dq_par.append('Timestamp latency is reported per source layer (field/edge, OT lake, DOF) when ingest '
                  'timestamps are present; this source carries none, so latency is not stamped.')
    dq_sec = Section('3', 'Data quality (SOW 4.2.2)', dq_par,
                     [Table(['Tag', 'n', 'GOOD %', 'RANGE', 'ROC', 'FLATLINE', 'SPIKE', 'STALE', 'GAP',
                             'Longest gap', 'Latency p95 (s)'], dq_rows)])

    # 4. Gauge drift evaluation (SOW 4.2.10) ------------------------------------
    st_rows = []
    for s in stations:
        env = s.get('drift_envelope_psi_yr') or [None, None]
        key = CLASS_KEY.get(s['classification'], {'status': '-', 'action': '-'})
        st_rows.append([s['channel'], _fmt(s.get('md_ft')), _fmt(s.get('predicted_baseline_psi')), s.get('n'),
                        f"{(s.get('span_years') or 0)*365.25:.0f}", _fmt(s.get('bias_psi')),
                        _fmt(s.get('slope_psi_yr')) if s.get('trend_usable') else 'window too short',
                        f"{_fmt(min(env))} to {_fmt(max(env))}" if env[0] is not None else '-',
                        _fmt(s.get('noise_sigma_psi')), s.get('transient_count'),
                        s['classification'], key['status'], key['action']])
    drift_sec = Section('4', 'Gauge drift evaluation and reconciliation (SOW 4.2.10)',
                        ['For each station the live pressure series is compared with the well model baseline at '
                         'that measured depth. Residual = measured - baseline. Bias is the mean residual; drift rate '
                         'is the fitted linear trend of the residual; the aging envelope is the expected instrument '
                         'drift band at station pressure and temperature from the gauge library; noise is the '
                         'robust 1-sigma of the detrended residual. Classification and action follow the key below.'],
                        [Table(['Tag', 'Station MD (ft)', 'Baseline (psi)', 'n', 'Window (days)', 'Bias (psi)',
                                'Drift rate (psi/yr)', 'Aging envelope (psi/yr)', 'Noise 1-sigma (psi)', 'Transients',
                                'Classification', 'Status', 'Action'], st_rows),
                         Table(['Classification', 'Meaning', 'Status', 'Action'],
                               [[k, v['meaning'], v['status'], v['action']] for k, v in CLASS_KEY.items()],
                               caption='Classification key')])

    # 5. SLA - model drift and staleness (SLA 1.0 / drift SLA) ------------------
    next_due = now + timedelta(hours=SLA_EVALUATE_EVERY_H)
    ms = monitor_status or {}
    stale = ms.get('staleness') or {}
    if stale:
        cad = stale.get('cadence_h', SLA_EVALUATE_EVERY_H)
        st_txt = {'CURRENT': f"CURRENT - last evaluation {stale.get('age_h')} h ago, cadence {cad:.0f} h",
                  'STALE': f"STALE - last evaluation {stale.get('age_h')} h ago, {stale.get('overdue_h')} h overdue against cadence {cad:.0f} h",
                  'NEVER_EVALUATED': 'NEVER EVALUATED'}.get(stale.get('status'), str(stale.get('status')))
        sla_rows = [['Evaluation timestamp (UTC)', stale.get('last_evaluation_utc') or now.strftime('%Y-%m-%dT%H:%M:%SZ')],
                    ['Evaluation ID', stale.get('last_evaluation_id') or '-'],
                    ['Evaluation cadence', f'every {cad:.0f} h'],
                    ['Next evaluation due (UTC)', stale.get('next_due_utc') or '-'],
                    ['Model staleness', st_txt],
                    ['Evaluations on record', str(ms.get('evaluation_count', 0))],
                    ['Model drift', 'DETECTED' if drift_detected else 'NOT DETECTED'],
                    ['Stations requiring action', ', '.join(s['channel'] for s in drift_stations) or 'none'],
                    ['Offset corrections in force (psi)', ', '.join(f'{k}: {v:+.2f}' for k, v in (ms.get('corrections_psi') or {}).items()) or 'none'],
                    ['Record integrity (sha256, first 16)', ', '.join(f'{k} {v}' for k, v in (ms.get('log_sha256') or {}).items()) or '-']]
    else:
        sla_rows = [['Evaluation timestamp (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
                    ['Evaluation cadence', f'every {SLA_EVALUATE_EVERY_H:.0f} h'],
                    ['Next evaluation due (UTC)', next_due.strftime('%Y-%m-%dT%H:%M:%SZ')],
                    ['Model staleness', 'CURRENT - evaluated within the cadence'],
                    ['Model drift', 'DETECTED' if drift_detected else 'NOT DETECTED'],
                    ['Stations requiring action', ', '.join(s['channel'] for s in drift_stations) or 'none']]
    if drift_detected:
        sla_rows += [['Notification due', _business_days_after(now, SLA_NOTIFY_BD).strftime('%Y-%m-%d') + f' ({SLA_NOTIFY_BD} business day)'],
                     ['Fallback due', _business_days_after(now, SLA_FALLBACK_BD).strftime('%Y-%m-%d') + f' ({SLA_FALLBACK_BD} business days)'],
                     ['Re-fit / redeploy due', _business_days_after(now, SLA_REFIT_BD).strftime('%Y-%m-%d') + f' ({SLA_REFIT_BD} business days)']]
    sla_tables = [Table(['Item', 'Value'], sla_rows)]
    hist = ms.get('history') or []
    if hist:
        sla_tables.append(Table(['Evaluation (UTC)', 'Evaluation ID', 'Station', 'Classification', 'Bias (psi)',
                                 'Drift rate (psi/yr)', 'Correction in force (psi)'],
                                [[h.get('timestamp_utc'), h.get('evaluation_id'), h.get('station'), h.get('classification'),
                                  _fmt(h.get('bias_psi')), _fmt(h.get('slope_psi_yr')), _fmt(h.get('correction_in_force_psi'))]
                                 for h in hist[-10:]], caption='Evaluation history (last 10 entries)'))
    sla_sec = Section('5', 'Model drift and staleness status (SLA 1.0; drift and staleness SLA)',
                      ['Model Drift: the live residual at a station moves outside the band the well model and the '
                       'instrument aging envelope account for. Model Staleness: the evaluation is older than the '
                       'cadence. Clocks below start at the evaluation timestamp when drift is detected.'],
                      sla_tables)

    # 6. Configuration in force (SOW 4.2.1.4) -----------------------------------
    th = dict(evaluation.get('thresholds_disclosed', {}))
    th.pop('note', None)
    cfg_rows = [['Bias significance', f"{th.get('bias_n_sigma')} x noise / sqrt(n) + 1 psi floor"],
                ['Transient gate', f"{th.get('transient_n_sigma')} x noise, single sample"],
                ['Bias too large for calibration', f"{th.get('model_mismatch_psi')} psi"],
                ['Aging envelope margin', f"{th.get('drift_envelope_margin')} x upper envelope"],
                ['Minimum window for a trend', f"{(th.get('min_trend_span_years') or 0)*365.25:.0f} days"],
                ['Minimum samples per station', '8']]
    rule_rows = []
    for r in catalogue.rows():
        rule_rows.append([r['tag_id'], f"{_fmt(r['eng_range_lo'])} to {_fmt(r['eng_range_hi'])} {r['unit']}",
                          _fmt(r['roc_limit_per_s']) + '/s' if r['roc_limit_per_s'] != '' else 'off',
                          f"{r['flatline_min_samples']} samples" if r['flatline_min_samples'] else 'off',
                          f"{_fmt(r['spike_n_sigma'])} x MAD, window {r['spike_window']}" if r['spike_n_sigma'] != '' else 'off',
                          f"{_fmt(r['stale_after_s'])} s" if r['stale_after_s'] != '' else 'off',
                          r['limits_basis']])
    cfg_par = ['Classification thresholds and per-tag quality rules applied by this evaluation. These are '
               'engineering settings under version control; a change is a change-log entry, never silent.']
    if gauge_spec is not None:
        cfg_par.append(f"Gauge datasheet in force: '{gauge_spec.name}' - {gauge_spec.source}")
    cfg_sec = Section('6', 'Configuration in force (SOW 4.2.1.4)', cfg_par,
                      [Table(['Threshold', 'Value'], cfg_rows, caption='Classification thresholds'),
                       Table(['Tag', 'Engineering range', 'Rate-of-change limit', 'Flatline', 'Spike', 'Staleness', 'Basis'],
                             rule_rows, caption='Quality rules per tag')])

    # 7. Change log ------------------------------------------------------------
    cl_rows = []
    for s in stations:
        if s['classification'] == 'CALIBRATION_OFFSET':
            cl_rows.append([now.strftime('%Y-%m-%dT%H:%M:%SZ'), s['channel'], 'PROPOSED offset correction',
                            f"-{_fmt(s.get('bias_psi'))} psi", 'awaiting approval'])
        elif s['classification'] in ('UNEXPLAINED_OFFSET', 'UNEXPLAINED_TREND'):
            cl_rows.append([now.strftime('%Y-%m-%dT%H:%M:%SZ'), s['channel'], 'PROPOSED fallback',
                            'hold model output for this station', 'awaiting approval'])
    if ms.get('change_log'):
        cl_rows = [[e.get('timestamp_utc'), e.get('station'), e.get('type'),
                    (f"{e.get('coefficient')}: {_fmt(e.get('before'))} -> {_fmt(e.get('after'))} psi" if e.get('type') == 'REFIT_OFFSET'
                     else str(e.get('detail', ''))),
                    e.get('status') + (f" by {e.get('approver')} {e.get('decided_utc')}" if e.get('approver') else '')]
                   for e in ms['change_log'][-10:]]
    cl_sec = Section('7', 'Change log entries proposed by this evaluation' if not ms.get('change_log') else 'Change log (last 10 entries)',
                     ['No configuration or model coefficient was changed by generating this report. Entries below '
                      'are proposals for the approval workflow; an approved entry is applied and logged with the '
                      'approver, timestamp and before/after values.' if cl_rows else
                      'No configuration or model coefficient was changed by generating this report, and no change '
                      'is proposed.'],
                     [Table(['Timestamp (UTC)', 'Tag', 'Change', 'Value', 'Status'], cl_rows)] if cl_rows else [])

    # 8. Data provenance and limitations ---------------------------------------
    prov = [f"Live data: {evaluation.get('stream', stream.name)}."]
    for k in ('source_channel', 'source_unit', 'conversion', 'station_md_ft', 'start_date', 'end_date', 'cadence_s'):
        if meta.get(k):
            prov.append(f"{k.replace('_', ' ')}: {meta[k]}.")
    w = evaluation.get('well', {})
    prov.append(f"Well model: total depth {_fmt(w.get('td_ft'))} ft; profile '{w.get('profile')}'; "
                f"deviation '{w.get('deviation')}'; gauge datasheet '{w.get('gauge_spec')}'.")
    prov.append('Limitations: a trend is only classified when the window meets the minimum in section 6; '
                'the aging envelope is a model band, not a measurement of this gauge; station depths supplied '
                'by the caller are marked as such above and should be confirmed from completion records.')
    prov_sec = Section('8', 'Data provenance and limitations', [' '.join(prov)])

    front = [['Report ID', report_id],
             ['Well', well],
             ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Data window', f'{first} to {last}'],
             ['Stations', str(n_st)],
             ['Result', 'MODEL DRIFT DETECTED' if drift_detected else 'NO MODEL DRIFT DETECTED']]
    doc = Document(title=f'{PROGRAM_NAME} - Gauge Drift and Reconciliation Report', report_id=report_id,
                   front=front, sections=[summary, tag_sec, dq_sec, drift_sec, sla_sec, cfg_sec, cl_sec, prov_sec],
                   footer='Every number in this report is recomputed from the source data at generation time. '
                          'Classifications are advisory; the numbers beside them are the record.',
                   data={'evaluation': evaluation, 'data_quality': dq, 'catalogue': catalogue.rows(),
                         'drift_detected': drift_detected, 'report_id': report_id,
                         'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ')})
    doc.data['_records'] = records
    doc.data['_catalogue_obj'] = catalogue
    return doc


# ---------------------------------------------------------------------------
# Report 2 - Accuracy Statement (SLA 4.0; SOW 4.2.10 MAPE)
# ---------------------------------------------------------------------------
BAND_TEXT = {'MEETS_TARGET': 'MEETS TARGET (>= 95 %)', 'BAND_2': 'BAND 2 (90-95 %)',
             'BAND_3': 'BAND 3 (85-90 %)', 'NOT_ACCEPTABLE': 'NOT ACCEPTABLE (< 85 %)'}


def accuracy_statement_report(backtest: dict, evaluated_at: Optional[datetime] = None,
                              program_version: str = '', scope: str = 'library back-test') -> Document:
    """The Accuracy Statement from a `library_backtest()` (or any dict of the
    same shape) - MAPE at the stated CI per predicted quantity per well, the
    calibration check, and the band read at the conservative end of the CI."""
    now = evaluated_at or _utc_now()
    meth = backtest.get('method', {})
    ci = meth.get('ci', 0.90)
    ok = backtest.get('statements', [])
    pend = backtest.get('pending', [])
    bands = backtest.get('bands', {})
    report_id = f"ACC-{scope.replace(' ', '_')[:20]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    n_meet = bands.get('MEETS_TARGET', 0)
    parts = [f'{len(ok)} predicted quantities scored on {sum(r["n"] for r in ok)} blind trials; '
             f'{len(pend)} pending below the minimum of {meth.get("min_trials")} trials.']
    parts.append('Bands at the conservative end of the ' + f'{ci*100:.0f} % CI: ' +
                 ', '.join(f'{BAND_TEXT.get(b, b)} x{n}' for b, n in sorted(bands.items(), key=lambda kv: -kv[1])) + '.')
    if ok:
        parts.append(f'Best MAPE {backtest.get("best_mape_pct"):.2f} %; worst {backtest.get("worst_mape_pct"):.2f} %; '
                     f'median calibration coverage at the CI level {backtest.get("median_coverage_at_ci")} (expected about {ci:.2f}).')
    parts.append(f'{n_meet} of {len(ok)} quantities meet the 95 % accuracy target; the quantities that do not are listed '
                 'with their numbers and are excluded from any accuracy claim until they do.')
    summary = Section('1', 'Summary', [' '.join(parts)])

    defs = Section('2', 'Definitions in force (SLA 4.0; SOW 4.2.10)',
                   ['Absolute percentage error per trial: |estimate - truth| / |truth| x 100. MAPE: the mean over trials. '
                    'Accuracy: 100 - MAPE. Confidence interval: bootstrap percentile interval on MAPE '
                    f'({meth.get("bootstrap_resamples")} resamples, seed {meth.get("seed")}, reproducible). '
                    f'Coverage at CI: the fraction of trials whose truth lies inside the estimate\'s own +/- {Z90:.3f}-sigma band '
                    '(the two-sided normal quantile at the CI level), a calibration check on the stated uncertainty. '
                    'Band: accuracy read against 95 / 90 / 85 % '
                    'using the upper CI bound of MAPE, so a statement never claims a band the interval does not support. '
                    'A quantity with fewer trials than the minimum is PENDING, never scored.'],
                   [Table(['Band', 'Accuracy (100 - MAPE at the upper CI bound)'],
                          [['MEETS TARGET', '>= 95 %'], ['BAND 2', '90 % to < 95 %'], ['BAND 3', '85 % to < 90 %'],
                           ['NOT ACCEPTABLE', '< 85 %']])])

    rows = []
    for r in ok:
        rows.append([r['well'], r['given'], r['target'], r['n'], f"{r['mape_pct']:.2f}",
                     f"{r['mape_ci_lo_pct']:.2f} to {r['mape_ci_hi_pct']:.2f}",
                     f"{r['accuracy_pct']:.2f}", f"{r['accuracy_conservative_pct']:.2f}",
                     _fmt(r['coverage_at_ci']), BAND_TEXT.get(r['band'], r['band'])])
    stmt = Section('3', 'Accuracy statement', 
                   [f'Per predicted quantity, per well, over the back-test window of the library. CI level {ci*100:.0f} %.'],
                   [Table(['Well', 'Given', 'Predicted quantity', 'n', 'MAPE %', f'MAPE {ci*100:.0f} % CI',
                           'Accuracy %', 'Accuracy % (conservative)', 'Coverage at CI', 'Band'], rows)])

    prow = [[r['well'], r['given'], r['target'], r['n'], r.get('min_n'), 'PENDING - below minimum trials'] for r in pend]
    pending = Section('4', 'Pending quantities (not scored)',
                      ['Quantities whose co-located trials are below the minimum are reported with their count and no number.'
                       if prow else 'None.'],
                      [Table(['Well', 'Given', 'Predicted quantity', 'n', 'Minimum', 'Status'], prow)] if prow else [])

    method = Section('5', 'Back-test method (for inspection; SCC 5.0)',
                     ['Hold-out: ' + str(meth.get('hold_out')) + '. Estimator: ' + str(meth.get('estimator')) +
                      '. Every trial holds one co-located observation out, predicts it from the rest with the same estimator '
                      'the product runs, and scores the prediction against the held-out truth. Nothing is tuned to this test. '
                      f'Resamples {meth.get("bootstrap_resamples")}, seed {meth.get("seed")}: the statement regenerates '
                      'byte-identically from the same library, so it can be re-run by the client from source.'])

    plan_rows = [['Go-live', 'Baseline accuracy statement on the client\'s own validated data, this method'],
                 ['Monthly', 'Statement regenerated; drift evaluation feeds re-fits; bands updated'],
                 ['Quarterly', 'Aggregated statement for the SLA review; bands read at the conservative CI end'],
                 ['Annually, years 1-5', 'Full re-statement with the year\'s validated tests as the back-test window']]
    plan = Section('6', 'Accuracy measurement plan (five years)',
                   ['The statement is regenerated on the cadence below from the client\'s validated measurements, '
                    'never from a stored snapshot. Each statement carries its window, n and CI.'],
                   [Table(['When', 'What'], plan_rows)])

    prov = Section('7', 'Provenance and limitations',
                   [f'Scope: {scope}. The quantities scored here are the property estimators over the archived public '
                    'library the program ships with; they are a statement about the estimator on that library, not a '
                    'claim about a client well. Accuracy of a deployed model is measured on the client\'s own data by the '
                    'same method, with the same definitions, before any band is claimed. Quantities with truth values at '
                    'zero cannot carry a percentage error and are excluded from MAPE with the count disclosed.'])

    front = [['Report ID', report_id], ['Scope', scope],
             ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['CI level', f'{ci*100:.0f} %'], ['Quantities scored / pending', f'{len(ok)} / {len(pend)}'],
             ['Result', f'{n_meet} of {len(ok)} MEET TARGET' if ok else 'NO QUANTITY SCORED']]
    machine = {k: v for k, v in backtest.items()}
    return Document(title=f'{PROGRAM_NAME} - Accuracy Statement', report_id=report_id, front=front,
                    sections=[summary, defs, stmt, pending, method, plan, prov],
                    footer='Every number in this statement is recomputed from the source data at generation time; '
                           'the bands are read at the conservative end of the confidence interval.',
                    data={'backtest': machine, 'report_id': report_id,
                          'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'), '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 3 - Well Test Validation (SOW 4.2.3.1)
# ---------------------------------------------------------------------------
def well_test_report(detection: dict, approvals=None, well_name: str = '',
                     evaluated_at: Optional[datetime] = None, program_version: str = '') -> Document:
    """The Well Test Validation Report from a `WellTestValidator.detect()`
    result and (optionally) an `ApprovalTrail`."""
    now = evaluated_at or _utc_now()
    c = detection.get('criteria', {})
    tests = detection.get('tests', [])
    rej = detection.get('rejected', [])
    well = well_name or detection.get('stream', 'well')
    report_id = f"WTV-{well.replace('/', '-').replace(' ', '_')[:24]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    win = detection.get('window') or ['-', '-']
    status_of = (lambda tid: approvals.status_of(tid)) if approvals is not None else (lambda tid: {'status': 'PENDING_LEVEL_1', 'decisions': []})
    n_app = sum(1 for t in tests if status_of(t['test_id'])['status'] == 'APPROVED_ALL_LEVELS')

    summary = Section('1', 'Summary', [
        f"{detection.get('n_samples')} samples from {win[0]} to {win[1]}; {detection.get('eligible_samples')} eligible "
        f"(on stream, complete, quality GOOD). {len(tests)} stable period{'s' if len(tests) != 1 else ''} detected and "
        f"ACCEPTED as well tests covering {detection.get('samples_in_accepted_tests')} samples; {len(rej)} candidate "
        f"period{'s' if len(rej) != 1 else ''} REJECTED with reason codes. {n_app} of {len(tests)} accepted tests "
        f"approved at all levels; the rest await review. Virtual rates are the means over each accepted test and are "
        f"released to allocation only after approval."])

    crit_rows = [[k, _fmt(v)] for k, v in c.items() if not k.startswith('_') and k not in ('approval_levels', 'name', 'basis')]
    crit_rows += [['approval levels', '; '.join(f"level {l['level']}: {l['role']}" for l in c.get('approval_levels', []))]]
    map_rows = [[role, chan] for role, chan in (detection.get('channel_map') or {}).items()]
    criteria = Section('2', 'Stability criteria in force (client-agreed logic; SOW 4.2.3.1)',
                       [f"Criteria: '{c.get('name')}'. Source: {c.get('_source')} (sha256 {c.get('_sha256')}). "
                        f"Basis: {c.get('basis')}. A change to this file is a change-log entry; the report always "
                        "prints the version it ran with."],
                       [Table(['Parameter', 'Value'], crit_rows, caption='Parameters'),
                        Table(['Role', 'Channel'], map_rows, caption='Channel roles')])

    rate_roles = list(next(iter(tests))['virtual_rates'].keys()) if tests else []
    _u = lambda u: (u or '').split(' ')[0]
    unit_notes = sorted({u for t in tests for u in (v.get('unit', '') for v in t['statistics'].values()) if u and ' ' in u})
    cols = ['Test', 'Start (UTC)', 'End (UTC)', 'n'] + [f'{r} (mean)' for r in rate_roles] + \
           ['Downhole P (mean)', 'Wellhead P (mean)', 'Operating point', 'On stream (h, mean)', 'Approval']
    trows = []
    for t in tests:
        st = t['statistics']
        dh = st.get('pressure:downhole', {})
        wh = st.get('pressure:wellhead', {})
        op = '; '.join(f"{k.split(':')[1]} {v['mean']:g}" for k, v in st.items() if k.startswith('operating:'))
        trows.append([t['test_id'], t['start_utc'][:10], t['end_utc'][:10], t['n']] +
                     [f"{t['virtual_rates'][r]:,.1f} {_u(st[f'rate:{r}']['unit'])}" for r in rate_roles] +
                     [f"{dh.get('mean', float('nan')):,.1f} {_u(dh.get('unit', ''))}", f"{wh.get('mean', float('nan')):,.1f} {_u(wh.get('unit', ''))}",
                      op or '-', _fmt(st.get('on_stream', {}).get('mean_hours')), status_of(t['test_id'])['status']])
    accepted = Section('3', 'Accepted well tests',
                       ['Each accepted test is the longest window from its start that satisfies every criterion. '
                        'Means are the test result; CV and trend per channel are in the machine JSON.'],
                       [Table(cols, trows)] if trows else [])

    rrows = [[r['candidate_id'], r['start_utc'][:10], r['end_utc'][:10], r['n'], r.get('primary_reason') or ', '.join(r['reason_codes']),
              r.get('detail', '')[:90]] for r in rej]
    rejected = Section('4', 'Rejected candidate periods (reason codes)',
                       ['Every period not accepted is listed with the rule that failed and the number that failed it.'],
                       [Table(['Candidate', 'Start (UTC)', 'End (UTC)', 'n', 'Primary reason', 'Detail'], rrows)] if rrows else [])

    arows = []
    if approvals is not None:
        for e in approvals.entries():
            arows.append([e['timestamp_utc'], e['test_id'], f"L{e['level']} {e['role']}", e['approver'], e['decision'], e.get('note', '')])
    trail = Section('5', 'Approval trail',
                    ['Multi-level approval with timestamps. A test is released when every level has approved; a '
                     'rejection at any level holds it. Corrections are recorded as CORRECTED decisions with a note.'
                     if arows else 'No approvals recorded yet.'],
                    [Table(['Timestamp (UTC)', 'Test', 'Level', 'Approver', 'Decision', 'Note'], arows)] if arows else [])

    prov = Section('6', 'Data provenance and limitations',
                   [f"Source stream: {detection.get('stream')}. Detection is deterministic from the stream and the criteria "
                    "file; re-running with the same inputs reproduces this report. Rates whose mean is below the negligible "
                    "fraction of the largest rate in the window are not stability criteria (disclosed in section 2). "
                    "Quality flags come from the record layer's rules when a tag catalogue is supplied."
                    + (' Units: ' + '; '.join(unit_notes) + '.' if unit_notes else '')])

    front = [['Report ID', report_id], ['Well', well], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Data window', f'{win[0]} to {win[1]}'],
             ['Accepted / rejected', f'{len(tests)} / {len(rej)}'],
             ['Result', f'{len(tests)} WELL TESTS ACCEPTED, {n_app} APPROVED' if tests else 'NO STABLE PERIOD FOUND']]
    return Document(title=f'{PROGRAM_NAME} - Well Test Validation Report', report_id=report_id, front=front,
                    sections=[summary, criteria, accepted, rejected, trail, prov],
                    footer='Every number in this report is recomputed from the source data and the criteria file at '
                           'generation time; reason codes name the rule and the value that failed it.',
                    data={'detection': detection, 'report_id': report_id,
                          'approvals': (approvals.entries() if approvals is not None else []),
                          'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'), '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 4 - Alarm & Event (SOW 4.2.4; ISA-18.2 / IEC 62682 practice)
# ---------------------------------------------------------------------------
def alarm_event_report(engine, well_name: str = '', evaluated_at: Optional[datetime] = None,
                       program_version: str = '', last_events: int = 60) -> Document:
    now = evaluated_at or _utc_now()
    k = engine.kpis()
    defs = list(engine.defs.values())
    active = engine.active()
    well = well_name or 'well'
    report_id = f"ALM-{well.replace('/', '-').replace(' ', '_')[:24]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    n_act = k.get('n_activations', 0)
    parts = [f"{len(defs)} alarm definitions in force on {len(engine.by_tag)} tags; {n_act} activations "
             + (f"over {k.get('window_hours')} h ({k.get('per_day')} per day, average {k.get('avg_per_10min_per_position')} "
                f"per 10 min per operator position, peak {k.get('peak_per_10min_per_position')})." if n_act else 'in the window.')]
    if n_act:
        parts.append(f"{k.get('flood_10min_bins')} ten-minute flood periods ({k.get('pct_time_in_flood')} % of the window); "
                     f"{len(k.get('standing_alarms_over_24h', []))} standing; {len(k.get('chattering_alarms', []))} chattering; "
                     f"{len(k.get('active_unacknowledged', []))} active unacknowledged at window end.")
        top = k.get('top_alarms', [])[:3]
        if top:
            parts.append('Most frequent: ' + ', '.join(f"{t['alarm_id']} ({t['activations']}, {t['pct_of_total']} %)" for t in top) + '.')
        if k.get('avg_per_10min_per_position', 0) > 2 or k.get('flood_10min_bins', 0):
            parts.append('The rate exceeds the manageable target; the definitions table shows which alarms carry the load, '
                         'and the usual remedy is to move journal-type conditions (sample quality) out of the operator alarm list.')
    summary = Section('1', 'Summary', [' '.join(parts)])

    drows = [[d.alarm_id, d.tag_id, d.kind, d.priority, _fmt(d.setpoint), _fmt(d.deadband), _fmt(d.on_delay_s),
              'yes' if d.enabled else 'no', d.basis] for d in defs]
    defsec = Section('2', 'Alarm definitions in force (SOW 4.2.4)',
                     ['Each alarm names its tag, kind, setpoint, return-to-normal deadband, on-delay, priority and the '
                      'basis of the setpoint. Over-range alarms derive from the tag catalogue; process setpoints are '
                      'client settings loaded from the definitions file.'],
                     [Table(['Alarm', 'Tag', 'Kind', 'Priority', 'Setpoint', 'Deadband', 'On-delay (s)', 'Enabled', 'Basis'], drows)])

    arows = [[a['alarm_id'], a['tag_id'], a['priority'], a['state'], a['active_since_utc'] or '-', _fmt(a['last_value'])] for a in active]
    actsec = Section('3', 'Active alarms at window end',
                     ['State per ISA-18.2: ACTIVE_UNACKED awaits operator acknowledgement; ACTIVE_ACKED is acknowledged and still in alarm.'
                      if arows else 'No alarm active at window end.'],
                     [Table(['Alarm', 'Tag', 'Priority', 'State', 'Active since (UTC)', 'Last value'], arows)] if arows else [])

    all_events = engine.events
    evs = all_events[-last_events:]
    erows = [[e['timestamp_utc'], e['alarm_id'], e['priority'], e['event'], _fmt(e['value']), e.get('operator', ''), e.get('note', '')[:60]] for e in evs]
    evsec = Section('4', f'Event log (last {len(erows)} of {len(all_events)} events)',
                    ['One line per transition; the full log is the JSON lines file named in section 6.'],
                    [Table(['Timestamp (UTC)', 'Alarm', 'Priority', 'Event', 'Value', 'Operator', 'Note'], erows)] if erows else [])

    tg = k.get('targets', {})
    krows = [['Activations per day', _fmt(k.get('per_day')), tg.get('per_day', '')],
             ['Average per 10 min per operator position', _fmt(k.get('avg_per_10min_per_position')), tg.get('avg_per_10min', '')],
             ['Peak per 10 min per operator position', _fmt(k.get('peak_per_10min_per_position')), tg.get('flood', '')],
             ['Ten-minute flood periods / % time in flood', f"{k.get('flood_10min_bins', 0)} / {k.get('pct_time_in_flood', 0)} %", tg.get('flood', '')],
             ['Standing alarms', ', '.join(k.get('standing_alarms_over_24h', [])) or 'none', tg.get('standing', '')],
             ['Chattering alarms', ', '.join(k.get('chattering_alarms', [])) or 'none', tg.get('chattering', '')],
             ['Priority distribution (%)', ', '.join(f'{p} {v}' for p, v in (k.get('priority_distribution_pct') or {}).items()), tg.get('priority_split', '')]] if n_act else \
            [['Activations', '0', 'no activations in the window']]
     
    trows = [[t['alarm_id'], t['activations'], t['pct_of_total']] for t in k.get('top_alarms', [])]
    kpisec = Section('5', 'Alarm-management KPIs against targets',
                     [tg.get('basis', 'targets as commonly stated in alarm-management practice') + '.'],
                     [Table(['KPI', 'Value', 'Target'], krows)] + ([Table(['Alarm', 'Activations', '% of total'], trows, caption='Most frequent alarms')] if trows else []))

    prov = Section('6', 'Provenance', [f"Operator positions: {engine.operator_positions}. Event log: "
                                       f"{engine.log_path or 'in memory for this run'}. Window: "
                                       f"{k.get('window', ['-', '-'])[0] if k.get('window') else '-'} to "
                                       f"{k.get('window', ['-', '-'])[1] if k.get('window') else '-'}. The state machine, "
                                       "deadband and on-delay are applied per alarm in time order; shelved alarms are "
                                       "suppressed and logged."])
    front = [['Report ID', report_id], ['Well', well], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Definitions / activations', f'{len(defs)} / {n_act}'],
             ['Result', (f"{len(active)} ACTIVE, {len(k.get('active_unacknowledged', []))} UNACKNOWLEDGED" if active else 'NO ACTIVE ALARM')]]
    return Document(title=f'{PROGRAM_NAME} - Alarm and Event Report', report_id=report_id, front=front,
                    sections=[summary, defsec, actsec, evsec, kpisec, prov],
                    footer='Every KPI is recomputed from the event log at generation time; targets are printed beside values, never in place of them.',
                    data={'kpis': k, 'definitions': [d.row() for d in defs], 'active': active, 'events': all_events,
                          'report_id': report_id, 'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                          '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 5 - Model Card (SCC 5.0 inspection; M3 explainability)
# ---------------------------------------------------------------------------
def model_card_report(card, evaluated_at: Optional[datetime] = None) -> Document:
    now = evaluated_at or _utc_now()
    report_id = f"MC-{card.model_id}-{card.version}"
    secs = [
        Section('1', 'Intended use', [card.intended_use]),
        Section('2', 'Out-of-scope uses', [card.out_of_scope]),
        Section('3', 'Inputs and feature definitions (with sources)', [],
                [Table(['Input', 'Unit / definition', 'Source'], [[i['name'], i['unit'], i['source']] for i in card.inputs])]),
        Section('4', 'Settings and hyperparameters', [],
                [Table(['Setting', 'Value', 'Basis'], [[x['setting'], x['value'], x['basis']] for x in card.settings])]),
        Section('5', 'Calibration and training data (provenance)', [],
                [Table(['Dataset', 'Records', 'Database', 'URL', 'Licence', 'Fetch date'],
                       [[d['dataset'], d['records'], d['database'], d['url'], d['licence'], d['fetch_date']] for d in card.calibration_data])]),
        Section('6', 'Evaluation, validation and back-test results (recomputed)', [],
                [Table(['Metric', 'Result', 'Method'], [[e['metric'], e['value'], e['method']] for e in card.evaluation])]),
        Section('7', 'Known limitations', [' '.join(f'({i+1}) {l}' for i, l in enumerate(card.limitations))]),
        Section('8', 'Re-fit and retraining history', ['No re-fit on record for this model.'] if not card.refit_history else [],
                [Table(['Decided (UTC)', 'Station', 'Coefficient', 'Before', 'After', 'Status', 'Approver'],
                       [[r['timestamp_utc'], r['station'], r['coefficient'], _fmt(r['before']), _fmt(r['after']), r['status'], r['approver']] for r in card.refit_history])] if card.refit_history else []),
        Section('9', 'Components for inspection', ['Each component is named by role with the sha256 (first 16 hex) of the source file that implements it in this build; the delivered source matches these hashes.'],
                [Table(['Component', 'sha256'], [[c['role'], c['sha256']] for c in card.components])]),
    ]
    front = [['Model ID', card.model_id], ['Version', f'{PROGRAM_NAME} build {card.version}'], ['Owner', card.owner],
             ['Generated (UTC)', card.generated_utc or now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Inputs / datasets / evaluations', f'{len(card.inputs)} / {len(card.calibration_data)} / {len(card.evaluation)}']]
    return Document(title=f'Model Card - {card.title}', report_id=report_id, front=front, sections=secs,
                    footer='Generated from the live objects of this build; every evaluation is recomputed at generation time.',
                    data={'card': card.to_dict(), 'report_id': report_id, '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 6 - Data Resilience: store-and-forward (SOW 4.2.1.6; SLA 5.0 remote site)
# ---------------------------------------------------------------------------
def data_resilience_report(sim: dict, site_name: str = '', evaluated_at: Optional[datetime] = None,
                           program_version: str = '') -> Document:
    import numpy as _np
    now = evaluated_at or _utc_now()
    st = sim.get('stats', {})
    cfg = sim.get('config', {})
    gr = sim.get('gap_report', {})
    outs = sim.get('outages', [])
    site = site_name or 'site'
    report_id = f"SF-{site.replace('/', '-').replace(' ', '_')[:24]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    delivered = sim.get('delivered', [])
    lat_all = _np.array([d.latency_s() for d in delivered if d.latency_s() is not None], dtype=float)
    lat_live = _np.array([d.latency_s() for d in delivered if d.source_layer == 'OT_LAKE' and d.latency_s() is not None], dtype=float)
    n_exp = sum(v['expected'] for v in gr.values())
    n_del = sum(v['delivered'] for v in gr.values())
    n_missing = sum(v['not_delivered'] for v in gr.values())
    dup = sum(v['duplicates_in_delivery'] for v in gr.values())
    chrono = all(v['chronological_replay'] for v in gr.values()) if gr else True
    total_out_h = sum(o['duration_h'] for o in outs)
    window_h = None
    if delivered:
        ts = sorted(parse_utc(d.timestamp_utc) for d in delivered)
        window_h = (ts[-1] - ts[0]).total_seconds() / 3600.0
    avail = round(100.0 * (1 - total_out_h / window_h), 3) if window_h else None
    pct2 = lambda a: (round(100.0 * float(_np.mean(a <= 120)), 2) if len(a) else None)
    pct5 = lambda a: (round(100.0 * float(_np.mean(a <= 300)), 2) if len(a) else None)

    summary = Section('1', 'Summary', [
        f"{n_exp} samples across {len(gr)} tags over {window_h:.1f} h with {len(outs)} link outage{'s' if len(outs) != 1 else ''} "
        f"totalling {total_out_h:.2f} h. {st.get('live', 0)} delivered live, {st.get('buffered', 0)} buffered at the edge, "
        f"{st.get('replayed', 0)} replayed in chronological order at {cfg.get('replay_rate_per_s')} records/s, "
        f"{st.get('dropped_over_capacity', 0)} dropped over the {cfg.get('capacity_hours')} h capacity, "
        f"{st.get('duplicates_suppressed', 0)} duplicates suppressed at the edge and {dup} in the delivered stream. "
        f"{n_del} of {n_exp} delivered ({n_missing} not delivered). Peak buffer occupancy "
        f"{100.0 * st.get('peak_backlog', 0) / max(cfg.get('capacity_records', 1), 1):.1f} % of capacity. "
        f"Chronological replay {'verified' if chrono else 'VIOLATED'}. Remote-site link availability {avail} % over the window."])

    cfg_rows = [['Buffer capacity', f"{cfg.get('capacity_hours')} h ({cfg.get('capacity_records')} records at {cfg.get('cadence_s')} s cadence x {len(gr)} tags)", 'SOW 4.2.1.6: 72 h store-and-forward'],
                ['Replay rate', f"{cfg.get('replay_rate_per_s')} records/s, metered per second", 'SOW 4.2.1.6: chronological, rate-controlled replay'],
                ['Edge processing latency (modelled)', f"{cfg.get('edge_latency_s')} s", 'sample timestamp to arrival at the edge'],
                ['Duplicate suppression', 'by (tag, timestamp) at the edge and verified in the delivered stream', 'SLA 5.0: no duplication']]
    cfgsec = Section('2', 'Buffer configuration (SOW 4.2.1.6)', [], [Table(['Item', 'Value', 'Requirement'], cfg_rows)])

    orows = [[o['start_utc'], o['end_utc'], f"{o['duration_h']:.2f}"] for o in outs]
    erows = [[e['timestamp_utc'], e['link'], e['backlog']] for e in sim.get('link_events', [])]
    outsec = Section('3', 'Link outages and events', [],
                     [Table(['Outage start (UTC)', 'Outage end (UTC)', 'Duration (h)'], orows, caption='Outage windows'),
                      Table(['Timestamp (UTC)', 'Link', 'Backlog at event'], erows, caption='Link events')])

    srows = [['Delivered live', st.get('live', 0)], ['Buffered at edge', st.get('buffered', 0)], ['Replayed', st.get('replayed', 0)],
             ['Dropped over capacity', st.get('dropped_over_capacity', 0)], ['Duplicates suppressed (edge)', st.get('duplicates_suppressed', 0)],
             ['Duplicates in delivered stream', dup], ['Peak backlog (records)', st.get('peak_backlog', 0)],
             ['Final backlog', sim.get('final_backlog', 0)], ['Replay slots used', sim.get('replay_slots', 0)]]
    stsec = Section('4', 'Delivery statistics', [], [Table(['Item', 'Value'], srows)])

    lrows = [['All delivered records', _fmt(float(_np.percentile(lat_all, 50))) if len(lat_all) else '-', _fmt(float(_np.percentile(lat_all, 95))) if len(lat_all) else '-',
              _fmt(float(_np.percentile(lat_all, 99))) if len(lat_all) else '-', _fmt(float(lat_all.max())) if len(lat_all) else '-', pct2(lat_all), pct5(lat_all)],
             ['Live delivery only (outage replay excluded)', _fmt(float(_np.percentile(lat_live, 50))) if len(lat_live) else '-', _fmt(float(_np.percentile(lat_live, 95))) if len(lat_live) else '-',
              _fmt(float(_np.percentile(lat_live, 99))) if len(lat_live) else '-', _fmt(float(lat_live.max())) if len(lat_live) else '-', pct2(lat_live), pct5(lat_live)]]
    latsec = Section('5', 'End-to-end latency, field edge to OT lake (SLA 1.0 definitions)',
                     ['Latency is measured per record from the sample timestamp to the ingest timestamp at the receiving layer. '
                      'The SLA target is 95 % within 2 min and 99 % within 5 min. Replayed backlog carries the outage duration as '
                      'latency by definition; SLA 7.0 excludes third-party telecom outages with a documented root cause, so both '
                      'rows are printed and the client applies the exclusion.'],
                     [Table(['Population', 'p50 (s)', 'p95 (s)', 'p99 (s)', 'max (s)', '% within 2 min', '% within 5 min'], lrows)])

    grows = [[t, v['expected'], v['delivered_live'], v['delivered_by_replay'], v['not_delivered'], v['source_gaps_in_data'],
              v['duplicates_in_delivery'], 'yes' if v['chronological_replay'] else 'NO', _fmt(v['latency_p95_s']), _fmt(v['latency_max_s'])]
             for t, v in gr.items()]
    gapsec = Section('6', 'Gap report per tag',
                     ['"Not delivered" are samples lost at the edge (over capacity); "source gaps" are samples the historian itself '
                      'had no value for (flag GAP) and are delivered as such, never invented.'],
                     [Table(['Tag', 'Expected', 'Live', 'Replayed', 'Not delivered', 'Source gaps', 'Duplicates', 'Chronological', 'Latency p95 (s)', 'Latency max (s)'], grows)])

    prov = Section('7', 'Provenance', [f"Site: {site}. The simulation drives the buffer with the records' own timestamps as the clock and the "
                                       "outage windows as the link schedule; the same buffer class serves a live edge. Results regenerate identically from the same input."])
    front = [['Report ID', report_id], ['Site', site], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Samples / delivered / lost', f'{n_exp} / {n_del} / {n_missing}'],
             ['Result', ('ALL SAMPLES DELIVERED, NO DUPLICATES, CHRONOLOGICAL' if n_missing == 0 and dup == 0 and chrono else f'{n_missing} LOST, {dup} DUPLICATES' + ('' if chrono else ', ORDER VIOLATED'))]]
    machine = {k: v for k, v in sim.items() if k != 'delivered'}
    return Document(title=f'{PROGRAM_NAME} - Data Resilience Report (store-and-forward)', report_id=report_id, front=front,
                    sections=[summary, cfgsec, outsec, stsec, latsec, gapsec, prov],
                    footer='Every statistic is recomputed from the delivered stream at generation time.',
                    data={'simulation': machine, 'report_id': report_id, 'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                          '_records': delivered, '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 7 - Monthly SLA (SLA 2.0 / 5.0)
# ---------------------------------------------------------------------------
def monthly_sla_report(m: dict, site_name: str = '', evaluated_at: Optional[datetime] = None, program_version: str = '',
                       config_summary: Optional[List[dict]] = None, sbom: Optional[dict] = None) -> Document:
    now = evaluated_at or _utc_now()
    site = site_name or 'site'
    report_id = f"SLA-{m['month']}-{site.replace('/', '-').replace(' ', '_')[:20]}"
    sc = m.get('status_counts', {})
    lines = m.get('lines', [])
    summary = Section('1', 'Summary', [
        f"SLA measurement for {m['month']} ({m['window'][0][:10]} to {m['window'][1][:10]}): {len(lines)} lines - "
        + ', '.join(f'{k} {v}' for k, v in sorted(sc.items())) + '. '
        + ('Lines marked NOT MEASURED had no record supplied and are not assumed met. ' if sc.get('NOT MEASURED') else '')
        + 'Penalty bands, if any, are read by the client from the NOT MET lines against the contract; this report states measurements only.'])
    areas = []
    seen = set()
    for l in lines:
        if l['area'] not in seen:
            seen.add(l['area']); areas.append(l['area'])
    tables = [Table(['Metric', 'Measured', 'Target', 'Status', 'Note'],
                    [[l['metric'], l['measured'], l['target'], l['status'], l['note']] for l in lines if l['area'] == ar], caption=ar) for ar in areas]
    meas = Section('2', 'SLA lines (SLA 2.0 measurement; 5.0 post-implementation; drift and staleness SLA)', [], tables)
    crows = [[c['name'], c['versions'], c['latest'], c['sha256'], c['last_change_utc'], c['last_author'], c['rollbacks']] for c in (config_summary or [])]
    csec = Section('3', 'Change and continuity (SOW 4.2.1.4, 4.2.24)',
                   ['Configuration store state at month end; every version carries author, note, sha256 and a key-level diff.' if crows else 'No configuration store supplied.'],
                   [Table(['Configuration', 'Versions', 'Latest', 'sha256', 'Last change (UTC)', 'Author', 'Rollbacks'], crows)] if crows else [])
    srows = [[c['name'], c['version'], c['supplier'], c['licence'], c['hash'], c['relationship']] for c in (sbom or {}).get('components', [])]
    ssec = Section('4', 'Software bill of materials (SCC 16.0)',
                   [f"Generated {sbom.get('generated_utc')} on {sbom.get('platform')}; {sbom.get('n_components')} components; format: {sbom.get('format')}." if sbom else 'No SBOM supplied.'],
                   [Table(['Component', 'Version', 'Supplier', 'Licence', 'Hash', 'Relationship'], srows)] if srows else [])
    irows = [[k, v or '-'] for k, v in m.get('inputs', {}).items()]
    prov = Section('5', 'Inputs and provenance', ['Records read for this measurement. A missing input yields NOT MEASURED lines above.'],
                   [Table(['Input', 'Path'], irows)])
    front = [['Report ID', report_id], ['Site', site], ['Month', m['month']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Result', f"{sc.get('MET', 0)} MET / {sc.get('NOT MET', 0)} NOT MET / {sc.get('NOT MEASURED', 0)} NOT MEASURED"]]
    return Document(title=f'{PROGRAM_NAME} - Monthly SLA Report', report_id=report_id, front=front, sections=[summary, meas, csec, ssec, prov],
                    footer='Every line is measured from the named records at generation time; nothing unmeasured is reported as met.',
                    data={'measurement': m, 'config_summary': config_summary or [], 'sbom': sbom or {}, 'report_id': report_id,
                          'evaluated_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'), '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Report 8 - FAT / SAT protocol (SOW 4.2.20 / 4.2.21)
# ---------------------------------------------------------------------------
def fat_sat_report(proto: dict, site_name: str = '', program_version: str = '') -> Document:
    kind = proto['kind']
    clause = '4.2.20' if kind == 'FAT' else '4.2.21'
    title = 'Factory Acceptance Test Protocol' if kind == 'FAT' else 'Site Acceptance Test Protocol'
    report_id = f"{kind}-{proto['started_utc'].replace(':', '').replace('-', '')}"
    summary = Section('1', 'Summary', [
        f"{proto['n_steps']} numbered steps across sections {', '.join(proto['sections'])}: {proto['n_pass']} PASS, {proto['n_fail']} FAIL. "
        f"Result: {proto['result']}. Run {proto['started_utc']} to {proto['finished_utc']}. "
        f"{proto['internal_checks_excluded']} internal build checks ran alongside and are excluded from this client protocol; "
        "no check was altered."])
    secs = [summary]
    n = 2
    if proto.get('environment'):
        env = proto['environment']
        secs.append(Section(str(n), 'Installed environment (SAT)', [f"Platform {env.get('platform')}; Python {env.get('python')}; SBOM generated {env.get('generated_utc')}."],
                            [Table(['Component', 'Version', 'Hash', 'Relationship'], [[c['name'], c['version'], c['hash'], c['relationship']] for c in env.get('components', [])])]))
        n += 1
    for key in proto['sections']:
        rows = [[r['step'], r['check'], r['expected'], r['actual'], r['witness'] or '________'] for r in proto['rows'] if r['section'] == key]
        title_s = next((t for k, _, t in __import__('gea.fat_sat', fromlist=['CLIENT_SECTIONS']).CLIENT_SECTIONS if k == key), key)
        secs.append(Section(str(n), f'Section {key} - {title_s}', [], [Table(['Step', 'Check', 'Expected', 'Actual', 'Witness'], rows)]))
        n += 1
    secs.append(Section(str(n), 'Signatures', [],
                        [Table(['Role', 'Name', 'Signature', 'Date'], [['Test engineer (supplier)', '________________', '________________', '__________'],
                                                                        ['Witness (client)', '________________', '________________', '__________'],
                                                                        ['Approver (client)', '________________', '________________', '__________']])]))
    front = [['Protocol ID', report_id], ['Type', f'{title} (SOW {clause})'], ['Site', site_name or ('factory' if kind == 'FAT' else 'site')],
             ['Program', f'{PROGRAM_NAME}' + (f' build {program_version}' if program_version else '')],
             ['Run (UTC)', f"{proto['started_utc']} to {proto['finished_utc']}"], ['Steps', f"{proto['n_steps']} ({proto['n_pass']} pass / {proto['n_fail']} fail)"],
             ['Result', proto['result']]]
    return Document(title=f'{PROGRAM_NAME} - {title}', report_id=report_id, front=front, sections=secs,
                    footer='Each step is executed by the program against its own outputs at run time; the witness column is signed on paper or in the approval workflow.',
                    data={'protocol': {k: v for k, v in proto.items() if k != 'environment'}, 'environment': proto.get('environment'),
                          'report_id': report_id, '_records': [], '_catalogue_obj': None})


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def render_markdown(doc: Document) -> str:
    L: List[str] = [f'# {doc.title}', '']
    L.append('| | |')
    L.append('|---|---|')
    for k, v in doc.front:
        L.append(f'| **{k}** | {v} |')
    L.append('')
    for s in doc.sections:
        L.append(f'## {s.number}. {s.title}')
        L.append('')
        for p in s.paragraphs:
            L.append(p)
            L.append('')
        for t in s.tables:
            if t.caption:
                L.append(f'*{t.caption}*')
                L.append('')
            L.append('| ' + ' | '.join(t.columns) + ' |')
            L.append('|' + '---|' * len(t.columns))
            for r in t.rows:
                L.append('| ' + ' | '.join(_fmt(c).replace('|', '/') for c in r) + ' |')
            L.append('')
    if doc.footer:
        L.append('---')
        L.append(f'*{doc.footer}*')
        L.append('')
    return '\n'.join(L)


_CSS = ('body{font-family:Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:1200px;color:#1a1a1a;'
        'background:#fff;padding:0 16px}h1{font-size:1.5rem}h2{font-size:1.1rem;margin-top:2rem;'
        'border-bottom:1px solid #ccc;padding-bottom:.2rem}table{border-collapse:collapse;margin:.5rem 0 1rem;'
        'font-size:.85rem;width:100%}th,td{border:1px solid #d0d0d0;padding:.3rem .5rem;text-align:left;'
        'vertical-align:top}th{background:#f2f2f2}caption{text-align:left;font-style:italic;padding:.2rem 0}'
        '.front td:first-child{font-weight:600;width:14rem}.result-bad{color:#a40000;font-weight:700}'
        '.result-ok{color:#0a6b1f;font-weight:700}footer{margin-top:2rem;font-size:.8rem;color:#555}')


def render_html(doc: Document) -> str:
    e = _html.escape
    H: List[str] = ['<!doctype html>', '<html lang="en"><head><meta charset="utf-8">',
                    f'<title>{e(doc.title)}</title>', f'<style>{_CSS}</style></head><body>',
                    f'<h1>{e(doc.title)}</h1>', '<table class="front">']
    for k, v in doc.front:
        cls = ''
        if k == 'Result':
            cls = ' class="result-bad"' if 'DETECTED' in v and 'NO ' not in v else ' class="result-ok"'
        H.append(f'<tr><td>{e(k)}</td><td{cls}>{e(v)}</td></tr>')
    H.append('</table>')
    for s in doc.sections:
        H.append(f'<h2>{e(s.number)}. {e(s.title)}</h2>')
        for p in s.paragraphs:
            H.append(f'<p>{e(p)}</p>')
        for t in s.tables:
            H.append('<table>')
            if t.caption:
                H.append(f'<caption>{e(t.caption)}</caption>')
            H.append('<tr>' + ''.join(f'<th>{e(c)}</th>' for c in t.columns) + '</tr>')
            for r in t.rows:
                H.append('<tr>' + ''.join(f'<td>{e(_fmt(c))}</td>' for c in r) + '</tr>')
            H.append('</table>')
    if doc.footer:
        H.append(f'<footer>{e(doc.footer)}</footer>')
    H.append('</body></html>')
    return '\n'.join(H)


def forbidden_terms(text: str) -> List[str]:
    low = text.lower()
    return [t for t in FORBIDDEN_TERMS if t in low]


def write(doc: Document, out_dir: str, basename: str = 'gauge_drift_report') -> dict:
    """Write markdown, HTML, the machine JSON, the records CSV and the tag
    catalogue CSV. Returns the paths. Raises if the rendered report contains
    any internal-register term."""
    os.makedirs(out_dir, exist_ok=True)
    md = render_markdown(doc)
    ht = render_html(doc)
    bad = forbidden_terms(md) + forbidden_terms(ht)
    if bad:
        raise ValueError(f'client report contains internal-register terms: {sorted(set(bad))}')
    paths = {'markdown': os.path.join(out_dir, basename + '.md'),
             'html': os.path.join(out_dir, basename + '.html'),
             'json': os.path.join(out_dir, basename + '.json'),
             'records_csv': os.path.join(out_dir, basename + '_records.csv'),
             'catalogue_csv': os.path.join(out_dir, basename + '_tags.csv')}
    with open(paths['markdown'], 'w', encoding='utf-8', newline='') as f:
        f.write(md)
    with open(paths['html'], 'w', encoding='utf-8', newline='') as f:
        f.write(ht)
    machine = {k: v for k, v in doc.data.items() if not k.startswith('_')}
    with open(paths['json'], 'w', encoding='utf-8') as f:
        json.dump(machine, f, indent=1, default=str)
    if doc.data.get('_records'):
        write_records_csv(doc.data['_records'], paths['records_csv'])
    else:
        paths.pop('records_csv')
    if doc.data.get('_catalogue_obj') is not None:
        write_catalogue_csv(doc.data['_catalogue_obj'], paths['catalogue_csv'])
    else:
        paths.pop('catalogue_csv')
    return paths
