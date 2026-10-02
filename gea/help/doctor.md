# Which code is running

> When the page looks old or a button says "no such route", this is the question.

**The command**: `gea doctor` and `gea doctor --workspace C:\site --port 8765`. It prints the Python, the package path and version, pip's record against the running code (an editable checkout or an installed release, and whether they are the same thing), any second copy on the path, the page, each dependency, the launcher, the newest release on PyPI; with a workspace, the manifest, the folders, the accounts, the patches, a free port and write access. Every WARN or BLOCK line carries its fix. `gea serve` runs the same checks and does not start on a BLOCK.

**What it writes**: nothing; `--json` prints the findings as JSON.

**The number to check**: the last line: `nothing blocks serving`, or `N blocking finding(s)`.

**What this page will not call a measurement**: a WARN about PyPI having a newer release is a comparison, not an instruction; an offline site sees `not compared` and that is fine.

Related: `gea help start`, `gea help site`.
