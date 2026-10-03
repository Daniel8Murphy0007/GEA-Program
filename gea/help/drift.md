# Drift

> The residual against the well model, the aging band, and what the labels mean.

**The command**: `gea workspace --path C:\site --action refresh` (or "Refresh every report" on Home); on one file, `gea dashboard --file historian.csv --out out`.

**What it writes**: `gauge_drift_report.html/.md/.json` per well: per station the bias, the fitted slope, the noise sigma, the datasheet aging rate and a classification - IN_FAMILY, CALIBRATION_OFFSET, UNEXPLAINED_OFFSET, DRIFT_CONSISTENT, UNEXPLAINED_TREND, TRANSIENTS, INSUFFICIENT_DATA - with the thresholds disclosed in section 6: bias significance 4 x noise / sqrt(n) + 1 psi, transient gate 6 x noise, bias too large for calibration 500 psi, envelope margin 2 x the conventional rate, minimum window for a trend about 18 days, at least 8 samples.

**The number to check**: the bias in psi per station against the instrument's own stated accuracy (section 9 prints both when a certificate is filed). A bias inside the certificate's band is not drift.

**What this page will not call a measurement**: the datasheet aging rate. It is the instrument's published drift specification (percent of full scale per year, from the cited datasheet), with nothing added for temperature or pressure - above the rating it is flagged, not changed - and the program has no aging model of its own. No gauge with a known history has yet been run against it by this program: the model card states `NONE ON RECORD`, and `gea bench` is the instrument that would change that line. The classifications are triage labels from disclosed cutoffs, not a measurement of the gauge. A recorded sensor swap restarts the fit at the swap.

Related: `gea help instruments`, `gea help quality`, `gea model-cards`.
