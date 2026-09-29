"""Operator surface (v1.44.0) - finish-sequence step 4.

The independent evaluation's largest hole: "An operator cannot pick a
catalogued well, hang a toolstring, run service-life, ingest a LAS, or see
unexplained offsets without writing Python." This module is that surface,
split honestly in two:

* OperatorSession - a HEADLESS controller carrying every operator action
  (well selection, toolstring + blocking rating check, run, service life,
  case study, ingest, reconcile + alerts, citations). It is fully exercised
  by the fidelity gate on every run - the product logic is tested where no
  display exists.
* launch_operator_app() - the Qt6 view over that controller (one window:
  well picker, toolstring builder with rating lights, live P/T + drift
  charts, service-life/case-study export, reconcile + undervalued-stream
  alerts, and a citations pane that is ALWAYS visible - Rule 7 in the UI).
  Refuses with the pip hint when PyQt6 is absent, same pattern as the ports.

Product-honesty rules carried into the surface:
- The rating check BLOCKS the run: a tool over its cited rating at its
  station (against the MEASURED profile) stops start_run with the stations
  named. There is no silent override; `acknowledge_over_rating=True` is the
  explicit, logged operator decision.
- The citations pane never disappears: gauge-spec sources, catalogue
  provenance, and the DERIVED_HYBRID suppression labeling ride with every
  view. The UI does not sell 1.0324 as a derived constant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .downhole_engine import (SimulatorConfig, DownholeEngine,
                                   WellProfile, load_well_profile_csv)
from .quartz_hpht_extension import canonical_suppression
from .tool_library import TOOL_LIBRARY, ToolString, rating_check
from .well_assembler import (BUILTIN_ASSEMBLIES, demo_config,
                                  production_live_stream)


class OperatorSession:
    """Headless operator controller - everything the UI can do, scriptable
    and gate-testable. State: one well, one toolstring, one engine."""

    def __init__(self) -> None:
        self.well_name: Optional[str] = None
        self.config: Optional[SimulatorConfig] = None
        self.toolstring: Optional[ToolString] = None
        self.engine: Optional[DownholeEngine] = None
        self.last_rating: List[dict] = []
        self.last_reconcile: Optional[dict] = None
        self.log: List[str] = []

    # -- well selection ----------------------------------------------------
    def wells(self) -> Dict[str, dict]:
        out = {}
        for name, maker in sorted(BUILTIN_ASSEMBLIES.items()):
            a = maker()
            out[name] = {
                "site": a.site,
                "components": {r: (c.entry, c.channel, c.coverage())
                               for r, c in a.components.items()},
                "engine_ready": "temperature" in a.components,
            }
        return out

    def load_well(self, name: str, n_gauges: int = 6, **kw) -> dict:
        self.config = demo_config(name, n_gauges=n_gauges, **kw)
        self.well_name = name
        self.engine = None
        self.log.append(f"well: {name} ({self.config.profile.name})")
        return {"well": name, "profile": self.config.profile.name,
                "gauges": list(self.config.sensor_depths_ft),
                "window_ft": (self.config.profile.depths_ft[0],
                              self.config.profile.depths_ft[-1])}

    def load_profile_csv(self, path: str, n_gauges: int = 6, **kw) -> dict:
        prof = load_well_profile_csv(path)
        lo, hi = prof.depths_ft[0], prof.depths_ft[-1]
        depths = [lo + (hi - lo) * (i + 1) / (n_gauges + 1)
                  for i in range(n_gauges)]
        self.config = SimulatorConfig(td_ft=hi, sensor_depths_ft=depths,
                                      profile=prof, **kw)
        self.well_name = f"csv:{path}"
        self.engine = None
        self.log.append(f"well: user CSV {path}")
        return {"well": self.well_name, "profile": prof.name,
                "gauges": depths, "window_ft": (lo, hi)}

    # -- toolstring + BLOCKING rating check --------------------------------
    def tools(self) -> Dict[str, dict]:
        return {n: {"class": t.tool_class, "temp_rating_C": t.temp_rating_C,
                    "pressure_rating_psi": t.pressure_rating_psi,
                    "source": t.source}
                for n, t in TOOL_LIBRARY.items()}

    def set_toolstring(self, stations: List[Tuple[float, str]]) -> List[dict]:
        """Hang tools ((md_ft, tool_name)...) and rate them against the
        CURRENT well's profile. Returns the rating report; run() blocks on
        any failing station."""
        if self.config is None:
            raise NotImplementedError(
                "no well loaded - pick a catalogue well (load_well) or a "
                "profile CSV (load_profile_csv) before hanging tools")
        self.toolstring = ToolString(stations=stations)
        self.last_rating = rating_check(
            self.toolstring, profile=self.config.profile,
            deviation=getattr(self.config, "deviation", None))
        for r in self.last_rating:
            if not r["ok"]:
                self.log.append(
                    f"RATING: {r['tool']} at {r['md_ft']:.0f} ft OVER rating "
                    f"({r['station_temp_C']} C vs {r['temp_rating_C']} C)")
        return self.last_rating

    def rating_blocks(self) -> List[dict]:
        return [r for r in self.last_rating if not r["ok"]]

    # -- run ---------------------------------------------------------------
    def start_run(self, acknowledge_over_rating: bool = False) -> DownholeEngine:
        if self.config is None:
            raise NotImplementedError("no well loaded - nothing to run")
        blocks = self.rating_blocks()
        if blocks and not acknowledge_over_rating:
            names = "; ".join(f"{b['tool']}@{b['md_ft']:.0f}ft "
                              f"({b['station_temp_C']}C > {b['temp_rating_C']}C rated)"
                              for b in blocks)
            raise RuntimeError(
                f"RUN BLOCKED by the rating check: {names}. The station "
                "conditions come from the MEASURED profile; move the tool, "
                "pick a higher-rated tool, or pass acknowledge_over_rating="
                "True as an explicit logged operator decision.")
        if blocks:
            self.log.append(f"OPERATOR ACKNOWLEDGED over-rating run "
                            f"({len(blocks)} station(s))")
        if self.toolstring is not None:
            self.config.toolstring = self.toolstring        # v1.47.0 mixed strings
            self.config.acknowledge_over_rating = bool(acknowledge_over_rating)
        self.engine = DownholeEngine(self.config)
        self.log.append("run started")
        return self.engine

    def step(self, n: int = 1) -> dict:
        if self.engine is None:
            raise NotImplementedError("run not started - start_run() first")
        for _ in range(n):
            self.engine.step()
        return self.engine.comparison_summary()

    def mixed_report(self) -> dict:
        """v1.47.0: per-station tool legs from the running engine (twin /
        single / refused, with the aggregate over twin stations only)."""
        if self.engine is None:
            raise NotImplementedError("run not started - start_run() first")
        if self.config.toolstring is None:
            raise NotImplementedError("no toolstring on this run - hang tools "
                                      "with set_toolstring() before start_run()")
        return self.engine.mixed_summary()

    # -- exports -----------------------------------------------------------
    def service_life(self, years: float = 5.0, **kw) -> dict:
        """Twin-leg accumulated error on THIS well: the service-life engine
        evaluates its rates at the loaded (measured) profile's base
        stations, so the divergence summary belongs to the archived well,
        not the template."""
        from .service_life import ServiceLifeConfig, ServiceLifeSimulator
        if self.config is None:
            raise NotImplementedError("no well loaded")
        sim = ServiceLifeSimulator(engine=DownholeEngine(self.config),
                                   config=ServiceLifeConfig(years=years, **kw))
        summ = sim.run().divergence_summary()
        self.log.append(f"service life {years} yr: {summ.get('years_to_budget_conventional', '?')}")
        return summ

    def case_study(self, out_path: str, points: int = 12,
                   horizon_years: float = 5.0) -> str:
        from .case_study import CaseStudyConfig, case_study, write_markdown
        if self.config is None:
            raise NotImplementedError("no well loaded")
        cfg = CaseStudyConfig(td_ft=self.config.td_ft, n_depth_points=points,
                              well_name=self.well_name or "operator well",
                              horizon_years=horizon_years,
                              profile=self.config.profile)
        write_markdown(case_study(cfg), out_path)
        self.log.append(f"case study -> {out_path}")
        return out_path

    # -- ingest + reconcile + alerts ---------------------------------------
    def ingest(self, source, port: str = "historian_csv"):
        from . import ingest as _ingest
        return _ingest(source, port=port)

    def reconcile(self, stream=None, live_catalog: Optional[str] = None,
                  live_well: Optional[str] = None,
                  station_md_ft: Optional[float] = None) -> dict:
        from .reconciler import Reconciler
        if self.config is None:
            raise NotImplementedError("no well loaded - the closed stream "
                                      "needs a well configuration")
        station_map = None
        if live_catalog is not None:
            stream, station_map = production_live_stream(
                live_catalog, live_well, station_md_ft)
        if stream is None:
            raise NotImplementedError("reconcile needs a stream or a "
                                      "live_catalog entry")
        self.last_reconcile = Reconciler(self.config).reconcile(
            stream, station_map=station_map)
        for a in self.alerts():
            self.log.append(f"ALERT: {a['channel']} at {a['md_ft']:.0f} ft "
                            f"UNEXPLAINED ({a.get('slope_psi_yr', '?')} psi/yr)")
        return self.last_reconcile

    def alerts(self) -> List[dict]:
        """Undervalued-stream alerts from the last reconciliation."""
        if not self.last_reconcile:
            return []
        return [s for s in self.last_reconcile["stations"]
                if s["classification"].startswith("UNEXPLAINED")]

    # -- Rule 7: the always-visible citations block ------------------------
    def citations(self) -> dict:
        """Everything the operator is looking at, sourced. The UI renders
        this pane permanently; it is never hidden behind a menu."""
        out = {
            "suppression": ("canonical_suppression() = "
                            f"{canonical_suppression():.4f} at unity trims - "
                            "DERIVED_HYBRID: industry baseline drift x locked "
                            "GEA primitive composition (NOT a derived "
                            "constant; bench test = finish-seq step 8)"),
            "gauge_spec": None, "well_provenance": {}, "tools": {}}
        if self.config is not None:
            spec = getattr(self.config, "gauge_spec", None)
            out["gauge_spec"] = (f"{spec.name}: {spec.source}" if spec
                                 else "template_generic (template class - no vendor claim)")
        if self.well_name in BUILTIN_ASSEMBLIES:
            a = BUILTIN_ASSEMBLIES[self.well_name]()
            for r, c in a.components.items():
                out["well_provenance"][r] = {
                    "entry": c.entry,
                    "source": c.provenance.get("source_database", "?"),
                    "license": c.provenance.get("license", "?")}
        if self.toolstring is not None:
            for _, tn in self.toolstring.stations:
                out["tools"][tn] = TOOL_LIBRARY[tn].source
        return out


# --------------------------------------------------------------------------
# The Qt6 view. Thin: every button calls one OperatorSession method.
# --------------------------------------------------------------------------
def launch_operator_app() -> int:
    """One operator window over OperatorSession. Refuses without PyQt6
    (pip hint), same pattern as the live-protocol port."""
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog,
                                     QHBoxLayout, QLabel, QListWidget,
                                     QMainWindow, QPushButton, QSpinBox,
                                     QTabWidget, QTextEdit, QVBoxLayout,
                                     QWidget)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
    except ImportError:
        raise NotImplementedError(
            "the operator app requires the optional GUI dependencies: "
            "pip install PyQt6 matplotlib (the OperatorSession controller "
            "works headless without them - this is only the view)")
    import sys as _sys

    session = OperatorSession()

    class OperatorWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("GEA Downhole - Operator (v1.44.0)")
            root = QWidget(); self.setCentralWidget(root)
            outer = QVBoxLayout(root)
            body = QHBoxLayout(); outer.addLayout(body, stretch=1)

            # left: controls
            left = QVBoxLayout(); body.addLayout(left)
            left.addWidget(QLabel("Well (measured catalogue assembly):"))
            self.well_box = QComboBox()
            self.well_box.addItems([n for n, w in session.wells().items()
                                    if w["engine_ready"]])
            left.addWidget(self.well_box)
            b_load = QPushButton("Load well"); left.addWidget(b_load)
            b_csv = QPushButton("Load profile CSV..."); left.addWidget(b_csv)
            left.addWidget(QLabel("Toolstring:"))
            self.tool_box = QComboBox(); self.tool_box.addItems(TOOL_LIBRARY)
            left.addWidget(self.tool_box)
            self.md_box = QSpinBox(); self.md_box.setRange(0, 40000)
            self.md_box.setValue(26000); left.addWidget(self.md_box)
            b_hang = QPushButton("Hang tool + rating check"); left.addWidget(b_hang)
            self.rating_lbl = QLabel("rating: -"); left.addWidget(self.rating_lbl)
            b_run = QPushButton("Start run"); left.addWidget(b_run)
            b_case = QPushButton("Case study (.md)"); left.addWidget(b_case)
            b_rec = QPushButton("Reconcile Volve F-12 (live catalogue)")
            left.addWidget(b_rec)
            b_ing = QPushButton("Ingest LAS / historian CSV...")
            left.addWidget(b_ing)
            left.addStretch(1)

            # right: tabs
            tabs = QTabWidget(); body.addWidget(tabs, stretch=1)
            self.fig = Figure(figsize=(6, 4))
            self.canvas = FigureCanvasQTAgg(self.fig)
            tabs.addTab(self.canvas, "Live P/T")
            self.dfig = Figure(figsize=(6, 4))
            self.dcanvas = FigureCanvasQTAgg(self.dfig)
            tabs.addTab(self.dcanvas, "Twin-leg drift")
            self.alerts_list = QListWidget()
            tabs.addTab(self.alerts_list, "Alerts")
            self.log_view = QTextEdit(); self.log_view.setReadOnly(True)
            tabs.addTab(self.log_view, "Log")

            # bottom: Rule 7 - citations pane, ALWAYS visible
            self.cite = QTextEdit(); self.cite.setReadOnly(True)
            self.cite.setMaximumHeight(140)
            outer.addWidget(QLabel("Citations / provenance (always visible):"))
            outer.addWidget(self.cite)

            self.stations: List[Tuple[float, str]] = []
            self.timer = QTimer(); self.timer.timeout.connect(self._tick)
            b_load.clicked.connect(self._load)
            b_csv.clicked.connect(self._load_csv)
            b_hang.clicked.connect(self._hang)
            b_run.clicked.connect(self._run)
            b_case.clicked.connect(self._case)
            b_rec.clicked.connect(self._reconcile)
            b_ing.clicked.connect(self._ingest)
            self._refresh_citations()

        def _refresh_citations(self):
            import json
            self.cite.setPlainText(json.dumps(session.citations(), indent=1))
            self.log_view.setPlainText("\n".join(session.log[-40:]))

        def _load(self):
            info = session.load_well(self.well_box.currentText())
            lo, hi = info["window_ft"]
            self.md_box.setRange(int(lo), int(hi))
            self.md_box.setValue(int((lo + hi) / 2))
            self.stations = []
            self._refresh_citations()

        def _load_csv(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Well profile CSV (depth_ft,pressure_psi,temp_F)")
            if path:
                session.load_profile_csv(path)
                self._refresh_citations()

        def _hang(self):
            self.stations.append((float(self.md_box.value()),
                                  self.tool_box.currentText()))
            rep = session.set_toolstring(self.stations)
            bad = [r for r in rep if not r["ok"]]
            self.rating_lbl.setText(
                "rating: BLOCKED - " + "; ".join(
                    f"{b['tool']}@{b['md_ft']:.0f}ft" for b in bad)
                if bad else f"rating: OK ({len(rep)} station(s))")
            self._refresh_citations()

        def _run(self):
            try:
                session.start_run()
            except RuntimeError as e:      # rating block surfaces verbatim
                self.rating_lbl.setText(str(e)[:160])
                self._refresh_citations()
                return
            self.timer.start(150)

        def _tick(self):
            session.step(1)
            eng = session.engine
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            hist = eng.history
            if hist:
                t = [row["time_s"] for row in hist]
                for i in range(len(eng.sensors)):
                    ax.plot(t, [row[f"P_S{i+1}"] for row in hist], lw=0.8)
                ax.set_xlabel("t, s"); ax.set_ylabel("P, psi")
                ax.set_title(session.config.profile.name, fontsize=8)
            self.canvas.draw_idle()
            summ = eng.comparison_summary()
            self.dfig.clear()
            dax = self.dfig.add_subplot(111)
            dax.bar([0, 1], [summ["avg_program_drift_pct"],
                             summ["avg_conventional_drift_pct"]],
                    tick_label=["program-model leg", "conventional leg"])
            dax.set_ylabel("avg drift, %FS/yr")
            dax.set_title(f"twin-leg ratio {summ['measured_ratio_mean']:.4f} "
                          "(suppression: DERIVED_HYBRID - see citations)",
                          fontsize=8)
            self.dcanvas.draw_idle()
            self._refresh_citations()

        def _case(self):
            path = session.case_study("operator_case_study.md")
            self.log_view.append(f"case study -> {path}")

        def _ingest(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Ingest live-stream file",
                filter="Well data (*.las *.csv);;All files (*)")
            if not path:
                return
            port = "las2" if path.lower().endswith(".las") else "historian_csv"
            try:
                st = session.ingest(path, port=port)
                session.log.append(
                    f"ingest[{port}]: {st.name} - {len(st.index)} samples, "
                    f"channels: {', '.join(list(st.channels)[:6])}")
            except Exception as e:
                session.log.append(f"ingest REFUSED: {str(e)[:160]}")
            self._refresh_citations()

        def _reconcile(self):
            try:
                session.reconcile(
                    live_catalog="volve_f12_f14_production_excerpt",
                    live_well="15/9-F-12", station_md_ft=10000.0)
            except Exception as e:
                self.alerts_list.addItem(str(e)[:200]); return
            self.alerts_list.clear()
            for a in session.alerts():
                self.alerts_list.addItem(
                    f"{a['channel']} @ {a['md_ft']:.0f} ft: "
                    f"{a['classification']} ({a.get('slope_psi_yr', '?')} psi/yr)")
            if not session.alerts():
                self.alerts_list.addItem("no unexplained streams")
            self._refresh_citations()

    app = QApplication.instance() or QApplication(_sys.argv)
    win = OperatorWindow(); win.resize(1100, 700); win.show()
    return app.exec()
