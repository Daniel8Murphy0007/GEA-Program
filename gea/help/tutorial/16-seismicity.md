# 16. Operations & seismicity, the earthquake watch and the SRA packet
Who: office
Where: #/opseis
Takes: 30 minutes
> Each well's operations against the earthquake catalogue around it, the watch that runs while a well operates, and what an operator inside a Seismicity Response Area puts in front of the Commission.

## Steps
1. Open **Operations & seismicity**. Choose the scene - your wells, or the worked example (the Mahoning Valley, Ohio, from the public record) - and the **protocol** (British Columbia, Alberta, Oklahoma, Ohio, Texas), the association radius, the days after an operation that still count, the completeness magnitude.
2. Each well's operations: under the well, the kind (drilling, completion, disposal, injection), its start and end and the source, then **Declare** (or the declarations sheet's `[operations]` table, lesson 7).
3. **Catalogues**: the program cannot reach a catalogue service itself, so the tab gives you the ComCat query to open in a browser; save the export and **Attach** it with the window, the minimum magnitude and the circle it was queried for.
4. Read the time line: the operations as bands, every event within the screening distance as a circle by its distance and magnitude, the rig's state at each event during a drilling record, the protocol level each event reaches, and whether the earthquake rate differs while the well works.
5. **Live earthquake watch**: on, the interval (5 minutes), only while an operation is under way or always; **Poll now** tries the connection. The program reads the USGS catalogue from this machine and raises an alert when an event near a well reaches the protocol's level; the watch going quiet is an alert of its own.
6. The SRA packet is written from a terminal, three commands. Declare each disposal well once: `gea workspace --path C:\site --action set-well --well SWD-1 --lat 31.4215 --lon -103.5038 --datum NAD27 --api 42-329-12345 --uic 123456 --depth-tier deep --formation "completed below the base of the Wolfcamp" --channel-pressure P_surf_psi --channel-rate Q_bpm --rate-unit bbl/min`. Define the area on the site once: `gea workspace --path C:\site --action sra-define --site retama-block --name "Gardendale SRA" --lat 31.95 --lon -102.25 --datum WGS84 --radius-km 9.08 --plan-date 2022-12-16`. Then every month, with the regulator's catalogue export: `gea workspace --path C:\site --action sra-report --site retama-block --catalog C:\site\_incoming\texnet_export.csv` - or let the month-end packet (lesson 11) assemble it. The **Sites** tab links the packet with its status; sections 2b to 2e carry the mapped structure, the interval, the catalogue against the faults, and the slip statement for every well inside the area.

## You know it worked when
The alert list shows the watch's last poll a few minutes ago, and the SRA packet is under the site's reports with the month's events in it.

Help: opseis, alerts, sra
