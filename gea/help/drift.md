# Drift

> The residual against the well model, the aging band, and what the labels mean.

**The command**: `gea workspace --path C:\site --action refresh` (or "Refresh every report" on Home); on one file, `gea dashboard --file historian.csv --out out`.

**What it writes**: `gauge_drift_report.html/.md/.json` per well: per station the bias, the fitted slope, the noise sigma, the aging envelope and a classification - IN_FAMILY, CALIBRATION_OFFSET, UNEXPLAINED_OFFSET, DRIFT_CONSISTENT, UNEXPLAINED_TREND, TRANSIENTS, INSUFFICIENT_DATA - with the thresholds disclosed in section 6: bias significance 4 x noise / sqrt(n) + 1 psi, transient gate 6 x noise, bias too large for calibration 500 psi, envelope margin 2 x the conventional rate, minimum window for a trend about 18 days, at least 8 samples.

**The number to check**: the bias in psi per station against the instrument's own stated accuracy (section 9 prints both when a certificate is filed). A bias inside the certificate's band is not drift.

**What this page will not call a measurement**: the aging band. It is the span between a conventional datasheet aging model and the program aging model; the program model multiplies the conventional one by a fixed composition of engineering constants (ratio 0.9686) that has no field validation on record - the model card states `NONE ON RECORD` and the band is never used alone. The classifications are triage labels from disclosed cutoffs, not a measurement of the gauge. A recorded sensor swap restarts the fit at the swap.

Related: `gea help instruments`, `gea help quality`, `gea model-cards`.
