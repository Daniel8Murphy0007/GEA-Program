# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""telemetry — field-telemetry realism layer.

The engine produces clean physics samples at an arbitrary internal step. Real
permanent-gauge telemetry does not look like that: it arrives at a fixed
cadence (one reading per minute is the permanent-quartz-gauge class), and it
carries faults — telemetry-line burst dropouts (whole string goes dark),
stuck gauges (electronics freeze, both channels repeat the last value), and
single-sample spikes (electrical noise on the line). Historians tag every
sample with a quality flag, and analysis pipelines are judged by how well
they reject the garbage without touching the physics.

This module wraps the engine in exactly that: timestamped fixed-cadence
sampling, a fault injector with per-mode ground-truth masks, quality flags on
every sample, a Hampel spike-rejection pass producing cleaned channels, and —
because the injector KNOWS what it injected — a precision/recall score for
the rejection filter. Exported CSVs look like real field data, so the module
doubles as a test bench for downhole analysis pipelines.

Headless-safe: numpy only, no display imports. Deterministic under a seed.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .downhole_engine import DownholeEngine, SimulatorConfig


@dataclass
class TelemetryConfig:
    sample_interval_s: float = 60.0                 # anchor: 1 sample/min permanent-gauge telemetry class
    duration_hours: float = 24.0
    start_time: str = "2026-01-01T00:00:00"         # timestamp origin for the field-style export
    line_dropout_start_prob: float = 0.001          # per sample: telemetry-line burst dropout begins (string-wide)
    line_dropout_len_samples: Tuple[int, int] = (1, 20)
    gauge_stuck_start_prob: float = 0.0008          # per gauge per sample: electronics freeze begins
    gauge_stuck_len_samples: Tuple[int, int] = (10, 120)
    spike_prob: float = 0.002                       # per channel per sample: single-sample outlier
    spike_sigma_psi: float = 250.0                  # spike excursion scale, pressure channel
    spike_sigma_F: float = 25.0                     # spike excursion scale, temperature channel
    hampel_window: int = 21                         # rejection filter: rolling window (odd; ~20 min context at 1/min)
    hampel_n_sigma: float = 5.0                     # rejection filter: MAD threshold
    common_mode_veto: bool = True                   # unflag multi-gauge excursions (well events, not gauge faults)
    stuck_min_run: int = 3                          # frozen-value QC: identical consecutive samples = stuck
    seed: Optional[int] = None


class TelemetryRecorder:
    """Records engine output as faulted, flagged, fixed-cadence field telemetry.

    Quality flags per gauge per sample: OK, MISSING (line dropout), STUCK
    (frozen electronics), SPIKE (outlier on P and/or T). Ground-truth fault
    masks are kept so the rejection filter can be scored honestly.
    """

    def __init__(self, engine: DownholeEngine | None = None,
                 config: TelemetryConfig | None = None):
        self.engine = engine or DownholeEngine(SimulatorConfig())
        self.cfg = config or TelemetryConfig()

    # -- acquisition ----------------------------------------------------------
    def run(self) -> "TelemetryRecorder":
        cfg = self.cfg
        rng = np.random.default_rng(cfg.seed)
        if cfg.seed is not None:
            # the engine's step() draws from the global RNGs (template-faithful);
            # seed them too so a seeded telemetry run is fully reproducible
            import random as _random
            np.random.seed(cfg.seed)
            _random.seed(cfg.seed)
        n_g = len(self.engine.sensors)
        n_s = int(cfg.duration_hours * 3600.0 / cfg.sample_interval_s)

        self.time_s = np.arange(n_s) * cfg.sample_interval_s
        self.P_raw = np.full((n_s, n_g), np.nan)
        self.T_raw = np.full((n_s, n_g), np.nan)
        self.flags: List[List[str]] = []
        # ground-truth fault masks
        self.mask_missing = np.zeros((n_s, n_g), dtype=bool)
        self.mask_stuck = np.zeros((n_s, n_g), dtype=bool)
        self.mask_spike_P = np.zeros((n_s, n_g), dtype=bool)
        self.mask_spike_T = np.zeros((n_s, n_g), dtype=bool)

        line_drop_left = 0
        stuck_left = np.zeros(n_g, dtype=int)
        stuck_P = np.zeros(n_g)
        stuck_T = np.zeros(n_g)

        for k in range(n_s):
            self.engine.step(dt=cfg.sample_interval_s)
            P = self.engine.P.copy()
            T = self.engine.T.copy()
            row_flags = ["OK"] * n_g

            # telemetry-line burst dropout (string-wide)
            if line_drop_left == 0 and rng.random() < cfg.line_dropout_start_prob:
                line_drop_left = int(rng.integers(cfg.line_dropout_len_samples[0],
                                                  cfg.line_dropout_len_samples[1] + 1))
            if line_drop_left > 0:
                line_drop_left -= 1
                self.mask_missing[k, :] = True
                self.flags.append(["MISSING"] * n_g)
                continue    # nothing recorded this sample

            for g in range(n_g):
                # stuck electronics (per gauge, freezes both channels)
                if stuck_left[g] == 0 and rng.random() < cfg.gauge_stuck_start_prob:
                    stuck_left[g] = int(rng.integers(cfg.gauge_stuck_len_samples[0],
                                                     cfg.gauge_stuck_len_samples[1] + 1))
                    stuck_P[g], stuck_T[g] = P[g], T[g]
                if stuck_left[g] > 0:
                    stuck_left[g] -= 1
                    P[g], T[g] = stuck_P[g], stuck_T[g]
                    self.mask_stuck[k, g] = True
                    row_flags[g] = "STUCK"
                    continue    # a frozen gauge doesn't also spike
                # single-sample spikes (per channel)
                sp = rng.random() < cfg.spike_prob
                st = rng.random() < cfg.spike_prob
                if sp:
                    P[g] += rng.normal(0.0, cfg.spike_sigma_psi)
                    self.mask_spike_P[k, g] = True
                if st:
                    T[g] += rng.normal(0.0, cfg.spike_sigma_F)
                    self.mask_spike_T[k, g] = True
                if sp or st:
                    row_flags[g] = "SPIKE"

            self.P_raw[k, :] = P
            self.T_raw[k, :] = T
            self.flags.append(row_flags)

        self._despike()
        return self

    # -- rejection filter (Hampel) --------------------------------------------
    @staticmethod
    def _hampel(series: np.ndarray, window: int, n_sigma: float):
        """Rolling-median MAD despike. Returns (cleaned, detected_mask,
        rolling_medians). NaNs (missing samples) pass through untouched and
        undetected."""
        x = series.copy()
        n = len(x)
        half = window // 2
        detected = np.zeros(n, dtype=bool)
        meds = series.copy()
        for i in range(n):
            if np.isnan(x[i]):
                continue
            lo, hi = max(0, i - half), min(n, i + half + 1)
            w = series[lo:hi]
            w = w[~np.isnan(w)]
            if len(w) < 3:
                continue
            med = np.median(w)
            meds[i] = med
            mad = np.median(np.abs(w - med))
            sigma = 1.4826 * mad
            if sigma > 0 and abs(x[i] - med) > n_sigma * sigma:
                detected[i] = True
                x[i] = med
        return x, detected, meds

    @staticmethod
    def _frozen_runs(series: np.ndarray, min_run: int) -> np.ndarray:
        """Frozen-value QC (standard historian check): runs of identical
        consecutive samples of length >= min_run are a stuck gauge — live
        noise never repeats a float exactly."""
        n = len(series)
        detected = np.zeros(n, dtype=bool)
        i = 0
        while i < n - 1:
            if not np.isnan(series[i]) and series[i + 1] == series[i]:
                j = i
                while j + 1 < n and series[j + 1] == series[j]:
                    j += 1
                if j - i + 1 >= min_run:
                    detected[i:j + 1] = True
                i = j + 1
            else:
                i += 1
        return detected

    def _despike(self) -> None:
        n_s, n_g = self.P_raw.shape
        self.P_clean = np.empty_like(self.P_raw)
        self.T_clean = np.empty_like(self.T_raw)
        self.detected_P = np.zeros((n_s, n_g), dtype=bool)
        self.detected_T = np.zeros((n_s, n_g), dtype=bool)
        # frozen-value detection first (gauge-level: P and T freeze together)
        self.detected_stuck = np.zeros((n_s, n_g), dtype=bool)
        for g in range(n_g):
            self.detected_stuck[:, g] = (
                self._frozen_runs(self.P_raw[:, g], self.cfg.stuck_min_run)
                | self._frozen_runs(self.T_raw[:, g], self.cfg.stuck_min_run))
        med_P = np.empty_like(self.P_raw)
        med_T = np.empty_like(self.T_raw)
        for g in range(n_g):
            # stuck samples are excluded from the filter statistics (their
            # zero-variance runs crush the MAD and flood the boundary with
            # false spikes) and are never themselves despiked
            p_stat = self.P_raw[:, g].copy()
            t_stat = self.T_raw[:, g].copy()
            p_stat[self.detected_stuck[:, g]] = np.nan
            t_stat[self.detected_stuck[:, g]] = np.nan
            self.P_clean[:, g], self.detected_P[:, g], med_P[:, g] = self._hampel(
                p_stat, self.cfg.hampel_window, self.cfg.hampel_n_sigma)
            self.T_clean[:, g], self.detected_T[:, g], med_T[:, g] = self._hampel(
                t_stat, self.cfg.hampel_window, self.cfg.hampel_n_sigma)
            st = self.detected_stuck[:, g]
            self.P_clean[st, g] = self.P_raw[st, g]
            self.T_clean[st, g] = self.T_raw[st, g]
        if self.cfg.common_mode_veto and n_g >= 3:
            # A gauge fault hits ONE gauge; a well transient hits the STRING.
            # Two vetoes: (a) coincidence — independent faults essentially
            # never hit two gauges on the same sample; (b) residual — at a
            # flagged sample, if the OTHER gauges' median residual moved the
            # same direction by a comparable amount, the excursion is physics
            # (a well transient seen string-wide), not electronics.
            for det, raw, clean, med in ((self.detected_P, self.P_raw, self.P_clean, med_P),
                                         (self.detected_T, self.T_raw, self.T_clean, med_T)):
                veto = np.sum(det, axis=1) >= 2
                for k in np.where(np.any(det, axis=1) & ~veto)[0]:
                    g = int(np.argmax(det[k, :]))
                    r = raw[k, :] - med[k, :]
                    others = np.delete(r, g)
                    others = others[~np.isnan(others)]
                    if len(others) >= 2:
                        m = float(np.median(others))
                        if m * r[g] > 0 and abs(m) >= 0.3 * abs(r[g]):
                            veto[k] = True
                for k in np.where(veto)[0]:
                    clean[k, :] = raw[k, :]
                    det[k, :] = False

    # -- reporting -------------------------------------------------------------
    @staticmethod
    def _score(detected: np.ndarray, injected: np.ndarray) -> dict:
        tp = int(np.sum(detected & injected))
        fp = int(np.sum(detected & ~injected))
        fn = int(np.sum(~detected & injected))
        return {
            'injected': int(np.sum(injected)),
            'detected': int(np.sum(detected)),
            'true_positives': tp,
            'false_positives': fp,
            'precision': round(tp / (tp + fp), 3) if (tp + fp) else None,
            'recall': round(tp / (tp + fn), 3) if (tp + fn) else None,
        }

    def telemetry_summary(self) -> dict:
        n_s, n_g = self.P_raw.shape
        total = n_s * n_g
        n_missing = int(np.sum(self.mask_missing))
        return {
            'samples': n_s,
            'gauges': n_g,
            'sample_interval_s': self.cfg.sample_interval_s,
            'duration_hours': self.cfg.duration_hours,
            'uptime_pct': round(100.0 * (1.0 - n_missing / total), 2),
            'missing_samples': n_missing,
            'stuck_samples': int(np.sum(self.mask_stuck)),
            'stuck_score': self._score(self.detected_stuck, self.mask_stuck),
            'spike_score_P': self._score(self.detected_P, self.mask_spike_P),
            'spike_score_T': self._score(self.detected_T, self.mask_spike_T),
        }

    # -- export (field-style) --------------------------------------------------
    def export_csv(self, path: str | None = None) -> Path:
        """Field-historian-style CSV: ISO timestamp, then per gauge the raw
        P/T, the quality flag, and the cleaned P/T. Missing samples are blank
        with flag MISSING — exactly what a real export hands an analyst."""
        if path is None:
            path = "telemetry.csv"
        p = Path(path)
        t0 = datetime.fromisoformat(self.cfg.start_time)
        names = [s.name for s in self.engine.sensors]
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            header = ["timestamp"]
            for nm in names:
                header += [f"P_raw_psi_{nm}", f"T_raw_F_{nm}", f"flag_{nm}",
                           f"P_clean_psi_{nm}", f"T_clean_F_{nm}"]
            w.writerow(header)
            for k in range(len(self.time_s)):
                row = [(t0 + timedelta(seconds=float(self.time_s[k]))).isoformat()]
                for g in range(len(names)):
                    if self.mask_missing[k, g]:
                        row += ["", "", "MISSING", "", ""]
                    else:
                        row += [round(float(self.P_raw[k, g]), 2),
                                round(float(self.T_raw[k, g]), 2),
                                self.flags[k][g],
                                round(float(self.P_clean[k, g]), 2),
                                round(float(self.T_clean[k, g]), 2)]
                w.writerow(row)
        return p
