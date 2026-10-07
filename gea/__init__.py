# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gea - GEA-Program: the downhole gauge monitoring program.

A described well (gradients or a real profile, a toolstring of cited
instruments), the data that comes back from it (files or live ports), and
the report family that judges the two against each other: data quality,
gauge drift against each instrument's datasheet aging rate, well tests,
alarms, shut-ins and build-ups, instruments and certificates, the monthly
service note. Every number on a report names the rule or the datasheet it
came from; the program adds no physics of its own.

Quick use (headless):
    from gea import DownholeEngine, SimulatorConfig
    e = DownholeEngine()
    for _ in range(100): e.step()
    print(e.summary()); e.export_csv("run.csv")

The dashboard: gea workspace --path C:\\site --action init --name "Pad 3"; gea serve --workspace C:\\site
"""

from .gauge_aging import aging_rate, rate_psi_yr, accuracy_psi
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
from .gauge_specs import (
    GaugeSpec,
    GAUGE_SPECS,
    DEFAULT_SPEC,
    get_spec,
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
    user_gauge_tool,
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
from .opcua_port import ASYNCUA_AVAILABLE, OpcUaTap          # registers the 'opcua' port at import
from .mqtt_port import PAHO_AVAILABLE, MqttTap                # registers the 'mqtt' port at import
from .wits0 import Wits0Tap, SERIAL_AVAILABLE                  # registers the 'wits0' port at import
from .witsml import WitsmlTap                                  # registers the 'witsml' port at import
from .well_assembler import (
    WellAssembly, WellComponent, assemble, assemble_ktb_hb, assemble_odp_504b,
    assemble_site_1027, assemble_u1324, BUILTIN_ASSEMBLIES,
    demo_config, production_live_stream,
)
from .gamma import (
    find_gr_channels, shale_volume, formation_flags, gamma_report, gamma_entries,
)
from .seismic import (
    Trace as SeismicTrace, read_mseed, write_mseed, read_sac, write_sac, read_any as read_seismic, fdsn_fetch, fdsn_stations,
    welch_psd, spectrogram, persistent_lines, band_power,
)
from .seismic_detect import Source as SeismicSource, detectability_test, load_sources_csv, haversine_km, DRILLING_BAND_HZ
from .seismic_response import read_stationxml, remove_response, transfer as response_transfer, fdsn_stationxml
from .seismic_array import Sensor as SeismicSensor, load_sensors_csv, beam as array_beam, array_detectability, intersect_backazimuths, locate_from_lags
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

__version__ = "0.8.1"
__all__ = [
    "aging_rate", "rate_psi_yr", "accuracy_psi",
    "Sensor", "SimulatorConfig", "DownholeEngine",
    "WellProfile", "load_well_profile_csv", "make_sensor_string",
    "ServiceLifeConfig", "ServiceLifeSimulator",
    "TelemetryConfig", "TelemetryRecorder",
    "GaugeSpec", "GAUGE_SPECS", "DEFAULT_SPEC", "get_spec", "load_gauge_spec_json",
    "DeviationSurvey", "load_deviation_csv", "run_batch",
    "ToolSpec", "TOOL_LIBRARY", "ToolString", "drift_model_for",
    "user_gauge_tool", "rating_check",
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
