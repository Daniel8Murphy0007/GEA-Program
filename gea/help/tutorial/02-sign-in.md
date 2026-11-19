# 2. Sign in and find your way around
Who: office
Where: #/
Pages: #/tutorial, #/help
Takes: 15 minutes
> The first account, the four roles, the tabs under headings, the search box, the help panels and the serve window.

## Steps
1. The first time the control panel opens it shows **First administrator**: this workspace has no accounts yet. Type a name and a password and press **Create and sign in**; this account is the administrator. Keep the password somewhere safe - nobody can read it back.
2. Sign in. The bar at the top has two rows. The first is the headings, in the order of the work: Start; Wells & data; Watch & report; Well paths; Drilling; Well design; Completion & production; Disposal; Seismic; Keep it running; Learn. The second is the tabs under the heading that is open - under Well paths: Map, 3D, Collision, Hard line, Slide sheet, Plan vs actual. Click a heading to see its tabs; nothing changes on the page until you click a tab. The heading of the tab you are on carries a dot, and a heading carries the count of what needs attention under it (alarms not acknowledged, approvals waiting, setup items to do). With the tabs under headings the bar is two rows on any screen; on a phone the rows wrap.
3. Click **Help** in the top right (the ? button). A short panel appears under the heading of every page saying what it is for, with a **more...** link to the full help page and the lesson of this tutorial that walks through the page. Leave it on while you learn; turn it off later in your preferences. The **Help** tab holds every page under the job you came with, and its search box finds a word in any of them; the **Tutorial** tab is this course, where each person ticks the lessons off.
4. Type a well name or a report name in the **search box** and press Enter: the program finds wells, alarms, reports, jobs, configuration entries, patches and schedules by name. It finds a tab by its name too, and says which heading it is under - type *casing* when you cannot remember where the casing page is.
5. Open **Administration**, **Users**, and add one account per person. The **roles**: *viewer* reads everything and changes nothing; *operator* adds wells and data, declares what the operator knows and writes reports; *approver* signs off well tests and approvals; *admin* manages users, settings, backups and the VPN. A person has one role; give the smallest that does the job.
6. Look at the window behind the control panel - the **serve window**. It is the server: it prints every sign-in, every job and every stop as it happens. Closing it stops the site; minimise it instead.
7. Your own settings are under your name, top right: units (field or SI), your time zone, light or dark, the help panels, your password, and **Sign out everywhere else**.

## You know it worked when
Every person has an account with the right role, and the Audit / Update tab shows a `user.add` line for each.

## Where things are on the disk
Everything is in the workspace: `wells\` (one folder per well with its source files and records), `reports\`, `records\audit.jsonl` (the audit log), `users.json`, `keys\` (the signing key and the VPN keys - the backup carries them, nothing else does), `exports\`, `installers\`.

Help: start, site
