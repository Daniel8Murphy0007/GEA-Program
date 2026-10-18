"""Ed25519 signatures (RFC 8032), in the standard library alone.

The program signs its dossiers with Ed25519 so that anyone can check one without this program: the algorithm is the
RFC's, the code below follows the RFC's own reference implementation (section 6) in extended coordinates, and
the gate checks it against the RFC's test vectors. Signing and verifying take a few milliseconds each; nothing
here is secret but the 32-byte seed.

This file is also copied verbatim into every dossier's verifier, so it must stay self-contained.
"""
from __future__ import annotations

import hashlib

p = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p
SQRT_M1 = pow(2, (p - 1) // 4, p)


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _recover_x(y: int, sign: int):
    if y >= p:
        return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_gy = 4 * pow(5, p - 2, p) % p
_gx = _recover_x(_gy, 0)
G = (_gx, _gy, 1, _gx * _gy % p)


def _compress(P) -> bytes:
    zinv = pow(P[2], p - 2, p)
    x, y = P[0] * zinv % p, P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError('an Ed25519 secret key is 32 bytes')
    h = _sha512(seed)
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _compress(_mul(a, G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(_mul(a, G))
    r = int.from_bytes(_sha512(prefix + msg), 'little') % L
    R = _compress(_mul(r, G))
    h = int.from_bytes(_sha512(R + A + msg), 'little') % L
    s = (r + h * a) % L
    return R + int.to_bytes(s, 32, 'little')


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    if A is None:
        return False
    Rs = signature[:32]
    R = _decompress(Rs)
    if R is None:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= L:
        return False
    h = int.from_bytes(_sha512(Rs + public + msg), 'little') % L
    return _equal(_mul(s, G), _add(R, _mul(h, A)))


def fingerprint(public: bytes) -> str:
    """A short, readable fingerprint of a public key: the first 16 bytes of its SHA-256, in four-character groups."""
    h = hashlib.sha256(public).hexdigest()[:32]
    return ' '.join(h[i:i + 4] for i in range(0, 32, 4))
