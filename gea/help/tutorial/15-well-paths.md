# 15. Where the hole is going: collision, the hard line, the slide sheet, plan against actual
Who: office
Where: #/collision
Pages: #/hardline, #/slides, #/plan
Takes: 35 minutes
> Four pages read a well's survey: how near it runs to the other wells, how near to a line it must not cross, what each slide did to it, and how far it is from its plan.

## Before you start
Each well needs its surface position and reference elevation declared ({lesson:site}) and its directional survey attached ({lesson:where-the-well-is}). A well without a survey is listed on each page as not placed, and left out; nothing is drawn from a guess.

## Steps
1. Open **Collision**. Every well is scanned against every other by the ISCWSA separation factor, with each well's uncertainty from its survey program and the Earth's field at the well. The table gives each pair's closest approach and where; pick a pair for the travelling cylinder. A planned well is scanned along its plan.
2. Open **Hard line**. Under **Declare a boundary**, name it, say whether the well must stay inside it or keep out of it, give the setback and the warning distance in feet, and its source - the lease, the unit order, the plat. The line itself is a polygon of a layer the site already has on the Map tab, or corners typed as latitude and longitude with their datum.
3. Read each well's card: the nearest the hole comes to the line and at what depth, beside the survey's own uncertainty across the line there; where the bit is now, and how much more hole it would take to reach the line on its present direction. A well still drilling raises an alert inside the warning distance, at the setback, and across the line.
4. Open **Slide sheet** and pick a well. Every slide of its drilling record is a row - from, to, the footage, the rate, the toolface where the record carries one - with the rotated stretches between. With the survey attached, each course between two stations gives the dogleg per 100 ft slid; the rotated courses give what the assembly builds or drops and how it walks when nobody steers it.
5. Open **Plan vs actual**. Under **The plan**, **Attach** the directional plan with its azimuth reference and its source. At each survey station the page gives how far the well is from the plan - high or low, right or left - against the survey's own uncertainty, the bit projected ahead, and the arc back to the plan.
6. Each page's **Write the report** writes what the page shows, with the declarations and their sources on the last page.

## On the practice pad
On the public site ({lesson:practice-pad}) the boundary is declared for you from the project's published extent, with a setback of 0 ft - none is published - and a warning distance of 330 ft. **Hard line**: 78B-32 is the well nearest it, 85 ft from the line at 8,104 ft. **Collision**: 16B(78)-32 runs about 297 ft from 16A(78)-32 at its toe. **Slide sheet**: open 16A(78)-32 - 38 slides, 7.3% of its footage, and five of them between 5,892 and 6,797 ft built its curve.

## You know it worked when
The Hard line card of each placed well shows a distance in feet with the depth it was measured at, and the Slide sheet's footage, slid and rotated together, adds up to the well's drilled footage.

## The same from a terminal
`gea collision --workspace C:\site`, `gea hardline --workspace C:\site`, `gea slide-sheet --workspace C:\site`, `gea plan --workspace C:\site`.

Help: collision, hardline, slide-sheet, plan
