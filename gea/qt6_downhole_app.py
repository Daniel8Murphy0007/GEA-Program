# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""qt6_downhole_app - PyQt6 window for the synthetic well (optional).

Control panel + embedded matplotlib canvas, QTimer-driven stepping at 120 ms,
CSV export button. The panel shows each station's datasheet aging rate and
whether the station is over the instrument's rating; there is nothing to tune.

PyQt6 is an OPTIONAL dependency: `pip install PyQt6 matplotlib`.
Run:  python -m gea.qt6_downhole_app
"""

from __future__ import annotations

import sys

from .downhole_engine import SimulatorConfig, DownholeEngine

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (QApplication, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                                 QPushButton, QVBoxLayout, QWidget)
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


if QT_AVAILABLE:

    class MplCanvas(FigureCanvasQTAgg):
        def __init__(self):
            self.fig = Figure(figsize=(9, 7), facecolor="#0b1220")
            self.ax_p = self.fig.add_subplot(211, facecolor="#101a2e")
            self.ax_t = self.fig.add_subplot(212, facecolor="#101a2e")
            super().__init__(self.fig)

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("GEA Deep-Well Quartz Simulator (PyQt6) - gea-program")
            self.resize(1480, 920)
            self.cfg = SimulatorConfig()
            self.engine = DownholeEngine(self.cfg)
            self._build_ui()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._on_timer)
            self.timer.setInterval(120)
            self.statusBar().showMessage("Ready")

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QHBoxLayout(central)

            control = QWidget()
            control.setFixedWidth(360)
            clayout = QVBoxLayout(control)

            gbox = QGroupBox("Gauge aging (from the datasheet)")
            form0 = QFormLayout(gbox)
            ag = self.engine.aging_summary()
            form0.addRow("datasheet", QLabel(self.engine.summary()["gauge_spec"]))
            for st in ag["stations"]:
                rate = st.get("rate_pct_fs_yr")
                form0.addRow(st["station"], QLabel((f"{rate:g} %FS/yr" if rate is not None else "no rate on record") + ("  OVER RATING" if str(st["status"]).startswith("OVER") else "")))
            clayout.addWidget(gbox)

            self.btn_start = QPushButton("Start")
            self.btn_stop = QPushButton("Stop")
            self.btn_export = QPushButton("Export CSV")
            self.btn_start.clicked.connect(self.timer.start)
            self.btn_stop.clicked.connect(self.timer.stop)
            self.btn_export.clicked.connect(self._on_export)
            for b in (self.btn_start, self.btn_stop, self.btn_export):
                clayout.addWidget(b)
            clayout.addStretch(1)

            self.canvas = MplCanvas()
            main_layout.addWidget(control)
            main_layout.addWidget(self.canvas, stretch=1)

        def _on_export(self):
            p = self.engine.export_csv()
            self.statusBar().showMessage(f"Exported: {p.resolve()}")

        def _on_timer(self):
            e = self.engine
            e.step()
            for ax, hist, label in ((self.canvas.ax_p, e.history_P, "Pressure (psi)"),
                                    (self.canvas.ax_t, e.history_T, "Temperature (\N{DEGREE SIGN}F)")):
                ax.clear()
                ax.set_facecolor("#101a2e")
                ax.grid(True, alpha=0.3)
                ax.set_ylabel(label, color="#e0f2fe")
                ax.tick_params(colors="#e0f2fe")
                for i, s in enumerate(e.sensors):
                    ax.plot(e.history_t, [row[i] for row in hist], lw=1.4,
                            label=f"{s.name} ({s.depth_ft:.0f} ft)")
                ax.legend(fontsize=7, loc="upper right")
            s = e.summary()
            self.statusBar().showMessage(
                f"t={s['time_s']}s  datasheet aging={s['avg_drift_pct']} %FS/yr  "
                f"{s['aging']['n_over_rating']} station(s) over rating")
            self.canvas.draw_idle()


def main():
    if not QT_AVAILABLE:
        print("PyQt6/matplotlib not available. Install with: pip install PyQt6 matplotlib")
        return 1
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.timer.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
