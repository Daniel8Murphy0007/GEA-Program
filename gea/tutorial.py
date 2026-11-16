# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tutorial - the walk-through of GEA and the programs around it, lesson by lesson.

The lessons are Markdown files under help/tutorial/ (NN-slug.md), each headed by its number and title and four
lines that say who it is for (the office, or the pad attendant), which page of the control panel it is done on,
how long it takes, and - at the end - which help pages carry the detail. A fifth line, Pages:, names the other tabs
the lesson walks through, so every tab of the control panel is reached by a lesson and each tab can say which one.
A lesson points at another by name - {lesson:vpn} - and the number is filled in when it is read, so the course can
be re-ordered without a wrong number left behind. The Tutorial tab lists them by part with each person's progress
(kept in the workspace, per account), opens one with a button to every page it is about, and prints the whole
course as one handout; `gea tutorial` prints the same at the command line. The acceptance gate holds every lesson
to the contract - the header, numbered steps, the "you know it worked when" line, help pages that exist, pages
that exist in the control panel - and the course to its coverage: no tab and no help page without a lesson."""
from __future__ import annotations

import html as _html
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import helplib as H

LESSON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'help', 'tutorial')
PROGRESS = os.path.join('records', 'tutorial_progress.json')
WHO = {'office': 'the office', 'pad': 'the pad attendant'}
PARTS = [
    ('Getting started', (1, 4)),
    ('Wells and their data', (5, 10)),
    ('Watching the pad', (11, 13)),
    ('Where the wells are', (14, 15)),
    ('Drilling', (16, 19)),
    ('Completion, production and disposal', (20, 22)),
    ('Seismic', (23, 25)),
    ('Reports for the client', (26, 27)),
    ('Keeping it running', (28, 32)),
    ('On the pad: the attendant', (33, 36)),
    ("The driller's agent", (37, 38)),
]
META_KEYS = ('Who', 'Where', 'Pages', 'Takes', 'Help')
WORKED = '## You know it worked when'
# help pages that are reference, read from a page's own panel or the Help tab, and taught by no lesson
REFERENCE_ONLY = ('tutorial', 'help')
_REF = re.compile(r'\{([Ll])esson:([a-z0-9-]+)\}')


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


_CACHE: Dict[str, tuple] = {}


def _signature(folder: str) -> tuple:
    """What a folder of lessons holds - each file's name, size and time - read in one pass of the directory."""
    try:
        with os.scandir(folder) as it:
            return tuple(sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns) for e in it if e.name.endswith('.md')))
    except OSError:
        return ()


def _load() -> List[dict]:
    """Every lesson, read once and kept until a file of the folder changes: the course is asked for many times by
    one page, and a file opened for every question is slow on a share."""
    sig = _signature(LESSON_DIR)
    hit = _CACHE.get(LESSON_DIR)
    if hit and hit[0] == sig:
        return hit[1]
    texts = {}
    for name, _, _ in sig:
        with open(os.path.join(LESSON_DIR, name), encoding='utf-8') as f:
            texts[name] = f.read()
    nums: Dict[str, int] = {}
    for name, text in texts.items():
        m = re.match(r'^# (\d+)\.', text)
        if m:
            nums[re.sub(r'^\d+-', '', name[:-3])] = int(m.group(1))
    out = [_parse(os.path.join(LESSON_DIR, name), texts[name], nums) for name in texts]
    out.sort(key=lambda d: d['n'])
    _CACHE.clear()
    _CACHE[LESSON_DIR] = (sig, out)
    return out


_TABS: Dict[str, tuple] = {}


def tabs() -> Dict[str, str]:
    """The control panel's tabs, each address with its label, read from the page itself."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'app.html')
    try:
        st = os.stat(p)
        hit = _TABS.get(p)
        if hit and hit[0] == (st.st_size, st.st_mtime_ns):
            return dict(hit[1])
        with open(p, encoding='utf-8') as f:
            src = f.read()
    except OSError:
        return {}
    m = re.search(r'const NAV = \[(.*?)\];', src)
    found = dict(re.findall(r"\['(#/[a-z0-9]*)', '([^']*)'\]", m.group(1))) if m else {}
    _TABS[p] = ((st.st_size, st.st_mtime_ns), found)
    return dict(found)


def _parse(path: str, text: str, nums: Dict[str, int]) -> dict:
    text = _REF.sub(lambda m: f"{m.group(1)}esson {nums[m.group(2)]}" if m.group(2) in nums else m.group(0), text)
    lines = text.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith('# ') else os.path.basename(path)
    m = re.match(r'^(\d+)\.\s+(.*)$', title)
    n = int(m.group(1)) if m else 0
    meta: Dict[str, str] = {}
    body: List[str] = []
    for ln in lines[1:]:
        km = re.match(r'^(Who|Where|Pages|Takes|Help):\s*(.*)$', ln)
        if km:
            meta[km.group(1)] = km.group(2).strip()
        else:
            body.append(ln)
    summary = next((l[2:].strip() for l in body if l.startswith('> ')), '')
    steps = len([l for l in body if re.match(r'^\d+\. ', l)])
    slug = re.sub(r'^\d+-', '', os.path.splitext(os.path.basename(path))[0])
    pages = [meta.get('Where', '')] + [t.strip() for t in meta.get('Pages', '').split(',') if t.strip() and t.strip() != meta.get('Where', '')]
    return {'n': n, 'id': slug, 'title': m.group(2) if m else title, 'who': meta.get('Who', ''), 'where': meta.get('Where', ''),
            'pages': [p for p in pages if p],
            'takes': meta.get('Takes', ''), 'help': [t.strip() for t in meta.get('Help', '').split(',') if t.strip()],
            'summary': summary, 'steps': steps, 'path': path, 'body': '\n'.join(body), 'markdown': text}


def lessons() -> List[dict]:
    """Every lesson in order, without the text."""
    return [{k: v for k, v in d.items() if k not in ('body', 'markdown')} for d in _load()]


def part_of(n: int) -> str:
    for name, (a, b) in PARTS:
        if a <= n <= b:
            return name
    return 'More'


def lesson(ident: str) -> Optional[dict]:
    """One lesson by its id (install) or its number (1), with its text as Markdown and as HTML."""
    for d in _load():
        if d['id'] == str(ident) or str(d['n']) == str(ident).lstrip('0'):
            full = dict(d)
            full['html'] = H.to_html(full['body'])
            full['part'] = part_of(full['n'])
            return full
    return None


def check(ident: str) -> List[str]:
    """What a lesson is missing against the contract; empty when it is complete."""
    d = lesson(ident)
    if d is None:
        return ['no such lesson']
    missing = []
    if not d['n']:
        missing.append('a `# N. Title` first line')
    if d['who'] not in WHO:
        missing.append('a Who: line (office or pad)')
    if not re.match(r'^#/[a-z0-9]*$', d['where']):
        missing.append('a Where: line naming a page (#/setup)')
    known = tabs()
    for pg in d['pages']:
        if known and pg not in known:
            missing.append(f'page {pg} is not a tab of the control panel')
    for _, ref in _REF.findall(d['markdown']):
        missing.append(f'no lesson named {ref}')
    if not d['takes']:
        missing.append('a Takes: line')
    if not d['summary']:
        missing.append('a `> summary` line')
    if d['steps'] < 3:
        missing.append('at least three numbered steps')
    if WORKED not in d['body']:
        missing.append('the "You know it worked when" section')
    if not d['help']:
        missing.append('a Help: line')
    for t in d['help']:
        if H.page(t) is None:
            missing.append(f'help page {t} does not exist')
    if re.search(r'<[a-zA-Z][a-zA-Z0-9 ._|/:\\-]*>', d['body']):
        missing.append('a <placeholder> in the text')
    return missing


def teaches(topic: str) -> List[dict]:
    """The lessons whose Help line carries a help page."""
    return [l for l in lessons() if topic in l['help']]


def for_tab(tab: str) -> Optional[dict]:
    """The lesson that teaches a tab (#/hardline): the one done on it, else the first that walks through it."""
    ls = lessons()
    return next((l for l in ls if l['where'] == tab), None) or next((l for l in ls if tab in l['pages']), None)


def by_view() -> Dict[str, List[dict]]:
    """Every tab's lessons by the tab's view name (hardline), for the page's own help panel: the lessons done on
    the tab first, then those that walk through it - the office's before the pad's, four at most."""
    ls = lessons()
    out: Dict[str, List[dict]] = {}
    for tab in tabs():
        on = [l for l in ls if l['where'] == tab] + [l for l in ls if l['where'] != tab and tab in l['pages']]
        on.sort(key=lambda l: (l['who'] != 'office',))
        if on:
            out[tab[2:] or 'home'] = [{'n': l['n'], 'id': l['id'], 'title': l['title']} for l in on[:4]]
    return out


def course_text() -> str:
    """Every lesson's text in order, as one string."""
    return '\n\n'.join(d['markdown'] for d in _load())


def coverage() -> dict:
    """What the course does not reach: tabs no lesson walks through, help pages no lesson names, lessons outside
    every part. All three empty is the contract."""
    ls = lessons()
    reached = {p for l in ls for p in l['pages']}
    helped = {t for l in ls for t in l['help']}
    return {'tabs_unreached': sorted(t for t in tabs() if t not in reached),
            'help_untaught': sorted(t for t, _, _ in H.INDEX if t not in helped and t not in REFERENCE_ONLY),
            'outside_parts': [l['n'] for l in ls if not any(a <= l['n'] <= b for _, (a, b) in PARTS)],
            'lessons': len(ls), 'tabs': len(tabs()), 'help_pages': len(H.INDEX)}


def _span(who: str) -> str:
    """The lessons for one audience as the handout's cover names them: 1 to 32, or 33 to 36 and 38."""
    ns = [l['n'] for l in lessons() if l['who'] == who]
    runs: List[List[int]] = []
    for n in ns:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    bits = [str(a) if a == b else f'{a} to {b}' for a, b in runs]
    return bits[0] if len(bits) == 1 else ', '.join(bits[:-1]) + ' and ' + bits[-1] if bits else 'none'


# -- progress -----------------------------------------------------------------------------------------------------------

def _progress_path(ws) -> str:
    return os.path.join(ws.path, PROGRESS)


def progress(ws, user: str) -> Dict[str, str]:
    try:
        with open(_progress_path(ws), encoding='utf-8') as f:
            return dict(json.load(f).get(user, {}))
    except (OSError, ValueError):
        return {}


def mark(ws, user: str, ident: str, done: bool = True) -> Dict[str, str]:
    d = lesson(ident)
    if d is None:
        raise KeyError(f'no lesson {ident}')
    p = _progress_path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, encoding='utf-8') as f:
            allp = json.load(f)
    except (OSError, ValueError):
        allp = {}
    mine = dict(allp.get(user, {}))
    if done:
        mine[d['id']] = _utc()
    else:
        mine.pop(d['id'], None)
    allp[user] = mine
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(allp, f, indent=1)
    os.replace(tmp, p)
    return mine


def _minutes(takes: str) -> int:
    """The minutes a lesson says it takes at the screen (the first figure of its Takes: line)."""
    m = re.match(r'^\s*(\d+)\s*(minute|hour)', takes or '')
    return int(m.group(1)) * (60 if m and m.group(2) == 'hour' else 1) if m else 0


def overview(ws, user: str) -> dict:
    ls = lessons()
    pr = progress(ws, user) if ws is not None and user else {}
    parts = []
    for name, (a, b) in PARTS:
        items = [{**l, 'done_utc': pr.get(l['id'])} for l in ls if a <= l['n'] <= b]
        parts.append({'name': name, 'lessons': items, 'done': sum(1 for i in items if i['done_utc'])})
    known = tabs()
    for p in parts:
        for i in p['lessons']:
            i['page_names'] = [known.get(pg, pg) for pg in i['pages']]
    return {'parts': parts, 'n_lessons': len(ls), 'n_done': sum(1 for l in ls if pr.get(l['id'])),
            'minutes': sum(_minutes(l['takes']) for l in ls), 'minutes_left': sum(_minutes(l['takes']) for l in ls if not pr.get(l['id'])),
            'who': WHO, 'next': next((l['id'] for l in ls if not pr.get(l['id'])), None)}


# -- the handout --------------------------------------------------------------------------------------------------------

def handout_html(version: str, site_name: str = '') -> str:
    """The whole course as one printable page: a cover, the contents, every lesson in order, the office's first
    and the pad's after. Self-contained; the browser's Print makes the PDF."""
    ls = lessons()
    known = tabs()
    esc = _html.escape
    css = """
    body{font:15px/1.5 Georgia,'Times New Roman',serif;color:#1a1a1a;max-width:52em;margin:0 auto;padding:2em 1.5em}
    h1{font-size:2.2em;margin:0 0 .2em}h2{font-size:1.5em;margin:1.6em 0 .4em;border-bottom:2px solid #1a1a1a;padding-bottom:.2em}
    h3{font-size:1.15em;margin:1.2em 0 .3em}p.muted{color:#555}code{font:13px Consolas,Menlo,monospace;background:#f3f3f3;padding:0 3px}
    .lesson{page-break-before:always}.meta{color:#555;font-size:.9em;margin:-.2em 0 .8em}.cover{min-height:70vh;display:flex;flex-direction:column;justify-content:center}
    .toc li{margin:.15em 0}.part{margin-top:1em;font-weight:bold}ol,ul{padding-left:1.4em}li{margin:.3em 0}
    h3.worked-h+p{background:#f3f7f3;border-left:4px solid #2a7;padding:.4em .8em;margin:.2em 0 1em}.toc-h{page-break-before:always}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 8px}
    @media print{body{font-size:12.5px;max-width:none;padding:0}.cover{min-height:85vh}a{color:inherit;text-decoration:none}}
    """
    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>GEA-Program tutorial {esc(version)}</title><style>{css}</style></head><body>']
    out.append(f'<div class="cover"><h1>GEA-Program</h1><p style="font-size:1.4em">The tutorial: every step of GEA and the programs around it</p>'
               f'<p class="muted">Version {esc(version)}{(" · " + esc(site_name)) if site_name else ""} · {len(ls)} lessons, {sum(l["steps"] for l in ls)} steps · printed {_utc()[:10]}</p>'
               f'<p>Lessons {_span("office")} are for the office - whoever installs the program, brings the data in, watches the pad and hands the reports over. '
               f'Lessons {_span("pad")} are for the people on the pad - the attendant who builds the box, keeps it recording and carries the files in, and whoever sets up the rig PC for the driller\'s agent. '
               f'Each lesson names the page it is done on, how long it takes, what you press, and how you know it worked. The same lessons are on the control panel\'s Tutorial tab, where each person ticks them off.</p></div>')
    out.append('<h2 class="toc-h">Contents</h2><div class="toc">')
    for name, (a, b) in PARTS:
        out.append(f'<div class="part">{esc(name)}</div><ol start="{a}">')
        for l in ls:
            if a <= l['n'] <= b:
                out.append(f'<li><a href="#l{l["n"]}">{esc(l["title"])}</a> <span class="muted">({esc(l["takes"])})</span></li>')
        out.append('</ol>')
    out.append('</div>')
    for l in ls:
        full = lesson(l['id'])
        body = H.to_html(full['body']).replace('<h3>You know it worked when</h3>', '<h3 class="worked-h">You know it worked when</h3>')
        out.append(f'<div class="lesson" id="l{l["n"]}"><h2>{l["n"]}. {esc(l["title"])}</h2>'
                   f'<p class="meta">For {esc(WHO.get(l["who"], l["who"]))} · on the control panel: {esc(", ".join(known.get(pg, pg) for pg in l["pages"]))} · {esc(l["takes"])}</p>{body}'
                   f'<p class="muted">Help pages: {", ".join("<code>gea help " + esc(t) + "</code>" for t in l["help"])}</p></div>')
    out.append(f'<p class="muted" style="margin-top:3em">GEA-Program {esc(version)}. Printed from the program; the Tutorial tab has the same lessons and each person\'s progress.</p></body></html>')
    return '\n'.join(out)


def write_handout(ws, actor: str, version: str) -> dict:
    """The handout into the workspace's exports/tutorial/ folder, as HTML (print it to PDF) and as Markdown."""
    d = ws.dir('exports', 'tutorial')
    name = f'GEA-Tutorial-{version}'
    hp = os.path.join(d, name + '.html')
    with open(hp, 'w', encoding='utf-8') as f:
        f.write(handout_html(version, str(ws.manifest.get('name') or '')))
    mp = os.path.join(d, name + '.md')
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(f'# GEA-Program tutorial - version {version}\n\n' + '\n\n---\n\n'.join(d['markdown'] for d in _load()))
    ws.audit(actor, 'tutorial.handout', {'html': hp, 'md': mp, 'lessons': len(lessons())})
    return {'html': hp, 'md': mp, 'dir': d, 'lessons': len(lessons())}


def text(ws=None, user: str = '') -> str:
    ov = overview(ws, user)
    lines = [f"GEA-Program tutorial: {ov['n_lessons']} lessons" + (f", {ov['n_done']} done by {user}" if user else '') + ". `gea tutorial --lesson 3` prints one; the Tutorial tab has them all."]
    for p in ov['parts']:
        lines.append(f"  {p['name']}")
        for l in p['lessons']:
            lines.append(f"    {'x' if l['done_utc'] else ' '} {l['n']:2d}. {l['title']:<62s} {WHO[l['who']]:<18s} {l['where']:<12s} {l['takes']}")
    return '\n'.join(lines)
