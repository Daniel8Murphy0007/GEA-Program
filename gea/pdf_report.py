"""The printable copy of a client report: a PDF written beside the HTML, from the same document, with nothing but
the standard library.

A report is written as markdown, HTML and JSON; a site that prints it, files it with a regulator or attaches it to
an email wants a page that looks the same everywhere. This module lays the report's document out on US Letter
pages (portrait, 0.75 in margins) in the PDF base fonts every reader carries (Helvetica and its bold and oblique,
so nothing is embedded): the title, the front table, each section's paragraphs, its drawings (the program's own SVG,
redrawn as PDF vector paths: lines, rectangles, paths of straight segments, circles and text) and its tables, which
break across pages with their header row repeated. Every page carries the report's title and number and "page n of
N". The text is the HTML's text, so the internal-register check `client_reports.write` makes covers it.

The format: PDF 1.4 (ISO 32000-1 reads it), one content stream per page, compressed with zlib's deflate
(FlateDecode), the standard 14-font metrics for the widths (Adobe's AFM files for Helvetica and Helvetica-Bold), and
WinAnsiEncoding for the characters - the Latin-1 letters, the degree sign, plus-minus, the dashes and quotes.
A character outside it is written as its nearest plain equivalent (>= for the greater-or-equal sign) or '?'.
The output is the same bytes for the same document: no creation time is stamped, so a report rewritten unchanged
hashes the same.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zlib
from typing import List, Optional, Tuple

PAGE_W, PAGE_H = 612.0, 792.0          # US Letter, points
MARGIN = 54.0                          # 0.75 in
BODY_W = PAGE_W - 2 * MARGIN
TOP = PAGE_H - MARGIN
BOTTOM = MARGIN + 18.0                 # room for the page footer

# Adobe AFM advance widths (1/1000 em), character codes 32-126
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
         556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556,
         556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_HELV_B = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
           556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
           667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611,
           611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
# WinAnsi codes 128-255 that reports use, regular and bold widths; the rest take the width of a digit
_HI = {0x80: (556, 556), 0x85: (1000, 1000), 0x91: (222, 278), 0x92: (222, 278), 0x93: (333, 500), 0x94: (333, 500),
       0x95: (350, 350), 0x96: (556, 556), 0x97: (1000, 1000), 0x99: (1000, 1000), 0xA0: (278, 278), 0xA9: (737, 737),
       0xAE: (737, 737), 0xB0: (400, 400), 0xB1: (584, 584), 0xB2: (333, 333), 0xB3: (333, 333), 0xB5: (556, 611),
       0xB7: (278, 278), 0xB9: (333, 333), 0xBC: (834, 834), 0xBD: (834, 834), 0xBE: (834, 834), 0xD7: (584, 584), 0xF7: (584, 584)}
_SUBST = {'\u2265': '>=', '\u2264': '<=', '\u2212': '-', '\u2248': '~', '\u221d': '~', '\u2192': '->', '\u2190': '<-', '\u2194': '<->',
          '\u2026': '...', '\u00a0': ' ', '\u2009': ' ', '\u202f': ' ', '\u2010': '-', '\u2011': '-', '\u2032': "'", '\u2033': '"',
          '\u03bc': '\u00b5', '\u2003': ' ', '\u2002': ' ', '\u200b': '', '\u2713': 'yes', '\u2717': 'no', '\u2022': '\u2022',
          '\u0394': 'delta ', '\u03c3': 'sigma', '\u03c1': 'rho', '\u03b1': 'alpha', '\u03b2': 'beta', '\u03bb': 'lambda',
          '\u03c6': 'phi', '\u03b8': 'theta', '\u2032\u2032': '"', '\u2219': '\u00b7', '\u22c5': '\u00b7', '\u00b7': '\u00b7'}
FONTS = {'F1': 'Helvetica', 'F2': 'Helvetica-Bold', 'F3': 'Helvetica-Oblique', 'F4': 'Helvetica-BoldOblique'}


def to_winansi(s: str) -> bytes:
    """The text in WinAnsiEncoding, each character outside it replaced by its plain equivalent or '?'."""
    out = []
    for ch in str(s):
        ch = _SUBST.get(ch, ch)
        for c in ch:
            try:
                out.append(c.encode('cp1252'))
            except UnicodeEncodeError:
                out.append(b'?')
    return b''.join(out)


def width(s, size: float, bold: bool = False) -> float:
    """The advance width of the text in points at the size."""
    b = s if isinstance(s, bytes) else to_winansi(s)
    tab = _HELV_B if bold else _HELV
    w = 0
    for c in b:
        if 32 <= c <= 126:
            w += tab[c - 32]
        else:
            w += _HI.get(c, (556, 556))[1 if bold else 0]
    return w * size / 1000.0


def _lit(b: bytes) -> str:
    """A PDF literal string of the WinAnsi bytes (latin-1 carries them through the content stream unchanged)."""
    return '(' + b.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)').replace(b'\r', b'\\r').replace(b'\n', b' ').decode('latin-1') + ')'


def wrap(text: str, size: float, w: float, bold: bool = False) -> List[bytes]:
    """The text broken into lines no wider than w: at spaces, and inside a word only when the word alone is wider."""
    lines: List[bytes] = []
    for para in str(text).split('\n'):
        words = to_winansi(para).split(b' ')
        cur = b''
        for wd in words:
            cand = cur + b' ' + wd if cur else wd
            if width(cand, size, bold) <= w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = b''
            while width(wd, size, bold) > w and len(wd) > 1:      # a word wider than the column: break it
                k = len(wd)
                while k > 1 and width(wd[:k], size, bold) > w:
                    k -= 1
                lines.append(wd[:k]); wd = wd[k:]
            cur = wd
        lines.append(cur)
    return lines or [b'']


def _rgb(c: Optional[str], opacity: float = 1.0) -> Optional[Tuple[float, float, float]]:
    """A colour as PDF rgb; an opacity below one is mixed toward the white page (the drawings sit on white)."""
    if not c or c == 'none':
        return None
    c = c.strip()
    named = {'black': '#000000', 'white': '#ffffff', 'red': '#ff0000', 'green': '#008000', 'blue': '#0000ff', 'gray': '#808080',
             'grey': '#808080', 'orange': '#ffa500', 'currentcolor': '#000000'}
    c = named.get(c.lower(), c)
    m = re.match(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$', c)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = ''.join(x * 2 for x in h)
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    else:
        m = re.match(r'^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', c)
        if not m:
            return (0.0, 0.0, 0.0)
        r, g, b = (float(m.group(i)) / 255.0 for i in (1, 2, 3))
    a = max(0.0, min(1.0, opacity))
    return tuple(round(1.0 - a * (1.0 - v), 4) for v in (r, g, b))


def _num(v, d: float = 0.0) -> float:
    try:
        return float(re.match(r'^\s*(-?[\d.]+(?:e-?\d+)?)', str(v)).group(1))
    except (AttributeError, ValueError, TypeError):
        return d


def svg_to_ops(svg: str, x0: float, y_top: float, scale: float) -> Tuple[str, float]:
    """The program's SVG drawing as PDF drawing operators, its top-left corner at (x0, y_top) on the page, scaled.
    Returns the operators and the drawing's height on the page. Elements outside the subset the program draws with
    (rect, line, polyline, polygon, path of M/L/H/V/Z segments, circle, text, g) are left out."""
    root = ET.fromstring(svg)
    vb = [float(v) for v in (root.get('viewBox') or '').replace(',', ' ').split()] or [0, 0, _num(root.get('width'), 400), _num(root.get('height'), 300)]
    vx, vy, vw, vh = vb
    ops: List[str] = ['q', '0 0 0 rg 0 0 0 RG']

    def P(x, y):                                 # SVG user units -> page points
        return x0 + (x - vx) * scale, y_top - (y - vy) * scale

    def style(el, inh):
        st = dict(inh)
        for k in ('fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'font-size', 'font-weight', 'text-anchor'):
            if el.get(k) is not None:
                st[k] = el.get(k)
        for part in (el.get('style') or '').split(';'):
            if ':' in part:
                k, v = part.split(':', 1)
                st[k.strip()] = v.strip()
        return st

    def paint(st, closed_fill_ok=True):
        op = float(st.get('opacity', 1) or 1)
        f = _rgb(st.get('fill', '#000000'), op * float(st.get('fill-opacity', 1) or 1)) if closed_fill_ok else None
        s_ = _rgb(st.get('stroke'), op * float(st.get('stroke-opacity', 1) or 1))
        pre = []
        if f:
            pre.append(f'{f[0]:g} {f[1]:g} {f[2]:g} rg')
        if s_:
            pre.append(f'{s_[0]:g} {s_[1]:g} {s_[2]:g} RG {max(0.1, _num(st.get("stroke-width"), 1.0) * scale):.2f} w')
        return pre, ('B' if f and s_ else 'f' if f else 'S' if s_ else 'n')

    def walk(el, inh):
        tag = el.tag.split('}')[-1]
        st = style(el, inh)
        if tag in ('svg', 'g'):
            for ch in el:
                walk(ch, st)
            return
        if tag == 'rect':
            x, y, w, h = (_num(el.get(k)) for k in ('x', 'y', 'width', 'height'))
            pre, op = paint(st)
            a, b = P(x, y + h)
            ops.extend(pre + [f'{a:.2f} {b:.2f} {w * scale:.2f} {h * scale:.2f} re {op}'])
        elif tag == 'line':
            st.setdefault('stroke', '#000000')
            pre, _ = paint({**st, 'fill': 'none'})
            a, b = P(_num(el.get('x1')), _num(el.get('y1'))); c, d = P(_num(el.get('x2')), _num(el.get('y2')))
            ops.extend(pre + [f'{a:.2f} {b:.2f} m {c:.2f} {d:.2f} l S'])
        elif tag in ('polyline', 'polygon'):
            nums = [float(v) for v in re.findall(r'-?[\d.]+(?:e-?\d+)?', el.get('points') or '')]
            pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 2:
                return
            pre, op = paint(st if tag == 'polygon' else {**st, 'fill': st.get('fill', 'none') if st.get('fill') != '#000000' else 'none'})
            seg = [f'{P(*pts[0])[0]:.2f} {P(*pts[0])[1]:.2f} m'] + [f'{P(*p)[0]:.2f} {P(*p)[1]:.2f} l' for p in pts[1:]]
            ops.extend(pre + seg + [('h ' if tag == 'polygon' else '') + op])
        elif tag == 'path':
            toks = re.findall(r'[MLHVZmlhvz]|-?[\d.]+(?:e-?\d+)?', el.get('d') or '')
            seg, cx, cy, sx, sy, cmd, i = [], 0.0, 0.0, 0.0, 0.0, 'M', 0
            closed = False
            while i < len(toks):
                t = toks[i]
                if re.match(r'[A-Za-z]', t):
                    cmd = t; i += 1
                    if cmd in 'Zz':
                        seg.append('h'); cx, cy = sx, sy; closed = True
                    continue
                rel = cmd.islower()
                C = cmd.upper()
                try:
                    if C in 'ML':
                        x, y = float(toks[i]), float(toks[i + 1]); i += 2
                        if rel:
                            x, y = cx + x, cy + y
                        cx, cy = x, y
                        a, b = P(x, y)
                        if C == 'M':
                            seg.append(f'{a:.2f} {b:.2f} m'); sx, sy = x, y
                            cmd = 'l' if rel else 'L'
                        else:
                            seg.append(f'{a:.2f} {b:.2f} l')
                    elif C == 'H':
                        x = float(toks[i]); i += 1
                        cx = cx + x if rel else x
                        a, b = P(cx, cy); seg.append(f'{a:.2f} {b:.2f} l')
                    elif C == 'V':
                        y = float(toks[i]); i += 1
                        cy = cy + y if rel else y
                        a, b = P(cx, cy); seg.append(f'{a:.2f} {b:.2f} l')
                    else:
                        i += 1
                except (IndexError, ValueError):
                    break
            if seg:
                pre, op = paint(st, closed or st.get('fill') not in (None, '#000000'))
                if op == 'n':
                    return
                ops.extend(pre + seg + [op])
        elif tag == 'circle':
            cx, cy, r = _num(el.get('cx')), _num(el.get('cy')), _num(el.get('r'))
            if r <= 0:
                return
            pre, op = paint(st)
            a, b = P(cx, cy); R = r * scale; k = 0.5523 * R
            ops.extend(pre + [f'{a + R:.2f} {b:.2f} m',
                              f'{a + R:.2f} {b + k:.2f} {a + k:.2f} {b + R:.2f} {a:.2f} {b + R:.2f} c',
                              f'{a - k:.2f} {b + R:.2f} {a - R:.2f} {b + k:.2f} {a - R:.2f} {b:.2f} c',
                              f'{a - R:.2f} {b - k:.2f} {a - k:.2f} {b - R:.2f} {a:.2f} {b - R:.2f} c',
                              f'{a + k:.2f} {b - R:.2f} {a + R:.2f} {b - k:.2f} {a + R:.2f} {b:.2f} c', op])
        elif tag == 'text':
            txt = ''.join(el.itertext()).strip()
            if not txt:
                return
            fs = _num(st.get('font-size'), 12.0) * scale
            bold = str(st.get('font-weight', '')) in ('bold', '600', '700', '800', '900')
            b = to_winansi(txt)
            w = width(b, fs, bold)
            x, y = _num(el.get('x')), _num(el.get('y'))
            anchor = st.get('text-anchor', 'start')
            col = _rgb(st.get('fill', '#000000'), float(st.get('opacity', 1) or 1)) or (0, 0, 0)
            m = re.search(r'rotate\(\s*(-?[\d.]+)(?:[\s,]+(-?[\d.]+)[\s,]+(-?[\d.]+))?\s*\)', el.get('transform') or '')
            a, bb = P(x, y)
            shift = {'middle': -w / 2, 'end': -w}.get(anchor, 0.0)
            if m:
                import math
                ang = -math.radians(float(m.group(1)))           # SVG turns clockwise with y down; PDF counter-clockwise
                if m.group(2) is not None:
                    pcx, pcy = P(float(m.group(2)), float(m.group(3)))
                    dx, dy = a - pcx, bb - pcy
                    a, bb = pcx + dx * math.cos(ang) - dy * math.sin(ang), pcy + dx * math.sin(ang) + dy * math.cos(ang)
                c_, s_ = math.cos(ang), math.sin(ang)
                ops.append(f'{col[0]:g} {col[1]:g} {col[2]:g} rg BT /{"F2" if bold else "F1"} {fs:.2f} Tf {c_:.4f} {s_:.4f} {-s_:.4f} {c_:.4f} '
                           f'{a + shift * c_:.2f} {bb + shift * s_:.2f} Tm {_lit(b)} Tj ET')
            else:
                ops.append(f'{col[0]:g} {col[1]:g} {col[2]:g} rg BT /{"F2" if bold else "F1"} {fs:.2f} Tf {a + shift:.2f} {bb:.2f} Td {_lit(b)} Tj ET')
    walk(root, {'fill': '#000000'})
    ops.append('Q')
    return '\n'.join(ops), vh * scale


class _Layout:
    def __init__(self, title: str, report_id: str):
        self.pages: List[List[str]] = []
        self.title, self.report_id = title, report_id
        self.y = TOP
        self.new_page()

    def new_page(self):
        self.pages.append([])
        self.y = TOP

    @property
    def ops(self):
        return self.pages[-1]

    def need(self, h: float) -> None:
        if self.y - h < BOTTOM and self.y < TOP - 1:
            self.new_page()

    def text(self, x: float, y: float, b: bytes, size: float, font: str = 'F1', rgb=(0, 0, 0)):
        self.ops.append(f'{rgb[0]:g} {rgb[1]:g} {rgb[2]:g} rg BT /{font} {size:g} Tf {x:.2f} {y:.2f} Td {_lit(b)} Tj ET')

    def para(self, text: str, size: float = 9.5, font: str = 'F1', lead: float = 1.32, gap: float = 5.0, rgb=(0, 0, 0), indent: float = 0.0):
        bold = font in ('F2', 'F4')
        for ln in wrap(text, size, BODY_W - indent, bold):
            self.need(size * lead)
            self.y -= size * lead
            self.text(MARGIN + indent, self.y + size * 0.25, ln, size, font, rgb)
        self.y -= gap

    def heading(self, text: str, size: float = 12.0, keep: float = 40.0):
        self.need(size * 1.6 + 16 + keep)            # a heading never sits alone at the foot of a page
        self.y -= 8
        for ln in wrap(text, size, BODY_W, True):
            self.y -= size * 1.25
            self.text(MARGIN, self.y + size * 0.2, ln, size, 'F2')
        self.ops.append(f'0.8 0.8 0.8 RG 0.6 w {MARGIN:.2f} {self.y - 2:.2f} m {PAGE_W - MARGIN:.2f} {self.y - 2:.2f} l S')
        self.y -= 8

    def table(self, columns: List[str], rows: List[List[str]], caption: str = '', size: float = 7.5, widths: Optional[List[float]] = None,
              bold_first: bool = False, colour=None):
        n = max(1, len(columns) if columns else max((len(r) for r in rows), default=1))
        pad = 3.0
        if widths is None:
            nat, mn = [], []
            for j in range(n):
                cells = ([columns[j]] if columns and j < len(columns) else []) + [r[j] if j < len(r) else '' for r in rows]
                nat.append(min(260.0, max([width(c, size, True) for c in cells[:1]] + [width(c, size) for c in cells[1:]] + [8.0]) + 2 * pad))
                longest = max([max((width(w, size) for w in str(c).split()), default=0.0) for c in cells] + [8.0])
                mn.append(min(nat[-1], min(72.0, longest + 2 * pad)))
            if sum(nat) <= BODY_W:
                extra = BODY_W - sum(nat)
                widths = [w_ + extra * w_ / sum(nat) for w_ in nat] if len(rows) and n > 1 else nat
            elif sum(mn) >= BODY_W:
                widths = [BODY_W * m_ / sum(mn) for m_ in mn]
            else:
                room = BODY_W - sum(mn)
                want = [a - b for a, b in zip(nat, mn)]
                widths = [m_ + room * w_ / (sum(want) or 1) for m_, w_ in zip(mn, want)]
        lead = size * 1.25
        if caption:
            self.need(lead * 4)
            self.para(caption, size + 0.5, 'F3', gap=2.0, rgb=(0.25, 0.25, 0.25))

        def row_lines(cells, bold):
            return [wrap(cells[j] if j < len(cells) else '', size, widths[j] - 2 * pad, bold) for j in range(n)]

        def draw_row(lines, bold, shade):
            h = max(len(x) for x in lines) * lead + 2 * pad
            top = self.y
            if shade:
                self.ops.append(f'0.95 0.95 0.95 rg {MARGIN:.2f} {top - h:.2f} {sum(widths):.2f} {h:.2f} re f')
            x = MARGIN
            for j in range(n):
                col = colour(j, lines[j]) if colour else (0, 0, 0)
                for k, ln in enumerate(lines[j]):
                    self.text(x + pad, top - pad - (k + 1) * lead + size * 0.28, ln, size, 'F2' if (bold or (bold_first and j == 0)) else 'F1', col)
                self.ops.append(f'0.78 0.78 0.78 RG 0.4 w {x:.2f} {top:.2f} {widths[j]:.2f} {-h:.2f} re S')
                x += widths[j]
            self.y -= h

        head = row_lines(columns, True) if columns else None
        hh = (max(len(x) for x in head) * lead + 2 * pad) if head else 0.0
        if head:
            self.need(hh + lead * 2 + 2 * pad)
            draw_row(head, True, True)
        for r in rows:
            ls = row_lines(r, False)
            h = max(len(x) for x in ls) * lead + 2 * pad
            max_lines = int((TOP - BOTTOM - hh - 2 * pad) / lead) - 1
            if h > TOP - BOTTOM - hh:                    # a row longer than a page: cut each cell to what fits
                ls = [x[:max_lines - 1] + [b'... (continued in the HTML copy)'] if len(x) > max_lines else x for x in ls]
                h = max(len(x) for x in ls) * lead + 2 * pad
            if self.y - h < BOTTOM:
                self.new_page()
                if head:
                    draw_row(head, True, True)
            draw_row(ls, False, False)
        self.y -= 8

    def figure(self, svg: str, caption: str, alt: str):
        try:
            root = ET.fromstring(svg)
            vb = [float(v) for v in (root.get('viewBox') or '').replace(',', ' ').split()]
            vw, vh = (vb[2], vb[3]) if len(vb) == 4 else (_num(root.get('width'), 400), _num(root.get('height'), 300))
        except (ET.ParseError, ValueError):
            self.para(f'[drawing: {alt}]', 9.0, 'F3')
            return
        scale = min(1.0, BODY_W / vw, (TOP - BOTTOM - 40) / vh)
        self.need(vh * scale + 30)
        try:
            ops, h = svg_to_ops(svg, MARGIN, self.y - 4, scale)
        except (ET.ParseError, ValueError):
            self.para(f'[drawing: {alt}]', 9.0, 'F3')
            return
        self.ops.append(ops)
        self.y -= h + 8
        self.para(caption, 8.0, 'F3', gap=8.0, rgb=(0.27, 0.27, 0.27))


def render(doc) -> bytes:
    """The document (a `client_reports.Document`) as PDF bytes."""
    from . import client_reports as CR
    L = _Layout(doc.title, doc.report_id)
    L.y -= 4
    for ln in wrap(doc.title, 16.0, BODY_W, True):
        L.y -= 19
        L.text(MARGIN, L.y, ln, 16.0, 'F2')
    L.y -= 10

    rows = [[str(k), str(v)] for k, v in doc.front]
    if rows:
        # the front table: the key column bold, the result line coloured as the HTML colours it
        kw = min(170.0, max([width(r[0], 8.5, True) for r in rows] + [60.0]) + 8)
        for r in rows:
            col = None
            if r[0] == 'Result':
                col = (0.64, 0, 0) if ('DETECTED' in r[1] and 'NO ' not in r[1]) else (0.04, 0.42, 0.12)
            L.table([], [r], size=8.5, widths=[kw, BODY_W - kw], bold_first=True,
                    colour=(lambda j, ls, c=col: c if (c and j == 1) else (0, 0, 0)))
            L.y += 8                                   # one table: no gap between its rows
        L.y -= 8
    for s in doc.sections:
        keep = 40.0
        if not s.paragraphs and s.figures:              # a section that opens with a drawing keeps its heading with it
            try:
                vb = [float(v) for v in (ET.fromstring(s.figures[0].svg).get('viewBox') or '').replace(',', ' ').split()]
                keep = vb[3] * min(1.0, BODY_W / vb[2]) + 12 if len(vb) == 4 else keep
            except (ET.ParseError, ValueError, ZeroDivisionError):
                pass
        L.heading(f'{s.number}. {s.title}', keep=min(keep, TOP - BOTTOM - 60))
        for p in s.paragraphs:
            L.para(p)
        for fg in s.figures:
            L.figure(fg.svg, fg.caption, fg.alt)
        for t in s.tables:
            L.table([str(c) for c in t.columns], [[CR._fmt(c) for c in r] for r in t.rows], t.caption)
    if doc.footer:
        L.y -= 6
        L.para(doc.footer, 8.0, 'F3', rgb=(0.33, 0.33, 0.33))
    # the page footers, now that the count is known
    N = len(L.pages)
    for i, ops in enumerate(L.pages):
        foot = to_winansi(f'{doc.title} \u00b7 {doc.report_id}')
        pg = to_winansi(f'page {i + 1} of {N}')
        if width(foot, 7.5) > BODY_W - width(pg, 7.5) - 20:
            while width(foot + b'...', 7.5) > BODY_W - width(pg, 7.5) - 20 and len(foot) > 10:
                foot = foot[:-1]
            foot = foot.rstrip() + b'...'
        ops.append(f'0.8 0.8 0.8 RG 0.5 w {MARGIN:.2f} {MARGIN + 10:.2f} m {PAGE_W - MARGIN:.2f} {MARGIN + 10:.2f} l S')
        ops.append(f'0.4 0.4 0.4 rg BT /F1 7.5 Tf {MARGIN:.2f} {MARGIN:.2f} Td {_lit(foot)} Tj ET')
        ops.append(f'0.4 0.4 0.4 rg BT /F1 7.5 Tf {PAGE_W - MARGIN - width(pg, 7.5):.2f} {MARGIN:.2f} Td {_lit(pg)} Tj ET')
    return assemble(L.pages, doc.title)


def assemble(pages: List[List[str]], title: str = '') -> bytes:
    """Pages of drawing operators as a PDF 1.4 file."""
    from . import __version__
    objs: List[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)
    cat = add(b'')                                   # filled in below
    pages_id = add(b'')
    font_ids = {k: add(f'<< /Type /Font /Subtype /Type1 /BaseFont /{v} /Encoding /WinAnsiEncoding >>'.encode()) for k, v in FONTS.items()}
    fonts = ' '.join(f'/{k} {i} 0 R' for k, i in font_ids.items())
    kids = []
    for ops in pages:
        data = zlib.compress('\n'.join(ops).encode('latin-1'), 6)
        cs = add(b'<< /Length ' + str(len(data)).encode() + b' /Filter /FlateDecode >>\nstream\n' + data + b'\nendstream')
        kids.append(add(f'<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W:g} {PAGE_H:g}] /Resources << /Font << {fonts} >> >> /Contents {cs} 0 R >>'.encode()))
    objs[cat - 1] = f'<< /Type /Catalog /Pages {pages_id} 0 R >>'.encode()
    objs[pages_id - 1] = f'<< /Type /Pages /Kids [{" ".join(f"{k} 0 R" for k in kids)}] /Count {len(kids)} >>'.encode()
    info = add(b'<< /Title ' + _lit(to_winansi(title)).encode('latin-1') + b' /Producer ' + _lit(to_winansi(f'GEA-Program {__version__}')).encode('latin-1') + b' >>')
    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out))
        out += f'{i} 0 obj\n'.encode() + b + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    for o in offs:
        out += f'{o:010d} 00000 n \n'.encode()
    out += f'trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(out)


# -- reading one back: the checks the gate makes on what this module writes -------------------------------------------
def read(data: bytes) -> dict:
    """A PDF this module wrote, read back: the page count, the cross-reference table checked against each object's
    offset, the fonts named, and the text of each page (the strings its text operators show). Not a general reader."""
    out = {'ok': False, 'pages': 0, 'text': [], 'fonts': [], 'problems': []}
    if not data.startswith(b'%PDF-1.'):
        out['problems'].append('no PDF header')
        return out
    m = re.search(rb'startxref\s+(\d+)\s+%%EOF\s*$', data)
    if not m:
        out['problems'].append('no startxref at the end')
        return out
    x = int(m.group(1))
    if data[x:x + 4] != b'xref':
        out['problems'].append('startxref does not point at the xref table')
        return out
    hdr = re.match(rb'xref\s+0\s+(\d+)\s+', data[x:])
    n = int(hdr.group(1))
    ents = re.findall(rb'(\d{10}) (\d{5}) ([nf]) ?\r?\n', data[x + hdr.end():x + hdr.end() + 20 * n + 4])
    objs = {}
    for i, (o, _, kind) in enumerate(ents):
        if kind == b'n':
            off = int(o)
            mm = re.match(rb'(\d+) 0 obj\n', data[off:off + 20])
            if not mm or int(mm.group(1)) != i:
                out['problems'].append(f'object {i}: the xref offset does not point at it')
                continue
            end = data.index(b'\nendobj', off)
            objs[i] = data[off + mm.end():end]
    for i, b in objs.items():
        if b.startswith(b'<< /Type /Font'):
            out['fonts'].append(re.search(rb'/BaseFont /(\S+)', b).group(1).decode())
    for i, b in sorted(objs.items()):
        if b.startswith(b'<< /Type /Page ') and b'/Contents' in b:
            cid = int(re.search(rb'/Contents (\d+) 0 R', b).group(1))
            s = objs[cid]
            raw = s[s.index(b'stream\n') + 7:s.rindex(b'\nendstream')]
            ops = zlib.decompress(raw).decode('latin-1')
            strings = re.findall(r'\(((?:\\.|[^\\)])*)\) Tj', ops)
            out['text'].append('\n'.join(re.sub(r'\\(.)', r'\1', t) for t in strings).encode('latin-1').decode('cp1252', errors='replace'))
    out['pages'] = len(out['text'])
    out['ok'] = not out['problems'] and out['pages'] > 0
    return out
