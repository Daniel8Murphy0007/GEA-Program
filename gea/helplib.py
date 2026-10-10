# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""helplib - the help library, indexed by the job, served from one source.

The pages live in `gea/help/<topic>.md` and ship inside the wheel. Each page
answers one job in four lines and stops: the command, what it writes, the
number to check, and what the page will not call a measurement. The same
text is what `gea help <topic>` prints, what `/api/help` serves, and what the
dashboard's help panels show - there is no second manual.

    gea help                 the index
    gea help drift           one page
    gea guide                the click-by-click tester guide (also in the wheel)
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

HELP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'help')

# the index order: by the job a reader arrives with, never by module
INDEX: List[tuple] = [
    ('start', 'Start', 'install, the first run, what the program will not claim'),
    ('data-in', 'Bring data in', 'CSV, LAS, SEG-Y, operator tables, WITS0, WITSML, OPC UA, MQTT, Modbus, the patch panel'),
    ('quality', 'Quality rules', 'RANGE, ROC, FLATLINE, SPIKE, STALE, GAP, each with the limit that fired'),
    ('drift', 'Drift', 'the datasheet aging rate, the labels, and the line NONE ON RECORD'),
    ('well-tests', 'Well tests', 'the criteria file, every reason code, the two approvals'),
    ('alarms', 'Alarms and the month', 'setpoint, deadband, delay, shelving, and why a blank month is not a pass'),
    ('site', 'The site', 'the folder layout, the four roles and what each cannot do'),
    ('instruments', 'Instruments', 'sensor swaps and calibration certificates'),
    ('transients', 'Shut-ins and build-ups', 'the first-look build-up analysis and its band'),
    ('sites', 'Sites - the engagement', 'the wells, stations and tracks of one client\'s ground, and the one report for all of it'),
    ('seismic', 'Seismic - the second leg', 'miniSEED/SAC in, spectra and persistent lines out, the detectability test against known rigs'),
    ('patches', 'Patches', 'supervised live connections, their states and records'),
    ('files', 'Files', 'import and export roots, detection by content, the evidence pack'),
    ('notifications', 'Notifications', 'rules, channels, the delivery log'),
    ('sra', 'The Seismicity Response Area', 'the packet an operator inside an SRA puts in front of the Commission'),
    ('osdu', 'The OSDU-shaped export', 'the site as the manifest an operator\'s data platform loads'),
    ('quakeml', 'The QuakeML catalogue export', 'the site\'s own events in the format the regulator\'s catalogue tools read'),
    ('ppdm', 'The well as master data names it', 'the US Well Number taken apart and the PPDM "What is a Well" components the site can name'),
    ('vibration', 'Machine vibration', 'the pump\'s record through the ISO 20816-3 zones and bearing envelope analysis'),
    ('conformance', 'Conformity, in the standards\' words', 'certificates against ISO/IEC 17025 7.8 and the bias against its class under a named ILAC-G8 decision rule'),
    ('etp', 'WITSML 2.x over ETP', 'the live port on Energistics ETP v1.2: subscribe to the store\'s channels and it pushes'),
    ('seedlink', 'Live stations (SeedLink)', 'the seismic leg\'s live port: a station\'s records as it writes them, resumed by sequence number, folded into its record list'),
    ('month-end', 'The month-end packet', 'the month\'s deliverable assembled once: every report collected, INCLUDED or NOT AVAILABLE with the reason, hashed'),
    ('backup', 'The backup', 'the site copied off the machine, verified, and restored once before it is trusted'),
    ('doctor', 'Which code is running', 'gea doctor and the serve start-up gate'),
    ('audit-update', 'Audit / Update', 'the whole audit log with filters, the program against PyPI, every report against its source'),
    ('soak', 'The soak', 'the patches through link outages for hours: reconnection, resume, loss by protocol, the page answering - measured, not modelled'),
    ('upkeep', 'Upkeep', 'housekeeping, sessions, the TLS proxy, the load test'),
]
# the four lines every page must carry, in this order
REQUIRED_LINES = ('**The command**', '**What it writes**', '**The number to check**', '**What this page will not call a measurement**')
# the dashboard view -> the topic its help panel shows
VIEW_TOPIC: Dict[str, str] = {
    'home': 'start', 'wells': 'data-in', 'well': 'drift', 'live': 'patches', 'patch': 'patches', 'files': 'files', 'alarms': 'alarms',
    'approvals': 'well-tests', 'config': 'site', 'reports': 'drift', 'verify': 'start', 'survey': 'data-in', 'jobs': 'site',
    'admin': 'site', 'prefs': 'site', 'search': 'start', 'welcome': 'start', 'seismic': 'seismic', 'seismic_station': 'seismic', 'track': 'seismic', 'sar': 'seismic', 'sites': 'sites', 'audit': 'audit-update',
}


def topics() -> List[dict]:
    return [{'topic': t, 'title': title, 'summary': summ, 'present': os.path.isfile(os.path.join(HELP_DIR, f'{t}.md'))} for t, title, summ in INDEX]


def page(topic: str) -> Optional[str]:
    t = re.sub(r'[^a-z0-9-]', '', (topic or '').lower())
    p = os.path.join(HELP_DIR, f'{t}.md')
    if not t or not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return f.read()


def summary_of(topic: str) -> str:
    """The one-line summary: the page's `>` line, else the index line."""
    text = page(topic) or ''
    for line in text.splitlines():
        if line.startswith('> '):
            return line[2:].strip()
    return next((s for t, _, s in INDEX if t == topic), '')


def check(topic: str) -> List[str]:
    """What a page is missing against the contract; empty when it is complete."""
    text = page(topic)
    if text is None:
        return ['no page']
    missing = [ln for ln in REQUIRED_LINES if ln not in text]
    if not text.lstrip().startswith('# '):
        missing.append('a `# Title` first line')
    if not any(l.startswith('> ') for l in text.splitlines()):
        missing.append('a `> summary` line')
    # the four lines in order
    pos = [text.find(ln) for ln in REQUIRED_LINES if ln in text]
    if pos != sorted(pos):
        missing.append('the four lines out of order')
    return missing


def index_text() -> str:
    lines = ['GEA-Program help, by the job you came with. `gea help <topic>` prints a page; the same text is on every dashboard page.', '']
    w = max(len(t) for t, _, _ in INDEX)
    for t, title, summ in INDEX:
        lines.append(f'  {t:{w}s}  {title} - {summ}')
    lines += ['', 'Each page has four lines: the command, what it writes, the number to check, and what the page will not call a measurement.',
              'The click-by-click tester guide: gea guide.']
    return '\n'.join(lines)


def guide_text() -> Optional[str]:
    p = os.path.join(HELP_DIR, 'TESTER_GUIDE.md')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return f.read()


def to_html(md: str) -> str:
    """A small renderer for the pages' own Markdown: headings, the summary quote, paragraphs, bold, code, bullets, tables."""
    import html
    out: List[str] = []
    para: List[str] = []
    table: List[str] = []

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
        s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
        s = re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', s)
        return s

    def flush():
        nonlocal para, table
        if para:
            out.append('<p>' + inline(' '.join(para)) + '</p>')
            para = []
        if table:
            rows = [r for r in table if not re.match(r'^\|?\s*-', r)]
            cells = [[c.strip() for c in r.strip().strip('|').split('|')] for r in rows]
            if cells:
                out.append('<table><thead><tr>' + ''.join(f'<th>{inline(c)}</th>' for c in cells[0]) + '</tr></thead><tbody>'
                           + ''.join('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r) + '</tr>' for r in cells[1:]) + '</tbody></table>')
            table = []
    in_list = False
    for line in md.splitlines():
        if line.startswith('|'):
            if para:
                flush()
            table.append(line)
            continue
        if table and not line.startswith('|'):
            flush()
        if line.startswith('- '):
            if para:
                flush()
            if not in_list:
                out.append('<ul>'); in_list = True
            out.append('<li>' + inline(line[2:]) + '</li>')
            continue
        if in_list:
            out.append('</ul>'); in_list = False
        if line.startswith('# '):
            flush(); out.append('<h2>' + inline(line[2:]) + '</h2>')
        elif line.startswith('## '):
            flush(); out.append('<h3>' + inline(line[3:]) + '</h3>')
        elif line.startswith('> '):
            flush(); out.append('<p class="muted">' + inline(line[2:]) + '</p>')
        elif not line.strip():
            flush()
        else:
            para.append(line)
    if in_list:
        out.append('</ul>')
    flush()
    return '\n'.join(out)
