# Patches

> A supervised live connection: it reconnects on its own, records every sample, and says when it is down.

**The command**: Patch panel -> Add a patch (protocol, well, the tag map; "Load the example map" for a start), then Start. `gea wits0-sim --port 5001` sends WITS0 frames to rehearse with; the WITSML test store in `gea/witsml.py` (section AD of the gate uses it) stands in for a server. States: STOPPED, CONNECTING, CONNECTED, DEGRADED (no sample for longer than `stale_after_s`), DOWN (reconnecting with backoff 2 to 60 s).

**What it writes**: `wells/<id>/records/live/patch_<name>_<YYYYMMDD>.records.csv` as each sample arrives, `patch_<name>.state.json` as a heartbeat (state, last sample, samples per minute, latency p50/p95, reconnects), and the folded `<stamp>_stream.csv` at refresh (priority decides when two patches carry the same tag).

**The number to check**: `samples per minute` and `latency p95` on the panel; a patch that is CONNECTED with zero samples per minute is a map that matches nothing (check the item codes or node ids).

**What this page will not call a measurement**: the latency, which is the sender's stamp against the receiver's clock; on a sender with no clock (serial WITS0) it is the receive time and the panel says so. A DEGRADED patch is a missing feed, not a bad gauge.

Related: `gea help data-in`, `gea help notifications` (patch.down), `gea loadtest`.
