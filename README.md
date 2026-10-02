# GEA-Program

Downhole gauge monitoring for production operations: the program takes a well's
historian data and returns the reports a client engineer reads, in the outline
and language of a production-operations scope of work.

Every number in every report is recomputed from the source data at generation
time. Reports are gated for vocabulary: the program's internal register never
reaches a client document. Nothing unmeasured is ever reported as met.

## What it does

- **Canonical measurement record** (`gea/sample_record.py`): one record shape for
  every sample - tag, UTC timestamp, value, unit, quality flag, the rule and limit
  that fired, source layer, ingest timestamp. Quality rules: RANGE / ROC /
  FLATLINE / SPIKE / STALE / GAP; ranges from the gauge datasheet, cited.
- **Gauge drift and reconciliation** (`reconciler.py`, `drift_monitor.py`): the
  live pressure series against the well baseline; classification with the
  numbers beside it; scheduled evaluation log; CURRENT / STALE; re-fit change
  log with before/after coefficients; approve -> apply -> re-evaluate; SLA clocks;
  annual re-fit cap.
- **Accuracy statement** (`accuracy_statement.py`): MAPE with a seeded bootstrap
  90 % CI; the band read at the conservative end of the interval.
- **Well-test validation** (`well_test_validation.py`): stable-period detection
  with the client-agreed criteria in a file (hashed on every report), reason
  codes naming the failing value, virtual rates, two-level approval trail.
- **Alarm management** (`alarm_engine.py`): setpoint / deadband / on-delay state
  machine, event log, ISA-18.2-style KPIs printed against their targets.
- **Model cards** (`model_card.py`): one card per model - inputs with sources,
  settings with basis, calibration data with provenance, recomputed evaluation,
  limitations, re-fit history, component hashes.
- **Store-and-forward** (`store_forward.py`): 72 h edge buffer, chronological
  rate-controlled replay, duplicate suppression, per-record latency.
- **Configuration versioning, SBOM, monthly SLA, FAT/SAT**
  (`config_versioning.py`, `sbom.py`, `sla_report.py`, `fat_sat.py`).
- **Live protocol ports** (`live_ports.py`, `wits0.py`, `witsml.py`, `opcua_port.py`,
  `mqtt_port.py`, `modbus.py`): WITS Level 0 from the drill floor (TCP connect,
  TCP listen, serial), WITSML 1.4.1 stores (read-only, polled), OPC UA (read and
  subscribe), MQTT (number, JSON, Sparkplug B), Modbus TCP; every message becomes
  a measurement record with its source timestamp, ingest timestamp and quality
  flag; units declared on the map are converted; every session can be recorded
  and replayed; in-package simulators for WITS0 and WITSML. See *Live data*.
- **The patch panel** (`patches.py`): supervised connections that stay up -
  reconnect with backoff, heartbeat, latency, per-tag last values, records to
  disk as they arrive, several sources folded into one stream by priority.
- **Dashboard** (`dashboard.py`): tiles, well ranking, alarm wall, drill-down to
  every report; light and dark; status is always an icon with a label.
- **The dashboard as the door** (`workspace.py`, `jobs.py`, `service.py`,
  `web/app.html`): one folder per site that the client owns; a standard-library
  HTTP service that serves the page, the reports and a JSON API; every action
  a job running the program's own command with its log kept; accounts with
  roles; an audit log with the hash of every input. See *Serving the dashboard*.
- **Survey track** (`earth_model.py`, `strata_join.py`, `inverse_engine.py`,
  `blind_harness.py`, `rock_inventory.py`, `forward_model.py`, ...): strata
  property estimation over a public co-located library, blind-scored on every run.

## Quick start

```
pip install -e .
gea quickstart                 # a real catalogue well -> its reports -> the dashboard; the KTB strata survey
gea survey mywell.las          # a LAS file in, one strata report out
gea dashboard --catalog-well volve_f12_f14_production_excerpt:15/9-F-12:10000 --td 10500 --out dashboard
gea client-report --report accuracy --out client_report
gea model-cards --out model_cards
gea sbom --out sbom
gea accept                     # the product gate (237 checks)
gea help drift                 # the help library, by the job (14 pages; the same text is on every dashboard page)
gea guide                      # the click-by-click tester guide (docs/TESTER_GUIDE.md)
gea gui                        # the desktop window (pip install "gea-program[desktop]")
gea workspace --path C:\site --action init --name "Pad 3"     # a site folder
gea serve --workspace C:\site                                 # the dashboard as the door: http://127.0.0.1:8765/
gea doctor --workspace C:\site                                # which code runs, can it serve, with every fix
gea files --workspace C:\site --action list --root historian  # the import roots, detection by content, import, export, packs
gea transient --file historian.csv --params params.json --out pta   # shut-ins and build-up analysis with a band
gea swaps / gea certificates / gea notify / gea housekeeping / gea loadtest   # instruments, notification rules, site upkeep
```

`gea` is the front door (`gea/cli.py`); every other subcommand passes through to
`python -m gea`. PATH-proof form: `python -m gea.cli ...`. Open `dashboard/index.html`.

## Live data

The engine reads live data through five protocol ports. All are **read-only**,
all map only what the site declares (a tag map the client owns; an empty map
is declined), and all write every received message to a JSON-lines recording
that `--replay` turns back into records without a connection - which is how a
site session is reproduced offline and how the mapping logic is tested.

```
pip install "gea-program[live]"                       # asyncua, paho-mqtt, pymodbus, pyserial (or one extra at a time)
gea wits0  --write-example-config wits0.json          # drill floor: transport tcp | listen | serial; item codes -> tags, units
gea wits0-sim --port 5001                             # a WITS0 sender to rehearse with (no rig needed)
gea wits0  --config wits0.json --seconds 60 --record floor/session.jsonl --out floor --stream-csv floor/stream.csv
gea witsml --write-example-config witsml.json         # a WITSML 1.4.1 store: url, uids, mnemonics -> tags; credentials by env name
gea witsml --config witsml.json --seconds 60 --out store
gea opcua  --write-example-config opcua.json          # edit: endpoint, security, credential env names, nodes
gea opcua  --config opcua.json --seconds 60 --record opc/session.jsonl --out opc --stream-csv opc/stream.csv
gea mqtt   --write-example-config mqtt.json           # edit: broker, TLS, credential env names, topics
gea mqtt   --config mqtt.json --replay mq/session.jsonl --out mq_replay     # no broker needed
gea ingest --file floor/stream.csv                    # a stream CSV feeds the historian port like any other
```

| port | transport | quality | timestamps |
|---|---|---|---|
| `wits0` | WITS Level 0 frames (`&&` ... `!!`, four-digit item codes) over TCP connect, TCP listen or serial; record-01 dictionary built in | numeric GOOD; sentinels (-9999, -999.25) and non-numeric -> GAP with the reason | items 0105/0106 (date, time) when present, else arrival; ingest at arrival |
| `witsml` | WITSML 1.4.1 SOAP store: GetVersion, GetCap, GetFromStore on one log object, rows newer than the last seen at each poll | numeric GOOD; the store's nullValue and empty fields -> GAP | the index curve of a time log; arrival for a depth log |
| `opcua` | opc.tcp, read + subscribe; Basic256Sha256 SignAndEncrypt when a certificate and key are given | StatusCode severity bits: Good -> GOOD, Uncertain -> STALE, Bad -> GAP (value withheld) | source timestamp, else server timestamp, else arrival |
| `mqtt` | MQTT v5 / v3.1.1, TLS optional, QoS per topic; `+`/`#` wildcards | number: GOOD or GAP; JSON: value/time/quality paths; Sparkplug B: `is_null` -> GAP, `Quality` != 192 -> STALE | JSON time path, Sparkplug metric timestamp, else arrival |
| `modbus_g6` | Modbus TCP, user-supplied register map with a citation | read failures -> GAP | arrival |

Units: a mapping may declare `unit_in` (what the wire carries) beside `unit`
(what the tag catalogue uses); known pairs (pressure, temperature, length,
force, flow, density, torque, volume) are converted before scale and offset,
and a pair the program cannot convert is declined when the map loads.
Credentials never sit in a map: the map names the environment variables that
hold them. The example maps are in `docs/examples/`.

**Patches** keep a source connected: a patch names a protocol, its map, the
well it feeds, a priority and a staleness limit; the service keeps every
enabled patch up, reconnects with backoff (2 ... 60 s) when the source drops,
keeps a heartbeat (state, last sample, samples per minute, latency p50/p95,
reconnects, last error, the last value of every tag), and appends every record
to `wells/<id>/records/live/patch_<name>_<day>.records.csv`. The refresh folds
the record files of every patch on a well into one stream (higher priority
wins a tie) and reports it beside the well; a drill-floor stream with no
downhole gauge stations gets quality and alarms, and its drift report is
marked not applicable rather than invented. The Patch panel page adds,
starts, stops, enables and edits patches and shows their live values.

## Serving the dashboard

The command line is the engine; the dashboard is the door. A client site is one
folder, the **workspace**, which the client owns and backs up like any project
folder:

```
gea workspace --path C:\site --action init --name "Pad 3"
gea workspace --path C:\site --action add-catalog --entry volve_f12_f14_production_excerpt --well 15/9-F-12 --station-md 10000
gea workspace --path C:\site --action add-file --file exports\historian.csv --name "Well A"
gea workspace --path C:\site --action add-live --name "Pad 3 OPC" --port opcua --file opcua.json
gea workspace --path C:\site --action refresh --month 2026-09      # every report, into C:\site\reports
gea serve --workspace C:\site                                      # http://127.0.0.1:8765/
```

Open the address in a browser. The first visit asks for the first
administrator's name and password (once); everyone else is added under
Administration with a role: **viewer** reads everything; **operator** adds
wells, runs reports and the gate, starts and stops live taps, acknowledges
alarms and commits configuration; **approver** decides well tests and re-fits;
**admin** manages users, schedules and rollbacks. Pages: Home (tiles, well
ranking), Wells (add from a file, the catalogue or a live tag map; each well's
reports, quality, trends, tests and alarms), Patch panel (supervised sources,
their state and live values, their maps, live values), Alarms (the wall, acknowledge, definitions), Approvals (one queue),
Files (the import and export roots an administrator allows, browse with
detection by content, import into a well, "save to..." on every report, the
evidence pack, watch folders), Alarms (the wall: acknowledge one or all, shelve
with a reason and an expiry, unshelve; definitions), Approvals (one queue),
Configuration (versioned JSON with history, diff and rollback), Reports
(generate and open every report), Verification (the acceptance suite, FAT/SAT,
SBOM and the standalone check from the page), Survey (a LAS file in, a strata
report out), Jobs (every run with its log), Administration (users, live
sessions, file roots, schedule, site settings, notifications, audit log). A
search box in the header spans wells, alarms, reports, jobs, configuration,
patches and schedules; the navigation shows badge counts (unacknowledged
alarms, decisions waiting, failed jobs, patches down); Home opens with what
happened since your last sign-in; every page has a help panel (the Help
button hides them); every timestamp is shown in your time zone and every
value in your units (field or SI - "Your preferences", with site defaults
under Administration); a first-run guide walks a new site through its
settings, first well, people and first refresh; every page prints cleanly.

Every action the page takes is a job: `python -m gea <command>` run by the
service with its log kept under `jobs/`, so one code path serves the page, the
terminal and the acceptance gate, and a failed action shows its log instead of
disappearing. Every action is written to `records/audit.jsonl` with the
actor's name and the SHA-256 of the inputs it used. The service is written on
the standard library only (no new dependency, the standalone check still
passes), listens on the loopback interface by default (`--host 0.0.0.0` for a
site network, behind the site's TLS proxy), keeps sessions in HttpOnly
SameSite cookies, and requires the page's own header on every action so a
foreign form cannot act. Accounts live in `users.json` as salted PBKDF2-SHA256
hashes; `gea users --workspace C:\site --action add --name ... --role admin`
adds one from the terminal (the password comes from the `GEA_PASSWORD`
environment variable, never the command line).

The workspace layout: `wells/<id>/source` (the client's files, copied in
verbatim and hashed), `config/` (the version store), `monitor/` (drift
evaluations and change logs), `reports/` (the printed dashboard and every
report), `jobs/` (every run), `records/audit.jsonl`, `users.json`. All of it is
text: JSON, JSON lines, CSV, Markdown, HTML.

### The help library

`gea help` lists fourteen pages indexed by the job a reader arrives with -
start, bring data in, quality rules, drift, well tests, alarms and the month,
the site, instruments, shut-ins and build-ups, patches, files, notifications,
which code is running, upkeep. Each page carries four lines and stops: the
command, what it writes, the one number to check, and what the page will not
call a measurement. The pages ship inside the package (`gea/help/`), and the
same text is what `/api/help` serves and what the dashboard shows under every
heading and under Help, so the terminal and the page cannot drift apart. The
click-by-click tester guide ships with them: `gea guide`.

### Which code is running

`gea doctor` answers the question every stale-page report comes down to:
which Python, which copy of the package, does pip's record match the code
that runs (an editable checkout or an installed release), is another copy
shadowing it, is the page present, which dependencies are installed, is a
newer release on PyPI; with `--workspace` it also checks the site folder, the
accounts, the patches, a free port and write access. Every finding carries
its fix. `gea serve` runs the same checks and does not start on a blocking
finding, so a stale page is never served silently.

### Notifications

Rules route events to channels: a webhook URL or an SMTP mailbox. Events:
`alarm.activated` (by priority), `alarm.shelved`, `job.failed`, `patch.down`
and `patch.up`, `approval.pending`, `file.imported`. A quiet window stops
repeats of the same key; every attempt and every suppression is in
`records/notifications.jsonl`; an SMTP password is read from the environment
variable the channel names, never from the configuration. `gea notify
--example` prints a configuration to commit as `notifications` (Administration
has the editor and a "send a test" button per channel).

### Instruments and transients

A swapped gauge is a new instrument. Record the swap on the well page (or
confirm one the step detector proposes - it looks for a jump in a gauge's
offset against its peers, which a process change does not produce) and the
drift fit restarts at the swap, with the swap printed in the report. File the
calibration certificate of each instrument (serial, lab, dates, stated
accuracy; the document is kept and hashed) and its status - VALID, EXPIRING,
EXPIRED, MISSING - is on the well page, on Home, and beside the measured bias
in the drift report, so a reader sees whether a bias is inside the
instrument's own class.

Every shut-in in a record (found by the rate channel, the on-stream hours, or
the pressure signature alone when there is no rate - marked so) gets a
build-up analysis on the next refresh: Horner slope and p* on a middle-time
region chosen by the flat Bourdet derivative, wellbore storage, and - once the
rock and fluid parameters are saved on the well page - kh, k, skin and the
radius of investigation, each with a 90 % band from a residual bootstrap. The
Shut-in and Pressure Transient report prints the region and the rule that
chose it; an analyst can move the region (`gea transient --mtr 2:40`) and the
result follows. It is the first look every shut-in should get automatically,
not a replacement for a full interpretation, and it says so.

### Before a site goes live

`deploy/README.md` is the checklist: keep the service on the loopback
address and put a TLS proxy in front (`deploy/Caddyfile`,
`deploy/nginx-gea.conf`), start with `--behind-proxy`. The service itself
rate-limits sign-ins (five failures lock the name and the address for 15
minutes, audited), lists and revokes live sessions, sends the security
headers on every response, and `gea housekeeping --workspace C:\site --apply`
(schedule it daily from Administration) segments the append-only logs rather
than truncating them and prunes finished job folders and old recordings. `gea
loadtest --patches 16 --seconds 60 --with-service` tells you whether the site
machine carries the patches it will be given, with the page still answering.

## The standalone install kit

A client site often has no internet and no Python. The kit is one folder
(and one zip) that carries its own Python, the package and every dependency
as files, with the scripts a site needs; it installs without a network and
without administrator rights, and keeps the site's data in a separate
workspace folder it never deletes.

```
python tools/build_installer.py                       # dist/gea-program-<version>-win64/ and .zip (needs internet here, none there)
python tools/build_installer.py --extras live,plotting,xls,desktop   # add the PyQt6 desktop window (~100 MB)
python tools/build_installer.py --platform linux      # a venv-based kit for a Linux site server
```

At the site: unzip, `install.cmd`, `start-dashboard.cmd`; the browser opens
`http://127.0.0.1:8765/`. `verify.cmd` runs the acceptance gate from the
installed kit (the client's own evidence), `register-service.cmd` starts the
dashboard at logon, `uninstall.cmd` removes the kit and leaves the workspace.
`SHA256SUMS.txt` lets the site verify the kit after copying it. Every tag
builds the Windows and Linux kits in CI (`build-installer.yml`), installs
them the way a client does, runs the gate from the installed kit, and
attaches the zips to the GitHub release of that tag.

## Layout

```
gea/            the package: engines, record layer, reports, monitor, dashboard, cli, shell, acceptance suite,
                workspace, jobs, service, patches and web/app.html (the served dashboard)
gea/catalog/    52 public archive entries, each with a provenance file
docs/           TESTER_GUIDE.md, REQUIREMENTS_MATRIX.md (the scope-of-work mirror that shaped the reports), examples/ (port configs),
                SESSION_LOG.md (the working record, session by session),
                commercial/ (pilot proposal, bench readiness, commercial use), HISTORY.md (the ship-by-ship record)
tools/          standalone_check.py (the self-contained guard; run by ci and ship.ps1), build_installer.py (the kit)
CHANGELOG.md    per-release summary (a tag ships only with its section); SHIP_LOG.md is written by ship.ps1
tests/          pytest wrapper around the acceptance suite and the standalone check
```

## Basis

GEA-Program is a self-contained package: every module imports only the
standard library, numpy, the declared optional extras and the package itself,
and `tools/standalone_check.py` (run by CI and by `ship.ps1`) fails the build
if that ever changes or if a tracked file names another program. The physics
is on standard constants - CODATA 2018 G, standard gravity 9.80665 m/s2, the
IUGG mean Earth radius - and the rock inventory carries seventeen published
density anchors and Vp ranges with their citations (Telford, Geldart and
Sheriff; Schon). Every catalogue entry under `gea/catalog/` has a provenance
file naming its public source.

Two statements the product carries on its own model cards: the gauge aging
envelope's lower bound is an engineering model with no field validation on
record, and five of the fourteen back-tested strata quantities are NOT
ACCEPTABLE at the 95 % target. Both are printed, never claimed otherwise.

## Shipping

`.\ship.ps1` (PowerShell) gates, commits, tags and pushes in one screen: version in
`pyproject.toml` must equal `gea.__version__` (`-Bump x.y.z` sets both), the tag must
not exist anywhere, every version in `SHIP_LOG.md` must have its tag, `python -m gea
accept` must be green, `SHIP_MESSAGE.txt` must start with the tag; then commit, tag,
push, and the remote tag must be seen before SHIPPED is printed. `CHANGELOG.md` must carry a
section headed by the tag, and `tools/standalone_check.py` must report no findings. `-DryRun` runs every
check and changes nothing; `-NoPush` stops after the local tag.

## Publishing to PyPI

The name `gea-program` is free on PyPI as of 2026-09-29; a PyPI project is created by
its first upload, there is nothing to "start" beforehand except the trusted publisher.
One-time setup, before the first tag is pushed: sign in to PyPI -> your account ->
Publishing -> "Add a new pending publisher": project name `gea-program`, owner
`Daniel8Murphy0007`, repository `GEA-Program`, workflow `release-to-pypi.yml`,
environment `pypi`. Then in GitHub -> Settings -> Environments create `pypi`. From then
on `.\ship.ps1` pushes the tag and `.github/workflows/release-to-pypi.yml` gates, builds,
verifies the wheel and publishes; the package page is
https://pypi.org/project/gea-program/ after the first successful run.

## Licence

Mozilla Public License 2.0 (MPL-2.0); see `LICENSE`. Every source file carries the MPL-2.0
header (Exhibit A). Copyright (c) 2026 Daniel T. Murphy.
