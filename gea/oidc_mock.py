# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A stand-in OpenID Connect provider for the gate: discovery, JWKS, the authorization endpoint (answers at once for the
person it is told to be) and the token endpoint (checks the client, the code, the redirect URI and the PKCE verifier),
over HTTPS with a certificate made by gea.tls. Faults can be set to make the ID token wrong in one way at a time."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import tls as TLS


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')


class MockIdP:
    def __init__(self, client_id: str = 'gea-test-client', client_secret: str = 'test-secret-123'):
        self.client_id, self.client_secret = client_id, client_secret
        self.key = TLS.rsa_key(2048)
        self.kid = 'k1'
        self.user = {'sub': 'subject-0001', 'preferred_username': 'olga@example.com', 'roles': ['GEA.Operator']}
        self.fault = None              # 'aud', 'expired', 'sig', 'alg_none', 'nonce', 'iss', 'rotate', 'hs256'
        self.codes = {}
        self.token_calls = []
        d = tempfile.mkdtemp()
        cert, key, _ = TLS.self_signed(['127.0.0.1', 'localhost'], days=30)
        self.cafile = os.path.join(d, 'idp.crt')
        open(self.cafile, 'wb').write(cert)
        open(os.path.join(d, 'idp.key'), 'wb').write(key)
        sctx = TLS.server_context(self.cafile, os.path.join(d, 'idp.key'))
        self.client_ctx = ssl.create_default_context(cafile=self.cafile)
        idp = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, headers=None):
                b = json.dumps(obj).encode() if obj is not None else b''
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if obj is not None:
                    self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == '/tenant/v2.0/.well-known/openid-configuration':
                    return self._send(200, {'issuer': idp.issuer, 'authorization_endpoint': idp.base + '/tenant/oauth2/v2.0/authorize',
                                            'token_endpoint': idp.base + '/tenant/oauth2/v2.0/token', 'jwks_uri': idp.base + '/tenant/discovery/v2.0/keys',
                                            'id_token_signing_alg_values_supported': ['RS256']})
                if u.path == '/tenant/discovery/v2.0/keys':
                    k = idp.key
                    return self._send(200, {'keys': [{'kty': 'RSA', 'use': 'sig', 'kid': idp.kid, 'n': _b64u(k['n'].to_bytes((k['n'].bit_length() + 7) // 8, 'big')),
                                                      'e': _b64u(k['e'].to_bytes(3, 'big'))}]})
                if u.path == '/tenant/oauth2/v2.0/authorize':
                    if q.get('client_id') != idp.client_id or q.get('code_challenge_method') != 'S256' or 'openid' not in q.get('scope', '').split():
                        return self._send(400, {'error': 'invalid_request'})
                    code = secrets.token_urlsafe(16)
                    idp.codes[code] = {'nonce': q.get('nonce'), 'challenge': q.get('code_challenge'), 'redirect_uri': q.get('redirect_uri'), 'user': dict(idp.user)}
                    loc = q['redirect_uri'] + '?' + urllib.parse.urlencode({'code': code, 'state': q.get('state', '')})
                    return self._send(302, None, {'Location': loc})
                return self._send(404, {'error': 'not_found'})

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                f = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                if urllib.parse.urlsplit(self.path).path != '/tenant/oauth2/v2.0/token':
                    return self._send(404, {'error': 'not_found'})
                idp.token_calls.append(f)
                cid, sec = f.get('client_id'), f.get('client_secret')
                if self.headers.get('Authorization', '').startswith('Basic '):
                    cid, _, sec = base64.b64decode(self.headers['Authorization'][6:]).decode().partition(':')
                    cid, sec = urllib.parse.unquote(cid), urllib.parse.unquote(sec)
                if cid != idp.client_id or sec != idp.client_secret:
                    return self._send(401, {'error': 'invalid_client', 'error_description': 'AADSTS7000215: Invalid client secret provided.'})
                c = idp.codes.pop(f.get('code', ''), None)
                if not c or c['redirect_uri'] != f.get('redirect_uri'):
                    return self._send(400, {'error': 'invalid_grant', 'error_description': 'the code is unknown, used or for another redirect URI'})
                if _b64u(hashlib.sha256(f.get('code_verifier', '').encode()).digest()) != c['challenge']:
                    return self._send(400, {'error': 'invalid_grant', 'error_description': 'the PKCE verifier does not match'})
                return self._send(200, {'token_type': 'Bearer', 'id_token': idp.id_token(c), 'access_token': 'unused', 'expires_in': 3600})

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.httpd.socket = sctx.wrap_socket(self.httpd.socket, server_side=True)
        self.base = f'https://127.0.0.1:{self.httpd.server_address[1]}'
        self.issuer = self.base + '/tenant/v2.0'
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def id_token(self, c: dict) -> str:
        now = int(time.time())
        claims = {'iss': self.issuer, 'aud': self.client_id, 'iat': now, 'nbf': now, 'exp': now + 3600, 'nonce': c['nonce'], **c['user']}
        head = {'alg': 'RS256', 'typ': 'JWT', 'kid': self.kid}
        f = self.fault
        if f == 'aud':
            claims['aud'] = 'some-other-app'
        elif f == 'expired':
            claims['exp'] = now - 3600; claims['iat'] = now - 7200
        elif f == 'nonce':
            claims['nonce'] = 'not-this-one'
        elif f == 'iss':
            claims['iss'] = self.base + '/other-tenant/v2.0'
        elif f == 'alg_none':
            head['alg'] = 'none'
        elif f == 'hs256':
            head['alg'] = 'HS256'
        elif f == 'rotate':
            self.key = TLS.rsa_key(2048); self.kid = 'k2'; head['kid'] = 'k2'
        si = _b64u(json.dumps(head).encode()) + '.' + _b64u(json.dumps(claims).encode())
        if f == 'alg_none':
            return si + '.'
        sig = TLS.sign_pkcs1_sha256(self.key, si.encode('ascii'))
        if f == 'sig':
            sig = bytes([sig[0] ^ 1]) + sig[1:]
        return si + '.' + _b64u(sig)

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()
