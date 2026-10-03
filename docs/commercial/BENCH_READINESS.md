# Bench Readiness - the Hardware Path (awaiting gauges)

**Status:** protocol WRITTEN and shipped in-package (`gea/BENCH_TEST_PROTOCOL.md`);
execution awaits physical hardware. This document is the procurement-ready summary.

## The question the bench answers

Does a gauge with a known history drift within its datasheet? The program
judges every drift trend against the instrument's published drift
specification; that specification has no field validation on record against
a gauge in the program's own hands. The bench is that record: one gauge, one
held setpoint, a reference standard, 90 days or more, and one of four
verdicts written down - WITHIN_DATASHEET, EXCEEDS_DATASHEET, INSUFFICIENT_SPAN,
INSUFFICIENT_SNR. Either of the first two is a deliverable.

## What the bench needs

- One quartz P/T gauge of a cited class (the tool library's `geoq177_30k`
  entry is the reference datasheet), with its serial number and calibration
  certificate.
- A pressure/temperature bench able to hold a setpoint inside the gauge's
  rating for 90 days or longer (a 0.01 %FS/yr gauge at 30,000 psi full scale
  drifts 0.75 psi in 90 days, so the reference and the logging must resolve
  tenths of a psi).
- A reference standard for pressure (deadweight tester or transfer standard
  with a current calibration certificate), logged at the same cadence.
- The program: `gea bench --gauge-csv ... --reference-csv ... --spec geoq177_30k
  --serial ... --certificate ... --out bench_register.jsonl` fits the drift
  slope of the gauge against the reference and compares it with the datasheet,
  with the slope's own uncertainty.

## What the record will show

A WITHIN_DATASHEET record attaches the serial, certificate, dates, apparatus
and fit to the model card's field-validation line. An EXCEEDS_DATASHEET record
is a finding about that instrument - the reason to recalibrate, replace or
re-rate it - and is filed with its certificate. Nothing in the program's
arithmetic changes with either; the datasheet rate stays the rate the reports
use.

*Prepared 2026-08-29, revised 2026-10-02 - ENRGYONE / GEA-Program.*
