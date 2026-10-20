# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Company sign-in: OpenID Connect, so people sign in to the control panel with the account the company already
manages - Microsoft Entra ID, Okta, Google Workspace, Ping, Keycloak, AD FS - and the company's own rules (multi-factor,
conditional access, leaving the company) apply. Local accounts stay, for the first administrator and for when the
company's sign-in cannot be reached.

The flow is the authorization code flow with PKCE (RFC 6749, RFC 7636; OpenID Connect Core 1.0 section 3.1), for a
confidential client (the service holds a client secret):
1. "Sign in with <company>" sends the browser to the provider's authorization endpoint with a fresh state, a nonce and
   a PKCE challenge (S256); the state is also bound to that browser by a short-lived cookie, so a sign-in started in
   one browser cannot be finished in another (login cross-site request forgery).
2. The provider sends the browser back to the redirect URI with a code. The service exchanges it, with the client
   secret and the PKCE verifier, for an ID token at the token endpoint, server to server.
3. The ID token is a JWT signed by the provider; it is checked here with nothing outside the standard library: the
   signature (RS256 - RSASSA-PKCS1-v1_5 with SHA-256 - against the provider's published keys, fetched from its JWKS
   and fetched again once when a token names a key not yet seen, which is how a provider rotates its keys), the
   issuer, the audience (this client), the expiry, the issue and not-before times (five minutes of clock difference
   allowed) and the nonce. Any other algorithm, `none` included, is refused.
4. The role in GEA comes from a claim the company controls - Entra ID's app roles (`roles`) or groups (`groups`), or
   any claim named - through a map the administrator keeps: the highest role any of the person's values maps to. No
   mapped value and no default role: no sign-in, and the page says why. The person is kept in users.json as an
   external account (no password), named by the claim chosen (`preferred_username` by default), and bound to the
   provider's subject: an administrator can disable them in GEA as well as in the company directory; a local account
   of the same name is never taken over.

Settings are in workspace.json (`control.oidc`); the client secret is in `keys\\oidc_client_secret.txt`, readable by
its owner only, never shown and never sent anywhere but the token endpoint - or read from an environment variable
the settings name.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import tls as TLS

SECRET_FILE = os.path.join('keys', 'oidc_client_secret.txt')
LEEWAY_S = 300
FLOW_S = 600                       # a sign-in started must be finished within ten minutes
HTTP_TIMEOUT_S = 15
ROLES = ('viewer', 'operator', 'approver', 'admin')
RANK = {r: i for i, r in enumerate(ROLES)}
DEFAULTS = {'issuer': '', 'client_id': '', 'redirect_uri': '', 'scopes': 'openid profile email', 'role_claim': 'roles',
            'role_map': {}, 'default_role': None, 'name_claim': 'preferred_username', 'label': 'Sign in with your company account',
            'token_auth': 'client_secret_post', 'client_secret_env': ''}
_LOCK = threading.Lock()
_CACHE: Dict[str, dict] = {}       # issuer -> {'discovery': ..., 'jwks': ..., 'at': ...}
_FLOWS: Dict[str, dict] = {}       # state -> {nonce, verifier, binding, created}


class OidcError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')


def b64url_decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


# -- settings ------------------------------------------------------------------------------------------------------------
def settings(ws) -> Optional[dict]:
    ws.refresh()
    s = (ws.manifest.get('control') or {}).get('oidc')
    return {**DEFAULTS, **s} if s else None


def public(ws) -> Optional[dict]:
    """What the sign-in page may know: that company sign-in is on, and its button's words."""
    s = settings(ws)
    return {'label': s['label']} if s and s.get('enabled', True) else None


def _secret(ws, s: dict) -> str:
    if s.get('client_secret_env'):
        v = os.environ.get(s['client_secret_env'], '')
        if not v:
            raise OidcError(f"the client secret's environment variable {s['client_secret_env']} is not set for the service")
        return v
    p = os.path.join(ws.path, SECRET_FILE)
    if not os.path.isfile(p):
        raise OidcError('no client secret kept: set it on the Company sign-in card, or name an environment variable')
    with open(p, encoding='utf-8') as f:
        return f.read().strip()


def check_settings(s: dict) -> dict:
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS or k == 'enabled'}}
    iss = str(out['issuer']).strip().rstrip('/')
    if not iss.startswith('https://'):
        raise OidcError('the issuer is an https:// address (Entra ID: https://login.microsoftonline.com/3f8b2c1d-6a4e-4d9b-8c2f-1e7a5b9d0c64/v2.0)')
    if '{tenantid}' in iss.lower() or iss.endswith(('/common/v2.0', '/organizations/v2.0', '/consumers/v2.0')):
        raise OidcError("the issuer names one tenant, the company's own (its tenant id), not common or organizations")
    out['issuer'] = iss
    if not str(out['client_id']).strip():
        raise OidcError('the client id (Entra ID: the application (client) id of the app registration)')
    ru = str(out['redirect_uri']).strip()
    p = urllib.parse.urlsplit(ru)
    if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1')):
        raise OidcError('the redirect URI is the control panel\'s https:// address followed by /api/oidc/callback (turn HTTPS on first: gea help https)')
    if p.path != '/api/oidc/callback':
        raise OidcError('the redirect URI ends in /api/oidc/callback, exactly as registered with the provider')
    out['redirect_uri'] = ru
    if 'openid' not in str(out['scopes']).split():
        raise OidcError("the scopes include 'openid'")
    rm = out.get('role_map') or {}
    if not isinstance(rm, dict) or any(v not in ROLES for v in rm.values()):
        raise OidcError(f'the role map takes a claim value to one of {", ".join(ROLES)}')
    if out.get('default_role') not in (None, '') + ROLES:
        raise OidcError(f'the default role is empty (no sign-in without a mapped value) or one of {", ".join(ROLES)}')
    out['default_role'] = out.get('default_role') or None
    if out['token_auth'] not in ('client_secret_post', 'client_secret_basic'):
        raise OidcError('token_auth is client_secret_post or client_secret_basic')
    if not rm and not out['default_role']:
        raise OidcError('map at least one claim value to a role, or set a default role - otherwise nobody can sign in')
    return out


def configure(ws, actor: str, s: dict, client_secret: Optional[str] = None) -> dict:
    out = check_settings(s)
    if client_secret:
        p = os.path.join(ws.path, SECRET_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(client_secret.strip())
        os.replace(p + '.tmp', p)
    ws.refresh()
    ws.manifest.setdefault('control', {})['oidc'] = {**out, 'enabled': bool(s.get('enabled', True)), 'set_utc': _utc(), 'by': actor}
    ws._save()
    ws.audit(actor, 'oidc.settings', {k: out[k] for k in ('issuer', 'client_id', 'redirect_uri', 'role_claim', 'role_map', 'default_role', 'name_claim')}
             | {'secret_changed': bool(client_secret), 'enabled': bool(s.get('enabled', True))})
    with _LOCK:
        _CACHE.pop(out['issuer'], None)
    return status(ws)


def turn_off(ws, actor: str) -> dict:
    ws.refresh()
    (ws.manifest.get('control') or {}).pop('oidc', None)
    ws._save()
    ws.audit(actor, 'oidc.off', {})
    return status(ws)


def status(ws) -> dict:
    s = settings(ws)
    if not s:
        return {'configured': False, 'detail': 'local accounts only'}
    try:
        _secret(ws, s)
        secret = 'kept' if not s.get('client_secret_env') else f"from {s['client_secret_env']}"
    except OidcError as e:
        secret = str(e)
    return {'configured': True, 'enabled': s.get('enabled', True), 'secret': secret, 'set_utc': s.get('set_utc'), 'by': s.get('by'),
            **{k: s[k] for k in DEFAULTS if k != 'client_secret_env'}, 'client_secret_env': s.get('client_secret_env') or ''}


# -- the provider ---------------------------------------------------------------------------------------------------------
def _get_json(url: str, ctx=None) -> dict:
    rq = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(rq, timeout=HTTP_TIMEOUT_S, context=ctx) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OidcError(f'could not read {url}: {getattr(e, "reason", e)}')


def discovery(issuer: str, ctx=None, refresh: bool = False) -> dict:
    with _LOCK:
        c = _CACHE.get(issuer)
    if c and not refresh and time.time() - c['at'] < 3600:
        return c
    d = _get_json(issuer + '/.well-known/openid-configuration', ctx)
    if str(d.get('issuer', '')).rstrip('/') != issuer:
        raise OidcError(f"the provider says its issuer is {d.get('issuer')!r}, not {issuer!r}")
    for k in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        if not str(d.get(k, '')).startswith('https://'):
            raise OidcError(f'the provider\'s discovery document has no usable {k}')
    jwks = _get_json(d['jwks_uri'], ctx)
    c = {'discovery': d, 'jwks': jwks, 'at': time.time()}
    with _LOCK:
        _CACHE[issuer] = c
    return c


def _rsa_key(jwks: dict, kid: Optional[str]) -> Optional[Tuple[int, int]]:
    for k in jwks.get('keys', []):
        if k.get('kty') == 'RSA' and (kid is None or k.get('kid') == kid) and k.get('use', 'sig') == 'sig':
            return int.from_bytes(b64url_decode(k['n']), 'big'), int.from_bytes(b64url_decode(k['e']), 'big')
    return None


def verify_id_token(token: str, issuer: str, client_id: str, nonce: str, jwks_get, now: Optional[float] = None) -> dict:
    """The ID token's claims, once its signature, issuer, audience, times and nonce are good; OidcError naming the
    first thing that is not. `jwks_get(refresh)` gives the provider's keys."""
    parts = token.split('.')
    if len(parts) != 3:
        raise OidcError('the ID token is not a signed JWT')
    try:
        head, claims = json.loads(b64url_decode(parts[0])), json.loads(b64url_decode(parts[1]))
        sig = b64url_decode(parts[2])
    except (ValueError, UnicodeDecodeError):
        raise OidcError('the ID token cannot be read')
    if head.get('alg') != 'RS256':
        raise OidcError(f"the ID token is signed with {head.get('alg')!r}; only RS256 is accepted")
    key = _rsa_key(jwks_get(False), head.get('kid'))
    if key is None:
        key = _rsa_key(jwks_get(True), head.get('kid'))           # the provider rotated its keys
    if key is None:
        raise OidcError(f"the ID token names a key ({head.get('kid')}) the provider does not publish")
    if not TLS.verify_pkcs1_sha256(key[0], key[1], (parts[0] + '.' + parts[1]).encode('ascii'), sig):
        raise OidcError('the ID token\'s signature is not good')
    now = time.time() if now is None else now
    if str(claims.get('iss', '')).rstrip('/') != issuer:
        raise OidcError(f"the ID token was issued by {claims.get('iss')!r}, not {issuer!r}")
    aud = claims.get('aud')
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise OidcError('the ID token is not for this application (audience)')
    if len(auds) > 1 and claims.get('azp') != client_id:
        raise OidcError('the ID token has several audiences and is not authorised for this application (azp)')
    try:
        exp, iat = float(claims['exp']), float(claims.get('iat', now))
    except (KeyError, TypeError, ValueError):
        raise OidcError('the ID token has no expiry')
    if exp < now - LEEWAY_S:
        raise OidcError('the ID token has expired')
    if iat > now + LEEWAY_S or float(claims.get('nbf', now)) > now + LEEWAY_S:
        raise OidcError("the ID token is not valid yet: check this machine's clock")
    if not nonce or not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
        raise OidcError('the ID token does not carry this sign-in\'s nonce (a replayed or foreign token)')
    if not claims.get('sub'):
        raise OidcError('the ID token names no subject')
    return claims


def role_of(claims: dict, s: dict) -> Tuple[Optional[str], List[str]]:
    if s['role_claim'] == 'groups' and 'groups' not in claims and 'groups' in (claims.get('_claim_names') or {}):
        raise OidcError('the person is in more groups than a token carries (Entra ID: over 200), so the token names none: map app roles '
                        "(role claim 'roles') instead, or limit the groups claim to the groups assigned to the application")
    v = claims.get(s['role_claim'])
    vals = [str(x) for x in (v if isinstance(v, list) else ([v] if v not in (None, '') else []))]
    mapped = [s['role_map'][x] for x in vals if x in s['role_map']]
    if mapped:
        return max(mapped, key=lambda r: RANK[r]), vals
    return s.get('default_role') or None, vals


# -- the flow -------------------------------------------------------------------------------------------------------------
def begin(ws, ctx=None) -> Tuple[str, str]:
    """(the provider's authorization URL, the binding value for the browser's cookie)."""
    s = settings(ws)
    if not s or not s.get('enabled', True):
        raise OidcError('company sign-in is not set up')
    d = discovery(s['issuer'], ctx)['discovery']
    state, nonce, verifier, binding = (secrets.token_urlsafe(32) for _ in range(4))
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _FLOWS.items() if now - v['created'] > FLOW_S]:
            _FLOWS.pop(k, None)
        if len(_FLOWS) > 5000:
            raise OidcError('too many sign-ins in progress; try again in a few minutes')
        _FLOWS[state] = {'nonce': nonce, 'verifier': verifier, 'binding': hashlib.sha256(binding.encode()).hexdigest(), 'created': now}
    q = {'response_type': 'code', 'client_id': s['client_id'], 'redirect_uri': s['redirect_uri'], 'scope': s['scopes'], 'state': state,
         'nonce': nonce, 'code_challenge': b64url(hashlib.sha256(verifier.encode('ascii')).digest()), 'code_challenge_method': 'S256',
         'response_mode': 'query'}
    ep = d['authorization_endpoint']
    return ep + ('&' if '?' in ep else '?') + urllib.parse.urlencode(q), binding


def finish(ws, query: Dict[str, str], binding: Optional[str], ctx=None, now: Optional[float] = None) -> dict:
    """The provider's answer at the redirect URI -> {'name', 'role', 'subject', 'claims_used'}; OidcError otherwise."""
    s = settings(ws)
    if not s or not s.get('enabled', True):
        raise OidcError('company sign-in is not set up')
    if query.get('error'):
        raise OidcError(f"the company sign-in answered: {query.get('error')} {query.get('error_description', '')}".strip())
    state = query.get('state') or ''
    with _LOCK:
        flow = _FLOWS.pop(state, None)               # one use: a state is never accepted twice
    if not flow or time.time() - flow['created'] > FLOW_S:
        raise OidcError('this sign-in is unknown or has expired: start again from the sign-in page')
    if not binding or not secrets.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), flow['binding']):
        raise OidcError('this sign-in was started in another browser: start again from the sign-in page')
    code = query.get('code') or ''
    if not code:
        raise OidcError('the company sign-in sent no code')
    c = discovery(s['issuer'], ctx)
    secret = _secret(ws, s)
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': s['redirect_uri'], 'code_verifier': flow['verifier']}
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
    if s['token_auth'] == 'client_secret_basic':
        headers['Authorization'] = 'Basic ' + base64.b64encode(
            f"{urllib.parse.quote(s['client_id'], safe='')}:{urllib.parse.quote(secret, safe='')}".encode()).decode()
    else:
        form.update(client_id=s['client_id'], client_secret=secret)
    rq = urllib.request.Request(c['discovery']['token_endpoint'], data=urllib.parse.urlencode(form).encode(), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(rq, timeout=HTTP_TIMEOUT_S, context=ctx) as r:
            tok = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode('utf-8'))
            why = f"{j.get('error')}: {j.get('error_description', '')}"
        except (ValueError, OSError):
            why = f'HTTP {e.code}'
        raise OidcError(f'the token endpoint did not accept the code ({why[:300]})')
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OidcError(f'could not reach the token endpoint: {getattr(e, "reason", e)}')
    if not tok.get('id_token'):
        raise OidcError('the token endpoint sent no ID token')

    def jwks_get(refresh: bool) -> dict:
        return discovery(s['issuer'], ctx, refresh=refresh)['jwks']
    claims = verify_id_token(tok['id_token'], s['issuer'], s['client_id'], flow['nonce'], jwks_get, now)
    role, vals = role_of(claims, s)
    name = str(claims.get(s['name_claim']) or claims.get('email') or claims.get('preferred_username') or '').strip()
    if not name:
        raise OidcError(f"the ID token carries no {s['name_claim']} to name the person by")
    if role is None:
        raise OidcError(f"{name} has no role in GEA: none of their {s['role_claim']} ({', '.join(vals) or 'none'}) is mapped, and there is no "
                        "default role. An administrator maps it on the Company sign-in card")
    return {'name': name, 'role': role, 'subject': f"{s['issuer']}#{claims['sub']}", 'claim_values': vals}


def test(ws, ctx=None) -> dict:
    """Read the provider's discovery document and keys now, and say what was found - before anyone tries to sign in."""
    s = settings(ws)
    if not s:
        raise OidcError('company sign-in is not set up')
    c = discovery(s['issuer'], ctx, refresh=True)
    d = c['discovery']
    try:
        _secret(ws, s)
        secret = 'kept'
    except OidcError as e:
        secret = str(e)
    keys = [k.get('kid') for k in c['jwks'].get('keys', []) if k.get('kty') == 'RSA']
    algs = d.get('id_token_signing_alg_values_supported') or []
    return {'ok': bool(keys) and 'RS256' in (algs or ['RS256']) and secret == 'kept', 'issuer': d['issuer'],
            'authorization_endpoint': d['authorization_endpoint'], 'token_endpoint': d['token_endpoint'], 'signing_keys': len(keys),
            'algorithms': algs, 'secret': secret, 'pkce': d.get('code_challenge_methods_supported'),
            'redirect_uri': s['redirect_uri'], 'note': 'the redirect URI must be registered with the provider exactly as shown'}


def text(st: dict) -> str:
    if not st.get('configured'):
        lines = ['Company sign-in: off - local accounts only']
    else:
        lines = [f"Company sign-in: {'on' if st.get('enabled', True) else 'paused'} - issuer {st['issuer']}, client {st['client_id']}",
                 f"  redirect URI {st['redirect_uri']} (registered with the provider exactly)",
                 f"  role from the claim '{st['role_claim']}': " + (', '.join(f'{k} -> {v}' for k, v in (st.get('role_map') or {}).items()) or 'no values mapped')
                 + f"; default {st.get('default_role') or 'none (no sign-in without a mapped value)'}",
                 f"  named by '{st['name_claim']}'; client secret {st.get('secret')}; set {st.get('set_utc')} by {st.get('by')}"]
    t = st.get('test')
    if t:
        lines.append(f"  provider: {'GOOD' if t['ok'] else 'NOT READY'} - {t['signing_keys']} signing key(s), algorithms {', '.join(t['algorithms']) or 'not stated'}, "
                     f"PKCE {', '.join(t['pkce'] or []) or 'not stated'}; token endpoint {t['token_endpoint']}; secret {t['secret']}")
    return '\n'.join(lines)
