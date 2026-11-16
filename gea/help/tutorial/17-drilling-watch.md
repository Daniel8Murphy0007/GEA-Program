# 17. Trouble while drilling: drilling alerts, kick and loss
Who: office
Where: #/dysfunction
Pages: #/kick
Takes: 30 minutes
> Rules replayed over every drilling record, and run on a record still being written: a washout, a motor stall, a hole getting tight, a flowback above its fingerprint, a gain or a loss in the pits.

## Steps
1. Open **Drilling alerts**. Three rules are replayed over each well's record: the off-bottom pressure falling at a steady flow (a washout), the differential pressure spiking with the bit stopped (a motor stall), the pick-up weight leaving its own trend (the hole getting tight).
2. Read a well's card: the readings connection by connection, what each rule would have said and when, and whether a trip out of the hole followed. A rule that would have spoken hours before a trip is the reason to keep it; one that speaks at every connection is the reason to change its number.
3. **The rules' numbers** at the foot change the thresholds, each with its source. On a record still being written an event raises an alert, and the notification rules carry it ({lesson:alarms}).
4. Open **Kick & loss**. At each pumps-off the pits rise by the rig's own few barrels as the lines drain; the page sets each flowback against the fingerprint of the eight before it and marks one that runs above it.
5. While circulating, a ten-minute change in the pits that leaves its own trend is named by what the return flow did: up with the pits is kick-like, down with them is losses, flat is mud added, moved or dumped at surface.
6. **The places to look** lists them; **show** draws the pit volume, the pump output and the return flow around one. Set each against the daily report for that hour: an event is a place to look, not a kick called.

## On the practice pad
On the public site ({lesson:practice-pad}) the rules run over five real records. **Kick & loss** judges three of them - 56-32, 78B-32 and 16B(78)-32 - and lists 58-32 and 16A(78)-32 as not judged: their records carry no active pit volume, and the page says so rather than guess. On 78B-32, look at the flowback at 8,530 ft: 58.8 bbl back five minutes after the pumps stopped, against 17.5 for the connections before it.

## You know it worked when
Each well's card on Drilling alerts shows its connections counted and each rule with a verdict or the channel it lacked, and Kick & loss lists each well's pumps-off flowbacks beside their fingerprint.

## The same from a terminal
`gea dysfunction --workspace C:\site`, `gea kick --workspace C:\site`.

Help: dysfunction, kick
