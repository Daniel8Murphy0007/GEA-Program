# Shut-ins and build-ups

> Every shut-in in the record gets a first-look build-up analysis, with its band and its caveats.

**The command**: runs at every refresh for file and live wells; `gea transient --file historian.csv --params params.json --out pta` for one file (`--mtr 2:40` to choose the middle-time region yourself). Save the rock and fluid parameters (q, B, mu, h, phi, c_t, r_w, field units) on the well page; without them the result is the slope and p* only.

**What it writes**: `pressure_transient_report.*` per well: the shut-ins found (by the rate channel, the on-stream hours, or the pressure signature alone - marked `inferred`) with duration, flowing time before, rise and a qualification or the rule that failed; for each qualified one the Horner slope m, p*, p at 1 h, the middle-time region and the rule that chose it (a flat Bourdet derivative on a log-binned copy), wellbore storage, and with parameters kh, k, skin, the pressure drop across the skin and the radius of investigation, each with a 90 % band from a residual bootstrap.

**The number to check**: the middle-time region's start and end and the rule beside them. Move the region and the result moves with it; if the rule says "late half of the data used, indicative only", no radial-flow plateau was found.

**What this page will not call a measurement**: a permeability from a build-up with no parameters, a result from a region that begins less than 1.5 log cycles after wellbore storage ends (the caveat is printed), or a shut-in inferred from pressure alone until the operations log confirms it. This is the first look every shut-in should get automatically, not a replacement for a full interpretation with a reservoir model.

Related: `gea help instruments`, `gea help data-in`.
