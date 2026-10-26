# History

The development record of GEA-Program, newest first. Every entry names what
shipped and what the acceptance gate counted at the time. Nothing here is a
claim the gate does not re-verify on every run.

## v0.68.0 - 2026-10-26 - GEA Reader

- GEA Reader, the companion program for clients: one HTML file that checks a
  signed dossier in any browser, offline, as `verify_dossier.py` does, and
  shows its reports as signed; it knows the site's key fingerprints. The
  Rocks tab shows the rock inventory with a lookup and its checks against
  real cores. The operator window gets a Start-menu shortcut, an icon menu
  item and its own taskbar button, and opens on computers without the
  operator-tier data (it could not before); its run chart works again.
  Duplicate zip entry names make a dossier INVALID. Section DN and E9;
  gate 516.

## v0.67.1 - 2026-10-25 - the derrick on the taskbar

- The control panel's window is GEA-Program on the taskbar (its own
  AppUserModelID, icon and relaunch command), not Edge; the icon by the
  clock comes out from behind the hidden icons. Section DM; gate 511.

## v0.67.0 - 2026-10-25 - no console window

- The installed control panel runs with no window (`gea supervise`, its
  output in `records\console.log`); no console or PowerShell window opens.
  Every page and button driven with and without cookies. Section DL;
  gate 510.

## v0.66.3 - 2026-10-24 - signing in where the browser keeps no cookies

- The control panel signs in where the browser keeps no cookies: the
  page notices and carries the session in the address. The app window
  has its own profile and the program's icon. Section DK; gate 509.

## v0.66.2 - 2026-10-24 - its own window however it starts

- `start-dashboard.cmd` and `gea serve` open the control panel in Edge's
  app window, as the shortcut does, not a browser tab. Gate 508.

## v0.66.1 - 2026-10-24 - the shortcut starts the control panel

- The Start-menu and desktop shortcuts start the control panel: the
  launcher (pythonw.exe) no longer hands its window a dead output pipe.
  The build's install test starts it from pythonw.exe as the shortcuts
  do. Section DJ; gate 508.

## v0.66.0 - 2026-10-23 - the site's own name

- **No kit names a site Pad 3 any more**: a workspace the launchers make
  is named after its folder; the setup asks the name and makes the
  workspace with it; an existing workspace keeps its own. Renamed by an
  administrator in the control panel. Workflow actions on Node 24.
  Section DI; gate 507.

## v0.65.0 - 2026-10-23 - the Windows installer

- **GEA-Program-Setup.exe**: the kit installed per user with no
  administrator rights, shortcuts, an Add/Remove Programs entry, a start
  at sign-in, upgrades in place; the workspace asked for and never
  touched. The control panel in Edge's app window (`gea launch`), its icon
  in the notification area (`gea tray`). Signed when the certificate
  exists; installed, upgraded and uninstalled by the workflow before it is
  attached. `stop-dashboard.cmd` without wmic; the logon task no longer
  meets the three-day limit. Section DH; gate 504.

## v0.64.0 - 2026-10-22 - one casing program

- **The casing is declared once.** Rig time, hydraulics, torque and drag
  and the casing design read one program per well; a string needs only
  its size and shoe; earlier rig-time shoes carried in, and what
  disagrees listed. Section DG; gate 498.

## v0.63.0 - 2026-10-22 - trusted timestamps on the signed dossier

- **A public authority's word for when a dossier was signed.** RFC 3161
  tokens requested over the dossier's signature, carried in the dossier,
  and checked - signature, certificate, chain to a trusted root - by the
  program and by the dossier's own standalone checker. Section DF; gate
  494.

## v0.62.0 - 2026-10-22 - kick tolerance on a real gas

- **The gas weighs what it weighs where it is.** Sutton and Dranchuk and
  Abou-Kassem's z at each depth's pressure and the geothermal temperature,
  in the burst load's gas column and in the kick's bubble (p V / z T
  between shoe and bottom); a swabbed kick beside each. Section DE; gate
  490.

## v0.61.0 - 2026-10-22 - casing design and kick tolerance

- **Every casing string against the loads the hole can put on it.** API TR
  5C3 ratings (every collapse regime, reduced under tension) against the
  textbook burst, collapse and tension loads, the smallest safety factor
  and where; the kick tolerance below each shoe; an interval declared in
  measured depth converted on the survey or the recorder's TVD.
  Section DD; gate 486.

## v0.60.0 - 2026-10-21 - every tab and button answers

- **The control panel driven end to end.** Every tab and well page and
  every button that does not change or remove something was driven in a
  browser; the ten faults it found are fixed (the Verification tab, the
  Rig time tab with a live well not yet streaming, a catalogue well from
  an entry that is not a gauge series stopping the refresh and the
  month-end, a live seismic station with no recording, Python's wording
  for an empty field). One well that cannot be read never stops the
  refresh again. Section DC; gate 482.

## v0.59.0 - 2026-10-21 - the declarations workbook carries the engineering

- **One workbook lights up the engineering tabs.** The declarations sheet
  takes the survey program and plans, pore pressure and measured
  pressures, torque and drag, hydraulics, frac stages and monitors,
  production and decline terms; it downloads as an Excel workbook; a well
  with no survey is read on the recorder's own TVD, labelled so.
  Section DB; gate 478.

## v0.58.0 - 2026-10-21 - hydraulics and ECD

- **The pressures the mud makes, against the window the rock allows.** The
  standpipe pressure part by part, the bit, the ECD at every depth against
  the pore pressure and fracture gradient, surge and swab and the fastest
  safe trip, and the rig's own standpipe pressure against the model.
  Section DA; gate 474.

## v0.57.0 - 2026-10-20 - the IT review

- **What an IT department checks before installing.** The security
  overview (`gea security`), checked against the code; both kits attested
  by their build; the Windows kit's file catalog, signed once the
  certificate is configured; the disclosure policy. Section CZ; gate 470.

## v0.56.0 - 2026-10-20 - company sign-in

- **The company's own accounts.** OpenID Connect with PKCE against Entra
  ID or any provider; the ID token checked by the standard library; the
  role from the company's app roles or groups; local accounts kept.
  Section CY; gate 467.

## v0.55.0 - 2026-10-20 - HTTPS

- **The control panel over HTTPS without a proxy.** The site's certificate
  or a self-signed one made with the standard library; TLS 1.2 floor,
  Secure cookie, HSTS, plain HTTP sent to HTTPS, the expiry alert, and no
  start on a broken certificate. Section CX; gate 463.

## v0.54.0 - 2026-10-20 - decline curves

- **What each well will make, and what it is worth.** Arps's curves fitted
  to monthly production, forecast to the economic limit, valued at the SEC
  12-month price or a declared one. Section CW; gate 459.

## v0.53.0 - 2026-10-20 - torque and drag

- **What the string should weigh and take to turn.** The soft-string model,
  the broomstick, and the friction factors that fit the rig's own trips and
  off-bottom rotation. Section CV; gate 455.

## v0.52.0 - 2026-10-20 - pore pressure and fracture gradient

- **The mud-weight window.** Overburden, the shale's normal compaction
  trend, pore pressure by Eaton from the sonic, the resistivity or the
  d-exponent, the fracture gradient, against measured pressures and the mud
  used; L07-01 as the worked example. Section CU; gate 451.

## v0.51.0 - 2026-10-20 - frac hits

- **What each frac stage did to the wells around it.** Every stage against
  every monitored gauge: none, poroelastic, mixed or a frac hit, the first
  response and the fluid pumped by then, the distance and the earthquakes
  beside each stage; a frac hit is an alert. Section CT; gate 447.

## v0.50.0 - 2026-10-20 - plan vs actual and the survey tools

- **Where a well is against its plan, and with which tools.** High/low and
  right/left at each survey against the survey's own uncertainty, the bit
  projected and the arc back; survey programs by depth on the ISCWSA MWD
  Rev 5.11 model (all 144 rows of its example) and 21 OWSG tool-code
  models; planned wells in the collision check; the travelling cylinder.
  Section CS; gate 443.

## v0.49.0 - 2026-10-20 - well collision checks

- **How close two wells come, against how sure their positions are.**
  Every well against every other by the ISCWSA separation factor, each
  well's uncertainty by the ISCWSA MWD Rev 4 error model (checked against
  the ISCWSA's own example, all 112 rows) and the field by WMM2025; the
  Volve template as the worked example. Section CR; gate 438.

## v0.48.0 - 2026-10-20 - one record, tied together

- **The parts read each other.** Invoice differences as alerts and in the
  morning summary with yesterday's rig time; the dossier carrying the
  contract, the invoices, the targets, the casing and both checks; the
  declarations sheet declaring all of it, from Excel too; rig time by hole
  section. Section CQ; gate 434.

## v0.47.0 - 2026-10-19 - rig time

- **What the rig did with each second, and what it lost.** Rig states from
  the recorder's own channels, connections timed weight to weight, trips by
  speed, and the time lost against the pad's own best quarter or the
  drilling program's targets, priced at the contract's rate. Section CP;
  gate 430.

## v0.46.0 - 2026-10-19 - the invoice cross-check

- **Three accounts of the same days, side by side.** The contractor's
  invoice, the daily drilling reports and the drilling record, report day by
  report day at the contract's declared terms; each difference with the
  dollars it puts in question and the line it comes from. Section CO;
  gate 427.

## v0.45.0 - 2026-10-19 - the lateral, the printable reports, the daily summary and the key

- **Four smaller finishes.** Earthquakes measured from the nearest point of
  each surveyed lateral; every client report also a PDF, kept in history;
  a one-page pad summary each morning, sent through the notification rules;
  the dossier signing key retired and replaced with the old key signing the
  hand-over. Section CN; gate 424.

## v0.44.0 - 2026-10-19 - the earthquake watch and the alerts

- **The record watched while it happens.** The USGS catalogue read every
  few minutes while a well operates, an alert when a protocol level is
  reached or the watch goes quiet; alerts for service intervals used up,
  penetrators outside their ratings and dossiers the workspace no longer
  matches; every alert recorded and sent through the notification rules.
  Section CM; gate 419.

## v0.43.0 - 2026-10-18 - report history and the signed dossier

- **Nothing written is lost, and anything sent can be checked.** Every
  client report kept by version with its hashes; a well's or the pad's
  record signed with the site's Ed25519 key in one dossier, with a
  standalone checker inside; the audit log hash-chained into each dossier
  and the dossiers chained to each other. Section CL; gate 416.

## v0.42.0 - 2026-10-18 - operations and seismicity

- **Each well's operations against the earthquakes around it.** Events by
  distance and time, during or after drilling, completion and disposal;
  the rig's state from the drilling record; the levels of five regulators'
  protocols; the exact conditional rate test. The Mahoning Valley worked
  example from the public record. Section CK; gate 413.

## v0.41.0 - 2026-10-17 - the declarations sheet

- **What the operator declares, in one sheet, checked before it is
  written.** Wells' identity, position and elevation, surveys, rates, items
  and AFEs, the fleet, drill-string runs, ESP penetrators and drives;
  planned NEW / CHANGE / SAME row by row, nothing written on an error, the
  template round-tripping. The Completion tab: the ESP wellhead
  penetrator's installation record and the drive, motor and wellhead
  against its maker's ratings; the PMM safety note. 43 motors. Section CJ;
  gate 410.

## v0.40.0 - 2026-10-17 - drill string

- **What was run, how much it has been used, how the motor behaved.** Runs
  declared from the bit up and drawn as a build diagram; usage from the
  drilling record summed per serial against declared service intervals;
  the motor's differential, stalls, speeds and torque estimate against the
  maker's figures, and the stability of the slow oscillations by Welch's
  method with a stated rule; 41 motors in the tool catalogue. Section CI;
  gate 407.

## v0.39.0 - 2026-10-17 - simulation

- **The well drilled again, on its own clock, beside its mud log.** The bit
  in 3D among the formations with the string in the hole; the panel of
  formations, rock and fluid volumes and logs growing with the hole; Volve
  15/9-F-4's logs and Schlumberger's interpretation shipped and checked
  against themselves; drilling-recorder wells on the recorder's clock.
  Section CH; gate 404.

## v0.38.0 - 2026-10-16 - the client well report

- **One document per well, one for the pad, every number with its source.**
  The drilling record and its depth events with the person's check, depth
  against days, the location and the mapped structure there, the survey's
  path in plan and section with the mapped tops as depth bands, the
  declared cost and the market - drawn as SVG from the report's own
  numbers; what a report cannot say yet is named with what would supply
  it. In the month-end packet. Section CG; gate 401.

## v0.37.0 - 2026-10-16 - economics

- **Time and footage measured, cost declared, prices as context.** Each
  well's days and feet from its drilling record; its cost from the
  operator's rates, items and AFE with their sources, undivided while the
  rates leave time uncovered; EIA's daily WTI and Henry Hub spot prices
  shipped, refreshable, and any imported series beside them; no
  correlation below ten wells. Section CF; gate 398.

## v0.36.0 - 2026-10-16 - the well in 3D

- **Surveys made into paths, among the formations, replayed.** Minimum
  curvature checked against a known arc and the survey companies' own
  positions; the Volve field shipped (three surveys, 408 picks, five
  horizons) and checked against itself; a workspace well's survey attached
  with its azimuth reference stated; the bit replayed from the recorder or
  the survey stations' own times. Section CE; gate 395. The first push
  failed on the runners (a CRLF picks file normalised by git) and was
  re-prepared with the picks compressed and a CRLF check in the gate.

## v0.35.0 - 2026-10-15 - the control room

- **A wall screen and the shift handover.** A tile per well, worst first,
  judged by a stated rule into four states each with its own shape and
  word; refreshed every 30 s with its age shown and a notice across the
  screen when it stops; full screen, kept awake, pages turned; a handover
  note signed by one operator and acknowledged by another, both audited;
  wall passes that show the wall and nothing else. Section CD; gate 392.

## v0.34.0 - 2026-10-15 - the site map

- **Everything with a position, one frame.** Placed wells (datum shifts
  shown), the structure layers thinned to the view without moving a vertex,
  stress directions, stations, response areas, over a shipped Census
  outline; what every layer says about a clicked spot; not-placed wells
  listed, never drawn. Section CC; gate 389.

## v0.33.0 - 2026-10-15 - interactive charts

- **Stacked channels, a linked cursor, zoom and pan from every sample.**
  The Charts card: envelope buckets from a per-well chart cache written by
  the refresh, gaps never bridged, time or depth for a drilling recorder
  with its depth events and sections; drilling progress for every well on
  the Wells page. Section CB; gate 386.

## v0.32.0 - 2026-10-15 - the setup checklist

- **What a workspace still needs, read as it stands.** The Setup tab and
  `gea checklist`: each well's position, elevation and number, the client
  site, the backup and its schedule, current reports, each with its form
  or button; a badge and the next item on the Home page; confirmations by
  a person with a note. A producing well's US Well Number is its identity,
  no longer a disposal declaration. Section CA; gate 383.

## v0.31.0 - 2026-10-15 - the downhole tool library

- **Every downhole tool class, with a tutorial.** Ninety-six classes in
  nine categories on the Tools & ports page, each a chip with a one-line
  hover and a pop-open tutorial in six parts; published figures quoted
  from the 192 pages they cite and no number of the program's own; the
  operator's fleet tied to the classes and the datasheets. Section BZ;
  gate 380.

## v0.30.1 - 2026-10-14 - the drilling record reads its own depth

- **Depth events before footage.** Stale openings, re-references,
  sidetracks and corrections named and handled before a foot is counted;
  total depth from the final borehole; the abandoned hole kept out of the
  gamma log and the TVD table; the on-bottom flag left on kept out of the
  drilling hours. The third well's total depth corrected from 25,779 to
  25,722 ft, a sidetrack at 16,855 ft found. BY4; gate 377.

## v0.30.0 - 2026-10-14 - the drilling recorder

- **A rig's one-second export, read as delivered.** The `edr` port: the
  two-column date and time, the units from the header, sentinels and
  blanks counted, sections from the gaps, streamed; the drilling record
  per section and for the well; the MD-TVD table labelled an
  interpolation; proven on three real 25,800 ft laterals on the owner's
  machine. Section BY; gate 376.

## v0.29.1 - 2026-10-13 - the kit's gate

- **Two checks that assumed a checkout.** BW3 and BX2 read a tools script
  and the README from beside the package; an installed kit has neither.
  In a kit each now checks what the kit carries and says so. Gate 373.

## v0.29.0 - 2026-10-13 - the catalogue on a world map

- **Where it was drilled, from its own file.** 44 of the 52 entries
  positioned from their headers or source records (PANGAEA, LAS, a KTB
  Gauss-Krueger reference unprojected on Bessel and shifted to WGS84), 8
  NO POSITION rather than looked up; the positions in the sidecars and held
  to a fresh read; the Catalogue tab and `gea catalog --map` on the Natural
  Earth coastline. Three keyboard defects fixed: the silent formation+role
  overwrite, the missing-file message, the `<placeholder>` that PowerShell
  refuses. Sections BW, BX; gate 373.

## v0.28.0 - 2026-10-12 - Tools and ports

- **The two registers that had no page.** The tool library and the port
  registry as one tab: every tool with its citation, ratings, declared
  interface and datasheet aging rate; every file format and live protocol
  with what the site must supply; the tools no port speaks, named; the
  rating check on a tool string. Section BV; gate 368.

## v0.27.0 - 2026-10-12 - ComCat

- **The national catalogue read as the plan reads TexNet's.** The FDSN
  text export with contributors and magnitude types; two verbatim
  public-domain extracts as fixtures; the SRA seismicity proven on the
  real 2011 Youngstown sequence (the M 4.0 of 2011-12-31 TRIGGERED);
  `gea comcat`. Section BU; gate 366.

## v0.26.0 - 2026-10-12 - slip tendency on the mapped faults

- **The stress state, stated, resolved on every trace.** The fault slip
  potential arithmetic on declared gradients; the SHmax azimuth from the
  World Stress Map records near the well; the Hennings 2021 Delaware Basin
  faults read from UTM with their dips, their published pressure-to-slip
  printed beside this arithmetic; the measured friction envelope beside the
  assumed coefficient. Section BT; gate 365.

## v0.25.0 - 2026-10-12 - the catalogue accounted for

- **What reads each entry.** The register of the 52 shipped datasets
  against the code both ways; every LAS log through the strata survey with
  its provenance; the measured friction envelope (0.21-0.90 on 70
  measurements, none from the Permian) against Byerlee's 0.6; the Volve
  core against the published sandstone range on the model card. Section
  BS; gate 362.

## v0.24.0 - 2026-10-12 - DLIS and the historian

- **The log as delivered, and the history where it sits.** A DLIS reader to
  RP66 V1 without a dependency, proven on a real Schlumberger station log;
  the strata survey reads the DLIS itself. A PI Web API pull, read-only,
  every value with its archive time and flags, on the Patch panel. Section
  BR; gate 359.

## v0.23.0 - 2026-10-12 - the field ports

- **Modbus RTU, DNP3, HART-IP.** The three links a gauge panel on a pad
  actually offers, each read-only, framed and parsed by this program as its
  specification lays it out, each with a stand-in to rehearse against, each
  supervised on the Patch panel. Section BQ; gate 356.

## v0.22.0 - 2026-10-11 - the interval against the mapped tops

- **Where the injection sits in the geology.** The declared disposal
  interval, converted to subsea through a stated reference elevation, set
  beside each formation-top bracket the layers give at the well: ABOVE,
  BELOW or NOT RESOLVED; a base from a top and an isopach by shown
  arithmetic; the depth tier CONSISTENT, INCONSISTENT or NOT RESOLVED
  against the mapped base the SRA names. The catalogue's events against the
  mapped faults. The ground elevation from USGS 3DEP, named as the ground.
  Section BP; gate 353.

## v0.21.0 - 2026-10-11 - mapped structure

- **The nearest fault, the contours, the basin, from a layer the site
  brought.** A shapefile reader to the specification without a
  dependency; a zip of several shapefiles by member; each layer kept by
  kind - structure (nearest fault and fold by distance and bearing),
  contours (between which two, read not interpolated), areas (inside
  which), points (nearest picks) - a position outside the layer said to
  be outside; the statement on the well page and in the SRA packet;
  proven on the USGS PP 1708 Appalachian structure layer and the EIA
  Permian Basin set. The workspace manifest re-read across processes.
  Section BO; gate 350.

## v0.20.0 - 2026-10-10 - the soak

- **The patches through link outages, measured.** The site's patches, an
  ETP patch and a SeedLink station through loopback relays cut on a
  schedule for hours; per outage the seconds each source took to be back
  and receiving, per source what the protocol lost and whether the resume
  left a gap, the page's answer time through it; the Soak Report. The
  client-report guard extended to plain text (the survey reworded).
  Section BN; gate 347.

## v0.19.0 - 2026-10-10 - the month-end packet

- **The month's deliverable, assembled once.** The refresh for the
  period, then every report for every well, site, station and track
  collected into one dated folder and archive with an index (INCLUDED or
  NOT AVAILABLE with the reason) and a manifest of every file's hash;
  scheduled monthly from the Reports page. Section BM; gate 344.

## v0.18.0 - 2026-10-09 - the backup

- **The site copied off the machine.** One dated archive of the whole
  site with a manifest of every file's hash, onto another disk or a share;
  verified; restored once onto an empty folder and opened before it is
  trusted; scheduled daily from the page; the doctor warns when it is
  missing or stale. Section BL; gate 342.

## v0.17.1 - 2026-10-09 - one kit run per ship

- **The kit workflow no longer races itself.** It ran on the tag and on
  main for every ship - two runs, one release - and lost a step to itself
  on v0.13.0, v0.16.0 and v0.17.0. It runs once per ship now, on the tag,
  serialised by commit; one writer per release. Gate 340.

## v0.17.0 - 2026-10-09 - live stations (SeedLink)

- **The station's records as it writes them.** A SeedLink customer speaking
  3 and 4.0, whichever the server offers: the handshake per station, every
  record appended byte for byte to a day file per channel, the last
  sequence number kept so a reconnection resumes with no record twice, the
  latency and the gaps per channel. In the workspace as a live station the
  serving process keeps up, on the Seismic page to add, start, stop and
  fold into the record list the leg refreshes on; a two-version simulator
  and a loopback self-test. The kit workflow's attach step survives the
  tag run and the main run creating the same release within a second
  (v0.16.0's red Linux job, both kits on the release). Section BK; gate 340.

## v0.16.0 - 2026-10-08 - WITSML 2.x over ETP

- **The store pushes.** An ETP v1.2 customer over WebSocket, Avro to the
  published schemas, Protocol 21 ChannelSubscribe: the declared channel
  URIs subscribed and every value the store writes carried as a record
  under the mapping's tag and unit at the store's own index, the latency
  per record. In the patch supervisor, the service, the page and the CLI
  like every other port; a two-channel simulated store and a loopback
  self-test. The doctor names a panel started through gea.exe, which pip
  cannot replace while it runs. Section BJ; gate 337.

## v0.15.0 - 2026-10-08 - conformity, in the standards' words

- **The decision rule, named.** The bias-against-class line is a statement
  of conformity in ISO/IEC 17025:2017 7.8.6's terms, under the ILAC-G8
  non-binary guard-band rule with the bias's expanded uncertainty, falling
  back to simple acceptance by name when no uncertainty exists. The
  certificate register carries 7.8.4's items and judges each certificate
  against the fourteen of 7.8.2.1 and 7.8.4.1. Section BI; gate 333.

## v0.14.0 - 2026-10-08 - machine vibration

- **The zone, and what the bearings are doing.** ISO 20816-1's broadband
  r.m.s. velocity, ISO 20816-3's zones for the declared machine, and
  envelope analysis in the most impulsive band against the bearing's own
  defect frequencies, the fundamental required. Declarations named, PARTIAL
  BAND named, no fault size. The miniSEED writer carries microseconds
  (blockette 1001) so a 20 kHz record round-trips whole. Section BH; gate
  330.

## v0.13.0 - 2026-10-08 - the well as master data names it

- **PPDM well identity.** The US Well Number taken apart to the standard and
  the "What is a Well" components the site can name - the Well and its
  Origin, the Wellbore only with a sidetrack code, the Wellhead Stream, the
  Well Set, typed aliases - carried into the SRA packet and the OSDU export;
  no sidetrack assumed, no interval or completion invented, no county named.
  Section BG; gate 327.

## v0.12.0 - 2026-10-08 - the QuakeML catalogue export

- **The site's events in the regulator's format.** The association leg's
  events written as QuakeML 1.2 to the Basic Event Description schema:
  origins with the misfit region as their uncertainties, declared depth
  named as such, the quality block, picks with the record's FDSN codes,
  arrivals with residuals; automatic and preliminary throughout; the
  catalogue's standing as comments; no magnitude. The shape checked as a
  reader checks it, and the file XSD-valid. Section BF; gate 324.

## v0.11.1 - 2026-10-08 - the Linux kit's gate judges the Linux kit

- **The Linux kit's gate failed at v0.11.0.** BB13 judged the Windows
  launcher text on a kit that has none - section BB substitutes a stand-in
  there for BB6's sake, and the new check read the stand-in. It keeps the
  shipped launcher apart from the stand-in now and judges a Linux kit on
  its shell launcher alone. Proven failing and then passing from an
  installed Linux layout. Gate 320.

## v0.11.0 - 2026-10-07 - the OSDU-shaped export

- **The manifest a buyer's platform loads.** Well, Wellbore, WellLog and
  File.Generic to the published schemas, the site as the work product,
  every position on its own datum and on WGS 84 with the operation between
  them written out, the operator's partition, ACL and legal tag required
  rather than invented, and the loader's first checks made here first.
  Section BE.
- **The console answers.** `gea serve` opens the browser itself once it is
  listening and prints what happens - the page opened, sign-ins, jobs,
  stops and restarts - because a server that answers in silence looks, from
  its own window, like one still loading; the launchers no longer race it.
  BB12-BB13; gate 320. The SAR panel gets a page and a navigation entry of
  its own.

## v0.10.0 - 2026-10-07 - association

- **The stations heard the same thing, or they did not.** A picker, an
  associator with a grid search for the origin under a declared model, a
  location with its misfit region, an array as one station, and the result
  set against the catalogue with both positions printed where they differ.
  Three or more stations or NOT ASSOCIATED; a depth declared, a magnitude
  not estimated. Feeds the SRA packet. Section BD; gate 314.

## v0.9.0 - 2026-10-07 - the Seismicity Response Area packet

- **The SRA packet.** The first deliverable shaped to a regulator rather
  than to a client engineer: the Railroad Commission's operator-led response
  plans and its Notice to Operators name a shape - the area, the plan's
  numbers, the four daily parameters, the three bottomhole-pressure methods -
  and the packet is built to it from the site's own wells and stations and
  a catalogue export the operator hands in. It names every gap and every
  declaration, refuses to invent a volume, a tier or an aftershock, and the
  daily export carries the Notice's own parameter names. Section BC; gate
  308.

## v0.8.1 - 2026-10-06 - the kit's gate reads the kit

- **The kit's gate failed at v0.8.0.** Section BB read the launcher templates
  from `tools/`, which a kit does not carry. In a kit it now reads the
  launcher the kit installed - the one the client double-clicks - found the
  way `gea doctor` finds it. Gate 302, proven from an installed layout.

## v0.8.0 - 2026-10-06 - the machine behind the lines, the ground under the positions, the site that owns them, and a panel that survives the power going out

- **The control panel owns its own stopping and starting.** It had one way to
  end - the window closed - and the operator was left looking at whatever
  shell was underneath, which on a console opened from a Python profile is a
  bare `>>>` where a program used to be. The service now states how it ended
  in its exit code (0 stop, 86 start me again), the launcher reads it, and on
  any other code hands the window to a PowerShell prompt that names the
  program; it never falls through. Stop, Restart behind an explicit
  authorisation, and an auto-restart switch live on the Administration page.
  The switch is one character in one file because the launcher is a batch
  script, capped at five stops in a row so a panel that crashes while starting
  hands over the error instead of flickering all night. Every start, stop and
  recovery is appended to `records/runlog.jsonl` and flushed before it is
  attempted, so a run that was killed reads back as killed; the recovery
  brings the live patches up first and rebuilds the stale records behind them,
  because what the stream does not record is not recoverable and a report that
  is behind its source always is. `gea doctor` reads the installed launcher
  and says whether it honours the contract. Section BB; gate 302.
- **The site: the engagement, not the leg.** A site holds the wells, the
  seismic stations and the tracks of one client's ground, and the Site Report
  pulls every leg of it into one document with anything not run since its
  source changed named. That is what makes this one program rather than three
  tools in a package. Section BA.
- **How well a bearing is known.** The ellipses came from geometry: what the
  array could resolve at perfect signal-to-noise, identical for a clean
  record and a marginal one. The sigma is now measured by cutting the window
  up and beaming each piece, floored at the beamformer's own grid, and -
  the part that matters - `coverage` counts how often the truth really falls
  inside it and returns the factor that would centre the claim. CALIBRATED on
  the labelled scene, with a median error of 0.37 degrees against a sigma of
  0.54. Section AZ.
- **The site, not the station.** Two reports the leg had no answer for: the
  Seismic Dataset Report, which says what is held - every record, its span,
  rate, gaps, checksum and datum, and whether any result is older than the
  record it came from - and the Seismic Field Report, which says what the
  site heard, rig by rig and station by station, with what nobody listed
  beside it. Section AY.
- **What else is out there.** `seismic_unlisted.py` is the first part of this
  leg that finds instead of confirming. Lines no listed source claims are
  grouped into combs; a comb with a fundamental is a machine, and it is
  beamed on its own lines like any other. Proven by leaving one of three rigs
  off the list: it comes back at half a degree of its true bearing and its
  true pump rate, while the mains line and the mains harmonic that folds back
  under Nyquist are named as electrical rather than as machines, and with
  every rig listed there is nothing to find. Section AX.
- **Is this array any good?** `seismic_qc.py` checks the assumption every
  bearing rested on. The timing test is the one that matters and it needs no
  reference clock: for a plane wave the delays must lie on a plane, so the
  plane is fitted to the array's own measured delays and each residual is
  what the wavefront does not explain. Proven by putting four known faults
  into a clean array - a dead channel, a clock 40 ms out, a sensor wired
  backwards, one at 8 % gain - and finding exactly those four, recovering the
  clock error to 0.04 ms, and measuring what they cost: 173 degrees of
  bearing. The same array with no faults comes back USABLE with nothing
  named, which is the half of the test that matters most. Section AW.
- **Which datum a position is on.** `geodesy.py` ends the program's oldest
  silent assumption: that a latitude and a longitude mean the same ground
  whoever wrote them. A Texas permit export is often NAD27 and a network's
  metadata WGS84, and at 31 N, 102 W those differ by 46 m - the size of a
  position ellipse, which is why a datum error never looked like an error.
  Positions carry their datum, conversions carry their accuracy, an unstated
  datum is UNKNOWN and prints what assuming costs in metres at that site, and
  the separation between two datums is computed there rather than quoted.
  Under it: Vincenty on the ellipsoid, the published three-parameter shifts,
  UTM and Texas state plane both ways, and the two feet kept apart. Checked
  against published values this program did not produce. Section AV.
- **The machine behind the lines.** `seismic_harmonic.py` stops treating a
  signature as a list of bins. A comb search over every line divided by every
  order recovers the fundamental - a pump rate in strokes per minute, not a
  bin index - and says how often this record's line density would produce
  that comb by accident, setting aside the ones it would. Every line is then
  followed window to window, so a machine whose rate walks with its load
  keeps its signature instead of disappearing from it, the walk is read as a
  rate over time, and a harmonic the fixed bins missed is attributed back to
  its machine by its whole-number ratio to a line already claimed. A line no
  listed source claims is listed as such. And the ground truth every earlier
  verdict rested on is now measurable: each source's working spells come off
  the record and sit beside the ones the rigs list declares, agreeing or
  disagreeing, because a permit date is a permission and not a drilling log.
  Proven on a labelled scene whose pump ramps 1.40 to 1.85 Hz with a stop in
  it: fixed bins hold two lines of it, the tracker holds every one with the
  harmonics walking k times as fast, and the fundamental comes back within
  0.01 Hz of the rate the scene was running at in that window. Section AU.
- **A field, not a rig.** `seismic_signature.py` learns each rig's own lines
  while it works alone and then beams it on those lines alone, so several
  rigs working together get a bearing each in the same window and a track
  each from two arrays. The refusals are the method: no signature without a
  spell alone, NOT_SEPARABLE for rigs whose lines collide, ALIASED where the
  geometry cannot tell the peak from its lobes. The track now also says
  whether the source moved at all. Section AT.

## v0.7.0 - 2026-10-04 - the time dimension - tracks from two arrays, the SAR panel, the Audit / Update tab, the permits importer, and the kit made the commit

- **The time dimension and the first mapped track.** `seismic_track.py`
  runs the array step window after window: bearing histories, positions
  from two or more arrays with their ellipses, the longest continuous
  segment as the track, and a verdict against ground truth - proven on a
  labelled scene of a bit advancing along a lateral (TRACKED, heading
  within a degree or two of the scene's). Tracks live in the workspace
  with their own report and view; the SAR screen's second scene draws the
  track as it is earned; `gea permits` turns a permit export into the rigs
  CSV with every assumption counted. Sections AR and AS; gate 266; 19 samples.
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
