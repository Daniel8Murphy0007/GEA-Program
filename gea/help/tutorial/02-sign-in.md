# 2. Sign in and find your way around
Who: office
Where: #/
Takes: 15 minutes
> The first account, the four roles, the tabs, the search box, the help panels and the serve window.

## Steps
1. The first time the control panel opens it shows **First administrator**: this workspace has no accounts yet. Type a name and a password and press **Create and sign in**; this account is the administrator. Keep the password somewhere safe - nobody can read it back.
2. Sign in. The tabs run across the top (on a phone they stack): Home, Control room, Setup, Sites, Wells, Map, 3D, the engineering pages, Seismic, Patch panel, Files, Alarms, Approvals, Configuration, Reports, Verification, Survey, Tools & ports, Catalogue, Rocks, Jobs, Audit / Update, Administration, Tutorial and Help.
3. Click **Help** in the top right (the ? button). A short panel appears under the heading of every page saying what it is for, with a **more...** link to the full help page. Leave it on while you learn; turn it off later in your preferences.
4. Type a well name or a report name in the **search box** and press Enter: the program finds wells, alarms, reports, jobs, configuration entries, patches and schedules by name.
5. Open **Administration**, **Users**, and add one account per person. The **roles**: *viewer* reads everything and changes nothing; *operator* adds wells and data, declares what the operator knows and writes reports; *approver* signs off well tests and approvals; *admin* manages users, settings, backups and the VPN. A person has one role; give the smallest that does the job.
6. Look at the window behind the control panel - the **serve window**. It is the server: it prints every sign-in, every job and every stop as it happens. Closing it stops the site; minimise it instead.
7. Your own settings are under your name, top right: units (field or SI), your time zone, light or dark, the help panels, your password, and **Sign out everywhere else**.

## You know it worked when
Every person has an account with the right role, and the Audit / Update tab shows a `user.add` line for each.

## Where things are on the disk
Everything is in the workspace: `wells\` (one folder per well with its source files and records), `reports\`, `records\audit.jsonl` (the audit log), `users.json`, `keys\` (the signing key and the VPN keys - the backup carries them, nothing else does), `exports\`, `installers\`.

Help: start, site
