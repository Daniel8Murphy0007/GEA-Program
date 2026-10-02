# Upkeep

> Keeping a site running for years: logs, jobs, sessions, the proxy, the load it can carry.

**The command**: `gea housekeeping --workspace C:\site` (a dry run that lists what it would do) and `--apply`; schedule it daily from Administration (preset "housekeeping"). `gea loadtest --patches 16 --seconds 60 --with-service` before a site goes live. Administration -> Live sessions lists who is signed in from where and revokes one or all of a user's sessions; "Your preferences" has "Sign out everywhere else". `deploy/README.md`, `Caddyfile` and `nginx-gea.conf` put TLS in front; start the service with `--behind-proxy`.

**What it writes**: housekeeping segments the append-only logs (`audit.jsonl.<stamp>`, `alarm_events.jsonl.<stamp>`; the fresh alarm log starts at the carried PROCESSED watermark so nothing is re-processed), removes finished job folders older than 30 days beyond the newest 500, removes live recordings older than 90 days except the newest stream file per well, and audits the run. The load test leaves nothing behind.

**The number to check**: the housekeeping dry run's counts before `--apply`; the load test's verdict line: all connected, all healthy at the end, the service's p95 under a second.

**What this page will not call a measurement**: a segmented log is not a deleted one - every segment is the record. Deletion is only ever of finished job folders and old recordings whose samples are already folded into the stream.

Related: `gea help site`, `gea help patches`.
