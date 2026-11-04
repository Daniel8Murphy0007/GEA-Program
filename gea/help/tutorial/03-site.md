# 3. Set up the site
Who: office
Where: #/setup
Takes: 30 minutes
> The Setup checklist: what the workspace still needs before a report can be handed over, and the site record that holds the wells together.

## Steps
1. Open **Setup**. The tab carries a badge with the number of required items still to do, and the Home page says which is next. Each item is done when what it asks for is there, not when someone ticks it.
2. **Site name**: as every report will print it. Set it once.
3. For each well: the **surface location** (latitude, longitude and the datum the figures are on - NAD27, NAD83 or WGS84, as the permit states), the **reference elevation** (kelly bushing, ground level or derrick floor, in feet, with its source - the completion report, the survey plat), and the **US Well Number** (the API number). With a position declared, **Fetch the ground elevation (USGS 3DEP)** brings the ground elevation there, to check your figure against before declaring.
4. **Create the site**: its name (`Retama block`), the client, and the wells it holds. A site is the engagement - what one client has - and the Site Report is the one document for it. The **Sites** tab lists every site with a link to its report.
5. The **backup folder**: a folder on another disk or a share (a folder inside the workspace is refused). Press **Back up now** once and **Schedule daily**. Lesson 21 says how to check and restore one.
6. **Refresh the reports**: the first run of every report. It is a job; the Jobs tab shows it running and its log.
7. Press **Go to the next item** until the badge reads 0. Recommended and optional items are listed beside the required ones; a report can be handed over without them, and says what it cannot say yet.

## You know it worked when
The Setup badge is gone, the Sites tab lists your site with its client and wells, and the Home page shows the wells with a state each.

## The same from a terminal
`gea checklist --workspace C:\site` prints the list and exits 0 when every required item is done.

Help: setup, sites, site
