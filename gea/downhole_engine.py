# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""downhole_engine - the synthetic well: a gauge string with noise and events.

Imperial units (ft / degF / psi); a string of gauges at stations along a
well; base pressure and temperature from gradients or a real profile; noise
and transient events; rolling history; CSV export. Each gauge's aging rate
is what its datasheet says (`gauge_aging`, through the tool library); the
simulator adds nothing to it.

What is synthetic here is said so: the noise amplitudes, the event rate and
shape, the plausibility clips are settings of a data generator used to
rehearse the pipeline, not physics claims.

Headless-safe by design: no matplotlib/Qt imports here.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np

from .gauge_aging import aging_rate
from .gauge_specs import DEFAULT_SPEC_NAME

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
    name: str = ""
    tool_name: str = "quartz_pt_geoq177_30k"   # which library tool sits here (its datasheet gives the aging rate)


@dataclass
class SimulatorConfig:
    td_ft: float = DEFAULT_TD_FT
    sensor_depths_ft: List[float] = field(default_factory=lambda: DEFAULT_SENSOR_DEPTHS_FT.copy())
    surface_temp_F: float = 75.0                    # simulator setting: surface ambient
    surface_pressure_psi: float = 14.7              # 1 atm
    temp_gradient_F_per_ft: float = 0.018           # simulator setting: a geothermal gradient (about 33 C/km)
    pressure_gradient_psi_per_ft: float = 0.465     # hydrostatic gradient of brine (industry rule of thumb)
    noise_scale: float = 1.0
    event_probability: float = 0.27                 # simulator setting: transient-event rate per step
    history_length: int = 400
    profile: Optional[WellProfile] = None           # real well profile (CSV); overrides gradients
    gauge_spec: object = None                       # GaugeSpec (gauge_specs); None = the default cited datasheet
    deviation: object = None                        # DeviationSurvey: sensors at MD, physics at TVD
    toolstring: object = None                       # ToolString: mixed per-station tool models
    acknowledge_over_rating: bool = False           # the ONLY way past the in-engine rating block


class DownholeEngine:
    """A synthetic gauge string in a described well."""

    def __init__(self, config: SimulatorConfig | None = None):
        self.cfg = config or SimulatorConfig()
        self.time = 0.0
        self.sensors: List[Sensor] = []
        self._build_sensors()
        self._init_state()
        if self.cfg.toolstring is not None:
            self._enforce_rating()

    def _enforce_rating(self) -> None:
        """The rating check runs INSIDE the engine, not only as a
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
                "engine - set acknowledge_over_rating=True in the "
                "config as an explicit operator decision, or fix the string.")

    # -- construction -------------------------------------------------------
    def _build_sensors(self) -> None:
        if self.cfg.toolstring is not None:
            stations = sorted(self.cfg.toolstring.stations, key=lambda x: x[0])
            self.sensors = [Sensor(depth_ft=float(md), name=f"S{i + 1}", tool_name=tn) for i, (md, tn) in enumerate(stations)]
            self.cfg.sensor_depths_ft = [s.depth_ft for s in self.sensors]
        else:
            self.sensors = [Sensor(depth_ft=float(d), name=f"S{i + 1}") for i, d in enumerate(self.cfg.sensor_depths_ft)]

    def _physics_depth_ft(self, md_ft: float) -> float:
        """Sensor addresses are MD (position on the string); pressure and
        temperature are set by TVD (deviation support)."""
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

    # -- aging ----------------------------------------------------------------
    def compute_drift(self, sensor: Sensor, temp_F: float, pressure_psi: float) -> float:
        """The gauge's datasheet aging rate (%FS/yr) at this station. With a toolstring the
        station's tool decides; otherwise the config's gauge_spec (or the default datasheet)."""
        temp_C = (temp_F - 32.0) * 5.0 / 9.0
        if self.cfg.toolstring is not None:
            from .tool_library import TOOL_LIBRARY, drift_model_for, PARAMETERS_USER_SUPPLIED
            tool = TOOL_LIBRARY[sensor.tool_name]
            if tool.spec_status == PARAMETERS_USER_SUPPLIED or tool.drift_model is None:
                return float('nan')                                   # no rate on record: the station carries none
            return float(drift_model_for(tool)(temp_C, pressure_psi))
        return float(aging_rate(self.cfg.gauge_spec, temp_C, pressure_psi)['rate_pct_fs_yr'])

    def station_aging(self, i: int) -> dict:
        """One station's aging on record: the rate, its source, and whether the station is over the rating."""
        s = self.sensors[i]
        t_C = (float(self.T[i]) - 32.0) * 5.0 / 9.0
        out = {"station": s.name, "md_ft": s.depth_ft, "tool": s.tool_name if self.cfg.toolstring is not None else None,
               "temp_C": round(t_C, 1), "pressure_psi": round(float(self.P[i]), 0)}
        if self.cfg.toolstring is not None:
            from .tool_library import TOOL_LIBRARY, PARAMETERS_USER_SUPPLIED
            tool = TOOL_LIBRARY[s.tool_name]
            if tool.spec_status == PARAMETERS_USER_SUPPLIED or tool.gauge_spec is None:
                out.update({"rate_pct_fs_yr": None, "status": "NO_RATE_ON_RECORD: the cited page publishes no drift rate for this tool; supply its datasheet"})
                return out
            spec = tool.gauge_spec
        else:
            spec = self.cfg.gauge_spec
        a = aging_rate(spec, t_C, float(self.P[i]))
        out.update({"rate_pct_fs_yr": a["rate_pct_fs_yr"], "rate_psi_yr": round(a["rate_psi_yr"], 3), "gauge_spec": a["gauge_spec"],
                    "status": ("OVER_RATING: " + a["over_rating_detail"]) if a["over_rating"] else "DATASHEET_RATE"})
        return out

    def aging_summary(self) -> dict:
        rows = [self.station_aging(i) for i in range(len(self.sensors))]
        rates = [r["rate_pct_fs_yr"] for r in rows if r.get("rate_pct_fs_yr") is not None]
        return {"stations": rows, "avg_rate_pct_fs_yr": (round(float(np.mean(rates)), 4) if rates else None),
                "n_without_rate": sum(1 for r in rows if r.get("rate_pct_fs_yr") is None),
                "n_over_rating": sum(1 for r in rows if str(r.get("status", "")).startswith("OVER_RATING"))}

    def _noise_drifts(self) -> np.ndarray:
        """Per-station aging rates feeding the synthetic noise shaping; a station with no rate on record gets 0."""
        d = self.current_drifts
        return np.where(np.isnan(d), 0.0, d)

    @property
    def current_drifts(self) -> np.ndarray:
        return np.array([self.compute_drift(s, self.T[i], self.P[i]) for i, s in enumerate(self.sensors)], dtype=float)

    # -- stepping (a synthetic data generator) ----------------------------------
    def step(self, dt: float = 0.12) -> None:
        self.time += dt
        n = len(self.sensors)
        drifts = self._noise_drifts()
        shaping = np.clip(1.0 - (drifts / 0.28), 0.25, 1.0)   # simulator setting: noisier stations are the ones aging faster
        noise_P = np.random.normal(0, 28 * self.cfg.noise_scale, n) * shaping
        noise_T = np.random.normal(0, 0.9 * self.cfg.noise_scale, n) * shaping
        event = 0.0
        if random.random() < self.cfg.event_probability:
            event = 110.0 * np.sin(self.time / 7.2) * float(np.mean(shaping))
        self.P = self.base_P + noise_P + event
        self.T = self.base_T + noise_T
        for i, s in enumerate(self.sensors):
            self.P[i] += s.cal_offset_P
            self.T[i] += s.cal_offset_T
        self.P = np.clip(self.P, 50, 28000)     # simulator plausibility bounds
        self.T = np.clip(self.T, 40, 550)
        self.history_t.append(self.time)
        self.history_P.append(self.P.copy())
        self.history_T.append(self.T.copy())
        if len(self.history_t) > self.cfg.history_length:
            self.history_t = self.history_t[-self.cfg.history_length:]
            self.history_P = self.history_P[-self.cfg.history_length:]
            self.history_T = self.history_T[-self.cfg.history_length:]

    # -- export -----------------------------------------------------------------
    def export_csv(self, path: str | None = None) -> Path:
        if path is None:
            path = f"downhole_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        p = Path(path)
        with p.open("w", newline="") as f:
            writer = csv.writer(f)
            header = ["time_s"]
            for s in self.sensors:
                header += [f"P_{s.name}_{s.depth_ft:.0f}ft", f"T_{s.name}_{s.depth_ft:.0f}ft"]
            writer.writerow(header)
            for i, t in enumerate(self.history_t):
                row = [round(t, 3)]
                for j in range(len(self.sensors)):
                    row.append(round(float(self.history_P[i][j]), 2))
                    row.append(round(float(self.history_T[i][j]), 2))
                writer.writerow(row)
        return p

    @property
    def depths_ft(self) -> np.ndarray:
        return np.array([s.depth_ft for s in self.sensors])

    def summary(self) -> dict:
        d = self.current_drifts
        return {
            "sensors": len(self.sensors),
            "td_ft": self.cfg.td_ft,
            "time_s": round(self.time, 3),
            "avg_drift_pct": (round(float(np.nanmean(d)), 4) if np.any(~np.isnan(d)) else None),
            "history_points": len(self.history_t),
            "profile": self.cfg.profile.name if self.cfg.profile else "linear gradients",
            "deviation": self.cfg.deviation.name if self.cfg.deviation is not None else "vertical (MD == TVD)",
            "gauge_spec": getattr(self.cfg.gauge_spec, 'name', None) or f"{DEFAULT_SPEC_NAME} (default datasheet)",
            "aging": self.aging_summary(),
        }


def run_batch(wells: dict, steps: int = 100, dt: float = 0.12) -> dict:
    """Multi-well batch: run each named SimulatorConfig for `steps`
    and return {well_name: summary}. A field-wide study in one call."""
    out = {}
    for name, cfg in wells.items():
        eng = DownholeEngine(cfg)
        for _ in range(int(steps)):
            eng.step(dt=dt)
        out[name] = eng.summary()
    return out
