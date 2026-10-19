# Changelog

All notable changes to GEA-Program, newest first. Each released section is
headed by its tag and date; `ship.ps1` refuses to ship a tag that has no
section here. The long-form record, by layer, is `docs/HISTORY.md`; the
session-by-session working record is `docs/SESSION_LOG.md`.

## [v0.47.0] - 2026-10-19 - Rig time

### Added
- **Rig time** (`gea/rig_state.py`, the Rig time tab, `gea rig-time`): the
  rig state of every second of a well's drilling record - ROTARY and SLIDE
  DRILLING, IN SLIPS (the hook load below the block weight and half again,
  the bit still, for half a minute or more), OFF THE HOOK (the same for half
  an hour or more), REAMING, WASHING, CIRCULATING, TRIPPING IN and OUT,
  STATIONARY. The block weight is estimated from the hook load while the
  block moves and the bit does not (or declared per well) and printed. A
  string pushed into a lateral, a depth re-set and a string hung up for
  seconds are not taken for the slips or for pipe moving.
- **Drilling connections**, weight to weight (the last second drilling to
  the first second drilling again) with off bottom to slips, in the slips,
  and slips to drilling; the breaks that are not connections listed with why.
- **Trips** of 1,000 ft or more, split where the bit turns back 500 ft:
  speed over the pipe-handling hours (moving and in the slips), the moving
  speed alone, the breaks inside, the stands and their slips time.
- **Time lost** (invisible lost time): each connection's weight to weight
  and each trip's pipe-handling hours past the target - the drilling
  program's, declared with its source, or this pad's own best quarter -
  summed per well and per report day, and priced at the contract's operating
  rate when the invoice check's terms are declared. The invoice check shows
  each report day's lost time beside the bill (not counted in question).
- Client reports `reports/rig_time_pad.*` and `reports/wells/<well>/rig_time.*`
  with their PDFs. Help page `rig-time`; section CP (3 checks).

## [v0.46.0] - 2026-10-19 - The invoice cross-check

### Added
- **The invoice cross-check** (`gea/invoice_check.py`, the Invoice check
  tab, `gea invoice-check`): the drilling contractor's daywork invoice set,
  report day by report day, against the daily drilling reports and the
  drilling record, at the contract's terms as the operator declares them
  (the report day's start on the rig's clock, the operating, standby,
  repair, moving and force majeure day rates, the repair allowance in hours
  a job and a month - IADC U.S. Daywork Contract 4.3-4.7).
- The checks: the report's lines add to 24 h; reported drilling (IADC code
  2) against the record's hours on bottom; the day's end depth and footage
  against the record's; a line coded drilling, reaming, circulating or trips
  over a span the record shows the pumps off, the rotary still and the bit
  still, and a repair line over a span it shows working; over 24 h billed,
  a day billed twice, billed with no report or nothing on the record;
  standby (code 33, or waiting on the operator's orders) billed at the
  operating rate; repair (code 8) past the allowance at the operating rate;
  a unit price not the contract's; quantity times price not the amount; and
  as a question, quiet on the record beyond the hours billed at standby,
  repair or force majeure. Each day AGREES, QUESTION or DIFFERS, each
  finding with the dollars it puts in question and the file and row it
  comes from; charges that are not day rates are listed, not checked.
- The files are CSV read by their headings in any order and spelling
  (`--template ddr | invoice` prints them); a client report with its PDF in
  `reports/wells/<well>/invoice_check.*`. Help page `invoice-check`;
  section CO (3 checks).

## [v0.45.0] - 2026-10-19 - The lateral, the printable reports, the daily summary and the key

### Added
- **Distance from the lateral** (`gea/op_seismicity.py`): a well with a
  directional survey is measured from its path, not its pad - the survey's
  stations by minimum curvature, a point every 25 m of measured depth on the
  arc, the azimuths turned to true north at the well (grid convergence, or
  the declared magnetic declination), and each earthquake's distance in plan
  to the nearest point, with the measured depth there, the distance to the
  hypocentre (when the reference elevation and the depth are known) and the
  distance from the pad beside it. The protocol radius, the association
  radius and the rate test use it; a well without a survey is measured from
  its pad and says so. The watch circle and the catalogue query reach as far
  as the laterals. Alerts name the lateral and the measured depth.
- **The printable PDF** (`gea/pdf_report.py`): every client report written
  through `client_reports.write` is also written as a PDF beside its HTML -
  US Letter, the PDF base fonts (nothing embedded), the report's SVG
  drawings redrawn as vector paths and text, tables continued across pages
  with the header repeated, "page n of N" on every page; the same bytes for
  the same report. Standard library only (zlib). Kept in report history;
  linked first on every report.
- **The daily pad summary** (`gea/pad_summary.py`, `gea pad-summary`, the
  Reports tab): one page - what changed since the last (the audit log by
  action, the alerts, the report versions, the earthquakes first seen),
  what is due (open alert conditions, components near or past their
  interval, operations under way or starting within seven days, the watch,
  the backup, the last dossier) and what is still missing (the setup
  checklist's items to do). Written by the service each day at its hour
  (11:00 UTC by default) and sent as the new event `pad.summary` through the
  notification rules, whose email carries the summary's text.
- **Signing-key rotation** (`gea dossier --rotate-key --reason`,
  `--key-history`, the Reports tab, the administrator role): a new key; a
  statement naming it signed by the old key and the new, then the old seed
  destroyed. Dossiers signed with a retired key before its retirement still
  check out; one signed with it afterwards is not the site's. Every later
  dossier carries `keys/rotations.json` under its signature, and the
  standalone checker verifies each rotation and prints the chain. A key file
  found missing at signing is recorded as a rotation the old key did not
  sign. Help page `pad-summary`; `dossier`, `opseis` and `report-history`
  updated; section CN (5 checks).

## [v0.44.0] - 2026-10-19 - The earthquake watch and the alerts

### Added
- **The live earthquake watch** (`gea/seismic_watch.py`, the Operations &
  Seismicity tab, `gea seismicity --watch-once | --watch on|off |
  --watch-every N | --watch-mode`): from the service's background poller,
  while a declared operation is under way at a well with a position (or
  always, if set), every 5 minutes by default. A poll reads the USGS
  real-time summary feed of the past day (all magnitudes) and keeps the
  earthquakes in the circle around the operating wells; on the first poll or
  after a gap of a day it asks the FDSN event service for the whole gap (at
  most 30 days). Each earthquake's newest version is kept
  (`seismicity/live/`) and written out as a catalogue the tab reads.
- **Alerts from the watch**: `seismic.event` (an earthquake within the
  association radius during or after an operation, once), `seismic.protocol`
  (a protocol level reached, once per level; a revision to a higher level is
  a new alert), `seismic.watch_down` after three intervals without an answer
  while an operation runs, and `seismic.watch_up`.
- **Condition alerts** (`gea/alerts.py`, `gea alerts [--check]`):
  `motor.due` and `penetrator.outside` checked every 15 minutes,
  `dossier.changed` (the newest dossier of each well and the pad re-checked)
  daily, and `condition.cleared` when one ends.
- **Every alert recorded and sent**: `records/alerts.jsonl`, listed on the
  Alarms tab's new Program alerts card (with where each was sent, and a
  notice when no channel is set up), and handed to the notification rules -
  the eight new events are accepted in `notifications.json` with their own
  subjects.
- The rate test says NO BACKGROUND when a catalogue covers only the
  operations. Help page `alerts`; section CM (3 checks).

## [v0.43.0] - 2026-10-18 - Report history and the signed dossier

### Added
- **Report history** (`gea/report_history.py`): every client report written
  inside a workspace's reports folder is kept - a copy of each file it
  wrote in `reports/history/<where>/<report>/<UTC time>/`, and a line in
  `reports/history/index.jsonl` with the report id and each file's size and
  SHA-256. The report at its usual path is unchanged. The Reports tab lists
  every report's versions with links; `gea reports-history` lists them and
  `--prune --keep N --apply` trims them on request. Reports written outside a
  workspace keep no history.
- **The signed dossier** (`gea/dossier.py`, `gea dossier`, the Reports
  tab): a well's, or the pad's, reports (each with its history version),
  declarations (identity, position, survey, rates and AFE, runs, penetrator,
  operations, catalogues, fleet), audit log with its line-by-line hash chain,
  and the source records by hash (or embedded), in one zip with a manifest of
  every file's SHA-256, signed with the site's Ed25519 key; each dossier
  names the previous one's manifest hash, so a site's dossiers form one
  chain, recorded in `reports/dossiers/ledger.jsonl`.
- **Ed25519 in the standard library** (`gea/ed25519.py`): RFC 8032, checked
  against the RFC's test vectors. The site key is made on first use in
  `keys/dossier_ed25519.json`, outside every report, export and evidence
  pack; its fingerprint is shown on the Reports tab to give to clients.
- **A checker anyone can run** (`verify_dossier.py`, inside every dossier):
  Python 3.8+ alone - the signature, every file's hash, nothing unsigned
  riding along, and with `--sources` the source records. Inside the
  workspace, `gea dossier --verify` and the tab add this site's key and
  ledger, the source records as they are now, and the audit log still
  holding the lines the dossier chained.
- The evidence pack leaves out the history and dossier folders and carries
  their index and ledger. Help pages `report-history` and `dossier`; section
  CL (3 checks).

## [v0.42.0] - 2026-10-18 - Operations and seismicity

### Added
- **The Operations & Seismicity tab** (`gea/op_seismicity.py`,
  `#/opseis`, `gea seismicity`): each well's operation windows against the
  earthquake catalogue around it. Every event within the screening
  distance (20 km) with its distance from the well and its relation -
  DURING an operation, AFTER one (within the association window, 90 days
  by default), OUTSIDE - on a time line by distance, sized by magnitude,
  with a zoom on each operation; an event near two wells' operations named
  on both.
- **The rig at the time**: a well with a drilling record carries the
  record's span as a drilling window, and an event during it shows what
  the rig was doing (drilling, circulating, not pumping) and the hole's
  depth then.
- **Protocols** (`reference/seismicity_protocols.json`): British
  Columbia's Kiskatinaw order (2021), Alberta's Subsurface Order No. 2
  (2015), Oklahoma's completion notice (2016), Ohio's 2014 permit
  conditions (a secondary summary, marked so) and Texas's Response Plan to
  Seismic Events (2022), each level with its action and the document; an
  event's highest level reached by the catalogue's magnitude, the
  after-restart levels conditional, British Columbia's cluster rule.
- **The rate test**: events at or above the completeness within the
  association radius, inside the operation windows against outside them
  over the catalogue's window, by the exact conditional test for two
  Poisson rates; the ratio, or with no background event its 95 % upper
  bound.
- **Catalogues and operations**: catalogue exports (FDSN text such as
  ComCat, or a TexNet CSV) attached with the window, minimum magnitude and
  circle they were queried for, kept verbatim with their SHA-256; the
  ComCat query drawn around the wells for the site to run; operation
  windows declared with their source; the declarations sheet gains
  `[operations]` and `[catalogs]`.
- **The worked example**: the Mahoning Valley, Ohio - ComCat 1990-2026
  within 30 km (21 events, read twice and compared), Northstar 1's
  injection year (Kim 2013) and the Poland-CLL2 stimulation of March 2014
  (the state's inspection and the regulator's halt), positions from the
  state's well records.
- Help page `opseis`; section CK (3 checks).

## [v0.41.0] - 2026-10-17 - The declarations sheet

### Added
- **The declarations sheet** (`gea/declarations.py`, the Setup tab, `gea
  declare`): one CSV of small tables - `[wells]` (API number, permit name,
  operator, position and datum, reference elevation with its source),
  `[fleet]`, `[rates]`, `[items]`, `[afe]`, `[surveys]` (a file beside the
  sheet), `[runs]` with their `[components]` from the bit up (a catalogued
  motor or the motor's own figures), `[penetrators]` and `[esp_drives]`.
  Every row is checked against the workspace and planned NEW, CHANGE or
  SAME; one error and nothing is written; with `--apply` (or Write on the
  Setup card) each row goes through the function its own command uses,
  audited under the importer. The import adds and replaces, never removes.
- **The sheet for a workspace** (`gea declare --template`, Download the
  sheet): every table with its header and a line saying what it takes,
  filled with what is already declared; imported unchanged it plans only
  SAME.
- **The Completion tab** (`gea/penetrator.py`, `#/completion`, `gea
  penetrator`): each ESP well's penetrator installation record (installer
  and certification, motor type, gauge distance, insulation per conductor
  with the test voltage and the site's own acceptance minimum, void test)
  and its ESP drive, motor nameplate and wellhead checked against the
  penetrator's ratings line by line - WITHIN, OUTSIDE, NOT RATED, NOT
  DECLARED, NOT STATED. Altitude comes from the well's declared elevation.
- **Penetrator ratings** (`reference/penetrator_ratings.json`): the Genco
  GPS-5 (IP-40, Rev. 0, Appendix A: 5,000 V, 300 A FLA, 3 phase, 10-100 Hz,
  10,000 psi proof, 5,000 psi API, Class I Division 1 and 2 Group D, Zone 1
  and 2 IIA T5, -30 to 110 F, 2,000 m), the GPS-5D and GPS-3 with the
  Division 2 of their titles. Ratings and statements of use only; none of
  the instructions' procedure text. A fleet tool of the new class carries
  its own datasheet's ratings.
- **The permanent magnet motor safety note**, with its sources (World Oil,
  June 2024; Levare), on the Completion tab and in a PMM well's check.
- **A tool-library class**, the wellhead electrical penetrator (97
  classes), with its tutorial and sources.
- **Two downhole motors** read from the maker's sheets (7 in 8/9 4.3 and
  8 in x 7 3/4 in 7/8 5.9), each twice; every other motor's flow, rev/gal,
  differentials, torque and weight on bit read a third time and agreed.
  43 motors.
- Help pages `declare` and `penetrator`; section CJ (3 checks).

## [v0.40.0] - 2026-10-17 - Drill string

### Added
- **The Drill String tab** (`gea/drill_string.py`, `#/drillstring`): each run
  the operator declares - the string from the bit up, every component a
  class of the tool library with its maker, model, serial, outside diameter
  and length, the stretch of the drilling record it covers on the
  recorder's own clock - drawn as a build diagram (widths to the outside
  diameters, the distance above the bit at each joint) beside its table.
- **Service life and tracking**: from the drilling record over each run's
  span, the hours in the hole, circulating, rotating and drilling, the
  footage and the gallons pumped, and a motor's revolutions; summed per
  serial (or fleet entry) across every run of every well against the
  service interval declared for it, counted in the basis the operator
  names, with its source - OK, NEAR or DUE.
- **The downhole motor tracker**: the differential on bottom against the
  maker's maximum operating and maximum differential, hour by hour and as
  hours on bottom per 100 psi; the hours above the first; the stalls (at or
  above the maximum while pumping, two seconds or longer); the motor's
  no-load speed (revolutions per gallon times the flow) and the bit's (the
  rotary's plus the motor's); a torque estimate from the maker's slope; the
  rotor's turning and whirl frequencies, for a dynamics sub to confirm.
- **Stability from the frequencies the record carries**: the surface torque
  and the differential, block by block of about two hours of drilling,
  through Welch's method in 0.03-0.45 Hz - the distinct oscillation
  frequency (a peak inside the band four times above its median) and the
  oscillation index (the band's RMS over the mean) charted across the run,
  the spectrum of its first third against its last, and a stated rule that
  calls a moved frequency or a changed amplitude a change.
- **Downhole motors in the tool catalogue**: 41 power sections with the
  maker's flow range, revolutions per gallon, maximum operating and maximum
  differential, maximum torque, weight on bit, bit sizes and connections,
  from MS Directional's Motor Handbook (April 2021), each figure transcribed
  twice and compared (`reference/mud_motor_specs.json`); a table on the
  Tools tab, `gea tools --action motors`, `/api/tools/motors`.
- `gea drillstring --workspace` prints it; `--declare run.json`,
  `--remove RUN`. API `/api/drillstring` and
  `/api/drillstring/{run,remove}` (declaring needs the operator role).
  `gea help drillstring`.
- Section CI (3 checks); the gate is 407.

### Fixed
- The Tools tab's hidden tool tips no longer widen the page.

## [v0.39.0] - 2026-10-17 - Simulation

### Added
- **The Simulation tab** (`gea/simulation.py`, `#/simulation`): one well
  drilled again on the screen. The bit runs down the well's path in 3D among
  the mapped formations - the 3D tab's own renderer, the hole drilled so far
  see-through with the drill string inside it, the path beyond the bottom
  of the hole ghosted - on the clock the well was drilled by, while a
  mud-log panel beside it grows with the hole: the formations as the picks
  name them, the rock and fluid volumes, up to six log and drilling tracks,
  the bit and the bottom of the hole. A readout gives what the record
  carries at the bit, or at any depth under the pointer; a click on the
  panel takes the bit to the moment it first reached that depth. Play at
  1x (the whole record in two minutes) to 64x, step, scrub; the depth
  window from 50 m to the whole well; the log below the bit hidden until it
  is drilled, or shown.
- **Volve 15/9-F-4 with its logs and their interpretation**: the
  logging-while-drilling record of its 8.5 in reservoir section (gamma ray,
  bulk density, neutron porosity, photoelectric factor, resistivities, rate
  of penetration) and Schlumberger's petrophysical interpretation of it for
  StatoilHydro (mineral volumes and total porosity summing to one, the
  water saturation of the uninvaded zone, the hydrocarbon volume, the
  permeability), shipped verbatim and gzip-compressed with their hashes,
  licence and attribution (`reference/volve_f4_*`, built by
  `tools/build_volve_logs.py` from a pinned public copy of the Volve
  release). Its survey carries no drilling times, so its bit is stepped
  along measured depth, and the page says so.
- **On the clock each record carries**: Volve 15/9-F-5 and F-10 on their
  survey stations' own times; every workspace well with a drilling record
  on its recorder's own clock (the bit depth, and the bottom of the hole
  from the depth pass), its channels binned by bit depth while drilling and
  its gamma index lithology; its path from an attached survey, or from the
  recorder's own MD-TVD pairs in one vertical plane whose azimuth the
  record does not give. Between two samples close in time the bit moves
  evenly; across a gap in the record it waits.
- `gea simulation` lists the scenes; `--scene volve-f4 --at 3300` prints
  what the record says at a depth; `--json`. API `/api/sim/scenes` and
  `/api/sim/scene/<id>`. `gea help simulation`.
- Section CH (3 checks); the gate is 404.

## [v0.38.0] - 2026-10-16 - The client well report

### Added
- **The client well report** (`gea/well_report.py`): one printable document
  per well, assembled from what the workspace already holds - the US Well
  Number, site, client, declared surface location and reference elevation,
  each with its source; the drilling record's own total depth, footage, days
  and hours on bottom; every depth event with what the record did with it
  and the person who checked it against the daily drilling reports; a chart
  of depth against days; what each mapped layer says at the surface
  location; the well path from its directional survey in plan and vertical
  section (minimum curvature, the station table with dogleg), with each
  mapped top drawn as a depth band from the two contours the location lies
  between below the declared reference; the declared cost with every rate
  and item and its source, and the EIA prices while the well was drilled.
  A part the workspace cannot supply yet is left out and named, with what
  would supply it - never drawn as zero.
- **The pad report**: every well side by side, their progress on one chart
  (a fixed colour per well and a key), the cost table, and what each well's
  report still lacks.
- **Drawings in client reports**: a report section can carry figures - SVG
  made by the program from the numbers in the report's own tables, written
  beside the report and inline in the HTML, each with a caption and the
  words a reader who cannot see it gets; printed, no drawing is split across
  two pages. The rendered text passes the same vocabulary check as every
  client report.
- The month-end packet writes and includes the well report for every well
  with a drilling record, and the pad report.
- `gea well-report --workspace W` (every well and the pad; `--well ID`;
  `--pad`). API `POST /api/reports/well-report` (the operator role). A
  "Client well report" card on each drilling well's page and a "Client well
  reports" card on the Reports page. `gea help well-report`. The report
  samples gain `well_report_SYNTHETIC.html` and `pad_report_SYNTHETIC.html`.
- Section CG (3 checks); the gate is 401.

## [v0.37.0] - 2026-10-16 - Economics

### Added
- **The Economics tab** (`gea/economics.py`, `#/economics`): each well's
  days on its drilling record, feet drilled, feet per day and hours on
  bottom - measured, on the recorder's own clock - beside what it cost by the
  operator's declared basis: day rates in USD over stated periods (open at
  either end, never overlapping), one-off items tied to a section or not,
  and the AFE, each with its source. Cost is the rates integrated over the
  record's time plus the items; cost per foot, per day and per hour on
  bottom, by well and by section, and the difference from the AFE. While
  the rates leave any of the record's time uncovered the cost is shown,
  marked incomplete, and not divided by the footage.
- **The market while each well was drilled**: the U.S. Energy Information
  Administration's daily spot prices - WTI at Cushing (RWTC) and Henry Hub
  gas (RNGWHHD) - ship with the program (`reference/eia_*`, public domain,
  built by `tools/build_eia_prices.py` from the Frictionless Data copies at
  pinned commits, hashed), and refresh into the workspace on request; a copy
  that does not read, or ends earlier than the one held, is refused. Any
  other series (a rig count, a regional price) is imported from a CSV of
  dates and values with its name, unit and source, kept byte for byte. One
  chart per series on its own axis, the wells' drilling windows shaded, a
  crosshair by pointer or arrow keys; each well's mean, low and high while
  it was drilled.
- **A correlation only where it means something**: a rank correlation
  between cost per foot and WTI across wells is computed from ten wells
  with a complete cost; below that the page says it is noise.
- `gea economics --workspace` prints it all; `--rate`, `--item`, `--afe`,
  `--refresh-prices`, `--import-series`. API `/api/economics` and
  `/api/economics/{rate,item,afe,remove,refresh,import}` (declaring needs
  the operator role). `gea help economics`.
- Section CF (3 checks); the gate is 398.

## [v0.36.0] - 2026-10-16 - The well in 3D

### Added
- **The 3D tab** (`gea/well3d.py`, `#/3d`): each well's path from its
  directional survey by the minimum-curvature method (between stations the
  path follows the same arc, never a chord), set below sea level from the
  declared rig floor, among the mapped formations - interpreted horizon
  surfaces as delivered, or a formation-top map's contour lines at their
  elevations - with the well's picks on it. WebGL written for the page, no
  library: drag to turn, shift-drag to pan, the wheel or a pinch to zoom,
  the arrows, + − and Home on the keyboard; vertical exaggeration 1-5×;
  surfaces toggled and see-through; point at a well or a pick for its
  measured depth, depth below sea level, offset from the slot, inclination
  and formation.
- **Colour along the path from what was measured there**: the formation
  (the mapped surface above each depth, from the well's own picks),
  inclination and dogleg severity from the survey, or a drilling recorder's
  gamma, rate of penetration and weight on bit binned by bit depth.
- **The replay**: the bit moving down the hole, the hole drilled so far in
  full colour and the rest faded - from a drilling recorder's bit depth
  (every sample by stride, none averaged, on the recorder's own clock), or
  from the time stamps of survey stations measured while drilling (a
  station copied in from an earlier section, or a resurvey run in one go,
  is not a record of drilling and is not used).
- **The Volve field ships as the worked example** (`reference/volve_*`,
  built by `tools/build_volve_3d.py` from public copies at pinned commits,
  hashed in `reference/volve_3d.provenance.json`): the original WITSML
  surveys of 15/9-F-4, F-5 and F-10, the field's 408 formation picks and
  five depth-converted horizons (Ty, Shetland, the base Cretaceous, Hugin
  top and base) at every fourth line, no point moved. Checked against
  itself: the paths reproduce the survey companies' own positions to under
  a centimetre and land on Equinor's picks to under 0.3 m; the frame is ED50
  zone 31N (the slots land within 0.1 m of the operator's log headers); each
  surface sits a few metres from its picks at the median. 15/9-F-4's 2016
  survey says true north but agrees with the picks only read as grid - it is
  read as grid and the page says so. Released by Equinor ASA and the former
  Volve partners under Equinor's licence (commercial use, no sale), with the
  attribution on the page.
- **A workspace well's survey**: `gea trajectory` or the form on the tab
  keeps a WITSML trajectory or an MD/Inc/Azi table verbatim beside the well,
  with its azimuth reference stated (grid with the grid named, true, or
  magnetic with the declination) and its source; audited. A well without a
  surface position, a reference elevation or a survey is listed with what
  it needs, never drawn; until one can be, the workspace's formation-top
  contour layers are drawn on their own (Pad 3's ten EIA tops, about
  100,000 vertices), an isopach or a value field that does not say it is an
  elevation listed with why it is not. `gea well3d --scene volve|workspace` prints a
  scene and its checks. The International 1924 ellipsoid joins the
  geodesy.
- API `/api/3d/scenes`, `/api/3d/scene/<id>`, `/api/wells/<id>/trajectory`.
  `gea help well3d`.
- Section CE (3 checks); the gate is 395.

### Fixed before release
- **The first push of this release failed its gate on every GitHub runner,
  and nothing was published.** The Volve formation picks were shipped as
  the CSV exactly as delivered, with CRLF line ends, and hashed that way.
  The repository stores every text file with LF line ends
  (`.gitattributes`), so the commit normalised the file: a checkout - the
  runners', and every wheel built from one - carried different bytes from
  the ones hashed, and section CE's provenance check refused them (a Windows
  working copy converts back to CRLF, which is why the gate passed before
  pushing). The picks now ship gzip-compressed
  (`reference/volve_well_picks.csv.gz`), like the surveys, so no line-ending
  rule can touch them; the provenance records both hashes. Section CE also
  refuses any file in the package that holds CRLF line ends, so a local pass
  can no longer hide a checkout failure.

## [v0.35.0] - 2026-10-15 - The control room

### Added
- **The wall** (`gea/control_room.py`, `#/wall`): a screen read from across
  the room - one tile per well, worst first, its state in a shape and a word
  (Serious, an octagon; Watch, a triangle; Good, a circle; No data, a dashed
  ring) so it reads without colour, the reasons in plain words, and the
  numbers that matter: the alarms active and unacknowledged, the gauges, and
  for a drilling recorder the bottom, the bit, the rate of penetration and
  the hours on bottom at the end of the record. Large type, high contrast,
  light and dark.
- **The rule a tile is judged by, stated on the page**: Serious is an
  unacknowledged P1/P2 alarm, a gauge reading off with no explanation, or a
  live feed down or silent five times its stale limit (fifteen minutes at
  least); Watch is any other active or shelved alarm, an explained drift, or
  a live feed past its stale limit, degraded or stopped; Good is a well with
  reports and none of these; No data is neither reports nor a live sample.
  A recorded well is described at the end of its record and is never called
  stale; only a live feed has an age, and it is measured.
- **It refreshes itself and says when it has stopped.** Every 30 seconds;
  the top right says how old the last answer is; after two missed refreshes
  a notice across the screen gives the age of everything below and every
  tile is greyed; a wall whose session or pass has ended shows nothing old.
  F or the button is full screen, Esc goes back; the screen is kept awake
  where the browser allows it, and says so where it does not; when the wells
  do not fit, the wall turns the page every 15 seconds, the worst first.
- **The shift handover** on the Control room tab: the outgoing operator
  writes what is open, what is being watched and what was done; the note is
  signed with the name and time, keeps the board's counts at that moment,
  goes up on the wall and badges the tab until the incoming operator
  acknowledges it (not the author). Appended to `handover/notes.jsonl`,
  never rewritten; each note and acknowledgement in the audit log; the
  history on the tab.
- **Wall passes**: an administrator issues a pass named for a screen (1 to
  365 days); the screen types the code once and shows the wall and the
  latest handover and nothing else, with no one's account signed in. The
  code is shown once and only its hash is kept; a wrong pass is locked out
  like a wrong password; a pass can be revoked at any time.
- `gea wall --workspace` prints the board; `gea handover --workspace` lists,
  signs (`--write`) and acknowledges (`--ack`) notes. API `/api/wall`,
  `/api/handover`, `/api/handover/ack`, `/api/wall/pass`,
  `/api/wall/passes`, `/api/wall/pass/revoke`, `/api/wall/enter`,
  `/api/wall/leave`. `gea help control-room`.
- Section CD (3 checks); the gate is 392.

## [v0.34.0] - 2026-10-15 - The site map

### Added
- **The Map tab** (`gea/site_map.py`, `#/map`): everything in the workspace
  that has a position, in one WGS84 frame - the wells at their declared
  surface positions, every structure layer the site brought (faults and
  folds, formation contours by role, areas, points), the World Stress Map's
  SHmax directions (best quality first), the seismic stations and the
  Seismicity Response Areas, over the outline of the US states and counties.
- **One frame, every shift stated.** A well declared on NAD27 is moved to
  WGS84 by its datum shift (about fifty metres in West Texas) and the move
  is shown beside it; NAD83 is taken as WGS84 to within about two metres, as
  everywhere else in the program. A well with no declared position is listed
  as NOT PLACED, with the way to place it, and is never drawn.
- **Drawn at the view's detail, nothing moved.** A layer is read once per
  run of the program (again when it changes) and each view gets its
  features in the box, thinned to the view's pixel: a vertex that would land
  in the same screen cell as the one before it is left out of the drawing,
  never moved, and the ends are kept; points are sampled evenly past four
  thousand.
- **What is here**: a click (or Enter for the centre) asks every layer for
  its own statement at that spot - the nearest fault and its distance and
  bearing, the contours it lies between, the area it falls in, the nearest
  points - with the distance and bearing to every placed well. Pointing
  shows the position and the nearest placed well.
- **Ergonomics**: drag to pan, the wheel or + − to zoom, Home to return (to
  the placed wells, else the response area, else the area most of the
  layers cover); the same on the keyboard; layer toggles with a fit button
  each; distance rings around the wells; a scale bar; light and dark; phone
  width.
- **The outline ships with the program**: `reference/us_outline.json`, the
  US Census Bureau's 2017 cartographic county boundaries by way of the
  us-atlas 3.0.1 package (built by `tools/build_us_outline.py`), with its
  provenance - hashes, the public-domain source and the package's ISC
  notice - beside it; state lines in a wide view, county lines and names
  once the view is close. The map needs no network.
- API `/api/map`, `/api/map/layer/<id>`, `/api/map/outline`,
  `/api/map/stress`, `/api/map/at`. `gea help map`.
- Section CC (3 checks); the gate is 389.

## [v0.33.0] - 2026-10-15 - Interactive charts

### Added
- **The Charts card on every well's page** replaces the single-channel
  trend: up to four channels stacked on one axis, each on its own scale,
  with a cursor that reads every track at the same moment (the bucket's
  mean with its minimum, maximum and sample count, or the sample itself).
  Drag across a stretch to zoom into it; the wheel zooms where you point;
  + and − zoom, ◀ and ▶ pan, Reset or a double-click shows everything; the
  keyboard does the same once the chart has focus (← → cursor, + − zoom,
  Page Up and Page Down pan, Home or Esc reset). Table shows the window as
  rows; the frame stays on screen, dimmed, while a window loads.
- **Every sample, not a thinned copy.** A window is cut from a chart cache
  (`gea/series_cache.py`, `cache/series/<well>/` in the workspace): equal
  time buckets each carrying the minimum, maximum, mean and count of the
  samples in it - so a one-second spike survives a month-wide view - and a
  bucket with no samples drawn empty, never bridged across a gap; the
  samples themselves when the window is close enough. A three-million-row
  export answers a window in about a fifth of a second.
- **For a drilling recorder**: the axis switches between time and depth (the
  drilling samples binned by bit depth, kept off hole a later sidetrack
  abandoned - the gamma log's samples); the depth events are marked at
  their times; the string coming off bottom is ticked when zoomed in; each
  section of the recording is a button that zooms to it.
- **Drilling progress on the Wells page**: each drilling-recorder well's
  hole depth against days since its first sample, one colour per well in a
  fixed order (both themes), a cursor that reads every well on that day,
  and a table view.
- The refresh writes each file well's chart cache from the read it already
  makes; the first view of a well without one builds it; a cache whose
  source file changed is rebuilt; backups leave the cache out. API
  `/api/wells/<id>/chart/meta | window | progress`, `/api/pad/progress`.
  `gea help charts`.
- Section CB (3 checks); the gate is 386.

## [v0.32.0] - 2026-10-15 - The setup checklist

### Added
- **The Setup tab** (`gea/setup_checklist.py`, `#/setup`, `gea checklist`):
  what a workspace still needs before its reports can be handed over, read
  from the workspace as it stands - an item is done because what it asks
  for is there, never because someone ticked it. Ten required items for a
  one-well site (an administrator; the wells; a site record naming the
  client and holding every well; a backup folder off the machine; a current
  backup; the daily schedule; current reports; and per well the surface
  location with its datum, the reference elevation with its source, and the
  US Well Number), the fleet and the depth-event check recommended, the
  stress state and structure layers optional.
- **Each item carries its action**: declare the position (latitude,
  longitude, datum), the elevation (KB, GL or DF and its source, or the
  ground level from USGS 3DEP at the declared position, offered to check
  before declaring), the well number; create the site with its client and
  wells; set the backup folder, back up now, schedule it daily; refresh the
  reports. "Go to the next item" takes the operator to the first required
  item not done. Where every value is known the item prints the command to
  paste; where the operator must supply one it names the values and where
  they come from, and prints no command with stand-ins in it.
- **The Setup tab's badge** counts the required items still to do; the
  Home page shows the progress and names the next item.
- **Confirmations**: a drilling record's depth events are confirmed by a
  person against the daily drilling reports, with a note, kept with the
  name and the time (`config/checklist.json`, the audit log); when the
  record changes - a different number of events or total depth - the
  confirmation stops counting and the item says to check again.
- `gea checklist --workspace C:\site` prints the list and exits 0 when
  every required item is done, 2 otherwise; `--json`; `--confirm ITEM
  --note ...`. API `/api/setup/checklist`, `/api/setup/confirm`,
  `/api/sites/add`. `gea help setup`.
### Fixed
- **A producing well's US Well Number made it a disposal well.** The
  number was kept only in the disposal block, so declaring it
  (`set-well --api`) marked any well as a disposal well. It is now the
  well's identity (`identity.api_number`, with the well name and operator
  beside it); a disposal well keeps its number in the disposal block as
  well, where the SRA packet reads it. The PPDM identity and the OSDU
  record read either.
- **A drilling record whose gamma was flat in the kept interval was not
  written**: the lithology message said it was "refusing" to invent a
  contrast, and the client-report guard rejected the word. The message now
  says none is invented.
- Section CA (3 checks); the gate is 383.

## [v0.31.0] - 2026-10-15 - The downhole tool library, with a tutorial on every tool

### Added
- **Every class of downhole tool in use across the industry**
  (`gea/tool_taxonomy.py`): ninety-six classes in nine categories - bits and
  hole enlargement; steering and drive; measurement and telemetry while
  drilling; drill string and BHA components; casing and cementing
  equipment; completion and artificial lift; wireline and cased-hole
  logging; fishing, milling and intervention; well testing.
- **A hover and a pop-open tutorial on every tool.** On the Tools & ports
  page each class is a chip: pointing at it, or reaching it with Tab, shows
  its one-line description; a click, a tap or Enter opens its tutorial -
  what it is, where it runs, how it works, what it shows in the data, what
  to watch, and what this program does with it - with the published figures
  and the sources. Esc, Close or a click outside puts it away and returns
  the focus to the chip. A find box and a category filter narrow the grid;
  on a touch screen the tap opens the tutorial directly. The gauge library
  rows open their class's tutorial too.
- **Cited figures only.** Every class was checked against public pages -
  the SLB Energy Glossary, SPE PetroWiki, the OSHA oil and gas eTool, maker
  datasheets and product pages, academic tool pages - 192 pages fetched on
  2026-10-14 and recorded with each class (`reference/tool_sources.json`).
  The 196 published figures are the pages' own words, with the unit and the
  page; most are one maker's product and the card says so. The tutorial
  prose carries no numbers at all, and a class with no published figure
  says to supply the model's datasheet. Encyclopaedia and re-publisher
  pages were left out, and so were figures the pages did not state in one
  piece.
- **The fleet**: the operator's own tools, each tied to a class and to the
  datasheet citation that rates it, kept in the workspace
  (`config/fleet.json`), added and removed by an operator from the tutorial
  or `gea tools --action fleet-add | fleet | fleet-remove`, every change in
  the audit log; a fleet tool carries a rating only as its datasheet gives
  it. `gea tools --action classes | tutorial --class ID`; the API
  `/api/tools/taxonomy` and `/api/tools/fleet`; `gea help tools`.
- Section BZ (3 checks); the gate is 380.

## [v0.30.1] - 2026-10-14 - The drilling record reads its own depth before counting

### Fixed
- **Total depth and the footage were wrong on the real files.** v0.30.0
  took the third well's total depth from 25,779 ft - a depth the recorder
  carried over from the well before - and none of the three footages
  equalled total depth less the first depth; it named the hole depth's
  falls and carried two arithmetics rather than deciding. The record now
  reads the hole depth channel for what it can be trusted for before it
  counts a foot (`depth_events`), and names each place it lies as a
  **depth event**: a section opening on a depth carried over from the last
  well and left by a jump (STALE OPENING) and a depth set back and counted
  up again by running pipe, regained within its own section faster than
  any bit drills or with the recorder's own rate of penetration at zero
  (RE-REFERENCE), are excluded; a set-back of 500 ft or more followed by
  six hours time-drilled under 20 ft/h is a SIDETRACK - total depth is the
  final borehole's, the footage counts every borehole, and the gamma log
  and the MD-TVD table are kept off the abandoned hole; a smaller fall
  (footage taken back) or a set-forward of more than 10 ft off bottom
  (footage added) is a CORRECTION; anything else is UNRESOLVED and said to
  be. Without a sidetrack the footage is exactly total depth less the
  first depth on bottom.
- **The on-bottom flag left on.** A stretch of 30 minutes or more with the
  flag set at the bottom of the hole, the depth still and the rate of
  penetration zero is kept out of the drilling hours and counted on its
  own (`flag_idle_hours`) - 19.9 h in one well, 8.7 h in another.
- A restart that opens on a carried-over depth is judged against the last
  depth the file can be trusted for, never against a stale section before
  it: one well restarted four times within the hour on the previous
  well's depth, and each restart is its own STALE OPENING.
- On the three real files (the owner's machine): 30636033 - TD 25,804.0 ft,
  23,796.2 ft drilled; 30665733 - TD 25,918.0 ft, 23,879.0 ft; 30693545 -
  TD 25,722.0 ft (not 25,779), sidetracked at 16,855 ft after drilling to
  24,902 ft, 31,752.2 ft drilled across both boreholes.
### Changed
- The record's sections give the running bottom when the section opened
  and the depth events in it; the well gives the boreholes, the first depth
  on bottom, the hours excluded and why. The removed v0.30.0 fields
  `feet_by_increments` and `hole_depth_drops` are gone. The well page
  carries the depth events, the boreholes and the hours kept out; the help
  page `edr` names every event. Section BY gains BY4 (`selftest_events`,
  `selftest_flag`); the gate is 377.

## [v0.30.0] - 2026-10-14 - The drilling recorder's export, read as delivered

### Added
- **The EDR port** (`gea/edr.py`, port `edr`, `gea edr`): a rig's
  electronic drilling recorder exports one row per second with the date and
  the time in two columns, then the WITS record-1 channels with their units
  in the header; `-999.25` and an empty cell where the recorder had nothing;
  the file stopping while the rig is off the well and resuming when it is
  back. This program now reads that layout as delivered - streamed, so a
  three-million-row export reads in under a minute - with the sentinels and
  blanks missing and counted per channel, the sections named from the gaps,
  and every sample's wall-clock stamp kept. `gea files` detects it by its
  first line; the historian port, handed one, says what it is and which
  port reads it instead of a traceback.
- **The drilling record**: per section, the span, the hours, the gap before,
  the hole depth where drilling began and the deepest it got, feet drilled
  (and the channel's own upward steps beside it when the two differ - the
  recorder resets its hole depth at a restart, and the record names each
  fall rather than choosing), hours on bottom and rotating, the rate of
  penetration while on bottom, the connections, the peaks; for the well,
  total depth and when the bit was first on bottom there, the recorder's
  TVD at that depth, feet per on-bottom hour; per channel, coverage and the
  counts of what was missing. `drilling_record.json/.md/.html` through the
  client-report guard, beside the well's other reports after `add-file` and
  `refresh`; the well page carries the card.
- **The MD-TVD table** (`md_tvd.csv`): hole depth against the recorder's
  interpolated TVD, one row per 100 ft, every row labelled an interpolation
  the recorder made from the surveys it was given - not a survey - and
  where that TVD comes within 1% of its maximum, read as the start of the
  lateral and said to be arithmetic on the table.
- **The gamma log the recorder carries** (`gamma_log.las`): the gamma
  channel while on bottom, as a log by bit depth in one-foot bins (the
  median and the count per bin), written as LAS 2.0 beside the record and
  said to be indexed to bit depth - shallow by the sensor's distance behind
  the bit, which the recorder does not state. The lithology-from-GR index
  this program applies to any gamma log runs on it (picks from the log's
  own percentiles, SAND/SHALE per bin), named a gamma index and not a
  formation. On the real files this is a complete log: every one-foot bin
  from below surface casing to total depth, 23,700 bins per well, which
  nothing had read.
- **Observations**: values below zero where the quantity cannot be, rate
  spikes above 1,000 ft/h, the differential pressure's negative stretches
  (the recorder's off-bottom reference, not a fault) - counted and named
  in the record, nothing altered.
- Proven on the synthetic export in the gate and on a rig's three real
  one-second files on the machine that holds them (1.4, 1.5 and 2.8 million
  rows; 25,800 ft MD laterals at 9,050-9,250 ft TVD; a recorder that reset
  its hole depth at every restart). `gea help edr`.
### Fixed
- **A LAS in feet was surveyed as metres.** `read_las` dropped the index
  curve's unit and `gea survey` printed every depth as metres and used it
  as such - the Kansas and Texas catalogue logs among them. The reader now
  keeps the index unit; the survey converts feet to metres once, says so
  on its depth line, and says when a source states no unit or one it does
  not convert.
- What it will not do: fill a sentinel; call the recorder's TVD a survey;
  name a formation, a landing point or a kick-off; judge the drilling; run
  a drilling recorder through the gauge pipeline or derive alarm defaults
  from it; correct a value it observes. Section BY (3 checks); the gate is
  376.

## [v0.29.1] - 2026-10-13 - The kit's gate: two checks that assumed a checkout

### Fixed
- **BW3 and BX2 crashed inside the installed kit.** Both v0.29.0 kit builds
  (Windows and Linux) failed at "install it offline and run its gate": BW3
  ran `tools/catalog_positions.py` and BX2 read `README.md` from beside the
  package, and an installed kit has neither - a `FileNotFoundError` in the
  gate, with the program itself untouched (the PyPI release and both ci
  runs were green). Each check now does what the tester guide and the
  report-samples sections already do: in a checkout it reads the file; in a
  kit it says so and checks what the kit does carry (BW3 proves a sidecar
  rewrite changes nothing; BX2 reads the help, the usage messages, the
  doctor and the page). Proven by running both sections from a copy of the
  package alone before this ship. The gate is still 373.

## [v0.29.0] - 2026-10-13 - The catalogue on a world map, and three things that broke at the keyboard

### Added
- **Where every catalogue entry was drilled** (`gea/catalog_map.py`,
  `tools/catalog_positions.py`): a position is taken from the entry's own
  file - a PANGAEA event line (WGS84), a LAS `LATI`/`LONG` pair, a LAS `LOC`
  line in degrees-minutes-seconds, or a KTB header in Gauss-Krueger zone 4
  (Rechtswert/Hochwert on DHDN, unprojected on the Bessel ellipsoid and
  shifted to WGS84 by EPSG 1776, the shift and its 3 m stated) - or from the
  sidecar's source record (the KGS well record for Collingwood 1-28; the
  Agassiz metadata, carried as approximate), or it is NO POSITION. Four
  entries are positioned as the same borehole as one that is (the KTB main
  and pilot holes), declared, not matched. Nothing is looked up or guessed:
  GISP2, Volve, the NLOG survey, Scorpio E1 and University 6-17 stay NO
  POSITION with what their files do say. 44 of 52 positioned, 33 distinct
  positions, 8 NO POSITION. Written once into each sidecar as a `position`
  block with the line it was read from; the gate recomputes from the files
  and holds the two equal.
- **The Catalogue tab** (`#/catalog`, `/api/catalog/map`,
  `/api/catalog/entry/NAME`): the world map - the Natural Earth 1:110m
  coastline (public domain, 134 lines, its upstream md5 in the fixtures
  README), plate carree, one numbered mark per position with the entries
  of one borehole sharing a mark, blue where a band reads the entry and
  amber where it is carried - with every entry beneath it: the position,
  the datum, the line it came from, the register status; a mark or a name
  opens the entry's provenance. `gea catalog --map catalogue.svg` writes
  the same picture as a file with the key and the NO POSITION list;
  `--positions`, `--check-positions`.
- `geodesy`: the Bessel 1841 ellipsoid, the DHDN datum with its published
  three-parameter shift, `gauss_krueger(zone)`.

### Fixed
- **A second layer of one formation and role silently replaced the first**
  in the interval band (`interval.statement` keys a formation's bracket on
  the pair). `add-layer` now refuses it, names the layer that is there and
  the two ways out - remove it, or name the formations apart
  (`Wolfcamp B (Delaware)` beside `Wolfcamp B (Midland)`, which is how the
  Pad 3 workspace was loaded). A different role, or a formation named
  apart, is taken as before.
- **A path that is not there was reported as a stem without `.shp`**
  (`no such shapefile: _incoming\layer.zip.shp`). It is now `no such file`,
  with the folder it was looked for from - a relative path is read from the
  folder the command runs in, which is the mistake it was hiding.
- **Command placeholders written as `<this>`** - in the help pages' command
  lines, the README, the usage messages, the doctor's fixes and the page's
  hints - which PowerShell refuses before Python runs (`The '<' operator is
  reserved for future use`). Every pasteable command now carries a real
  example value (`--well SWD-1`, a catalogue entry, a tool name, a path
  under `C:\site\_incoming`) or an uppercase metavar (`--well WELL_ID`),
  and the gate reads every one of those surfaces for a `<placeholder>`.
- What it will not do: put a mark where no file states a coordinate; call a
  mark a position this program measured; move a Pad 3 layer that is already
  loaded. Sections BW (3 checks) and BX (2); the gate is 373.

## [v0.28.0] - 2026-10-12 - Tools and ports: the two registers that had no page

### Added
- **The Tools & ports tab** (`gea/tool_catalog.py`, `#/tools`, `gea
  tools`): two registers have been in the code since the beginning with no
  page. The tool library - the downhole and surface tools, each with the
  citation its numbers came from, the temperature and pressure it is rated
  for, what it measures, the telemetry interface it declares and the
  datasheet aging rate where the vendor published one - and the port
  registry, the file formats this program reads and the eleven live
  protocols it speaks, each with what it speaks to, what the site must
  supply before it runs, and what to rehearse against.
- **The line between them, drawn**: a tool declares an interface and a port
  speaks one, so the page says which ports can carry each tool and lists
  the tools whose declared interface no port in this build speaks - the
  vibrating-wire readout, the 18-point thermocouple card, the fibre
  interrogator and the piezoresistive family - named rather than hidden. A
  tool whose vendor published no numbers stays PARAMETERS_USER_SUPPLIED,
  carries no aging rate and says why on its own line.
- **The rating check, on the page**: a tool string hung in a well - each
  station's tool against the temperature and pressure at that station, from
  the gradients declared on the page, with the basis printed beside the
  answer: the check that catches a 177 C gauge at 16,000 ft (183.9 C) before
  the well does. `gea tools --rating --station <ft>:<tool>` exits 1 when a
  station is over a rating. `/api/tools` (any signed-in reader),
  `/api/tools/rating`. `gea help tools`.
- What it will not do: invent a vendor number; call a station within its
  ratings a promise the tool survives; use conditions it was not given -
  without a well profile the gradients are the page's and the answer says
  so; call a port a connection - whether the site's device answers is what
  the Patch panel shows. Section BV (2 checks); the gate is 368.

## [v0.27.0] - 2026-10-12 - ComCat: the national catalogue read as the plan reads TexNet's, proven on the Youngstown sequence

### Added
- **The FDSN event-service text export** (`sra.read_fdsn_text`): USGS
  ComCat - and every FDSN-compliant catalogue service - answers
  `format=text` with `#EventID|Time|Latitude|Longitude|Depth/km|...|
  MagType|Magnitude|...|EventLocationName`. `read_catalog` now recognises
  it and reads it with the contributor (tx = TexNet, us = NEIC, ld =
  Lamont-Doherty) and the magnitude type on every event, and the packet's
  basis says a window across networks mixes scales. `sra-report --catalog`
  takes the file as it takes a TexNet export.
- **Two verbatim public-domain extracts as fixtures** (`gea/fixtures/`,
  provenance in its README): the Delaware and Midland basins for
  September 2026 - 28 events M >= 2.5, TexNet and NEIC, ML and mb_Lg,
  largest M 3.4 - and the 2011 Youngstown, Ohio sequence - 9 events within
  25 km, ending in the M 4.0 of 2011-12-31 3 km NW of Youngstown. The gate
  runs the SRA seismicity on the real Youngstown sequence inside a 10 km
  area with an M 2.0 threshold: six at or above, the M 4.0 TRIGGERED with
  its 48-hour respond-by, the goal clock from 2011-12-31.
- `gea comcat --lat --lon --radius-km --start --end --min-mag` prints the
  query to open (this program's machines may not reach the service; a
  browser does); `gea comcat --file <export> [--lat --lon]` reads it and
  lists the events with their distances. `gea help sra` names it.
- What it will not do: fetch the catalogue itself from a machine that
  cannot reach the service; read one network's magnitude as another's;
  decide an aftershock. Section BU (1 check); the gate is 366.

## [v0.26.0] - 2026-10-12 - slip tendency on the mapped faults: the stress state, stated, resolved on every trace

### Added
- **Slip tendency** (`gea/slip.py`): the open method - Morris slip
  tendency, the fault slip potential arithmetic of Walsh and Zoback - done
  on inputs the site declares with their sources and nothing else. The
  stress state from declared gradients (Sv, Shmin, SHmax, the ambient pore
  pressure; psi/ft) at the depth of the declared interval; the regime from
  their ordering; for every mapped fault trace within the radius, its
  length-weighted strike from the map, its dip from the layer's own `dip`
  attribute when it states one or else each declared dip, the normal and
  shear stress resolved on the plane, the effective normal stress at the
  ambient and the increased pore pressure, the slip tendency at both, the
  pore-pressure increase that brings the plane to the friction line and the
  margin left after the declared increase - BELOW or PAST THE FRICTION LINE
  for the worst and the best declared case. Where the layer publishes its
  own pressure-to-slip - the BEG/TexNet `Pp2S_MPa` of Hennings et al. 2021,
  computed under their calibrated stress model - that value is printed
  beside this arithmetic with the difference. The measured friction
  envelope from the catalogue (v0.25.0) printed last.
- **The World Stress Map as the SHmax source**: the WSM 2016 database CSV
  (CC BY 4.0) read by column name (a 999 azimuth and a D/E quality left
  out); the records within a radius of a position with the axial mean
  azimuth, its spread, the counts by quality and regime - a stated source
  for a site's SHmax azimuth, taken for a well that declares none. Proven
  on the real database: 40,730 records; at 31.42 N, 103.50 W, 35 A/B/C
  records within 100 km give SHmax 92 deg (median deviation 17 deg); near
  Youngstown, 9 records give 63 deg (deviation 8 deg).
- **The Hennings 2021 Delaware Basin faults read**: the BEG/TexNet
  basement-rooted (6,505 segments, with dip, confidence and `Pp2S_MPa`) and
  shallow-normal (1,827 segments, with dip, name, source, `Pp2S_MPa`) layers
  are UTM zone 13N - so the shapefile reader now unprojects any Transverse
  Mercator or Lambert Conformal Conic layer from its own .prj parameters on
  WGS84, NAD83 or NAD27 (the unit from the PROJCS's own UNIT), and a layer
  whose attributes do not say "fault" is declared so (`add-layer
  --feature-kind fault`). Both read in a tenth of a second; the statement
  runs on them with their dips and prints their published values beside.
  Not shipped in the package.
- `set-stress` (the gradients with their source required; an azimuth with
  its source, or left to the map), `add-stress-map`, `wsm --lat --lon`,
  `slip --well --delta-pp --depth-ft`; `gea slip` for any layer and
  position; `/api/stress` (GET, POST), `/api/stress/map`, `/api/stress/wsm`,
  `/api/wells/<id>/slip` (GET, POST); the Stress state card on the Seismic
  page and the Slip tendency card on the well page; the SRA packet's section
  2e; `gea help slip`. NO SURFACE POSITION, NO STRESS STATE, NO FAULT LAYER,
  NO SHMAX AZIMUTH and NO DEPTH each named with the remedy.
- What it will not do: measure a stress - every gradient and azimuth is
  declared with its source or taken from the WSM with the count and spread
  printed; give a fault a dip the map does not carry; give a probability
  of an earthquake - a pressure margin on a plane under stated assumptions
  says nothing about whether the fault exists at depth, is connected, or
  will slip; attribute an event. Section BT (3 checks); the gate is 365.

## [v0.25.0] - 2026-10-12 - the catalogue accounted for: what reads each entry, the measured friction, the core check

### Added
- **The catalogue register** (`gea/catalog_register.py`, `gea catalog`):
  the 52 shipped public datasets each with what in this program reads it,
  in the user's terms, or the plain statement CARRIED, NO BAND with the
  reason (a science-hole dataset gathered for the simulator's priors that
  no band of a gauge-monitoring and disposal-well program reads; dropping
  it from the wheel is the owner's call). The gate holds the register to
  the code both ways - an entry a module names must be registered as used,
  an entry registered as used must be named or be a LAS the survey takes -
  and reads every entry through its own reader (52 of 52). `--list`,
  `--unused`, `--check`, `--entry --info`, `--entry --stream-csv` (any entry
  as a stream file the other commands ingest). 29 entries used, 23 carried.
- **Every LAS log through the strata survey**: `gea survey --catalog
  <entry>` runs the survey on any catalogue log with its provenance on the
  source line. On the six real excerpts: L07-01 (Dutch North Sea) is
  estimated - density 2.64-2.74 g/cc, a Vp estimate; Scorpio E1 (South
  Australia) and Collingwood 1-28 (Kansas) NOT ESTIMATED, no density
  channel; University 6-17 (Texas), Kennetcook #2 (Nova Scotia) and 15/9-19
  SR (Volve) NOT ESTIMATED on too little usable support - each with the
  reason, as the gate checks.
- **The measured friction envelope** (`gea/friction_priors.py`, `gea
  catalog --friction`): seven catalogue datasets read as what they measure,
  each with its source and licence - ODP Costa Rica friction ladders (31
  coefficients, 0.21-0.88, 15-90 MPa, 19-212 C), IODP Hikurangi peak and
  steady-state friction per lithology with a-b, IODP Nankai shear strength
  over effective stress, IODP Sumatra cohesion (188-990 kPa), ODP Barbados
  vane shear (19.5-290 kPa), IODP JFAST Tohoku fault-zone peak strength at
  slow slip (1.7-1.9 MPa), KTB pilot-hole uniaxial compressive strength
  (3-265 MPa, 113 cores). The envelope: friction coefficients 0.21 to 0.90
  on 70 measurements from three datasets, none from the Permian Basin,
  against Byerlee's 0.6 - what the slip-tendency leg will print beside its
  assumed coefficient so the reader sees how far a law can sit from a
  measurement.
- **The core check** (`rock_inventory.core_check`, `gea catalog --core`):
  the Volve 15/9-19 A conventional core analysis in the catalogue, bulk
  density per plug as grain density x (1 - porosity) + brine x porosity
  with the brine stated (1.03 g/cc), against the published sandstone range
  the inventory carries: 65 of 70 plugs inside 2.05-2.55 g/cc, five outside
  (grain densities to 3.03 - heavy minerals the range does not cover). On
  the rock inventory model card as an evaluation the inventory was not
  tuned to.
- What it will not do: give the site a friction coefficient - the envelope
  is what was measured on other rocks elsewhere; invent a use for a carried
  entry; estimate strata from too little support; report a core bulk
  density the core did not state without labelling it derived. Section BS
  (3 checks); the gate is 362.
- The register, audit and check cover the shipped (public) tier only; an
  operator tier present on a machine is counted, never listed or named.

## [v0.24.0] - 2026-10-12 - the log as delivered and the historian: DLIS and PI Web API

### Added
- **DLIS (API RP66 V1)** (`gea/dlis.py`): the format a well log is delivered
  in, read as the standard lays the container out and with no dependency -
  the storage unit label; the visible records; the logical record segments
  with their predecessor and successor flags and their trailers (padding,
  checksum, trailing length); the explicitly formatted records as sets of
  objects under a template, with the representation codes of Appendix B
  (FSINGL, FDOUBL, FSHORT, ISINGL, VSINGL, the integers, UVARI in its three
  lengths, IDENT, ASCII, DTIME, ORIGIN, OBNAME, OBJREF, ATTREF, STATUS,
  UNITS); the FRAME and CHANNEL objects; the frame data records as the
  channel values in the frame's order. An encrypted record is skipped and
  counted by type, never decrypted or guessed; a channel with a dimension
  other than one or a non-numeric code is read past and named; a frame
  whose index is not depth is read as time and refused by the survey with
  the frames named; a LAS handed to the reader is refused by its label. The
  frame becomes the same stream the LAS port makes, with the origin's well,
  field, company and producer on it. A writer, so the reader is proven on
  files this program made before it meets a public one.
- **Proven on a real file**: Schlumberger's MSCT station log of well
  206/05a-3 (Faroe Petroleum, Fulla, 2011), the actually produced file in
  the public dlisio test set: 3,252 logical records, 19 sets, 104 channels,
  2 frames (2000T: 921 rows x 3 channels; 800T: 2,301 rows x 42 channels,
  TIME 16,677,259 to 17,597,260 ms, TDEP 852,606 to 893,304 x 0.1 in), 11
  vendor-encrypted records skipped and named, read whole in 0.07 s. Not
  shipped in the package.
- `gea dlis --file --info | --frame --out | --to-las`; `gea survey --file
  log.dlis [--frame]`; the Files page detects a DLIS by its label, previews
  it and imports it as a well; the `dlis` port in the registry. `gea help
  dlis`.
- **PI Web API** (`gea/piwebapi.py`): the operator's gauge history out of
  the AVEVA (OSIsoft) PI System - GET only, on the point and stream
  controllers: the point by path, `recorded` between two times with the
  server's paging followed through `Links.Next`, `interpolated` at an
  interval, the current `value`. Every value a record with the archive's
  timestamp as the source time and the archive's flags kept: Good is GOOD;
  Questionable and Substituted carried in the rule; not Good is BAD with
  the value; a digital state is GAP with its name; a system state (I/O
  Timeout, Shutdown...) GAP named as the archive's; the point's engineering
  unit converted where a conversion is known. Basic or bearer auth from
  environment variables the map NAMES (the map never holds a credential),
  a credential over plain HTTP refused in code. Protocol `piwebapi` on the
  Patch panel pulls on an interval with the window moving from the last
  archive time seen; `gea piwebapi`; the stand-in server for the rehearsal
  and the gate. `gea help historian`.
- What it will not do: decrypt; lay out a channel by guess; place a time
  frame on a depth axis; write to PI; hold a credential; fill a gap the
  archive left. Section BR (3 checks); the gate is 359.

## [v0.23.0] - 2026-10-12 - the field ports: Modbus RTU, DNP3, HART-IP

### Added
- **Modbus RTU** (`gea/modbus_rtu.py`): the serial line most wellsite gauge
  panels actually speak, framed and parsed by this program as the
  specification lays it out - address, function, data, CRC-16 (0xA001, low
  byte first) - with no dependency: pyserial (the `serial` extra, already
  the WITS0 port's) for the RS-485 drop, the same bytes over a TCP socket
  for a serial-to-Ethernet converter that passes RTU through ("RTU over
  TCP"), and an in-process stand-in slave for the rehearsal and the gate.
  Functions 03 and 04 only; no code path writes. Every register type in
  either word order; an exception response, a CRC mismatch and a silent
  slave each named and recorded as GAP; the register map is the site's
  with its source, required. `gea modbus-rtu`, protocol `modbus_rtu` on the
  Patch panel.
- **DNP3 (IEEE 1815)** (`gea/dnp3.py`): a read-only master - the data link
  frame with CRC-16/DNP over the header and every 16-byte block, the
  transport function's FIR/FIN/sequence reassembly, the application layer's
  control, function and internal indications. An integrity poll (class 1, 2,
  3 and 0) to start and an event poll after; confirms sent as the outstation
  asks; an unsolicited response confirmed and read. Analog inputs (group 30,
  every variation; group 32 events with the outstation's 48-bit time as the
  source time), binary inputs (1, 2), counters (20, 22), each with its flags:
  not ONLINE, COMM_LOST, OVER_RANGE, REFERENCE_ERR or RESTART is BAD with
  the flag named, a FORCED value kept with the flag in the rule; an object
  this master does not parse ends the fragment with the group and variation
  named. No control, write, time set or restart exists in the code. The
  stand-in outstation serves static points, events with time, a confirm
  request, an unsolicited null response at connect, and a corrupt frame on
  demand. `gea dnp3`, protocol `dnp3`.
- **HART-IP** (`gea/hartip.py`): the transmitter itself through a HART-IP
  gateway or a native device - the session, command 0 for the long address
  and identity, command 3 for the loop current and the four dynamic
  variables with their unit codes, command 9 for device variables by code
  with their status. The HART unit-code table for the common units,
  converted to the mapping's unit where a conversion is known; a response
  code that is an error is GAP, DEVICE_MALFUNCTION or a variable status not
  GOOD is BAD with the bit named. No command that changes a device exists in
  the code. The stand-in device for the rehearsal and the gate. `gea
  hartip`, protocol `hartip`.
- **On the Patch panel**: the three protocols supervised like every other -
  one connection, polled, restarted with backoff, the records folded into
  the well's stream - with `transport: sim` starting the port's own stand-in
  for a rehearsal; the example maps served; the live-well port list; the
  job runner. `gea help field-ports`.
- What it will not do: write to a device; clean a value the device flagged;
  guess a register map or point list; give a static value a time the device
  did not send; parse past an object it does not know. Section BQ (3
  checks); the gate is 356.

## [v0.22.0] - 2026-10-11 - the interval against the mapped tops: where the injection sits in the geology

### Added
- **The declared interval against the mapped formation tops** (`gea/interval.py`):
  the first question a reviewer of a disposal well in a seismic response area
  asks, answered from the operator's declaration and the layers the site
  brought. `set-well` now takes the base of the interval, whether the depths
  are true vertical or measured (`--depth-ref`), and the reference elevation
  the depths are measured from - its height, what it is (kelly bushing,
  ground level, derrick floor) and where it came from, each required; a base
  above its top and a reference without a source are refused. The interval
  becomes subsea elevation (reference minus depth, the arithmetic printed);
  each formation-top layer's bracket at the well (read, not interpolated)
  places it ABOVE the mapped top, BELOW it, or NOT RESOLVED where the bracket
  overlaps the interval - the map cannot place a top on one side of an
  interval it straddles. A layer is told what it maps (`add-layer
  --formation-name Wolfcamp --role top|isopach|base`); a formation with a top
  and an isopach gets a base bracket by arithmetic the report shows (top
  minus thickness, both as brackets), and the declared depth tier - shallow
  or deep by the base the SRA names - is CONSISTENT with the map,
  INCONSISTENT, or NOT RESOLVED. A measured-depth interval is refused with the
  remedy; a ground-elevation reference carries the note that the kelly
  bushing is higher by a height only the completion report states.
  `--action interval --well`, `reports/wells/<id>/interval.json`, audited as
  `well.interval`; the "Interval against the mapped tops" card on the well
  page, with the declaration form; `/api/wells/<id>/interval` (GET, POST),
  `/api/wells/<id>/declare`; the SRA packet's section 2c. `gea help interval`.
- **The catalogue events against the mapped faults**: every event inside the
  SRA against each structure layer - the nearest mapped trace to the
  epicentre by distance and bearing, the counts within 1, 2 and 5 km of any
  trace, the trace with the most events within 2 km. Section 2d of the SRA
  packet. The depth is not used (a surface trace against an epicentre) and
  proximity is not attribution; the packet says so.
- **The ground elevation from USGS 3DEP** (`gea/elevation.py`): the Elevation
  Point Query Service, queried as it specifies (x, y, wkid 4326, feet), the
  answer kept with its raster, resolution and query time as the source; no
  data, not JSON and unreachable each named. `gea elevation --lat --lon`;
  `set-well --elev-3dep` stores it as the reference with datum GL; the page's
  "Fetch the ground elevation" button. It is the ground, NAVD88, not the
  kelly bushing, and every statement built on it says so. Nothing is fetched
  unless asked.
- What it will not do: interpolate a top between two contours; place an
  interval the bracket overlaps; convert a measured depth; take a KB depth
  from a ground elevation silently; call the tier right - it says whether the
  declaration and the map agree, and the completion report is the record;
  attribute an event to a trace it is near. Section BP (3 checks); the gate
  is 353.

## [v0.21.0] - 2026-10-11 - mapped structure: the nearest fault, the contours, the basin, from a layer the site brought

### Added
- **The shapefile, read as specified** (`gea/shapefile.py`): no
  dependency. The .shp header and records (Point, PolyLine, Polygon,
  MultiPoint and their Z/M forms, Z and M read past), the records found by
  the .shx index when it is there - the index exists because a record's
  own content length is not always right: one of the 15,699 in a USGS
  layer understated its length, and a sequential read stops there and says
  so - the .dbf attributes (text, number, logical, date), the .prj
  coordinate system (geographic NAD83 or WGS84 taken as they are, the
  Texas State Plane NAD83 zones unprojected through `geodesy`, anything
  else refused by name), a zip, a folder or the .shp as the source, and
  the FGDC metadata beside the layer (titles, originators, dates,
  abstract) carried with it. A writer, so the reader is proven on files
  this program made and the gate needs no download.
- **Mapped structure** (`gea/structure.py`): for a position, the nearest
  mapped fault and the nearest fold in a layer - the distance to the
  nearest segment in a local plane, then the distance along the ellipsoid
  and the bearing to that point - the n nearest features within a radius,
  the counts by kind, the statement as the well page prints it; a
  position outside the layer's extent is OUTSIDE LAYER EXTENT, never "no
  faults". The structure type and name attributes are found by their
  field names (`type`, `name`, and the usual variants) and can be named.
- **In the workspace**: `add-layer` reads the layer once, keeps every
  part under `layers/<id>/` with its hash, the extent, the feature count,
  the type counts, the layer's own metadata and the site's citation;
  `layers`, `remove-layer`; `structure --well <id> --radius-km` writes
  `reports/wells/<id>/structure.json` and audits `well.structure`, saying
  NO LAYER or NO SURFACE POSITION (naming `set-well --lat --lon --datum`)
  when that is the case. The SRA packet carries section 2b, "Mapped
  structure around the wells", for every well inside the area. The page:
  the Structure layers card on the Seismic page (add a zip, the extent and
  types, the source and citation, remove) and the Mapped structure card on
  the well page. `/api/layers`, `/api/layers/add`,
  `/api/layers/<id>/at?lat&lon`, `/api/wells/<id>/structure`. `gea
  structure --layer <file> --lat --lon` and `--info` for any position and
  any layer without a workspace. `gea help structure`.
- **Proven on a real layer**: the USGS Professional Paper 1708
  Appalachian basin structure layer (15,699 fault and fold traces, NAD83
  geographic, 2014), supplied by the user, reads whole in a quarter of a
  second; from Youngstown, Ohio, the nearest mapped fault is the
  Akron-Suffield Fault, 31.5 km to the south-west; a Permian position is
  OUTSIDE LAYER EXTENT. The layer is not shipped in the package.
- **Every kind of layer the public structure downloads carry**: the EIA
  Permian Basin set (2017-2019, WGS84 geographic), supplied by the user,
  is nine shapefiles in one zip and five more zips of formation-top
  contours - so a zip holding several shapefiles lists its members
  (`gea structure --layer <zip> --list`) and one is named with
  `--member` (`add-layer --member`, the member select on the page,
  `/api/layers/members`); a layer with no `--member` and several
  shapefiles is refused naming them. Each layer is kept with its kind,
  and the statement is the kind's: a **structure** layer (faults and
  folds - `Faults_Permian_201712`, 2,055 traces whose one text field says
  'fault', 'dotted fault', 'dashed fault' or 'hidden contact', classified
  by its words and by the layer's name) gives the nearest fault and fold;
  a **contours** layer (`Wolfcamp_Formation_Elevation_201712`,
  `ShalePlay_Wolfcamp_B_Elevation_EIA_201811`, the Bone Spring, Delaware
  and Midland Wolfcamp A-D elevations and isopachs; the value attribute
  found - `ZLEVEL`, `ElevTop_ft`, `Thick_ft`) says which two contours the
  position lies between, or on one, read and not interpolated: "between"
  is said only when the two nearest contours are one contour interval
  apart, otherwise the nearest contour and its distance; an **areas**
  layer (`Permian_SubBasins_201712`: Delaware, Midland, Central Basin
  Platform, Ozona Arch, Val Verde) says which polygon the position is
  inside, or none; a **points** layer
  (`Wells_WolfCamp_with_StratigraphicPicks_201712`, 7,739 picked wells)
  gives the nearest picks with their values and says a pick at another
  well is not a pick at this one. Real numbers at 31.42 N, 103.50 W:
  inside the Delaware Basin; Wolfcamp top between -8,000 and -7,600 ft;
  Wolfcamp B top between -8,500 and -8,000 ft; Delaware top between
  -2,000 and -1,800 ft; the nearest EIA fault 52.6 km SSW, a dotted
  fault; the nearest pick 3.25 km NNW. The layers are not shipped in the
  package.
- **The manifest re-read**: a serving process and a `gea workspace`
  command on the same folder each hold their own copy of
  `workspace.json`; every method that lists or changes the wells,
  stations, tracks, layers or sites now re-reads it when another process
  wrote it, so one process's addition is neither hidden from the other
  nor dropped by the other's next save (found when the gate's own
  process and its serving process added layers in turn).
- What it will not do: say a position is clear of faults when the layer
  does not cover it; interpolate a depth between contours or add a
  depth, dip, throw or activity the layer does not state; call a pick at
  another well a pick at this one; call a distance a hazard; vouch for
  the map's accuracy - a compiled regional map is not a site survey.
  Section BO (3 checks); the gate is 350.

## [v0.20.0] - 2026-10-10 - the soak: the patches through link outages, measured

### Added
- **`gea soak`** (`gea/soak.py`): the last item on the go-live list that is
  code. N WITS0 patches (the site's own count), an ETP patch and a SeedLink
  live station each connect through a link relay on the loopback - a plain
  TCP forwarder - and on the declared schedule every relay closes its
  listener and every connection it carries for the declared seconds, then
  listens again, while the simulators keep producing (the WITS0 simulator
  now has a `repeat` mode: a client that goes away is replaced and the
  sender carries on from the next frame, counting the frames nobody was
  there for). What is measured is the supervisor's own reconnection,
  backoff and resume: per outage, which sources were down during it and
  how many seconds after the link returned each was CONNECTED and
  receiving again, or NOT BACK within the budget; per source, received,
  produced, lost (WITS0's, by the protocol's nature), gaps (SeedLink's,
  which must be zero - the resume by sequence number is the proof),
  reconnection attempts, latency p95 and how the protocol resumes; the
  page's answer time outside and during the outages with
  `--with-service`; the checks and the verdict. `soak.json` and the Soak
  Report (the outage table, the per-source table, what it does not call a
  measurement) go to `--out`. `--hours 72` is the go-live soak;
  Verification runs a 15-minute one from the page at the site's patch
  count. `gea help soak`. Section BN (3 checks); the gate is 347.

### Fixed
- **The internal-register guard now covers plain text.** The strata
  survey's text report ended "This report is honest or it is nothing" and
  headed its last section "what this tool refused to guess" - two of the
  terms the client-report guard refuses, which it never saw because the
  survey writes plain text rather than a rendered report.
  `client_reports.guard_text` is that guard for any text the program hands
  a client; the survey report passes through it, its closing line reads
  "A number without its provenance is not in this report", its declined
  estimates are NOT ESTIMATED and its last section is "what this tool does
  not call a measurement"; the tester guide follows. Two internal strings
  of the same register (the harness method note, the correlation census
  line) are reworded.
- The Verification page no longer names a gate count typed by hand
  ("172 checks"); the count is in the run's own log.

## [v0.19.0] - 2026-10-10 - the month-end packet: the month's deliverable, assembled once

### Added
- **`gea workspace --action month-end`** (`gea/month_end.py`): the refresh
  for the period (`--period 2026-09`, or `previous` for the last closed
  month; `--no-refresh` to collect as things stand; `--catalog` for the SRA
  packet), then every report the program writes, collected into
  `reports/month_end/<period>/`: for the site, the dashboard index, the
  accuracy statement, the SLA month for that period, the FAT/SAT protocol,
  the model cards and the SBOM; for every well, the gauge drift report, the
  alarm event report, the well-test validation, the shut-in / build-up
  report, the data resilience report, the machine vibration report, and
  the certificate register's conformity against ISO/IEC 17025 7.8 as it
  stands; for every site, the SRA packet for the period (where a
  Seismicity Response Area is defined) and the site report; for every
  seismic station and track, their reports. `INDEX` (html, md, json) says
  item by item INCLUDED with its files or NOT AVAILABLE with the reason
  that names what the site lacks (no machine record, no SRA defined, no
  certificate register, no well test detected, never refreshed), lists
  every failed step, and marks a month that has not ended OPEN;
  `MANIFEST.json` carries every file's SHA-256; the folder is zipped
  beside it as `month_end_<site>_<period>.zip`. A period is rebuilt whole,
  never merged. Each packet is logged in `records/month_end.jsonl` and in
  the audit (`month_end.assemble`); `month_end.verify` checks a folder or
  archive against its manifest. Nothing is recomputed in the index.
- **The page**: Reports carries the packet card - the packets on record
  with their index and archive, the period (defaulting to the last closed
  month), Assemble as a job, and for an administrator Schedule monthly
  (day 1..28 and the hour, UTC) as the `month-end-monthly` schedule entry
  for the previous month. `/api/month-end`, `/api/month-end/run`,
  `/api/month-end/schedule`. `gea help month-end`. Section BM (2 checks);
  the gate is 344.

## [v0.18.0] - 2026-10-09 - the backup: the site copied off the machine

### Added
- **`gea backup`** (`gea/backup.py`): the one thing the go-live list named
  that no command wrote. `--workspace C:\site --out D:\gea-backups --keep
  14` writes one dated archive, `gea-site-<name>-<UTC>.zip`, carrying the
  whole site - every well's source files and records, every station's
  source and live day files, `config/`, `monitor/`, `reports/`,
  `users.json`, the audit log, the notifications, the patches, the
  certificates and swaps, `workspace.json`, `jobs/schedule.json` - with
  `MANIFEST.json` inside (program version, site name, time, every file's
  SHA-256 and size, the wells and stations by id) and the archive's own
  SHA-256 beside it; the job run folders and the upload scratch are left
  out (reproducible, and what grows). A folder inside the workspace is
  refused. `--keep` prunes the older archives of that site. Every backup
  is logged in `records/backups.jsonl` and in the audit (`backup.make`).
- **`--verify`** reads an archive against its manifest and its `.sha256`,
  names any mismatched, missing or extra file; **`--restore <archive> --to
  <folder>`** extracts onto an empty folder only, verifies every file
  again, opens the result as a workspace, writes `restored_from.json` and
  `backup.restore` in its audit, and says what came back (the wells, the
  stations, whether `users.json` is there) - a backup nobody has restored
  is a hope; a tampered archive is refused. **`--status`** says when the
  last backup was taken and whether it is still where the log says.
- **The doctor** warns when no backup has ever been taken from the
  workspace or the last is more than 26 hours old, and says when the last
  one was otherwise; its users.json line now names the restore command.
- **The page**: Audit / Update carries "The backup" card (administrator):
  the folder (another disk or a share; the workspace itself is refused),
  the keep count, the daily hour; Back up now as a job; Schedule daily as
  the `backup-daily` schedule entry. `/api/backup`, `/api/backup/config`,
  `/api/backup/run`, `/api/backup/schedule`; `backup` is a runnable job.
  `gea help backup`; `deploy/README.md` and the go-live section carry it.
  Section BL (2 checks); the gate is 342.

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
