# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Headless CLI for GEA-Program.

    python -m gea run          --steps 200 --out run.csv
    python -m gea service-life --years 5 --out curves.csv
    python -m gea telemetry    --hours 24 --seed 11 --out field.csv
    python -m gea bench        --gauge-csv A.csv --spec geoq177_30k

Shared options (all subcommands): --gauges N, --td FT, --profile CSV,
--spec PRESET|JSON, --kickoff FT --inclination DEG (deviation).
No display needed anywhere; every subcommand writes a file and prints a
one-line summary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__


def _add_well_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--well", type=str, default=None,
                   help="MEASURED catalogue assembly (ktb_hb, site_1027, ...): base P/T "
                        "come from the archived well, not gradient templates; overrides --td/--profile")
    p.add_argument("--td", type=float, default=None, help="total depth (MD), ft")
    p.add_argument("--gauges", type=int, default=None, help="number of gauges (evenly spaced)")
    p.add_argument("--profile", type=str, default=None, help="well profile CSV (depth_ft,pressure_psi,temp_F; TVD-indexed)")
    p.add_argument("--spec", type=str, default=None, help="gauge spec: preset name or a datasheet JSON path")
    p.add_argument("--kickoff", type=float, default=None, help="deviation: kickoff MD, ft")
    p.add_argument("--inclination", type=float, default=None, help="deviation: tangent inclination, deg from vertical")


def _build_config(a):
    from . import (SimulatorConfig, load_well_profile_csv, make_sensor_string,
                   GAUGE_SPECS, load_gauge_spec_json, DEFAULT_TD_FT)
    from .deviation import DeviationSurvey
    if getattr(a, "well", None):
        from . import demo_config, GAUGE_SPECS as _GS
        kw2 = {}
        if a.spec:
            kw2["gauge_spec"] = (_GS[a.spec] if a.spec in _GS
                                 else load_gauge_spec_json(a.spec))
        cfg = demo_config(a.well, n_gauges=(a.gauges or 6), **kw2)
        print(f"[well] {a.well}: profile '{cfg.profile.name}' - "
              f"{len(cfg.sensor_depths_ft)} gauges inside the measured window "
              f"{cfg.profile.depths_ft[0]:.0f}-{cfg.profile.depths_ft[-1]:.0f} ft")
        return cfg
    td = a.td if a.td is not None else DEFAULT_TD_FT
    kw = {"td_ft": td}
    if a.gauges is not None:
        kw["sensor_depths_ft"] = make_sensor_string(a.gauges, td_ft=td)
    if a.profile:
        kw["profile"] = load_well_profile_csv(a.profile)
    if a.spec:
        kw["gauge_spec"] = (GAUGE_SPECS[a.spec] if a.spec in GAUGE_SPECS
                            else load_gauge_spec_json(a.spec))
    if a.kickoff is not None or a.inclination is not None:
        if a.kickoff is None or a.inclination is None:
            raise SystemExit("--kickoff and --inclination must be given together")
        kw["deviation"] = DeviationSurvey.from_kickoff(a.kickoff, a.inclination, td)
    return SimulatorConfig(**kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gea",
                                 description="GEA Downhole Simulator - headless CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the engine, export the P/T history CSV")
    _add_well_args(p_run)
    p_run.add_argument("--steps", type=int, default=200)
    p_run.add_argument("--out", type=str, default="downhole_run.csv")

    p_sl = sub.add_parser("service-life", help="integrate each station's datasheet aging rate over years; years to the error budget")
    _add_well_args(p_sl)
    p_sl.add_argument("--years", type=float, default=5.0)
    p_sl.add_argument("--recal", type=float, default=None, help="recalibration interval, years")
    p_sl.add_argument("--seed", type=int, default=None)
    p_sl.add_argument("--out", type=str, default="service_life.csv")

    p_tm = sub.add_parser("telemetry", help="field-telemetry acquisition, export historian CSV")
    _add_well_args(p_tm)
    p_tm.add_argument("--hours", type=float, default=24.0)
    p_tm.add_argument("--seed", type=int, default=None)
    p_tm.add_argument("--out", type=str, default="telemetry.csv")

    p_in = sub.add_parser("ingest", help="ingest a live-stream file through a port (read-only)")
    p_in.add_argument("--file", type=str, required=True, help="source file (historian CSV or LAS)")
    p_in.add_argument("--port", type=str, default="historian_csv",
                      help="port name from PORT_REGISTRY (historian_csv | las2 | site plug-ins)")

    p_w = sub.add_parser("wells", help="list the MEASURED catalogue assemblies (--well targets)")
    p_rep = sub.add_parser("report", help="generate the client survey report (Part 7)")
    p_rep.add_argument("--out", default="survey_report", help="output directory")

    sub.add_parser("operator", help="launch the operator GUI (requires PyQt6+matplotlib)")

    sub.add_parser("accept", help="run the simulator ACCEPTANCE suite (product gate)")

    p_b = sub.add_parser("bench", help="the bench record: a gauge's measured drift at a held setpoint against its datasheet (BENCH_TEST_PROTOCOL.md), or --selftest")
    p_b.add_argument("--gauge-csv", type=str, default=None, help="historian CSV of the gauge under test (timestamp or time_s + a pressure column)")
    p_b.add_argument("--reference-csv", type=str, default=None, help="historian CSV of the reference standard at the same setpoint (optional; subtracted)")
    p_b.add_argument("--spec", type=str, default=None, help="the gauge's datasheet: preset name or JSON path (default: the GEOQ 177 30k entry)")
    p_b.add_argument("--serial", type=str, default="", help="the instrument's serial number, for the record")
    p_b.add_argument("--certificate", type=str, default="", help="its calibration certificate id, for the record")
    p_b.add_argument("--k-sigma", type=float, default=2.0)
    p_b.add_argument("--out", type=str, default=None, help="append the record as a JSON line here (a site's bench register)")
    p_b.add_argument("--selftest", action="store_true", help="SIMULATION_SELF_TEST: verify the analysis arithmetic on synthetic series")

    p_g = sub.add_parser("gamma", help="lithology-from-GR on a catalogue entry (measured API curves only)")
    p_g.add_argument("--catalog", type=str, default=None, help="catalogue entry (omit to list gamma-bearing entries)")
    p_g.add_argument("--channel", type=str, default=None)
    p_g.add_argument("--cutoff", type=float, default=0.5)
    p_g.add_argument("--gr-clean", type=float, default=None)
    p_g.add_argument("--gr-shale", type=float, default=None)

    p_rc = sub.add_parser("reconcile", help="two-stream reconciliation: live file vs closed-stream prediction")
    _add_well_args(p_rc)
    p_rc.add_argument("--file", type=str, default=None, help="live-stream file (historian CSV)")
    p_rc.add_argument("--live-catalog", type=str, default=None,
                      help="catalogue production entry as the live leg (measured downhole P)")
    p_rc.add_argument("--live-well", type=str, default=None, help="well tag inside the entry (e.g. 15/9-F-12)")
    p_rc.add_argument("--station-md", type=float, default=None,
                      help="gauge MD ft for the catalogue live leg (caller-supplied; not in the archived excerpt)")
    p_rc.add_argument("--port", type=str, default="historian_csv")

    p_cr = sub.add_parser("client-report", help="client-facing report family in scope-of-work outline "
                                                "(drift: Gauge Drift & Reconciliation Report)")
    _add_well_args(p_cr)
    p_cr.add_argument("--report", type=str, default="drift", choices=["drift", "accuracy"],
                      help="which report to generate (drift: Gauge Drift & Reconciliation; "
                           "accuracy: Accuracy Statement, MAPE at 90 pct CI)")
    p_cr.add_argument("--ci", type=float, default=0.90, help="accuracy: confidence level")
    p_cr.add_argument("--file", type=str, default=None, help="live-stream file (historian CSV)")
    p_cr.add_argument("--live-catalog", type=str, default=None,
                      help="catalogue production entry as the live leg (measured downhole P)")
    p_cr.add_argument("--live-well", type=str, default=None, help="well tag inside the entry (e.g. 15/9-F-12)")
    p_cr.add_argument("--station-md", type=float, default=None,
                      help="gauge MD ft for the catalogue live leg (caller-supplied; not in the archived excerpt)")
    p_cr.add_argument("--port", type=str, default="historian_csv")
    p_cr.add_argument("--name", type=str, default=None, help="well name printed on the report")
    p_cr.add_argument("--out", type=str, default="client_report", help="output directory")

    p_dm = sub.add_parser("drift-monitor", help="drift evaluation as a scheduled job with an evaluation log, "
                                                "staleness state and re-fit change log")
    _add_well_args(p_dm)
    p_dm.add_argument("--action", type=str, default="evaluate", choices=["evaluate", "status", "approve"])
    p_dm.add_argument("--log-dir", type=str, default="drift_monitor", help="record directory (JSON lines + state)")
    p_dm.add_argument("--now", type=str, default=None, help="clock for this run, ISO UTC (default: wall clock)")
    p_dm.add_argument("--force", action="store_true", help="evaluate even if not due")
    p_dm.add_argument("--file", type=str, default=None, help="live-stream file (historian CSV)")
    p_dm.add_argument("--live-catalog", type=str, default=None)
    p_dm.add_argument("--live-well", type=str, default=None)
    p_dm.add_argument("--station-md", type=float, default=None)
    p_dm.add_argument("--port", type=str, default="historian_csv")
    p_dm.add_argument("--name", type=str, default=None, help="well name on the record")
    p_dm.add_argument("--entry", type=str, default=None, help="approve: change-log entry id")
    p_dm.add_argument("--approver", type=str, default=None, help="approve: approver name and role")
    p_dm.add_argument("--decision", type=str, default="APPLIED", choices=["APPLIED", "REJECTED"])
    p_dm.add_argument("--note", type=str, default="")
    p_dm.add_argument("--report", type=str, default=None, help="evaluate: also write the drift report to this directory")

    p_wt = sub.add_parser("well-test", help="well-test validation: stable-period detection with config-file "
                                            "criteria, accept/reject reason codes, approval trail, report")
    p_wt.add_argument("--live-catalog", type=str, default=None, help="catalogue production entry")
    p_wt.add_argument("--live-well", type=str, default=None, help="well tag inside the entry (e.g. 15/9-F-12)")
    p_wt.add_argument("--criteria", type=str, default=None, help="criteria JSON (client-agreed logic)")
    p_wt.add_argument("--write-default-criteria", type=str, default=None, help="write the default criteria file here and exit")
    p_wt.add_argument("--record-dir", type=str, default="well_tests", help="approval trail directory")
    p_wt.add_argument("--approve", type=str, default=None, help="test id to approve/reject")
    p_wt.add_argument("--approver", type=str, default=None)
    p_wt.add_argument("--level", type=int, default=1)
    p_wt.add_argument("--decision", type=str, default="APPROVED", choices=["APPROVED", "REJECTED", "CORRECTED"])
    p_wt.add_argument("--note", type=str, default="")
    p_wt.add_argument("--now", type=str, default=None, help="clock, ISO UTC")
    p_wt.add_argument("--out", type=str, default=None, help="write the Well Test Validation Report to this directory")

    p_al = sub.add_parser("alarms", help="alarm engine: definitions, state machine with deadband/on-delay, "
                                         "event log, alarm-management KPIs, Alarm & Event report")
    _add_well_args(p_al)
    p_al.add_argument("--file", type=str, default=None, help="historian CSV to process")
    p_al.add_argument("--port", type=str, default="historian_csv")
    p_al.add_argument("--definitions", type=str, default=None, help="alarm definitions JSON (client settings)")
    p_al.add_argument("--write-default-definitions", type=str, default=None,
                      help="write catalogue-derived defaults (over-range + quality) for --file here and exit")
    p_al.add_argument("--event-log", type=str, default=None, help="append-only event log (JSON lines)")
    p_al.add_argument("--positions", type=int, default=1, help="operator positions for the rate KPIs")
    p_al.add_argument("--ack", type=str, default=None, help="acknowledge these alarm ids (comma-separated) after processing")
    p_al.add_argument("--ack-all", action="store_true", help="acknowledge every unacknowledged alarm after processing")
    p_al.add_argument("--shelve", type=str, default=None, help="shelve these alarm ids (comma-separated) after processing")
    p_al.add_argument("--shelve-hours", type=float, default=None, help="shelf expiry in hours (default: until unshelved)")
    p_al.add_argument("--unshelve", type=str, default=None, help="unshelve these alarm ids (comma-separated)")
    p_al.add_argument("--note", type=str, default="", help="reason recorded on the operator action")
    p_al.add_argument("--operator", type=str, default="")
    p_al.add_argument("--now", type=str, default=None, help="clock for operator actions, ISO UTC")
    p_al.add_argument("--name", type=str, default=None)
    p_al.add_argument("--out", type=str, default=None, help="write the Alarm & Event report to this directory")

    p_mc = sub.add_parser("model-cards", help="one model card per model, generated from the live objects")
    p_mc.add_argument("--out", type=str, default="model_cards", help="output directory")
    p_mc.add_argument("--monitor-log-dir", type=str, default=None, help="drift-monitor record directory for re-fit history")

    p_sf = sub.add_parser("store-forward", help="store-and-forward simulation: outages, 72 h edge buffer, "
                                                "chronological rate-controlled replay, gap report, Data Resilience report")
    p_sf.add_argument("--file", type=str, required=True, help="historian CSV (the field samples)")
    p_sf.add_argument("--port", type=str, default="historian_csv")
    p_sf.add_argument("--outage", type=str, action="append", default=[], help="'START,END' ISO UTC; repeatable")
    p_sf.add_argument("--capacity-hours", type=float, default=72.0)
    p_sf.add_argument("--replay-rate", type=float, default=50.0, help="records per second on replay")
    p_sf.add_argument("--edge-latency", type=float, default=2.0, help="seconds from sample to edge arrival")
    p_sf.add_argument("--tags", type=str, default=None, help="comma-separated tag prefixes to include (default all)")
    p_sf.add_argument("--name", type=str, default=None)
    p_sf.add_argument("--out", type=str, default=None, help="write the Data Resilience report to this directory")

    p_cfg = sub.add_parser("config", help="version-controlled configuration: commit, history, export, rollback")
    p_cfg.add_argument("--store", type=str, default="config_store")
    p_cfg.add_argument("--action", type=str, default="summary", choices=["commit", "history", "export", "rollback", "summary"])
    p_cfg.add_argument("--name", type=str, default=None, help="configuration name (e.g. well_test_criteria)")
    p_cfg.add_argument("--file", type=str, default=None, help="commit: JSON file to commit; export: destination path")
    p_cfg.add_argument("--author", type=str, default=None)
    p_cfg.add_argument("--note", type=str, default="")
    p_cfg.add_argument("--version", type=int, default=None, help="export/rollback: version number")
    p_cfg.add_argument("--now", type=str, default=None)

    p_sb = sub.add_parser("sbom", help="software bill of materials from the running environment (eight fields)")
    p_sb.add_argument("--out", type=str, default="sbom")

    p_sla = sub.add_parser("sla-report", help="monthly SLA measurement from the program's records")
    p_sla.add_argument("--month", type=str, required=True, help="YYYY-MM")
    p_sla.add_argument("--monitor-log-dir", type=str, default=None)
    p_sla.add_argument("--store-forward-json", type=str, default=None, help="Data Resilience machine JSON")
    p_sla.add_argument("--alarm-log", type=str, default=None)
    p_sla.add_argument("--well-test-dir", type=str, default=None)
    p_sla.add_argument("--accuracy-json", type=str, default=None)
    p_sla.add_argument("--config-store", type=str, default=None)
    p_sla.add_argument("--name", type=str, default=None)
    p_sla.add_argument("--out", type=str, default="sla_report")

    p_fat = sub.add_parser("fat-sat", help="render the acceptance suite as a FAT or SAT protocol with signature block")
    p_fat.add_argument("--kind", type=str, default="FAT", choices=["FAT", "SAT"])
    p_fat.add_argument("--sections", type=str, default=None, help="comma-separated section keys (default: client set)")
    p_fat.add_argument("--name", type=str, default=None)
    p_fat.add_argument("--out", type=str, default="fat_sat")

    p_db = sub.add_parser("dashboard", help="run the client report family for the given wells and build the web view (index.html)")
    p_db.add_argument("--catalog-well", type=str, action="append", default=[],
                      help="'ENTRY:WELL_TAG:STATION_MD_FT' (repeatable), e.g. volve_f12_f14_production_excerpt:15/9-F-12:10000")
    p_db.add_argument("--file", type=str, action="append", default=[], help="historian CSV as a well (repeatable)")
    p_db.add_argument("--td", type=float, default=None, help="total depth (MD), ft, for the well model")
    p_db.add_argument("--spec", type=str, default=None, help="gauge datasheet preset or JSON")
    p_db.add_argument("--outage", type=str, action="append", default=[], help="'START,END' for the resilience simulation on file wells")
    p_db.add_argument("--month", type=str, default=None, help="YYYY-MM for the SLA report")
    p_db.add_argument("--criteria", type=str, default=None, help="well-test criteria JSON")
    p_db.add_argument("--monitor-root", type=str, default=None, help="drift-monitor record root (one dir per well)")
    p_db.add_argument("--sat", action="store_true", help="also run and include the SAT protocol")
    p_db.add_argument("--name", type=str, default="site")
    p_db.add_argument("--out", type=str, default="dashboard")

    for name, helptxt in (("opcua", "OPC UA client port: read once or subscribe; record; replay a recording offline"),
                          ("mqtt", "MQTT subscriber port (3.1.1/5.0; number | json | Sparkplug B): run; record; replay offline"),
                          ("wits0", "WITS Level 0 port (drill floor: mud logger / EDR) over TCP connect, TCP listen or serial: run; record; replay offline"),
                          ("witsml", "WITSML 1.4.1 read-only client: poll a log object from the store; record; replay offline"),
                          ("etp", "WITSML 2.x over Energistics ETP v1.2 (WebSocket, Avro): subscribe to channels and the store pushes; record; replay the session's shape")):
        p_lp = sub.add_parser(name, help=helptxt)
        p_lp.add_argument("--config", type=str, default=None, help="site map JSON (client-owned)")
        p_lp.add_argument("--write-example-config", type=str, default=None, help="write an example map here and exit")
        p_lp.add_argument("--replay", type=str, default=None, help="recording (JSON lines) to replay instead of connecting")
        p_lp.add_argument("--record", type=str, default=None, help="write every received message to this recording")
        p_lp.add_argument("--seconds", type=float, default=10.0, help="subscribe/run duration")
        p_lp.add_argument("--read-once", action="store_true", help="opcua: one Read of all nodes instead of a subscription")
        p_lp.add_argument("--out", type=str, default=None, help="write the records CSV here")
        p_lp.add_argument("--stream-csv", type=str, default=None, help="also write a historian-style CSV the other commands ingest")
        p_lp.add_argument("--selftest", action="store_true", help="etp: the codec round-trip and a session against the simulated store on the loopback")

    p_tl = sub.add_parser("tools", help="the tool library and the port registry: every tool with its citation, ratings and the interface it declares; every file format and live protocol; --rating checks a tool string")
    p_tl.add_argument("--json", action="store_true")
    p_tl.add_argument("--list", action="store_true", help="just the tool names")
    p_tl.add_argument("--rating", action="store_true", help="check a tool string: --station 9850:quartz_pt_geoq177_16k (depth in ft, then the tool; repeatable)")
    p_tl.add_argument("--station", type=str, action="append", default=None, help="a station as DEPTH_FT:TOOL, e.g. 9850:quartz_pt_geoq177_16k")
    p_tl.add_argument("--surface-temp-f", type=float, default=75.0); p_tl.add_argument("--temp-gradient-f-ft", type=float, default=0.018)
    p_tl.add_argument("--surface-pressure-psi", type=float, default=14.7); p_tl.add_argument("--pressure-gradient-psi-ft", type=float, default=0.465)
    p_tl.add_argument("--action", choices=["classes", "tutorial", "fleet", "fleet-add", "fleet-remove", "motors"], default=None,
                      help="the downhole tool classes in use across the industry: classes (--category, --find), tutorial --class ID, the workspace's own fleet (fleet, fleet-add, fleet-remove), and the downhole motor specifications (motors)")
    p_tl.add_argument("--category", type=str, default=None, help="classes: one category (bits, drive, measure, bha, casing, completion, logging, fishing, testing)")
    p_tl.add_argument("--find", type=str, default="", help="classes: words to look for in the name and description")
    p_tl.add_argument("--class", dest="class_id", type=str, default=None, help="tutorial / fleet-add: the tool class id, e.g. pdm_motor")
    p_tl.add_argument("--workspace", type=str, default=None, help="fleet actions: the workspace folder")
    p_tl.add_argument("--maker", type=str, default=""); p_tl.add_argument("--model", type=str, default="")
    p_tl.add_argument("--datasheet", type=str, default="", help="fleet-add: the datasheet that rates the tool - title and revision, or its URL (required)")
    p_tl.add_argument("--serial", type=str, default=""); p_tl.add_argument("--od-in", type=float, default=None); p_tl.add_argument("--note", type=str, default="")
    p_tl.add_argument("--id", type=str, default=None, help="fleet-remove: the fleet tool id (gea tools --action fleet lists them)")
    p_tl.add_argument("--actor", type=str, default="cli")
    p_cc = sub.add_parser("comcat", help="USGS ComCat / any FDSN event service: --lat --lon --radius-km --start --end prints the text-format query to open; --file reads the export it returns")
    p_cc.add_argument("--lat", type=float, default=None); p_cc.add_argument("--lon", type=float, default=None); p_cc.add_argument("--radius-km", type=float, default=25.0)
    p_cc.add_argument("--start", type=str, default=None); p_cc.add_argument("--end", type=str, default=None); p_cc.add_argument("--min-mag", type=float, default=1.0)
    p_cc.add_argument("--file", type=str, default=None, help="an FDSN text export to read and summarise")
    p_cc.add_argument("--json", action="store_true")
    p_sl = sub.add_parser("slip", help="slip tendency on the mapped faults of a layer at a position, from declared gradients: --layer --lat --lon --depth-ft --sv --shmin --shmax (--shmax-azimuth or --wsm)")
    p_sl.add_argument("--layer", type=str, required=True); p_sl.add_argument("--member", type=str, default=None); p_sl.add_argument("--feature-kind", type=str, choices=["fault", "fold"], default=None)
    p_sl.add_argument("--lat", type=float, required=True); p_sl.add_argument("--lon", type=float, required=True); p_sl.add_argument("--radius-km", type=float, default=25.0)
    p_sl.add_argument("--depth-ft", type=float, required=True)
    p_sl.add_argument("--sv", type=float, required=True); p_sl.add_argument("--shmin", type=float, required=True); p_sl.add_argument("--shmax", type=float, required=True)
    p_sl.add_argument("--pp", type=float, default=0.433); p_sl.add_argument("--shmax-azimuth", type=float, default=None); p_sl.add_argument("--wsm", type=str, default=None, help="the WSM database CSV: the azimuth from its records within 100 km")
    p_sl.add_argument("--dips", type=float, nargs="+", default=[60.0, 75.0, 90.0]); p_sl.add_argument("--mu", type=float, nargs="+", default=[0.6]); p_sl.add_argument("--delta-pp", type=float, default=0.0)
    p_sl.add_argument("--json", action="store_true")
    p_cat = sub.add_parser("catalog", help="the shipped catalogue: --list every entry with what reads it; --unused the carried ones; --check reads every entry; --friction the measured friction envelope; --core the core check; --entry X --info | --stream-csv")
    p_cat.add_argument("--list", action="store_true"); p_cat.add_argument("--unused", action="store_true"); p_cat.add_argument("--check", action="store_true")
    p_cat.add_argument("--friction", action="store_true"); p_cat.add_argument("--core", action="store_true")
    p_cat.add_argument("--entry", type=str, default=None); p_cat.add_argument("--info", action="store_true"); p_cat.add_argument("--stream-csv", type=str, default=None)
    p_cat.add_argument("--json", action="store_true")
    p_cat.add_argument("--positions", action="store_true", help="where every entry was drilled, from its own file or sidecar, or NO POSITION")
    p_cat.add_argument("--map", type=str, default=None, metavar="OUT.SVG", help="write the world map of the catalogue (coastline + one mark per positioned entry + the key) as an SVG file")
    p_cat.add_argument("--check-positions", action="store_true", help="the sidecars' recorded positions against a fresh read of the files (the gate's check)")
    p_ed = sub.add_parser("edr", help="an electronic drilling recorder's one-second export (date and time columns, WITS record-1 channels): --file reads it and prints the drilling record; --out DIR writes the record (json, md, html) and the MD-TVD table; --selftest proves the port on a synthetic export")
    p_ed.add_argument("--file", type=str, default=None); p_ed.add_argument("--out", type=str, default=None); p_ed.add_argument("--json", action="store_true"); p_ed.add_argument("--selftest", action="store_true")
    p_dl = sub.add_parser("dlis", help="a DLIS (RP66 V1) well log: --info describes it (origin, channels, frames, what was skipped); --frame --out writes the frame as a historian-style CSV; --to-las writes LAS 2.0")
    p_dl.add_argument("--file", type=str, required=True)
    p_dl.add_argument("--info", action="store_true")
    p_dl.add_argument("--frame", type=str, default=None, help="the frame to read when the file holds several")
    p_dl.add_argument("--out", type=str, default=None, help="write the frame's channels as CSV (index first)")
    p_dl.add_argument("--to-las", type=str, default=None, help="write the frame as a LAS 2.0 file")
    p_dl.add_argument("--json", action="store_true")
    p_pi = sub.add_parser("piwebapi", help="the PI Web API historian pull, read-only: --config (base_url, auth by environment variable names, tags with paths); --selftest against the stand-in")
    p_pi.add_argument("--config", type=str, default=None)
    p_pi.add_argument("--write-example-config", type=str, default=None)
    p_pi.add_argument("--out", type=str, default=None, help="write the records CSV here")
    p_pi.add_argument("--stream-csv", type=str, default=None)
    p_pi.add_argument("--seconds", type=float, default=None, help="ignored: a pull is one request per tag (kept so the live tap's call shape is the same)")
    p_pi.add_argument("--record", type=str, default=None, help="write the pull's summary as one JSON line here")
    p_pi.add_argument("--selftest", action="store_true")
    for name, helptxt in (("modbus-rtu", "Modbus RTU port, read-only: the serial line (pyserial) or RTU over TCP; functions 03/04 only; the stand-in slave with --selftest"),
                          ("dnp3", "DNP3 (IEEE 1815) master, read-only: integrity and event polls over TCP; analog inputs, binaries, counters with flags and event times; the stand-in outstation with --selftest"),
                          ("hartip", "HART-IP client, read-only: session, command 0, 3 and 9; the variables with their unit codes and status; the stand-in device with --selftest")):
        p_fp = sub.add_parser(name, help=helptxt)
        p_fp.add_argument("--config", type=str, default=None, help="site map JSON (client-owned): the transport, the mapping and its source")
        p_fp.add_argument("--write-example-config", type=str, default=None, help="write the example map (the stand-in) here and exit")
        p_fp.add_argument("--seconds", type=float, default=None, help="run for this long (polls = seconds / interval_s); default the map's polls")
        p_fp.add_argument("--polls", type=int, default=None, help="poll this many times instead")
        p_fp.add_argument("--record", type=str, default=None, help="write one JSON line per poll (the values and faults) here")
        p_fp.add_argument("--replay", type=str, default=None, help="not offered by these ports (a poll is a request; there is nothing to replay)")
        p_fp.add_argument("--out", type=str, default=None, help="write the records CSV here")
        p_fp.add_argument("--stream-csv", type=str, default=None, help="also write a historian-style CSV the other commands ingest")
        p_fp.add_argument("--selftest", action="store_true", help="the codec vectors and a session against the stand-in on the loopback")

    p_ws = sub.add_parser("workspace", help="the client site folder: init, add wells (file | catalogue | live), list, migrate a --out folder, refresh the dashboard, audit log")
    p_ws.add_argument("--path", type=str, required=True, help="the workspace folder")
    p_ws.add_argument("--action", type=str, default="list",
                      choices=["init", "add-file", "add-catalog", "add-live", "remove", "list", "migrate", "refresh", "audit",
                               "add-seismic", "refresh-seismic", "remove-seismic", "sar-film", "refresh-all", "add-track", "refresh-track", "remove-track",
                               "seismic-dataset", "seismic-field", "add-site", "sites", "site-report", "remove-site", "rename",
                               "set-well", "sra-define", "sra-report", "associate", "osdu-export", "quakeml-export", "well-identity", "vibration-report",
                               "add-live-station", "live-fold", "live-run", "month-end", "add-layer", "layers", "remove-layer", "structure", "interval",
                               "set-stress", "add-stress-map", "wsm", "slip"])
    p_ws.add_argument("--stations", type=str, nargs="+", default=None, help="add-track: two or more array station ids of this workspace")
    p_ws.add_argument("--site", type=str, default=None, help="site-report/remove-site: the site id")
    p_ws.add_argument("--api", type=str, default=None, help="set-well: the API number")
    p_ws.add_argument("--uic", type=str, default=None, help="set-well: the UIC permit number")
    p_ws.add_argument("--depth-tier", type=str, choices=["shallow", "deep"], default=None, help="set-well: above or below the named formation base")
    p_ws.add_argument("--formation", type=str, default=None, help="set-well: the basis of the tier, e.g. 'completed below the base of the Wolfcamp'")
    p_ws.add_argument("--channel-pressure", type=str, default=None, help="set-well: the channel that is the surface injection pressure (psi)")
    p_ws.add_argument("--channel-rate", type=str, default=None, help="set-well: the channel that is the injection rate")
    p_ws.add_argument("--rate-unit", type=str, default=None, help="set-well: the rate channel's unit: bbl/min or bbl/d")
    p_ws.add_argument("--bhp-method", type=str, choices=["calculated", "dip_in", "probe"], default=None, help="set-well: how bottomhole pressure is known")
    p_ws.add_argument("--channel-bhp", type=str, default=None, help="set-well: the downhole gauge channel (probe method)")
    p_ws.add_argument("--top-interval-ft", type=float, default=None, help="set-well: top of the disposal interval, ft (calculated method)")
    p_ws.add_argument("--gradient-psi-ft", type=float, default=None, help="set-well: fluid gradient, psi/ft (calculated method)")
    p_ws.add_argument("--radius-km", type=float, default=None, help="sra-define: the area's radius in km (Gardendale: 9.08); structure: how far around the well to look (default 50)")
    p_ws.add_argument("--plan-date", type=str, default=None, help="sra-define: the plan date, YYYY-MM-DD")
    p_ws.add_argument("--threshold-m", type=float, default=3.5, help="sra-define: the magnitude the plan is written against (3.5)")
    p_ws.add_argument("--goal-months", type=int, default=18, help="sra-define: months without a threshold event that meet the goal (18)")
    p_ws.add_argument("--response-hours", type=int, default=48, help="sra-define: hours within which the response group meets (48)")
    p_ws.add_argument("--checkpoint-months", type=int, default=3, help="sra-define: months between checkpoints with Commission staff (3)")
    p_ws.add_argument("--formation-boundary", type=str, default="base of the Wolfcamp", help="sra-define: the stratigraphic boundary between the two tiers")
    p_ws.add_argument("--triggering-event", type=str, default="", help="sra-define: the catalogue id of the event the plan answers")
    p_ws.add_argument("--catalog", type=str, default=None, help="sra-report / month-end: a TexNet earthquake-catalogue export (CSV)")
    p_ws.add_argument("--aftershocks", type=str, nargs="*", default=None, help="sra-report: catalogue ids the operator declares as aftershocks (exempt)")
    p_ws.add_argument("--start", type=str, default=None, help="sra-report: first day of the record, YYYY-MM-DD")
    p_ws.add_argument("--end", type=str, default=None, help="sra-report: last day of the record, YYYY-MM-DD")
    p_ws.add_argument("--vp", type=float, default=5.8, help="associate: the P velocity of the model, km/s (declared)")
    p_ws.add_argument("--partition", type=str, default="", help="osdu-export: the platform's data partition id (declared)")
    p_ws.add_argument("--acl-owner", type=str, nargs="*", default=None, help="osdu-export: ACL owners group(s)")
    p_ws.add_argument("--acl-viewer", type=str, nargs="*", default=None, help="osdu-export: ACL viewers group(s)")
    p_ws.add_argument("--legal-tag", type=str, nargs="*", default=None, help="osdu-export: legal tag(s)")
    p_ws.add_argument("--country", type=str, nargs="*", default=None, help="osdu-export: otherRelevantDataCountries (default US)")
    p_ws.add_argument("--operator-org", type=str, default="", help="osdu-export: the operator's Organisation record id on the platform")
    p_ws.add_argument("--out", type=str, default=None, help="osdu-export: the folder to write the manifest and files into; quakeml-export: the file to write")
    p_ws.add_argument("--agency", type=str, default="", help="quakeml-export: the agencyID written in creationInfo (default: the site's name)")
    p_ws.add_argument("--record", type=str, default=None, help="vibration-report: the machine record (CSV with a timestamp or elapsed-seconds column, or miniSEED/SAC)")
    p_ws.add_argument("--channel", type=str, default=None, help="vibration-report: the channel to assess (default: the first)")
    p_ws.add_argument("--vib-unit", type=str, default="g", help="vibration-report: the channel's unit - g, m/s2, mm/s, in/s, or counts with --sensitivity")
    p_ws.add_argument("--sensitivity", type=float, default=None, help="vibration-report: units per count, when the record is in counts")
    p_ws.add_argument("--rpm", type=float, default=None, help="vibration-report: the shaft speed, r/min (declared)")
    p_ws.add_argument("--group", type=int, choices=[1, 2], default=None, help="vibration-report: ISO 20816-3 machine group - 1 above 300 kW, 2 from 15 to 300 kW (declared)")
    p_ws.add_argument("--support", type=str, choices=["rigid", "flexible"], default=None, help="vibration-report: the support class (declared)")
    p_ws.add_argument("--bearing", type=str, default=None, help="vibration-report: elements,ball_mm,pitch_mm[,contact_deg] - the bearing geometry (declared)")
    p_ws.add_argument("--demod-band", type=float, nargs=2, default=None, help="vibration-report: the demodulation band in Hz (default: chosen by kurtosis)")
    p_ws.add_argument("--depth-km", type=float, default=6.0, help="associate: the hypocentral depth the location is made under, km (declared)")
    p_ws.add_argument("--rms-tol", type=float, default=0.15, help="associate: the RMS residual an event must fit within, s")
    p_ws.add_argument("--wells", type=str, nargs="+", default=None, help="add-site: well ids of this workspace")
    p_ws.add_argument("--seismic", type=str, nargs="+", default=None, help="add-site: seismic station ids of this workspace")
    p_ws.add_argument("--tracks", type=str, nargs="+", default=None, help="add-site: track ids of this workspace")
    p_ws.add_argument("--client", type=str, default="", help="add-site: whose engagement this is")
    p_ws.add_argument("--note", type=str, default="", help="add-site: a line about this engagement")
    p_ws.add_argument("--truth", type=str, default=None, help="add-track: ground truth CSV (point_id, lat, lon, utc[, note])")
    p_ws.add_argument("--track", type=str, default=None, help="refresh-track / remove-track: the track id")
    p_ws.add_argument("--win", type=float, default=600.0, help="add-track: the window length, s (default 600)")
    p_ws.add_argument("--hours", type=float, default=8.0, help="sar-film: the synthetic scene's length, h")
    p_ws.add_argument("--step", type=float, default=600.0, help="sar-film: seconds per frame")
    p_ws.add_argument("--seed", type=int, default=5, help="sar-film: the scene's seed")
    p_ws.add_argument("--method", type=str, default="bartlett", choices=["bartlett", "capon"], help="sar-film: the beamformer")
    p_ws.add_argument("--scene", type=str, default="rigs", choices=["rigs", "lateral"], help="sar-film: fixed rigs, or a bit advancing along a lateral past two arrays")
    p_ws.add_argument("--files", type=str, nargs="+", default=None, help="add-seismic: the station's record (or one record per sensor of an array, in the sensors CSV's order)")
    p_ws.add_argument("--lat", type=float, default=None, help="add-seismic: the station's latitude")
    p_ws.add_argument("--lon", type=float, default=None, help="add-seismic: the station's longitude")
    p_ws.add_argument("--stationxml", type=str, default=None, help="add-seismic: the station's FDSN StationXML (the response is removed at every refresh)")
    p_ws.add_argument("--sensors", type=str, default=None, help="add-seismic: sensors CSV (sensor_id, lat, lon) - makes the station an array")
    p_ws.add_argument("--sources", type=str, default=None, help="add-seismic: the rigs CSV (source_id, lat, lon, start_utc, end_utc[, kind, note, datum])")
    p_ws.add_argument("--datum", type=str, default=None, help="add-seismic: the datum the station's own position - and any sensor or rig row that does not say - is on (WGS84, NAD83, NAD27). Unstated is recorded as UNKNOWN, not assumed")
    p_ws.add_argument("--zone", type=str, default=None, help="add-seismic with --permits: the export's grid zone, when it gives easting/northing")
    p_ws.add_argument("--unit", type=str, default="m", help="add-seismic with --permits and --zone: the grid unit (m, usft, ft)")
    p_ws.add_argument("--band", type=float, nargs=2, default=None, metavar=("LO", "HI"), help="add-seismic: the band, Hz (default 1 50)")
    p_ws.add_argument("--permits", type=str, default=None, help="add-seismic: a permit export to turn into the rigs CSV (instead of --sources)")
    p_ws.add_argument("--station", type=str, default=None, help="refresh-seismic / remove-seismic / live-fold / live-run: the station id")
    p_ws.add_argument("--seedlink-config", type=str, default=None, help="add-live-station: the SeedLink config JSON (gea seedlink --write-example-config)")
    p_ws.add_argument("--include-open", action="store_true", help="live-fold: fold the current day's files too (they are still being written)")
    p_ws.add_argument("--seconds", type=float, default=None, help="live-run: how long to run (default: until Ctrl+C)")
    p_ws.add_argument("--layer", type=str, default=None, help="add-layer: the shapefile (zip, folder or .shp); remove-layer: the layer id")
    p_ws.add_argument("--member", type=str, default=None, help="add-layer: which shapefile inside a zip or folder that holds several (gea structure --layer layer.zip --list)")
    p_ws.add_argument("--citation", type=str, default="", help="add-layer: where the map came from, in the site's words")
    p_ws.add_argument("--formation-name", type=str, default=None, help="add-layer: the formation a contours layer maps (Wolfcamp)")
    p_ws.add_argument("--role", type=str, choices=["top", "isopach", "base"], default=None, help="add-layer: what the contours are of that formation: its top elevation, its thickness, or its base")
    p_ws.add_argument("--base-interval-ft", type=float, default=None, help="set-well: base of the disposal interval, ft")
    p_ws.add_argument("--feature-kind", type=str, choices=["fault", "fold"], default=None, help="add-layer: what every feature of a structure layer is when its attributes do not say")
    p_ws.add_argument("--sv", type=float, default=None, help="set-stress: vertical stress gradient, psi/ft")
    p_ws.add_argument("--shmin", type=float, default=None, help="set-stress: minimum horizontal stress gradient, psi/ft")
    p_ws.add_argument("--shmax", type=float, default=None, help="set-stress: maximum horizontal stress gradient, psi/ft")
    p_ws.add_argument("--pp", type=float, default=None, help="set-stress: ambient pore-pressure gradient, psi/ft (hydrostatic 0.433 unless declared)")
    p_ws.add_argument("--shmax-azimuth", type=float, default=None, help="set-stress: SHmax azimuth, degrees from north (or leave it to the stress map)")
    p_ws.add_argument("--azimuth-source", type=str, default=None, help="set-stress: where the azimuth came from")
    p_ws.add_argument("--gradients-source", type=str, default=None, help="set-stress: where the gradients came from")
    p_ws.add_argument("--dips", type=float, nargs="+", default=None, help="set-stress: the dips to run when the layer states none (default 60 75 90)")
    p_ws.add_argument("--mu", type=float, nargs="+", default=None, help="set-stress: the friction coefficients to run (default 0.6)")
    p_ws.add_argument("--delta-pp", type=float, default=None, help="set-stress / slip: the pore-pressure increase to judge at, psi")
    p_ws.add_argument("--depth-ft", type=float, default=None, help="slip: the depth to judge at (default the midpoint of the declared interval)")
    p_ws.add_argument("--depth-ref", type=str, choices=["TVD", "MD"], default=None, help="set-well: the interval depths are true vertical (TVD) or measured (MD) depth")
    p_ws.add_argument("--ref-elev-ft", type=float, default=None, help="set-well: the reference elevation the depths are measured from, ft above sea level")
    p_ws.add_argument("--ref-elev-datum", type=str, choices=["KB", "GL", "DF"], default=None, help="set-well: what that elevation is: kelly bushing, ground level, derrick floor")
    p_ws.add_argument("--ref-elev-source", type=str, default=None, help="set-well: where the elevation came from (completion report, survey plat)")
    p_ws.add_argument("--elev-3dep", action="store_true", help="set-well: fetch the GROUND elevation at the surface position from the USGS 3DEP point service and store it as the reference (datum GL, source stated)")
    p_ws.add_argument("--epqs-url", type=str, default=None, help="set-well --elev-3dep / gea elevation: the EPQS endpoint (default the USGS one)")
    p_ws.add_argument("--period", type=str, default="previous", help="month-end: the month, YYYY-MM, or 'previous' (the last closed month)")
    p_ws.add_argument("--no-refresh", action="store_true", help="month-end: collect the reports as they stand without refreshing first")
    p_ws.add_argument("--name", type=str, default=None, help="init: site name; add-*: display name")
    p_ws.add_argument("--file", type=str, default=None, help="add-file: the client's data file; add-live: the port configuration JSON")
    p_ws.add_argument("--entry", type=str, default=None, help="add-catalog: catalogue entry")
    p_ws.add_argument("--well", type=str, default=None, help="add-catalog: well tag inside the entry; remove: well id")
    p_ws.add_argument("--station-md", type=float, default=None, help="station MD in ft (catalogue wells need it)")
    p_ws.add_argument("--port", type=str, default=None, choices=["opcua", "mqtt", "modbus_g6", "wits0", "witsml", "etp", "modbus_rtu", "dnp3", "hartip"], help="add-live: the port")
    p_ws.add_argument("--from", dest="from_dir", type=str, default=None, help="migrate: a folder written by `gea dashboard --out`")
    p_ws.add_argument("--actor", type=str, default="cli", help="who is acting (recorded in the audit log)")
    p_ws.add_argument("--month", type=str, default=None, help="refresh: also measure the SLA for YYYY-MM")
    p_ws.add_argument("--sat", action="store_true", help="refresh: also run the SAT protocol")
    p_ws.add_argument("--outage", action="append", default=None, help="refresh: outage window start/end (ISO), repeatable")

    p_ck = sub.add_parser("checklist", help="the setup checklist: what this workspace still needs before its reports can be handed over - read from the workspace as it stands")
    p_ck.add_argument("--workspace", type=str, default=None, help="the workspace folder, e.g. C:\\site")
    p_ck.add_argument("--json", action="store_true")
    p_ck.add_argument("--confirm", type=str, default=None, help="record a person's check of a confirmation item, e.g. events:Well-30636033")
    p_ck.add_argument("--note", type=str, default="", help="--confirm: what it was checked against (required)")
    p_ck.add_argument("--actor", type=str, default="cli")

    p_wr = sub.add_parser("well-report", help="the client well report: one printable document per well (drilling record, depth events, progress chart, location and structure, well path, cost and the market) and one for the pad")
    p_wr.add_argument("--workspace", type=str, required=True)
    p_wr.add_argument("--well", type=str, default=None, help="one well's report (the default is every well and the pad)")
    p_wr.add_argument("--pad", action="store_true", help="the pad report only")
    p_wr.add_argument("--actor", type=str, default="cli")

    p_ec = sub.add_parser("economics", help="each well's time, footage and declared cost against the market while it was drilled; declare rates, items and an AFE; refresh or import price series")
    p_ec.add_argument("--workspace", type=str, required=True)
    p_ec.add_argument("--json", action="store_true")
    p_ec.add_argument("--well", type=str, default=None, help="the well a rate, item or AFE is for")
    p_ec.add_argument("--rate", type=float, default=None, help="a day rate, USD/day, over --from/--to (open when left out)")
    p_ec.add_argument("--item", type=float, default=None, help="a one-off cost, USD (a bit, a cement job), optionally --section N")
    p_ec.add_argument("--afe", type=float, default=None, help="the well's AFE, USD")
    p_ec.add_argument("--what", type=str, default="", help="what the rate or item pays for")
    p_ec.add_argument("--section", type=int, default=None)
    p_ec.add_argument("--from", dest="date_from", type=str, default=None, help="rate start, ISO date or date-time on the recorder's clock")
    p_ec.add_argument("--to", dest="date_to", type=str, default=None, help="rate end")
    p_ec.add_argument("--source", type=str, default="", help="where the number came from (contract, invoice, AFE number)")
    p_ec.add_argument("--refresh-prices", action="store_true", help="fetch the latest EIA spot-price copies into the workspace")
    p_ec.add_argument("--import-series", type=str, default=None, help="a CSV of dates and values to keep beside the prices (a rig count)")
    p_ec.add_argument("--name", type=str, default="")
    p_ec.add_argument("--unit", type=str, default="")
    p_ec.add_argument("--actor", type=str, default="cli")

    p_tj = sub.add_parser("trajectory", help="attach a well's directional survey (WITSML trajectory or a table of MD, Inc, Azi), kept verbatim beside the well, so the 3D tab can draw its path")
    p_tj.add_argument("--workspace", type=str, required=True)
    p_tj.add_argument("--well", type=str, required=True)
    p_tj.add_argument("--file", type=str, required=True)
    p_tj.add_argument("--azimuth-reference", type=str, required=True, choices=("grid", "true", "magnetic"))
    p_tj.add_argument("--grid", type=str, default=None, help="grid azimuths: the grid, e.g. TX_CENTRAL or UTM14N")
    p_tj.add_argument("--declination", type=float, default=None, help="magnetic azimuths: the declination used, degrees east positive")
    p_tj.add_argument("--md-unit", type=str, default="ft", choices=("ft", "m"))
    p_tj.add_argument("--depth-reference", type=str, default=None, help="KB, DF or GL: must match the well's declared reference elevation")
    p_tj.add_argument("--source", type=str, required=True, help='where the survey came from, e.g. "RRC imaged records, directional survey"')
    p_tj.add_argument("--actor", type=str, default="cli")

    p_3d = sub.add_parser("well3d", help="the 3D scene in words: each well's path, how it was checked, the surfaces and the replay (the 3D tab draws it)")
    p_3d.add_argument("--scene", type=str, default="volve", choices=("volve", "workspace"))
    p_3d.add_argument("--workspace", type=str, default=None)
    p_3d.add_argument("--json", action="store_true")

    p_ds = sub.add_parser("drillstring", help="the drill string: each declared run's components from the bit up, their usage from the drilling record and their service life; the downhole motor's statistics and the stability of its oscillations")
    p_ds.add_argument("--workspace", type=str, required=True)
    p_ds.add_argument("--well", type=str, default=None)
    p_ds.add_argument("--declare", type=str, default=None, help="a JSON file declaring a run: {well_id, name, start, end, components:[{class_id, maker, model, serial, od_in, length_ft, motor_id | motor{...}, service_limit, service_basis, service_source}]}")
    p_ds.add_argument("--remove", type=str, default=None, help="a run id to remove")
    p_ds.add_argument("--actor", type=str, default="cli")
    p_ds.add_argument("--json", action="store_true")

    p_dc = sub.add_parser("declare", help="the declarations sheet: wells' identity, position and elevation, surveys, rates, items and AFEs, fleet tools, drill-string runs, ESP penetrators and drives - one CSV, checked against the workspace before anything is written")
    p_dc.add_argument("--workspace", type=str, required=True)
    p_dc.add_argument("--file", type=str, default=None, help="the sheet (CSV); every row is planned NEW, CHANGE or SAME and nothing is written without --apply")
    p_dc.add_argument("--apply", action="store_true", help="write the NEW and CHANGE rows (only when the sheet has no error)")
    p_dc.add_argument("--template", type=str, default=None, help="write the sheet for this workspace, filled with what is already declared, to this path")
    p_dc.add_argument("--actor", type=str, default="cli")
    p_dc.add_argument("--json", action="store_true")

    p_pn = sub.add_parser("penetrator", help="the ESP wellhead penetrator: the penetrators the program knows with their ratings, the permanent magnet motor safety note, and each well's installation record and drive checked against the ratings")
    p_pn.add_argument("--workspace", type=str, default=None)
    p_pn.add_argument("--well", type=str, default=None)
    p_pn.add_argument("--remove", type=str, default=None, choices=("install", "drive"), help="remove the well's installation record or its drive")
    p_pn.add_argument("--actor", type=str, default="cli")
    p_pn.add_argument("--json", action="store_true")

    p_sm = sub.add_parser("simulation", help="the simulation in words: the scenes (Volve 15/9-F-4 with its logs and interpretation, Volve F-5 and F-10 on the survey clock, every drilling-recorder well), and what the record says at a depth (the Simulation tab replays it)")
    p_sm.add_argument("--workspace", type=str, default=None)
    p_sm.add_argument("--scene", type=str, default=None, help="e.g. volve-f4, or well:Well-30636033")
    p_sm.add_argument("--at", type=float, default=None, help="a measured depth, in the scene's unit (m for Volve, ft for a workspace well)")
    p_sm.add_argument("--json", action="store_true")

    p_wl = sub.add_parser("wall", help="the control-room board as text: a line per well, worst first, its state and why (the same board the wall screen shows)")
    p_wl.add_argument("--workspace", type=str, default=None, help="the workspace folder, e.g. C:\\site")
    p_wl.add_argument("--json", action="store_true")

    p_ho = sub.add_parser("handover", help="the shift handover: list the notes, sign one (--write) or acknowledge one (--ack)")
    p_ho.add_argument("--workspace", type=str, default=None, help="the workspace folder, e.g. C:\\site")
    p_ho.add_argument("--write", action="store_true", help="sign a note: give at least one of --open, --watching, --done")
    p_ho.add_argument("--open", type=str, default="", help="--write: what the next shift must pick up")
    p_ho.add_argument("--watching", type=str, default="", help="--write: what is being watched, and why")
    p_ho.add_argument("--done", type=str, default="", help="--write: what this shift did")
    p_ho.add_argument("--ack", type=str, default=None, help="acknowledge the note with this id (not your own)")
    p_ho.add_argument("--comment", type=str, default="", help="--ack: an optional comment")
    p_ho.add_argument("--actor", type=str, default=None, help="who signs or acknowledges (required with --write and --ack)")
    p_ho.add_argument("--json", action="store_true")

    p_sv = sub.add_parser("serve", help="serve the dashboard over a workspace: the page, the reports and the JSON API (standard library HTTP)")
    p_sv.add_argument("--workspace", type=str, required=True, help="the workspace folder (gea workspace --action init)")
    p_sv.add_argument("--host", type=str, default="127.0.0.1", help="bind address; 0.0.0.0 exposes it on the site network (put TLS in front)")
    p_sv.add_argument("--port", type=int, default=8765)
    p_sv.add_argument("--workers", type=int, default=1, help="jobs run at once")
    p_sv.add_argument("--no-scheduler", action="store_true", help="do not run scheduled jobs from this process")
    p_sv.add_argument("--behind-proxy", action="store_true", help="a reverse proxy terminates TLS in front: trust X-Forwarded-For/-Proto, mark the cookie Secure (deploy/ has nginx and Caddy examples)")
    p_sv.add_argument("--no-browser", action="store_true", help="do not open the control panel in a browser once the service is listening (a scheduled start, a restart, a server with no desktop)")

    p_hk = sub.add_parser("housekeeping", help="rotate the append-only logs, prune finished job folders and old live recordings (dry run unless --apply)")
    p_hk.add_argument("--workspace", type=str, required=True)
    p_hk.add_argument("--apply", action="store_true")
    p_hk.add_argument("--rotate-mb", type=float, default=50.0)
    p_hk.add_argument("--jobs-keep-days", type=int, default=30)
    p_hk.add_argument("--jobs-keep-n", type=int, default=500)
    p_hk.add_argument("--records-keep-days", type=int, default=90)
    p_hk.add_argument("--actor", type=str, default="housekeeping")
    p_hk.add_argument("--json", action="store_true")

    p_el = sub.add_parser("elevation", help="the ground elevation of a position from the USGS 3DEP point service: --lat --lon (the ground, NAVD88; not the kelly bushing)")
    p_el.add_argument("--lat", type=float, required=True); p_el.add_argument("--lon", type=float, required=True)
    p_el.add_argument("--url", type=str, default=None, help="the EPQS endpoint (default the USGS one)")
    p_el.add_argument("--json", action="store_true")
    p_st = sub.add_parser("structure", help="the nearest mapped fault and fold to a position, from a shapefile: --layer layer.zip (or a folder, or a .shp) --lat --lon; --info describes the layer")
    p_st.add_argument("--layer", type=str, required=True)
    p_st.add_argument("--lat", type=float, default=None); p_st.add_argument("--lon", type=float, default=None)
    p_st.add_argument("--radius-km", type=float, default=50.0)
    p_st.add_argument("--member", type=str, default=None, help="which shapefile inside a zip or folder that holds several")
    p_st.add_argument("--list", action="store_true", help="list the shapefiles a zip or folder holds and exit")
    p_st.add_argument("--info", action="store_true", help="describe the layer (coordinate system, extent, types, metadata) and exit")
    p_st.add_argument("--json", action="store_true")
    p_sk = sub.add_parser("soak", help="the patches through link outages for hours, measured: N WITS0 patches, an ETP patch and a SeedLink station through relays that are cut on a schedule; the Soak Report")
    p_sk.add_argument("--hours", type=float, default=None, help="how long (72 for the go-live soak); or --seconds")
    p_sk.add_argument("--seconds", type=float, default=None)
    p_sk.add_argument("--patches", type=int, default=2, help="WITS0 patches (the site's own count)")
    p_sk.add_argument("--no-etp", action="store_true"); p_sk.add_argument("--no-seedlink", action="store_true")
    p_sk.add_argument("--outage-every", type=float, default=600.0, help="seconds between link outages")
    p_sk.add_argument("--outage-len", type=float, default=60.0, help="seconds each outage lasts")
    p_sk.add_argument("--interval", type=float, default=1.0, help="simulator frame interval (s)")
    p_sk.add_argument("--reconnect-budget", type=float, default=90.0, help="seconds a source may take to be back and receiving after the link returns")
    p_sk.add_argument("--with-service", action="store_true", help="also serve the page and time its answers through the outages")
    p_sk.add_argument("--out", type=str, default=None, help="where soak.json and the Soak Report go (default: the throw-away workspace's reports/soak)")
    p_sk.add_argument("--keep", action="store_true", help="keep the throw-away workspace")
    p_sk.add_argument("--json", action="store_true")
    p_lt = sub.add_parser("loadtest", help="how many supervised patches this machine carries: N WITS0 simulators and patches for a while, with the service answering")
    p_lt.add_argument("--patches", type=int, default=8)
    p_lt.add_argument("--seconds", type=float, default=30.0)
    p_lt.add_argument("--interval", type=float, default=1.0, help="simulator frame interval (s)")
    p_lt.add_argument("--with-service", action="store_true", help="also start the service and time its answers under load")
    p_lt.add_argument("--keep", action="store_true", help="keep the throw-away workspace")
    p_lt.add_argument("--json", action="store_true")

    p_us = sub.add_parser("users", help="accounts for the dashboard: add, list, password, role, disable, enable")
    p_us.add_argument("--workspace", type=str, required=True)
    p_us.add_argument("--action", type=str, default="list", choices=["add", "list", "password", "role", "disable", "enable"])
    p_us.add_argument("--name", type=str, default=None)
    p_us.add_argument("--role", type=str, default="viewer", choices=["viewer", "operator", "approver", "admin"])
    p_us.add_argument("--password-env", type=str, default="GEA_PASSWORD", help="environment variable holding the password (never a flag, never on the command line)")
    p_us.add_argument("--actor", type=str, default="cli")

    p_es = sub.add_parser("etp-sim", help="an ETP v1.2 store for testing a patch without a rig: two channels of a synthetic disposal well, pushed as a customer subscribes")
    p_es.add_argument("--port", type=int, default=9800)
    p_es.add_argument("--frames", type=int, default=600)
    p_es.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    p_es.add_argument("--seed", type=int, default=1)
    p_bk = sub.add_parser("backup", help="the site copied off the machine: one dated archive with a manifest; --verify an archive; --restore one onto an empty folder; --status")
    p_bk.add_argument("--workspace", type=str, default=None, help="the site folder (for a backup, or --status)")
    p_bk.add_argument("--out", type=str, default=None, help="the folder the archive goes to - another disk or a share, never inside the workspace")
    p_bk.add_argument("--keep", type=int, default=None, help="keep only the newest N archives of this site in --out")
    p_bk.add_argument("--label", type=str, default="", help="a note carried in the manifest (e.g. 'before the gauge swap')")
    p_bk.add_argument("--verify", type=str, default=None, help="check this archive against its manifest and its .sha256")
    p_bk.add_argument("--restore", type=str, default=None, help="restore this archive ...")
    p_bk.add_argument("--to", type=str, default=None, help="... onto this empty folder")
    p_bk.add_argument("--status", action="store_true", help="when the last backup of --workspace was taken, and whether that is too long ago")
    p_bk.add_argument("--actor", type=str, default="cli")
    p_sl = sub.add_parser("seedlink", help="the seismic leg's live port: a SeedLink (3 / 4.0) station's records as it writes them, byte for byte into day files; --selftest against the simulator")
    p_sl.add_argument("--config", type=str, default=None, help="the SeedLink config JSON (host, port, streams)")
    p_sl.add_argument("--write-example-config", type=str, default=None, help="write an example config here and exit")
    p_sl.add_argument("--out", type=str, default="seedlink", help="the folder for the day files and the state (default ./seedlink)")
    p_sl.add_argument("--seconds", type=float, default=60.0, help="how long to run (the state resumes the next run from the last sequence number)")
    p_sl.add_argument("--max-records", type=int, default=None, help="stop after this many records")
    p_sl.add_argument("--selftest", action="store_true", help="both protocols against the simulator on the loopback")
    p_sl.add_argument("--hello", action="store_true", help="connect, print what the server says of itself (HELLO) and its INFO ID, and exit")
    p_ss = sub.add_parser("seedlink-sim", help="a SeedLink server (3 and 4.0) for rehearsing the live station without a data centre: one synthetic three-component station")
    p_ss.add_argument("--port", type=int, default=18000)
    p_ss.add_argument("--network", type=str, default="XX")
    p_ss.add_argument("--station", type=str, default="PAD3")
    p_ss.add_argument("--channels", type=str, default="HHZ,HHN,HHE")
    p_ss.add_argument("--rate", type=float, default=100.0, help="samples per second")
    p_ss.add_argument("--seed", type=int, default=7)
    p_w0 = sub.add_parser("wits0-sim", help="a WITS0 sender for testing a patch without a rig: listens once, then streams a deterministic drilling sequence")
    p_w0.add_argument("--port", type=int, default=5001)
    p_w0.add_argument("--frames", type=int, default=600)
    p_w0.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    p_w0.add_argument("--seed", type=int, default=1)
    p_w0.add_argument("--connect", type=str, default=None, help="host:port - instead of listening, connect to a listening tap and push frames")

    p_osdu = sub.add_parser("osdu", help="the OSDU-shaped export: the self-test on a labelled site")
    p_osdu.add_argument("--action", choices=["selftest"], default="selftest")
    p_cf = sub.add_parser("conformance", help="ISO/IEC 17025 7.8 certificate items and the ILAC-G8 decision rules: the self-test, or one decision")
    p_cf.add_argument("--action", choices=["selftest", "decide"], default="selftest")
    p_cf.add_argument("--value", type=float, default=None, help="decide: the measured value (a deviation; the tolerance is symmetric)")
    p_cf.add_argument("--tolerance", type=float, default=None, help="decide: the tolerance limit TL")
    p_cf.add_argument("--uncertainty", type=float, default=None, help="decide: the expanded uncertainty U of the value (omit: simple acceptance)")
    p_cf.add_argument("--k", type=float, default=2.0)
    p_cf.add_argument("--rule", choices=["simple-acceptance", "binary-guard-band", "non-binary-guard-band"], default="non-binary-guard-band")
    p_cf.add_argument("--unit", type=str, default="")
    p_vib = sub.add_parser("vibration", help="machine vibration: ISO 20816-3 zone and bearing envelope analysis on a record, or the self-test")
    p_vib.add_argument("--action", choices=["selftest", "assess"], default="selftest")
    p_vib.add_argument("--record", type=str, default=None, help="assess: the record (CSV with a time column, or miniSEED/SAC)")
    p_vib.add_argument("--channel", type=str, default=None)
    p_vib.add_argument("--unit", type=str, default="g", help="g, m/s2, mm/s, in/s, or counts with --sensitivity")
    p_vib.add_argument("--sensitivity", type=float, default=None)
    p_vib.add_argument("--rpm", type=float, default=None)
    p_vib.add_argument("--group", type=int, choices=[1, 2], default=None)
    p_vib.add_argument("--support", type=str, choices=["rigid", "flexible"], default=None)
    p_vib.add_argument("--bearing", type=str, default=None, help="elements,ball_mm,pitch_mm[,contact_deg]")
    p_vib.add_argument("--demod-band", type=float, nargs=2, default=None)
    p_vib.add_argument("--json", action="store_true")
    p_ppdm = sub.add_parser("ppdm", help="the well named as the US Well Number and PPDM name it: take a number apart, or run the self-test")
    p_ppdm.add_argument("--action", choices=["selftest", "parse"], default="selftest")
    p_ppdm.add_argument("--number", type=str, default=None, help="parse: a US Well Number (10, 12 or 14 digits, dashes optional)")
    p_qml = sub.add_parser("quakeml", help="the QuakeML 1.2 catalogue export: the self-test on the association leg's labelled scene")
    p_qml.add_argument("--action", choices=["selftest"], default="selftest")
    p_qml.add_argument("--out", type=str, default=None, help="where to leave the self-test's file (default: a temporary folder)")
    p_sra = sub.add_parser("sra", help="the Seismicity Response Area packet: the self-test on a labelled scene")
    p_sra.add_argument("--action", choices=["selftest"], default="selftest")
    p_sra.add_argument("--json", action="store_true")
    p_dr = sub.add_parser("doctor", help="which code is running and can it serve: Python, the package, duplicates, the page, PyPI; with --workspace also the site folder and the port")
    p_dr.add_argument("--workspace", type=str, default=None)
    p_dr.add_argument("--host", type=str, default="127.0.0.1")
    p_dr.add_argument("--port", type=int, default=8765)
    p_dr.add_argument("--json", action="store_true")

    p_pm = sub.add_parser("permits", help="a drilling-permit export (CSV/TSV) to the rigs CSV the seismic tests judge against, with an import note")
    p_pm.add_argument("--file", type=str, required=True, help="the permit export (a regulator's query CSV, an operator's schedule)")
    p_pm.add_argument("--out", type=str, required=True, help="the rigs CSV to write (an .import.json note is written beside it)")
    p_pm.add_argument("--mapping", type=str, default=None, help='JSON {"lat": "LAT_COLUMN", "lon": ..., "start": ..., "end": ..., "id": ..., "name": ...} when the guesses are wrong')
    p_pm.add_argument("--default-days", type=float, default=30.0, help="a row without an end date works this many days from its start (default 30)")
    p_pm.add_argument("--within", type=float, nargs=3, default=None, metavar=("LAT", "LON", "KM"), help="keep only rigs within KM of LAT, LON")
    p_pm.add_argument("--datum", type=str, default=None, help="the datum the export's positions are on (WGS84, NAD83, NAD27) when no column says; every row is converted to WGS84 and the note records the shift")
    p_pm.add_argument("--zone", type=str, default=None, help="read the position columns as grid easting/northing in this zone (TX_NORTH, TX_NORTH_CENTRAL, TX_CENTRAL, TX_SOUTH_CENTRAL, TX_SOUTH, UTM13N, ...) instead of latitude/longitude")
    p_pm.add_argument("--unit", type=str, default="m", help="with --zone: the grid unit - m, usft (US survey foot) or ft (international foot). They are different units and a state plane northing read in the wrong one is metres out")
    p_pm.add_argument("--json", action="store_true")

    p_up = sub.add_parser("update", help="the program update: compare the running version with PyPI and, unless --check, run pip --upgrade for it from this Python")
    p_up.add_argument("--check", action="store_true", help="only report the running and the newest version")
    p_up.add_argument("--extras", type=str, default="live,plotting,xls,desktop", help="the extras to carry through the upgrade (default live,plotting,xls,desktop)")
    p_up.add_argument("--json", action="store_true")

    p_nt = sub.add_parser("notify", help="notification rules: validate the configuration, send a test message, show the delivery log")
    p_nt.add_argument("--workspace", type=str, default=None)
    p_nt.add_argument("--test", type=str, default=None, help="send a test message through this channel name")
    p_nt.add_argument("--log", type=int, default=None, help="show the last N deliveries")
    p_nt.add_argument("--example", action="store_true", help="print an example configuration to commit as 'notifications'")
    p_nt.add_argument("--actor", type=str, default="cli")

    p_se = sub.add_parser("seismic", help="the second leg's ingest: miniSEED/SAC in, spectra and persistent lines out, an FDSN fetch, and the detectability test against known rigs")
    p_se.add_argument("--action", choices=["info", "spectrum", "lines", "fetch", "stations", "detect", "selftest", "convert", "response", "remove-response",
                                           "beam", "array-detect", "locate", "array-selftest", "sar-film", "bearings", "track", "track-selftest",
                                           "signatures", "multi-beam", "multi-bearings", "multi-track", "signature-selftest", "field-selftest",
                                           "harmonics", "harmonic-selftest", "datum", "geodesy-selftest", "array-qc", "qc-selftest", "unlisted", "unlisted-selftest", "bearing-sigma", "coverage", "uncertainty-selftest",
                                           "assoc-selftest"], default="info")
    p_se.add_argument("--signatures", type=str, default=None, help="multi-beam/multi-bearings: the signatures JSON from --action signatures")
    p_se.add_argument("--histories", type=str, nargs="+", default=None, help="track: two or more bearing-history JSONs (from --action bearings), one per array")
    p_se.add_argument("--truth", type=str, default=None, help="track: ground truth CSV (point_id, lat, lon, utc[, note]) - the lateral's points in time")
    p_se.add_argument("--name", type=str, default=None, help="bearings: a name for the array in the history")
    p_se.add_argument("--scene", type=str, default="rigs", choices=["rigs", "lateral"], help="sar-film: the labelled scene - fixed rigs, or a bit advancing along a lateral past two arrays")
    p_se.add_argument("--hours", type=float, default=8.0, help="sar-film: the synthetic scene's length, h (default 8: four rigs, each working alone for an hour)")
    p_se.add_argument("--step", type=float, default=600.0, help="sar-film: one frame per this many seconds (default 600)")
    p_se.add_argument("--seed", type=int, default=5, help="sar-film: the scene's random seed")
    p_se.add_argument("--files", type=str, nargs="+", default=None, help="beam/array-detect: one record per sensor, in the order of --sensors")
    p_se.add_argument("--sensors", type=str, default=None, help="beam/array-detect: CSV of the array's sensors (sensor_id, lat, lon[, elevation_m])")
    p_se.add_argument("--bearings", type=str, default=None, help="locate: CSV of arrays' bearings (lat, lon, back_azimuth_deg, sigma_deg)")
    p_se.add_argument("--method", type=str, default="bartlett", choices=["bartlett", "capon"], help="beam: the beamformer")
    p_se.add_argument("--seg", type=float, default=10.0, help="beam: Welch segment length, s (default 10)")
    p_se.add_argument("--smax", type=float, default=3.0, help="beam: slowness grid half-width, s/km (default 3: apparent velocities down to 0.33 km/s)")
    p_se.add_argument("--stationxml", type=str, default=None, help="the station's FDSN StationXML (level=response); with spectrum/lines/detect the response is removed first")
    p_se.add_argument("--output", type=str, default="VEL", choices=["VEL", "DISP", "ACC"], help="ground-motion unit after response removal (default VEL, m/s)")
    p_se.add_argument("--water-level", type=float, default=60.0, help="water level for the deconvolution, dB (default 60)")
    p_se.add_argument("--pre-filt", type=float, nargs=4, default=None, metavar=("F1", "F2", "F3", "F4"), help="cosine band window applied before the deconvolution, Hz")
    p_se.add_argument("--with-response", action="store_true", help="fetch: also fetch the StationXML with responses, beside the record")
    p_se.add_argument("--file", type=str, default=None, help="a miniSEED or SAC file (decided by content)")
    p_se.add_argument("--trace", type=int, default=0, help="which trace of the file when it holds several (default the first)")
    p_se.add_argument("--band", type=float, nargs=2, default=[1.0, 50.0], metavar=("LO", "HI"), help="frequency band, Hz (default 1 50: rig machinery)")
    p_se.add_argument("--win", type=float, default=None, help="window length, s (spectrum/lines default 60; detect default 600)")
    p_se.add_argument("--snr-db", type=float, default=6.0, help="a line or a source must stand this far above the floor (default 6 dB)")
    p_se.add_argument("--persistence", type=float, default=0.5, help="lines: the fraction of windows a line must be present in (default 0.5)")
    p_se.add_argument("--base", type=str, default="iris", help="FDSN centre: iris, texnet, or a base URL")
    p_se.add_argument("--network", type=str, default=None)
    p_se.add_argument("--station", type=str, default=None)
    p_se.add_argument("--location", type=str, default="*")
    p_se.add_argument("--channel", type=str, default=None)
    p_se.add_argument("--start", type=str, default=None, help="ISO UTC")
    p_se.add_argument("--end", type=str, default=None, help="ISO UTC")
    p_se.add_argument("--station-lat", type=float, default=None, help="detect: the station's latitude")
    p_se.add_argument("--station-lon", type=float, default=None)
    p_se.add_argument("--sources", type=str, default=None, help="detect: CSV of known sources (source_id, lat, lon, start_utc, end_utc[, kind, note, datum])")
    p_se.add_argument("--datum", type=str, default=None, help="datum: the datum the positions are on (WGS84, NAD83, NAD27) when the files do not say")
    p_se.add_argument("--measure-sigma", action="store_true", help="bearings/track: measure each window's bearing uncertainty from its own sub-windows instead of taking the array's resolution, and draw the ellipses from it (several times slower)")
    p_se.add_argument("--true-bearing", type=float, default=None, help="coverage: the bearing known independently of this record, in degrees")
    p_se.add_argument("--parts", type=int, default=5, help="bearing-sigma/coverage: how many sub-windows each window is cut into (default 5)")
    p_se.add_argument("--encoding", type=str, default="STEIM2", choices=["STEIM1", "STEIM2", "INT32", "FLOAT32", "FLOAT64"], help="convert: output encoding")
    p_se.add_argument("--out", type=str, default=None, help="fetch: the .mseed to write; spectrum: a CSV; detect/lines: a JSON")
    p_se.add_argument("--json", action="store_true")

    p_sw = sub.add_parser("swaps", help="sensor swap register: list, record a swap, propose candidates from a file; a swap segments the drift fit")
    p_sw.add_argument("--register", type=str, required=True, help="the well's sensor_swaps.jsonl")
    p_sw.add_argument("--action", choices=["list", "add", "detect"], default="list")
    p_sw.add_argument("--file", type=str, default=None, help="historian file for --action detect")
    p_sw.add_argument("--tag", type=str, default=None)
    p_sw.add_argument("--at", type=str, default=None, help="swap time, ISO UTC")
    p_sw.add_argument("--old-serial", type=str, default="")
    p_sw.add_argument("--new-serial", type=str, default="")
    p_sw.add_argument("--certificate", type=str, default="")
    p_sw.add_argument("--note", type=str, default="")
    p_sw.add_argument("--actor", type=str, default="cli")

    p_ce = sub.add_parser("certificates", help="calibration certificates per instrument: list, file one, status against today")
    p_ce.add_argument("--register", type=str, required=True, help="the well's certificates.jsonl")
    p_ce.add_argument("--action", choices=["list", "add", "status", "conformance"], default="list")
    p_ce.add_argument("--tag", type=str, default=None)
    p_ce.add_argument("--tags", type=str, default=None, help="comma-separated tags for --action status")
    p_ce.add_argument("--serial", type=str, default="")
    p_ce.add_argument("--certificate", type=str, default="")
    p_ce.add_argument("--lab", type=str, default="")
    p_ce.add_argument("--issued", type=str, default=None, help="ISO date")
    p_ce.add_argument("--valid-until", type=str, default=None, help="ISO date")
    p_ce.add_argument("--accuracy-pct-fs", type=float, default=None)
    p_ce.add_argument("--full-scale", type=float, default=None)
    p_ce.add_argument("--unit", type=str, default="")
    p_ce.add_argument("--doc", type=str, default=None, help="the certificate file (hashed, not copied)")
    p_ce.add_argument("--note", type=str, default="")
    p_ce.add_argument("--actor", type=str, default="cli")
    p_ce.add_argument("--calibrated", type=str, default="", help="ISO/IEC 17025 7.8.2.1 i: the date the calibration was performed")
    p_ce.add_argument("--method", type=str, default="", help="7.8.2.1 f: the method used")
    p_ce.add_argument("--authorised-by", type=str, default="", help="7.8.2.1 o: who authorised the certificate")
    p_ce.add_argument("--uncertainty", type=float, default=None, help="7.8.4.1 a: the expanded uncertainty U, in --uncertainty-unit (default the result's unit)")
    p_ce.add_argument("--uncertainty-unit", type=str, default="")
    p_ce.add_argument("--k", type=float, default=None, help="7.8.4.1 a: the coverage factor U is stated at")
    p_ce.add_argument("--probability", type=float, default=None, help="7.8.4.1 a: the coverage probability as a fraction (0.95)")
    p_ce.add_argument("--conditions", type=str, default="", help="7.8.4.1 b: the conditions the calibration was made under")
    p_ce.add_argument("--traceability", type=str, default="", help="7.8.4.1 c: the statement of metrological traceability, as the certificate words it")
    p_ce.add_argument("--adjustment", type=str, default="", help="7.8.4.1 d: results before and after adjustment, if the certificate carries them")
    p_ce.add_argument("--decision-rule", type=str, default="", help="7.8.6: the decision rule the certificate's conformity statement names, if any")

    p_tr = sub.add_parser("transient", help="shut-in detection and build-up analysis (Horner + Bourdet derivative with a bootstrap band)")
    p_tr.add_argument("--file", type=str, required=True, help="historian file")
    p_tr.add_argument("--pressure", type=str, default=None, help="pressure channel (default: guessed)")
    p_tr.add_argument("--rate", type=str, default=None)
    p_tr.add_argument("--on-stream", type=str, default=None)
    p_tr.add_argument("--criteria", type=str, default=None, help="shut_in.json")
    p_tr.add_argument("--params", type=str, default=None, help="transient_params.json: q_stb_d, B_rb_stb, mu_cp, h_ft, phi, ct_1_psi, rw_ft")
    p_tr.add_argument("--shut-in", type=str, default=None, help="analyse only this shut-in id")
    p_tr.add_argument("--mtr", type=str, default=None, help="middle-time region override as start_h:end_h")
    p_tr.add_argument("--name", type=str, default=None)
    p_tr.add_argument("--out", type=str, default=None, help="write the report to this directory")
    p_tr.add_argument("--json", action="store_true")

    p_fl = sub.add_parser("files", help="the site's files: import/export roots, browse with type detection, import into a well, export reports, evidence pack, watch folders")
    p_fl.add_argument("--workspace", type=str, required=True)
    p_fl.add_argument("--action", type=str, default="list", choices=["roots", "add-root", "remove-root", "list", "detect", "preview", "import", "export", "pack", "watch"])
    p_fl.add_argument("--which", type=str, default="import", choices=["import", "export"], help="add-root/remove-root: which kind of root")
    p_fl.add_argument("--name", type=str, default=None, help="root name; import: display name of the new well")
    p_fl.add_argument("--path", type=str, default=None, help="add-root: the folder; detect/preview: any file path")
    p_fl.add_argument("--root", type=str, default=None, help="list/import/watch: import root; export/pack: export root")
    p_fl.add_argument("--rel", type=str, default="", help="path inside the root")
    p_fl.add_argument("--report", type=str, default=None, help="export: a file or folder under reports/ (e.g. accuracy_statement.html or wells/Well-A)")
    p_fl.add_argument("--dest", type=str, default="", help="export/pack: folder inside the export root")
    p_fl.add_argument("--station-md", type=float, default=None)
    p_fl.add_argument("--actor", type=str, default="cli")

    p_sy = sub.add_parser("survey", help="a LAS or DLIS file in, one strata report out (--demo: the bundled public KTB excerpt); --out writes report.txt + survey.json")
    p_sy.add_argument("--file", type=str, default=None, help="LAS 2.0 or DLIS (RP66 V1) file")
    p_sy.add_argument("--frame", type=str, default=None, help="DLIS: the frame to read when the file holds several (gea dlis --file X --info lists them)")
    p_sy.add_argument("--catalog", type=str, default=None, help="a catalogue LAS entry instead of a file (gea catalog --list names them)")
    p_sy.add_argument("--demo", action="store_true")
    p_sy.add_argument("--family", type=str, default="continental_crystalline", help="prior family for the estimator")
    p_sy.add_argument("--lat", type=float, default=None)
    p_sy.add_argument("--elev", type=float, default=None)
    p_sy.add_argument("--out", type=str, default=None, help="directory for report.txt and survey.json")

    a = ap.parse_args(argv)

    from . import (DownholeEngine, ServiceLifeConfig, ServiceLifeSimulator,
                   TelemetryConfig, TelemetryRecorder, GAUGE_SPECS, load_gauge_spec_json,
                   load_well_profile_csv, DEFAULT_TD_FT)

    if a.cmd == "run":
        eng = DownholeEngine(_build_config(a))
        for _ in range(a.steps):
            eng.step()
        out = eng.export_csv(a.out)
        s = eng.summary()
        print(f"run: {s['sensors']} gauges x {a.steps} steps -> {out} "
              f"(datasheet aging {s['avg_drift_pct']} %FS/yr; {s['aging']['n_over_rating']} station(s) over rating)")
    elif a.cmd == "service-life":
        eng = DownholeEngine(_build_config(a))
        cfg = ServiceLifeConfig(years=a.years, recalibration_interval_years=a.recal, seed=a.seed)
        sim = ServiceLifeSimulator(engine=eng, config=cfg).run()
        out = sim.export_csv(a.out)
        d = sim.summary()
        print(f"service-life: {a.years:g} yr -> {out} "
              f"(datasheet rate {d['rate_psi_yr']} psi/yr; years to the {d['error_budget_pct_fs']:g} %FS budget {d['years_to_budget']})")
    elif a.cmd == "telemetry":
        eng = DownholeEngine(_build_config(a))
        rec = TelemetryRecorder(engine=eng, config=TelemetryConfig(duration_hours=a.hours, seed=a.seed)).run()
        out = rec.export_csv(a.out)
        s = rec.telemetry_summary()
        print(f"telemetry: {s['samples']} samples -> {out} "
              f"(uptime {s['uptime_pct']}%, stuck P/R {s['stuck_score']['precision']}/{s['stuck_score']['recall']})")
    elif a.cmd == "ingest":
        from . import ingest as _ingest
        try:
            st = _ingest(a.file, port=a.port)
        except (ValueError, OSError) as e:
            raise SystemExit(f"ingest: {e}")
        import json as _json
        print(_json.dumps(st.summary(), indent=1))
    elif a.cmd == "bench":
        import json as _json
        from .bench import bench_analysis, bench_selftest
        if a.selftest:
            r = bench_selftest()
            print(_json.dumps(r, indent=1))
            return 0 if r["ok"] else 1
        if not a.gauge_csv:
            raise SystemExit("bench needs --gauge-csv (and optionally --reference-csv), or --selftest")
        from .files import read_any
        from .gauge_specs import get_spec

        def _series(path):
            st = read_any(path)
            pc = [c for c in st.channels if c.upper().startswith("P")]
            if not pc:
                raise SystemExit(f"{path}: no pressure channel found")
            return st.index, st.channels[pc[0]].values
        spec = (get_spec(a.spec) if (a.spec or "") in GAUGE_SPECS or not a.spec else load_gauge_spec_json(a.spec))
        t, p = _series(a.gauge_csv)
        rt, rp = (_series(a.reference_csv) if a.reference_csv else (None, None))
        r = bench_analysis(t, p, spec, rt, rp, k_sigma=a.k_sigma, serial=a.serial, certificate_id=a.certificate)
        r["gauge_csv"] = a.gauge_csv
        r["reference_csv"] = a.reference_csv
        r["recorded_utc"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(_json.dumps(r, indent=1))
        if a.out:
            with open(a.out, "a", encoding="utf-8") as f:
                f.write(_json.dumps(r, sort_keys=True) + "\n")
            print(f"bench: record appended to {a.out}", file=sys.stderr)
    elif a.cmd == "gamma":
        import json as _json
        from .gamma import gamma_entries, gamma_report
        if not a.catalog:
            for n, chans in sorted(gamma_entries().items()):
                print(f"{n}: {', '.join(chans)}")
            return 0
        print(_json.dumps(gamma_report(a.catalog, channel=a.channel, cutoff=a.cutoff,
                                       gr_clean=a.gr_clean, gr_shale=a.gr_shale), indent=1))
    elif a.cmd == "accept":
        from .acceptance_tests import main as _accept
        return _accept()
    elif a.cmd == "operator":
        from .operator_app import launch_operator_app
        return launch_operator_app()
    elif a.cmd == "report":
        from .project import generate_report
        r = generate_report(a.out)
        print("report:", r["report_path"])
        for k, v in r["renders"].items():
            print("  %s: %s" % (k, v))
    elif a.cmd == "wells":
        from . import BUILTIN_ASSEMBLIES
        for name, maker in sorted(BUILTIN_ASSEMBLIES.items()):
            try:
                asm = maker()
            except Exception:
                # operator-tier assemblies on machines without the private
                # data: listed plainly, never crashing the listing
                print(f"{name}: OPERATOR-TIER assembly - private data not "
                      "present on this machine (tier is per-machine, never required)")
                continue
            roles = {r: f"{c.entry}/{c.channel} {c.coverage()[0]:.0f}-{c.coverage()[1]:.0f} m"
                     for r, c in asm.components.items()}
            bridge = "engine-ready" if "temperature" in asm.components else \
                     "no measured T (bridge refuses; lookups still live)"
            print(f"{name}: {asm.site}")
            for r, d in roles.items():
                print(f"    {r:12s} {d}")
            if asm.attachments:
                print(f"    attachments: {', '.join(sorted(asm.attachments))}")
            print(f"    [{bridge}]")
    elif a.cmd == "reconcile":
        from . import ingest as _ingest, Reconciler
        station_map = None
        if a.live_catalog:
            from . import production_live_stream
            if not a.live_well or a.station_md is None:
                raise SystemExit("--live-catalog needs --live-well and --station-md "
                                 "(the archived excerpt does not state the gauge depth; "
                                 "the CLI will not invent one)")
            stream, station_map = production_live_stream(a.live_catalog, a.live_well, a.station_md)
            print(f"[live] {stream.name} | {len(stream.index)} samples | "
                  f"NaN days dropped: {stream.meta.get('nan_days_dropped')}")
        elif a.file:
            stream = _ingest(a.file, port=a.port)
        else:
            raise SystemExit("reconcile needs --file OR --live-catalog")
        rep = Reconciler(_build_config(a)).reconcile(stream, station_map=station_map)
        import json as _json
        print(_json.dumps(rep, indent=1))
    elif a.cmd == "survey":
        import json as _json
        from .survey_cmd import run_survey
        if not (a.file or a.demo or a.catalog):
            raise SystemExit("survey needs --file mywell.las (a LAS or DLIS), --catalog collingwood_1_28_ks_complete, or --demo")
        txt, machine = run_survey(path=a.file, demo=a.demo, family=a.family, lat=a.lat, elev=a.elev, frame=a.frame, catalog=a.catalog)
        print(txt)
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, "report.txt"), "w", encoding="utf-8") as f:
                f.write(txt)
            with open(os.path.join(a.out, "survey.json"), "w", encoding="utf-8") as f:
                _json.dump(machine, f, indent=1, default=str)
            print("written:", os.path.join(a.out, "report.txt"))
        return 0
    elif a.cmd == "files":
        import json as _json
        from .workspace import Workspace, WorkspaceError
        from . import files as F
        try:
            ws = Workspace(a.workspace)
            if a.action == "roots":
                print(_json.dumps({"import": F.Roots(ws).list("import"), "export": F.Roots(ws).list("export")}, indent=1))
            elif a.action == "add-root":
                print(_json.dumps(F.Roots(ws).add(a.which, a.name or os.path.basename(os.path.abspath(a.path)), a.path, a.actor), indent=1))
            elif a.action == "remove-root":
                F.Roots(ws).remove(a.which, a.name, a.actor); print("removed", a.which, "root", a.name)
            elif a.action == "list":
                r = F.browse(ws, a.root, a.rel)
                for e in r["entries"]:
                    print(f"{e['type']:4s} {e.get('kind', ''):14s} {e.get('bytes', ''):>10} {e['name']}")
            elif a.action == "detect":
                print(_json.dumps(F.detect(a.path), indent=1))
            elif a.action == "preview":
                pv = F.preview(a.path); print(pv["kind"], "-", pv["detail"]); print("\n".join(pv["lines"]))
            elif a.action == "import":
                w = F.import_file(ws, a.root, a.rel, a.actor, display=a.name, station_md_ft=a.station_md); print("well:", w["id"])
            elif a.action == "export":
                r = F.export_report(ws, a.report, a.root, a.dest, a.actor); print(f"exported {len(r['files'])} file(s) to", os.path.dirname(r["files"][0]) if r["files"] else "")
            elif a.action == "pack":
                dest = F.Roots(ws).resolve("export", a.root, a.dest); os.makedirs(dest, exist_ok=True)
                out = os.path.join(dest, f"evidence_{ws.manifest['name'].replace(' ', '_')}_{__import__('datetime').datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip")
                m = F.evidence_pack(ws, out, a.actor); print("evidence pack:", m["path"], m["n_files"], "files, sha256", m["sha256"][:16])
            elif a.action == "watch":
                print(_json.dumps(F.scan_root(ws, a.root, a.actor), indent=1))
        except WorkspaceError as e:
            raise SystemExit(f"files: {e}")
        return 0
    elif a.cmd == "swaps":
        import json as _json
        from .sensor_swap import SwapRegister, detect_swaps
        reg = SwapRegister(a.register)
        if a.action == "add":
            try:
                print(_json.dumps(reg.add(a.tag or "", a.at or "", a.actor, a.old_serial, a.new_serial, a.certificate, a.note), indent=1))
            except ValueError as e:
                raise SystemExit(f"swaps: {e}")
            return 0
        if a.action == "detect":
            from .files import read_any
            if not a.file:
                raise SystemExit("swaps: --file is required for detect")
            c = detect_swaps(read_any(a.file), tags=[a.tag] if a.tag else None)
            print(_json.dumps(c, indent=1))
            print(f"swaps: {len(c)} candidate(s) - confirm one with --action add", file=sys.stderr)
            return 0
        for e in reg.list():
            print(f"{e['swap_utc']}  {e['tag_id']:24s} {e.get('old_serial') or '-':>12s} -> {e['new_serial']:<12s} cert {e.get('certificate_id') or '-'}  by {e['recorded_by']}  {e.get('note', '')}")
        print(f"swaps: {len(reg.list())} on record")
        return 0
    elif a.cmd == "certificates":
        import json as _json
        from .certificates import CertificateRegister
        reg = CertificateRegister(a.register)
        if a.action == "add":
            try:
                print(_json.dumps(reg.add(a.tag or "", a.serial, a.certificate, a.issued or "", a.valid_until or "", a.actor, a.lab,
                                          a.accuracy_pct_fs, a.full_scale, a.unit, a.doc, a.note,
                                          calibrated_utc=a.calibrated, method=a.method, authorised_by=a.authorised_by, uncertainty=a.uncertainty,
                                          uncertainty_unit=a.uncertainty_unit, uncertainty_k=a.k, uncertainty_probability=a.probability,
                                          conditions=a.conditions, traceability=a.traceability, adjustment=a.adjustment, decision_rule=a.decision_rule), indent=1))
            except (ValueError, TypeError) as e:
                raise SystemExit(f"certificates: {e}")
            return 0
        if a.action == "status":
            tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()] or sorted({e['tag_id'] for e in reg.list()})
            rows = reg.status(tags)
            for r in rows:
                print(f"{r['tag_id']:24s} {r['status']:9s} {r.get('certificate_id') or '-':16s} until {r.get('valid_until_utc') or '-'}  {('' if r['days_left'] is None else str(r['days_left']) + ' d')}")
            print(_json.dumps(CertificateRegister.summary(rows)))
            return 0 if CertificateRegister.summary(rows)['all_valid'] else 1
        if a.action == "conformance":
            from .conformance import report_lines
            c = reg.conformance()
            for r in c["rows"]:
                print("\n".join(report_lines(r)))
            print(f"certificates: {c['n']} filed, {c['complete']} COMPLETE against ISO/IEC 17025:2017 7.8" + (f"; incomplete: {', '.join(c['incomplete'])}" if c['incomplete'] else ''))
            return 0 if not c["incomplete"] else 1
        for e in reg.list():
            print(f"{e['tag_id']:24s} {e['serial']:12s} {e['certificate_id']:16s} {e['issued_utc'][:10]} to {e['valid_until_utc'][:10]}  ±{e.get('accuracy_pct_fs') or '-'} % FS  {e.get('lab', '')}")
        print(f"certificates: {len(reg.list())} filed")
        return 0
    elif a.cmd == "transient":
        import json as _json
        from .files import read_any
        from .shut_in import detect_shut_ins, extract_buildup, load_criteria as _si_crit
        from .transient import analyze_buildup
        stream = read_any(a.file)
        det = detect_shut_ins(stream, a.pressure, a.rate, a.on_stream, _si_crit(a.criteria))
        prm = None
        if a.params:
            with open(a.params, encoding="utf-8") as f:
                prm = _json.load(f)
        mtr = None
        if a.mtr:
            lo, hi = a.mtr.split(":")
            mtr = {"start_dt_h": float(lo), "end_dt_h": float(hi)}
        analyses = []
        for si in det["shut_ins"]:
            if a.shut_in and si["shut_in_id"] != a.shut_in:
                continue
            if not si["qualified"] and not a.shut_in:
                continue
            b = extract_buildup(stream, si)
            r = analyze_buildup(b["dt_h"], b["p_ws"], b["tp_h"], b["p_wf"], prm, mtr=mtr)
            r["shut_in_id"] = si["shut_in_id"]
            analyses.append(r)
        if a.json:
            print(_json.dumps({"detection": det, "analyses": analyses}, indent=1, default=str))
        else:
            print(f"transient: {len(det['shut_ins'])} shut-in(s) by {det['mode']}, {det.get('n_qualified', 0)} qualified, {len(analyses)} analysed")
            for r in analyses:
                if r.get("status") == "OK":
                    h, dd = r["horner"], r.get("derived", {})
                    print(f"  {r['shut_in_id']}: m = {h['m_psi_per_cycle']:.2f} psi/cycle, p* = {h['p_star']:.1f}"
                          + (f", k = {dd['k_md']:.1f} md" if "k_md" in dd else "") + (f", skin = {dd['skin']:+.2f}" if "skin" in dd else "")
                          + f"  [MTR {r['mtr']['start_dt_h']:.3g}-{r['mtr']['end_dt_h']:.3g} h]")
                else:
                    print(f"  {r['shut_in_id']}: {r.get('status')} - {'; '.join(r.get('caveats', []))}")
        if a.out:
            from .client_reports import transient_report, write as _write_report
            from . import __version__ as _v
            paths = _write_report(transient_report(det, analyses, well_name=a.name or stream.name, program_version=_v), a.out, basename="pressure_transient_report")
            print("report:", paths.get("html"), file=sys.stderr if a.json else sys.stdout)
        return 0
    elif a.cmd == "housekeeping":
        import json as _json
        from .housekeeping import run as _hk
        r = _hk(a.workspace, apply=a.apply, rotate_mb=a.rotate_mb, jobs_keep_days=a.jobs_keep_days, jobs_keep_n=a.jobs_keep_n,
                records_keep_days=a.records_keep_days, actor=a.actor)
        if a.json:
            print(_json.dumps(r, indent=1))
        else:
            print(f"housekeeping ({'applied' if a.apply else 'dry run - add --apply'}): {len(r['rotated'])} log(s) to rotate, "
                  f"{len(r['jobs_removed'])} finished job folder(s) and {len(r['records_removed'])} old recording(s) to remove, {r['bytes_freed'] / 1048576:.1f} MB")
            for x in r['rotated']:
                print(f"  rotate  {x['file']} ({x['size_mb']} MB) -> {os.path.basename(x['segment'])}")
            for x in r['jobs_removed'][:20]:
                print(f"  job     {x['job']}  {x['age_days']} d  {x['bytes']} B")
            for x in r['records_removed'][:20]:
                print(f"  record  {x['well_id']}/{x['file']}  {x['age_days']} d")
        return 0
    elif a.cmd == "elevation":
        from .elevation import EPQS_URL, epqs
        r = epqs(a.lat, a.lon, url=a.url or EPQS_URL)
        if a.json:
            print(json.dumps(r, indent=1)); return 0 if r["status"] == "OK" else 1
        if r["status"] != "OK":
            print(f"elevation: {r['status']} - {r.get('detail', '')}", file=sys.stderr); return 1
        print(f"ground elevation at {a.lat}, {a.lon}: {r['elev_ft']:,.2f} ft ({r['vertical_datum']})")
        print(f"  source: {r['source']}")
        return 0
    elif a.cmd == "structure":
        import json as _json
        from . import shapefile as _SF
        from . import structure as _ST
        if a.list:
            ms = _SF.members(a.layer)
            print("\n".join(ms) if ms else f"no shapefile in {a.layer}")
            return 0 if ms else 1
        try:
            L = _SF.read(a.layer, member=a.member)
        except (ValueError, OSError) as e:
            raise SystemExit(f"structure: {e}")
        ta, na = _ST.guess_attrs(L)
        kind = _ST.layer_kind(L)
        if a.info or a.lat is None or a.lon is None:
            md = L.source.get("metadata", {})
            info = {"name": L.name, "kind": kind, "shape_type": L.shape_type, "crs": {k: v for k, v in L.crs.items() if k != "wkt"}, "bbox": list(L.bbox), "n_features": L.n,
                    "fields": L.fields, "type_attr": ta, "name_attr": na, "value_attr": _ST.guess_value_attr(L) if kind == "contours" else None,
                    "type_counts": dict(list(L.counts(ta).items())[:20]) if ta else {}, "metadata": md}
            if a.json:
                print(_json.dumps(info, indent=1))
            else:
                print(f"{L.name} ({kind}): {L.n} {L.shape_type} features; {L.crs['kind']} {L.crs.get('datum') or ''}; extent lon {L.bbox[0]:.3f}..{L.bbox[2]:.3f} lat {L.bbox[1]:.3f}..{L.bbox[3]:.3f}")
                print(f"  fields: {', '.join(f['name'] + ' (' + f['type'] + ')' for f in L.fields)}; type attribute {ta!r}, name attribute {na!r}")
                for k, v in list(info["type_counts"].items())[:12]:
                    print(f"  {v:7d}  {k}")
                if md.get("titles"):
                    print(f"  source: {'; '.join(md['titles'][:2])}" + (f" ({md['pubdates'][0]})" if md.get("pubdates") else ""))
            if a.lat is None or a.lon is None:
                return 0
        r = _ST.describe(L, a.lat, a.lon, a.radius_km, ta, na)
        if a.json:
            print(_json.dumps(r, indent=1, default=str))
        else:
            for line in _ST.lines(r):
                print(line)
            for h in r.get("within", [])[:10]:
                print(f"  {h['distance_km']:8.2f} km  {h['bearing_deg']:5.0f} deg  {h['kind']:5s}  {h['type']}  {h['name']}")
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "soak":
        import json as _json
        from .soak import run as _soak
        secs = a.seconds if a.seconds is not None else (a.hours * 3600.0 if a.hours is not None else 600.0)
        r = _soak(seconds=secs, patches=a.patches, etp=not a.no_etp, seedlink=not a.no_seedlink, outage_every_s=a.outage_every, outage_s=a.outage_len,
                  interval_s=a.interval, with_service=a.with_service, keep=a.keep, out_dir=a.out, reconnect_budget_s=a.reconnect_budget, verbose=not a.json)
        if a.json:
            print(_json.dumps({k: v for k, v in r.items() if k != "samples_timeline"}, indent=1, default=str))
        return 0 if r["ok"] else 1
    elif a.cmd == "loadtest":
        import json as _json
        from .loadtest import run as _lt
        r = _lt(patches=a.patches, seconds=a.seconds, interval_s=a.interval, with_service=a.with_service, keep=a.keep, verbose=not a.json)
        if a.json:
            print(_json.dumps(r, indent=1))
        return 0 if r["ok"] else 1
    elif a.cmd == "seismic":
        import json as _json
        from . import seismic as S
        from . import seismic_detect as D

        def _trace(apply_response=True):
            if not a.file:
                raise SystemExit("seismic: --file is needed for this action")
            trs = S.read_any(a.file)
            if not trs:
                raise SystemExit(f"seismic: {a.file} holds no samples")
            if a.trace >= len(trs):
                raise SystemExit(f"seismic: {a.file} holds {len(trs)} trace(s); --trace {a.trace} is out of range")
            tr = trs[a.trace]
            if a.stationxml and apply_response:
                from . import seismic_response as R
                try:
                    chan = R.select(R.read_stationxml(a.stationxml), tr)
                    tr, note = R.remove_response(tr, chan, a.output, a.water_level, tuple(a.pre_filt) if a.pre_filt else None)
                except (LookupError, ValueError) as e:
                    raise SystemExit(f"seismic: response not removed - {e}")
                print(f"seismic: response of {chan.id} removed ({chan.sensor or 'sensor unnamed'}); the record is now in {note['unit']}", file=sys.stderr)
            return trs, tr
        band = (float(a.band[0]), float(a.band[1]))
        if a.action == "sar-film":
            from . import seismic_film as FM
            film = FM.sar_film(seed=a.seed, hours=a.hours, step_s=a.step, band=(float(a.band[0]), float(a.band[1])) if a.band != [1.0, 50.0] else (1.0, 20.0), method=a.method, scene=a.scene)
            if a.out:
                FM.write_film(film, a.out)
            if a.json:
                print(_json.dumps(FM.film_summary(film), indent=1))
            else:
                print(FM.report_text(film))
                if a.out:
                    print(f"film: {a.out} ({len(film['frames'])} frames)")
            return 0
        if a.action == "array-selftest":
            from . import seismic_array as AR
            r = AR.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k != "detectability"} | {"detectability": {kk: vv for kk, vv in r["detectability"].items()}}, indent=1, default=str))
            else:
                print(f"seismic array-selftest: {r['status']} ok={r['ok']}")
                print(AR.report_text(r["detectability"]))
                c = r["crossing"]
                print(f"  three arrays' bearings cross {c['miss_km']:.2f} km from the rig; 1-sigma ellipse {c['ellipse_1sigma']['major_km']} x {c['ellipse_1sigma']['minor_km']} km; "
                      f"crossing angle {c['crossing_angle_deg']} deg; in front of every array: {c['in_front_of_every_array']}")
            return 0 if r["ok"] else 1
        if a.action in ("signature-selftest", "field-selftest"):
            from . import seismic_signature as SG
            r = SG.selftest() if a.action == "signature-selftest" else SG.field_selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k not in ("learned", "beam", "multi_track")}, indent=1, default=str))
            elif a.action == "signature-selftest":
                print(f"seismic signature-selftest: {r['status']} ok={r['ok']}")
                print(SG.report_text(r["beam"]))
                print(f"  the colliding pair (two rigs given the same pump rate): {', '.join(r['collide']['verdicts'])}")
            else:
                print(f"seismic field-selftest: {r['status']} ok={r['ok']}; {len(r['tracked'])} of {len(r['multi_track']['source_ids'])} rigs tracked while all worked at once")
                for sid, row in r["multi_track"]["tracks"].items():
                    v = r["verdicts"].get(sid, {})
                    print(f"  {sid}: {row['status']}, {row.get('positions', 0)} position(s) of {row.get('windows', 0)}"
                          + (f"; verdict {v['verdict']} ({v['hit']} of {v['of']} inside their ellipse)" if v else "")
                          + (f" - {row.get('detail', '')}" if row.get("detail") else ""))
            return 0 if r["ok"] else 1
        if a.action == "assoc-selftest":
            from . import seismic_assoc as _SA
            r = _SA.selftest()
            print(f"association selftest [{r['label']}]: {r['status']} - {r['n_picks']} picks, {len(r['events'])} events, errors {r['errors_km']} km")
            for k, v in r["checks"].items():
                print(f"  {'ok ' if v else 'BAD'} {k}")
            for e in r["events"]:
                print(f"  {e['id']}: {e['origin_utc']}  {e['lat']:.4f}, {e['lon']:.4f}  {e['n_stations']} stations  RMS {e['rms_s']} s  "
                      f"misfit region {e['region_km']['extent_east_km']} x {e['region_km']['extent_north_km']} km  magnitude: not estimated")
            for x in r["catalogue"]["rows"]:
                print(f"  vs catalogue: {x['event']} {x['standing']}")
            return 0 if r["status"] == "OK" else 1
        if a.action == "uncertainty-selftest":
            from . import seismic_uncertainty as UQ
            r = UQ.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k not in ("coverage", "one_window")}, indent=1, default=str))
            else:
                c, o = r["coverage"], r["one_window"]
                print(f"seismic uncertainty-selftest: {r['status']} ok={r['ok']}")
                print(UQ.report_text(c).split("\n  not a measurement")[0])
                print(f"  one window: {o['sigma_deg']:g} deg, measured from {o['n_parts']} sub-windows; the beamformer's own grid is "
                      f"{o['grid_step_deg']:g} deg and the array could only separate two sources {o['resolution_half_width_deg']:g} deg apart, "
                      "which is a different question")
                print(f"  the estimator tried first and turned down - one bearing per frequency bin - scatters by {r['per_bin_scatter_deg']:g} deg on "
                      "this array, because one frequency cannot break its spatial aliasing: that is the geometry, not the data")
            return 0 if r["ok"] else 1
        if a.action in ("bearing-sigma", "coverage"):
            from . import seismic_uncertainty as UQ
            from . import seismic_array as AR
            if not (a.files and a.sensors):
                raise SystemExit(f"seismic {a.action} needs --files (one per sensor, in order) and --sensors CSV")
            if a.action == "coverage" and a.true_bearing is None:
                raise SystemExit("seismic coverage needs --true-bearing: a bearing known independently of this record")
            sensors = AR.load_sensors_csv(a.sensors, a.datum)
            if len(a.files) != len(sensors):
                raise SystemExit(f"seismic {a.action}: {len(a.files)} files for {len(sensors)} sensors")
            trs = []
            for f in a.files:
                t = S.read_any(f)
                if not t:
                    raise SystemExit(f"seismic: {f} holds no samples")
                trs.append(t[a.trace])
            if a.action == "bearing-sigma":
                r = UQ.bearing_sigma(trs, sensors, band, parts=a.parts, diagnose_bins=True)
            else:
                r = UQ.coverage(trs, sensors, a.true_bearing, band, a.win or 300.0, parts=a.parts)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(r, fh, indent=1, default=str)
            print(_json.dumps(r, indent=1, default=str) if a.json else UQ.report_text(r))
            return 0 if r.get("status") in ("OK", "CALIBRATED", "CONSERVATIVE") else 1
        if a.action == "unlisted-selftest":
            from . import seismic_unlisted as UL
            r = UL.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k != "result"}, indent=1, default=str))
            else:
                print(f"seismic unlisted-selftest: {r['status']} ok={r['ok']}")
                print(f"  the rig left off the list was found at {r['bearing_error_deg']:+.2f} deg of its true bearing (tolerance "
                      f"{r['tolerance_deg']:.1f}) running at {r['rate_found_hz']:g} Hz against the scene's {r['rate_true_hz']:g}")
                print(UL.report_text(r["result"]).split("\n  not a measurement")[0])
                print(UL.report_text(r["result"]["candidates"]).split("\n  not a measurement")[0])
                print(f"  with every rig on the list there is nothing to find: {r['with_every_rig_listed']['status']}")
            return 0 if r["ok"] else 1
        if a.action == "unlisted":
            from . import seismic_unlisted as UL
            from . import seismic_array as AR
            if not (a.files and a.sensors and a.signatures):
                raise SystemExit("seismic unlisted needs --files (one per sensor), --sensors CSV and --signatures JSON (from --action signatures)")
            sensors = AR.load_sensors_csv(a.sensors, a.datum)
            if len(a.files) != len(sensors):
                raise SystemExit(f"seismic unlisted: {len(a.files)} files for {len(sensors)} sensors")
            with open(a.signatures, encoding="utf-8") as fh:
                sg = _json.load(fh)
            sigs = sg["signatures"] if isinstance(sg, dict) else sg
            trs = []
            for f in a.files:
                t = S.read_any(f)
                if not t:
                    raise SystemExit(f"seismic: {f} holds no samples")
                trs.append(t[a.trace])
            r = UL.find_unlisted(trs, sensors, sigs, band, a.win or 600.0)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(r, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps(r, indent=1, default=str))
            else:
                print(UL.report_text(r))
                if r.get("candidates"):
                    print(UL.report_text(r["candidates"]))
                if a.out:
                    print(f"  -> {a.out}")
            return 0
        if a.action == "qc-selftest":
            from . import seismic_qc as QC
            r = QC.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k != "qc"}, indent=1, default=str))
            else:
                print(f"seismic qc-selftest: {r['status']} ok={r['ok']}")
                print(QC.report_text(r["qc"]).split("\n  not a measurement")[0])
                print(f"  the clock error put into the scene was {r['timing_injected_ms']:g} ms and the plane fit measured "
                      f"{r['timing_recovered_ms']:g} ms")
                print(QC.report_text(r["beam_cost"]).split("\n  not a measurement")[0])
                print(f"  the same array with no faults in it: {r['clean_array']['status']}, {r['clean_array']['n_usable']} sensors, nothing named")
            return 0 if r["ok"] else 1
        if a.action == "array-qc":
            from . import seismic_qc as QC
            from . import seismic_array as AR
            if not (a.files and a.sensors):
                raise SystemExit("seismic array-qc needs --files (one record per sensor, in order) and --sensors CSV")
            sensors = AR.load_sensors_csv(a.sensors, a.datum)
            if len(a.files) != len(sensors):
                raise SystemExit(f"seismic array-qc: {len(a.files)} files for {len(sensors)} sensors")
            trs = []
            for f in a.files:
                t = S.read_any(f)
                if not t:
                    raise SystemExit(f"seismic: {f} holds no samples")
                trs.append(t[a.trace])
            r = QC.array_qc(trs, sensors, band)
            cost = QC.beam_cost(trs, sensors, r, band) if r["status"] == "DEGRADED" else None
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump({"qc": r, "cost": cost}, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps({"qc": r, "cost": cost}, indent=1, default=str))
            else:
                print(QC.report_text(r))
                if cost:
                    print(QC.report_text(cost))
                if a.out:
                    print(f"  -> {a.out}")
            return 0 if r["status"] == "USABLE" else 1
        if a.action == "geodesy-selftest":
            from . import geodesy as GD
            r = GD.selftest()
            if a.json:
                print(_json.dumps(r, indent=1, default=str))
            else:
                ap = r["against_published"]
                print(f"seismic geodesy-selftest: {r['status']} ok={r['ok']}")
                print(f"  meridian arc to 45 deg N {ap['meridian_arc_to_45N_m']:.3f} m against the published {ap['published_m']:.3f}")
                print(f"  a degree of latitude at the equator {ap['degree_of_latitude_at_equator_m']:.3f} m against {ap['published_lat_m']:.3f}; "
                      f"a degree of longitude {ap['degree_of_longitude_at_equator_m']:.3f} against {ap['published_lon_m']:.3f}")
                gi = r["grid_identities"]
                print(f"  at a transverse Mercator central meridian the easting is {gi['tm_central_meridian_easting_m']:.6f} and the scale "
                      f"{gi['tm_scale_at_cm']}; at a Lambert zone's origin {gi['lcc_origin_easting_m']:.6f}, {gi['lcc_origin_northing_m']:.6f}; "
                      f"at its standard parallel the scale is {gi['lcc_scale_at_standard_parallel']}")
                print(f"  projection round trips close to {r['round_trip_worst_m'] * 1000:.3f} mm (worst at {r['round_trip_worst_at']['zone']} "
                      f"{r['round_trip_worst_at']['lat']}, {r['round_trip_worst_at']['lon']}); the datum round trip to {r['datum_round_trip_m'] * 1000:.3f} mm")
                sp = r["nad27_to_wgs84_at_31N_102W"]
                print(f"  NAD27 against WGS84 at 31 N, 102 W: {sp['separation_m']:.1f} m on a bearing of {sp['bearing_deg']:.0f} deg - computed here, "
                      "not quoted, because it varies across the country")
                print(f"  the same state plane northing read in international rather than US survey feet: {r['us_survey_vs_international_foot_m_at_this_northing']:.1f} m out")
            return 0 if r["ok"] else 1
        if a.action == "datum":
            from . import geodesy as GD
            from . import seismic_detect as D
            from . import seismic_array as AR
            pts = []
            if a.station_lat is not None and a.station_lon is not None:
                pts.append(GD.Position(a.station_lat, a.station_lon, a.datum or "", 0.0, "station"))
            if a.sensors:
                for sn in AR.load_sensors_csv(a.sensors, a.datum):
                    pts.append(GD.Position(sn.lat, sn.lon, sn.datum_as_given or sn.datum, sn.elevation_m, f"sensor {sn.sensor_id}"))
            if a.sources:
                for so in D.load_sources_csv(a.sources, a.datum):
                    pts.append(GD.Position(so.lat, so.lon, so.datum_as_given or so.datum, 0.0, f"source {so.source_id}"))
            if not pts:
                raise SystemExit("seismic datum needs --station-lat/--station-lon, --sensors or --sources (and --datum when the files do not say)")
            r = GD.check_set(pts)
            if a.json:
                print(_json.dumps(r, indent=1, default=str))
            else:
                print(GD.report_text(r))
            return 0 if r["status"] in ("OK",) else 1
        if a.action == "harmonic-selftest":
            from . import seismic_harmonic as HM
            r = HM.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k not in ("tracks", "rate_history", "activity_fixed_bins", "activity_with_drift")}, indent=1, default=str))
            else:
                print(f"seismic harmonic-selftest: {r['status']} ok={r['ok']}")
                fb = r["fixed_bins"]
                print(f"  a signature of fixed bins over this record holds {len(fb['lines_hz'])} line(s) [{fb['status']}]: the machine's rate walked out "
                      "of its own bins, which is what this band is for")
                print(HM.report_text(r["tracks"]).split("\n  not a measurement")[0])
                print("  the harmonics walk together: " + ", ".join(f"order {k} at {v:g}x the first" for k, v in r["drift_ratios"].items()))
                print(HM.report_text(r["families"]).split("\n  not a measurement")[0])
                print(f"  the fundamental in that window {r['fundamental_hz']:g} Hz against the scene's {r['scene_rate_in_that_window_hz']:g} Hz "
                      f"({r['fundamental_error_hz']:+g} Hz)")
                print(HM.report_text(r["rate_history"]).split("\n  not a measurement")[0])
                print(f"  when it was working, from the bins it was learned on: {r['activity_fixed_bins']['verdict']}; allowing for the walk: "
                      f"{r['activity_with_drift']['verdict']} ({r['activity_with_drift']['n_spells']} spell(s))")
                print(f"  {r['dense_lines']['n']} lines with no machine behind them: {r['dense_lines']['status']}")
            return 0 if r["ok"] else 1
        if a.action == "harmonics":
            from . import seismic_harmonic as HM
            from . import seismic_detect as D
            if not a.file:
                raise SystemExit("seismic harmonics needs --file (one record); --signatures adds each source's own rate, --sources the declared windows")
            trs = S.read_any(a.file)
            if not trs:
                raise SystemExit(f"seismic: {a.file} holds no samples")
            tr = trs[a.trace]
            w = a.win or 600.0
            hband = (band[0], min(band[1], 0.4 * tr.sample_rate))
            tl = HM.track_lines(tr, hband, w)
            rate = HM.rate_history(tr, hband, w)
            ts = HM.tracked_signature(tl, source_id=tr.id)
            fam = HM.harmonic_families(ts["freqs_hz"], ts["excess_db"], hband) if ts["status"] == "OK" else None
            sigs = []
            if a.signatures:
                with open(a.signatures, encoding="utf-8") as fh:
                    sg = _json.load(fh)
                sigs = sg["signatures"] if isinstance(sg, dict) else sg
            sources = D.load_sources_csv(a.sources) if a.sources else []
            sf = HM.signature_families(sigs, hband, tracks=tl) if sigs else None
            tol = min(max(float(ts.get("drift_tol_frac") or 0.0), 0.0), 0.5)
            act = [HM.activity_from_signature(tr, x, hband, w, sources=sources, drift_tol_frac=tol) for x in sigs] if (sigs and sources) else []
            res = {"protocol": "seismic_harmonic.cli/1", "record": tr.id, "band_hz": list(hband), "win_s": w, "drift_tol_frac": round(tol, 4),
                   "line_tracks": tl, "rate": rate, "tracked_signature": ts, "families": fam, "signature_families": sf, "activity": act}
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(res, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps(res, indent=1, default=str))
            else:
                print(HM.report_text(tl).split("\n  not a measurement")[0])
                if fam:
                    print(HM.report_text(fam).split("\n  not a measurement")[0])
                print(HM.report_text(rate).split("\n  not a measurement")[0])
                if sf:
                    print(HM.report_text(sf).split("\n  not a measurement")[0])
                    at = sf.get("attribution") or {}
                    for u in (at.get("unattributed") or []):
                        print(f"  no listed source was learned on the line at {u['freq_median_hz']:g} Hz [{u['status']}]: something is making it and this "
                              "program was not told what")
                    for c in (at.get("contested") or []):
                        print(f"  the line at {c['freq_median_hz']:g} Hz is as close to {' as to '.join(c['sources'])}: it separates nothing")
                for x in act:
                    print(HM.report_text(x).split("\n  not a measurement")[0])
                if a.out:
                    print(f"  -> {a.out}")
            return 0
        if a.action == "signatures":
            from . import seismic_signature as SG
            from . import seismic_detect as D
            if not (a.file and a.sources):
                raise SystemExit("seismic signatures needs --file (one record) and --sources (the rigs CSV)")
            trs = S.read_any(a.file)
            if not trs:
                raise SystemExit(f"seismic: {a.file} holds no samples")
            r = SG.learn_signatures(trs[a.trace], D.load_sources_csv(a.sources), band, a.win or 600.0)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(r, fh, indent=1)
            if a.json:
                print(_json.dumps(r, indent=1))
            else:
                print(f"seismic signatures: {r['n_with_signature']} of {len(r['signatures'])} source(s) learned from {r['windows']} windows "
                      f"({r['quiet_windows']} quiet), band {r['band_hz'][0]:g}-{r['band_hz'][1]:g} Hz")
                for sig in r["signatures"]:
                    print(f"  {sig['source_id']}: " + (", ".join(f"{x:g}" for x in sig["freqs_hz"]) + f" Hz  ({sig['detail']})" if sig["status"] == "OK"
                                                       else f"{sig['status']} - {sig['detail']}"))
            return 0
        if a.action in ("multi-beam", "multi-bearings"):
            from . import seismic_signature as SG
            from . import seismic_array as AR
            if not (a.files and a.sensors and a.signatures):
                raise SystemExit(f"seismic {a.action} needs --files (one per sensor), --sensors CSV and --signatures JSON")
            sensors = AR.load_sensors_csv(a.sensors)
            if len(a.files) != len(sensors):
                raise SystemExit(f"seismic {a.action}: {len(a.files)} files for {len(sensors)} sensors")
            with open(a.signatures, encoding="utf-8") as fh:
                sigs = _json.load(fh)
            sigs = sigs["signatures"] if isinstance(sigs, dict) else sigs
            trs = []
            for f in a.files:
                t = S.read_any(f)
                if not t:
                    raise SystemExit(f"seismic: {f} holds no samples")
                tr = t[0]
                if a.start or a.end:
                    tr = tr.slice(S.parse_time(a.start) if a.start else tr.starttime, S.parse_time(a.end) if a.end else tr.endtime)
                trs.append(tr)
            if a.action == "multi-beam":
                r = SG.beam_by_signature(trs, sensors, sigs, band, a.seg, a.smax, 41, a.method)
                if a.out:
                    with open(a.out, "w", encoding="utf-8") as fh:
                        _json.dump(r, fh, indent=1)
                print(_json.dumps(r, indent=1) if a.json else SG.report_text(r))
                return 0
            r = SG.multi_bearing_history(trs, sensors, sigs, band, a.win or 600.0, None, a.seg, a.smax, 41, a.method, name=a.name or sensors[0].sensor_id)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(r, fh, indent=1)
            if a.json:
                print(_json.dumps(r, indent=1))
            else:
                print(f"seismic multi-bearings: {r['n_windows']} windows of {r['win_s']:g} s, {len(r['source_ids'])} source(s)")
                for sid in r["source_ids"]:
                    h = r["histories"][sid]
                    print(f"  {sid}: {h['n_coherent']} of {h['n_windows']} window(s) pointed, tolerance {h['tolerance_deg']} deg")
            return 0
        if a.action == "multi-track":
            from . import seismic_signature as SG
            from . import seismic_track as T
            if not a.histories or len(a.histories) < 2:
                raise SystemExit("seismic multi-track needs --histories (two or more multi-bearing JSONs, one per array)")
            per = []
            for hp in a.histories:
                with open(hp, encoding="utf-8") as fh:
                    per.append(_json.load(fh))
            truth = None
            if a.truth:
                pts = T.load_truth_csv(a.truth)
                truth = {}
                for pt in pts:
                    truth.setdefault(pt.point_id, []).append(pt)
            r = SG.multi_track(per, truth)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(r, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k != "tracks"}, indent=1, default=str))
            else:
                print(f"seismic multi-track: {r['n_tracked']} of {len(r['source_ids'])} source(s) positioned from {len(per)} arrays")
                for sid, row in r["tracks"].items():
                    if row["status"] != "OK":
                        print(f"  {sid}: {row['status']} - {row.get('detail', '')}")
                        continue
                    tr_ = row.get("track") or {}
                    print(f"  {sid}: {row['positions']} position(s) of {row['windows']}"
                          + (f", heading {tr_['heading_deg']} deg over {tr_['length_km']} km" if tr_.get("heading_deg") is not None else "")
                          + (f"; verdict {row['verdict']['verdict']} ({row['verdict']['n_hit']} of {row['verdict']['n_compared']})" if row.get("verdict") else ""))
            return 0
        if a.action == "track-selftest":
            from . import seismic_track as T
            r = T.selftest()
            if a.json:
                print(_json.dumps({k: v for k, v in r.items() if k != "bearing_histories"}, indent=1, default=str))
            else:
                print(f"seismic track-selftest: {r['status']} ok={r['ok']}")
                for h in r["bearing_histories"]:
                    print(f"  {h['array']['name']}: {h['n_coherent']} coherent of {h['n_windows']} windows, tolerance {h['tolerance_deg']} deg, span {h['span_deg']} deg, {len(h['change_points'])} change point(s)")
                print(T.report_text(r["positions"], r["verdict"]))
            return 0 if r["ok"] else 1
        if a.action == "track":
            from . import seismic_track as T
            if not a.histories or len(a.histories) < 2:
                raise SystemExit("seismic track needs --histories (two or more bearing-history JSONs)")
            hists = []
            for hp in a.histories:
                with open(hp, encoding="utf-8") as fh:
                    hists.append(_json.load(fh))
            pos = T.position_history(hists)
            ver = T.track_verdict(pos, T.load_truth_csv(a.truth)) if a.truth else None
            res = {"positions": pos, "verdict": ver}
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(res, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps(res, indent=1, default=str))
            else:
                print(T.report_text(pos, ver))
            return 0
        if a.action in ("beam", "array-detect", "bearings"):
            from . import seismic_array as AR
            if not a.files or not a.sensors:
                raise SystemExit(f"seismic {a.action} needs --files (one per sensor) and --sensors CSV")
            sensors = AR.load_sensors_csv(a.sensors)
            if len(a.files) != len(sensors):
                raise SystemExit(f"seismic {a.action}: {len(a.files)} files for {len(sensors)} sensors")
            trs = []
            for f in a.files:
                t = S.read_any(f)
                if not t:
                    raise SystemExit(f"seismic: {f} holds no samples")
                tr = t[0]
                if a.start or a.end:
                    tr = tr.slice(S.parse_time(a.start) if a.start else tr.starttime, S.parse_time(a.end) if a.end else tr.endtime)
                trs.append(tr)
            if a.action == "bearings":
                from . import seismic_track as T
                h = T.bearing_history(trs, sensors, band, a.win or 600.0, None, a.seg, a.smax, 41, a.method, measure_sigma=a.measure_sigma)
                h["array"]["name"] = a.name or sensors[0].sensor_id
                if a.out:
                    with open(a.out, "w", encoding="utf-8") as fh:
                        _json.dump(h, fh, indent=1)
                if a.json:
                    print(_json.dumps(h, indent=1))
                else:
                    print(f"seismic bearings: {h['array']['name']}, {h['n_coherent']} coherent of {h['n_windows']} windows of {h['win_s']:g} s, tolerance {h['tolerance_deg']} deg"
                          + (f", span {h['span_deg']} deg" if h['span_deg'] is not None else "") + f", {len(h['change_points'])} change point(s)")
                    for w in h["windows"]:
                        print(f"  {w['start_utc']}  " + (f"{w['back_azimuth_deg']:7.2f} deg  coherence {w['coherence_max_bin']}" if w.get("back_azimuth_deg") is not None else "no beam")
                              + ("" if w["coherent"] else "  (gap: not coherent)"))
                return 0
            if a.action == "beam":
                b = AR.beam(trs, sensors, band, a.seg, a.smax, 61, a.method)
                b = {k: v for k, v in b.items() if not k.startswith("_")}
                if a.out:
                    with open(a.out, "w", encoding="utf-8") as fh:
                        _json.dump(b, fh, indent=1)
                if a.json:
                    print(_json.dumps(b, indent=1))
                else:
                    print(f"seismic beam: {b['n_sensors']} sensors, aperture {b['aperture_km']} km, {b['window']['start']} to {b['window']['end']}, band {band[0]:g}-{band[1]:g} Hz ({b['method']})")
                    print(f"  back-azimuth {b['back_azimuth_deg']} deg +/- {b['resolution']['azimuth_half_width_deg']} (array response), slowness {b['slowness_s_km']} s/km "
                          f"(apparent velocity {b['apparent_velocity_km_s']} km/s), best-bin coherence {b['coherence_max_bin']} with {b['coherent_bins']} coherent bins"
                          + (f" at {', '.join(f'{x:g}' for x in b['coherent_frequencies_hz'][:6])} Hz" if b['coherent_frequencies_hz'] else ''))
                    if b["resolution"]["aliasing_lobes"]:
                        print("  the array response has aliasing lobes at this band: a lone peak may be a lobe of the geometry")
                    for pk in b["peaks"][1:4]:
                        print(f"  also: {pk['back_azimuth_deg']} deg at {pk['slowness_s_km']} s/km (power {pk['power']})")
                    print(f"  plane-wave limit: sources nearer than {b['plane_wave_limit_km']} km are not plane waves here")
                    print("  not a measurement: " + "; ".join(b["not_a_measurement"]))
                return 0
            if not a.sources:
                raise SystemExit("seismic array-detect needs --sources CSV")
            res = AR.array_detectability(trs, sensors, D.load_sources_csv(a.sources), band, a.win or 600.0, a.seg, a.smax, 61, a.method)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(res, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps(res, indent=1, default=str))
            else:
                print(AR.report_text(res))
            return 0
        if a.action == "locate":
            from . import seismic_array as AR
            import csv as _csv
            if not a.bearings:
                raise SystemExit("seismic locate needs --bearings CSV (lat, lon, back_azimuth_deg, sigma_deg)")
            with open(a.bearings, newline="", encoding="utf-8") as fh:
                rows = [{k.strip(): float(v) for k, v in r.items() if k} for r in _csv.DictReader(fh)]
            c = AR.intersect_backazimuths(rows)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(c, fh, indent=1)
            if a.json:
                print(_json.dumps(c, indent=1))
            else:
                e = c["ellipse_1sigma"]
                print(f"seismic locate: {len(rows)} bearings cross at ({c['lat']:.5f}, {c['lon']:.5f}); 1-sigma ellipse {e['major_km']} x {e['minor_km']} km "
                      f"(major axis at {e['major_azimuth_deg']} deg); crossing angle {c['crossing_angle_deg']} deg; residuals {c['residual_deg']} deg; "
                      f"in front of every array: {c['in_front_of_every_array']}")
                print("  not a measurement: " + "; ".join(c["not_a_measurement"]))
            return 0
        if a.action == "response":
            from . import seismic_response as R
            if not a.stationxml:
                raise SystemExit("seismic response needs --stationxml")
            chans = R.read_stationxml(a.stationxml)
            if a.json:
                print(_json.dumps([c.summary() for c in chans], indent=1))
            else:
                for c in chans:
                    sm = c.summary()
                    print(f"{c.id:18s} {sm['start'] or '..'} to {sm['end'] or '..'}  ({sm['latitude']}, {sm['longitude']}) {sm['sample_rate_hz']} Hz  "
                          f"{sm['sensor']}  sensitivity {sm['sensitivity']} {sm['output_units']}/({sm['input_units']}) at {sm['sensitivity_frequency_hz']} Hz  stages {', '.join(sm['stages'])}")
                print(f"seismic: {len(chans)} channel(s) in {a.stationxml}")
            return 0
        if a.action == "remove-response":
            from . import seismic_response as R
            if not a.stationxml or not a.out:
                raise SystemExit("seismic remove-response needs --stationxml and --out")
            trs, _ = _trace(apply_response=False)
            resp = R.read_stationxml(a.stationxml)
            done, notes = [], []
            for tr in trs:
                try:
                    chan = R.select(resp, tr)
                    out_tr, note = R.remove_response(tr, chan, a.output, a.water_level, tuple(a.pre_filt) if a.pre_filt else None)
                except (LookupError, ValueError) as e:
                    print(f"  {tr.id}: left in counts - {e}", file=sys.stderr)
                    continue
                done.append(out_tr)
                notes.append({"id": tr.id, **note})
            if not done:
                raise SystemExit("seismic remove-response: no trace had a usable response")
            S.write_mseed(done, a.out, "FLOAT64")
            with open(a.out + ".units.json", "w", encoding="utf-8") as fh:
                _json.dump({"unit": done[0].unit, "traces": notes}, fh, indent=1)
            print(f"seismic: {len(done)} trace(s) in {done[0].unit} -> {a.out} (FLOAT64 miniSEED; the unit and the response record are in {a.out}.units.json)")
            return 0
        if a.action == "selftest":
            r = D.selftest()
            if a.json:
                print(_json.dumps(r, indent=1, default=str))
            else:
                print(f"seismic selftest: {r['status']} ok={r['ok']}")
                print(D.report_text(r["result"]))
            return 0 if r["ok"] else 1
        if a.action == "info":
            trs, _ = _trace()
            infos = [t.info() for t in trs]
            if a.json:
                print(_json.dumps(infos, indent=1))
            else:
                for i in infos:
                    print(f"{i['id']:18s} {i['start']} to {i['end']}  {i['sample_rate_hz']:g} Hz  {i['npts']} samples ({i['duration_s']:.1f} s)  {i['encoding']}  "
                          f"{i['gaps']} gap(s)  [{i['unit']}]")
            return 0
        if a.action == "convert":
            trs, _ = _trace()
            if not a.out:
                raise SystemExit("seismic convert: --out is needed")
            S.write_mseed(trs, a.out, a.encoding)
            print(f"seismic: {len(trs)} trace(s) written to {a.out} as {a.encoding}")
            return 0
        if a.action in ("spectrum", "lines"):
            _, tr = _trace()
            win = a.win or 60.0
            if a.action == "spectrum":
                f, p = S.welch_psd(tr.data, tr.sample_rate)
                m = (f >= band[0]) & (f <= band[1])
                if a.out:
                    S.spectrum_csv(f[m], p[m], a.out, tr)
                    print(f"seismic: Welch PSD of {tr.id} ({tr.duration_s:.0f} s) in {band[0]:g}-{band[1]:g} Hz -> {a.out} [{'counts^2/Hz, response not removed' if tr.unit == 'counts' else '(' + tr.unit + ')^2/Hz, response removed'}]")
                else:
                    top = sorted(zip(f[m], p[m]), key=lambda z: -z[1])[:10]
                    print(f"seismic: Welch PSD of {tr.id}, {int(m.sum())} bins in {band[0]:g}-{band[1]:g} Hz (df {f[1] - f[0]:.4f} Hz); the ten strongest bins:")
                    for fr, pv in top:
                        print(f"  {fr:8.3f} Hz  {10 * __import__('math').log10(max(pv, 1e-30)):7.1f} dB")
                return 0
            spec = S.spectrogram(tr, win)
            L = S.persistent_lines(spec, band, snr_db=a.snr_db, min_fraction=a.persistence)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(L, fh, indent=1)
            if a.json:
                print(_json.dumps(L, indent=1))
            else:
                print(f"seismic: {len(L['lines'])} persistent line(s) in {band[0]:g}-{band[1]:g} Hz over {L['windows']} windows of {win:g} s ({tr.id}); "
                      f"a line is >= {a.snr_db:g} dB over its floor in >= {a.persistence:.0%} of windows")
                for ln in L["lines"][:25]:
                    print(f"  {ln['freq_hz']:8.3f} Hz  {ln['median_excess_db']:+6.1f} dB over the floor  in {ln['persistence']:.0%} of windows")
                print(f"  not a measurement: {L['not_a_measurement']}")
            return 0
        if a.action == "stations":
            if not a.network:
                raise SystemExit("seismic stations: --network is needed")
            st = S.fdsn_stations(a.base, a.network, a.station or "*", a.channel or "*", a.start, a.end)
            if a.json:
                print(_json.dumps(st, indent=1))
            else:
                for x in st:
                    print(f"{x['network']}.{x['station']:6s} {x['latitude']:9.4f} {x['longitude']:10.4f}  {x['site']}  {x['start'][:10]}..{x['end'][:10]}")
                print(f"seismic: {len(st)} station(s) from {S.fdsn_url(a.base, 'station').split('/fdsnws')[0]}")
            return 0
        if a.action == "fetch":
            miss = [k for k in ("network", "station", "channel", "start", "end", "out") if not getattr(a, k)]
            if miss:
                raise SystemExit(f"seismic fetch needs --{' --'.join(miss)}")
            rec = S.fdsn_fetch(a.base, a.network, a.station, a.channel, a.start, a.end, a.out, a.location)
            print(f"seismic: {rec['bytes']} bytes -> {a.out} ({rec['unit']}); request written to {a.out}.request.json")
            if a.with_response:
                from . import seismic_response as R
                xml_out = os.path.splitext(a.out)[0] + ".stationxml"
                rx = R.fdsn_stationxml(a.base, a.network, a.station, a.channel, a.start, a.end, xml_out, a.location)
                print(f"seismic: station file with responses -> {xml_out} ({', '.join(rx['channels']) or 'no channels'})")
            return 0
        if a.action == "detect":
            _, tr = _trace()
            if a.station_lat is None or a.station_lon is None or not a.sources:
                raise SystemExit("seismic detect needs --station-lat --station-lon and --sources CSV")
            srcs = D.load_sources_csv(a.sources)
            res = D.detectability_test(tr, a.station_lat, a.station_lon, srcs, band, win_s=a.win or 600.0, snr_db=a.snr_db)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    _json.dump(res, fh, indent=1, default=str)
            if a.json:
                print(_json.dumps(res, indent=1, default=str))
            else:
                print(D.report_text(res))
            return 0
    elif a.cmd == "notify":
        import json as _json
        from .notify import Notifier, NotifyError, EXAMPLE
        if a.example:
            print(_json.dumps(EXAMPLE, indent=1))
            return 0
        if not a.workspace:
            raise SystemExit("notify: --workspace is required (or --example)")
        n = Notifier(a.workspace)
        n.reload()
        if a.test:
            try:
                r = n.test(a.test, a.actor)
            except NotifyError as e:
                raise SystemExit(f"notify: {e}")
            print(_json.dumps(r, indent=1, default=str))
            return 0 if r.get("ok") else 1
        if a.log is not None:
            for e in n.tail(a.log):
                print(f"{e.get('utc')}  {'ok ' if e.get('ok') else 'ERR'}  {e.get('event'):18s} {e.get('channel') or '-':12s} {e.get('subject') or e.get('result')}")
            return 0
        print(f"notifications: {'configured' if n.cfg else 'not configured'}" + (f" - {len(n.cfg['channels'])} channel(s), {len(n.cfg['rules'])} rule(s), quiet {n.cfg['quiet_s']} s" if n.cfg else " (gea notify --example)"))
        return 0
    elif a.cmd == "conformance":
        from . import conformance as _C
        if a.action == "decide":
            if a.value is None or a.tolerance is None:
                raise SystemExit("conformance --action decide needs --value and --tolerance")
            d = _C.decide(a.value, a.tolerance, a.uncertainty, rule=a.rule, k=a.k if a.uncertainty is not None else None,
                          probability=(0.95 if abs(a.k - 2.0) < 1e-9 else None) if a.uncertainty is not None else None, unit=a.unit)
            print(d["statement"]); print(f"  {d['rule_text']}")
            return 0 if d["outcome"] in ("PASS", "CONDITIONAL PASS") else 1
        r = _C.selftest()
        print(f"conformance selftest [{r['label']}]: {r['status']}")
        for k, v in r["checks"].items():
            print(f"  {'ok ' if v else 'BAD'} {k}")
        print("\n".join(_C.report_lines(r["full"])))
        for d in r["decisions"]:
            print("  " + d["statement"])
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "vibration":
        from . import vibration as _V
        if a.action == "assess":
            if not a.record:
                raise SystemExit("vibration --action assess needs --record")
            brg = None
            if a.bearing:
                parts = [float(x) for x in a.bearing.split(",")]
                if len(parts) not in (3, 4):
                    raise SystemExit("--bearing is elements,ball_mm,pitch_mm[,contact_deg]")
                brg = {"n_elements": int(parts[0]), "d_mm": parts[1], "D_mm": parts[2], "contact_deg": parts[3] if len(parts) == 4 else 0.0}
            try:
                rec = _V.read_record(a.record, a.channel)
            except ValueError as e:
                raise SystemExit(f"vibration: {e}")
            r = _V.assess(rec["x"], rec["fs"], a.unit, a.rpm, group=a.group, support=a.support, bearing=brg,
                          band_hz=tuple(a.demod_band) if a.demod_band else None, label=f"{rec['name']}:{rec['channel']}", sensitivity=a.sensitivity)
            r["gaps"] = list(r["gaps"]) + list(rec["notes"])
            if a.json:
                print(json.dumps({k: v for k, v in r.items()}, indent=1, default=str))
            else:
                print(_V.report_text(r))
            return 0 if r["status"] == "ASSESSED" else 1
        r = _V.selftest()
        print(f"vibration selftest [{r['label']}]: {r['status']}")
        for k, v in r["checks"].items():
            print(f"  {'ok ' if v else 'BAD'} {k}")
        print(_V.report_text(r["assessment"]))
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "ppdm":
        from . import ppdm as _P
        if a.action == "parse":
            if not a.number:
                raise SystemExit("ppdm --action parse needs --number")
            r = _P.parse_well_number(a.number)
            print(f"{r['input']}: {r['status']}" + (f" - {r['form']}; identifies the {r['identifies'].replace('_', ' ')}" if r.get('form') and r['status'] == 'VALID' else ''))
            if r.get('state'):
                print(f"  state {r['state']['code']} ({r['state']['name'] or 'not a state code'}), county {r['county']['code']}, unique well {r['unique_well']['code']}"
                      f" ({r['unique_well']['range'] or '-'})" + (f"; sidetrack {r['sidetrack']['code']}: {r['sidetrack']['meaning']}" if r.get('sidetrack') else '')
                      + (f"; event {r['event']['code']}: {r['event']['meaning']}" if r.get('event') else ''))
            for x in r["problems"]:
                print(f"  problem: {x}")
            for x in r.get("notes") or []:
                print(f"  note: {x}")
            return 0 if r["status"] == "VALID" else 1
        r = _P.selftest()
        print(f"ppdm selftest [{r['label']}]: {r['status']}")
        for k, v in r["checks"].items():
            print(f"  {'ok ' if v else 'BAD'} {k}")
        print(_P.report_text(r["identity"]))
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "quakeml":
        from . import quakeml as _Q
        r = _Q.selftest(a.out)
        print(f"quakeml selftest [{r['label']}]: {r['status']}")
        for k, v in r["checks"].items():
            print(f"  {'ok ' if v else 'BAD'} {k}")
        print(_Q.report_text({**r, 'status': 'WRITTEN' if r['status'] == 'OK' else 'FAILED', 'agency': 'SIMULATION_SELF_TEST site', 'refused': 2}))
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "osdu":
        from . import osdu as _O
        r = _O.selftest()
        print(f"osdu selftest [{r['label']}]: {r['status']}")
        for k, v in r["checks"].items():
            print(f"  {'ok ' if v else 'BAD'} {k}")
        print(_O.report_text(r["summary"]))
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "sra":
        from . import sra as _SRA
        r = _SRA.selftest()
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "packet"}, indent=1))
        else:
            print(f"sra selftest [{r['label']}]: {r['status']}")
            for k, v in r["checks"].items():
                print(f"  {'ok ' if v else 'BAD'} {k}")
            print(f"  deep well volume {r['deep_mean_volume_bbl_day']} bbl/day; probe offset {r['probe_offset_psi']} psi")
            print(_SRA.report_text(r["packet"]))
        return 0 if r["status"] == "OK" else 1
    elif a.cmd == "doctor":
        from .doctor import run as _doctor
        return _doctor(a.workspace, a.host, a.port, a.json)
    elif a.cmd == "permits":
        import json as _json
        from . import permits as PM
        mapping = None
        if a.mapping:
            with open(a.mapping, encoding="utf-8") as fh:
                mapping = _json.load(fh)
        try:
            note = PM.import_permits(a.file, a.out, mapping=mapping, default_days=a.default_days, within=tuple(a.within) if a.within else None,
                                     datum=a.datum, zone=a.zone, unit=a.unit)
        except ValueError as e:
            raise SystemExit(f"permits: {e}")
        print(_json.dumps(note, indent=1) if a.json else PM.report_text(note))
        return 0
    elif a.cmd == "update":
        from .doctor import update as _update
        return _update(check_only=a.check, extras=a.extras, as_json=a.json)
    elif a.cmd == "checklist":
        import json as _json
        from . import setup_checklist as _SC
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        if not a.workspace:
            raise SystemExit("checklist needs --workspace, e.g. --workspace C:\\site")
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"checklist: {e}")
        if a.confirm:
            try:
                rec = _SC.confirm(ws, a.confirm, a.actor, a.note)
            except ValueError as e:
                raise SystemExit(f"checklist: {e}")
            print(f"confirmed {a.confirm} by {rec['by']} at {rec['utc']}: {rec['note']}")
            return 0
        r = _SC.checklist(ws)
        if a.json:
            print(_json.dumps(r, indent=1))
        else:
            print(_SC.text(r))
        return 0 if r["ready"] else 2
    elif a.cmd == "economics":
        import json as _json
        from . import economics as _EC
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"economics: {e}")
        try:
            if a.rate is not None:
                r = _EC.set_rate(ws, a.well or "", a.actor, a.rate, a.what, a.source, a.date_from, a.date_to)
                print(f"{a.well}: {r['usd_per_day']:,.2f} USD/day {r['what']} from {r['from'] or 'open'} to {r['to'] or 'open'}")
                return 0
            if a.item is not None:
                r = _EC.add_item(ws, a.well or "", a.actor, a.item, a.what, a.source, a.section)
                print(f"{a.well}: {r['usd']:,.2f} USD {r['what']}")
                return 0
            if a.afe is not None:
                r = _EC.set_afe(ws, a.well or "", a.actor, a.afe, a.source)
                print(f"{a.well}: AFE {r['usd']:,.2f} USD ({r['source']})")
                return 0
            if a.refresh_prices:
                for r in _EC.refresh_prices(ws, a.actor):
                    print(f"{r['id']}: {r['status']}" + (f" to {r['as_of']} ({r['rows']} days)" if r.get('as_of') else f" - {r.get('why', '')}"))
                return 0
            if a.import_series:
                with open(a.import_series, "rb") as f:
                    r = _EC.import_series(ws, a.actor, a.name, a.unit, a.source, f.read())
                print(f"{r['name']}: {r['rows']} values, {r['first']} to {r['as_of']} ({r['unit']})")
                return 0
        except (ValueError, KeyError, OSError, WorkspaceError) as e:
            raise SystemExit(f"economics: {e.args[0] if e.args else e}")
        e = _EC.economics(ws)
        print(_json.dumps(e, indent=1) if a.json else _EC.text(e))
        return 0
    elif a.cmd == "well-report":
        from . import well_report as _WR
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"well-report: {e}")
        ids = [w["id"] for w in ws.wells()]
        if a.well and a.well not in ids:
            raise SystemExit(f"well-report: no well {a.well} (the wells are: {', '.join(ids) or 'none'})")
        todo = [] if a.pad else ([a.well] if a.well else ids)
        for wid in todo:
            r = _WR.write_well(ws, wid, __version__, actor=a.actor)
            print(f"{wid}: {r['paths']['html']}" + (f"\n  not in it yet: {'; '.join(r['missing'])}" if r["missing"] else ""))
        if a.pad or not a.well:
            r = _WR.write_pad(ws, __version__, actor=a.actor)
            print(f"pad: {r['paths']['html']}")
        return 0
    elif a.cmd == "drillstring":
        import json as _json
        from . import drill_string as _DS
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"drillstring: {e}")
        try:
            if a.declare:
                with open(a.declare, encoding="utf-8") as f:
                    d = _json.load(f)
                r = _DS.save_run(ws, a.actor, d.get("well_id", ""), d.get("name", ""), d.get("components") or [], d.get("start"), d.get("end"),
                                 d.get("depth_in_ft"), d.get("depth_out_ft"), d.get("note", ""), d.get("run_id"))
                print(f"declared {r['id']}: {r['name']} on {r['well_id']}, {len(r['components'])} component(s)")
                return 0
            if a.remove:
                _DS.remove_run(ws, a.actor, a.remove); print(f"removed {a.remove}"); return 0
            p = _DS.page(ws, a.well)
        except (ValueError, KeyError, OSError, WorkspaceError) as e:
            raise SystemExit(f"drillstring: {e.args[0] if e.args else e}")
        print(_json.dumps(p, default=str) if a.json else _DS.text(p))
        return 0
    elif a.cmd == "declare":
        import json as _json
        from . import declarations as _DC
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"declare: {e}")
        if a.template:
            with open(a.template, "w", encoding="utf-8", newline="") as f:
                f.write(_DC.template(ws))
            print(f"wrote {a.template}: fill in the blanks, then gea declare --workspace {a.workspace} --file {a.template}")
            return 0
        if not a.file:
            raise SystemExit("declare: give --file <sheet.csv> (or --template <out.csv> to start one)")
        try:
            with open(a.file, encoding="utf-8-sig") as f:
                text_ = f.read()
        except OSError as e:
            raise SystemExit(f"declare: {e}")
        p = _DC.apply(ws, text_, a.actor, os.path.dirname(os.path.abspath(a.file)), write=a.apply)
        print(_json.dumps(p, default=str) if a.json else _DC.text(p))
        return 1 if p["errors"] else 0
    elif a.cmd == "penetrator":
        import json as _json
        from . import penetrator as _PN
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        ws = None
        if a.workspace:
            try:
                ws = Workspace(a.workspace)
            except WorkspaceError as e:
                raise SystemExit(f"penetrator: {e}")
        try:
            if a.remove:
                if ws is None or not a.well:
                    raise SystemExit("penetrator: --remove needs --workspace and --well")
                _PN.remove(ws, a.actor, a.well, a.remove)
                print(f"removed the {a.remove} record of {a.well}")
                return 0
            if a.well:
                if ws is None:
                    raise SystemExit("penetrator: --well needs --workspace")
                c = _PN.check(ws, a.well)
                print(_json.dumps(c, default=str) if a.json else _PN.text_check(c))
                return 0
            p = _PN.page(ws)
        except (ValueError, KeyError, OSError) as e:
            raise SystemExit(f"penetrator: {e.args[0] if e.args else e}")
        print(_json.dumps(p, default=str) if a.json else _PN.text(p))
        return 0
    elif a.cmd == "simulation":
        import json as _json
        from . import simulation as _SIM
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        ws = None
        if a.workspace:
            try:
                ws = Workspace(a.workspace)
            except WorkspaceError as e:
                raise SystemExit(f"simulation: {e}")
        if not a.scene:
            for s_ in _SIM.scenes(ws):
                print(f"{s_['id']:<28} {s_['title']}  ({s_['note']})")
            return 0
        try:
            sim = _SIM.scene(ws, a.scene)
        except KeyError as e:
            raise SystemExit(f"simulation: {e.args[0] if e.args else e}")
        if a.json:
            print(_json.dumps(sim if a.at is None else _SIM.at(sim, a.at * (0.3048 if sim.get('depth_unit') == 'ft' else 1.0)), default=str))
        else:
            print(_SIM.text(sim, a.at))
        return 0
    elif a.cmd in ("trajectory", "well3d"):
        import json as _json
        from . import well3d as _W3
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        ws = None
        if a.cmd == "trajectory" or a.scene == "workspace":
            if not a.workspace:
                raise SystemExit(f"{a.cmd} needs --workspace, e.g. --workspace C:\\site")
            try:
                ws = Workspace(a.workspace)
            except WorkspaceError as e:
                raise SystemExit(f"{a.cmd}: {e}")
        if a.cmd == "trajectory":
            try:
                with open(a.file, "rb") as f:
                    content = f.read()
                r = _W3.attach_trajectory(ws, a.well, a.file, content, a.actor, a.azimuth_reference, a.md_unit, a.depth_reference, a.grid,
                                          a.declination, a.source)
            except (ValueError, OSError, WorkspaceError) as e:
                raise SystemExit(f"trajectory: {e}")
            print(f"{a.well}: {r['stations']} stations, {r['md_range_m'][0]:,.1f} to {r['md_range_m'][1]:,.1f} m MD, azimuths {r['azimuth_reference']}"
                  + (f" ({r['grid']})" if r.get('grid') else "") + f"; kept as wells/{a.well}/trajectory/{r['file']}")
            return 0
        sc = _W3.scene(ws, a.scene)
        print(_json.dumps(sc, indent=1) if a.json else _W3.text(sc))
        return 0
    elif a.cmd in ("wall", "handover"):
        import json as _json
        from . import control_room as _CR
        from .cli import _safe_stdout
        from .workspace import Workspace, WorkspaceError
        _safe_stdout()
        if not a.workspace:
            raise SystemExit(f"{a.cmd} needs --workspace, e.g. --workspace C:\\site")
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"{a.cmd}: {e}")
        if a.cmd == "wall":
            b = _CR.board(ws)
            print(_json.dumps(b, indent=1) if a.json else _CR.text(b))
            return 0
        try:
            if a.write:
                if not a.actor:
                    raise SystemExit("handover --write needs --actor, the name the note is signed with")
                b = _CR.board(ws)
                counts = {**b["counts"], "alarms_unacked": sum(((t.get("alarms") or {}).get("unacked") or 0) for t in b["tiles"])}
                n = _CR.write_note(ws, a.actor, a.open, a.watching, a.done, counts)
                print(f"handover {n['id']} signed by {n['by']} at {n['utc']}")
                return 0
            if a.ack:
                if not a.actor:
                    raise SystemExit("handover --ack needs --actor, the incoming operator's name")
                r = _CR.ack_note(ws, a.ack, a.actor, a.comment)
                print(f"handover {a.ack} acknowledged by {r['by']} at {r['utc']}")
                return 0
        except (ValueError, KeyError, PermissionError) as e:
            raise SystemExit(f"handover: {e.args[0] if e.args else e}")
        ns = _CR.notes(ws, 0)
        if a.json:
            print(_json.dumps(ns, indent=1))
        elif not ns:
            print("no handover notes yet")
        else:
            for n in ns:
                print(f"{n['id']}  {n['utc']}  by {n['by']}  " + (f"acknowledged by {n['ack']['by']} at {n['ack']['utc']}" if n.get('ack') else "NOT YET ACKNOWLEDGED"))
                for k in _CR.NOTE_FIELDS:
                    if n.get(k):
                        print(f"    {k}: {n[k]}")
        return 0
    elif a.cmd == "serve":
        from .service import Service
        from .workspace import WorkspaceError
        from .doctor import check_environment, check_workspace, code_matches_launch
        pre = [x for x in check_environment() + check_workspace(a.workspace, a.host, a.port) if x["level"] == "block"]
        mism = code_matches_launch()
        for x in pre:
            print(f"serve: BLOCK {x['what']}\n       fix: {x['fix']}")
        if mism:
            print(f"serve: BLOCK {mism}")
        if pre or mism:
            raise SystemExit("serve: not starting - fix the above (gea doctor --workspace ... shows the full picture)")
        try:
            svc = Service(a.workspace, host=a.host, port=a.port, workers=a.workers, scheduler=not a.no_scheduler, behind_proxy=a.behind_proxy)
        except WorkspaceError as e:
            raise SystemExit(f"serve: {e}")
        n_users = svc.app.users.count()
        print(f"gea {__version__}: serving workspace '{svc.ws.manifest['name']}' at {svc.url}")
        print(f"   code: {os.path.dirname(os.path.abspath(__file__))}   (an installed copy serves the files it was installed from; pip install -e <clone> follows the clone)")
        print(f"   patches: {len(svc.app.patches.store.list())} defined, {len([s for s in svc.app.patches.states() if s['status'] != 'STOPPED'])} running")
        if n_users == 0:
            print("   no accounts yet: the page will ask for the first administrator's name and password (one time), or run: gea users --workspace ... --action add --role admin --name ...")
        if a.host not in ("127.0.0.1", "localhost", "::1") and not a.behind_proxy:
            print("   NOTE: bound to a network address without --behind-proxy: traffic is plain HTTP. Put TLS in front (deploy/README.md) for anything beyond a trusted LAN.")
        print("   Ctrl+C stops it; every action is in records/audit.jsonl, every start and stop in records/runlog.jsonl")
        print("   the control panel's Stop and Restart end this run with a stated code: 0 stop, 86 start me again -")
        print("   the launcher reads it, and on anything but 86 it hands the window to a PowerShell prompt, never a Python one")
        print("   this window is the server and has nothing more to load: the control panel is the page in the browser,")
        print("   and what happens there (the page opening, sign-ins, jobs, stops and restarts) is printed below as it happens")
        svc.console = True
        svc.open_browser = not a.no_browser
        return svc.serve_forever()
    elif a.cmd == "users":
        import getpass
        from .workspace import Workspace, WorkspaceError
        from .service import Users, ApiError
        try:
            ws = Workspace(a.workspace)
        except WorkspaceError as e:
            raise SystemExit(f"users: {e}")
        users = Users(os.path.join(ws.path, "users.json"))
        try:
            if a.action == "list":
                for u in users.list():
                    print(f"{u['name']:24s} {u['role']:10s} {'disabled' if u['disabled'] else 'active':8s} since {u['created_utc']}")
                if not users.list():
                    print("no accounts yet")
                return 0
            if not a.name:
                raise SystemExit("users: --name is required")
            if a.action in ("add", "password"):
                pw = os.environ.get(a.password_env) or (getpass.getpass(f"password for {a.name}: ") if sys.stdin.isatty() else "")
                if a.action == "add":
                    u = users.add(a.name, pw, a.role); ws.audit(a.actor, "user.add", {"name": a.name, "role": a.role}); print("added", u["name"], u["role"])
                else:
                    users.set_password(a.name, pw); ws.audit(a.actor, "user.password", {"name": a.name}); print("password set for", a.name)
            elif a.action == "role":
                users.set_role(a.name, a.role); ws.audit(a.actor, "user.role", {"name": a.name, "role": a.role}); print(a.name, "->", a.role)
            elif a.action in ("disable", "enable"):
                users.set_disabled(a.name, a.action == "disable"); ws.audit(a.actor, f"user.{a.action}", {"name": a.name}); print(a.name, a.action + "d")
        except ApiError as e:
            raise SystemExit(f"users: {e.message}")
        return 0
    elif a.cmd == "workspace":
        import json as _json
        from .workspace import Workspace, WorkspaceError
        try:
            if a.action == "init":
                ws = Workspace.create(a.path, a.name or os.path.basename(os.path.abspath(a.path)), actor=a.actor)
                print("workspace:", ws.path); return 0
            ws = Workspace(a.path)
            if a.action == "rename":
                if not a.name:
                    raise SystemExit("rename needs --name")
                r = ws.rename(a.name, actor=a.actor)
                print(f"site: {r['name']}" + (f"  (was {r['from']})" if r['changed'] else "  (unchanged)")); return 0
            if a.action == "add-file":
                if not a.file:
                    raise SystemExit("add-file needs --file")
                w = ws.add_well_file(a.file, display=a.name, actor=a.actor, station_md_ft=a.station_md)
            elif a.action == "add-catalog":
                if not (a.entry and a.well and a.station_md is not None):
                    raise SystemExit("add-catalog needs --entry, --well and --station-md")
                w = ws.add_well_catalog(a.entry, a.well, a.station_md, actor=a.actor, display=a.name)
            elif a.action == "add-live":
                if not (a.name and a.port and a.file):
                    raise SystemExit("add-live needs --name, --port and --file (the port configuration JSON)")
                w = ws.add_well_live(a.name, a.port, a.file, actor=a.actor, station_md_ft=a.station_md)
            elif a.action == "remove":
                if not a.well:
                    raise SystemExit("remove needs --well WELL_ID")
                ws.remove_well(a.well, actor=a.actor); print("removed (folder kept):", a.well); return 0
            elif a.action == "migrate":
                if not a.from_dir:
                    raise SystemExit("migrate needs --from FOLDER (one written by gea dashboard --out)")
                print(_json.dumps(ws.migrate_out_dir(a.from_dir, actor=a.actor), indent=1)); return 0
            elif a.action == "refresh":
                r = ws.refresh_dashboard(actor=a.actor, outages=a.outage, month=a.month, run_sat=a.sat)
                print("dashboard:", r.get("index") if isinstance(r, dict) else r); return 0
            elif a.action == "audit":
                for row in ws.audit_log(limit=50):
                    print(row["utc"], row["actor"], row["action"], _json.dumps(row["detail"]))
                return 0
            elif a.action == "add-seismic":
                if not (a.name and a.files):
                    raise SystemExit("add-seismic needs --name and --files (one record, or one per sensor with --sensors)")
                st = ws.add_seismic_station(a.name, a.files, a.lat, a.lon, actor=a.actor, stationxml=a.stationxml, sensors_csv=a.sensors,
                                            sources_csv=a.sources, band=a.band, permits_csv=a.permits,
                                            datum=a.datum, permits_zone=a.zone, permits_unit=a.unit)
                print("seismic station:", st["id"], f"({st['kind']}, {len(st['files'])} record(s))"); return 0
            elif a.action == "refresh-seismic":
                ids = [a.station] if a.station else [x["id"] for x in ws.seismic_stations()]
                if not ids:
                    raise SystemExit("refresh-seismic: no seismic station in the workspace")
                for sid in ids:
                    r = ws.refresh_seismic(sid, actor=a.actor)
                    print(f"seismic {sid}: {r['records']} record(s), {r['hours']} h, {r['unit']}; {r['lines']} line(s)"
                          + (f"; detectability {r['detect_status']}: {r['detected']} of {r['n_sources']} detected - {r['radius']}" if r["detect_status"] else "; no detectability test (needs --sources and a position)")
                          + (f"; beam {r['beam_back_azimuth_deg']} deg, pointed at {r['pointed']}" if r["beam_back_azimuth_deg"] is not None else "")
                          + f"\n  report: {r['report']}")
                return 0
            elif a.action == "add-track":
                if not (a.name and a.stations):
                    raise SystemExit("add-track needs --name and --stations (two or more array station ids)")
                tk = ws.add_track(a.name, a.stations, actor=a.actor, truth_csv=a.truth, band=a.band, win_s=a.win)
                print("track:", tk["id"], f"({len(tk['stations'])} arrays" + (f", truth {tk['truth']}" if tk["truth"] else ", no ground truth") + ")"); return 0
            elif a.action == "refresh-track":
                ids = [a.track] if a.track else [x["id"] for x in ws.tracks()]
                if not ids:
                    raise SystemExit("refresh-track: no track in the workspace")
                for tid in ids:
                    r = ws.refresh_track(tid, actor=a.actor)
                    print(f"track {tid}: {r['arrays']} arrays, {r['positions']} positions of {r['windows']} windows"
                          + (f", {r['segments']} segment(s), heading {r['heading_deg']} deg, length {r['length_km']} km" if r["heading_deg"] is not None else "")
                          + (f"; verdict {r['verdict']} ({r['hit_fraction']:.0%})" if r["verdict"] else "; no ground truth") + f"\n  report: {r['report']}")
                return 0
            elif a.action == "remove-track":
                if not a.track:
                    raise SystemExit("remove-track needs --track TRACK_ID")
                ws.remove_track(a.track, actor=a.actor); print("removed (folder kept):", a.track); return 0
            elif a.action == "sar-film":
                r = ws.sar_film(actor=a.actor, seed=a.seed, hours=a.hours, step_s=a.step, band=tuple(a.band) if a.band else (1.0, 20.0), method=a.method, scene=a.scene)
                print(f"sar-film: {r['label']}, {r['frames']} frames over {r['hours']} h; verdicts {r['verdicts']}\n  film: {r['path']}"); return 0
            elif a.action == "add-site":
                if not a.name:
                    raise SystemExit("add-site needs --name")
                st = ws.add_site(a.name, wells=a.wells, seismic=a.seismic, tracks=a.tracks, client=a.client, note=a.note or "", actor=a.actor)
                print(f"site: {st['id']} ({len(st['wells'])} well(s), {len(st['seismic'])} seismic station(s), {len(st['tracks'])} track(s))")
                return 0
            elif a.action == "sites":
                rows = ws.sites()
                if not rows:
                    print("no sites yet; `--action add-site --name \"Retama block\" --wells SWD-1 --seismic TX-PB01 --tracks pad-7-lateral`")
                for st in rows:
                    print(f"  {st['id']:<24} {st.get('client') or '-':<20} {len(st['wells'])} well(s), {len(st['seismic'])} station(s), "
                          f"{len(st['tracks'])} track(s)")
                return 0
            elif a.action == "site-report":
                if not a.site:
                    raise SystemExit("site-report needs --site SITE_ID")
                r = ws.write_site_report(a.site, actor=a.actor)
                c = r["summary"]["counts"]
                print(f"site-report: {a.site} - {c['wells']} well(s), {c['seismic']} seismic station(s), {c['tracks']} track(s)")
                for w in r["summary"]["warnings"]:
                    print("  needs attention:", w)
                print("  report:", r["report"])
                return 0
            elif a.action == "remove-site":
                if not a.site:
                    raise SystemExit("remove-site needs --site SITE_ID")
                ws.remove_site(a.site, actor=a.actor); print("removed (folder kept):", a.site); return 0
            elif a.action == "set-well":
                if not a.well:
                    raise SystemExit("set-well needs --well WELL_ID")
                surface = {"lat": a.lat, "lon": a.lon, "datum": a.datum} if (a.lat is not None or a.lon is not None) else {}
                if a.elev_3dep:
                    from .elevation import EPQS_URL, as_reference, epqs
                    w0 = ws.well(a.well)
                    pos = {"lat": a.lat, "lon": a.lon} if a.lat is not None else (w0.get("surface") or {})
                    if pos.get("lat") is None:
                        raise SystemExit("--elev-3dep needs the surface position (give --lat --lon --datum, or declare it first)")
                    r = epqs(float(pos["lat"]), float(pos["lon"]), url=a.epqs_url or EPQS_URL)
                    if r["status"] != "OK":
                        raise SystemExit(f"3DEP: {r['status']} - {r.get('detail', '')} ({r['url']})")
                    surface["elevation"] = as_reference(r)
                    print(f"3DEP ground elevation {r['elev_ft']:,.2f} ft ({r['source']})")
                elif a.ref_elev_ft is not None or a.ref_elev_datum or a.ref_elev_source:
                    surface["elevation"] = {"ft": a.ref_elev_ft, "datum": a.ref_elev_datum, "source": a.ref_elev_source}
                identity = {"api_number": a.api} if a.api else {}
                disposal = {k: v for k, v in {"uic_number": a.uic, "depth_tier": a.depth_tier, "formation_basis": a.formation,
                                              "channel_pressure": a.channel_pressure, "channel_rate": a.channel_rate, "rate_unit": a.rate_unit,
                                              "bhp_method": a.bhp_method, "channel_bhp": a.channel_bhp, "top_interval_ft": a.top_interval_ft,
                                              "base_interval_ft": a.base_interval_ft, "depth_reference": a.depth_ref,
                                              "gradient_psi_ft": a.gradient_psi_ft}.items() if v is not None}
                if a.api and (disposal or (ws.well(a.well).get("disposal") or {})):
                    disposal["api_number"] = a.api          # a disposal well keeps its number in the block the SRA packet reads too
                if not surface and not disposal and not identity:
                    raise SystemExit("set-well: nothing to declare (give --lat/--lon, --ref-elev-ft, or any of --api --uic --depth-tier --top-interval-ft ...)")
                surface = surface or None
                w = ws.set_well(a.well, surface=surface, disposal=disposal or None, identity=identity or None, actor=a.actor)
                print(f"well {a.well}: surface {w.get('surface')}; identity {w.get('identity')}; disposal {w.get('disposal')}"); return 0
            elif a.action == "sra-define":
                if not a.site or not a.name or a.lat is None or a.lon is None or a.radius_km is None or not a.plan_date:
                    raise SystemExit("sra-define needs --site, --name, --lat, --lon, --radius-km and --plan-date")
                sra = ws.sra_define(a.site, actor=a.actor, name=a.name, lat=a.lat, lon=a.lon, radius_km=a.radius_km, plan_date=a.plan_date,
                                    datum=a.datum or "WGS84", threshold_m=a.threshold_m, goal_months=a.goal_months, response_hours=a.response_hours,
                                    checkpoint_months=a.checkpoint_months, formation_boundary=a.formation_boundary,
                                    triggering_event=a.triggering_event, note=a.note or "")
                print(f"sra: {sra['name']} on site {a.site} - {sra['radius_km']} km around {sra['centre']['lat']}, {sra['centre']['lon']} "
                      f"({sra['centre']['datum']}); plan {sra['plan_date']}; M {sra['threshold_m']}; goal {sra['goal_months']} months"); return 0
            elif a.action == "sra-report":
                from .sra import report_text as _sra_text
                if not a.site:
                    raise SystemExit("sra-report needs --site SITE_ID")
                r = ws.write_sra_packet(a.site, catalog_csv=a.catalog, aftershocks=a.aftershocks, start=a.start, end=a.end, actor=a.actor)
                print(_sra_text(r["packet"]))
                print("  report:", r["paths"]["html"]); print("  daily export:", r["paths"]["daily_csv"], f"({r['export_rows']} rows)"); return 0
            elif a.action == "associate":
                from .seismic_assoc import report_text as _assoc_text
                if not a.site:
                    raise SystemExit("associate needs --site SITE_ID")
                r = ws.associate_site(a.site, vp_km_s=a.vp, depth_km=a.depth_km, band=a.band, start=a.start, end=a.end, catalog_csv=a.catalog,
                                      rms_tol_s=a.rms_tol, actor=a.actor)
                for sid, x in r["stations"].items():
                    print(f"  {sid}: {x['status']}, {x['picks']} pick(s)")
                print(_assoc_text(r["association"], r["catalogue"]))
                print("  written:", os.path.join(ws.path, "reports", "sites", a.site, "association.json")); return 0
            elif a.action == "osdu-export":
                from .osdu import report_text as _osdu_text
                if not a.site:
                    raise SystemExit("osdu-export needs --site SITE_ID")
                r = ws.osdu_export(a.site, partition=a.partition, acl_owners=a.acl_owner, acl_viewers=a.acl_viewer, legal_tags=a.legal_tag,
                                   countries=a.country, operator_org_id=a.operator_org, out_dir=a.out, actor=a.actor)
                print(_osdu_text(r)); v = r["validate"]
                print(f"  structure: {'ok' if v['ok'] else 'PROBLEMS'} ({v['records']} record(s))" + ('' if v['ok'] else '; ' + '; '.join(v['problems'][:5]))); return 0
            elif a.action == "vibration-report":
                from .vibration import report_text as _vib_text
                if not a.well or not a.record:
                    raise SystemExit("vibration-report needs --well WELL_ID and --record pump.csv")
                brg = None
                if a.bearing:
                    parts = [float(x) for x in a.bearing.split(",")]
                    if len(parts) not in (3, 4):
                        raise SystemExit("--bearing is elements,ball_mm,pitch_mm[,contact_deg]")
                    brg = {"n_elements": int(parts[0]), "d_mm": parts[1], "D_mm": parts[2], "contact_deg": parts[3] if len(parts) == 4 else 0.0}
                try:
                    r = ws.vibration_report(a.well, a.record, channel=a.channel, unit=a.vib_unit, rpm=a.rpm, group=a.group, support=a.support, bearing=brg,
                                            band_hz=a.demod_band, sensitivity=a.sensitivity, actor=a.actor)
                except ValueError as e:
                    raise SystemExit(f"vibration-report: {e}")
                print(_vib_text(r)); print("  written:", r["paths"].get("md") or r["paths"]); return 0
            elif a.action == "well-identity":
                from .ppdm import report_text as _ppdm_text
                if not a.well:
                    raise SystemExit("well-identity needs --well WELL_ID")
                r = ws.well_identity(a.well)
                print(_ppdm_text(r)); return 0
            elif a.action == "add-layer":
                if not a.layer or not a.name:
                    raise SystemExit("add-layer needs --name \"EIA Wolfcamp top\" and --layer C:\\site\\_incoming\\layer.zip (a shapefile zip, a folder, or a .shp)")
                L = ws.add_layer(a.layer, a.name, actor=a.actor, citation=a.citation, member=a.member, formation=a.formation_name, role=a.role, feature_kind=a.feature_kind)
                tc = ", ".join(f"{k} {v}" for k, v in list(L["type_counts"].items())[:6])
                print(f"layer {L['id']} ({L['kind']}): {L['n_features']} {L['shape_type']} features, {L['crs']['kind']} {L['crs'].get('datum') or ''}, extent lon {L['bbox'][0]:.3f}..{L['bbox'][2]:.3f} lat {L['bbox'][1]:.3f}..{L['bbox'][3]:.3f}"
                      + (f"; contour value {L['value_attr']}" if L.get('value_attr') else ''))
                print(f"  types: {tc}" + (" ..." if len(L["type_counts"]) > 6 else ""))
                md = L.get("metadata") or {}
                if md.get("titles"):
                    print(f"  source: {'; '.join(md['titles'][:2])}" + (f" ({md['pubdates'][0]})" if md.get("pubdates") else ""))
                return 0
            elif a.action == "layers":
                for L in ws.layers():
                    print(f"{L['id']:28s} {L.get('kind', 'structure'):10s} {L['n_features']:7d} {L['shape_type']:9s} {L['crs']['kind']} {L['crs'].get('datum') or '':6s} {L['display']}")
                if not ws.layers():
                    print("no structure layer - add one with --action add-layer --name \"EIA Wolfcamp top\" --layer C:\\site\\_incoming\\layer.zip")
                return 0
            elif a.action == "remove-layer":
                if not a.layer:
                    raise SystemExit("remove-layer needs --layer LAYER_ID (gea workspace --action layers lists them)")
                ws.remove_layer(a.layer, actor=a.actor); print(f"removed layer {a.layer} (folder kept, renamed)"); return 0
            elif a.action == "structure":
                if not a.well:
                    raise SystemExit("structure needs --well WELL_ID")
                r = ws.well_structure(a.well, a.radius_km if a.radius_km is not None else 50.0, actor=a.actor)
                print(f"structure {a.well}: {r['status']}" + (f" - {r['note']}" if r.get("note") else ""))
                for L in r["layers"]:
                    print(f"  [{L.get('layer_display') or L['layer']}]")
                    for line in L["lines"]:
                        print("    " + line)
                return 0 if r["status"] == "OK" else 1
            elif a.action == "set-stress":
                st = ws.set_stress({"sv_psi_ft": a.sv, "shmin_psi_ft": a.shmin, "shmax_psi_ft": a.shmax, "pp_psi_ft": a.pp, "shmax_azimuth_deg": a.shmax_azimuth,
                                    "azimuth_source": a.azimuth_source, "gradients_source": a.gradients_source, "dips_deg": a.dips, "mus": a.mu, "delta_pp_psi": a.delta_pp, "note": a.note or ""}, actor=a.actor)
                print(f"stress: Sv {st['sv_psi_ft']} Shmin {st['shmin_psi_ft']} SHmax {st['shmax_psi_ft']} Pp {st['pp_psi_ft']} psi/ft ({st['gradients_source']}); "
                      f"SHmax azimuth {st.get('shmax_azimuth_deg', 'from the stress map')} ({st.get('azimuth_source', '')}); dips {st['dips_deg']}; mu {st['mus']}; delta Pp {st['delta_pp_psi']} psi"); return 0
            elif a.action == "add-stress-map":
                if not a.file:
                    raise SystemExit("add-stress-map needs --file C:\\site\\_incoming\\wsm2016.csv")
                r = ws.add_stress_map(a.file, actor=a.actor, citation=a.citation)
                print(f"stress map: {r['n_records']} records kept as {r['file']} (sha256 {r['sha256'][:16]})"); return 0
            elif a.action == "wsm":
                if a.lat is None or a.lon is None:
                    raise SystemExit("wsm needs --lat --lon [--radius-km]")
                r = ws.wsm_at(a.lat, a.lon, a.radius_km if a.radius_km is not None else 100.0)
                print(r.get("statement") or f"{r['status']}: {r.get('note', '')}")
                for rec in r.get("records", [])[:10]:
                    print(f"  {rec['distance_km']:7.1f} km  {rec['azimuth']:5.0f} deg  {rec['quality']}  {rec['regime'] or '?':3s}  {rec['type']:4s}  {rec['locality'][:40]}")
                return 0 if r.get("n") else 1
            elif a.action == "slip":
                if not a.well:
                    raise SystemExit("slip needs --well WELL_ID")
                r = ws.well_slip(a.well, a.radius_km if a.radius_km is not None else 25.0, delta_pp_psi=a.delta_pp, depth_ft=a.depth_ft, actor=a.actor)
                print(f"slip {a.well}: {r['status']}")
                for line in r["lines"]:
                    print("  " + line)
                return 0 if r["status"] == "OK" else 1
            elif a.action == "interval":
                if not a.well:
                    raise SystemExit("interval needs --well WELL_ID")
                r = ws.well_interval(a.well, a.radius_km if a.radius_km is not None else 50.0, actor=a.actor)
                print(f"interval {a.well}: {r['status']}")
                for line in r["lines"]:
                    print("  " + line)
                return 0 if r["status"] == "OK" else 1
            elif a.action == "month-end":
                from .month_end import report_text as _me_text
                r = ws.month_end(a.period, actor=a.actor, refresh=not a.no_refresh, catalog_csv=a.catalog)
                print(_me_text(r)); return 0 if not r["errors"] else 1
            elif a.action == "add-live-station":
                if not a.seedlink_config or not a.name:
                    raise SystemExit("add-live-station needs --name \"TX PB01 live\" and --seedlink-config seedlink.json")
                st = ws.add_live_station(a.name, a.seedlink_config, a.lat, a.lon, actor=a.actor, band=a.band, stationxml=a.stationxml, sources_csv=a.sources)
                print(f"live station {st['id']}: {st['live']['host']}:{st['live']['port']} {st['live']['stations']} - run `gea workspace --path ... --action live-run --station {st['id']}` or Start it on the page")
                return 0
            elif a.action == "live-fold":
                if not a.station:
                    raise SystemExit("live-fold needs --station STATION_ID")
                r = ws.fold_live_station(a.station, actor=a.actor, include_open=a.include_open)
                print(f"folded {len(r['folded'])} file(s) into {a.station}: {r['folded']}; still open: {r['kept_open']}")
                return 0
            elif a.action == "live-run":
                from . import seedlink as _L
                if not a.station:
                    raise SystemExit("live-run needs --station STATION_ID")
                cfg = ws.live_station_config(a.station)
                d = ws.live_dir(a.station)
                tap = _L.SeedLinkTap(cfg, d, os.path.join(d, "seedlink_state.json"))
                try:
                    st = tap.run(a.seconds if a.seconds else None)
                except KeyboardInterrupt:
                    tap.stop(); st = tap.state
                print(_L.report_text(st))
                return 0 if st["records"] else 1
            elif a.action == "quakeml-export":
                from .quakeml import report_text as _qml_text
                if not a.site:
                    raise SystemExit("quakeml-export needs --site SITE_ID")
                r = ws.quakeml_export(a.site, agency=a.agency, out=a.out, actor=a.actor)
                print(_qml_text(r)); return 0 if r["status"] == "WRITTEN" else 1
            elif a.action == "seismic-dataset":
                r = ws.write_seismic_dataset_report(actor=a.actor)
                inv = r["inventory"]
                print(f"seismic-dataset: {inv['n_stations']} station(s), {inv['n_records']} record(s), {inv['total_hours']:.1f} h")
                for w in inv["warnings"]:
                    print("  needs attention:", w)
                print("  report:", r["report"])
                return 0
            elif a.action == "seismic-field":
                r = ws.write_seismic_field_report(actor=a.actor)
                f = r["field"]
                heard = [x for x in f["sources"] if x["heard_at"]]
                print(f"seismic-field: {len(f['stations'])} station(s), {len(heard)} of {len(f['sources'])} listed source(s) heard, "
                      f"{len(f['tracks'])} track(s), {len(f['unlisted'])} not on the list")
                for u in f["unlisted"]:
                    print(f"  not on the list: {u['source_id']} at {u['station']}, {u['back_azimuth_deg']:.0f} deg, "
                          f"{u['line_rate_per_min']:.0f} a minute")
                print("  report:", r["report"])
                return 0
            elif a.action == "refresh-all":
                r = ws.refresh_all(actor=a.actor)
                print(f"refresh-all: {r['wells']} well(s) refreshed, {r['seismic']} seismic station(s) refreshed, {len(r['errors'])} error(s)")
                for e in r["errors"]:
                    print("  error:", e)
                return 0 if not r["errors"] else 1
            elif a.action == "remove-seismic":
                if not a.station:
                    raise SystemExit("remove-seismic needs --station STATION_ID")
                ws.remove_seismic_station(a.station, actor=a.actor); print("removed (folder kept):", a.station); return 0
            else:
                print(_json.dumps(ws.summary(), indent=1)); return 0
            print("well:", w["id"], f"({w['kind']})"); return 0
        except WorkspaceError as e:
            raise SystemExit(f"workspace: {e}")
    elif a.cmd == "wits0-sim":
        from . import wits0 as _w0
        if a.connect:
            host, _, port = a.connect.rpartition(":")
            n = _w0.simulate_client(host or "127.0.0.1", int(port), frames=a.frames, interval_s=a.interval, seed=a.seed)
            print(f"wits0-sim: pushed {n} frames to {a.connect}")
            return 0
        th, port, stop = _w0.simulate_server(port=a.port, frames=a.frames, interval_s=a.interval, seed=a.seed, verbose=True)
        print(f"wits0-sim: listening on 127.0.0.1:{port} - this window is the SENDER and stays here; nothing happens until a reader connects.\n"
              f"  Open a SECOND window and run:  gea wits0 --config wits0.json --seconds 60 --out floor   (with wits0.json: transport tcp, host 127.0.0.1, port {port})\n"
              f"  or on the dashboard: Patch panel -> Add a patch -> wits0, tcp, 127.0.0.1, {port}. Then it sends {a.frames} frames {a.interval:g} s apart. Ctrl+C stops it.", flush=True)
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            stop.set()
            print("wits0-sim: stopped")
        return 0
    elif a.cmd == "backup":
        from . import backup as _B
        try:
            if a.verify:
                r = _B.verify(a.verify); print(_B.report_text(r, "verify")); return 0 if r["status"] == "OK" else 1
            if a.restore:
                if not a.to:
                    raise SystemExit("backup --restore needs --to C:\\site-restored (an empty folder)")
                print(_B.report_text(_B.restore(a.restore, a.to, a.actor), "restore")); return 0
            if not a.workspace:
                raise SystemExit("backup needs --workspace C:\\site (with --out D:\\gea-backups, another disk), or --verify / --restore an archive")
            if a.status:
                r = _B.status(a.workspace); print(_B.report_text(r, "status")); return 0 if not (r["never"] or r["stale"]) else 1
            if not a.out:
                raise SystemExit("backup needs --out D:\\gea-backups (a folder on another disk or a share)")
            print(_B.report_text(_B.make(a.workspace, a.out, a.actor, keep=a.keep, label=a.label), "make")); return 0
        except (ValueError, OSError) as e:
            raise SystemExit(f"backup: {e}")
    elif a.cmd == "seedlink":
        import json as _json
        from . import seedlink as _L
        if a.selftest:
            r = _L.selftest()
            print(_json.dumps({k: r[k] for k in ("label", "status", "checks")}, indent=1))
            print(_json.dumps(r["detail"], indent=1, default=str))
            return 0 if r["status"] == "OK" else 1
        if a.write_example_config:
            print("config:", _L.write_example_config(a.write_example_config))
            return 0
        if not a.config:
            raise SystemExit("seedlink needs --config (or --write-example-config to start one, or --selftest)")
        try:
            cfg = _L.load_config(a.config)
        except (ValueError, OSError) as e:
            raise SystemExit(f"seedlink: {e}")
        if a.hello:
            try:
                cl = _L.SeedLinkClient(cfg["host"], cfg["port"], cfg["timeout_s"]).connect()
                h = cl.say_hello()
                print(f"seedlink: {cfg['host']}:{cfg['port']} says {h['software']!r}; organization {cl.organization!r}; protocols {h['protocols']}; capabilities {h['capabilities']}")
                cl.info("ID")
                cl.sock.settimeout(cfg["timeout_s"])
                pkt = cl.read_packet()
                print(f"  INFO ID: {pkt['kind']} packet, SeedLink {pkt.get('protocol')}, {len(pkt.get('payload') or b'')} bytes")
                cl.close()
            except (OSError, ValueError, _L.SeedLinkError) as e:
                raise SystemExit(f"seedlink: {e}\n  no SeedLink server answers at that host:port - start `gea seedlink-sim --port {cfg['port']}` in a SECOND window first, or point the config at the data centre (rtserve.iris.washington.edu:18000 for TexNet)")
            return 0
        tap = _L.SeedLinkTap(cfg, a.out)
        try:
            st = tap.run(a.seconds, max_records=a.max_records)
        except KeyboardInterrupt:
            tap.stop(); st = tap.state
        print(_L.report_text(st))
        if st["status"] == "REFUSED" or (st["records"] == 0 and st.get("last_error")):
            print(f"  no records: start `gea seedlink-sim --port {cfg['port']}` in a SECOND window first, or point the config at the data centre (rtserve.iris.washington.edu:18000, network TX for TexNet)")
            return 1
        return 0
    elif a.cmd == "seedlink-sim":
        from . import seedlink as _L
        srv = _L.simulate_server(port=a.port, network=a.network, station=a.station, channels=[c.strip().upper() for c in a.channels.split(",") if c.strip()], rate=a.rate, seed=a.seed)
        print(f"seedlink-sim: a SeedLink server (3.1 and 4.0) listening on 127.0.0.1:{srv.port} - this window is the STATION and stays here; it writes {a.network}.{a.station} "
              f"({a.channels}) at {a.rate:g} Hz in 512-byte records and serves them as customers ask.\n"
              f"  Open a SECOND window and run:  gea seedlink --write-example-config seedlink.json; gea seedlink --config seedlink.json --seconds 60 --out live\n"
              f"  or on the dashboard: Seismic -> Live stations -> Add (host 127.0.0.1, port {srv.port}, network {a.network}, station {a.station}) -> Start. Ctrl+C stops it.", flush=True)
        try:
            while srv.is_alive():
                srv.join(0.5)
        except KeyboardInterrupt:
            srv.stop()
            print("seedlink-sim: stopped")
        return 0
    elif a.cmd == "etp-sim":
        from . import etp as _E
        th, port, stop = _E.simulate_store(port=a.port, frames=a.frames, interval_s=a.interval, seed=a.seed, verbose=True)
        print(f"etp-sim: an ETP v1.2 store listening on ws://127.0.0.1:{port}/ - this window is the STORE and stays here; nothing happens until a customer connects.\n"
              f"  Open a SECOND window and run:  gea etp --config etp.json --seconds 60 --out floor   (with etp.json: url ws://127.0.0.1:{port}/ and the two example channel URIs)\n"
              f"  or on the dashboard: Patch panel -> Add a patch -> etp, url ws://127.0.0.1:{port}/. Then it pushes {a.frames} samples per channel {a.interval:g} s apart. Ctrl+C stops it.", flush=True)
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            stop.set()
            print("etp-sim: stopped")
        return 0
    elif a.cmd == "tools":
        import json as _json
        from . import tool_catalog as _TC
        if a.action:
            from . import tool_taxonomy as _TX
            from .cli import _safe_stdout
            _safe_stdout()                                  # the cited figures carry fractions and degree signs
            if a.action == "motors":
                from . import drill_string as _DS
                print(_json.dumps(_DS.catalogue(), indent=1) if a.json else _DS.motor_text())
                return 0
            if a.action == "classes":
                cs = _TX.classes(a.category, a.find)
                if a.category and a.category not in _TX.CATEGORY_NAMES:
                    raise SystemExit(f"tools: no category {a.category!r}; one of {', '.join(_TX.CATEGORY_NAMES)}")
                if a.json:
                    print(_json.dumps(cs, indent=1)); return 0
                sm = _TX.summary()
                print(f"{len(cs)} of {sm['classes']} tool classes; {sm['sources']} pages checked (fetched {sm['sources_fetched']}); {sm['figures']} published figures, each quoted from its page")
                last = None
                for t in cs:
                    if t["category"] != last:
                        print(f"\n{t['category_name']}"); last = t["category"]
                    print(f"  {t['id']:<24s} {t['name']}")
                    print(f"      {t['one_line']}")
                print("\n`gea tools --action tutorial --class ID` opens one.")
                return 0
            if a.action == "tutorial":
                if not a.class_id:
                    raise SystemExit("tools: tutorial needs --class, e.g. --class pdm_motor (gea tools --action classes lists them)")
                try:
                    if a.json:
                        print(_json.dumps(_TX.tool_class(a.class_id), indent=1)); return 0
                    print(_TX.tutorial_text(a.class_id))
                except KeyError as e:
                    raise SystemExit(f"tools: {e.args[0]}")
                return 0
            if not a.workspace:
                raise SystemExit(f"tools: --action {a.action} needs --workspace, e.g. --workspace C:\\site")
            from .workspace import Workspace, WorkspaceError
            try:
                ws = Workspace(a.workspace)
            except WorkspaceError as e:
                raise SystemExit(f"tools: {e}")
            if a.action == "fleet":
                fl = _TX.fleet_list(ws.path)
                if a.json:
                    print(_json.dumps(fl, indent=1)); return 0
                if not fl:
                    print("the fleet is empty: gea tools --action fleet-add --workspace ... --class pdm_motor --maker ... --model ... --datasheet ...")
                for t in fl:
                    print(f"{t['id']:<32s} {t['class_name']}  -  {t['maker']} {t['model']}" + (f" s/n {t['serial']}" if t['serial'] else '') + (f", {t['od_in']:g} in OD" if t['od_in'] else ''))
                    print(f"      datasheet: {t['datasheet']}")
                return 0
            if a.action == "fleet-add":
                try:
                    rec = _TX.fleet_add(ws.path, a.class_id or "", a.maker, a.model, a.datasheet, serial=a.serial, od_in=a.od_in, note=a.note, actor=a.actor)
                except ValueError as e:
                    raise SystemExit(f"tools: {e}")
                ws.audit(a.actor, 'fleet.add', {'id': rec['id'], 'class': rec['class_id'], 'datasheet': rec['datasheet']})
                print(f"added {rec['id']}: {rec['maker']} {rec['model']} ({rec['class_name']}); datasheet {rec['datasheet']}")
                return 0
            if a.action == "fleet-remove":
                if not a.id:
                    raise SystemExit("tools: fleet-remove needs --id (gea tools --action fleet lists them)")
                try:
                    rec = _TX.fleet_remove(ws.path, a.id)
                except ValueError as e:
                    raise SystemExit(f"tools: {e}")
                ws.audit(a.actor, 'fleet.remove', {'id': rec['id'], 'class': rec['class_id']})
                print(f"removed {rec['id']}")
                return 0
        if a.rating or a.station:
            stations = []
            for spec in (a.station or []):
                if ":" not in spec:
                    raise SystemExit(f"--station takes DEPTH_FT:TOOL (9850:quartz_pt_geoq177_16k), got {spec!r}")
                md, _, nm = spec.partition(":")
                try:
                    stations.append((float(md), nm.strip()))
                except ValueError:
                    raise SystemExit(f"--station depth is not a number: {md!r}")
            try:
                r = _TC.check_string(stations, surface_temp_F=a.surface_temp_f, temp_gradient_F_per_ft=a.temp_gradient_f_ft,
                                     surface_pressure_psi=a.surface_pressure_psi, pressure_gradient_psi_per_ft=a.pressure_gradient_psi_ft)
            except ValueError as e:
                raise SystemExit(f"tools: {e}")
            if a.json:
                print(_json.dumps(r, indent=1)); return 0 if not r["n_over_rating"] else 1
            for line in r["lines"]:
                print(line)
            return 0 if not r["n_over_rating"] else 1
        if a.list:
            for t in _TC.tools():
                print(t["name"])
            return 0
        if a.json:
            print(_json.dumps(_TC.catalog(), indent=1)); return 0
        print(_TC.text())
        return 0
    elif a.cmd == "comcat":
        import json as _json
        from . import sra as _SR
        from . import geodesy as _GD
        if a.file:
            c = _SR.read_catalog(a.file)
            if c.get("format") != "fdsn-text":
                print(f"comcat: {a.file} is not an FDSN text export (it read as a {c.get('basis', '')[:60]}...)"); return 1
            ev = c["events"]
            if a.lat is not None and a.lon is not None:
                for e in ev:
                    dm = _GD.geodesic_m(a.lat, a.lon, e["lat"], e["lon"])["distance_m"]
                    e["distance_km"] = round(dm / 1000.0, 2) if dm is not None else None
            if a.json:
                print(_json.dumps(c, indent=1, default=str)); return 0
            big = max(ev, key=lambda e: e["magnitude"]) if ev else None
            ts = sorted(e["time"] for e in ev)
            print(f"comcat: {c['n_events']} event(s) ({c['n_skipped']} skipped) from {ts[0][:10] if ts else '-'} to {ts[-1][:10] if ts else '-'}; "
                  f"contributors {', '.join(c['contributors'])}; magnitude types {', '.join(c['magnitude_types'])}")
            if big:
                print(f"  largest: M {big['magnitude']:.1f} {big['mag_type']} {big['time']} at {big['lat']:.4f}, {big['lon']:.4f}, {big['depth_km']} km - {big['place']} ({big['event_id']})")
            for e in sorted(ev, key=lambda x: x["time"])[:40]:
                print(f"  {e['time'][:19]}  M {e['magnitude']:>4.1f} {e['mag_type']:<6s} {e['lat']:9.4f} {e['lon']:10.4f} {str(e['depth_km']):>7s} km " + (f"{e['distance_km']:7.2f} km  " if e.get("distance_km") is not None else "") + f"{e['place']}")
            return 0
        if a.lat is None or a.lon is None or not a.start or not a.end:
            raise SystemExit("comcat needs --lat --lon --start --end (--radius-km, --min-mag), or --file comcat.txt")
        print(_SR.comcat_url(a.lat, a.lon, a.radius_km, a.start, a.end, a.min_mag))
        print("  open it in a browser (this machine may not reach the service) and save the text it returns; `gea comcat --file comcat.txt` reads it, and sra-report --catalog takes it.")
        return 0
    elif a.cmd == "slip":
        import json as _json
        from . import shapefile as _SF, slip as _SL, friction_priors as _FP
        try:
            L = _SF.read(a.layer, member=a.member)
        except (ValueError, OSError) as e:
            raise SystemExit(f"slip: {e}")
        if a.feature_kind:
            L.declared_kind = a.feature_kind
        az, az_src = a.shmax_azimuth, "declared"
        if az is None and a.wsm:
            nb = _SL.wsm_nearby(_SL.read_wsm(a.wsm), a.lat, a.lon, 100.0)
            if not nb["n"]:
                raise SystemExit("slip: no World Stress Map record of quality A/B/C within 100 km; declare --shmax-azimuth with its source")
            az, az_src = nb["azimuth_mean"], nb["statement"]
        if az is None:
            raise SystemExit("slip: give --shmax-azimuth or --wsm wsm2016.csv")
        faults = _SL.faults_in_radius(L, a.lat, a.lon, a.radius_km)
        r = _SL.statement(faults, a.depth_ft, {"sv_psi_ft": a.sv, "shmin_psi_ft": a.shmin, "shmax_psi_ft": a.shmax, "pp_psi_ft": a.pp}, az, tuple(a.dips), tuple(a.mu), a.delta_pp,
                          sources={"shmax_azimuth": az_src, "gradients": "the command line"})
        r["friction_envelope"] = _FP.envelope()
        if a.json:
            print(_json.dumps(r, indent=1, default=str)); return 0
        for line in r["lines"]:
            print(line)
        print(r["friction_envelope"].get("statement", ""))
        return 0
    elif a.cmd == "catalog":
        import json as _json
        from . import catalog_register as _CR
        if a.positions or a.map or a.check_positions:
            from . import catalog_map as _CM
            if a.check_positions:
                chk = _CM.check_sidecars()
                print(_json.dumps(chk, indent=1) if a.json else ("catalog positions: the sidecars match a fresh read of every file" if chk["ok"] else "catalog positions: DRIFT - " + _json.dumps(chk["bad"])))
                return 0 if chk["ok"] else 1
            rows = _CM.sidecar_positions()
            if a.map:
                reg = {r["entry"]: r["status"] for r in _CR.register()}
                with open(a.map, "w", encoding="utf-8") as f:
                    f.write(_CM.svg(rows, register=reg))
                c = _CM.counts(rows)
                print(f"catalog map: {c['positioned']} of {c['entries']} entries on {c['distinct_positions']} marks, {c['no_position']} NO POSITION listed below the key -> {a.map}")
            if a.positions:
                print(_json.dumps({"positions": rows, "counts": _CM.counts(rows)}, indent=1, default=str) if a.json else _CM.text(rows))
            return 0
        if a.friction:
            from . import friction_priors as _FP
            print(_json.dumps({"measured": _FP.measured(), "envelope": _FP.envelope()}, indent=1, default=str) if a.json else _FP.text()); return 0
        if a.core:
            from .rock_inventory import core_check
            c = core_check()
            print(_json.dumps(c, indent=1) if a.json else (f"core check ({c.get('entry')}): {c.get('how')}\n  grain density {c['grain_density']['min']}-{c['grain_density']['max']} g/cc, porosity "
                  f"{c['porosity_frac']['min']:.3f}-{c['porosity_frac']['max']:.3f}, bulk {c['bulk_density']['min']}-{c['bulk_density']['max']} g/cc; {c['outside']} plug(s) outside the range\n  source: {c['source']} [{c['license']}]" if c.get('status') == 'OK' else str(c)))
            return 0
        if a.check:
            rows = _CR.check_all()
            bad = [r for r in rows if not r["ok"]]
            for r in rows:
                print(f"  {'ok ' if r['ok'] else 'BAD'} {r['entry']:<40s} {r['shape']}")
            print(f"catalog: {len(rows) - len(bad)} of {len(rows)} entries read through their own readers" + (f"; {len(bad)} did not" if bad else ""))
            return 0 if not bad else 1
        if a.entry:
            from .profile_catalog import CATALOG as _CAT
            e = _CAT.get(a.entry)
            if e is None:
                raise SystemExit(f"catalog: no entry {a.entry!r} (gea catalog --list)")
            if a.stream_csv:
                st = e.stream()
                import csv as _csv
                with open(a.stream_csv, "w", newline="", encoding="utf-8") as f:
                    wr = _csv.writer(f)
                    wr.writerow([st.index_kind] + list(st.channels)); wr.writerow([""] + [st.channels[c].unit for c in st.channels])
                    for i in range(len(st.index)):
                        wr.writerow([float(st.index[i])] + ["" if st.channels[c].values[i] != st.channels[c].values[i] else float(st.channels[c].values[i]) for c in st.channels])
                print(f"{a.entry}: {len(st.index)} rows x {len(st.channels)} channels -> {a.stream_csv}")
            reg = next(r for r in _CR.register() if r["entry"] == a.entry)
            if a.json:
                print(_json.dumps({"register": reg, "provenance": e.provenance}, indent=1, default=str))
            else:
                print(f"{a.entry}: {reg['kind']} ({reg['tier']}), {reg['file']} {reg['bytes']:,} bytes, {reg['status']}")
                for u in reg["used_by"]:
                    print("  used by:", u)
                for k in ("well", "source_database", "source_url", "license", "fetch_date", "coverage", "purpose"):
                    if e.provenance.get(k):
                        print(f"  {k}: {str(e.provenance[k])[:300]}")
                print("  reads as:", _CR.check_entry(e)["shape"])
            return 0
        rows = _CR.register()
        if a.json:
            print(_json.dumps({"entries": rows, "audit": _CR.audit()}, indent=1, default=str)); return 0
        print(_CR.text(rows, unused_only=a.unused))
        if a.unused:
            print(f"\n  {_CR.NO_BAND_REASON}")
        return 0
    elif a.cmd == "edr":
        import json as _json
        from . import edr as _EDR
        from . import __version__ as _ver
        if a.selftest:
            import tempfile as _tf
            with _tf.TemporaryDirectory() as _t:
                r = _EDR.selftest(_t)
            print(_json.dumps(r["record"], indent=1, default=str) if a.json else "\n".join(r["record"]["lines"])); return 0
        if not a.file:
            raise SystemExit("edr needs --file C:\\site\\_incoming\\30636033.csv (an EDR export), or --selftest")
        try:
            st = _EDR.read(a.file)
        except ValueError as e:
            raise SystemExit(f"edr: {e}")
        if a.out:
            paths = _EDR.write_record(st, a.out, _ver)
            rec = paths.pop("record")
            print("\n".join(rec["lines"]))
            print("written:", ", ".join(os.path.basename(v) for k, v in paths.items())); return 0
        rec = _EDR.record(st)
        print(_json.dumps(rec, indent=1, default=str) if a.json else "\n".join(rec["lines"])); return 0
    elif a.cmd == "dlis":
        import json as _json
        from .dlis import DlisError, DlisFile, read as _read_dlis
        try:
            if a.info or not (a.out or a.to_las):
                with open(a.file, "rb") as f:
                    info = DlisFile(f.read(), os.path.basename(a.file)).describe()
                if a.json:
                    print(_json.dumps(info, indent=1, default=str)); return 0
                o = info["origin"]
                print(f"dlis: {a.file}")
                print(f"  storage set: {info['storage_unit']['storage_set_id']}; file {info['file_header'].get('ID', '').strip()} seq {info['file_header'].get('SEQUENCE-NUMBER', '').strip()}")
                print(f"  well: {o.get('WELL-NAME', '').strip() or '?'}  field: {o.get('FIELD-NAME', '').strip() or '?'}  company: {o.get('COMPANY', '').strip() or '?'}  producer: {o.get('PRODUCER-NAME', '').strip() or '?'}  created: {o.get('CREATION-TIME', '?')}")
                print(f"  {info['n_records']} logical records; sets: " + ", ".join(f"{t}{'(' + n + ')' if n else ''} x{k}" for t, n, k in info["eflr_sets"]))
                if info.get("encrypted_records"):
                    print("  encrypted records skipped: " + ", ".join(f"{k} x{n}" for k, n in info["encrypted_records"].items()))
                for u in info["unparsed"]:
                    print("  not parsed: " + u)
                print(f"  {len(info['channels'])} channels:")
                for c in info["channels"]:
                    print(f"    {c['name']:<12s} {c['units']:<10s} {c['code']:<7s} dim {c['dimension']}  {c['long_name']}")
                for fr in info["frames"]:
                    print(f"  frame {fr['name']}: index {fr['index_type']} {fr['direction']}, spacing {fr['spacing']} {fr['spacing_units']}, {fr['index_min']}..{fr['index_max']}; {len(fr['channels'])} channels: {', '.join(fr['channels'][:12])}{' ...' if len(fr['channels']) > 12 else ''}")
                return 0
            st = _read_dlis(a.file, frame=a.frame)
        except (DlisError, OSError) as e:
            raise SystemExit(f"dlis: {e}")
        print(f"dlis: frame {st.meta['frame']}: {len(st.index)} rows, index {st.meta['index_channel']} ({st.meta['index_units']}) {float(st.index[0]):g}..{float(st.index[-1]):g}, {len(st.channels)} channels"
              + (f"; {st.meta['notes']}" if st.meta.get("notes") else ""))
        if a.out:
            import csv as _csv
            with open(a.out, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow([st.meta["index_channel"]] + list(st.channels))
                w.writerow([st.meta["index_units"]] + [st.channels[c].unit for c in st.channels])
                for i in range(len(st.index)):
                    w.writerow([float(st.index[i])] + ["" if st.channels[c].values[i] != st.channels[c].values[i] else float(st.channels[c].values[i]) for c in st.channels])
            print("  csv:", a.out)
        if a.to_las:
            with open(a.to_las, "w", encoding="utf-8") as f:
                f.write("~VERSION INFORMATION\n VERS.   2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0\n WRAP.   NO  : ONE LINE PER DEPTH STEP\n")
                f.write("~WELL INFORMATION\n")
                f.write(f" STRT.{st.meta['index_units']:<6s} {float(st.index[0]):.4f} : START\n STOP.{st.meta['index_units']:<6s} {float(st.index[-1]):.4f} : STOP\n")
                f.write(f" STEP.{st.meta['index_units']:<6s} {(float(st.index[1]) - float(st.index[0])) if len(st.index) > 1 else 0.0:.4f} : STEP\n NULL.       -999.25 : NULL VALUE\n")
                f.write(f" WELL.       {st.meta.get('WELL-NAME', '').strip()} : WELL\n FLD .       {st.meta.get('FIELD-NAME', '').strip()} : FIELD\n COMP.       {st.meta.get('COMPANY', '').strip()} : COMPANY\n SRVC.       {st.meta.get('PRODUCER-NAME', '').strip()} : SERVICE COMPANY\n")
                f.write(f" SRC .       DLIS {os.path.basename(a.file)} frame {st.meta['frame']} : SOURCE (gea dlis --to-las)\n")
                f.write("~CURVE INFORMATION\n")
                f.write(f" {st.meta['index_channel']:<8s}.{st.meta['index_units']:<8s} : INDEX\n")
                for c in st.channels:
                    f.write(f" {c:<8s}.{st.channels[c].unit:<8s} : {c}\n")
                f.write("~A\n")
                for i in range(len(st.index)):
                    f.write(f"{float(st.index[i]):.4f}" + "".join(f" {(-999.25 if st.channels[c].values[i] != st.channels[c].values[i] else float(st.channels[c].values[i])):.4f}" for c in st.channels) + "\n")
            print("  las:", a.to_las)
        return 0
    elif a.cmd == "piwebapi":
        import json as _json
        from .live_ports import records_to_stream, summarize, write_records_csv
        from . import piwebapi as _PI
        if a.selftest:
            r = _PI.selftest()
            print(f"piwebapi selftest: {'OK' if r['ok'] else 'FAILED'}")
            for k, v in r.items():
                if k != "ok":
                    print(f"  {'ok ' if v else 'BAD'} {k}")
            return 0 if r["ok"] else 1
        if a.write_example_config:
            with open(a.write_example_config, "w", encoding="utf-8") as f:
                _json.dump(_PI.EXAMPLE_CONFIG, f, indent=1)
            print("config:", a.write_example_config); return 0
        if not a.config:
            raise SystemExit("piwebapi needs --config (or --write-example-config to start one)")
        cfg = _PI.load_config(a.config)
        sim_h = None
        try:
            if cfg.get("base_url") == "sim":
                sim = _PI.SimServer(); sim_h, port_ = sim.serve(); cfg = {**cfg, "base_url": f"http://127.0.0.1:{port_}/piwebapi", "auth": "none"}
            try:
                port = _PI.PiPort(cfg)
                r = port.pull()
            except (_PI.PiError, ValueError) as e:
                raise SystemExit(f"piwebapi: {e}")
        finally:
            if sim_h is not None:
                sim_h.close()
        recs = port.records
        print(f"piwebapi: {_json.dumps(port.summary(), default=str)}")
        print(_json.dumps(summarize(recs), indent=1))
        if a.record:
            with open(a.record, "w", encoding="utf-8") as f:
                f.write(_json.dumps({"pull": r, "summary": port.summary()}, default=str) + "\n")
        if a.out:
            print("  records:", write_records_csv(recs, a.out))
        if a.stream_csv and recs:
            st = records_to_stream(recs, name="piwebapi")
            import csv as _csv
            from datetime import datetime as _dt, timedelta as _td
            t0 = _dt.fromisoformat(st.meta["start_time"])
            with open(a.stream_csv, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow(["timestamp"] + list(st.channels))
                for i, t in enumerate(st.index):
                    w.writerow([(t0 + _td(seconds=float(t))).isoformat()] + ["" if st.channels[c].values[i] != st.channels[c].values[i] else round(float(st.channels[c].values[i]), 4) for c in st.channels])
            print("  stream:", a.stream_csv)
        return 0 if not port.faults else 1
    elif a.cmd in ("modbus-rtu", "dnp3", "hartip"):
        import json as _json
        from .live_ports import records_to_stream, summarize, write_records_csv
        mod = __import__({"modbus-rtu": "gea.modbus_rtu", "dnp3": "gea.dnp3", "hartip": "gea.hartip"}[a.cmd], fromlist=["x"])
        if a.selftest:
            r = mod.selftest()
            print(f"{a.cmd} selftest: {'OK' if r['ok'] else 'FAILED'}")
            for k, v in r.items():
                if k != "ok":
                    print(f"  {'ok ' if v else 'BAD'} {k}")
            return 0 if r["ok"] else 1
        if a.write_example_config:
            with open(a.write_example_config, "w", encoding="utf-8") as f:
                _json.dump(mod.EXAMPLE_CONFIG, f, indent=1)
            print("config:", a.write_example_config); return 0
        if a.replay:
            raise SystemExit(f"{a.cmd}: a poll is a request to the device; there is no recording to replay - run against the device or the stand-in")
        if not a.config:
            raise SystemExit(f"{a.cmd} needs --config (or --write-example-config to start one)")
        cfg = mod.load_config(a.config)
        polls = a.polls
        if polls is None and a.seconds is not None:
            polls = max(1, int(round(a.seconds / max(float(cfg.get("interval_s", 1.0)), 0.05))))
        from .patches import close_field_port, open_field_port
        proto = {"modbus-rtu": "modbus_rtu", "dnp3": "dnp3", "hartip": "hartip"}[a.cmd]
        try:
            tap = open_field_port(proto, mod, cfg)
        except (NotImplementedError, ConnectionError, ValueError, OSError) as e:
            raise SystemExit(f"{a.cmd}: {e}")
        rec_f = open(a.record, "w", encoding="utf-8") if a.record else None
        n = int(cfg.get("polls", 3) if polls is None else polls)
        dt = float(cfg.get("interval_s", 1.0))
        try:
            for k in range(n):
                before = len(tap.records)
                r = tap.poll_once(integrity=(k == 0)) if a.cmd == "dnp3" else tap.poll_once()
                if rec_f:
                    rec_f.write(_json.dumps({"poll": k + 1, "utc": tap.records[-1].ingest_timestamp_utc if tap.records else None,
                                             "values": {x.tag_id: x.value for x in tap.records[before:]}, "quality": {x.tag_id: x.quality_flag for x in tap.records[before:]},
                                             "detail": (r if isinstance(r, dict) and a.cmd != "modbus-rtu" else None)}, default=str) + "\n")
                if dt > 0 and k < n - 1:
                    import time as _time
                    _time.sleep(dt)
        finally:
            close_field_port(proto, tap)
            if rec_f:
                rec_f.close()
        recs = tap.records
        print(f"{a.cmd}: {_json.dumps(tap.summary(), default=str)}")
        print(_json.dumps(summarize(recs), indent=1))
        if a.out:
            print("  records:", write_records_csv(recs, a.out))
        if a.stream_csv and recs:
            st = records_to_stream(recs, name=proto)
            import csv as _csv
            from datetime import datetime as _dt, timedelta as _td
            t0 = _dt.fromisoformat(st.meta["start_time"])
            with open(a.stream_csv, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow(["timestamp"] + list(st.channels))
                for i, t in enumerate(st.index):
                    w.writerow([(t0 + _td(seconds=float(t))).isoformat()] + ["" if st.channels[c].values[i] != st.channels[c].values[i] else round(float(st.channels[c].values[i]), 4) for c in st.channels])
            print("  stream:", a.stream_csv)
        return 0
    elif a.cmd in ("opcua", "mqtt", "wits0", "witsml", "etp"):
        import json as _json
        from .live_ports import records_to_stream, summarize, write_records_csv
        mod = __import__({"opcua": "gea.opcua_port", "mqtt": "gea.mqtt_port", "wits0": "gea.wits0", "witsml": "gea.witsml", "etp": "gea.etp"}[a.cmd], fromlist=["x"])
        if a.cmd == "etp" and getattr(a, "selftest", False):
            r = mod.selftest()
            print(f"etp selftest [{r['label']}]: {r['status']}")
            for k, v in r["checks"].items():
                print(f"  {'ok ' if v else 'BAD'} {k}")
            print(f"  {r['n_records']} record(s) on {r['tags']}; store {r['store'].get('application')} protocols {r['store'].get('protocols')}; counts {r['counts']}")
            return 0 if r["status"] == "OK" else 1
        if a.write_example_config:
            print("config:", mod.write_example_config(a.write_example_config))
            return 0
        if not a.config:
            raise SystemExit(f"{a.cmd} needs --config (or --write-example-config to start one)")
        try:
            if a.replay:
                recs = mod.replay(a.config, a.replay)
                src = f"replay {a.replay}"
            elif a.cmd == "opcua":
                tap = mod.OpcUaTap(a.config, recording_path=a.record).connect()
                try:
                    recs = tap.read_once() if a.read_once else tap.subscribe(a.seconds)
                finally:
                    tap.close()
                src = tap.cfg["endpoint"]
            elif a.cmd == "mqtt":
                tap = mod.MqttTap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = tap.cfg["broker"]["host"]
            elif a.cmd == "wits0":
                tap = mod.Wits0Tap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = f"{tap.cfg['transport']} {tap.cfg.get('host') or tap.cfg.get('device') or ('listen:' + str(tap.cfg.get('listen_port')))}"
            elif a.cmd == "etp":
                tap = mod.EtpTap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = tap.cfg["url"] + (f"  (store: {tap.state.get('store', {}).get('application')}, {tap.state.get('channels')} channel(s) subscribed"
                                        + (f", missing {tap.state.get('missing_uris')}" if tap.state.get('missing_uris') else '') + ')')
            else:
                tap = mod.WitsmlTap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = tap.cfg["url"]
        except (NotImplementedError, ConnectionError, ValueError) as e:
            hint = {"wits0": "nothing is sending on that host:port - start `gea wits0-sim --port 5001` in a SECOND window first (it must be running while this reads), or point the config at the rig's real WITS0 feed",
                    "witsml": "no WITSML store answers at that url - the example config points at 127.0.0.1:8000, a placeholder; put the real store's url, uids and credential env names in it",
                    "etp": "no ETP store answers at that url - start `gea etp-sim --port 9800` in a SECOND window first, or point the config at the rig's ETP endpoint (wss://...) with the channel URIs it publishes",
                    "opcua": "no OPC UA server answers at that endpoint - the example config is a placeholder; put the real server's endpoint, security and node ids in it",
                    "mqtt": "no MQTT broker answers at that host:port - the example config is a placeholder; put the real broker in it"}.get(a.cmd, "")
            raise SystemExit(f"{a.cmd}: {e}" + (f"\n  {hint}" if hint else ""))          # the reason and what to do, without a traceback
        print(f"{a.cmd}: {src}")
        if a.cmd == "etp" and a.replay:
            shape = getattr(mod.replay, "summary", {})
            print(_json.dumps({"session_shape": shape, "records": 0,
                               "note": "an ETP recording holds the messages as received with ChannelData as counts (the samples went to --out); the replay reproduces the session's shape, not the samples"}, indent=1))
        else:
            print(_json.dumps(summarize(recs), indent=1))
        if a.out:
            print("  records:", write_records_csv(recs, a.out))
        if a.stream_csv and recs:
            st = records_to_stream(recs, name=a.cmd)
            import csv as _csv
            from datetime import datetime as _dt, timedelta as _td
            t0 = _dt.fromisoformat(st.meta["start_time"])
            with open(a.stream_csv, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow(["timestamp"] + list(st.channels))
                for i, t in enumerate(st.index):
                    w.writerow([(t0 + _td(seconds=float(t))).isoformat()] + ["" if st.channels[c].values[i] != st.channels[c].values[i] else round(float(st.channels[c].values[i]), 4) for c in st.channels])
            print("  stream:", a.stream_csv)
    elif a.cmd == "dashboard":
        from . import GAUGE_SPECS, load_gauge_spec_json, DEFAULT_TD_FT
        from .dashboard import orchestrate
        cws = []
        for spec in a.catalog_well:
            parts = spec.rsplit(":", 2)
            if len(parts) != 3:
                raise SystemExit(f"--catalog-well needs ENTRY:WELL_TAG:STATION_MD_FT, got {spec!r}")
            cws.append({"entry": parts[0], "well": parts[1], "md_ft": float(parts[2])})
        fws = [{"path": f} for f in a.file]
        gs = None
        if a.spec:
            gs = GAUGE_SPECS[a.spec] if a.spec in GAUGE_SPECS else load_gauge_spec_json(a.spec)
        r = orchestrate(a.out, cws, fws, td_ft=a.td if a.td is not None else DEFAULT_TD_FT, gauge_spec=gs,
                        outages=a.outage or None, month=a.month, site_name=a.name, criteria_path=a.criteria,
                        monitor_root=a.monitor_root, run_sat=a.sat)
        print(f"dashboard: {r['index']}")
        for w, reps in r["reports"].items():
            print(f"  {w}: {', '.join(reps)}")
        print(f"  site: {', '.join(r['site_reports'])}")
    elif a.cmd == "config":
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from .config_versioning import ConfigStore
        cs = ConfigStore(a.store)
        now = _dt.fromisoformat(a.now.replace("Z", "")).replace(tzinfo=_tz.utc) if a.now else None
        if a.action == "commit":
            if not (a.name and a.file and a.author):
                raise SystemExit("config commit needs --name --file --author")
            print(_json.dumps(cs.import_file(a.name, a.file, a.author, a.note, now=now), indent=1, default=str))
        elif a.action == "history":
            print(_json.dumps(cs.history(a.name), indent=1, default=str))
        elif a.action == "export":
            print("exported:", cs.export(a.name, a.file, a.version))
        elif a.action == "rollback":
            if not (a.name and a.version and a.author):
                raise SystemExit("config rollback needs --name --version --author")
            print(_json.dumps(cs.rollback(a.name, a.version, a.author, a.note, now=now), indent=1, default=str))
        else:
            print(_json.dumps(cs.summary(), indent=1))
    elif a.cmd == "sbom":
        from .sbom import generate, write as _write_sbom
        sb = generate()
        paths = _write_sbom(sb, a.out)
        print(f"sbom: {sb['n_components']} components")
        for c in sb["components"]:
            print(f"  {c['name']} {c['version']} | {c['licence']} | {c['relationship']}")
        for kk, vv in paths.items():
            print(f"  {kk}: {vv}")
    elif a.cmd == "sla-report":
        from . import __version__ as _v
        from .sla_report import measure
        from .sbom import generate as _sbom
        from .client_reports import monthly_sla_report, write as _write_report
        m = measure(a.month, monitor_log_dir=a.monitor_log_dir, store_forward_json=a.store_forward_json, alarm_log=a.alarm_log,
                    well_test_dir=a.well_test_dir, accuracy_json=a.accuracy_json, config_dir=a.config_store)
        csum = None
        if a.config_store:
            from .config_versioning import ConfigStore
            csum = ConfigStore(a.config_store).summary()
        doc = monthly_sla_report(m, site_name=a.name or "site", program_version=_v, config_summary=csum, sbom=_sbom())
        paths = _write_report(doc, a.out, basename=f"sla_report_{a.month}")
        print(f"sla-report {a.month}: " + ", ".join(f"{k} {v}" for k, v in sorted(m["status_counts"].items())))
        for kk, vv in paths.items():
            print(f"  {kk}: {vv}")
    elif a.cmd == "fat-sat":
        from . import __version__ as _v
        from .fat_sat import run_protocol
        from .client_reports import fat_sat_report, write as _write_report
        proto = run_protocol(a.kind, sections=[x.strip() for x in a.sections.split(",")] if a.sections else None)
        doc = fat_sat_report(proto, site_name=a.name or "", program_version=_v)
        paths = _write_report(doc, a.out, basename=f"{a.kind.lower()}_protocol")
        print(f"{a.kind}: {proto['n_steps']} steps, {proto['n_pass']} pass, {proto['n_fail']} fail, "
              f"{proto['internal_checks_excluded']} internal checks excluded - {proto['result']}")
        for kk, vv in paths.items():
            print(f"  {kk}: {vv}")
    elif a.cmd == "store-forward":
        import json as _json
        from . import ingest as _ingest, __version__ as _v
        from .sample_record import TagCatalogue, records_from_stream
        from .store_forward import simulate, parse_outages, BufferConfig
        stream = _ingest(a.file, port=a.port)
        cat = TagCatalogue.from_stream(stream)
        recs = records_from_stream(stream, cat)
        if a.tags:
            pref = tuple(x.strip() for x in a.tags.split(","))
            recs = [r for r in recs if r.tag_id.startswith(pref)]
        cad = next((t.cadence_s for t in cat.tags.values() if t.cadence_s), 60.0)
        sim = simulate(recs, parse_outages(a.outage), BufferConfig(capacity_hours=a.capacity_hours, cadence_s=cad,
                                                                    replay_rate_per_s=a.replay_rate), edge_latency_s=a.edge_latency)
        print(_json.dumps({k: v for k, v in sim.items() if k in ("stats", "outages", "final_backlog", "replay_slots")}, indent=1))
        if a.out:
            from .client_reports import data_resilience_report, write as _write_report
            doc = data_resilience_report(sim, site_name=a.name or stream.name, program_version=_v)
            paths = _write_report(doc, a.out, basename="data_resilience_report")
            for kk, vv in paths.items():
                print(f"  {kk}: {vv}")
    elif a.cmd == "model-cards":
        import json as _json, os as _os
        from .model_card import build_cards, card_index
        from .client_reports import model_card_report, write as _write_report
        cards = build_cards(monitor_log_dir=a.monitor_log_dir)
        _os.makedirs(a.out, exist_ok=True)
        for c in cards:
            paths = _write_report(model_card_report(c), a.out, basename=f"model_card_{c.model_id}")
            print(f"model-card {c.model_id}: {paths['html']}")
        with open(_os.path.join(a.out, "model_cards_index.json"), "w", encoding="utf-8") as f:
            _json.dump(card_index(cards), f, indent=1)
        print(f"  index: {_os.path.join(a.out, 'model_cards_index.json')} ({len(cards)} cards)")
    elif a.cmd == "alarms":
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from . import ingest as _ingest, __version__ as _v
        from .sample_record import TagCatalogue, records_from_stream
        from .alarm_engine import (AlarmEngine, defaults_from_catalogue, load_alarm_definitions,
                                   write_alarm_definitions)
        if not a.file:
            raise SystemExit("alarms needs --file")
        cfg = _build_config(a)
        stream = _ingest(a.file, port=a.port)
        cat = TagCatalogue.from_stream(stream, gauge_spec=getattr(cfg, "gauge_spec", None))
        if a.write_default_definitions:
            print("definitions:", write_alarm_definitions(defaults_from_catalogue(cat), a.write_default_definitions))
            return 0
        defs = load_alarm_definitions(a.definitions) if a.definitions else defaults_from_catalogue(cat)
        eng = AlarmEngine(defs, event_log_path=a.event_log, operator_positions=a.positions)
        evs = eng.process(records_from_stream(stream, cat))
        if a.ack or a.ack_all or a.shelve or a.unshelve:
            now = _dt.fromisoformat(a.now.replace("Z", "")).replace(tzinfo=_tz.utc) if a.now else None
            if now is None:
                raise SystemExit("operator actions (--ack/--ack-all/--shelve/--unshelve) need --now (the action timestamp)")
            ids = lambda v: [x.strip() for x in v.split(",") if x.strip()]
            acts = []
            try:
                if a.ack_all:
                    acts += eng.acknowledge_all(a.operator, now, note=a.note)
                for aid in ids(a.ack or ""):
                    acts.append(eng.acknowledge(aid, a.operator, now, a.note))
                for aid in ids(a.shelve or ""):
                    acts.append(eng.shelve(aid, a.operator, now, a.note, hours=a.shelve_hours))
                for aid in ids(a.unshelve or ""):
                    acts.append(eng.unshelve(aid, a.operator, now, a.note))
            except (KeyError, ValueError) as e:
                raise SystemExit(f"alarm action: {e}")
            print(_json.dumps(acts if len(acts) != 1 else acts[0], indent=1))
        k = eng.kpis()
        print(f"alarms: {len(defs)} definitions, {len(evs)} events this run, "
              f"{k.get('n_activations', 0)} activations, {len(eng.active())} active at end")
        if a.out:
            from .client_reports import alarm_event_report, write as _write_report
            doc = alarm_event_report(eng, well_name=a.name or stream.name, program_version=_v)
            paths = _write_report(doc, a.out, basename="alarm_event_report")
            for kk, vv in paths.items():
                print(f"  {kk}: {vv}")
    elif a.cmd == "well-test":
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from . import CATALOG, __version__ as _v
        from .well_test_validation import (WellTestValidator, ApprovalTrail, load_criteria,
                                           write_default_criteria, volve_channel_map)
        if a.write_default_criteria:
            print("criteria:", write_default_criteria(a.write_default_criteria))
            return 0
        now = None
        if a.now:
            now = _dt.fromisoformat(a.now.replace("Z", ""))
            now = now.replace(tzinfo=_tz.utc) if now.tzinfo is None else now
        crit = load_criteria(a.criteria)
        trail = ApprovalTrail(a.record_dir, levels=crit.get("approval_levels"))
        if a.approve:
            if not a.approver:
                raise SystemExit("--approve needs --approver")
            print(_json.dumps(trail.approve(a.approve, a.approver, a.level, a.decision, a.note, now=now), indent=1))
            print(_json.dumps(trail.status_of(a.approve), indent=1))
        if not (a.live_catalog and a.live_well):
            if a.approve:
                return 0
            raise SystemExit("well-test needs --live-catalog and --live-well (or --approve)")
        src = CATALOG[a.live_catalog].stream()
        det = WellTestValidator(crit, volve_channel_map(a.live_well)).detect(src)
        print(f"well-test: {det['n_accepted']} accepted, {det['n_rejected']} rejected candidates, "
              f"{det['eligible_samples']}/{det['n_samples']} eligible samples")
        for t in det["tests"]:
            print(f"  {t['test_id']} {t['start_utc'][:10]}..{t['end_utc'][:10]} n={t['n']} "
                  + " ".join(f"{k}={v:,.1f}" for k, v in t["virtual_rates"].items())
                  + f"  [{trail.status_of(t['test_id'])['status']}]")
        if a.out:
            from .client_reports import well_test_report, write as _write_report
            doc = well_test_report(det, approvals=trail, well_name=f"{a.live_catalog} {a.live_well}",
                                   program_version=_v, evaluated_at=now)
            paths = _write_report(doc, a.out, basename="well_test_validation")
            for k, v in paths.items():
                print(f"  {k}: {v}")
    elif a.cmd == "drift-monitor":
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from . import ingest as _ingest, __version__ as _v
        from .drift_monitor import DriftMonitor
        now = None
        if a.now:
            now = _dt.fromisoformat(a.now.replace("Z", ""))
            now = now.replace(tzinfo=_tz.utc) if now.tzinfo is None else now
        cfg = _build_config(a)
        mon = DriftMonitor(cfg, a.log_dir, well_name=a.name or a.live_well or (a.file or "well"))
        if a.action == "status":
            print(_json.dumps(mon.status(now), indent=1))
        elif a.action == "approve":
            if not a.entry or not a.approver:
                raise SystemExit("approve needs --entry and --approver")
            print(_json.dumps(mon.approve(a.entry, a.approver, now=now, decision=a.decision, note=a.note), indent=1))
        else:
            station_map = None
            if a.live_catalog:
                from . import production_live_stream
                if not a.live_well or a.station_md is None:
                    raise SystemExit("drift-monitor --live-catalog needs --live-well and --station-md")
                stream, station_map = production_live_stream(a.live_catalog, a.live_well, a.station_md)
            elif a.file:
                stream = _ingest(a.file, port=a.port)
            else:
                raise SystemExit("drift-monitor evaluate needs --file OR --live-catalog")
            r = mon.run_scheduled(stream, station_map=station_map, now=now, force=a.force)
            out = {k: v for k, v in r.items() if k != "evaluation"}
            if r["action"] == "EVALUATED":
                out["classification_counts"] = r["evaluation"]["classification_counts"]
            print(_json.dumps(out, indent=1))
            if a.report and r["action"] == "EVALUATED":
                from .client_reports import gauge_drift_report, write as _write_report
                stt = mon.status(now)
                stt["history"] = mon.history()
                stt["change_log"] = mon.change_log()
                doc = gauge_drift_report(r["evaluation"], mon.corrected_stream(stream), well_name=mon.well_name,
                                         program_version=_v, gauge_spec=getattr(cfg, "gauge_spec", None),
                                         monitor_status=stt, evaluated_at=now)
                paths = _write_report(doc, a.report)
                print("  report:", paths["html"])
    elif a.cmd == "client-report" and a.report == "accuracy":
        from . import __version__ as _v
        from .accuracy_statement import library_backtest
        from .client_reports import accuracy_statement_report, write as _write_report
        bt = library_backtest(ci=a.ci)
        doc = accuracy_statement_report(bt, program_version=_v)
        paths = _write_report(doc, a.out, basename="accuracy_statement")
        print(f"client-report (accuracy): {doc.report_id} - {bt['n_ok']} scored, "
              f"{bt['bands'].get('MEETS_TARGET', 0)} meet target, {bt['n_pending']} pending")
        for k, v in paths.items():
            print(f"  {k}: {v}")
    elif a.cmd == "client-report":
        from . import ingest as _ingest, Reconciler, __version__ as _v
        from .client_reports import gauge_drift_report, write as _write_report
        station_map = None
        well_name = a.name
        if a.live_catalog:
            from . import production_live_stream
            if not a.live_well or a.station_md is None:
                raise SystemExit("client-report --live-catalog needs --live-well and --station-md "
                                 "(the archived excerpt does not state the gauge depth; "
                                 "the CLI will not invent one)")
            stream, station_map = production_live_stream(a.live_catalog, a.live_well, a.station_md)
            well_name = well_name or f"{a.live_catalog} {a.live_well}"
        elif a.file:
            stream = _ingest(a.file, port=a.port)
            well_name = well_name or stream.name
        else:
            raise SystemExit("client-report needs --file OR --live-catalog")
        cfg = _build_config(a)
        ev = Reconciler(cfg).reconcile(stream, station_map=station_map)
        doc = gauge_drift_report(ev, stream, well_name=well_name, program_version=_v,
                                 gauge_spec=getattr(cfg, 'gauge_spec', None))
        paths = _write_report(doc, a.out)
        print(f"client-report ({a.report}): {doc.report_id} - "
              f"{'MODEL DRIFT DETECTED' if doc.data['drift_detected'] else 'NO MODEL DRIFT DETECTED'}")
        for k, v in paths.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
