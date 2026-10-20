"""The daily pad summary: one page each morning - what changed since the last one, what is due, what is still missing.

A site that runs the program for weeks stops opening every tab every day. This page gathers what a person
would look for first, from the records the program already keeps:

- what changed: the actions in the audit log since the last summary (grouped, with who did them), the alerts
  raised, the report versions written, the earthquakes the live watch saw for the first time, and yesterday's
  rig time for each well drilling - its drilling hours, connections and the time lost against the targets;
- what is due: the alert conditions still open (a service interval used up, a penetrator line outside its
  rating, a signed dossier that no longer matches, an invoice that differs), each well's invoice check, drill-string
  components near or past their interval, the
  operations under way or starting in the next seven days, the earthquake watch's state, the last backup and the
  last signed dossier;
- what is still missing: the setup checklist's items still TO DO, required first.

It is a client report like the others (`client_reports.write`: markdown, HTML, the printable PDF, JSON) under
`reports/daily/pad_summary.*`; report history keeps every day's version. The service writes it once a day at the
hour set here (UTC) and hands it to the notification rules as the event `pad.summary`, so a rule for that event
emails it; `gea pad-summary` writes it on demand.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DEFAULTS = {'enabled': True, 'at': '11:00', 'send': True}          # 11:00 UTC is 06:00 in US Central daylight time
MAX_ROWS = {'alerts': 6, 'changed': 8, 'missing': 10}     # what keeps it to one page; the rest is counted, and in the JSON


def _utc(now: datetime) -> str:
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19].replace('Z', ''), '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _p(ws, name: str) -> str:
    return os.path.join(ws.path, 'records', name)


def _load(p: str, default):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _store(p: str, obj) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(p + '.tmp', p)


def settings(ws) -> dict:
    return {**DEFAULTS, **_load(_p(ws, 'pad_summary.json'), {}).get('settings', {})}


def state(ws) -> dict:
    return _load(_p(ws, 'pad_summary.json'), {}).get('state', {})


def set_settings(ws, actor: str, enabled=None, at=None, send=None) -> dict:
    s = settings(ws)
    if enabled is not None:
        s['enabled'] = bool(enabled) if not isinstance(enabled, str) else enabled.lower() in ('1', 'true', 'on', 'yes')
    if at not in (None, ''):
        try:
            hh, mm = (int(x) for x in str(at).strip().split(':'))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except (ValueError, AssertionError):
            raise ValueError('the time is HH:MM, 24-hour, UTC')
        s['at'] = f'{hh:02d}:{mm:02d}'
    if send is not None:
        s['send'] = bool(send) if not isinstance(send, str) else send.lower() in ('1', 'true', 'on', 'yes')
    d = _load(_p(ws, 'pad_summary.json'), {})
    d['settings'] = s
    _store(_p(ws, 'pad_summary.json'), d)
    ws.audit(actor, 'pad_summary.settings', s)
    return s


def _clean(s: str) -> str:
    """Text from the records (a note someone typed) that a client report may not carry is left out here."""
    from . import client_reports as CR
    s = str(s)
    return s if not CR.forbidden_terms(s) else '(see the audit log)'


def _short(s: str, n: int = 150) -> str:
    """A cell kept to a few lines: the Setup tab and the alert itself carry the whole text."""
    s = str(s)
    return s if len(s) <= n else s[:n].rsplit(' ', 1)[0].rstrip(';,') + ' ...'


def _audit_since(ws, since: datetime) -> List[dict]:
    rows = []
    p = ws.audit_path
    if not os.path.isfile(p):
        return rows
    with open(p, encoding='utf-8', errors='replace') as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = _parse(e.get('utc'))
            if t and t > since:
                rows.append(e)
    return rows


def gather(ws, now: Optional[datetime] = None, since: Optional[datetime] = None) -> dict:
    """The three lists, as data."""
    from . import alerts as AL
    from . import op_seismicity as OS
    from . import report_history as RH
    from . import seismic_watch as SW
    from . import setup_checklist as SC
    now = now or datetime.now(timezone.utc)
    if since is None:
        since = _parse(state(ws).get('last_utc')) or now - timedelta(hours=24)
    wells = {w['id']: (w.get('display') or w['id']) for w in ws.wells()}
    # -- what changed --------------------------------------------------------------------------------------------
    aud = [e for e in _audit_since(ws, since) if not str(e.get('action', '')).startswith(('session.', 'login'))]
    groups = {}
    for e in aud:
        g = groups.setdefault(e['action'], {'action': e['action'], 'n': 0, 'last': e['utc'], 'by': set()})
        g['n'] += 1; g['last'] = max(g['last'], e['utc']); g['by'].add(e.get('actor') or '')
    changed = sorted(({**g, 'by': ', '.join(sorted(x for x in g['by'] if x))} for g in groups.values()), key=lambda g: g['last'], reverse=True)
    alerts = [a for a in reversed(AL.recent(ws, 500)) if (_parse(a.get('utc')) or now) > since]
    versions = [e for e in RH.entries(ws.reports_dir) if (_parse(e.get('utc')) or since) > since and not e.get('family', '').startswith('daily/')]
    fams = {}
    for e in versions:
        fams[e['family']] = fams.get(e['family'], 0) + 1
    quakes = [e for e in SW.live_events(ws).values() if (_parse(e.get('first_seen_utc')) or since) > since]
    # -- what is due -------------------------------------------------------------------------------------------------
    due: List[dict] = []
    for k, c in sorted(AL.state(ws).get('conditions', {}).items()):
        due.append({'item': c.get('event', k.split(':')[0]), 'status': 'OPEN', 'detail': f"{k.split(':', 1)[-1]} since {c.get('since')}"})
    try:
        from . import drill_string as DS
        if DS.runs(ws):
            for e in DS.tracking(ws, build=False):
                life = e.get('life') or {}
                if life.get('status') in ('DUE', 'NEAR') and not any(d['detail'].startswith(e['key']) for d in due):
                    due.append({'item': f"service: {e['class_name']} {e['maker']} {e['model']} {e['serial']}".strip(), 'status': life['status'],
                                'detail': f"{life['used']:g} of {life['limit']:g} {life['basis'].replace('_', ' ')} used ({life['used_pct']:g} %)"})
    except Exception:
        pass
    naive = now.replace(tzinfo=None)
    for o in OS.operations(ws):
        a, b = OS._dt(o['start']), OS._dt(o.get('end'))
        if a and a <= naive and (b is None or b >= naive):
            due.append({'item': f"{o['kind']} under way at {wells.get(o['well_id'], o['well_id'])}", 'status': 'UNDER WAY',
                        'detail': f"{o['start']} to {o.get('end') or 'open'} ({_clean(o['source'])})"})
        elif a and naive < a <= naive + timedelta(days=7):
            due.append({'item': f"{o['kind']} at {wells.get(o['well_id'], o['well_id'])}", 'status': 'STARTS SOON', 'detail': f"from {o['start']}"})
    try:
        w = SW.status(ws, now)
        due.append({'item': 'the earthquake watch', 'status': w['state'],
                    'detail': (f"last good poll {w['last_ok_utc']}" if w.get('last_ok_utc') else 'no poll yet') + f"; {w['events']} earthquake(s) held"
                              + (f"; last error: {_clean(w['last_error'])}" if w.get('last_error') else '')})
    except Exception:
        pass
    try:
        from . import backup as BK
        b = BK.status(ws.path)
        due.append({'item': 'the backup', 'status': 'NEVER' if b['never'] else ('STALE' if b['stale'] or b['present'] is False else 'CURRENT'),
                    'detail': 'no backup has been taken' if b['never'] else f"last {b['last']['utc']} ({b['age_h']:.0f} h ago)"})
    except Exception:
        pass
    try:
        from . import dossier as DZ
        led = DZ.ledger(ws)
        due.append({'item': 'the last signed dossier', 'status': 'SIGNED' if led else 'NONE',
                    'detail': f"{led[-1]['dossier_id']}, {led[-1]['utc']}" if led else 'none signed yet (Reports tab)'})
    except Exception:
        pass
    # the invoice cross-check, well by well
    try:
        from . import invoice_check as IC
        if IC.terms(ws):
            for wid in sorted({f['well_id'] for f in IC.files(ws)}):
                if wid not in wells:
                    continue
                r = IC.check(ws, wid)
                T = r.get('totals') or {}
                due.append({'item': f'the invoice check of {wells[wid]}', 'status': r.get('status', ''),
                            'detail': f"{T.get('days', 0)} day(s): {T.get('agrees', 0)} agree, {T.get('question', 0)} in question, {T.get('differs', 0)} differ; "
                                      f"{T.get('usd_in_question', 0):,.2f} USD in question"})
    except Exception:
        pass
    # yesterday's rig time: the last whole report day of each well's record
    rig = []
    try:
        from . import rig_state as RS
        P_ = RS.pad(ws)
        ds = P_['day_start_h']
        day = (now.replace(tzinfo=None) - timedelta(hours=ds) - timedelta(days=1)).date().isoformat()
        for w in P_['wells']:
            d = next((x for x in w['days'] if x['date'] == day), None)
            if d:
                drl = d['hours']['ROTARY DRILLING'] + d['hours']['SLIDE DRILLING']
                rig.append({'well': w['display'], 'date': day, 'drilling_h': round(drl, 1), 'connections': d['connections'],
                            'median_w2w_min': d['median_w2w_min'], 'lost_h': d['lost_h'],
                            'lost_usd': round(d['lost_h'] / 24.0 * P_['operating_usd_per_day'], 2) if P_['operating_usd_per_day'] else None})
    except Exception:
        pass
    # -- what is still missing ---------------------------------------------------------------------------------------
    try:
        items = SC.checklist(ws)['items']
    except Exception:
        items = []
    order = {'required': 0, 'recommended': 1, 'optional': 2}
    missing = sorted(({'level': i['level'], 'title': i['title'], 'well': wells.get(i.get('well'), i.get('well') or ''), 'detail': _clean(i.get('detail', ''))}
                      for i in items if i['status'] != 'DONE' and i['level'] != 'optional'), key=lambda i: order.get(i['level'], 3))
    return {'since': _utc(since), 'now': _utc(now), 'changed': changed, 'n_actions': len(aud), 'alerts': alerts, 'versions': fams, 'rig_time': rig,
            'n_versions': len(versions), 'quakes': sorted(quakes, key=lambda e: -float(e.get('magnitude') or 0)), 'due': due, 'missing': missing,
            'n_wells': len(wells)}


def build(ws, version: str, now: Optional[datetime] = None, since: Optional[datetime] = None):
    from . import client_reports as CR
    now = now or datetime.now(timezone.utc)
    g = gather(ws, now, since)
    name = ws.manifest.get('name') or 'the pad'

    def cap(rows, n):
        return rows[:n], (f'and {len(rows) - n} more' if len(rows) > n else '')
    s1 = CR.Section('1', 'What changed', [], [])
    lead = (f"Since {g['since']}: {g['n_actions']} recorded action(s), {len(g['alerts'])} alert(s), {g['n_versions']} report version(s) written, "
            f"{len(g['quakes'])} earthquake(s) first seen by the live watch.")
    s1.paragraphs.append(lead)
    if g['alerts']:
        r, more = cap(g['alerts'], MAX_ROWS['alerts'])
        s1.tables.append(CR.Table(['When (UTC)', 'Alert', 'Well', 'Detail'], [[a.get('utc', ''), a.get('event', ''), a.get('well') or '', _short(_clean(a.get('detail', '')))] for a in r],
                                  'Alerts raised' + (f' ({more})' if more else '')))
    if g['changed']:
        r, more = cap(g['changed'], MAX_ROWS['changed'])
        s1.tables.append(CR.Table(['Action', 'Times', 'Last (UTC)', 'By'], [[_clean(c['action']), c['n'], c['last'], _clean(c['by'])] for c in r],
                                  'The audit log, grouped by action' + (f' ({more})' if more else '')))
    if g['versions']:
        s1.paragraphs.append('Reports written: ' + '; '.join(f"{k} ({v})" for k, v in sorted(g['versions'].items())) + '.')
    if g['quakes']:
        q = g['quakes'][0]
        s1.paragraphs.append(f"Largest new earthquake: M {q.get('magnitude')} {q.get('mag_type') or ''} at {q.get('time')}, {_clean(q.get('place') or '')}; the Operations & seismicity tab sets each against the wells.")
    if g.get('rig_time'):
        s1.tables.append(CR.Table(['Well', 'Report day', 'Drilling (h)', 'Connections', 'Median W2W (min)', 'Time lost (h)', 'At the operating rate (USD)'],
                                  [[x['well'], x['date'], f"{x['drilling_h']:g}", x['connections'], '-' if x['median_w2w_min'] is None else f"{x['median_w2w_min']:g}",
                                    f"{x['lost_h']:g}", '-' if x['lost_usd'] is None else f"{x['lost_usd']:,.2f}"] for x in g['rig_time']],
                                  "Yesterday's rig time: the time lost against the targets (the Rig time tab)"))
    if not (g['alerts'] or g['changed'] or g['versions'] or g['quakes'] or g.get('rig_time')):
        s1.paragraphs.append('Nothing was recorded in this period.')
    s2 = CR.Section('2', 'What is due', [], [])
    if g['due']:
        s2.tables.append(CR.Table(['Item', 'Status', 'Detail'], [[d['item'], d['status'], _short(d['detail'])] for d in g['due']]))
    else:
        s2.paragraphs.append('Nothing is due.')
    s3 = CR.Section('3', 'What is still missing', [], [])
    if g['missing']:
        grouped: dict = {}                          # one row per item, its wells together: the same lack on three wells is one line
        for m in g['missing']:
            k = (m['level'], m['title'])
            row = grouped.setdefault(k, {'level': m['level'], 'title': m['title'], 'wells': [], 'details': []})
            if m['well'] and m['well'] not in row['wells']:
                row['wells'].append(m['well'])
            if m['detail'] and m['detail'] not in row['details']:
                row['details'].append(m['detail'])
        rows_m = [{'level': v['level'], 'title': v['title'], 'well': ', '.join(v['wells']), 'detail': '; '.join(v['details'])} for v in grouped.values()]
        r, more = cap(rows_m, MAX_ROWS['missing'])
        s3.paragraphs.append(f"{sum(1 for m in g['missing'] if m['level'] == 'required')} required and {sum(1 for m in g['missing'] if m['level'] == 'recommended')} recommended item(s) of the setup checklist are still to do (the Setup tab says how to do each).")
        s3.tables.append(CR.Table(['Level', 'Item', 'Wells', 'What is missing'], [[m['level'], m['title'], m['well'], _short(m['detail'])] for m in r], more))
    else:
        s3.paragraphs.append('The setup checklist has nothing required or recommended left to do.')
    rid = f"DAY-{now.strftime('%Y%m%d')}"
    front = [['Report ID', rid], ['Site', name], ['Period (UTC)', f"{g['since']} to {g['now']}"], ['Wells', str(g['n_wells'])],
             ['Generated (UTC)', g['now']], ['Program', f'Downhole Gauge Monitoring build {version}']]
    doc = CR.Document(f'Daily pad summary - {name}', rid, front, [s1, s2, s3],
                      footer='Written by the program from its own records: the audit log, the alerts, the report history, the live earthquake watch and the setup checklist.',
                      data={'report_id': rid, **{k: v for k, v in g.items() if k != 'quakes'}, 'quakes': g['quakes'][:20]})
    return doc


def write(ws, version: str, actor: str = 'system', notifier=None, now: Optional[datetime] = None, send: Optional[bool] = None) -> dict:
    from . import client_reports as CR
    now = now or datetime.now(timezone.utc)
    doc = build(ws, version, now)
    out = os.path.join(ws.reports_dir, 'daily')
    paths = CR.write(doc, out, 'pad_summary')
    d = _load(_p(ws, 'pad_summary.json'), {})
    d['state'] = {'last_utc': _utc(now), 'last_date': now.strftime('%Y-%m-%d'), 'report_id': doc.report_id}
    _store(_p(ws, 'pad_summary.json'), d)
    ws.audit(actor, 'report.pad_summary', {'report_id': doc.report_id, 'since': doc.data['since']})
    deliveries = []
    if notifier is not None and (settings(ws)['send'] if send is None else send):
        due = doc.data['due']
        ev = {'event': 'pad.summary', 'key': f"pad.summary:{now.strftime('%Y-%m-%d')}:{now.strftime('%H%M%S')}",
              'site': ws.manifest.get('name') or '', 'report_id': doc.report_id,
              'detail': f"{doc.data['n_actions']} action(s), {len(doc.data['alerts'])} alert(s), {sum(1 for x in due if x['status'] in ('OPEN', 'DUE'))} open or due, {len(doc.data['missing'])} still missing",
              'report': 'reports/daily/pad_summary.pdf', 'text': CR.render_markdown(doc)}
        try:
            deliveries = notifier.emit(ev)
        except Exception as e:
            deliveries = [{'ok': False, 'error': f'{type(e).__name__}: {e}'}]
    return {'report_id': doc.report_id, 'paths': paths, 'delivered': [{'channel': x.get('channel'), 'ok': bool(x.get('ok'))} for x in deliveries],
            'since': doc.data['since']}


def due_now(ws, now: datetime) -> bool:
    s = settings(ws)
    if not s['enabled']:
        return False
    hh, mm = (int(x) for x in s['at'].split(':'))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= at and state(ws).get('last_date') != now.strftime('%Y-%m-%d')


def tick(app, now: Optional[datetime] = None) -> Optional[dict]:
    """From the service's background poller: write and send today's summary once its hour has come."""
    from . import __version__
    now = now or datetime.now(timezone.utc)
    if not due_now(app.ws, now):
        return None
    return write(app.ws, __version__, 'scheduler', app.notifier, now)


def status(ws) -> dict:
    s, st = settings(ws), state(ws)
    from . import report_history as RH
    vers = [e for e in RH.entries(ws.reports_dir) if e.get('family') == 'daily/pad_summary']
    return {'settings': s, 'state': st, 'versions': len(vers),
            'latest': ('daily/pad_summary' if os.path.isfile(os.path.join(ws.reports_dir, 'daily', 'pad_summary.html')) else None)}
