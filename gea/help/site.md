# The site

> The workspace folder, the four roles and what each cannot do, the audit log.

**The command**: `gea workspace --path C:\site --action init --name "Pad 3"`; `gea serve --workspace C:\site` (loopback by default; `--behind-proxy` with a TLS proxy in front, see `deploy/README.md`); `gea users --workspace C:\site --action add --name ... --role admin` (the password from `GEA_PASSWORD`, never the command line).

**What it writes**: `workspace.json`, `wells/<id>/{source,records}`, `config/` (every configuration versioned, diffed, reversible), `monitor/`, `reports/`, `jobs/<id>/{job.json,log.txt}`, `records/audit.jsonl` (append-only, every action with the actor and the SHA-256 of its inputs), `users.json` (salted PBKDF2-SHA256, never a password).

**The roles**: viewer reads everything and changes nothing; operator adds wells, runs reports, starts patches, acknowledges and shelves alarms, commits configuration, records swaps and certificates - but cannot decide a well test or a re-fit, add users, or roll back; approver does everything an operator does and decides well tests and re-fits - but cannot add users, revoke sessions, change site settings or roll back; admin does all of it. Five failed sign-ins lock a name and an address for 15 minutes.

**The number to check**: the last line of `records/audit.jsonl` is the last thing anyone did; `gea doctor --workspace C:\site` ends with `nothing blocks serving`.

**What this page will not call a measurement**: the audit log records who did what to which inputs (by hash); it does not grade the result. Sessions live in the service process: a restart signs everyone out.

Related: `gea help upkeep`, `gea help start`.
