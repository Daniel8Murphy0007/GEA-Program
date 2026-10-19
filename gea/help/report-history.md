# Report history

> Every version of every client report, kept. A report is written to a fixed name so the latest is always where a reader expects it; each new one replaced the last. Now, every time a client report is written inside the workspace's reports folder, a copy of each file it wrote is kept in a dated folder under `reports\history\`, with the report id, the time and each file's SHA-256.

**The printable copy**: every client report is now also written as a PDF beside its HTML (US Letter, the PDF base fonts, the report's drawings redrawn as vector paths, long tables continued across pages with their header row, "page n of N" on each), and the PDF is kept in the history with the rest.

**The command**: nothing to press - it happens whenever a client report is written (the pad and well reports, the drilling records, drift, alarm, well-test, accuracy, SRA, site, seismic and other client reports). The Reports tab's **Report history** card lists every report with its versions and links to each file. From a terminal: `gea reports-history --workspace C:\site` lists them; `--family wells/Well-1/well_report` lists one report's versions in full; `--prune --keep 20` shows what keeping the newest 20 of each would remove, and `--apply` removes it.

**What it writes**: `reports\history\<where>\<report>\<UTC time>\` holding the files of that version, and one line per version in `reports\history\index.jsonl` (time, report, report id, and each file's size and SHA-256). A report written outside a workspace - a sample, an export - keeps no history; the month-end packets keep their own copies.

**The number to check**: the SHA-256 of a version's file against its line in the index (`certutil -hashfile <file> SHA256` on Windows); the newest version's files against the report at its usual path - identical until the next one is written.

**What this page will not call a measurement**: a version's content - it is the report as written then, from what the workspace held then; the history is a copy, and the report at its usual path is the one the program reads. A full disk loses the copy, never the report.
