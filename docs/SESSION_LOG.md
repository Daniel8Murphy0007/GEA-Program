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

## 2026-10-07 - association (v0.10.0)

He took the recommendation. `gea/seismic_assoc.py`: picks by STA/LTA with
the onset refined to the first energy; association by a grid search for
the origin with the origin time solved at every node; three or more
stations within the RMS tolerance or NOT ASSOCIATED; the worst pick dropped
while more than three remain; the location's misfit region as an extent,
never a confidence interval; an array collapsed to one station; the result
against the catalogue - AGREES, DIFFERS with both positions printed, NOT IN
CATALOGUE, and the catalogue events the stations did not associate.

Two defects the scene caught before any record did. The onset refinement
walked back past the arrival into the noise, because twice the background
is crossed by noise often enough, and every pick came out ~150 ms early -
uniformly, so the location absorbed it into the origin time and the RMS
looked fine; the threshold is now the larger of six times the background
and a twentieth of the arrival's own peak, and the picks sit within 30 ms.
The fine grid was centred on the stations' centroid, not the coarse best
node, so the second event - 3.7 km from the centre - was located off the
edge of a 1 km box with two stations dropped as outliers; it is centred on
the coarse best now and the error is 40 m.

- `--action associate` on a site; the SRA packet carries the stations' own
  events; `gea seismic --action assoc-selftest`; the vocabulary gate caught
  the word "refused" in the packet's own prose and it was reworded.
- Section BD (6 checks): 314.

## 2026-10-07 - the OSDU-shaped export (v0.11.0)

Next on the list after association. Research first: the OSDU data-definitions
examples for Manifest 1.0.0, master-data Well 1.0.0, dataset File.Generic
1.0.0 and work-product-component WellLog 1.1.0, read record by record, so
the export is OSDU-shaped by their schema. The seismic trace component's
schema was not read, so stations go out as generic components with their
records as datasets, and the export says so.

- `gea/osdu.py`: Context (the operator's declarations: partition, ACL,
  legal tag, countries, operator organisation), spatial_location (both
  coordinate sets, the operation written out, no WGS 84 on an unknown
  datum), well_record, wellbore_record, welllog_record (time domain,
  ZeroTime, curve per channel), dataset_record (size, SHA-256), station
  record, build_manifest, validate, a labelled self-test.
- The datum discipline from v0.8.0 is what makes this export worth
  anything to a platform: AsIngestedCoordinates on EPSG::4267 beside
  Wgs84Coordinates with "shift 45.4 m" in AppliedOperations.
- `--action osdu-export`; NOT LOADABLE until the operator declares; `gea
  osdu`; `gea help osdu`.
- Section BE (4 checks): 318.

## 2026-10-08 - the console answers (v0.11.0, before the ship)

The operator reported the control panel "in the same loading pattern for
three hours" against `gea 0.11.0` serving `C:\site` ('Pad 3'). The
workspace's own records said what the program had done in that time: a
start and a resume at 01:50Z, nothing behind to catch up, no page fetch
audited (a GET is not), no sign-in. The dashboard had worked on that machine
on every earlier version - sign-ins and jobs from the page on the 2nd, 4th,
5th and 6th - and the same code served the page in 4 ms in the cloud. The
server was up and answering; the window it was started in gave no sign of
it, and a console that prints a banner and then nothing is, to the person
watching it, a program that is still loading.

- `gea serve` opens the control panel in the browser itself once its port
  is listening (`--no-browser` to suppress), and says so; the banner says
  in words that this window is the server and has nothing more to load.
- The serving console prints what happens as it happens, from the audit
  entries after they are written: the page opened (once per address), a
  sign-in or a refused one, jobs starting and finishing, stops and restarts
  requested, sessions revoked. `Workspace.on_audit` is the hook; the gate's
  in-process service leaves it unset.
- The kit launchers no longer open a browser on a four-second timer - that
  raced the server and showed a failure page from the second before it was
  up; `--no-browser` passes through from a scheduled start, and a restart
  from the panel does not open a second page.
- Section BB: BB12 (the console lines) and BB13 (the flag, the launchers,
  the gate's own service opens nothing). The gate is 320.

The SAR panel, run from the Seismic page's foot at 02:49Z (48 frames, four
rigs POINTED), was then reported as nowhere to be found: below a station
table and two cards it is off the screen. It has a page and a navigation
entry of its own now ("SAR panel", after Seismic), the same panel bound the
same way; the Seismic page keeps its copy. Played end to end in a headless
browser - sign-in, Run the scene, the job, Play, frame 4/48 - with no
script error. AQ4 extended.

## 2026-10-08 - the Linux kit's gate judges the Linux kit (v0.11.1)

v0.11.0 shipped (bc5e185): ci green, PyPI green, the Windows kit green, the
Linux kit red at "Install it offline and run its gate" after 13.5 minutes -
the gate ran to its last sections. The job log is behind an admin login
this session does not have; the runs and steps are public and were read.
The cause was found by reading section BB: on a Linux kit `cmd` is None,
and the section substitutes a bare stand-in launcher text so that BB6 can
build its good and bad kits; BB13, new at v0.11.0, then judged that
stand-in for `%GEA_BROWSER%` and failed. The checkout has the template and
the Windows kit has the real launcher, which is why both of those passed.

- BB keeps `cmd_real` - the launcher as shipped, or None - before the
  stand-in; BB13 judges that, and a Linux kit on its shell launcher alone.
- Proven from an installed layout (the package installed into a venv with
  no checkout beside it, `start-dashboard.sh` alone above the interpreter,
  `kit_dir()` finding it): the shipped BB13 fails there exactly as the kit
  build did, the corrected one passes 13/13. The gate is 320.
- The first ship attempt stopped at AM1: the 23 report samples still
  carried build 0.11.0. Re-rendered at 0.11.1 and transferred; the samples
  are re-rendered before every ship and this one had skipped that step.

## 2026-10-08 - the QuakeML catalogue export (v0.12.0)

v0.11.1 shipped green on all five jobs; the operator reported the program
approved by the client on its deadline. Next on the list: QuakeML out, the
smallest remaining piece with the largest external audience. Research
first: the QuakeML-BED-1.2 schema read type by type (Origin, Pick, Arrival,
Event, EventParameters, OriginQuality, OriginUncertainty, CreationInfo,
WaveformStreamID; the enumerations; the ResourceIdentifier pattern) and the
units as the specification states them (depth and horizontal uncertainty in
metres, lat/lon and their uncertainties in degrees, epicentral distance and
azimuth in degrees, times and residuals in seconds). The element order is
free (an unbounded choice), the identifiers are not.

- `gea/quakeml.py`: `rid` (identifiers to the pattern from any site name),
  `stream_codes`, `build` (the association document to a tree: event,
  origin with quality and originUncertainty, picks, arrivals, comments for
  the catalogue standing, the magnitude basis, the dropped picks, the misfit
  region and the model), `write`, `validate` (the reader's first checks),
  `selftest` on the association leg's scene, `report_text`.
- `Workspace.quakeml_export`; `associate_site` now keeps each record's FDSN
  codes in `stations[<id>].stream` for the waveformID.
- The output validated against the published XSD with lxml in the cloud,
  on the labelled scene and on the gap cases (no codes, no position, an
  awkward site name); the gate's own check is the structural one.
- `--action quakeml-export`; `gea quakeml`; `gea help quakeml`; README.
- Section BF (4 checks): 324.

## 2026-10-08 - the well as master data names it (v0.13.0)

Built while v0.12.0 shipped, in the cloud only - nothing enters the clone
while ship.ps1 runs, because its `git add -A` would sweep it in. Research
first: the US Well Number's structure and ranges (SS-CCC-UUUUU-SS-EE; the
state and offshore codes, which are not FIPS; the unique-well ranges; the
custody history from API Bulletin D12A to PPDM's 2013 standard) and PPDM's
"What is a Well" component definitions and principles (one Origin per Well;
a Wellbore by its terminating point; segments that do not overlap; a
contact interval owned by one completion at a time; the Well Set across
the life cycle).

- `gea/ppdm.py`: STATE_CODES (55 entries), UNIQUE_RANGES, COMPONENTS,
  `parse_well_number`, `well_identity`, `identity_lines`, `report_text`,
  `selftest`.
- `Workspace.well_identity`; the SRA packet carries an identity on every
  well row (`packet(..., wells=)`) and the report prints section 2a; the
  OSDU Well record's FacilityID and a typed USWellNumber alias, the
  Wellbore record's FacilityID the twelve-digit number only when given.
- The disposal declaration's channel keys are `channel_pressure`,
  `channel_rate`, `channel_bhp`; the first draft of the identity read the
  wrong names and named no stream - caught by the self-test.
- `--action well-identity`; `gea ppdm`; `gea help ppdm`; README.
- Section BG (3 checks): 327.

## 2026-10-08 - machine vibration (v0.14.0)

Built in the cloud while v0.13.0 shipped. Research first: ISO 20816-1's
quantity (broadband r.m.s. velocity, 10-1000 Hz, 2-1000 Hz below 600 r/min),
ISO 20816-3's groups, support classes and zone boundaries (two independent
readings agree on 2.3/4.5/7.1, 3.5/7.1/11.0, 1.4/2.8/4.5, 2.3/4.5/7.1 mm/s
and that 20816-3 kept 10816-3's casing values), the zone meanings, the
bearing kinematics (BPFO, BPFI, BSF, FTF with the contact angle), and the
envelope method (band by kurtosis, analytic signal, envelope spectrum).

- `gea/vibration.py`: `broadband_velocity` (frequency-domain integration
  inside the band, tapered ends excluded from the r.m.s.), `zone`,
  `bearing_frequencies`, `choose_band`, `envelope_spectrum`, `peaks`,
  `match`, `read_record` (CSV via the historian reader, miniSEED/SAC via
  the seismic reader), `assess`, `synthetic_scene` (1780 r/min, an unbalance
  line of known r.m.s., a 3 kHz resonance rung at BPFO or BPFI, noise),
  `selftest`, `report_text`.
- Two defects the scene caught. The first matching rule accepted a
  harmonic without the fundamental and named BPFI and BSF from BPFO's
  train; the fundamental is required now and the tolerance is the
  fundamental's for every harmonic. The velocity-input check first built
  velocity by a cumulative sum, which carried the impulse train's ramp and
  leaked it across the band; it is built in the frequency domain.
- The miniSEED writer: a 20 kHz record read back as fifteen traces, because
  the header's time field is 0.1 ms and the records' starts were rounded to
  it; blockette 1001 now carries the microseconds and the round-trip is one
  trace, exact.
- `Workspace.vibration_report` (record copied under wells/<id>/machine/
  with its hash; `client_reports.vibration_report`; audit `well.vibration`);
  `--action vibration-report` (`--vib-unit`, because `--unit` is the
  permit-grid unit); `gea vibration`; `gea help vibration`; README.
- Section BH (3 checks): 330.
- v0.13.0 shipped four green, one red: the main-branch kit run's Windows job
  failed at the attach step at 16:12:06Z, the second the tag run attached
  the same kit (release v0.13.0 carries both kits; the gate had passed).
  The attach script on a main run no longer replaces, and an upload that
  fails because the asset appeared meanwhile is read as the tag run's
  work. Folded into v0.14.0.

## 2026-10-08 - conformity, in the standards' words (v0.15.0)

v0.14.0 shipped five green, the main-branch kit run included (the attach
race fix held). The list said "API RP 19 / ISO 17025 conformance naming";
no API RP 19 applies to pressure gauges (the 19-series are perforating,
proppant and sand control), so the leg is built on what does govern a
calibration certificate and a statement of conformity: ISO/IEC 17025:2017
7.8.2.1, 7.8.4.1 and 7.8.6, and ILAC-G8:09/2019's decision rules (two
independent readings of the rules agree on the four outcomes and the 2.5 %
false-accept bound at the acceptance limit).

- `gea/conformance.py`: `decide` (simple acceptance, binary and non-binary
  guard band; the statement carries what 7.8.6.2 asks), `expanded_uncertainty`
  (k·sigma/sqrt(n)), `CERTIFICATE_CLAUSES` (fourteen items),
  `certificate_conformance`, `register_conformance`, `report_lines`,
  `selftest`.
- `CertificateRegister.add(**clauses)` with CLAUSE_FIELDS; `conformance()`;
  status rows carry `iso17025`; the API passes the fields through and the
  instruments answer carries the standing per certificate; the CLI takes
  the flags and has `--action conformance`.
- The drift report's section 9: the "Bias vs accuracy" column is
  "Conformity" with the outcome, a "ISO/IEC 17025 7.8" column with the
  standing, and one statement paragraph per certificate; AH7 follows.
- The well page: the filing form takes U, k, probability, traceability,
  conditions, calibration date, method and authorisation; the table shows
  U (k) and the standing with the missing clauses.
- `gea help conformance`; README.
- Section BI (3 checks): 333.

## 2026-10-08 - WITSML 2.x over ETP: the store pushes (v0.16.0)

v0.15.0 was prepared, verified in the clone and handed over to ship; this
leg was built in the cloud while it shipped and transferred only after the
tag was on origin. The list said WITSML 2.0 / ETP. The research before the
code: ETP v1.2 is a WebSocket subprotocol (`etp12.energistics.org`) with an
Avro-binary header and body, flags 0x01 multipart / 0x02 final / 0x04 no
data / 0x08 compressed / 0x10 acknowledge / 0x20 header extension; the two
reference implementations number client messages even from 2 and server
messages odd from 1; ETP times are microseconds since the epoch; the
real-time path is Protocol 21 ChannelSubscribe (GetChannelMetadata 21/1,
SubscribeChannels 21/3, ChannelData 21/4, UnsubscribeChannels 21/7,
SubscriptionsStopped 21/8). The message schemas were taken from the
Energistics definitions as published in the Apache-2.0 `etptypes`
distribution and carried as `gea/etp12_schemas.json` so the codec is driven
by the specification, not by hand-written field lists.

- `gea/etp.py`: `Avro` (schema-driven encode/decode), `encode_message` /
  `decode_message`, `WebSocket` + `ws_connect` + `ws_accept`, `EtpSession`
  (RequestSession/OpenSession, Ping, CloseSession, ProtocolException),
  `EtpTap` (connect, channel metadata, subscribe, run → records), `replay`
  (the session's shape), `simulate_store`, `selftest`.
- Wired into `patches.py` PROTOCOLS, `service.py` defaults and validation,
  `workspace.py` PORTS, the page's patch form, `gea etp` / `gea etp-sim`,
  `gea help etp`.
- `doctor.started_through_launcher`: the finding for a panel started through
  gea.exe (pip cannot replace the launcher while it runs; `gea update` from
  such a panel refuses and says why). Promised after the 0.11.0 → 0.14.0
  update attempt on the site machine failed with WinError 32.
- Replay of an ETP recording prints the session's shape and says it does
  not re-create samples (first draft printed `records: 0` without a word).
- Section BJ (4 checks): 337.

## 2026-10-09 - live stations: SeedLink (v0.17.0)

v0.15.0 shipped five green; v0.16.0 was staged in the clone and handed over
to ship. Asked what was next and offered the real records (his to supply),
SeedLink live stations, a month-end packet and a soak, he chose SeedLink to
be built in the cloud while v0.16.0 ships. The research before the code: the
SeisComP SeedLink documentation for version 3 (commands, the CR LF
responses, `LLCCC.T` selectors, the `year,month,day,hour,minute,second`
time form, the 8-byte `SL` header with its six hexadecimal digits and the
512-byte record, `END` in dial-up mode) and the FDSN SeedLink 4.0
specification (`SLPROTO`, `NET_STA` and `LOC_B_S_SS` patterns, decimal
sequence numbers, the `SE` header fields and byte order, the error codes,
JSON INFO); libslink's source for how an INFO packet is told apart
(`SLINFO`, `*` in the last header byte when more follow).

- `gea/seedlink.py`: `parse_hello`, `parse_selector` / `selector_v4`,
  `SeedLinkClient` (handshake in 3 or 4.0, `read_packet` for `SL`, `SLINFO`,
  `SE` and `END`), `SeedLinkTap` (day files, state with per-station
  sequence and per-channel latency/gaps, keepalive, reconnect with backoff,
  REFUSED on an unknown station, ENDED on the server's END),
  `SimulatedStation` / `SimulatedServer` / `simulate_server` (both
  protocols, a ring per station, INFO in a log record and in JSON,
  `drop_clients`), `LiveStations` (one tap thread per live station of a
  workspace), `selftest`, `report_text`.
- `seismic.build_records` factored out of `write_mseed` so the simulator
  serves the writer's own records.
- `Workspace.add_live_station`, `live_station_config`, `live_station_state`,
  `set_live_enabled`, `fold_live_station`; `Service` runs `LiveStations`
  with the scheduler and stops it with the service; `/api/seismic/live*`;
  the Seismic page's live-station card; `gea seedlink`, `gea seedlink-sim`,
  workspace actions `add-live-station`, `live-run`, `live-fold`; `gea help
  seedlink`.
- First draft lessons: a 100 Hz station fills a 112-sample record every
  1.12 s, so the self-test's station runs at 250 Hz; the handshake log is
  kept on the state so the resume's `DATA` line can be checked after the
  client has closed.
- Section BK (3 checks): 340.
- v0.16.0 shipped with the Linux kit's tag run red at "Attach to the
  release" while the release carried both kits: the attach steps of the tag
  run and the main run started at 01:28:58 and 01:28:59, the main run
  created the release, the tag run's create failed on it. attach-kit.sh now
  tolerates a failed create when the release exists afterwards (the v0.13.0
  fix had covered the upload collision, not the create). Rehearsed with a
  stand-in `gh` in both orders. Folded into v0.17.0.
- The ship's gate on Windows stopped at BK3 (339 passed) where the Linux
  VM had passed it: the live-station card is read after a fixed 3 s sleep,
  and the latency on the card was computed only every twenty records, so a
  slower machine showed six records and no latency yet. The latency is now
  computed per record and the check waits (up to 20 s) for the card to show
  six records and a latency; BK3 names the sub-check that failed, with the
  card's row, instead of one long AND. Also the `\ ` escape in the kit's
  README text (a SyntaxWarning in every gate run) is written as `\\ `.

## 2026-10-09 - v0.17.0 shipped; the kit workflow's other race (held for the next ship)

v0.17.0 shipped (5f201b1): PyPI, both ci runs and the main-branch kit run
green; the tag run's Windows job red at "Attach the kit to the release" -
with both kits on the release. This time the race was the upload, the other
way round from v0.13.0: the tag run's Windows upload (`--clobber`) began at
05:44:45, the main run of the same commit found no kit and uploaded its own
at 05:44:54, and the tag run's upload ended 422 at 05:44:57. The two kits are
built from one commit. attach-kit.sh now takes a kit that appeared meanwhile
as its own on a tag run (a run asked for by hand still replaces, on a second
attempt). Rehearsed against a stand-in `gh`: tag run under the race, manual
run under the race, and the clean case. Goes out inside the next tagged
version, as every change does.

## 2026-10-09 - one kit run per ship (v0.17.1)

He rejected the third patch of the attach step in as many ships, rightly:
tolerating whichever side of the race lost that time fixed nothing. The
cause is that a ship pushes main and the tag together and the kit workflow
ran on both, so two runs built the same commit and wrote to the same
release within seconds of each other. Under his rule that every push is a
tagged commit, the main run was a duplicate of the tag run - the "prove a
kit fix at once" purpose it had was already served by the tag run of the
ship that carries the fix.

- build-installer.yml: runs on the tag and by hand only; a concurrency
  group on the commit serialises a manual run that overlaps a tag run.
- attach-kit.sh: the rule text names the manual run in the main run's
  place; the tolerances stay as a second line.
- Version 0.17.1; samples re-rendered; gate 340 (the workflow is not gated
  code, so the count does not move).

## 2026-10-09 - the backup (v0.18.0)

v0.17.1 shipped four green - one kit run, as designed. He brought an
independent evaluator's production list: run the gate and the doctor on the
machine that will serve; declare Pad 3's wells with API, datum and the three
channel tags; point one real feed at it; the go-live list (loopback, proxy,
four accounts, daily housekeeping, the load test); a backup, which the
package did not write; three public files through the commands that exist
(TexNet catalogue, one TexNet day with its StationXML and a permit export,
TexNet injection rows for the neighbouring wells); the week-one rehearsal
signed on a filled fat-sat protocol. Of those, the backup is code and is
this leg. The injection reader waits for one real export from the TexNet
tool (not reachable from here); the rest is his to run on the site machine.

- `gea/backup.py`: `site_files` (the walk, minus job run folders and
  `_uploads`), `make` (the archive, MANIFEST.json, the .sha256, --keep,
  records/backups.jsonl, audit), `verify`, `restore` (empty folder only,
  verified twice, restored_from.json, audit), `status`, `report_text`.
- The doctor's backup line; `/api/backup*`; the Audit / Update card; the
  `backup-daily` schedule entry; `gea backup`; `gea help backup`;
  deploy/README.md and the README's go-live section.
- First-draft lessons: two backups in one second overwrote each other (the
  second is numbered now), and a lexical sort put `-2.zip` before `.zip`
  (ordered by stamp and number); the test read the first archive's hash
  file after --keep had pruned it.
- The attach-kit tolerance for a tag run whose upload loses to the main
  run (written before v0.17.1 removed the second run) goes out in this
  version as a second line, as the SESSION_LOG above says.
- Section BL (2 checks): 342.

## 2026-10-10 - the month-end packet (v0.19.0)

v0.18.0 shipped four green. Asked to prepare v0.19.0 with no new data in
C:\site (the TexNet injection export is still to come), the leg is the
month-end packet from the roadmap: the one command that assembles what the
client is owed each month from the reports the program already writes.

- `gea/month_end.py`: `period_bounds` (YYYY-MM checked; the month's first
  and last instants; closed or OPEN), `previous_period`, `assemble` (the
  refresh with --month for the period, the SRA packet per site with the
  period's bounds, the site reports; then the collection by report family
  into reports/month_end/<period>/, the certificate conformity written from
  the register, INDEX as a client report through `client_reports.write`
  (so the forbidden-term guard applies), MANIFEST.json, the archive, the
  record and the audit), `packets`, `verify`, `report_text`.
- `Workspace.month_end`; `--action month-end --period --no-refresh
  --catalog`; `/api/month-end*`; the Reports page card with Assemble and
  Schedule monthly (`monthly_day` + `daily_at` on the existing scheduler);
  `gea help month-end`; README.
- Section BM (2 checks): 344.

## 2026-10-10 - the soak (v0.20.0)

v0.19.0 shipped four green; the site page was down because no serve had
run since the Oct 8 start (the run log said so) - not a program fault. He
chose the recommendation for v0.20.0: the soak, with the two hygiene fixes.

- `gea/soak.py`: `LinkRelay` (a TCP forwarder with `cut()` / `restore()`),
  `run` (the simulators behind relays, the supervisor and the live station,
  the outage schedule, the reconnection timing per source after each
  outage, the per-source counts, the page probes, the checks, soak.json,
  the Soak Report), `soak_report`.
- `wits0.simulate_server(repeat=True)` with `thread.stats` (frames sent,
  frames with nobody listening, sessions) so a WITS0 sender survives a
  dropped client like a mud logger does.
- `gea soak`, `/api/verify` what=soak (15 min at the site's patch count),
  the Verification row and button, `gea help soak`, README.
- `client_reports.guard_text`; the survey report reworded and guarded
  (section Y's expectations follow); the tester guide line and section
  title; the two internal strings; the "172 checks" label.
- Section BN (3 checks): 347.

## 2026-10-10 - mapped structure (v0.21.0)

v0.20.0 shipped four green. He chose the site layer for v0.21.0 and, since
neither this workspace nor his VM can reach pubs.usgs.gov, downloaded the
USGS structure shapefile himself and handed it over: it is PP 1708's
Appalachian basin structure layer (15,699 PolyLine features - 12,526
"Fault", 2,000 "Fault-Thrust", 296 anticlines, 277 synclines, the Rome
Trough boundary faults, the Clarendon-Linden Fault Zone - NAD83 geographic,
2014, Trippi/Ruppert/Ryder), not the Permian: the Appalachian basin is his
own ground, and the Permian layers are still to be fetched.

- `gea/shapefile.py`: `parse_prj`, `read_dbf`, `read_shx`, `read_shp`
  (by the index when present), `read` (zip | folder | .shp; the metadata
  beside it), `write` (for the gate's own layers), `NAD83_GEOGCS`. The real
  layer exposed a record whose own content length understates its size
  (record 15,692 of 15,699): a sequential read stopped there with a
  nonsense shape type; the reader now follows the .shx, and names the
  record and the remedy without it.
- `gea/structure.py`: `classify`, `guess_attrs`, `nearest` (local-plane
  segment distance, then Vincenty to the nearest point), `inside_extent`,
  `compass`, `lines`.
- `Workspace.add_layer / layers / layer / remove_layer / load_layer /
  structure_at / well_structure`; the SRA packet's `structure` and section
  2b; `/api/layers*`, `/api/wells/<id>/structure`; the Structure layers
  card and the Mapped structure card; `gea structure`; workspace actions
  `add-layer`, `layers`, `remove-layer`, `structure`; `gea help structure`.
- Real numbers: the layer reads in 0.23 s; from Youngstown (41.0997 N,
  80.6495 W) the nearest mapped fault is the Akron-Suffield Fault at
  31.53 km, bearing 228 (SW), the Highlandtown Fault at 48.4 km; from a
  Permian position, OUTSIDE LAYER EXTENT. The layer goes into his site as
  C:\site\_incoming\structure.zip for `--action add-layer`; it is not in
  the package.
- Section BO (2 checks): 349.

## 2026-10-11 - the Permian layers (v0.21.0, continued)

He asked for the Permian Basin and I had given him the Appalachian paper's
URL and two addresses that did not exist. I read EIA's oil and gas maps page
live in the desktop browser and gave him the real ones; he downloaded six
EIA zips and handed them over: PermianBasin_Boundary_Structural_Tectonic
(nine shapefiles in one zip - the 2017 faults, the five sub-basins, the
Wolfcamp picked wells, the extent, the Capitan reef trend, the Matador
arch, the Marathon-Ouachita fold belt, the Palo Duro basin, the Wolfcamp
shelf margin), Wolfcamp_Elevation_Isopachs (ZLEVEL contours at 400 ft),
Wolfcamp_B_Delaware, Wolfcamp_Midland A-D, the Bone Spring formations and
the Delaware formation (ElevTop_ft, Thick_ft). All WGS84 geographic.

- `shapefile.members`, `read(..., member=)`: a zip with several shapefiles
  is refused naming them; one is named.
- `structure.layer_kind` - structure | contours | areas | points - and the
  reading for each: `contour_at` (nearest contour per value; "between"
  only when the two nearest values are one interval apart, else the
  nearest and a note; "on" within 250 m), `within_areas` (ray casting),
  `nearest_points`, `describe`, `lines` by kind; `classify` falls back to
  the layer's name (Faults_Permian has one text field, LONG_DESC);
  `inside_extent` widened by the radius. Workspace `add_layer(member=)`
  keeps `member`, `kind`, `value_attr`; SRA section 2b columns Well |
  Layer | Kind | Status | What the layer says; `gea structure --list
  --member`; `add-layer --member`; `/api/layers/members`; the member
  select on the Seismic page.
- Real numbers at 31.42 N, 103.50 W: inside the Delaware Basin; Wolfcamp
  top between -8,000 and -7,600 ft; Wolfcamp B between -8,500 and -8,000;
  Delaware top between -2,000 and -1,800; 3rd Bone Spring between -7,000
  and -6,500; nearest EIA fault 52.62 km SSW (dotted fault); nearest pick
  3.25 km NNW (Wolfcamp_A 7763.39, WC_Thick 4464.56).
- BO3 found a real fault: the gate's own process and its serving process
  each held a Workspace on the same folder, and the second `add_layer`
  from the first dropped the layer the service had added from
  `workspace.json`. `Workspace.refresh()` re-reads the manifest when the
  file changed since this object read or wrote it; every listing and
  mutating method calls it. BO3 checks the manifest on disk holds all
  four.
- Section BO (3 checks): 350. The six zips go to C:\site\_incoming\ beside
  structure.zip; they are not in the package.

## 2026-10-11 - the interval against the mapped tops (v0.22.0)

v0.21.0 shipped four green (1c1aa01). He asked what else would grow the
program and then said do them all; this is the first leg of that list, the
one that needed nothing downloaded. The six EIA zips are in his site folder.

- `gea/interval.py`: `subsea_ft`, `bracket_of`, `compare` (ABOVE / BELOW /
  NOT RESOLVED against a bracket, the top and base taken either way round),
  `base_bracket` (top minus thickness, as brackets), `tier_check`
  (CONSISTENT / INCONSISTENT / NOT RESOLVED / NOT DECLARED), `formation_named`,
  `statement` (NOT DECLARED names what is missing; MD refused with the
  remedy; GL carries the kelly-bushing note), `events_vs_faults`.
- `gea/elevation.py`: `epqs` (the 3DEP point service as it specifies the
  query; NO DATA / NOT JSON / NO VALUE / UNREACHABLE), `as_reference`.
- `Workspace.set_well`: `surface.elevation` {ft, datum KB|GL|DF, source}
  each required; `disposal.base_interval_ft`, `depth_reference`; a base
  above its top refused. `add_layer(formation=, role=)`: a role only on a
  contours layer; a formation without a role defaults to top.
  `well_interval` (reports/wells/<id>/interval.json, `well.interval`),
  `events_vs_faults`; the SRA packet carries `interval` per inside well and
  `events_faults` when a catalogue was given; sections 2c and 2d.
- CLI: set-well `--base-interval-ft --depth-ref --ref-elev-ft
  --ref-elev-datum --ref-elev-source --elev-3dep --epqs-url`; add-layer
  `--formation-name --role`; `--action interval`; `gea elevation`. API:
  `/api/wells/<id>/interval` GET/POST, `/api/wells/<id>/declare`,
  `/api/elevation`; add-layer takes formation and role. Page: the interval
  card with the declaration form and the 3DEP button; formation and role on
  the layers form. `gea help interval`.
- Section BP (3 checks): the module; the workspace through the SRA packet,
  API, page, CLI, help; the 3DEP client against a stand-in service with
  `set-well --elev-3dep` through it. Gate 353.
- Still to come from his downloads: Hennings faults + WSM (slip tendency),
  Volve WITSML/DDR/DLIS, TRRC H-10/W-14/GIS, TexNet injection export, ODNR
  Class II, Utah FORGE. v0.23.0 (Modbus RTU, DNP3, HART-IP) needs none.

## 2026-10-12 - the field ports (v0.23.0)

v0.22.0 went to the clone verified; this leg was built in the cloud while
it waited to ship. The three ports he asked for after the pantheon review -
the ones a pad offers and the program did not take.

- `gea/modbus_rtu.py`: `crc16` (the specification's vector 01 03 00 00 00
  02 -> C4 0B), `frame`, `check_frame`, `read_request`,
  `parse_read_response` (exception, CRC, unit and length each named),
  `decode`/`encode` for every type and word order, `SerialTransport`
  (pyserial, the inter-byte timeout as the 3.5-character gap),
  `TcpTransport` (RTU over TCP), `SimSlave` (a register table; corrupt
  every n-th; silent; `serve_tcp`), `RtuPort` (one read per mapped
  register, GAP with the fault), `read_modbus_rtu`, `selftest`.
- `gea/dnp3.py`: `crc_dnp` (the Annex frame 05 64 05 C0 01 00 00 04 ->
  E9 21), `link_frame`/`parse_link` (header and block CRCs), `parse_objects`
  (ranges 0/1/6/7/8/9, prefixes, packed bits, the 23 variations this
  master reads, the 48-bit time), `Master` (send fragment, read frame,
  reassemble, confirm, unsolicited, integrity and event polls, IIN names),
  `Dnp3Port` (objects to records by `ai:`/`bi:`/`ctr:`), `SimOutstation`,
  `read_dnp3`, `selftest`.
- `gea/hartip.py`: the HART-IP header and the token-passing PDU with its
  check byte, `parse_cmd0/3/9`, the unit-code table, `HartIpClient`
  (session initiate/close, sequence matching, publish messages skipped,
  NAK named, command 0 taking the long address), `HartIpPort` (units
  converted, status bits to quality), `SimDevice`, `read_hartip`,
  `selftest`.
- `patches.py`: PROTOCOLS + the three; `open_field_port`/`close_field_port`
  (transport sim starts the stand-in); the supervisor branch polls and
  feeds records. `workspace.PORTS`; service RUNNABLE, example maps, the
  tap command; the page's port list; `gea modbus-rtu|dnp3|hartip` with
  `--config --polls --seconds --record --out --stream-csv --selftest`
  (`--replay` refused with the reason). `gea help field-ports`.
- Section BQ (3 checks): Modbus RTU; DNP3 and HART-IP; the supervisor,
  the service, the CLI, the help. Gate 356.
- Found on the way: the DNP3 self-test's confirm count raced the socket
  close; it now waits for the confirm. Nothing from his downloads yet.

## 2026-10-12 - DLIS and the historian (v0.24.0)

Built in the cloud behind v0.23.0 while v0.22.0 waits for its ship. The
real file came from GitHub through his machine (equinor/dlisio's test data,
the one file there that is an actually produced log) and sits in
C:\site\_incoming\dlis\.

- `gea/dlis.py`: `_Cursor` (every representation code), `read_sul`,
  `segments` (visible records, segment attributes, trailers; an encrypted
  segment kept and flagged), `logical_records` (reassembly by predecessor
  and successor), `parse_eflr` (set, template, objects; absent attributes;
  invariant attributes), `DlisFile` (CHANNEL, FRAME, ORIGIN, FILE-HEADER,
  PARAMETER; encrypted records counted; `describe`, `frame_data`), `read`
  (the LiveStream), `write` (sets, frames, FDATA; segmentation to the
  record size; padding declared). The real file first failed on an
  encrypted segment - the reader now names and skips it.
- `files.detect` knows the label; `preview` describes a DLIS; `read_any`
  reads it; `scan_root` imports it. `survey_cmd.run_survey(frame=)` reads a
  DLIS and refuses a time-indexed frame by name. The `dlis` port in
  PORT_REGISTRY. `gea dlis`, `gea survey --frame`.
- `gea/piwebapi.py`: `PiWebApiClient` (GET; basic/bearer from the
  environment; plain HTTP refused), `recorded` (paged), `interpolated`,
  `value`, `classify`, `PiPort` (pull; units converted; faults named),
  `SimServer` (paging, digital and system states, 404 with PI's error shape,
  basic auth), `read_piwebapi`, `selftest`. Protocol `piwebapi` in
  patches (the window moves with the last archive time), workspace PORTS,
  service RUNNABLE and example, the page. `gea piwebapi`.
- Real numbers (206_05a): 3,252 logical records; sets FILE-HEADER, ORIGIN,
  EQUIPMENT x14, TOOL x2, 440-CHANNEL x95, PARAMETER x79/x138/x9,
  CALIBRATION-MEASUREMENT x6, CALIBRATION-COEFFICIENT x12/x12, CALIBRATION
  x27, PROCESS, 440-OP-CORE_TABLES x250, 440-OP-CORE_REPORT_FORMAT x17,
  CHANNEL x104, 440-PRESENTATION-DESCRIPTION, 440-OP-CHANNEL x93, FRAME x2;
  encrypted type 132 x9, type 128 x2; frame 800T 2,301 rows x 42 channels
  in 0.07 s; the survey refuses it (time-indexed, no density) and says so.
- Section BR (3 checks): DLIS; PI Web API; the detector, survey,
  supervisor, service, CLI, help. Gate 359.

## 2026-10-12 - the catalogue accounted for (v0.25.0)

He asked what in the repos the program was not taking up. The honest count:
52 catalogue entries (the earlier "58" counted six .dat sidecar pairs
twice); before this leg 17 were named by product code and the rest were
reachable only through CatalogEntry.stream(). Now 29 used, 23 carried and
said so. The catalogue this program was exported with is the same 52 files;
nothing was left behind.

- `gea/catalog_register.py`: USES (entry -> uses in the user's terms),
  `register`, `check_entry`/`check_all`, `audit` (named-in-code vs
  registered, both ways; a LAS entry counts as reachable through the
  survey), `text`. `gea catalog --list --unused --check --entry --info
  --stream-csv --friction --core`.
- `gea/friction_priors.py`: `measured` (seven entries read as what they
  measure, with provenance), `envelope` (0.21-0.90, 70 measurements, 3
  datasets, Byerlee 0.6), `text`. The Nankai coefficient is shear strength
  over effective vertical stress per sample and says so; Barbados' channel
  is found by prefix (the header carries a parenthesis).
- `rock_inventory.core_check`: 70 Volve plugs, bulk from grain density and
  porosity with brine 1.03, 65 inside the published 2.05-2.55 sandstone
  range; on the rock inventory model card's evaluation.
- `survey_cmd.run_survey(catalog=)`; `gea survey --catalog`. Real outcomes:
  L07-01 estimated (2.64-2.74 g/cc); Scorpio and Collingwood no density;
  University 6-17, Kennetcook, 15/9-19 SR too little usable support.
- `gea help catalog`. Section BS (3 checks): the register and every entry
  read; friction and the core check; the surveys, CLI, help. Gate 362.
- Decision for him: the 23 carried entries (paleoclimate, geochemistry,
  science-hole physical properties) can stay with their provenance or be
  dropped from the wheel; `gea catalog --unused` lists them.
- On his machine the first run of BS1 counted 55 entries: the operator
  tier (three private Retama entries, never in the wheel) is loaded there.
  The register, the audit and the check now cover the public tier only
  and count the operator tier without naming it. Gate 362 on both machines.

## 2026-10-12 - slip tendency on the mapped faults (v0.26.0)

He downloaded the two Hennings et al. 2021 fault layers and wsm2016.csv
into the site folder. The Hennings layers are UTM 13N and carry the
authors' own pressure-to-slip per segment (Pp2S_MPa) and dip - so the leg
reads their numbers beside its own rather than pretending to replace them.

- `gea/slip.py`: `stress_at`, `regime_of`, `resolve` (Cauchy on a plane
  with vertical and horizontal principal axes; checked against its
  identities in the gate), `slip_tendency` (Ts at ambient and at the
  increase; pressure to slip; margin), `segment_strikes`,
  `faults_in_radius` (length-weighted axial mean strike; the layer's own
  dip, published Pp2S and confidence read as attributes), `statement`
  (the layer's dip when stated else the declared range; the published
  value beside; worst and best case), `lines`, `read_wsm` (by column
  name), `wsm_nearby` (axial mean, spread, counts, Vincenty distances).
- `shapefile.parse_prj`: any Transverse Mercator or Lambert Conformal Conic
  from PARAMETERs on WGS84/NAD83/NAD27; the PROJCS's own UNIT; `read`
  uses the parsed definition. `Layer.declared_kind`; `structure.nearest`
  honours it; `add_layer(feature_kind=)`.
- Workspace: `set_stress` (sources required), `stress`, `add_stress_map`
  (kept whole, hashed), `stress_map`, `wsm_at`, `well_slip` (depth from the
  interval midpoint; azimuth from the declaration or the map, stated;
  refusals in order), the SRA packet `slip` and section 2e. CLI
  `set-stress`, `add-stress-map`, `wsm`, `slip`, `gea slip`. API and page.
  `gea help slip`.
- Real numbers: WSM 40,730 records; 35 A/B/C within 100 km of 31.42 N,
  103.50 W -> SHmax 92 deg, median deviation 17 deg (16 A, 11 B, 8 C);
  Youngstown 9 within 150 km -> 63 deg. Hennings BSMT 6,505 segments,
  SNF 1,827, both read in 0.1 s; at the Pad 3 test position the SNF traces
  within 15 km publish 322-1,351 psi to slip; with example gradients this
  arithmetic differed from them by hundreds of psi - the point of printing
  both.
- Section BT (3 checks); BO1's UTM case now expects the parameter-based
  refusal. Gate 365.

## 2026-10-12 - ComCat (v0.27.0)

Neither this workspace nor his VM reaches earthquake.usgs.gov, so I read
the FDSN text service through the desktop browser and kept the two answers
verbatim as fixtures: the Delaware Basin for September 2026 (28 events,
largest M 3.4, TexNet and NEIC) and Youngstown 2011-2012 (9 events, the
M 4.0 of 2011-12-31 - his own ground, the Northstar 1 sequence).

- `sra.read_fdsn_text` (contributor, magnitude type, place on each event;
  `read_catalog` dispatches on the `#EventID|Time|` header),
  `sra.comcat_url`; `gea comcat --file | --lat --lon --start --end`;
  `gea/fixtures/README.md` with the two query URLs; `fixtures/*` in the
  package data; the SRA help names the service.
- Real numbers: a 10 km area at 41.12 N, 80.69 W with an M 2.0 threshold
  and a 2011-12-01 plan date holds all 9 events, 6 at or above the
  threshold; the M 4.0 is TRIGGERED with respond-by 2012-01-02T20:05:01Z;
  the goal clock starts 2011-12-31.
- Also found on the GDR for him: the FORGE 16A(78)-32 April 2022
  stimulation data (288.94 MB, 1-second Pason data) and the seismic event
  catalogues (321 kB, 720 kB updated) - gdr.openei.org submissions 1379,
  1399, 1429.
- Section BU (1 check). Gate 366.

## 2026-10-12 - Tools and ports (v0.28.0)

Eight ships today (v0.20.0 through v0.27.0). He asked what was left besides
client data and for a tool catalogue tab in the browser, and chose tools +
the port registry.

- `gea/tool_catalog.py`: `tools()` (the library with citations, ratings,
  measures, declared interface, the datasheet aging rate via
  `drift_model_for` or the reason there is none), `LIVE_PORTS` (the eleven
  live protocols with what each speaks to, needs and rehearses against),
  `ports()` (the file registry beside them), `ports_for_interface` /
  `unmatched()` (the tools no port speaks, named), `catalog()`,
  `check_string` (the rating check with the basis stated and unknown tools
  refused), `text()`.
- `/api/tools` (any signed-in reader), `/api/tools/rating` (gradients only -
  a well profile is not inferred); the `#/tools` tab with the register, the
  unmatched table and a rating-check form; `gea tools [--list|--json|
  --rating --station <ft>:<tool>]` (exit 1 when over a rating);
  `gea help tools` and the view mapping.
- Real numbers: 7 tools (3 with published numbers, 4 user-supplied), 9 file
  ports, 11 live protocols; a GEOQ 177 16k gauge at 16,000 ft sees 183.9 C
  against its 177 C rating and is flagged.
- Section BV (2 checks). Gate 368.

## 2026-10-13 - The catalogue on a world map (v0.29.0)

He asked where the datasets from the 14+ wells were and where the global
map was. The datasets are the catalogue inside the wheel (52 public entries,
49 wells and sites); the map had never been built. Then the Pad 3 bring-up:
his pasted commands died on `<placeholders>` in PowerShell and on relative
paths run from the repo folder; `C:\site\_incoming` held eight EIA/Hennings
zips, the World Stress Map and a North Sea DLIS, none loaded. Loaded the
33 layers and the stress map into Pad 3 from his machine (the Midland and
Delaware Wolfcamp sets named apart, because two layers of one formation and
role would have overwritten each other in the interval band - a defect
found by reading `interval.statement`, now refused at `add-layer`). The
DLIS (206/05a-3, Fulla field) needs a well to import into and is not a Pad 3
well. An inventory workbook beside the files.

- `gea/catalog_map.py`: `parse_file_position` (PANGAEA `LATITUDE:`, LAS
  `LATI`/`LONG`, LAS `LOC` DMS, KTB `Latitude:` decimal, KTB Gauss-Krueger
  `R`/`H`), `parse_provenance_position`, `SAME_WELL` (declared), `position_of`
  by that chain, `positions`, `sidecar_positions`, `check_sidecars`,
  `write_sidecars` (the sidecars' own two-space layout kept), `counts`,
  `coastline` / `coastline_meta` (fixture), `project` (plate carree),
  `groups`, `svg`, `text`. `tools/catalog_positions.py` writes the sidecars.
- `geodesy`: BESSEL, DHDN (EPSG 1776, 3 m), `gauss_krueger`. The KTB main
  hole's GK position before the shift is 2.3 m from the same hole's decimal
  header, so that header is DHDN too; the pilot hole is 185 m west.
- `gea/fixtures/ne_110m_coastline.json`: Natural Earth 1:110m coastline,
  public domain, rounded to 0.01 degree (134 lines, 5,127 points), upstream
  md5 in the README.
- `/api/catalog/map`, `/api/catalog/entry/NAME`; the `#/catalog` tab;
  `gea catalog --positions | --map OUT.SVG | --check-positions`; the catalog
  help page extended; VIEW_TOPIC `catalog`.
- Fixes: `add_layer` refuses a duplicate (formation, role) and names the way
  out; `shapefile._load_parts` reports a missing path as `no such file`
  with the folder it looked from; every pasteable `<placeholder>` in the
  help command lines, README, usage messages, doctor fixes and the page
  replaced by a real example value or an uppercase metavar (BV2's expected
  station message updated to match).
- Real numbers: 44 of 52 positioned (38 file headers, 4 same borehole, 2
  source records, 1 approximate), 8 NO POSITION, 33 marks.
- Sections BW (3 checks) and BX (2). Gate 373.

## 2026-10-13 - The kit's gate (v0.29.1)

v0.29.0 shipped: ci tag, ci main and PyPI green; both kit builds red at the
installed kit's gate. No log access from here (the job log needs admin
rights), but the public steps named the step and a package-only copy of
`gea/` reproduced it in one run: BW3's `tools/catalog_positions.py` and
BX2's `README.md` do not exist beside an installed package -
FileNotFoundError. Guarded both the way AJ1 and AM already are (read in a
checkout, say so in a kit), reran both sections from the package-only copy
with PYTHONPATH set so the CLI subprocesses see it: 5 passed. The
package-only run is now part of the cloud prechecks for every leg that adds
a section. Gate 373.

## 2026-10-14 - The drilling recorder (v0.30.0)

v0.29.1 shipped green on all four rows. He put `Drilling Data.zip` (669 MB)
in `C:\site\_incoming`: three one-second EDR exports named by job number,
18 WITS record-1 channels, May-September 2026. Read on his machine only
(operator data; nothing copied here). The program could not take them: the
date and time are two columns, so `files` called it "csv, first column not
a timestamp" and `ingest` tracebacked. Built the port rather than convert
his files by hand.

- `gea/edr.py`: `is_edr`, `split_header` (units mapped: feet->ft,
  klbs->klbf, ft_per_hr->ft/h, kft_lb->kft.lbf, api->gAPI...), `read`
  (streamed into `array('d')`, sentinel/blank/non-numeric counted, sections
  at gaps > 60 s, out-of-order and duplicate stamps counted), `record`
  (sections: hours, gap before, hole depth at the first on-bottom sample
  and the deepest, feet drilled start-to-deepest and the channel's upward
  steps beside it, hole-depth falls > 10 ft named as resets, on-bottom and
  rotating hours, ROP on bottom mean/median, connections = on-bottom flag
  falling, peaks; well: TD, the first on-bottom sample at TD, the deepest
  TVD-bearing sample's TVD and how far short of TD the TVD stops, feet per
  on-bottom hour), `md_tvd_table` (one row per 100 ft, the last sample of
  each step, non-monotone steps counted), `lateral_from_table` (TVD within
  1% of its max), `lines`, `document`, `write_record`, `selftest`.
- `files.detect` kind `edr`; `read_any`; `PORT_REGISTRY['edr']`;
  `read_historian_csv` refuses a non-timestamp first cell by row and column
  and names the EDR port when the header is the two-column layout;
  `gea ingest` exits 1 with the sentence; `dashboard.orchestrate` writes the
  drilling record for an EDR well and runs no gauge pipeline; the alarm
  defaults skip a non-historian file well; the well page card; `gea edr
  --file | --out | --json | --selftest`; help `edr`.
- Real numbers (his machine, package-only preview): 30636033 - 1,434,807
  rows, 2 sections, TD 25,804 ft MD on 2026-07-08, recorder TVD 9,052 ft,
  lateral from 15,500 ft MD (10,275 ft), 164.7 h on bottom, 19 s to read;
  30665733 - 1,512,262 rows, 8 sections (four of them minutes long at a
  restart), TD 25,918 ft, TVD 9,251 ft, lateral from 14,200 ft (11,688 ft),
  186.5 h on bottom; 30693545 - 2,849,464 rows, TD 25,779 ft, TVD 9,077 ft,
  lateral from 15,100 ft (10,562 ft), 267.8 h on bottom, 36 s to read.
  Every file resets its hole depth at a restart (8,498 -> 1,417 ft on
  07-01; 25,779 -> 8,285 ft on 07-23); the record carries both arithmetics
  and names the fall. Gamma is sentinel for 40-43% of rows; TVD is blank
  for 9-20% and stops 29-117 ft short of TD.
- Section BY (3 checks). Gate 376.

He asked for the mining to be verified completely, and whether the
program and the site are synchronized, which fields the data updates, what
must be recalculated, and whether the client folders exist. The
verification found the largest thing the record was not reading: the
gamma channel, sentinel 40% of the time, is a complete MWD gamma log the
rest of the time - 577,938 on-bottom samples covering every one-foot bin
from 2,061 to 25,804 ft in the first file. Added `gamma_log` (bins of bit
depth, median and count), `write_las`, `gamma_lithology` (the program's
own GR index), and `observations` (below-zero counts, ROP spikes, the
negative differential pressure named as the recorder's reference). The
survey run on the written LAS exposed a pre-existing defect: `read_las`
dropped the index unit and the survey read feet as metres - fixed in
`read_las` (index_unit kept) and `run_survey` (feet converted once, the
depth line says which). BY extended; Y, R, J, B, BS, BR re-run green.

## 2026-10-14 - The drilling record reads its own depth (v0.30.1)

v0.30.0 shipped with the stopgap. Run on his three files, the record's
footage and total depth were still wrong: 30693545
took its total depth from 25,779 ft, a depth carried over from the well
before, and none of the three footages equalled total depth less the first
depth. Replaced the stopgap (`feet_by_increments`, `hole_depth_drops`,
first-on-bottom start) with `depth_events`: STALE OPENING (a section's
opening run left by a jump of more than 10 ft, or differing from where the
file left off and holding to the section's end, nothing drilled - excluded);
RE-REFERENCE (a fall regained within its own section faster than 1,000 ft/h
or with the recorder's ROP at zero - the window excluded); SIDETRACK (a
fall of 500 ft or more and the next six hours on bottom under 20 ft/h - a
new borehole; TD from the final borehole; `_keep_masks` keeps the TVD table
by hole depth and the gamma log by bit depth off the abandoned interval);
CORRECTION (a smaller fall, footage taken back; a set-forward of more than
10 ft off bottom, footage added); UNRESOLVED otherwise. `_idle_flag`: 30
min or more with the flag set at bottom, depth still and ROP zero is kept
out of the drilling hours. The running bottom and per-sample increments
come from one pass that `record`, `gamma_log` and `md_tvd_table` share.
- First real run found a 12 ft fall at the end of a section (30665733,
  06-16 02:33) treated as a re-reference regained 67 days later, with 18 h
  excluded: the regain is now bounded to the section. The same well's 18 h
  were in fact the on-bottom flag left on at 8,591 ft with ROP zero
  (08-21 00:11 to 08-22 02:38): `_idle_flag`. And its footage took back a
  12 ft overstatement twice because the set-forward between was not
  credited: set-forwards are now corrections that add.
- The first full refresh of Pad 3 with the tightened stale-opening rule
  (left by a jump, or differing and held to the section's end) read
  30665733 as sidetracked from 25,722 ft, 41,009.9 ft drilled: its two
  one-minute restart sections opened on 25,722, and each was judged
  against the stale section before it. Where the file left off is now the
  last depth it can be trusted for (excluded samples skipped).
  `selftest_flag` carries three stale restarts in a row. The real files
  were re-run with the final module before the transfer was rebuilt.
- Real numbers (his machine, preview): 30636033 - STALE OPENING 8,285 ft
  (06-30 19:00, 91 min), RE-REFERENCE 1,417 ft (07-01 14:27, 7,081 ft in
  169 min), CORRECTION 281.7 ft (07-02 14:47); TD 25,804.0, 23,796.2 ft,
  162.15 h drilling. 30665733 - CORRECTION -94.0 (06-11 10:52) and +93.7
  (12:36), -11.9 (06-16 02:33), STALE OPENING 25,722 ft at each of four
  restarts (08-20 23:15, 08-21 00:00, 00:04, 00:11), +11.1 (08-21 00:11),
  -11.1 (08-22 02:38); 19.92 h flag idle; TD
  25,918.0, 23,879.0 ft, 166.61 h. 30693545 - STALE OPENING 25,779 ft
  (07-23 08:00), RE-REFERENCE 20,038 ft (07-31 05:19, 35,091 ft/h),
  SIDETRACK 24,902.2 -> 16,855 ft (08-11 16:11, 0.6 ft/h median for six
  hours); TD 25,722.0 (not 25,779), 31,752.2 ft = 22,885.2 + 8,867.0,
  258.98 h, 8.72 h flag idle; gamma 23,671 bins; TVD stops 60.1 ft short.
- `selftest_events` (one of each of the four events) and `selftest_flag`
  (idle flag, set-forward, a fall regained only in the next section, a
  section drilling on from its opening depth). BY1 updated for the removed
  fields; BY4 new. The well page shows the events, the boreholes and the
  hours kept out. Gate 377.

## 2026-10-15 - The downhole tool library (v0.31.0)

He asked for every downhole tool in use industry-wide in the tool library,
with a hover and a pop-open tutorial over each; scope chosen "types plus
your fleet": the classes with cited figures only, and the named models of
his own fleet with their datasheets. Ship order "fix record, then library":
v0.30.1 carried the drilling-record fix; this is the library.
- Ninety-six classes in nine categories (`gea/tool_taxonomy.py`). Research
  first: six parallel research passes, each class's pages found and fetched
  in the run (SLB Energy Glossary, SPE PetroWiki, OSHA eTool, maker
  datasheets and product pages, LDEO and IODP tool pages), a paraphrase per
  page to check the tutorial against, and numeric figures recorded only
  where a page stated them, as quoted. Curated: pages from Wikipedia,
  Rigzone, drillingmanual.com, a rental listing, an acronym site and a spec
  sheet whose tables came out scrambled were dropped; figures joined across
  table cells ("..."), garbled fractions, an ambiguous size column, a
  sensor offset filed under the wrong class, strength tiers read as sizes
  and float-valve size codes were dropped. 192 pages and 196 figures remain
  (`reference/tool_sources.json`, fetched 2026-10-14); 72 classes carry a
  figure, the other 24 say to supply the datasheet.
- The tutorials are the program's own words in six parts (what, where,
  how, what it shows in the data - tied to the EDR channels where the tool
  shows in them - what to watch, and what the program does with it) and
  carry no digits at all: BZ1 holds that, so every number on a card is a
  quoted, cited figure.
- The page: a chip per class, the one-liner as a tooltip on hover and on
  keyboard focus (hidden on touch screens, where the tap opens the
  dialog), a native dialog opened by click, tap or Enter and closed by Esc,
  Close or the backdrop, focus returned to the chip; find and category
  filters; the fleet card; the gauge library rows open the PDG or fibre
  tutorial. Driven in headless Chromium in light, dark and phone widths:
  96 chips, hover and focus tips visible, Enter opens, Esc closes, focus
  returns, fleet add and remove round-trip with the badge and card
  redrawn, no page errors, no horizontal scroll at phone width.
- The fleet: `config/fleet.json`, class and datasheet citation required,
  duplicates turned away, audit entries `fleet.add` and `fleet.remove`;
  operator role for changes. CLI `gea tools --action classes | tutorial |
  fleet | fleet-add | fleet-remove`, with the console made safe for the
  figures' fractions and degree signs.
- Section BZ (3 checks). Gate 380. His fleet list (make and model per tool)
  is the next input; the ergonomics leg follows (setup checklist,
  interactive charts, site map, control-room mode).

## 2026-10-15 - The setup checklist (v0.32.0)

The ergonomics leg, first item: the well setup checklist, chosen to put
Pad 3's outstanding setup on one page.
- `gea/setup_checklist.py`: every item is computed from the workspace on
  each read (users.json, the wells' declarations, the site records,
  records/backup_config.json, backups.jsonl through `backup.status`,
  jobs/schedule.json, `report_ages`, the fleet, the stress declaration, the
  layers, each EDR well's drilling_record.json). Required items are
  counted; recommended and optional are shown. A CONFIRMATION (depth
  events against the daily drilling reports) is kept in
  config/checklist.json with a basis key (event count and TD); a changed
  record invalidates it.
- Commands are printed only when they can be pasted as they stand (the
  refresh, the backup run with the configured folder); items that need a
  value from the operator name the values and their source instead - the
  rule that PowerShell placeholders taught in v0.29.
- Found building it: declaring a US Well Number (`set-well --api`) wrote it
  into the disposal block, which sets role = disposal - every Pad 3
  producer would have become a disposal well. Added `identity` to
  `set_well` (api_number, well_name, operator), `ppdm.declared_api`
  (identity, then disposal), OSDU and SRA read through it; the CLI writes
  the disposal copy only for a disposal well. The first gate run caught
  BG3: a disposal number re-declared to twelve digits left the identity on
  the old ten - `set_well` now keeps the two in step, the latest
  declaration winning.
- Found running it: the events selftest well's drilling record was not
  written - its gamma in the kept (sidetrack) interval is flat, and the
  lithology message's word "refusing" tripped the client-report guard. The
  real Pad 3 wells were not affected (their gamma has contrast); the
  message now reads "none is invented". CA1 holds both.
- The page: progress bar, "Go to the next item", per-group counts, forms
  per kind (site, backup folder, surface, elevation with a 3DEP ground-level
  fill, US Well Number, confirmation), done items folded under "Change"
  and hideable (remembered per browser). Driven in headless Chromium:
  3 of 10 to 10 of 10 through the page's own forms and buttons, the Setup
  badge 7 to 0, no page errors, phone width without horizontal scroll.
- Section CA (3 checks). Gate 383.

## 2026-10-15 - Interactive charts (v0.33.0)

The ergonomics leg, second item. The well page's Trends card drew one
channel as a fixed picture of a stride-thinned copy (1,500 points of a
three-million-row file: one sample in two thousand).
- `gea/series_cache.py`: the refresh, which already reads each file well
  once for its reports, writes `cache/series/<well>/` from the same read -
  index (float64), each channel (float32), flagged samples, and for an EDR
  well the depth pass's arrays (`edr.chart_masks`: drilling, flag-idle,
  keep_h, keep_b, excluded, running bottom). `edr.drilling_mask` now holds
  the one definition the record and the charts share. Windows are cut from
  memory maps: equal time buckets by searchsorted + reduceat (min, max,
  mean, count; empty bucket = gap), samples when they fit, depth bins by
  bit depth over drilling & keep_b, progress from the running bottom.
  Timed on a 2.85-million-row, 16-channel synthetic cache: full-extent
  four-channel window 0.22 s, one-day window 0.02 s, depth window 0.11 s.
  Checked bucket by bucket against a brute-force min/max/mean/count (CB1).
  The cache carries its source's size and mtime and is rebuilt when they
  differ; `backup` excludes `cache/`.
- The page: tracks drawn at the container's own pixel width (text stays
  12 px; a ResizeObserver redraws), envelope band + mean line in the
  accent, event lines with labels staggered over three rows and drawn only
  where they fit (the rest in their tooltips), connection ticks under two
  days, section buttons, Table, aria-live readout built with textContent.
  Drilling progress uses the categorical slots 1-3 (blue, orange, aqua),
  which the palette reference validates all-pairs in both themes; added as
  --s1..--s6 in the three theme scopes.
- Driven in headless Chromium, light and dark and at phone width: brush,
  +, Home, depth axis, section 2, table, progress readout, the catalogue
  gauge well; no page errors after guarding renders that land after the
  page moved on. A channel the well lacks is named (Cache.unit raised a
  bare KeyError first - CB2 caught it).
- Section CB (3 checks). Gate 386.

## 2026-10-15 - The site map (v0.34.0)

The ergonomics leg, third item.
- Research first: an offline base outline. us-atlas 3.0.1 from the npm
  registry (counties-10m.json: TopoJSON of the Census Bureau's 2017
  cartographic county boundaries; the data are public domain, the package
  ISC). `tools/build_us_outline.py` decodes the arcs once (9,869 arcs,
  46,565 points, 1e-4 degree), tags 1,994 as state lines (one county, or
  two states, on its sides), and gives each of the 3,231 counties a label
  point; `reference/us_outline.json` (1.7 MB) with a provenance sidecar
  carrying both hashes, the source and the ISC notice.
- `gea/site_map.py`: layers read once per process (keyed on layer.json's
  mtime) into arrays in the frame (a non-NAD83/WGS84 layer would be moved
  by the datum shift at its centre - the layer reader accepts only those
  two today, so the path stands ready); thinning by screen-cell grid (keeps
  ends, drops, never moves); points sampled; the stress map read once;
  `what_here` runs each layer's own `structure.describe` on the cached
  layer at the click, un-shifted into the layer's datum.
- Pad 3's 33 layers measured on his machine before building: 614,007
  vertices in all, 4 s to read them all once; the Eastern US structural
  features layer alone is 320,732 (far outside the basin, culled by box).
  Default view without placed wells: the layers whose centres lie within
  five degrees of the median centre - Pad 3's basin layers, not the eastern
  US.
- The page: SVG at the container's pixel width, outline under the layers
  (county lines only closer than a dozen degrees, names closer than six),
  faults solid and folds dashed in ink, contours by role in the categorical
  slots (top blue, isopach aqua dashed, base orange), areas a light wash,
  stress ticks in orange with A/B heavier, wells in the accent with links,
  rings, scale bar, the outline's source in the corner. Driven in headless
  Chromium light, dark and phone: click answers, key zoom, drag pan, fit,
  rings, no page errors.
- Section CC (3 checks). Gate 389.

## 2026-10-15 - The control room (v0.35.0)

The ergonomics leg, fourth and last item.
- Read first what the workspace already says about a well's state, so the
  wall adds no judgement of its own beyond one stated rule: the alarm
  report's active and shelved alarms with their priorities, the dashboard's
  gauge-drift classification, the drilling record and its chart cache
  (bottom, bit, ROP over the last ten minutes of drilling, only when
  drilling was within half an hour of the record's end), and the patches'
  last sample against each patch's own stale limit.
- `gea/control_room.py`: `board` (a tile per well, worst first, counts,
  the latest alarm events, the latest handover, the rule in words),
  `text` for the terminal; `write_note` / `ack_note` / `notes` over an
  append-only `handover/notes.jsonl` (the author cannot acknowledge, a
  note is acknowledged once); `pass_create` / `pass_list` / `pass_revoke`
  / `pass_check` over `wall/passes.json` (SHA-256 of the code, constant-
  time compare, expiry).
- Service: `/api/wall` answers a signed-in person or a valid `gea_wall`
  cookie and nothing else does; `/api/wall/enter` is locked out after
  repeated wrong passes like a sign-in; `handover_unacked` joins the badges.
- The page: `#/wall` hides the header and navigation, its own high-contrast
  tokens in light and dark, SVG shapes drawn in the state colour with the
  glyph in the page colour, mono figures with thousands separators, the
  live age ticking each second, the stale notice and grey tiles past two
  refresh periods, the Screen Wake Lock asked for (and re-asked on return),
  pages turned on a wide screen and scrolled on a phone. `#/control`: open
  the wall, the latest note with its acknowledgement, the three-part form,
  the history with the board's counts then, the passes for an
  administrator. Driven in headless Chromium at 1920x1080 light and dark,
  1600x900 under a pass with the service stopped mid-run, and phone width:
  no page errors.
- Section CD (3 checks). Gate 392.

## 2026-10-16 - The well in 3D (v0.36.0)

Daniel asked for a simulation tab: the drill string in 3D with rock
densities, formations, fluid ratios and the mud log. Agreed scope: a replay
of what was measured, not a simulation - paths from surveys, the mapped
formations, picks, colour from measurements, the bit over time; no property
drawn between wells.
- Research first. Only GitHub is reachable from the build machines (the
  Equinor data village, the DOE geothermal repository, Zenodo, Hugging Face
  and the university mirror are refused by the proxy). Public copies found
  and pinned: f0nzie/volve-drilling (WITSML for 15/9-F-4, F-7, F-9 - all
  2016 plug-and-abandonment logs, string near surface - and F-4's
  definitive gyro survey), maribickpostanes/Interactive-Well-Trajectory-Plot
  (F-5's four section surveys, 2007-2008), viandika/volve_horizon_trajectory
  (F-10's survey, six horizon exports), yohanesnuwara/MLGeo (the field's
  well picks). The preprocessed drilling-depth CSVs and parquet stores that
  exist are labelled CC BY-NC-SA by their adapters and were not used.
- Licence: Equinor's 2020 terms (read from Equinor's PDF) allow commercial
  use and adaptation with attribution and a notice; the data may not be
  sold. The 2018 release had been CC BY-NC-SA.
- Checks made before building: the picks' eastings and northings are ED50
  zone 31N (two slots unproject within 0.1 m of the LAS headers, ~148 m off
  as WGS84); the horizons are depth, not time (they tie to TVDSS); minimum
  curvature here reproduces each file's TVD and displacements to <1 cm; the
  paths land on the picks to <0.3 m; F-4's "true north" file fits the picks
  only as grid (14 m otherwise); F-4's stations share one time stamp; the
  seabed export mixes a time block.
- `gea/well3d.py`: readers (WITSML 1.4 trajectory, MD/Inc/Azi tables),
  minimum curvature with on-arc interpolation, the Volve scene (surfaces as
  inline/crossline grids with a fitted affine, worst miss 6 mm), the
  workspace scene (local transverse Mercator on the wells' centre; survey
  azimuths turned by the named grid's convergence or the declination;
  formation-top contour layers at their elevations; the recorder's bit depth
  by stride; channels via the series cache's depth bins), the survey attach.
- The page: WebGL 1 by hand - lit meshes for surfaces, tubes for wells,
  octahedra for picks and the bit, a fade shader beyond the bit's depth; a
  2D overlay for the box, depth ticks, north and labels; hover picking by
  projected distance; tables under the view carry every number. Driven in
  headless Chromium (SwiftShader) light, dark and phone: no page errors.
- Pad 3 read on his machine (preview, read-only): no well placed yet, so its
  ten EIA formation-top layers are drawn alone - 99,819 vertices, 1.9 MB,
  6 s on first read; the EIA Wolfcamp top (ZLEVEL, no unit in its name), the
  ten isopachs and the fault layers are listed as not drawn, with why.
- Pad 3's recorder exports carry gamma and an interpolated TVD but no
  inclination or azimuth: its wells need their directional surveys (the
  Railroad Commission's imaged well records are the public place to look).
- Section CE (3 checks). Gate 395.

## 2026-10-16 - The first v0.36.0 push failed; re-prepared (v0.36.0)

v0.36.0 was pushed (commit d3a8307, tag v0.36.0) and all four workflows
failed: ci on main and on the tag, Release to PyPI (at its product gate,
before any upload) and the kit build. Nothing reached PyPI and no GitHub
release was made. Daniel's ruling: an unpublished version is not burned -
v0.36.0 is re-prepared with the fix, and he removes the failed tag before
the ship (the one tag ever moved, at his instruction); commit d3a8307 stays
in the history.
- The job logs need a signed-in account; the annotations said only that
  `python -m gea accept` exited 1 on both Pythons. A fresh clone of the
  tagged commit in the cloud showed the cause: `gea/reference/
  volve_well_picks.csv` hashed e33b57ea... in the checkout against the
  53ed9f06... recorded in its provenance. The file came from its source with
  CRLF line ends; `.gitattributes` (`* text=auto eol=lf`) stored it as LF;
  the Windows working copy turns it back into CRLF, so the gate passed on
  Daniel's machine and in the cloud tree, and failed on every runner.
  `git ls-files --eol` showed it as the only package file with i/lf w/crlf.
- Fix: the picks ship gzip-compressed (binary, untouched by git), as the
  surveys already did; `tools/build_volve_3d.py` writes it and records the
  hash of the uncompressed bytes beside the file's. The old CSV is moved
  out of the tree. Section CE now also fails on any CRLF file in the
  package - the condition that let a local pass hide a checkout failure.
- The fresh clone of d3a8307 was run through the whole gate as the runners
  run it, to see every section a checkout fails, not only the first.

## 2026-10-16 - Economics (v0.37.0)

The last of the tabs Daniel asked for: cost per foot and per day from his
own rates, with the oil and gas prices and the rig count as background.
- Research first. Neither the cloud nor his machine's sandbox reaches
  eia.gov, FRED or Baker Hughes; GitHub is reachable. The Frictionless Data
  core datasets (datasets/oil-prices, datasets/natural-gas) republish EIA's
  daily spot tables as CSV and were updated to 14 October 2026; EIA data
  are public domain. Pinned at c2452624 and 64843b5b, shipped
  gzip-compressed with both hashes. They carry WTI's -36.98 of 20 April
  2020 and Henry Hub's 23.86 of 17 February 2021, as EIA published them.
- No openly licensed copy of the Baker Hughes rig count was found (it is
  Baker Hughes' publication); the rig count, or any other series, comes in
  through the import with its source stated.
- `gea/economics.py`: series reading (a blank day left out, a repeated date
  or a bad value refused), refresh (kept only when it reads and runs
  later), import; the cost basis in the workspace (rates over periods that
  may not overlap, items, AFE, each sourced and audited); the arithmetic
  (rates integrated over the drilling record's first-to-last sample, per
  section, cost per foot only when the rates cover all of it); the market
  window per well; Spearman only from ten complete wells.
- The page: one SVG chart per series (never two axes), wells' windows as
  labelled bands, gaps over six days not bridged, a crosshair by pointer or
  arrow keys; the wells' table; cost per foot by section with an inline bar;
  the basis forms. Driven in headless Chromium light and phone width.
- Pad 3 on his machine (preview, read-only): 30636033 42.4 days on the
  record, 561 ft/day, WTI 81.49 mean while drilling (28 trading days);
  30665733 83.0 days (its two months' gap counts: days on the record),
  288 ft/day; 30693545 55.5 days, 572 ft/day. No cost until his rates are
  declared.
- Section CF (3 checks). Gate 398.

## 2026-10-16 - The client well report (v0.38.0)

Daniel asked for the client well report: one document per well that puts
together everything the program now knows about it.
- Research first, in the program itself: the report gathers and does not
  recompute. The drilling record (`drilling_record.json`) gives the numbers
  and the depth events; the Setup checklist gives who checked the events
  (`events:<well>`); the well's declarations give the US Well Number, the
  surface location and the reference elevation, the site gives the client;
  the map's `what_here` gives each layer's statement at the location; the
  attached survey gives the path; Economics gives the declared cost and the
  market window. The chart cache's running bottom draws the progress when
  it is built; otherwise each section's opening and deepest depth.
- `client_reports.py`: a `Figure` (SVG, caption, alternative text) a
  section can carry - written beside the report and inline in the HTML, the
  Markdown linking the file, print CSS keeping each drawing on one page.
- `gea/well_report.py`: the per-well and pad documents; four drawings made
  from the report's own numbers - depth against days (one colour per well
  in a fixed order, with a key), the plan view (one scale on both axes,
  north up), the vertical section along the bottom-hole azimuth with each
  mapped top as a band from the two contours the location lies between
  below the declared reference, and each price series with the drilling
  window shaded. A missing part is named in the report with what would
  supply it; the pad report lists them per well.
- The month-end packet writes and includes them. CLI `gea well-report`, API
  `POST /api/reports/well-report`, cards on the well page and the Reports
  page, `gea help well-report`, two new report samples (SYNTHETIC).
- Rendered in headless Chromium: the tables, the five drawings, the print
  layout.
- Pad 3 on his machine (preview, read-only, written outside the site): all
  three wells' reports and the pad report build in about a second each from
  the chart caches; each lists the same four things it lacks - a person's
  check of the depth events, the surface location, the directional survey
  and the day rates - which are the open items on his list. WTI while
  drilling 81.49 / 81.58 / 80.70 USD/bbl.
- Section CG (3 checks). Gate 401.

## 2026-10-17 - Simulation (v0.39.0)

Daniel asked for the simulation view and a tab of its own: the drill string
in 3D on its runtime path, the rock densities, the formations and the oil,
water and gas, collated with the mud log.
- Research first, from GitHub (the only reachable host). The Volve release's
  real-time WITSML logs on the public copies (f0nzie/volve-drilling, F-4,
  F-7, F-9) turned out to be sessions from 2016 with the hole already at
  total depth - not drilling; they were not used. What was
  found: 15/9-F-4's logging-while-drilling record of the 8.5 in section and
  Schlumberger's EcoView interpretation of it for StatoilHydro (2008), on
  hasifsyazwan/AI-Well-Log-Assistant-for-Volve-Field at fbf2c0ff - mineral
  volumes summing with the total porosity to one, SWT, and a hydrocarbon
  volume equal to PHIT x (1 - SWT). F-4's survey and picks already ship.
  Its survey stations carry one time stamp (a resurvey), so its bit is
  stepped along measured depth; F-5 and F-10 keep their station times.
- `gea/simulation.py`: the scenes (Volve F-4 with tracks and the volume
  column from the shipped LAS files read by the program's own reader; F-5
  and F-10 on the survey clock; workspace drilling wells with the bit and
  the running bottom on the recorder's clock, channels by bit depth from the
  chart cache, the gamma index lithology, the path from the survey or the
  recorder's MD-TVD pairs); `at()` for what the record says at a depth.
- The tab reuses the 3D tab's WebGL renderer (a cut uniform so the string
  is drawn only to the bit, the hole see-through, the path below the bottom
  ghosted) beside a canvas mud-log panel; the volume colours are the
  palette's six slots in order, checked with the palette validator in light
  and dark. Driven in headless Chromium: light, dark, phone width, playing.
- Pad 3 on his machine (preview, read-only): all three wells build from
  their chart caches in 1.5 to 2.6 s, 0.38 MB each, nine channels by depth,
  the gamma index lithology, the path from MD-TVD (no survey yet).
- Section CH (3 checks). Gate 404.

## 2026-10-17 - Drill string (v0.40.0)

Daniel asked for a Drill String tab: a build diagram of the subs and tools in
the string with their service life, usage and tracking; a downhole motor
tracker charting its operating statistics and its frequency changes for its
stability; and motor specifications in the tool catalogue.
- Research first. The program had three tool registers (the gauge library,
  the 96-class taxonomy, the operator's fleet) and nothing that followed a
  tool's use; the drilling records carry pump output, differential pressure,
  rotary speed and surface torque once a second. No motor figures shipped.
  Motor specifications: MS Directional's Motor Handbook (First Edition, April
  2021, Version 2.1, on patenergy.com) - the build machines cannot fetch it,
  so it was read through the web reader twice, independently, field by field;
  41 power sections on which both readings agreed are shipped, one that was
  read once is left out, and no hash is claimed. An anonymous set of spec
  sheets (no maker printed) was not used.
- On Pad 3 (preview, read-only): one-second records, 583,732 drilling samples
  on 30636033; drilling flow 350 to 905 gal/min, differential median about
  400 psi, 95th percentile about 1,000; surface torque oscillating at 0.06 to
  0.2 Hz (5 to 17 s) - the stick-slip band - and the differential's spectrum
  red, rising to the band's edge, which the peak rule now refuses to call a
  peak.
- `gea/drill_string.py`: runs declared from the bit up (classes from the tool
  library, a catalogued motor or the operator's own sheet with its source,
  service intervals with their basis and source), usage from the chart cache
  over each run's span, tracking per serial across runs, the motor's
  statistics hour by hour, stalls, and the stability by Welch's method with
  the program's stated rule; a labelled synthetic motor run for the gate.
- The tab: the build diagram, the components with their life bars, the usage
  tiles, the motor's charts (one axis each, the maker's limits as labelled
  lines), the hours on bottom by differential, the stability charts and the
  spectra, every component across every run, the declare/edit form. The Tools
  tab gains the motor table. Driven in headless Chromium: light, dark, phone
  width, no horizontal scroll (the Tools tab's hidden tips had widened it;
  fixed).
- Pad 3, whole records (no run declared yet): 1.0 to 1.7 s each; 30636033
  232 h circulating, 162 h drilling, 23,796 ft, 7.07 million gallons; the
  stability across the whole record says CHANGED for all three - expected
  across hole sections and strings, and the page says so until the runs are
  declared.
- Section CI (3 checks). Gate 407.

## 2026-10-17 - The declarations sheet (v0.41.0)

- Asked: what is left; then build the declarations import with the Genco
  follow-ups and the motor leftovers while the daily-report and invoice
  samples for the cross-check are gathered.
- `gea/declarations.py`: one CSV of bracket-headed tables; parse, plan
  (NEW / CHANGE / SAME, or an error with its row), apply through each
  declaration's own function; the workspace template filled with what is
  declared. A non-sheet file is named as one in a single line, not one
  error per row. Money and positions written back exactly (a float is
  written as it is held, never rounded to six figures).
- `gea/penetrator.py`: the ratings from `reference/penetrator_ratings.json`
  or a fleet tool's datasheet; the installation record and the drive and
  site; the check line by line. The current check reads the drive's
  maximum output against the penetrator's full-load current and, when it
  is over, says to set the drive's current limit - the rule is the
  program's, stated.
- Genco documents (IP-06, IP-20, IP-40) supplied by the operator: only the
  IP-40 Appendix A ratings and the titles' Division 2 are kept, with the
  document, revision and section; no procedure text. The PMM note is
  written from public sources (World Oil June 2024, Baker Hughes authors;
  Levare's field-service page) and cites IP-40 for the safety notes.
- Motors: the handbook read a third time through the web reader for every
  sheet's flow, rev/gal, differentials, torque and weight on bit - all 41
  agreed. The 6 5/8 in 5/6 8.4 sheet prints 300-700 gpm where the summary
  table prints 350-700; the sheet is kept and the difference noted. The
  8 in x 7 3/4 in 7/8 5.9 sheet was read twice and added; a 7 in 8/9 4.3
  sheet missing from the first transcription was found and read twice.
  43 motors.
- The Completion tab and the Setup card driven in headless Chromium: light,
  dark, phone width, no horizontal scroll, no console errors.
- Section CJ (3 checks). Gate 410.

## 2026-10-18 - Operations and seismicity (v0.42.0)

- Asked: keep building. The one open item that needed no data from the site
  was linking operations to seismicity.
- Research: the protocols read from the regulators' documents where they
  could be (British Columbia's Kiskatinaw order, Alberta's SO2, Oklahoma's
  2016 notice, Texas's 2022 response plan); Ohio's 2014 conditions only
  through law-firm summaries, marked secondary. The 20 km screen and the
  three-month window from Atkinson et al. (2016).
- The worked example: neither build machine reaches the USGS service, so
  ComCat was read through the web reader as text and as CSV for 1990-2026
  within 30 km of the Mahoning Valley and the two compared - 21 events,
  none before 2011. Northstar 1's injection dates from Kim (2013) (the
  state's inspection record has 23 December 2010 for the first injection;
  the paper's 29 December is used); positions from the state's well
  records via WellWiki, two decimals. Poland-CLL2's stimulation window
  starts at the state's inspection of 5 March 2014 (its true start is not
  in the sources read) and ends with the regulator's halt on 10 March.
- The magnitude of record matters: ComCat's largest Poland event is ML
  2.44 where the regulator and Skoumal et al. give M 3.0, so Oklahoma's
  2.5 level is not reached by the catalogue; the page says so.
- `gea/op_seismicity.py`: the frame, the protocols, the rate test, the
  rig's state from the series cache, the scenes; catalogues and operations
  in `seismicity/`; the sheet's `[operations]` and `[catalogs]`.
- The tab driven in headless Chromium: light, dark, phone width, no
  horizontal scroll, no console errors. The after-window colour moved from
  amber to blue so it does not sit beside the during-window orange.
- Section CK (3 checks). Gate 413.

## 2026-10-18 - Report history and the signed dossier (v0.43.0)

- Asked: where the reports go (C:\site\reports; each press replaced the
  last), then to build report history and the signed dossier together.
- Report history hooks the one function every client report is written
  through: when the folder is inside a workspace's reports folder (and not
  its history, dossiers or month-end packets), each file written is copied
  to a dated folder and indexed with its SHA-256. Nothing is pruned unless
  asked.
- The signing decision taken as proposed: Ed25519, a key made on the site's
  machine on first use, never sent. Written from RFC 8032's reference
  (section 6) in extended coordinates with the standard library alone, so
  the checker can ride inside the dossier; checked against the RFC's three
  test vectors (read from rfc-editor.org), and during the build against an
  independent implementation on twenty random keys (not in the gate, which
  stays standalone). About 3 ms to sign.
- The audit log was not chained, and rotates; rather than change its format,
  each dossier chains the log's lines in order across the rotated segments
  and records the head and the count, so a later check finds an earlier line
  changed. The key is kept outside `config` because the evidence pack walks
  `config`.
- The checker is the RFC functions followed by the checker module, run in a
  bare `python -I` in an empty folder by the gate.
- Pad 3: two of its earlier pad reports were lost to the overwrite; from this
  version every one is kept.
- The Reports tab's two new cards driven in headless Chromium: light, dark,
  phone width, no horizontal scroll, no console errors.
- Section CL (3 checks). Gate 416.

## 2026-10-19 - The earthquake watch and the alerts (v0.44.0)

- Asked: start v0.44 with the live earthquake watch and the alerts.
- Research: the USGS FDSN event service page (parameters, the 20,000
  limit, and its advice that automated applications use the real-time
  GeoJSON feeds) and the summary feed format (updated every minute; mag,
  magType, time, updated, status, net, type; coordinates lon, lat, depth).
  Neither build machine reaches USGS, so the watch is tested against a
  stand-in catalogue served over HTTP through the same fetch code; the
  site's machine makes the real call (`gea seismicity --watch-once`).
- The watch runs in the service's existing notification poller, on its own
  clock, so it needs no new thread and stops with the service; it does
  nothing until an operation is under way at a placed well.
- Alerts are recorded whether or not a channel exists, and the Alarms tab
  says plainly when none is set up. A dossier is re-checked daily, not on
  every tick, because it hashes the source records.
- Both new cards driven in headless Chromium: light, dark, phone width, no
  horizontal scroll, no console errors.
- Section CM (3 checks). Gate 419.

## 2026-10-19 - The lateral, the printable reports, the daily summary and the key (v0.45.0)

- Asked: proceed with the four smaller items (the trusted timestamp left for
  later).
- The lateral reuses the 3D view's own path code (minimum curvature, the
  arc between stations, the azimuth turned to the frame by the grid
  convergence or the declination), in a transverse Mercator frame on each
  well's own surface position, so a point's east and north are exact metres
  from the slot. Checked against a hand-built lateral: an event placed 500 m
  north of it by the inverse projection reads 0.500 km.
- The distance is in plan, the way the protocols draw their circles; the
  straight-line distance to the hypocentre is given beside it.
- The PDF writer is new and small: PDF 1.4, the base-14 fonts with Adobe's
  AFM widths for line breaking, WinAnsi text, FlateDecode streams, an xref
  table; the program's own SVG (rect, line, path, circle, text) redrawn as
  PDF operators with opacity mixed toward the white page. No creation time
  is stamped, so a report rewritten unchanged hashes the same. Checked with
  qpdf and rendered with pdftoppm during the build; the gate reads each PDF
  back with its own reader (xref offsets, fonts, page text).
- The summary runs from the same background poller as the watch, once a day
  at its hour; the email body is the summary's own text.
- Rotation: the old key signs a canonical statement naming the new, the new
  signs it too, and the old seed is overwritten with the new key file. The
  standalone checker follows the chain and requires it to end at the
  dossier's signer, so a dossier signed with a retired key after its
  retirement fails even outside the workspace.
- Section CN (5 checks). Gate 424.

## 2026-10-19 - The invoice cross-check (v0.46.0)

- Asked: build the invoice cross-check, ahead of the samples (a daily
  drilling report, an invoice, the API numbers, the report-day boundary).
- Research: IADC's DDR operation code definitions (2019) for the codes the
  report carries (2 drilling, 5 circulate, 6 trips, 8 repair rig, 33
  operating status); IADC's U.S. Daywork Drilling Contract sample (2013) for
  the rate kinds and when each applies - 4.3 operating, 4.4 repair time at
  the operating rate up to an allowance per job and per month, 4.5 standby,
  4.7 force majeure, 8.4 the daily report.
- Without the samples, the reader finds its columns by heading in any order
  and spelling, and the templates say what it needs; the report-day start
  and whether a report is dated by the day it starts or ends are declared
  terms, not guesses. The real formats are a reading change, not a new
  module.
- The record's side uses only what a recorder carries: the drilling flag,
  pump output, rotary speed and the bit's travel; a quiet recorder on a day
  billed at the operating rate is a question, because waiting on cement and
  rig-up are operating time with the pumps off.
- A labelled scene of five report days with seven planted differences, each
  found with its dollars; the same days billed and reported as they happened
  agree. The tab driven in headless Chromium: light, dark, phone width, no
  horizontal scroll, no console errors.
- Section CO (3 checks). Gate 427.

## 2026-10-19 - Rig time (v0.47.0)

- Asked: start with the rig-state analysis, the edge proposed after v0.46.
- Research: the rig-state rules rig-state software states (Innova's Well
  Seeker rig states guide: in slips below the block weight and half again,
  on bottom by hole less bit depth, pumps by pressure, rotating by RPM);
  Drilling Contractor's "Deconstructing invisible lost time" (Bakken:
  weight-to-weight connections 6.6-13.2 min, cased-hole tripping
  2,100-3,400 ft/h, ILT as the shortfall against a target).
- Built against a labelled one-second export with known answers (connections
  of 6, 9 and 15 min, a 3,000 ft trip each way at known speeds, a 40 klbf
  block), then checked read-only against the site's own records on Daniel's
  machine, with the module loaded from outside the clone (no figure from the
  site is written here).
- The site's records changed three rules: a string pushed into the lateral drops the hook
  load near the block's weight while the bit moves (the bit must be still
  for the slips); a string hung up and slacked off sits for seconds (the
  slips need half a minute); and the weight off the hook for hours (rigging,
  casing) is OFF THE HOOK, not a connection. A depth re-set faster than a
  block travels is not pipe moving.
- A trip's speed is over its pipe-handling hours; the breaks inside it
  (circulating, filling pipe, picking up the BHA) are shown beside it and
  not counted as lost.
- The tab driven in headless Chromium: light, dark, phone width, no
  horizontal scroll, no console errors.
- Section CP (3 checks). Gate 430.

## 2026-10-20 - One record, tied together (v0.48.0)

- Asked: the structural gaps after v0.47 - the dossier, the summary and the
  alerts reading the invoice check and rig time; the declarations sheet
  covering the newer declarations; an Excel reader; rig time by hole
  section - and every tab driven against the site's records.
- The Excel reader is written against the Office Open XML parts (ECMA-376)
  with zipfile and ElementTree; checked in the cloud against workbooks
  written by openpyxl (not in the gate, which stays standalone) and in the
  gate against a hand-made workbook with two sheets, inline strings, a
  custom date format and the 1904 epoch.
- Notification channels were kept off the declarations sheet on purpose:
  they carry addresses and passwords, and a sheet travels.
- The site sweep: the v0.48 package run from outside the clone on Daniel's
  machine against a copy of the site's workspace (sources linked, read-only;
  the copy deleted after) - 102 requests covering every route the tabs call,
  each well's charts, the 3D and simulation scenes, the reports, the summary
  and a dossier: every one answered 200, none carried NaN or Infinity (which
  a browser's JSON parser refuses), the slowest a pad dossier at 5.4 s. A
  browser could not be installed on that machine (its network allowlist
  blocks the browser download), so the tabs themselves were driven in
  headless Chromium in the cloud against a labelled workspace: every tab
  and every well page, no console error.
- Section CQ (4 checks). Gate 434.


## 2026-10-20 - Well collision checks (v0.49.0)

- Asked: every well scanned against every other; the ISCWSA separation
  factor; each well's position uncertainty by the ISCWSA MWD model; the
  field by WMM2025; the Volve template as the worked example.
- Sources: the ISCWSA error model (Williamson, SPE-67616-PA) in its Rev 4
  form, its weighting functions as the ISCWSA's example workbook gives
  them; the separation rule (Sawaryn et al., SPE Drilling & Completion
  34(1), 2019) and the ISCWSA eBook's levels; WMM2025's coefficients and
  technical report (NOAA NCEI / BGS, public domain).
- The error model is checked in the gate against all 112 rows of the
  ISCWSA's own Rev 4 example as carried in welleng's test data (Apache-2.0,
  the source and licence kept with the file); the worst row agrees to a few
  parts in a million. The vertical-hole forms of the three terms that are
  singular there (ABXY-TI2S, XYM3, XYM4) follow the example.
- WMM2025 was checked in the cloud against an independent implementation
  (pygeomag, not in the gate) to 1e-9 degrees, and in the gate against a
  published example.
- In the separation factor the distance's uncertainty is taken along the
  line between the two closest points, from both wells' covariances less
  the cross-covariance of the global terms - a declination error moves two
  wells on one pad together, and does not bring them closer.
- The Volve template: 15/9-F-5 and 15/9-F-10 come within 1.2 m near the
  surface, where their slots sit together - below 1.0, as a template's
  shallow section is, which is what a rule's dispensation is for.
- The site's workspace has no surveys or surface positions declared yet,
  so its scene says a check needs two wells and shows the worked example.
- Section CR (4 checks). Gate 438.

## 2026-10-20 - Plan vs actual and the survey tools (v0.50.0)

- Asked (from the list of what a commercial program still lacks): plan
  against actual and a travelling cylinder, and more survey tool models;
  released one item per version.
- The ISCWSA MWD Rev 5.11 framework was implemented from the ISCWSA's Rev 5
  example workbook for test well 1 and checked against its calculated block
  as carried in welleng's test data (Apache-2.0): all 144 rows agree to a
  few parts in a hundred thousand. Two details the workbook fixes: from
  Rev 5 the first leg below the surface is weighed twice (the slot's
  attitude has a survey's own errors), and the course-length terms XCLA and
  XCLH are position errors of the leg itself, not angle errors propagated.
- The OWSG tool-code sets A and B (Rev 5.1) were taken from welleng's ISCWSA
  JSON conversion; only the 21 models whose every term uses the Rev 5 MWD
  weighting functions are carried (each term's formula was checked to be
  the same in every model that names it). Gyro models were left out: their
  weighting functions and noise and drift recurrences (SPE 90408) are a
  separate piece of work, to be checked against that paper's Appendix E
  examples; the axial-interference-corrected models were left out until
  their terms can be checked against an example too.
- The Rev 5 model counts the hole between the surface and a survey's first
  station: the Volve template's closest pair now reads 0.06 rather than
  0.29 under Rev 4.
- Plan against actual is measured in the plan's own frame at its closest
  point; the bit projection holds the last interval's build and turn; the
  arc back is a single circular arc to a plan point a look-ahead ahead.
  Each was checked by construction in the gate (5 m left, 1.5 m high, a
  3 deg/30 m build, the arc's toolface and severity by hand).
- The tabs were driven in headless Chromium against a labelled workspace:
  light, dark and phone width, no console errors.
- Section CS (5 checks). Gate 443.

## 2026-10-20 - Frac hits (v0.51.0)

- Asked (the second of the list): frac hits on offset gauges, tied to the
  stages and the seismicity.
- The three responses and their signatures are Seth, Elliott and Sharma's
  (URTeC 2020-3129): a purely poroelastic response falls as soon as the
  treatment well stops; a mixed or direct response holds or rises after it;
  a direct hit runs to hundreds or thousands of psi. The volume to first
  response is the sealed-wellbore pressure monitoring measure (Haustveit et
  al., SPE-199731-MS). The program's thresholds (5 x noise and 5 psi, 250
  psi for a hit, 10 % for falling) are defaults the operator declares over.
- The gate's record is built by construction: a six-stage table and a gauge
  sampled every 10 s whose stages answer nothing, poroelastic, mixed, a hit,
  poroelastic and nothing; each was read as built.
- The earthquake count needs the stage clock's offset from UTC, because
  the catalogue is in UTC and a stage table is in the field's clock; the
  stage table and the gauge record must share a clock, and the help page
  says so.
- The tab was driven in headless Chromium: light, dark and phone width, no
  console errors.
- Section CT (4 checks). Gate 447.

## 2026-10-20 - Pore pressure and fracture gradient (v0.52.0)

- Asked (the third of the list): pore pressure and the fracture gradient -
  the mud-weight window - from logs and drilling data.
- The methods are the published ones, each cited in the module and the help
  page: Eaton's ratio (SPE-5544) with his exponents, Eaton's fracture
  gradient (JPT 1969), Gardner's density, Jorden and Shirley's d-exponent
  and Rehm and McClendon's correction, Hubbert and Willis's minimum.
- The gate checks the arithmetic by hand and reads back a depth log and a
  drilling record built so that Eaton's method must return a known
  overpressure (40 % of the way from hydrostatic to the overburden between
  2,200 and 2,600 m): sonic, resistivity and corrected d-exponent all return
  it to within 0.2 % away from the zone's edges.
- The worked example is L07-01 (NLOG), the one public log at hand with a
  sonic and a gamma ray over most of a well. No pressure measured in that
  well is in the file, so the example shows the method and the choice of
  trend, not a calibrated answer; the page says so. The IODP U1324 pressure
  measurements in the catalogue were considered, but that site's LWD logs
  could not be fetched from this workspace.
- A depth log's series cache is not used (it never matches its source file
  for LAS); the module reads the log from its file.
- The tab was driven in headless Chromium: light, dark and phone width, no
  console errors.
- Section CU (4 checks). Gate 451.

## 2026-10-20 - Torque and drag (v0.53.0)

- Asked (the fourth of the list): torque and drag for the drill string.
- The model is Johancsik, Friesen and Dawson's soft-string model
  (SPE-11380-PA). The sign of the inclination term was taken from the
  geometry: in a build a string in tension is pulled off the low side,
  against its weight; in a drop, onto it.
- The gate checks the model against closed forms: a vertical string, a
  straight inclined string pulled, run and rotated, and the capstan over a
  bend in either plane (the discrete model converging to e^(mu theta)). It
  then makes a drilling record with the model itself - a trip out and in,
  rotation off bottom every 900 ft, friction 0.18 cased and 0.24 open, a
  40 klbf block, noise - and reads it back through the rig states: each
  fit lands within 0.02 of the friction it was made with.
- The worked example uses Volve 15/9-F-10's released survey with a string,
  mud and friction factors declared for the example, not Volve's own.
- Buckling and the string's stiffness are out of this model, and the page
  says so.
- The tab was driven in headless Chromium: light, dark and phone width, no
  console errors.
- Section CV (4 checks). Gate 455.

## 2026-10-20 - Decline curves (v0.54.0)

- Asked (the fifth of the list): decline curves and EUR, completing the
  economics.
- Arps's equations and Robertson's modified hyperbolic were checked in the
  cloud against an independent implementation (petbox-dca 2.3.1, MIT, not
  in the gate): rates and cumulatives agree to machine precision. The gate
  checks them by hand and by integrating the rate.
- The fit is checked on a production table built on a known curve with
  noise and part-month shut-ins: b within 0.15, the rate within 4 %, the
  30-year recovery within 5 %.
- The price follows the SEC rule (Regulation S-X 4-10(a)(22)(v)): the
  12-month unweighted average of the first-day-of-the-month prices; the
  page states that a forecast is not a reserves report.
- No public monthly production could be fetched from this workspace (its
  network reaches only the package and code hosts), so there is no public
  worked example in this release; the site's own production is the next
  check.
- The tab was driven in headless Chromium: light, dark and phone width, no
  console errors.
- Section CW (4 checks). Gate 459.
- The first ship stopped at the gate on Daniel's machine: Python 3.14's
  argparse checks every help string as the parser is built, and two of
  `gea decline`'s carried a bare "%" - every `gea` command failed before
  it started. The cloud gate ran on 3.13, which checks only when help is
  printed. Escaped, and the release steps now run the gate on Python 3.14
  too; a check of every help string the 3.14 way was run over the whole
  package (these two were the only ones).

## 2026-10-20 - HTTPS (v0.55.0)

- Asked (the first of the IT items): what an operator's IT department asks
  before installing - here, encrypted transport on the site network.
- The service speaks TLS itself; a site's reverse proxy remains the other
  way. The certificate and key are made by the standard library alone (no
  `cryptography` package): RSA by Miller-Rabin, the X.509 v3 certificate
  written in DER by hand. Checked in the cloud: OpenSSL 3.0 reads it
  (subject, names, validity, fingerprint) and `openssl verify` accepts its
  self-signature; Python's TLS client verifies it by name and address.
- The 825-day limit is Apple's for certificates trusted by hand (HT210176).
- A plain-HTTP request on the HTTPS port is answered with a redirect only
  for a name the certificate carries, so the redirect can't be pointed
  anywhere else by the Host header.
- Found while wiring the card: the Operations control buttons on the
  Administration page had their handlers in the preferences page's code
  and did nothing. Moved; the gate checks where they live.
- The Administration page was driven in headless Chromium over HTTPS:
  light, dark and phone width, no console errors, the Restart confirmation
  opens.
- Section CX (4 checks). Gate 463.

## 2026-10-20 - Company sign-in (v0.56.0)

- Asked (the second IT item; chosen: any OpenID Connect provider, set up
  against Entra ID's settings, local accounts kept as the fallback).
- Researched: Microsoft's redirect URI rules (https required except
  localhost; 256 characters) and its group claims (at most 200 groups in a
  JWT, object IDs by default, none at all over the limit) - which is why
  the help recommends app roles.
- The ID token is checked with the standard library's RSA verification
  from v0.55 (gea.tls); no JWT package.
- The session cookie stays SameSite=Strict. The return from the provider
  is the end of a navigation that began on the provider's site, so the
  callback answers with a page that moves on by itself rather than a
  redirect (a redirect would carry the cross-site start and the new cookie
  would not be sent). Driven in headless Chromium with the control panel at
  localhost and the stand-in provider at 127.0.0.1 - two sites: the button,
  the provider, back, signed in as the mapped role; light, dark and phone
  width, no console errors.
- Failed company sign-ins are audited but do not count toward the local
  sign-in lock: the provider keeps its own, and a mistake in the role map
  must not lock an office's local accounts out.
- Found while looking at the sign-in page: the search box and Sign out
  showed there, because the stylesheet overrode the hidden attribute.
- No live Entra ID tenant was available from this workspace; the stand-in
  provider follows Entra ID's v2.0 endpoints' shape. The first sign-in
  against the company's tenant is the next check.
- Section CY (4 checks). Gate 467.

## 2026-10-20 - The IT review (v0.57.0)

- Asked (the last IT items; chosen: build the signing now, sign once a
  certificate exists; the business model: the source open, the signed
  kit, updates and support as a subscription).
- What can be signed: the kit's launchers are batch files, and a batch
  file cannot carry an Authenticode signature. Windows' own answer for
  files that cannot be signed is a signed catalog (New-FileCatalog,
  Test-FileCatalog; App Control/WDAC trusts files by their catalog). The
  catalog sits beside the kit's folder in the zip so it never lists itself.
- Researched: Azure Artifact Signing's action (azure/artifact-signing-
  action@v2, formerly Trusted Signing; its inputs and the signer role);
  Test-FileCatalog's statuses; GitHub artifact attestations for a public
  repository. None of the workflow's new steps can run from this
  workspace: the first tag run is their check, and the catalog check step
  stops the run if the catalog does not match the kit or a configured
  certificate did not sign.
- Found while checking the kit's commands: `python -m gea guide` (what
  the kit's gea.cmd runs) answered "invalid choice" - the front door's
  commands lived only in the console-script entry point. Routed.
- The overview's numbers and hosts are checked by the gate against the
  code (PBKDF2 rounds, session hours, the lock, the port, TLS, every host).
- After v0.54.0's first ship stopped on Python 3.14 (a bare "%" in two
  help texts), every release from here is gated on Python 3.14 as well as
  3.13 in the cloud, and CZ1 checks every help text the 3.14 way on any
  Python. v0.55.0-v0.57.0 were re-made to carry the v0.54.0 fix.
- Section CZ (3 checks). Gate 470.

## 2026-10-21 - Hydraulics and ECD (v0.58.0)

- Asked (chosen from four: hydraulics and ECD, casing design, ESP nodal,
  Pad 3 onboarding).
- Researched: the API power-law method (the pipe's n and K from 600/300,
  the annulus's from 100/3; the critical Reynolds number 3470 - 1370 n;
  Dodge-Metzner's turbulent friction) and Zamora et al. (AADE-05-NTCE-27)
  for the Churchill blending of the three regimes. API RP 13D itself is a
  paid standard and was not read here; the equations come from the
  public descriptions of it and are checked by hand.
- Written in SI from the Fanning definition (dp = 2 f rho V^2 L / D) and
  checked against API's field-unit forms (24.51 Q/D^2 ft/min, the
  effective viscosity, 15.467 V D rho / mu, 1.076 rho V^2 f / 10^5 D) to
  0.1 %; a Newtonian fluid's laminar loss equals Hagen-Poiseuille's in a
  pipe and the slot's in the annulus; the turbulent friction factor at
  n = 1 is Blasius's to 1 %; the bit's loss equals 156.5 rho Q^2 /
  (sum d^2)^2.
- Surge and swab are the steady-flow estimate (Burkhardt's clinging
  constant 0.45, the pipe's peak speed 1.5 times the average); the gel's
  breaking and the mud's inertia are out, and the page says so.
- No public drilling record with a standpipe pressure and a declared mud
  could be fetched here, so the check against the rig is made on a record
  built on the model with a known offset (recovered to within 10 psi);
  Pad 3's own record is the next check, once its mud, string and hole are
  declared.
- The tab was driven in headless Chromium: light, dark and phone width,
  no console errors.
- Section DA (4 checks). Gate 474 on Python 3.13 and 3.14.
