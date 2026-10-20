# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""HTTPS for the dashboard: the site's own certificate, or one this program makes, with nothing outside the standard
library.

The service speaks TLS when the workspace names a certificate and its private key (PEM files):
- the site's own, from its certificate authority (the company's internal one, which every managed computer on the
  site already trusts, or a public one). The files stay where the site keeps them and are read at each start, so a
  renewed certificate dropped in the same place is picked up by a restart; or
- a self-signed one this module makes, for a site that has none yet. It encrypts the connection from the first
  start, but a browser warns until the site trusts it: the administrator hands out the certificate (never the key)
  for the site's computers to trust, by group policy or by hand, and compares the SHA-256 fingerprint.

TLS 1.2 is the floor. With TLS on, the session cookies are marked Secure, the service tells browsers to come back
only over HTTPS (Strict-Transport-Security), and a browser that asks over plain HTTP on the same port is sent to the
same address over HTTPS (when it named this machine by a name the certificate carries) or told to use HTTPS. A
certificate in its last 30 days raises the `tls.expiring` alert. A workspace whose certificate files are missing or
do not belong together does not start: a site that turned HTTPS on is never served in plain text by surprise.

The self-signed certificate is made here: an RSA key of 2048 or 3072 bits (primes by Miller-Rabin, e = 65537) and an
X.509 version 3 certificate written in DER by hand (RFC 5280: the subject and the issuer, the validity, the subject
alternative names - every host name and address the dashboard is reached by - not a certificate authority, the key
usage and the extended key usage of a TLS server), signed with SHA-256 and PKCS #1 v1.5 (RFC 8017). Its key is
written to the workspace's `keys\\tls` folder (readable by its owner only, where the system allows), and never put in
a report, an export, an evidence pack or a dossier, nor sent by the service; the certificate itself is public.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import math
import os
import secrets
import socket
import ssl
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

TLS_DIR = os.path.join('keys', 'tls')
SELF_CERT = os.path.join(TLS_DIR, 'self_signed_certificate.pem')
SELF_KEY = os.path.join(TLS_DIR, 'self_signed_key.pem')
MIN_BITS = 2048
BITS = (2048, 3072)
MAX_DAYS = 825                     # Apple's systems refuse a longer server certificate even when it is trusted by hand (HT210176)
EXPIRING_DAYS = 30                 # the alert and the doctor's warning
HANDSHAKE_S = 10.0                 # a connection that has not finished its handshake by then is dropped
_SMALL_PRIMES = [p for p in range(3, 2000) if all(p % q for q in range(2, int(p ** 0.5) + 1))]


class TlsError(ValueError):
    pass


# -- RSA ------------------------------------------------------------------------------------------------------------------
def _probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2; s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, e: int = 65537) -> int:
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if c % e == 1:
            continue
        if _probable_prime(c):
            return c


def rsa_key(bits: int = 2048) -> Dict[str, int]:
    if bits < MIN_BITS:
        raise ValueError(f'an RSA key of at least {MIN_BITS} bits')
    e = 65537
    while True:
        p, q = _prime(bits // 2), _prime(bits - bits // 2)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        lam = (p - 1) * (q - 1) // math.gcd(p - 1, q - 1)
        if math.gcd(e, lam) != 1:
            continue
        d = pow(e, -1, lam)
        return {'n': n, 'e': e, 'd': d, 'p': p, 'q': q, 'dp': d % (p - 1), 'dq': d % (q - 1), 'qi': pow(q, -1, p)}


def _i2b(x: int, length: Optional[int] = None) -> bytes:
    length = length or max(1, (x.bit_length() + 7) // 8)
    return x.to_bytes(length, 'big')


SHA256_PREFIX = bytes.fromhex('3031300d060960864801650304020105000420')


def sign_pkcs1_sha256(key: Dict[str, int], msg: bytes) -> bytes:
    k = (key['n'].bit_length() + 7) // 8
    t = SHA256_PREFIX + hashlib.sha256(msg).digest()
    em = b'\x00\x01' + b'\xff' * (k - len(t) - 3) + b'\x00' + t
    m = int.from_bytes(em, 'big')
    m1, m2 = pow(m, key['dp'], key['p']), pow(m, key['dq'], key['q'])        # the Chinese remainder theorem
    h = (key['qi'] * (m1 - m2)) % key['p']
    return _i2b(m2 + h * key['q'], k)


def verify_pkcs1_sha256(n: int, e: int, msg: bytes, sig: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    em = _i2b(pow(int.from_bytes(sig, 'big'), e, n), k)
    t = SHA256_PREFIX + hashlib.sha256(msg).digest()
    return em == b'\x00\x01' + b'\xff' * (k - len(t) - 3) + b'\x00' + t


# -- DER ------------------------------------------------------------------------------------------------------------------
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = _i2b(n)
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(x: int) -> bytes:
    b = _i2b(x)
    if b[0] & 0x80:
        b = b'\x00' + b
    return _tlv(0x02, b)


def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b''.join(parts))


def _set(*parts: bytes) -> bytes:
    return _tlv(0x31, b''.join(parts))


def _oid(dotted: str) -> bytes:
    p = [int(x) for x in dotted.split('.')]
    out = bytes([40 * p[0] + p[1]])
    for v in p[2:]:
        enc = [v & 0x7F]
        v >>= 7
        while v:
            enc.append(0x80 | (v & 0x7F)); v >>= 7
        out += bytes(reversed(enc))
    return _tlv(0x06, out)


def _utf8(s: str) -> bytes:
    return _tlv(0x0C, s.encode('utf-8'))


def _time(t: datetime) -> bytes:
    if 1950 <= t.year < 2050:
        return _tlv(0x17, t.strftime('%y%m%d%H%M%SZ').encode())
    return _tlv(0x18, t.strftime('%Y%m%d%H%M%SZ').encode())


def _name(cn: str, org: str) -> bytes:
    return _seq(_set(_seq(_oid('2.5.4.10'), _utf8(org))), _set(_seq(_oid('2.5.4.3'), _utf8(cn))))


def _ext(oid: str, critical: bool, value: bytes) -> bytes:
    return _seq(_oid(oid), *( [_tlv(0x01, b'\xff')] if critical else []), _tlv(0x04, value))


def _pem(label: str, der: bytes) -> bytes:
    b = base64.b64encode(der).decode('ascii')
    return (f'-----BEGIN {label}-----\n' + '\n'.join(b[i:i + 64] for i in range(0, len(b), 64)) + f'\n-----END {label}-----\n').encode('ascii')


def self_signed(names: List[str], days: int = MAX_DAYS, bits: int = 2048, org: str = 'GEA-Program site') -> Tuple[bytes, bytes, dict]:
    """A self-signed server certificate for the given host names and addresses: (certificate PEM, private key PEM,
    the certificate's facts as `read_certificate` gives them)."""
    names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not names:
        raise TlsError('a certificate needs at least one host name or address the dashboard is reached by')
    for n in names:
        try:
            ipaddress.ip_address(n)
        except ValueError:
            if not all(c.isalnum() or c in '-.*' for c in n) or len(n) > 253:
                raise TlsError(f"'{n}' is neither a host name nor an address")
    if not 1 <= int(days) <= MAX_DAYS:
        raise TlsError(f'a server certificate is valid for 1 to {MAX_DAYS} days (Apple\'s systems do not accept a longer one)')
    if int(bits) not in BITS:
        raise TlsError(f'an RSA key of {" or ".join(map(str, BITS))} bits')
    key = rsa_key(int(bits))
    alg = _seq(_oid('1.2.840.113549.1.1.11'), _tlv(0x05, b''))                  # sha256WithRSAEncryption
    now = datetime.now(timezone.utc).replace(microsecond=0)
    nb, na = now - timedelta(minutes=5), now + timedelta(days=int(days))
    spki = _seq(_seq(_oid('1.2.840.113549.1.1.1'), _tlv(0x05, b'')), _tlv(0x03, b'\x00' + _seq(_int(key['n']), _int(key['e']))))
    san = []
    for n in names:
        try:
            san.append(_tlv(0x87, ipaddress.ip_address(n).packed))
        except ValueError:
            san.append(_tlv(0x82, n.encode('ascii')))
    ski = hashlib.sha1(_seq(_int(key['n']), _int(key['e']))).digest()
    exts = _tlv(0xA3, _seq(
        _ext('2.5.29.19', True, _seq()),                                        # basicConstraints: not a certificate authority
        _ext('2.5.29.15', True, _tlv(0x03, b'\x05\xa0')),                       # keyUsage: digitalSignature, keyEncipherment
        _ext('2.5.29.37', False, _seq(_oid('1.3.6.1.5.5.7.3.1'))),              # extKeyUsage: serverAuth
        _ext('2.5.29.17', False, _seq(*san)),                                   # subjectAltName
        _ext('2.5.29.14', False, _tlv(0x04, ski))))                             # subjectKeyIdentifier
    serial = secrets.randbits(63) | (1 << 62)
    name = _name(names[0], org)
    tbs = _seq(_tlv(0xA0, _int(2)), _int(serial), alg, name, _seq(_time(nb), _time(na)), name, spki, exts)
    der = _seq(tbs, alg, _tlv(0x03, b'\x00' + sign_pkcs1_sha256(key, tbs)))
    pk = _seq(_int(0), *(_int(key[k]) for k in ('n', 'e', 'd', 'p', 'q', 'dp', 'dq', 'qi')))
    return _pem('CERTIFICATE', der), _pem('RSA PRIVATE KEY', pk), read_certificate(_pem('CERTIFICATE', der))


# -- reading a certificate ----------------------------------------------------------------------------------------------
def _read(b: bytes, i: int) -> Tuple[int, bytes, int]:
    """One DER element at i: (tag, contents, the index after it)."""
    if i + 2 > len(b):
        raise TlsError('the certificate ends in the middle of an element')
    tag, ln = b[i], b[i + 1]
    i += 2
    if ln & 0x80:
        k = ln & 0x7F
        if not 1 <= k <= 4:
            raise TlsError('the certificate has an element length this reader does not take')
        ln = int.from_bytes(b[i:i + k], 'big')
        i += k
    if i + ln > len(b):
        raise TlsError('the certificate ends in the middle of an element')
    return tag, b[i:i + ln], i + ln


def _children(b: bytes) -> List[Tuple[int, bytes, bytes]]:
    """The elements inside a constructed one: (tag, contents, the whole element)."""
    out, i = [], 0
    while i < len(b):
        t, v, j = _read(b, i)
        out.append((t, v, b[i:j]))
        i = j
    return out


def _oid_str(v: bytes) -> str:
    p = [v[0] // 40, v[0] % 40]
    x = 0
    for c in v[1:]:
        x = (x << 7) | (c & 0x7F)
        if not c & 0x80:
            p.append(x); x = 0
    return '.'.join(map(str, p))


def _when(t: int, v: bytes) -> datetime:
    s = v.decode('ascii')
    if t == 0x17:
        y = int(s[:2]); s = (f'20{s[:2]}' if y < 50 else f'19{s[:2]}') + s[2:]
    return datetime.strptime(s[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)


def _cn(v: bytes) -> str:
    cn = ''
    for _, rdn, _ in _children(v):
        for _, atv, _ in _children(rdn):
            parts = _children(atv)
            if len(parts) == 2 and _oid_str(parts[0][1]) == '2.5.4.3':
                cn = parts[1][1].decode('utf-8', 'replace')
    return cn


def pem_blocks(pem: bytes, label: str) -> List[bytes]:
    """The DER of every `label` block in a PEM text, in order."""
    t = pem.decode('ascii', 'replace')
    out, start = [], f'-----BEGIN {label}-----'
    i = t.find(start)
    while i >= 0:
        j = t.find(f'-----END {label}-----', i)
        if j < 0:
            break
        try:
            out.append(base64.b64decode(''.join(t[i + len(start):j].split()), validate=True))
        except ValueError:
            raise TlsError(f'a {label.lower()} block is not valid base64')
        i = t.find(start, j)
    return out


def read_certificate(pem: bytes) -> dict:
    """The first certificate of a PEM file (the server's own; any after it are the chain): its subject and issuer, the
    names it is valid for, its validity, its key, whether it signed itself, and its SHA-256 fingerprint."""
    ders = pem_blocks(pem, 'CERTIFICATE')
    if not ders:
        raise TlsError('no certificate in the file (a PEM file with a BEGIN CERTIFICATE block; a .pfx or .p12 is exported to PEM first)')
    der = ders[0]
    t, body, _ = _read(der, 0)
    cert = _children(body)
    if t != 0x30 or len(cert) != 3:
        raise TlsError('the first certificate in the file is not an X.509 certificate')
    tbs = _children(cert[0][1])
    k = 1 if tbs[0][0] == 0xA0 else 0
    serial, issuer, validity, subject, spki = tbs[k][1], tbs[k + 2], tbs[k + 3], tbs[k + 4], tbs[k + 5]
    (t1, v1, _), (t2, v2, _) = _children(validity[1])
    names: List[str] = []
    ca = None
    for tt, v, _ in tbs[k + 6:]:
        if tt != 0xA3:
            continue
        for _, ext, _ in _children(_children(v)[0][1]):
            parts = _children(ext)
            oid, value = _oid_str(parts[0][1]), parts[-1][1]
            if oid == '2.5.29.17':
                for gt, gv, _ in _children(_read(value, 0)[1]):
                    if gt == 0x82:
                        names.append(gv.decode('ascii', 'replace'))
                    elif gt == 0x87:
                        names.append(str(ipaddress.ip_address(gv)))
            elif oid == '2.5.29.19':
                inner = _children(_read(value, 0)[1])
                ca = bool(inner and inner[0][0] == 0x01 and inner[0][1] != b'\x00')
    alg = _children(spki[1])
    key_alg = _oid_str(_children(alg[0][1])[0][1])
    bits, n, e = None, None, None
    if key_alg == '1.2.840.113549.1.1.1':
        rsa = _children(_read(alg[1][1][1:], 0)[1])
        n, e = int.from_bytes(rsa[0][1], 'big'), int.from_bytes(rsa[1][1], 'big')
        bits = n.bit_length()
    nb, na = _when(t1, v1), _when(t2, v2)
    self_signed_ = issuer[2] == subject[2]
    sig_ok = None
    if self_signed_ and n is not None and _oid_str(_children(cert[1][1])[0][1]) == '1.2.840.113549.1.1.11':
        sig_ok = verify_pkcs1_sha256(n, e, cert[0][2], cert[2][1][1:])
    return {'subject': _cn(subject[1]), 'issuer': _cn(issuer[1]), 'names': names, 'not_before': nb.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'not_after': na.strftime('%Y-%m-%dT%H:%M:%SZ'), 'serial': serial.hex(),
            'key': {'1.2.840.113549.1.1.1': 'RSA', '1.2.840.10045.2.1': 'EC'}.get(key_alg, key_alg), 'bits': bits,
            'self_signed': self_signed_, 'self_signature_ok': sig_ok, 'ca': ca, 'chain': len(ders) - 1,
            'sha256_fingerprint': ':'.join(f'{x:02X}' for x in hashlib.sha256(der).digest())}


# -- the workspace's certificate ---------------------------------------------------------------------------------------
def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _abs(ws, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ws.path, p)


def default_names() -> List[str]:
    """The names a self-signed certificate is made for when none are given: this machine's name, its full name, and
    the loopback name and address."""
    out = []
    try:
        h = socket.gethostname()
        out += [h, socket.getfqdn(h)]
    except OSError:
        pass
    return list(dict.fromkeys(n for n in out + ['localhost', '127.0.0.1'] if n))


def _no_password():
    raise TlsError('the private key is encrypted with a passphrase: the service starts unattended, so export the key without one '
                   '(and keep the file readable by the service account only)')


def server_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    """The TLS context of a server with this certificate and key (TLS 1.2 or later); refuses a pair that does not
    belong together, a missing file, or an encrypted key."""
    for what, p in (('certificate', cert_path), ('private key', key_path)):
        if not os.path.isfile(p):
            raise TlsError(f'the {what} file is not there: {p}')
    with open(key_path, 'rb') as f:
        kt = f.read()
    if b'PRIVATE KEY-----' not in kt:
        raise TlsError(f'no private key in {key_path} (a PEM file with a BEGIN PRIVATE KEY or BEGIN RSA PRIVATE KEY block)')
    if b'ENCRYPTED' in kt:
        _no_password()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert_path, key_path, password=_no_password)
    except ssl.SSLError as e:
        why = str(e)
        if any(x in why.upper() for x in ('KEY_VALUES_MISMATCH', 'KEY VALUES MISMATCH', 'NO_CERTIFICATE_ASSIGNED')):
            raise TlsError('the private key does not belong to the certificate (they come as a pair: the key the certificate request was made with)')
        raise TlsError(f'the certificate and key could not be loaded: {why}')
    return ctx


def setting(ws) -> Optional[dict]:
    ws.refresh()
    return ((ws.manifest.get('control') or {}).get('tls')) or None


def status(ws, now: Optional[datetime] = None) -> dict:
    """What the workspace's HTTPS setting is and how its certificate stands: OFF, OK, EXPIRING (in its last 30 days),
    EXPIRED, NOT_YET_VALID or BROKEN (the files are missing, unreadable or not a pair - the service will not start)."""
    s = setting(ws)
    if not s:
        return {'state': 'OFF', 'configured': False, 'detail': 'plain HTTP: fine on this machine alone; for the site network turn HTTPS on '
                                                                '(the site\'s certificate, or a self-signed one made here)'}
    cert, key = _abs(ws, s['cert']), _abs(ws, s['key'])
    out = {'configured': True, 'mode': s.get('mode'), 'cert': cert, 'key': key, 'set_utc': s.get('set_utc'), 'by': s.get('by')}
    try:
        with open(cert, 'rb') as f:
            facts = read_certificate(f.read())
        server_context(cert, key)
    except (OSError, TlsError) as e:
        return {**out, 'state': 'BROKEN', 'detail': f'{e}. The service will not start until it is put right or HTTPS is turned off'}
    now = now or datetime.now(timezone.utc)
    na = datetime.strptime(facts['not_after'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    nb = datetime.strptime(facts['not_before'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    days = (na - now).total_seconds() / 86400.0
    out.update(facts=facts, days_left=round(days, 1))
    if days <= 0:
        return {**out, 'state': 'EXPIRED', 'detail': f"the certificate expired on {facts['not_after']}: browsers will not open the page with it. "
                                                     + ('Make a new one.' if s.get('mode') == 'self-signed' else 'Put the renewed one in its place and restart.')}
    if now < nb:
        return {**out, 'state': 'NOT_YET_VALID', 'detail': f"the certificate is valid from {facts['not_before']}: check this machine's clock"}
    if days <= EXPIRING_DAYS:
        return {**out, 'state': 'EXPIRING', 'detail': f"the certificate expires on {facts['not_after']}, in {days:.0f} day(s): "
                                                      + ('make a new one and hand it out again' if s.get('mode') == 'self-signed'
                                                         else "ask for the renewal and put it in its place")}
    trust = ('self-signed: browsers warn until the site trusts this certificate (hand out the certificate, never the key, and compare '
             'the SHA-256 fingerprint)' if facts['self_signed'] else f"issued by {facts['issuer'] or 'the site authority'}")
    return {**out, 'state': 'OK', 'detail': f"HTTPS with {facts['subject']}, valid to {facts['not_after'][:10]} ({days:.0f} days); {trust}"}


def context(ws) -> Optional[ssl.SSLContext]:
    """The server's TLS context from the workspace's setting, None when HTTPS is off; TlsError when it is on and its
    files are not right (the service then does not start)."""
    s = setting(ws)
    if not s:
        return None
    return server_context(_abs(ws, s['cert']), _abs(ws, s['key']))


def _store_setting(ws, entry: Optional[dict], actor: str, action: str, detail: dict) -> None:
    ws.refresh()
    ctl = ws.manifest.setdefault('control', {})
    if entry is None:
        ctl.pop('tls', None)
    else:
        ctl['tls'] = entry
    ws._save()
    ws.audit(actor, action, detail)


def make_self_signed(ws, actor: str, names: Optional[List[str]] = None, days: int = MAX_DAYS, bits: int = 2048) -> dict:
    """Make a self-signed certificate and its key in the workspace's keys\\tls folder and serve with it from the next
    start."""
    names = names or default_names()
    cert, key, facts = self_signed(names, days=days, bits=bits)
    d = os.path.join(ws.path, TLS_DIR)
    os.makedirs(d, exist_ok=True)
    for rel, data, private in ((SELF_KEY, key, True), (SELF_CERT, cert, False)):
        p = os.path.join(ws.path, rel)
        fd = os.open(p + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(p + '.tmp', p)
    server_context(os.path.join(ws.path, SELF_CERT), os.path.join(ws.path, SELF_KEY))
    _store_setting(ws, {'mode': 'self-signed', 'cert': SELF_CERT, 'key': SELF_KEY, 'set_utc': _utc(), 'by': actor}, actor, 'tls.self_signed',
         {'names': facts['names'], 'not_after': facts['not_after'], 'sha256_fingerprint': facts['sha256_fingerprint'], 'bits': bits})
    return status(ws)


def use_site_certificate(ws, actor: str, cert_path: str, key_path: str) -> dict:
    """Serve with the site's own certificate and key, read from where the site keeps them at each start."""
    cert_path, key_path = os.path.abspath(cert_path.strip().strip('"')), os.path.abspath(key_path.strip().strip('"'))
    server_context(cert_path, key_path)
    with open(cert_path, 'rb') as f:
        facts = read_certificate(f.read())
    if facts.get('ca') and not facts.get('self_signed'):
        raise TlsError('that is a certificate authority\'s certificate, not a server\'s: use the server certificate the authority issued')
    _store_setting(ws, {'mode': 'site', 'cert': cert_path, 'key': key_path, 'set_utc': _utc(), 'by': actor}, actor, 'tls.site_certificate',
         {'cert': cert_path, 'subject': facts['subject'], 'issuer': facts['issuer'], 'not_after': facts['not_after'],
          'sha256_fingerprint': facts['sha256_fingerprint']})
    return status(ws)


def turn_off(ws, actor: str) -> dict:
    """Plain HTTP from the next start. The certificate files are left where they are."""
    was = setting(ws)
    _store_setting(ws, None, actor, 'tls.off', {'was': (was or {}).get('mode')})
    return status(ws)


def certificate_pem(ws) -> Optional[bytes]:
    """The certificate the service presents (public; what the site's computers are told to trust). Never the key."""
    s = setting(ws)
    if not s:
        return None
    with open(_abs(ws, s['cert']), 'rb') as f:
        data = f.read()
    blocks = pem_blocks(data, 'CERTIFICATE')
    return b''.join(_pem('CERTIFICATE', b) for b in blocks) or None


def text(st: dict) -> str:
    lines = [f"HTTPS: {st['state']} - {st['detail']}"]
    if st.get('configured'):
        lines.append(f"  mode {st.get('mode')}; certificate {st['cert']}; key {st['key']}; set {st.get('set_utc')} by {st.get('by')}")
    f = st.get('facts')
    if f:
        lines.append(f"  subject {f['subject']}; issuer {f['issuer']}; names {', '.join(f['names']) or '(none)'}")
        lines.append(f"  valid {f['not_before']} to {f['not_after']}; {f['key']} {f['bits'] or ''} bits; chain {f['chain']}")
        lines.append(f"  SHA-256 fingerprint {f['sha256_fingerprint']}")
    return '\n'.join(lines)
