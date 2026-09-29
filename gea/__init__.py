"""gea — the GEA Downhole HPHT Quartz-Gauge Simulator.

The gea-program's first packaged industry-application module: a
deep-well (TD ~20,300 ft) six-gauge quartz P/T string with GEA-stabilized
drift, transient events, live animation (matplotlib / optional PyQt6), and
CSV export.

Provenance: ported 2026-08-22 (Daniel GO) from the 22Aug2026 Grok thread
template (grok_cce7a73b); canonical primitives locked, template tuning knobs
renamed to engineering trims per the knob ruling. See README.md and
PAPER_2256.

Quick use (headless):
    from gea import DownholeEngine, SimulatorConfig
    e = DownholeEngine()
    for _ in range(100): e.step()
    print(e.summary()); e.export_csv("run.csv")

Demos (need a display):
    python -m gea.matplotlib_demo
    python -m gea.qt6_downhole_app   (pip install PyQt6)
"""

from .quartz_hpht_extension import (
    calculate_quartz_transducer_hpht_program,
    canonical_suppression,
    conventional_drift,
    drift_comparison,
    PROGRAM_MODEL_AVAILABLE,
)
from .downhole_engine import (
    Sensor,
    SimulatorConfig,
    DownholeEngine,
    WellProfile,
    load_well_profile_csv,
    make_sensor_string,
    DEFAULT_TD_FT,
    DEFAULT_SENSOR_DEPTHS_FT,
)
from .service_life import (
    ServiceLifeConfig,
    ServiceLifeSimulator,
)
from .telemetry import (
    TelemetryConfig,
    TelemetryRecorder,
)
from .case_study import (
    CaseStudyConfig,
    case_study,
    depth_sweep,
    write_markdown,
)
from .gauge_specs import (
    GaugeSpec,
    GAUGE_SPECS,
    load_gauge_spec_json,
)
from .deviation import (
    DeviationSurvey,
    load_deviation_csv,
)
from .downhole_engine import run_batch
from .tool_library import (
    ToolSpec,
    TOOL_LIBRARY,
    ToolString,
    drift_model_for,
    piezoresistive_drift,
    rating_check,
)
from .ports import (
    LiveStream,
    StreamChannel,
    PortSpec,
    PORT_REGISTRY,
    ingest,
    read_historian_csv,
    read_las,
    register_port,
)
from .reconciler import (
    Reconciler,
    ReconcilerConfig,
    auto_station_map,
)
from .follower import (
    FollowerPoll,
    HistorianFollower,
)
from .modbus import (
    PYMODBUS_AVAILABLE,
    RegisterMap,
    load_register_map,
)
from .well_assembler import (
    WellAssembly, WellComponent, assemble, assemble_ktb_hb, assemble_odp_504b,
    assemble_site_1027, assemble_u1324, BUILTIN_ASSEMBLIES,
    demo_config, production_live_stream,
)
from .gamma import (
    find_gr_channels, shale_volume, formation_flags, gamma_report, gamma_entries,
)
from .bench import (
    bench_analysis, bench_selftest,
)
from .operator_app import (
    OperatorSession, launch_operator_app,
)
from .profile_catalog import (
    CATALOG,
    CatalogEntry,
    PROFILE_SOURCES,
    las_to_profile,
    read_temperature_csv,
    read_survey_csv,
    read_core_csv,
    read_production_csv,
    read_ktb_dat,
    read_ktb_table,
    read_pangaea_txt,
)

__version__ = "1.90.0"
__all__ = [
    "calculate_quartz_transducer_hpht_program", "canonical_suppression",
    "conventional_drift", "drift_comparison",
    "PROGRAM_MODEL_AVAILABLE", "Sensor", "SimulatorConfig", "DownholeEngine",
    "WellProfile", "load_well_profile_csv", "make_sensor_string",
    "ServiceLifeConfig", "ServiceLifeSimulator",
    "TelemetryConfig", "TelemetryRecorder",
    "CaseStudyConfig", "case_study", "depth_sweep", "write_markdown",
    "GaugeSpec", "GAUGE_SPECS", "load_gauge_spec_json",
    "DeviationSurvey", "load_deviation_csv", "run_batch",
    "ToolSpec", "TOOL_LIBRARY", "ToolString", "drift_model_for",
    "piezoresistive_drift", "rating_check",
    "LiveStream", "StreamChannel", "PortSpec", "PORT_REGISTRY",
    "ingest", "read_historian_csv", "read_las", "register_port",
    "Reconciler", "ReconcilerConfig", "auto_station_map",
    "FollowerPoll", "HistorianFollower",
    "PYMODBUS_AVAILABLE", "RegisterMap", "load_register_map",
    "CATALOG", "CatalogEntry", "PROFILE_SOURCES", "las_to_profile", "read_temperature_csv", "read_survey_csv", "read_core_csv", "read_production_csv", "read_ktb_dat", "read_ktb_table", "read_pangaea_txt",
    "DEFAULT_TD_FT", "DEFAULT_SENSOR_DEPTHS_FT",
    "strata_join",
    "well_assembler", "gamma", "bench",
    "operator_app", "acceptance_tests",
    "reconcile_survey_tvd",
    "earth_model",
    "forward_model",
    "inverse_engine",
    "survey_view",
    "correlation",
    "blind_harness",
    "segy",
    "project",
]
