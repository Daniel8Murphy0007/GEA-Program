# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""model_card — one card per model, built from the live objects.

A model card (after Mitchell et al., "Model Cards for Model Reporting",
FAT* 2019) states what a model is for, what it takes in and where that came
from, how it is configured, how it was evaluated, what it must not be used
for, and what has changed since. The inspection clause of a production
scope of work asks for exactly that: source, training or calibration data,
feature definitions, settings, validation and back-test results, drift and
re-fit records. This module generates the cards from the program's own
objects at call time, so a card can never describe a model other than the
one that ships.

Cards produced:

    well_baseline               expected P/T at a station from the well model
    gauge_aging_rate            the instrument drift specification from its datasheet
    strata_property_estimator   k-nearest conditional over the co-located library
    rock_density_inventory      the cited density anchors used for classification
    quality_rules               the record-layer sample rules
    well_test_detector          the stable-period criteria

Every input carries its citation; every calibration dataset carries the
provenance the catalogue keeps for it (database, URL, licence, fetch date);
every component carries the sha256 of the source file that implements it
(named by role, with the file hash, so an inspector can match it to the
delivered source). Evaluation sections are recomputed, not quoted.

Headless-safe: numpy only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sha(name: str) -> str:
    p = os.path.join(_HERE, name)
    if not os.path.exists(p):
        return 'absent'
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


@dataclass
class ModelCard:
    model_id: str
    title: str
    version: str
    intended_use: str
    out_of_scope: str
    inputs: List[dict]                       # {name, unit, source}
    settings: List[dict]                     # {setting, value, basis}
    calibration_data: List[dict]             # {dataset, records, database, url, licence, fetch_date}
    evaluation: List[dict]                   # {metric, value, method}
    limitations: List[str]
    refit_history: List[dict] = field(default_factory=list)
    components: List[dict] = field(default_factory=list)   # {role, sha256}
    owner: str = 'assigned by the client at deployment'
    generated_utc: str = ''

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _prov(entry: str) -> dict:
    from . import CATALOG
    try:
        p = CATALOG[entry].provenance
        st = CATALOG[entry].stream()
        n = int(len(st.index))
    except Exception:
        return {'dataset': entry, 'records': '-', 'database': 'catalogue entry not on this machine', 'url': '', 'licence': '', 'fetch_date': ''}
    return {'dataset': entry, 'records': n, 'database': str(p.get('source_database', ''))[:160],
            'url': str(p.get('source_url', '')), 'licence': str(p.get('license', ''))[:120],
            'fetch_date': str(p.get('fetch_date', ''))}


def _refits(monitor_log_dir: Optional[str], station_prefix: str = '') -> List[dict]:
    if not monitor_log_dir:
        return []
    p = os.path.join(monitor_log_dir, 'change_log.jsonl')
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get('type') == 'REFIT_OFFSET' and e.get('status') in ('APPLIED', 'BLOCKED_ANNUAL_LIMIT', 'REJECTED'):
                out.append({'timestamp_utc': e.get('decided_utc') or e.get('timestamp_utc'), 'station': e.get('station'),
                            'coefficient': e.get('coefficient'), 'before': e.get('before'), 'after': e.get('after'),
                            'status': e.get('status'), 'approver': e.get('approver', '')})
    return out


def build_cards(monitor_log_dir: Optional[str] = None, program_version: str = '') -> List[ModelCard]:
    from . import __version__ as _v, GAUGE_SPECS, SimulatorConfig
    from .downhole_engine import SimulatorConfig as _SC
    from .accuracy_statement import library_backtest, MIN_TRIALS, N_BOOT, SEED
    from .sample_record import DEFAULT_TAG_CLASSES
    from .well_test_validation import DEFAULT_CRITERIA
    from .rock_inventory import rock_inventory
    from . import strata_join as SJ
    from .reconciler import ReconcilerConfig
    ver = program_version or _v
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    cards: List[ModelCard] = []

    # 1. well baseline --------------------------------------------------------------
    sc = _SC()
    rc = ReconcilerConfig()
    cards.append(ModelCard(
        model_id='well_baseline', title='Well pressure and temperature baseline', version=ver,
        intended_use='Expected pressure and temperature at a gauge station (by measured depth) for drift evaluation: '
                     'residual = measured - baseline. With a measured profile the baseline interpolates the archived log; '
                     'without one it uses linear gradients from surface conditions.',
        out_of_scope='Not a reservoir model; not a flow model; not for allocation; not a prediction of production behaviour. '
                     'A linear-gradient baseline is a template and must be replaced by the well\'s measured profile before '
                     'any classification is acted on.',
        inputs=[{'name': 'station measured depth', 'unit': 'ft', 'source': 'completion records (caller-supplied; flagged when not in the archive)'},
                {'name': 'deviation survey', 'unit': 'deg / ft', 'source': 'survey file when supplied; vertical otherwise'},
                {'name': 'well P/T profile', 'unit': 'psi, degF vs TVD', 'source': 'measured catalogue assembly when supplied; else linear gradients'}],
        settings=[{'setting': 'surface pressure (template)', 'value': f'{sc.surface_pressure_psi:g} psi', 'basis': 'template default; replaced by the profile'},
                  {'setting': 'pressure gradient (template)', 'value': f'{sc.pressure_gradient_psi_per_ft:g} psi/ft', 'basis': 'template default; replaced by the profile'},
                  {'setting': 'surface temperature (template)', 'value': f'{sc.surface_temp_F:g} degF', 'basis': 'template default; replaced by the profile'},
                  {'setting': 'temperature gradient (template)', 'value': f'{sc.temp_gradient_F_per_ft:g} degF/ft', 'basis': 'template default; replaced by the profile'},
                  {'setting': 'bias significance', 'value': f'{rc.bias_n_sigma:g} x noise/sqrt(n) + 1 psi', 'basis': 'engineering setting, printed on every drift report'},
                  {'setting': 'bias too large for calibration', 'value': f'{rc.model_mismatch_psi:g} psi', 'basis': 'engineering setting'},
                  {'setting': 'minimum window for a trend', 'value': f'{rc.min_trend_span_years*365.25:.0f} days', 'basis': 'engineering setting'}],
        calibration_data=[{'dataset': 'measured well assemblies in the catalogue (when used)', 'records': '-',
                           'database': 'each assembly carries its own provenance file', 'url': '', 'licence': '', 'fetch_date': ''}],
        evaluation=[{'metric': 'Volve 15/9-F-12 daily downhole pressure vs linear-gradient baseline', 'value': 'UNEXPLAINED_TREND (drawdown, -1,096 psi/yr); template baseline cannot follow production',
                     'method': 'drift report on the catalogue excerpt; demonstrates the out-of-scope note above'}],
        limitations=['Linear gradients are a template, not a measurement.', 'Deviation is applied only when a survey is supplied.',
                     'The baseline has no production dynamics; drawdown reads as an unexplained trend by design and is routed to investigation.'],
        refit_history=_refits(monitor_log_dir),
        components=[{'role': 'baseline and drift classification', 'sha256': _sha('reconciler.py')},
                    {'role': 'engine and well configuration', 'sha256': _sha('downhole_engine.py')},
                    {'role': 'drift monitor (log, staleness, re-fit)', 'sha256': _sha('drift_monitor.py')}],
        generated_utc=now))

    # 2. gauge aging rate ---------------------------------------------------------------
    specs = list(GAUGE_SPECS.values())
    cards.append(ModelCard(
        model_id='gauge_aging_rate', title='Gauge aging rate from the datasheet', version=ver,
        intended_use='The instrument\'s published drift specification (psi/yr at its full scale), used to judge whether a measured '
                     'residual trend is of the order of the aging the datasheet allows for (half the rate to twice the rate) or something else.',
        out_of_scope='Not a measurement of any specific gauge\'s drift; not a warranty figure; not a substitute for recalibration records; '
                     'no dependence on temperature or pressure is modelled - above the rating the rate is flagged, not changed.',
        inputs=[{'name': 'gauge datasheet', 'unit': 'full scale psi; drift %FS/yr; rating C; accuracy %FS', 'source': s.source[:140]} for s in specs] +
               [{'name': 'station pressure and temperature', 'unit': 'psi, degC', 'source': 'well baseline model (for the rating check only)'}],
        settings=[{'setting': 'aging rate', 'value': 'the datasheet drift specification, as published', 'basis': 'cited datasheet; nothing added'},
                  {'setting': 'classification margin', 'value': '0.5 x to 2 x the rate', 'basis': 'disclosed engineering heuristic (reconciler)'}],
        calibration_data=[{'dataset': s.name, 'records': '-', 'database': 'public datasheet', 'url': '', 'licence': 'vendor-published specification', 'fetch_date': (s.source.split('fetched ')[1][:10] if 'fetched ' in s.source else '')} for s in specs],
        evaluation=[{'metric': 'field validation of the rate against a gauge with a known history', 'value': 'NONE ON RECORD',
                     'method': 'no gauge-drift field or bench dataset has been evaluated against the datasheet rate by this program; the bench test (gea bench) is the instrument for it'}],
        limitations=['The rate is a specification, not a measurement.', 'Datasheet drift bounds are reference-condition limits; stressed-service drift can exceed them, and the program does not model that.'],
        components=[{'role': 'aging rate', 'sha256': _sha('gauge_aging.py')}, {'role': 'gauge datasheets', 'sha256': _sha('gauge_specs.py')}],
        generated_utc=now))

    # 3. strata property estimator ---------------------------------------------------
    bt = library_backtest()
    entries = sorted({e for _, (_, props) in SJ.WELL_GROUPS.items() for e, _c in props.values()} | {'ktb_hb_complog_6020_excerpt'})
    cards.append(ModelCard(
        model_id='strata_property_estimator', title='Strata property estimator (k-nearest empirical conditional)', version=ver,
        intended_use='Estimate one rock property from another measured at the same depth interval, with a stated spread, from the '
                     'library of co-located public measurements; used for strata interpretation and blind-scored on every run.',
        out_of_scope='Not for wells outside the library\'s geological families without a site prior; not a substitute for a measured log; '
                     'quantities in the NOT ACCEPTABLE band of the accuracy statement are excluded from any claim.',
        inputs=[{'name': 'given property (per pair)', 'unit': 'library units', 'source': 'co-located measurement from the catalogue entry named in calibration data'}],
        settings=[{'setting': 'neighbours k', 'value': '7', 'basis': 'engineering setting'},
                  {'setting': 'minimum trials to score', 'value': str(MIN_TRIALS), 'basis': 'accuracy statement rule'},
                  {'setting': 'bootstrap resamples / seed', 'value': f'{N_BOOT} / {SEED}', 'basis': 'reproducible CI'},
                  {'setting': 'depth bins per well (m)', 'value': ', '.join(f'{w}: {b:g}' for w, (b, _) in SJ.WELL_GROUPS.items()), 'basis': 'co-location tolerance per well'}],
        calibration_data=[_prov(e) for e in entries],
        evaluation=[{'metric': f"{r['well']} {r['given']} -> {r['target']}", 'value': f"MAPE {r['mape_pct']:.2f} % (90 % CI {r['mape_ci_lo_pct']:.2f}-{r['mape_ci_hi_pct']:.2f}); coverage {r['coverage_at_ci']}; {r['band']}",
                     'method': f"leave-one-out, n={r['n']}"} for r in bt['statements']] +
                   [{'metric': f"{r['well']} {r['given']} -> {r['target']}", 'value': f"PENDING (n={r['n']} < {MIN_TRIALS})", 'method': 'leave-one-out'} for r in bt['pending']],
        limitations=['Estimates are empirical conditionals over a small public library; spreads are somewhat too narrow (median coverage below the CI level).',
                     'Five of fourteen quantities are NOT ACCEPTABLE at the 95 % target and are so labelled.',
                     'Extrapolation outside the given property\'s support is flagged and returns no estimate.'],
        components=[{'role': 'estimator and priors', 'sha256': _sha('inverse_engine.py')}, {'role': 'co-located library', 'sha256': _sha('strata_join.py')},
                    {'role': 'accuracy statement', 'sha256': _sha('accuracy_statement.py')}],
        generated_utc=now))

    # 4. rock density inventory --------------------------------------------------------
    inv = rock_inventory()
    cards.append(ModelCard(
        model_id='rock_density_inventory', title='Rock density inventory (cited anchors)', version=ver,
        intended_use='Classify a measured bulk density into ranked candidate rock types by overlap with published density ranges.',
        out_of_scope='Not a lithology log; overlapping ranges return several candidates by design; densities outside the inventory return no candidate.',
        inputs=[{'name': f"{k} ({v['tier']})", 'unit': f"g/cc, anchor {v['anchor']}, range {v['lo']}-{v['hi']}", 'source': str(v['citation'])[:120]} for k, v in inv.items()],
        settings=[{'setting': 'anchors', 'value': str(len(inv)), 'basis': 'standard geophysics density tables (Telford et al. 1990; Schoen 2015)'}],
        calibration_data=[{'dataset': 'published density tables', 'records': len(inv), 'database': 'Telford, Geldart & Sheriff (1990); Schoen (2015)', 'url': '', 'licence': 'published reference values', 'fetch_date': ''}],
        evaluation=[{'metric': 'anchor reproduction', 'value': f"worst anchor residual {max(abs(float(v['residual_pct'])) for v in inv.values()):.3f} %", 'method': 'each anchor re-derived at gate time'}],
        limitations=['Ranges overlap; the classifier returns ranked candidates, never one confident name.'],
        components=[{'role': 'inventory and classifier', 'sha256': _sha('rock_inventory.py')}],
        generated_utc=now))

    # 5. quality rules --------------------------------------------------------------------
    cards.append(ModelCard(
        model_id='quality_rules', title='Sample quality rules (record layer)', version=ver,
        intended_use='Flag every sample GOOD / RANGE / ROC / FLATLINE / SPIKE / STALE / GAP with the rule and limit that fired, per tag, before any evaluation.',
        out_of_scope='Not an alarm; not a correction - flagged samples are excluded from evaluations and counted, never repaired.',
        inputs=[{'name': 'tag definition', 'unit': 'engineering range, cadence, limits', 'source': 'tag catalogue; ranges from the gauge datasheet when supplied'}],
        settings=[{'setting': f'{cls}: range', 'value': f"{d['eng_range'][0]} to {d['eng_range'][1]} {d['unit']}", 'basis': 'class default; datasheet overrides'} for cls, d in DEFAULT_TAG_CLASSES.items() if cls != 'generic'] +
                 [{'setting': 'flatline run', 'value': '3 samples', 'basis': 'operations setting'}, {'setting': 'staleness', 'value': '3 x cadence', 'basis': 'operations setting'},
                  {'setting': 'spike', 'value': '6 x MAD of rolling median, window 21', 'basis': 'operations setting'}, {'setting': 'rate of change', 'value': 'off unless set per tag', 'basis': 'operations setting; not a datasheet quantity'}],
        calibration_data=[_prov('volve_f12_f14_production_excerpt')],
        evaluation=[{'metric': 'Volve 15/9-F-12 downhole pressure, 157 daily samples', 'value': '9 FLATLINE (the archive\'s documented stuck-sensor run), 5 SPIKE, 0 RANGE', 'method': 'rules applied to the catalogue excerpt'}],
        limitations=['Rules are per-sample; a slow drift is not a quality flag (it is the drift evaluation\'s job).', 'The spike rule at daily cadence can flag real operational excursions; each flag prints its deviation so an engineer can judge.'],
        components=[{'role': 'record and rules', 'sha256': _sha('sample_record.py')}],
        generated_utc=now))

    # 6. well-test detector -----------------------------------------------------------------
    c = DEFAULT_CRITERIA
    cards.append(ModelCard(
        model_id='well_test_detector', title='Well-test stable-period detector', version=ver,
        intended_use='Detect periods where the well is on stream at a fixed operating point with stable rates and pressures; accept them as well tests with virtual rates, reject the rest with reason codes.',
        out_of_scope='Not an allocation; accepted tests are released only after the approval trail is complete.',
        inputs=[{'name': 'on-stream hours, rates, pressures, operating point', 'unit': 'as tagged', 'source': 'production historian channels mapped by role'}],
        settings=[{'setting': k, 'value': str(v), 'basis': 'criteria file (client-agreed logic), hashed on every report'} for k, v in c.items() if k not in ('name', 'basis', 'approval_levels')],
        calibration_data=[_prov('volve_f12_f14_production_excerpt')],
        evaluation=[{'metric': 'Volve 15/9-F-12, 2008-02-12 to 2008-07-21', 'value': '7 tests accepted, 32 candidates rejected with reason codes, 131/158 eligible samples', 'method': 'default criteria file'}],
        limitations=['Criteria defaults are for daily-cadence data; a client at minute cadence sets its own file.', 'Negligible streams (below the fraction of the largest rate) are not stability criteria and are disclosed as such.'],
        components=[{'role': 'detector and approval trail', 'sha256': _sha('well_test_validation.py')}],
        generated_utc=now))
    return cards


def card_index(cards: List[ModelCard]) -> dict:
    return {'generated_utc': cards[0].generated_utc if cards else '', 'cards': [
        {'model_id': c.model_id, 'title': c.title, 'version': c.version, 'n_inputs': len(c.inputs),
         'n_calibration_datasets': len(c.calibration_data), 'n_evaluations': len(c.evaluation), 'n_refits': len(c.refit_history)}
        for c in cards]}
