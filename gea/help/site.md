# The site

> The workspace folder, the four roles and what each cannot do, the audit log.

**The command**: `gea workspace --path C:\site --action init --name "Pad 3"`; `gea serve --workspace C:\site` (loopback by default; `--behind-proxy` with a TLS proxy in front, see `deploy/README.md`); `gea users --workspace C:\site --action add --name ... --role admin` (the password from `GEA_PASSWORD`, never the command line). `gea workspace --path C:\site --action rename --name "Pad 3"` sets the site's name as every report prints it.

**What it writes**: `workspace.json`, `wells/<id>/{source,records}`, `config/` (every configuration versioned, diffed, reversible), `monitor/`, `reports/`, `jobs/<id>/{job.json,log.txt}`, `records/audit.jsonl` (append-only, every action with the actor and the SHA-256 of its inputs), `users.json` (salted PBKDF2-SHA256, never a password).

**The roles**: viewer reads everything and changes nothing; operator adds wells, runs reports, starts patches, acknowledges and shelves alarms, commits configuration, records swaps and certificates - but cannot decide a well test or a re-fit, add users, or roll back; approver does everything an operator does and decides well tests and re-fits - but cannot add users, revoke sessions, change site settings or roll back; admin does all of it. Five failed sign-ins lock a name and an address for 15 minutes.

**The number to check**: the last line of `records/audit.jsonl` is the last thing anyone did; `gea doctor --workspace C:\site` ends with `nothing blocks serving`.

**What this page will not call a measurement**: the audit log records who did what to which inputs (by hash); it does not grade the result. Sessions live in the service process: a restart signs everyone out.

## Operations control: stopping and starting the panel

The Administration page carries **Operations control**. Stop ends the run; Restart
ends it and the launcher starts it again. A restart is authorised explicitly every
time, through the popup whose link is the authorisation - a control that reboots the
program on a stray click is not a control. Both are an administrator's; an operator
is refused them.

How it works is one contract. The panel decides how it ends and says so in its exit
code: **0** stopped on purpose, **86** start me again. `start-dashboard.cmd` (and
`start-dashboard.sh`) reads that code, runs the panel again on 86, and on any other
code hands the window to a **PowerShell** prompt that names the program and how to
start it. It never falls back to the shell underneath - which on a console opened
from a Python profile is a bare `>>>` prompt where a program used to be.

The switch marked *let the launcher start it again by itself* covers an unexpected
stop - a crash, not a decision. It is written to `records/auto_restart.flag`, one
character in one file, because the launcher is a batch script and cannot read
`workspace.json`; the manifest stays the record of your choice and the flag is how
that choice reaches the thing that acts on it. A missing or unreadable flag reads as
off. At most five stops in a row are answered with a restart, so a panel that
crashes while starting hands you the error instead of flickering all night.

Every start, stop, restart and recovery is one appended line in
`records/runlog.jsonl`, flushed to the disk **before** the thing it describes is
attempted - a log written afterwards is no use to a machine that lost power during
it. A run that logged a start and never logged a stop reads back as *killed*, and
the card and `gea doctor --workspace ...` both say so rather than calling it a clean
stop.

After a restart or a power cut the recovery runs in an order that is not negotiable:
**the live patches come up first**, and the rebuilding of the reports runs behind
them. A stream that is not running is losing records nothing can recover later; a
report that is behind its source can be rebuilt at any time from records already on
the disk. The catch-up rebuilds exactly what the staleness table says is behind its
source and leaves the rest alone. It does not call a gap in the stream recovered:
what did not arrive did not arrive, and the records say where the gap is.

Related: `gea help upkeep`, `gea help start`.
