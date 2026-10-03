# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""bench - does this gauge drift within its datasheet? The bench record.

The analysis half of BENCH_TEST_PROTOCOL.md. One gauge under test is held at
a constant setpoint against a reference standard for weeks; its readings are
logged; this module fits the drift slope with its standard error and compares
it with the instrument's published drift specification. The outcome is the
bench record the drift report has been waiting for: a gauge with a known
history, measured against the rate the program uses, pass or fail, written
down.

Verdicts:
    WITHIN_DATASHEET    the measured rate plus k sigma is inside the specification
    EXCEEDS_DATASHEET   the measured rate minus k sigma is above the specification
    INSUFFICIENT_SPAN   the record is shorter than the reconciler's own slope floor (18 days)
    INSUFFICIENT_SNR    the k-sigma band contains both zero and the specification: more data, no verdict

Rules:
- The span floor is READ FROM the reconciler's configuration - the bench
  cannot be rushed past the product's standing rule.
- A reference series, when given, is subtracted first (the setpoint's own
  wander is not the gauge's drift); without one the setpoint is assumed held.
- The self-test is labelled SIMULATION_SELF_TEST in its own output: it checks
  the arithmetic on synthetic data and proves nothing about a physical gauge.
- EXCEEDS_DATASHEET is a first-class outcome, not an error.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .gauge_aging import aging_rate
from .gauge_specs import GaugeSpec, DEFAULT_SPEC
from .reconciler import ReconcilerConfig

YEAR_S = 365.25 * 86400.0


def fit_slope(times_s, p_psi, full_scale_psi: float) -> dict:
    """OLS drift slope of a series at constant setpoint: psi/yr and %FS/yr with the standard error from the residuals."""
    t = np.asarray(times_s, dtype=float) / YEAR_S
    p = np.asarray(p_psi, dtype=float)
    m = ~(np.isnan(t) | np.isnan(p))
    t, p = t[m], p[m]
    n = len(t)
    if n < 8:
        raise ValueError(f"a series needs >= 8 finite samples (got {n})")
    span_years = float(t.max() - t.min())
    A = np.vstack([t - t.mean(), np.ones(n)]).T
    coef, res, _, _ = np.linalg.lstsq(A, p, rcond=None)
    slope_psi_yr = float(coef[0])
    dof = max(n - 2, 1)
    sigma2 = float(res[0]) / dof if len(res) else float(np.var(p - A @ coef))
    se_slope = float(np.sqrt(sigma2 / np.sum((t - t.mean()) ** 2))) if np.sum((t - t.mean()) ** 2) > 0 else float('nan')
    return {"n": n, "span_years": round(span_years, 4), "slope_psi_yr": round(slope_psi_yr, 3), "se_slope_psi_yr": round(se_slope, 3),
            "drift_pct_fs_yr": round(slope_psi_yr / full_scale_psi * 100.0, 5), "se_drift_pct_fs_yr": round(se_slope / full_scale_psi * 100.0, 5),
            "rms_resid_psi": round(float(np.sqrt(sigma2)), 3)}


def bench_analysis(times_s, p_psi, spec: Optional[GaugeSpec] = None, ref_times_s=None, ref_p_psi=None,
                   k_sigma: float = 2.0, serial: str = '', certificate_id: str = '') -> dict:
    """The protocol's section 5: the gauge's drift against its datasheet specification."""
    spec = spec or DEFAULT_SPEC
    cfg = ReconcilerConfig()
    t = np.asarray(times_s, dtype=float)
    p = np.asarray(p_psi, dtype=float)
    ref_note = 'no reference series given: the setpoint is taken as held'
    if ref_times_s is not None and ref_p_psi is not None:
        rt, rp = np.asarray(ref_times_s, dtype=float), np.asarray(ref_p_psi, dtype=float)
        m = ~np.isnan(rp)
        p = p - np.interp(t, rt[m], rp[m])                        # the gauge against the standard, not against the clock
        ref_note = f'reference series subtracted ({int(m.sum())} points)'
    fit = fit_slope(t, p, spec.full_scale_psi)
    a = aging_rate(spec)
    out = {"protocol": "BENCH_TEST_PROTOCOL.md section 5", "gauge_spec": spec.name, "serial": serial, "certificate_id": certificate_id,
           "datasheet_rate_pct_fs_yr": a["rate_pct_fs_yr"], "datasheet_rate_psi_yr": round(a["rate_psi_yr"], 3),
           "datasheet_source": spec.source, "fit": fit, "reference": ref_note, "k_sigma": k_sigma,
           "measured_rate_pct_fs_yr": abs(fit["drift_pct_fs_yr"]), "measured_direction": ("up" if fit["slope_psi_yr"] >= 0 else "down")}
    min_span = float(cfg.min_trend_span_years)
    if fit["span_years"] < min_span:
        out["verdict"] = "INSUFFICIENT_SPAN"
        out["detail"] = (f"span {fit['span_years'] * 365.25:.0f} days is below the floor {min_span * 365.25:.0f} days "
                         "(the reconciler's own slope rule) - no verdict; the bench cannot be rushed")
        return out
    rate = abs(fit["drift_pct_fs_yr"])
    se = fit["se_drift_pct_fs_yr"]
    lo, hi = rate - k_sigma * se, rate + k_sigma * se
    spec_rate = a["rate_pct_fs_yr"]
    out["band_pct_fs_yr"] = [round(max(lo, 0.0), 5), round(hi, 5)]
    if hi <= spec_rate:
        out["verdict"] = "WITHIN_DATASHEET"
        out["detail"] = f"measured {rate:.4f} +/- {k_sigma:.0f}x{se:.4f} %FS/yr is inside the specification {spec_rate:g} %FS/yr"
    elif lo > spec_rate:
        out["verdict"] = "EXCEEDS_DATASHEET"
        out["detail"] = (f"measured {rate:.4f} +/- {k_sigma:.0f}x{se:.4f} %FS/yr is above the specification {spec_rate:g} %FS/yr - "
                         "a first-class outcome: this gauge, at these conditions, drifts faster than its datasheet")
    else:
        out["verdict"] = "INSUFFICIENT_SNR"
        out["detail"] = (f"the {k_sigma:.0f}-sigma band [{max(lo, 0):.4f}, {hi:.4f}] %FS/yr straddles the specification {spec_rate:g} - "
                         "more data (a longer span or a quieter setpoint) is needed for a verdict")
    return out


def bench_selftest(seed: int = 7) -> dict:
    """SIMULATION_SELF_TEST: synthetic series at the datasheet rate, at three times it, and too short - the arithmetic only."""
    rng = np.random.default_rng(seed)
    spec = DEFAULT_SPEC
    fs = spec.full_scale_psi
    rate_psi_yr = aging_rate(spec)["rate_psi_yr"]
    days = np.arange(0, 120, 1.0)
    t = days * 86400.0
    out = {"status": "SIMULATION_SELF_TEST", "note": "synthetic series; proves the arithmetic, not a gauge", "cases": {}}
    for name, mult, n_days in (("at_datasheet_rate", 1.0, 120), ("three_times_rate", 3.0, 120), ("too_short", 1.0, 10)):
        tt = t[:n_days]
        p = 10000.0 + mult * rate_psi_yr * tt / YEAR_S + rng.normal(0, 0.02, len(tt))
        out["cases"][name] = {k: v for k, v in bench_analysis(tt, p, spec).items() if k in ("verdict", "detail", "measured_rate_pct_fs_yr", "band_pct_fs_yr", "fit")}
    expect = {"at_datasheet_rate": ("WITHIN_DATASHEET", "INSUFFICIENT_SNR"), "three_times_rate": ("EXCEEDS_DATASHEET",), "too_short": ("INSUFFICIENT_SPAN",)}
    out["ok"] = all(out["cases"][k]["verdict"] in v for k, v in expect.items())
    return out
