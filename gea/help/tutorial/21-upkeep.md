# 21. Backups, jobs, the audit log, the doctor and verification
Who: office
Where: #/audit
Takes: 30 minutes
> Keeping a site running for years: the copy off the machine, what ran and what failed, who did what, which code is running, and the gate that proves it.

## Steps
1. **The backup** (Audit / Update): the folder on another disk or a share, **Back up now**, **Schedule daily**. One dated archive each, `gea-site-Pad-3-20261013T020000Z.zip`, with a manifest and a `.sha256`. The doctor warns when there has never been one or the last is more than 26 hours old.
2. Restore one, once, so you know it works: in a terminal, `gea backup --restore D:\gea-backups\gea-site-Pad-3-20261013T020000Z.zip --to C:\site-restored`; it verifies every file again and opens the result as a workspace. Then Administration, **Workspaces** opens it in a window of its own to look. A backup nobody has restored is a hope.
3. **Jobs**: every long run - a refresh, a pull, a packet, a fetch - is a job with a log; the tab carries a badge with those that failed in the last 24 hours. Click one for its log.
4. **Audit / Update**, the audit log: every action with who, when, and the SHA-256 of its inputs, append-only; filter by person, by action, since a date; download the CSV.
5. **Verification**: the acceptance gate the program ships with - the same checks the build runs before a release - **Run the acceptance suite** on this machine (half an hour, as a job); **Standalone check** proves the program imports nothing outside its own files; **Soak (15 min)** cuts and restores the site's own patches to see them reconnect; **SBOM** lists what is installed. Each result is a report with every check named.
6. **Which code is running**: when the page looks old or a button says *no such route*, the doctor - `gea doctor --workspace C:\site --port 8765` in a terminal - prints the Python, the package, any second copy on the path, each dependency, the launcher, the newest release, and with the workspace the folders, the accounts and the patches; every WARN or BLOCK line carries its fix.
7. Housekeeping: schedule it daily from Administration (the `housekeeping` preset): logs rotated, finished jobs trimmed, stale sessions closed.

## You know it worked when
The restored workspace opens and shows the same wells; the gate on this machine reports every check passed.

Help: backup, audit-update, doctor, soak, upkeep
