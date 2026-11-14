# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""mcp_server - the site, readable by an assistant: a Model Context Protocol server on standard input and output.

An assistant that speaks MCP (the protocol by which a chat program is given tools) can be handed this program as a
tool box: it starts `gea mcp --workspace <folder>` as a child process, asks what tools there are, and calls them. Each
tool is one of this program's own pages - the rig time, the roadmap, the drilling alerts, the fluid balance - read
out as the same text the command line prints, so what the assistant says about the site is what the program says,
with the program's own labels (SIMULATION where it is one) and its own "nothing declared" where nothing is.

What it is, exactly:

- LOCAL. It listens on nothing. It reads requests from its standard input and writes answers to its standard
  output, one JSON message a line, and exits when the input closes. There is no port, no address, no account.
- READ-ONLY. Every tool reads; none declares, attaches, removes, writes a report or changes a setting. The tools
  call the same page functions the tabs do, which keep no record of having been read; the only things a call can
  leave behind are a chart cache (as opening the tab would) and one line in the access log.
- LOGGED. Every call is a line in `records/mcp_access.jsonl`: the time, the tool, its arguments, the program that
  asked (as it names itself), whether it answered, how many characters, how long - and a call for a tool that is
  withheld or does not exist, as turned away.
- CHOSEN. `records/mcp.json` holds which tools are offered (all of them until changed) and the most characters one
  answer may carry; a tool not offered is not listed and cannot be called.

What it is not: private from the assistant's maker. The server is on this computer; the assistant usually is not.
What a tool returns goes into the conversation, and so to whoever runs the model behind it. Offering a tool is
deciding that its page may leave the site.

The protocol. Two eras are spoken, chosen by how the client opens. A client of revision 2026-07-28 sends every
request with its protocol version in `_meta`; it is answered statelessly, with `resultType` on every result, and
may ask `server/discover` first. A client of 2025-11-25 or earlier opens with `initialize`; it is answered with
the handshake of the revision it names. Tools only: no resources, no prompts, nothing asked of the client.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

MODERN = ('2026-07-28',)
LEGACY = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
SUPPORTED = MODERN + LEGACY
K_VERSION = 'io.modelcontextprotocol/protocolVersion'
K_CLIENT = 'io.modelcontextprotocol/clientInfo'
K_SERVER = 'io.modelcontextprotocol/serverInfo'
SERVER_NAME = 'gea-program'
E_PARSE, E_REQUEST, E_METHOD, E_PARAMS, E_INTERNAL, E_VERSION = -32700, -32600, -32601, -32602, -32603, -32022
MAX_CHARS, MAX_CHARS_BOUNDS = 40000, (2000, 400000)
LOG_ROTATE_BYTES = 5_000_000
INSTRUCTIONS = ('GEA-Program, read-only: each tool reads one page of a well site\'s records as the program itself computes it. Start with `site` for the wells. '
                'Quote the figures as given; a page marked SIMULATION is a projection, and a page that says nothing is declared has no figures to give. Nothing here can change the site.')
_LOCK = threading.Lock()


class ToolError(ValueError):
    """A tool could not answer in a way the model can act on (an unknown well, nothing declared): returned as a tool
    result with isError, not as a protocol error."""


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _version() -> str:
    from . import __version__
    return __version__


def server_info() -> dict:
    return {'name': SERVER_NAME, 'title': 'GEA-Program', 'version': _version()}


# --------------------------------------------------------------------------------------------------------------
# the settings and the access log
# --------------------------------------------------------------------------------------------------------------
def _path(ws, name: str) -> str:
    return os.path.join(ws.path, 'records', name)


def settings(ws) -> dict:
    s = {'tools': None, 'max_chars': MAX_CHARS, 'source': ''}
    p = _path(ws, 'mcp.json')
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if isinstance(d.get('tools'), list):
            s['tools'] = [t for t in d['tools'] if t in NAMES]
        if isinstance(d.get('max_chars'), (int, float)):
            s['max_chars'] = int(min(max(d['max_chars'], MAX_CHARS_BOUNDS[0]), MAX_CHARS_BOUNDS[1]))
        s['source'] = str(d.get('source') or '')
    return s


def set_settings(ws, actor: str, tools=None, max_chars=None, source: str = '') -> dict:
    """Which tools are offered (a list of names; every name for all of them) and the most characters an answer carries."""
    s = settings(ws)
    before = {'tools': s['tools'], 'max_chars': s['max_chars']}
    if tools is not None:
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(',') if t.strip()]
        bad = [t for t in tools if t not in NAMES]
        if bad:
            raise ToolError(f'no tool named {", ".join(bad)}: the tools are {", ".join(NAMES)}')
        chosen = [n for n in NAMES if n in set(tools)]
        s['tools'] = None if len(chosen) == len(NAMES) else chosen
    if max_chars is not None and str(max_chars).strip() != '':
        try:
            m = int(float(max_chars))
        except (TypeError, ValueError):
            raise ToolError('the most characters an answer carries must be a number')
        if not (MAX_CHARS_BOUNDS[0] <= m <= MAX_CHARS_BOUNDS[1]):
            raise ToolError(f'the most characters an answer carries must be between {MAX_CHARS_BOUNDS[0]:,} and {MAX_CHARS_BOUNDS[1]:,}')
        s['max_chars'] = m
    if (source or '').strip():
        s['source'] = source.strip()
    after = {'tools': s['tools'], 'max_chars': s['max_chars']}
    if after != before:
        os.makedirs(os.path.dirname(_path(ws, 'mcp.json')), exist_ok=True)
        with open(_path(ws, 'mcp.json'), 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1, sort_keys=True)
        ws.audit(actor, 'mcp.settings', {'before': before, 'after': after, 'source': s['source']})
    return s


def offered(ws, allow: Optional[List[str]] = None) -> List[str]:
    """The tools offered: the workspace's choice, narrowed (never widened) by the names this run was started with."""
    s = settings(ws)
    names = list(NAMES) if s['tools'] is None else list(s['tools'])
    if allow is not None:
        names = [n for n in names if n in set(allow)]
    return names


def log_call(ws, rec: dict) -> None:
    p = _path(ws, 'mcp_access.jsonl')
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with _LOCK:
            if os.path.isfile(p) and os.path.getsize(p) > LOG_ROTATE_BYTES:
                os.replace(p, p + '.1')
            with open(p, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, sort_keys=True, default=str) + '\n')
    except OSError:
        pass                                           # a log that cannot be written never stops an answer


def access_log(ws, n: int = 50) -> List[dict]:
    p = _path(ws, 'mcp_access.jsonl')
    if not os.path.isfile(p):
        return []
    rows = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return list(reversed(rows[-n:]))


# --------------------------------------------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------------------------------------------
def _well(ws, value) -> Optional[str]:
    """A well's id from its id or its name; None when none was given."""
    if value is None or str(value).strip() == '':
        return None
    v = str(value).strip()
    wells = ws.wells()
    for w in wells:
        if w['id'] == v:
            return w['id']
    hit = [w for w in wells if (w.get('display') or '').lower() == v.lower() or w['id'].lower() == v.lower()]
    if len(hit) == 1:
        return hit[0]['id']
    raise ToolError(f'no well {v!r} at this site; the wells are: ' + (', '.join(f"{w.get('display') or w['id']} ({w['id']})" for w in wells) or 'none'))


def _plain(o: Any) -> Any:
    """A page as plain JSON: the program's own working arrays (keys starting with an underscore) left out."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items() if not str(k).startswith('_')}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, (str, int, bool)) or o is None:
        return o
    if isinstance(o, float):
        return o if o == o and abs(o) != float('inf') else None
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return _plain(o.item())
        if isinstance(o, np.ndarray):
            return f'(an array of {o.size} values, left out)'
    except ImportError:
        pass
    return str(o)


def _paged(mod: str, call: Callable, well_arg: bool = False):
    """A tool from a module with page(ws, ...) and text(page)."""
    def run(ws, a: dict) -> Tuple[str, Any]:
        import importlib
        m = importlib.import_module(f'.{mod}', __package__)
        p = call(m, ws, _well(ws, a.get('well')) if well_arg else None)
        return m.text(p), p
    return run


def _t_site(ws, a: dict) -> Tuple[str, Any]:
    wells = ws.wells()
    rows = []
    for w in wells:
        s = w.get('surface') or {}
        rows.append({'id': w['id'], 'name': w.get('display') or w['id'], 'kind': w.get('kind'), 'files': len(w.get('files') or []),
                     'surface_position': bool(s.get('lat') is not None or s.get('latitude') is not None), 'survey': bool(w.get('trajectory')),
                     'formation': (w.get('formation') or None)})
    page = {'site': ws.manifest.get('name'), 'wells': rows, 'program_version': _version()}
    lines = [f"Site: {page['site']} - {len(rows)} well(s). GEA-Program {page['program_version']}."]
    for r in rows:
        lines.append(f"  {r['name']} (id {r['id']}): {r['kind']}, {r['files']} file(s) attached"
                     + (', surface position declared' if r['surface_position'] else ', no surface position declared') + (', survey attached' if r['survey'] else ''))
    from . import public_pad as PP
    cr = PP.credit(ws)
    if cr:
        page['public_source'] = cr
        lines.append(cr['line'])
    lines.append('Ask the other tools by a well\'s name or id; a tool with no well named answers for the whole site.')
    return '\n'.join(lines), page


def _t_well(ws, a: dict) -> Tuple[str, Any]:
    wid = _well(ws, a.get('well'))
    if wid is None:
        raise ToolError('name the well: its name or its id, as the `site` tool lists them')
    w = _plain(ws.well(wid))
    return json.dumps(w, indent=1, sort_keys=True), w


def _t_invoice(ws, a: dict) -> Tuple[str, Any]:
    from . import invoice_check as IC
    p = IC.page(ws, _well(ws, a.get('well')))
    if not p.get('check'):
        return 'Invoice cross-check: the site has no well.', p
    return IC.text(p['check']), p


def _t_frac_treatment(ws, a: dict) -> Tuple[str, Any]:
    from . import frac_treatment as FT
    p = FT.page(ws, _well(ws, a.get('well')), str(a['stage']) if a.get('stage') not in (None, '') else None)
    if p.get('detail'):
        p['detail'] = {k: v for k, v in p['detail'].items() if k != 'traces'}       # the charts' points are for the screen
        fo = p['detail'].get('falloff')
        if isinstance(fo, dict):
            p['detail']['falloff'] = {k: v for k, v in fo.items() if k not in ('g', 'pressure', 'gdpdg', 'minutes_at_g')}
    return FT.text(p), p


def _t_alerts(ws, a: dict) -> Tuple[str, Any]:
    from . import alerts as AL
    n = int(min(max(int(a.get('n') or 50), 1), 500))
    rows = AL.recent(ws, n)
    if not rows:
        return 'No alert has been raised at this site.', {'alerts': []}
    lines = [f'The last {len(rows)} alert(s), newest first:']
    for r in rows:
        lines.append(f"  {r.get('utc', '')}  {r.get('event', '')}  {r.get('well') or r.get('well_id') or ''}  {r.get('detail') or r.get('what') or ''}".rstrip())
    return '\n'.join(lines), {'alerts': rows}


def _t_audit(ws, a: dict) -> Tuple[str, Any]:
    n = int(min(max(int(a.get('n') or 50), 1), 500))
    rows = ws.audit_log(n)
    if not rows:
        return 'The audit log is empty.', {'audit': []}
    lines = [f'The last {len(rows)} line(s) of the audit log, oldest first:']
    for r in rows:
        d = json.dumps(r.get('detail'), sort_keys=True, default=str) if r.get('detail') else ''
        lines.append(f"  {r.get('utc', r.get('time', ''))}  {r.get('actor', '')}  {r.get('action', '')}  {d[:300]}".rstrip())
    return '\n'.join(lines), {'audit': rows}


def _t_help(ws, a: dict) -> Tuple[str, Any]:
    from . import helplib as H
    t = str(a.get('topic') or '').strip()
    if not t:
        tp = [x for x in H.topics() if x['present']]
        return 'The help topics (ask again with one as `topic`):\n' + '\n'.join(f"  {x['topic']}: {x['title']} - {x['summary']}" for x in tp), {'topics': tp}
    p = H.page(t)
    if p is None:
        raise ToolError(f'no help topic {t!r}; ask with no topic for the list')
    return p, {'topic': t, 'markdown': p}


_W = {'well': {'type': 'string', 'description': "A well's name or id, as the `site` tool lists them. Left out: the whole site, or the page's own choice of well."}}
_N = {'n': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'description': 'How many, newest; 50 when left out.'}}

# name, title, what it reads, its own arguments, how it runs
TOOLS: List[tuple] = [
    ('site', 'The site and its wells', "The site's name and every well: its name, id, kind, attached files, and whether a surface position and a survey are declared. Start here.", {}, _t_site),
    ('well', 'One well as declared', "Everything declared for one well, as the program keeps it: its files, channels, surface position, survey, operations and formation.", {'well': _W['well']}, _t_well),
    ('rig_time', 'Rig time', "What the rig did each second of every drilling record: hours by state, drilling connections weight to weight, trips by speed, and the time lost against the targets.",
     {}, _paged('rig_state', lambda m, ws, w: m.page(ws))),
    ('drill_string', 'Drill string', "The declared drill-string runs of a well: the components with their hours and footage against their service intervals, and the motor's statistics.",
     _W, _paged('drill_string', lambda m, ws, w: m.page(ws, w), True)),
    ('roadmap', 'Drilling roadmap', "For every stretch of hole, the weight on bit, rotary speed, flow and differential pressure the fastest quarter of the pad's stands were drilled with, the fastest well there, "
     "and each well checked against the roadmap made from the others.", {'well': {**_W['well'], 'description': "The well the next stand is given for; left out, the well whose record ends last."}},
     _paged('roadmap', lambda m, ws, w: m.page(ws, w), True)),
    ('benchmark', 'Benchmark across wells', "The wells of the pad against one another: feet per day, on-bottom rate, connections and trips for each, ranked, and each well's on-bottom time against the pad's best - "
     "every stretch at the rate of the fastest well through it.", {}, _paged('benchmark', lambda m, ws, w: m.page(ws))),
    ('drilling_alerts', 'Drilling alerts', "Washout, motor stall and tight hole as rules replayed over each well's drilling record: what each would have said, when, and whether a trip out of the hole followed.",
     {}, _paged('dysfunction', lambda m, ws, w: m.page(ws))),
    ('torque_drag', 'Torque and drag', "A well's pick-up, slack-off and rotating hook loads from the declared string, mud and friction factors, against the rig's own points.",
     _W, _paged('torque_drag', lambda m, ws, w: m.page(ws, 'workspace', w), True)),
    ('hydraulics', 'Hydraulics and ECD', "A well's standpipe pressure part by part, the equivalent circulating density against the pore-pressure and fracture window, surge and swab.",
     _W, _paged('hydraulics', lambda m, ws, w: m.page(ws, 'workspace', w), True)),
    ('pore_pressure', 'Pore pressure', "A well's pore pressure and fracture gradient from its logs or the d-exponent, with its measured pressures and the mud-weight window.",
     _W, _paged('pore_pressure', lambda m, ws, w: m.page(ws, 'workspace', w), True)),
    ('casing', 'Casing and kick tolerance', "A well's casing strings checked for burst, collapse and tension, and the kick each open hole can take.",
     _W, _paged('casing_design', lambda m, ws, w: m.page(ws, 'workspace', w), True)),
    ('collision', 'Well collision check', "Every surveyed well against every other by the separation factor, with each well's positional uncertainty.", {}, _paged('collision', lambda m, ws, w: m.page(ws))),
    ('plan_vs_actual', 'Plan against actual', "A surveyed well against its attached directional plan: the distance from the plan at each station against the survey's own uncertainty, and the projection to the bit.",
     _W, _paged('wellplan', lambda m, ws, w: m.page(ws, w), True)),
    ('frac_hits', 'Frac hits', "What each fracturing stage did to the pressure in the monitored wells around it: none, poroelastic, mixed or a frac hit.", {}, _paged('frac_hits', lambda m, ws, w: m.page(ws))),
    ('kick_detection', 'Kick and loss detection', "Every drilling record replayed for kicks and losses: the flowback at each pumps-off against the fingerprint of the connections before it, "
     "and the pits while circulating against their own trend, each event named by what the return flow did.", {}, _paged('kick', lambda m, ws, w: m.page(ws))),
    ('frac_treatment', 'Frac treatment', "Each fracturing stage from its own pumping record: what was pumped, the ISIP with its range, the friction and its two parts from a step-down, the pressure after the shut-in, "
     "a step-rate test; for one stage, every shut-in in it.",
     {'well': _W['well'], 'stage': {'type': 'string', 'description': "The stage to give in full (1, 3R, 6A); left out, the first stage that was read."}}, _t_frac_treatment),
    ('fluid_balance', 'Fluid balance', "Every barrel into or out of a formation through the site's wells, the storage the pressure says against the storage declared, the Hall plot and the neighbours.",
     {}, _paged('fluid', lambda m, ws, w: m.page(ws))),
    ('pressure_plume', 'Pressure plume (SIMULATION)', "The pressure rise around the injection wells on a grid through time: the area of review, each mapped fault's rise. A SIMULATION on the declared formation.",
     {}, _paged('plume', lambda m, ws, w: m.page(ws))),
    ('fluid_front', 'Fluid front (SIMULATION)', "Where the injected water itself has gone: how far it fills the thickness, when it reaches each producer and its share of that stream. A SIMULATION.",
     {}, _paged('front', lambda m, ws, w: m.page(ws))),
    ('calibration', 'Calibration (SIMULATION)', "The plume and the front run over the uncertain numbers and ranked against the measured pressures: the kept result as it stands, every forecast as a range, "
     "and what to measure next. A SIMULATION; this reads the kept result and runs nothing.", {}, _paged('calibration', lambda m, ws, w: m.page(ws))),
    ('decline', 'Decline curves', "A producing well's fitted decline, its forecast to the economic limit, the EUR and the present value at the declared terms.", _W, _paged('decline', lambda m, ws, w: m.page(ws, w), True)),
    ('invoice_check', 'Invoice check', "The drilling contractor's invoice day by day against the daily reports and the drilling record: each day agrees, is in question or differs, with the dollars in question.",
     _W, _t_invoice),
    ('seismicity', 'Operations and seismicity', "Each well's drilling, completion and disposal windows against the earthquake catalogue attached to the site, with the protocol levels reached.",
     {}, _paged('op_seismicity', lambda m, ws, w: m.page(ws, 'workspace'))),
    ('alerts', 'Program alerts', "The alerts the program has raised at this site, newest first: earthquakes near an operation, a service interval used up, a frac hit, a drilling alert.", _N, _t_alerts),
    ('audit', 'Audit log', "The last lines of the site's audit log: who declared, attached, removed, ran or wrote what, and when.", _N, _t_audit),
    ('help', 'Help pages', "The program's own help: with no topic, the list of topics; with one, that page - what a tab does, its arithmetic, the number to check, and what it will not call a measurement.",
     {'topic': {'type': 'string', 'description': 'A help topic, such as roadmap or rig-time. Left out: the list of topics.'}}, _t_help),
]
NAMES: Tuple[str, ...] = tuple(t[0] for t in TOOLS)
_BY_NAME = {t[0]: t for t in TOOLS}
_JSON_ARG = {'json': {'type': 'boolean', 'description': "True for the page as JSON (the program's own structure) instead of its text. Left out: the text."}}


def tool_definition(name: str) -> dict:
    n, title, desc, args, _ = _BY_NAME[name]
    return {'name': n, 'title': title, 'description': desc,
            'inputSchema': {'type': 'object', 'properties': {**args, **({} if n == 'help' else _JSON_ARG)}, 'additionalProperties': False},
            'annotations': {'title': title, 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}}


def call_tool(ws, name: str, arguments: Optional[dict], max_chars: int = MAX_CHARS) -> dict:
    """One tool's answer: {'text', 'is_error', 'truncated'}. Raises KeyError for a name that is no tool."""
    n, _, _, args, fn = _BY_NAME[name]
    a = dict(arguments or {})
    allowed = set(args) | ({'json'} if n != 'help' else set())
    try:
        extra = sorted(set(a) - allowed)
        if extra:
            raise ToolError(f'{n} takes ' + (', '.join(sorted(allowed)) or 'no arguments') + f'; not {", ".join(extra)}')
        for k in ('well', 'topic'):
            if k in a and a[k] is not None and not isinstance(a[k], str):
                raise ToolError(f'{k} must be text')
        if 'n' in a and a['n'] is not None and (isinstance(a['n'], bool) or not isinstance(a['n'], (int, float))):
            raise ToolError('n must be a number')
        text, page = fn(ws, a)
        if a.get('json') is True and page is not None:
            text = json.dumps(_plain(page), indent=1, sort_keys=True, default=str)
        err = False
    except ToolError as e:
        text, err = str(e), True
    except Exception as e:                                 # a page that cannot be computed says why; the server goes on
        msg = e.args[0] if (isinstance(e, (KeyError, ValueError)) and e.args) else f'{type(e).__name__}: {e}'
        text, err = f'{n} could not be read: {msg}', True
    trunc = len(text) > max_chars
    if trunc:
        text = text[:max_chars] + f'\n[cut at {max_chars:,} of {len(text):,} characters: ask for one well, or raise the limit on the Assistant access tab]'
    return {'text': text, 'is_error': err, 'truncated': trunc}


# --------------------------------------------------------------------------------------------------------------
# the protocol
# --------------------------------------------------------------------------------------------------------------
class Server:
    """One client's conversation. `handle(message)` takes a parsed JSON-RPC message (or a batch) and returns what to
    write back, or None for a notification."""

    def __init__(self, ws, allow: Optional[List[str]] = None):
        self.ws, self.allow = ws, allow
        self.client: Optional[dict] = None
        self.legacy_version: Optional[str] = None

    # -- the envelope --------------------------------------------------------------------------------------------------
    @staticmethod
    def _err(mid, code: int, message: str, data: Optional[dict] = None) -> dict:
        e = {'code': code, 'message': message}
        if data is not None:
            e['data'] = data
        return {'jsonrpc': '2.0', 'id': mid, 'error': e}

    def _ok(self, mid, result: dict, modern: bool) -> dict:
        if modern:
            result = {'resultType': 'complete', **result, '_meta': {K_SERVER: server_info()}}
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def handle(self, msg) -> Optional[Any]:
        if isinstance(msg, list):                              # a batch (revision 2025-03-26 allowed them)
            if not msg:
                return self._err(None, E_REQUEST, 'an empty batch')
            out = [r for r in (self._one(m) for m in msg) if r is not None]
            return out or None
        return self._one(msg)

    def _one(self, msg) -> Optional[dict]:
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            if isinstance(msg, dict) and 'method' not in msg and ('result' in msg or 'error' in msg):
                return None                                    # a response: this server asks the client nothing, so there is none to take
            return self._err(msg.get('id') if isinstance(msg, dict) else None, E_REQUEST, 'not a JSON-RPC 2.0 request')
        method, mid = msg['method'], msg.get('id')
        if 'id' not in msg:
            return None                                        # a notification (initialized, cancelled): nothing to answer
        params = msg.get('params')
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._err(mid, E_PARAMS, 'params must be an object')
        meta = params.get('_meta') if isinstance(params.get('_meta'), dict) else {}
        modern = K_VERSION in meta or method == 'server/discover'
        if modern:
            asked = meta.get(K_VERSION)
            if isinstance(meta.get(K_CLIENT), dict):
                self.client = {k: meta[K_CLIENT].get(k) for k in ('name', 'version')}
            if method != 'server/discover' and asked not in MODERN:
                return self._err(mid, E_VERSION, 'Unsupported protocol version', {'supported': list(SUPPORTED), 'requested': asked})
            if method == 'server/discover' and asked is not None and asked not in MODERN:
                return self._err(mid, E_VERSION, 'Unsupported protocol version', {'supported': list(SUPPORTED), 'requested': asked})
        try:
            if method == 'server/discover':
                return self._ok(mid, {'supportedVersions': list(SUPPORTED), 'capabilities': {'tools': {}}, 'instructions': INSTRUCTIONS, 'ttlMs': 3600000, 'cacheScope': 'private'}, True)
            if method == 'initialize':
                if modern:
                    return self._err(mid, E_METHOD, f'initialize is not part of revision {MODERN[0]}: send requests with their protocol version in _meta')
                asked = params.get('protocolVersion')
                self.legacy_version = asked if asked in LEGACY else LEGACY[0]
                if isinstance(params.get('clientInfo'), dict):
                    self.client = {k: params['clientInfo'].get(k) for k in ('name', 'version')}
                return self._ok(mid, {'protocolVersion': self.legacy_version, 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': server_info(), 'instructions': INSTRUCTIONS}, False)
            if method == 'ping' and not modern:
                return self._ok(mid, {}, False)
            if method == 'tools/list':
                res: Dict[str, Any] = {'tools': [tool_definition(n) for n in offered(self.ws, self.allow)]}
                if modern:
                    res.update(ttlMs=60000, cacheScope='private')
                return self._ok(mid, res, modern)
            if method == 'tools/call':
                name, arguments = params.get('name'), params.get('arguments')
                if not isinstance(name, str) or (arguments is not None and not isinstance(arguments, dict)):
                    return self._err(mid, E_PARAMS, 'tools/call needs a tool name and, if any, its arguments as an object')
                if name not in offered(self.ws, self.allow):
                    log_call(self.ws, {'utc': _utc(), 'tool': name[:80], 'client': self.client, 'ok': False, 'turned_away': 'withheld' if name in NAMES else 'no such tool', 'chars': 0})
                    return self._err(mid, E_PARAMS, f'Unknown tool: {name}')
                t0 = time.time()
                r = call_tool(self.ws, name, arguments, settings(self.ws)['max_chars'])
                log_call(self.ws, {'utc': _utc(), 'tool': name, 'arguments': {k: v for k, v in (arguments or {}).items() if k != '_meta'}, 'client': self.client, 'ok': not r['is_error'],
                                   'chars': len(r['text']), 'truncated': r['truncated'], 'ms': int((time.time() - t0) * 1000)})
                return self._ok(mid, {'content': [{'type': 'text', 'text': r['text']}], 'isError': r['is_error']}, modern)
            if method in ('resources/list', 'prompts/list', 'resources/templates/list') and not modern:
                key = {'resources/list': 'resources', 'prompts/list': 'prompts', 'resources/templates/list': 'resourceTemplates'}[method]
                return self._ok(mid, {key: []}, False)        # some clients ask whatever was declared: an empty list is the true answer
            return self._err(mid, E_METHOD, f'Method not found: {method}')
        except Exception as e:                                 # never a traceback on the wire, never a dead server
            return self._err(mid, E_INTERNAL, f'{type(e).__name__}: {e}')


def serve(ws, stdin=None, stdout=None, allow: Optional[List[str]] = None) -> int:
    """Read one JSON message a line from `stdin` until it closes, answering on `stdout`. Nothing but protocol messages
    is ever written to `stdout`: while a tool runs, anything the program would print goes to the error stream."""
    fin = stdin if stdin is not None else sys.stdin.buffer
    fout = stdout if stdout is not None else sys.stdout.buffer
    srv = Server(ws, allow)
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        while True:
            line = fin.readline()
            if not line:
                return 0
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode('utf-8') if isinstance(line, (bytes, bytearray)) else line)
            except (ValueError, UnicodeDecodeError):
                out: Any = Server._err(None, E_PARSE, 'Parse error: a message is one line of JSON')
            else:
                out = srv.handle(msg)
            if out is None:
                continue
            fout.write((json.dumps(out, ensure_ascii=True, separators=(',', ':'), default=str) + '\n').encode('ascii'))
            fout.flush()
    finally:
        sys.stdout = real_stdout


# --------------------------------------------------------------------------------------------------------------
# the client's side: how an assistant is told to start this
# --------------------------------------------------------------------------------------------------------------
def launch_command(ws_path: str, allow: Optional[List[str]] = None) -> dict:
    """The command an MCP client runs. The console interpreter, not the windowless one: the conversation is its
    standard streams."""
    exe = sys.executable or 'python'
    d, b = os.path.split(exe)
    if b.lower() == 'pythonw.exe' and os.path.isfile(os.path.join(d, 'python.exe')):
        exe = os.path.join(d, 'python.exe')
    args = ['-m', 'gea', 'mcp', '--workspace', os.path.abspath(ws_path)]
    if allow:
        args += ['--allow', ','.join(allow)]
    return {'command': exe, 'args': args}


def client_config(ws_path: str, allow: Optional[List[str]] = None) -> dict:
    return {'mcpServers': {'gea': launch_command(ws_path, allow)}}


def claude_desktop_paths() -> List[str]:
    """Where Claude Desktop keeps its configuration on this computer: the documented place, and on Windows the one a
    Microsoft Store install uses instead, when that install is there."""
    home = os.path.expanduser('~')
    if os.name == 'nt':
        out = []
        ad = os.environ.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming')
        out.append(os.path.join(ad, 'Claude', 'claude_desktop_config.json'))
        pk = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local'), 'Packages')
        try:
            for n in sorted(os.listdir(pk)):
                if n.lower().startswith('claude_') or n.lower().startswith('anthropicclaude_'):
                    out.append(os.path.join(pk, n, 'LocalCache', 'Roaming', 'Claude', 'claude_desktop_config.json'))
        except OSError:
            pass
        return out
    if sys.platform == 'darwin':
        return [os.path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json')]
    return [os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(home, '.config'), 'Claude', 'claude_desktop_config.json')]


def claude_desktop_status(ws_path: str, paths: Optional[List[str]] = None) -> dict:
    want = launch_command(ws_path)
    rows = []
    for p in (paths if paths is not None else claude_desktop_paths()):
        r = {'path': p, 'folder_exists': os.path.isdir(os.path.dirname(p)), 'exists': os.path.isfile(p), 'entry': None, 'this_site': False, 'readable': True}
        if r['exists']:
            try:
                with open(p, encoding='utf-8') as f:
                    cfg = json.load(f)
                e = (cfg.get('mcpServers') or {}).get('gea') if isinstance(cfg, dict) else None
                r['entry'] = e
                r['this_site'] = bool(isinstance(e, dict) and e.get('args') and os.path.abspath(ws_path) in [str(x) for x in e['args']])
                r['current'] = bool(isinstance(e, dict) and e.get('command') == want['command'] and e.get('args') == want['args'])
            except (OSError, ValueError):
                r['readable'] = False
        rows.append(r)
    return {'paths': rows, 'installed': any(r['this_site'] for r in rows), 'found': any(r['folder_exists'] for r in rows)}


def install_claude_desktop(ws, actor: str, remove: bool = False, paths: Optional[List[str]] = None) -> dict:
    """Add this site's server to Claude Desktop's configuration (or take it out), leaving every other entry as it is
    and a dated copy of the file as it was. A configuration that is not readable JSON is left alone, and said."""
    done, skipped = [], []
    for p in (paths if paths is not None else claude_desktop_paths()):
        folder = os.path.dirname(p)
        if not os.path.isdir(folder):
            skipped.append({'path': p, 'why': 'Claude Desktop has no configuration folder here'}); continue
        cfg: Dict[str, Any] = {}
        raw = None
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    raw = f.read()
                cfg = json.loads(raw) if raw.strip() else {}
                if not isinstance(cfg, dict) or not isinstance(cfg.get('mcpServers', {}), dict):
                    raise ValueError('not an object')
            except (OSError, ValueError):
                skipped.append({'path': p, 'why': 'the file is there and is not JSON this program can read: left as it is'}); continue
        servers = dict(cfg.get('mcpServers') or {})
        if remove:
            if 'gea' not in servers:
                skipped.append({'path': p, 'why': 'no gea entry in it'}); continue
            servers.pop('gea')
        else:
            servers['gea'] = launch_command(ws.path)
        cfg['mcpServers'] = servers
        backup = None
        if raw is not None:
            backup = p + '.before-gea-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            with open(backup, 'w', encoding='utf-8') as f:
                f.write(raw)
        tmp = p + '.gea-tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, p)
        done.append({'path': p, 'backup': backup})
    if done:
        ws.audit(actor, 'mcp.claude_desktop', {'removed' if remove else 'added': [d['path'] for d in done], 'backups': [d['backup'] for d in done if d['backup']]})
    return {'done': done, 'skipped': skipped, 'removed': remove}


def page(ws) -> dict:
    s = settings(ws)
    off = set(offered(ws))
    cd = claude_desktop_status(ws.path)
    return {'status': 'OK', 'launch': launch_command(ws.path), 'config': client_config(ws.path), 'versions': list(SUPPORTED),
            'tools': [{'name': n, 'title': t, 'description': d, 'arguments': sorted(a), 'offered': n in off} for n, t, d, a, _ in TOOLS],
            'settings': {'tools': s['tools'], 'max_chars': s['max_chars'], 'source': s['source']}, 'bounds': {'max_chars': list(MAX_CHARS_BOUNDS)},
            'claude_desktop': cd, 'log': access_log(ws, 50),
            'lines': [f"{len(off)} of {len(TOOLS)} tools offered, every one read-only; an answer carries {s['max_chars']:,} characters at most.",
                      ('Claude Desktop on this computer is set to start this site\'s server.' if cd['installed'] else
                       'Claude Desktop on this computer is not set to start this site\'s server.' if cd['found'] else 'No Claude Desktop configuration folder was found on this computer.'),
                      'What a tool returns goes into the assistant\'s conversation, and so to whoever runs the model behind it. Offering a tool is deciding that its page may leave the site.']}


def text(p: dict) -> str:
    out = ['Assistant access (MCP)', '=' * 22]
    out.extend(p['lines'])
    out.append('  start it with: ' + p['launch']['command'] + ' ' + ' '.join(p['launch']['args']))
    for t in p['tools']:
        out.append(f"  {'offered ' if t['offered'] else 'withheld'}  {t['name']:16s}  {t['title']}")
    for r in p['log'][:10]:
        out.append(f"  {r.get('utc', '')}  {r.get('tool', ''):16s}  {'answered' if r.get('ok') else 'could not'}  {r.get('chars', 0):,} characters  {(r.get('client') or {}).get('name') or ''}")
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------------------------
# the self-test
# --------------------------------------------------------------------------------------------------------------
def exchange(ws, messages: List[Any], allow: Optional[List[str]] = None) -> List[Any]:
    """Run the server over these messages as lines on a stream and return what it wrote, parsed: the wire, in memory."""
    import io
    fin = io.BytesIO(b''.join((m if isinstance(m, bytes) else json.dumps(m).encode('utf-8')) + b'\n' for m in messages))
    fout = io.BytesIO()
    serve(ws, fin, fout, allow)
    raw = fout.getvalue()
    lines = raw.split(b'\n')
    assert lines[-1] == b'' and all(l and b'\r' not in l for l in lines[:-1]), 'one message a line, nothing else'
    return [json.loads(l) for l in lines[:-1]]


def selftest() -> dict:
    import tempfile
    from . import dysfunction as DY
    from .workspace import Workspace
    checks = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ws = Workspace.create(os.path.join(tmp, 'ws'), 'Pad MCP', actor='selftest')
        DY.write_edr(DY.synthetic_record(True, stands=14), os.path.join(tmp, 'rig.csv'))
        wid = ws.add_well_file(os.path.join(tmp, 'rig.csv'), display='RIG-1', actor='selftest')['id']
        meta = {K_VERSION: MODERN[0], K_CLIENT: {'name': 'selftest', 'version': '1'}, 'io.modelcontextprotocol/clientCapabilities': {}}
        # a client of the earlier revisions: the handshake, then the tools
        old = exchange(ws, [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'old-client', 'version': '0.1'}}},
                            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
                            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'},
                            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'site', 'arguments': {}}},
                            {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'drilling_alerts'}}])
        checks.append({'check': 'a client of an earlier revision: the handshake at the revision it names, a ping, the tools, a call - and no answer to a notification', 'value': [len(old), old[0]['result']['protocolVersion']],
                       'pass': len(old) == 5 and old[0]['result']['protocolVersion'] == '2025-06-18' and old[0]['result']['serverInfo']['name'] == SERVER_NAME and old[0]['result']['capabilities'] == {'tools': {'listChanged': False}}
                               and old[1]['result'] == {} and [t['name'] for t in old[2]['result']['tools']] == list(NAMES) and 'resultType' not in old[2]['result']
                               and 'RIG-1' in old[3]['result']['content'][0]['text'] and old[3]['result']['isError'] is False and 'motor stall' in old[4]['result']['content'][0]['text']})
        new = exchange(ws, [{'jsonrpc': '2.0', 'id': 'd', 'method': 'server/discover', 'params': {'_meta': meta}},
                            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'_meta': meta}},
                            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'well', 'arguments': {'well': 'rig-1'}, '_meta': meta}},
                            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list', 'params': {'_meta': {**meta, K_VERSION: '2031-01-01'}}},
                            {'jsonrpc': '2.0', 'id': 4, 'method': 'ping', 'params': {'_meta': meta}},
                            {'jsonrpc': '2.0', 'id': 5, 'method': 'initialize', 'params': {'_meta': meta}}])
        checks.append({'check': 'a client of revision 2026-07-28: discovery, every result complete and signed, a version it does not speak named with the ones it does, no ping and no handshake', 'value': [r.get('error', {}).get('code') for r in new],
                       'pass': new[0]['result']['supportedVersions'][0] == MODERN[0] and new[0]['result']['resultType'] == 'complete' and new[0]['result']['capabilities'] == {'tools': {}}
                               and new[1]['result']['resultType'] == 'complete' and new[1]['result']['cacheScope'] == 'private' and new[1]['result']['ttlMs'] > 0 and new[1]['result']['_meta'][K_SERVER]['version'] == _version()
                               and new[2]['result']['resultType'] == 'complete' and wid in new[2]['result']['content'][0]['text']
                               and new[3]['error']['code'] == E_VERSION and new[3]['error']['data'] == {'supported': list(SUPPORTED), 'requested': '2031-01-01'}
                               and new[4]['error']['code'] == E_METHOD and new[5]['error']['code'] == E_METHOD})
        defs = [tool_definition(n) for n in NAMES]
        checks.append({'check': 'every tool read-only by its own declaration, its arguments closed, its name plain', 'value': len(defs),
                       'pass': all(d['annotations']['readOnlyHint'] is True and d['annotations']['destructiveHint'] is False and d['inputSchema']['additionalProperties'] is False
                                   and d['name'].replace('_', '').isalnum() and len(d['description']) > 40 for d in defs) and len(set(NAMES)) == len(NAMES)})
        bad = exchange(ws, [b'{not json', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'declare_well', 'arguments': {}}},
                            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'well', 'arguments': {'well': 'NOPE'}}},
                            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'site', 'arguments': {'delete': True}}},
                            {'jsonrpc': '2.0', 'id': 4, 'method': 'workspace/write', 'params': {}},
                            [{'jsonrpc': '2.0', 'id': 5, 'method': 'ping'}, {'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 5}}],
                            {'id': 6, 'method': 'ping'}])
        checks.append({'check': 'what is not a request: a line that is not JSON, a tool that does not exist, a well that does not, an argument it does not take, a method it does not have, a batch, a message that is not JSON-RPC',
                       'value': [(r if isinstance(r, list) else r).__class__.__name__ for r in bad],
                       'pass': bad[0]['error']['code'] == E_PARSE and bad[0]['id'] is None and bad[1]['error']['code'] == E_PARAMS and 'Unknown tool' in bad[1]['error']['message']
                               and bad[2]['result']['isError'] is True and 'RIG-1' in bad[2]['result']['content'][0]['text'] and bad[3]['result']['isError'] is True and 'not delete' in bad[3]['result']['content'][0]['text']
                               and bad[4]['error']['code'] == E_METHOD and isinstance(bad[5], list) and len(bad[5]) == 1 and bad[5][0]['result'] == {} and bad[6]['error']['code'] == E_REQUEST})
        set_settings(ws, 'selftest', tools=['site', 'roadmap', 'help'], max_chars=2000, source='selftest')
        few = exchange(ws, [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'audit'}},
                            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'help', 'arguments': {'topic': 'roadmap'}}}])
        narrow = exchange(ws, [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}], allow=['site', 'audit'])
        checks.append({'check': 'the tools offered are the site\'s choice: a withheld tool is not listed and cannot be called, a run started with fewer offers fewer and never more, a long answer is cut and says so',
                       'value': [[t['name'] for t in few[0]['result']['tools']], [t['name'] for t in narrow[0]['result']['tools']]],
                       'pass': [t['name'] for t in few[0]['result']['tools']] == ['site', 'roadmap', 'help'] and few[1]['error']['code'] == E_PARAMS and [t['name'] for t in narrow[0]['result']['tools']] == ['site']
                               and '[cut at 2,000 of' in few[2]['result']['content'][0]['text'] and len(few[2]['result']['content'][0]['text']) < 2200})
        log = access_log(ws, 100)
        checks.append({'check': 'every call a line in the access log: the tool, who asked, whether it answered - and the calls turned away', 'value': len(log),
                       'pass': len(log) == 8 and log[0]['tool'] == 'help' and log[0]['truncated'] and any(r['tool'] == 'well' and not r['ok'] for r in log)
                               and [r.get('turned_away') for r in log if r.get('turned_away')] == ['withheld', 'no such tool']
                               and any((r.get('client') or {}).get('name') == 'old-client' for r in log) and any((r.get('client') or {}).get('name') == 'selftest' for r in log)})
        cfgp = os.path.join(tmp, 'Claude', 'claude_desktop_config.json'); os.makedirs(os.path.dirname(cfgp))
        with open(cfgp, 'w', encoding='utf-8') as f:
            json.dump({'mcpServers': {'other': {'command': 'x', 'args': []}}, 'theme': 'dark'}, f)
        ins = install_claude_desktop(ws, 'selftest', paths=[cfgp, os.path.join(tmp, 'nowhere', 'claude_desktop_config.json')])
        with open(cfgp, encoding='utf-8') as f:
            after = json.load(f)
        st = claude_desktop_status(ws.path, [cfgp])
        rem = install_claude_desktop(ws, 'selftest', remove=True, paths=[cfgp])
        with open(cfgp, encoding='utf-8') as f:
            after_rm = json.load(f)
        with open(cfgp, 'w', encoding='utf-8') as f:
            f.write('{broken')
        brk = install_claude_desktop(ws, 'selftest', paths=[cfgp])
        with open(cfgp, encoding='utf-8') as f:
            still = f.read()
        checks.append({'check': 'added to an assistant\'s configuration: the other entries and settings kept, a dated copy of the file as it was, taken out again, and a file that is not JSON left alone', 'value': [len(ins['done']), len(ins['skipped']), len(brk['skipped'])],
                       'pass': len(ins['done']) == 1 and len(ins['skipped']) == 1 and after['theme'] == 'dark' and after['mcpServers']['other'] == {'command': 'x', 'args': []}
                               and after['mcpServers']['gea']['args'][:3] == ['-m', 'gea', 'mcp'] and os.path.abspath(ws.path) in after['mcpServers']['gea']['args'] and os.path.isfile(ins['done'][0]['backup'])
                               and st['installed'] and st['paths'][0]['current'] and 'gea' not in after_rm['mcpServers'] and 'other' in after_rm['mcpServers'] and not brk['done'] and still == '{broken'})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SELF-TEST'}
