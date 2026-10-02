# Session log

The working record: one entry per working session, what was decided, what
was built, what was verified, and what was left open. Amendments are added
at the end of the entry they amend, dated. `CHANGELOG.md` is the per-release
summary; `docs/HISTORY.md` the record by layer.

## 2026-09-29 - v0.1.0: the package exists

- Repository created; the downhole gauge program brought across as its own
  package (`gea/`), with the physics on standard constants and the rock
  inventory on published anchors only.
- `ship.ps1` written for this repository (nine steps, one screen); CI and the
  PyPI trusted-publisher workflow added; `.github/workflows/ci.yml` placed by
  hand after a checkout dropped it.
- Licence chosen: MPL-2.0. `LICENSE` from GitHub; the Exhibit A header put on
  every `.py` and on `ship.ps1`; `pyproject.toml` licence text and classifier.
- Shipped: v0.1.0, commit a426b1a, tag verified, `SHIP_LOG.md` written by the
  ship script. Acceptance 157/157.
- Open: PyPI pending publisher and the GitHub `pypi` environment (owner
  Daniel8Murphy0007, repository GEA-Program, workflow release-to-pypi.yml).

## 2026-09-29/30 - the live ports

- Built `live_ports`, `opcua_port`, `mqtt_port`; wired into `__init__`,
  `__main__` (`gea opcua`, `gea mqtt`), `pyproject.toml` extras, the SBOM
  optional list, `fat_sat.CLIENT_SECTIONS`; README "Live data" section;
  example configs.
- Acceptance section AB (14 checks). Section F now covers the empty-config
  refusal of both ports; the SBOM checks count the new optional components;
  the FAT protocol count of excluded internal checks moved from two to one
  after check messages were reworded for the vocabulary gate.
- Bug found by the first live run and fixed: `load_config` in both ports
  mutated the caller's dictionary (the example config picked up the parsed
  mappings and stopped being JSON-serialisable). Both now work on a copy.
- Verified on the development machine: live in-process OPC UA server -
  read_once both nodes GOOD with 14 ms latency, subscription 10 data changes
  (p50 0.15 s, p95 0.22 s), replay reproduces the session; MQTT unreachable
  broker reported as `ConnectionError`. Gate 172/172.

## 2026-09-30 - standalone

- Decision: GEA-Program is independent of the program it came from. The
  importer, the import record and the register audit are removed; nothing
  flows in automatically again; future bands are built here.
- Decision: the inherited 1,331-line history is replaced by GEA-Program's own
  `docs/HISTORY.md`, starting at v0.1.0; the sweep of docstrings, comments and
  documents is a full re-authoring, not a lineage-only trim.
- Built `tools/standalone_check.py` (imports, text, metadata; Python 3.10
  fallback for the TOML read); wired into CI, `ship.ps1` and `pytest`.
- Re-authored 38 modules, 13 catalogue provenance files, the example register
  map, the bench protocol, the requirements matrix, the tester guide and the
  three commercial documents (now MPL-2.0). Result keys renamed: `doctrine`
  -> `method` (blind harness), `landmark_rho` -> `anchor_rho` (classifier).
- Found by the guard: `read_drift_xls` imported `xlrd` without declaring it
  -> extra `xls`. Found in the commercial documents: an AGPL dual licence that
  never applied to this package, and a citation of a module the package does
  not contain -> rewritten. Found in a provenance file: the same AGPL wording
  -> corrected.
- Verification: AST comparison of every module before and after (48 of 52
  identical in code structure and non-string constants; the other four differ
  only where a check message, a count or a key changed); the outputs of
  quickstart, survey, client report, model cards and dashboard compared file
  by file against the previous tree - identical after masking timestamps,
  except the SBOM's added `xlrd` row, one citation wording and one section
  title; gate 172/172 in the repository and from the wheel in an empty
  environment holding only numpy; standalone check 0 findings; the port
  commands now report a missing dependency as one line.
- Housekeeping left to the owner: delete `_to_delete/` (the removed files,
  parked because the working shell cannot delete) and the stale `build/`
  folder; both are ignored by git.
- Amendment (2026-09-30, ship preparation): version 0.2.0 set in
  `pyproject.toml` and `gea/__init__.py`; `CHANGELOG.md` and this log added;
  `ship.ps1` step 5 now also requires a `CHANGELOG.md` section for the tag;
  `SHIP_MESSAGE.txt` written for v0.2.0.

## 2026-09-30 - the dashboard as the door

- Decision: the dashboard is where the client interacts with the program and
  must reach every feature; the command line stays the engine. Build order:
  workspace, jobs, service, pages and actions, plots, acceptance and docs.
- Built `workspace.py`, `jobs.py`, `service.py`, `web/app.html`; wired
  `gea workspace`, `gea serve`, `gea users`, `gea survey` into the CLI.
- Found and fixed along the way: `gea alarms --ack` needed `--now` (the
  service passes it); the alarm engine re-logged every activation on every run
  and lost acknowledgements between runs (idempotence against the event log
  added; KPI activations halved to the true count); uploads kept a staging
  name instead of the client's file name; a job-state file could be read
  half-written (atomic writes, tolerant reads); two views rendering at once
  could interleave (views now render one at a time, in order); the approvals
  queue listed superseded re-fit proposals (only the open proposal per station
  is shown); tables and approval rows overflowed at phone width.
- Verification: gate 189/189 in the cloud; a Chromium run through every page
  and action; the standalone check green (the service and page add no
  dependency).
- Not yet: the live loopback of a tap from the page on the development
  machine (needs asyncua, which the cloud lacks); the standalone check from
  the Verification page works from a checkout, not from the wheel (documented
  in the page's message).

## 2026-10-01 - band A: the patch panel

- Decision: WITS0 first (TCP and serial, the most common floor feed and the
  easiest to simulate), WITSML behind it; both with in-package simulators so a
  site rehearses a patch before the rig is on line and the gate tests the
  protocols with no hardware.
- Built `wits0.py`, `witsml.py`, `patches.py`; unit normalisation in
  `live_ports.TagMapping`; patch API and Patch panel page; `gea wits0`,
  `gea witsml`, `gea wits0-sim`.
- Found along the way: a bounded WITS0 run treated the sender closing as an
  error (now: a bounded run ends, a supervised run reconnects); the WITSML
  test store read the query unescaped and returned rows before the start
  index (fixed; incremental polling verified: the next poll returns only
  rows newer than the last seen); the refresh failed on a drill-floor stream
  because the reconciler needs gauge stations (now reported as not
  applicable; quality and alarms still run); the simulator stamped frames at
  schedule time instead of send time, inflating latency.
- Verification: gate 199/199 in the cloud (AD3 and AD6 are live loopbacks over
  real sockets); a Chromium run adds a WITS0 patch against the simulator from
  the page, watches it connect, reads its live values, stops it; standalone
  check 0 findings.
- Amendment (2026-10-01, ship preparation): version 0.3.0 set in
  `pyproject.toml` and `gea/__init__.py`; CHANGELOG and HISTORY sections
  headed v0.3.0; `SHIP_MESSAGE.txt` written for v0.3.0. Found on the
  development machine before the ship: a plain `pip install .` had frozen an
  older copy of the package under `gea`, so the served page lacked the patch
  routes; `gea serve` now prints the folder it serves from, and `gea
  wits0-sim` reports that it is waiting, when a client connects and as frames
  go out. The editable install (`pip install -e .`) is the recommended way to
  run the service from a checkout.
- Amendment (2026-10-01, first ship attempt): `ship.ps1 -DryRun` was green and
  the real run went red on AD9 a minute later - the overview request read the
  audit log while a patch thread was appending to it and hit a half-written
  line. Fixed at the root: audit writes are serialised under a lock and the
  reader skips a line that is still being written; AD9 now reports what the
  overview returned instead of raising. The gate passed three consecutive
  runs after the fix. Also fixed: a `re.split` deprecation warning in the
  standalone check.

## 2026-10-01 - the standalone install kit

- Decision: the stronger form of standalone - the kit carries its own Python
  so a client machine needs nothing installed and no internet.
- Built `tools/build_installer.py` (Windows: embeddable CPython + wheels +
  scripts; Linux: venv + wheels + scripts + systemd unit) and
  `build-installer.yml` (both kits on every tag, installed and gated the way a
  client does, attached to the release).
- Verified: the Linux kit built on the development machine, its hashes
  checked, installed offline, started the dashboard, ran the gate; the Windows
  kit assembled with a stand-in Python zip (python.org is not reachable from
  this session's shell; it is from PowerShell and from CI) - `_pth` enabled,
  CRLF scripts, Windows wheels present. The real Windows kit is built with
  `python tools\build_installer.py` in PowerShell or by CI at the tag.
- Found: `wits0`/`witsml` registered only on command import; a section F
  message tripped the vocabulary gate with pymodbus installed. Both fixed;
  gate 200.

## 2026-10-02 - doctor, files, the operator experience, instruments and transients, hardening

- Sequence agreed: `gea doctor`, then Band B (files), then Band D (the
  experience), then Band 2 (instruments and transients), hardening threaded
  in before the first site goes live. No version bump: this session's work
  sits under CHANGELOG `[Unreleased]` until the ship is prepared.
- `gea doctor` and the `serve` start-up gate: the two stale-page reports of
  the previous session both came down to "which copy of the package is
  running"; the doctor answers it with the fix on every line. Found while
  testing: a connect-based port probe was fooled by a listen backlog (now
  bind-based); a check that evaluated "busy" after closing its socket.
- Files: detection by content rather than extension (a Petrel LAS export
  starts with `#` lines; an `.xls` without xlrd is declined with the reason
  rather than imported as text); one unreadable file no longer aborts the
  refresh; a depth-indexed log goes to the survey instead of the gauge
  pipeline.
- Band D: the alarm engine's `shelve` gained an expiry (`until` on the
  event, auto-UNSHELVED by 'expiry' at the first sample past it) and the
  operator actions now check the id and state; the service grew preferences,
  site defaults, since-last-visit, badges, search, the alarm action routes and
  the notification poller; the page grew the search box, help panels, the
  first-run guide, preferences, print CSS, the sessions and notifications
  cards. Notifications deliver in the poller thread and never block a
  request; the SMTP password is an environment variable by rule (a
  configuration with a password in it is declined). Section AG tests both
  channels against a local webhook receiver and a 40-line SMTP stand-in.
- Band 2: the first step detector compared raw levels and was fooled by the
  shut-in's 500 psi process move (mirrored by every gauge); rewritten on the
  offset against the peer median, it finds the 30 psi swap and nothing else.
  The build-up clock is the last flowing sample (the first version measured
  from the first closed sample and the skin came out at -5); the radial-flow
  finder moved from a point-wise log-log slope (too noisy on hourly data) to
  a regression over log-binned medians with a half-window hump check; both an
  hourly noisy record and a log-spaced record with storage now return k and
  skin within the band. Instruments section in the drift report; transient
  report; well-page cards; tiles.
- Hardening: rate limit, sessions, headers, proxy mode, housekeeping (the
  alarm log's PROCESSED watermark is re-written as the first line of the
  fresh file so nothing is re-processed), load test (6 patches at 2 Hz with
  the service at p95 4 ms here). `deploy/` with the TLS examples.
- Verification: sections AG (9), AH (8), AI (6); gate 233/233 in the cloud;
  standalone check 0 findings over 207 tracked files; Chromium runs of the
  alarm wall (shelve through the dialogs, unshelve), preferences, search, the
  first-run guide, Administration, the well page with the instruments and
  shut-in cards, the print view and phone width.
- Docs: CHANGELOG `[Unreleased]` (the kit entry moved out of the shipped
  v0.3.0 section), HISTORY, README (dashboard pages, doctor, notifications,
  instruments and transients, before a site goes live, quick-start lines),
  TESTER_GUIDE.
- On the development machine (Python 3.10): the standalone check 0 findings;
  the gate run section by section in the foreground (a background run is
  ended with the shell there), 233/233 after one fix - `ports.py` could not
  read a historian CSV with `Z` timestamps on 3.10, which section AH's
  synthetic record exposed; normalised in the reader. A stale
  `.git/index.lock` left by a read-only `git status` in that shell was moved
  to `_to_delete/`.
- An independent review of 0.3.0 was read against the code. Its one concrete
  hole was real: `gea guide` fails on an installed release because the guide
  was not in the wheel. Its help-library proposal was taken as written: pages
  by the job, four lines each, generated from one text. Built as
  `gea/help/*.md` + `helplib.py`; `gea help`, `/api/help`, the dashboard's
  panels and a Help view all read the same files; the inline help strings in
  the page were removed. The guide now ships in the package with a gate check
  that it matches the repository copy. Section AJ; the release workflow checks
  the wheel's contents. Gate 237/237 in the cloud.
- Amendment (2026-10-02, ship preparation): version 0.4.0 set in
  `pyproject.toml` and `gea/__init__.py`; CHANGELOG and HISTORY sections
  headed v0.4.0; `SHIP_MESSAGE.txt` written. Left on the list after this
  ship, in this order: the bench record for the aging band, and the worked
  site with every number recomputable.
- Amendment (2026-10-02, first ship attempt): `ship.ps1` went red on AJ1 on
  the development machine while every Linux run was green. Cause: `gea guide`
  run as a subprocess on Windows prints through a cp1252 pipe, and an arrow
  character added to the guide that day cannot be encoded there, so the
  command raised and exited non-zero. Fixed at the root: the front door sets
  its output streams to replace unencodable characters, the acceptance checks
  decode subprocess output as UTF-8, and the arrow is plain text. Verified
  under a cp1252 pipe before re-sending. Nothing else had been missed: the
  version, CHANGELOG section, SHIP_MESSAGE and ship-log chain all passed.
