# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Bench-test analysis.

The analysis half of BENCH_TEST_PROTOCOL.md: fit each leg's drift slope,
propagate uncertainties into the conventional/GEA ratio, and return a
verdict from the protocol's vocabulary (MEASURED_CONFIRMS /
MEASURED_REFUTES / INSUFFICIENT_SPAN / INSUFFICIENT_SNR).

Honesty rules:
- The 18-day span floor is READ FROM the reconciler's own configuration -
  the bench cannot be rushed past the product's standing rule.
- The self-test is labeled SIMULATION_SELF_TEST in its own output: it
  verifies the analysis arithmetic against the engine's twin models, and
  proves NOTHING about physical gauges.
- A refutation is a first-class outcome, not an error.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .quartz_hpht_extension import (calculate_quartz_transducer_hpht_program,
                                         canonical_suppression,
                                         conventional_drift)
from .reconciler import ReconcilerConfig

YEAR_S = 365.25 * 86400.0


def _leg_slope(times_s: np.ndarray, p_psi: np.ndarray,
               full_scale_psi: float) -> dict:
    """OLS drift slope of one leg at constant setpoint: %FS/yr with the
    standard error of the slope propagated from the fit residuals."""
    t = np.asarray(times_s, dtype=float) / YEAR_S
    p = np.asarray(p_psi, dtype=float)
    m = ~(np.isnan(t) | np.isnan(p))
    t, p = t[m], p[m]
    n = len(t)
    if n < 8:
        raise ValueError(f"leg needs >= 8 finite samples (got {n})")
    span_years = float(t.max() - t.min())
    A = np.vstack([t - t.mean(), np.ones(n)]).T
    coef, res, _, _ = np.linalg.lstsq(A, p, rcond=None)
    slope_psi_yr = float(coef[0])
    dof = max(n - 2, 1)
    sigma2 = float(res[0]) / dof if len(res) else float(np.var(p - A @ coef))
    se_slope = float(np.sqrt(sigma2 / np.sum((t - t.mean()) ** 2)))
    return {"n": n, "span_years": round(span_years, 4),
            "slope_psi_yr": round(slope_psi_yr, 3),
            "se_slope_psi_yr": round(se_slope, 3),
            "drift_pct_fs_yr": round(slope_psi_yr / full_scale_psi * 100.0, 5),
            "se_drift_pct_fs_yr": round(se_slope / full_scale_psi * 100.0, 5)}


def bench_analysis(times_s, p_psi, conv_times_s, conv_p_psi,
                   full_scale_psi: float = 30000.0,
                   k_sigma: float = 2.0) -> dict:
    """The protocol section 5 analysis. Inputs are the two legs' raw
    pressure series at a constant setpoint; output carries the measured
    ratio, its propagated uncertainty, the prediction, and the verdict."""
    cfg = ReconcilerConfig()
    uq = _leg_slope(times_s, p_psi, full_scale_psi)
    cv = _leg_slope(conv_times_s, conv_p_psi, full_scale_psi)
    prediction = canonical_suppression()
    out = {"protocol": "BENCH_TEST_PROTOCOL.md section 5",
           "prediction_ratio": round(prediction, 4),
           "prediction_status": ("DERIVED_HYBRID composition - the quantity "
                                 "this bench exists to confirm or refute"),
           "leg": uq, "conventional_leg": cv,
           "full_scale_psi": full_scale_psi}
    min_span = float(cfg.min_trend_span_years)
    if uq["span_years"] < min_span or cv["span_years"] < min_span:
        out["verdict"] = "INSUFFICIENT_SPAN"
        out["detail"] = (f"span floor {min_span:g} yr "
                         f"(the reconciler's own >=18-day slope rule) not met "
                         f"- no verdict; the bench cannot be rushed")
        return out
    if uq["slope_psi_yr"] <= 0:
        out["verdict"] = "INSUFFICIENT_SNR"
        out["detail"] = ("program-model-leg slope is non-positive - a drift ratio has "
                         "no meaning here; check setpoint stability")
        return out
    r = cv["slope_psi_yr"] / uq["slope_psi_yr"]
    se_r = abs(r) * float(np.sqrt(
        (cv["se_slope_psi_yr"] / cv["slope_psi_yr"]) ** 2
        + (uq["se_slope_psi_yr"] / uq["slope_psi_yr"]) ** 2))
    out["measured_ratio"] = round(r, 4)
    out["se_ratio"] = round(se_r, 4)
    lo, hi = r - k_sigma * se_r, r + k_sigma * se_r
    contains_pred = lo <= prediction <= hi
    contains_unity = lo <= 1.0 <= hi
    if contains_pred and contains_unity:
        out["verdict"] = "INSUFFICIENT_SNR"
        out["detail"] = (f"the {k_sigma:.0f}-sigma band [{lo:.4f}, {hi:.4f}] "
                         "contains BOTH the prediction and 1.0 - suppression "
                         "cannot be distinguished from no-suppression; more "
                         "data required, no verdict")
    elif contains_pred:
        out["verdict"] = "MEASURED_CONFIRMS"
        out["detail"] = (f"measured R = {r:.4f} +/- {se_r:.4f} contains the "
                         f"predicted {prediction:.4f} and excludes 1.0 - on "
                         "REAL bench data this outcome would support "
                         "relabeling per protocol section 6")
    else:
        out["verdict"] = "MEASURED_REFUTES"
        out["detail"] = (f"measured R = {r:.4f} +/- {se_r:.4f} excludes the "
                         f"predicted {prediction:.4f} - a first-class "
                         "outcome: the composition is falsified at these "
                         "conditions; the label stays DERIVED_HYBRID with "
                         "this refutation on record (protocol section 6)")
    return out


def bench_selftest(days: int = 120, setpoint_psi: float = 10000.0,
                   setpoint_temp_C: float = 150.0,
                   noise_psi: float = 0.5, seed: int = 8,
                   conv_scale: float = 1.0) -> dict:
    """SIMULATION_SELF_TEST: synthesize both legs from the engine's own
    drift models at the protocol's setpoint class and run the analysis.
    Verifies the ARITHMETIC, proves nothing about gauges - and says so in
    its own output. conv_scale != 1 exercises the refutation path."""
    r_uq = float(calculate_quartz_transducer_hpht_program(
        depth_m=3000.0, temp_c=setpoint_temp_C,
        pressure_psi=setpoint_psi)["value"]["drift_pct"])
    r_cv = conventional_drift(setpoint_temp_C, setpoint_psi) * conv_scale
    fs = 30000.0
    rng = np.random.default_rng(seed)
    t = np.arange(days) * 86400.0
    ty = t / YEAR_S
    p_uq = setpoint_psi + r_uq / 100.0 * fs * ty + rng.normal(0, noise_psi, days)
    p_cv = setpoint_psi + r_cv / 100.0 * fs * ty + rng.normal(0, noise_psi, days)
    out = bench_analysis(t, p_uq, t, p_cv, full_scale_psi=fs)
    out["mode"] = ("SIMULATION_SELF_TEST: both legs synthesized from the "
                   "engine's own models - this verifies the analysis "
                   "arithmetic and the bench pipeline, NOT the physics; "
                   "no gauge was measured")
    out["synth_inputs"] = {"days": days, "setpoint_psi": setpoint_psi,
                           "setpoint_temp_C": setpoint_temp_C,
                           "noise_psi": noise_psi, "seed": seed,
                           "conv_scale": conv_scale}
    return out
