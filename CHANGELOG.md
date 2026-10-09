# Changelog

All notable changes to GEA-Program, newest first. Each released section is
headed by its tag and date; `ship.ps1` refuses to ship a tag that has no
section here. The long-form record, by layer, is `docs/HISTORY.md`; the
session-by-session working record is `docs/SESSION_LOG.md`.

## [v0.17.1] - 2026-10-09 - one kit run per ship

### Fixed
- **The kit workflow no longer races itself.** Every ship pushes main and
  the tag together, and the workflow ran on both - two runs building the
  same commit and attaching the same kits to the same release within
  seconds of each other. Three ships in a row went red at the attach step
  with the kits already on the release: v0.13.0 (the main run's upload
  lost), v0.16.0 (the tag run's create lost), v0.17.0 (the tag run's upload
  lost). Each was patched where it hit; the cause was two writers. The
  workflow now runs once per ship - on the tag - and by hand, under a
  concurrency group on the commit so a manual run that overlaps a tag run
  is queued behind it rather than beside it. One writer per release: no
  race to lose. The attach script keeps its tolerances (a create or an
  upload that loses to a run beside it) as a second line, and its rule
  text names the manual run that took the main run's place. A gap left by
  a failed tag run is filled by a manual run of the workflow with the
  release tag, as before.

## [v0.17.0] - 2026-10-09 - live stations: SeedLink, the station's records as it writes them

### Added
- **The seismic leg's live port** (`gea/seedlink.py`): the stations do not
  hand out files; they serve SeedLink, and this is the customer. A TCP
  session on port 18000; `HELLO` parsed for the versions and capabilities
  offered; SeedLink 3 as every SeisComP and ringserver serves it - ASCII
  commands ending CR LF answered `OK` / `ERROR`, `STATION STA NET`, `SELECT
  LLCCC.T`, `DATA` with the six-digit hexadecimal sequence number per
  station, `END`, every packet an 8-byte `SL` header and a 512-byte miniSEED
  record, `INFO` answered in a log record headed `SLINFO` - and SeedLink 4.0
  as FDSN specifies it - `SLPROTO 4.0`, `USERAGENT`, `STATION NET_STA`,
  `SELECT LOC_B_S_SS` (the 3 selector converted), `DATA n` decimal, `ERROR
  <CODE> text`, the `SE` header with format and subformat bytes, the payload
  length (uint32 LE), the sequence (uint64 LE) and the station id, `INFO` in
  JSON. Whichever the server offers is spoken; `protocol` in the config may
  insist on one.
- **Byte for byte, resumed by sequence**: every record is appended as
  received to a day file per channel (`NET.STA.LOC.CHA.YYYY.DDD.mseed`)
  under the station's `live/` folder, the state beside it: the last
  sequence number per station so a reconnection asks `DATA` for the next
  and files no record twice (proven across a dropped link), per channel the
  last sample, the latency (arrival minus the record's last sample), the
  records, the samples, the gaps and overlaps between consecutive records -
  counted, never bridged. The handshake is kept on the state. A station the
  server does not have is REFUSED and named, with no reconnect loop; the
  server's own `END` ends the run with the reason. INFO ID is the keepalive
  when nothing has arrived.
- **A live station in the workspace**: `add_live_station` with the SeedLink
  config beside an empty record list; the serving process keeps every
  enabled one up (`LiveStations`, one tap thread each, started with the
  service and stopped with it); the Seismic page's "Live stations
  (SeedLink)" card adds, starts, stops and folds; `/api/seismic/live`,
  `/api/seismic/live/add`, `/api/seismic/live/<id>/start|stop|fold`; the
  station list carries the live state. A **fold** moves the finished day
  files (every day but the current UTC day, unless asked) into `source/`,
  hashes each and appends it to the station's files - the vertical channel
  first, so the leg's primary trace is Z - with the audit line naming what
  was folded; Refresh then runs the leg on them exactly as on a brought
  record. Audit: `seismic.live.add/start/stop/fold`.
- **To rehearse against**: `gea seedlink-sim` is a synthetic
  three-component station speaking 3.1 and 4.0 (INT32 512-byte records
  from the writer's own `build_records`, a ring per station for resuming,
  INFO in both forms, `drop_clients()` for the link-loss rehearsal); `gea
  seedlink --selftest` runs both protocols against it on the loopback; `gea
  seedlink --config ... --hello` prints what a server says of itself and
  its INFO ID; `gea seedlink --config --seconds --out` runs a session
  outside a workspace; `gea workspace --action add-live-station |
  live-run | live-fold`; `gea help seedlink`. `seismic.build_records` is the
  record builder `write_mseed` now writes through.
- What it will not do: invent a sample across a gap; give a record a time
  its header does not carry; decode a payload format it does not know
  (counted with the format byte); keep a session the server has ended;
  turn counts into ground motion - the refresh with a StationXML is where
  that happens. Section BK (3 checks); the gate is 340.

### Fixed
- **The kit workflow's release race, the other way round**: v0.16.0's tag
  run went red at the Linux attach step while the release had both kits.
  The tag's own run and the main run of the same commit reach the attach
  step within a second of each other; both saw no release, the main run
  created it (the tag exists on origin, so a main run may), and the tag
  run's `gh release create` failed on a release that now existed. The
  attach script now treats "whichever run creates it, creates it": a
  failed create is followed by a view, and a release that is there - made
  by this run or by the one beside it - is attached to. Rehearsed against
  a stand-in `gh` in both orders.
- The live-station card showed no latency until twenty records had
  arrived (the statistic was computed every twenty); it is computed per
  record now. The kit's README text no longer carries an invalid `\ `
  escape (a SyntaxWarning printed in every gate run).

## [v0.16.0] - 2026-10-08 - WITSML 2.x over ETP: the store pushes

### Added
- **ETP v1.2 customer** (`gea/etp.py`): the live-port leg on the protocol
  the newer rigs and stores speak. A WebSocket client and server with the
  ETP handshake (`Sec-WebSocket-Protocol: etp12.energistics.org`,
  `etp-encoding: binary`, the payload-size limits), masking, fragments and
  ping/pong; an Avro binary codec driven by the specification's own
  schemas (`gea/etp12_schemas.json`, twenty messages plus the header and
  the error record, from the Energistics ETP 1.2 Avro definitions as
  published in the Apache-2.0 `etptypes` distribution) - zig-zag varints,
  unions by branch, arrays and maps with block counts, defaults filled;
  the message header with the flags as specified (multipart, final, no
  data, compressed, acknowledge, header extension); client message ids
  even from 2 and the store's odd from 1, as both reference
  implementations number them. The session: RequestSession for the
  customer roles of Discovery (3), Store (4) and ChannelSubscribe (21),
  OpenSession read for what the store supports, Ping answered, CloseSession
  honoured, a ProtocolException carried to the caller with its code and
  message. The stream: GetChannelMetadata for the URIs the mapping declares
  (a URI the store did not return is named as missing), SubscribeChannels,
  and each ChannelData DataItem as a `SampleRecord` under the mapping's tag
  and unit - the store's own index as the source time (an ETP DateTime
  index is microseconds since the epoch; an ElapsedTime index is seconds
  from its declared start; a depth index leaves the arrival time standing
  and the record says so), the arrival as the ingest time, so the latency
  is per record; a null or non-numeric value is BAD with the reason; a
  channel the map does not name is counted unmapped; SubscriptionsStopped
  and the store closing after records end the run normally with the reason
  on the state.
- **Everywhere a port is**: the patch supervisor runs `etp` like every
  other protocol (CONNECTED with the tags, units and latency; records on
  disk; STOPPED on request; reconnect with backoff); the service validates
  its config (ws:// or wss://, channels with URIs) and serves its example;
  the page's patch form offers "WITSML 2.x over ETP" (the 1.4 port is now
  named "WITSML 1.4 store"); `gea etp` with `--write-example-config`,
  `--seconds`, `--record`, `--out`, `--stream-csv`, `--replay` (a
  recording holds the messages as received with ChannelData as counts, so
  the replay reproduces the session's shape and says it does not re-create
  samples) and `--selftest`; `gea etp-sim --port 9800 --frames --interval
  --seed` is a store with two channels of a synthetic disposal well for a
  site with no rig yet; `gea help etp`.
- **The doctor names a panel that cannot be updated in place**: a panel
  started through the `gea` / `gea.exe` console launcher holds that
  launcher open, so pip cannot replace it while the panel runs (WinError 32
  on Windows) and `gea update` from such a panel refuses to run pip and
  says why - stop the panel, update from a prompt, start it with
  `python -m gea serve`, which the kit launcher and start-gea.cmd do.
- What it will not do: give a time to a depth-indexed sample; name a tag it
  was not told; convert a unit it did not read; decode a message of a kind
  it does not know (counted, with its header); talk to a store that is not
  ETP (refused at the handshake). Section BJ (4 checks); the gate is 337.

## [v0.15.0] - 2026-10-08 - conformity, in the standards' words

### Added
- **The decision rule, named** (`gea/conformance.py`): the drift report
  printed a certificate's accuracy beside the measured bias and said
  "inside" or "OUTSIDE" - a statement of conformity made under the
  simple-acceptance rule applied silently, which ISO/IEC 17025:2017 7.8.6
  does not allow: a statement of conformity carries the results it applies
  to, the specification, and the decision rule, and the rule takes the
  measurement uncertainty into account. The report now states it that way,
  per certificate: the tag's bias over the evaluation window with its sample
  count, the certificate's accuracy class as the tolerance limit, the bias's
  expanded uncertainty (k = 2, from the noise and the sample count) as the
  guard band, and the ILAC-G8:09/2019 non-binary rule - PASS below TL - U,
  CONDITIONAL PASS to TL, CONDITIONAL FAIL to TL + U, FAIL above - with the
  acceptance limit printed. Without an uncertainty the rule is simple
  acceptance and the statement says so and says the uncertainty was not
  taken into account. The binary guard-band rule and simple acceptance are
  available by name.
- **The certificate against 7.8**: the register takes what 7.8.4.1 asks a
  calibration certificate to carry - the expanded uncertainty with its
  coverage factor and probability (a), the conditions (b), the traceability
  statement as the certificate words it (c), the results before and after
  adjustment (d), the decision rule its own conformity statement names
  (e / 7.8.6) - and 7.8.2.1's calibration date, method and authorisation;
  rejects a coverage factor of zero, a probability above one, a negative
  uncertainty and a field it does not know; leaves empty what was not
  filed. Each certificate is judged item by item - CARRIED, NOT CARRIED
  (the field is empty), NOT RECORDED (filed before the field existed) -
  and is COMPLETE when the identification, laboratory, serial, dates,
  result, uncertainty with k and traceability are all carried. The standing
  rides on every status row, in the instruments API, on the well page
  (which also takes the items in its filing form) and in the drift report
  beside the conformity column.
- What it will not do: take an uncertainty into account that was not
  available; assume k = 2 for a certificate that did not state it; read the
  PDF - the register holds what a person transcribed.
- `gea certificates --action add` with the 7.8 flags, `--action
  conformance`; `gea conformance` self-test and `--action decide`; `gea
  help conformance`. Section BI (3 checks); AH7 follows the new column
  names; the gate is 333.

## [v0.14.0] - 2026-10-08 - machine vibration: the zone, and what the bearings are doing

### Added
- **Machine vibration** (`gea/vibration.py`): the one condition-monitoring
  deliverable an operations engineer expects by name. The broadband r.m.s.
  vibration velocity over 10 Hz to 1000 Hz (2 Hz to 1000 Hz below 600
  r/min), the quantity ISO 20816-1 defines - from an acceleration channel
  by integration in the frequency domain inside the band, from a velocity
  channel by band-limiting - and the ISO 20816-3 zone for the declared
  machine group (Group 1 above 300 kW; Group 2 from 15 kW to 300 kW) and
  support class (rigid or flexible by the 1.25× rule), with the boundaries
  printed (2.3/4.5/7.1, 3.5/7.1/11.0, 1.4/2.8/4.5, 2.3/4.5/7.1 mm/s) and
  the margin to the next.
- **Envelope analysis**: the demodulation band chosen as the candidate band
  with the highest kurtosis (a bearing fault's impacts are impulsive and
  kurtosis is their measure) or declared; the analytic signal's envelope
  and its spectrum; the bearing's defect frequencies - BPFO, BPFI, BSF, FTF
  from its geometry and the shaft speed - each MATCHED or NOT MATCHED at
  its fundamental within 2% or one resolution bin and four times the
  spectrum's median, harmonics at the same tolerance, the inner race with
  its sidebands at the shaft speed. The fundamental is required because the
  harmonics of one defect frequency fall near multiples of another; the
  first draft matched on harmonics alone and named three faults where the
  labelled scene had one.
- What it will not call a measurement: a group or a support class (declared);
  a zone from a record that cannot carry the band (PARTIAL BAND, to the
  record's own Nyquist frequency) or from a record under a second; a fault
  size; a defect frequency without the bearing's geometry; an mm/s value
  from counts without a sensitivity.
- `gea workspace --action vibration-report --well <id> --record <file>
  [--channel] --vib-unit --rpm --group --support --bearing [--demod-band]
  [--sensitivity]`: the record copied beside the well under `machine/` with
  its hash, the report in three forms with the zone as its result, the
  declarations in their own table, audited. `gea vibration --action assess`
  without a workspace; `gea vibration` self-test on a labelled pump; `gea
  help vibration`. CSV with a time column, or miniSEED/SAC.

### Fixed
- The miniSEED writer wrote the record start time to the header's 0.1 ms
  field only. Above 5 kHz that is coarser than a sample, and a 20 kHz
  record written by this program read back as fifteen traces with fourteen
  gaps. Blockette 1001 now carries the microseconds the header cannot, the
  reader (which already applied it) joins the records, and the round-trip
  is exact. Section BH (3 checks); the gate is 330.
- The v0.13.0 ship went four green and one red: the Windows kit job of
  the main-branch run failed at "Attach the kit to the release" while the
  tag run of the same commit attached the same kit at the same second; the
  gate had passed and the release carries both kits. A push to main on a
  shipped commit runs beside the tag's own run, and the two reached the
  upload within seconds. The attach script now uploads without replacing
  on a main run, and treats an upload that fails because the asset appeared
  meanwhile as the tag run's work, not a failure.

## [v0.13.0] - 2026-10-08 - the well as master data names it

### Added
- **PPDM well identity** (`gea/ppdm.py`): every report named a well by the
  operator's display name and, since v0.9.0, by the API and UIC numbers
  declared on it. A buyer's data team, a regulator's well file and the
  operator's master data name it more carefully: by the US Well Number
  (the API number; custody passed from the API to the PPDM Association in
  2010, and PPDM's 2013 standard is its successor) taken apart - state and
  offshore code (51 states and territories, four offshore areas, 52-54
  reserved), county code, unique well in its historical, current, reserved
  or exempt range, directional sidetrack, event sequence - and by the
  components of PPDM's "What is a Well": the Well, its one Well Origin, each
  Wellbore, the Wellbore Segments, the Contact Intervals and Completions,
  the Wellhead Streams, the Well Set.
- The number: read in any written form (10, 12 or 14 digits, dashes
  optional), each part named, and which component it identifies - ten
  digits the Well Origin, twelve a Wellbore, fourteen an event. A reserved
  or unknown state, a wrong length, a stray letter, a zero unique well are
  named as problems, never repaired.
- The identity: the Well and its Origin at the declared surface position on
  its datum; the Wellbore only when the sidetrack code was given; the gauge
  station as a measured depth along the wellbore; the Wellhead Stream as
  the channels recorded at the wellhead with the declared pressure, rate and
  bottomhole channels and the direction; the Well Set as the site; the
  aliases - operator name, program id, US Well Number, UIC permit - each
  typed. Every component the site cannot name is named as not named with
  the reason.
- Carried into the SRA packet (section 2a, a table per well, its gaps not
  counted against the packet) and the OSDU export (the Well record's
  FacilityID and a typed USWellNumber alias with the state and the parts in
  the extension; the Wellbore record's FacilityID is the twelve-digit number
  when given, otherwise the program's id with the reason).
- What it will not do: assume "00" for a sidetrack that was not given;
  invent a Contact Interval or a Completion (the permit's and the completion
  report's facts); name a county (the standard's booklet carries it).
- `gea workspace --action well-identity --well <id>`; `gea ppdm --action
  parse --number ...`; `gea ppdm` self-test; `gea help ppdm`. Section BG
  (3 checks); the gate is 327.

## [v0.12.0] - 2026-10-08 - the QuakeML catalogue export

### Added
- **The QuakeML catalogue export** (`gea/quakeml.py`): the site's own
  events - what its stations located together under the association leg -
  in QuakeML 1.2, the exchange format a regulator's catalogue tool, a
  university network's review desk and every seismological package read.
  Built to the published Basic Event Description schema
  (QuakeML-BED-1.2.xsd), record by record: one `event` per associated event
  with its `origin` (time; latitude and longitude with the misfit region's
  half-extent as their uncertainty in degrees; depth in metres with
  `depthType` "operator assigned", because it was declared; `methodID` and
  `earthModelID` naming the grid search and the one-velocity flat-earth
  model; `quality` with the station and phase counts, the RMS, the azimuthal
  gap and the epicentral distances in degrees; `originUncertainty` with the
  misfit region in metres), one `pick` per station with the record's own
  FDSN codes in `waveformID` (the association now keeps them), one `arrival`
  per pick with its residual and unit weight. Every identifier to the
  schema's ResourceIdentifier pattern.
- Every origin and pick `evaluationMode` automatic and `evaluationStatus`
  preliminary: a person has not reviewed these and the file says so in the
  schema's own words. The catalogue's standing - AGREES, DIFFERS with both
  positions, NOT IN CATALOGUE - is a comment on the event with the
  catalogue's own event id. No `magnitude` is written and a comment on every
  event says why.
- The shape checked the way a reader checks it first: namespaces, every
  publicID to the pattern and used once, every child an element the schema
  allows for its parent, the required children, the enumerations, every
  arrival's pick in its own event, every event's preferred origin. The
  output validates against the published XSD (checked in the writing of this
  leg, on the labelled scene and on the gap cases).
- What it will not write: the catalogue's events as this site's; a reviewed
  or final status; a located depth; a station code it did not read (a record
  without one goes out under the test network XX with the station id and is
  named as a gap); an azimuth for a station with no position.
- `gea workspace --action quakeml-export --site <id> [--agency] [--out]`;
  `gea quakeml` self-test; `gea help quakeml`. Section BF (4 checks); the
  gate is 324.

## [v0.11.1] - 2026-10-08 - the Linux kit's gate judges the Linux kit

### Fixed
- The Linux kit's own gate (`verify.sh`) failed at v0.11.0 on both GitHub
  kit builds; the Windows kit's passed, and so did the gate from the
  checkout. BB13, new at v0.11.0, checked the Windows launcher text for the
  browser hand-off - but on a Linux kit, which carries no Windows launcher,
  section BB had already substituted a bare stand-in text so that BB6 could
  build its good and bad kits from something, and BB13 judged the stand-in
  as if it were the launcher. It now keeps the launcher as shipped apart
  from the stand-in and judges a Linux kit on its shell launcher alone.
  Proven both ways from an installed layout with only `start-dashboard.sh`
  beside the interpreter: the shipped BB13 fails there exactly as the kit
  build did, the corrected one passes. The gate is still 320.
- The report samples re-rendered from this build.

## [v0.11.0] - 2026-10-07 - the OSDU-shaped export

### Added
- **The OSDU-shaped export** (`gea/osdu.py`): the site as the Manifest a
  large operator's data platform loads - what a buyer's data team asks for
  on the first call. Built to the published well-known schemas, not to a
  guess: `osdu:wks:Manifest:1.0.0` with each well as `master-data--Well`
  and `master-data--Wellbore` 1.0.0 (API and UIC as name aliases, the gauge
  station as a vertical measurement), each well's record as
  `work-product-component--WellLog:1.1.0` in the time domain (ZeroTime, the
  sampling interval, a curve per channel with its unit) over a
  `dataset--File.Generic:1.0.0` carrying the file's size and SHA-256, each
  seismic station as a generic component with its records as datasets, and
  the site as the WorkProduct. The files are copied beside the manifest.
- **Every position goes out twice.** The schema's SpatialLocation carries
  `AsIngestedCoordinates` - the point as the operator gave it, with that
  datum's EPSG code - and `Wgs84Coordinates`, with the operation between
  them written into `AppliedOperations` (a NAD27 well says "shift 45.4 m").
  A point on an unknown datum gets no WGS 84 coordinates at all and is
  named as a gap, because a platform that indexed it would place the well
  tens of metres wrong. The datum discipline of v0.8.0 reaches the
  platform intact.
- What the operator declares and the export never fills in: the data
  partition, the ACL groups, the legal tag. Without them the manifest is
  written and marked **NOT LOADABLE** with the missing names. A structural
  validation makes the platform loader's first checks here first: every
  record's id, kind, ACL, legal and data; every dataset a component names;
  every wellbore's well; every component the work product lists.
- What it will not write: a seismic trace component under a schema not
  read in the writing of this export. `gea workspace --action osdu-export`;
  `gea osdu` self-test; `gea help osdu`. Section BE (4 checks).
- **The console answers.** `gea serve` opens the control panel in the
  browser itself once its port is listening (`--no-browser` for a scheduled
  start or a box with no desktop), and the window it was started in says
  what happens as it happens: the page being opened, a sign-in or a refused
  one, a job starting and finishing, a stop or restart requested. An
  operator waited hours at a console that had been serving the page all
  along, because a server that answers in silence looks, from that window,
  exactly like one that is still loading. The banner now says so in words:
  this window is the server and has nothing more to load. The kit launchers
  no longer open a browser of their own on a timer - opened first, it
  showed a failure page from the second before the server was up - and a
  restart from the panel does not open a second page. BB12-BB13; the gate
  is 320.
- **The SAR panel has a page of its own** - "SAR panel" in the navigation,
  after Seismic - as well as its place at the foot of the Seismic page,
  where, below the station table and two cards, it was reported as nowhere
  to be found. The same panel, the same film. AQ4 extended.

## [v0.10.0] - 2026-10-07 - association: the stations heard the same thing, or they did not

### Added
- **Association** (`gea/seismic_assoc.py`): the piece every monitoring
  pipeline is built around and this program had none of. Until now the leg
  could say what one station heard, what direction an array heard it from,
  and where two arrays' bearings cross; it could not say that the arrivals
  at three or more stations belong to one event, or where and when that
  event was. Three honest pieces: a **pick** - the onset of an impulsive
  arrival by the short-term over long-term average, refined back to the
  first energy, carrying its time and signal-to-noise ratio and nothing
  else; an **association** - one pick per station consistent with a single
  origin under a declared velocity, found by a grid search over the ground
  with the origin time solved at every node, accepted at three or more
  stations inside a declared RMS tolerance, the worst pick dropped while
  more than three remain, and NOT ASSOCIATED below three; a **location** -
  the best node with the misfit region (the nodes within one pick
  uncertainty of it), reported as its east-west and north-south extent and
  never called a confidence interval. An array is one station: its sensors'
  picks reduce to one by the median, because sensors a few hundred metres
  apart do not constrain an epicentre tens of kilometres away.
- What it refuses, on every result: a depth (declared; every plan on file
  works from a regional value, and the model is a straight ray at one
  velocity on a flat earth, named); a magnitude (needs the instrument
  response and an attenuation relation, neither in this band); an event
  from fewer than three stations; a verdict on which of two positions is
  right when the stations and the catalogue differ - both are printed.
- `gea workspace --action associate --site <id> [--vp --depth-km --catalog]`
  picks every station of a site, associates, locates, sets the events
  against a catalogue export (AGREES, DIFFERS, NOT IN CATALOGUE, and the
  catalogue events the stations did not associate), writes
  `association.json` and audits it. The **SRA packet** now carries the
  events the site's own stations located beside the catalogue's, each
  inside or outside the area. `gea seismic --action assoc-selftest` runs
  the labelled scene. Section BD (6 checks); the gate is 314.
- Two defects found by the scene before they reached a record: the onset
  refinement walked back into the noise and every pick came out 150 ms
  early, which the location absorbed into the origin time and nobody would
  have seen; and the fine grid was centred on the stations' centroid
  instead of the coarse best node, so every event more than a kilometre
  from the centre was lost. Both are in the gate now.

## [v0.9.0] - 2026-10-07 - the Seismicity Response Area packet

### Added
- **The Seismicity Response Area packet** (`gea/sra.py`): what an operator
  inside a Railroad Commission SRA puts in front of the Commission, built
  from what the site holds. The shape is the Commission's, not invented
  here - the operator-led response plans on file (Gardendale, Northern
  Culberson-Reeves, Stanton: a circle of about 9 km, a plan written against
  M 3.5, an 18-month goal, a response group meeting within 48 hours of a
  threshold event, quarterly checkpoints with Commission staff, disposal
  wells in two tiers by the base of the Wolfcamp) and the December 2023
  Notice to Operators on disposal-well monitoring in the Permian Basin,
  which names the four daily parameters (maximum and average surface
  injection pressure, injection volume, maximum injection rate) and the
  three bottomhole-pressure methods. A downhole gauge this program monitors
  is the Notice's permanent probe.
- `gea workspace --action set-well`: what the operator declares about a
  well and the program cannot measure - surface position and datum, API and
  UIC numbers, depth tier by the named formation, which channels are the
  surface injection pressure, the rate and the downhole gauge, and how the
  bottomhole pressure is known. Audited as a declaration.
- `--action sra-define`: the area on the site - centre on a stated datum
  (carried to WGS84 with its shift named), radius, plan date, and the plan's
  numbers as declarations. `--action sra-report --catalog texnet.csv
  [--aftershocks ...]`: the packet - membership by geodesic distance; the
  daily record per well from its own channels, rolled up by month; the
  catalogue's events against the plan with the response trigger, its
  deadline and the goal clock; the checkpoint schedule; and every gap by
  name. `sra_daily_export.csv` carries the Notice's parameter names with the
  API and UIC on every line. The Sites page shows the area and the packet.
- What it refuses, and says so: a daily record from a stream with no
  calendar; a volume from a well with no rate channel (NOT RECORDED, not a
  blank); a depth tier from depth alone; an aftershock it decided itself
  (the operator declares them; they are shown as exempt); a catalogue it
  fetched (the catalogue is a file with an export date). `gea sra` runs the
  self-test on a labelled scene. Section BC (6 checks); the gate is 308.
- Report sample `sra_packet_SYNTHETIC.html`; help page `gea help sra`.

### Fixed
- The job runner's `list()` opened `job.json` directly, with none of the
  retry `_read()` had carried since the v0.7.0 kit run: on Windows a read
  during `os.replace` is refused, and the v0.9.0 ship fell over on exactly
  that in section AH. Every read of a job file now goes through one
  tolerant reader, and a job whose file cannot be read at that instant is
  left out of the listing rather than crashing it. The gate's temp folder is
  cleaned up with errors ignored, so a handle a job subprocess still holds
  cannot turn a finished gate into a traceback.

## [v0.8.1] - 2026-10-06 - the kit's gate reads the kit

### Fixed
- The installed kit's own gate (`verify.cmd` / `verify.sh`, the client's SAT
  evidence) failed at v0.8.0 on both GitHub kit builds. Section BB read the
  launcher templates from `tools/build_installer.py`, which exists in a
  checkout and not in a kit - the kit has no `tools/`. The section now does
  what the rest of the gate does: in a checkout it reads the templates, and
  in a kit it reads the launcher the kit actually installed beside its
  `python/`, found the way `gea doctor` finds it. The kit's gate therefore
  checks the launcher the client will double-click, which is the better
  test. Proven by installing the package as a kit does (no checkout beside
  it) and running the full gate from there: 302.
- The report samples re-rendered from this build.

## [v0.8.0] - 2026-10-06 - the machine behind the lines, the ground under the positions, the site that owns them, and a panel that survives the power going out

### Added
- The control panel owns its own stopping and starting, and a power cut is a
  thing it recovers from (`gea/supervisor.py`). It had one way to end: the
  window closed. Whatever shell was underneath was then what the operator was
  left looking at - and on a Windows console opened from a Python profile,
  that is a bare `>>>` prompt where a program used to be, an interpreter that
  is not this program and tells the operator nothing. So the service now
  decides how it ends and says so in its exit code (**0** stopped on purpose,
  **86** start me again), the launcher reads that code, and on anything other
  than 86 it hands the window to a **PowerShell** prompt that names the
  program and how to start it. It never falls through to the shell underneath.
- **Operations control** on the Administration page: Stop, Restart, and a
  switch for letting the launcher start the panel again by itself. A restart
  is authorised explicitly every time, through a popup whose link is the
  authorisation - a control that reboots the program on a stray click is not a
  control - and the card says who stopped the previous run, or that it was
  killed. Administrators only; an operator is refused it.
- Letting the panel start itself again after an unexpected stop is one
  character in one file (`records/auto_restart.flag`), because the launcher is
  a batch script and cannot read the workspace manifest. The manifest stays
  the record of the choice and the flag is how that choice reaches the thing
  that acts on it; a missing or unreadable flag reads as off. At most five
  stops in a row are answered with a restart: a panel that crashes while
  starting would otherwise flicker all night instead of handing over the
  error.
- Every start, stop, restart and recovery is one appended line in
  `records/runlog.jsonl`, flushed to the disk before the thing it describes is
  attempted - a log written after the event is no use to a machine that lost
  power during it. A run that logged a start and never logged a stop reads
  back as **killed**, not as a clean stop, and the difference is reported
  rather than smoothed over.
- What a restart does, in an order that is not negotiable: the live patches
  come up first, and the rebuilding of the records runs behind them on its own
  thread. A stream that is not running is losing records nothing can recover
  later; a report that is behind its source can be rebuilt at any time from
  records already on the disk. The catch-up rebuilds exactly what the
  workspace's own staleness table says is behind its source, names it, and
  leaves the rest alone. It does not call a gap in the stream recovered: what
  did not arrive did not arrive, and the records say where the gap is.
- The site is named what every document names it. The launchers created the
  first workspace under the machine's own name, so a site came up as a
  hostname and every report printed it. They now create it as `Pad 3`, the
  name the README, the help pages and the package's own docstring have used
  from the start, and `gea workspace --action rename --name ...` sets the
  name a site actually has - the same name again changes nothing and writes
  no audit line, so a launcher may state it on every start. BA4.
- `gea doctor` reads the installed launcher off the disk and says whether it
  honours the contract - the exit-86 loop, the PowerShell tail, the flag file
  - so a kit built before this existed is named as three warnings with fixes
  instead of being discovered by an operator meeting a Python prompt. With
  `--workspace` it also says whether the last run was killed and whether that
  workspace will start itself again. Section BB (11 checks) and BA4; the gate is 302.
- The site: the engagement, not the leg. The workspace held wells, seismic
  stations and tracks as peers with nothing owning them, which made this
  three tools in one package rather than one program - nobody could ask what
  a client has, or what every leg of it says, without knowing the ids by
  heart. A site holds what belongs to one client's ground, and the **Site
  Report** pulls every leg of it into one document: the wells with when their
  dashboards were written, the stations with what they can hear and whether
  their own sensors agree and which datum they are on, the tracks with what
  they resolved, and anything that has not been run since the thing it was
  made from changed. A site will not hold what the workspace does not hold, a
  site with nothing in it is turned down, and a site is a list of what
  belongs to an engagement and not a boundary on the ground. `gea workspace
  --action add-site | sites | site-report | remove-site`, all audited, with a
  Sites view on the page. Section BA (3 checks).
- How well a bearing is known, measured rather than assumed
  (`gea/seismic_uncertainty.py`). Every ellipse this leg drew came from the
  array response half-power width - what the array could resolve at perfect
  signal-to-noise, which is a floor and not an error bar. Two records of the
  same array, one clean and one barely above the noise, got the same ellipse.
  The sigma is now measured: the window is cut into equal sub-windows, each
  beamed on its own, and the scatter of their bearings is what the noise does
  to the answer - no model of the noise required. The floor is the
  beamformer's own grid step, and the sub-windows are searched on a grid
  narrowed to the slowness the whole window already found so that floor sits
  below what the noise is doing instead of hiding it.
- And the part that makes it a measurement rather than a number: `coverage`
  takes a record whose true bearing is known, runs every window, and counts
  how often the truth falls inside one sigma and two. On the labelled scene:
  CALIBRATED, 75 % inside one and 100 % inside two over eight windows, a
  median error of 0.37 degrees against a sigma of 0.54, and a recommended
  scale of 0.69 - the factor that would centre the claim exactly, returned so
  that a record where the sigma is wrong says by how much. Resampling sees
  the random part and not a bias steady through a window, and the module says
  so rather than letting it through.
- The positions are crossed with what the data is worth: `bearing_history`
  takes `measure_sigma`, and `position_history` draws each ellipse from the
  measured sigma where there is one and from the array's resolution where
  there is not, recording which. With a sigma eight times smaller the
  1-sigma ellipse is eight times smaller. Measuring costs about four times
  the beams, so it is asked for rather than assumed. `gea seismic --action
  bearing-sigma | coverage | uncertainty-selftest`, and `--measure-sigma` on
  bearings and track. Section AZ (3 checks).
- The estimator tried first is kept as a number rather than a story: a
  bearing from each coherent frequency bin scatters by 43 degrees on a 1.2 km
  array whatever the signal-to-noise, because one frequency cannot break the
  array's spatial aliasing. That is the geometry and not the data, and it is
  printed beside the real figure so the difference is visible.
- Two reports about the site rather than about one station. The leg had a
  station report and a track report and nothing that answered the first
  question a client asks and the last question an auditor asks.
- The **Seismic Dataset Report** says what is held, not what was concluded:
  every station, every record, the span it actually covers, its sample rate,
  the number of gaps in it, the checksum taken when it was brought in, the
  datum its positions are on, the band the leg uses for it, when it was last
  run, and - the part that matters - which outputs are current with the
  record against which are older than it. Touch a source file and every
  result for that station is marked stale, because a result older than the
  record it came from is not a result about that record. A station that has
  never been run is named as such. It says plainly that it describes what is
  held and not whether any of it is any good.
- The **Seismic Field Report** says what the site heard: every station, how
  far it can hear and whether its own sensors agree; every listed rig, which
  stations heard it and which did not, the rate its own lines say it runs at,
  and whether the hours it was heard agree with the hours the list declares;
  every track; and everything found that nobody listed. It states that a
  source nobody heard is not a source that was not working - it may be
  outside the radius of every station there.
- `gea workspace --action seismic-dataset | seismic-field`, each run in the
  audit log with what it covered, and a card on the Seismic page for the site
  as a whole. Section AY (3 checks).
- What else is out there (`gea/seismic_unlisted.py`). Every test before this
  one answers a question somebody already asked - here is a list of rigs,
  were they heard, where are they, when were they working - so the program
  could only ever confirm or deny what it was told. This one asks what else
  is out there, because the source nobody listed is the one nobody is
  watching. The tracked lines no listed source claims are grouped into combs;
  a comb with a fundamental is a machine running at a rate, and it is beamed
  on its own lines by exactly the code that beams a listed rig, so it
  inherits the same refusals. On the labelled scene - three rigs, one left
  off the list - the rig nobody listed comes back at 0.5 degrees of its true
  bearing and 2.2001 Hz against the scene's 2.2.
- What it will not call a machine is the point of it, because this is the one
  test whose output is a claim nobody asked for: a line on the mains
  frequency or one of its multiples is electrical and is named as such; so is
  a mains harmonic above half the sample rate, which does not disappear but
  folds back into the band at an arbitrary-looking frequency and looks
  exactly like a machine (the scene's 120 Hz lands at 30 Hz and is named
  there); a candidate that beams to zero slowness arrived everywhere at once,
  so it is in the cabling or the supply and not in the ground; a candidate
  whose lines are a whole-number ratio of a listed rate is that rig's
  harmonic; one line is not a machine and neither are two. And a candidate is
  a direction with a rate on it, never an identification - a compressor, a
  pump jack, a water pump, a passing train and a drilling rig all put lines in
  this band. With every rig on the list, there is nothing to find.
- On a site every array station's refresh runs the search (`unlisted.json`),
  and the report and the page carry it. `gea seismic --action unlisted |
  unlisted-selftest`. Section AX (3 checks).
- Is this array any good? (`gea/seismic_qc.py`). Every bearing the array leg
  ever gave rested on an unchecked assumption: that the sensors record the
  same ground motion at the same time, scaled the same way, with the same
  polarity. An array is not a set of records, it is the differences between
  them, so a fault invisible in one record is fatal across several - a dead
  channel drags the beam toward nothing, a clock thirty milliseconds out
  looks exactly like a source in a slightly different direction, a sensor
  wired backwards subtracts where it should add, and a sensor at a tenth of
  the gain means the array quietly has fewer sensors than its geometry
  claims.
- The timing test needs no reference clock. For a plane wave the arrival
  delays must lie on a plane, so the delays are measured by
  cross-correlation, that plane is fitted, and each sensor's residual is the
  part of its arrival the wavefront does not explain - the fit recovers the
  velocity and direction as a by-product, which is what makes it its own
  reference. On a clean array it returns the scene's velocity to 0.002 km/s
  and agrees with the beam to half a degree, with every residual under a
  millisecond. Machinery is narrow-band and its correlation function is
  periodic, so the search is anchored on the beam's own prediction, which
  combines many frequencies: the limit that imposes - a clock error larger
  than half the period of the dominant line cannot be told from a whole cycle
  - is stated rather than hidden. The scatter is a median absolute deviation
  so the one wrong clock cannot raise the bar until it clears it, and the
  plane is fitted again without the sensors that stand outside it.
- And what the faults were doing to the answer: `beam_cost` runs the same
  beam twice, with every sensor and with only those that pass, and prints the
  difference. On the labelled scene that is 173 degrees of bearing and a
  quarter of the best-bin coherence.
- Verdicts per sensor (DEAD, CLIPPED, POLARITY, TIMING, INCOHERENT,
  LOW_GAIN, HIGH_GAIN, INSUFFICIENT) and for the array (USABLE, DEGRADED,
  NOT_USABLE). A window with nothing coherent crossing the array returns
  INSUFFICIENT rather than calling a good array faulty, fewer than three
  sensors is turned down rather than judged, and orientation is stated as out
  of reach: these are vertical-component records. On a site every array
  station's refresh runs QC before anything that uses the array
  (`array_qc.json`), and the report and the page carry it. `gea seismic
  --action array-qc | qc-selftest`. Section AW (3 checks).
- Which datum a position is on (`gea/geodesy.py`). Every position in the
  program was a bare pair of numbers - a station, a sensor, a rig from a
  permit export, a position on a track - and nothing said which datum. A
  regulator's export in Texas is often NAD27; a network's station metadata is
  WGS84. At 31 N, 102 W those are 46 m apart, which is the size of a position
  ellipse: the arithmetic is right, the ellipse is honest, and the whole
  picture sits in the wrong place. Positions now carry their datum, every
  conversion carries what it is worth, and a position whose datum nobody
  stated is UNKNOWN - used as given, which is printed as the assumption it is
  rather than treated as a fact. The separation between two datums is
  computed at the site, never quoted, because it varies: 46 m in the Permian,
  85 m in Bakersfield, 18 m in Ohio.
- The geodesy under it: geodetic to geocentric on any of three ellipsoids,
  Vincenty's inverse for distance and azimuth on the ellipsoid (the spherical
  formula the leg used is half a percent out - 500 m at 100 km), the
  published three-parameter datum shifts, transverse Mercator (UTM) and
  Lambert conformal conic (Texas state plane) both ways, and the US survey
  foot kept distinct from the international foot, because over a state plane
  northing they differ by metres. It is checked against values this program
  did not produce - the WGS84 meridian arc to 45 N, a degree of latitude and
  a degree of longitude at the equator - and against the identities every
  projection must satisfy at its own origin.
- Carried through: `gea permits --datum --zone --unit` reads a permit
  export's datum from its own column (or the argument) and converts every row
  to WGS84 with the shift recorded in the import note, or reads state plane /
  UTM easting and northing in metres or either foot; the rigs CSV and the
  sensors CSV take a `datum` column; `workspace add-seismic --datum`; a
  station refresh checks the whole set and writes `datum.json`, and the
  Seismic Station Report and the station page say what is on what and what
  the assumption costs in metres at that site. `gea seismic --action datum |
  geodesy-selftest`. Section AV (3 checks).
- What it turns down: a NAD83 state plane zone handed NAD27 coordinates (a
  different grid, wrong by thousands of metres, so it is refused rather than
  misread); an array whose sensors are on two datums, because an array's
  geometry is the differences between its sensors; an unknown unit; and any
  claim that a three-parameter shift is survey grade - it is good to several
  metres, where a grid transformation is good to centimetres and is not in
  this program.
- The machine behind the lines (`gea/seismic_harmonic.py`). A signature was a
  flat list of frequencies; machinery is not. A pump at a given rate puts
  energy at that rate and at its multiples, so `harmonic_families` searches
  every line divided by every order for the comb that explains the most of
  them, normalises it to the largest spacing the orders found allow, and
  reports the fundamental as a rate per minute - the number a driller reads -
  instead of a bin index. It says how strong the claim is and turns down the
  weak ones: fewer than three lines is not a family (any two lines define a
  comb), a comb this record's line density would produce by accident more
  often than 5 % is `COULD_BE_CHANCE`, two spacings that explain the same
  lines equally well are `AMBIGUOUS`, a comb whose orders are more gaps than
  teeth is not reported, and a fundamental whose half would fall below the
  analysed band is flagged, because the machine may be running at half the
  rate with only its even harmonics in view.
- Lines followed through time (`track_lines`), which is what makes a
  signature survive the machine. A rate follows the load: a line at 1.40 Hz
  walks to 1.85 Hz over a tour, no single bin stands above its floor in
  enough windows, and the rig all but disappears from its own signature. Each
  line is now followed window to window by nearest neighbour within a drift
  ceiling, each peak refined inside its bin by a parabola, so a walk smaller
  than the bin width can be read; every track carries STEADY / DRIFTING /
  INTERMITTENT, its drift in Hz per hour, and a drift smaller than the bin
  width is not called a drift. `tracked_signature` is the drift-tolerant
  signature - the lines as they stood in one window - in the shape the
  signature band already reads, and `rate_history` reads the walk as a rate
  over time (`RATE_STEADY` / `RATE_VARIES`).
- Each tracked line attributed to a source (`attribute_tracks`): by
  proximity to a line that source was learned on, or - the point of the band
  - by standing in a small whole-number ratio, in a window they share, to a
  line that source already claims, so a harmonic the fixed bins missed comes
  back to its machine. A line two sources could both claim is given to
  neither, and a line no listed source claims is listed as such: something is
  making it and the program was not told what. `signature_families` then
  gives each source its own rate, from its tracked lines where they make the
  stronger family and from the bins it was learned on otherwise, and says
  which; two sources at the same rate are marked rather than told apart.
- When each source was working, measured instead of declared
  (`activity_from_signature`). Every verdict in the leg rested on the rigs
  CSV's working window; this reads it off the record - the fraction of a
  source's own lines standing above their floor, window by window, each line
  looked for within a fraction of its own frequency of where it was learned,
  because a harmonic walks as far as its order. The spells it finds sit beside
  the declared ones with AGREES / DIFFERS and the counts both ways: a permit
  date is a permission, not a drilling log, so a disagreement is a finding.
- `sideband_pairs`: lines standing symmetrically either side of a carrier, the
  spacing being a rate in its own right; an asymmetric pair is two lines, not
  a modulation.
- On a site: every station refresh - a single sensor as well as an array -
  follows the lines, finds the families, reads the rate, attributes each line
  and measures each listed source's working spells (`harmonics.json`); the
  Seismic Station Report gains the section with its tables and the station
  page the card, with the tracks drawn against time. A station with no array
  uses its own detectability test's lines per source, so one geophone can
  still say when each rig was working. `gea seismic --action harmonics |
  harmonic-selftest`. Section AU (3 checks).
- Several rigs at once (`gea/seismic_signature.py`). A source's signature is
  the set of bins that stand above the local floor while it worked alone and
  do not in the quiet windows; with it the cross-spectral matrix is sliced to
  one source's lines and beamed, which gives a bearing per source in a window
  where several are working - the step from one rig to a field. Every refusal
  is named: a source that never worked alone has no signature and no bearing;
  two sources whose lines fall within the spectral resolution of each other
  are both NOT_SEPARABLE; a source whose own lines alias on the geometry is
  ALIASED, with the peaks the array cannot separate listed instead of a
  direction. `multi_bearing_history` gives a history per source and
  `multi_track` crosses two or more arrays into a track per source. Labelled
  scenes for both: three rigs round one array (with a colliding pair on
  demand) and a two-array field. `gea seismic --action signatures |
  multi-beam | multi-bearings | multi-track | signature-selftest |
  field-selftest`.
- On a site: an array station with a rigs list learns every source's
  signature at its refresh and beams each of them in a window where several
  work (`signatures.json`, `multi_beam.json`); a track carries a track per
  source beside the whole-band one (`multi_track.json`), the Seismic Track
  Report gains the per-source section with its signatures table, and the
  Seismic station and track pages show both. Section AT (3 checks).
- The track summary says whether the source moved at all: a span inside
  twice the median 1-sigma ellipse is `NOT_RESOLVED`, because a line fitted
  through any scatter has a heading and a length.

### Fixed
- The acceptance gate opened the operator's Qt window on a desktop with
  PyQt6 installed, then waited for a person to close it - and a ship on
  Windows sat in the gate for three and a half hours with a window hidden
  behind everything else. `launch_operator_app(run=False)` builds the window
  and returns without entering the event loop; check E8 uses it. The command
  line still runs the loop.
- The service sent `Infinity` in JSON (a beam peaking at zero slowness, a
  crossing with no finite ellipse). Python's json writes and reads it; every
  browser rejects it as a syntax error, so one such number made the whole
  body fail to parse - the Seismic station page did. Non-finite numbers are
  now `null` at their source and the service refuses to emit one at all.
- A one-window array detectability test raised a numpy warning computing the
  circular spread of a single azimuth.
- The live-port commands say what to do when nothing answers. `gea
  wits0-sim` says it is the sender, that it waits until a reader connects,
  and gives the reader's command for a second window; `gea wits0 | witsml |
  opcua | mqtt` on a refused connection say what was missing (the sender in
  a second window; a real store or server in place of the example config's
  placeholder) instead of a bare WinError. The README's live-data tour says
  "in a SECOND window" and "needs a real store" where it should have.

## [v0.7.0] - 2026-10-04 - the time dimension - tracks from two arrays, the SAR panel, the Audit / Update tab, the permits importer, and the kit made the commit

### Added
- The time dimension of the second leg (`gea/seismic_track.py`). A bearing
  history per array: the beam on every window, a bearing with the array's
  tolerance where it was coherent, a gap where it was not, change points
  where the bearing moved beyond the tolerance. A position history from two
  or more arrays crossed window by window: a position only where two or more
  arrays were coherent, the bearings crossed at 15 degrees or more and the
  point lies in front of every array, every other window a gap with its
  reason, every position with its 1-sigma ellipse. The track: the principal
  line through the longest continuous segment, with heading, length and
  rate; positions that jump farther than their ellipses allow are segments
  listed apart, never joined. The verdict against ground truth (TRACKED,
  PARTIAL, NOT_TRACKED, INSUFFICIENT) with the hit fraction and the heading
  difference. A labelled scene of a bit advancing along a lateral past two
  arrays; `gea seismic --action bearings | track | track-selftest`.
- Tracks on the dashboard. `Workspace.add_track` over two or more array
  stations with an optional truth CSV (copied in, hashed), `refresh_track`
  writing every array's bearing history, the positions, the verdict and the
  new Seismic Track Report (`client_reports.seismic_track_report`) under
  `reports/seismic/tracks/<id>/`; `gea workspace --action add-track |
  refresh-track | remove-track`; routes `/api/tracks`, `/api/tracks/<id>`,
  `/api/tracks/add`, `/<id>/refresh` (a job), `/<id>/remove`; the overview
  and the home tile carry the tracks; the Tracks card on the Seismic page
  and the track view (map with every position's ellipse, a slider through
  time, the bearings over time, the positions table with hits).
  `refresh-all` and the report ages include tracks.
- The SAR panel's second scene: a bit advancing along a lateral past two
  arrays, with both arrays' bearings, the crossing with its ellipse and the
  track drawn as it is earned, and the tracker's verdict at the close -
  still SIMULATION_SELF_TEST on every frame. A Scene selector on the panel;
  `--scene lateral` on `sar-film`.
- The permits importer (`gea/permits.py`, `gea permits`). A permit export
  (a regulator's query CSV/TSV, an operator's schedule) to the rigs CSV the
  tests read: columns found by name with a mapping file to override, the
  approval date used only where the spud date is empty and said so in the
  row, end dates assumed and counted, rows without a position or a date,
  duplicates and rigs outside a radius dropped and counted - all in an
  import note beside the output. `add-seismic --permits` and the page's add
  form take a permit export in place of the rigs CSV and keep both.
- The job runner on Windows: a reader could hit PermissionError while the
  writer was replacing `job.json` (the v0.7.0 kit's first Windows run, in
  section AC); both sides now retry through the moment, as they already did
  for a half-replaced file.
- The seismic track report sample (19 samples); sections AR (1 check: the
  tracker and its scene, the self-test) and AS (3: tracks in the workspace
  and the report, the API, the page and the lateral film, the permits
  importer). The gate is 266.
- The SAR panel on the Seismic page: a control panel (scene length, seconds
  per frame, band, beamformer, seed; Run the scene; Play, Pause, Stop, speed,
  a frame scrubber) and a screen that plays the second leg's arithmetic
  forward in time on the labelled synthetic array scene. `gea/seismic_film.py`
  makes the film - one frame per window with the trace envelope, the spectrum
  column, the beam power grid on the slowness grid, the bearing, which rig
  works and the per-rig tally - and closes it with the array detectability
  test's verdict on the whole scene, the same function a real record runs.
  Every frame and the film carry SIMULATION_SELF_TEST and the screen prints
  "not a measurement of any ground" on each; a real record never goes through
  the film. `gea seismic --action sar-film --out film.json`; `gea workspace
  --action sar-film` writes it under `reports/seismic/SIMULATION/`; the
  routes `GET /api/seismic/sar` and `POST /api/seismic/sar/run` (operator, a
  job, parameters checked).
- The Audit / Update tab. The whole audit log with filters by who, by action
  and since a date (`/api/audit?actor=&action=&since=&limit=`) and a CSV
  download; Administration keeps its last hundred lines and points here. The
  program card (`GET /api/update`): running version and code path, the
  newest on PyPI, the extras installed here, links to PyPI, the releases and
  the CHANGELOG, and "Update the program from PyPI" (admin) - the new `gea
  update [--check] [--extras ...]` command as a job, pip --upgrade from the
  same Python, after which the service must be restarted. The data card:
  every report against its source (`Workspace.report_ages`, `current` or
  `stale`) and "Update every report from its source" (operator) - the new
  `gea workspace --action refresh-all`, the dashboard for every well then
  every seismic station, errors collected and listed.
- Help page `audit-update` (sixteen pages); the seismic page names the film
  and what it is not. Section AQ (4 checks): the film engine, the workspace
  and CLI, the routes and roles, the page and the help. The gate is 262.

### Fixed
- The kit carried the released wheel, not the commit. `tools/build_installer.py`
  built the checkout's wheel into `wheels/` and then asked pip to download
  `gea-program[extras]` with the index on; pip took PyPI's wheel of the same
  version and wrote it over the one just built, so kit runs #4, #5 and #6 of
  v0.6.0 all installed and gated the released 0.6.0 - which is why run #6
  failed on AN4 after AN4 had been fixed. The builder now reads the
  dependencies from the built wheel's own `Requires-Dist` (base and the named
  extras) and never names gea-program to the index, asserts afterwards that
  the only package wheel in the kit is the one it built (same SHA-256), and
  records the commit in MANIFEST.json.
- The installed kit's gate. Check AN4 asked for `tools/seismic_reader_check.py`
  beside the package, which is true in a checkout and false in every
  installed kit (site-packages has no `tools/`), so both v0.6.0 kit jobs
  built, installed and then failed their own `verify` at 257/258. The check
  is now asked only where a checkout is (where `pyproject.toml` sits beside
  the package), the way AJ1 and AM1 already read their checkout-only files.
  Proven by running `gea accept` from the package installed in a bare
  virtual environment with nothing beside it.
- The kit workflow. `attach-kit.sh` on a push to main now creates the
  release when the tag exists in the repository but a failed tag run left no
  release (v0.6.0's case), using SHIP_MESSAGE.txt as the notes when its
  first line is that tag; the workflow also runs on pushes that touch the
  attach script or `gea/acceptance_tests.py`, since the gate is what the
  installed kit runs.
- A missing input file at the prompt (`gea survey mywell.las`, `gea
  transient --file historian.csv`, `gea seismic --file tx.mseed` with no
  such file) printed a Python traceback. The console entry point now
  answers `gea <command>: no such file: <name>` and exits 2.
- The tester guide said the Verification page runs "the same 189-check
  gate"; the gate is 258 and the number had gone stale twice. The guide
  names no count now, and AJ1 refuses one.
- `gea doctor` said "no `gea` launcher in C:\Python314\Scripts" on a machine
  where `gea.exe` was in the per-user Scripts folder (a `pip install --user`
  or a "Defaulting to user installation"). It looks in both now and names
  whichever holds the launcher.

## [v0.6.0] - 2026-10-04 - the second leg whole - the reader proven on real files, the response, the array step, and the Seismic page

### Added
- The second leg on the dashboard. `Workspace.add_seismic_station` /
  `refresh_seismic` / `remove_seismic_station` / `seismic_results`: a station
  (one miniSEED or SAC record) or an array (one record per sensor plus a
  sensors CSV) under `seismic/<station>/`, copied in verbatim and hashed with
  its position, band, rigs list and StationXML; the refresh runs the leg
  (response removal when the station file is there, Welch spectrum,
  persistent lines, the detectability test, the beam and the array test for
  an array) and writes the machine JSONs and the new Seismic Station Report
  (`client_reports.seismic_station_report`) under `reports/seismic/<station>/`.
  `gea workspace --action add-seismic | refresh-seismic | remove-seismic`.
  Service: `GET /api/seismic`, `GET /api/seismic/<id>`, `POST
  /api/seismic/add` (upload), `/api/seismic/<id>/refresh` (a job),
  `/api/seismic/<id>/remove` (admin); the overview carries the stations.
  Page: the Seismic view (list, add by upload, refresh, remove), the station
  view (spectrum drawn on a canvas, lines, detectability, array), a nav
  entry, a home tile; both views map to the seismic help page.
  `docs/report_samples/seismic_station_report_SYNTHETIC.html` from the
  labelled scene, through the workspace. Section AP (4 checks). Gate: 258
  checks.
- `gea/seismic_array.py`: the array step. Sensor geometry on the local plane;
  the Welch cross-spectral matrix; Bartlett and Capon beamforming over a
  slowness grid, refined around the maximum; the array response function
  with its half-power width (the bearing's resolution) and aliasing lobes;
  per-frequency coherence at the found slowness and the coherent
  frequencies; the weighted crossing of several arrays' bearings with its
  1-sigma ellipse, crossing angle, residuals and the in-front check;
  location from station-pair lags (envelope cross-correlation, Gauss-Newton)
  with the velocity named as an input; the array detectability test
  (POINTED / NOT_POINTED / INCOHERENT / AMBIGUOUS / INSUFFICIENT_WINDOWS)
  against the ground-truth list with the array's own tolerance; a labelled
  synthetic array scene and self-test. `gea seismic --action beam |
  array-detect | locate | array-selftest`; `--files`, `--sensors`,
  `--bearings`, `--method`, `--seg`, `--smax`; `--start/--end` window the
  beam. Section AO (4 checks): a plane wave's direction and slowness
  recovered with the ARF width beside them; three bearings crossing at their
  point and six lags locating theirs; the scene's four rigs POINTED from 6 to
  45 km with a wrong bearing NOT_POINTED and noise INCOHERENT; the command
  line.
- `gea/seismic_response.py`: the instrument response. FDSN StationXML read
  into channels and stages (poles/zeros in radians, hertz or z-transform;
  coefficient and FIR stages with symmetry, decimation and delay correction;
  stage gains; polynomial stages named and refused); the response evaluated
  as evalresp evaluates it and checked against the declared sensitivity;
  removal in the frequency domain with a water level and a cosine pre-filter
  to VEL (m/s), DISP (m) or ACC (m/s^2); `fdsn_stationxml` to fetch the file.
  `gea seismic --action response | remove-response`, `--stationxml` on
  spectrum, lines and detect, `fetch --with-response`. Every trace now carries
  its unit and every spectrum the unit of its trace.
- `gea/reference/IU_ANMO_10_BHZ_response.xml` (IRIS StationXML, with
  provenance) and evalresp's values at seven frequencies for VEL, DISP and
  ACC: the response evaluation's independent reference. Section AN (4
  checks): the file's stages, the evaluation against evalresp to 1e-5 in
  amplitude and 1e-6 degree in phase, a known motion through the response
  and back with no error in the band, the six refusals, the command line.
- `tools/seismic_reader_check.py`: the miniSEED reader against libmseed on
  the obspy test corpus (about ninety real-station and odd-recorder files,
  fetched with pip, not redistributed). Result on the maintainer's machine:
  76 files sample-exact, 5 text/log files skipped by design, 0 mismatches;
  SAC: 12 of 12 readable files exact against obspy.

### Changed
- `seismic.py`: the Steim decoders follow the reference implementation's
  byte-order rules (8-bit differences in memory order, 16-bit in memory
  order as int16, multi-bit fields from the swapped word), which little-endian
  Steim data needs; a blockette 1000 byte-order value other than 0 is
  big-endian; a data offset of 0 means no data; text (log) records are listed
  as skipped, not read as samples; the gain-ranged formats GEOSCOPE, CDSN, SRO
  and DWWSSN and 24-bit integers are decoded; a record that cannot be read is
  written down and stepped over (`read_mseed(...).skipped`); SAC files with
  no reference time or NUL-terminated strings read.

### Fixed
- The install kit's `install.cmd` failed on the v0.5.0 runner at the pip
  step: "To modify pip, please run ... python.exe -m pip install ...". pip,
  run as `python wheels\pip-*.whl\pip`, refuses on Windows to install pip
  itself; `python -m pip` is the only form it accepts, and the embeddable
  Python ignores PYTHONPATH, so the wheel cannot be put on the path that way.
  The kit now carries `pip_bootstrap.py`, which puts the pip wheel on
  `sys.path` and runs pip as a module from inside it (argv[0] is then pip's
  own `__main__.py`, the form pip accepts); `install.cmd` calls it. The Linux
  kit installs into a venv with `-m pip` and was never affected.
- The kit workflow now also runs on a push to `main` that touches the kit
  builder, the workflow or `pyproject.toml`, so a kit fix is proven by the
  push that carries it (GitHub showed no "Run workflow" button for the manual
  trigger, and "Re-run jobs" on a tag's run re-runs the tag's own commit, so
  neither could run a fix). The attach step moved to
  `.github/workflows/attach-kit.sh` with one rule: a tag run attaches to its
  tag and replaces; a manual run with `release_tag` attaches to that tag and
  replaces; a main run attaches to the release of the version in
  `pyproject.toml` only where that platform's kit is missing, and never a kit
  whose version is not the tag's. The Linux kit is uploaded as a run artifact
  too.

## [v0.5.0] - 2026-10-03 - standard physics only, the second leg begins, the report samples, and the kit made whole

### Removed
- The program's own gauge aging model. Until v0.4.0 the drift band had two
  edges: the instrument's datasheet aging rate, and a second model that
  scaled it by a fixed composition of engineering constants inherited from the
  predecessor program - a fixed divisor just below one on the datasheet rate, with no
  field validation on record. Both the model and its constants are removed
  from the package (`quartz_hpht_extension.py` deleted), together with
  everything built on the comparison between the two: the two-leg bench ratio
  test, the service-life divergence curves, the depth-sweep case study
  (`case_study.py` deleted, `gea case-study` removed), the instrument "trims",
  and the suppression panels of the desktop windows and the demo. The
  datasheet rate alone remains, and the program adds nothing to it.
- The template gauge preset (a baseline and stress "knees" and "exponents"
  that were engineering fits from the predecessor's design note). The default
  datasheet is now the cited GEOQ 177 30,000 psi entry; `GaugeSpec` carries
  only published numbers (full scale, drift specification, accuracy, rating)
  and its citation, and a datasheet JSON with any other field is declined.

### Added
- The second leg begins: `gea/seismic.py` reads miniSEED (SEED 2.4 data
  records; Steim1, Steim2, int16/int32, float32/float64; byte order and record
  length from the header and blockette 1000; records joined into traces with
  gaps listed) and SAC, both decided by content; writes both (so a recorder's
  CSV can be archived in the format the archives use); fetches from any FDSN
  dataselect/station service (`--base texnet`, `--base iris`, or a URL) with
  the request filed beside the file; and produces Welch PSDs, spectrograms,
  band power and the persistent lines above a running-median floor. The
  Steim decoders are proven bit for bit against records written by libmseed
  (`gea/reference/`, with provenance), in both directions.
- `gea/seismic_detect.py`: the detectability test - one station's record
  against a CSV of known rigs (position, working window); verdict per rig
  (DETECTED / NOT_DETECTED / AMBIGUOUS / INSUFFICIENT_WINDOWS) from band power
  in the rig's exclusive windows against the quiet baseline; the lines that
  belong to a detected rig and not to the quiet hours; the radius bracketed
  between the farthest rig heard and the nearest not heard; NO_QUIET_BASELINE
  when the record has no hour with every listed rig down. The report prints
  its basis and what it will not call a measurement. A labelled synthetic
  scene (`SIMULATION_SELF_TEST`) exercises every verdict.
- `gea seismic` (info, spectrum, lines, fetch, stations, detect, convert,
  selftest); the `seismic` help page; `files.detect` names `mseed` and `sac`
  and the well import refuses them; `gea/reference/*` as package data.
- Acceptance section AL (5 checks): the libmseed records decode to the filed
  series; the writer's records read back in every encoding and a gap splits a
  channel; SAC round-trips; the spectra find exactly the tones put in and
  Parseval holds; the detectability test earns every verdict on the scene;
  the CLI, the help page and the FDSN URL builder work without a network.
  Gate: 246 checks.
- `docs/report_samples/`: one rendered example of every client report
  (dashboard index, gauge drift for the two Volve wells and for a monitored
  synthetic historian, well-test validation, alarm and event, data
  resilience, the six model cards, the accuracy statement, a monthly SLA
  report, the SAT protocol), produced by `tools/render_report_samples.py`
  from this checkout and named in `SAMPLES.md` with the command behind each;
  the synthetic ones say SYNTHETIC. Shipped inside the install kit as
  `report-samples/`. Section AM (1 check) holds the set to the build.
- The install kit carries every optional dependency by default (`desktop`
  added: PyQt6), so `gea sbom` from a kit lists them all as installed.
- `gea/gauge_aging.py`: the one aging function - the datasheet's published
  drift specification as the rate, with an `over_rating` flag above the
  instrument's rated temperature or full scale (flagged, never changed).
- `gea bench` rebuilt as the datasheet-conformance test, the bench record the
  drift report cites as NONE ON RECORD until it exists: one gauge against a
  reference standard at a held setpoint, the drift slope with its standard
  error against the datasheet specification, verdicts WITHIN_DATASHEET /
  EXCEEDS_DATASHEET / INSUFFICIENT_SPAN / INSUFFICIENT_SNR, the serial and
  certificate on the record, a JSON-lines register (`--out`).
  `BENCH_TEST_PROTOCOL.md` and `docs/commercial/BENCH_READINESS.md` rewritten
  for it.
- `gea service-life`: years to the error budget from the datasheet rate per
  station; a station whose tool has no published rate carries none.
- `tool_library.user_gauge_tool`: a tool from the user's own datasheet.
- Acceptance section AK (4 checks): the rate is the datasheet number at every
  temperature and pressure; the removed model does not import and none of
  its names, constants or ratio appear in any module, help page or protocol;
  a datasheet cannot carry model parameters; the reconciler's band and the
  simulator's outputs carry no model quantity. `tools/standalone_check.py`
  blocks the removed model's names, phrases and numbers in every tracked file.

### Fixed
- The install-kit workflow's Windows job (`build-installer.yml`) failed on the
  v0.4.0 tag in 38 seconds at "Install the kit the way a client does": the
  step opened the kit folder with `cd dist\gea-program-*-win64`, and cmd.exe's
  `cd` does not expand wildcards. The folder is now resolved with `for /d`
  first. The Linux job had passed and attached its kit to the v0.4.0 release;
  the Windows kit was never built there. A manual run (`workflow_dispatch`)
  now takes an optional `release_tag` and attaches both kits to that existing
  release, so the v0.4.0 Windows kit can be added without a new tag.

### Changed
- `reconciler.py`: DRIFT_CONSISTENT is a slope between half and twice the
  datasheet rate; the station record carries `datasheet_rate_psi_yr`,
  `datasheet_accuracy_psi` and `over_rating` instead of an envelope pair.
- `client_reports.py`: the drift table's "Aging envelope" column is
  "Datasheet aging rate"; the model card is `gauge_aging_rate`.
- `downhole_engine.py`: each station's aging rate comes from its tool's
  datasheet; a tool whose cited page publishes no rate (the piezoresistive
  class) carries `NO_RATE_ON_RECORD` rather than a representative fit; the
  CSV export carries readings only.
- `tool_library.py`: the quartz entries are `quartz_pt_geoq177_30k` and
  `quartz_pt_geoq177_16k`; the piezoresistive class is PARAMETERS_USER_SUPPLIED.
- Acceptance sections A, C, E, H, I, AA re-derived against the datasheet-only
  engine (the injected-offset checks AA17/AA34 now measure 43.1 psi for a
  +40 psi injection, the simulator's synthetic noise having lost the removed
  model's shaping).
- The gate banner names GEA-Program.

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
