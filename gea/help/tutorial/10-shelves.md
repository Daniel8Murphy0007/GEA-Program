# 10. The shelves: the catalogue, the rocks, the strata survey, tools and ports
Who: office
Where: #/catalog
Pages: #/rocks, #/survey, #/tools, #/files
Takes: 25 minutes
> What the program ships with and reads from: the public datasets with their provenance, the rock inventory a log is ranked against, the survey that does the ranking, and every class of downhole tool with what the program does with it.

## Steps
1. Open **Catalogue**. Every public dataset the program ships is a row: what it is, where it was drilled, its licence, and which page of the program reads it - or that none does. Nothing here is your client's data; it is what the worked examples and the checks are made from.
2. Open **Rocks**. **The table** lists the 17 rocks, minerals and the fluid with each one's published density and velocity range; **Density ranges** draws them so the overlaps show. Under **Look up a measurement**, type a bulk density (2.65) and press **Which entries hold it**: several do - a density alone never names a rock, and the page says so.
3. Open **Survey**. Press **Run the bundled demo (KTB)**: a real density log from the German deep hole goes through the strata survey as a job. For a log of your own, choose the LAS file from an import root and press **Run the survey**. **Latest surveys** opens each report: the depth column, the property estimates with their spread, the ranked candidates, and the depths where the data supports no estimate.
4. Open **Tools & ports**. Point at a tool class for its one-line description; click it for the page on it - what it is, where it runs, what it shows in the data, what to watch. An operator adds the site's own tool there with its maker, model, serial and datasheet; the Drill string and Completion tabs then offer it by name.
5. On the same tab, **the ports**: every way data comes in live, each with what the site must supply before it runs and the simulator to rehearse against.
6. **Files** is the other shelf - yours: the import roots the program may read and the export roots it may write, each file with the kind the program detects from its content ({lesson:add-well}).

## You know it worked when
The Rocks lookup for 2.65 g/cc lists more than one entry, and the demo survey's report is under **Latest surveys** with gneiss ranked first.

## The same from a terminal
`gea catalog --list`, `gea rocks --density 2.65`, `gea survey --catalog ktb_hb_complog_6020_excerpt`, `gea tools --action classes`.

Help: catalog, rocks, tools, files
