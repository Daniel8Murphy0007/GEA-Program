# 22. New versions from inside the program
Who: office
Where: #/audit
Takes: 10 minutes
> The program fetches and installs its own new versions by clicking; nobody downloads a setup again.

## Steps
1. Open **Audit / Update**, the card **The release**: the newest release on GitHub against the version running, with the three kits it carries - `GEA-Program-Setup`, `GEA-Reader-Setup` and `GEA-Recorder` - each with the SHA-256 GitHub recorded.
2. Press **Fetch the kits** (the button names the version). A job brings them into the workspace's `installers\` folder, checks each against its recorded hash (a kit altered after that is not kept), and moves older kits to `installers\_older`. The card marks every kit on hand ✓.
3. Press **Install now** (the button names the version). The program's setup runs silently from there with this workspace, and the reader's setup with it; the control panel stops for about a minute and is started again by a helper outside the program. Sign in again. The card now shows the new version running.
4. The recorder's kit, `GEA-Recorder-0.81.0.tar.gz`, is in the same folder for the attendant to take to the box (lesson 25).
5. On a computer that cannot reach GitHub, the setup from the release page installs over the old version the same way; the workspace is kept.

## You know it worked when
The card says the running version is the newest, and `installers\install.json` records what was installed over what, when, by whom.

Help: audit-update, install
