# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""matplotlib_demo — animated demo of the GEA Downhole Simulator.

Imperial-unit animated view: a well
schematic on the left (gauges labeled with live P/T), pressure and temperature
strip charts on the right, FuncAnimation at 120 ms, CSV export on close.

Run:  python -m gea.matplotlib_demo
Needs a display (or a matplotlib backend that has one). The engine itself is
headless — only this demo draws.
"""

from __future__ import annotations

import numpy as np

from .downhole_engine import SimulatorConfig, DownholeEngine


def run_demo():
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    cfg = SimulatorConfig()
    engine = DownholeEngine(cfg)

    fig = plt.figure(figsize=(13, 8), facecolor="#0b1220")
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 2.2])
    ax_w = fig.add_subplot(gs[:, 0], facecolor="#0b1220")
    ax_p = fig.add_subplot(gs[0, 1], facecolor="#101a2e")
    ax_t = fig.add_subplot(gs[1, 1], facecolor="#101a2e")

    def draw_well():
        ax_w.clear()
        ax_w.set_facecolor("#0b1220")
        ax_w.set_xlim(-1.6, 1.6)
        ax_w.set_ylim(cfg.td_ft * 1.03, -600)
        ax_w.set_xticks([])
        ax_w.set_ylabel("Depth (ft)", color="#e0f2fe")
        ax_w.tick_params(colors="#e0f2fe")
        ax_w.plot([-0.28, -0.28], [0, cfg.td_ft], color="#475569", lw=3)
        ax_w.plot([0.28, 0.28], [0, cfg.td_ft], color="#475569", lw=3)
        ax_w.plot([-0.28, 0.28], [cfg.td_ft, cfg.td_ft], color="#475569", lw=3)
        for i, s in enumerate(engine.sensors):
            ax_w.plot(0, s.depth_ft, "o", ms=10, color="#22d3ee")
            ax_w.text(0.45, s.depth_ft,
                      f"{s.name}  {engine.P[i]:.0f} psi  {engine.T[i]:.1f}\N{DEGREE SIGN}F",
                      fontsize=8, va="center", color="#e0f2fe")

    def update(_frame):
        engine.step()
        draw_well()
        t_hist = engine.history_t
        for ax, series_hist, label in ((ax_p, engine.history_P, "Pressure (psi)"),
                                       (ax_t, engine.history_T, "Temperature (\N{DEGREE SIGN}F)")):
            ax.clear()
            ax.set_facecolor("#101a2e")
            ax.grid(True, alpha=0.3)
            ax.set_ylabel(label, color="#e0f2fe")
            ax.tick_params(colors="#e0f2fe")
            for i, s in enumerate(engine.sensors):
                ax.plot(t_hist, [row[i] for row in series_hist], lw=1.5,
                        label=f"{s.name} ({s.depth_ft:.0f} ft)")
            ax.legend(fontsize=7, loc="upper right")
        ax_t.set_xlabel("Time (s)", color="#e0f2fe")
        avg_drift = float(np.mean(engine.current_drifts))
        fig.suptitle(
            f"GEA Deep-Well Simulator  |  t={engine.time:.1f}s  |  "
            f"Avg Drift={avg_drift:.3f}% FS/yr  |  TD={cfg.td_ft:.0f} ft  |  "
            f"K_MEX=25/12, \N{GREEK CAPITAL LETTER PHI}_res=0.84 (locked)",
            fontsize=12, color="#67e8f9")
        return []

    ani = FuncAnimation(fig, update, interval=120, blit=False, cache_frame_data=False)
    plt.tight_layout()
    plt.show()
    out = engine.export_csv()
    print(f"Exported: {out.resolve()}")
    return ani


if __name__ == "__main__":
    run_demo()
