# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""setup_checklist - what a workspace still needs before its reports can be handed over.

Read from the workspace as it stands, never kept as a list someone ticks: an item is DONE because the thing it
asks for is there (a declared position, a backup on the disk, a site record), and TO DO otherwise, with what is
missing named. The one exception is a CONFIRMATION - a thing only a person can check, such as a drilling
record's depth events against the daily drilling reports - which is DONE when someone has confirmed it, with
their name, the time and their note kept in `config/checklist.json` and the audit log.

Each item says why it matters, what it needs, and how to do it: a form or a button on the Setup page, and the
command line where every value is already known. A command is printed only when it can be pasted as it stands;
where the operator must supply a value (a position, an elevation, a well number) the item names the values and
where they come from instead of printing a command with stand-ins in it.

Levels: REQUIRED items are counted in "n of m"; RECOMMENDED and OPTIONAL items are shown and not counted.

Headless-safe: stdlib only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

REQUIRED, RECOMMENDED, OPTIONAL = 'required', 'recommended', 'optional'
DONE, TODO = 'DONE', 'TO DO'
CONFIRM_FILE = 'checklist.json'


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _read_json(p: str, default):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def confirmations(ws) -> Dict[str, dict]:
    return _read_json(os.path.join(ws.config_dir, CONFIRM_FILE), {}).get('confirmed', {})


def confirm(ws, item_id: str, actor: str, note: str = '') -> dict:
    """Record that a person checked a CONFIRMATION item. The item must be one the checklist offers for confirmation
    now, and the note - what was checked against - is required."""
    items = {i['id']: i for i in checklist(ws)['items']}
    it = items.get(item_id)
    if it is None or (it.get('action') or {}).get('kind') != 'confirm':
        raise ValueError(f'{item_id!r} is not an item this checklist asks a person to confirm')
    if not (note or '').strip():
        raise ValueError('a confirmation needs a note: what it was checked against (the daily drilling reports, by date)')
    p = os.path.join(ws.config_dir, CONFIRM_FILE)
    d = _read_json(p, {'schema': 1, 'confirmed': {}})
    rec = {'by': actor, 'utc': _utc(), 'note': note.strip(), 'basis': it.get('basis_key')}
    d.setdefault('confirmed', {})[item_id] = rec
    os.makedirs(ws.config_dir, exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(p + '.tmp', p)
    ws.audit(actor, 'checklist.confirm', {'item': item_id, 'note': rec['note']})
    return rec


def _q(s: str) -> str:
    """A PowerShell-safe argument: quoted when it carries a space."""
    return f'"{s}"' if (' ' in s or not s) else s


def checklist(ws) -> dict:
    from . import backup as BK
    from . import ppdm as P
    from . import tool_taxonomy as TX
    from .interval import REF_DATUMS
    from .geodesy import DATUMS
    path = ws.path
    items: List[dict] = []

    def add(**kw):
        kw.setdefault('scope', 'site'); kw.setdefault('well', None); kw.setdefault('cli', None); kw.setdefault('needs', [])
        items.append(kw)

    wells = ws.wells()
    sites = ws.sites()
    in_site = {w for s in sites for w in (s.get('wells') or [])}
    users = _read_json(os.path.join(path, 'users.json'), {}).get('users', [])
    admins = [u for u in users if u.get('role') == 'admin' and not u.get('disabled')]
    bcfg = {'out_dir': '', 'keep': 14, 'daily_at': '02:30', **_read_json(os.path.join(path, 'records', 'backup_config.json'), {})}
    bst = BK.status(path)
    sched = next((e for e in _read_json(os.path.join(path, 'jobs', 'schedule.json'), {}).get('entries', []) if e.get('name') == 'backup-daily'), None)
    ages = ws.report_ages()
    has_dash = os.path.isfile(os.path.join(ws.reports_dir, 'dashboard.json'))
    fleet = TX.fleet_list(path)
    conf = confirmations(ws)
    wsq = _q(path)

    # -- the workspace ----------------------------------------------------------------------------------------------
    add(id='accounts', level=REQUIRED, title='An administrator account',
        status=DONE if admins else TODO,
        detail=(f"{len(admins)} administrator(s): {', '.join(u['name'] for u in admins)}; {len(users)} account(s) in all" if admins
                else 'no administrator yet - the first visit to the page creates one'),
        why='Every change is signed by an account; backups, schedules and users need an administrator.',
        action={'kind': 'link', 'href': '#/admin', 'label': 'Administration'})
    add(id='wells', level=REQUIRED, title='The wells are in the workspace',
        status=DONE if wells else TODO,
        detail=(f"{len(wells)} well(s): " + ', '.join(w.get('display') or w['id'] for w in wells)) if wells else 'no wells yet',
        why='Each well is a record to read and report on: a gauge history, a drilling recorder export, a live port.',
        action={'kind': 'link', 'href': '#/files', 'label': 'Files'},
        cli=None if wells else f'gea files --workspace {wsq} --action list')
    loose = [w for w in wells if w['id'] not in in_site]
    add(id='site', level=REQUIRED, title='A site record ties the wells to the client',
        status=DONE if (sites and not loose) else TODO,
        detail=(f"{len(sites)} site(s): " + '; '.join(f"{s['display']} ({s.get('client') or 'no client named'}, {len(s.get('wells') or [])} well(s))" for s in sites)
                + (f"; not in any site: {', '.join(w.get('display') or w['id'] for w in loose)}" if loose else '')) if sites
               else 'no site yet: the wells are in the workspace, but nothing says whose they are',
        why='A site is the engagement: one thing to refresh, report and hand over to one client.',
        action={'kind': 'site', 'wells': [w['id'] for w in loose], 'well_names': {w['id']: (w.get('display') or w['id']) for w in loose}},
        needs=['the site name', 'the client'])
    add(id='backup_folder', level=REQUIRED, title='A backup folder off this machine',
        status=DONE if bcfg.get('out_dir') else TODO,
        detail=f"backups go to {bcfg['out_dir']}" if bcfg.get('out_dir') else 'no backup folder set',
        why='A lost disk is a lost month: the records are the product.',
        action={'kind': 'backup_folder', 'value': bcfg.get('out_dir') or ''},
        needs=['a folder on another disk or a share, outside the workspace'])
    if bst['never']:
        bdet, bok = 'no backup has ever been taken', False
    elif bst['present'] is False:
        bdet, bok = f"the last backup ({bst['last']['utc']}) is not where the log says: {bst['last']['path']}", False
    elif bst['stale']:
        bdet, bok = f"the last backup is {bst['age_h']:.0f} h old ({bst['last']['utc']})", False
    else:
        bdet, bok = f"last backup {bst['last']['utc']}, {bst['age_h']:.1f} h ago, at {bst['last']['path']}", True
    add(id='backup_taken', level=REQUIRED, title='A current backup', status=DONE if bok else TODO, detail=bdet,
        why=f"Daily means under {bst['stale_after_h']:g} hours old, and the file where the log says it is.",
        action={'kind': 'button', 'post': '/api/backup/run', 'label': 'Back up now', 'role': 'admin', 'job': True, 'disabled': not bcfg.get('out_dir'),
                'disabled_reason': 'set the backup folder first'},
        cli=(f"gea backup --workspace {wsq} --out {_q(bcfg['out_dir'])} --keep {bcfg.get('keep', 14)}" if bcfg.get('out_dir') else None))
    add(id='backup_schedule', level=REQUIRED, title='The backup scheduled daily',
        status=DONE if (sched and sched.get('enabled', True)) else TODO,
        detail=(f"daily at {sched.get('daily_at')}" + ('' if sched.get('enabled', True) else ' - but the entry is disabled')) if sched else 'no daily backup in the schedule',
        why='A backup that depends on someone remembering stops on the first busy week.',
        action={'kind': 'button', 'post': '/api/backup/schedule', 'label': f"Schedule daily at {bcfg.get('daily_at', '02:30')}", 'role': 'admin',
                'disabled': not bcfg.get('out_dir'), 'disabled_reason': 'set the backup folder first'})
    stale = [r for r in ages if r.get('stale')]
    add(id='reports_current', level=REQUIRED, title='The reports are current',
        status=DONE if (has_dash and not stale) else TODO,
        detail=('the dashboard has never been refreshed' if not has_dash else
                (f"{len(stale)} report(s) older than their source: " + ', '.join(f"{r['kind']} {r['id']}" for r in stale) if stale else 'every report is newer than its source')),
        why='A report older than its data says something the data no longer says.',
        action={'kind': 'button', 'post': '/api/refresh', 'label': 'Refresh the reports', 'role': 'operator', 'job': True},
        cli=f'gea workspace --path {wsq} --action refresh')
    add(id='fleet', level=RECOMMENDED, title='Your tools in the fleet',
        status=DONE if fleet else TODO,
        detail=(f"{len(fleet)} tool(s): " + ', '.join(f"{t['maker']} {t['model']}" for t in fleet[:6]) + (' …' if len(fleet) > 6 else '')) if fleet else 'the fleet is empty',
        why="The tools you run, each with the datasheet that rates it: the record of what was in the hole.",
        action={'kind': 'link', 'href': '#/tools', 'label': 'Tools & ports'},
        needs=['maker, model and serial per tool', 'the datasheet that rates it'])
    add(id='stress', level=OPTIONAL, title='The stress state declared',
        status=DONE if ws.stress() else TODO,
        detail='declared' if ws.stress() else 'not declared - needed only for the fault-slip statement',
        why='The slip statement on a well needs the site\'s three stress gradients and the SHmax azimuth with their sources.',
        action={'kind': 'link', 'href': '#/seismic', 'label': 'Seismic: stress card'})
    add(id='layers', level=OPTIONAL, title='Structure layers',
        status=DONE if ws.layers() else TODO,
        detail=f"{len(ws.layers())} layer(s)" if ws.layers() else 'none - needed only for the structure and interval statements',
        why='Mapped faults, tops and isopachs place a well in its formations.',
        action={'kind': 'link', 'href': '#/seismic', 'label': 'Seismic: layers card'})

    # -- each well ---------------------------------------------------------------------------------------------------
    for w in wells:
        wid, name = w['id'], (w.get('display') or w['id'])
        surf = w.get('surface') or {}
        pos_ok = surf.get('lat') is not None and surf.get('lon') is not None and surf.get('datum') not in (None, 'UNKNOWN')
        add(id=f'surface:{wid}', scope='well', well=wid, well_name=name, level=REQUIRED, title='Surface location and its datum',
            status=DONE if pos_ok else TODO,
            detail=(f"{surf['lat']:.6f}, {surf['lon']:.6f} ({surf.get('datum')})" if pos_ok else
                    ('a position is declared but its datum is not known' if surf.get('lat') is not None else 'no surface position declared')),
            why='The site map, the distance to every fault and seismic station, and the regulator\'s area all start from it.',
            action={'kind': 'surface', 'well': wid, 'datums': [d for d in DATUMS if d != 'UNKNOWN'], 'value': {k: surf.get(k) for k in ('lat', 'lon', 'datum')}},
            needs=['latitude and longitude in decimal degrees', 'the datum they are on (NAD27, NAD83, WGS84)', 'from the survey plat or the drilling permit'])
        el = surf.get('elevation') or {}
        add(id=f'elevation:{wid}', scope='well', well=wid, well_name=name, level=REQUIRED, title='Reference elevation (KB, GL or DF) and its source',
            status=DONE if el.get('ft') is not None else TODO,
            detail=(f"{el['ft']:,.1f} ft, {el.get('datum')}, from {el.get('source')}" if el.get('ft') is not None else 'not declared'),
            why='Every depth is measured from it; TVD below sea level and a formation top from a map need it.',
            action={'kind': 'elevation', 'well': wid, 'datums': list(REF_DATUMS), 'needs_surface': not pos_ok,
                    'pos': {'lat': surf['lat'], 'lon': surf['lon']} if pos_ok else None},
            needs=['the kelly bushing (or ground level, or derrick floor) elevation in feet', 'where it came from: the completion report or the survey plat'])
        api = P.declared_api(w)
        pw = P.parse_well_number(api) if api else None
        add(id=f'api:{wid}', scope='well', well=wid, well_name=name, level=REQUIRED, title='US Well Number (API)',
            status=DONE if (pw and pw['status'] == 'VALID') else TODO,
            detail=(f"{pw['api10']}" + (f" (wellbore {pw['api12']})" if pw.get('api12') else '') + f" - {pw['state']['name'] or 'state ' + pw['state']['code']}, county {pw['county']['code']}"
                    if pw and pw['status'] == 'VALID' else
                    (f"{api!r} is not a valid US Well Number: " + '; '.join(pw['problems']) if pw else 'not declared - the file name is the recorder\'s job number, not the well\'s')),
            why="The number the regulator, the operator and every other system know the well by.",
            action={'kind': 'api', 'well': wid, 'value': api or ''},
            needs=['the ten-digit number from the permit (twelve with the sidetrack code)'])
        rec = _read_json(os.path.join(ws.reports_dir, 'wells', wid, 'drilling_record.json'), None)
        evs = (rec or {}).get('depth_events') or []
        if evs:
            key = f'events:{wid}'
            basis = f"{len(evs)} event(s), TD {rec.get('total_depth_md_ft')}"
            c = conf.get(key)
            okc = bool(c) and c.get('basis') == basis
            add(id=key, scope='well', well=wid, well_name=name, level=RECOMMENDED, title='Depth events checked against the daily drilling reports',
                status=DONE if okc else TODO,
                detail=((f"confirmed by {c['by']} {c['utc']}: {c['note']}") if okc else
                        ((f"confirmed before the record changed ({c['basis']}); the record now has {basis} - check again: " if c else '')
                         + '; '.join(f"{e['type']} at {e['at'][:16]}" for e in evs))),
                why="Each event is a shape read from the recorder's hole depth; the daily drilling report decides what happened.",
                action={'kind': 'confirm', 'well': wid, 'events': [{'type': e['type'], 'at': e['at'], 'effect': e.get('effect', '')} for e in evs]},
                basis_key=basis)

    req = [i for i in items if i['level'] == REQUIRED]
    counts = {'required': len(req), 'required_done': sum(1 for i in req if i['status'] == DONE),
              'recommended': sum(1 for i in items if i['level'] == RECOMMENDED),
              'recommended_done': sum(1 for i in items if i['level'] == RECOMMENDED and i['status'] == DONE),
              'optional': sum(1 for i in items if i['level'] == OPTIONAL),
              'optional_done': sum(1 for i in items if i['level'] == OPTIONAL and i['status'] == DONE)}
    nxt = next((i for i in req if i['status'] != DONE), None)
    return {'workspace': ws.manifest.get('name'), 'generated_utc': _utc(), 'items': items, 'counts': counts,
            'ready': counts['required_done'] == counts['required'],
            'next': nxt['id'] if nxt else None,
            'statement': ('Read from the workspace as it stands: an item is done because what it asks for is there. '
                          'A confirmation is a person\'s check, kept with their name, the time and their note.')}


def text(r: dict) -> str:
    c = r['counts']
    out = [f"Setup checklist - {r['workspace']}: {c['required_done']} of {c['required']} required done"
           + f"; recommended {c['recommended_done']} of {c['recommended']}; optional {c['optional_done']} of {c['optional']}"]
    last = None
    for i in r['items']:
        grp = 'Workspace' if i['scope'] == 'site' else f"Well {i.get('well_name') or i['well']}"
        if grp != last:
            out.append(''); out.append(grp); last = grp
        mark = '[x]' if i['status'] == DONE else '[ ]'
        out.append(f"  {mark} {i['title']} ({i['level']}) - {i['detail']}")
        if i['status'] != DONE:
            if i.get('needs'):
                out.append(f"        needs: {'; '.join(i['needs'])}")
            if i.get('cli'):
                out.append(f"        run:   {i['cli']}")
            else:
                out.append("        do it: on the Setup page (the dashboard's Setup tab)")
    out.append('')
    out.append('Ready to hand over.' if r['ready'] else f"Next: {next(i['title'] for i in r['items'] if i['id'] == r['next'])}.")
    out.append(r['statement'])
    return '\n'.join(out)
