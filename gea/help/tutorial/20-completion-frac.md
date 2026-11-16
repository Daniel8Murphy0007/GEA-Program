# 20. Completion and the frac: the penetrator, the treatment, frac hits
Who: office
Where: #/treatment
Pages: #/completion, #/frac
Takes: 35 minutes
> After drilling: the ESP wellhead penetrator checked against its ratings, what each frac stage's own pumping record says, and what each stage did to the wells around it.

## Steps
1. Open **Completion**. **Record the installation** of the ESP wellhead penetrator - the penetrator (a catalogue entry, or a tool of yours with its datasheet), the date, the installer, the motor type, the gauge distance, the insulation readings, the void test - and **Declare the drive**: the drive's output limits, the motor nameplate, the hazardous-area classification, the ambient range. The page checks the well against the penetrator's ratings one line at a time; a line outside raises a `penetrator.outside` alert.
2. Open **Frac treatment**. Under **Attach a stage's treatment record**, pick the well, name the stage and choose the stage's pumping record as the frac service company gave it - the pressure, the rate and the proppant, second by second - with its source.
3. **Stage by stage** gives each stage's volumes beside the record's own totalisers, the ISIP with the range it can be read to, the friction at the last rate held and, where the rate was stepped down, its two parts. Pick a stage for its charts; declare the depth of the perforations for a gradient.
4. **Give the Frac hits tab this well's stage times** saves typing the stage table.
5. Open **Frac hits**. Attach each treatment well's stage table (CSV or Excel, with its source and the UTC offset of its clock), unless the last step gave it, and name each monitored well's pressure channel.
6. The grid shows every stage against every monitored well - no response, poroelastic, mixed, or a frac hit. Pick a cell for the pressure's departure around the stage. Each page's **Write the report** keeps what it shows.

## On the practice pad
On the public site ({lesson:practice-pad}), with the stimulation files in the folder, sixteen real stages are attached to the two deviated wells. Open **Frac treatment**, pick 16A(78)-32's first stage of April 2022 and set the page's ISIP beside the service company's own post-job report, which is published with the data: the company gives 4,019 psi; the page reads 3,985 psi, within a range of 3,975 to 3,997, and says why its figure is the lower.

## You know it worked when
Each attached stage has a row under **Stage by stage** with its volumes agreeing with the record's own totalisers, and the Frac hits grid has a verdict in every cell.

## The same from a terminal
`gea penetrator --workspace C:\site`, `gea frac-treatment --workspace C:\site`, `gea frac-hits --workspace C:\site`.

Help: penetrator, frac-treatment, frac-hits
