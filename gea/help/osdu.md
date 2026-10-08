# The OSDU-shaped export

> The site as the Manifest a large operator's OSDU data platform loads, built to the published well-known schemas, with every file beside it.

**The command**: `gea workspace --path C:\site --action osdu-export --site <id> --partition <partition> --acl-owner <group> --acl-viewer <group> --legal-tag <tag> [--country US] [--operator-org <partition>:master-data--Organisation:<id>:] [--out <folder>]`. The partition, the ACL groups and the legal tag are the platform's and yours; without them the manifest is still written and marked NOT LOADABLE with the missing names. `gea osdu` runs the self-test on a labelled site.

**What it writes**: `reports/sites/<site>/osdu/manifest.json` - `osdu:wks:Manifest:1.0.0` with MasterData (each well as `master-data--Well:1.0.0` and `master-data--Wellbore:1.0.0`, its API and UIC numbers as name aliases, the gauge station as a vertical measurement), Data (the site as the WorkProduct; each well's record as `work-product-component--WellLog:1.1.0` in the time domain with ZeroTime, the sampling interval and a curve per channel; each seismic station as a generic component with its position) and Datasets (`dataset--File.Generic:1.0.0` for every file, with its size and SHA-256); `files/` beside it with the files themselves; `export_summary.json` with the counts, the gaps and whether it is loadable. Every export is in `records/audit.jsonl`.

**The number to check**: `[LOADABLE]` or `[NOT LOADABLE]` on the first line, and `structure: ok (<n> record(s))` - the platform loader's first checks made here first: every record has its id, kind, ACL, legal block and data; every dataset a component names is in the manifest; every wellbore's well is in MasterData; the work product lists every component.

**What this page will not call a measurement**: a WGS 84 position for a point whose datum is unknown - every position goes out twice, as given on its own datum with that datum's EPSG code, and on WGS 84 with the operation between them written into AppliedOperations; a point on an unknown datum gets no WGS 84 coordinates at all, because a platform that indexed it would place the well tens of metres wrong. A loadable manifest without the operator's partition, ACL and legal tag - they are never filled in. A seismic trace component - the seismic trace schema was not verified against a published example when this export was written, and a kind not read here is not a kind written here; stations go out as generic components with their records as datasets.

Related: `gea help sites`, `gea help sra`, `gea help data-in`.
