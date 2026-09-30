> The requirements matrix that shaped the report family: a production-operations scope of work mirrored clause by clause, with what the program does today and what it gets better by. Format reference, not a bid.

# TENDER NO. CDG2752P27 — PROGRAM IMPROVEMENT REVIEW, MIRRORED TO THE SOW

Format mirrors Part-4 Section-II (SOW/TOR), Section-III (SCC/SLA) and Section-IV (BOQ). Under each clause: **Tender asks** (the requirement as written) · **Program today** (`gea/`, graded against the code) · **Gets better as the client-user program by** (the concrete change OIL's users would see). Client users per §10.0: 50 viewers, 120 operators, 10 administrators, plus production engineers, IMs and DGMs per §2.2.1.

A grade of HAVE means a code hit that does the thing; PARTIAL means the mechanism exists for a different target; MISSING means no code. Hardware, civil, HSE and commercial clauses are marked *not a program clause* and get one line.

---

# PART-4 · SECTION-II · SCOPE OF WORK / TOR / TECHNICAL SPECIFICATIONS

## 1.0 ABBREVIATIONS & ACRONYMS

**Tender asks:** a shared vocabulary (CHP, THP, FLP, FLT, GOR, PI, PIP, PDP, MPFM, VFM, PDMS, MAAPE…).
**Program today:** its own vocabulary — "station", "leg", "twin", "closed stream / live stream", "toolstring". None of the tender's terms appear in the user-facing surface.
**Gets better by:** adopting the tender's acronyms as the canonical tag and label vocabulary in every screen, export and log. A user reading CHP on the wellhead and CHP in the program should never translate. Ship a glossary in-app (§1.0 table, verbatim).

## 2.0 INTRODUCTION

### 2.1 Purpose of Document
*Scope statement; no program clause.*

### 2.2 Background
**Tender asks:** DRIVE programme — leverage AI/ML to improve efficiency, safety, reduce wastage.
**Program today:** a validation and surveying engine with no framing for an operator's day.
**Gets better by:** giving each user role a landing view tied to the DRIVE goals: operators see today's exceptions; engineers see model confidence and pending approvals; DGMs see field KPIs. The program's honesty features (refusals, coverage) must be presented as operational states, not as developer diagnostics.

### 2.2.1 Production Operations at OIL
**Tender asks:** 631 producing wells across Eastern / Western / Rajasthan assets; hierarchy CGM(Field) → DGM(Production Zone) → IM(Installation) → OCS/GCS/FGGS/QPS/WHS/EPS/SPF; ~40% gas-lift assisted, 45 SRP, 44 PAGL, 8 ESP; TCC-controlled intermittent lift; 4–5 km flowlines; test separator; manual 6+ hr well tests entered into ERP; monthly allocation; BS&W by lab sample; limited IoT; GSM/wireless; remote sites without power/network.
**Program today:** no asset hierarchy; a "well" is a `WellAssembly` or a `WellProfile` CSV. No notion of installation, zone, field, or lift type.
**Gets better by:** implementing the OIL asset hierarchy as the program's primary navigation (Field → Zone → Installation → Well), with lift type (SF / GL / PAGL / SRP / ESP) as a first-class well attribute that selects the model family, the tag set, and the validation rules. The four stated pain points become the four things the program's home screen answers: flow assurance status, blockage/wax risk, custody/leak anomalies, and which wells are due for a test.

## 3.0 AS-IS STATE OF SENSORISATION ACROSS WELLHEADS

### 3.1–3.3 Current installs (90 wells), sensor types (WirelessHART/ISA100 vs HART), measurement points (FLP, FLT, THP, A/B annulus, gas-injection rate)
**Program today:** tool library and gauge-spec injection exist (`tool_library.py`, `gauge_specs.py`) for quartz P/T gauges; no ISA100/WirelessHART awareness; no annulus or injection tags.
**Gets better by:** extending the tag dictionary to the exact §3.3 measurement points per lift type, with the Yokogawa (10 wells) and Kelton (77 wells) legacy sources modelled as two ingestion adapters so users see one well, not two vendors.

### 3.4 List of sensors existing and planned (self-flow, gas lift, indirect heaters, MPFM)
**Program today:** no MPFM model; no heater-vessel tags.
**Gets better by:** adding MPFM (SkidM/Roxsar/Duplex) as a first-class measurement source with its own uncertainty (±5 % liquid, ±8 % gas, ±4 % WC absolute per §4.2.13.5) so VFM can be calibrated against it with correct error propagation.

## 4.0 BUSINESS REQUIREMENT

### 4.1 Solution Description
**Tender asks:** 700 wells, 380 sensorised; "such additions must be accommodated without any extra cost"; real-time IoT, AI/ML, predictive maintenance.
**Program today:** single-process Python; per-run invocation; no multi-well scheduler.
**Gets better by:** treating the well count as data, not code: a well registry where adding a well is a form, not a deployment; per-well workers so 700 wells run on the same cadence without an operator noticing.

### 4.1.1 Instrumentation Installation & Commissioning
*Hardware clause.* Program consumes commissioned tags via the tag dictionary (§4.2.2); the improvement is a "commissioning checklist" view where a user marks each §4.1.1 instrument live and the program refuses to model a well until its required tags are commissioned (the refusal pattern already in the code, `tool_library.py` status strings).

### 4.1.2 Production Data Management System
**Tender asks:** single source of truth; interfaces to OT data lake, SCADA, historian, GRPC, RTPMA; role-based dashboards; alarms; mass & volumetric balance; **well test validation**; event management; deferment management; well (production) allocation.
**Program today:** well-test validation core exists as a gauge-drift bench (`bench.py`: OLS slope + standard error, span/SNR floors read from config, verdicts MEASURED_CONFIRMS / MEASURED_REFUTES / INSUFFICIENT_SPAN / INSUFFICIENT_SNR). No balance, event, deferment, allocation.
**Gets better by:** the well-test validation module becoming a user workflow, not a function: a test arrives → the program shows the stable window it found and why (slope, SE, span) → the engineer accepts, rejects, or overrides with a reason → the decision is timestamped and versioned → the validated rate flows to VFM recalibration. Deferment and allocation stay with the PDMS platform; the program should expose validated rates and confidence to it via API.

### 4.1.3 Remote wireless wellhead monitoring and control
#### 4.1.3.1 IoT sensor deployment & data acquisition
**Program today:** simulated telemetry with fault injection (`telemetry.py`); Modbus/OPC-UA port abstractions (`modbus.py`, `ports.py`).
**Gets better by:** live ingestion replacing simulation as the default path, with the simulator retained as the "what should this well read" reference stream the reconciler already uses.
#### 4.1.3.2 Wireless communication & SCADA integration
**Gets better by:** the user seeing, per well, the path the data took (Yokogawa cloud / Kelton cloud / ABB RTU / edge MQTT) and its freshness — the program's provenance sidecars turned into a "data lineage" panel.
#### 4.1.3.3 Remote actuation & configuration
*Control clause; out of the program's scope by §4.2.1.1 (no cloud-issued control).*
#### 4.1.3.4 Safety & fail-safe operations
*Edge/RTU clause.* Program improvement: never present an advisory as if it were a setpoint; label every recommendation "ADVISORY — Level 3 approval required" per §4.2.1.1(4).
#### 4.1.3.5 Visualization & decision support
**Program today:** matplotlib demo and Qt6 app (`qt6_downhole_app.py`, 141 lines); a survey view.
**Gets better by:** retiring the desktop app in favour of a web surface (§4.2.4, §4.2.12): real-time tiles, performance ranking, trend overlays, geospatial map with alarm overlays — built on the historian, not on the program's own memory.

### 4.1.4 Artificial Lift Monitoring, Control & Optimization
**Tender asks:** monitor and model gas lift, plunger, SRP, ESP; users can modify input parameters and run scenarios; AGLO closed-loop with hybrid physics + ML; real-time alerts on pump trips / insufficient injection.
**Program today:** none of the lift physics; the scenario-and-alert pattern exists only for gauge strings.
**Gets better by:** one shared "scenario" object across all four lift types (inputs, model version, outputs, uncertainty, run log) so the user's mental model is identical whether the well is SRP or ESP.

#### 4.1.4.1 SRP Optimization (§4.1.4.1.1–4.1.4.1.11: dynacard, edge IoT, RPC/VFD, operating modes Auto/Advisory/Hold/Fallback/Manual)
**Program today:** MISSING.
**Gets better by:** the five operating modes (Auto, Advisory, Hold, Fallback, Manual) being a single, visible mode selector per well with the program's fallback state driven by the reconciler's UNEXPLAINED_* classification — when the model can't explain the well, it visibly drops to Fallback, which is the tender's own requirement ("fallback to power-based control if dynamometer data becomes unreliable").

#### 4.1.4.2 Plunger Lift Optimization (§4.1.4.2.1–4.1.4.2.7)
**Program today:** MISSING.
**Gets better by:** the "arrival behaviour" and "cycle qualification" outputs shown as the same verdict vocabulary the program already uses (CONFIRMS / REFUTES / INSUFFICIENT) so a plunger operator and a well-test engineer read the same language.

#### 4.1.4.3 Gas Lift Optimization (§4.1.4.3.1–4.1.4.3.6)
**Tender asks:** lift performance curves and optimum cycle time from real-time data; nodal analysis; multi-well allocation; step-rate testing; restart sequencing.
**Program today:** MISSING (shares the nodal/tubing core with VFM once built).
**Gets better by:** the lift curve being a user artifact with a date, a data window, and a confidence band — regenerated on schedule, versioned, and diff-able — not a static chart.

#### 4.1.4.4 ESP Optimisation (§4.1.4.4.1–4.1.4.4.11)
**Program today:** anomaly/drift classification pattern reusable; ESP physics MISSING.
**Gets better by:** the integrated dashboard (§4.1.4.4.11) showing "recommended action + why + confidence", with the program refusing to recommend when the ESP's telemetry is stale (the refusal pattern, made visible).

### 4.1.5 AI-based Digital Well Integrity Management (§4.1.5.1–4.1.5.6)
**Program today:** MISSING.
**Gets better by:** (if the bidder builds it) reusing the program's inspection bundle so barrier-risk scores are auditable the same way VFM is.

### 4.1.6 Virtual Flow Metering (§4.1.6.1–4.1.6.5)
**Tender asks:** estimate oil, gas, water without physical measurement for all wells; hybrid physics + ML; minimize labelled-data dependence; validate at test wells; production insights and integration.
**Program today:** the validation half is HAVE (leave-one-out harness with published misses, MAE/MAPE, ±σ coverage, refusals, provenance — `blind_harness.py`, `acceptance_tests.py`); the estimation half is MISSING (no IPR, no tubing correlation, no choke model, no ML residual).
**Gets better by:** building the physics core (Vogel/Fetkovich inflow + a published multiphase correlation + choke model) *under* the existing harness, so from the user's first day every VFM rate carries a per-well confidence, a "last validated against test on <date>" stamp, and a refusal when the well has no valid test — and the accuracy table the user sees is regenerated live from held-out tests, never a static claim.

### 4.1.7 Integrated Production System Modelling (§4.1.7.1–4.1.7.6)
**Program today:** MISSING (transient multiphase, wax/hydrate kinetics are COTS-simulator territory).
**Gets better by:** consuming the simulator's outputs as another validated stream in the reconciler so users see "model vs field" residuals for the network the same way they do for a well.

## 4.2 TECHNICAL REQUIREMENTS

### 4.2.1 General technical requirements

#### 4.2.1.1 Indicative Data Flow Architecture / Data communication (cellular APN, existing SCADA path, encryption, **no cloud-issued control — Level 3.5 advisory only**)
**Program today:** no network layer; runs locally.
**Gets better by:** the program declaring itself a Level 4/5 advisory system in every output — recommendations carry an "advisory, Level 3 approval required" marker and a hand-off record when an engineer accepts one. This is a user-visible safety boundary, not a config flag.

#### 4.2.1.2 Data model (canonical schema: owner, unit, engineering range, quality flags)
**Program today:** ad-hoc dataclasses per module.
**Gets better by:** one canonical tag schema (name, unit, range, criticality, quality flag, source) used everywhere; users see quality flags on every value, and the program refuses computations on bad-quality tags with the reason shown.

#### 4.2.1.3 Messaging, APIs & Integration (OPC UA, MQTT 3.1.1/5.0, REST, SAP S/4HANA, RBAC/ABAC with corporate IdP)
**Program today:** OPC-UA and Modbus abstractions PARTIAL; MQTT MISSING; REST MISSING; RBAC MISSING.
**Gets better by:** a REST/OpenAPI surface for every user action (validate test, run VFM, approve, export), MQTT subscribe for ingest, and SSO via OIL's Azure AD so a user has one login for the whole DOF.

#### 4.2.1.4 Change & Continuity (version-controlled configs; export/import; rollback)
**Program today:** version strings and config objects; no rollback.
**Gets better by:** every model/config change being a versioned, named revision the user can compare and roll back from the UI — "which model produced this rate?" answered in one click.

#### 4.2.1.5 AI/ML Compute Infrastructure & Sizing
**Gets better by:** the program publishing its own per-run compute cost so the sizing submission is measured, not guessed.

#### 4.2.1.6 Remote Site Connectivity & Data Resilience (30 % spare bandwidth; **72-h store-and-forward**; chronological rate-controlled replay; dual-SIM failover)
**Program today:** MISSING.
**Gets better by:** the user seeing per-site buffer depth and replay progress after an outage, and the program marking every buffered-then-replayed record so latency SLAs are attributed correctly (§4.2.2).

#### 4.2.1.7 Platform Performance & Capacity Benchmarks (180 concurrent users; 95 % of transactions < 3 s; 700 wells; 95 % of analytics inside the configured interval; 30 % headroom)
**Program today:** single-user, single-process.
**Gets better by:** stateless per-well workers behind a queue and a UI that never blocks on a model run — runs are submitted, tracked, and surfaced when done.

### 4.2.2 Data ingestion and validation (tag onboarding & catalogue; **range, rate-of-change, flatline, spike** filters per tag class; **timestamp latency measured separately at field/edge, OT lake, DOF**)
**Program today:** spike (Hampel) and flatline (FROZEN) PARTIAL in `telemetry.py`; range and rate-of-change MISSING; latency stamps MISSING.
**Gets better by:** all four filters configurable per tag class from an admin screen, with a "why was this sample rejected" view for operators; three latency clocks stamped on every record so the SLA attribution the tender requires is automatic.

### 4.2.3 Production Data Management System

#### 4.2.3.1 Well Test Validation (automated DQ checks; **detect stable period per OIL-agreed logic**; accept/reject; multi-level approval with timestamps; user corrections on MPFM recalibration; virtual rates from validated tests; auto-update models; anomaly flags)
**Program today:** the algorithm is HAVE for gauge legs (`bench.py`); approvals, corrections and model-update loop MISSING.
**Gets better by:** the stable-period logic being an editable rule set OIL signs off (not code), a two-level approval workflow with an immutable event log, and the accepted test automatically triggering VFM recalibration with the before/after model diff shown to the approver.

#### 4.2.3.2 Production Allocation
**Program today:** MISSING. *Platform scope.* The program should hand validated rates + confidence to allocation with lineage intact.

#### 4.2.3.3 Reporting & Analytics (daily/operational/regulatory reports; configurable alerts; data QA/QC; user-defined reports)
**Program today:** CSV export (`export_csv`).
**Gets better by:** scheduled reports per role (daily production, weekly model-health, monthly SLA), all traceable to the run ledger.

### 4.2.4 Remote Well Head Monitoring and Control (real-time dashboards, Grafana/Power BI, < 2 s; map-based; rules + anomaly alerts per ISA-18.2/IEC 62682 with deadband/hysteresis)
**Program today:** matplotlib/Qt.
**Gets better by:** publishing to the historian and letting the platform render; the program contributes alarm *definitions* with deadband and hysteresis so users aren't flooded.

### 4.2.5 SRP Optimization (architecture, integration, security)
**Program today:** MISSING. *See 4.1.4.1.*

### 4.2.6 Plunger Lift Optimization (architecture, edge, HMI, cybersecurity, fail-safe)
**Program today:** MISSING. *See 4.1.4.2.*

### 4.2.7 Gas-Lift Optimization & AGLO (architecture, field instrumentation, HMI, cybersecurity, integration)
**Program today:** MISSING. *See 4.1.4.3.*

### 4.2.8 ESP Optimization (physics core, ML analytics, autonomous control, KPIs: PI excursions −30 %, energy −5 %, gas-lock precision/recall ≥ 80 %)
**Program today:** MISSING. Harness reusable for the KPI back-test.
**Gets better by:** the three KPIs being computed by the program's own held-out harness and shown as a running scoreboard, so the user can see the contract KPI being met or missed in real time.

### 4.2.9 AI-Based Digital Well Integrity (data fusion, MAASP, barrier dashboard, risk scoring, KPIs: alert ≤ 5 min, barrier state ≥ 90 %)
**Program today:** MISSING.

### 4.2.10 Virtual Flow Metering (hybrid engine; offline calibration from well tests; **MAPE ≤ 10–15 % oil/water, ≤ 15–20 % gas**; auto re-fit on drift with change log; uncertainty bands; staleness flags; cadence 1 min – daily; cloud-deployable)
**Program today:** validation HAVE; estimation MISSING; drift classification HAVE (reconciler); re-fit loop and staleness state MISSING.
**Gets better by:** the user's VFM page showing, per well: rate ± band, coverage of the band on past tests, "model version / last calibration / next scheduled check", a staleness badge that changes state per the SLA clock, and a re-fit button that is *disabled* when the annual retraining budget (§4.2.26 note 3) is spent — with the reason shown.

### 4.2.11 Integrated Production System Modelling
**Program today:** MISSING. *See 4.1.7.*

### 4.2.12 Visualization, Reporting & Decision Support (real-time tiles, ranking, alarm wall, drill-downs; self-service analytics; annotations; scalable to all wells with no extra cost)
**Program today:** minimal.
**Gets better by:** user annotations on any point (workover, choke change) stored with the data and visible in exports — the tender's "context & annotations" clause, and the single most-requested feature by production engineers.

### 4.2.13 Technical Specifications of Instrumentation (§4.2.13.1–4.2.13.12: environmental, wireless P/T transmitters, magnetic flow, MPFM, water-cut, Coriolis, gateways, solar, VFD, dynamometer, gas-lift flow computer)
*Instrument clauses.* Program improvement: the tag dictionary carries each instrument's accuracy and stability from these specs (±0.075 % span, ±0.5 °C, ±0.25 % magnetic, ±5 % MPFM liquid, ±0.05 % Coriolis) so every VFM uncertainty band is built from the actual instrument error, not a guess — the gauge-spec injection pattern already in the code, extended to every instrument class.

### 4.2.14–4.2.17 Actuated choke / production valves, autocatcher, VFD for ESP
*Control hardware.* Program shows valve position and fail-safe state as read-only context on the well page.

### 4.2.18 Installation scope · 4.2.19 Safety measures · 4.2.20 FAT · 4.2.21 SAT
*Project clauses.* Program improvement for FAT/SAT: a scripted acceptance run that executes the §4.2.1.7 benchmarks and the SAT scenarios (buffering during link failure, chronological replay, no duplication) and produces the acceptance report — the existing `acceptance_tests.py` extended from unit tests to OIL's acceptance vocabulary.

### 4.2.22 Warranty · 4.2.23 Special Terms · 4.2.25 Instrument Maintainability · 4.2.26 Statutory
*Commercial/statutory.* Program hooks: calibration events (§4.2.25) recorded as first-class data so the program can attribute a rate change to a recalibration rather than to the well.

### 4.2.24 Software Maintainability (web app; no-cost upgrades; audit trail of changes; ticket support; SLA dashboard)
**Program today:** no ticketing, no change history for users.
**Gets better by:** an in-app change log per model and config, and a "what changed since my last login" panel per user.

## 5.0 ANNUAL MAINTENANCE — TERMS & CONDITIONS (53 clauses)
**Program today:** nothing operational.
**Gets better by:** the program producing the AMC evidence OIL asks for without manual work: monthly SLA report (cl. 34), drift/retraining log, change management records (cl. 15), backup verification (cl. 42), and a root-cause template for exclusions (SLA §7(d)).

## 6.0 TRAINING (instrumentation; software — PDMS & online simulator; manuals; UAT)
**Gets better by:** the program shipping with a "training mode" on a sandbox well set (public Volve/Norne data) so OIL staff can practise validation and approval without touching production, and every screen having contextual help using §1.0 vocabulary.

## 7.0 MANPOWER SKILL EXPECTATIONS
*Bidder staffing clause.* Program hook: role-based views map to the roles named (Data Scientist sees hyperparameters and back-tests; Production SME sees validations and approvals; Resident Engineer sees alarms and drift).

## 8.0 COMMON DELIVERABLES (solution design; integration; cybersecurity; dashboards; data governance; training; KPIs; documentation)
**Gets better by:** the program generating its own documentation artifacts from the run ledger and tag dictionary (data dictionary, model cards, KPI report) so the deliverable is a build output, not a writing task.

## 9.0 PROPOSED IMPLEMENTATION PLAN (T1+2 blueprint · T1+10 go-live · T1+11 KT · 5-yr accuracy measurement)
**Gets better by:** the accuracy measurement "ongoing for 5 years post go-live" being the program's own held-out scoreboard, published monthly — the thing it already does for rock properties, pointed at flow rates.

## 10.0 NUMBER OF CONCURRENT USERS (50 viewers · 120 operators · 10 administrators)
**Program today:** one user.
**Gets better by:** three role profiles with matching permissions and landing pages; audit of every approval by user ID.

## Annexure-A (sensorisation of 380 wells) · Annexure-B (well plinths)
*Quantities.* Program hook: the well registry seeded from Annexure-A/B so day-one navigation matches OIL's field, with lift type and instrument set pre-filled.

---

# PART-4 · SECTION-III · SPECIAL CONDITIONS OF CONTRACT

## A. Special Terms & Conditions (1.0–24.0)
**5.0 Inspection** — OIL may inspect source, training datasets, feature definitions, hyperparameters, validation & back-testing results, drift/retraining records.
**Program today:** provenance sidecars; harness outputs; no single bundle.
**Gets better by:** `export_inspection()` — one command producing the SCC 5.0 list for any model version, so an inspection is a download.

**11.0 De-mobilization** — hand over models, pipelines, retraining procedures, dashboards, configs.
**Gets better by:** `export_handover()` producing the SCC 11.0 list, run at every milestone so handover is never a scramble.

**14.0 Sub-contracting not allowed · 13.0/17.0 Key personnel** — *staffing clauses; no program content.*
**16.0 Software with licenses · SBOM** — **Gets better by:** the SBOM (SLA §8b) generated automatically from the dependency graph with hashes, refreshed on every release.
**18.0 Confidentiality / NDA** — program hook: no field data leaves OIL's Azure landing zone; the program runs where the data is.
**20.0 Public cloud** — deploy as a container in OIL's landing zone; SSO via OIL Azure AD.
*21.0–24.0 GST, customs, notices — not program clauses.*

## B. Service Level Agreement

### 1.0 Definitions (Uptime; Downtime; End-to-end latency 95 % ≤ 2 min / 99 % ≤ 5 min; Remote-site availability; Packet loss; Incident; **Model Drift**; **Model Staleness**; P1–P5)
**Program today:** drift *detection* HAVE (reconciler classes); staleness *state* MISSING; latency clocks MISSING.
**Gets better by:** the program's model-health page using the SLA's own definitions as its states — HEALTHY → DRIFT_CONFIRMED → FALLBACK_ACTIVE → RETRAINING → REDEPLOYED — with the SLA timers visible as countdowns.

### 2.0 Measurement of SLA (monthly reports; quarterly penalties; latency measured at three layers)
**Gets better by:** the monthly SLA report generated from the program's own stamps, with the three-layer attribution the tender requires.

### SLAs for Drift & Staleness (evaluate ≥ every 24 h; notify 1 bd; fallback 2 bd; retrain/redeploy 10 bd; safety models revert immediately)
**Gets better by:** a 24-h scheduled drift evaluation writing to the model-health page; automatic notification; one-click "activate approved fallback"; retrain queued against the 4/yr budget.

### 3.0 During-implementation SLA (LDs for mobilization/go-live delay)
*Schedule clause.*

### 4.0 SLA Linked to Tool Accuracy (≥ 95 % at 90 % CI: no penalty; 90–95 %: 5 %; 85–90 %: 10 %; < 85 %: not acceptable)
**Program today:** coverage metric at ±1σ HAVE.
**Gets better by:** reporting at the 90 % CI the SLA specifies, per well and per model, on a page the client can open before OIL's auditor does — so the user knows the tier before the quarter closes. Flag to OIL the inconsistency with §4.2.10's MAPE band.

### 5.0 Post-implementation SLA (uptime 99.95 %; remote-site table: ≥ 99 % availability, 72-h buffer, failover, no duplication, monthly report)
**Gets better by:** per-site availability and buffer status on an admin page; monthly site-wise report auto-generated.

### 6.0 Escalation (L1/L2/L3; P1 15 min / 6 h / 48 h)
**Gets better by:** incidents raised by the program carrying severity per the SLA table, so escalation starts with the right clock.

### 7.0 Exclusions from penalty (force majeure; OIL systems; OIL delays; third-party telecom; external cyber incidents; power/site restrictions — with documented root cause accepted by OIL)
**Gets better by:** the run ledger and latency stamps making the root-cause package a report, not an investigation.

### 8.0 Cybersecurity (VAPT by CERT-In agency pre-go-live and annually; **SBOM** with 8 fields)
**Gets better by:** SBOM auto-generated per release; VAPT findings tracked to closure in-app.

---

# PART-4 · SECTION-IV · SCHEDULE OF RATES (BOQ) / TOR / PAYMENT TERMS

## A. Core Application & Analytics Modules (BOQ 1–13) · B. Web Application (14) · C. AMC (15–20) · D–I hardware, implementation, spares, manpower, installation, migration (21–54)
**Where the program lives in the BOQ:** BOQ 8 (VFM) as the core; the well-test-validation slice of BOQ 1 (PDMS); the harness and inspection/handover bundles as governance for BOQ 5–7's AI/ML; SBOM/lineage evidence for BOQ 11. Everything else is platform, controls, COTS or hardware.

## C. Payment Terms & Milestones
**M2 (20 %)** — architecture, sensorisation, ingestion pipelines. Program hook: tag dictionary and instrument-spec sheets.
**M3 (30 %)** — "calibration and back-testing of AI/ML models using historical and live data; explainability, confidence metrics and performance KPIs demonstrated; code check-in." **This milestone is what the program's validation layer is for.** Gets better by: producing the M3 evidence pack (back-test tables with misses, coverage, refusal reasons, model cards, repo tag) as a build output.
**M4 (40 %)** — validation against approved scenarios; SAT; performance test. Gets better by: the scenario set being a versioned artifact the client can re-run.
**M5 (10 %)** — hypercare with SLA adherence; handover. Gets better by: `export_handover()` at M5.
**AMC** — quarterly. Gets better by: the monthly SLA and drift reports being the invoice backup.

---

## Summary of where the program gets better, by size

**Already the program's strength (make it visible to users):** blind validation with published misses; calibrated confidence; refusals with reasons; residual-drift classification; cited instrument specs; provenance. Today these are developer outputs; every one becomes a user-facing state, badge, page or report.

**Bounded builds (weeks):** VFM physics core under the harness; well-test stable-period workflow with approvals; canonical tag schema with four validators; three-layer latency stamps; drift/staleness state machine with SLA timers and the 4/yr budget; `export_inspection()` and `export_handover()`; auto-SBOM.

**Platform work (integrator):** web UI on the historian, RBAC/SSO, MQTT and 72-h store-and-forward, per-well workers, map dashboards, ticketing.

**Not the program:** lift control, IPSM simulation, well integrity, instrumentation, installation, AMC staffing.

One rule across all of it: every constant in a deliverable is a standard value with its source named, and nothing in a client deliverable uses the program's internal working vocabulary (the vocabulary gate in `client_reports.py` refuses to write a report that does). The gravity kernel runs on CODATA 2018 G, standard gravity and the IUGG mean radius; the rock inventory carries the seventeen published density anchors and Vp ranges (Telford/Geldart/Sheriff; Schön; Christensen and Mooney) with the ranked-candidates classifier, overlap disclosure, out-of-inventory refusals, per-station provenance and the KTB lithology validation.
