# ENRGYONE — Subsurface Surveying Pilot Proposal

**GEA-Program: the GEA Subsurface Surveying Tool**
Daniel T. Murphy · ENRGYONE · daniel.murphy00@enrgyone.com
Prepared 2026-08-29 · Product: `gea-program` v0.406.0+ (PyPI) · Technical basis: PAPER_2258

---

## What we do differently, in one paragraph

Every number this tool reports is recomputed from primary archives by an automated
fidelity gate — 5,999 assertions at this writing — on every run, and the tool's
operating doctrine is the scientific method as a control loop: **it publishes its
predictions before the data arrives, scores them against ground truth, and pins the
misses next to the hits.** Our first strata prediction is on the permanent record as
*refuted* (off by 10%), together with the pre-disclosed assumption that caused it and
the correction that now reproduces measured ground to 0.05% in-sample. No competitor
shows you their scoring record. We are built around ours.

## What exists today (all claims machine-verified)

- **A 52-entry verified ground-truth library** spanning 37 regions and 39 physical
  kinds — every entry license-checked, transcribed verbatim from public archives
  (PANGAEA, IODP, ICDP/GFZ), and re-validated against its own archive's arithmetic
  on every gate run. One license refusal on record: data we could not lawfully
  redistribute stayed out, visibly.
- **The Earth Model:** those sites registered into one geographic and vertical frame
  by archive-declared coordinates only — 29 sites, 19,313 km of great-circle span.
- **GEA sensing kernels:** a gravity forward model composed entirely from
  framework-derived constants (free-air gradient 0.30804 mGal/m, never fit to any
  dataset) validated against the KTB deep-borehole gravimeter at **correlation
  0.9968**; and a structural prior whose seven Earth-shell values are exact
  primitive compositions, consistent with all 29 independent archives (zero
  violations).
- **The inverse engine:** measured gravity → strata properties with honest
  uncertainty — every estimate carries its sample support, spread, and the
  assumption it rests on, in words; thin data *refuses* rather than guesses.
- **An instrument layer:** quartz-gauge simulation, tool library, read-only site
  ingestion (LAS, historian CSV, Modbus TCP), and a two-stream reconciler that
  classifies live-vs-predicted offsets with disclosed thresholds.

## Proof of client-data handling (the part most vendors only promise)

Operator field data ingested to date — a complete horizontal-well directional
survey, drilling-mechanics record, and plan-tracking table — lives in a **private
tier that is excluded from the published package by construction**: absent from the
distribution manifest (machine-enforced on every build), never committed to any
repository, and never required by any test. Confidentiality is demonstrated in the
build artifacts themselves, not asserted in a slide. The same ingestion found real
value in that data on day one: two vendors' TVD integrations of the same wellbore
disagreed by 8 ft, and the reconciler located the cause (a projected survey station
present in one export and absent in the other) automatically.

## Proposed pilot (scope options — select any)

1. **Data-room verification.** We ingest a well's existing exports (surveys, EDR,
   logs, plan files) verbatim, cross-checksum them against each other, and deliver
   a discrepancy report in which every finding is reproducible from your own files.
2. **Plan-vs-actual and twin-stream reconciliation.** Your planned trajectory and
   drilled surveys, or twin gauge streams, reconciled with disclosed thresholds —
   divergences located and quantified, causes identified where the data supports it.
3. **Strata inference with honest uncertainty.** Where the data permits (density,
   gravity, sonic), the inverse engine delivers property columns with stated
   support and spread, under a geological-family prior matched to your basin —
   and a written statement of what the data does *not* support.

Deliverables ship as reproducible reports plus, at your option, the software itself
under commercial license. Client data is handled under the private-tier discipline
above and an NDA (ENRGYONE standard confidentiality agreement available).

## Licensing

`gea-program` is dual-licensed: AGPL-3.0 for research use; commercial
licensing through ENRGYONE for proprietary deployment, per COMMERCIAL.md. Pilot
terms, scope, and pricing by engagement.

## What we will not do

We will not quote accuracy we have not measured, fill missing measurements with
templates, or present a transferred model as site truth. Where the tool does not
know, it says so — that refusal discipline is enforced by the same gate that
verifies this document's numbers.

---
*Technical appendix: PAPER_2258 (in-package, `whitepapers/`); every figure above is
re-verified by `fidelity_tests.py` and the 77-check corpus-independent
acceptance suite on every release.*
