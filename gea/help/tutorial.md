# The tutorial

> Every step of GEA and the programs around it - the control panel, the Reader, the Recorder on the pad, GEA-VPN, GEA Driller - as 29 lessons: the office's first, the pad attendant's after, each naming the page it is done on, what you press, and how you know it worked.

**The command**: the **Tutorial** tab. The lessons are listed by part - Getting started, Wells and their data, Watching the pad, The engineering pages, Seismic, Reports for the client, Keeping it running, On the pad, The driller's agent - each with who it is for and how long it takes. Open one: **Open the page this lesson is about** goes to the tab it is done on, the numbered steps say what to press, **You know it worked when** says what to look for, and **Mark this lesson done** ticks it off for you (each account has its own progress; the list says how many are done and which comes next). **Print the handout** opens the whole course as one page for the people who do not sit at this screen - the attendant on the pad - and the browser's Print makes the PDF; **Write the handout into exports\tutorial** (the operator role) writes the same as `GEA-Tutorial-0.90.0.html` and `.md` into the workspace. From a terminal: `gea tutorial` lists the lessons, `gea tutorial --lesson 3` prints one, `gea tutorial --lesson install` the same by name, `gea tutorial --handout --workspace C:\site` writes the handout into the workspace, `gea tutorial --handout --out C:\site\GEA-Tutorial.html` writes it anywhere. The lessons are Markdown files under the program's `help\tutorial` folder.

**What it writes**: `records\tutorial_progress.json` in the workspace (who finished which lesson, when); `exports\tutorial\GEA-Tutorial-0.90.0.html` and `.md` when the handout is written, with the audit line `tutorial.handout`; `tutorial.print` when the handout is opened from the page. Reading a lesson writes nothing.

**The number to check**: the list's "done" count against 29, and on the Tutorial tab which lesson is next. A lesson's own check is its **You know it worked when** - the thing on the screen or the disk that is there only if the steps were done; a tick without it is a tick.

**What this page will not call a measurement**: a lesson done is a person's tick, not the program's verdict - the Setup tab's checklist is what says what the workspace still needs, and the Verification tab's gate is what says the program works; the tutorial says how to get there, and nothing it says changes a report.

Related: `gea help start`, `gea help setup`, `gea help install`.
