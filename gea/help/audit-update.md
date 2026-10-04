# Audit / Update

> The whole audit log with filters and a CSV, the running program against the newest release, and every report against its source.

**The command**: `gea workspace --path C:\site --action audit` prints the last fifty audit lines; the page's Audit / Update tab shows the whole log filtered by who, by action and since a date, with a CSV download. `gea update --check` compares the running version with the newest on PyPI; `gea update` (or the page's "Update the program from PyPI", admin) runs `pip install --upgrade "gea-program[live,plotting,xls,desktop]"` from the same Python and then asks for the service to be restarted - the program never restarts itself. `gea workspace --path C:\site --action refresh-all` (or the page's "Update every report from its source", operator) runs the dashboard for every well and then every seismic station, as one job with a log.

**What it writes**: the audit log is `records/audit.jsonl`, append-only - every action with its actor, its time and the SHA-256 of its inputs; a program update writes nothing into the site, only into Python's site-packages (the job log holds pip's output); a data update rewrites `reports/` from the sources and adds one line, `workspace.refresh_all`, with the counts and the number of errors.

**The number to check**: on the program card, the running version beside the newest on PyPI - `current` means nothing newer exists, `behind` names the newer one, `PyPI not reachable` means this machine is offline and an offline installation is updated by installing the newer kit from the release page. On the data card, every row says `current` or `stale`: `stale` means the source file changed after the report was generated, or no report exists yet.

**What this page will not call a measurement**: a version number is not a verdict on the program - the acceptance gate under Verification is; a `current` row says only that nothing changed under the report since it was written, not that the report is right; and the audit log records what was done, by whom, to which inputs - never why.

Related: `gea help site`, `gea help doctor`, `gea help start`.
