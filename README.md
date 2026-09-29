# GEA-Program

Downhole gauge monitoring for production operations: the program takes a well's
historian data and returns the reports a client engineer reads, in the outline
and language of a production-operations scope of work.

Every number in every report is recomputed from the source data at generation
time. Reports are gated for vocabulary: the program's internal register never
reaches a client document. Nothing unmeasured is ever reported as met.

## What it does

- **Canonical measurement record** (`gea/sample_record.py`): one record shape for
  every sample - tag, UTC timestamp, value, unit, quality flag, the rule and limit
  that fired, source layer, ingest timestamp. Quality rules: RANGE / ROC /
  FLATLINE / SPIKE / STALE / GAP; ranges from the gauge datasheet, cited.
- **Gauge drift and reconciliation** (`reconciler.py`, `drift_monitor.py`): the
  live pressure series against the well baseline; classification with the
  numbers beside it; scheduled evaluation log; CURRENT / STALE; re-fit change
  log with before/after coefficients; approve -> apply -> re-evaluate; SLA clocks;
  annual re-fit cap.
- **Accuracy statement** (`accuracy_statement.py`): MAPE with a seeded bootstrap
  90 % CI; the band read at the conservative end of the interval.
- **Well-test validation** (`well_test_validation.py`): stable-period detection
  with the client-agreed criteria in a file (hashed on every report), reason
  codes naming the failing value, virtual rates, two-level approval trail.
- **Alarm management** (`alarm_engine.py`): setpoint / deadband / on-delay state
  machine, event log, ISA-18.2-style KPIs printed against their targets.
- **Model cards** (`model_card.py`): one card per model - inputs with sources,
  settings with basis, calibration data with provenance, recomputed evaluation,
  limitations, re-fit history, component hashes.
- **Store-and-forward** (`store_forward.py`): 72 h edge buffer, chronological
  rate-controlled replay, duplicate suppression, per-record latency.
- **Configuration versioning, SBOM, monthly SLA, FAT/SAT**
  (`config_versioning.py`, `sbom.py`, `sla_report.py`, `fat_sat.py`).
- **Dashboard** (`dashboard.py`): tiles, well ranking, alarm wall, drill-down to
  every report; light and dark; status is always an icon with a label.
- **Survey track** (`earth_model.py`, `strata_join.py`, `inverse_engine.py`,
  `blind_harness.py`, `rock_inventory.py`, `forward_model.py`, ...): strata
  property estimation over a public co-located library, blind-scored on every run.

## Quick start

```
pip install -e .
gea quickstart                 # a real catalogue well -> its reports -> the dashboard; the KTB strata survey
gea survey mywell.las          # a LAS file in, one strata report out
gea dashboard --catalog-well volve_f12_f14_production_excerpt:15/9-F-12:10000 --td 10500 --out dashboard
gea client-report --report accuracy --out client_report
gea model-cards --out model_cards
gea sbom --out sbom
gea accept                     # the product gate (157 checks)
gea guide                      # the click-by-click tester guide (docs/TESTER_GUIDE.md)
gea gui                        # the desktop window (pip install "gea-program[desktop]")
```

`gea` is the front door (`gea/cli.py`); every other subcommand passes through to
`python -m gea`. PATH-proof form: `python -m gea.cli ...`. Open `dashboard/index.html`.

## Layout

```
gea/            the package: engines, record layer, reports, monitor, dashboard, cli, shell, acceptance suite
gea/catalog/    52 public archive entries, each with a provenance file
docs/           TESTER_GUIDE.md, REQUIREMENTS_MATRIX.md (the scope-of-work mirror that shaped the reports),
                commercial/ (pilot proposal, bench readiness, renders), HISTORY.md (development history)
tools/          import_from_star_magic.py (the importer), register_audit.py, native/ (cli.py, shell.py sources)
tests/          pytest wrapper around the acceptance suite and the no-corpus check
```

## Where the code came from

The package was imported from the `uqff_downhole_simulator` package of the
Star-Magic-Program repository by `tools/import_from_star_magic.py`, which renames
the modules, removes every dependency on that repository's physics corpus, puts
the gravity kernel on standard constants (CODATA 2018 G, standard gravity, IUGG
mean radius), keeps the rock inventory's seventeen published density anchors and
Vp ranges without their decompositions, and drops the two modules that existed
only to compose numbers from that corpus. `gea/IMPORT_RECORD.md` lists the
source commit and every file's hash. `tools/register_audit.py` lists what
remains of the source program's vocabulary in comments and docstrings.

Two statements the product carries on its own model cards: the gauge aging
envelope's lower bound is an engineering model with no field validation on
record, and five of the fourteen back-tested strata quantities are NOT
ACCEPTABLE at the 95 % target. Both are printed, never claimed otherwise.

## Shipping

`.\ship.ps1` (PowerShell) gates, commits, tags and pushes in one screen: version in
`pyproject.toml` must equal `gea.__version__` (`-Bump x.y.z` sets both), the tag must
not exist anywhere, every version in `SHIP_LOG.md` must have its tag, `python -m gea
accept` must be green, `SHIP_MESSAGE.txt` must start with the tag; then commit, tag,
push, and the remote tag must be seen before SHIPPED is printed. `-DryRun` runs every
check and changes nothing; `-NoPush` stops after the local tag.

## Publishing to PyPI

The name `gea-program` is free on PyPI as of 2026-09-29; a PyPI project is created by
its first upload, there is nothing to "start" beforehand except the trusted publisher.
One-time setup, before the first tag is pushed: sign in to PyPI -> your account ->
Publishing -> "Add a new pending publisher": project name `gea-program`, owner
`Daniel8Murphy0007`, repository `GEA-Program`, workflow `release-to-pypi.yml`,
environment `pypi`. Then in GitHub -> Settings -> Environments create `pypi`. From then
on `.\ship.ps1` pushes the tag and `.github/workflows/release-to-pypi.yml` gates, builds,
verifies the wheel and publishes; the package page is
https://pypi.org/project/gea-program/ after the first successful run.

## Licence

Mozilla Public License 2.0 (MPL-2.0); see `LICENSE`. Every source file carries the MPL-2.0
header (Exhibit A). Copyright (c) 2026 Daniel T. Murphy.
