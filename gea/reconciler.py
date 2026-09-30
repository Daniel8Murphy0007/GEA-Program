# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""reconciler — the two-stream reconciler.

Piece 3 of the two-stream build — the coordinator itself. The CLOSED STREAM
(the simulator's aging models on their locked constants) predicts what every gauge
in a described well SHOULD read; the LIVE STREAM (a `LiveStream` from the
ports layer) delivers what it DOES read. The reconciler aligns the two on the
shared toolstring and works the residual series per station:

    offset(t) = measured(t) - predicted_baseline

then classifies each station's offset:

  * IN_FAMILY            — within the gauge's own noise; streams agree.
  * CALIBRATION_OFFSET   — constant bias beyond noise but within instrument
                           scale; recovered magnitude reported.
  * DRIFT_CONSISTENT     — a trend whose slope sits inside the closed
                           stream's drift envelope [rate, conv_rate]
                           at that station — instrument aging the model
                           already predicts (needs a long enough window).
  * TRANSIENTS           — clustered short excursions (well events; real
                           signal, not an instrument fault).
  * UNEXPLAINED_OFFSET / UNEXPLAINED_TREND — structure the closed stream
                           cannot account for. THE FIND: the offset
                           undervalued data stream (a kick zone the assumed
                           gradient model cannot see lands here).

Disclosure: classification thresholds are DISCLOSED engineering
heuristics, not derivations — bias test 4-sigma-of-mean, transient test
6-sigma, model-mismatch magnitude 500 psi, drift-envelope margin 2x, minimum
trend window 18 days. Labels are advisory triage for a human; the numbers
(bias, slope, sigma, transient count) are always reported alongside. Drift
classification is only attempted when the data span supports it — a slope
measured over hours is noise, and the reconciler says so rather than
classifying on it.

Headless-safe: numpy only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .downhole_engine import DownholeEngine, SimulatorConfig
from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    conventional_drift,
)
from .ports import LiveStream

YEAR_S = 365.25 * 24 * 3600.0


@dataclass
class ReconcilerConfig:
    bias_n_sigma: float = 4.0            # disclosed heuristic: bias significance on the mean
    transient_n_sigma: float = 6.0       # disclosed heuristic: single-sample excursion gate
    transient_frac: float = 0.01         # disclosed heuristic: >1% excursions = transient-rich
    model_mismatch_psi: float = 500.0    # disclosed heuristic: bias too large for calibration
    drift_envelope_margin: float = 2.0   # disclosed heuristic: slope within margin x conv rate
    min_trend_span_years: float = 0.05   # ~18 days: below this, slopes are noise - not classified
    full_scale_psi: float = 30000.0      # anchor: HPHT quartz FS class (spec overrides)
    use_clean_channels: bool = False     # default: reconcile the RAW leg


def auto_station_map(stream: LiveStream, well: SimulatorConfig) -> Dict[str, float]:
    """Map the stream's pressure channels to station MDs by the S<i> naming
    used by both the engine export (P_S1_2600ft) and the telemetry export
    (P_raw_psi_S1). Explicit maps override; this is the convenience path."""
    eng = DownholeEngine(well)
    out: Dict[str, float] = {}
    for name in stream.channels:
        if not name.startswith('P') or 'clean' in name:
            continue     # raw pressure channels only (the honest leg)
        m = re.search(r'S(\d+)', name)
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < len(eng.sensors):
                out[name] = float(eng.sensors[i].depth_ft)
    return out


class Reconciler:
    """Coordinates one described well (closed stream) against live data."""

    def __init__(self, well: SimulatorConfig, config: ReconcilerConfig | None = None):
        self.well = well
        self.cfg = config or ReconcilerConfig()
        self.engine = DownholeEngine(well)   # closed-stream baseline machinery
        spec = getattr(well, 'gauge_spec', None)
        self.full_scale_psi = float(spec.full_scale_psi) if spec is not None else self.cfg.full_scale_psi

    # -- closed-stream prediction at an MD ------------------------------------
    def predicted_baseline(self, md_ft: float) -> tuple:
        tvd = self.engine._physics_depth_ft(md_ft)
        if self.well.profile is not None:
            p, tF = self.well.profile.interp(tvd)
        else:
            p = self.well.surface_pressure_psi + tvd * self.well.pressure_gradient_psi_per_ft
            tF = self.well.surface_temp_F + tvd * self.well.temp_gradient_F_per_ft
        return float(p), float(tF)

    def drift_envelope_psi_yr(self, md_ft: float) -> tuple:
        """The closed stream's own aging prediction at this station:
        [program-model-leg rate, conventional-leg rate] in psi/yr at station T/P."""
        p, tF = self.predicted_baseline(md_ft)
        tC = (tF - 32.0) * 5.0 / 9.0
        spec = getattr(self.well, 'gauge_spec', None)
        uq = calculate_quartz_transducer_hpht_program(0.0, tC, p, spec=spec)['value']['drift_pct']
        cv = conventional_drift(tC, p, spec=spec)
        f = self.full_scale_psi / 100.0
        return float(uq) * f, float(cv) * f

    # -- the reconciliation ----------------------------------------------------
    def _classify(self, resid: np.ndarray, t_years: np.ndarray, md_ft: float) -> dict:
        c = self.cfg
        v = ~np.isnan(resid)
        n = int(np.sum(v))
        if n < 8:
            return {'classification': 'INSUFFICIENT_DATA', 'n': n}
        r, t = resid[v], t_years[v]
        span = float(t.max() - t.min())
        slope, intercept = np.polyfit(t, r, 1)     # psi per year
        detr = r - (intercept + slope * t)
        sigma = float(1.4826 * np.median(np.abs(detr - np.median(detr)))) or float(np.std(detr)) or 1e-9
        bias = float(np.mean(r))
        transients = int(np.sum(np.abs(detr) > c.transient_n_sigma * sigma))
        uq_env, cv_env = self.drift_envelope_psi_yr(md_ft)
        bias_gate = c.bias_n_sigma * sigma / np.sqrt(n) + 1.0   # +1 psi absolute floor (disclosed)
        trend_usable = span >= c.min_trend_span_years

        if trend_usable and abs(slope) > c.drift_envelope_margin * cv_env:
            cls = 'UNEXPLAINED_TREND'
        elif trend_usable and 0.5 * uq_env <= abs(slope) <= c.drift_envelope_margin * cv_env \
                and abs(slope) * span > bias_gate:
            cls = 'DRIFT_CONSISTENT'
        elif abs(bias) > max(bias_gate, c.model_mismatch_psi):
            cls = 'UNEXPLAINED_OFFSET'
        elif abs(bias) > bias_gate:
            cls = 'CALIBRATION_OFFSET'
        elif transients / n > c.transient_frac:
            cls = 'TRANSIENTS'
        else:
            cls = 'IN_FAMILY'
        return {
            'classification': cls,
            'n': n,
            'span_years': round(span, 4),
            'trend_usable': bool(trend_usable),
            'bias_psi': round(bias, 2),
            'slope_psi_yr': round(float(slope), 2) if trend_usable else None,
            'noise_sigma_psi': round(sigma, 2),
            'transient_count': transients,
            'drift_envelope_psi_yr': [round(uq_env, 2), round(cv_env, 2)],
        }

    def reconcile(self, stream: LiveStream,
                  station_map: Optional[Dict[str, float]] = None) -> dict:
        """The two-stream coordination: per-station offset statistics +
        classification + the undervalued-streams list."""
        if stream.index_kind != 'time_s':
            raise ValueError("reconcile() needs a time-indexed stream (historian side); "
                             "depth-indexed logs reconcile against the profile, not the clock")
        if station_map is None:
            station_map = auto_station_map(stream, self.well)
        if not station_map:
            raise ValueError("no pressure channels mapped to stations - pass station_map explicitly")
        t_years = stream.index / YEAR_S
        stations: List[dict] = []
        for chan, md in sorted(station_map.items(), key=lambda kv: kv[1]):
            pred_p, pred_tF = self.predicted_baseline(md)
            resid = stream.channel(chan).values - pred_p
            rep = self._classify(resid, t_years, md)
            rep.update({'channel': chan, 'md_ft': round(float(md), 0),
                        'predicted_baseline_psi': round(pred_p, 0)})
            stations.append(rep)
        undervalued = [s for s in stations
                       if s['classification'].startswith('UNEXPLAINED')]
        counts: Dict[str, int] = {}
        for s in stations:
            counts[s['classification']] = counts.get(s['classification'], 0) + 1
        return {
            'well': {'td_ft': self.well.td_ft,
                     'profile': self.well.profile.name if self.well.profile else 'linear gradients',
                     'deviation': self.well.deviation.name if getattr(self.well, 'deviation', None) else 'vertical',
                     'gauge_spec': getattr(getattr(self.well, 'gauge_spec', None), 'name', None) or 'template_generic (default)'},
            'stream': stream.name,
            'stations': stations,
            'classification_counts': counts,
            'undervalued_streams': [{'channel': s['channel'], 'md_ft': s['md_ft'],
                                     'classification': s['classification'],
                                     'bias_psi': s['bias_psi'], 'slope_psi_yr': s['slope_psi_yr']}
                                    for s in undervalued],
            'thresholds_disclosed': {
                'bias_n_sigma': self.cfg.bias_n_sigma, 'transient_n_sigma': self.cfg.transient_n_sigma,
                'model_mismatch_psi': self.cfg.model_mismatch_psi,
                'drift_envelope_margin': self.cfg.drift_envelope_margin,
                'min_trend_span_years': self.cfg.min_trend_span_years,
                'note': 'engineering heuristics, disclosed - labels are advisory triage; the numbers are the record'},
        }
