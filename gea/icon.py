# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""icon - the program's icons, drawn in code. GEA-Program's: a derrick with its crown block on a rounded square, teal
while the control panel runs and grey while it does not. GEA Reader's (`kind='reader'`): a signed page - a white sheet
with a folded corner on a blue rounded square, a green seal with a check on it - so the client's program never looks
like the site's. `ico_bytes()` is the .ico an installer and its shortcuts use; `dib()` one image as the notification
area takes it. Standard library only and no imports from the program, so the
kit's build (tools/build_installer.py) can load this file on its own."""

from __future__ import annotations

import math
import struct
import zlib

TEAL = (14, 92, 110)
GREY = (110, 116, 122)
WHITE = (255, 255, 255)
AMBER = (245, 158, 11)
READER_BLUE = (27, 74, 140)
SEAL_GREEN = (22, 150, 72)
PAGE_FOLD = (196, 210, 230)
PAGE_LINE = (138, 154, 182)


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _derrick_segments():
    top, base = (0.5, 0.17), 0.84
    def lx(v): return 0.5 - (v - top[1]) / (base - top[1]) * 0.25
    def rx(v): return 1.0 - lx(v)
    a, b = 0.45, 0.66
    return [(top[0], top[1], lx(base), base), (top[0], top[1], rx(base), base), (0.19, base, 0.81, base),
            (lx(a), a, rx(a), a), (lx(b), b, rx(b), b), (lx(a), a, rx(b), b), (rx(a), a, lx(b), b)]


def _pixel(u: float, v: float, size: int, bg: tuple, segs) -> tuple:
    """(r, g, b, a) of one sample at (u, v) in the unit square: a rounded square of the state's colour, a white
    derrick, an amber crown block."""
    m, r = 0.04, 0.2
    qx, qy = abs(u - 0.5) - (0.5 - m - r), abs(v - 0.5) - (0.5 - m - r)
    out = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    if out > 0:
        return (0, 0, 0, 0)
    w = max(0.05, 1.15 / size)
    if math.hypot(u - 0.5, v - 0.15) <= max(0.075, 1.6 / size):
        return AMBER + (255,)
    if any(_seg_dist(u, v, *s) <= w / 2 for s in segs):
        return WHITE + (255,)
    shade = 1.0 - 0.18 * v
    return (int(bg[0] * shade), int(bg[1] * shade), int(bg[2] * shade), 255)


_CHECK = [(0.585, 0.725, 0.665, 0.805), (0.665, 0.805, 0.815, 0.645)]


def _reader_pixel(u: float, v: float, size: int, bg: tuple, segs) -> tuple:
    """GEA Reader: a white page with a folded corner and three lines of text, a green seal with a white check."""
    m, r = 0.04, 0.2
    qx, qy = abs(u - 0.5) - (0.5 - m - r), abs(v - 0.5) - (0.5 - m - r)
    if math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r > 0:
        return (0, 0, 0, 0)
    w = max(0.06, 1.3 / size)
    small = size < 28
    sr = 0.25 if small else 0.215
    dseal = math.hypot(u - 0.70, v - 0.72)
    if dseal <= sr:
        if any(_seg_dist(u, v, *c) <= (max(w * 0.62, 1.15 / size)) for c in _CHECK):
            return WHITE + (255,)
        if not small and dseal >= sr - max(0.025, 0.9 / size):
            return WHITE + (255,)                         # a white rim parts the seal from the page
        return SEAL_GREEN + (255,)
    x0, x1, y0, y1, c = 0.22, 0.70, 0.14, 0.86, 0.15
    if x0 <= u <= x1 and y0 <= v <= y1:
        cu, cv = u - (x1 - c), v - y0                     # the folded corner, top right
        if cu > 0 and cv < c:
            if cu > cv:
                return bg + (255,)
            return PAGE_FOLD + (255,)
        if size >= 24 and 0.29 <= u <= 0.60 and any(abs(v - yl) <= w * 0.42 for yl in (0.38, 0.50, 0.62)) and not (v > 0.55 and u > 0.48):
            return PAGE_LINE + (255,)
        return WHITE + (255,)
    shade = 1.0 - 0.18 * v
    return (int(bg[0] * shade), int(bg[1] * shade), int(bg[2] * shade), 255)


def draw(size: int, running: bool = True, kind: str = 'gea') -> list:
    """size x size RGBA rows (top row first), supersampled so the small sizes stay legible."""
    ss = 4 if size <= 64 else 2
    if kind == 'reader':
        bg, segs, pixel = READER_BLUE, None, _reader_pixel
    else:
        bg, segs, pixel = (TEAL if running else GREY), _derrick_segments(), _pixel
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            acc = [0, 0, 0, 0]
            for j in range(ss):
                for i in range(ss):
                    p = pixel((x + (i + 0.5) / ss) / size, (y + (j + 0.5) / ss) / size, size, bg, segs)
                    acc[0] += p[0] * p[3]; acc[1] += p[1] * p[3]; acc[2] += p[2] * p[3]; acc[3] += p[3]
            n = ss * ss
            a = acc[3] / n
            row.append((int(acc[0] / acc[3]) if acc[3] else 0, int(acc[1] / acc[3]) if acc[3] else 0,
                        int(acc[2] / acc[3]) if acc[3] else 0, int(round(a))))
        rows.append(row)
    return rows


def dib(size: int, running: bool = True, kind: str = 'gea') -> bytes:
    """One icon image as Windows keeps it in an .ico (and as CreateIconFromResourceEx takes it): a 32-bit
    BITMAPINFOHEADER image of twice the height, bottom row first, then an all-clear AND mask."""
    rows = draw(size, running, kind)
    mask_row = ((size + 31) // 32) * 4
    head = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, size * size * 4 + mask_row * size, 0, 0, 0, 0)
    px = b''.join(bytes((p[2], p[1], p[0], p[3])) for row in reversed(rows) for p in row)
    return head + px + b'\x00' * (mask_row * size)


def png(size: int, running: bool = True, kind: str = 'gea') -> bytes:
    rows = draw(size, running, kind)
    raw = b''.join(b'\x00' + b''.join(bytes(p) for p in row) for row in rows)
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def ico_bytes(sizes=ICO_SIZES, running: bool = True, kind: str = 'gea') -> bytes:
    """The .ico: every size as a DIB, 256 as a PNG (as Windows Vista and later read it)."""
    images = [(s, png(s, running, kind) if s >= 256 else dib(s, running, kind)) for s in sizes]
    out = struct.pack('<HHH', 0, 1, len(images))
    off = 6 + 16 * len(images)
    for s, data in images:
        out += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(data), off)
        off += len(data)
    return out + b''.join(d for _, d in images)


def read_ico(data: bytes) -> list:
    """[(width, height, bytes, kind)] of an .ico - what the check reads back."""
    z, typ, n = struct.unpack_from('<HHH', data, 0)
    if z != 0 or typ != 1 or n < 1:
        raise ValueError('not an icon file')
    out = []
    for i in range(n):
        w, h, _c, _r, planes, bpp, ln, off = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        blob = data[off:off + ln]
        if len(blob) != ln:
            raise ValueError(f'image {i} runs past the end of the file')
        kind = 'png' if blob[:8] == b'\x89PNG\r\n\x1a\n' else 'dib'
        if kind == 'dib':
            hs, bw, bh, _pl, bc = struct.unpack_from('<IiiHH', blob, 0)
            if hs != 40 or bw != (w or 256) or bh != 2 * (h or 256) or bc != 32:
                raise ValueError(f'image {i}: header {hs} {bw}x{bh} {bc}-bit does not match its entry')
        out.append((w or 256, h or 256, ln, kind))
    return out
