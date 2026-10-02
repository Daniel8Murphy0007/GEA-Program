# Well tests

> Stable periods found by the criteria file; every rejection names its rule; two approvals.

**The command**: runs at every refresh for catalogue wells with rates; `gea well-test --help` for one file. The criteria live in `criteria.json`, committed as `criteria` in the configuration store (Configuration page), never in code.

**What it writes**: `well_test_validation.*` per well: accepted tests with their means (the virtual rates), rejected candidates with the reason code - MISSING_CHANNEL, MISSING_VALUE, INSUFFICIENT_DURATION, ON_STREAM_BELOW_MIN, QUALITY_FLAGS, RATE_UNSTABLE:<channel>, PRESSURE_UNSTABLE:<channel>, TREND_EXCEEDS:<channel>, OPERATING_POINT_CHANGED:<channel> - each with the number that failed; `well_test_records/approvals.jsonl` with every decision.

**The number to check**: the criteria file's SHA-256 printed in section 2 of the report equals the committed version's hash on the Configuration page; a rejected test's reason carries the value and the limit, for example `RATE_UNSTABLE:oil 4.1 % > 3.0 %`.

**What this page will not call a measurement**: a virtual rate is a mean over an accepted window, released to allocation only after the level-1 and level-2 approvals (approver role) are on the trail. An unapproved test is a candidate.

Related: `gea help site` (the roles), `gea help alarms`.
