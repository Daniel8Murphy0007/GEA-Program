# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Alert rules: a card for each rule that raises an alert from a drilling record.

An alert is worth what the person who gets it knows of the rule behind it. A model card (after Mitchell et al., "Model
Cards for Model Reporting", 2019) says what a model is for, what it takes in, how it is set, how it was checked and
what it must not be taken for. These rules are not models fitted to data - each is a driller's rule with thresholds set
by hand - and the card says that first. Then, for each rule:

- what it is for, and the alert it raises;
- how it decides: the rule as the module that applies it states it, taken from that module at the moment the card is
  made, so a card cannot describe a rule other than the one that runs;
- what it reads: the channels it cannot do without and the ones it reads when they are there, by the names it looks
  for (the Channels tab's own list), and what must be declared before it can run;
- its settings as they stand in this workspace: each threshold with its meaning, its default, its bounds, and where a
  changed one came from;
- when the alert is raised, and how often it has been raised here;
- on which of this workspace's wells it can run, and on which it cannot and why;
- its checks, run again on request: the rule's own self-test on a record made with the fault in it and without;
- what it will not call a measurement;
- the file that holds it, with a hash an inspector can match to the delivered source.

What a card will not say: how often the rule is right. None of these rules has been set against labelled field events -
a washout confirmed at the trip, a kick confirmed by a flow check. Its checks say it finds what was put into a made
record and stays quiet on one without; the count of alerts raised here says how often it spoke, not how often it
should have.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
KIND = 'a rule with thresholds set by hand, not a model fitted to data'
LABELLED = 'none: the rule has not been set against field events that someone confirmed - its checks are on made records'
SILENCE = "a rule's silence: no alert means the rule did not fire on this record, not that all was well"
CADENCE = 'checked every 15 minutes while the service runs'
# the rule's card: its events, the readers of the Channels tab's list it stands on, the module that states it and where in that module's own description,
# its settings, and what must be declared for it
SPECS: List[dict] = [
    {'id': 'washout', 'title': 'Off-bottom pressure falling (washout)', 'events': ('drilling.washout',), 'readers': ('washout',), 'module': 'dysfunction', 'how': ('- WASHOUT.', '- MOTOR STALL.'),
     'settings': ('washout_fall', 'washout_min_psi'), 'for': "to say, connection by connection, that the pressure it takes to pump through the string has fallen at a steady flow - before the string parts",
     'notm': ('doc', 'dysfunction', ('a washout',)), 'declared': None},
    {'id': 'stall', 'title': 'Motor stall', 'events': ('drilling.stall',), 'readers': ('stall',), 'module': 'dysfunction', 'how': ('- MOTOR STALL.', '- TIGHT HOLE.'),
     'settings': ('stall_rise', 'stall_min_psi'), 'for': "to count the times the downhole motor was stopped by its own load, each with its time and depth, and to say so when it has just happened",
     'notm': ('doc', 'dysfunction', ('a stall',)), 'declared': None},
    {'id': 'tight', 'title': 'Pick-up weight off its trend (tight hole)', 'events': ('drilling.tight',), 'readers': ('tight',), 'module': 'dysfunction', 'how': ('- TIGHT HOLE.', None),
     'settings': ('drag_sigma', 'drag_min_klbf'), 'for': "to say that the string has begun to weigh more coming up, or less going down, than the connections before it - a jump in the hole's drag",
     'notm': ('doc', 'dysfunction', ('stuck pipe',)), 'declared': None},
    {'id': 'stickslip', 'title': 'Stick-slip in the surface torque', 'events': ('drilling.stickslip',), 'readers': ('stickslip',), 'module': 'stickslip', 'how': ('- STICK-SLIP.', '- BIT BOUNCE.'),
     'also': ('Two flagged blocks running open an event', 'Both rules need a record'), 'settings': ('stickslip_swing',),
     'for': "to say that the surface torque is swinging with one period, as it does when the bit sticks and the string winds up",
     'notm': ('list', 'stickslip', ('stick-slip', 'sideways', 'a quiet record')), 'declared': None},
    {'id': 'bounce', 'title': 'Hook load jumping (bit bounce)', 'events': ('drilling.bounce',), 'readers': ('bounce',), 'module': 'stickslip', 'how': ('- BIT BOUNCE.', None),
     'also': ('Two flagged blocks running open an event', 'Both rules need a record'), 'settings': ('bounce_min_klbf', 'bounce_share'),
     'for': "to say that the hook load is jumping from second to second about its own level, as it does when the bit is bouncing on bottom",
     'notm': ('list', 'stickslip', ('bit bounce', 'sideways', 'a quiet record')), 'declared': None},
    {'id': 'sticking', 'title': 'Friction factor rising', 'events': ('drilling.sticking',), 'readers': ('sticking',), 'module': 'sticking', 'how': ('The rule:', None), 'settings': ('stick_rise',),
     'for': "to say that the hole is holding the pipe harder than it did - a slow climb in drag that the tight-hole rule follows without a word",
     'notm': ('list', 'sticking', ()), 'declared': 'the drill string, the mud and the block weight on the Torque & drag tab; without them the rule does not run'},
    {'id': 'flowback', 'title': 'Flowback above its fingerprint', 'events': ('kick.flowback',), 'readers': ('kick',), 'module': 'kick', 'how': ('- THE FLOWBACK AT EACH PUMPS-OFF.', '- THE PITS WHILE CIRCULATING.'),
     'settings': ('flowback_sigma', 'flowback_min_bbl'), 'for': "to say, at a connection, that more mud came back with the pumps off than the connections before it gave - the earliest sign a record has of a well giving fluid",
     'notm': ('list', 'kick', ()), 'declared': None},
    {'id': 'pits', 'title': 'Pit gain or fall while circulating', 'events': ('kick.gain', 'kick.loss'), 'readers': ('kick',), 'module': 'kick', 'how': ('- THE PITS WHILE CIRCULATING.', None),
     'settings': ('gain_sigma', 'gain_min_bbl', 'returns_change'), 'for': "to say that the pits have left their own trend with the pumps steady, and by what the return line did, whether it looks like a kick or like losses",
     'notm': ('list', 'kick', ()), 'declared': None},
    {'id': 'trips', 'title': 'A trip whose mud departs from the pipe moved', 'events': ('trip.fill', 'trip.loss', 'trip.unread'), 'readers': ('trips',), 'module': 'trip_sheet', 'how': ('- a trip departs when', None),
     'also': ('On a record still being written', None), 'settings': (), 'constants': (('MIN_BBL', 'bbl', 'the least departure that counts'), ('SHARE', '', 'or this share of what the pipe accounts for, with a pipe declared'),
                                                                                  ('SIGMA', '', "or this many scatters of the well's own trips, with none"), ('STEP_BBL', 'bbl', 'a change this large inside half a minute is mud moved at surface'),
                                                                                  ('FINGERPRINT_MIN', '', "like trips needed before a trip is set against the well's own")),
     'for': "to say that the hole took less mud than the pipe pulled, or gave more than the pipe run - or is losing mud - while the trip is under way",
     'notm': ('list', 'trip_sheet', ()), 'declared': "the pipe's sizes and how it is tripped, on the Trip sheet tab; without them each trip is set against the well's own other trips"},
    {'id': 'hardline', 'title': 'Bit near a hard line', 'events': ('hardline.warn', 'hardline.setback', 'hardline.crossed'), 'readers': ('hardline',), 'module': 'hardline', 'how': ('The distance is in plan', None),
     'settings': (), 'for': "to say that the bit has come within the warning distance of a declared boundary's hard line, is inside the setback, or is over the boundary",
     'notm': ('list', 'hardline', ()), 'declared': "a boundary with its setback and warning distance on the Hard line tab, and each well's surface position, elevation and survey; without them there is nothing to measure to"},
    {'id': 'stability', 'title': 'Mud against the mud-weight window', 'events': ('stability.mud', 'stability.ecd'), 'readers': ('stability',), 'module': 'stability', 'how': ('- THE WINDOW', None), 'settings': (),
     'for': "to say that the mud weight is below the window's floor in the hole drilled so far, or that the circulating density at the record's own pump rate is over the least stress above the bit",
     'notm': ('list', 'stability', ()), 'declared': "the rock's strength and the stresses on the Wellbore stability tab, and the mud and the string on the Hydraulics tab; none of it is in a drilling record"},
    {'id': 'procedure', 'title': 'A declared procedure not met', 'events': ('procedure.departed',), 'readers': ('procedures',), 'module': 'procedures', 'how': ('A RULE is one of those measures', None), 'settings': (),
     'for': "to say that a connection or a trip was not made as a rule declared from the drilling program asks, as far as a recorder can see",
     'notm': ('list', 'procedures', ()), 'declared': 'at least one rule on the Procedures tab, with where it came from; with none declared nothing is raised'},
]
# the alerts that are not rules read from a drilling record: named so that every alert the service can raise is on this page
OTHERS: Dict[str, tuple] = {
    'seismic.protocol': ('a protocol level of the traffic light reached', '#/seismic'), 'seismic.event': ('an earthquake near an operation', '#/opseis'),
    'seismic.watch_down': ('the earthquake watch not reaching the catalogue', '#/seismic'), 'seismic.watch_up': ('the earthquake watch back', '#/seismic'),
    'motor.due': ("a component's service interval used up", '#/drillstring'), 'penetrator.outside': ('a penetrator outside its rating', '#/completion'),
    'dossier.changed': ('the workspace no longer matching a dossier', '#/verify'), 'invoice.differs': ('an invoice that differs from the record', '#/invoices'),
    'frac.hit': ('a frac hit on an offset well', '#/frac'), 'tls.expiring': ("the dashboard's HTTPS certificate expiring", '#/admin'), 'condition.cleared': ('one of the standing conditions no longer so', '#/alarms'),
}
_LOCK = threading.Lock()
_CHECKS: Dict[tuple, dict] = {}


class AlertCardError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha(module: str) -> str:
    with open(os.path.join(_HERE, f'{module}.py'), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _flat(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').strip()


def _passage(doc: str, start: str, end: Optional[str] = None) -> str:
    """The passage of a module's own description that begins `start`: up to the line that begins `end`, or to the next empty line."""
    lines = (doc or '').splitlines()
    i = next((k for k, l in enumerate(lines) if l.strip().startswith(start)), None)
    if i is None:
        return ''
    out = []
    for l in lines[i:]:
        if (end and l.strip().startswith(end)) or (out and not l.strip()):
            break
        out.append(l)
    return _flat(' '.join(out)).lstrip('- ')


def _when(doc: str) -> str:
    """A module's own statement of what its alert is raised for, as a sentence: its heading words taken off."""
    s = _flat(doc)
    head, sep, rest = s.partition(': ')
    if sep and 'raised for' in head:
        s = ('in a record still being written, ' if 'still being written' in head else '') + rest
    return s[:1].upper() + s[1:]


def _mod(name: str):
    return importlib.import_module(f'.{name}', __package__)


def _not_measured(spec: dict) -> List[str]:
    kind, module, keys = spec['notm']
    M = _mod(module)
    if kind == 'list':
        items = [x for x in M.NOT_A_MEASUREMENT if not keys or any(x.startswith(k) for k in keys)]
    else:
        para = _passage(M.__doc__, 'What it will not call a measurement:')[len('What it will not call a measurement:'):]
        parts = [p.strip(' ;') for p in re.split(r';\s+(?=(?:and )?(?:a |stuck ))', para)]
        items = [re.sub(r'^and ', '', p) for p in parts if any(re.sub(r'^and ', '', p).startswith(k) for k in keys)]
    return [x for x in items if x] + [SILENCE]


def _settings(spec: dict, ws) -> List[dict]:
    out = []
    if spec['settings']:
        from . import dysfunction as DY
        from . import kick as KD
        M = KD if spec['module'] == 'kick' else DY
        cur = M.settings(ws) if ws is not None else dict(M.DEFAULTS)
        srcs = cur.get('sources') or {}
        for k in spec['settings']:
            d = float(M.DEFAULTS[k]); v = float(cur.get(k, d)); lo, hi = M.BOUNDS[k]
            out.append({'setting': k, 'words': M.WORDS.get(k, ''), 'value': v, 'default': d, 'bounds': [lo, hi], 'changed': abs(v - d) > 1e-12,
                        'source': (srcs.get(k) or (cur.get('source') if abs(v - d) > 1e-12 else '') or ''), 'where': 'a setting of this workspace'})
    for name, unit, words in spec.get('constants', ()):
        v = getattr(_mod(spec['module']), name)
        out.append({'setting': name, 'words': words, 'value': float(v), 'default': float(v), 'bounds': None, 'changed': False, 'source': '', 'unit': unit, 'where': 'fixed in the program'})
    return out


def _reads(spec: dict) -> dict:
    from . import channels as CH
    R = {r['id']: r for r in CH.readers()}
    needs, reads, either = [], [], []
    for rid in spec['readers']:
        r = R[rid]
        needs += [{'what': lab, 'looks_for': list(names)[:6]} for lab, names in r['needs']]
        either += [{'what': lab, 'looks_for': list(names)[:6]} for lab, names in r['either']]
        reads += [{'what': lab, 'looks_for': list(names)[:6]} for lab, names in r['reads']]
    return {'needs': needs, 'either': either, 'reads': reads, 'tab': R[spec['readers'][0]]['tab']}


def card(spec: dict, ws=None) -> dict:
    M = _mod(spec['module'])
    how = _passage(M.__doc__, *spec['how']) if spec['how'] else ''
    for start, end in ([spec['also']] if spec.get('also') else []):
        extra = _passage(M.__doc__, start, end)
        how = (how + ' ' + extra).strip() if extra else how
    raised = _when(getattr(M, 'live_events').__doc__ if spec['module'] not in ('stickslip', 'sticking') else _mod('dysfunction').live_events.__doc__)
    rd = _reads(spec)
    files = [spec['module']] + (['dysfunction'] if spec['module'] in ('stickslip', 'sticking') else [])
    return {'id': spec['id'], 'title': spec['title'], 'events': list(spec['events']), 'tab': rd['tab'], 'kind': KIND, 'labelled_data': LABELLED, 'what_for': spec['for'], 'how': how,
            'raised': f"{raised.rstrip('.')} ({CADENCE}).", 'reads': {k: rd[k] for k in ('needs', 'either', 'reads')}, 'declared': spec['declared'], 'settings': _settings(spec, ws),
            'not_a_measurement': _not_measured(spec), 'components': [{'file': f'gea/{m}.py', 'sha256': _sha(m)} for m in files], 'checks': checks_kept(spec['id'])}


def _selftest_of(spec: dict):
    return _mod({'washout': 'dysfunction', 'stall': 'dysfunction', 'tight': 'dysfunction'}.get(spec['id'], spec['module'])).selftest


def checks_kept(card_id: str) -> Optional[dict]:
    spec = next((s for s in SPECS if s['id'] == card_id), None)
    if spec is None:
        raise KeyError(f'no alert rule {card_id}')
    with _LOCK:
        return _CHECKS.get((spec['module'], _sha(spec['module'])))


def run_checks(card_id: str) -> dict:
    """The rule's own self-test, run now: the module's checks on records made with the fault in them and without. Kept
    until the module's file changes; the rules of one module share their checks."""
    spec = next((s for s in SPECS if s['id'] == card_id), None)
    if spec is None:
        raise KeyError(f'no alert rule {card_id}')
    key = (spec['module'], _sha(spec['module']))
    with _LOCK:
        if key in _CHECKS:
            return _CHECKS[key]
    t0 = time.time()
    r = _selftest_of(spec)()
    names = [c.get('name') or c.get('check') or '' for c in r['checks']]
    res = {'ok': bool(r['ok']), 'passed': sum(1 for c in r['checks'] if c.get('pass')), 'of': len(r['checks']), 'label': r.get('label', ''), 'ran_utc': _utc(), 'seconds': round(time.time() - t0, 1),
           'module': f"gea/{spec['module']}.py", 'checks': [{'name': n, 'pass': bool(c.get('pass'))} for n, c in zip(names, r['checks'])]}
    with _LOCK:
        _CHECKS[key] = res
    return res


def _raised_here(ws) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    p = os.path.join(ws.dir('records'), 'alerts.jsonl')
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                e = a.get('event')
                if e:
                    o = out.setdefault(e, {'n': 0, 'last_utc': None})
                    o['n'] += 1; o['last_utc'] = a.get('utc') or o['last_utc']
    except OSError:
        pass
    return out


def _wells(ws) -> List[dict]:
    """Each well with a drilling record and the names of its channels (the names only: the record is not read)."""
    from . import drill_string as DS
    from . import rig_state as RS
    out = []
    for wid in RS._wells_with_record(ws):
        try:
            c = DS._cache(ws, wid, build=True)
            out.append({'well': wid, 'display': ws.well(wid).get('display') or wid, 'names': [x['name'] for x in c.meta['channels']]})
        except Exception:
            continue
    return out


def page(ws=None) -> dict:
    """Every card. With a workspace: its settings, its wells, the alerts raised in it. Reading it writes nothing and runs no check."""
    from . import alerts as AL
    from . import channels as CH
    cards = [card(s, ws) for s in SPECS]
    covered = {e for c in cards for e in c['events']}
    out = {'generated_utc': _utc(), 'status': 'OK', 'kind': KIND, 'labelled_data': LABELLED, 'cards': cards,
           'others': [{'event': e, 'words': OTHERS[e][0], 'tab': OTHERS[e][1]} for e in AL.EVENTS if e not in covered and e in OTHERS],
           'unlisted': [e for e in AL.EVENTS if e not in covered and e not in OTHERS], 'wells': [], 'lines': []}
    if ws is not None:
        raised = _raised_here(ws); wells = _wells(ws)
        out['wells'] = [{'well': w['well'], 'display': w['display']} for w in wells]
        verdicts = {w['well']: {r['id']: r for r in CH.read_by(w['names'])[0]} for w in wells}
        for c, s in zip(cards, SPECS):
            c['raised_here'] = {'n': sum(raised.get(e, {}).get('n', 0) for e in c['events']), 'last_utc': max([raised[e]['last_utc'] for e in c['events'] if e in raised and raised[e]['last_utc']] or [None], key=lambda v: v or ''),
                                'by_event': {e: raised.get(e, {}).get('n', 0) for e in c['events']}}
            c['wells'] = []
            for w in wells:
                v = [verdicts[w['well']][rid] for rid in s['readers']]
                miss = [m for x in v for m in x['missing']]
                c['wells'].append({'well': w['well'], 'display': w['display'], 'can_read': not miss, 'missing': miss})
        for o in out['others']:
            o['raised_here'] = raised.get(o['event'], {}).get('n', 0)
        n_can = sum(1 for c in cards if c['wells'] and all(w['can_read'] for w in c['wells']))
        out['lines'] = [f"{len(cards)} rules raise {len(covered)} of the {len(AL.EVENTS)} alerts the service knows; the other {len(out['others'])} are conditions, not rules read from a drilling record.",
                        (f"{len(wells)} well(s) of this workspace have a drilling record: {n_can} of the {len(cards)} rules find the channels they cannot do without on every one of them." if wells
                         else "No well of this workspace has a drilling record yet: the rules have nothing to read."),
                        f"{sum(c['raised_here']['n'] for c in cards)} alert(s) from these rules are in this workspace's record."]
    else:
        out['lines'] = [f"{len(cards)} rules raise {len(covered)} of the {len(AL.EVENTS)} alerts the service knows; the other {len(out['others'])} are conditions, not rules read from a drilling record."]
    out['lines'].append("None is a model fitted to data, and none has been set against field events that someone confirmed: a card says what the rule does, not how often it is right.")
    return out


def text(p: dict, checks: bool = False) -> str:
    out = ['Alert rules'] + ['  ' + l for l in p['lines']]
    for c in p['cards']:
        out += ['', f"{c['title']}  [{', '.join(c['events'])}]", f"  what it is: {c['kind']}", f"  what it is for: {c['what_for']}", f"  how it decides: {c['how'] or 'it compares declared values with the record: see its tab'}",
                f"  when the alert is raised: {c['raised']}"]
        need = [x['what'] for x in c['reads']['needs']] + ([' or '.join(x['what'] for x in c['reads']['either'])] if c['reads']['either'] else [])
        out.append(f"  cannot do without: {', '.join(need) if need else 'no channel it says itself'}; reads when there: {', '.join(x['what'] for x in c['reads']['reads']) or 'nothing more'}")
        if c['declared']:
            out.append(f"  must be declared: {c['declared']}")
        for s in c['settings']:
            out.append(f"  setting {s['setting']} = {s['value']:g}" + (f" (default {s['default']:g}, changed" + (f": {s['source']}" if s['source'] else '') + ')' if s['changed'] else f" ({'the default' if s['bounds'] else s['where']})") + f" - {s['words']}")
        if 'wells' in c:
            cannot = [f"{w['display']} (no {', no '.join(w['missing'])})" for w in c['wells'] if not w['can_read']]
            out.append(f"  on this workspace: reads {sum(1 for w in c['wells'] if w['can_read'])} of {len(c['wells'])} well(s)" + (f"; cannot read {'; '.join(cannot)}" if cannot else '') + f"; raised {c['raised_here']['n']} time(s)")
        k = c['checks']
        out.append(f"  its checks: {k['passed']} of {k['of']} pass ({k['label']}; run {k['ran_utc']})" if k else "  its checks: not run in this session")
        if checks and k:
            out += [f"    {'PASS' if x['pass'] else 'FAIL'}  {x['name']}" for x in k['checks']]
        out += [f"  not a measurement: {x}" for x in c['not_a_measurement']]
        out.append('  held in: ' + ', '.join(f"{x['file']} ({x['sha256']})" for x in c['components']))
    out += ['', 'The other alerts: ' + '; '.join(f"{o['event']} ({o['words']})" for o in p['others'])]
    return '\n'.join(out)


def write_report(ws, program_version: str, actor: str = 'system', checks: bool = True) -> dict:
    """The cards as files. With `checks`, every rule's checks are run first, so the report carries them."""
    if checks:
        for s in SPECS:
            run_checks(s['id'])
    p = page(ws)
    d = ws.dir('reports', 'alert_rules')
    with open(os.path.join(d, 'alert_rules.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **json.loads(json.dumps(p, default=str))}, f, indent=1)
    with open(os.path.join(d, 'alert_rules.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Alert rules\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p, checks=True) + '\n```\n')
    files = ['alert_rules.json', 'alert_rules.md']
    ws.audit(actor, 'alert_rules.report', {'files': files, 'cards': len(p['cards']), 'checks_run': bool(checks)})
    return {'folder': d, 'files': files, 'page': p}


def selftest() -> dict:
    from . import alerts as AL
    from . import channels as CH
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    p = page(None)
    C = {c['id']: c for c in p['cards']}
    ck("every alert the service can raise is on the page: under a rule's card, or among the conditions that are not rules", p['unlisted'] == [] and {e for c in p['cards'] for e in c['events']} | {o['event'] for o in p['others']} == set(AL.EVENTS))
    ck("no alert is under two cards, and every card's alerts are alerts the service knows", sum(len(c['events']) for c in p['cards']) == len({e for c in p['cards'] for e in c['events']})
       and all(e in AL.EVENTS for c in p['cards'] for e in c['events']))
    ck("how each rule decides is the passage of its own module's description, found there now", all((c['how'].startswith(s['how'][0].lstrip('- ')) and len(c['how']) > 150) if s['how'] else c['how'] == '' for c, s in zip(p['cards'], SPECS)))
    ck("the washout card states the rule's own numbers, and the settings beside it are those numbers", '6 % and 100 psi' in C['washout']['how'] and [(s['setting'], s['value']) for s in C['washout']['settings']] == [('washout_fall', 0.06), ('washout_min_psi', 100.0)])
    ck("every setting has its meaning, its default and its bounds, and stands inside them", all(s['words'] and (s['bounds'] is None or s['bounds'][0] <= s['value'] <= s['bounds'][1]) for c in p['cards'] for s in c['settings']))
    R = {r['id']: r for r in CH.readers()}
    ck("what each rule reads is the Channels tab's own list for it", all(len(c['reads']['needs']) + len(c['reads']['either']) + len(c['reads']['reads']) == sum(len(R[r]['needs']) + len(R[r]['either']) + len(R[r]['reads']) for r in s['readers'])
                                                                         for c, s in zip(p['cards'], SPECS)) and [x['what'] for x in C['flowback']['reads']['needs']] == ['the pump rate', 'the active pit volume'])
    ck("when each alert is raised is its module's own statement, and names a record still being written", all('still being written' in c['raised'] and CADENCE in c['raised'] for c in p['cards']))
    ck("every card says what the rule will not call a measurement, and that its silence is not a clean bill", all(len(c['not_a_measurement']) >= 2 and c['not_a_measurement'][-1] == SILENCE for c in p['cards'])
       and C['washout']['not_a_measurement'][0].startswith('a washout') and C['stall']['not_a_measurement'][0].startswith('a stall') and C['tight']['not_a_measurement'][0].startswith('stuck pipe'))
    ck("every card names the file that holds its rule with that file's hash, says it is not a fitted model, and that no labelled data stands behind it",
       all(c['components'] and all(len(x['sha256']) == 16 and os.path.isfile(os.path.join(_HERE, os.path.basename(x['file']))) for x in c['components']) and c['kind'] == KIND and c['labelled_data'] == LABELLED for c in p['cards']))
    ck("a rule that needs something declared says what and where", all(C[k]['declared'] for k in ('sticking', 'trips', 'hardline', 'stability', 'procedure')) and all(C[k]['declared'] is None for k in ('washout', 'stall', 'stickslip')))
    k1 = run_checks('stickslip'); k2 = run_checks('bounce')
    ck("a rule's checks are its module's self-test, run on request and kept: two rules of one module share them", k1['ok'] and k1['of'] >= 6 and k1 is k2 and checks_kept('stickslip') is k1
       and all(x['name'] for x in k1['checks']))
    bad = 0
    for fn in (lambda: run_checks('nothing'), lambda: checks_kept('nothing')):
        try:
            fn()
        except KeyError:
            bad += 1
    ck("a rule that is not one is said to be none", bad == 2)
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'the cards against the modules they describe'}
