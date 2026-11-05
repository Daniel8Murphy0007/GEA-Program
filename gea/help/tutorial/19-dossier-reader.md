# 19. The signed dossier and GEA Reader
Who: office
Where: #/reports
Takes: 20 minutes
> A well's or the pad's record in one file anyone can check without this program, and the one HTML file the client opens to check it.

## Steps
1. On **Reports**, **Signed dossiers**: **Sign a dossier for the pad**, or one of the wells (the operator role). Tick *embed the source records* to carry the drilling records themselves rather than their hashes. The dossier is a zip: the reports as they stand, the declarations behind them, the audit log, the source records' hashes, the site's Ed25519 signature, and a checker inside (`verify_dossier.py`, plain Python).
2. **Check** re-checks a dossier this site signed against the workspace as it now stands; a workspace that no longer matches a signed dossier raises an alert. *Check a dossier someone sent you* checks an uploaded one.
3. **Download GEA Reader for your clients**: one HTML file. Send it to the client once by any channel - email, a USB stick, their portal. Send the site's **key fingerprint** separately - by phone, or in the contract - as for any dossier; the card shows it.
4. The client opens `GEA-Reader.html` in any current browser, offline, and drops a dossier on it. The verdict: **Valid** (signed with the site's key, or a newer key the site's key handed over to), **Valid, but signed by a key you were not given**, or **Not valid** with the reason. Below it, every file the dossier holds: a report opens in the page as it was signed, a PDF in the browser's own viewer. A client who wants it installed, with a right-click **Check with GEA Reader** on dossiers, gets `GEA-Reader-Setup-0.77.0.exe` from the release page.
5. **The key**: it lives in `keys\dossier_ed25519.json` in the workspace. The backup carries it; nothing else does; never send it. An administrator **rotates** it with a reason when a person who had access leaves; the old key's history rides in every later dossier so a reader of an old dossier still knows it was the site's.

## You know it worked when
The Reader on another computer shows **Valid** for a dossier you signed today, and the site's fingerprint on the Reader matches the one on the card.

Help: dossier, reader
