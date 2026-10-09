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
- **Live protocol ports** (`live_ports.py`, `wits0.py`, `witsml.py`, `etp.py`, `seedlink.py`, `opcua_port.py`,
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
- **The second leg - seismic** (`seismic.py`, `seismic_detect.py`): raw seismic
  records in (miniSEED with Steim1/Steim2, SAC, or fetched from any FDSN
  archive - TexNet, EarthScope), Welch spectra, spectrograms and the persistent
  lines above the local floor out, and the detectability test: one station's
  record against a list of known rigs with positions and working windows,
  verdict per rig, the detection radius bracketed between the farthest rig
  heard and the nearest not heard. The instrument response (`seismic_response.py`):
  the station's StationXML read into its stages, evaluated as evalresp does,
  removed with a water level - counts to m/s, m or m/s^2. The decoder is proven
  against libmseed on a corpus of real-station files; the response against
  evalresp on a real IRIS channel. The array step (`seismic_array.py`):
  beamforming over a slowness grid with the array response function's width
  beside every bearing, the crossing of bearings from several arrays with its
  ellipse, location from lags with the velocity named as an input, and the
  array's detectability test - did it point at each known rig. On a site the
  leg lives on the dashboard: a Seismic page where a station or an array is
  added by upload with its rigs list and station file, refreshed as a job,
  and read - spectrum, lines, verdicts, bearing - with its Seismic Station
  Report under Reports; and the SAR panel on that page, a control panel and a
  screen that plays the leg's arithmetic forward in time on the labelled
  synthetic scene (`seismic_film.py`), every frame stamped SIMULATION_SELF_TEST.
  See *The second leg*.

## Quick start

```
pip install -e .
gea quickstart                 # a real catalogue well -> its reports -> the dashboard; the KTB strata survey
gea survey mywell.las          # a LAS file in, one strata report out
gea dashboard --catalog-well volve_f12_f14_production_excerpt:15/9-F-12:10000 --td 10500 --out dashboard
gea client-report --report accuracy --out client_report
gea model-cards --out model_cards
gea sbom --out sbom
gea accept                     # the product gate (340 checks)
gea help drift                 # the help library, by the job (16 pages; the same text is on every dashboard page)
gea guide                      # the click-by-click tester guide (docs/TESTER_GUIDE.md)
gea gui                        # the desktop window (pip install "gea-program[desktop]")
gea workspace --path C:\site --action init --name "Pad 3"     # a site folder
gea serve --workspace C:\site                                 # the dashboard as the door: opens http://127.0.0.1:8765/ in your browser
gea doctor --workspace C:\site                                # which code runs, can it serve, with every fix
gea files --workspace C:\site --action list --root historian  # the import roots, detection by content, import, export, packs
gea transient --file historian.csv --params params.json --out pta   # shut-ins and build-up analysis with a band
gea seismic --action detect --file tx.mseed --station-lat 31.9 --station-lon -102.1 --sources rigs.csv   # the second leg: is a known rig in this record?
gea swaps / gea certificates / gea notify / gea housekeeping / gea loadtest   # instruments, notification rules, site upkeep
```

`gea` is the front door (`gea/cli.py`); every other subcommand passes through to
`python -m gea`. PATH-proof form: `python -m gea.cli ...`. Open `dashboard/index.html`.

## Live data

The engine reads live data through six protocol ports. All are **read-only**,
all map only what the site declares (a tag map the client owns; an empty map
is declined), and all write every received message to a JSON-lines recording
that `--replay` turns back into records without a connection - which is how a
site session is reproduced offline and how the mapping logic is tested.

```
pip install "gea-program[live]"                       # asyncua, paho-mqtt, pymodbus, pyserial (or one extra at a time)
gea wits0  --write-example-config wits0.json          # drill floor: transport tcp | listen | serial; item codes -> tags, units
gea wits0-sim --port 5001                             # a WITS0 sender to rehearse with (no rig needed) - run it in a SECOND window and leave it running
gea wits0  --config wits0.json --seconds 60 --record floor/session.jsonl --out floor --stream-csv floor/stream.csv   # in the first window, while the sender runs
gea witsml --write-example-config witsml.json         # a WITSML 1.4.1 store: url, uids, mnemonics -> tags; credentials by env name
gea witsml --config witsml.json --seconds 60 --out store                 # needs a real store: the example url is a placeholder until you edit it
gea etp    --write-example-config etp.json            # a WITSML 2.x store over ETP v1.2: ws:// or wss:// url, bearer token by env name, channel URIs -> tags, units
gea etp-sim --port 9800                               # an ETP store with two channels to rehearse with - run it in a SECOND window and leave it running
gea etp    --config etp.json --seconds 60 --record rig/session.jsonl --out rig   # subscribes; the store pushes every value as it is written
gea opcua  --write-example-config opcua.json          # edit: endpoint, security, credential env names, nodes
gea opcua  --config opcua.json --seconds 60 --record opc/session.jsonl --out opc --stream-csv opc/stream.csv   # needs a real OPC UA server, likewise
gea mqtt   --write-example-config mqtt.json           # edit: broker, TLS, credential env names, topics
gea mqtt   --config mqtt.json --replay mq/session.jsonl --out mq_replay     # no broker needed
gea ingest --file floor/stream.csv                    # a stream CSV feeds the historian port like any other
```

| port | transport | quality | timestamps |
|---|---|---|---|
| `wits0` | WITS Level 0 frames (`&&` ... `!!`, four-digit item codes) over TCP connect, TCP listen or serial; record-01 dictionary built in | numeric GOOD; sentinels (-9999, -999.25) and non-numeric -> GAP with the reason | items 0105/0106 (date, time) when present, else arrival; ingest at arrival |
| `witsml` | WITSML 1.4.1 SOAP store: GetVersion, GetCap, GetFromStore on one log object, rows newer than the last seen at each poll | numeric GOOD; the store's nullValue and empty fields -> GAP | the index curve of a time log; arrival for a depth log |
| `etp` | Energistics ETP v1.2 over WebSocket (`etp12.energistics.org`, Avro binary to the published schemas): RequestSession/OpenSession, GetChannelMetadata for the declared URIs, SubscribeChannels, ChannelData pushed by the store (Protocol 21) | numeric GOOD; a non-numeric or null value -> BAD with the reason; a channel the map does not name is counted unmapped | the store's own index: an ETP DateTime index is microseconds since the epoch; an ElapsedTime index is seconds from its declared start; a depth index leaves the arrival time standing, named |
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

`gea help` lists sixteen pages indexed by the job a reader arrives with -
start, bring data in, quality rules, drift, well tests, alarms and the month,
the site, instruments, shut-ins and build-ups, seismic (the second leg),
patches, files, notifications, which code is running, audit / update, upkeep. Each page carries four lines and stops: the
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

### The second leg

The second leg of the program is seismic: the client's site records ground
motion that conventional processing treats as noise, and in that noise are
the machinery lines of every rig working within range - mud pumps and their
harmonics, rotary tables, engines. The leg starts with ingest and one measured
number, not with a map. `gea seismic` reads the two formats the archives and
most field recorders write (miniSEED with Steim1/Steim2 and integer/float
encodings, decided by content; SAC), fetches records from any FDSN web service
(`--base texnet` for the Texas network TX, `--base iris` for EarthScope), and
produces the standard spectral products: the Welch PSD, a spectrogram, and
the lines that persist above a running-median floor in the band where rig
machinery sits (1-50 Hz by default). The decoder is proven in the acceptance
suite against records written by libmseed, the format's reference
implementation (`gea/reference/`), and `tools/seismic_reader_check.py` runs it
against libmseed on about ninety real-station files of every encoding and
byte order (the obspy test corpus, fetched with pip, never redistributed):
every time-series file sample-exact.

Physical units come from the station's own response file. `gea seismic
--action remove-response --stationxml station.xml` reads the FDSN StationXML
the archives serve beside the data (`fetch --with-response` fetches it),
builds the response from its stages - the poles and zeros of the sensor, the
gain of each stage, the FIR and IIR coefficients of the digitizer with their
delay corrections - the way evalresp builds it, and removes it in the
frequency domain with a water level and a pre-filter, giving velocity,
displacement or acceleration in SI units. The acceptance suite holds the
evaluation to evalresp's numbers on a real IRIS channel (IU.ANMO.10.BHZ,
`gea/reference/`) to one part in a hundred thousand. A station file whose
stages disagree with their own declared sensitivity, a polynomial stage, a
channel the file does not cover: refused with the reason, never patched, and
the record stays in counts, labelled.

One station detects; an array gives a direction. `gea seismic --action beam`
takes three or more sensors' records with their positions and returns the
back-azimuth and slowness the band's energy crossed the array with, by
frequency-domain beamforming over a slowness grid (the conventional Bartlett
beam, or Capon), refined around its maximum - and, beside the bearing, the
array response function's half-power width, which is the resolution the
geometry allows at that band, and whether the geometry has aliasing lobes
there, so a lone peak is not mistaken for a source. `--action array-detect`
is the array's form of the detectability test: for each rig on the
ground-truth list, in the windows it worked alone, did the array point at it
within its own tolerance (POINTED, NOT_POINTED, INCOHERENT). `--action
locate` crosses the bearings of two or more arrays, weighted by their
uncertainties, and returns the point with its 1-sigma ellipse, the crossing
angle and the residual of each bearing; a location from station-pair lags is
there too, with the medium velocity it needs named as the input it is. On
the labelled synthetic scene (`--action array-selftest`) a 1.2 km,
nine-sensor array points at rigs from 6 to 45 km within 0.3 degrees against
a 7-degree tolerance, and three arrays' bearings cross within a kilometre of
the rig. What none of it claims: a position from one array, a bearing to a
source nearer than five apertures, a direction finer than the array response
function, or a velocity that was not measured.

On a site the leg is a page. Seismic, beside Wells: a station (one record)
or an array (one record per sensor and a sensors CSV) is added by upload with
its position, its rigs list and, when there is one, its StationXML, and lives
under `seismic/<station>/` in the workspace with its files copied in verbatim
and hashed. Refresh - a button, or `gea workspace --action refresh-seismic`,
and like every other action a job with its log - reads the record, removes
the response when the station file is there, writes the spectrum, the
persistent lines, the detectability verdicts and, for an array, the beam and
the array verdicts under `reports/seismic/<station>/`, and the Seismic
Station Report beside them; the station page draws the spectrum and shows
the tables with the limits printed under each. The home page counts the
stations and the listed rigs heard. The sample report is in
`docs/report_samples/`.

The first number is the detectability test (`--action detect`): one station's
continuous record, the station's position, and a CSV of known rigs - position
and working window from a public permit register or the operator's own
schedule. For every rig that worked alone for enough windows the test
compares the band power in its windows with the quiet baseline (the windows
when no listed rig was working) and says DETECTED or NOT_DETECTED, with the
excess in dB and the lines that belong to those windows and not to the quiet
ones; a rig that never worked alone is AMBIGUOUS, and a record with no quiet
hour gets NO_QUIET_BASELINE and no verdict at all. The result is the radius
bracketed between the farthest rig heard and the nearest not heard - the
station's measured reach for rigs like those on the list. Everything the leg
can later claim sits inside that radius. What the test will not call a
measurement is printed with it: a well's position or track (one station
detects, it does not locate - locating is the array step that comes after
this number exists), a radius beyond the farthest listed rig, anything about
a rig not on the list, and any quantity in physical units until a station's
response has been applied and named. `--action selftest` runs the test on a
synthetic scene and labels its output SIMULATION_SELF_TEST.

The SAR panel at the foot of the Seismic page is the control panel and the
screen. "Run the scene" is `gea workspace --action sar-film` as a job: the
labelled synthetic array scene, one frame per window, written under
`reports/seismic/SIMULATION/`. The screen plays it - the trace, the
spectrogram column arriving, the beam power map on the slowness grid swinging
onto the working rig, the map with the bearing and the array's tolerance
wedge against the rigs where the scene put them, the tally per rig, and at
the end the array detectability test's verdict on the whole scene, the same
function a real record goes through. Every frame carries SIMULATION_SELF_TEST
and the line "not a measurement of any ground"; a real record is refreshed on
its station and the page shows what it earned, never a film. `gea seismic
--action sar-film --out film.json` writes the same film from the command line.

### Tracks - the time dimension

The array step gives one bearing for one window. `seismic_track.py` runs it
window after window: a bearing history per array (a bearing with the array's
own tolerance where the beam was coherent, a gap where it was not, change
points where the bearing moved beyond the tolerance), a position history from
two or more arrays crossed window by window (a position only where two or
more arrays were coherent, the bearings crossed at 15 degrees or more and the
point lies in front of every array - every other window a gap with its
reason), and the track: the principal line through the longest continuous
segment of positions, with its heading, length and rate, the ellipses beside
every point, and segments listed apart where positions jump farther than
their ellipses allow (a coherent background crossing in a quiet hour is not
the rig an hour later). The verdict against ground truth - the lateral's
surveyed points or a permit's surface hole with its date, interpolated to
each position's time - is TRACKED, PARTIAL, NOT_TRACKED or INSUFFICIENT, with
the fraction and the heading difference printed. The labelled scene behind
`--action track-selftest` is a bit advancing along a straight lateral past
two arrays. On a site a track is two or more array stations and an optional
truth CSV, refreshed as a job into the Seismic Track Report; the track view
shows the map with every position's ellipse, a slider through time and the
bearings over time, and the SAR panel's second scene plays the same thing
forward with the track drawn as it is earned.

Ground truth from a permit export: `gea permits --file export.csv --out
rigs.csv --within LAT LON KM` finds the columns of a regulator's query (an
identifier, a surface position, a spud or approval date) by name, with a
mapping file when the guesses are wrong, and writes the rigs CSV the tests
read beside an import note that says which columns were used, how many rows
were dropped and why, and how many end dates it assumed (a permit rarely says
when drilling stopped). The Seismic page's add form takes a permit export in
place of the rigs CSV and keeps both.

### Several rigs at once

One bearing is one direction for every machine in the band, so when two rigs
work together the crossing of whole-band bearings means nothing. That is the
wall between "a rig" and "a field", and `seismic_signature.py` is the step
through it. While a rig works alone, the bins that stand above the local
floor in its windows and not in the quiet ones are kept as its signature -
the same lines the detectability test already prints. In the windows where
several rigs work together, the cross-spectral matrix is sliced to one rig's
lines and beamed, which gives a bearing per rig in the same window; a
machinery line arriving as one plane wave is coherent in its own bin even
when the band as a whole is noise, which is why it works.

What it refuses is as much of the method as what it claims. A rig that never
worked alone has no signature and gets no bearing. Two rigs whose lines fall
within the spectral resolution of each other are NOT_SEPARABLE, both of them,
rather than handed a bearing each. A rig whose own lines all sit above the
array's spatial Nyquist is ALIASED: the measured beam has another peak within
a tenth of the height of the one found, the geometry cannot say which is the
source, and the peaks it cannot separate are listed instead of a direction.
Run through two arrays, every rig that both pointed at in the same window
gets its own positions, ellipses and track, and a track whose extent is
inside its own ellipses says `NOT_RESOLVED` instead of reporting the heading
of a scatter as a direction of travel. `gea seismic --action signatures |
multi-beam | multi-bearings | multi-track | signature-selftest |
field-selftest`; on a site, an array station with a rigs list learns the
signatures at every refresh and a track carries a track per rig beside the
whole-band one.

### The machine behind the lines

A signature as a list of frequencies is a list of facts about bins. A pump at
1.7 strokes per second is one fact about a machine, and it puts energy at
1.7, 3.4, 5.1 and 8.5 Hz. `seismic_harmonic.py` searches every line divided
by every order for the comb that explains the most of them, normalises it to
the largest spacing the orders found allow - a comb of every second tooth is
that comb at twice the spacing, and the larger one claims less - and reports
the fundamental as a rate per minute. That is a far stronger claim than the
same lines listed separately, and the module says how strong: `by chance` is
how often this record's line density puts that many lines on a comb by
accident, and a family above the ceiling is set aside rather than reported.
Fewer than three lines is not a family, because any two lines define a comb;
two spacings that explain the same lines equally well are AMBIGUOUS; and a
fundamental whose half would fall below the analysed band is flagged, because
the machine may be running at half that rate with only its even harmonics in
view.

The other half is time. A pump's rate follows the work: a line at 1.40 Hz
walks to 1.85 Hz over a tour, no single bin stands above its floor in enough
windows, and the rig all but disappears from its own signature - which is the
defect this band exists to fix. Every line is followed window to window by
nearest neighbour within a drift ceiling, each peak refined inside its bin by
a parabola so a walk smaller than the bin width can be read, and each track
carries STEADY, DRIFTING or INTERMITTENT with its drift in Hz per hour. A
drift no larger than the bin width is not called a drift. The walk is also an
observable in its own right: `rate_history` reads the rate over the record,
and a rate that moves is the machine's load changing, not a different
machine.

Both halves meet in attribution. A track belongs to a source when it passes
within tolerance of a line that source was learned on - or when it stands in
a small whole-number ratio, in a window they share, to a track that source
already claims, which is how a harmonic the fixed bins missed comes back to
its machine wherever it has walked to. A line two sources could both claim is
given to neither. A line no listed source claims is listed as exactly that:
something is making it and the program was not told what.

And it closes the loop on ground truth. Every verdict in the leg rested on
the rigs CSV's working window - the detectability test, the array test and the
track all believed it. `activity_from_signature` measures it instead: the
fraction of a source's own lines standing above their floor, window by
window, each line looked for within a fraction of its own frequency of where
it was learned, because a harmonic walks as far as its order. The spells it
finds sit beside the declared ones with AGREES or DIFFERS and the counts both
ways. A permit date is a permission, not a drilling log, so a disagreement is
a finding and not an error - and which of the two is right is not decided
here.

`gea seismic --action harmonics | harmonic-selftest`; on a site every station
refresh does all of it - a single sensor as well as an array, using its own
detectability test's lines per source when there is no array to learn from,
so one geophone can still say when each rig was working - and the Seismic
Station Report and the station page carry the section, with the tracks drawn
against time.

### The Seismicity Response Area packet

The Railroad Commission of Texas declares a Seismicity Response Area around a
cluster of felt earthquakes and expects the operators inside it to run a
response plan. The plans on file have one shape - a circle of about 9 km, a
plan written against M 3.5, an 18-month goal, a response group meeting
within 48 hours of a threshold event, quarterly checkpoints with Commission
staff, disposal wells in two tiers by the base of the Wolfcamp - and the
Commission's December 2023 Notice to Operators names the data: four daily
parameters (maximum and average surface injection pressure, injection
volume, maximum injection rate) and three bottomhole-pressure methods, one
of which is a permanent downhole probe. `gea/sra.py` builds the packet to
that shape from what the site holds. The operator declares what the program
cannot measure (`set-well`: surface position and datum, API and UIC, depth
tier, which channels are the pressure, the rate and the downhole gauge, the
BHP method); `sra-define` puts the area on the site; `sra-report --catalog
texnet.csv` writes the packet - membership by geodesic distance, the daily
record per well rolled up by month, the catalogue's events against the plan
with the trigger and the goal clock, the schedule, and every gap by name -
and a daily export under the Notice's own parameter names. It will not
invent a volume for a well with no rate channel, a tier from depth alone, or
an aftershock: those are declarations, and the packet says so.

### The OSDU-shaped export

What a buyer's data team asks for on the first call. `gea workspace --action
osdu-export --site <id> --partition ... --acl-owner ... --acl-viewer ...
--legal-tag ...` writes the site as an `osdu:wks:Manifest:1.0.0` to the
published well-known schemas: each well as Well and Wellbore 1.0.0 with its
API and UIC as name aliases, its record as a WellLog 1.1.0 in the time
domain over a File.Generic dataset with the file's SHA-256, each seismic
station as a generic component with its records as datasets, the site as the
work product, the files copied beside it. Every position goes out twice - as
given on its own datum with that datum's EPSG code, and on WGS 84 with the
operation between them written out; a point on an unknown datum gets no WGS
84 coordinates and is named. Without the operator's partition, ACL and legal
tag the manifest is NOT LOADABLE and says so. `gea help osdu`.

### The QuakeML catalogue export

The site's own events in the format the regulator's catalogue tools read.
After `associate`, `gea workspace --action quakeml-export --site <id>
[--agency "<name>"]` writes `reports/sites/<site>/quakeml/<site>_events.xml`
as QuakeML 1.2 to the Basic Event Description schema: one event per
associated event with its origin (the misfit region as the uncertainties,
depth in metres and "operator assigned" because it was declared, the method
and the earth model named, the quality block with the counts, the RMS, the
azimuthal gap and the distances), one pick per station with the record's own
FDSN codes, one arrival per pick with its residual. Every origin and pick is
automatic and preliminary in the schema's own words; the catalogue's AGREES
or DIFFERS is a comment with the catalogue's event id; no magnitude is
written and a comment says why. The shape is checked the way a reader checks
it first - identifiers, allowed children, required children, enumerations,
references - and the file validates against the published XSD. `gea help
quakeml`; `gea quakeml` runs the labelled scene.

### The well as master data names it

`gea workspace --action well-identity --well <id>` names a well the way the
operator's master data and a regulator's well file name it: the US Well
Number (the API number, whose standard PPDM has held since 2010) taken apart
- state and offshore code, county code, unique well and its range, sidetrack
and event when given, and which component the number identifies (ten digits
the Well Origin, twelve a Wellbore, fourteen an event) - and the components
of PPDM's "What is a Well" the site can name: the Well and its one Origin at
the declared position on its datum, the Wellbore only when the sidetrack
code was given, the gauge station as a measured depth along the wellbore,
the Wellhead Stream as the channels recorded at the wellhead, the Well Set
as the site, every alias typed. The identity rides on every well row of the
SRA packet (section 2a) and in the OSDU export's Well and Wellbore records.
It will not assume a sidetrack, invent an injection interval or a
completion, or name a county. `gea ppdm --action parse --number ...` takes a
number apart from the command line; `gea help ppdm`.

### Machine vibration

The pump's record read the way an operations engineer expects. `gea
workspace --action vibration-report --well <id> --record pump.csv --vib-unit
g --rpm 1780 --group 2 --support rigid --bearing 9,7.94,39.04` writes the
broadband r.m.s. vibration velocity over 10-1000 Hz (2-1000 Hz below 600
r/min), the quantity ISO 20816-1 defines, and the ISO 20816-3 zone for the
declared machine group and support class with the boundaries printed; then
the envelope spectrum of the most impulsive band (chosen by kurtosis, or
declared) against the bearing's defect frequencies - BPFO, BPFI, BSF, FTF
from its geometry and the shaft speed - each MATCHED at its fundamental or
NOT MATCHED, the inner race with its sidebands. A record in counts needs a
sensitivity; a rate that cannot carry 1000 Hz is PARTIAL BAND, never
promoted; the group and the support are declarations; a matched frequency
is not a fault size. CSV or miniSEED/SAC; `gea vibration` runs the labelled
pump; `gea help vibration`.

### Live stations (SeedLink)

The seismic leg had run on records that were already files. The stations
serve SeedLink - the real-time protocol every data centre and most
digitisers speak - and `gea/seedlink.py` is the customer: the TCP session on
port 18000, `HELLO` read for what the server offers, SeedLink 3 (`STATION
STA NET`, `SELECT LLCCC.T`, `DATA` with a hexadecimal sequence, `END`, the
8-byte `SL` header on each 512-byte record) or SeedLink 4.0 (`SLPROTO 4.0`,
`STATION NET_STA`, `SELECT LOC_B_S_SS`, a decimal sequence, the `SE` header
with its length, uint64 sequence and station id, `ERROR <CODE>`), whichever
the server speaks. Every record is appended byte for byte to a day file per
channel under the station's `live/` folder with the state beside it - the
last sequence number per station, so a reconnection (with backoff) resumes
where it stopped and no record is filed twice; per channel the last sample,
the latency, the records, the samples, the gaps (counted, never bridged).
On the dashboard's Seismic page a live station is added with its host, port,
network, station and selectors, started and stopped (the serving process
keeps every enabled one up), and **folded**: the finished day files move into
the station's record list, hashed, vertical first, and Refresh runs the leg
on them as on any brought file. `gea seedlink-sim` is a three-component
synthetic station speaking both versions; `gea seedlink --selftest` runs both
against it; `gea seedlink --config ... --hello` prints what a server says of
itself; `gea help seedlink`. A TexNet station is served by EarthScope at
`rtserve.iris.washington.edu:18000`, network `TX`.

### WITSML 2.x over ETP

The newer rigs and stores do not poll: Energistics ETP v1.2 is a WebSocket
session, Avro-encoded to the published schemas, on which the customer names
the channels it wants and the store pushes every value as it is written.
`gea/etp.py` is that customer: the handshake with the subprotocol, the
binary encoding and the payload limits; RequestSession for Discovery, Store
and ChannelSubscribe and what the store answered; the channel metadata for
the URIs the mapping declares (a URI the store does not have is named as
missing); the subscription; and each ChannelData DataItem as a record under
the mapping's tag and unit, with the store's own index as the source time -
microseconds since the epoch for a DateTime index, seconds from the declared
start for ElapsedTime, the arrival time standing (and named) for a depth
index - and the arrival as the ingest time, so the latency is per record.
Client message ids even, the store's odd; a ProtocolException carried with
its code; a client or a store without the subprotocol refused at the
handshake. The patch supervisor runs it like every other port; the page
offers it; `gea etp-sim` is a store with two channels of a synthetic
disposal well; `gea etp --selftest` runs the codec and a loopback session;
`gea help etp`. The doctor now also names a panel started through the
`gea.exe` launcher, which pip cannot replace while it runs - stop, update,
start with `python -m gea serve`.

### Conformity, in the standards' words

The drift report's "bias inside the instrument's class" is a statement of
conformity, and ISO/IEC 17025:2017 7.8.6 says what one carries: the result it
applies to, the specification, and the decision rule - which has to take
the uncertainty into account. The report now states it that way: the tag's
bias over the window with its sample count, the certificate's accuracy
class as the tolerance limit, the bias's expanded uncertainty (k = 2) as the
guard band, and the ILAC-G8:09/2019 non-binary rule - PASS, CONDITIONAL
PASS, CONDITIONAL FAIL or FAIL, with the acceptance limit printed; without
an uncertainty it falls back to simple acceptance and says so. The
certificate register takes what 7.8.4 asks the paper to carry (the expanded
uncertainty with k and probability, the traceability statement as worded,
the conditions, the calibration date, the method, who authorised it) and
`gea certificates --action conformance` prints each certificate against
the fourteen items of 7.8.2.1 and 7.8.4.1 - CARRIED, NOT CARRIED, NOT
RECORDED - with COMPLETE or INCOMPLETE and the missing clauses named. The
well page's filing form takes the items and its table shows U (k) and the
standing. `gea conformance --action decide` states one decision. `gea help
conformance`.

### Audit / Update

The Audit / Update tab holds three things. The whole audit log
(`records/audit.jsonl`) with filters by who, by action and since a date, and
a CSV download - Administration keeps the last hundred lines and points here.
The program card: the running version and code path, the newest release on
PyPI (`current`, `behind`, or `PyPI not reachable` when the machine is
offline), which extras are installed here, and links to PyPI, the releases
with the install kits, and the CHANGELOG; "Update the program from PyPI"
(admin) is `gea update` as a job - pip --upgrade from the same Python, after
which the service must be restarted; the program never restarts itself, and
an offline kit is updated by installing the newer kit. The data card: every
report against its source - `current` or `stale` when the source changed
after the report was written - and "Update every report from its source"
(operator), which is `gea workspace --action refresh-all` as a job: the
dashboard for every well, then every seismic station, errors collected and
listed, never hidden.

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
python tools/build_installer.py --extras live,plotting,xls   # a smaller kit without the PyQt6 desktop window
python tools/build_installer.py --platform linux      # a venv-based kit for a Linux site server
```

The kit carries every optional dependency by default - the live ports
(asyncua, paho-mqtt, pymodbus, pyserial), matplotlib, xlrd and the PyQt6
desktop window - so `gea sbom` from an installed kit lists all of them as
installed; and `report-samples/`, one rendered example of every report.

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
gea/reference/  miniSEED records written by libmseed and an IRIS StationXML with evalresp's numbers, with provenance: the independent references
docs/report_samples/  one rendered example of every client report, from this build (tools/render_report_samples.py; SAMPLES.md names each command)
docs/           TESTER_GUIDE.md, REQUIREMENTS_MATRIX.md (the scope-of-work mirror that shaped the reports), examples/ (port configs),
                SESSION_LOG.md (the working record, session by session),
                commercial/ (pilot proposal, bench readiness, commercial use), HISTORY.md (the ship-by-ship record)
tools/          standalone_check.py (the self-contained guard; run by ci and ship.ps1), build_installer.py (the kit),
                render_report_samples.py (docs/report_samples), seismic_reader_check.py (the reader against libmseed)
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

The gauge aging rate the drift evaluation uses is the instrument's published
drift specification, read from a cited datasheet (`gea/gauge_specs.py`), with
nothing added for temperature or pressure - above the rating the rate is
flagged, not changed. The program carries no aging model of its own, no
engineering constant that is not a datasheet number, and no claim about a
gauge beyond what its datasheet says; `tools/standalone_check.py` fails the
build on the names, phrases and numbers of the model that once stood there.
The seismic leg is textbook signal processing - Welch's PSD estimate, a
running-median floor, band power, great-circle distance - and the decoder is
checked against libmseed; the synthetic scene behind its self-test states its
own assumptions (body-wave spreading, a Q) and is labelled as a scene, not a
ground. Two statements the product carries on its own model cards: the datasheet
rate has no field validation on record against a gauge with a known history
(`gea bench` is the instrument for it), and five of the fourteen back-tested
strata quantities are NOT ACCEPTABLE at the 95 % target. Both are printed,
never claimed otherwise.

## Shipping

`.\ship.ps1` (PowerShell) gates, commits, tags and pushes in one screen: version in
`pyproject.toml` must equal `gea.__version__` (`-Bump x.y.z` sets both), the tag must
not exist anywhere, every version in `SHIP_LOG.md` must have its tag, `python -m gea
accept` must be green, `SHIP_MESSAGE.txt` must start with the tag; then commit, tag,
push, and the remote tag must be seen before SHIPPED is printed. `CHANGELOG.md` must carry a
section headed by the tag, and `tools/standalone_check.py` must report no findings. `-DryRun` runs every
check and changes nothing; `-NoPush` stops after the local tag. Before the gate, after the bump:
`python tools/render_report_samples.py` re-renders `docs/report_samples/` from the new build
(section AM fails on a stale build number).

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
