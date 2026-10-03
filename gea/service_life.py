# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""service_life - how long until a gauge's accumulated drift reaches the error budget.

The datasheet gives an aging RATE (%FS/yr). This module integrates it over
years of service at every station of a described well, with optional
recalibration resets and a small random-walk component for realism (off by
default in tests), and reports the years to the error budget per station.
It is arithmetic on the datasheet rate, nothing more; a station whose tool
has no rate on record carries none.

Headless-safe: numpy only, no display imports.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from .downhole_engine import DownholeEngine, SimulatorConfig


@dataclass
class ServiceLifeConfig:
    years: float = 5.0                                # simulated service horizon
    dt_days: float = 7.0                              # accumulation step (one reading per week)
    full_scale_psi: float = 30000.0                   # overridden by the engine's datasheet when one is set
    error_budget_pct_fs: float = 0.5                  # a site's allowed total error, as the site sets it (a setting, not a vendor number)
    recalibration_interval_years: Optional[float] = None   # None = never recalibrated (permanent install)
    random_walk_pct_fs_per_sqrt_yr: float = 0.0       # stochastic component (0 = deterministic)
    seed: Optional[int] = None


class ServiceLifeSimulator:
    """Accumulates each station's datasheet aging rate over simulated service years."""

    def __init__(self, engine: DownholeEngine | None = None, config: ServiceLifeConfig | None = None):
        self.engine = engine or DownholeEngine(SimulatorConfig())
        self.cfg = config or ServiceLifeConfig()
        spec = getattr(self.engine.cfg, 'gauge_spec', None)
        from dataclasses import replace as _replace
        if spec is not None:
            self.cfg = _replace(self.cfg, full_scale_psi=float(spec.full_scale_psi))
        else:
            from .gauge_specs import DEFAULT_SPEC
            self.cfg = _replace(self.cfg, full_scale_psi=float(DEFAULT_SPEC.full_scale_psi))
        self.rate = np.array([self.engine.compute_drift(s, float(self.engine.base_T[i]), float(self.engine.base_P[i]))
                              for i, s in enumerate(self.engine.sensors)], dtype=float)          # %FS/yr per station; NaN = no rate on record
        self._reset_history()

    def _reset_history(self) -> None:
        n = len(self.engine.sensors)
        self.time_years: List[float] = [0.0]
        self.err: List[np.ndarray] = [np.zeros(n)]   # accumulated error, %FS
        self.recal_times: List[float] = []

    def run(self) -> "ServiceLifeSimulator":
        self._reset_history()
        n = len(self.engine.sensors)
        dt = self.cfg.dt_days / 365.25
        rng = np.random.default_rng(self.cfg.seed)
        rw = self.cfg.random_walk_pct_fs_per_sqrt_yr
        rate = np.where(np.isnan(self.rate), 0.0, self.rate)
        t = 0.0
        e = np.zeros(n)
        since_recal = 0.0
        while t < self.cfg.years - 1e-12:
            t += dt
            since_recal += dt
            e = e + rate * dt + (rng.normal(0.0, rw * np.sqrt(dt), n) if rw else 0.0)
            if self.cfg.recalibration_interval_years is not None and since_recal >= self.cfg.recalibration_interval_years - 1e-12:
                e = np.zeros(n)
                since_recal = 0.0
                self.recal_times.append(round(t, 6))
            self.time_years.append(round(t, 6))
            self.err.append(e.copy())
        return self

    def summary(self) -> dict:
        fs = self.cfg.full_scale_psi
        budget = self.cfg.error_budget_pct_fs
        with np.errstate(divide='ignore', invalid='ignore'):
            t_budget = np.where(self.rate > 0, budget / self.rate, np.inf)
        final = self.err[-1]
        return {
            'horizon_years': self.cfg.years,
            'full_scale_psi': fs,
            'rate_pct_fs_yr': [None if np.isnan(r) else round(float(r), 5) for r in self.rate],
            'rate_psi_yr': [None if np.isnan(r) else round(float(r) * fs / 100.0, 3) for r in self.rate],
            'final_error_psi': [round(float(v) * fs / 100.0, 3) for v in final],
            'error_budget_pct_fs': budget,
            'error_budget_psi': round(budget / 100.0 * fs, 2),
            'years_to_budget': [None if np.isnan(self.rate[i]) else (round(float(t), 2) if np.isfinite(t) else None) for i, t in enumerate(t_budget)],
            'stations_without_rate': int(np.isnan(self.rate).sum()),
            'recalibrations': self.recal_times,
            'basis': 'the datasheet aging rate integrated over service time; a setting (the error budget) decides the horizon',
        }

    divergence_summary = summary     # the older name

    def export_csv(self, path: str | None = None) -> Path:
        """Accumulated error (psi) per station, one row per accumulation step."""
        p = Path(path or "service_life.csv")
        fs = self.cfg.full_scale_psi
        names = [s.name for s in self.engine.sensors]
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_years"] + [f"err_psi_{nm}" for nm in names])
            for i, t in enumerate(self.time_years):
                w.writerow([round(t, 4)] + [round(float(self.err[i][j]) * fs / 100.0, 3) for j in range(len(names))])
        return p
