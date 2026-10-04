# History

The development record of GEA-Program, newest first. Every entry names what
shipped and what the acceptance gate counted at the time. Nothing here is a
claim the gate does not re-verify on every run.

## Unreleased

- **The SAR panel and the Audit / Update tab.** The Seismic page gained its
  control panel and screen: the second leg's arithmetic played forward in
  time on the labelled synthetic scene (`seismic_film.py`), every frame
  stamped SIMULATION_SELF_TEST, closing on the array detectability test's own
  verdict; a real record never goes through it. A new tab holds the whole
  audit log with filters and a CSV, the running program against PyPI with
  `gea update` as a job, and every report against its source with
  `refresh-all` as a job. Section AQ; gate 262; sixteen help pages.
- **The kit is the commit.** The kit builder let pip replace the checkout's
  wheel with PyPI's of the same version, so three v0.6.0 kit runs gated the
  released code instead of the commit; it now downloads only the wheel's own
  requirements, asserts the built wheel survived, and records the commit.
- **The installed kit's gate.** Both v0.6.0 kit jobs built and installed the
  kit and then failed its own `verify` at 257/258: check AN4 asked for
  `tools/seismic_reader_check.py` beside the package, true in a checkout and
  false in site-packages. The check now guards on the checkout like AJ1 and
  AM1, and the gate was run from the package installed in a bare virtual
  environment before the fix left the cloud. `attach-kit.sh` creates the
  release on a push to main when the tag exists and a failed tag run left
  none; the workflow runs on pushes touching the attach script or the gate.

## v0.6.0 - 2026-10-04 - the second leg whole - the reader proven on real files, the response, the array step, and the Seismic page

- **The leg on the dashboard.** A seismic station or array is a thing in the
  workspace now, beside the wells: added by upload or from the command line
  with its record, position, band, rigs list and station file, refreshed as
  a job that runs the whole leg and writes the Seismic Station Report, and
  read on its own page - the spectrum drawn, the persistent lines, the
  detectability verdicts with the radius, the beam with its half-width, the
  array verdicts - with the limits printed under each card. The sample report
  joins `docs/report_samples/` and the kit. Gate: 258 checks.
- **The array step.** One station detects; an array gives a direction;
  arrays give a point. `seismic_array.py` is the standard array processing
  (Rost and Thomas 2002): Bartlett and Capon beams over a slowness grid on
  the Welch cross-spectral matrix, refined around the maximum so the grid is
  not the error; the array response function beside every bearing, so the
  resolution is a number and the aliasing lobes of a sparse geometry are
  named; coherence judged per frequency bin at the found slowness, because a
  rig's lines are a few bins in a band of noise; the weighted crossing of
  bearings with its ellipse; lag location with its velocity named as the
  input it is; and the array detectability test against the ground-truth
  list. On the labelled scene a 1.2 km, nine-sensor array points at rigs
  from 6 to 45 km within 0.3 degrees of their bearings against a 7-degree
  tolerance, and three arrays cross within a kilometre of the rig. Nothing
  is claimed from one array but a direction.
- **The reader proven on real files; the response added.** With obspy's
  test corpus fetched by pip on the device VM (about ninety miniSEED files
  from real stations and odd recorders) and libmseed beside it, the reader
  was held to the reference file by file: the first pass found the
  little-endian Steim rule (8-bit differences in memory order, not in the
  swapped word), the byte-order flag's meaning, the data-offset-zero case,
  the text records, and the gain-ranged formats of the old networks, all
  read from libmseed's own source; the last pass is 76 files sample-exact,
  5 text files skipped by design, no mismatches, and SAC 12 of 12 against
  obspy. `tools/seismic_reader_check.py` repeats it anywhere. Then the
  response: `seismic_response.py` reads StationXML into its stages and
  evaluates them as evalresp does - proven on IU.ANMO.10.BHZ to 1e-5 in
  amplitude and 1e-6 degree in phase, the FIR delay correction included -
  and removes the response with a water level and pre-filter to m/s, m or
  m/s^2; a known motion through the real response and back comes out with
  no error in the band. The reference files ship under `gea/reference/`
  with provenance; section AN holds the evaluation to evalresp's numbers.

- **The Windows kit's install step, read at last.** With the owner signed in
  to GitHub in the browser pane the v0.5.0 log could be read: the `cd` fix
  held, the build step passed in 26 s with every wheel including PyQt6, and
  `install.cmd` failed at pip's own guard - pip run as `python wheel\pip`
  will not install pip on Windows. `pip_bootstrap.py` runs pip as a module
  from inside its wheel; install.cmd uses it. Unverified on a Windows runner
  until the next manual run.

## v0.5.0 - 2026-10-03 - standard physics only, the second leg begins, the report samples, and the kit made whole

- **The second leg: seismic ingest and the detectability test.** The client's
  site records ground motion that conventional processing treats as noise;
  the leg's purpose is the rigs inside it. It starts with ingest and one
  measured number, not a map. `seismic.py` reads miniSEED (Steim1/Steim2 and
  the plain encodings) and SAC by content, writes both, fetches from any FDSN
  service (TexNet, EarthScope), and gives Welch PSDs, spectrograms, band power
  and persistent lines over a running-median floor. The Steim decoders were
  checked against libmseed, the reference implementation, on the maintainer's
  machine in both directions (1,904 samples exercising every packing form,
  bit for bit) and the libmseed records ship under `gea/reference/` so the
  gate repeats the check anywhere. `seismic_detect.py` is the detectability
  test: one station's record against a CSV of known rigs, verdict per rig
  from band power in its exclusive windows against the quiet baseline, the
  lines that belong to it, and the radius bracketed between the farthest rig
  heard and the nearest missed; the report prints what it will not call a
  measurement (a position or track - one station detects, it does not
  locate). Public data to start on: TexNet (network TX, open FDSN, Texas
  RRC permits as ground truth), EarthScope, Utah FORGE and Brady's nodal sets
  on the DOE GDR. Neither the cloud workspace nor the device VM can reach the
  FDSN hosts (egress policy), so the first real record is fetched by the
  owner and dropped into the clone. Gate: 246 checks.

- **The program's own aging model removed.** An independent review of 0.3.0
  pointed at the fixed factor on the band; the owner's rule is that the program carries
  standard physics and cited numbers only. Reading the code: the drift band's
  lower edge was the datasheet rate divided by a composition of constants
  inherited from the predecessor program, renamed but not removed in the
  standalone rewrite, and the bench protocol, service-life curves, case
  study, desktop panels and demo were built around the comparison between
  that model and the datasheet. All of it is gone: `gauge_aging.py` returns
  the datasheet's published drift specification and nothing else (flagged
  above the rating, never changed); the reconciler's band is half to twice
  that rate; the simulator's stations take their rate from their tool's
  datasheet and a tool with no published rate carries none; the template
  preset with its engineering-fit knees is gone and the default datasheet is
  the cited GEOQ 177 entry; a datasheet JSON cannot carry a model parameter.
  The bench is rebuilt as the datasheet-conformance test the drift report
  cites as NONE ON RECORD - the bench record on the list. The guard now
  blocks the removed model's names, phrases and numbers in every tracked
  file; section AK proves the absence from inside the package. Report numbers changed only where the removed model had reached:
  the band's lower edge (now the datasheet rate) and the synthetic
  simulator's noise shaping (the injected-offset checks re-derived).

- **The report samples live in the repository.** The twelve rendered reports
  the owner had been reading were chat deliverables from 2026-09-28, made by
  the predecessor's build before this package existed, and never committed
  anywhere; they carried that build's number and the removed model card. They
  are replaced by `docs/report_samples/`, seventeen files rendered from this
  checkout by `tools/render_report_samples.py` (both Volve wells; the alarm,
  resilience, monitored-drift and SLA reports from the synthetic field
  generator, labelled), named in `SAMPLES.md` with their commands, shipped
  in the kit, held to the build by section AM. The kit now carries every
  optional dependency including the desktop window.

## v0.4.0 - 2026-10-02 - doctor, files, the operator's experience, instruments and transients, hardening, and the help library

- **The help library** (`help/*.md`, `helplib.py`): indexed by the job a
  reader arrives with, never by module; four lines per page and no more; one
  source of text for `gea help`, `/api/help` and the dashboard's panels, so
  the terminal and the page cannot drift apart. The tester guide ships inside
  the package and `gea guide` works on a plain install (an independent review
  of 0.3.0 found that it did not). Section AJ (4 checks). Gate: 237 checks.
- **On the list, not built**: a bench record - one gauge with a known history
  run against the aging band, pass or fail, written down - is the next physics
  item; the band keeps its label (NONE ON RECORD) until then. And one worked
  site, start to finish, with every number a person can recompute by hand.
- **The standalone install kit** (`tools/build_installer.py`,
  `.github/workflows/build-installer.yml`): one folder that carries its own
  Python (the embeddable CPython from python.org), the package wheel built
  from the checkout, every dependency wheel for the chosen extras, and the
  scripts a site needs (install, gea, start-dashboard, stop-dashboard,
  register-service, unregister-service, verify, uninstall), with a README, a
  manifest and a SHA-256 list. It installs with no network and no
  administrator rights; the site's data lives in a workspace the kit creates
  and never deletes. A Linux variant builds a virtual environment from the
  machine's python3 and ships a systemd unit. CI builds both kits on every
  tag, installs them the way a client does, runs the gate from the installed
  kit, and attaches the zips to the GitHub release. Found while running the
  gate from an installed kit: `wits0` and `witsml` were registered only when
  their command imported them (now at `import gea`), and one check message
  tripped the vocabulary gate when pymodbus was present (reworded; gate 200).
- **`gea doctor`** (`doctor.py`): the environment and the workspace in one
  screen, every finding with its fix; `gea serve` runs the checks and refuses
  to start on a blocking one, so an installed copy shadowing a checkout (the
  cause of two "the page has no such route" reports) is caught before the
  page is served. Section AE (3 checks).
- **The file system** (`files.py`): roots the administrator allows and that
  no path can escape; detection by content (an OLE workbook, a zip, SEG-Y, a
  LAS file behind its comment lines, an operator table, a historian CSV, JSON);
  import with a duplicate guard by hash; watch folders; "save to..." on every
  report; the evidence pack (zip, manifest, SHA-256 list). The dashboard
  reads any detected type, marks an unreadable file on its well instead of
  stopping, and sends a depth log to the survey. Section AF (7 checks).
- **The operator experience** (Band D): shelving with an expiry and a
  reason, expiring on its own and logged as such; acknowledge-all; shelved
  alarms in the report, on the wall and on the well page; per-user units,
  time zone, theme and help preference, site-wide defaults; since-your-last-
  visit; badge counts; one search box; notification rules (`notify.py`:
  webhook and SMTP, event rules by priority, a quiet window, a delivery log,
  secrets from the environment only, a poller that announces new activations,
  failed jobs, patches down and up, items waiting for approval, imports);
  the first-run guide; help panels; print. Section AG (9 checks, with a
  local webhook receiver and a local mailbox as stand-ins).
- **Instruments and transients** (Band 2): the sensor swap register and the
  step detector on the offset against peer gauges (a process change moves
  every gauge; a swap moves one; with a single peer the step is reported on
  both and flagged; with more the swapped gauge is named), the fit segmented
  at the swap (`sensor_swap.py`); calibration certificates per instrument with
  a status and the stated accuracy beside the measured bias in the drift
  report (`certificates.py`); shut-in detection by rate, on-stream hours or
  the pressure signature (`shut_in.py`); the build-up analysis
  (`transient.py`): Horner line on a middle-time region chosen by the flat
  Bourdet derivative on a log-binned copy with a hump check, wellbore storage,
  kh / k / skin / radius of investigation, a residual bootstrap band, an
  analyst's override of the region. On a synthetic line-source record with k
  = 50 md and skin = 3 the analysis returns 50.7 md and +3.15 with the truth
  inside the band; with log-spaced samples and storage, 50.0 md and 3.00.
  The Shut-in and Pressure Transient report; the instruments section of the
  drift report; the well-page cards; Home tiles. Section AH (8 checks).
- **Hardening**: sign-in rate limit with lock-out (per name and per address,
  429 with Retry-After, audited), live sessions listed and revocable, sign
  out everywhere else, security headers on every response, `--behind-proxy`
  (forwarded address, Secure cookie, HSTS) with nginx and Caddy examples and
  a deployment README, housekeeping that segments the append-only logs (the
  alarm log's watermark carried into the fresh file) and prunes finished jobs
  and old recordings after a dry run, the supervisor load test with the
  service answering. Section AI (6 checks).

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
- **Aging models** (`downhole_engine`, `service_life`, `bench`, and a module
  since removed): at this version the drift band had two edges - a
  conventional datasheet model and a second model that scaled it by a fixed
  composition of engineering constants inherited from the predecessor
  program. That second model was labelled as having no field validation and
  was removed entirely after v0.4.0 (see the entry above); the datasheet rate
  alone remains.
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
