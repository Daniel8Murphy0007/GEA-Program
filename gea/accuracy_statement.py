"""accuracy_statement — the accuracy statement in the client's statistic.

A production-operations client states tool accuracy as MAPE (mean absolute
percentage error) at a stated confidence interval, per predicted quantity,
per well, with n and the back-test window, and reads the result against an
accuracy band. This module produces exactly that from per-trial back-test
results, and a back-test runner that re-scores the library's blind
(leave-one-out) predictions trial by trial so the statistic is computed on
the raw errors, never on a summary.

Definitions in force (printed on every statement; client-configurable):

    APE_i      = |estimate_i - truth_i| / |truth_i| x 100
    MAPE       = mean(APE_i)
    Accuracy   = 100 - MAPE
    CI         = bootstrap percentile interval on MAPE (default 90 %,
                 2,000 resamples, fixed seed - reproducible)
    Coverage90 = fraction of trials whose truth lies inside the estimate's
                 own +/- 1.645 sigma band (calibration check; ~0.90 expected)
    Band       = accuracy read against the thresholds
                 MEETS_TARGET >= 95 / BAND_2 90-95 / BAND_3 85-90 /
                 NOT_ACCEPTABLE < 85, using the CONSERVATIVE end of the CI
                 (accuracy at the upper CI bound of MAPE) so a statement
                 never claims a band the interval does not support.

Trials below MIN_TRIALS are reported PENDING with n, never scored.

Headless-safe: numpy only.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence

import numpy as np

MIN_TRIALS = 10
DEFAULT_CI = 0.90
N_BOOT = 2000
SEED = 20260928
Z90 = 1.6448536269514722

BANDS = [(95.0, 'MEETS_TARGET'), (90.0, 'BAND_2'), (85.0, 'BAND_3'), (-1e9, 'NOT_ACCEPTABLE')]


def band_for(accuracy_pct: float) -> str:
    for thr, name in BANDS:
        if accuracy_pct >= thr:
            return name
    return 'NOT_ACCEPTABLE'


@dataclass
class Trial:
    truth: float
    estimate: float
    std: float            # the estimate's own 1-sigma spread (0 if none)


def statement_from_trials(trials: Sequence[Trial], ci: float = DEFAULT_CI,
                          n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """MAPE with a bootstrap CI, coverage at the CI level, and the band."""
    n = len(trials)
    if n < MIN_TRIALS:
        return {'status': 'PENDING', 'n': n, 'min_n': MIN_TRIALS}
    truth = np.array([t.truth for t in trials], dtype=float)
    est = np.array([t.estimate for t in trials], dtype=float)
    std = np.array([t.std for t in trials], dtype=float)
    nz = np.abs(truth) > 0
    if nz.sum() < MIN_TRIALS:
        return {'status': 'PENDING', 'n': int(nz.sum()), 'min_n': MIN_TRIALS,
                'note': 'truth values at zero cannot carry a percentage error'}
    ape = np.abs(est[nz] - truth[nz]) / np.abs(truth[nz]) * 100.0
    mape = float(ape.mean())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ape), size=(n_boot, len(ape)))
    boots = ape[idx].mean(axis=1)
    alpha = (1.0 - ci) / 2.0
    lo, hi = float(np.percentile(boots, 100 * alpha)), float(np.percentile(boots, 100 * (1 - alpha)))
    z = Z90 if abs(ci - 0.90) < 1e-9 else float(_z_for(ci))
    with_std = std > 0
    cov = float(np.mean(np.abs(est[with_std] - truth[with_std]) <= z * std[with_std])) if with_std.any() else None
    acc = 100.0 - mape
    acc_conservative = 100.0 - hi
    return {
        'status': 'OK', 'n': int(len(ape)), 'ci': ci,
        'mape_pct': round(mape, 3), 'mape_ci_lo_pct': round(lo, 3), 'mape_ci_hi_pct': round(hi, 3),
        'accuracy_pct': round(acc, 3), 'accuracy_conservative_pct': round(acc_conservative, 3),
        'coverage_at_ci': (round(cov, 3) if cov is not None else None),
        'n_with_spread': int(with_std.sum()),
        'band': band_for(acc_conservative), 'band_point': band_for(acc),
        'max_ape_pct': round(float(ape.max()), 3), 'median_ape_pct': round(float(np.median(ape)), 3),
    }


def _z_for(ci: float) -> float:
    # two-sided normal quantile by bisection on the error function (no scipy)
    import math
    target = ci
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if math.erf(mid / math.sqrt(2)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Back-test runner: the library's blind predictions, trial by trial
# ---------------------------------------------------------------------------
def _loo_trials(pairs: List[tuple], k: int = 7) -> List[Trial]:
    from .inverse_engine import _conditional_from_pairs
    out: List[Trial] = []
    for i in range(len(pairs)):
        rest = pairs[:i] + pairs[i + 1:]
        g, t = pairs[i]
        c = _conditional_from_pairs(rest, g, k)
        if c['status'] != 'OK':
            continue
        out.append(Trial(truth=float(t), estimate=float(c['estimate']), std=float(c['std'])))
    return out


def library_backtest(ci: float = DEFAULT_CI, k: int = 7) -> dict:
    """Leave-one-out back-test of every supported property pair in the
    library, scored as an accuracy statement per pair. Same estimator, same
    library, same hold-out as the standing blind harness; the difference is
    that the raw trials are kept so MAPE and its CI can be computed."""
    from . import strata_join as SJ
    from .inverse_engine import _site_native_pairs
    rows = []
    for well, (_, props) in SJ.WELL_GROUPS.items():
        names = sorted(props)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs, _bin = SJ._colocated(well, a, b, None)
                st = statement_from_trials(_loo_trials(pairs, k), ci=ci)
                st.update({'well': well, 'given': a, 'target': b, 'source': 'strata_join',
                           'n_pairs': len(pairs)})
                rows.append(st)
    try:
        pairs, washouts = _site_native_pairs('ktb_hb_complog_6020_excerpt')
        st = statement_from_trials(_loo_trials(pairs, k), ci=ci)
        st.update({'well': 'ktb_complog', 'given': 'rho (g/cc)', 'target': 'Vp (m/s)',
                   'source': 'site_pairs', 'n_pairs': len(pairs), 'washouts_excluded': washouts})
        rows.append(st)
    except KeyError:
        pass
    ok = [r for r in rows if r['status'] == 'OK']
    pending = [r for r in rows if r['status'] != 'OK']
    ok.sort(key=lambda r: r['mape_pct'])
    bands: Dict[str, int] = {}
    for r in ok:
        bands[r['band']] = bands.get(r['band'], 0) + 1
    return {
        'method': {'hold_out': 'leave-one-out', 'estimator': f'k-nearest empirical conditional, k={k}',
                   'ci': ci, 'bootstrap_resamples': N_BOOT, 'seed': SEED, 'min_trials': MIN_TRIALS,
                   'band_rule': 'band from accuracy at the upper CI bound of MAPE (conservative)'},
        'statements': ok, 'pending': pending, 'n_ok': len(ok), 'n_pending': len(pending),
        'bands': bands,
        'worst_mape_pct': (ok[-1]['mape_pct'] if ok else None),
        'best_mape_pct': (ok[0]['mape_pct'] if ok else None),
        'median_coverage_at_ci': (statistics.median([r['coverage_at_ci'] for r in ok if r['coverage_at_ci'] is not None])
                                  if any(r['coverage_at_ci'] is not None for r in ok) else None),
    }


def statement_from_arrays(truth, estimate, std=None, ci: float = DEFAULT_CI) -> dict:
    """Convenience: arrays -> statement (for any predictor, gauge track included)."""
    truth = np.asarray(truth, dtype=float)
    estimate = np.asarray(estimate, dtype=float)
    std = np.zeros_like(truth) if std is None else np.asarray(std, dtype=float)
    m = ~(np.isnan(truth) | np.isnan(estimate))
    return statement_from_trials([Trial(float(t), float(e), float(s)) for t, e, s in zip(truth[m], estimate[m], std[m])], ci=ci)
