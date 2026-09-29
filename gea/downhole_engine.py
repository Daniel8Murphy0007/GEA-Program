"""downhole_engine — the simulation engine of the GEA Downhole Simulator.

Faithful port of the 22Aug2026 template's converged design (imperial units:
ft / degF / psi; six quartz gauges; noise + transient events; rolling history;
CSV export), adjusted to the gea-program repo: drift comes from the
canonical-primitive physics layer, and the template's per-sensor "K_MEX"/
"Phi_res" fields are the renamed engineering trims (knob ruling, 2026-08-22).

Headless-safe by design: no matplotlib/Qt imports here — the engine runs and
exports under the fidelity gate with no display.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np

from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    canonical_suppression,
    conventional_drift,
    PROGRAM_MODEL_AVAILABLE,
)

# Well geometry defaults (port of the template's 6,200 m TD well to the
# converged imperial layout: TD ~20,300 ft, six gauges spanning the string).
DEFAULT_TD_FT = 20300.0
DEFAULT_SENSOR_DEPTHS_FT = [2600.0, 6200.0, 9800.0, 13400.0, 17000.0, 20000.0]


@dataclass
class WellProfile:
    """A real well profile (survey/log): depth vs pressure and temperature.

    Load with `load_well_profile_csv`; when attached to SimulatorConfig, the
    engine interpolates base P/T from the profile instead of linear gradients.
    """
    depths_ft: List[float]
    pressures_psi: List[float]
    temps_F: List[float]
    name: str = "profile"

    def interp(self, depth_ft: float) -> tuple:
        p = float(np.interp(depth_ft, self.depths_ft, self.pressures_psi))
        tF = float(np.interp(depth_ft, self.depths_ft, self.temps_F))
        return p, tF


def load_well_profile_csv(path) -> WellProfile:
    """Load a well profile CSV with header: depth_ft,pressure_psi,temp_F
    (rows in any depth order; sorted on load). Extra columns are ignored.
    """
    d, pr, tf = [], [], []
    with Path(path).open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d.append(float(row["depth_ft"]))
            pr.append(float(row["pressure_psi"]))
            tf.append(float(row["temp_F"]))
    order = np.argsort(d)
    return WellProfile(depths_ft=[d[i] for i in order],
                       pressures_psi=[pr[i] for i in order],
                       temps_F=[tf[i] for i in order],
                       name=Path(path).stem)


def make_sensor_string(n: int, td_ft: float = DEFAULT_TD_FT,
                       start_ft: float = 2000.0) -> List[float]:
    """Evenly spaced N-gauge string from start_ft to just above TD."""
    if n < 1:
        raise ValueError("need at least one gauge")
    if n == 1:
        return [td_ft * 0.985]
    return list(np.linspace(start_ft, td_ft * 0.985, n))


@dataclass
class Sensor:
    depth_ft: float
    cal_offset_P: float = 0.0
    cal_offset_T: float = 0.0
    k_structural_trim: float = 1.0    # engineering trim (renamed from template 'K_MEX' knob)
    phi_coupling_trim: float = 1.0    # engineering trim (renamed from template 'Phi_res' knob)
    name: str = ""
    tool_name: str = "quartz_pt_program_geoq177_30k"   # v1.47.0 mixed strings: which library tool sits here


@dataclass
class SimulatorConfig:
    td_ft: float = DEFAULT_TD_FT
    sensor_depths_ft: List[float] = field(default_factory=lambda: DEFAULT_SENSOR_DEPTHS_FT.copy())
    surface_temp_F: float = 75.0                    # anchor: surface ambient (template)
    surface_pressure_psi: float = 14.7              # anchor: 1 atm
    temp_gradient_F_per_ft: float = 0.018           # anchor: geothermal gradient (template)
    pressure_gradient_psi_per_ft: float = 0.465     # anchor: hydrostatic gradient (industry)
    global_k_structural_trim: float = 1.0
    global_phi_coupling_trim: float = 1.0
    noise_scale: float = 1.0
    event_probability: float = 0.27                 # template transient-event rate
    history_length: int = 400
    profile: Optional[WellProfile] = None           # real well profile (CSV); overrides gradients
    comparison_mode: bool = True                    # twin-gauge GEA-vs-conventional tracking
    gauge_spec: object = None                       # GaugeSpec (v1.5.0, gauge_specs); None = template anchors
    deviation: object = None                        # DeviationSurvey (v1.6.0): sensors at MD, physics at TVD
    toolstring: object = None                       # ToolString (v1.47.0): mixed per-station tool models
    acknowledge_over_rating: bool = False           # v1.47.0: the ONLY way past the in-engine rating block


class DownholeEngine:
    """Deep-well quartz-gauge string simulator (GEA-stabilized drift)."""

    def __init__(self, config: SimulatorConfig | None = None):
        self.cfg = config or SimulatorConfig()
        self.time = 0.0
        self.sensors: List[Sensor] = []
        self._build_sensors()
        self._init_state()
        if self.cfg.toolstring is not None:
            self._enforce_rating()

    def _enforce_rating(self) -> None:
        """v1.47.0: the rating check runs INSIDE the engine, not only as a
        print - a tool over its cited rating at its station (against the
        REAL profile when one is attached) blocks construction unless the
        operator's explicit acknowledge_over_rating rides in the config."""
        from .tool_library import rating_check
        report = rating_check(self.cfg.toolstring,
                              profile=self.cfg.profile,
                              deviation=self.cfg.deviation)
        blocks = [r for r in report if not r["ok"]]
        self.rating_report = report
        if blocks and not self.cfg.acknowledge_over_rating:
            names = "; ".join(
                f"{b['tool']}@{b['md_ft']:.0f}ft ({b['station_temp_C']}C > "
                f"{b['temp_rating_C']}C rated)" for b in blocks)
            raise RuntimeError(
                f"ENGINE RATING BLOCK: {names}. The check runs inside the "
                "engine (v1.47.0) - set acknowledge_over_rating=True in the "
                "config as an explicit operator decision, or fix the string.")

    # -- construction -------------------------------------------------------
    def _build_sensors(self) -> None:
        if self.cfg.toolstring is not None:
            stations = sorted(self.cfg.toolstring.stations, key=lambda x: x[0])
            self.sensors = [
                Sensor(depth_ft=float(md),
                       k_structural_trim=self.cfg.global_k_structural_trim,
                       phi_coupling_trim=self.cfg.global_phi_coupling_trim,
                       name=f"S{i + 1}", tool_name=tn)
                for i, (md, tn) in enumerate(stations)
            ]
            self.cfg.sensor_depths_ft = [s.depth_ft for s in self.sensors]
        else:
            self.sensors = [
                Sensor(depth_ft=float(d),
                       k_structural_trim=self.cfg.global_k_structural_trim,
                       phi_coupling_trim=self.cfg.global_phi_coupling_trim,
                       name=f"S{i + 1}")
                for i, d in enumerate(self.cfg.sensor_depths_ft)
            ]

    def _physics_depth_ft(self, md_ft: float) -> float:
        """Sensor addresses are MD (position on the string); pressure and
        temperature are set by TVD (v1.6.0 deviation support)."""
        if self.cfg.deviation is not None:
            return float(self.cfg.deviation.tvd_of(md_ft))
        return float(md_ft)

    def _init_state(self) -> None:
        tvds = [self._physics_depth_ft(s.depth_ft) for s in self.sensors]
        if self.cfg.profile is not None:
            pairs = [self.cfg.profile.interp(tvd) for tvd in tvds]   # profiles are TVD-indexed
            self.base_P = np.array([p for p, _ in pairs], dtype=float)
            self.base_T = np.array([tF for _, tF in pairs], dtype=float)
        else:
            self.base_P = np.array(
                [self.cfg.surface_pressure_psi + tvd * self.cfg.pressure_gradient_psi_per_ft
                 for tvd in tvds], dtype=float)
            self.base_T = np.array(
                [self.cfg.surface_temp_F + tvd * self.cfg.temp_gradient_F_per_ft
                 for tvd in tvds], dtype=float)
        self.P = self.base_P.copy()
        self.T = self.base_T.copy()
        self.history_t: List[float] = [0.0]
        self.history_P: List[np.ndarray] = [self.P.copy()]
        self.history_T: List[np.ndarray] = [self.T.copy()]

    # -- physics ------------------------------------------------------------
    def compute_drift(self, sensor: Sensor, temp_F: float, pressure_psi: float) -> float:
        temp_C = (temp_F - 32.0) * 5.0 / 9.0
        depth_m = sensor.depth_ft / 3.28084
        r = calculate_quartz_transducer_hpht_program(
            depth_m=depth_m, temp_c=temp_C, pressure_psi=pressure_psi,
            k_structural_trim=sensor.k_structural_trim,
            phi_coupling_trim=sensor.phi_coupling_trim,
            spec=self.cfg.gauge_spec)
        return float(r["value"]["drift_pct"])

    def station_drift_legs(self, i: int) -> dict:
        """v1.47.0 mixed strings: each station's drift legs come from ITS
        tool's runnable model - and tools without a runnable model REFUSE
        (status says why) instead of borrowing the quartz curve."""
        from .tool_library import TOOL_LIBRARY, piezoresistive_drift
        s = self.sensors[i]
        tool = TOOL_LIBRARY.get(s.tool_name)
        t_C = (float(self.T[i]) - 32.0) * 5.0 / 9.0
        out = {"station": s.name, "md_ft": s.depth_ft, "tool": s.tool_name,
               "tool_class": tool.tool_class if tool else "?",
               "drift_pct": None, "conventional_drift_pct": None}
        if tool is None:
            out["status"] = f"UNKNOWN_TOOL '{s.tool_name}' - refused"
            return out
        if tool.tool_class == "QUARTZ_PT_GAUGE":
            out["conventional_drift_pct"] = round(
                conventional_drift(t_C, float(self.P[i]),
                                   spec=self.cfg.gauge_spec), 4)
            if "conventional" in s.tool_name:
                out["status"] = ("NO_PROGRAM_LEG: conventional instrument - the "
                                 "reference leg only (twin comparison needs "
                                 "the GEA tool at this station)")
            else:
                out["drift_pct"] = round(
                    self.compute_drift(s, float(self.T[i]),
                                       float(self.P[i])), 4)
                out["status"] = "TWIN_LEGS"
        elif tool.tool_class == "PIEZORESISTIVE_PT_GAUGE":
            out["conventional_drift_pct"] = round(piezoresistive_drift(t_C), 4)
            out["status"] = ("PIEZO_CLASS_ENVELOPE (labeled representative "
                            "fit, vendor-scatter disclosed) - NO_PROGRAM_MODEL: "
                            "no GEA piezo derivation in the corpus, refused "
                            "rather than invented")
        else:
            out["status"] = ("PARAMETERS_USER_SUPPLIED: no vendor datasheet "
                            "fetched for this tool class - drift model "
                            "REFUSED; station still streams well P/T")
        return out

    def mixed_summary(self) -> dict:
        """Per-station tool report + aggregates computed ONLY over stations
        possessing both legs, with the counts disclosed."""
        rows = [self.station_drift_legs(i) for i in range(len(self.sensors))]
        twin = [r for r in rows if r["status"] == "TWIN_LEGS"]
        agg = None
        if twin:
            uq = float(np.mean([r["drift_pct"] for r in twin]))
            cv = float(np.mean([r["conventional_drift_pct"] for r in twin]))
            agg = {"avg_program_drift_pct": round(uq, 4),
                   "avg_conventional_drift_pct": round(cv, 4),
                   "measured_ratio_mean": round(cv / uq, 4) if uq > 0 else None}
        return {"stations": rows,
                "twin_leg_stations": len(twin),
                "single_or_refused_stations": len(rows) - len(twin),
                "aggregate_over_twin_stations_only": agg}

    def _noise_drifts(self) -> np.ndarray:
        """Drift values feeding the template noise-suppression map: each
        station's own runnable model; refused stations contribute 0.0
        (neutral suppression - template noise unshaped, per mixed_summary)."""
        if self.cfg.toolstring is None:
            return self.current_drifts
        out = []
        for i in range(len(self.sensors)):
            legs = self.station_drift_legs(i)
            d = legs["drift_pct"]
            if d is None:
                d = legs["conventional_drift_pct"]
            out.append(0.0 if d is None else float(d))
        return np.array(out)

    @property
    def current_drifts(self) -> np.ndarray:
        return np.array([self.compute_drift(s, self.T[i], self.P[i])
                         for i, s in enumerate(self.sensors)])

    @property
    def current_conventional_drifts(self) -> np.ndarray:
        """Comparison-mode reference leg: conventional gauges at the same T/P."""
        out = []
        for i in range(len(self.sensors)):
            temp_C = (self.T[i] - 32.0) * 5.0 / 9.0
            out.append(conventional_drift(temp_C, self.P[i], spec=self.cfg.gauge_spec))
        return np.array(out)

    def comparison_summary(self) -> dict:
        """Twin-gauge report: measured conventional/GEA drift ratio vs the
        suppression prediction (equal when no gauge clips)."""
        uq = self.current_drifts
        cv = self.current_conventional_drifts
        ratios = cv / np.where(uq > 0, uq, np.nan)
        return {
            'avg_program_drift_pct': round(float(np.mean(uq)), 4),
            'avg_conventional_drift_pct': round(float(np.mean(cv)), 4),
            'measured_ratio_mean': round(float(np.nanmean(ratios)), 4),
            'predicted_ratio_suppression': round(canonical_suppression(
                self.cfg.global_k_structural_trim, self.cfg.global_phi_coupling_trim), 4),
            'per_sensor_ratio': [round(float(r), 4) for r in ratios],
        }

    # -- stepping (template-faithful) ---------------------------------------
    def step(self, dt: float = 0.12) -> None:
        self.time += dt
        n = len(self.sensors)
        drifts = self._noise_drifts()
        suppression = np.clip(1.0 - (drifts / 0.28), 0.25, 1.0)   # template noise-suppression map
        noise_P = np.random.normal(0, 28 * self.cfg.noise_scale, n) * suppression
        noise_T = np.random.normal(0, 0.9 * self.cfg.noise_scale, n) * suppression
        event = 0.0
        if random.random() < self.cfg.event_probability:
            event = 110.0 * np.sin(self.time / 7.2) * float(np.mean(suppression))
        self.P = self.base_P + noise_P + event
        self.T = self.base_T + noise_T
        for i, s in enumerate(self.sensors):
            self.P[i] += s.cal_offset_P
            self.T[i] += s.cal_offset_T
        self.P = np.clip(self.P, 50, 28000)     # template plausibility bounds
        self.T = np.clip(self.T, 40, 550)
        self.history_t.append(self.time)
        self.history_P.append(self.P.copy())
        self.history_T.append(self.T.copy())
        if len(self.history_t) > self.cfg.history_length:
            self.history_t = self.history_t[-self.cfg.history_length:]
            self.history_P = self.history_P[-self.cfg.history_length:]
            self.history_T = self.history_T[-self.cfg.history_length:]

    def set_global_trims(self, k_structural_trim: float, phi_coupling_trim: float) -> None:
        self.cfg.global_k_structural_trim = float(k_structural_trim)
        self.cfg.global_phi_coupling_trim = float(phi_coupling_trim)
        for s in self.sensors:
            s.k_structural_trim = float(k_structural_trim)
            s.phi_coupling_trim = float(phi_coupling_trim)

    # -- export (template-faithful) -----------------------------------------
    def export_csv(self, path: str | None = None) -> Path:
        if path is None:
            path = f"downhole_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        p = Path(path)
        with p.open("w", newline="") as f:
            writer = csv.writer(f)
            header = ["time_s"]
            for s in self.sensors:
                header += [f"P_{s.name}_{s.depth_ft:.0f}ft", f"T_{s.name}_{s.depth_ft:.0f}ft"]
            if self.cfg.comparison_mode:
                cmp_now = self.comparison_summary()
                header += ["avg_program_drift_pct", "avg_conventional_drift_pct", "measured_ratio_mean"]
            writer.writerow(header)
            for i, t in enumerate(self.history_t):
                row = [round(t, 3)]
                for j in range(len(self.sensors)):
                    row.append(round(float(self.history_P[i][j]), 2))
                    row.append(round(float(self.history_T[i][j]), 2))
                if self.cfg.comparison_mode:
                    row += [cmp_now['avg_program_drift_pct'], cmp_now['avg_conventional_drift_pct'],
                            cmp_now['measured_ratio_mean']]
                writer.writerow(row)
        return p

    @property
    def depths_ft(self) -> np.ndarray:
        return np.array([s.depth_ft for s in self.sensors])

    def summary(self) -> dict:
        return {
            "live": PROGRAM_MODEL_AVAILABLE,
            "canonical_suppression_at_unity_trims": round(canonical_suppression(), 4),
            "sensors": len(self.sensors),
            "td_ft": self.cfg.td_ft,
            "time_s": round(self.time, 3),
            "avg_drift_pct": round(float(np.mean(self.current_drifts)), 4),
            "history_points": len(self.history_t),
            "profile": self.cfg.profile.name if self.cfg.profile else "linear gradients",
            "deviation": self.cfg.deviation.name if self.cfg.deviation is not None else "vertical (MD == TVD)",
            "gauge_spec": getattr(self.cfg.gauge_spec, 'name', None) or "template_generic (default)",
            "comparison": self.comparison_summary() if self.cfg.comparison_mode else None,
        }


def run_batch(wells: dict, steps: int = 100, dt: float = 0.12) -> dict:
    """Multi-well batch (v1.6.0): run each named SimulatorConfig for `steps`
    and return {well_name: summary}. A field-wide study in one call."""
    out = {}
    for name, cfg in wells.items():
        eng = DownholeEngine(cfg)
        for _ in range(int(steps)):
            eng.step(dt=dt)
        out[name] = eng.summary()
    return out
