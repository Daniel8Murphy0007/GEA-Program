# History

The development record of GEA-Program, newest first. Every entry names what
shipped and what the acceptance gate counted at the time. Nothing here is a
claim the gate does not re-verify on every run.

## v0.3.0 - 2026-10-01 - the dashboard as the door, and the patch panel

- **The patch panel** (`wits0.py`, `witsml.py`, `patches.py`): WITS Level 0
  from the drill floor over TCP connect, TCP listen or serial (`pyserial`
  extra), with the record-01 item dictionary, sentinels and non-numeric
  values as GAP, frame date/time as the source timestamp, recording and
  replay, and `gea wits0-sim`, an in-package sender to rehearse with;
  WITSML 1.4.1 read-only client (GetVersion, GetCap, GetFromStore on one log
  object; rows newer than the last seen at each poll; credentials by
  environment-variable name; the store's null as GAP; recording and replay;
  an in-package test store); unit normalisation on every mapping (`unit_in`
  beside `unit`, known pairs converted before scale and offset, unknown pairs
  declined at load); the supervisor (reconnect with backoff 2 ... 60 s,
  heartbeat, latency, per-tag last values, records appended to daily files,
  several sources folded into one stream by priority); the patch API with
  roles; the Patch panel page with a per-patch page of live values and the
  map. A drill-floor stream with no gauge stations is reported for quality
  and alarms with its drift report marked not applicable. CLI `gea wits0`,
  `gea witsml`, `gea wits0-sim`; extras `serial`, `live` now includes
  pyserial. Acceptance section AD (AD1-AD10; gate 199), with live loopbacks
  for both protocols that need no hardware.
- **The dashboard as the door.** `gea/workspace.py` (one folder per site:
  wells from a file, the catalogue or a live tag map; the client's files
  copied in verbatim and hashed; the version store; monitor logs; reports;
  jobs; an append-only audit log with the SHA-256 of every input; migration
  of a `--out` folder), `gea/jobs.py` (every action a job running
  `python -m gea <command>` with its log, status and return code; a
  scheduler for daily, monthly and interval jobs; atomic state files),
  `gea/service.py` (a standard-library HTTP service: the page, the reports as
  static files, a JSON API for every read and action; accounts in
  `users.json` as salted PBKDF2-SHA256 hashes; roles viewer < operator <
  approver < admin; HttpOnly SameSite sessions; the page's own header
  required on every action; validated configuration commits; a series
  endpoint for trend plots; a survey endpoint; only a fixed list of commands
  may run), `gea/web/app.html` (one self-contained page, no external assets:
  Home, Wells, Live data, Alarms, Approvals, Configuration, Reports,
  Verification, Survey, Jobs, Administration; inline SVG trend plots with
  flagged samples marked; keyboard-operable, labelled, status always an icon
  with a label, light and dark, phone width without horizontal scroll).
  CLI: `gea workspace`, `gea serve`, `gea users`, `gea survey`.
- **Alarm log idempotence** (`alarm_engine.py`): the engine rebuilds its
  states from the event log and processes only samples newer than the log's
  watermark (a `PROCESSED` marker per run, kept in the file, never in the
  reports), so re-processing the same stream adds no activations, KPIs no
  longer double, and an acknowledgement made yesterday still stands today.
  Found by the first acknowledge from the page.
- Acceptance section AC (AC1-AC17): the workspace, jobs and scheduler,
  accounts, the service through HTTP (sessions, header, roles, reads, jobs,
  configuration, upload, acknowledgement, approvals, audit) and the CLI.
  Gate: 189 checks. Section AC is part of the FAT/SAT protocol.
- Verified in a real browser (Chromium): setup, sign-in, every page, the
  trend plot and channel switch, acknowledge from the wall, a level-1
  well-test decision, a criteria commit, a job from Verification, keyboard
  focus order, the theme toggle, a viewer's restricted pages, phone width.

## v0.2.0 - 2026-09-30 - the standalone ship

- **Live protocol ports.** `opcua_port` (read-only asyncua client: read once
  or subscribe; StatusCode severity -> GOOD / STALE / GAP; Basic256Sha256 when
  a certificate and key are given; credentials by environment-variable name
  only) and `mqtt_port` (paho-mqtt v2 subscriber, MQTT 3.1.1/5, TLS; number,
  JSON and Sparkplug B payloads with a dependency-free Sparkplug codec and
  alias learning from any NBIRTH/DBIRTH). `live_ports` holds what every port
  shares: the client-owned tag map (an empty map is declined), record creation
  with source and ingest timestamps, records -> time-indexed stream, hand-off
  to the store-and-forward buffer, JSON-lines recording and replay. CLI
  `gea opcua` / `gea mqtt`; extras `opcua`, `mqtt`, `live`; example configs in
  `docs/examples/`. Acceptance section AB (14 checks; the live OPC UA loopback
  runs wherever asyncua is installed). Gate: 172 checks.
- **Standalone.** The package now proves on every build that it depends on
  nothing but the standard library, numpy, its declared optional extras and
  itself: `tools/standalone_check.py` (imports, text, metadata), run by CI, by
  `ship.ps1` and by `pytest`. The import machinery that produced v0.1.0, its
  import record and the register audit are gone; every docstring, comment,
  catalogue provenance note and document is written in the program's own
  words. The acceptance suite, the constants and every computed result are
  unchanged (48 of 52 modules byte-identical in code structure; the other
  four changed only where a check message or a dictionary key was renamed).
  Commercial documents rewritten for the MPL-2.0 licence.

## v0.1.0 - 2026-09-29 - first ship

The downhole gauge program as its own package: 49 modules under `gea/`, the
52-entry public archive catalogue with a provenance file per entry, the
front door (`gea`) and the desktop window, the tester guide, the requirements
matrix and the commercial documents under `docs/`. Licence MPL-2.0 (`LICENSE`;
Exhibit A header on every source file). Acceptance 157/157.

What the package contains, by layer:

- **Measurement record and quality** (`sample_record`): one record shape for
  every sample; quality rules RANGE / ROC / FLATLINE / SPIKE / STALE / GAP
  with the rule and limit that fired; ranges from the gauge datasheet, cited.
- **Gauge drift and reconciliation** (`reconciler`, `drift_monitor`): live
  series against the well baseline; classification with the numbers beside
  it; scheduled evaluation log; CURRENT / STALE; re-fit change log with
  before/after coefficients; approve -> apply -> re-evaluate; SLA clocks;
  annual re-fit cap.
- **Accuracy statement** (`accuracy_statement`): MAPE with a seeded bootstrap
  90 % CI; the band read at the conservative end of the interval.
- **Well-test validation** (`well_test_validation`): stable-period detection
  with the client-agreed criteria in a file (hashed on every report), reason
  codes, virtual rates, two-level approval trail.
- **Alarm management** (`alarm_engine`): setpoint / deadband / on-delay state
  machine, event log, ISA-18.2-style KPIs against their targets.
- **Model cards, store-and-forward, configuration versioning, SBOM, monthly
  SLA, FAT/SAT, dashboard** (`model_card`, `store_forward`,
  `config_versioning`, `sbom`, `sla_report`, `fat_sat`, `dashboard`).
- **Aging models** (`quartz_hpht_extension`, `downhole_engine`,
  `service_life`, `case_study`, `bench`): the conventional datasheet model and
  the program aging model, which multiplies it by a fixed suppression
  composition of three locked engineering constants (K_MEX = 25/12,
  PHI_RES = 0.84, F_TRZ = 0.1; ratio 1.0324 at unity trims). The composition
  is an engineering model with no field validation on record; the model card
  says so and the drift evaluation uses the band between the two models. The
  bench protocol that would confirm or refute it ships in
  `gea/BENCH_TEST_PROTOCOL.md`.
- **Instrument library and ingest** (`gauge_specs`, `tool_library`, `ports`,
  `follower`, `modbus`, `telemetry`, `deviation`, `profile_catalog`,
  `well_assembler`, `segy`): cited datasheets and tools (an entry without a
  source is rejected), read-only LAS / historian CSV / file-follower /
  Modbus TCP / SEG-Y ingest, the well-profile catalogue, the well assembler.
- **Surveying track** (`earth_model`, `strata_join`, `forward_model`,
  `inverse_engine`, `blind_harness`, `correlation`, `gravity_reference`,
  `rock_inventory`, `gamma`, `survey_cmd`, `survey_view`, `project`): the
  Earth Model over the catalogue, depth-joined joint tables, a borehole-gravity
  forward model on standard constants (CODATA 2018 G, standard gravity, IUGG
  mean radius), the inverse engine with site-family priors, the leave-one-out
  blind harness regenerated on every run, well-to-well correlation with the
  distance rule, the cited WGS84 gravity reference, the rock inventory
  (seventeen published density anchors and Vp ranges; ranked classifier with
  overlap disclosure; KTB lithology validation), gamma-ray lithology, the
  one-command `gea survey` path and its renderer.
- **Operator surfaces** (`cli`, `shell`, `operator_app`, `qt6_downhole_app`,
  `matplotlib_demo`, `survey_view`): the `gea` front door, the client shell,
  the desktop operator window (PyQt6, optional) and the animated demo.
- **The gate** (`acceptance_tests`): the product ships only when every check
  is green; the suite ships inside the package and runs on any machine with
  Python and numpy.
