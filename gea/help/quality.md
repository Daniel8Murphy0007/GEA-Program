# Quality rules

> Every sample carries a flag and the rule that fired, with its limit.

**The command**: the rules run inside every report; `gea alarms --file historian.csv --write-default-definitions defs.json` writes the catalogue-derived limits so you can read them. Flags: GOOD, RANGE (outside the engineering range), ROC (rate of change over the limit per second), FLATLINE (the same value for the configured number of samples), SPIKE (beyond n x MAD in a window), STALE (older than the staleness limit), GAP (no value, or a sentinel).

**What it writes**: the data-quality section of the drift report (`gauge_drift_report.*`) with GOOD % per tag and the flag counts; `gauge_drift_report_records.csv` with the flag and the reason on every sample, for example `value 6209.49 > eng_range_hi 6000`.

**The number to check**: GOOD % per tag in section 3 of the drift report; the limits per tag in section 6 ("Quality rules per tag") with their basis (datasheet, catalogue default, or client setting).

**What this page will not call a measurement**: a flag is a rule firing on a limit, not a judgement of the gauge. A limit comes from the gauge datasheet or the catalogue and is printed; change it in `alarm_definitions` or the tag catalogue and the change is a versioned commit.

Related: `gea help alarms`, `gea help drift`.
