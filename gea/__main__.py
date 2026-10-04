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
                          ("witsml", "WITSML 1.4.1 read-only client: poll a log object from the store; record; replay offline")):
        p_lp = sub.add_parser(name, help=helptxt)
        p_lp.add_argument("--config", type=str, default=None, help="site map JSON (client-owned)")
        p_lp.add_argument("--write-example-config", type=str, default=None, help="write an example map here and exit")
        p_lp.add_argument("--replay", type=str, default=None, help="recording (JSON lines) to replay instead of connecting")
        p_lp.add_argument("--record", type=str, default=None, help="write every received message to this recording")
        p_lp.add_argument("--seconds", type=float, default=10.0, help="subscribe/run duration")
        p_lp.add_argument("--read-once", action="store_true", help="opcua: one Read of all nodes instead of a subscription")
        p_lp.add_argument("--out", type=str, default=None, help="write the records CSV here")
        p_lp.add_argument("--stream-csv", type=str, default=None, help="also write a historian-style CSV the other commands ingest")

    p_ws = sub.add_parser("workspace", help="the client site folder: init, add wells (file | catalogue | live), list, migrate a --out folder, refresh the dashboard, audit log")
    p_ws.add_argument("--path", type=str, required=True, help="the workspace folder")
    p_ws.add_argument("--action", type=str, default="list",
                      choices=["init", "add-file", "add-catalog", "add-live", "remove", "list", "migrate", "refresh", "audit",
                               "add-seismic", "refresh-seismic", "remove-seismic", "sar-film", "refresh-all", "add-track", "refresh-track", "remove-track"])
    p_ws.add_argument("--stations", type=str, nargs="+", default=None, help="add-track: two or more array station ids of this workspace")
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
    p_ws.add_argument("--sources", type=str, default=None, help="add-seismic: the rigs CSV (source_id, lat, lon, start_utc, end_utc[, kind, note])")
    p_ws.add_argument("--band", type=float, nargs=2, default=None, metavar=("LO", "HI"), help="add-seismic: the band, Hz (default 1 50)")
    p_ws.add_argument("--permits", type=str, default=None, help="add-seismic: a permit export to turn into the rigs CSV (instead of --sources)")
    p_ws.add_argument("--station", type=str, default=None, help="refresh-seismic / remove-seismic: the station id")
    p_ws.add_argument("--name", type=str, default=None, help="init: site name; add-*: display name")
    p_ws.add_argument("--file", type=str, default=None, help="add-file: the client's data file; add-live: the port configuration JSON")
    p_ws.add_argument("--entry", type=str, default=None, help="add-catalog: catalogue entry")
    p_ws.add_argument("--well", type=str, default=None, help="add-catalog: well tag inside the entry; remove: well id")
    p_ws.add_argument("--station-md", type=float, default=None, help="station MD in ft (catalogue wells need it)")
    p_ws.add_argument("--port", type=str, default=None, choices=["opcua", "mqtt", "modbus_g6"], help="add-live: the port")
    p_ws.add_argument("--from", dest="from_dir", type=str, default=None, help="migrate: a folder written by `gea dashboard --out`")
    p_ws.add_argument("--actor", type=str, default="cli", help="who is acting (recorded in the audit log)")
    p_ws.add_argument("--month", type=str, default=None, help="refresh: also measure the SLA for YYYY-MM")
    p_ws.add_argument("--sat", action="store_true", help="refresh: also run the SAT protocol")
    p_ws.add_argument("--outage", action="append", default=None, help="refresh: outage window start/end (ISO), repeatable")

    p_sv = sub.add_parser("serve", help="serve the dashboard over a workspace: the page, the reports and the JSON API (standard library HTTP)")
    p_sv.add_argument("--workspace", type=str, required=True, help="the workspace folder (gea workspace --action init)")
    p_sv.add_argument("--host", type=str, default="127.0.0.1", help="bind address; 0.0.0.0 exposes it on the site network (put TLS in front)")
    p_sv.add_argument("--port", type=int, default=8765)
    p_sv.add_argument("--workers", type=int, default=1, help="jobs run at once")
    p_sv.add_argument("--no-scheduler", action="store_true", help="do not run scheduled jobs from this process")
    p_sv.add_argument("--behind-proxy", action="store_true", help="a reverse proxy terminates TLS in front: trust X-Forwarded-For/-Proto, mark the cookie Secure (deploy/ has nginx and Caddy examples)")

    p_hk = sub.add_parser("housekeeping", help="rotate the append-only logs, prune finished job folders and old live recordings (dry run unless --apply)")
    p_hk.add_argument("--workspace", type=str, required=True)
    p_hk.add_argument("--apply", action="store_true")
    p_hk.add_argument("--rotate-mb", type=float, default=50.0)
    p_hk.add_argument("--jobs-keep-days", type=int, default=30)
    p_hk.add_argument("--jobs-keep-n", type=int, default=500)
    p_hk.add_argument("--records-keep-days", type=int, default=90)
    p_hk.add_argument("--actor", type=str, default="housekeeping")
    p_hk.add_argument("--json", action="store_true")

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

    p_w0 = sub.add_parser("wits0-sim", help="a WITS0 sender for testing a patch without a rig: listens once, then streams a deterministic drilling sequence")
    p_w0.add_argument("--port", type=int, default=5001)
    p_w0.add_argument("--frames", type=int, default=600)
    p_w0.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    p_w0.add_argument("--seed", type=int, default=1)
    p_w0.add_argument("--connect", type=str, default=None, help="host:port - instead of listening, connect to a listening tap and push frames")

    p_dr = sub.add_parser("doctor", help="which code is running and can it serve: Python, the package, duplicates, the page, PyPI; with --workspace also the site folder and the port")
    p_dr.add_argument("--workspace", type=str, default=None)
    p_dr.add_argument("--host", type=str, default="127.0.0.1")
    p_dr.add_argument("--port", type=int, default=8765)
    p_dr.add_argument("--json", action="store_true")

    p_pm = sub.add_parser("permits", help="a drilling-permit export (CSV/TSV) to the rigs CSV the seismic tests judge against, with an import note")
    p_pm.add_argument("--file", type=str, required=True, help="the permit export (a regulator's query CSV, an operator's schedule)")
    p_pm.add_argument("--out", type=str, required=True, help="the rigs CSV to write (an .import.json note is written beside it)")
    p_pm.add_argument("--mapping", type=str, default=None, help='JSON {"lat": "<column>", "lon": ..., "start": ..., "end": ..., "id": ..., "name": ...} when the guesses are wrong')
    p_pm.add_argument("--default-days", type=float, default=30.0, help="a row without an end date works this many days from its start (default 30)")
    p_pm.add_argument("--within", type=float, nargs=3, default=None, metavar=("LAT", "LON", "KM"), help="keep only rigs within KM of LAT, LON")
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
                                           "beam", "array-detect", "locate", "array-selftest", "sar-film", "bearings", "track", "track-selftest"], default="info")
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
    p_se.add_argument("--sources", type=str, default=None, help="detect: CSV of known sources (source_id, lat, lon, start_utc, end_utc[, kind, note])")
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
    p_ce.add_argument("--action", choices=["list", "add", "status"], default="list")
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

    p_sy = sub.add_parser("survey", help="a LAS file in, one strata report out (--demo: the bundled public KTB excerpt); --out writes report.txt + survey.json")
    p_sy.add_argument("--file", type=str, default=None, help="LAS 2.0 file")
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
        st = _ingest(a.file, port=a.port)
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
        if not (a.file or a.demo):
            raise SystemExit("survey needs --file <LAS> or --demo")
        txt, machine = run_survey(path=a.file, demo=a.demo, family=a.family, lat=a.lat, elev=a.elev)
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
                                          a.accuracy_pct_fs, a.full_scale, a.unit, a.doc, a.note), indent=1))
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
                h = T.bearing_history(trs, sensors, band, a.win or 600.0, None, a.seg, a.smax, 41, a.method)
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
            note = PM.import_permits(a.file, a.out, mapping=mapping, default_days=a.default_days, within=tuple(a.within) if a.within else None)
        except ValueError as e:
            raise SystemExit(f"permits: {e}")
        print(_json.dumps(note, indent=1) if a.json else PM.report_text(note))
        return 0
    elif a.cmd == "update":
        from .doctor import update as _update
        return _update(check_only=a.check, extras=a.extras, as_json=a.json)
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
        print("   Ctrl+C stops it; every action is in records/audit.jsonl")
        svc.serve_forever()
        return 0
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
                    raise SystemExit("remove needs --well <id>")
                ws.remove_well(a.well, actor=a.actor); print("removed (folder kept):", a.well); return 0
            elif a.action == "migrate":
                if not a.from_dir:
                    raise SystemExit("migrate needs --from <folder written by gea dashboard --out>")
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
                                            sources_csv=a.sources, band=a.band, permits_csv=a.permits)
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
                    raise SystemExit("remove-track needs --track <id>")
                ws.remove_track(a.track, actor=a.actor); print("removed (folder kept):", a.track); return 0
            elif a.action == "sar-film":
                r = ws.sar_film(actor=a.actor, seed=a.seed, hours=a.hours, step_s=a.step, band=tuple(a.band) if a.band else (1.0, 20.0), method=a.method, scene=a.scene)
                print(f"sar-film: {r['label']}, {r['frames']} frames over {r['hours']} h; verdicts {r['verdicts']}\n  film: {r['path']}"); return 0
            elif a.action == "refresh-all":
                r = ws.refresh_all(actor=a.actor)
                print(f"refresh-all: {r['wells']} well(s) refreshed, {r['seismic']} seismic station(s) refreshed, {len(r['errors'])} error(s)")
                for e in r["errors"]:
                    print("  error:", e)
                return 0 if not r["errors"] else 1
            elif a.action == "remove-seismic":
                if not a.station:
                    raise SystemExit("remove-seismic needs --station <id>")
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
        print(f"wits0-sim: listening on 127.0.0.1:{port}; it waits (silently is normal) until a patch connects, then sends {a.frames} frames {a.interval:g} s apart. Ctrl+C stops it.", flush=True)
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            stop.set()
            print("wits0-sim: stopped")
        return 0
    elif a.cmd in ("opcua", "mqtt", "wits0", "witsml"):
        import json as _json
        from .live_ports import records_to_stream, summarize, write_records_csv
        mod = __import__({"opcua": "gea.opcua_port", "mqtt": "gea.mqtt_port", "wits0": "gea.wits0", "witsml": "gea.witsml"}[a.cmd], fromlist=["x"])
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
            else:
                tap = mod.WitsmlTap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = tap.cfg["url"]
        except (NotImplementedError, ConnectionError, ValueError) as e:
            raise SystemExit(f"{a.cmd}: {e}")          # the reason, without a traceback
        print(f"{a.cmd}: {src}")
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
