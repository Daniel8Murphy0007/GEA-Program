# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tutorial - the walk-through of GEA and the programs around it, lesson by lesson.

The lessons are Markdown files under help/tutorial/ (NN-slug.md), each headed by its number and title and four
lines that say who it is for (the office, or the pad attendant), which page of the control panel it is done on,
how long it takes, and - at the end - which help pages carry the detail. The Tutorial tab lists them by part with
each person's progress (kept in the workspace, per account), opens one with a button to the page it is about, and
prints the whole course as one handout; `gea tutorial` prints the same at the command line. The acceptance gate
holds every lesson to the contract: the header, numbered steps, the "you know it worked when" line, help pages that
exist and a page that exists in the control panel."""
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
    ('Getting started', (1, 3)),
    ('Wells and their data', (4, 8)),
    ('Watching the pad', (9, 11)),
    ('The engineering pages', (12, 14)),
    ('Seismic', (15, 17)),
    ('Reports for the client', (18, 19)),
    ('Keeping it running', (20, 23)),
    ('On the pad: the attendant', (24, 27)),
    ("The driller's agent", (28, 29)),
]
META_KEYS = ('Who', 'Where', 'Takes', 'Help')
WORKED = '## You know it worked when'


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(path: str) -> dict:
    text = open(path, encoding='utf-8').read()
    lines = text.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith('# ') else os.path.basename(path)
    m = re.match(r'^(\d+)\.\s+(.*)$', title)
    n = int(m.group(1)) if m else 0
    meta: Dict[str, str] = {}
    body: List[str] = []
    for ln in lines[1:]:
        km = re.match(r'^(Who|Where|Takes|Help):\s*(.*)$', ln)
        if km:
            meta[km.group(1)] = km.group(2).strip()
        else:
            body.append(ln)
    summary = next((l[2:].strip() for l in body if l.startswith('> ')), '')
    steps = len([l for l in body if re.match(r'^\d+\. ', l)])
    slug = re.sub(r'^\d+-', '', os.path.splitext(os.path.basename(path))[0])
    return {'n': n, 'id': slug, 'title': m.group(2) if m else title, 'who': meta.get('Who', ''), 'where': meta.get('Where', ''),
            'takes': meta.get('Takes', ''), 'help': [t.strip() for t in meta.get('Help', '').split(',') if t.strip()],
            'summary': summary, 'steps': steps, 'path': path, 'body': '\n'.join(body), 'markdown': text}


def lessons() -> List[dict]:
    """Every lesson in order, without the text."""
    out = []
    if os.path.isdir(LESSON_DIR):
        for n in sorted(os.listdir(LESSON_DIR)):
            if n.endswith('.md'):
                d = _parse(os.path.join(LESSON_DIR, n))
                out.append({k: v for k, v in d.items() if k not in ('body', 'markdown')})
    out.sort(key=lambda d: d['n'])
    return out


def part_of(n: int) -> str:
    for name, (a, b) in PARTS:
        if a <= n <= b:
            return name
    return 'More'


def lesson(ident: str) -> Optional[dict]:
    """One lesson by its id (install) or its number (1), with its text as Markdown and as HTML."""
    for d in lessons():
        if d['id'] == str(ident) or str(d['n']) == str(ident).lstrip('0'):
            full = _parse(d['path'])
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


def overview(ws, user: str) -> dict:
    ls = lessons()
    pr = progress(ws, user) if ws is not None and user else {}
    parts = []
    for name, (a, b) in PARTS:
        items = [{**l, 'done_utc': pr.get(l['id'])} for l in ls if a <= l['n'] <= b]
        parts.append({'name': name, 'lessons': items, 'done': sum(1 for i in items if i['done_utc'])})
    return {'parts': parts, 'n_lessons': len(ls), 'n_done': sum(1 for l in ls if pr.get(l['id'])),
            'who': WHO, 'next': next((l['id'] for l in ls if not pr.get(l['id'])), None)}


# -- the handout --------------------------------------------------------------------------------------------------------

def handout_html(version: str, site_name: str = '') -> str:
    """The whole course as one printable page: a cover, the contents, every lesson in order, the office's first
    and the pad's after. Self-contained; the browser's Print makes the PDF."""
    ls = lessons()
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
               f'<p>Lessons 1 to 23 are for the office - whoever installs the program, brings the data in, watches the pad and hands the reports over. '
               f'Lessons 24 to 27 are for the attendant on the pad - whoever builds the box, keeps it recording and carries the files in; lessons 28 and 29 are the driller\'s agent, for the office and for whoever sets up the rig PC. '
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
                   f'<p class="meta">For {esc(WHO.get(l["who"], l["who"]))} · on the control panel: {esc(l["where"])} · {esc(l["takes"])}</p>{body}'
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
        f.write(f'# GEA-Program tutorial - version {version}\n\n' + '\n\n---\n\n'.join(lesson(l['id'])['markdown'] for l in lessons()))
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
