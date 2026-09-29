# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tool_library — the downhole tool library (v1.7.0 extension).

Generalizes the v1.5.0 gauge-spec discipline to the whole toolstring: a cited
catalog of downhole and surface tools (`ToolSpec`), per-class drift/response
models, a `ToolString` builder, and rating checks against real well
conditions. Every entry declares the telemetry interface it speaks — the
declaration the ports/plug-in layer will implement when the program taps
running logging systems (the live-stream side of the two-stream design).

Honesty rules (Rule 7 / PAPER_2149), same as gauge_specs:
  * Every catalog entry carries a mandatory `source` citation.
  * Where a tool's EXISTENCE and class are verified but its quantitative
    parameters were not published on the fetched pages, the entry is marked
    `PARAMETERS_USER_SUPPLIED`: its numbers are None, and asking it for a
    drift model raises rather than inventing vendor data.
  * The piezoresistive drift model's COEFFICIENTS are a representative
    engineering fit (disclosed as such); its FORM is cited — the ChampionX
    Quartzdyne performance page states verbatim that piezoresistive drift is
    unpredictable and increases exponentially with increasing temperature,
    while quartz drift is predictable and compensatable.

Verified sources (fetched 2026-08-23):
  * GEO PSI product catalog + GEOQ 177 public specification table
    (geopsi.com/products/downhole-gauges/) — Quartzdyne-sensor quartz P/T
    gauges (spec table), GEOP piezoresistive family, GEOVW 250 vibrating-wire,
    GEOXTR 18pt thermocouple input card, GEOPulse fiber optics (DAS/DTS),
    G6 interface card (Modbus RS485 + 4-20mA surface communications),
    PSK downhole telemetry, up to 10 sensors per TEC line.
  * ChampionX Quartzdyne performance page — quartz-vs-piezoresistive drift
    character statement cited above.

Headless-safe: numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .gauge_specs import GAUGE_SPECS, GaugeSpec
from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    conventional_drift,
)

# tool classes
QUARTZ_PT = "QUARTZ_PT_GAUGE"
PIEZO_PT = "PIEZORESISTIVE_PT_GAUGE"
VIBRATING_WIRE = "VIBRATING_WIRE_GAUGE"
THERMOCOUPLE = "THERMOCOUPLE_STRING"
FIBER_DTS = "FIBER_DTS"
SURFACE_INTERFACE = "SURFACE_INTERFACE"

FULLY_SPECIFIED = "FULLY_SPECIFIED"
PARAMETERS_USER_SUPPLIED = "PARAMETERS_USER_SUPPLIED"


@dataclass(frozen=True)
class ToolSpec:
    """A catalog entry: what the tool is, what it measures, what it speaks.

    `source` is mandatory citation prose (Rule 7). `spec_status` says whether
    the numbers are published-and-verified or must come from the user's own
    datasheet. `telemetry_interface` is the declaration the ports layer will
    implement — the tool library names the protocol, the plug-in speaks it.
    """
    name: str
    tool_class: str
    source: str
    measures: Tuple[str, ...]
    telemetry_interface: str
    spec_status: str = FULLY_SPECIFIED
    temp_rating_C: Optional[float] = None
    pressure_rating_psi: Optional[float] = None
    gauge_spec: Optional[GaugeSpec] = None      # quartz tools wrap a v1.5.0 GaugeSpec
    drift_model: Optional[str] = None           # 'quartz_program' | 'quartz_conventional' | 'piezoresistive'
    params: dict = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Drift/response models
# ---------------------------------------------------------------------------
def piezoresistive_drift(temp_c: float,
                         base_drift_pct_fs_yr: float = 0.1,
                         ref_temp_C: float = 25.0,
                         e_fold_C: float = 40.0) -> float:
    """Piezoresistive P/T gauge drift (%FS/yr).

    FORM (cited): ChampionX Quartzdyne performance page — "Piezoresistive
    drift is unpredictable. Piezoresistive drift increases exponentially with
    increasing temperature." Modeled as base * exp((T - T_ref)/e_fold).

    COEFFICIENTS (disclosed, representative engineering fit — NOT vendor
    data): 0.1 %FS/yr at 25 C reference with a 40 C e-folding scale. The
    'unpredictable' character means real units scatter widely around this
    curve; this is a class-typical envelope for simulation, not a spec bound.
    """
    return base_drift_pct_fs_yr * math.exp((temp_c - ref_temp_C) / e_fold_C)


def drift_model_for(tool: ToolSpec) -> Callable[[float, float], float]:
    """Return f(temp_c, pressure_psi) -> drift %FS/yr for a measuring tool.

    Raises for PARAMETERS_USER_SUPPLIED entries (no invented vendor numbers)
    and for non-measuring tools (surface interfaces have no drift model).
    """
    if tool.spec_status == PARAMETERS_USER_SUPPLIED:
        raise ValueError(
            f"{tool.name}: parameters are user-supplied - load your datasheet "
            f"values (Rule 7: the library does not invent vendor numbers)")
    if tool.drift_model == 'quartz_program':
        def f(temp_c, pressure_psi, _s=tool.gauge_spec):
            r = calculate_quartz_transducer_hpht_program(0.0, temp_c, pressure_psi, spec=_s)
            return float(r['value']['drift_pct'])
        return f
    if tool.drift_model == 'quartz_conventional':
        return lambda temp_c, pressure_psi, _s=tool.gauge_spec: conventional_drift(temp_c, pressure_psi, spec=_s)
    if tool.drift_model == 'piezoresistive':
        p = tool.params
        return lambda temp_c, pressure_psi: piezoresistive_drift(
            temp_c, p.get('base_drift_pct_fs_yr', 0.1),
            p.get('ref_temp_C', 25.0), p.get('e_fold_C', 40.0))
    raise ValueError(f"{tool.name}: no drift model (tool class {tool.tool_class})")


# ---------------------------------------------------------------------------
# The catalog — every entry cited
# ---------------------------------------------------------------------------
_GEOPSI = ("GEO PSI product catalog + GEOQ 177 public specification table "
           "(Quartzdyne sensor), geopsi.com/products/downhole-gauges/, fetched 2026-08-23")
_CHAMPIONX = ("ChampionX Quartzdyne performance page, championx.com, fetched 2026-08-23: "
              "quartz drift predictable/compensatable; piezoresistive drift unpredictable, "
              "increases exponentially with temperature")

TOOL_LIBRARY: Dict[str, ToolSpec] = {
    'quartz_pt_program_geoq177_30k': ToolSpec(
        name='quartz_pt_program_geoq177_30k', tool_class=QUARTZ_PT,
        source=_GEOPSI + "; GEA-conditioned leg (canonical suppression, PAPER_2256)",
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='PSK downhole telemetry -> Modbus RS485 + 4-20mA via G6 interface card (GEOQ 177 spec table)',
        temp_rating_C=177.0, pressure_rating_psi=30000.0,
        gauge_spec=GAUGE_SPECS['geoq177_30k'], drift_model='quartz_program'),
    'quartz_pt_conventional_geoq177_30k': ToolSpec(
        name='quartz_pt_conventional_geoq177_30k', tool_class=QUARTZ_PT,
        source=_GEOPSI + "; conventional reference leg (no GEA suppression)",
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='PSK downhole telemetry -> Modbus RS485 + 4-20mA via G6 interface card (GEOQ 177 spec table)',
        temp_rating_C=177.0, pressure_rating_psi=30000.0,
        gauge_spec=GAUGE_SPECS['geoq177_30k'], drift_model='quartz_conventional'),
    'quartz_pt_template_stressed': ToolSpec(
        name='quartz_pt_template_stressed', tool_class=QUARTZ_PT,
        source="22Aug2026 template thread (grok_cce7a73b): stressed-service quartz class, 0.215 %FS/yr baseline",
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='per-site (template does not specify)',
        temp_rating_C=200.0, pressure_rating_psi=30000.0,
        gauge_spec=GAUGE_SPECS['template_generic'], drift_model='quartz_program'),
    'piezoresistive_pt_class': ToolSpec(
        name='piezoresistive_pt_class', tool_class=PIEZO_PT,
        source=_CHAMPIONX + "; GEO PSI GEOP family existence (product catalog). "
               "Model coefficients are a representative engineering fit, DISCLOSED, not vendor data.",
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='per-site (GEOP family: downhole telemetry via TEC, surface via interface card)',
        temp_rating_C=150.0,
        drift_model='piezoresistive',
        params={'base_drift_pct_fs_yr': 0.1, 'ref_temp_C': 25.0, 'e_fold_C': 40.0},
        notes="cited FORM (exponential-in-T, unpredictable); representative coefficients"),
    'vibrating_wire_geovw250': ToolSpec(
        name='vibrating_wire_geovw250', tool_class=VIBRATING_WIRE,
        source="GEO PSI GEOVW 250 product listing (existence + class), geopsi.com product catalog, "
               "fetched 2026-08-23. Quantitative specs NOT published on the fetched page.",
        measures=('pressure_psi',),
        telemetry_interface='per-site (vibrating-wire frequency readout)',
        spec_status=PARAMETERS_USER_SUPPLIED,
        notes="user must supply datasheet parameters before this tool can be simulated"),
    'thermocouple_string_geoxtr18': ToolSpec(
        name='thermocouple_string_geoxtr18', tool_class=THERMOCOUPLE,
        source="GEO PSI GEOXTR 18pt Thermocouple Input Card product listing (existence + 18-point class), "
               "geopsi.com product catalog, fetched 2026-08-23. Per-point specs NOT published on the fetched page.",
        measures=('temperature_F',) * 1,
        telemetry_interface='GEOXTR 18pt thermocouple input card',
        spec_status=PARAMETERS_USER_SUPPLIED,
        params={'points': 18},
        notes="18 temperature points along the string; user supplies accuracy/drift from datasheet"),
    'fiber_dts_geopulse': ToolSpec(
        name='fiber_dts_geopulse', tool_class=FIBER_DTS,
        source="GEO PSI GEOPulse fiber-optics product family (existence + DAS/DTS class), "
               "geopsi.com, fetched 2026-08-23. Spatial/thermal resolution NOT published on the fetched page.",
        measures=('temperature_profile',),
        telemetry_interface='fiber-optic interrogator (GEOPulse surface unit)',
        spec_status=PARAMETERS_USER_SUPPLIED,
        notes="distributed temperature along the whole bore; user supplies interrogator specs"),
    'surface_interface_g6': ToolSpec(
        name='surface_interface_g6', tool_class=SURFACE_INTERFACE,
        source="GEO PSI GEOQ 177 spec table footnote (1): surface communications Modbus RS485 and "
               "4-20mA output via G6 Interface Card; up to 10 sensors per TEC line (footnote 2). Fetched 2026-08-23.",
        measures=(),
        telemetry_interface='Modbus RS485 + 4-20mA analog out; PSK downhole side',
        notes="THE PORT TARGET: the live-stream plug-in layer will speak this interface"),
}


# ---------------------------------------------------------------------------
# Toolstring: composition + rating checks
# ---------------------------------------------------------------------------
@dataclass
class ToolString:
    """A composed string: (md_ft, tool_name) stations, validated against the
    library. This is the object the reconciler will hang live streams on."""
    stations: List[Tuple[float, str]]
    name: str = "toolstring"

    def __post_init__(self):
        for md, tn in self.stations:
            if tn not in TOOL_LIBRARY:
                raise KeyError(f"unknown tool '{tn}' - not in TOOL_LIBRARY")

    def summary(self) -> dict:
        by_class: Dict[str, int] = {}
        for _, tn in self.stations:
            c = TOOL_LIBRARY[tn].tool_class
            by_class[c] = by_class.get(c, 0) + 1
        return {'name': self.name, 'stations': len(self.stations), 'by_class': by_class,
                'interfaces': sorted({TOOL_LIBRARY[tn].telemetry_interface for _, tn in self.stations})}


def rating_check(toolstring: ToolString,
                 profile=None,
                 deviation=None,
                 surface_temp_F: float = 75.0,               # anchor: template surface ambient
                 temp_gradient_F_per_ft: float = 0.018,      # anchor: template geothermal gradient
                 surface_pressure_psi: float = 14.7,         # anchor: 1 atm
                 pressure_gradient_psi_per_ft: float = 0.465 # anchor: industry hydrostatic
                 ) -> List[dict]:
    """Check every station's tool against the well conditions AT that station
    (real profile or gradients; MD->TVD deviation honored). A tool over its
    temperature or pressure rating is flagged - the check that catches a
    177 C gauge hung in a 233 C kick zone before the well does."""
    out = []
    for md, tn in toolstring.stations:
        tool = TOOL_LIBRARY[tn]
        tvd = float(deviation.tvd_of(md)) if deviation is not None else float(md)
        if profile is not None:
            p_psi, t_F = profile.interp(tvd)
        else:
            p_psi = surface_pressure_psi + tvd * pressure_gradient_psi_per_ft
            t_F = surface_temp_F + tvd * temp_gradient_F_per_ft
        t_C = (t_F - 32.0) * 5.0 / 9.0
        over_t = tool.temp_rating_C is not None and t_C > tool.temp_rating_C
        over_p = tool.pressure_rating_psi is not None and p_psi > tool.pressure_rating_psi
        out.append({'md_ft': round(float(md), 0), 'tool': tn,
                    'station_temp_C': round(t_C, 1), 'station_pressure_psi': round(float(p_psi), 0),
                    'temp_rating_C': tool.temp_rating_C, 'pressure_rating_psi': tool.pressure_rating_psi,
                    'over_temp_rating': bool(over_t), 'over_pressure_rating': bool(over_p),
                    'ok': not (over_t or over_p)})
    return out
