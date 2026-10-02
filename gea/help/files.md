# Files

> The folders the dashboard may read and write, and the evidence pack.

**The command**: Administration -> File roots (an import root the dashboard may read, an export root it may write); the Files page browses a root with detection by content and imports a file into a well; "save to..." beside any report copies it to an export root; "Build the evidence pack there" zips every report with a manifest and a SHA-256 list. `gea files --workspace C:\site --action list|detect|preview|import|export|pack|watch`.

**What it writes**: `records/imported.jsonl` (hash, root, path, well) so the same content is never imported twice; the pack as `evidence_<site>_<stamp>.zip` with `MANIFEST.json` and `SHA256SUMS.txt`; a watch folder imports new files on a schedule.

**The number to check**: a file's detected kind on the Files page before importing it (historian_csv, las, segy, operator_table, pangaea, json, xls, csv, unknown); `sha256sum -c SHA256SUMS.txt` inside an unpacked pack.

**What this page will not call a measurement**: a `csv` whose first column is not a timestamp is declined with the reason rather than read as a gauge stream; an `xls` without the `xls` extra is declined, not guessed at. No path reaches outside a root.

Related: `gea help data-in`, `gea help site`.
