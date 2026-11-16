# 4. A real pad to practise on
Who: office
Where: #/publicpad
Takes: 40 minutes
> Before your own wells are in: five real wells from a published project, made into a site of their own, so every engineering page has something true on it to learn from.

## What it is
The Utah FORGE project, funded by the U.S. Department of Energy, publishes the rig recorders of five wells, their surveys, sixteen frac stages and its own boundary under CC BY 4.0. The program does not carry those files; you download them once, and it makes a site from them that is separate from yours - nothing of it is mixed into your wells or your reports. The lessons on drilling and completion each end with what to look at on this pad.

## Steps
1. Open **Public data** (an administrator). **The files** lists, for each well, the file to download, its size and the page of the Geothermal Data Repository it is on. Follow the links in a browser and save the files into one folder - a `forge` folder in your Downloads is where the program looks first. Two wells make a pad; all five are about 2.8 GB.
2. **The surveys** and **The stimulation records** list the smaller files - each well's published survey, the wellhead locations, the project's extent, the stages' pumping records. Put them in the same folder. They are optional: without the surveys the wells are drilled but not placed.
3. Type the folder the files are in and press **Check the folder**: the tab counts the recorder files, the survey files and the stimulation files it finds there, and each table marks its files present or missing.
4. Name the folder for the new site (a `forge-site` folder beside your own is offered) and press **Make the site**. It takes some minutes: every file is hashed, each well is attached with its drilling record, the surveys place the wells, and the boundary is declared. The progress line says which step it is on.
5. Press **Open the site**. It opens in a window of its own, titled with the public site's name, with its own tabs; sign in there with the same name and password. Your site stays in the first window. Later it is under Administration, **Workspaces**, **Open in its own window**.
6. In the public site, open **Wells**: five wells, each with its drilling record. Open **3D**: the two deviated wells run side by side. Open **Benchmark**: the five ranked against one another. Every page of this site ends with the credit the licence asks for.
7. After a new version of the program, press **Make the site** once more from the public site's own window: wells and files it already has are left alone, and anything the new version reads further is added.

## You know it worked when
The public site's window lists five wells, the 3D tab draws 16A(78)-32 and 16B(78)-32 turning to the east together, and the Collision tab puts them about 297 ft apart at the toe - the offset the project drilled them at.

## The same from a terminal
`gea public-pad --list` prints the files and where each is published; `gea public-pad --folder C:\forge --verify` hashes what is there; `gea public-pad --folder C:\forge --build --site C:\forge-site` makes the site.

Help: public-data, sites
