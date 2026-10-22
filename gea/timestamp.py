"""Trusted timestamps (RFC 3161): a public time-stamping authority's signed word that a dossier's signature existed at
a given time, so the dossier's time is not only this machine's clock.

What is sent: a TimeStampReq - the SHA-256 of the dossier's Ed25519 signature (32 bytes), a random nonce and a request
for the authority's certificate. Nothing else of the dossier leaves the machine. What comes back: a TimeStampResp whose
token is a CMS SignedData (RFC 5652) over a TSTInfo: the hash, the authority's time (genTime), its accuracy, a serial
number, the policy and the nonce.

The check, all of it here in the standard library:

1. the token reads, its content is a TSTInfo, and its message imprint is the hash of this signature (and the nonce is
   the one sent, when it is known);
2. the signed attributes bind it: their content type is a TSTInfo and their message digest is the hash of the TSTInfo's
   exact bytes;
3. the signature over the signed attributes verifies against the signer's certificate - RSA (PKCS #1 v1.5) or ECDSA on
   P-256, P-384 or P-521, with SHA-256, SHA-384 or SHA-512;
4. the signing-certificate attribute (ESS, RFC 2634 / RFC 5035) names that same certificate;
5. the certificate is a time-stamping one (extended key usage id-kp-timeStamping, critical) and was valid at the time;
6. its chain - each certificate signed by the next, each issuer a CA, each valid at the time - reaches a root this
   machine trusts (the operating system's store, as Python's ssl module reads it), or one the site gives.

A token that verifies but whose root this machine does not trust is reported as such: the time is then that
authority's word only for whoever trusts it. Revocation (CRL, OCSP) is not checked.

This file has no imports from the rest of the program: it rides, as it is, inside every dossier's checker.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

TS_OID = {
    'sha1': '1.3.14.3.2.26', 'sha256': '2.16.840.1.101.3.4.2.1', 'sha384': '2.16.840.1.101.3.4.2.2', 'sha512': '2.16.840.1.101.3.4.2.3',
    'signedData': '1.2.840.113549.1.7.2', 'tstInfo': '1.2.840.113549.1.9.16.1.4', 'contentType': '1.2.840.113549.1.9.3',
    'messageDigest': '1.2.840.113549.1.9.4', 'signingCertificate': '1.2.840.113549.1.9.16.2.12', 'signingCertificateV2': '1.2.840.113549.1.9.16.2.47',
    'rsaEncryption': '1.2.840.113549.1.1.1', 'ecPublicKey': '1.2.840.10045.2.1', 'timeStamping': '1.3.6.1.5.5.7.3.8',
    'extKeyUsage': '2.5.29.37', 'basicConstraints': '2.5.29.19', 'keyUsage': '2.5.29.15', 'commonName': '2.5.4.3', 'organization': '2.5.4.10',
}
TS_HASH_BY_OID = {v: k for k, v in TS_OID.items() if k in ('sha1', 'sha256', 'sha384', 'sha512')}
TS_SIG_ALGS = {   # signature algorithm OID -> (key kind, hash); rsaEncryption alone takes the signer's digest algorithm
    '1.2.840.113549.1.1.5': ('rsa', 'sha1'), '1.2.840.113549.1.1.11': ('rsa', 'sha256'), '1.2.840.113549.1.1.12': ('rsa', 'sha384'),
    '1.2.840.113549.1.1.13': ('rsa', 'sha512'), '1.2.840.113549.1.1.1': ('rsa', None),
    '1.2.840.10045.4.3.2': ('ec', 'sha256'), '1.2.840.10045.4.3.3': ('ec', 'sha384'), '1.2.840.10045.4.3.4': ('ec', 'sha512'),
}
TS_CURVES = {   # name OID -> (p, a, b, Gx, Gy, n) of the NIST prime curves (FIPS 186-4, D.1.2)
    '1.2.840.10045.3.1.7': (0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff, -3,
                            0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b,
                            0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
                            0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5,
                            0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551),
    '1.3.132.0.34': (int('fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffeffffffff0000000000000000ffffffff', 16), -3,
                     int('b3312fa7e23ee7e4988e056be3f82d19181d9c6efe8141120314088f5013875ac656398d8a2ed19d2a85c8edd3ec2aef', 16),
                     int('aa87ca22be8b05378eb1c71ef320ad746e1d3b628ba79b9859f741e082542a385502f25dbf55296c3a545e3872760ab7', 16),
                     int('3617de4a96262c6f5d9e98bf9292dc29f8f41dbd289a147ce9da3113b5f0b8c00a60b1ce1d7e819d7a431d7c90ea0e5f', 16),
                     int('ffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf581a0db248b0a77aecec196accc52973', 16)),
    '1.3.132.0.35': (2 ** 521 - 1, -3,
                     int('0051953eb9618e1c9a1f929a21a0b68540eea2da725b99b315f3b8b489918ef109e156193951ec7e937b1652c0bd3bb1bf073573df883d2c34f1ef451fd46b503f00', 16),
                     int('00c6858e06b70404e9cd9e3ecb662395b4429c648139053fb521f828af606b4d3dbaa14b5e77efe75928fe1dc127a2ffa8de3348b3c1856a429bf97e7e31c2e5bd66', 16),
                     int('011839296a789a3bc0045c8a5fb42c7d1bd998f54449579b446817afbd17273e662c97ee72995ef42640c550b9013fad0761353c7086a272c24088be94769fd16650', 16),
                     int('01fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffa51868783bf2f966b7fcc0148f709a5d03bb5c9b8899c47aebb6fb71e91386409', 16)),
}
TS_DIGEST_INFO = {   # the DER prefix of PKCS #1 v1.5's DigestInfo for each hash (RFC 8017, 9.2)
    'sha1': bytes.fromhex('3021300906052b0e03021a05000414'), 'sha256': bytes.fromhex('3031300d060960864801650304020105000420'),
    'sha384': bytes.fromhex('3041300d060960864801650304020205000430'), 'sha512': bytes.fromhex('3051300d060960864801650304020305000440'),
}
TS_STATUS = {0: 'granted', 1: 'granted with modifications', 2: 'rejection', 3: 'waiting', 4: 'revocation warning', 5: 'revocation notification'}


class TimestampError(ValueError):
    pass


# -- DER ----------------------------------------------------------------------------------------------------------------
def _ts_rd(b: bytes, i: int) -> Tuple[int, bytes, int, int]:
    """One element at i: (tag, contents, the index after it, the index its contents start at)."""
    if i + 2 > len(b):
        raise TimestampError('the token ends in the middle of an element')
    tag, ln = b[i], b[i + 1]
    j = i + 2
    if ln & 0x80:
        k = ln & 0x7F
        if not 1 <= k <= 4:
            raise TimestampError('an element length this reader does not take')
        ln = int.from_bytes(b[j:j + k], 'big')
        j += k
    if j + ln > len(b):
        raise TimestampError('the token ends in the middle of an element')
    return tag, b[j:j + ln], j + ln, j


def _ts_kids(b: bytes) -> List[Tuple[int, bytes, bytes]]:
    """The elements inside a constructed one: (tag, contents, the whole element's bytes)."""
    out, i = [], 0
    while i < len(b):
        t, v, j, _ = _ts_rd(b, i)
        out.append((t, v, b[i:j]))
        i = j
    return out


def _ts_one(b: bytes) -> Tuple[int, bytes]:
    t, v, j, _ = _ts_rd(b, 0)
    if j != len(b):
        raise TimestampError('bytes after the end of the structure')
    return t, v


def _ts_oid(v: bytes) -> str:
    parts, x = [], 0
    for c in v:
        x = (x << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(x); x = 0
    first = parts[0]
    head = [min(first // 40, 2), first - 40 * min(first // 40, 2)]
    return '.'.join(map(str, head + parts[1:]))


def _ts_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    s = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return bytes([0x80 | len(s)]) + s


def _ts_tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _ts_len(len(body)) + body


def _ts_enc_oid(dotted: str) -> bytes:
    a = [int(x) for x in dotted.split('.')]
    out = bytearray([40 * a[0] + a[1]])
    for n in a[2:]:
        s = [n & 0x7F]
        n >>= 7
        while n:
            s.append(0x80 | (n & 0x7F)); n >>= 7
        out += bytes(reversed(s))
    return _ts_tlv(0x06, bytes(out))


def _ts_enc_int(x: int) -> bytes:
    b = x.to_bytes(max(1, (x.bit_length() + 8) // 8), 'big')
    return _ts_tlv(0x02, b)


def _ts_int(v: bytes) -> int:
    return int.from_bytes(v, 'big', signed=True)


def _ts_time(tag: int, v: bytes) -> datetime:
    """UTCTime (0x17) or GeneralizedTime (0x18), with or without fractional seconds, as a UTC datetime."""
    s = v.decode('ascii').rstrip('Z')
    if tag == 0x17:
        yy = int(s[:2]); s = ('20' if yy < 50 else '19') + s
    frac = 0
    if '.' in s:
        s, f = s.split('.', 1)
        frac = int((f + '000000')[:6])
    return datetime.strptime(s[:14], '%Y%m%d%H%M%S').replace(microsecond=frac, tzinfo=timezone.utc)


def _ts_name(v: bytes) -> str:
    """A distinguished name's common name, or its organisation."""
    cn = org = ''
    for _, rdn, _ in _ts_kids(v):
        for _, atv, _ in _ts_kids(rdn):
            k = _ts_kids(atv)
            if len(k) == 2:
                oid = _ts_oid(k[0][1])
                if oid == TS_OID['commonName']:
                    cn = k[1][1].decode('utf-8', 'replace')
                elif oid == TS_OID['organization']:
                    org = k[1][1].decode('utf-8', 'replace')
    return cn or org or '(unnamed)'


# -- the request and the response -------------------------------------------------------------------------------------------
def ts_request(digest: bytes, hash_name: str = 'sha256', nonce: Optional[int] = None) -> Tuple[bytes, int]:
    """A TimeStampReq (RFC 3161, 2.4.1) for a hash, asking for the authority's certificate: (DER, the nonce)."""
    if hash_name not in TS_DIGEST_INFO or len(digest) != hashlib.new(hash_name).digest_size:
        raise TimestampError(f'a {hash_name} hash is {hashlib.new(hash_name).digest_size} bytes')
    nonce = secrets.randbits(63) if nonce is None else int(nonce)
    alg = _ts_tlv(0x30, _ts_enc_oid(TS_OID[hash_name]) + _ts_tlv(0x05, b''))
    imprint = _ts_tlv(0x30, alg + _ts_tlv(0x04, digest))
    return _ts_tlv(0x30, _ts_enc_int(1) + imprint + _ts_enc_int(nonce) + _ts_tlv(0x01, b'\xff')), nonce


def ts_fetch(url: str, request_der: bytes, timeout: float = 30.0) -> bytes:
    """POST the request to the authority (application/timestamp-query) and return its reply's bytes."""
    import urllib.request
    if not str(url).lower().startswith(('http://', 'https://')):
        raise TimestampError('the time-stamping service is an http:// or https:// address')
    rq = urllib.request.Request(url, data=request_der, method='POST',
                                headers={'Content-Type': 'application/timestamp-query', 'Accept': 'application/timestamp-reply',
                                         'User-Agent': 'GEA-Program'})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        body = r.read(1 << 20)
        ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if ctype and ctype not in ('application/timestamp-reply', 'application/timestamp-response', 'application/octet-stream'):
        raise TimestampError(f'the service answered {ctype}, not a time-stamp reply (is the address right?)')
    return body


def ts_read_response(der: bytes) -> dict:
    """A TimeStampResp: its status and, when granted, the token (a ContentInfo's DER)."""
    t, v = _ts_one(der)
    if t != 0x30:
        raise TimestampError('the reply is not a time-stamp response')
    k = _ts_kids(v)
    st = _ts_kids(k[0][1])
    status = _ts_int(st[0][1])
    text = ''
    for tt, vv, _ in st[1:]:
        if tt == 0x30:
            text = '; '.join(x[1].decode('utf-8', 'replace') for x in _ts_kids(vv))
    out = {'status': status, 'status_text': TS_STATUS.get(status, str(status)), 'detail': text, 'token': k[1][2] if len(k) > 1 else None}
    if status not in (0, 1) or out['token'] is None:
        raise TimestampError(f"the service did not grant a time stamp: {out['status_text']}" + (f' ({text})' if text else ''))
    return out


# -- certificates -------------------------------------------------------------------------------------------------------------
def ts_certificate(der: bytes) -> dict:
    """The parts of an X.509 certificate a timestamp check needs."""
    t, body = _ts_one(der)
    c = _ts_kids(body)
    if t != 0x30 or len(c) != 3:
        raise TimestampError('a certificate in the token is not X.509')
    tbs = _ts_kids(c[0][1])
    k = 1 if tbs[0][0] == 0xA0 else 0
    serial, issuer, validity, subject, spki = tbs[k], tbs[k + 2], tbs[k + 3], tbs[k + 4], tbs[k + 5]
    (t1, v1, _), (t2, v2, _) = _ts_kids(validity[1])
    out = {'der': der, 'tbs': c[0][2], 'sig_alg': _ts_oid(_ts_kids(c[1][1])[0][1]), 'signature': c[2][1][1:],
           'serial': _ts_int(serial[1]), 'issuer': issuer[2], 'subject': subject[2], 'issuer_name': _ts_name(issuer[1]),
           'subject_name': _ts_name(subject[1]), 'not_before': _ts_time(t1, v1), 'not_after': _ts_time(t2, v2),
           'ca': False, 'eku': [], 'eku_critical': False, 'key_cert_sign': None, 'sha256': hashlib.sha256(der).hexdigest()}
    alg = _ts_kids(spki[1])
    aid = _ts_kids(alg[0][1])
    kind = _ts_oid(aid[0][1])
    bits = alg[1][1][1:]
    if kind == TS_OID['rsaEncryption']:
        rsa = _ts_kids(_ts_one(bits)[1])
        out['key'] = ('rsa', int.from_bytes(rsa[0][1], 'big'), int.from_bytes(rsa[1][1], 'big'))
    elif kind == TS_OID['ecPublicKey']:
        out['key'] = ('ec', _ts_oid(aid[1][1]) if len(aid) > 1 and aid[1][0] == 0x06 else '', bits)
    else:
        out['key'] = ('unknown', kind)
    for tt, v, _ in tbs[k + 6:]:
        if tt != 0xA3:
            continue
        for _, ext, _ in _ts_kids(_ts_kids(v)[0][1]):
            parts = _ts_kids(ext)
            oid, crit, value = _ts_oid(parts[0][1]), (len(parts) == 3 and parts[1][1] != b'\x00'), parts[-1][1]
            if oid == TS_OID['extKeyUsage']:
                out['eku'] = [_ts_oid(x[1]) for x in _ts_kids(_ts_one(value)[1])]
                out['eku_critical'] = crit
            elif oid == TS_OID['basicConstraints']:
                inner = _ts_kids(_ts_one(value)[1])
                out['ca'] = bool(inner and inner[0][0] == 0x01 and inner[0][1] != b'\x00')
            elif oid == TS_OID['keyUsage']:
                bs = _ts_one(value)[1]
                out['key_cert_sign'] = bool(len(bs) > 1 and bs[1] & 0x04)
    return out


def _ts_rsa_ok(key, hash_name: str, msg: bytes, sig: bytes) -> bool:
    _, n, e = key
    kl = (n.bit_length() + 7) // 8
    if len(sig) != kl or hash_name not in TS_DIGEST_INFO:
        return False
    m = pow(int.from_bytes(sig, 'big'), e, n).to_bytes(kl, 'big')
    t = TS_DIGEST_INFO[hash_name] + hashlib.new(hash_name, msg).digest()
    return kl >= len(t) + 11 and m == b'\x00\x01' + b'\xff' * (kl - len(t) - 3) + b'\x00' + t


def _ts_ec_add(P, Q, cv):
    p_, a_ = cv[0], cv[1]
    if P is None:
        return Q
    if Q is None:
        return P
    if P[0] == Q[0]:
        if (P[1] + Q[1]) % p_ == 0:
            return None
        lam = (3 * P[0] * P[0] + a_) * pow(2 * P[1], -1, p_) % p_
    else:
        lam = (Q[1] - P[1]) * pow(Q[0] - P[0], -1, p_) % p_
    x = (lam * lam - P[0] - Q[0]) % p_
    return (x, (lam * (P[0] - x) - P[1]) % p_)


def _ts_ec_mul(k: int, P, cv):
    R = None
    while k:
        if k & 1:
            R = _ts_ec_add(R, P, cv)
        P = _ts_ec_add(P, P, cv)
        k >>= 1
    return R


def _ts_ec_ok(key, hash_name: str, msg: bytes, sig: bytes) -> bool:
    _, curve, point = key
    cv = TS_CURVES.get(curve)
    if not cv or hash_name not in TS_DIGEST_INFO:
        return False
    p_, a_, b_, gx, gy, n = cv
    size = (p_.bit_length() + 7) // 8
    if point[:1] == b'\x04' and len(point) == 1 + 2 * size:
        Q = (int.from_bytes(point[1:1 + size], 'big'), int.from_bytes(point[1 + size:], 'big'))
    elif point[:1] in (b'\x02', b'\x03') and len(point) == 1 + size:
        x = int.from_bytes(point[1:], 'big')
        y = pow((x ** 3 + a_ * x + b_) % p_, (p_ + 1) // 4, p_)
        if (y & 1) != (point[0] & 1):
            y = p_ - y
        Q = (x, y)
    else:
        return False
    if (Q[1] * Q[1] - (Q[0] ** 3 + a_ * Q[0] + b_)) % p_:
        return False
    try:
        rs = _ts_kids(_ts_one(sig)[1])
        r, s = _ts_int(rs[0][1]), _ts_int(rs[1][1])
    except (TimestampError, IndexError):
        return False
    if not (0 < r < n and 0 < s < n):
        return False
    h = hashlib.new(hash_name, msg).digest()
    e = int.from_bytes(h, 'big')
    if len(h) * 8 > n.bit_length():
        e >>= len(h) * 8 - n.bit_length()
    w = pow(s, -1, n)
    X = _ts_ec_add(_ts_ec_mul(e * w % n, (gx, gy), cv), _ts_ec_mul(r * w % n, Q, cv), cv)
    return X is not None and X[0] % n == r


def _ts_sig_ok(key, alg_oid: str, digest_alg: Optional[str], msg: bytes, sig: bytes) -> Tuple[bool, str]:
    kind, h = TS_SIG_ALGS.get(alg_oid, (None, None))
    h = h or digest_alg
    if kind is None or h is None:
        return False, f'signature algorithm {alg_oid} is not one this check reads (RSA or ECDSA with SHA-1/2)'
    if kind != key[0]:
        return False, 'the signature algorithm does not match the certificate\'s key'
    ok_ = _ts_rsa_ok(key, h, msg, sig) if kind == 'rsa' else _ts_ec_ok(key, h, msg, sig)
    return ok_, f"{'RSA' if kind == 'rsa' else 'ECDSA ' + {'1.2.840.10045.3.1.7': 'P-256', '1.3.132.0.34': 'P-384', '1.3.132.0.35': 'P-521'}.get(key[1], key[1])} with {h.upper()}"


def ts_system_anchors() -> List[bytes]:
    """The root certificates this machine trusts, as Python's ssl module reads the operating system's store (on Windows
    its ROOT and CA stores), and the default CA file where there is one."""
    out: List[bytes] = []
    try:
        ctx = ssl.create_default_context()
        out += [bytes(c) for c in ctx.get_ca_certs(binary_form=True)]
    except (ssl.SSLError, OSError):
        pass
    try:
        caf = ssl.get_default_verify_paths().cafile
        if caf and os.path.isfile(caf):
            out += ts_pem_certs(open(caf, 'rb').read())
    except OSError:
        pass
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d); uniq.append(d)
    return uniq


def ts_pem_certs(pem: bytes) -> List[bytes]:
    import base64
    t = pem.decode('ascii', 'replace')
    out, start = [], '-----BEGIN CERTIFICATE-----'
    i = t.find(start)
    while i >= 0:
        j = t.find('-----END CERTIFICATE-----', i)
        if j < 0:
            break
        out.append(base64.b64decode(''.join(t[i + len(start):j].split())))
        i = t.find(start, j)
    return out


# -- the token ----------------------------------------------------------------------------------------------------------------
def ts_read_token(token: bytes) -> dict:
    """A time-stamp token (a ContentInfo holding a CMS SignedData over a TSTInfo): the TSTInfo's fields, the certificates
    it carries and its signer's information."""
    t, ci = _ts_one(token)
    k = _ts_kids(ci)
    if t != 0x30 or _ts_oid(k[0][1]) != TS_OID['signedData']:
        raise TimestampError('the token is not a CMS SignedData')
    sd = _ts_kids(_ts_one(k[1][1])[1])
    i = 0
    i += 1                                                     # version
    i += 1                                                     # digestAlgorithms
    eci = _ts_kids(sd[i][1]); i += 1
    if _ts_oid(eci[0][1]) != TS_OID['tstInfo']:
        raise TimestampError('the token does not hold a TSTInfo')
    tst_der = _ts_one(eci[1][1])[1]                            # [0] EXPLICIT OCTET STRING
    certs: List[bytes] = []
    while i < len(sd) and sd[i][0] in (0xA0, 0xA1):
        if sd[i][0] == 0xA0:
            certs += [whole for tt, _, whole in _ts_kids(sd[i][1]) if tt == 0x30]
        i += 1
    signers = _ts_kids(sd[i][1])
    if len(signers) != 1:
        raise TimestampError('the token has more or fewer than one signer')
    si = _ts_kids(signers[0][1])
    j = 1
    sid = si[j]; j += 1
    digest_alg = TS_HASH_BY_OID.get(_ts_oid(_ts_kids(si[j][1])[0][1])); j += 1
    signed_attrs_whole = signed_attrs = None
    if si[j][0] == 0xA0:
        signed_attrs, signed_attrs_whole = si[j][1], si[j][2]; j += 1
    sig_alg = _ts_oid(_ts_kids(si[j][1])[0][1]); j += 1
    signature = si[j][1]
    tk = _ts_kids(_ts_one(tst_der)[1])
    imp = _ts_kids(tk[2][1])
    tst = {'version': _ts_int(tk[0][1]), 'policy': _ts_oid(tk[1][1]), 'hash': TS_HASH_BY_OID.get(_ts_oid(_ts_kids(imp[0][1])[0][1])),
           'imprint': imp[1][1], 'serial': _ts_int(tk[3][1]), 'gen_time': _ts_time(tk[4][0], tk[4][1]), 'accuracy_s': None,
           'ordering': False, 'nonce': None, 'tsa': None}
    for tt, vv, whole in tk[5:]:
        if tt == 0x30:                                          # accuracy: seconds, [0] millis, [1] micros
            acc = 0.0
            for at, av, _ in _ts_kids(vv):
                acc += _ts_int(av) * {0x02: 1.0, 0x80: 1e-3, 0x81: 1e-6}.get(at, 0.0)
            tst['accuracy_s'] = acc
        elif tt == 0x01:
            tst['ordering'] = vv != b'\x00'
        elif tt == 0x02:
            tst['nonce'] = _ts_int(vv)
        elif tt == 0xA0:
            try:
                g = _ts_kids(vv)[0]
                tst['tsa'] = _ts_name(g[1]) if g[0] == 0xA4 else None
            except (TimestampError, IndexError):
                pass
    attrs = {}
    for _, av, _ in _ts_kids(signed_attrs or b''):
        a = _ts_kids(av)
        attrs[_ts_oid(a[0][1])] = [x[1] for x in _ts_kids(a[1][1])]
        attrs[_ts_oid(a[0][1]) + '#whole'] = [x[2] for x in _ts_kids(a[1][1])]
    if sid[0] == 0x30:
        s_ = _ts_kids(sid[1])
        signer_id = ('issuer_serial', s_[0][2], _ts_int(s_[1][1]))
    else:
        signer_id = ('ski', sid[1], None)
    return {'tst': tst, 'tst_der': tst_der, 'certs': certs, 'digest_alg': digest_alg, 'sig_alg': sig_alg, 'signature': signature,
            'signed_attrs': (b'\x31' + signed_attrs_whole[1:]) if signed_attrs_whole else None, 'attrs': attrs, 'signer_id': signer_id}


def _ts_ski(c: dict) -> Optional[bytes]:
    t, body = _ts_one(c['der'])
    tbs = _ts_kids(_ts_kids(body)[0][1])
    for tt, v, _ in tbs:
        if tt == 0xA3:
            for _, ext, _ in _ts_kids(_ts_kids(v)[0][1]):
                parts = _ts_kids(ext)
                if _ts_oid(parts[0][1]) == '2.5.29.14':
                    return _ts_one(parts[-1][1])[1]
    return None


def ts_verify(token: bytes, digest: bytes, hash_name: str = 'sha256', nonce: Optional[int] = None,
              anchors: Optional[List[bytes]] = None, extra_certs: Optional[List[bytes]] = None) -> dict:
    """Check a time-stamp token against the hash it should cover: every step, the authority's time and name, and
    whether its chain reaches a root this machine (or the given anchors) trusts. status: TRUSTED, VERIFIED (the root
    not trusted here) or INVALID."""
    out = {'status': 'INVALID', 'checks': [], 'gen_time': None, 'tsa': None, 'serial': None, 'policy': None, 'accuracy_s': None, 'chain': [], 'root': None}

    def add(name, ok_, detail):
        out['checks'].append({'check': name, 'ok': bool(ok_), 'detail': detail})
        return ok_
    try:
        tk = ts_read_token(token)
    except (TimestampError, IndexError, ValueError) as e:
        add('the token reads', False, str(e))
        return out
    tst = tk['tst']
    out.update({'gen_time': tst['gen_time'].strftime('%Y-%m-%dT%H:%M:%S') + (f".{tst['gen_time'].microsecond:06d}".rstrip('0') if tst['gen_time'].microsecond else '') + 'Z',
                'serial': tst['serial'], 'policy': tst['policy'], 'accuracy_s': tst['accuracy_s'], 'nonce': tst['nonce']})
    add('the token reads', True, f"a TSTInfo, serial {tst['serial']}, policy {tst['policy']}")
    add('it covers this signature', tst['hash'] == hash_name and tst['imprint'] == digest,
        f"the {tst['hash']} it carries is this signature's" if tst['imprint'] == digest else 'the hash it carries is not this signature\'s')
    if nonce is not None:
        add('the nonce is the one sent', tst['nonce'] == nonce, 'the same' if tst['nonce'] == nonce else 'a different nonce: not the reply to this request')
    pool = [ts_certificate(d) for d in tk['certs'] + list(extra_certs or [])]
    kind, a, b = tk['signer_id']
    signer = next((c for c in pool if (kind == 'issuer_serial' and c['issuer'] == a and c['serial'] == b) or (kind == 'ski' and _ts_ski(c) == a)), None)
    if signer is None:
        add('the signer\'s certificate is in the token', False, 'the token does not carry the certificate that signed it')
        return out
    out['tsa'] = signer['subject_name']
    attrs = tk['attrs']
    ct = attrs.get(TS_OID['contentType'], [b''])[0]
    md = attrs.get(TS_OID['messageDigest'], [b''])[0]
    dig = hashlib.new(tk['digest_alg'], tk['tst_der']).digest() if tk['digest_alg'] else b''
    add('the signed attributes bind the TSTInfo', tk['signed_attrs'] is not None and _ts_oid(ct) == TS_OID['tstInfo'] and md == dig,
        'its content type and message digest are the TSTInfo\'s' if md == dig else 'the message digest is not the TSTInfo\'s')
    good, how = _ts_sig_ok(signer['key'], tk['sig_alg'], tk['digest_alg'], tk['signed_attrs'] or b'', tk['signature'])
    add('the authority\'s signature verifies', good, f"{how}, by {signer['subject_name']}" if good else how if 'not one' in how else f'it does not verify ({how})')
    ess = None
    if TS_OID['signingCertificateV2'] + '#whole' in attrs:
        v2 = _ts_kids(attrs[TS_OID['signingCertificateV2'] + '#whole'][0])[0]
        ids = _ts_kids(_ts_kids(v2[1])[0][1]) if v2[0] == 0x30 else []
        first = _ts_kids(ids[0][1]) if ids else []
        h_name, h_val = 'sha256', None
        for tt, vv, _ in first:
            if tt == 0x30 and h_val is None:                    # the hash algorithm comes before the hash; the issuer and serial after
                h_name = TS_HASH_BY_OID.get(_ts_oid(_ts_kids(vv)[0][1]), 'sha256')
            elif tt == 0x04 and h_val is None:
                h_val = vv
        ess = h_val is not None and hashlib.new(h_name, signer['der']).digest() == h_val
    elif TS_OID['signingCertificate'] + '#whole' in attrs:
        v1 = _ts_kids(attrs[TS_OID['signingCertificate'] + '#whole'][0])[0]
        ids = _ts_kids(_ts_kids(v1[1])[0][1])
        ess = hashlib.sha1(signer['der']).digest() == _ts_kids(ids[0][1])[0][1]
    add('the signing-certificate attribute names it', ess is True, 'it does' if ess else ('no signing-certificate attribute' if ess is None else 'it names another certificate'))
    when = tst['gen_time']
    add('a time-stamping certificate, valid at the time', TS_OID['timeStamping'] in signer['eku'] and signer['eku_critical']
        and signer['not_before'] <= when <= signer['not_after'],
        (f"{signer['subject_name']}: time stamping only, valid {signer['not_before']:%Y-%m-%d} to {signer['not_after']:%Y-%m-%d}"
         if TS_OID['timeStamping'] in signer['eku'] and signer['eku_critical'] else 'the certificate is not marked for time stamping only (RFC 3161 2.3)')
        + ('' if signer['not_before'] <= when <= signer['not_after'] else '; not valid at the time stamped'))
    crypto_ok = all(c['ok'] for c in out['checks'])
    # the chain: from the signer up, through the token's certificates, to a trusted root
    roots = [ts_certificate(d) for d in (ts_system_anchors() if anchors is None else anchors)]
    cur, chain, seen, trusted, broken = signer, [signer['subject_name']], {signer['sha256']}, None, ''
    for _ in range(8):
        root = next((r for r in roots if r['subject'] == cur['issuer'] and _ts_sig_ok(r['key'], cur['sig_alg'], None, cur['tbs'], cur['signature'])[0]), None)
        if root is not None:
            if not (root['not_before'] <= when <= root['not_after']) and root['sha256'] != cur['sha256']:
                broken = f"the root {root['subject_name']} was not valid at the time"
                break
            trusted = root
            if root['sha256'] != cur['sha256']:
                chain.append(root['subject_name'])
            break
        up = next((c for c in pool if c['subject'] == cur['issuer'] and c['sha256'] not in seen), None)
        if up is None:
            broken = f"{cur['issuer_name']}, which issued {cur['subject_name']}, is not in the token and not trusted here"
            break
        if not _ts_sig_ok(up['key'], cur['sig_alg'], None, cur['tbs'], cur['signature'])[0]:
            broken = f"{cur['subject_name']} is not signed by {up['subject_name']}"
            break
        if not up['ca'] or up['key_cert_sign'] is False or not (up['not_before'] <= when <= up['not_after']):
            broken = f"{up['subject_name']} is not a certificate authority valid at the time"
            break
        chain.append(up['subject_name']); seen.add(up['sha256']); cur = up
    out['chain'] = chain
    out['root'] = trusted['subject_name'] if trusted else None
    out['chain_detail'] = (f"{' -> '.join(chain)}: a root this machine trusts" if trusted else (broken or 'the chain is longer than this check follows'))
    out['status'] = 'INVALID' if not crypto_ok else ('TRUSTED' if trusted else 'VERIFIED')
    return out


def ts_text(r: dict) -> str:
    L = [f"Trusted timestamp: {r['status']}" + (f" - {r['gen_time']} by {r['tsa']}" if r.get('gen_time') and r.get('tsa') else '')]
    for c in r['checks']:
        L.append(f"  {'ok ' if c['ok'] else 'NO '} {c['check']}: {c['detail']}")
    if r.get('chain_detail'):
        L.append(f"  {'ok ' if r.get('root') else 'NO '} the chain: {r['chain_detail']}")
    return '\n'.join(L)
