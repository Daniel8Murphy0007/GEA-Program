# Bench Readiness — the Hardware Path (awaiting gauges)

**Status:** protocol WRITTEN and shipped in-package (`gea/BENCH_TEST_PROTOCOL.md`);
execution awaits physical hardware. This document is the procurement-ready summary.

## The falsifiable claim the bench decides

The program's drift-suppression composition predicts a **drift ratio of 1.0324 at
unity trims** between paired quartz gauges — ~2.3 psi/yr separation at 30,000 psi
full scale. Today that number is labeled DERIVED_HYBRID (an engineering model with no
field validation on record); the bench turns it into either MEASURED_ON_BENCH
(confirmation, test record attached) or a REFUTATION ON RECORD. Both outcomes are
designed in; no silent retuning of trims is permitted (gate-pinned rule).

## What the bench needs

- Two quartz P/T gauges of the same class (the tool library's `geoq177_30k` entry
  is the reference datasheet), one conventional, one conditioned.
- A pressure/temperature bench able to hold matched conditions in the 150-175 degC,
  15,000-25,000 psi window for a run of 90 days or longer (drift accrues slowly).
- A reference standard for pressure (deadweight tester or transfer standard with a
  current calibration certificate) read at every checkpoint.
- The program: `gea bench` fits each leg's drift slope from the recorded CSV and
  reports the measured ratio with its uncertainty against the predicted 1.0324.

## What the record will show

Either outcome is a deliverable. A confirmation attaches the test ID, dates,
apparatus and per-pair results to the model card and moves the label. A refutation
records the conditions, the measured ratio and its uncertainty beside the
prediction, and the label stays DERIVED_HYBRID. The model card and the accuracy
statement print whichever it is.

*Prepared 2026-08-29, revised 2026-09-30 - ENRGYONE / GEA-Program.*
