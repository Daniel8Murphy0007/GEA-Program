# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Headless CLI for the GEA Downhole Simulator.

    python -m gea run          --steps 200 --out run.csv
    python -m gea service-life --years 5 --out curves.csv
    python -m gea telemetry    --hours 24 --seed 11 --out field.csv
    python -m gea case-study   --td 25000 --out case.md

Shared options (all subcommands): --gauges N, --td FT, --profile CSV,
--spec PRESET|JSON, --kickoff FT --inclination DEG (deviation).
No display needed anywhere; every subcommand writes a file and prints a
one-line summary.
"""

from __future__ import annotations

import argparse
import json
import sys


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

    p_sl = sub.add_parser("service-life", help="accumulate twin-leg drift, export divergence curves")
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

    p_b = sub.add_parser("bench", help="bench-test analysis per BENCH_TEST_PROTOCOL.md (or --selftest)")
    p_b.add_argument("--program-csv", type=str, default=None, help="program-model-leg historian CSV (time_s + pressure column)")
    p_b.add_argument("--conv-csv", type=str, default=None, help="conventional-leg historian CSV")
    p_b.add_argument("--full-scale", type=float, default=30000.0)
    p_b.add_argument("--selftest", action="store_true", help="SIMULATION_SELF_TEST: verify the analysis arithmetic on synthetic legs")

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
    p_al.add_argument("--ack", type=str, default=None, help="acknowledge this alarm id after processing")
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
                          ("mqtt", "MQTT subscriber port (3.1.1/5.0; number | json | Sparkplug B): run; record; replay offline")):
        p_lp = sub.add_parser(name, help=helptxt)
        p_lp.add_argument("--config", type=str, default=None, help="site map JSON (client-owned)")
        p_lp.add_argument("--write-example-config", type=str, default=None, help="write an example map here and exit")
        p_lp.add_argument("--replay", type=str, default=None, help="recording (JSON lines) to replay instead of connecting")
        p_lp.add_argument("--record", type=str, default=None, help="write every received message to this recording")
        p_lp.add_argument("--seconds", type=float, default=10.0, help="subscribe/run duration")
        p_lp.add_argument("--read-once", action="store_true", help="opcua: one Read of all nodes instead of a subscription")
        p_lp.add_argument("--out", type=str, default=None, help="write the records CSV here")
        p_lp.add_argument("--stream-csv", type=str, default=None, help="also write a historian-style CSV the other commands ingest")

    p_cs = sub.add_parser("case-study", help="depth sweep, write the one-page markdown case")
    _add_well_args(p_cs)
    p_cs.add_argument("--points", type=int, default=12)
    p_cs.add_argument("--name", type=str, default="case-study well")
    p_cs.add_argument("--horizon", type=float, default=5.0)
    p_cs.add_argument("--out", type=str, default="downhole_case_study.md")

    a = ap.parse_args(argv)

    from . import (DownholeEngine, ServiceLifeConfig, ServiceLifeSimulator,
                   TelemetryConfig, TelemetryRecorder, CaseStudyConfig,
                   case_study, write_markdown, GAUGE_SPECS, load_gauge_spec_json,
                   load_well_profile_csv, DEFAULT_TD_FT)

    if a.cmd == "run":
        eng = DownholeEngine(_build_config(a))
        for _ in range(a.steps):
            eng.step()
        out = eng.export_csv(a.out)
        s = eng.summary()
        print(f"run: {s['sensors']} gauges x {a.steps} steps -> {out} "
              f"(avg drift {s['avg_drift_pct']} %FS/yr, suppression {s['canonical_suppression_at_unity_trims']})")
    elif a.cmd == "service-life":
        eng = DownholeEngine(_build_config(a))
        cfg = ServiceLifeConfig(years=a.years, recalibration_interval_years=a.recal, seed=a.seed)
        sim = ServiceLifeSimulator(engine=eng, config=cfg).run()
        out = sim.export_csv(a.out)
        d = sim.divergence_summary()
        print(f"service-life: {a.years:g} yr -> {out} "
              f"(sep {d['predicted_separation_rate_psi_yr']} psi/yr, ratio pred {d['predicted_ratio_suppression']})")
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
            print(_json.dumps(bench_selftest(), indent=1))
        elif a.csv and a.conv_csv:
            from . import ingest as _ingest
            import numpy as _np
            def _leg(path):
                st = _ingest(path, port="historian_csv")
                pc = [c for c in st.channels if "P" in c.upper()]
                if not pc:
                    raise SystemExit(f"{path}: no pressure channel found")
                return st.index, st.channels[pc[0]].values
            tu, pu = _leg(a.csv)
            tc, pc_ = _leg(a.conv_csv)
            print(_json.dumps(bench_analysis(tu, pu, tc, pc_, full_scale_psi=a.full_scale), indent=1))
        else:
            raise SystemExit("bench needs --program-csv AND --conv-csv, or --selftest")
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
    elif a.cmd in ("opcua", "mqtt"):
        import json as _json
        from .live_ports import records_to_stream, summarize, write_records_csv
        mod = __import__("gea.opcua_port" if a.cmd == "opcua" else "gea.mqtt_port", fromlist=["x"])
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
            else:
                tap = mod.MqttTap(a.config, recording_path=a.record)
                recs = tap.run(a.seconds)
                src = tap.cfg["broker"]["host"]
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
        if a.ack:
            now = _dt.fromisoformat(a.now.replace("Z", "")).replace(tzinfo=_tz.utc) if a.now else None
            if now is None:
                raise SystemExit("--ack needs --now (the acknowledgement timestamp)")
            print(_json.dumps(eng.acknowledge(a.ack, a.operator, now), indent=1))
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
    elif a.cmd == "case-study":
        from .deviation import DeviationSurvey
        if getattr(a, "well", None):
            from . import demo_config
            _cfg = demo_config(a.well)
            td = _cfg.td_ft
            kw = {"td_ft": td, "n_depth_points": a.points,
                  "well_name": a.well, "horizon_years": a.horizon,
                  "profile": _cfg.profile}
        else:
            td = a.td if a.td is not None else DEFAULT_TD_FT
            kw = {"td_ft": td, "n_depth_points": a.points, "well_name": a.name, "horizon_years": a.horizon}
        if a.profile and "profile" not in kw:
            kw["profile"] = load_well_profile_csv(a.profile)
        if a.spec:
            kw["gauge_spec"] = (GAUGE_SPECS[a.spec] if a.spec in GAUGE_SPECS
                                else load_gauge_spec_json(a.spec))
        if a.kickoff is not None and a.inclination is not None:
            kw["deviation"] = DeviationSurvey.from_kickoff(a.kickoff, a.inclination, td)
        out = write_markdown(case_study(CaseStudyConfig(**kw)), a.out)
        print(f"case-study: -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
