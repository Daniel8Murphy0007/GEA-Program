"""case_study — depth-sweep case-study mode (v1.4.0 extension).

Sweeps a well from top to TD and reports, at every depth, both drift legs
(conventional vs GEA-stabilized), the separation in psi/yr, and the
service-life arithmetic — then identifies WHERE the GEA advantage is
largest (the deep hot interval past the stress knees, which is exactly where
permanent-gauge drift matters most and gauges are hardest to replace).

`write_markdown()` renders the sweep as the one-page case a customer would
read: well summary, depth table, headline numbers, the bench-test claim, and
the honest DERIVED_HYBRID classification note. Also runnable as a CLI:

    python -m gea.case_study --td 25000 --out case.md

Headless-safe: numpy only, no display imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

import numpy as np

from .downhole_engine import WellProfile, load_well_profile_csv, DEFAULT_TD_FT
from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    canonical_suppression,
    conventional_drift,
)


@dataclass
class CaseStudyConfig:
    td_ft: float = DEFAULT_TD_FT
    n_depth_points: int = 12
    start_ft: float = 1000.0
    surface_temp_F: float = 75.0                    # anchor: surface ambient (template)
    surface_pressure_psi: float = 14.7              # anchor: 1 atm
    temp_gradient_F_per_ft: float = 0.018           # anchor: geothermal gradient (template)
    pressure_gradient_psi_per_ft: float = 0.465     # anchor: hydrostatic gradient (industry)
    profile: Optional[WellProfile] = None           # real well profile overrides gradients
    full_scale_psi: float = 30000.0                 # anchor: HPHT quartz-gauge FS class
    error_budget_pct_fs: float = 0.5                # anchor: typical spec total-error budget
    horizon_years: float = 5.0
    k_structural_trim: float = 1.0
    phi_coupling_trim: float = 1.0
    gauge_spec: object = None                       # GaugeSpec (v1.5.0); None = template anchors
    deviation: object = None                        # DeviationSurvey (v1.6.0): sweep depths are MD, physics at TVD
    well_name: str = "case-study well"


def depth_sweep(cfg: CaseStudyConfig | None = None) -> List[dict]:
    """Per-depth twin-leg drift table from surface interval to TD."""
    cfg = cfg or CaseStudyConfig()
    fs = float(cfg.gauge_spec.full_scale_psi) if cfg.gauge_spec is not None else cfg.full_scale_psi
    knee_C = float(cfg.gauge_spec.thermal_knee_C) if cfg.gauge_spec is not None else 150.0
    knee_psi = float(cfg.gauge_spec.pressure_knee_psi) if cfg.gauge_spec is not None else 15000.0
    depths = np.linspace(cfg.start_ft, cfg.td_ft * 0.985, cfg.n_depth_points)
    rows = []
    for d in depths:
        d = float(d)                     # keep every reported number a plain float; d is MD
        tvd = float(cfg.deviation.tvd_of(d)) if cfg.deviation is not None else d
        if cfg.profile is not None:
            p_psi, t_F = cfg.profile.interp(tvd)     # profiles are TVD-indexed
        else:
            p_psi = cfg.surface_pressure_psi + tvd * cfg.pressure_gradient_psi_per_ft
            t_F = cfg.surface_temp_F + tvd * cfg.temp_gradient_F_per_ft
        t_C = (t_F - 32.0) * 5.0 / 9.0
        r = calculate_quartz_transducer_hpht_program(
            depth_m=float(d) / 3.28084, temp_c=t_C, pressure_psi=p_psi,
            k_structural_trim=cfg.k_structural_trim,
            phi_coupling_trim=cfg.phi_coupling_trim,
            spec=cfg.gauge_spec)
        uq = r["value"]["drift_pct"]
        cv = conventional_drift(t_C, p_psi, spec=cfg.gauge_spec)
        sep = cv - uq
        rows.append({
            'depth_ft': round(float(d), 0),
            'tvd_ft': round(tvd, 0),
            'temp_F': round(t_F, 1),
            'temp_C': round(t_C, 1),
            'pressure_psi': round(p_psi, 0),
            'past_thermal_knee': bool(t_C > knee_C),
            'past_pressure_knee': bool(p_psi > knee_psi),
            'conv_drift_pct_fs_yr': round(cv, 4),
            'drift_pct_fs_yr': round(uq, 4),
            'separation_pct_fs_yr': round(sep, 5),
            'separation_psi_yr': round(sep * fs / 100.0, 2),
            'horizon_separation_psi': round(sep * fs / 100.0 * cfg.horizon_years, 1),
            'extra_service_life_yr': round(
                cfg.error_budget_pct_fs / uq - cfg.error_budget_pct_fs / cv, 3) if uq > 0 else None,
        })
    return rows


def case_study(cfg: CaseStudyConfig | None = None) -> dict:
    """The sweep plus headline metrics: where the advantage is largest."""
    cfg = cfg or CaseStudyConfig()
    rows = depth_sweep(cfg)
    best = max(rows, key=lambda r: r['separation_psi_yr'])
    knee_rows = [r for r in rows if r['past_thermal_knee'] or r['past_pressure_knee']]
    return {
        'config': {
            'well_name': cfg.well_name,
            'td_ft': cfg.td_ft,
            'profile': cfg.profile.name if cfg.profile else 'linear gradients',
            'full_scale_psi': (float(cfg.gauge_spec.full_scale_psi)
                               if cfg.gauge_spec is not None else cfg.full_scale_psi),
            'gauge_spec': cfg.gauge_spec.name if cfg.gauge_spec is not None else 'template_generic (default)',
            'gauge_spec_source': cfg.gauge_spec.source if cfg.gauge_spec is not None else '22Aug2026 template anchors',
            'horizon_years': cfg.horizon_years,
        },
        'canonical_suppression': round(canonical_suppression(
            cfg.k_structural_trim, cfg.phi_coupling_trim), 4),
        'rows': rows,
        'headline': {
            'best_depth_ft': best['depth_ft'],
            'best_separation_psi_yr': best['separation_psi_yr'],
            'best_horizon_separation_psi': best['horizon_separation_psi'],
            'td_conv_drift_pct_fs_yr': rows[-1]['conv_drift_pct_fs_yr'],
            'td_program_drift_pct_fs_yr': rows[-1]['drift_pct_fs_yr'],
            'hpht_interval_top_ft': knee_rows[0]['depth_ft'] if knee_rows else None,
            'advantage_grows_with_depth': bool(
                rows[-1]['separation_psi_yr'] >= rows[0]['separation_psi_yr']),
        },
    }


def write_markdown(result: dict | None = None, path: str | None = None,
                   cfg: CaseStudyConfig | None = None) -> Path:
    """Render the one-page customer-facing case study as markdown."""
    if result is None:
        result = case_study(cfg)
    if path is None:
        path = "downhole_case_study.md"
    c = result['config']
    h = result['headline']
    sup = result['canonical_suppression']
    lines = [
        f"# GEA Downhole Drift Case Study — {c['well_name']}",
        "",
        f"*Generated {date.today().isoformat()} by gea "
        f"(gea-program). Basis: {c['profile']}; TD {c['td_ft']:,.0f} ft; "
        f"full scale {c['full_scale_psi']:,.0f} psi; horizon {c['horizon_years']:g} yr.*",
        "",
        f"*Gauge spec: **{c['gauge_spec']}** — {c['gauge_spec_source']}*",
        "",
        "## The claim",
        "",
        f"Quartz-gauge drift ({c['gauge_spec']} baseline) divided by the GEA",
        f"canonical suppression composition = **{sup}** at the locked primitives",
        "(F_TRZ = 0.1, K_MEX = 25/12, Phi_res = 0.84) — drift **below** the",
        "conventional gauge at every depth, with the largest advantage in the",
        "deep hot interval where gauges are hardest to replace.",
        "",
        "## Depth sweep",
        "",
        "| Depth (ft) | T (degF) | P (psi) | HPHT | Conv (%FS/yr) | GEA (%FS/yr) | Sep (psi/yr) | Extra life (yr) |",
        "|---:|---:|---:|:--:|---:|---:|---:|---:|",
    ]
    for r in result['rows']:
        knee = "YES" if (r['past_thermal_knee'] or r['past_pressure_knee']) else "-"
        lines.append(
            f"| {r['depth_ft']:,.0f} | {r['temp_F']} | {r['pressure_psi']:,.0f} | {knee} "
            f"| {r['conv_drift_pct_fs_yr']} | {r['drift_pct_fs_yr']} "
            f"| {r['separation_psi_yr']} | {r['extra_service_life_yr']} |")
    lines += [
        "",
        "## Headlines",
        "",
        f"- Largest advantage at **{h['best_depth_ft']:,.0f} ft**: "
        f"**{h['best_separation_psi_yr']} psi/yr** separation, "
        f"**{h['best_horizon_separation_psi']} psi** over the {c['horizon_years']:g}-yr horizon.",
        f"- At TD: conventional {h['td_conv_drift_pct_fs_yr']} vs GEA "
        f"{h['td_program_drift_pct_fs_yr']} %FS/yr.",
        (f"- HPHT interval (past the 150 degC / 15,000 psi knees) begins near "
         f"**{h['hpht_interval_top_ft']:,.0f} ft** — the advantage compounds there."
         if h['hpht_interval_top_ft'] else
         "- This well stays below the HPHT stress knees; the advantage is the flat suppression ratio."),
        "- The advantage " + ("**grows with depth**." if h['advantage_grows_with_depth']
                              else "is approximately flat across depth."),
        "",
        "## How to test it",
        "",
        "Twin-gauge bench test (PAPER_2250 LABORATORY tier / PAPER_2256 sec 5): one",
        "conventional quartz gauge, one under GEA SCm-resonance conditioning, at",
        f"matched T/P. Predicted drift ratio: **{sup}**. The simulator's comparison",
        "mode and service-life divergence curves supply the reference data streams.",
        "",
        "## Classification (honest basis)",
        "",
        "DERIVED_HYBRID (PAPER_2149): industry-observed anchors (0.215 %FS/yr",
        "baseline; 150 degC / 15,000 psi stress knees; gradients; FS class; spec",
        "budget) x canonical-GEA suppression from locked primitives. The dressing",
        "coefficients are the template's engineering fit, disclosed — not claimed as",
        "derivations. Record: PAPER_2256. (c) Daniel T. Murphy / GEA Research",
        "Program, AGPL-3.0 + Commercial.",
        "",
    ]
    p = Path(path)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="GEA downhole depth-sweep case study")
    ap.add_argument("--td", type=float, default=DEFAULT_TD_FT, help="total depth, ft")
    ap.add_argument("--points", type=int, default=12, help="depth points in the sweep")
    ap.add_argument("--profile", type=str, default=None, help="well profile CSV (depth_ft,pressure_psi,temp_F)")
    ap.add_argument("--name", type=str, default="case-study well")
    ap.add_argument("--horizon", type=float, default=5.0, help="service horizon, years")
    ap.add_argument("--out", type=str, default="downhole_case_study.md")
    a = ap.parse_args()
    prof = load_well_profile_csv(a.profile) if a.profile else None
    cfg = CaseStudyConfig(td_ft=a.td, n_depth_points=a.points, profile=prof,
                          well_name=a.name, horizon_years=a.horizon)
    out = write_markdown(case_study(cfg), a.out)
    print(f"case study written: {out}")


if __name__ == "__main__":
    _main()
