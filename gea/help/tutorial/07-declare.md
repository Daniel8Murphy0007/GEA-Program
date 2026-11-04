# 7. Declarations in one sheet
Who: office
Where: #/setup
Takes: 20 minutes
> Everything the operator declares about a pad in one spreadsheet, checked against the workspace before anything is written.

## Steps
1. On **Setup**, press **Download the sheet**. It is an Excel workbook with one table per subject - who each well is and where, the reference elevation, the day rates and the AFE, the fleet, the drill-string runs, the ESP penetrator and drive, the operation windows and catalogues for Operations & Seismicity, the survey program and plans, the casing program, the torque-and-drag string, the hydraulics, frac stage tables, production and decline terms - already filled with what is declared, with a line above each table saying what it takes.
2. Fill in the blanks in Excel. A row names the well it belongs to; a file a row refers to (a survey, a plan, a stage table, a production file, a catalogue export) is named by its file beside the sheet.
3. Save it, and on **Setup** press **Check the sheet** and choose it. The program plans every row: **NEW** (nothing declared yet), **CHANGE** (it replaces what is declared - the old value is shown) or **SAME**. One error anywhere and nothing is written; the error names the row and the column.
4. With no error, press **Write** (the operator role). Every write is an audit line.
5. Import the same sheet again whenever something changes - rows already declared read SAME and are left alone.

## You know it worked when
The check lists only SAME rows the second time you check the same sheet.

## The same from a terminal
`gea declare --workspace C:\site --template C:\site\pad3.xlsx` writes the sheet; `gea declare --workspace C:\site --file C:\site\pad3.xlsx` checks it; add `--apply` to write.

Help: declare
