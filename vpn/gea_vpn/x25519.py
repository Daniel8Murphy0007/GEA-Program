# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""x25519 - the key agreement WireGuard's keys use (RFC 7748), in plain Python, so GEA can make a member's key pair
where the WireGuard tools are not installed (the office PC before WireGuard is, the workspace that hands configs
out). The Montgomery ladder of the RFC, checked against its test vectors in the acceptance gate and against
`wg pubkey` where that tool exists. Keys are 32 bytes; WireGuard writes them base64.

Standard library only; integers, as ed25519.py does."""
from __future__ import annotations

import base64
import os

P = 2 ** 255 - 19
A24 = 121665
BASE = 9


def _decode_scalar(k: bytes) -> int:
    b = bytearray(k[:32])
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(bytes(b), 'little')


def _decode_u(u: bytes) -> int:
    b = bytearray(u[:32])
    b[31] &= 127
    return int.from_bytes(bytes(b), 'little') % P


def _encode_u(u: int) -> bytes:
    return (u % P).to_bytes(32, 'little')


def _ladder(k: int, u: int) -> int:
    x1 = u
    x2, z2 = 1, 0
    x3, z3 = u, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) % P
        x3 = x3 * x3 % P
        z3 = (da - cb) % P
        z3 = x1 * (z3 * z3) % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return x2 * pow(z2, P - 2, P) % P


def x25519(k: bytes, u: bytes) -> bytes:
    """The shared point: scalar k (32 bytes) times point u (32 bytes)."""
    return _encode_u(_ladder(_decode_scalar(k), _decode_u(u)))


def public_key(private: bytes) -> bytes:
    return x25519(private, _encode_u(BASE))


def generate_private() -> bytes:
    """A WireGuard private key: 32 random bytes, clamped as the RFC does (wg genkey does the same)."""
    b = bytearray(os.urandom(32))
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def keypair_b64() -> tuple:
    priv = generate_private()
    return base64.b64encode(priv).decode('ascii'), base64.b64encode(public_key(priv)).decode('ascii')


def preshared_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode('ascii')


def public_from_b64(private_b64: str) -> str:
    return base64.b64encode(public_key(base64.b64decode(private_b64))).decode('ascii')
