# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Well assembler: one well object from mixed catalogue pieces (v1.42.0).

Finish-sequence step 2 (independent evaluation, adopted 2026-08-27): twenty-
plus catalogued datasets were ingested but STRANDED - the engine consumed only
depth/pressure/temperature templates. This module assembles a WellAssembly
from the verbatim catalogue entries of ONE site (temperature + trajectory +
density + pressure + attachments), exposes measured-value lookups that REFUSE
outside measured coverage (no silent clamping, no invented values), derives
overburden by integrating the site's own measured density, and emits the
engine's WellProfile so measured wells - not linear gradient templates -
drive simulations (finish-sequence step 3 consumes this).

Discipline unchanged: every number traces to a catalogue entry (which traces
to a public archive via its provenance sidecar); every derivation is labeled
in the object it produces; refusal messages name exactly what is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .downhole_engine import WellProfile
from .profile_catalog import CATALOG

M_TO_FT = 3.280839895            # exact definition: 1 m = 1/0.3048 ft
_TRAPEZOID = getattr(np, "trapezoid", getattr(np, "trapz", None))
# numpy >= 2.0 removed the trapz alias (CI numpy is newer than the authoring
# sandbox's proxy-pinned 2.2.6, which still carried it - the v0.403.0
# remanufacture lesson): prefer the current name, fall back for numpy 1.x.
G_MS2 = 9.80665                  # standard gravity (SI definition)
RHO_FRESHWATER_KGM3 = 1000.0     # labeled derivation input, not a measurement
RHO_SEAWATER_KGM3 = 1025.0       # labeled derivation input (matches catalogue WBD-identity audits)


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


@dataclass
class WellComponent:
    """One catalogue entry playing one role in an assembly."""
    role: str
    entry: str
    channel: str
    unit: str
    depths: np.ndarray          # ascending, NaN-free, paired with values
    values: np.ndarray
    provenance: dict

    def coverage(self) -> Tuple[float, float, int]:
        return float(self.depths[0]), float(self.depths[-1]), int(len(self.depths))


@dataclass
class WellAssembly:
    """A well built from mixed catalogue pieces - the object the engine eats.

    Lookups are STRICT by default: asking for a value outside the component's
    measured depth coverage raises (naming the coverage) instead of silently
    clamping - the assembly never invents data the archive did not measure.
    """
    name: str
    site: str
    water_depth_m: float = 0.0
    components: Dict[str, WellComponent] = field(default_factory=dict)
    attachments: Dict[str, object] = field(default_factory=dict)   # role -> LiveStream (verbatim)

    # -- construction ------------------------------------------------------
    def add(self, role: str, entry_name: str, channel: str) -> None:
        entry = CATALOG[entry_name]
        st = entry.stream()
        if channel not in st.channels:
            raise KeyError(
                f"assembly '{self.name}': entry '{entry_name}' has no channel "
                f"'{channel}' (has: {list(st.channels)})")
        d = np.asarray(st.index, dtype=float)
        v = np.asarray(st.channels[channel].values, dtype=float)
        m = ~(np.isnan(d) | np.isnan(v))
        d, v = d[m], v[m]
        order = np.argsort(d, kind="stable")
        d, v = d[order], v[order]
        self.components[role] = WellComponent(
            role=role, entry=entry_name, channel=channel,
            unit=st.channels[channel].unit, depths=d, values=v,
            provenance=dict(entry.provenance))

    def add_hole_filtered(self, role: str, entry_name: str, channel: str,
                          hole_prefix: str) -> None:
        """Add a component from a MULTI-SITE table entry, keeping only the rows
        whose Hole starts with hole_prefix (v1.70.0, driven by Exp 308 Table T2
        - one verbatim archive, many sites). The filter is processing, not
        archive editing: the entry stays whole; the component records the
        filter in its provenance (Rule 7)."""
        entry = CATALOG[entry_name]
        st = entry.stream()
        holes = st.meta.get("hole")
        if holes is None:
            raise ValueError(
                f"assembly '{self.name}': entry '{entry_name}' carries no "
                "row-aligned hole column - add_hole_filtered needs one")
        if channel not in st.channels:
            raise KeyError(
                f"assembly '{self.name}': entry '{entry_name}' has no channel "
                f"'{channel}' (has: {list(st.channels)})")
        mask = np.array([h.startswith(hole_prefix) for h in holes])
        d = np.asarray(st.index, dtype=float)[mask]
        v = np.asarray(st.channels[channel].values, dtype=float)[mask]
        m = ~(np.isnan(d) | np.isnan(v))
        d, v = d[m], v[m]
        if len(d) == 0:
            raise ValueError(
                f"assembly '{self.name}': no rows of '{entry_name}' match "
                f"hole prefix '{hole_prefix}'")
        order = np.argsort(d, kind="stable")
        prov = dict(entry.provenance)
        prov["component_filter"] = (
            f"rows with Hole startswith '{hole_prefix}' ({int(mask.sum())} of "
            f"{len(holes)} table rows; filter applied by the assembler, "
            "archive untouched)")
        self.components[role] = WellComponent(
            role=role, entry=entry_name, channel=channel,
            unit=st.channels[channel].unit, depths=d[order], values=v[order],
            provenance=prov)

    def attach(self, role: str, entry_name: str) -> None:
        """Carry a whole entry verbatim (strength tables, fluids, elastics...)."""
        self.attachments[role] = CATALOG[entry_name].stream()

    # -- measured lookups (strict) -----------------------------------------
    def value_at(self, role: str, depth_m: float, strict: bool = True) -> float:
        if role not in self.components:
            raise NotImplementedError(
                f"assembly '{self.name}' has no '{role}' component - the site's "
                f"catalogue pieces are {sorted(self.components)}; the assembly "
                "refuses rather than invent one (add a catalogue entry for it)")
        c = self.components[role]
        lo, hi, _ = c.coverage()
        if strict and not (lo <= depth_m <= hi):
            raise ValueError(
                f"assembly '{self.name}': depth {depth_m:g} m is outside the "
                f"MEASURED {role} coverage {lo:g}-{hi:g} m ({c.entry}/{c.channel}) "
                "- strict mode refuses to extrapolate beyond what was measured")
        return float(np.interp(depth_m, c.depths, c.values))

    def temperature_C_at(self, depth_m: float, strict: bool = True) -> float:
        return self.value_at("temperature", depth_m, strict)

    def density_gcc_at(self, depth_m: float, strict: bool = True) -> float:
        return self.value_at("density", depth_m, strict)

    def pressure_kPa_at(self, depth_m: float, strict: bool = True) -> float:
        return self.value_at("pressure", depth_m, strict)

    def tvd_at(self, md_m: float, strict: bool = True) -> float:
        return self.value_at("trajectory", md_m, strict)

    # -- derivations (labeled) --------------------------------------------
    def overburden_kPa(self, depth_m: float) -> float:
        """Vertical stress from the site's OWN measured density: integral of
        rho*g dz over the measured density profile (trapezoid), from the
        shallowest measured station down to depth_m. Labeled derivation -
        the density is measured, the integral is arithmetic."""
        c = self.components.get("density")
        if c is None:
            raise NotImplementedError(
                f"assembly '{self.name}' has no density component - overburden "
                "refuses without measured density")
        lo, hi, _ = c.coverage()
        if not (lo <= depth_m <= hi):
            raise ValueError(
                f"overburden: depth {depth_m:g} m outside measured density "
                f"coverage {lo:g}-{hi:g} m")
        d = np.append(c.depths[c.depths < depth_m], depth_m)
        r = np.append(c.values[c.depths < depth_m],
                      float(np.interp(depth_m, c.depths, c.values)))
        return float(_TRAPEZOID(r * 1000.0 * G_MS2, d) / 1000.0)   # kPa

    # -- the engine bridge -------------------------------------------------
    def to_engine_profile(self, pressure_source: str = "auto",
                          n_points: int = 64) -> WellProfile:
        """Emit the engine's WellProfile from MEASURED components.

        Temperature: required, measured; the profile spans exactly the
        measured temperature coverage (never wider).
        Pressure: 'measured' uses the pressure component (refuses if absent);
        'hydrostatic_freshwater'/'hydrostatic_seawater' are labeled
        derivations (rho*g*(z + water_depth)); 'auto' prefers measured, else
        seawater when the site is submarine, else freshwater.
        The chosen method is recorded in the profile's name (Rule 7)."""
        if "temperature" not in self.components:
            raise NotImplementedError(
                f"assembly '{self.name}' has no measured temperature - the "
                "engine bridge refuses to substitute a gradient template "
                "(that is exactly the stranding this module exists to end)")
        tlo, thi, _ = self.components["temperature"].coverage()
        if pressure_source == "auto":
            if "pressure" in self.components:
                pressure_source = "measured"
            elif self.water_depth_m > 0:
                pressure_source = "hydrostatic_seawater"
            else:
                pressure_source = "hydrostatic_freshwater"
        if pressure_source == "measured":
            plo, phi, _ = self.components["pressure"].coverage()
            lo, hi = max(tlo, plo), min(thi, phi)
            if lo >= hi:
                raise ValueError(
                    f"assembly '{self.name}': measured temperature "
                    f"({tlo:g}-{thi:g} m) and pressure ({plo:g}-{phi:g} m) "
                    "coverages do not overlap - no honest joint profile exists")
        else:
            lo, hi = tlo, thi
        depths = np.linspace(lo, hi, n_points)
        temps_F = [_c_to_f(self.temperature_C_at(z)) for z in depths]
        if pressure_source == "measured":
            press_psi = [self.pressure_kPa_at(z) * 0.1450377377 for z in depths]
        else:
            rho = (RHO_SEAWATER_KGM3 if pressure_source == "hydrostatic_seawater"
                   else RHO_FRESHWATER_KGM3)
            press_psi = [(rho * G_MS2 * (z + self.water_depth_m)) / 1000.0
                         * 0.1450377377 for z in depths]
        return WellProfile(
            depths_ft=[z * M_TO_FT for z in depths],
            pressures_psi=press_psi, temps_F=temps_F,
            name=f"{self.name} [T=measured({self.components['temperature'].entry}); "
                 f"P={pressure_source}]")

    def summary(self) -> dict:
        return {
            "name": self.name, "site": self.site,
            "water_depth_m": self.water_depth_m,
            "components": {r: {"entry": c.entry, "channel": c.channel,
                               "unit": c.unit, "coverage_m": c.coverage()}
                           for r, c in self.components.items()},
            "attachments": {r: getattr(s, "name", "?")
                            for r, s in self.attachments.items()},
        }


# -- generic constructor ----------------------------------------------------
def assemble(name: str, site: str, components: Dict[str, Tuple[str, str]],
             attachments: Optional[Dict[str, str]] = None,
             water_depth_m: float = 0.0) -> WellAssembly:
    """Build a WellAssembly from catalogue pieces: components maps
    role -> (catalogue entry, channel); attachments maps role -> entry."""
    w = WellAssembly(name=name, site=site, water_depth_m=water_depth_m)
    for role, (entry_name, channel) in components.items():
        w.add(role, entry_name, channel)
    for role, entry_name in (attachments or {}).items():
        w.attach(role, entry_name)
    return w


# -- built-in assemblies (the site families the catalogue already holds) ----
def assemble_ktb_hb() -> WellAssembly:
    """KTB main hole (Germany): measured temperature (HLOG246 TMP3) +
    verticality trajectory + BHGM in-situ density + strength attachment -
    the four-file family the catalogue accumulated across entries 12-17."""
    return assemble(
        "KTB-HB", "KTB Hauptbohrung, Windischeschenbach, Germany (continental deep hole)",
        components={
            "temperature": ("ktb_hb_hlog246_temperature", "TMP3"),
            "trajectory": ("ktb_hb_tvd_0_9080_excerpt", "TVD"),
            "density": ("ktb_hb_bhgm_density", "RHO"),
        },
        attachments={
            "strength": "ktb_hb_rockmech_compress",
        })


def assemble_odp_504b() -> WellAssembly:
    """DSDP/ODP 504B (Costa Rica Rift): measured density + sonic velocity
    from the paired 1979 tables, with the fluids and dike-elastics entries
    attached verbatim. No in-situ temperature profile is catalogued for the
    hole, so to_engine_profile HONESTLY REFUSES for this assembly."""
    return assemble(
        "ODP-504B", "DSDP/ODP Hole 504B, Costa Rica Rift flank, Panama Basin",
        components={
            "density": ("dsdp_504b_physical_properties", "WBD"),
            "velocity": ("dsdp_504b_sound_velocity", "Vp"),
        },
        attachments={
            "fluids": "odp_504b_leg137_borehole_fluids",
            "elastics": "odp_504b_dike_elastic_moduli",
        },
        water_depth_m=3460.0)


def assemble_site_1027() -> WellAssembly:
    """ODP Site 1027 (Juan de Fuca): the CORK observatory's 1999 equilibrium
    temperature column drives the assembly; shipboard thermal conductivity
    rides as attachment (the heat-flow-closure pair, entries 20-21)."""
    return assemble(
        "ODP-1027", "ODP Site 1027, Juan de Fuca Ridge flank (CORK observatory)",
        components={
            "temperature": ("odp_1027c_cork_temperature", "t (1999)"),
        },
        attachments={
            "thermal_conductivity": "odp_1027b_thermal_conductivity",
        },
        water_depth_m=2656.2)


def assemble_u1324() -> WellAssembly:
    """IODP U1324 (Gulf of Mexico): MEASURED pore pressure (penetrometer u2,
    PANGAEA 725472) joined by MEASURED in-situ temperature (Exp 308 Table T2,
    the 18 U1324B/C DVTPP+T2P equilibrium stations, hole-filtered from the
    verbatim multi-site table) - the catalogue's first site with BOTH engine
    coordinates measured in the formation. The engine bridge, which honestly
    refused this assembly from v1.42.0 until the temperature column existed,
    now accepts it (v1.70.0). The Table T2 water-depth identity
    (BOH mbsl - BOH mbsf = 1056.8 m on every U1324B row) independently
    re-derives the water_depth_m constant below."""
    w = assemble(
        "IODP-U1324", "IODP Site U1324, Ursa Basin, Gulf of Mexico (overpressure)",
        components={
            "pressure": ("iodp_u1324_pore_pressure", "u2 (hydrostatic fluid pressure)"),
            "overburden_archived": ("iodp_u1324_pore_pressure", "Overb press"),
        },
        water_depth_m=1056.8)
    w.add_hole_filtered("temperature", "gom_308_t2p_insitu", "Tend degC", "U1324")
    return w


def assemble_retama_403h() -> WellAssembly:
    """Retama Ranch #403H (OPERATOR TIER, v1.73.0): the catalogue's first
    modern unconventional horizontal well as an ASSEMBLY - measured trajectory
    (MD -> TVD from the 182-station drift survey, minimum-curvature-verified
    to 0.005 ft) with the plan-tracking table and the connection-by-connection
    drag report attached verbatim. No formation temperature or pressure was
    measured, so to_engine_profile HONESTLY REFUSES - the assembly serves
    trajectory lookups and the operator attachments. Raises KeyError on
    machines without the private operator tier (by design: the tier is
    per-machine and never required)."""
    return assemble(
        "RETAMA-403H",
        "Retama Ranch #403H, Hawkville (Eagle Ford Shale), Webb County, Texas "
        "(Kimmeridge Energy, rig H&P431, KB 746 ft) - OPERATOR TIER, private",
        components={
            "trajectory": ("retama_403h_drift_survey", "TVD (ft)"),
        },
        attachments={
            "plan_tracking": "retama_403h_projections_plan",
            "drag_report": "retama_403h_drag_report",
        })


def reconcile_survey_tvd(entry_a: str, entry_b: str,
                         channel_a: str = "TVD (ft)",
                         channel_b: str = "TVD (ft)",
                         agree_ft: float = 0.1) -> dict:
    """Two-stream reconciliation for SURVEYS (v1.73.0): compare two archives'
    TVD integrations of the same wellbore at their SHARED stations only (no
    interpolation between archives - stations either match in MD or are
    listed as unshared). Returns per-station deltas, the worst divergence and
    where it lives, the count of stations agreeing within `agree_ft`
    (disclosed threshold), and each side's unshared stations. Honest by
    construction: it never averages the two archives into a 'truth' - it
    reports where and how much they disagree and leaves both intact."""
    sa = CATALOG[entry_a].stream()
    sb = CATALOG[entry_b].stream()
    amap = {float(m): float(v) for m, v in zip(sa.index, sa.channels[channel_a].values)
            if v == v}
    bmap = {float(m): float(v) for m, v in zip(sb.index, sb.channels[channel_b].values)
            if v == v}
    shared = sorted(set(amap) & set(bmap))
    if not shared:
        return {"status": "NO_SHARED_STATIONS", "a": entry_a, "b": entry_b}
    deltas = [(m, bmap[m] - amap[m]) for m in shared]
    worst = max(deltas, key=lambda d: abs(d[1]))
    return {
        "status": "OK", "a": entry_a, "b": entry_b,
        "shared_stations": len(shared),
        "only_a": sorted(set(amap) - set(bmap)),
        "only_b": sorted(set(bmap) - set(amap)),
        "agree_within_ft": agree_ft,
        "n_agreeing": sum(1 for _, d in deltas if abs(d) <= agree_ft),
        "worst_delta_ft": worst[1], "worst_at_md_ft": worst[0],
        "first_disagreement_md_ft": next((m for m, d in deltas if abs(d) > agree_ft), None),
        "deltas": deltas,
    }


BUILTIN_ASSEMBLIES = {
    "ktb_hb": assemble_ktb_hb,
    "odp_504b": assemble_odp_504b,
    "site_1027": assemble_site_1027,
    "u1324": assemble_u1324,
    "retama_403h": assemble_retama_403h,   # OPERATOR TIER: raises where the private data is absent
}


# -- finish-sequence step 3: measured wells as engine defaults --------------
BAR_TO_PSI = 14.503773773            # exact: 1 bar = 100 kPa x 0.1450377...

def demo_config(well: str, n_gauges: int = 6, **cfg_kwargs):
    """A SimulatorConfig whose base P/T come from a MEASURED catalogue
    assembly: gauges are hung evenly inside the assembly's measured
    temperature window and the engine interpolates the archived log - the
    0.465 psi/ft / 0.018 F/ft templates never enter. Refuses (via the
    assembly bridge) for sites without measured temperature."""
    from .downhole_engine import SimulatorConfig
    if well not in BUILTIN_ASSEMBLIES:
        raise KeyError(f"unknown assembly '{well}' - built-ins: "
                       f"{sorted(BUILTIN_ASSEMBLIES)}")
    a = BUILTIN_ASSEMBLIES[well]()
    prof = a.to_engine_profile()
    lo, hi = prof.depths_ft[0], prof.depths_ft[-1]
    span = hi - lo
    depths = [lo + span * (i + 1) / (n_gauges + 1) for i in range(n_gauges)]
    return SimulatorConfig(td_ft=hi, sensor_depths_ft=depths, profile=prof,
                           **cfg_kwargs)


def production_live_stream(entry_name: str, well_tag: str,
                           station_md_ft: float) -> Tuple[object, Dict[str, float]]:
    """Adapt a catalogued production time-series into the reconciler's live
    leg: the MEASURED downhole-gauge pressure channel (bar) becomes
    P_raw_psi_S1 (psi, conversion labeled in meta), NaN days are dropped
    with the count recorded, and the station MD must be CALLER-SUPPLIED -
    the excerpt's provenance does not state the gauge depth, and the
    assembler does not invent one."""
    from .ports import LiveStream, StreamChannel
    if station_md_ft is None or station_md_ft <= 0:
        raise NotImplementedError(
            "production_live_stream requires station_md_ft: the catalogued "
            "excerpt does not state the downhole gauge depth, and the "
            "assembler refuses to invent one (supply it from the well's "
            "completion records)")
    src = CATALOG[entry_name].stream()
    chan = f"AVG_DOWNHOLE_PRESSURE[{well_tag}]"
    if chan not in src.channels:
        raise KeyError(f"'{entry_name}' has no channel '{chan}' - wells in "
                       f"this entry: {sorted(set(n[n.find('[')+1:-1] for n in src.channels if '[' in n))}")
    p_bar = np.asarray(src.channels[chan].values, dtype=float)
    t_s = np.asarray(src.index, dtype=float)
    keep = ~np.isnan(p_bar)
    dropped = int((~keep).sum())
    stream = LiveStream(
        name=f"{entry_name}[{well_tag}] measured downhole P (bar->psi x{BAR_TO_PSI})",
        source_format=src.source_format, index_kind='time_s',
        index=t_s[keep],
        channels={'P_raw_psi_S1': StreamChannel(
            name='P_raw_psi_S1', unit='psi',
            values=p_bar[keep] * BAR_TO_PSI)},
        meta={**src.meta,
              'adapter': 'production_live_stream (v1.43.0)',
              'source_channel': chan, 'source_unit': 'bar',
              'conversion': f'psi = bar x {BAR_TO_PSI} (exact)',
              'nan_days_dropped': str(dropped),
              'station_md_ft': f'{station_md_ft:g} (CALLER-SUPPLIED - not in the archived excerpt)'})
    return stream, {'P_raw_psi_S1': float(station_md_ft)}
