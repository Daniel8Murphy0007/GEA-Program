# 14. Completion, frac hits, the fluid balance, decline curves, economics and the invoice check
Who: office
Where: #/completion
Takes: 30 minutes
> The pages after drilling: the ESP penetrator, what each frac stage did to the wells around it, where the disposal water went, the decline forecast, the cost against the market, and the contractor's invoice day by day.

## Steps
1. **Completion**: **Record the installation** of the ESP wellhead penetrator - the penetrator (a catalogue entry or a fleet tool with its datasheet), the date, the installer, the motor type, the gauge distance, the insulation readings, the void test - and **Declare the drive** (the drive's output limits, the motor nameplate, the hazardous-area classification, the ambient range). The page checks the well against the penetrator's ratings one line at a time; a line outside raises a `penetrator.outside` alert.
2. **Frac hits**: attach each treatment well's stage table (CSV or Excel, with its source and the UTC offset of its clock) and name each monitored well's pressure channel. The grid shows every stage against every monitored well - no response, poroelastic, mixed, or a frac hit - and a cell picked shows the pressure's departure around the stage.
3. **Fluid balance**: declare the disposal well's role, formation and the density (or TDS) of what it injects, and its neighbours' roles and formations, each with its source; attach each well's injection or production report as a CSV (or declare the disposal well's injection channels and let its own stream be the record); declare the formation's porosity, thickness, area, permeability and initial pressure. The page adds every barrel in and out by day with its tonnes of brine and dissolved solids, fits the storage the pressure says against the storage declared, names the unaccounted barrels, draws the Hall plot, and for each neighbour gives the day the pressure front reaches it and the volume it would take for the water to - both labelled SIMULATION. **Write the report** writes the ledger CSVs.
4. **Decline curves**: attach a well's monthly production and declare the terms (terminal decline, price basis, NRI, severance, opex, discount rate). The page fits Arps's curves, forecasts to the economic limit and values it: the EUR and the PV.
5. **Economics**: declare the day rates with what they pay for and their period, the one-off items (a bit, a cement job) and the AFE. Each well's days, footage and hours on bottom come from its drilling record; **Refresh the EIA prices** fetches the WTI and Henry Hub spot series; **Import a series** keeps any CSV of dates and values with its source.
6. **Invoice check**: declare the contract's terms once (the hour the report day starts, the day rates, the repair allowance), attach the daily drilling reports and the invoice as CSV, and **Write the report**: each day AGREES, is in QUESTION, or DIFFERS, with the dollars each difference puts in question.

## You know it worked when
The Economics table shows cost per foot beside the WTI price while the well was drilled, and the invoice check names the days that differ.

Help: penetrator, frac-hits, fluid, decline, economics, invoice-check
