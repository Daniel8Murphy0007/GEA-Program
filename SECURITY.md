# Security policy

## Reporting a vulnerability

Please report a suspected vulnerability privately - never in a public
issue:

- on GitHub: this repository's **Security** tab -> **Report a
  vulnerability** (private vulnerability reporting), or
- by email: daniel.murphy00@enrgyone.com, subject "GEA-Program security".

Say which version (the Audit / Update page of the control panel, or `MANIFEST.json` in a kit), what you
did, what happened, and what an attacker gains. A report is acknowledged
when it is read; the fix or mitigation ships in a release, and the advisory
names that release and credits the reporter if they wish.

## Supported versions

Fixes go into the newest release. A site on an older kit installs the
newest beside it (`docs/IT_SECURITY.md`, "Updates"); the workspace carries
over unchanged.

## What a reviewer needs

`docs/IT_SECURITY.md` answers what an IT department asks before installing:
where the data is and where it goes, the port, HTTPS, sign-in and roles,
the audit log, the bill of materials, the kit's provenance and code
signing.
