# 17. Trouble while drilling: drilling alerts, kick and loss, the trip sheet
Who: office
Where: #/dysfunction
Pages: #/kick, #/trips
Takes: 40 minutes
> Rules replayed over every drilling record, and run on a record still being written: a washout, a motor stall, a hole getting tight, a flowback above its fingerprint, a gain or a loss in the pits - and each trip's mud against the pipe that was moved.

## Steps
1. Open **Drilling alerts**. Three rules are replayed over each well's record: the off-bottom pressure falling at a steady flow (a washout), the differential pressure spiking with the bit stopped (a motor stall), the pick-up weight leaving its own trend (the hole getting tight).
2. Read a well's card: the readings connection by connection, what each rule would have said and when, and whether a trip out of the hole followed. A rule that would have spoken hours before a trip is the reason to keep it; one that speaks at every connection is the reason to change its number.
3. **The rules' numbers** at the foot change the thresholds, each with its source. On a record still being written an event raises an alert, and the notification rules carry it ({lesson:alarms}).
4. Open **Kick & loss**. At each pumps-off the pits rise by the rig's own few barrels as the lines drain; the page sets each flowback against the fingerprint of the eight before it and marks one that runs above it.
5. While circulating, a ten-minute change in the pits that leaves its own trend is named by what the return flow did: up with the pits is kick-like, down with them is losses, flat is mud added, moved or dumped at surface.
6. **The places to look** lists them; **show** draws the pit volume, the pump output and the return flow around one. Set each against the daily report for that hour: an event is a place to look, not a kick called.
7. Open **Trip sheet** and pick a well. Every trip of 1,000 ft or more is a row: the mud the hole took pulling out, or gave running in, read from the trip tank where the record has one that moves and otherwise from the pits, beside what the pipe accounts for. Under **The pipe**, enter its outside and inside diameters, its weight with tool joints, how it is pulled and run, and the source - the pipe tally - and press **Declare the pipe**; without one each trip is set against the well's own other trips.
8. Read **The sheet**: a *short fill* pulling out, or a hole that *gave more* running in, is more mud at surface than the pipe accounts for; *took more* and *short return* are losses; *not read* is a trip the record carries no moving volume for, and is not a short fill. **show** draws a trip and gives it 500 ft at a time.

## On the practice pad
On the public site ({lesson:practice-pad}) the rules run over five real records. **Kick & loss** judges all five: on 16A(78)-32, 935 pumps-offs with 13 flowbacks above their fingerprint; on 58-32, 562 with one. (A public site made before this version judged three: press **Make the site** once more and the other two records are written out again with their pit volume and return flow.) On 78B-32, look at the flowback at 8,530 ft: 58.8 bbl back five minutes after the pumps stopped, against 17.5 for the connections before it. **Trip sheet** reads every trip from the pits there - no well's record carries a trip tank that moves - and sets each well against itself: on 16B(78)-32, 0.85 bbl per 100 ft pulling out and 1.78 running in at the median.

## You know it worked when
Each well's card on Drilling alerts shows its connections counted and each rule with a verdict or the channel it lacked, Kick & loss lists each well's pumps-off flowbacks beside their fingerprint, and the Trip sheet has a row for every trip with where it was read from.

## The same from a terminal
`gea dysfunction --workspace C:\site`, `gea kick --workspace C:\site`, `gea trip-sheet --workspace C:\site`.

Help: dysfunction, kick, trip-sheet
