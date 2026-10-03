# ENRGYONE — Subsurface Surveying Pilot Proposal

**GEA-Program: the downhole gauge and subsurface surveying program**
Daniel T. Murphy · ENRGYONE · daniel.murphy00@enrgyone.com
Prepared 2026-08-29, revised 2026-09-30 · Product: `gea-program` (PyPI, MPL-2.0)

---

## What we do differently, in one paragraph

Every number this program reports is recomputed from primary archives by its own
acceptance gate — 172 checks at this writing, shipped inside the package — on every
run, and the program's working method is the scientific method as a control loop:
**it publishes its predictions before the data arrives, scores them against ground
truth, and prints the misses next to the hits.** Our first strata prediction is on
the permanent record as *refuted* (off by 10 %), together with the pre-disclosed
assumption that caused it and the correction that now reproduces measured ground to
0.05 % in-sample. No competitor shows you their scoring record. We are built around
ours.

## What exists today (every claim machine-verified by `gea accept`)

- **A 52-entry verified ground-truth library** — every entry licence-checked,
  transcribed verbatim from public archives (PANGAEA, IODP, ICDP/GFZ), with a
  provenance file naming its source, and re-validated against its own archive's
  arithmetic on every gate run. One licence refusal on record: data we could not
  lawfully redistribute stayed out, visibly.
- **The Earth Model:** those sites registered into one geographic and vertical
  frame by archive-declared coordinates only.
- **The sensing kernel:** a borehole-gravity forward model on standard constants
  (CODATA 2018 G, standard gravity, the IUGG mean radius; free-air gradient
  0.30785 mGal/m, never fitted to any dataset), validated against the KTB
  deep-borehole gravimeter.
- **The inverse engine:** measured gravity → strata properties with stated
  uncertainty — every estimate carries its sample support, spread, and the
  assumption it rests on, in words; thin data *declines* rather than guesses. A
  leave-one-out blind harness regenerates the accuracy table on every run.
- **The rock inventory:** seventeen minerals and rocks with published density
  anchors and Vp ranges (Telford, Geldart and Sheriff; Schön; Christensen and
  Mooney), a ranked classifier that prints the overlap instead of one confident
  name, validated against the KTB's published lithology.
- **An instrument and reporting layer:** quartz-gauge simulation with a cited tool
  library, read-only site ingestion (LAS, historian CSV, file follower, Modbus TCP,
  OPC UA, MQTT/Sparkplug B), a two-stream reconciler with disclosed thresholds,
  and the client report family: measurement records with quality flags, gauge
  drift and accuracy statements with bootstrap confidence intervals, well-test
  validation with an approval trail, alarm management with ISA-18.2 KPIs, model
  cards, store-and-forward resilience, monthly SLA measurement, FAT/SAT protocols,
  a software bill of materials, and a dashboard.

## Proof of client-data handling (the part most vendors only promise)

Operator field data ingested to date — a complete horizontal-well directional
survey, drilling-mechanics record, and plan-tracking table — lives in a **private
tier that is excluded from the published package by construction**: absent from the
distribution, never committed to any repository, and never required by any test.
Confidentiality is demonstrated in the build artifacts themselves, not asserted in
a slide. The same ingestion found real value in that data on day one: two vendors'
TVD integrations of the same wellbore disagreed by 8 ft, and the reconciler located
the cause (a projected survey station present in one export and absent in the
other) automatically.

## Proposed pilot (scope options — select any)

1. **Data-room verification.** We ingest a well's existing exports (surveys, EDR,
   logs, plan files) verbatim, cross-checksum them against each other, and deliver
   a discrepancy report in which every finding is reproducible from your own files.
2. **Plan-vs-actual and two-stream reconciliation.** Your planned trajectory and
   drilled surveys, or gauge streams against the described well, reconciled with disclosed thresholds —
   divergences located and quantified, causes identified where the data supports it.
3. **Strata inference with stated uncertainty.** Where the data permits (density,
   gravity, sonic), the inverse engine delivers property columns with stated
   support and spread, under a geological-family prior matched to your basin —
   and a written statement of what the data does *not* support.
4. **Live-data onboarding.** Your OPC UA nodes, MQTT topics or Modbus registers
   mapped to the program's tag catalogue, recorded and replayed, with the
   measurement-record quality rules and alarm definitions run against the live
   stream and the monthly SLA report produced from it.

Deliverables ship as reproducible reports plus, at your option, site integration
and support (see `COMMERCIAL.md`). Client data is handled under the private-tier
discipline above and an NDA (ENRGYONE standard confidentiality agreement available).

## Licensing

`gea-program` is licensed under the Mozilla Public License 2.0. Proprietary
deployment needs no further licence; support, integration and pilot terms are by
engagement, per `COMMERCIAL.md`.

## What we will not do

We will not quote accuracy we have not measured, fill missing measurements with
templates, or present a transferred model as site truth. Where the program does not
know, it says so — that discipline is enforced by the same gate that verifies this
document's numbers.

---
*Every figure above is re-verified by the 172-check acceptance suite
(`gea accept`) on every release; `tools/standalone_check.py` proves on every
release that the package depends on nothing outside itself, numpy and its declared
optional extras.*
