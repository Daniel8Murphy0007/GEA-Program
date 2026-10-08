# Start

> Install, the first run, and what the program will not claim.

**The command**: `python -m pip install "gea-program[live]"` then `gea quickstart` (a real catalogue well through every report, then the strata survey on a public hole). A site: `gea workspace --path C:\site --action init --name "Pad 3"` and `gea serve --workspace C:\site`, which opens http://127.0.0.1:8765/ in your browser (the serve window is the server, not the panel: it stays open and prints what happens on the page).

**What it writes**: `gea_quickstart/index.html` with every report beside it; a site writes under its workspace folder (`reports/`, `records/`, `jobs/`).

**The number to check**: `gea accept` ends with `[ACCEPTANCE] OK - N checks passed`; `gea doctor` ends with `nothing blocks serving`.

**What this page will not call a measurement**: nothing the program computes is a gauge measurement. It reports the data it was given, the rules it applied and the numbers those rules produced, with the rule printed beside each number. The gauge aging band is a model band with no field validation on record, and every report that uses it says so.

Related: `gea help site`, `gea help data-in`, `gea guide` (the click-by-click tester guide), `gea doctor`.
