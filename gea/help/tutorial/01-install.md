# 1. Install GEA on your computer
Who: office
Where: #/
Takes: 15 minutes
> The program installed like any Windows program, with the site's folder chosen once.

## Before you start
You need a Windows PC that will stay on while the site is watched (the office machine, or the doghouse PC), and the setup file for this version, `GEA-Program-Setup-0.82.0.exe`, from the release page on GitHub. No administrator rights are needed: the program installs for you alone.

## Steps
1. Double-click `GEA-Program-Setup-0.82.0.exe`. If Windows asks whether to run it, choose **Run** - the setup is published by the site, not by a store.
2. The setup asks for the **site workspace**: the one folder that will hold the wells, the reports, the records, the accounts and the keys. For a new site keep the default or type `C:\site`. For a workspace that already exists, point at it - it is used exactly as it is, its name included.
3. For a new workspace it asks the **site's name** (`Pad 3`). This name heads every report the program writes; an administrator can change it later in Administration, site settings.
4. Tick the desktop shortcut and finish. The setup puts **GEA-Program** in the Start menu and an icon in the notification area by the clock.
5. Start it: the Start menu's **GEA-Program** or the desktop shortcut. The control panel opens in a window of its own; the first time it asks for the first administrator's name and password (lesson 2).

## What happened on the disk
The program itself is under your user profile (`%LOCALAPPDATA%\Programs\GEA-Program`); the site is in the workspace folder you chose. Those two never mix: a newer setup replaces the program and leaves the workspace as it is.

## You know it worked when
The control panel window is open with the site's name in its title, and the notification area shows the GEA icon.

## Common questions
A newer version: from 0.74.0 the program fetches and installs its own new versions by clicking (lesson 22); you never download a setup again. A second site on the same PC: Administration, **Workspaces**, **Make it and open it** - each site is its own folder and its own window.

Help: install, start
