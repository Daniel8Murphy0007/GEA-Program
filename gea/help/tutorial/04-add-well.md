# 4. Add a well from a file
Who: office
Where: #/wells
Takes: 20 minutes
> A file becomes a well with the same reports as any other: a gauge history, a drilling recorder's export, a well log, a catalogue entry.

## What the program reads
A CSV from a historian (one timestamp column, then the channels); a drilling recorder's export as the rig delivered it (date and time in two columns, then the WITS channels with -999.25 for what the recorder did not have); a LAS or DLIS well log; a SEG-Y record; an Excel workbook; a table an operator exported. The program detects the layout from the file's first lines and says what it found.

## Steps
1. Put the file somewhere the program may read. An administrator names those folders once: Administration, **File roots** - an import root it may read and an export root it may write. `C:\site\_incoming` is a good import root.
2. Open **Files**. Browse the import root; beside each file the program says what kind it is (`historian`, `edr`, `las`, `dlis`...). **Preview** shows the first rows as it reads them.
3. Press **Import** beside the file, name the well (`Well 30636033`, `SWD-1`) and confirm. The file is copied into the well's folder verbatim and hashed; a file already imported is refused by its hash.
4. Open **Wells**. The new well is listed with a state; click it for its page. The first reports are written by **Refresh** (on Home, or the well page): the drilling record for a recorder's export, the drift and quality reports for a gauge history, the strata survey for a log.
5. A well from a public dataset: **Wells**, **Add a catalogue well**, choose an entry that carries a downhole gauge series (the Volve production excerpt), name the well inside it and the gauge depth. The Catalogue tab is the map of the 52 datasets the program ships with their provenance.
6. For a drilling recorder's well, confirm its **depth events** against the daily drilling reports: on the Setup tab, the item names the well; tick it with a note saying which reports you checked. The well report prints who confirmed them.

## You know it worked when
The well page shows the file under **Sources** with its SHA-256, and the first report under **Reports**.

## The same from a terminal
`gea workspace --path C:\site --action add-file --file C:\site\_incoming\30636033.csv --name "Well 30636033"` then `gea workspace --path C:\site --action refresh`.

Help: data-in, files, edr, dlis, catalog
