# 21. Disposal water: the fluid balance, the plume, the front, calibration
Who: office
Where: #/fluid
Pages: #/plume, #/front, #/calibration
Takes: 45 minutes
> Where the injected water went: every barrel in a ledger, the pressure it raised, how far the water itself has travelled, and how much of that the measurements can pin down.

## Steps
1. Open **Fluid balance**. Declare the disposal well's role, its formation and the density (or TDS) of what it injects, and its neighbours' roles and formations, each with its source. Attach each well's injection or production report as a CSV - or declare the disposal well's injection channels and let its own stream be the record.
2. Declare the formation: its porosity, thickness, area, permeability and initial pressure, each with its source. The page adds every barrel in and out by day with its tonnes of brine and dissolved solids, fits the storage the pressure says against the storage declared, names the unaccounted barrels and draws the Hall plot.
3. For each neighbour the page gives the day the pressure front reaches it and the volume it would take for the water to - both labelled SIMULATION. **Write the report** writes the ledger CSVs.
4. Open **Pressure plume**. With the ledger's injection well placed and the formation's permeability declared, the page puts the barrels on a grid and steps the pressure through time. Pick a snapshot for the rise as contours, the area of review over the threshold, and each mapped fault's rise and slip tendency. Declare a fault layer sealing (with its source) and the plume piles up against it. The 3D tab draws the same contours at the declared top.
5. Open **Fluid front** - the same ledger, for the water itself. With the injected water's TDS or density and the formation water's declared on the Fluid balance tab, the page shows how far the injected water fills the thickness, the tongue it runs along the base or the top, the day it reaches each producer and its share of that producer's stream. Where a producer's report has a TDS column, the measured value stands beside the predicted one - that is the check.
6. Open **Calibration**. When a neighbour has a gauge (declared on the Fluid balance tab with the role observation, its record attached), the injector's report has a bottomhole pressure, or a producer's report has a TDS column, press **Run the calibration**: the plume and the front are run over a range for every uncertain number and ranked against those measurements.
7. Read the result: which runs are kept, each forecast as a range - what the declarations alone allow beside what the measurements leave - and **What to measure next**, ranked by how much each would narrow the area of review and the front. With nothing measured yet, it still shows the spread the declarations allow and what to measure first.

## What these pages are
The ledger is arithmetic on the reports you attach. The plume, the front and the calibration are each a SIMULATION and say so on every figure: they are what the declared formation would do, to be set against a gauge wherever there is one.

## You know it worked when
The Fluid balance ledger's total barrels agree with the injection reports you attached, and the Calibration page names what to measure next.

## The same from a terminal
`gea fluid --workspace C:\site`, `gea plume --workspace C:\site`, `gea front --workspace C:\site`, `gea calibrate --workspace C:\site`.

Help: fluid, plume, front, calibration
