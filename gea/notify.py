# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""notify - tell someone when something needs a person.

Configuration (versioned as `notifications` in the configuration store, read
from `config/notifications.json`):

    {
     "channels": [
      {"name": "ops-hook", "kind": "webhook", "url": "https://hooks.example/abc", "headers": {"X-Token": "..."}},
      {"name": "mail", "kind": "smtp", "host": "smtp.example", "port": 587, "starttls": true,
       "from": "gea@site", "to": ["ops@site"], "user": "gea", "password_env": "GEA_SMTP_PASSWORD"}
     ],
     "rules": [
      {"event": "alarm.activated", "priority": ["P1", "P2"], "channels": ["ops-hook", "mail"]},
      {"event": "job.failed", "channels": ["ops-hook"]},
      {"event": "patch.down", "channels": ["mail"]},
      {"event": "approval.pending", "channels": ["mail"]}
     ],
     "quiet_s": 300
    }

Events: alarm.activated (per alarm, with well and priority), alarm.shelved,
job.failed, patch.down (a patch entered DOWN or DEGRADED), patch.up (back to
CONNECTED), approval.pending (a new well test or re-fit waits for a decision),
file.imported. `quiet_s` suppresses repeats of the same event key (for
example the same patch going down again) inside that window.

Secrets never live in the configuration: an SMTP password is read from the
environment variable named by `password_env`; webhook headers are the only
place a token may appear, and the service never echoes them.

Every delivery attempt is appended to `records/notifications.jsonl` with the
outcome, so "did anyone get told?" has an answer. Delivery runs in the
service's poller thread and never blocks a request or a job.

    gea notify --workspace C:\\site --test mail       send a test message
    gea notify --workspace C:\\site --log 20          the last 20 deliveries
"""

from __future__ import annotations

import json
import os
import smtplib
import threading
import time
import urllib.request
from email.message import EmailMessage
from typing import Dict, List, Optional

EXAMPLE = {
    'channels': [
        {'name': 'ops-hook', 'kind': 'webhook', 'url': 'https://hooks.example.com/gea', 'headers': {}},
        {'name': 'mail', 'kind': 'smtp', 'host': 'smtp.example.com', 'port': 587, 'starttls': True,
         'from': 'gea@example.com', 'to': ['operations@example.com'], 'user': 'gea', 'password_env': 'GEA_SMTP_PASSWORD'},
    ],
    'rules': [
        {'event': 'alarm.activated', 'priority': ['P1'], 'channels': ['ops-hook', 'mail']},
        {'event': 'job.failed', 'channels': ['ops-hook']},
        {'event': 'patch.down', 'channels': ['ops-hook', 'mail']},
        {'event': 'approval.pending', 'channels': ['mail']},
    ],
    'quiet_s': 300,
}
EVENTS = ('alarm.activated', 'alarm.shelved', 'job.failed', 'patch.down', 'patch.up', 'approval.pending', 'file.imported', 'test')
_LOCK = threading.Lock()


class NotifyError(ValueError):
    pass


def validate(cfg) -> dict:
    """Shape-check a configuration; returns it normalized. Raises NotifyError with a plain reason."""
    if not isinstance(cfg, dict):
        raise NotifyError('notifications must be a JSON object with "channels" and "rules"')
    chans = cfg.get('channels', [])
    rules = cfg.get('rules', [])
    if not isinstance(chans, list) or not isinstance(rules, list):
        raise NotifyError('"channels" and "rules" must be lists')
    names = set()
    for c in chans:
        if not isinstance(c, dict) or not c.get('name') or c.get('kind') not in ('webhook', 'smtp'):
            raise NotifyError('each channel needs a "name" and a "kind" of webhook or smtp')
        if c['name'] in names:
            raise NotifyError(f"channel named twice: {c['name']}")
        names.add(c['name'])
        if c['kind'] == 'webhook':
            if not str(c.get('url', '')).startswith(('http://', 'https://')):
                raise NotifyError(f"webhook {c['name']}: url must start with http:// or https://")
        else:
            if not c.get('host') or not c.get('from') or not c.get('to'):
                raise NotifyError(f"smtp {c['name']}: host, from and to are required")
            if 'password' in c:
                raise NotifyError(f"smtp {c['name']}: do not put a password in the configuration; name an environment variable in password_env")
            if not isinstance(c['to'], list):
                raise NotifyError(f"smtp {c['name']}: to must be a list of addresses")
    for r in rules:
        if not isinstance(r, dict) or r.get('event') not in EVENTS:
            raise NotifyError(f'each rule needs an "event" from {list(EVENTS)}')
        for n in r.get('channels', []):
            if n not in names:
                raise NotifyError(f"rule {r['event']}: unknown channel {n}")
        if 'priority' in r and not isinstance(r['priority'], list):
            raise NotifyError(f"rule {r['event']}: priority must be a list such as [\"P1\"]")
    q = cfg.get('quiet_s', 300)
    if not isinstance(q, (int, float)) or q < 0:
        raise NotifyError('quiet_s must be a non-negative number of seconds')
    return {'channels': chans, 'rules': rules, 'quiet_s': q}


def load(workspace_path: str) -> Optional[dict]:
    p = os.path.join(workspace_path, 'config', 'notifications.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return validate(json.load(f))


# -- channels ----------------------------------------------------------------------------
def send_webhook(chan: dict, payload: dict, timeout_s: float = 10.0) -> dict:
    data = json.dumps(payload, default=str).encode('utf-8')
    req = urllib.request.Request(chan['url'], data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'gea-program notify')
    for k, v in (chan.get('headers') or {}).items():
        req.add_header(str(k), str(v))
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return {'ok': 200 <= r.status < 300, 'status': r.status}


def send_smtp(chan: dict, subject: str, body: str, timeout_s: float = 20.0) -> dict:
    msg = EmailMessage()
    msg['From'] = chan['from']
    msg['To'] = ', '.join(chan['to'])
    msg['Subject'] = subject
    msg.set_content(body)
    port = int(chan.get('port', 587 if chan.get('starttls', True) else 25))
    with smtplib.SMTP(chan['host'], port, timeout=timeout_s) as s:
        if chan.get('starttls', True):
            s.starttls()
        if chan.get('user'):
            pw = os.environ.get(chan.get('password_env') or '', '')
            s.login(chan['user'], pw)
        s.send_message(msg)
    return {'ok': True, 'recipients': len(chan['to'])}


def _subject(ev: dict) -> str:
    k = ev['event']
    w = ev.get('well') or ''
    if k == 'alarm.activated':
        return f"[GEA] {ev.get('priority', '')} alarm {ev.get('alarm_id', '')} on {w}".strip()
    if k == 'alarm.shelved':
        return f"[GEA] alarm {ev.get('alarm_id', '')} shelved on {w} by {ev.get('operator', '')}"
    if k == 'job.failed':
        return f"[GEA] job failed: {ev.get('label', ev.get('job_id', ''))}"
    if k == 'patch.down':
        return f"[GEA] patch {ev.get('patch', '')} is {ev.get('status', 'DOWN')}"
    if k == 'patch.up':
        return f"[GEA] patch {ev.get('patch', '')} is back"
    if k == 'approval.pending':
        return f"[GEA] {ev.get('what', 'an item')} waits for approval on {w}"
    if k == 'file.imported':
        return f"[GEA] file imported: {ev.get('name', '')}"
    return f"[GEA] {k}"


def _body(ev: dict) -> str:
    lines = [f"{k}: {v}" for k, v in ev.items() if k not in ('event',)]
    return f"Event: {ev['event']}\n" + '\n'.join(lines) + '\n\nThis message was sent by the GEA-Program notification rules.'


# -- the notifier -------------------------------------------------------------------------
class Notifier:
    """Matches events to rules and delivers them; logs every attempt."""

    def __init__(self, workspace_path: str, cfg: Optional[dict] = None):
        self.ws_path = workspace_path
        self.cfg = cfg
        self.log_path = os.path.join(workspace_path, 'records', 'notifications.jsonl')
        self._recent: Dict[str, float] = {}

    def reload(self) -> None:
        try:
            self.cfg = load(self.ws_path)
        except (NotifyError, json.JSONDecodeError, OSError) as e:
            self._log({'event': 'config', 'error': str(e)}, None, {'ok': False, 'error': f'notifications.json: {e}'})
            self.cfg = None

    def _log(self, ev: dict, channel: Optional[str], result: dict) -> None:
        entry = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'event': ev.get('event'), 'key': ev.get('key'),
                 'channel': channel, 'ok': bool(result.get('ok')), 'result': {k: v for k, v in result.items() if k != 'ok'},
                 'subject': _subject(ev) if ev.get('event') in EVENTS else ''}
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with _LOCK:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, default=str) + '\n')

    def tail(self, n: int = 50) -> List[dict]:
        if not os.path.isfile(self.log_path):
            return []
        with open(self.log_path, encoding='utf-8') as f:
            rows = []
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-n:]

    def channels_for(self, ev: dict) -> List[dict]:
        if not self.cfg:
            return []
        names: List[str] = []
        for r in self.cfg['rules']:
            if r['event'] != ev['event']:
                continue
            if r.get('priority') and ev.get('priority') not in r['priority']:
                continue
            if r.get('wells') and ev.get('well_id') not in r['wells']:
                continue
            names += [n for n in r.get('channels', []) if n not in names]
        return [c for c in self.cfg['channels'] if c['name'] in names]

    def deliver(self, ev: dict, channel: dict) -> dict:
        try:
            if channel['kind'] == 'webhook':
                res = send_webhook(channel, {'subject': _subject(ev), **ev})
            else:
                res = send_smtp(channel, _subject(ev), _body(ev))
        except Exception as e:                                   # a dead channel is logged, never raised into the service
            res = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        self._log(ev, channel['name'], res)
        return res

    def emit(self, ev: dict) -> List[dict]:
        """Deliver one event through every matching channel, honouring the quiet window per event key."""
        key = ev.get('key') or f"{ev['event']}:{ev.get('well_id', '')}:{ev.get('alarm_id', ev.get('patch', ev.get('job_id', '')))}"
        ev = {**ev, 'key': key}
        chans = self.channels_for(ev)
        if not chans:
            return []
        quiet = float((self.cfg or {}).get('quiet_s', 300))
        now = time.time()
        if quiet and now - self._recent.get(key, 0) < quiet and ev['event'] != 'test':
            self._log(ev, None, {'ok': True, 'suppressed': f'repeat inside quiet window ({quiet:g} s)'})
            return []
        self._recent[key] = now
        return [{'channel': c['name'], **self.deliver(ev, c)} for c in chans]

    def test(self, channel_name: str, actor: str = '') -> dict:
        if not self.cfg:
            raise NotifyError('no notifications configuration (commit one as "notifications")')
        c = next((x for x in self.cfg['channels'] if x['name'] == channel_name), None)
        if c is None:
            raise NotifyError(f'no channel named {channel_name}')
        ev = {'event': 'test', 'key': f'test:{channel_name}:{time.time()}', 'actor': actor, 'note': 'test message from the GEA-Program dashboard'}
        return {'channel': channel_name, **self.deliver(ev, c)}


class Watcher:
    """Finds new events in a workspace between polls: alarm activations (per-well event logs), failed jobs,
    patch state changes, newly pending approvals. Keeps only file offsets and last-seen sets, so a restart
    does not re-send history: the first poll establishes the baseline."""

    def __init__(self, app, notifier: Notifier):
        self.app = app
        self.n = notifier
        self._alarm_offsets: Dict[str, int] = {}
        self._jobs_seen: set = set()
        self._patch_status: Dict[str, str] = {}
        self._approvals_seen: set = set()
        self._primed = False

    def poll(self) -> int:
        ws = self.app.ws
        emitted = 0
        # alarms: tail each well's event log from the remembered offset
        for w in ws.wells():
            p = os.path.join(self.app._well_reports_dir(w['id']), 'alarm_events.jsonl')
            if not os.path.isfile(p):
                continue
            size = os.path.getsize(p)
            off = self._alarm_offsets.get(p)
            if off is None:                    # baseline: do not announce history
                self._alarm_offsets[p] = size
                continue
            if size < off:                     # rewritten/truncated: start over from its end
                self._alarm_offsets[p] = size
                continue
            if size == off:
                continue
            with open(p, encoding='utf-8') as f:
                f.seek(off)
                chunk = f.read()
            if not chunk.endswith('\n'):       # a line still being written: wait for it
                chunk = chunk[:chunk.rfind('\n') + 1]
            self._alarm_offsets[p] = off + len(chunk.encode('utf-8'))
            for line in chunk.splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get('event') == 'ACTIVATED' and self._primed:
                    emitted += len(self.n.emit({'event': 'alarm.activated', 'well_id': w['id'], 'well': w['display'], 'alarm_id': e.get('alarm_id'),
                                                'priority': e.get('priority'), 'tag_id': e.get('tag_id'), 'value': e.get('value'),
                                                'setpoint': e.get('setpoint'), 'timestamp_utc': e.get('timestamp_utc')}))
                elif e.get('event') == 'SHELVED' and self._primed:
                    emitted += len(self.n.emit({'event': 'alarm.shelved', 'well_id': w['id'], 'well': w['display'], 'alarm_id': e.get('alarm_id'),
                                                'operator': e.get('operator'), 'until': e.get('until'), 'note': e.get('note'),
                                                'timestamp_utc': e.get('timestamp_utc')}))
        # jobs
        for j in self.app.runner.list(100):
            if j.get('status') == 'FAILED' and j['id'] not in self._jobs_seen:
                self._jobs_seen.add(j['id'])
                if self._primed:
                    emitted += len(self.n.emit({'event': 'job.failed', 'job_id': j['id'], 'label': j.get('label'), 'actor': j.get('actor'),
                                                'returncode': j.get('returncode'), 'finished_utc': j.get('finished_utc')}))
        # patches
        for p in self.app.patches.states():
            prev = self._patch_status.get(p['name'])
            cur = p.get('status')
            self._patch_status[p['name']] = cur
            if prev is None or not self._primed or prev == cur:
                continue
            if cur in ('DOWN', 'DEGRADED') and prev not in ('DOWN', 'DEGRADED'):
                emitted += len(self.n.emit({'event': 'patch.down', 'patch': p['name'], 'status': cur, 'well_id': p.get('well_id'),
                                            'protocol': p.get('protocol'), 'last_sample_utc': p.get('last_sample_utc')}))
            elif cur == 'CONNECTED' and prev in ('DOWN', 'DEGRADED'):
                emitted += len(self.n.emit({'event': 'patch.up', 'patch': p['name'], 'status': cur, 'well_id': p.get('well_id')}))
        # approvals
        try:
            q = self.app.approvals_queue()
            keys = [('well test', x['well_id'], x['test_id']) for x in q.get('well_tests', [])] + \
                   [('re-fit', x['well_id'], x.get('entry_id')) for x in q.get('refits', [])]
            for k in keys:
                if k not in self._approvals_seen:
                    self._approvals_seen.add(k)
                    if self._primed:
                        w = next((x for x in ws.wells() if x['id'] == k[1]), {})
                        emitted += len(self.n.emit({'event': 'approval.pending', 'what': k[0], 'well_id': k[1], 'well': w.get('display', k[1]), 'item': k[2]}))
        except Exception:
            pass
        self._primed = True
        return emitted
