# Bench-Test Protocol - Does this gauge drift within its datasheet?

The program judges a measured drift trend against one number: the
instrument's published drift specification (percent of full scale per year,
from its datasheet). That number is a specification, not a measurement of
the gauge in your hand. This protocol is the measurement: one gauge, one
held setpoint, one reference standard, enough weeks, and a verdict written
down - the bench record the drift report cites as `NONE ON RECORD` until it
exists.

---

## 1. What is being tested

For a gauge of serial number S with datasheet drift specification D (%FS/yr):

```
measured drift rate (|slope| of the gauge's reading against the reference, at a held setpoint)
compared with D, with the slope's own standard error
```

The outcome is one of four words (section 5). A gauge that drifts faster
than its datasheet is a finding, not an error.

## 2. Apparatus

- The gauge under test, with its serial number and its calibration
  certificate id (both go on the record; `gea certificates` files the
  certificate).
- A temperature-controlled bath or pressure vessel holding a setpoint inside
  the gauge's rating (the rating check applies on the bench too).
- A reference pressure standard of a class better than the specification
  under test (deadweight tester or transfer standard), logged at the same
  cadence, or at least checked weekly and logged.
- Logging at one reading per day or better into the program's historian CSV
  format (a timestamp or `time_s` column and one pressure column per
  instrument).

## 3. Duration - the program's own rule applies

The reconciler refuses trend verdicts below its minimum span
(`ReconcilerConfig.min_trend_span_years`, the 18-day rule), and the bench
analysis reads that floor from the same configuration: a shorter record is
`INSUFFICIENT_SPAN`, no verdict. The protocol asks for **at least 90 days**
at setpoint; 180 days or more when the specification is tight (a 0.01 %FS/yr
gauge at 30,000 psi full scale drifts 3 psi in a year - 0.75 psi in 90
days - so the reference and the logging must resolve tenths of a psi).

## 4. Procedure

1. File the certificate (`gea certificates --action add ...`) and record the
   serial number.
2. Install the gauge and the reference at the setpoint; log continuously.
3. Weekly: verify the setpoint against the reference; do not adjust the gauge
   under test. Log every intervention.
4. Export the gauge's series and the reference's series as historian CSVs.
5. Run:

       gea bench --gauge-csv gauge.csv --reference-csv reference.csv --spec geoq177_30k --serial SN --certificate CERT --out bench_register.jsonl

   The reference series is subtracted first (the setpoint's own wander is
   not the gauge's drift). `--spec` names the datasheet preset or a JSON
   datasheet of your own.
6. Keep the record: the JSON line appended to the register carries the fit,
   the band, the datasheet source, the serial, the certificate and the verdict.

## 5. Analysis and verdict vocabulary

- The slope of the (gauge - reference) series is fitted by least squares;
  its standard error comes from the residuals.
- A band of k sigma (default 2) is placed around the measured rate.
- `WITHIN_DATASHEET` - the whole band is at or below the specification.
- `EXCEEDS_DATASHEET` - the whole band is above the specification: this
  gauge, at these conditions, drifts faster than its datasheet says. A
  first-class outcome.
- `INSUFFICIENT_SPAN` - shorter than the 18-day floor; no verdict.
- `INSUFFICIENT_SNR` - the band straddles the specification; a longer span
  or a quieter setpoint is needed; no verdict.

`gea bench --selftest` runs the arithmetic on synthetic series and labels
its output `SIMULATION_SELF_TEST`; it proves nothing about a physical gauge.

## 6. What the record changes

Nothing in the program's arithmetic changes with a bench result; the
datasheet rate stays the rate the reports use. What changes is the model
card's `field validation` line, which can cite the register once a gauge
with a known history has been run, and the reader's confidence in a
`DRIFT_CONSISTENT` label on that instrument. An `EXCEEDS_DATASHEET` record
is the reason to recalibrate, replace or re-rate that gauge, and to file
the result with its certificate.
