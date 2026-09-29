"""service_life — long-horizon drift accumulation (v1.2.0 extension).

The v1.0/1.1 layers report drift as an instantaneous RATE (%FS/yr). This module
integrates that rate over months/years of simulated service, twin-leg at every
station (GEA-stabilized vs conventional), producing the DIVERGENCE CURVES a
real bench test or field trial would record: two error traces separating at
exactly the rate the canonical suppression predicts. Adds optional periodic
recalibration resets (workover/recal events) and a small random-walk component
for realism (defaults chosen so the deterministic rate dominates).

This is the full simulation instrument for the PAPER_2250 LABORATORY-tier
quartz bench test (PAPER_2256 sec 5): the separation you'd measure, not just
the ratio you'd predict.

Headless-safe: numpy only, no display imports.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from .downhole_engine import DownholeEngine, SimulatorConfig
from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    canonical_suppression,
    conventional_drift,
)


@dataclass
class ServiceLifeConfig:
    years: float = 5.0                                # simulated service horizon
    dt_days: float = 7.0                              # accumulation step (one reading per week class)
    full_scale_psi: float = 30000.0                   # anchor: HPHT quartz-gauge full-scale class (industry)
    error_budget_pct_fs: float = 0.5                  # anchor: typical permanent-gauge total-error spec budget
    recalibration_interval_years: Optional[float] = None   # None = never recalibrated (permanent install)
    random_walk_pct_fs_per_sqrt_yr: float = 0.002     # small stochastic component (realism; 0 = deterministic)
    seed: Optional[int] = None                        # rng seed (deterministic runs for tests)


class ServiceLifeSimulator:
    """Accumulates twin-leg gauge error over simulated service years.

    Rates are evaluated once per sensor at the engine's base station T/P
    (permanent-install conditions); both legs share the same industry baseline
    and stress dressing, differing ONLY by the canonical suppression — so the
    accumulated-error ratio converges to the suppression composition and the
    separation grows linearly at the predicted rate.
    """

    def __init__(self, engine: DownholeEngine | None = None,
                 config: ServiceLifeConfig | None = None):
        self.engine = engine or DownholeEngine(SimulatorConfig())
        self.cfg = config or ServiceLifeConfig()
        spec = getattr(self.engine.cfg, 'gauge_spec', None)   # v1.5.0: engine's datasheet spec
        if spec is not None:
            from dataclasses import replace as _replace
            self.cfg = _replace(self.cfg, full_scale_psi=float(spec.full_scale_psi))
        n = len(self.engine.sensors)
        # Per-sensor rates (%FS/yr) at base station conditions
        self.rate = np.zeros(n)
        self.conv_rate = np.zeros(n)
        for i, s in enumerate(self.engine.sensors):
            temp_C = (float(self.engine.base_T[i]) - 32.0) * 5.0 / 9.0
            p_psi = float(self.engine.base_P[i])
            r = calculate_quartz_transducer_hpht_program(
                depth_m=s.depth_ft / 3.28084, temp_c=temp_C, pressure_psi=p_psi,
                k_structural_trim=s.k_structural_trim,
                phi_coupling_trim=s.phi_coupling_trim,
                spec=spec)
            self.rate[i] = r["value"]["drift_pct"]
            self.conv_rate[i] = conventional_drift(temp_C, p_psi, spec=spec)
        self._reset_history()

    def _reset_history(self) -> None:
        n = len(self.engine.sensors)
        self.time_years: List[float] = [0.0]
        self.err: List[np.ndarray] = [np.zeros(n)]   # accumulated error, %FS
        self.conv_err: List[np.ndarray] = [np.zeros(n)]
        self.recal_times: List[float] = []

    # -- run ------------------------------------------------------------------
    def run(self) -> "ServiceLifeSimulator":
        """Integrate accumulated error over the configured horizon."""
        self._reset_history()
        n = len(self.engine.sensors)
        dt = self.cfg.dt_days / 365.25
        rng = np.random.default_rng(self.cfg.seed)
        rw = self.cfg.random_walk_pct_fs_per_sqrt_yr
        t = 0.0
        uq = np.zeros(n)
        cv = np.zeros(n)
        since_recal = 0.0
        while t < self.cfg.years - 1e-12:
            t += dt
            since_recal += dt
            uq = uq + self.rate * dt + rng.normal(0.0, rw * np.sqrt(dt), n)
            cv = cv + self.conv_rate * dt + rng.normal(0.0, rw * np.sqrt(dt), n)
            if (self.cfg.recalibration_interval_years is not None
                    and since_recal >= self.cfg.recalibration_interval_years - 1e-12):
                uq = np.zeros(n)                    # recal zeroes both legs
                cv = np.zeros(n)
                since_recal = 0.0
                self.recal_times.append(round(t, 6))
            self.time_years.append(round(t, 6))
            self.err.append(uq.copy())
            self.conv_err.append(cv.copy())
        return self

    # -- reporting --------------------------------------------------------------
    def divergence_summary(self) -> dict:
        """The bench-test report: predicted vs measured separation, ratio check,
        and the service-life arithmetic (time to error budget, extra years)."""
        fs = self.cfg.full_scale_psi
        budget = self.cfg.error_budget_pct_fs
        sep_rate = self.conv_rate - self.rate                 # %FS/yr
        uq_final = self.err[-1]
        cv_final = self.conv_err[-1]
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio_final = np.where(uq_final > 0, cv_final / uq_final, np.nan)
        t_budget_program = budget / self.rate                    # yr to spec budget
        t_budget_conv = budget / self.conv_rate
        return {
            'horizon_years': self.cfg.years,
            'full_scale_psi': fs,
            'rate_pct_fs_yr': [round(float(r), 4) for r in self.rate],
            'conv_rate_pct_fs_yr': [round(float(r), 4) for r in self.conv_rate],
            'predicted_separation_rate_pct_fs_yr': [round(float(r), 5) for r in sep_rate],
            'predicted_separation_rate_psi_yr': [round(float(r) * fs / 100.0, 2) for r in sep_rate],
            'final_separation_psi': [round(float(cv_final[i] - uq_final[i]) * fs / 100.0, 2)
                                     for i in range(len(sep_rate))],
            'measured_ratio_final': [round(float(r), 4) for r in ratio_final],
            'predicted_ratio_suppression': round(canonical_suppression(
                self.engine.cfg.global_k_structural_trim,
                self.engine.cfg.global_phi_coupling_trim), 4),
            'error_budget_pct_fs': budget,
            'years_to_budget_program': [round(float(t), 2) for t in t_budget_program],
            'years_to_budget_conventional': [round(float(t), 2) for t in t_budget_conv],
            'extra_service_life_years': [round(float(t_budget_program[i] - t_budget_conv[i]), 2)
                                         for i in range(len(sep_rate))],
            'recalibrations': self.recal_times,
        }

    def export_csv(self, path: str | None = None) -> Path:
        """Divergence curves: per-sensor accumulated error (psi) for both legs
        plus the separation, one row per accumulation step."""
        if path is None:
            path = "service_life.csv"
        p = Path(path)
        fs = self.cfg.full_scale_psi
        names = [s.name for s in self.engine.sensors]
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            header = ["time_years"]
            for nm in names:
                header += [f"err_psi_{nm}", f"conv_err_psi_{nm}", f"separation_psi_{nm}"]
            w.writerow(header)
            for i, t in enumerate(self.time_years):
                row = [round(t, 4)]
                for j in range(len(names)):
                    uq = float(self.err[i][j]) * fs / 100.0
                    cv = float(self.conv_err[i][j]) * fs / 100.0
                    row += [round(uq, 3), round(cv, 3), round(cv - uq, 3)]
                w.writerow(row)
        return p
