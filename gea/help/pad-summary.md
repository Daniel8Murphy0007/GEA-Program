# The daily pad summary

> One page each morning, from the program's own records: what changed since the last summary, what is due, and what is still missing - written as a report (with its printable PDF), kept in report history, and sent through the notification rules.

**The command**: the service writes it once a day at the time set on the Reports tab's **Daily pad summary** card (11:00 UTC by default, 06:00 in US Central daylight time; the administrator role sets it), and hands it to the notification rules as the event `pad.summary` - add a rule for that event on the Notifications page to have it emailed. **Write it now** and **Write and send it now** do it on demand (the operator role). From a terminal: `gea pad-summary --workspace C:\site` (add `--send` to send it); `--at 11:00`, `--on`, `--off` set the daily time.

**What it says**: what changed - the actions in the audit log since the last summary, grouped by action with who did them, the alerts raised, the report versions written and the earthquakes the live watch saw for the first time; and yesterday's rig time for each well drilling (drilling hours, connections, the time lost against the targets and its cost at the contract's rate); what is due - each alert condition still open, each well's invoice check, drill-string components near or past their service interval, operations under way or starting in the next seven days, the earthquake watch's state, the last backup and the last signed dossier; what is still missing - the setup checklist's required and recommended items still to do.

**What it writes**: `reports\daily\pad_summary.pdf` with `.html`, `.md` and `.json` beside it, a version of each in `reports\history\daily\pad_summary\`, the audit line `report.pad_summary`, and `records\pad_summary.json` (the settings and when it was last written). The email carries the summary's text and names the PDF.

**The number to check**: the period on the page (from the last summary to now) against the audit log's lines for the same hours; the count of alerts against the Alarms tab's Program alerts.

**What this page will not call a measurement**: anything the records do not hold - a summary lists what the program recorded, and an action taken outside it is not on the page; the time - it is this machine's clock.
