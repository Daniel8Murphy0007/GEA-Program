# 11. The daily pad summary and the month-end packet
Who: office
Where: #/reports
Takes: 15 minutes
> One page each morning from the program's own records, and the whole month assembled once.

## Steps
1. On **Reports**, the **Daily pad summary** card. The service writes it once a day at the time set there (11:00 UTC by default; an administrator sets it) and hands it to the notification rules as the event `pad.summary` - add a rule for that event (lesson 9) to have it emailed. **Write it now** writes one on demand; **Write and send it now** sends it too.
2. Read it: what changed since the last summary, what is due, and what is still missing, with its printable PDF; every summary is kept in report history.
3. The **Month-end packet**: choose the period (`2026-09`, or *previous*, the last closed month) and press **Assemble** - a job. It refreshes every report for the period and collects every report for every well, site, station and track into one dated folder and one archive, with an index that says item by item what is there and what is not, and a manifest of every file's hash.
4. The result is under `reports\month_end\2026-09\` with `INDEX.html` and `MANIFEST.json`, and the zip beside it; **Report history** lists it. An administrator presses **Schedule monthly** once, on the day and hour given, for the previous month.
5. Hand the client the zip, or sign a dossier of the month (lesson 19).

## You know it worked when
The index of the packet lists every well and station with a report beside each, and the few it cannot say, named.

Help: pad-summary, month-end, report-history
