"""Program alerts: the conditions this program watches for, raised once when they begin and once when they clear.

The notification rules (`notify`) deliver an event to email or a webhook. This module decides when a condition is
worth an event, for the conditions the operator does not watch by hand:

- `seismic.protocol` - an earthquake near a well during (or in the association window after) one of its
  operations reaches a level of the chosen regulator's protocol; raised again only if a later revision of its
  magnitude reaches a higher level (from the live earthquake watch, `seismic_watch`);
- `seismic.event` - any new earthquake within the association radius during or after an operation;
- `seismic.watch_down` / `seismic.watch_up` - the earthquake watch has not reached the catalogue for three of its
  intervals while an operation runs, and when it does again;
- `motor.due` - a drill-string component's declared service interval is used up (the Drill String tab's DUE);
- `penetrator.outside` - a line of an ESP well's penetrator check is OUTSIDE its rating (the Completion tab);
- `dossier.changed` - the newest signed dossier of a well or the pad no longer matches the workspace: a source
  record or an earlier audit line has changed since it was signed (checked once a day);
- `invoice.differs` - a well's invoice cross-check finds a day that DIFFERS (checked once a day, and when a file is
  attached);
- `frac.hit` - a fracturing stage reads as a FRAC HIT on a monitored well's pressure (the Frac hits tab; checked every
  15 minutes, once per stage and well);
- `condition.cleared` - one of motor.due, penetrator.outside, dossier.changed or invoice.differs is no longer so.

Every alert is written to `records/alerts.jsonl` whether or not any notification channel is set up - the Alarms
tab lists them - and is handed to the notification rules, which send it where the site said (a rule per event
name in `notifications.json`). The state that keeps an alert from repeating is `records/alerts_state.json`.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

EVENTS = ('seismic.protocol', 'seismic.event', 'seismic.watch_down', 'seismic.watch_up', 'motor.due', 'penetrator.outside',
          'dossier.changed', 'invoice.differs', 'frac.hit', 'condition.cleared')
CONDITIONS_EVERY_S = 900          # motors and penetrators: every 15 minutes
DOSSIERS_EVERY_S = 86400          # dossiers: once a day
_LOCK = threading.Lock()


def _utc(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws, name: str) -> str:
    return os.path.join(ws.path, 'records', name)


def state(ws) -> dict:
    p = _path(ws, 'alerts_state.json')
    if not os.path.isfile(p):
        return {'conditions': {}, 'last': {}}
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'conditions': {}, 'last': {}}


def save_state(ws, s: dict) -> None:
    p = _path(ws, 'alerts_state.json')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)
    os.replace(p + '.tmp', p)


def raise_alert(ws, notifier, ev: dict, now: Optional[datetime] = None) -> dict:
    """Record an alert and hand it to the notification rules. Returns the record with the deliveries."""
    rec = {'utc': _utc(now), **ev}
    deliveries: List[dict] = []
    if notifier is not None:
        try:
            deliveries = notifier.emit(dict(ev))
        except Exception as e:                       # a channel never stops the record
            deliveries = [{'ok': False, 'error': f'{type(e).__name__}: {e}'}]
    rec['delivered'] = [{'channel': d.get('channel'), 'ok': bool(d.get('ok'))} for d in deliveries]
    p = _path(ws, 'alerts.jsonl')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with _LOCK:
        with open(p, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str, sort_keys=True) + '\n')
    return rec


def recent(ws, n: int = 100) -> List[dict]:
    p = _path(ws, 'alerts.jsonl')
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


def _transition(ws, notifier, st: dict, key: str, bad: bool, ev: dict, now: datetime, raised: List[dict]) -> None:
    was = st['conditions'].get(key)
    if bad and not was:
        st['conditions'][key] = {'since': _utc(now), 'event': ev['event']}
        raised.append(raise_alert(ws, notifier, {**ev, 'key': key}, now))
    elif not bad and was:
        st['conditions'].pop(key, None)
        raised.append(raise_alert(ws, notifier, {'event': 'condition.cleared', 'key': key + ':cleared', 'cleared': was['event'],
                                                 'since': was['since'], 'well_id': ev.get('well_id'), 'well': ev.get('well'),
                                                 'detail': f"no longer so: {ev.get('what', key)}"}, now))


def check_conditions(ws, notifier, now: Optional[datetime] = None, force: bool = False) -> List[dict]:
    """Motors, penetrators (every 15 minutes) and dossiers (daily): raise what began, clear what ended."""
    from . import drill_string as DS
    from . import penetrator as PN
    now = now or datetime.now(timezone.utc)
    st = state(ws)
    raised: List[dict] = []
    last = st.setdefault('last', {})

    def due(name, every):
        t = last.get(name)
        if force or not t:
            return True
        return (now - datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)).total_seconds() >= every
    wells = {w['id']: (w.get('display') or w['id']) for w in ws.wells()}
    if due('conditions', CONDITIONS_EVERY_S):
        last['conditions'] = _utc(now)
        seen = set()
        if DS.runs(ws):
            try:
                for e in DS.tracking(ws, build=True):
                    life = e.get('life')
                    if not life:
                        continue
                    key = f"motor.due:{e['key']}"
                    seen.add(key)
                    wid = e['runs'][-1]['well_id'] if e['runs'] else None
                    what = f"{e['class_name']} {e['maker']} {e['model']} {e['serial']}".strip()
                    _transition(ws, notifier, st, key, life['status'] == 'DUE',
                                {'event': 'motor.due', 'well_id': wid, 'well': wells.get(wid, wid), 'what': what,
                                 'detail': f"{life['used']:g} of {life['limit']:g} {life['basis'].replace('_', ' ')} used ({life['used_pct']:g} %); interval: {life['basis']}, {(e['service'] or {}).get('source', '')}"},
                                now, raised)
            except Exception as ex:
                last['conditions_error'] = f'{type(ex).__name__}: {ex}'
        for wid in list(PN.records(ws)):
            if wid not in wells:
                continue
            try:
                c = PN.check(ws, wid)
            except Exception:
                continue
            bad = [r for r in c['rows'] if r['status'] == 'OUTSIDE']
            key = f'penetrator.outside:{wid}'
            seen.add(key)
            _transition(ws, notifier, st, key, bool(bad),
                        {'event': 'penetrator.outside', 'well_id': wid, 'well': wells[wid], 'what': f"the penetrator check of {wells[wid]}",
                         'detail': '; '.join(f"{r['item']}: {r['why']}" for r in bad)}, now, raised)
        try:
            from . import frac_hits as FH
            if FH.stages(ws):
                done = set(st.setdefault('frac_hits_seen', []))
                for h in FH.analyse(ws)['hits']:
                    key = f"frac.hit:{h['well_id']}:{h['stage']}:{h['monitor_id']}"
                    if key in done:
                        continue
                    done.add(key)
                    raised.append(raise_alert(ws, notifier, {'event': 'frac.hit', 'key': key, 'well_id': h['well_id'], 'well': h['well'],
                                                             'stage': h['stage'], 'monitor': h['monitor'], 'what': f"stage {h['stage']} of {h['well']}",
                                                             'detail': f"{h['peak_psi']:,.0f} psi on {h['monitor']}, not falling after the stage stopped "
                                                                       f"(pumping from {h['start'][:16].replace('T', ' ')})"}, now))
                st['frac_hits_seen'] = sorted(done)
        except Exception as ex:
            last['frac_error'] = f'{type(ex).__name__}: {ex}'
        for key in [k for k in st['conditions'] if k.split(':')[0] in ('motor.due', 'penetrator.outside') and k not in seen]:
            st['conditions'].pop(key, None)                  # the run or the record was removed: nothing left to clear
    if due('dossiers', DOSSIERS_EVERY_S):
        last['dossiers'] = _utc(now)
        try:
            from . import dossier as DZ
            newest: Dict[str, dict] = {}
            for e in DZ.ledger(ws):
                newest[e.get('well_id') or 'pad'] = e
            for scope, e in newest.items():
                p = os.path.join(ws.reports_dir, e['path'])
                key = f'dossier.changed:{scope}'
                if not os.path.isfile(p):
                    continue
                r = DZ.verify(p, ws)
                bad = [c for c in (r.get('workspace_checks') or []) + r['checks'] if not c['ok']]
                _transition(ws, notifier, st, key, bool(bad),
                            {'event': 'dossier.changed', 'well_id': e.get('well_id'), 'well': e.get('display'), 'what': f"the dossier {e['dossier_id']}",
                             'dossier_id': e['dossier_id'], 'detail': '; '.join(f"{c['check']}: {c['detail']}" for c in bad)}, now, raised)
        except Exception as ex:
            last['dossiers_error'] = f'{type(ex).__name__}: {ex}'
    if due('invoices', DOSSIERS_EVERY_S):
        last['invoices'] = _utc(now)
        try:
            from . import invoice_check as IC
            if IC.terms(ws):
                for wid in sorted({f['well_id'] for f in IC.files(ws)}):
                    if wid not in wells:
                        continue
                    r = IC.check(ws, wid)
                    T = r.get('totals') or {}
                    _transition(ws, notifier, st, f'invoice.differs:{wid}', r.get('status') == 'DIFFERS',
                                {'event': 'invoice.differs', 'well_id': wid, 'well': wells[wid], 'what': f'the invoice cross-check of {wells[wid]}',
                                 'detail': f"{T.get('differs', 0)} day(s) differ, {T.get('usd_in_question', 0):,.2f} USD in question of {T.get('billed_checked_usd', 0):,.2f} billed at day rates",
                                 'usd_in_question': T.get('usd_in_question')}, now, raised)
        except Exception as ex:
            last['invoices_error'] = f'{type(ex).__name__}: {ex}'
    save_state(ws, st)
    return raised
