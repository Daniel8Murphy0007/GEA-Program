# Changelog

All notable changes to GEA-Program, newest first. Each released section is
headed by its tag and date; `ship.ps1` refuses to ship a tag that has no
section here. The long-form record, by layer, is `docs/HISTORY.md`; the
session-by-session working record is `docs/SESSION_LOG.md`.

## [v0.4.0] - 2026-10-02 - doctor, files, the operator's experience, instruments and transients, hardening, and the help library

### Added
- The help library (`gea/help/*.md`, `gea/helplib.py`): fourteen pages indexed
  by the job - start, bring data in, quality rules, drift, well tests, alarms
  and the month, the site, instruments, shut-ins and build-ups, patches,
  files, notifications, which code is running, upkeep. Each page carries four
  lines and stops: the command, what it writes, the number to check, and what
  the page will not call a measurement. One source: `gea help <topic>` prints
  it, `/api/help` serves it, and every dashboard page's help panel shows the
  same text with a link to the page (`#/help/<topic>`); the page keeps no help
  text of its own. The tester guide now ships inside the package
  (`gea/help/TESTER_GUIDE.md`, byte-identical to `docs/TESTER_GUIDE.md` by a
  gate check), so `gea guide` works on a plain install. Acceptance section AJ
  (4 checks: the guide in the package, every page's four lines and every
  view mapped, the API, the wheel's contents); the release workflow checks the
  wheel for the guide and the pages before publishing.
- The standalone install kit (`tools/build_installer.py`): its own Python
  (embeddable CPython), the package wheel built from the checkout, every
  dependency wheel, install / start / stop / verify / register-service /
  uninstall scripts, README, manifest and SHA-256 list; a Linux venv variant;
  `build-installer.yml` builds both on every tag, installs them the way a
  client does, runs the gate from the installed kit and attaches the zips to
  the release. README "The standalone install kit"; TESTER_GUIDE part 0.
- `gea doctor` (`gea/doctor.py`): which code is running and can it serve -
  Python, the package and its path, pip's record against the running code
  (editable or installed), duplicates on the path, the page, dependencies,
  the launcher, PyPI's newest; with `--workspace` the site folder, accounts,
  patches, a free port, write access. `gea serve` runs the same checks and
  does not start on a blocking finding. Acceptance section AE.
- The file system (`gea/files.py`, `gea files`, the Files page): import and
  export roots an administrator allows (no path escapes them), browse with
  detection by content (historian CSV, LAS behind comments, SEG-Y, operator
  table, JSON, OLE workbook, unknown), preview, import into a well with a
  duplicate guard by hash, "save to..." on every report, the evidence pack
  (zip with manifest and SHA-256 list), watch folders and scheduled packs.
  The dashboard reads any detected file type; an unreadable file marks its
  well and never blocks the others; a depth log runs the survey, not the
  gauge pipeline. Acceptance section AF.
- The operator experience: alarm shelving with an expiry and a reason
  (`--shelve/--shelve-hours/--unshelve/--note`, the shelf expires on its own
  at the first sample past it, UNSHELVED by 'expiry'), acknowledge-all per
  well and across wells, shelved alarms in the report and on the wall;
  per-user preferences (units field/SI, time zone, theme, help panels) and
  site-wide defaults (Administration); the since-your-last-visit strip on
  Home; badge counts on the navigation; one search box over wells, alarms,
  reports, jobs, configuration, patches, schedules; notification rules
  (`gea/notify.py`, `gea notify`): webhook and SMTP channels, events
  alarm.activated by priority, alarm.shelved, job.failed, patch.down/up,
  approval.pending, file.imported, a quiet window, a delivery log, secrets
  only from the environment; the first-run guide; help panels on every page;
  a print stylesheet. Acceptance section AG.
- Instruments and transients: the sensor swap register and step detector on
  the offset against peer gauges (`gea/sensor_swap.py`, `gea swaps`) - a
  recorded swap restarts the drift fit at the swap; calibration certificates
  per instrument with VALID / EXPIRING / EXPIRED / MISSING status and the
  stated accuracy printed beside the measured bias (`gea/certificates.py`,
  `gea certificates`); shut-in detection by rate, on-stream hours or the
  pressure signature alone (`gea/shut_in.py`); build-up analysis with the
  Horner line on a middle-time region chosen by the flat Bourdet derivative,
  wellbore storage, kh / k / skin / radius of investigation and a residual
  bootstrap band (`gea/transient.py`, `gea transient`); the Shut-in and
  Pressure Transient report; the instruments section of the drift report;
  the Instruments and Shut-ins cards on the well page; tiles on Home.
  Acceptance section AH.
- Hardening: sign-in rate limit (five failures lock a name or an address for
  15 min; 429 with Retry-After; audited), live sessions listed and revocable
  by an administrator, "sign out everywhere else" for every user, security
  headers on every response (CSP, X-Frame-Options, nosniff, referrer policy,
  HSTS behind HTTPS), `gea serve --behind-proxy` (forwarded address and
  Secure cookie) with nginx and Caddy examples in `deploy/`, `gea
  housekeeping` (segment the append-only logs with the alarm watermark
  carried, prune finished jobs and old recordings; dry run first; a schedule
  preset), `gea loadtest` (N simulated WITS0 patches with the service
  answering; a verdict). Acceptance section AI.
- Sections AE-AJ in the FAT/SAT protocol. Gate: 237 checks.

### Changed
- `wits0` and `witsml` are registered in the port registry on `import gea`.
- Acceptance section F covers all four live ports with one message shape;
  AA49 counts no excluded checks.
- `gea/alarm_engine.py`: operator actions check the alarm id and the state
  and say why they are declined; the SHELVED event carries `until`.
- `gea/service.py`: `/api/session` carries the user's preferences, the
  previous sign-in and the site defaults; `users.json` entries carry
  `prefs`, `last_login_utc`, `prev_login_utc`; the manifest carries `site`.
- `gea/dashboard.py`: per file well, the instruments and the shut-in look
  run beside the drift and alarm reports and never block them.
- `gea/web/app.html`: every timestamp is shown in the viewer's zone;
  Administration gains live sessions, site settings and notifications.

### Fixed
- `gea/ports.py`: a historian CSV whose timestamps end in `Z` failed to read
  on Python 3.10 (`fromisoformat` accepts the suffix only from 3.11); the
  reader now normalises it, and a file that mixes naive and zoned stamps is
  compared on the wall clock. Found by section AH on the development machine.
- `gea guide` on an installed release printed "not found": the guide was not
  in the wheel (an independent review of 0.3.0 found it). Fixed by shipping
  it in the package.
- `gea/cli.py`: `gea guide` and `gea help` no longer stop on a character the
  console cannot encode (a cp1252 pipe on Windows); the output streams replace
  it instead. The guide's one such character was removed.

## [v0.3.0] - 2026-10-01 - the dashboard as the door, and the patch panel

### Added
- The dashboard as the door: `gea/workspace.py`, `gea/jobs.py`,
  `gea/service.py`, `gea/web/app.html`; CLI `gea workspace`, `gea serve`,
  `gea users`, `gea survey`. See README "Serving the dashboard".
- Acceptance section AC (17 checks; gate 189); section AC in the FAT/SAT
  protocol; a browser-driven check of the page.
- The patch panel: `gea/wits0.py` (WITS Level 0 over TCP connect, TCP
  listen, serial; in-package simulator `gea wits0-sim`), `gea/witsml.py`
  (WITSML 1.4.1 read-only client; in-package test store), `gea/patches.py`
  (supervised patches with reconnect, heartbeat, daily record files, priority
  fold), unit normalisation on mappings (`unit_in`), the patch API and the
  Patch panel page. Extras `serial`; `live` now includes pyserial.
  Acceptance section AD (10 checks; gate 199).

### Changed
- `gea/alarm_engine.py`: processing is idempotent against the event log
  (states rebuilt from the log, samples before the watermark skipped);
  acknowledgements persist across runs; KPIs no longer double on re-runs.
- `gea/client_reports.py`: the alarm report reads the engine's event list
  (run markers never appear in a report).
- `pyproject.toml`: `web/*` is package data; `serial` extra.
- `gea/dashboard.py`: a stream with no downhole gauge stations (a drill-floor
  feed) gets quality and alarm reports; its drift report is marked not
  applicable instead of failing the refresh.
- `gea/sbom.py`: pyserial listed among the optional components (ten in all).
- README, TESTER_GUIDE: the served dashboard.

## [v0.2.0] - 2026-09-30 - the standalone ship

### Added
- `gea/opcua_port.py`: read-only OPC UA client (asyncua, optional) - read
  once or subscribe; StatusCode severity -> GOOD / STALE / GAP; source,
  server or arrival timestamp; Basic256Sha256 when a certificate and key are
  given; credentials by environment-variable name only; recording + replay.
- `gea/mqtt_port.py`: MQTT subscriber (paho-mqtt v2, optional) - MQTT
  3.1.1 / 5, TLS, QoS per topic, `+` / `#` wildcards; number, JSON
  (value / time / quality paths) and Sparkplug B payloads with a
  dependency-free Sparkplug codec and alias learning from any NBIRTH / DBIRTH;
  recording + replay.
- `gea/live_ports.py`: what every live port shares - the client-owned tag
  map (an empty map is declined), record creation with source and ingest
  timestamps, records -> time-indexed stream, hand-off to the
  store-and-forward buffer, JSON-lines recording and replay, summary.
- CLI `gea opcua` and `gea mqtt` (`--config`, `--write-example-config`,
  `--replay`, `--record`, `--seconds`, `--read-once`, `--out`,
  `--stream-csv`); a missing dependency or an unreachable server is one
  line, not a traceback.
- Extras `opcua`, `mqtt`, `live` (all three live ports) and `xls`
  (`xlrd`, which `read_drift_xls` imported without declaring it).
- `docs/examples/opcua_config.json`, `docs/examples/mqtt_config.json`.
- Acceptance section AB (AB1-AB14): Sparkplug round trip, quality mapping,
  alias learning, number / JSON payloads and wildcards, recording -> replay
  -> stream -> buffer, OPC UA status mapping and replay latency, registry
  status, empty-map refusal, CLI replay and stream CSV round trip; a live
  in-process OPC UA server loopback wherever asyncua is installed. Section
  AB is part of the FAT/SAT protocol.
- `tools/standalone_check.py`: the self-contained guard (imports, text,
  metadata), run by CI, by `ship.ps1` and by `pytest`.
- `CHANGELOG.md` (this file) and `docs/SESSION_LOG.md`.

### Changed
- The package is standalone. Every docstring, comment, check message,
  catalogue provenance note and document is in the program's own words;
  no tracked file names another program, its modules, paper numbers, version
  chains or design-thread handles. Two result keys renamed for consistency:
  blind-harness `doctrine` -> `method`; classifier candidate `landmark_rho`
  -> `anchor_rho`. Function names and every computed value are unchanged
  (48 of 52 modules identical in code structure; the outputs of quickstart,
  survey, client reports, model cards and the dashboard match the previous
  tree number for number).
- `docs/HISTORY.md` restarted at v0.1.0 as GEA-Program's own record.
- `docs/commercial/*` rewritten for the MPL-2.0 licence (they described a
  dual AGPL/commercial licence that never applied to this package) and for
  the current gate and port list.
- `README.md`: "Basis" replaces the lineage section; "Live data" section;
  layout updated.
- `gea/sbom.py`: optional components now list asyncua, paho-mqtt, pymodbus
  and xlrd; the SBOM has nine components.
- `gea/fat_sat.py`: client sections A, C, F, AA, AB.
- `ship.ps1`: step 4 runs the standalone check as a gate; step 5 requires a
  CHANGELOG section for the tag.
- `.github/workflows/ci.yml`: runs the standalone check.
- `.gitignore`: `/_transport/`, `/_to_delete/`.

### Removed
- The import machinery that produced v0.1.0: the importer script under
  `tools/`, its per-file record under `gea/`, the audit that measured what
  the import left behind, and `tools/native/`. Nothing flows in automatically
  any more; future work is built here.

### Verification
- `gea accept` 172/172 in the repository and from the installed wheel in an
  empty virtual environment holding only numpy; the live OPC UA loopback ran
  on the development machine (asyncua 2.0.1, paho-mqtt 2.1.0).
- `tools/standalone_check.py`: 0 findings.

## [v0.1.0] - 2026-09-29 - first ship

The downhole gauge program as its own package: 49 modules under `gea/`, the
52-entry public archive catalogue with a provenance file per entry, the `gea`
front door and the desktop window, the tester guide, the requirements matrix
and the commercial documents. Licence MPL-2.0. Acceptance 157/157.
