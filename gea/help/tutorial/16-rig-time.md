# 16. What the rig did: rig time, the benchmark, the roadmap, the daily report
Who: office
Where: #/rigtime
Pages: #/benchmark, #/roadmap, #/daily
Takes: 40 minutes
> Three pages read the drilling record for time: where each well's hours went, how the wells of the pad compare, and what the fastest stands were drilled with.

## How every engineering page works
The same shape each time: pick the well; the tiles at the top; the charts; a **declare** form (the operator role) for what the record cannot know - with a **source** box for where the figure came from; **Write the report**. Nothing here needs a declaration to start: these three pages read the recorder alone.

## Steps
1. Open **Rig time**. The page gives the hours of each well by rig state - what the rig did each second, read from the hook load, the block, the bit, the pumps and the rotary. Connections are timed weight to weight, and split into their three parts; trips are timed by their speed.
2. The time lost is measured against the targets you declare (a connection time, a trip speed in and out, with their source - the drilling program) or, with none declared, against the pad's own best quarter. It is hours the record shows, not a cause. **Write the pad's report** keeps it.
3. Open **Benchmark**. Each well is measured the same way from its own record - feet per day from its first stand to its last, the rate on bottom, the median connection, the trip speeds - and ranked on each.
4. Read **Against the pad's best**: each well set against the well it would have been with every stretch drilled at the rate of whichever well was fastest through it. The gap is hours the pad has already shown can be saved, not a forecast.
5. Open **Roadmap**. With two or more wells' records attached, every well is cut into stands of 100 ft and each stand put in its stretch of hole - 1,000 ft of TVD down to the heel, 1,000 ft of lateral beyond it. For each stretch the table gives the weight on bit, rotary speed, flow and differential pressure the fastest quarter of the stands were drilled with, and which well was fastest there.
6. Read **The check** before using the table: each well is set against the roadmap made from the others. Drill the next well from the table only as far as the check supports it.
7. Open **Daily report** (under Watch & report). It writes the rig's morning sheet from the same record: pick the well and a day in **The days**. The bar is the day's 24 hours by operation; under it the time log, to the quarter hour as a daily report is kept, each line with its depths; then the exact hours by rig state, the readings while drilling, the trips and the places in the day's record to look at. Type the crew's words in **Remarks** - what the record cannot say - and press **Keep the remarks**; press **Write this day's sheet**, then **Open the sheet to print**.

## On the practice pad
On the public site ({lesson:practice-pad}) the Benchmark ranks five real wells drilled in the same granite between 2017 and 2023: 56-32 made 487 ft a day from its first stand to its last, and 58-32, the first of them, 156. Against the pad's best the five spent 823 of their 1,270 hours on bottom beyond it. The Roadmap's check there is a hard test of the idea: wells years apart, in different hole sizes.

## You know it worked when
The Rig time tiles for a well add up to the length of its record, and the Benchmark lists every well with a drilling record on the same rows.

## The same from a terminal
`gea rig-time --workspace C:\site`, `gea benchmark --workspace C:\site`, `gea roadmap --workspace C:\site`, `gea daily-report --workspace C:\site`.

Help: rig-time, benchmark, roadmap, daily-report
