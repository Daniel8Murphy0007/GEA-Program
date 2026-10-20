# Putting the dashboard behind TLS

`gea serve` speaks plain HTTP until HTTPS is turned on. On a single
operator's machine, bound to `127.0.0.1` (the default), that is fine: nothing
leaves the machine. The moment the page is to be opened from other computers,
it needs TLS - one of two ways:

* **the service itself** (no proxy to run): give the workspace the site's
  certificate, or let it make a self-signed one, and bind the network address -

      gea tls --workspace C:\site --site-cert C:\certs\gea-pad3.crt --site-key C:\certs\gea-pad3.key
      gea serve --workspace C:\site --host 0.0.0.0 --port 8765

  (or Administration -> HTTPS in the page; `gea help https` has the whole
  story: the names the certificate must carry, handing a self-signed one out,
  renewal and the expiry alert);
* **a reverse proxy** the site already runs, terminating TLS in front of it,
  with the service started with
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
  (`gea backup --restore D:\gea-backups\gea-site-Pad-3-20261013T020000Z.zip --to C:\site-restored`) and open the page
  before the first well is live. The doctor warns when there has never been
  one or the last is more than 26 hours old.

Company sign-in (single sign-on) is the service's own, by OpenID Connect
against the company's provider - Entra ID, Okta, Google, Ping, Keycloak,
AD FS (`gea help company-sign-in`); local accounts stay beside it.

What it does not do: obtain or renew a certificate from an authority (the
site's authority issues it, or Caddy does in front). Sessions live in the service process, so a restart signs
everyone out.
