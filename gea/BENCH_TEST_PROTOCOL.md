# Bench-Test Protocol — Measuring the 1.0324 Suppression Ratio

Status of the quantity under test: `canonical_suppression()` = **1.0324 at
unity trims** is today a **DERIVED_HYBRID** composition — industry baseline
drift (0.215 %FS/yr class) with HPHT stress dressing, divided by a
the program's suppression composition of three locked constants. It is **not** a measured constant, and the
product labels it so everywhere it appears. This protocol is the experiment
that would change — or refuse to change — that label.

---

## 1. Falsifiable prediction

Two matched GEOQ-177-class quartz P/T gauges (same spec, same batch
preferred), one operated with the GEA-informed configuration and one as the
conventional reference, co-located at a fixed HPHT setpoint, will exhibit a
long-term drift-rate ratio

```
R = drift_conventional / drift_program = 1.0324   (at unity trims)
```

equivalently a predicted separation rate of ~2.3 psi/yr at a 30,000-psi
full scale (the service-life module's number for these conditions). The
prediction is falsifiable in both directions: a measured R statistically
inconsistent with 1.0324 **refutes the composition at bench conditions**.

## 2. Apparatus

- 2× (minimum; 3+ pairs preferred for scatter) quartz P/T gauges,
  177 °C / 30 kpsi class, from the same vendor lot.
- Temperature-controlled bath or HPHT vessel holding a setpoint in the
  150 °C / 10,000 psi class (inside every rating — the rating_check rule
  applies on the bench too).
- Reference pressure standard, 0.01 %-class (deadweight tester or
  transfer standard), for baseline and periodic checks.
- Logging at ≥ 1 reading/day per gauge into the package's historian CSV
  format (`time_s` + one raw pressure column per gauge).

## 3. Duration — the package's own rule applies

The reconciler refuses trend verdicts below its minimum span
(`ReconcilerConfig.min_trend_span_years`, the ≥18-day slope rule). The bench
protocol requires **≥ 90 days** at setpoint for slope confidence; 180+ days
preferred. The analysis tool enforces the 18-day floor mechanically and
reports `INSUFFICIENT_SPAN` below it — the bench cannot be rushed past the
product's own honesty rule.

## 4. Procedure

1. Baseline both gauges against the reference standard; record offsets.
2. Install co-located at the setpoint; log continuously.
3. Weekly: verify setpoint stability against the reference (do not adjust
   the gauges under test).
4. Export each leg as a historian CSV; run
   `python -m gea bench --program-csv A.csv --conv-csv B.csv`.
5. Repeat with roles swapped on a second pair (control for unit-to-unit
   scatter — the piezo lesson: individual units scatter around class curves).

## 5. Analysis and verdict vocabulary

`bench.bench_analysis()` fits each leg's drift slope (OLS, psi/yr →
%FS/yr), propagates slope uncertainties into the ratio, and returns one of:

- `MEASURED_CONFIRMS` — R within its own propagated uncertainty of 1.0324.
- `MEASURED_REFUTES`  — R inconsistent with 1.0324 (the uncertainty is small
  enough to exclude it). **This is a valid and useful outcome** — the
  composition is falsified at bench conditions and the label stays
  DERIVED_HYBRID with the refutation on record.
- `INSUFFICIENT_SPAN` — under the 18-day floor: no verdict.
- `INSUFFICIENT_SNR`  — the uncertainty band contains both 1.0324 and 1.0
  (cannot distinguish suppression from no-suppression): no verdict, more
  data required.

## 6. Outcome handling (labeling rules)

- **Confirmed:** `canonical_suppression()`'s label may move from
  DERIVED_HYBRID to MEASURED_ON_BENCH **with the test ID, dates, apparatus
  and per-pair results attached**. The composition's constants are then a
  measured-consistent model, not just a labeled one.
- **Refuted:** the label stays DERIVED_HYBRID; the refutation (conditions,
  measured R, uncertainty) is recorded beside it. No silent retuning of
  trims to fit the bench — a post-hoc trim fit would be a new calibration
  claim, labeled as such, never a derivation.
- **Either way:** `U_i` remains loaded-but-unused in the drift formula until
  a GEA derivation path for its role is supplied (Rule 10 — the framework
  author provides the physics; the product does not invent it).

## 7. Analysis-tool verification (available today, and labeled)

The analysis tool is verified NOW against the simulator's own twin legs:
`bench --selftest` generates synthetic paired-leg data from the engine's
models and must return `MEASURED_CONFIRMS` at R ≈ 1.0324. **This is a
SIMULATION_SELF_TEST — it verifies the analysis arithmetic, not the
physics.** It proves the bench pipeline is ready; it proves nothing about
gauges. The acceptance suite runs it on every gate.
