# Instruments

> A swapped gauge is a new instrument; a certificate says what the instrument is good for.

**The command**: on the well page, "Record a swap" (tag, time, old and new serial, certificate id) or "Confirm" a candidate the step detector proposes; `gea swaps --register wells/<id>/records/sensor_swaps.jsonl --action add --tag P_raw_psi_S2 --at 2026-03-09T00:00:00Z --new-serial SN-NEW`. "File a certificate" (serial, laboratory, issued, valid until, accuracy as % of full scale, the document); `gea certificates --register ... --action status`.

**What it writes**: `sensor_swaps.jsonl` and `certificates.jsonl` (append-only) under the well's records; the next refresh fits each swapped tag on the samples after its swap only and prints section 9 of the drift report: the swaps, the samples excluded, each certificate's status - VALID, EXPIRING (inside 60 days), EXPIRED, MISSING - and the stated accuracy beside the measured bias.

**The number to check**: `Bias vs accuracy` in section 9: inside or OUTSIDE the instrument's own band. Home shows how many certificates are in date.

**What this page will not call a measurement**: a swap candidate. The detector looks for a jump in one gauge's offset against its peers (a process change moves every gauge together; a swap moves one); with a single peer it cannot tell which gauge moved and says so. A candidate changes nothing until a person confirms it from the work order.

Related: `gea help drift`, `gea help transients`.
