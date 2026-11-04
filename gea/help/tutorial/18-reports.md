# 18. Reports, report history and the evidence pack
Who: office
Where: #/reports
Takes: 20 minutes
> One printable document per well and one for the pad, every version kept, and the folder a reviewer gets.

## Steps
1. Open **Reports**. **Write the pad report**, or the button per well: the drilling record and its depth events with who checked them, the chart of depth against days, the declared location and what the mapped structure says there, the well path with the mapped tops, the declared cost against the market. Each number says where it came from, and the report lists what it cannot say yet and what would supply it.
2. Every report is written as HTML, Markdown and JSON to a fixed name, so the latest is always where a reader expects it; the client reports carry a printable PDF.
3. **Report history**: every time a client report is written, a copy of each file goes into a dated folder under `reports\history\` with its SHA-256. The card lists every report with its versions; a version that was handed over can always be opened again as it was.
4. **save to...** beside any report copies it to the export root (Administration, File roots). **Build the evidence pack there** zips every report with a manifest and a SHA-256 list - the folder a reviewer gets.
5. **Update every report from its source** (Audit / Update) runs the dashboard for every well and then every station as one job - after a source file changed, or after a new version of the program. The Audit / Update tab's data card says per report `current` or `stale`.
6. The report's **source statement** on its last page is the part a reviewer reads first: the files by hash, the declarations by source, the version of the program.

## You know it worked when
The well report opens as a PDF with the depth events confirmed by a named person, and Report history shows two versions after you write it twice.

Help: well-report, report-history, files, audit-update
