# Putting the dashboard behind TLS

`gea serve` speaks plain HTTP. On a single operator's machine, bound to
`127.0.0.1` (the default), that is fine: nothing leaves the machine. The
moment the page is to be opened from other computers, put a reverse proxy
that terminates TLS in front of it and start the service with
`--behind-proxy`, so it trusts the proxy's `X-Forwarded-For` (for the
sign-in rate limit's per-address count) and `X-Forwarded-Proto` (to mark
the session cookie `Secure` and send HSTS).

    gea serve --workspace C:\site --host 127.0.0.1 --port 8765 --behind-proxy

Keep the service itself on the loopback address; only the proxy listens on
the network. Two working examples are in this folder:

* `Caddyfile` - Caddy obtains and renews a certificate on its own when the
  host name is reachable from the internet, or use `tls internal` on a LAN.
* `nginx-gea.conf` - nginx with a certificate you supply (your IT department's
  internal CA, or a public one).

Both forward to `127.0.0.1:8765`, pass the two headers, and allow the 64 MB
request body the file upload needs.

What the service does on its own, proxy or not:

* passwords are salted PBKDF2-SHA256 hashes; sessions are random 256-bit
  tokens in an `HttpOnly; SameSite=Strict` cookie that expires after 12 h;
* five failed sign-ins for a name or from one address inside 15 minutes lock
  that name/address out for 15 minutes (HTTP 429 with `Retry-After`), audited;
* every action needs the page's `X-GEA-Action` header, so a form on another
  site cannot act on the dashboard;
* responses carry `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`;
* an administrator sees every live session (who, from where, since when) and
  can revoke one or all of a user's sessions; any user can sign out everywhere;
* the audit log is append-only and `gea housekeeping` segments it rather than
  truncating it;
* `gea backup --workspace C:\site --out D:\gea-backups --keep 14` copies the
  whole site - records, configuration, users.json, the audit log, the
  stations' live files - into one dated archive with a manifest, onto another
  disk or a share (a folder inside the workspace is refused); schedule it
  daily from Audit / Update, and restore it once onto an empty folder
  (`gea backup --restore <archive> --to C:\site-restored`) and open the page
  before the first well is live. The doctor warns when there has never been
  one or the last is more than 26 hours old.

What it does not do: the service has no TLS of its own, no certificate
handling, and no single sign-on; those belong to the proxy and the site's
identity provider. Sessions live in the service process, so a restart signs
everyone out.
