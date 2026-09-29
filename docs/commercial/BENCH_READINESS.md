# Bench Readiness — the Hardware Path (awaiting gauges)

**Status:** protocol WRITTEN and shipped in-package (`gea/BENCH_TEST_PROTOCOL.md`,
v1.48.0); execution awaits physical hardware. This document is the procurement-ready summary.

## The falsifiable claim the bench decides

The GEA drift-suppression composition predicts a **drift ratio of 1.0324 at unity trims**
between paired quartz gauges — ~2.3 psi/yr separation at 30,000 psi full scale. Today that
number is labeled DERIVED_HYBRID (simulated composition); the bench turns it into either
MEASURED_ON_BENCH (confirmation, test record attached) or a REFUTATION ON RECORD. Both
outcomes are designed in; no silent retuning of trims is permitted (gate-pinned rule).

## What to procure

- **2x matched HPHT quartz P/T gauges**, GEOQ-class (30k psi FS, 0.215 %FS/yr conventional
  drift spec, digital output) — the pairing is the experiment; same model, adjacent serials.
- **Pressure source + reference:** dead-weight tester or calibrated controller to 30k psi FS
  class; NIST-traceable reference transducer.
- **Thermal stability:** bath or oven holding setpoint to +/-0.1 degC over months.
- **Logging:** any historian emitting CSV or Modbus TCP — both already ingest read-only
  into the reconciler; no custom software needed.

## Duration and analysis (from the shipped protocol)

>= 90 days at constant setpoint; the in-package analysis classifies the pair drift under
the four-verdict scheme with disclosed thresholds. The reconciler and drift-envelope code
that will judge the bench are the same gate-verified modules that ship to clients.

## Why this matters commercially

One bench confirmation converts the product's single hybrid-labeled constant into measured
physics — the last honesty flag the independent evaluation listed. One refutation is a
published correction, which under this program's doctrine is also a deliverable.

*Prepared 2026-08-29 - ENRGYONE / GEA-Program.*
