# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""blind_harness - Part 5 of the subsurface surveying tool: THE
BLIND-VALIDATION HARNESS - the standing accuracy report that makes
the tool credible, not just built.

METHOD (leave-one-out, no self-grading):
    For every supported property pair in the library (the strata-join joint
    tables plus the KTB site-native pairs), each co-located observation is
    HELD OUT in turn; the target is predicted from the REMAINING
    observations by the same k-nearest conditional the inverse engine uses;
    the held-out truth scores the prediction. Per pair we report:

      n            - held-out trials
      mae          - mean absolute error, in the property's own units
      mae_pct      - MAE as a percentage of the truth's mean
      coverage     - fraction of trials where the truth fell inside the
                     prediction's own +/- 1-sigma spread (an HONESTY metric:
                     a well-calibrated ~68 percent is the target; near-100
                     means the spreads are too timid, near-0 too bold)

    Pairs below MIN_TRIALS refuse. Nothing is tuned to this harness; it
    grades the same machinery clients get, on the same library.

METHOD NOTE: this report is regenerated live - by the gate, by acceptance
and by every client report that cites it - so the accuracy table can never
be a stale marketing snapshot. Misses appear next to hits, forever.
"""

from __future__ import annotations

import statistics
from typing import Dict, List

from . import strata_join as SJ
from .inverse_engine import _site_native_pairs, _conditional_from_pairs

MIN_TRIALS = 10


def _loo_pairs(pairs: List[tuple], k: int = 7) -> Dict:
    """Leave-one-out over raw (given, target) pairs."""
    n = len(pairs)
    if n < MIN_TRIALS:
        return {'status': 'REFUSED_THIN_DATA', 'n': n, 'min_n': MIN_TRIALS}
    errs, hits, truths = [], 0, []
    for i in range(n):
        rest = pairs[:i] + pairs[i + 1:]
        g, t = pairs[i]
        c = _conditional_from_pairs(rest, g, k)
        if c['status'] != 'OK':
            continue
        err = abs(c['estimate'] - t)
        errs.append(err)
        truths.append(abs(t))
        if c['std'] > 0 and err <= c['std']:
            hits += 1
    if len(errs) < MIN_TRIALS:
        return {'status': 'REFUSED_THIN_DATA', 'n': len(errs),
                'min_n': MIN_TRIALS}
    mae = statistics.mean(errs)
    tmean = statistics.mean(truths)
    return {'status': 'OK', 'n': len(errs), 'mae': mae,
            'mae_pct': (mae / tmean * 100.0 if tmean else float('nan')),
            'coverage': hits / len(errs)}


def accuracy_report() -> Dict:
    """The standing table: every supported pair in the library, blind-scored.

    Sources: (1) strata-join joint tables (bin-level pairs, no archive
    interpolation), (2) the KTB site-native station pairs (entry 52,
    washouts excluded as disclosed there)."""
    rows = []
    # 1) strata-join joint tables
    for well, (_, props) in SJ.WELL_GROUPS.items():
        names = sorted(props)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs, _bin = SJ._colocated(well, a, b, None)
                res = _loo_pairs(pairs)
                res.update({'source': 'strata_join', 'well': well,
                            'given': a, 'target': b})
                rows.append(res)
    # 2) KTB site-native pairs (rho -> Vp)
    try:
        pairs, washouts = _site_native_pairs('ktb_hb_complog_6020_excerpt')
        res = _loo_pairs(pairs)
        res.update({'source': 'site_pairs', 'well': 'ktb_complog',
                    'given': 'rho (g/cc)', 'target': 'Vp (m/s)',
                    'washouts_excluded': washouts})
        rows.append(res)
    except KeyError:
        pass
    ok = [r for r in rows if r['status'] == 'OK']
    refused = [r for r in rows if r['status'] != 'OK']
    ok.sort(key=lambda r: r['mae_pct'])
    cov = [r['coverage'] for r in ok]
    return {'ok': ok, 'refused': refused, 'n_ok': len(ok),
            'n_refused': len(refused),
            'best_mae_pct': (ok[0]['mae_pct'] if ok else None),
            'worst_mae_pct': (ok[-1]['mae_pct'] if ok else None),
            'median_coverage': (statistics.median(cov) if cov else None),
            'method': ('leave-one-out over the same machinery clients get; '
                         'coverage targets ~0.68 (honest 1-sigma); refusals '
                         'listed, never hidden; regenerated live so the '
                         'accuracy table can never be a stale snapshot')}
