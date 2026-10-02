# Alarms and the month

> Setpoint, deadband, delay, priority; the event log; the monthly note that says NOT MEASURED when it cannot say MET.

**The command**: alarms run at every refresh from `alarm_definitions` (Alarms page or `gea alarms --file ... --definitions defs.json --event-log events.jsonl`). An operator acknowledges, shelves (with hours and a reason) or unshelves on the wall; `--ack`, `--ack-all`, `--shelve`, `--unshelve` do the same from the terminal, each with `--now` and `--operator`. The month: `gea workspace ... --action refresh --month 2026-09`.

**What it writes**: `alarm_events.jsonl` (append-only: ACTIVATED, ACKNOWLEDGED, CLEARED, RTN_UNACKED, SHELVED with `until`, UNSHELVED), `alarm_event_report.*` with the active and shelved lists and the KPIs (activations per 10 minutes per operator position, target <= 1 acceptable, <= 2 manageable; floods > 10 in 10 minutes; chattering >= 5 activations of one alarm in 10 minutes; standing > 24 h), and `sla_report_<month>.*`.

**The number to check**: `avg per 10 min per position` on the alarm report; on the monthly note, each SLA line reads MET, NOT MET or NOT MEASURED with the count it was measured on.

**What this page will not call a measurement**: a month with no re-fit, no fallback or no outage is NOT MEASURED for those lines, not a pass. A shelf is a suppression with a name, a reason and an expiry on the record; it is never silent and it never changes the KPIs of the alarms that did fire.

Related: `gea help notifications`, `gea help quality`.
