# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tool_library - the downhole tool library.

A cited catalog of downhole and surface tools (`ToolSpec`), a `ToolString`
builder, rating checks against real well conditions, and the aging rate of
each measuring tool read from its datasheet. Every entry declares the
telemetry interface it speaks - the declaration the ports layer implements.

Citation rules, the same as gauge_specs:
  * Every catalog entry carries a mandatory `source` citation.
  * Where a tool's EXISTENCE and class are verified but its quantitative
    parameters were not published on the fetched pages, the entry is marked
    `PARAMETERS_USER_SUPPLIED`: its numbers are None, and asking it for an
    aging rate raises rather than inventing vendor data. That includes the
    piezoresistive class: the cited vendor page says its drift is
    unpredictable and grows with temperature, and publishes no rate, so the
    program carries no rate for it until the user supplies one from a datasheet.

Verified sources (fetched 2026-08-23):
  * GEO PSI product catalog + GEOQ 177 public specification table
    (geopsi.com/products/downhole-gauges/) - Quartzdyne-sensor quartz P/T
    gauges (spec table), GEOP piezoresistive family, GEOVW 250 vibrating-wire,
    GEOXTR 18pt thermocouple input card, GEOPulse fiber optics (DAS/DTS),
    G6 interface card (Modbus RS485 + 4-20mA surface communications),
    PSK downhole telemetry, up to 10 sensors per TEC line.
  * ChampionX Quartzdyne performance page - the quartz-vs-piezoresistive
    drift character statement cited above.

Headless-safe: stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .gauge_specs import GAUGE_SPECS, GaugeSpec
from .gauge_aging import aging_rate

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

    `source` is mandatory citation prose. `spec_status` says whether
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
    gauge_spec: Optional[GaugeSpec] = None      # quartz tools wrap a GaugeSpec
    drift_model: Optional[str] = None           # 'datasheet' (the gauge_spec's published rate) | 'user_supplied'
    params: dict = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Drift/response models
# ---------------------------------------------------------------------------
def drift_model_for(tool: ToolSpec) -> Callable[[float, float], float]:
    """Return f(temp_c, pressure_psi) -> aging rate in %FS/yr for a measuring tool.

    The rate is the datasheet's published drift specification (`gauge_aging.aging_rate`);
    the function does not vary it with conditions. Raises for PARAMETERS_USER_SUPPLIED
    entries (no invented vendor numbers) and for non-measuring tools.
    """
    if tool.spec_status == PARAMETERS_USER_SUPPLIED:
        raise ValueError(
            f"{tool.name}: parameters are user-supplied - load your datasheet "
            f"values (the library does not invent vendor numbers)")
    if tool.drift_model == 'datasheet' and tool.gauge_spec is not None:
        return lambda temp_c, pressure_psi, _s=tool.gauge_spec: float(aging_rate(_s, temp_c, pressure_psi)['rate_pct_fs_yr'])
    raise ValueError(f"{tool.name}: no aging rate on record (tool class {tool.tool_class})")


def user_gauge_tool(name: str, spec: GaugeSpec, telemetry_interface: str = 'per-site', tool_class: str = QUARTZ_PT) -> ToolSpec:
    """A tool built from the user's own datasheet (`load_gauge_spec_json`); registered in TOOL_LIBRARY under `name`."""
    t = ToolSpec(name=name, tool_class=tool_class, source=spec.source, measures=('pressure_psi', 'temperature_F'),
                 telemetry_interface=telemetry_interface, temp_rating_C=spec.max_temp_C, pressure_rating_psi=spec.full_scale_psi,
                 gauge_spec=spec, drift_model='datasheet')
    TOOL_LIBRARY[name] = t
    return t


# ---------------------------------------------------------------------------
# The catalog — every entry cited
# ---------------------------------------------------------------------------
_GEOPSI = ("GEO PSI product catalog + GEOQ 177 public specification table "
           "(Quartzdyne sensor), geopsi.com/products/downhole-gauges/, fetched 2026-08-23")
_CHAMPIONX = ("ChampionX Quartzdyne performance page, championx.com, fetched 2026-08-23: "
              "quartz drift predictable/compensatable; piezoresistive drift unpredictable, "
              "increases exponentially with temperature")

TOOL_LIBRARY: Dict[str, ToolSpec] = {
    'quartz_pt_geoq177_30k': ToolSpec(
        name='quartz_pt_geoq177_30k', tool_class=QUARTZ_PT,
        source=_GEOPSI,
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='PSK downhole telemetry -> Modbus RS485 + 4-20mA via G6 interface card (GEOQ 177 spec table)',
        temp_rating_C=177.0, pressure_rating_psi=30000.0,
        gauge_spec=GAUGE_SPECS['geoq177_30k'], drift_model='datasheet'),
    'quartz_pt_geoq177_16k': ToolSpec(
        name='quartz_pt_geoq177_16k', tool_class=QUARTZ_PT,
        source=_GEOPSI,
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='PSK downhole telemetry -> Modbus RS485 + 4-20mA via G6 interface card (GEOQ 177 spec table)',
        temp_rating_C=177.0, pressure_rating_psi=16000.0,
        gauge_spec=GAUGE_SPECS['geoq177_16k'], drift_model='datasheet'),
    'piezoresistive_pt_class': ToolSpec(
        name='piezoresistive_pt_class', tool_class=PIEZO_PT,
        source=_CHAMPIONX + "; GEO PSI GEOP family existence (product catalog). No drift rate is published on the fetched pages.",
        measures=('pressure_psi', 'temperature_F'),
        telemetry_interface='per-site (GEOP family: downhole telemetry via TEC, surface via interface card)',
        temp_rating_C=150.0,
        spec_status=PARAMETERS_USER_SUPPLIED,
        notes="the cited page states the drift character (unpredictable, grows with temperature) and no rate; supply the datasheet before simulating it"),
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
