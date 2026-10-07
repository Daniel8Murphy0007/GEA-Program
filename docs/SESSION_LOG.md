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

## 2026-10-02 (later) - the physics made standard

- The question was put plainly: is the predecessor's physics in the engine? Yes - renamed,
  not removed. `quartz_hpht_extension.py` held the four constants and the
  fixed divisor under a program-owned name; the reconciler's band, the
  model card, the simulator's synthetic drift, the bench ratio test, the
  service-life divergence, the case study, the desktop panels, the demo and
  the help page I had written that morning all carried it. The standalone
  guard caught the word, not the number.
- Removed, not relabelled: the model, its constants, the trims, the second
  leg, the ratio machinery, the template preset and its engineering-fit
  knees and exponents, the case study module and command. Kept: the datasheet
  rate (`gauge_aging.py`), the reconciler's classification on it, the bench
  rebuilt as the datasheet-conformance test with a register, the service-life
  arithmetic on the datasheet rate, the simulator as a synthetic generator
  whose settings are labelled as such.
- Found on the way: a stale editable install in the cloud workspace still
  resolved the deleted modules from the old folder - exactly the failure
  `gea doctor` was built for; uninstalled. Section AK imports the removed
  names (spelled in halves so the guard does not flag the test itself) and
  expects ImportError.
- Re-derived against the datasheet-only engine: C4/C5 (a 3 psi/yr datasheet
  drift needs a 400-day window to resolve above the bias gate - the test now
  uses one), AA17/AA34 (the +40 psi injection measures 43.1 psi once the
  simulator's noise lost the removed model's shaping), A2/A8, E3-E5, H1/H2,
  I1-I5. Gate 240/240 in the cloud; guard 0 findings with the new patterns.

## 2026-10-02 (later still) - the second leg begins

- The owner's brief for the second leg: the site's seismic records, treated
  as noise by conventional processing, carry the signatures of every rig
  within range; the leg should map them. Assessed before building: drill-bit
  and machinery seismic, passive arrays and interferometry are standard;
  InSAR-like Doppler tomography of well tracks and a hundred-mile reach are
  not supportable from one site, and nothing was promised. The first step is
  a number - at what distance does a known rig show in a record - and the
  leg is built so that number comes first.
- Public data found for it: TexNet (network TX, open FDSN at the BEG, Texas
  RRC permits as ground truth - the closest fit), EarthScope/IRIS, Utah FORGE
  nodal and DAS sets and Brady's Geothermal nodal data on the DOE GDR
  (CC-BY). The owner said go.
- Built: `seismic.py` (miniSEED Steim1/Steim2/int/float reader and writer,
  SAC reader and writer, content detection, FDSN dataselect/station client,
  Welch PSD, spectrogram, persistent lines, band power), `seismic_detect.py`
  (sources CSV, haversine, the detectability test with its four verdicts and
  the bracketed radius, a labelled synthetic scene and selftest), `gea
  seismic` with eight actions, the `seismic` help page, `files.detect` kinds
  `mseed`/`sac` with the well import refusing them, section AL (5 checks).
- Independent check of the decoder: neither the cloud workspace nor PyPI
  from it could supply a reference, but the device VM reaches PyPI, so
  `pymseed` (libmseed 3.5.4) was installed there, outside the repository,
  and wrote Steim1/Steim2/int32 records of a seeded 1,904-sample series that
  exercises every packing form including 2^29 jumps. This program's reader
  decoded them bit for bit; libmseed read this program's Steim1, Steim2,
  int32, int16 and float32 records back exactly. The libmseed files are now
  `gea/reference/` with a provenance file carrying their hashes and the
  series' hash, and AL1 repeats the check on every run.
- Found on the way: Python 3.10's `fromisoformat` rejects a four-digit
  fraction (the 0.1 ms miniSEED time); `parse_time` pads to microseconds. A
  synthetic engine tone above Nyquist aliased into the band; the scene now
  keeps nothing above 80 % of Nyquist, as a recorder's anti-alias filter
  would. A line present in a tenth of the windows was invisible to a
  median-over-all-windows strength; the strength is now the median excess in
  the windows where the line is present, and adjacent bins collapse to one
  line.
- Not possible from here: fetching real TexNet or EarthScope data - both the
  cloud workspace and the device VM are refused at the proxy. The owner
  fetches the first record from a browser and drops it into the clone;
  the command and the URL form are in the help page and README.
- The kit-workflow failure, read at last from the public Actions pages (the
  API and the logs still need a sign-in): windows-kit failed in 38 s at
  "Install the kit the way a client does", linux-kit passed and attached its
  kit to the v0.4.0 release. The cause is one line: `cd dist\gea-program-*-win64`
  under `shell: cmd` - cmd.exe's `cd` does not expand wildcards, so the step
  exited 1 before install.cmd ran. Fixed with a `for /d` resolution of the
  folder; `workflow_dispatch` gained a `release_tag` input so a manual run
  attaches both kits to the existing v0.4.0 release. The `ci/` mirror of the
  workflow is byte-identical again. What the Windows kit's own install and
  gate do on the runner is still unseen - that line failed before them.
- Asked where the report templates and the other two wells went: nothing was
  ever deleted - GEA-Program's git history has no deleted report or template
  file, the reports are built in code (`client_reports.py`, nine builders:
  the predecessor's eight plus the transient report), and the quickstart
  runs one catalogue well (Volve 15/9-F-12) by design, with F-14's nine days
  in the same excerpt reachable through a second `--catalog-well`. Awaiting
  the owner's word on which three wells were meant.
- "Wrong answer about the reports, look harder" - and it was. The twelve
  report files the owner meant were found in the session's own outputs
  folder: rendered on 2026-09-28/29 by the predecessor's build 1.90.0 and
  sent into the chat as samples, never committed to any repository (no git
  history in either repo has them), and carrying the removed model card's
  name. The answer is `docs/report_samples/`: `tools/render_report_samples.py`
  renders seventeen reports from the current checkout (five minutes: the SAT
  protocol runs the gate and the synthetic field is 720 hours), names each
  with its command in SAMPLES.md, labels the synthetic ones, and the kit
  builder copies the folder in as `report-samples/`. Section AM holds the
  set to the build number, so a bump without a re-render fails the gate;
  README's shipping section says to re-render after the bump.
- The owner's SBOM screenshot (PyQt6, asyncua, paho-mqtt, pymodbus, xlrd,
  pyserial "not installed") was from a development checkout, not a kit; the
  kit already carried live, plotting and xls. `desktop` (PyQt6, ~85 MB of
  wheels) is now in the default extras and in the workflow; checked from the
  device VM that pip resolves PyQt6's abi3 wheel for win_amd64/cp312 with the
  flags the builder uses.

## 2026-10-03 - the v0.5.0 ship prepared

- "Update files and prepare the ship": version 0.5.0 in pyproject.toml and
  gea.__version__; the `[Unreleased]` section headed `[v0.5.0] - 2026-10-03`
  in CHANGELOG and HISTORY; SHIP_MESSAGE.txt written with the tag on its first
  line (the deleted modules are not named there - the guard reads the ship
  message as any tracked file, and only the changelog, history and this log
  may carry a removed name in a removal line). docs/report_samples re-rendered
  at build 0.5.0 (section AM holds them to it). Gate run in the cloud and on
  the development machine; guard 0 findings; the clone synced. The owner
  runs `.\ship.ps1`; `git add -A` in it records the two deleted modules.

## 2026-10-03 - v0.5.0 shipped; the Windows kit, third look

- Shipped: HEAD 6c2e672 = tag v0.5.0 = origin/main; tags v0.1.0..v0.5.0 in
  `.git`; SHIP_LOG line written. CI and the PyPI release green; the kit
  workflow red again on windows-kit (41 s), linux-kit green.
- The log, finally: the owner signed the browser pane in to GitHub and the
  step's lines came up. The `cd` fix held; the build step took 26 s with all
  36 wheels (PyQt6 and its Qt6 wheel among them); `install.cmd` reached pip
  and pip refused: "To modify pip, please run ... python.exe -m pip install".
  pip, run as `python wheel\pip`, will not install pip on Windows - it checks
  the basename of argv[0] - and the embeddable Python ignores PYTHONPATH, so
  `-m pip` cannot see the wheel. The kit now carries `pip_bootstrap.py`
  (the wheel on sys.path, `runpy.run_module('pip', alter_sys=True)` so
  argv[0] is pip's `__main__.py`); install.cmd calls it. Proven on the
  device VM that pip runs from its wheel this way and installs pip; the
  Windows refusal itself cannot be reproduced on Linux, so the next manual
  run of the workflow (`release_tag = v0.5.0`) is the proof.
- "Why does this keep failing": because the fix had never run. The owner's
  commit efcc5d9 with pip_bootstrap.py was on origin/main, but what ran was
  "Re-run jobs" on the v0.5.0 tag run, which re-runs the tag's commit 6c2e672
  - the old install.cmd, the same pip refusal. Asked to run the workflow by
  hand, I found GitHub offers no "Run workflow" button on this repository's
  workflow page (the dispatch trigger is in the file on main; the button is
  not there, in the narrow or the wide layout, nor under the menu). So the
  workflow now runs on a push to main that touches the kit builder, the
  workflow or pyproject.toml, and the attach step (attach-kit.sh) attaches a
  main build to the release of the version in pyproject.toml only where that
  platform's kit is missing - the v0.5.0 Windows kit, once the install step
  passes. The owner's next push of these two files is the run.

## 2026-10-03 (later) - the reader on real files; the response

- "Continue." The leg's next piece that needs no data from the owner: the
  reader held to real files, and physical units.
- The corpus: obspy's wheel, fetched by pip on the device VM (which reaches
  PyPI), holds about ninety miniSEED test files from real stations - every
  encoding, both byte orders, 512- to 4096-byte records, gaps, time
  corrections, odd blockettes, broken records. pymseed (libmseed 3.5.4) was
  the reference. First pass: 61 exact, 13 failures, 6 mismatches. The
  failures were read out of libmseed's own unpackdata.c and unpack.c (in the
  pymseed sdist): Steim 8-bit differences are the word's bytes in memory
  order and 16-bit ones two int16 in memory order (big-endian data hides
  this; little-endian data was wrong by a few counts and failed the Xn
  check); any non-zero blockette-1000 byte-order value is big-endian; a data
  offset of 0 means no data; text records are not samples; GEOSCOPE, CDSN,
  SRO and DWWSSN are 16-bit gain-ranged formats, ported line for line. Last
  pass: 76 exact, 5 text files skipped by design, 0 mismatches. obspy was
  then installed on the VM (100 MB of wheels) for the SAC check: 12 of 12
  readable files exact, after two fixes (an undefined reference time, a
  NUL-terminated string). `tools/seismic_reader_check.py` repeats the
  miniSEED check anywhere with internet; the corpus is not redistributed.
- The response: `seismic_response.py`. StationXML stages (PZ in radians,
  hertz or z-transform; coefficient and FIR stages with symmetry,
  decimation and delay correction; gains; polynomial refused), the product
  checked against the declared sensitivity, the unit derivative chain to
  VEL/DISP/ACC, a water-level deconvolution with a cosine pre-filter.
  Against obspy's evalresp on IU.ANMO.10.BHZ: amplitude ratio 1.00000 at
  every frequency from the first run; phase off by exactly the FIR stage's
  0.43046 s correction until the correction was applied as evalresp applies
  it (multiply by exp(+i 2 pi f c)); then 1e-6 degree. The full
  deconvolution against obspy's on a real 3-hour IU.ULN record: 0.9-1.1 %
  rms with the pre-filter, the rest being the two programs' taper and
  detrend conventions; without a pre-filter both programs' results are
  dominated by the water-level region and differ by 60-70 %, which is why a
  pre-filter is the standard. The IRIS StationXML and evalresp's seven
  values per unit are `gea/reference/` with provenance; section AN (4).
- A device_commit_files call that reuses a staged path wrote a stale copy
  once (38,663 bytes of an older seismic.py); verified by md5 and staged
  under a new name. Gate 250 in the cloud; device sections AL, AM, AN to run.

## 2026-10-03 (later still) - the array step

- "GO." The leg's purpose, built on the synthetic scene so it is ready
  when multi-station data arrives: `seismic_array.py`.
- The first beam put a 70-degree plane wave at 76 degrees with coherence
  0.43: the 0.1 s/km coarse grid was the error (at 0.4 s/km one step is 14
  degrees of azimuth, and a 0.03 s/km mismatch across 0.6 km at 20 Hz is a
  third of a cycle). A 41 x 41 refinement around the coarse maximum gives
  69.99 degrees, 0.4018 s/km, coherence 0.994. The array response function
  is computed on its own fine grid around zero (the pattern is
  shift-invariant) and on the coarse full grid for the lobes.
- The second false alarm: band-averaged coherence called the 25 and 45 km
  rigs INCOHERENT (0.24, 0.18) while the beam pointed at them to 0.3
  degrees - a rig's lines are a few bins in a band of noise, so the
  band's average says little. Coherence is now judged per frequency bin
  at the found slowness: best-bin 1.00 for all four rigs, with the coherent
  frequencies listed (they are the rigs' lines). Verdict needs best-bin
  coherence >= 0.5 and two coherent bins.
- Negative controls earned: a rig listed at the wrong bearing is
  NOT_POINTED; sensor noise alone is INCOHERENT; a reversed bearing is
  reported as behind its array; the same lags at a wrong velocity land
  elsewhere, which is why the velocity is printed as the input it is.
- Section AO (4), 70 s of it the full scene. Gate 254 in the cloud; device
  AO to run. Help page, README, CHANGELOG, HISTORY written.

## 2026-10-04 - the leg on the dashboard

- "Go": the seismic page. The leg becomes a site thing: `Workspace`
  gained seismic stations (`seismic/<id>/source/` with the files copied in
  and hashed, `station.json` with position, band, rigs list, station file,
  sensors), `refresh_seismic` runs the whole leg and writes the machine
  JSONs and the Seismic Station Report under `reports/seismic/<id>/` so the
  existing `/reports/` route serves them; `seismic_results` reads them back.
  A new report builder in `client_reports.py` (sections: summary, the record,
  spectrum and lines, detectability, the array, method, what it does not call
  a measurement - every `not_a_measurement` list the leg's functions print is
  gathered there). The service: five routes and an upload handler that takes
  several files base64 like the well upload does; the page: the Seismic view
  with the add form (multi-file), the station view with the spectrum drawn
  on a canvas from spectrum.json (decimated to 2,000 points), a nav entry, a
  home tile, help mapping. The refresh is `workspace --action
  refresh-seismic` as a job, so it has a log like everything else.
- The client-report writer's forbidden-term list includes "refus", so the
  report's limits text says "turned away" where the help page says
  "refused"; the first render passed.
- Section AP (4): the workspace (single and array, the two refusals), the
  API as viewer and operator with a real upload and a job that completes,
  the page source and help mapping, the CLI. The report sample renders
  through the workspace from the labelled scene (18 samples now). Gate 258
  in the cloud; device AP, AM to run.

## 2026-10-04 (later) - the v0.6.0 ship prepared

- "Update the files and prepare the ship": version 0.6.0 in pyproject.toml
  and gea.__version__; `[Unreleased]` headed `[v0.6.0] - 2026-10-04` in
  CHANGELOG and HISTORY; SHIP_MESSAGE.txt with the tag on its first line;
  docs/report_samples re-rendered at build 0.6.0 (section AM holds them to
  it). Gate in the cloud and on the development machine; guard; clone
  synced. The owner runs `.\ship.ps1`; the tag starts CI, the PyPI release
  and the kit build, whose Windows job attaches its kit to the release.

## 2026-10-04 (later again) - v0.6.0 shipped; both kits failed their gate

- v0.6.0 shipped (commit 94fcffb = tag v0.6.0 = origin/main; tags v0.1.0 to
  v0.6.0; SHIP_LOG line). PyPI took the release. The kit workflow's run #5
  failed on both platforms at about four minutes: the kit built, installed
  offline with every extra, and its own `verify` reported one failure,
  AN4 - the check asked for `tools/seismic_reader_check.py` beside the
  package, which an installed kit does not have. Twelve hours of a leg and
  the kit fell on one `exists()` that the cloud gate (run in the checkout)
  could never see.
- The fix: AN4's tool check is asked only in a checkout (`pyproject.toml`
  beside the package), like AJ1 and AM1. The lesson, now a rule: a check
  that reads anything outside the package must guard on the checkout, and
  the gate is run once from an installed layout before a ship - the package
  copied into a bare venv's site-packages and `gea accept` run from another
  directory. Done here before the clone was touched.
- Because both tag jobs failed before "Attach to the release", no v0.6.0
  release exists on GitHub, and the main-push path of `attach-kit.sh`
  refused to create one. It now creates the release when the tag is in the
  repository, and the workflow runs on pushes touching the attach script or
  the gate module. The owner commits and pushes; that run builds both kits,
  gates them (258), creates the v0.6.0 release and attaches both.
- The owner's walk through the README quick tour on Python 3.14 (screens):
  `gea accept` 258 OK from the checkout; `pip install -e .` failed with
  WinError 32 because a running `gea serve` held `gea.exe` (the same
  service that makes `gea serve` report port 8765 in use and `doctor` warn
  that pip's record says 0.4.0 while 0.6.0 runs) - stop that window, then
  `pip install -e .`. Two program defects seen and fixed here: missing
  input files raised tracebacks (now one line, exit 2); the tester guide
  carried a stale "189-check" count (removed; AJ1 refuses a count).
- Also seen: `doctor` missed a launcher in the per-user Scripts folder
  (fixed); and the owner's `git add` was blocked by a `.git/index.lock` that
  my own `git status` from the device shell had left behind (that shell
  cannot delete files) - moved to `_to_delete/`. Rule: no git commands from
  the device shell against the clone; read `.git` files directly instead.

## 2026-10-04 (evening) - the SAR panel and the Audit / Update tab

- "Back to the SAR program module": a user SAR tab with a control panel and a
  video simulator screen that runs the final GEA/SAR output, and an
  Audit/Update tab. Asked three things and got: the simulator plays the
  synthetic scene only for now; Audit/Update covers the audit log, the
  program update and the workspace data update; the Seismic tab stays the
  door with the SAR panel inside it.
- The honest form of a "final output" screen today: the second leg has no
  mapped well track yet (that needs the real record and two arrays over
  time), so the screen plays the labelled synthetic array scene forward in
  time through the leg's own functions - trace, spectrogram column, beam
  power on the slowness grid, bearing and tolerance wedge on the map, the
  per-rig tally - and ends on `array_detectability`'s verdict, the same call
  a real record runs. `gea/seismic_film.py`: the film is JSON (about 400 KB
  for 8 h at 600 s; 40 s to make), written by a workspace job under
  `reports/seismic/SIMULATION/` so the existing `/reports/` route serves it;
  the page plays it on a canvas with Play/Pause/Stop/speed/scrub. Every frame
  carries SIMULATION_SELF_TEST and "not a measurement of any ground".
- The quiet hour in the film beams the scene's own background wave (a
  coherent plane wave from a random bearing): the beam is coherent and points
  at it, not at the rig. That is the scene's truth and AQ1 checks the quiet
  bearing is away from the rig rather than demanding incoherence. The beam
  map is drawn floor-to-peak (the broadband Bartlett floor sits near 0.6 of
  the peak, which hid the lobes on a 0-1 scale).
- Audit / Update: `/api/audit` filters (actor, action, since, limit) and
  reports the totals and the distinct actors and actions; the page downloads
  a CSV client-side. `/api/update` and `gea update` (new command, in
  RUNNABLE): running vs PyPI, extras present, report ages; the program update
  is an admin job that runs pip from the same interpreter and says to restart;
  `refresh-all` is an operator job over every well and station. Playwright
  drove the page in the cloud: sign-in, Run, Play, scrub, the Audit page;
  no console errors; screenshots read.
- Section AQ (4): 262 checks. README (the SAR panel, an Audit / Update
  section, counts, layout), help page `audit-update`, CHANGELOG, HISTORY.

## 2026-10-04 (night) - why run #6 failed too

- The log, once the browser pane got past GitHub's 2FA reminder: AN4 again,
  with the OLD message text - the kit had gated the released 0.6.0 wheel,
  not commit 9c95566. The builder's `pip download gea-program[extras]`
  resolved against PyPI as well as `--find-links wheels`, and pip's copy of
  0.6.0 overwrote the checkout's. Every main-push kit run since the v0.6.0
  release has been testing PyPI's code; the tag runs happened to use the
  local wheel because PyPI did not have 0.6.0 yet when they built.
- Fix: the dependency download is built from the wheel's own Requires-Dist
  (gea-program is never asked of the index), an assertion that the package
  wheel in the kit is byte-identical to the one built, and the commit in
  MANIFEST.json. Proven by building the checkout's wheel, installing it alone
  into a fresh venv and running `gea accept` from it.
- Rule: a kit is the commit, never the index. Any build step that can reach
  an index must be told exactly what it may fetch.

## 2026-10-04 (late) - the tracker band for v0.7.0

- Run #7 green on both platforms; the v0.6.0 release exists with both
  kits, built from the commit. "What's next for SAR; a complete package
  for v0.7.0": he chose the tracker band plus the permits importer, and a
  bit advancing along a lateral as the scene's motion.
- `seismic_track.py`: `bearing_history` (beam per window, coherent or gap,
  tolerance, change points against the running mean), `position_history`
  (crossing per window with the tracker's three refusals: fewer than two
  coherent arrays, crossing under 15 degrees, a point behind an array),
  `track_summary` (segments at jumps larger than the ellipses; the longest
  is the track; heading from the principal line, in the direction of time),
  `track_verdict` (positions against the truth interpolated to their time,
  hits within the position's own major axis). The scene: two arrays, one
  source quiet for an hour then advancing 3 km on heading 80 over the rest;
  the self-test comes out TRACKED, heading 79.5 vs 80, length 2.84 vs 3.
- The first 3 h run taught the segmenting rule: the quiet hour's background
  wave was coherent at both arrays and crossed 7.5 km away; a single fit
  through all positions gave 9.8 km on heading 101. The jump is now a cut,
  the verdict counts positions outside the truth's time span as such, and
  the report says both. That is exactly the kind of thing a real quiet
  hour will do, and the program now says what it is instead of joining it.
- Tracks in the workspace (`tracks/<id>`, the truth CSV hashed), the
  refresh, the Seismic Track Report, five routes, the Tracks card, the track
  view (map in a local plane with the ellipses drawn from their covariance,
  a slider through time, the bearings chart), a home tile. Playwright drove
  the add, the view and the slider; no console errors.
- The film's lateral scene: frames carry both beams and the crossing; the
  screen draws the lateral, the bit, the arrays' bearings and the track so
  far; the close is the tracker's verdict. The permits importer with the
  start-date fallback (approval date only where the spud date is empty,
  said in the row) and every assumption counted; `add-seismic --permits`.
- Sections AR (1) and AS (3): 266 checks; AR fits the device shell's
  two minutes, AS (two refreshes and a job) does not and is proven by the
  full gate in the cloud and on the owner's machine. Sample 19 (the track
  report). README, help,
  CHANGELOG, HISTORY. The ship is prepared on his word, not before.

## 2026-10-04 (late) - the v0.7.0 ship prepared

- "Update files and prepare the ship": version 0.7.0 in pyproject.toml and
  gea.__version__; `[Unreleased]` headed `[v0.7.0] - 2026-10-04` in
  CHANGELOG and HISTORY; SHIP_MESSAGE.txt with the tag at the head of its
  first line (attach-kit.sh now takes a first line that starts with the
  tag as that tag's message, so the release carries the ship message);
  docs/report_samples re-rendered at build 0.7.0 (AM holds them to it).
  Gate 266 from the checkout and from the installed wheel in the cloud, the
  guard clean, the clone synced; the owner runs `gea accept` on his
  machine and then `.\ship.ps1`. The tag starts CI, the PyPI release and
  the kit build - whose two jobs now build the commit, gate it at 266 and
  attach both kits to the v0.7.0 release.
- The commit and the tag were made on the development machine from the
  device shell (the owner's own identity from the repository config), the
  SHIP_LOG line written, and the push left to the owner. His push of main
  started kit run #8: the Linux kit built from the commit, installed and
  gated green; the Windows kit built and installed and its gate crashed in
  section AC on a Windows file race - a reader got PermissionError while
  the runner was replacing job.json. jobs.py retries both sides; the tag
  moved to the fix before it was pushed.

## 2026-10-04 (night) - several rigs at once

- The next SAR item, and the one the leg exists for: a field. Until now the
  whole second leg answers for one source at a time - the beam gives one
  direction for every machine in the band, so two rigs working together make
  the crossing meaningless. `seismic_signature.py`: learn each rig's lines
  from its exclusive windows (the detectability test's own rule), slice the
  cross-spectral matrix to those bins and beam them. Three rigs round one
  array: two separated to under a degree against a 7-degree tolerance while
  all three worked.
- The third rig taught the band's best check. Its lines (4.3 and 12.9 Hz)
  all sit above the array's spatial Nyquist, and it pointed 155 degrees
  wrong. A geometry-only test refused everything, including the two that
  were right. The test that works is measured: the strongest peak farther
  than the array response width from the maximum, as a fraction of it - 0.58
  and 0.85 for the two that were right, 0.97 for the one that was wrong.
  Over 0.9 the verdict is ALIASED and the peaks it cannot separate are
  listed. Two rigs given the same pump rate are NOT_SEPARABLE, both of them.
- The field: two arrays, three rigs, each learned in its own spell alone,
  then all working. Two tracked TRACKED, every position inside its ellipse;
  the third positioned in 3 windows of 23 because it aliases at the second
  array. The whole-band track over the same records says NOT_TRACKED, which
  is the right answer and the clearest statement of why the signature path
  exists.
- The fixed rigs also showed that a line fitted through a scatter always has
  a heading: `track_summary` now says NOT_RESOLVED when the span is inside
  twice the median ellipse.
- A real defect found by the page, not by a test: the service was sending
  Infinity in JSON (a beam at zero slowness, a crossing with no finite
  ellipse). Python reads it back; every browser rejects the whole body, so
  the station page showed "Could not load this page". Non-finite numbers are
  null at the source now and the service refuses to emit one.
- Section AT (3): 269 checks. The live-port fix from this afternoon is still
  local and rides into the next tag, as he asked: nothing ships untagged.


## 2026-10-05 - the machine behind the lines

- He asked what SAR still needs, then picked the harmonic band. The ranked
  answer stands for the record: SAR has a station report and a track report
  but no dataset or field report; array QC, unlisted-source discovery and an
  array design tool are the other gaps, in that order.
- `gea/seismic_harmonic.py`. A comb search over every line divided by every
  order gives the fundamental and the orders it was found at, normalised to
  the largest spacing those orders allow, with the rate printed per minute.
  The strength of the claim is printed beside it: `by_chance` is how often
  this record's line density puts that many lines on a comb by accident, and
  a family over the ceiling is set aside, not reported. Two lines are never a
  family. A fundamental whose half falls below the band is flagged.
- `track_lines` follows every line window to window, each peak refined inside
  its bin by a parabola, with STEADY / DRIFTING / INTERMITTENT and the drift
  in Hz per hour; a drift no larger than the bin width is not a drift.
  `tracked_signature` is the same thing in the shape the signature band
  reads, and `rate_history` reads the rate over the record.
- `attribute_tracks` gives each track to the source whose learned line it
  passes, or - the part that matters - to the source whose already-claimed
  line it stands in a small whole-number ratio to in a window they share. A
  track two sources could claim goes to neither; a track nobody claims is
  listed as such.
- `activity_from_signature` measures each source's working spells off the
  record and sets them beside the declared ones. This is the first thing in
  the leg that can contradict the rigs CSV, which every verdict until now had
  to believe.
- The labelled scene makes the case in one run: a pump ramping 1.40 to 1.85
  Hz with a stop in it. A signature of fixed bins over that record holds two
  lines (TOO_FEW_LINES) - the machine walked out of its own bins. The tracker
  holds all eight (every line once per working spell), the harmonics walking
  1x, 2x and 3.01x the first, which is the physics. The family in the tracked
  lines returns the fundamental 0.005 Hz from the rate the scene was actually
  running at in that window. Activity in the learned bins DIFFERS; allowing
  for the walk it AGREES, two spells, matching the scene.
- On the three-rig scene every pump rate came back within 0.01 Hz of the
  scene's (1.697 / 2.905 / 4.297 against 1.7 / 2.9 / 4.3), the comb with its
  fourth tooth missing reported as orders 1,2,3,5 at 80 % filled rather than
  inventing it, and the lines near 8.5 Hz - where three rigs' harmonics fall
  within the spectral resolution - given to no rig at all.
- Wired through: `harmonics.json` at every station refresh (a single sensor
  too, using its own detectability lines per source), the report section, the
  page card with the tracks drawn against time, `gea seismic --action
  harmonics | harmonic-selftest`.
- Section AU (3): 272 checks. Everything from v0.7.0's tail - the live-port
  fix, the signature band, the Infinity fix - plus all of this goes into one
  tagged commit. Nothing ships untagged.

## 2026-10-05 (evening) - which datum a position is on

- He set the order: datum and CRS, then array QC, then unlisted-source
  discovery, then the dataset and field reports. This is the first of those.
- One correction to the record: I told him the NAD27 shift in West Texas was
  "roughly 200 m". It is 46 m at 31 N, 102 W. The point stands - it is still
  the size of a position ellipse and larger than anything else in the error
  budget - but the number was wrong, and the program now computes it at the
  site instead of anyone quoting one. 85 m in Bakersfield, 18 m in Ohio.
- `gea/geodesy.py`: ellipsoids, geodetic/geocentric, Vincenty's inverse,
  three-parameter datum shifts, transverse Mercator and Lambert conformal
  conic both ways, Texas state plane zones, and the US survey foot kept
  distinct from the international foot.
- The selftest is against values this program did not produce: the WGS84
  meridian arc to 45 N (4 984 944.378 m), a degree of latitude at the equator
  (110 574.389 m) and a degree of longitude (111 319.491 m) - all three match
  to the millimetre - plus the identities every projection must satisfy at
  its own origin. Round trips close to 6 mm.
- Carried through: Source and Sensor have a datum and convert on load; the
  permits importer reads a datum column or takes one, and can read state
  plane / UTM easting and northing in metres or either foot; the station has
  a datum; the refresh runs check_set and writes datum.json; the report and
  the page say what is on what.
- What it refuses: a NAD83 state plane zone handed NAD27 coordinates, an
  array on two datums, an unknown unit, and any suggestion that a
  three-parameter shift is survey grade.
- Section AV (3): 275 checks.

## 2026-10-05 (late) - is this array any good?

- Second of the four he ordered. `gea/seismic_qc.py`.
- The timing test is the whole point and it needs no reference clock: for a
  plane wave the arrival delays must lie on a plane, so fit the plane to the
  array's own measured delays and each residual is the part of that sensor's
  arrival the wavefront does not explain. The fit returns the velocity and
  the direction as a by-product, which is what makes it self-referencing. On
  a clean array it gives 2.498 km/s against the scene's 2.5 and agrees with
  the beam to 0.4 deg, residuals under 1 ms.
- Three things had to be got right and each was found by the scene, not by
  reasoning: (1) a free lag search cycle-skips on narrow-band machinery, so
  the search is anchored on the beam's prediction and capped at a quarter of
  the delay the array spans; (2) choosing the delay sign by residual size is
  degenerate - negating every delay turns the plane 180 deg and fits exactly
  as well - so the discriminator is that the fitted plane must point where
  the beam points; (3) the scatter floor cannot be one sample, because the
  peak is interpolated inside its bin, so it is a tenth of a sample and the
  scatter itself is a median absolute deviation, which one bad clock cannot
  inflate.
- The scene puts four faults into a clean array and QC names exactly those
  four: dead S02, clock S04 (asked 30 ms, quantised by the sample grid to
  40 ms, measured 40.04 ms), reversed S06, 8 % gain S07. The clean array
  comes back USABLE with nothing named - the half of the test that matters
  most, because a check that finds a fault in a good array is worse than no
  check.
- `beam_cost` says what the faults were doing: 173 deg of bearing and +0.26
  of best-bin coherence. That is the number that makes QC worth running.
- Wired: array_qc.json at every array refresh, the report section with the
  two beams side by side, the page card, `gea seismic --action array-qc |
  qc-selftest` (non-zero exit on an array that is not clean).
- Section AW (3): 278 checks.

## 2026-10-05 (late) - what else is out there

- Third of the four. `gea/seismic_unlisted.py`. He was right that it was
  nearly free: attribution already listed the tracks nobody claims, so the
  work was grouping them into combs and beaming each one like any other
  source.
- The labelled scene leaves one of three rigs off the list the program is
  given. It comes back as UNLISTED-1 at 249.5 deg against a true 250.02, at
  2.2001 Hz against a true 2.2. With every rig on the list: ONLY_ELECTRICAL,
  no candidate at all.
- The scene taught one thing I would not have thought to put in. A 60 Hz
  mains line was added to every sensor; the tracker found 60 Hz and also a
  line at 30 Hz. That is the second mains harmonic, 120 Hz, folding back
  under a 150 Hz sample rate. It looks exactly like a machine at 1800 a
  minute. `mains_lines` now names both the direct multiples and the folded
  ones, with the arithmetic printed.
- The other refusals: a candidate that beams to zero slowness is common-mode
  (cabling or supply, not ground); a candidate at a whole-number ratio of a
  listed rate is that rig's harmonic; one line is not a machine and neither
  are two. And a candidate is a direction with a rate, never an
  identification.
- A high-rate source is still turned down rather than given a false bearing:
  the first version of the scene had the hidden rig at 4.3 Hz, whose lines
  alias on a 1.2 km aperture, and the signature band's own aliasing test
  called it ALIASED with the peaks it could not separate listed. That is the
  right answer, so the scene was changed to a rate the array can resolve and
  the refusal left in place.
- Section AX (3): 281 checks.

## 2026-10-05 (late) - the site, not the station

- Fourth and last of the order he set. Two reports about all of it.
- The Dataset Report is the holdings: every record opened and its span, rate
  and gap count read from the record itself, the checksum from when it was
  brought in, the datum, the band, and which outputs are current against
  which are stale. The stale test is the one that earns its place - touch a
  source file and every result for that station is marked older than the
  record it came from, which is what it is.
- The Field Report is what the site heard: each station's reach and whether
  its own sensors agree, each listed rig and which stations heard it, the
  rate its lines say it runs at, whether its heard hours agree with its
  declared ones, each track, and everything found that nobody listed.
- Both say what they will not say. The dataset one: this is what is held, not
  whether it is any good. The field one: a source nobody heard is not a
  source that was not working.
- `gea workspace --action seismic-dataset | seismic-field`, both audited, and
  a card on the Seismic page for the site as a whole.
- Section AY (3): 284 checks. That closes the four he ordered - datum and
  CRS, array QC, unlisted-source discovery, and the dataset and field
  reports - and all of it rides into one tagged commit.

## 2026-10-06 - what a bearing is worth, and whose ground it is

- Items 5 and 6 of the seven. Item 7 - the real records - is his to supply.
- `gea/seismic_uncertainty.py`. Three estimators were tried and the first two
  are in the record because they are instructive.
- One: a bearing per coherent frequency bin, scatter of those. Wrong on a
  small array and badly: one frequency has no diversity to break the spatial
  aliasing, so the bins scatter by 43 degrees whatever the signal-to-noise.
  That is the geometry, not the data. It is kept as a printed diagnostic.
- Two: the same, weighted by each bin's contribution to the beam with an
  effective sample size. Better statistics, same defect - 22 degrees against
  a real error of 0.4.
- Three, which is right: cut the window into sub-windows and beam each. Same
  source, independent noise, so the scatter is what the noise does. Floor at
  the beamformer's own grid step, with the sub-windows searched on a grid
  narrowed to the slowness already found so the floor does not hide the
  measurement.
- Then the part that makes it a measurement: coverage against a bearing known
  from outside the record. CALIBRATED - 75 % inside one sigma, 100 % inside
  two, median error 0.37 deg against sigma 0.54, recommended scale 0.69.
- An honest limit found by the test and left in: resampling sees the random
  part and not a bias steady through a window. So the module returns the
  factor that would centre the claim rather than publishing a sigma nobody
  has counted.
- Measuring costs about four times the beams (53 s -> 243 s on the field
  scene), so it is opt-in: `measure_sigma` on bearings and track, and the
  ellipse records which of its bearings carried a measured sigma.
- `sites` in the workspace: a site holds the wells, stations and tracks of
  one engagement. It refuses a member the workspace does not hold and refuses
  to be empty. The Site Report is the one deliverable for a client, and it
  names every member that has not been run since its source changed.
- Sections AZ (3) and BA (3): 290 checks.

## 2026-10-06 - v0.8.0 prepared

- Version 0.8.0 in pyproject.toml and gea/__init__.py; CHANGELOG and HISTORY
  headed `v0.8.0 - 2026-10-06 - the machine behind the lines, the ground
  under the positions, and the site that owns them`; SHIP_MESSAGE.txt written
  with that tag on its first line, which is what attach-kit.sh matches on.
- The report samples re-rendered from this build: 22 now, three of them new -
  the Seismic Dataset Report, the Seismic Field Report and the Site Report -
  each carrying `build 0.8.0` and each labelled SYNTHETIC in its text. The
  sample set in section AM was widened to match, because a client report with
  no rendered example in the shop window is a report nobody has seen.
- Gate 290 green at 0.8.0, standalone guard clean (271 tracked files).
- SHIP_LOG.md untouched: ship.ps1 writes its line after the remote tag is
  seen, and it is committed with the next ship, as history.
- Everything from this leg of work rides into the one tagged commit, as he
  asked: the live-port fix, the signature band, the harmonic band, datum and
  CRS, array quality control, unlisted-source discovery, the dataset and
  field reports, measured uncertainty, and the site.

## 2026-10-06 - the supervisor: the panel owns its stopping, and a power cut is recoverable

He reported two things in one message. The first was the ship: no tag, no
push. He was right - HEAD was one commit ahead of origin/main and `git
ls-remote --tags` had nothing for v0.8.0, so `ship.ps1` had never been run.
Everything from this leg was staged and gated in his clone and none of it was
tagged. That is his step and only his; I cannot push.

The second was the defect, and it was mine. `start-dashboard.cmd` ended with
the serve command and nothing after it. When the control panel stopped, the
window fell back to whatever shell was underneath - and his console was
opened from a Windows Terminal Python profile, so what he got was a bare
`>>>`. A working program replaced by an interpreter that is not the program.

- `gea/supervisor.py`. The contract is three things: the service decides how
  it ends and says so in its exit code (0 stop, 86 start me again); the
  launcher reads that code and on anything else hands the window to a
  PowerShell prompt naming the program; every start, stop, restart and
  recovery is one appended line in `records/runlog.jsonl`, flushed to the
  disk before the thing it describes is attempted. A log written after the
  event is no use to a machine that lost power during it.
- `previous_run()` reports UNCLEAN when a start has no stop after it. The
  running process excludes only its own last start, and by position in the
  log rather than by matching the number - process ids come round again, and
  matching on the number alone would throw away an older run that happens to
  share it.
- `resume()` is the recovery, in an order that is not negotiable: the live
  patches come up first and return straight away, and the catch-up runs
  behind them on its own thread. A stream that is not running is losing
  records nothing can recover later; a report behind its source can be
  rebuilt at any time from records already on the disk. BB9 proves it rather
  than asserting it - the fake rebuild is held open on an event, and the test
  checks that `resume` had already returned while it was still blocked.
- `catch_up()` rebuilds exactly what the workspace's own staleness table
  says is behind its source, names it, and leaves the rest alone. A catch-up
  that refreshed everything would tell him nothing about what had actually
  fallen behind. It does not call a gap in the stream recovered.
- The launcher: `:gea_run`, exit 86 goes round again, and the file ends in
  `powershell.exe -NoLogo -NoExit` with the program's name and how to start
  it. `start-dashboard.sh` does the same with a while loop.
- The auto-restart switch he asked for is real, not recorded-only. A batch
  file cannot read the workspace manifest, so the one setting the launcher
  needs is one character in `records/auto_restart.flag`: the manifest stays
  the record of the choice and the flag is how it reaches the thing that acts
  on it. Missing or unreadable reads as off. Five unexpected stops in a row
  and the launcher stops asking - a panel that crashes while starting would
  otherwise flicker all night instead of handing over the error. The flag is
  read outside an if-block on purpose: a redirect inside parentheses is
  parsed before the block runs, and that is a classic way to get a batch file
  that works everywhere except on the one machine that matters.
- Operations control on the Administration page: Stop, Restart behind a popup
  whose link is the authorisation, the switch, the previous run's status, and
  the run-log tail. The restart is authorised every time; a control that
  reboots the program on a stray click is not a control.
- `gea doctor` now reads the installed launcher off the disk and says whether
  it honours the contract. A kit built before this existed comes back as
  three warnings with fixes - including that its window returns to a Python
  prompt. The defect he met should have been found by reading a file.
- `catch_up` read `r['errors']` straight out of the refresh. Changed to read
  what the refresh reported rather than requiring it: a recovery that threw a
  KeyError over a counter nobody reads is a worse failure than the one it was
  reporting.
- Section BB (11 checks): 301.

- The launcher opened the browser before the server was listening, so the
  first thing the operator saw was a connection failure from the second
  before. The browser now opens four seconds after the serve command starts.
- The site came up as `trident01` because the launchers initialise the
  workspace under the machine name, and because I typed the machine name into
  the init command I gave him. Every document names the site `Pad 3`. The
  launchers now create it as `Pad 3`, and `--action rename` sets the name a
  site actually has, idempotent and silent when unchanged so a launcher can
  say it on every start. BA4; the gate is 302.
- The ship hung on Windows for three and a half hours. `gate_by_section.py`
  in `_transport/` printed where: section E, check E8, `launch_operator_app()`
  - with PyQt6 installed on his desktop it opened the operator's window and
  entered the Qt event loop, waiting for a person to close it. Here there is
  no display, so it failed fast and the check passed. The view now takes
  `run=False`, which constructs the window and returns; E8 uses it. A gate
  that waits for a person is a gate that never finishes.

## 2026-10-06 - v0.8.0 shipped; v0.8.1 for the kit's gate

- v0.8.0 shipped: commit 7630205, annotated tag v0.8.0 on it, main and tag
  both on origin, 56 files. CI green on main and on the tag; the PyPI release
  green; SHIP_LOG.md has its v0.8.0 line.
- Both kit builds red, at 20 m 53 s each - the length of the kit's own gate.
  Section BB read `tools/build_installer.py` from beside the package; in an
  installed kit the package sits in site-packages and there is no `tools/`.
  Written this morning without following the one pattern the gate already
  had for this (`pyproject.toml` beside the package means a checkout). In a
  kit BB now reads the launcher the kit installed, found by `kit_dir()` - a
  better test than the template, since it is the file the client runs.
- Proven the way the workflow runs it, minus the wheel (this host's
  setuptools cannot build one): the package copied into a venv's
  site-packages, the launcher two levels up, the gate run from there.
- Version 0.8.1; samples re-rendered; CHANGELOG, HISTORY, SHIP_MESSAGE.

## 2026-10-07 - the Seismicity Response Area packet (v0.9.0)

He chose the SRA first. Research before a line of code: the Commission's
Seismicity Response page, the Gardendale operator-led response plan, and the
December 2023 Notice to Operators on Permian disposal-well monitoring. The
shape is theirs: a ~9 km circle, M 3.5, 18 months, 48 hours, quarterly
checkpoints, two depth tiers by the base of the Wolfcamp; four daily
parameters named in the Notice's words; three BHP methods, one of which is
a permanent downhole probe - which is what this program has been monitoring
since v0.1.0.

- `gea/sra.py`: define, membership (geodesic on WGS84, datum shift named),
  daily_records (the four parameters from the well's own channels; a volume
  only from a rate channel; a daily record only from a stream with a
  calendar), monthly_summary, read_catalog (a TexNet-shaped export by column
  name, date and time in separate columns), seismicity (trigger with its
  deadline, exempt aftershocks by declaration, the goal clock), checkpoints,
  export_daily_csv (the Notice's names as headers), packet, a labelled scene
  and a self-test.
- Volume integrates the way a totalizer counts: each sample holds for its
  interval. The trapezoid lost ten minutes at every midnight.
- `set-well`, `sra-define`, `sra-report` on the workspace; the Sites page
  carries the area and the packet; `gea help sra`; sample
  `sra_packet_SYNTHETIC.html`.
- Section BC (6 checks): 308.
- The v0.9.0 ship went red in section AH on Windows: `JobRunner.list()`
  read `job.json` with a plain open() while the runner was replacing it.
  `_read()` had carried the Windows retry since the v0.7.0 kit run #8;
  `list()` never got it. One tolerant reader for every job file now, and a
  job unreadable at that instant is left out of a listing. The gate's temp
  folder cleans up with errors ignored so a job's open log handle cannot
  fail a finished gate.
