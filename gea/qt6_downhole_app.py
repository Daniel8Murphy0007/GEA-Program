# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""qt6_downhole_app — PyQt6 GUI for the GEA Downhole Simulator (optional).

Control panel + embedded matplotlib canvas, QTimer-driven stepping at 120 ms,
CSV export button. The spinboxes are the ENGINEERING TRIMS (k_structural_trim /
phi_coupling_trim, range 0.50-1.50, default 1.00) — the constants K_MEX = 25/12
and Phi_res = 0.84 are locked inside the aging model and shown read-only.

PyQt6 is an OPTIONAL dependency: `pip install PyQt6 matplotlib`.
Run:  python -m gea.qt6_downhole_app
"""

from __future__ import annotations

import sys

from .downhole_engine import SimulatorConfig, DownholeEngine
from .quartz_hpht_extension import canonical_suppression

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (QApplication, QDoubleSpinBox, QFormLayout,
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

            gbox = QGroupBox("Aging-model constants (LOCKED)")
            form0 = QFormLayout(gbox)
            form0.addRow("K_MEX", QLabel("25/12 = 2.0833"))
            form0.addRow("Phi_res", QLabel("0.84"))
            form0.addRow("rho ratio", QLabel("F_TRZ = 0.1"))
            form0.addRow("suppression", QLabel(f"{canonical_suppression():.4f} at unity trims"))
            clayout.addWidget(gbox)

            tbox = QGroupBox("Engineering trims (instrument tuning)")
            form = QFormLayout(tbox)
            self.spin_k = QDoubleSpinBox()
            self.spin_k.setRange(0.50, 1.50)
            self.spin_k.setSingleStep(0.01)
            self.spin_k.setValue(1.00)
            self.spin_phi = QDoubleSpinBox()
            self.spin_phi.setRange(0.50, 1.50)
            self.spin_phi.setSingleStep(0.01)
            self.spin_phi.setValue(1.00)
            form.addRow("k_structural_trim", self.spin_k)
            form.addRow("phi_coupling_trim", self.spin_phi)
            self.spin_k.valueChanged.connect(self._on_trims)
            self.spin_phi.valueChanged.connect(self._on_trims)
            clayout.addWidget(tbox)

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

        def _on_trims(self):
            self.engine.set_global_trims(self.spin_k.value(), self.spin_phi.value())

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
                f"t={s['time_s']}s  avg drift={s['avg_drift_pct']}% FS/yr  "
                f"suppression={s['canonical_suppression_at_unity_trims']} (unity trims)")
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
