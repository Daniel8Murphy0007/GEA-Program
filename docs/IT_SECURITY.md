# GEA-Program for an IT department

What an IT or security reviewer asks before GEA-Program is installed on a
site machine, answered from what the program does - each answer names the
command or file that shows it. The program's version is in `MANIFEST.json`
of the kit and on the control panel's Audit / Update page.
`gea security` prints this page from any installation of the program.

## What it is

One Python package (`gea-program`, MPL-2.0 source on GitHub and PyPI) with
one required dependency (numpy) and optional extras, delivered for a site as
a standalone kit: the package, every wheel it needs, the embeddable CPython
from python.org, and plain launch scripts. It installs into its own folder
without administrator rights and without network access. It writes nothing
outside that folder and the site's workspace folder, adds no driver, no
system service (a per-user scheduled task only if the site runs
`register-service.cmd`), and no registry keys.

**The Windows installer** (on each GitHub release since 0.65.0:
`GEA-Program-Setup-0.65.0.exe` for 0.65.0) installs that same kit for the signed-in user alone:
`PrivilegesRequired=lowest` (no elevation prompt), the program under
`%LOCALAPPDATA%\Programs\GEA-Program`, shortcuts in the user's Start menu
and desktop, an optional shortcut in the user's Startup folder, and the
user's own Add/Remove Programs entry (`HKCU`, the only registry key it
writes, removed by its uninstaller). Silent install for a software
deployment: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /WORKSPACE="C:\site"`
(`/SITENAME="Pad 3"` names a workspace the setup makes).
The setup never writes into an existing site workspace (a new one it makes,
with the site name asked for) and will not take one inside the program's own
folder; the uninstaller stops the control panel, removes the
program and leaves the workspace. The program's window is Microsoft Edge's
app window (`msedge --app=` on the local address); its notification-area
icon (standard library `ctypes`, `Shell_NotifyIconW`) reads only whether the
control panel's port answers on this computer.

`tools/standalone_check.py` (run by the acceptance gate) fails the build if
any module imports anything outside the standard library, numpy and the
declared extras.

## Data: where it is and where it goes

- **At rest**: everything the program keeps for a site is in the workspace
  folder the site chooses (by default `%LOCALAPPDATA%\GEA-Program\site`):
  the client's files as received, hashed; the derived records; the reports;
  the configuration with its version history; `users.json`; the append-only
  audit log `records\audit.jsonl`. Disk encryption, file-system permissions
  and backup are the site's (`gea backup` writes a verified archive to the
  folder or share the site names - never inside the workspace).
- **Keys** are in the workspace's `keys\` folder, readable by their owner
  where the file system allows: the site's dossier signing key (Ed25519), the
  HTTPS private key when the program made the certificate, the company
  sign-in client secret when one is kept. None of them is ever put in a
  report, an export, an evidence pack or a signed dossier, or sent by the
  service. The backup copies them with the rest of the workspace: protect
  the backup location accordingly.
- **Outbound connections** - none at install, none unless the site turns a
  feature on or a person asks:

  | What | To | When |
  |---|---|---|
  | Earthquake watch | `earthquake.usgs.gov` (ComCat) | while an operation runs, if the site enables it |
  | Elevation look-up | `epqs.nationalmap.gov` | when a person asks for a well's ground elevation |
  | Seismic archives | an FDSN service the site names (e.g. `service.iris.edu`) | when a person fetches station data |
  | Price series refresh | `raw.githubusercontent.com` (the EIA series' public mirror) | when a person refreshes them |
  | Update check | `pypi.org` | when a person runs `gea update --check` or opens Audit / Update |
  | Notifications | the site's own SMTP server or webhook | when the site configures a channel |
  | Company sign-in | the site's identity provider (Entra ID: `login.microsoftonline.com`) | at each company sign-in, if configured |
  | Trusted timestamp | the RFC 3161 time-stamping service the site names (e.g. `timestamp.digicert.com`); only the SHA-256 of a dossier's signature and a random nonce are sent | when a dossier is signed, if the site turns it on, or when a person asks |
  | Live data | the site's historian (PI Web API), WITSML store, OPC UA server, MQTT broker, Modbus/DNP3/HART-IP device, SeedLink server | when the site defines a patch for it |

  No telemetry, no usage reporting, no licence server. Everything else runs
  on the machine.

## Who can reach it

- **Port**: one, TCP 8765 by default (`GEA_PORT` / `--port`), bound to
  `127.0.0.1` unless the site starts it with `--host`. Nothing else listens.
- **Transport**: HTTPS when the workspace has a certificate - the site's own
  (from its certificate authority) or one the program makes (`gea tls`,
  `gea help https`): TLS 1.2 minimum, the session cookie `Secure`,
  `Strict-Transport-Security`, plain HTTP on the same port redirected; a
  certificate in its last 30 days raises an alert; a broken certificate stops
  the start rather than falling back to plain HTTP. Or the site's reverse
  proxy terminates TLS (`deploy/`).
- **Sign-in**: company accounts by OpenID Connect (Entra ID, Okta, Google,
  Ping, Keycloak, AD FS; authorization code flow with PKCE; the ID token's
  RS256 signature, issuer, audience, expiry and nonce checked;
  `gea help company-sign-in`), and local accounts (PBKDF2-SHA256, 200,000
  rounds, a salt per user; five failures lock the name and the address for
  15 minutes). Sessions are random 256-bit tokens in `HttpOnly;
  SameSite=Strict` cookies, 12 hours; an administrator sees every live
  session and revokes any.
- **Roles**: viewer < operator < approver < admin, enforced by the service
  on every request; a company account's role comes from the company's app
  roles or groups at each sign-in.
- **The page**: every action needs a header a cross-site form cannot set;
  every response carries a Content-Security-Policy (`default-src 'self'`,
  `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff` and a
  same-origin referrer policy. The page loads nothing from the internet.

## What it records

Every action - by a person through the page, by a scheduled job, by the
command line - is a line in `records\audit.jsonl` with the actor, the time
and the SHA-256 of its inputs; sign-ins and failures with the address and
the reason. The log is append-only; `gea housekeeping` segments it, never
truncates it. A signed dossier carries the audit chain's head and lets
anyone verify, without GEA, that the reports and the log have not changed
since it was signed.

## What is installed, and from where

- `gea sbom --out C:\site\reports` writes the software bill of materials of
  the running installation (each component's name, version, supplier,
  licence, hash, package URL and relationship, from the installed metadata).
- The kit's `SHA256SUMS.txt` holds the hash of every file and `MANIFEST.json`
  the wheels and the commit it was built from.
- **Provenance**: every kit attached to a GitHub release is attested by the
  build that made it (GitHub artifact attestations, Sigstore). Check a
  downloaded kit with the GitHub CLI, for example:
  `gh attestation verify gea-program-0.57.0-win64.zip -R Daniel8Murphy0007/GEA-Program`.
  The PyPI release is published by GitHub's trusted publishing, which
  attaches the publisher's attestations to the files on PyPI.
- **Code signing (Windows)**: the batch launchers of the kit cannot carry a
  signature of their own, so the kit carries a file catalog beside its
  folder in the zip - `gea-program-0.57.0-win64.cat` for 0.57.0 - holding
  the SHA-256 of every file in the kit. When the publisher's signing
  certificate is in place the catalog is Authenticode-signed:
  `Get-AuthenticodeSignature .\gea-program-0.57.0-win64.cat` shows the
  publisher, and, in the folder the zip was extracted to and before
  `install.cmd` runs,
  `Test-FileCatalog -CatalogFilePath .\gea-program-0.57.0-win64.cat -Path .\gea-program-0.57.0-win64`
  says Valid when no file has changed. An application-control policy
  (App Control for Business / WDAC) can trust the kit by its signed
  catalog. Until the certificate is in place the catalog ships unsigned and
  the build says so. The embeddable Python's own executables are signed by
  the Python Software Foundation.
- **The setup's signature**: when the certificate is in place the setup
  executable is Authenticode-signed and timestamped by the same build
  (`Get-AuthenticodeSignature .\GEA-Program-Setup-0.65.0.exe`), and it is
  attested like the kit (`gh attestation verify GEA-Program-Setup-0.65.0.exe
  -R Daniel8Murphy0007/GEA-Program`). Before each release the build installs
  it silently, upgrades it in place with the control panel running, starts
  it and uninstalls it, and checks the workspace untouched. The uninstaller
  it writes (`unins000.exe`, Inno Setup's) is not signed separately.
- **Updates**: a new kit is installed beside the old one; the workspace is
  found and opened as it is. A newer setup installs over the older one in
  place, after stopping the control panel. Nothing updates itself: on a pip installation,
  `gea update` upgrades from PyPI only when a person runs it.
- **The acceptance gate**: `verify.cmd` runs every check of the program's
  acceptance suite from the installed kit - the site's own evidence that
  this installation does what the release says.

## Reporting a vulnerability

See `SECURITY.md`: privately, through the repository's "Report a
vulnerability" (GitHub private vulnerability reporting) or by email to the
address there - never in a public issue. A fix or a mitigation ships in a
release the advisory names.

## Support

The source is open (MPL-2.0). The signed kit, its updates and support are a
subscription from ENRGYONE: see `docs/commercial/COMMERCIAL.md`.
