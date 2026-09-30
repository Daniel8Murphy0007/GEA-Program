# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""quartz_hpht_extension - the gauge aging models.

Two aging models evaluated at station pressure and temperature: the conventional
datasheet model (baseline drift scaled by thermal and pressure factors with the
template knees and exponents) and the program aging model, which multiplies the
conventional model by a fixed suppression composition of three engineering
constants (K_MEX = 25/12, PHI_RES = 0.84, F_TRZ = 0.1; ratio 0.9686). The
suppression composition is an engineering model with no field validation on
record; the model card says so, and the drift evaluation uses the band between
the two models, never the program model alone. `k_structural_trim` and
`phi_coupling_trim` (default 1.0) are instrument-tuning gains. Industry anchors
carry inline source comments.
"""

from __future__ import annotations

# Engineering constants of the program aging model.
# They are settings, not measurements; the model card labels this model as
# having no field validation on record.
_K_MEX = 25.0 / 12.0        # structural factor (engineering constant)
_PHI_RES = 0.84             # resonance factor (engineering constant)
_F_TRZ = 0.1                # vacuum-stability factor (engineering constant)
_U_I = 2.75e-7              # coupling constant (engineering constant)
PROGRAM_MODEL_AVAILABLE = True


def canonical_suppression(k_structural_trim: float = 1.0,
                          phi_coupling_trim: float = 1.0) -> float:
    """The program's drift-suppression composition, constants locked.

    suppression = vacuum * structural * resonance. The three factors' linear
    coefficients are an engineering fit (classification DERIVED_HYBRID); the
    three constants they act on are fixed settings of the model, never tuned
    at run time. Trims are external instrument gains, applied multiplicatively
    and reported as such.
    """
    vacuum_stab = 0.58 + 0.32 * (1.0 - _F_TRZ)          # F_TRZ = 0.1 (locked)
    structural = 0.52 + 0.38 * _K_MEX                   # K_MEX = 25/12 (locked)
    resonance = 0.68 + 0.27 * _PHI_RES                  # Phi_res = 0.84 (locked)
    return vacuum_stab * structural * resonance * float(k_structural_trim) * float(phi_coupling_trim)


def calculate_quartz_transducer_hpht_program(depth_m: float,
                                          temp_c: float,
                                          pressure_psi: float,
                                          k_structural_trim: float = 1.0,
                                          phi_coupling_trim: float = 1.0,
                                          spec=None) -> dict:
    """Physics-informed quartz HPHT drift model (GEA-stabilized).

    Returns the template's rich dictionary shape, current-API values.
    Anchors (inline, per charter): 0.215 %FS/yr typical good-quartz baseline
    (industry spec class); 150 C thermal knee / 15,000 psi pressure knee with
    exponents 1.15 / 0.9 (template engineering fit); clip band 0.035-0.48 %FS/yr
    (physical plausibility bounds, template).

    `spec`: an optional GaugeSpec (gauge_specs) replacing the
    template anchors with a cited datasheet's baseline/knees/exponents. The
    clip band scales proportionally with the baseline so a datasheet bound
    ~20x below the template baseline is not floored by template-scaled clips.
    With spec=None every number is bit-identical to v1.0-1.4.
    """
    base_drift = 0.215          # anchor: %FS/yr typical good-quartz baseline (industry)
    knee_C, knee_psi = 150.0, 15000.0   # anchors: industry knees (template; ChampionX confirms >=150 C focus)
    exp_T, exp_P = 1.15, 0.9            # template engineering fit
    if spec is not None:
        base_drift = float(spec.baseline_drift_pct_fs_yr)
        knee_C = float(spec.thermal_knee_C)
        knee_psi = float(spec.pressure_knee_psi)
        exp_T = float(spec.thermal_exponent)
        exp_P = float(spec.pressure_exponent)

    thermal_stress = max(0.0, (temp_c - knee_C) / 80.0) ** exp_T
    pressure_stress = max(0.0, (pressure_psi - knee_psi) / 5000.0) ** exp_P

    suppression = canonical_suppression(k_structural_trim, phi_coupling_trim)

    drift = base_drift * (1.0 + 0.55 * thermal_stress + 0.35 * pressure_stress) / suppression
    clip_scale = base_drift / 0.215     # clip band scales with the baseline (template-exact at 0.215)
    drift = min(max(drift, 0.035 * clip_scale), 0.48 * clip_scale)

    expected_temp_c = 15.0 + (depth_m / 1000.0) * 29.5   # anchor: ~29.5 C/km geothermal gradient
    hydrostatic_psi = depth_m * 3.28084 * 0.465          # anchor: 0.465 psi/ft gradient

    return {
        "value": {
            "drift_pct": round(drift, 4),
            "stability_factor": round(base_drift / drift, 3),
            "suppression": round(suppression, 4),
            "rho_SCm_over_rho_UA": _F_TRZ,
            "U_i": _U_I,
            "K_MEX_canonical": _K_MEX,
            "Phi_res_canonical": _PHI_RES,
            "k_structural_trim": float(k_structural_trim),
            "phi_coupling_trim": float(phi_coupling_trim),
            "expected_temp_c": round(expected_temp_c, 1),
            "hydrostatic_psi": round(hydrostatic_psi, 0),
            "live": PROGRAM_MODEL_AVAILABLE,
            "gauge_spec": spec.name if spec is not None else "template_generic (default)",
        },
        "classification": "DERIVED_HYBRID: industry baseline x the program's suppression composition",
        "notes": "program aging model; the two trims are the only run-time settings",
    }


def conventional_drift(temp_c: float, pressure_psi: float, spec=None) -> float:
    """Conventional-gauge drift: SAME baseline and stress dressing as the GEA
    leg (from the template anchors or the given GaugeSpec), NO GEA suppression
    (suppression = 1). The comparison-mode reference leg.
    """
    base_drift = 0.215   # anchor: same industry baseline as the program-model leg
    knee_C, knee_psi, exp_T, exp_P = 150.0, 15000.0, 1.15, 0.9
    if spec is not None:
        base_drift = float(spec.baseline_drift_pct_fs_yr)
        knee_C = float(spec.thermal_knee_C)
        knee_psi = float(spec.pressure_knee_psi)
        exp_T = float(spec.thermal_exponent)
        exp_P = float(spec.pressure_exponent)
    thermal_stress = max(0.0, (temp_c - knee_C) / 80.0) ** exp_T
    pressure_stress = max(0.0, (pressure_psi - knee_psi) / 5000.0) ** exp_P
    drift = base_drift * (1.0 + 0.55 * thermal_stress + 0.35 * pressure_stress)
    clip_scale = base_drift / 0.215
    return min(max(drift, 0.035 * clip_scale), 0.48 * clip_scale)


def drift_comparison(depth_m: float, temp_c: float, pressure_psi: float,
                     k_structural_trim: float = 1.0,
                     phi_coupling_trim: float = 1.0,
                     spec=None) -> dict:
    """Twin-gauge comparison at matched T/P: GEA-stabilized vs conventional.

    The module's testable claim: away from the clip band, the
    conventional/program drift ratio EQUALS the suppression composition -
    1.0324 at unity trims. This function is the simulation side of the
    twin-gauge bench test (gea/BENCH_TEST_PROTOCOL.md).
    """
    uq = calculate_quartz_transducer_hpht_program(
        depth_m, temp_c, pressure_psi, k_structural_trim, phi_coupling_trim, spec=spec)
    conv = conventional_drift(temp_c, pressure_psi, spec=spec)
    uqd = uq["value"]["drift_pct"]
    sup = uq["value"]["suppression"]
    return {
        "drift_pct": uqd,
        "conventional_drift_pct": round(conv, 4),
        "measured_ratio": round(conv / uqd, 4) if uqd > 0 else None,
        "predicted_ratio_suppression": sup,
        "clip_active": bool(conv >= 0.48 or uqd <= 0.035),
        "note": "measured_ratio == suppression exactly when neither leg clips",
    }
