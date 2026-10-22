# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""service - the dashboard as the door: an HTTP service over one workspace.

    gea serve --workspace C:\\site [--host 127.0.0.1] [--port 8765]

The service is written on the standard library (http.server, threads), so the
package's promise - nothing outside itself, numpy and its declared extras -
holds. It serves the page (`gea/web/app.html`), the workspace's reports as
static files under /reports/, and a JSON API under /api/. Every action the
page can take is either a read of a workspace file or a job: `python -m gea
<args>` run by gea.jobs with its log kept, so one code path serves the page,
the terminal and the acceptance gate.

Accounts and roles live in `<workspace>/users.json` (PBKDF2-SHA256, 200,000
rounds, per-user salt). Roles nest: viewer < operator < approver < admin.
    viewer    read everything
    operator  add wells, run reports and the gate, start and stop live taps,
              acknowledge alarms, commit configuration
    approver  approve or reject well tests and re-fits
    admin     users, schedules, rollbacks, removing wells
Sessions are HttpOnly, SameSite=Strict cookies held in memory; every POST
must carry the header `X-GEA-Action: 1`, which a cross-site form cannot set.
Every action is written to the workspace audit log with the actor's name.

Transport: plain HTTP on the loopback interface by default. For the site
network, HTTPS: the service speaks TLS itself when the workspace names a
certificate - the site's own, from its certificate authority, or a self-signed
one made by `gea tls` (gea.tls) - with TLS 1.2 as the floor, the cookie marked
Secure, Strict-Transport-Security, and a plain-HTTP request on the same port
sent to HTTPS. A site that already runs a reverse proxy can terminate TLS there
instead (--behind-proxy; deploy/ has nginx and Caddy examples).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .workspace import Workspace, WorkspaceError, slug, utc_now_iso


def _finite(o):
    """NaN and Infinity are valid to Python's json and are a syntax error to every browser: a page that fetched
    one would fail to parse the whole body. Every number the service sends is finite or null."""
    if isinstance(o, float):
        return o if o == o and o not in (float('inf'), float('-inf')) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o
from .jobs import JobRunner, Scheduler
from .patches import PatchSupervisor, PROTOCOLS

ROLES = ('viewer', 'operator', 'approver', 'admin')
RANK = {r: i for i, r in enumerate(ROLES)}
SESSION_HOURS = 12
LOGIN_MAX_FAILURES = 5          # failed sign-ins per name or per client address ...
LOGIN_WINDOW_S = 15 * 60        # ... inside this window ...
LOGIN_LOCK_S = 15 * 60          # ... lock that name/address out for this long (429, Retry-After)
PBKDF2_ROUNDS = 200_000
# Commands the page may run as jobs. Anything else is refused (never `serve`, never a shell).
RUNNABLE = ('accept', 'fat-sat', 'sbom', 'permits', 'sla-report', 'model-cards', 'client-report', 'dashboard', 'workspace', 'drift-monitor',
            'well-test', 'alarms', 'notify', 'swaps', 'certificates', 'transient', 'housekeeping', 'store-forward', 'config', 'reconcile', 'ingest', 'opcua', 'mqtt', 'report', 'gamma', 'bench',
            'service-life', 'telemetry', 'run', 'wells', 'survey', 'wits0', 'witsml', 'wits0-sim', 'etp', 'etp-sim', 'files', 'doctor', 'update', 'backup', 'soak',
            'modbus-rtu', 'dnp3', 'hartip', 'piwebapi', 'dlis')
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')


def _iso_epoch(t: Optional[float]) -> Optional[str]:
    if not t:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


_NUM_RAW = re.compile(r"^(?:ValueError: |TypeError: )?(?:could not convert string to (?:float|int): |invalid literal for int\(\) with base 10: )'(.*)'$", re.S)
_NONE_RAW = re.compile(r"^(?:ValueError: |TypeError: )?(?:float|int)\(\) argument must be a string or a (?:real )?number, not 'NoneType'$")


def friendly_error(msg: str) -> str:
    """Python's own wording for a number that was not given, in the words of the form: a field left empty, or text
    where a number goes. Every other message is passed as it is."""
    m = _NUM_RAW.match(msg or '')
    if m:
        v = m.group(1).strip()
        return 'a number is needed: a field was left empty' if not v else f"'{v[:40]}' is not a number"
    if _NONE_RAW.match(msg or ''):
        return 'a number is needed: a field was left empty'
    return msg


def app_catalog_well(name: str) -> str:
    from .profile_catalog import CATALOG as _CAT
    e = _CAT.get(name)
    return str(e.provenance.get('well', '')) if e else ''


class ApiError(Exception):
    def __init__(self, status: int, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
DEFAULT_PREFS = {'units': 'field', 'time_zone': 'UTC', 'theme': 'auto', 'wizard_done': False, 'help_open': True}


class Users:
    """users.json: [{name, role, salt, hash, created_utc, disabled, prefs, last_login_utc, prev_login_utc}]"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        if not os.path.isfile(path):
            self._save({'users': []})

    def _load(self) -> dict:
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def _save(self, d: dict) -> None:
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()

    def list(self) -> List[dict]:
        return [{**{k: u[k] for k in ('name', 'role', 'created_utc', 'disabled')}, 'kind': u.get('kind', 'local')} for u in self._load()['users']]

    def _is_external(self, name: str) -> bool:
        return any(u['name'] == name and u.get('kind') == 'company' for u in self._load()['users'])

    def external(self, name: str, role: str, subject: str) -> dict:
        """The company account signing in: made on its first sign-in, its role from the company's claims at every one.
        A local account of the same name is never taken over, nor an external one bound to another subject."""
        if role not in ROLES:
            raise ApiError(400, f'role must be one of {ROLES}')
        with self._lock:
            d = self._load()
            u = next((x for x in d['users'] if x['name'] == name), None)
            if u is None:
                u = {'name': name, 'role': role, 'kind': 'company', 'subject': subject, 'created_utc': utc_now_iso(), 'disabled': False}
                d['users'].append(u)
            elif u.get('kind') != 'company':
                raise ApiError(409, f'a local account is named {name}: company sign-in will not take it over (rename or remove the local account)')
            elif u.get('subject') != subject:
                raise ApiError(409, f'{name} is bound to another company identity; an administrator removes the old account first')
            elif u.get('disabled'):
                raise ApiError(403, f'{name} is disabled in GEA')
            else:
                u['role'] = role
            self._save(d)
        return {'name': name, 'role': role}

    def count(self) -> int:
        return len(self._load()['users'])

    def add(self, name: str, password: str, role: str) -> dict:
        if role not in ROLES:
            raise ApiError(400, f'role must be one of {ROLES}')
        if not name or not name.strip():
            raise ApiError(400, 'a user needs a name')
        if len(password or '') < 8:
            raise ApiError(400, 'password must be at least 8 characters')
        with self._lock:
            d = self._load()
            if any(u['name'] == name for u in d['users']):
                raise ApiError(409, f'user exists: {name}')
            salt = secrets.token_hex(16)
            u = {'name': name, 'role': role, 'salt': salt, 'hash': self._hash(password, salt),
                 'created_utc': utc_now_iso(), 'disabled': False}
            d['users'].append(u)
            self._save(d)
        return {k: u[k] for k in ('name', 'role', 'created_utc', 'disabled')}

    def set_password(self, name: str, password: str) -> None:
        if len(password or '') < 8:
            raise ApiError(400, 'password must be at least 8 characters')
        if self._is_external(name):
            raise ApiError(400, f'{name} signs in with the company account and has no password here')
        with self._lock:
            d = self._load()
            for u in d['users']:
                if u['name'] == name:
                    u['salt'] = secrets.token_hex(16)
                    u['hash'] = self._hash(password, u['salt'])
                    self._save(d)
                    return
        raise ApiError(404, f'no such user: {name}')

    def set_role(self, name: str, role: str) -> None:
        if role not in ROLES:
            raise ApiError(400, f'role must be one of {ROLES}')
        if self._is_external(name):
            raise ApiError(400, f"{name}'s role comes from the company's claims at each sign-in: change the role map on the Company sign-in card")
        with self._lock:
            d = self._load()
            for u in d['users']:
                if u['name'] == name:
                    u['role'] = role
                    self._save(d)
                    return
        raise ApiError(404, f'no such user: {name}')

    def set_disabled(self, name: str, disabled: bool) -> None:
        with self._lock:
            d = self._load()
            for u in d['users']:
                if u['name'] == name:
                    u['disabled'] = bool(disabled)
                    self._save(d)
                    return
        raise ApiError(404, f'no such user: {name}')

    def check(self, name: str, password: str) -> Optional[dict]:
        for u in self._load()['users']:
            if u['name'] == name:
                if u.get('disabled') or u.get('kind') == 'company':
                    return None
                if hmac.compare_digest(self._hash(password or '', u['salt']), u['hash']):
                    return {'name': u['name'], 'role': u['role']}
                return None
        return None

    def stamp_login(self, name: str) -> Optional[str]:
        """Record this sign-in; return the previous one (what 'since your last visit' counts from)."""
        with self._lock:
            d = self._load()
            for u in d['users']:
                if u['name'] == name:
                    prev = u.get('last_login_utc')
                    u['prev_login_utc'], u['last_login_utc'] = prev, utc_now_iso()
                    self._save(d)
                    return prev
        return None

    def prefs(self, name: str) -> dict:
        for u in self._load()['users']:
            if u['name'] == name:
                return dict(DEFAULT_PREFS, **(u.get('prefs') or {}))
        return dict(DEFAULT_PREFS)

    def set_prefs(self, name: str, prefs: dict) -> dict:
        clean = {}
        for k, v in (prefs or {}).items():
            if k not in DEFAULT_PREFS:
                raise ApiError(400, f'unknown preference: {k} (known: {", ".join(DEFAULT_PREFS)})')
            if k == 'units' and v not in ('field', 'si'):
                raise ApiError(400, "units must be 'field' (psi, °F, ft) or 'si' (kPa, °C, m)")
            if k == 'time_zone' and not isinstance(v, str):
                raise ApiError(400, 'time_zone must be a string such as UTC or America/Chicago')
            if k == 'theme' and v not in ('auto', 'light', 'dark'):
                raise ApiError(400, "theme must be auto, light or dark")
            clean[k] = v
        with self._lock:
            d = self._load()
            for u in d['users']:
                if u['name'] == name:
                    u['prefs'] = {**(u.get('prefs') or {}), **clean}
                    self._save(d)
                    return dict(DEFAULT_PREFS, **u['prefs'])
        raise ApiError(404, f'no such user: {name}')


def _epoch(iso: str) -> float:
    """ISO-8601 UTC ('...Z' or offset) to seconds; 0 for anything unparsable."""
    if not iso:
        return 0.0
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# The application: routes over one workspace
# ---------------------------------------------------------------------------
class App:
    def __init__(self, workspace: Workspace, workers: int = 1, scheduler: bool = True):
        self.ws = workspace
        self.users = Users(os.path.join(workspace.path, 'users.json'))
        self.runner = JobRunner(workspace, workers=workers)
        self.scheduler = Scheduler(self.runner)
        if scheduler:
            self.scheduler.start()
        self.behind_proxy = False                 # set by Service when a reverse proxy terminates TLS in front
        self.tls: Optional[dict] = None           # set by Service when it speaks TLS itself: the certificate's facts
        self.oidc_ssl_context = None              # None: the system's trusted authorities (the gate sets its stand-in's)
        self.sessions: Dict[str, dict] = {}
        self._failures: Dict[str, List[float]] = {}
        self._locks: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.started_utc = utc_now_iso()
        self.taps: Dict[str, str] = {}          # well_id -> job id of a running bounded tap
        # the serving process sets this to a printer; the console then shows the page being opened, people
        # signing in, jobs starting and finishing, stops and restarts - a server that answers in silence looks,
        # from the window it was started in, exactly like one that is still loading
        self.console: Optional[Callable[[str], None]] = None
        self._seen_pages: set = set()
        self.patches = PatchSupervisor(workspace)
        from .seedlink import LiveStations
        self.live = LiveStations(workspace)      # the seismic leg's live stations (SeedLink), one tap thread each
        if scheduler:                            # a serving process keeps the enabled patches up; the gate does not
            self.patches.start_enabled()
            self.live.start_enabled()
        from .notify import Notifier, Watcher
        self.notifier = Notifier(workspace.path)
        self.notifier.reload()
        self.watcher = Watcher(self, self.notifier)
        self._poll_stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        if scheduler:
            self._poll_thread = threading.Thread(target=self._poll_loop, name='gea-notify', daemon=True)
            self._poll_thread.start()

    def say(self, msg: str) -> None:
        """One line on the serving console, if there is one. Never raises: the console is not the record."""
        c = self.console
        if c is None:
            return
        try:
            c(msg)
        except Exception:
            pass

    def page_opened(self, client: str) -> None:
        """The first time a browser fetches the page from an address, the console says so - that is the moment
        the operator stops waiting for a window that is not going to appear by itself."""
        with self._lock:
            first = client not in self._seen_pages
            self._seen_pages.add(client)
        if first:
            self.say(f'control panel opened in a browser from {client or "a browser"} - it asks for a sign-in there')

    def _poll_loop(self, every_s: float = 10.0) -> None:
        while not self._poll_stop.is_set():
            try:
                self.watcher.poll()
            except Exception as e:                            # the poller must outlive any one bad file
                try:
                    self.notifier._log({'event': 'poll', 'error': str(e)}, None, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
                except Exception:
                    pass
            try:                                              # the earthquake watch and the condition alerts, each on its own clock
                from . import seismic_watch as _SW
                _SW.tick(self)
            except Exception:
                pass
            try:                                              # the daily pad summary, once a day at its hour
                from . import pad_summary as _PS
                _PS.tick(self)
            except Exception:
                pass
            self._poll_stop.wait(every_s)

    def stop_background(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(5)

    # -- sessions ------------------------------------------------------------------------
    def _locked(self, key: str, now: float) -> Optional[int]:
        """Seconds left on a lock for this key, or None."""
        until = self._locks.get(key)
        if until and until > now:
            return int(until - now) + 1
        return None

    def _note_failure(self, keys: List[str], now: float) -> None:
        for k in keys:
            hist = [t for t in self._failures.get(k, []) if now - t < LOGIN_WINDOW_S] + [now]
            self._failures[k] = hist
            if len(hist) >= LOGIN_MAX_FAILURES:
                self._locks[k] = now + LOGIN_LOCK_S
                self._failures[k] = []

    def login(self, name: str, password: str, client: str = '') -> Tuple[str, dict]:
        now = time.time()
        keys = [f'name:{name}', f'addr:{client}'] if client else [f'name:{name}']
        with self._lock:
            waits = [w for w in (self._locked(k, now) for k in keys) if w]
        if waits:
            self.ws.audit(name or 'unknown', 'login.locked', {'client': client, 'retry_after_s': max(waits)})
            raise ApiError(429, f'too many failed sign-ins; try again in {max(waits)} s', retry_after=max(waits))
        u = self.users.check(name, password)
        if not u:
            with self._lock:
                self._note_failure(keys, now)
                locked = any(self._locked(k, now) for k in keys)
            self.ws.audit(name or 'unknown', 'login.failed', {'client': client, 'locked': locked})
            raise ApiError(401, 'wrong name or password' + (f'; locked for {LOGIN_LOCK_S // 60} min after {LOGIN_MAX_FAILURES} failures' if locked else ''))
        token = secrets.token_urlsafe(32)
        prev = self.users.stamp_login(name)
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)
            self.sessions[token] = {**u, 'expires': now + SESSION_HOURS * 3600, 'prev_login_utc': prev, 'created': now, 'last_seen': now,
                                    'client': client, 'sid': secrets.token_hex(4)}
        self.ws.audit(name, 'login', {'role': u['role'], 'client': client})
        return token, u

    def login_company(self, result: dict, client: str = '') -> Tuple[str, dict]:
        """A session for a person the company's sign-in vouched for (gea.oidc.finish)."""
        u = self.users.external(result['name'], result['role'], result['subject'])
        now = time.time()
        token = secrets.token_urlsafe(32)
        prev = self.users.stamp_login(u['name'])
        with self._lock:
            self.sessions[token] = {**u, 'expires': now + SESSION_HOURS * 3600, 'prev_login_utc': prev, 'created': now, 'last_seen': now,
                                    'client': client, 'sid': secrets.token_hex(4), 'via': 'company'}
        self.ws.audit(u['name'], 'login', {'role': u['role'], 'client': client, 'via': 'company', 'claims': result.get('claim_values', [])[:20]})
        return token, u

    def sessions_list(self) -> List[dict]:
        now = time.time()
        with self._lock:
            live = [(t, s) for t, s in self.sessions.items() if s['expires'] > now]
        return [{'sid': s.get('sid'), 'name': s['name'], 'role': s['role'], 'client': s.get('client', ''), 'via': s.get('via', 'password'),
                 'created_utc': _iso_epoch(s.get('created')), 'last_seen_utc': _iso_epoch(s.get('last_seen')), 'expires_utc': _iso_epoch(s['expires'])}
                for t, s in sorted(live, key=lambda x: x[1].get('created') or 0)]

    def sessions_revoke(self, actor: dict, sid: Optional[str] = None, name: Optional[str] = None, keep_token: Optional[str] = None) -> dict:
        """Revoke one session (by sid) or every session of a user (by name), optionally keeping the caller's own."""
        n = 0
        with self._lock:
            for t in list(self.sessions):
                s = self.sessions[t]
                if (sid and s.get('sid') == sid) or (name and s['name'] == name):
                    if keep_token and t == keep_token:
                        continue
                    self.sessions.pop(t, None)
                    n += 1
        self.ws.audit(actor['name'], 'session.revoke', {'sid': sid, 'name': name, 'revoked': n})
        return {'revoked': n}

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            s = self.sessions.pop(token or '', None)
        if s:
            self.ws.audit(s['name'], 'logout', {})

    def session(self, token: Optional[str]) -> Optional[dict]:
        with self._lock:
            s = self.sessions.get(token or '')
            if s and s['expires'] < time.time():
                self.sessions.pop(token, None)
                return None
            if s:
                s['last_seen'] = time.time()
            return dict(s) if s else None

    @staticmethod
    def require(user: Optional[dict], role: str) -> dict:
        if user is None:
            raise ApiError(401, 'sign in first')
        if RANK[user['role']] < RANK[role]:
            raise ApiError(403, f"this action needs the {role} role; you are {user['role']}")
        return user

    # -- helpers ---------------------------------------------------------------------------
    def _well_reports_dir(self, well_id: str) -> str:
        return os.path.join(self.ws.reports_dir, 'wells', well_id)

    def _job(self, args: List[str], user: dict, label: str, inputs: Optional[List[str]] = None, wait: bool = False) -> dict:
        if not args or args[0] not in RUNNABLE:
            raise ApiError(400, f"'{args[0] if args else ''}' is not a command the page may run")
        jid = self.runner.submit(args, actor=user['name'], label=label, inputs=inputs)
        return self.runner.wait(jid, 900) if wait else self.runner.status(jid)

    # -- reads -------------------------------------------------------------------------------
    def overview(self) -> dict:
        s = self.ws.summary()
        dj = os.path.join(self.ws.reports_dir, 'dashboard.json')
        data = None
        if os.path.isfile(dj):
            with open(dj, encoding='utf-8') as f:
                data = json.load(f)
        from . import __version__
        seismic = []
        for st in self.ws.seismic_stations():
            sm = self.ws.seismic_results(st['id']).get('summary') or {}
            seismic.append({'id': st['id'], 'display': st['display'], 'kind': st['kind'], 'detected': sm.get('detected'), 'n_sources': sm.get('n_sources'),
                            'pointed': sm.get('pointed'), 'lines': sm.get('lines'), 'unit': sm.get('unit'), 'generated_utc': sm.get('generated_utc')})
        tracks = []
        for tk in self.ws.tracks():
            sm = self.ws.track_results(tk['id']).get('summary') or {}
            tracks.append({'id': tk['id'], 'display': tk['display'], 'arrays': len(tk['stations']), 'positions': sm.get('positions'), 'verdict': sm.get('verdict'),
                           'heading_deg': sm.get('heading_deg'), 'length_km': sm.get('length_km'), 'generated_utc': sm.get('generated_utc')})
        return {'workspace': s, 'dashboard': data, 'seismic': seismic, 'tracks': tracks, 'jobs_running': [j['id'] for j in self.runner.list(20, 'RUNNING')],
                'taps': dict(self.taps), 'patches': [{k: st[k] for k in ('name', 'protocol', 'well_id', 'status', 'last_sample_utc', 'samples_last_min')} for st in self.patches.states()],
                'service': {'started_utc': self.started_utc, 'program_version': __version__,
                                                     'users': self.users.count(), 'schedule': self.scheduler.entries()}}

    def well_detail(self, well_id: str) -> dict:
        w = self.ws.well(well_id)
        d = self._well_reports_dir(well_id)
        reports = {}
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith('.json') and fn != 'well.json':
                    with open(os.path.join(d, fn), encoding='utf-8') as f:
                        try:
                            reports[fn[:-5]] = json.load(f)
                        except json.JSONDecodeError:
                            pass
        mon = os.path.join(self.ws.path, 'monitor', well_id)
        monitor = None
        if os.path.isdir(mon):
            monitor = {}
            for fn in ('state.json',):
                p = os.path.join(mon, fn)
                if os.path.isfile(p):
                    with open(p, encoding='utf-8') as f:
                        monitor['state'] = json.load(f)
            for fn in ('evaluations.jsonl', 'change_log.jsonl'):
                p = os.path.join(mon, fn)
                if os.path.isfile(p):
                    with open(p, encoding='utf-8') as f:
                        monitor[fn[:-6]] = [json.loads(l) for l in f if l.strip()]
        alarms = []
        p = os.path.join(d, 'alarm_events.jsonl')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                alarms = [json.loads(l) for l in f if l.strip()]
        approvals = []
        p = os.path.join(d, 'well_test_records', 'approvals.jsonl')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                approvals = [json.loads(l) for l in f if l.strip()]
        files = {'reports': sorted(os.listdir(d)) if os.path.isdir(d) else [],
                 'live': sorted(os.listdir(os.path.join(self.ws.path, 'wells', well_id, 'records', 'live')))
                 if os.path.isdir(os.path.join(self.ws.path, 'wells', well_id, 'records', 'live')) else []}
        return {'well': w, 'reports': reports, 'monitor': monitor, 'alarm_events': alarms, 'approvals': approvals,
                'files': files, 'tap_job': self.taps.get(well_id)}

    # -- the charts: windows cut from the series cache -------------------------------------------------
    CHART_DEFAULTS = ('Hook Load', 'Convertible Torque', 'Standpipe Pressure', 'Rate Of Penetration')

    def chart(self, well_id: str, what: str, qs: dict) -> dict:
        from . import series_cache as SCH
        c = SCH.ensure(self.ws, well_id)
        f = lambda k: (float(qs[k][0]) if qs.get(k) and qs[k][0] not in ('', 'null') else None)
        if what == 'meta':
            chans = [{'name': x['name'], 'unit': x['unit'], 'present': x.get('present', 0)} for x in c.meta['channels']]
            live = [x['name'] for x in chans if x['present']]
            dflt = [n for n in self.CHART_DEFAULTS if n in live] or live[:3]
            events = []
            rp = os.path.join(self._well_reports_dir(well_id), 'drilling_record.json')
            if os.path.isfile(rp) and c.meta.get('start_time'):
                from datetime import datetime as _dt
                t0 = _dt.fromisoformat(c.meta['start_time'])
                try:
                    with open(rp, encoding='utf-8') as fh:
                        rec = json.load(fh)
                    for e in rec.get('depth_events') or []:
                        events.append({'type': e['type'], 'at': e['at'], 'x': (_dt.fromisoformat(e['at']) - t0).total_seconds(),
                                       'from_ft': e.get('from_ft'), 'to_ft': e.get('to_ft'), 'effect': e.get('effect', '')})
                except (OSError, ValueError, KeyError):
                    pass
            idx = c.index
            return {'well_id': well_id, 'n': c.meta['n'], 'index_kind': c.meta['index_kind'], 'source_format': c.meta['source_format'],
                    'start_time': c.meta.get('start_time'), 'extent': [float(idx[0]), float(idx[-1])] if len(idx) else [0, 0],
                    'channels': chans, 'default': dflt, 'axes': ['time', 'depth'] if c.extra('drilling') is not None else [c.meta['index_kind']],
                    'events': events, 'sections': self._chart_sections(c)}
        if what == 'window':
            names = [x for x in qs.get('ch', []) if x][:6]
            if not names:
                raise ValueError('name one or more channels: ?ch=Hook Load&ch=Rate Of Penetration')
            pts = int(qs.get('points', ['1200'])[0])
            if qs.get('axis', ['time'])[0] == 'depth':
                return SCH.depth_window(c, names, f('x0'), f('x1'), pts)
            w = SCH.window(c, names, f('x0'), f('x1'), pts)
            if w.get('x1') is not None and (w['x1'] - w['x0']) <= 2 * 86400:
                w['connections'] = SCH.connections(c, w['x0'], w['x1'])
            return w
        if what == 'progress':
            return {'well_id': well_id, 'progress': SCH.progress(c)}
        raise ValueError(f'no chart view {what!r}: meta, window or progress')

    @staticmethod
    def _chart_sections(c) -> list:
        st = c.meta.get('start_time')
        out = []
        for a, b in c.meta.get('sections') or []:
            lab = f'{a / 3600:.1f} h to {b / 3600:.1f} h'
            if st:
                from datetime import datetime as _dt, timedelta as _td
                t0 = _dt.fromisoformat(st)
                lab = f"{(t0 + _td(seconds=a)).isoformat()[:16]} to {(t0 + _td(seconds=b)).isoformat()[:16]}"
            out.append({'x0': a, 'x1': b, 'label': lab})
        return out

    def pad_progress(self) -> dict:
        """Days against depth for every drilling-recorder well in the workspace, for one chart."""
        from . import series_cache as SCH
        out = []
        for w in self.ws.wells():
            d = SCH.cache_dir(self.ws, w['id'])
            c = SCH.load(d, SCH.source_path(self.ws, w['id']))
            if c is None and w['kind'] == 'file':
                try:
                    if self.ws.well(w['id']).get('files') and os.path.isfile(SCH.source_path(self.ws, w['id']) or ''):
                        from .files import detect
                        if detect(SCH.source_path(self.ws, w['id'])).get('kind') == 'edr':
                            c = SCH.ensure(self.ws, w['id'])
                except Exception:
                    c = None
            if c is None or c.extra('bottom') is None:
                continue
            p = SCH.progress(c)
            if p:
                out.append({'well_id': w['id'], 'display': w.get('display') or w['id'], **p})
        return {'wells': out, 'how': 'the running bottom of each hole against days since its first sample; stale and re-referenced depths excluded, a sidetrack as a step back'}

    # -- the control room: the wall board, the shift handover, wall passes ---------------------------
    @staticmethod
    def _cr(fn, *a, **k):
        try:
            return fn(*a, **k)
        except PermissionError as e:
            raise ApiError(403, str(e))
        except KeyError as e:
            raise ApiError(404, str(e.args[0]) if e.args else 'not found')
        except ValueError as e:
            raise ApiError(400, str(e))

    def wall(self) -> dict:
        from . import control_room as CR
        return CR.board(self.ws, self.patches.states())

    def handover_write(self, user: dict, body: dict) -> dict:
        from . import control_room as CR
        b = CR.board(self.ws, self.patches.states())
        counts = {**b['counts'], 'alarms_unacked': sum(((t.get('alarms') or {}).get('unacked') or 0) for t in b['tiles'])}
        return self._cr(CR.write_note, self.ws, user['name'], str(body.get('open', '')), str(body.get('watching', '')), str(body.get('done', '')), counts)

    def handover_ack(self, user: dict, body: dict) -> dict:
        from . import control_room as CR
        return self._cr(CR.ack_note, self.ws, str(body.get('id', '')), user['name'], str(body.get('comment', '')))

    def wall_enter(self, code: str, client: str = '') -> dict:
        """A wall screen presents its pass: checked like a sign-in, with the same lock-out after repeated failures."""
        from . import control_room as CR
        now = time.time()
        keys = [f'wall:{client}'] if client else ['wall:']
        with self._lock:
            waits = [w for w in (self._locked(k, now) for k in keys) if w]
        if waits:
            raise ApiError(429, f'too many wrong wall passes; try again in {max(waits)} s', retry_after=max(waits))
        p = CR.pass_check(self.ws, code)
        if p is None:
            with self._lock:
                self._note_failure(keys, now)
            self.ws.audit('wall', 'wall.pass.failed', {'client': client})
            raise ApiError(401, 'that wall pass is not valid (wrong, expired or revoked)')
        self.ws.audit('wall:' + p['id'], 'wall.pass.enter', {'label': p['label'], 'client': client})
        return p

    def series(self, well_id: str, max_points: int = 1500) -> dict:
        """The well's measured stream, down-sampled by stride for the page's trend plots
        (values, quality per sample, the unit) - read from the source, never from a cache."""
        import math
        from . import ingest, production_live_stream
        w = self.ws.well(well_id)
        if w['kind'] == 'catalog':
            stream, _ = production_live_stream(w['source']['entry'], w['source']['well'], w.get('station_md_ft') or 10000.0)
        else:
            src = os.path.join(self.ws.path, 'wells', well_id, 'source', w['files'][0]) if w['kind'] == 'file' else self.ws.latest_live_stream_csv(well_id)
            if not src:
                raise ApiError(404, 'this live well has no recorded stream yet - start a tap first')
            from .files import read_any
            stream = read_any(src)
        n = len(stream.index)
        stride = max(1, math.ceil(n / max_points))
        idx = [float(x) for x in stream.index[::stride]]
        chans = {}
        for name, ch in stream.channels.items():
            vals = [None if (v != v) else round(float(v), 4) for v in ch.values[::stride]]
            q = list(ch.quality[::stride]) if getattr(ch, 'quality', None) is not None else []
            chans[name] = {'unit': ch.unit, 'values': vals, 'quality': q}
        return {'well_id': well_id, 'index_kind': stream.index_kind, 'index': idx, 'n_source': n, 'stride': stride,
                'start_time': stream.meta.get('start_time'), 'channels': chans}

    # -- files ------------------------------------------------------------------------------
    def files_api(self, user: dict, method: str, sub: str, qs: dict, body: dict):
        from . import files as F
        try:
            if method == 'GET':
                if sub == 'roots':
                    return {'import': F.Roots(self.ws).list('import'), 'export': F.Roots(self.ws).list('export')}
                if sub == 'browse':
                    return F.browse(self.ws, qs.get('root', [''])[0], qs.get('path', [''])[0])
                if sub == 'preview':
                    full = F.Roots(self.ws).resolve('import', qs.get('root', [''])[0], qs.get('path', [''])[0])
                    if not os.path.isfile(full):
                        raise ApiError(404, 'not a file')
                    return F.preview(full)
                raise ApiError(404, 'no such files route')
            App.require(user, 'operator')
            if sub == 'import':
                r = F.import_file(self.ws, str(body.get('root', '')), str(body.get('path', '')), user['name'],
                                  display=body.get('display') or None, station_md_ft=body.get('station_md'))
                if r.get('id'):
                    self.notifier.emit({'event': 'file.imported', 'name': os.path.basename(str(body.get('path', ''))), 'actor': user['name'],
                                        'well_id': r.get('id'), 'key': f"file.imported:{body.get('path', '')}"})
                return r
            if sub == 'export':
                return F.export_report(self.ws, str(body.get('report', '')), str(body.get('root', '')), str(body.get('dest', '')), user['name'])
            if sub == 'pack':
                dest = F.Roots(self.ws).resolve('export', str(body.get('root', '')), str(body.get('dest', '')))
                os.makedirs(dest, exist_ok=True)
                out = os.path.join(dest, f"evidence_{slug(self.ws.manifest['name'])}_{utc_now_iso().replace(':', '').replace('-', '')}.zip")
                return F.evidence_pack(self.ws, out, user['name'])
            if sub == 'watch':
                F.Roots(self.ws).resolve('import', str(body.get('root', '')), '')       # an unknown root is answered here, not in a failed job
                return self._job(['files', '--workspace', self.ws.path, '--action', 'watch', '--root', str(body.get('root', '')), '--actor', user['name']], user, f"watch folder {body.get('root')}")
            App.require(user, 'admin')
            if sub == 'roots/add':
                return F.Roots(self.ws).add(str(body.get('which', 'import')), str(body.get('name', '')), str(body.get('path', '')), user['name'])
            if sub == 'roots/remove':
                F.Roots(self.ws).remove(str(body.get('which', 'import')), str(body.get('name', '')), user['name'])
                return {'ok': True}
            raise ApiError(404, 'no such files route')
        except WorkspaceError as e:
            raise ApiError(400, str(e))

    # -- patches ------------------------------------------------------------------------------
    def patch_add(self, user: dict, body: dict) -> dict:
        try:
            return self.patches.store.add(str(body.get('name', '')), str(body.get('protocol', '')), str(body.get('well_id', '')),
                                          body.get('config') or {}, user['name'], priority=int(body.get('priority', 1)),
                                          stale_after_s=float(body.get('stale_after_s', 120.0)), auto_restart=bool(body.get('auto_restart', True)),
                                          enabled=bool(body.get('enabled', True)))
        except (WorkspaceError, ValueError, KeyError, TypeError) as e:
            raise ApiError(400, str(e))

    def patch_action(self, user: dict, name: str, action: str, body: dict) -> dict:
        try:
            if action == 'start':
                return self.patches.start(name, user['name'])
            if action == 'stop':
                return self.patches.stop(name, user['name'])
            if action == 'remove':
                try:
                    self.patches.stop(name, user['name'])
                except WorkspaceError:
                    pass
                self.patches.store.remove(name, user['name'])
                return {'ok': True}
            if action == 'update':
                fields = {k: body[k] for k in ('enabled', 'auto_restart', 'priority', 'stale_after_s') if k in body}
                return self.patches.store.update(name, user['name'], **fields)
            if action == 'config':
                return self.patches.store.set_config(name, body.get('config') or {}, user['name'])
        except (WorkspaceError, ValueError) as e:
            raise ApiError(400, str(e))
        raise ApiError(404, 'no such patch action')

    def config_defaults(self, name: str):
        if name == 'criteria':
            from .well_test_validation import DEFAULT_CRITERIA
            return DEFAULT_CRITERIA
        if name == 'opcua':
            from .opcua_port import EXAMPLE_CONFIG
            return EXAMPLE_CONFIG
        if name == 'mqtt':
            from .mqtt_port import EXAMPLE_CONFIG
            return EXAMPLE_CONFIG
        if name == 'wits0':
            from .wits0 import EXAMPLE_CONFIG
            return EXAMPLE_CONFIG
        if name == 'witsml':
            from .witsml import EXAMPLE_CONFIG
            return EXAMPLE_CONFIG
        if name == 'etp':
            from .etp import EXAMPLE_CONFIG
            return EXAMPLE_CONFIG
        if name == 'modbus_g6':
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'example_register_map.json'), encoding='utf-8') as f:
                return json.load(f)
        if name in ('modbus_rtu', 'dnp3', 'hartip', 'piwebapi'):
            mod = __import__({'modbus_rtu': 'gea.modbus_rtu', 'dnp3': 'gea.dnp3', 'hartip': 'gea.hartip', 'piwebapi': 'gea.piwebapi'}[name], fromlist=['x'])
            return mod.EXAMPLE_CONFIG
        if name == 'alarm_definitions':
            from .alarm_engine import defaults_from_catalogue
            from .sample_record import TagCatalogue
            from . import ingest
            for w in self.ws.wells():
                if w['kind'] == 'file':
                    from .files import detect as _detect
                    src = os.path.join(self.ws.path, 'wells', w['id'], 'source', w['files'][0])
                    if _detect(src)['kind'] != 'historian_csv':
                        continue                                    # a drilling recorder or a log is not a gauge historian
                    st = ingest(src)
                    return [d.__dict__ for d in defaults_from_catalogue(TagCatalogue.from_stream(st))]
            raise ApiError(404, 'default alarm definitions are derived from a historian well; add one first')
        raise ApiError(404, f'no defaults for {name}' if name else 'choose a configuration by name first (criteria, alarm_definitions, or a port such as wits0)')

    def set_port_config(self, user: dict, well_id: str, config: dict) -> dict:
        w = self.ws.well(well_id)
        if w['kind'] != 'live':
            raise ApiError(400, 'only a live well has a port configuration')
        port = w['source']['port']
        self.validate_config(port, config)
        p = os.path.join(self.ws.path, 'wells', well_id, 'source', w['source']['config'])
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=1)
        from .config_versioning import ConfigStore
        from .workspace import sha256_file
        meta = ConfigStore(self.ws.config_dir).commit(f'{well_id}.{port}', config, user['name'], 'port map edited on the dashboard')
        w['source']['sha256'] = sha256_file(p)
        with open(os.path.join(self.ws.well_dir(well_id), 'well.json'), 'w', encoding='utf-8') as f:
            json.dump(w, f, indent=1)
        self.ws.audit(user['name'], 'port.config', {'well': well_id, 'port': port, 'version': meta.get('version')}, inputs=[p])
        return meta

    def survey(self, user: dict, filename: str, content_b64: str, family: str, lat, elev, demo: bool = False) -> dict:
        stamp = utc_now_iso().replace(':', '').replace('-', '')
        d = self.ws.dir('reports', 'survey', stamp)
        args = ['survey', '--family', family or 'continental_crystalline', '--out', d]
        inputs = []
        if demo:
            args.append('--demo')
        else:
            if not filename or '/' in filename or '\\' in filename:
                raise ApiError(400, 'a plain LAS file name is required')
            p = os.path.join(d, filename)
            with open(p, 'wb') as f:
                f.write(base64.b64decode(content_b64 or ''))
            args += ['--file', p]
            inputs = [p]
        if lat is not None:
            args += ['--lat', str(float(lat))]
        if elev is not None:
            args += ['--elev', str(float(elev))]
        j = self._job(args, user, f"strata survey {'demo' if demo else filename}", inputs=inputs)
        j['out'] = os.path.relpath(d, self.ws.reports_dir).replace(os.sep, '/')
        return j

    def catalog(self) -> List[dict]:
        from . import CATALOG
        from .workspace import catalog_gauge_wells
        out = []
        for name, e in CATALOG.items():
            out.append({'entry': name, 'kind': getattr(e, 'kind', ''), 'region': getattr(e, 'region', ''),
                        'title': getattr(e, 'title', '') or getattr(e, 'description', '')[:120],
                        'gauge_wells': catalog_gauge_wells(name)})
        return out

    def reports_index(self) -> dict:
        root = self.ws.reports_dir
        out = {'site': [], 'wells': {}}
        if not os.path.isdir(root):
            return out
        for fn in sorted(os.listdir(root)):
            p = os.path.join(root, fn)
            if os.path.isfile(p) and fn.split('.')[-1] in ('html', 'md', 'json', 'csv'):
                out['site'].append(fn)
        wd = os.path.join(root, 'wells')
        if os.path.isdir(wd):
            for w in sorted(os.listdir(wd)):
                out['wells'][w] = sorted(f for f in os.listdir(os.path.join(wd, w)) if os.path.isfile(os.path.join(wd, w, f)))
        return out

    def config_index(self) -> List[dict]:
        from .config_versioning import ConfigStore
        cs = ConfigStore(self.ws.config_dir)
        return cs.summary()

    def config_detail(self, name: str) -> dict:
        from .config_versioning import ConfigStore
        cs = ConfigStore(self.ws.config_dir)
        hist = cs.history(name)
        if not hist:
            raise ApiError(404, f'no configuration named {name}' if name else 'choose a configuration by name first (criteria, alarm_definitions, or one in the list)')
        return {'name': name, 'current': cs.get(name), 'history': hist}

    def approvals_queue(self) -> dict:
        """Everything waiting on a person, across wells."""
        q = {'well_tests': [], 'refits': []}
        for w in self.ws.wells():
            d = self._well_reports_dir(w['id'])
            p = os.path.join(d, 'well_test_validation.json')
            if os.path.isfile(p):
                from .well_test_validation import ApprovalTrail, load_criteria
                with open(p, encoding='utf-8') as f:
                    rep = json.load(f)
                crit_path = os.path.join(self.ws.config_dir, 'criteria.json')
                crit = load_criteria(crit_path if os.path.isfile(crit_path) else None)
                trail = ApprovalTrail(os.path.join(d, 'well_test_records'), levels=crit.get('approval_levels'))
                for t in (rep.get('detection') or {}).get('tests', []):
                    st = trail.status_of(t['test_id'])
                    if st['status'].startswith('PENDING'):
                        q['well_tests'].append({'well_id': w['id'], 'test_id': t['test_id'], 'start_utc': t.get('start_utc'),
                                                'end_utc': t.get('end_utc'), 'n': t.get('n'), 'approval': st['status'],
                                                'decisions': st['decisions'], 'virtual_rates': t.get('virtual_rates')})
            mon = os.path.join(self.ws.path, 'monitor', w['id'], 'change_log.jsonl')
            if os.path.isfile(mon):
                with open(mon, encoding='utf-8') as f:
                    entries = [json.loads(l) for l in f if l.strip()]
                decided = {e.get('supersedes_entry_id') for e in entries if e.get('supersedes_entry_id')}
                latest_per_station = {}
                for e in entries:
                    if e.get('status') == 'PROPOSED' and e['entry_id'] not in decided:
                        latest_per_station[e.get('station')] = e        # the newest proposal per station is the open one
                for e in latest_per_station.values():
                    q['refits'].append({'well_id': w['id'], **e})
        return q

    # -- actions ---------------------------------------------------------------------------------
    def add_well_file(self, user: dict, display: str, filename: str, content_b64: str, station_md: Optional[float]) -> dict:
        if not filename or '/' in filename or '\\' in filename:
            raise ApiError(400, 'a plain file name is required')
        tmp_dir = os.path.join(self.ws.path, 'jobs', '_uploads')
        os.makedirs(tmp_dir, exist_ok=True)
        tmp = os.path.join(tmp_dir, f'{secrets.token_hex(4)}_{filename}')
        with open(tmp, 'wb') as f:
            f.write(base64.b64decode(content_b64 or ''))
        try:
            w = self.ws.add_well_file(tmp, display=display or os.path.splitext(filename)[0], actor=user['name'], station_md_ft=station_md, filename=filename)
        except WorkspaceError as e:
            raise ApiError(409, str(e))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        w['source']['original_path'] = f'uploaded: {filename}'
        with open(os.path.join(self.ws.well_dir(w['id']), 'well.json'), 'w', encoding='utf-8') as f:
            json.dump(w, f, indent=1)
        return w

    # -- backup: the site copied off the machine -------------------------------------------------------------
    def backup_config(self) -> dict:
        p = os.path.join(self.ws.path, 'records', 'backup_config.json')
        cfg = {'out_dir': '', 'keep': 14, 'daily_at': '02:30'}
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
            except (OSError, ValueError):
                pass
        return cfg

    def backup_view(self) -> dict:
        from . import backup as BK
        cfg = self.backup_config()
        st = BK.status(self.ws.path)
        sched = next((e for e in self.scheduler.entries() if e['name'] == 'backup-daily'), None)
        return {'config': cfg, 'status': st, 'schedule': sched, 'stale_after_h': BK.STALE_AFTER_H}

    def backup_set_config(self, user: dict, body: dict) -> dict:
        from . import backup as BK
        cfg = self.backup_config()
        out_dir = str(body.get('out_dir', cfg['out_dir'])).strip()
        if out_dir and BK._inside(out_dir, self.ws.path):
            raise ApiError(400, 'the backup folder must be outside the workspace - another disk or a share')
        try:
            keep = int(body.get('keep', cfg['keep']))
        except (TypeError, ValueError):
            raise ApiError(400, 'keep must be a whole number')
        daily_at = str(body.get('daily_at', cfg['daily_at'])).strip() or '02:30'
        if len(daily_at) != 5 or daily_at[2] != ':' or not (daily_at[:2] + daily_at[3:]).isdigit():
            raise ApiError(400, 'daily_at is HH:MM')
        cfg.update({'out_dir': out_dir, 'keep': max(1, keep), 'daily_at': daily_at})
        p = os.path.join(self.ws.dir('records'), 'backup_config.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=1)
        self.ws.audit(user['name'], 'backup.config', cfg)
        return cfg

    def backup_args(self) -> List[str]:
        cfg = self.backup_config()
        if not cfg['out_dir']:
            raise ApiError(400, 'set the backup folder first (another disk or a share)')
        return ['backup', '--workspace', self.ws.path, '--out', cfg['out_dir'], '--keep', str(cfg['keep'])]

    def update_view(self, check_pypi: bool = True) -> dict:
        """The Audit/Update page's Update panel: what runs here, what is newest, where the kit is, what is stale."""
        from . import __version__
        from . import doctor as DR
        import importlib.util
        import platform
        newest = DR.pypi_newest() if check_pypi else None
        extras = {}
        for mod, extra in (('matplotlib', 'plotting'), ('PyQt6', 'desktop'), ('asyncua', 'opcua'), ('paho', 'mqtt'), ('pymodbus', 'modbus'), ('serial', 'serial'), ('xlrd', 'xls')):
            extras[extra] = importlib.util.find_spec(mod) is not None
        ver_t = lambda v: tuple(int(x) if x.isdigit() else 0 for x in str(v).split('.'))
        state = 'unknown' if newest is None else ('current' if ver_t(newest) <= ver_t(__version__) else 'behind')
        return {'running': {'version': __version__, 'code_path': os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'python': platform.python_version(),
                            'executable': sys.executable, 'started_utc': self.started_utc},
                'newest_pypi': newest, 'state': state,
                'links': {'pypi': 'https://pypi.org/project/gea-program/', 'releases': 'https://github.com/Daniel8Murphy0007/GEA-Program/releases',
                          'release': f'https://github.com/Daniel8Murphy0007/GEA-Program/releases/tag/v{newest or __version__}',
                          'changelog': 'https://github.com/Daniel8Murphy0007/GEA-Program/blob/main/CHANGELOG.md'},
                'extras': extras, 'reports': self.ws.report_ages(),
                'note': 'the program update runs pip against PyPI from this Python and needs the service restarted afterwards; an offline kit is updated by '
                        'installing the newer kit from the release page. The data update runs every report from its source again.'}

    def add_track(self, user: dict, body: dict) -> dict:
        """A track over two or more array stations; the optional truth CSV arrives base64 and is copied in by the workspace."""
        stations = body.get('stations') or []
        if len(stations) < 2:
            raise ApiError(400, 'a track needs two or more array stations')
        tmp_dir = os.path.join(self.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            truth_path = None
            f = body.get('truth')
            if f and f.get('filename'):
                name = str(f['filename'])
                if '/' in name or '\\' in name:
                    raise ApiError(400, 'a plain file name is required')
                truth_path = os.path.join(tmp_dir, name)
                with open(truth_path, 'wb') as fh:
                    fh.write(base64.b64decode(f.get('content_b64') or ''))
            band = body.get('band')
            try:
                return self.ws.add_track(str(body.get('display') or 'track'), [str(x) for x in stations], actor=user['name'], truth_csv=truth_path,
                                         band=[float(band[0]), float(band[1])] if band else None, win_s=float(body.get('win_s') or 600.0), note=str(body.get('note', '')))
            except (WorkspaceError, ValueError) as e:
                raise ApiError(409, str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def add_live_station(self, user: dict, body: dict) -> dict:
        """A SeedLink station: the display name and the config (host, port, streams) from the page; the optional
        station file and rigs CSV arrive base64-encoded like the file station's do."""
        cfg = body.get('config')
        if not isinstance(cfg, dict):
            raise ApiError(400, 'a seedlink config object is needed')
        tmp_dir = os.path.join(self.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            extras = {}
            for key in ('stationxml', 'sources'):
                f = body.get(key)
                if f and f.get('filename'):
                    name = str(f['filename'])
                    if '/' in name or '\\' in name:
                        raise ApiError(400, 'a plain file name is required')
                    p = os.path.join(tmp_dir, name)
                    with open(p, 'wb') as fh:
                        fh.write(base64.b64decode(f.get('content_b64') or ''))
                    extras[key] = p
            band = body.get('band')
            try:
                return self.ws.add_live_station(str(body.get('display') or ''), cfg,
                                                float(body['lat']) if body.get('lat') not in (None, '') else None,
                                                float(body['lon']) if body.get('lon') not in (None, '') else None,
                                                actor=user['name'], band=[float(band[0]), float(band[1])] if band else None,
                                                datum=body.get('datum') or None, note=str(body.get('note', '')),
                                                stationxml=extras.get('stationxml'), sources_csv=extras.get('sources'))
            except (WorkspaceError, ValueError) as e:
                raise ApiError(409, str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def add_layer(self, user: dict, body: dict) -> dict:
        """A structure layer arrives as one zip (base64) holding the shapefile's parts; the workspace reads it once and keeps it."""
        f = body.get('file') or {}
        name = str(f.get('filename', ''))
        if not name or '/' in name or '\\' in name:
            raise ApiError(400, 'a plain file name is required')
        tmp_dir = os.path.join(self.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            p = os.path.join(tmp_dir, name)
            with open(p, 'wb') as fh:
                fh.write(base64.b64decode(f.get('content_b64') or ''))
            try:
                return self.ws.add_layer(p, str(body.get('display') or os.path.splitext(name)[0]), actor=user['name'],
                                         note=str(body.get('note', '')), citation=str(body.get('citation', '')), member=(str(body['member']) if body.get('member') else None),
                                         formation=(str(body['formation']) if body.get('formation') else None), role=(str(body['role']) if body.get('role') else None),
                                         feature_kind=(str(body['feature_kind']) if body.get('feature_kind') else None))
            except (WorkspaceError, ValueError) as e:
                raise ApiError(409, str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def add_seismic(self, user: dict, body: dict) -> dict:
        """Records (and the optional station file, sensors CSV and rigs CSV) arrive base64-encoded; each lands in a temporary
        folder and is copied into the workspace verbatim by the workspace, which hashes it."""
        files = body.get('files') or []
        if not files:
            raise ApiError(400, 'at least one record file is needed')
        tmp_dir = os.path.join(self.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
        os.makedirs(tmp_dir, exist_ok=True)
        paths, names = [], []
        try:
            for f in files:
                name = str(f.get('filename', ''))
                if not name or '/' in name or '\\' in name:
                    raise ApiError(400, 'a plain file name is required for every record')
                p = os.path.join(tmp_dir, name)
                with open(p, 'wb') as fh:
                    fh.write(base64.b64decode(f.get('content_b64') or ''))
                paths.append(p); names.append(name)
            extras = {}
            for key in ('stationxml', 'sensors', 'sources', 'permits'):
                f = body.get(key)
                if f and f.get('filename'):
                    name = str(f['filename'])
                    if '/' in name or '\\' in name:
                        raise ApiError(400, 'a plain file name is required')
                    p = os.path.join(tmp_dir, name)
                    with open(p, 'wb') as fh:
                        fh.write(base64.b64decode(f.get('content_b64') or ''))
                    extras[key] = p
            band = body.get('band')
            try:
                st = self.ws.add_seismic_station(str(body.get('display') or os.path.splitext(names[0])[0]), paths,
                                                 float(body['lat']) if body.get('lat') not in (None, '') else None,
                                                 float(body['lon']) if body.get('lon') not in (None, '') else None,
                                                 actor=user['name'], stationxml=extras.get('stationxml'), sensors_csv=extras.get('sensors'),
                                                 sources_csv=extras.get('sources'), band=[float(band[0]), float(band[1])] if band else None,
                                                 filenames=names, note=str(body.get('note', '')), permits_csv=extras.get('permits'))
            except (WorkspaceError, ValueError) as e:
                raise ApiError(409, str(e))
            return st
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def start_tap(self, user: dict, well_id: str, seconds: float) -> dict:
        w = self.ws.well(well_id)
        if w['kind'] != 'live':
            raise ApiError(400, 'only a live well has a tap')
        if well_id in self.taps and self.runner.status(self.taps[well_id])['status'] in ('QUEUED', 'RUNNING'):
            raise ApiError(409, 'a tap is already running for this well')
        port = w['source']['port']
        cfg = os.path.join(self.ws.path, 'wells', well_id, 'source', w['source']['config'])
        live = self.ws.dir('wells', well_id, 'records', 'live')
        stamp = utc_now_iso().replace(':', '').replace('-', '')
        cmd = {'modbus_rtu': 'modbus-rtu'}.get(port, port)
        if cmd == 'modbus_g6':
            args = ['ingest', '--file', cfg, '--port', 'modbus_g6']
        else:
            args = [cmd, '--config', cfg, '--seconds', str(float(seconds)), '--record', os.path.join(live, f'{stamp}_session.jsonl'),
                    '--out', os.path.join(live, f'{stamp}_records.csv'), '--stream-csv', os.path.join(live, f'{stamp}_stream.csv')]
        j = self._job(args, user, f'live tap {well_id} ({port}, {seconds:g} s)', inputs=[cfg])
        self.taps[well_id] = j['id']
        return j

    def stop_tap(self, user: dict, well_id: str) -> dict:
        jid = self.taps.get(well_id)
        if not jid:
            raise ApiError(404, 'no tap running for this well')
        return self.runner.cancel(jid, user['name'])

    def approve_well_test(self, user: dict, well_id: str, test_id: str, level: int, decision: str, note: str) -> dict:
        w = self.ws.well(well_id)
        if w['kind'] != 'catalog':
            raise ApiError(400, 'well tests are validated on catalogue production wells in this version')
        d = self._well_reports_dir(well_id)
        crit = os.path.join(self.ws.config_dir, 'criteria.json')
        args = ['well-test', '--live-catalog', w['source']['entry'], '--live-well', w['source']['well'],
                '--record-dir', os.path.join(d, 'well_test_records'), '--approve', test_id, '--approver', user['name'],
                '--level', str(int(level)), '--decision', decision, '--note', note or '', '--out', d]
        if os.path.isfile(crit):
            args += ['--criteria', crit]
        return self._job(args, user, f'well test {test_id}: {decision} by {user["name"]}', wait=True)

    def approve_refit(self, user: dict, well_id: str, entry: str, decision: str, note: str) -> dict:
        w = self.ws.well(well_id)
        args = ['drift-monitor', '--action', 'approve', '--log-dir', os.path.join(self.ws.path, 'monitor', well_id),
                '--entry', entry, '--approver', f"{user['name']} ({user['role']})", '--decision', decision, '--note', note or '',
                '--name', w['display']]
        if w['kind'] == 'catalog':
            args += ['--live-catalog', w['source']['entry'], '--live-well', w['source']['well'], '--station-md', str(w.get('station_md_ft') or 10000)]
        return self._job(args, user, f're-fit {entry}: {decision} by {user["name"]}', wait=True)

    def ack_alarm(self, user: dict, well_id: str, alarm_id: str) -> dict:
        return self.alarm_action(user, well_id, 'ack', [alarm_id])

    def alarm_action(self, user: dict, well_id: str, action: str, alarm_ids: List[str], hours: Optional[float] = None, note: str = '') -> dict:
        """ack | ack-all | shelve | unshelve on one well: re-processes the well's stream with the action on the record."""
        if action not in ('ack', 'ack-all', 'shelve', 'unshelve'):
            raise ApiError(400, "action must be ack, ack-all, shelve or unshelve")
        ids = [str(x).strip() for x in (alarm_ids or []) if str(x).strip()]
        if action != 'ack-all' and not ids:
            raise ApiError(400, 'name at least one alarm id')
        if hours is not None and not (0 < float(hours) <= 24 * 90):
            raise ApiError(400, 'shelve hours must be between 0 and 2160 (90 days)')
        w = self.ws.well(well_id)
        d = self._well_reports_dir(well_id)
        src = os.path.join(self.ws.path, 'wells', well_id, 'source', w['files'][0]) if w['kind'] == 'file' else self.ws.latest_live_stream_csv(well_id)
        if not src:
            raise ApiError(400, 'this well has no historian file or live stream to process alarms on')
        args = ['alarms', '--file', src, '--event-log', os.path.join(d, 'alarm_events.jsonl'),
                '--operator', user['name'], '--now', utc_now_iso(), '--name', w['display'], '--out', d]
        if note:
            args += ['--note', note[:200]]
        if action == 'ack':
            args += ['--ack', ','.join(ids)]
        elif action == 'ack-all':
            args += ['--ack-all']
        elif action == 'shelve':
            args += ['--shelve', ','.join(ids)]
            if hours is not None:
                args += ['--shelve-hours', str(float(hours))]
        else:
            args += ['--unshelve', ','.join(ids)]
        defs = os.path.join(self.ws.config_dir, 'alarm_definitions.json')
        if os.path.isfile(defs):
            args += ['--definitions', defs]
        label = {'ack': f'acknowledge {", ".join(ids)}', 'ack-all': 'acknowledge all', 'shelve': f'shelve {", ".join(ids)}' + (f' for {hours} h' if hours else ''),
                 'unshelve': f'unshelve {", ".join(ids)}'}[action] + f' on {w["display"]} by {user["name"]}'
        self.ws.audit(user['name'], f'alarm.{action}', {'well_id': well_id, 'alarm_ids': ids, 'hours': hours, 'note': note})
        return self._job(args, user, label, inputs=[src], wait=True)

    def ack_all_wells(self, user: dict, note: str = '') -> dict:
        """One job per well that has unacknowledged alarms; returns them all."""
        out = []
        for w in self.ws.wells():
            rep = os.path.join(self._well_reports_dir(w['id']), 'alarm_event_report.json')
            if not os.path.isfile(rep):
                continue
            try:
                with open(rep, encoding='utf-8') as f:
                    act = json.load(f).get('active', [])
            except (OSError, json.JSONDecodeError):
                continue
            if any(a.get('state') == 'ACTIVE_UNACKED' for a in act):
                try:
                    out.append({'well_id': w['id'], **self.alarm_action(user, w['id'], 'ack-all', [], note=note)})
                except ApiError as e:
                    out.append({'well_id': w['id'], 'status': 'FAILED', 'error': e.message})
        return {'jobs': out}

    def set_site(self, user: dict, site: dict) -> dict:
        """Site-wide display defaults in the manifest: units, time zone, display name, contact line. Admin only."""
        cur = dict(self.ws.manifest.get('site') or {})
        for k, v in (site or {}).items():
            if k not in ('units', 'time_zone', 'display_name', 'contact', 'wizard_done'):
                raise ApiError(400, f'unknown site setting: {k}')
            if k == 'units' and v not in ('field', 'si'):
                raise ApiError(400, "units must be 'field' or 'si'")
            if k in ('time_zone', 'display_name', 'contact') and not isinstance(v, str):
                raise ApiError(400, f'{k} must be text')
            cur[k] = v
        self.ws.manifest['site'] = cur
        self.ws._save()
        self.ws.audit(user['name'], 'site.update', cur)
        return cur

    def notifications_view(self, user: dict) -> dict:
        """Configuration summary (never the header or credential values) and the delivery log tail."""
        cfg = self.notifier.cfg
        chans = []
        if cfg:
            for c in cfg['channels']:
                chans.append({'name': c['name'], 'kind': c['kind'],
                              'target': (c.get('url', '').split('?')[0] if c['kind'] == 'webhook' else f"{c.get('host')} -> {', '.join(c.get('to', []))}"),
                              'secret': bool(c.get('headers') or c.get('password_env'))})
        return {'configured': bool(cfg), 'channels': chans, 'rules': (cfg or {}).get('rules', []), 'quiet_s': (cfg or {}).get('quiet_s'),
                'events': list(__import__('gea.notify', fromlist=['EVENTS']).EVENTS), 'log': self.notifier.tail(50)}

    def notifications_test(self, user: dict, channel: str) -> dict:
        from .notify import NotifyError
        try:
            r = self.notifier.test(channel, user['name'])
        except NotifyError as e:
            raise ApiError(400, str(e))
        self.ws.audit(user['name'], 'notify.test', {'channel': channel, 'ok': r.get('ok')})
        return r

    # -- instruments and transients (Band 2) ------------------------------------------------------
    def _records_dir(self, well_id: str) -> str:
        self.ws.well(well_id)
        d = os.path.join(self.ws.path, 'wells', well_id, 'records')
        os.makedirs(d, exist_ok=True)
        return d

    def instruments(self, well_id: str) -> dict:
        from .sensor_swap import SwapRegister
        from .certificates import CertificateRegister
        d = self._records_dir(well_id)
        inst_path = os.path.join(self._well_reports_dir(well_id), 'instruments.json')
        inst = {}
        if os.path.isfile(inst_path):
            try:
                with open(inst_path, encoding='utf-8') as f:
                    inst = json.load(f)
            except (OSError, json.JSONDecodeError):
                inst = {}
        swaps = SwapRegister(os.path.join(d, 'sensor_swaps.jsonl')).list()
        certs = CertificateRegister(os.path.join(d, 'certificates.jsonl')).list()
        prm_path = os.path.join(d, 'transient_params.json')
        prm = None
        if os.path.isfile(prm_path):
            try:
                with open(prm_path, encoding='utf-8') as f:
                    prm = json.load(f)
            except (OSError, json.JSONDecodeError):
                prm = None
        from .conformance import register_conformance
        conf = register_conformance(certs)
        return {'swaps': swaps, 'certificates': certs, 'certificate_status': inst.get('certificates', []), 'candidates': inst.get('candidates', []),
                'swap_notes': inst.get('swap_notes', []), 'transient_params': prm, 'evaluated': bool(inst),
                'iso17025': {'complete': conf['complete'], 'n': conf['n'], 'incomplete': conf['incomplete'],
                             'by_certificate': {r['certificate_id']: {'status': r['status'], 'carried': r['carried'], 'n_items': r['n_items'], 'missing': r['missing']} for r in conf['rows']}}}

    def swap_add(self, user: dict, well_id: str, body: dict) -> dict:
        from .sensor_swap import SwapRegister
        d = self._records_dir(well_id)
        try:
            e = SwapRegister(os.path.join(d, 'sensor_swaps.jsonl')).add(str(body.get('tag_id', '')), str(body.get('swap_utc', '')), user['name'],
                                                                         str(body.get('old_serial', '')), str(body.get('new_serial', '')),
                                                                         str(body.get('certificate_id', '')), str(body.get('note', '')),
                                                                         source='confirmed candidate' if body.get('from_candidate') else 'recorded')
        except ValueError as ex:
            raise ApiError(400, str(ex))
        self.ws.audit(user['name'], 'swap.add', {'well_id': well_id, **{k: e[k] for k in ('swap_id', 'tag_id', 'swap_utc', 'new_serial')}})
        return e

    def certificate_add(self, user: dict, well_id: str, body: dict) -> dict:
        from .certificates import CertificateRegister
        d = self._records_dir(well_id)
        fpath = None
        if body.get('filename') and body.get('content_b64'):
            import base64
            cdir = os.path.join(d, 'certificates')
            os.makedirs(cdir, exist_ok=True)
            name = os.path.basename(str(body['filename'])) or 'certificate'
            fpath = os.path.join(cdir, f"{slug(str(body.get('certificate_id', 'cert')))}_{name}")
            try:
                with open(fpath, 'wb') as f:
                    f.write(base64.b64decode(str(body['content_b64'])))
            except (ValueError, OSError) as ex:
                raise ApiError(400, f'certificate file: {ex}')
        try:
            e = CertificateRegister(os.path.join(d, 'certificates.jsonl')).add(
                str(body.get('tag_id', '')), str(body.get('serial', '')), str(body.get('certificate_id', '')), str(body.get('issued_utc', '')),
                str(body.get('valid_until_utc', '')), user['name'], str(body.get('lab', '')),
                (float(body['accuracy_pct_fs']) if body.get('accuracy_pct_fs') not in (None, '') else None),
                (float(body['full_scale']) if body.get('full_scale') not in (None, '') else None), str(body.get('unit', '')), fpath, str(body.get('note', '')),
                **{k: (body.get(k) if body.get(k) not in ('',) else None) if k in ('uncertainty', 'uncertainty_k', 'uncertainty_probability') else str(body.get(k) or '')
                   for k in CertificateRegister.CLAUSE_FIELDS})
        except (ValueError, TypeError) as ex:
            raise ApiError(400, str(ex))
        self.ws.audit(user['name'], 'certificate.add', {'well_id': well_id, **{k: e[k] for k in ('certificate_id', 'tag_id', 'serial', 'valid_until_utc')}})
        return e

    def transient_params_set(self, user: dict, well_id: str, params: dict) -> dict:
        from .transient import DEFAULT_PARAMS
        d = self._records_dir(well_id)
        clean = {}
        for k, v in (params or {}).items():
            if k not in DEFAULT_PARAMS:
                raise ApiError(400, f'unknown parameter {k}; the parameters are {", ".join(DEFAULT_PARAMS)}')
            if v in (None, ''):
                continue
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                raise ApiError(400, f'{k} must be a number')
            if clean[k] <= 0:
                raise ApiError(400, f'{k} must be positive')
        with open(os.path.join(d, 'transient_params.json'), 'w', encoding='utf-8') as f:
            json.dump(clean, f, indent=1)
        self.ws.audit(user['name'], 'transient.params', {'well_id': well_id, **clean})
        return clean

    # -- experience: badges, since-last-visit, search ------------------------------------------
    def badges(self) -> dict:
        """The counts the navigation shows: unacknowledged alarms, pending approvals, jobs, patches down."""
        unacked = active = shelved = 0
        for w in self.ws.wells():
            rep = os.path.join(self._well_reports_dir(w['id']), 'alarm_event_report.json')
            if os.path.isfile(rep):
                try:
                    with open(rep, encoding='utf-8') as f:
                        j = json.load(f)
                    act = j.get('active', [])
                    active += len(act)
                    unacked += sum(1 for a in act if a.get('state') == 'ACTIVE_UNACKED')
                    shelved += len(j.get('shelved', []))
                except (OSError, json.JSONDecodeError):
                    pass
        pend = 0
        try:
            aq = self.approvals_queue()
            pend = len(aq.get('well_tests', [])) + len(aq.get('refits', []))
        except Exception:
            pass
        jobs = self.runner.list(200)
        cutoff = time.time() - 24 * 3600
        failed = sum(1 for j in jobs if j.get('status') == 'FAILED' and _epoch(j.get('finished_utc') or j.get('submitted_utc')) >= cutoff)
        running = sum(1 for j in jobs if j.get('status') in ('RUNNING', 'QUEUED'))
        pst = self.patches.states()
        return {'alarms_unacked': unacked, 'alarms_active': active, 'alarms_shelved': shelved, 'approvals_pending': pend,
                'jobs_running': running, 'jobs_failed_24h': failed,
                'patches_down': sum(1 for p in pst if p.get('status') in ('DOWN', 'DEGRADED')), 'patches': len(pst),
                'setup_todo': self._setup_todo(), 'handover_unacked': self._handover_unacked(), 'generated_utc': utc_now_iso()}

    def _handover_unacked(self) -> int:
        """1 when the latest handover note waits for the incoming operator's acknowledgement, for the Control room badge."""
        try:
            from . import control_room as CR
            h = CR.latest(self.ws)
            return 1 if h and not h.get('ack') else 0
        except Exception:
            return 0

    def _setup_todo(self) -> int:
        """The required setup items not done, for the Setup tab's badge."""
        try:
            from . import setup_checklist as _SC
            c = _SC.checklist(self.ws)['counts']
            return c['required'] - c['required_done']
        except Exception:
            return 0

    def since(self, after: Optional[str], limit: int = 50) -> dict:
        """What happened since `after` (ISO UTC; default: the signed-in user's previous sign-in): audit actions grouped, alarm events, jobs."""
        if not after:
            return {'since': None, 'counts': {}, 'items': [], 'alarm_events': 0, 'note': 'first visit - nothing to compare with'}
        t0 = _epoch(after)
        counts: Dict[str, int] = {}
        items = []
        for e in self.ws.audit_log(5000):
            if _epoch(e.get('utc', '')) <= t0:
                continue
            a = e.get('action', '')
            counts[a] = counts.get(a, 0) + 1
            items.append({'timestamp_utc': e.get('utc'), 'actor': e.get('actor'), 'action': a, 'detail': e.get('detail', {})})
        n_alarm = 0
        alarm_items = []
        for w in self.ws.wells():
            log = os.path.join(self._well_reports_dir(w['id']), 'alarm_events.jsonl')
            if not os.path.isfile(log):
                continue
            try:
                with open(log, encoding='utf-8') as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if e.get('event') in ('ACTIVATED', 'SHELVED', 'UNSHELVED') and _epoch(e.get('timestamp_utc', '')) > t0:
                            n_alarm += 1
                            alarm_items.append({'well_id': w['id'], 'well': w['display'], **{k: e.get(k) for k in ('timestamp_utc', 'alarm_id', 'event', 'priority', 'operator')}})
            except OSError:
                pass
        jobs = [j for j in self.runner.list(500) if _epoch(j.get('submitted_utc', '')) > t0]
        items.sort(key=lambda x: x['timestamp_utc'] or '', reverse=True)
        alarm_items.sort(key=lambda x: x['timestamp_utc'] or '', reverse=True)
        return {'since': after, 'counts': counts, 'items': items[:limit], 'alarm_events': n_alarm, 'alarm_items': alarm_items[:limit],
                'jobs': {'total': len(jobs), 'failed': sum(1 for j in jobs if j.get('status') == 'FAILED'),
                         'done': sum(1 for j in jobs if j.get('status') == 'DONE')}}

    def search(self, user: dict, q: str, limit: int = 40) -> dict:
        """One box over wells, alarms, reports, jobs, configuration, patches, schedule and (admin) users."""
        q = (q or '').strip().lower()
        if len(q) < 2:
            raise ApiError(400, 'type at least two characters')
        hits = []

        def hit(kind, title, href, detail=''):
            if len(hits) < limit:
                hits.append({'kind': kind, 'title': title, 'href': href, 'detail': detail})
        for w in self.ws.wells():
            text = f"{w['display']} {w['id']} {w['kind']} {' '.join(w.get('files', []))}".lower()
            if q in text:
                hit('well', w['display'], f"#/wells/{w['id']}", f"{w['kind']} well")
            d = self._well_reports_dir(w['id'])
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if q in fn.lower() and not fn.endswith('.json'):
                        hit('report', fn, f"/reports/wells/{w['id']}/{fn}", w['display'])
                rep = os.path.join(d, 'alarm_event_report.json')
                if os.path.isfile(rep):
                    try:
                        with open(rep, encoding='utf-8') as f:
                            j = json.load(f)
                        for a in j.get('active', []) + j.get('shelved', []):
                            if q in f"{a.get('alarm_id', '')} {a.get('tag_id', '')} {a.get('state', '')} {a.get('priority', '')}".lower():
                                hit('alarm', a['alarm_id'], '#/alarms', f"{a.get('state')} {a.get('priority')} on {w['display']}")
                        for dfn in j.get('definitions', []):
                            if q in f"{dfn.get('alarm_id', '')} {dfn.get('tag_id', '')} {dfn.get('kind', '')}".lower():
                                hit('alarm definition', dfn['alarm_id'], '#/alarms', f"{dfn.get('kind')} on {dfn.get('tag_id')} ({w['display']})")
                    except (OSError, json.JSONDecodeError):
                        pass
        for j in self.runner.list(300):
            if q in f"{j.get('label', '')} {j.get('id', '')} {' '.join(j.get('args', []))} {j.get('status', '')}".lower():
                hit('job', j.get('label') or j.get('id'), f"#/jobs/{j['id']}", f"{j.get('status')} · {j.get('submitted_utc', '')}")
        try:
            from .config_versioning import ConfigStore
            for name in ConfigStore(self.ws.config_dir).names():
                if q in name.lower():
                    hit('configuration', name, '#/config', 'versioned configuration')
        except Exception:
            pass
        for p in self.patches.states():
            if q in f"{p.get('name', '')} {p.get('protocol', '')} {p.get('well_id', '')} {p.get('status', '')}".lower():
                hit('patch', p['name'], f"#/live/{p['name']}", f"{p.get('protocol')} · {p.get('status')}")
        for e in self.scheduler.entries():
            if q in f"{e.get('name', '')} {' '.join(e.get('args', []))}".lower():
                hit('schedule', e.get('name') or e.get('id'), '#/admin', 'scheduled job')
        if RANK[user['role']] >= RANK['admin']:
            for u in self.users.list():
                if q in f"{u['name']} {u['role']}".lower():
                    hit('user', u['name'], '#/admin', u['role'])
        return {'q': q, 'hits': hits, 'truncated': len(hits) >= limit}

    def config_commit(self, user: dict, name: str, content, note: str) -> dict:
        from .config_versioning import ConfigStore
        if not name or not slug(name) == name:
            raise ApiError(400, 'configuration name: letters, digits, dot, dash, underscore')
        self.validate_config(name, content)
        cs = ConfigStore(self.ws.config_dir)
        meta = cs.commit(name, content, user['name'], note or 'committed from the dashboard')
        # the file the commands read is the exported current version
        cs.export(name, os.path.join(self.ws.config_dir, f'{name}.json'))
        self.ws.audit(user['name'], 'config.commit', {'name': name, 'version': meta.get('version'), 'note': note})
        if name == 'notifications':
            self.notifier.reload()
        return meta

    @staticmethod
    def validate_config(name: str, content) -> None:
        """The named configurations the commands read must load the way the commands load them."""
        if name == 'criteria':
            from .well_test_validation import DEFAULT_CRITERIA
            if not isinstance(content, dict):
                raise ApiError(400, 'criteria must be a JSON object')
            unknown = sorted(k for k in content if k not in DEFAULT_CRITERIA)
            if unknown:
                raise ApiError(400, f'criteria: unknown keys {unknown}; the keys are {sorted(DEFAULT_CRITERIA)}')
            lv = content.get('approval_levels', DEFAULT_CRITERIA['approval_levels'])
            if not (isinstance(lv, list) and all(isinstance(x, dict) and 'level' in x and 'role' in x for x in lv)):
                raise ApiError(400, 'criteria.approval_levels must be a list of {"level": n, "role": "..."}')
            for k, v in content.items():
                if k != 'approval_levels' and not isinstance(v, type(DEFAULT_CRITERIA[k])) and not (isinstance(v, int) and isinstance(DEFAULT_CRITERIA[k], float)):
                    raise ApiError(400, f'criteria.{k} must be {type(DEFAULT_CRITERIA[k]).__name__}')
        elif name == 'alarm_definitions':
            from .alarm_engine import AlarmDefinition
            if not isinstance(content, list):
                raise ApiError(400, 'alarm definitions must be a JSON list')
            try:
                for d in content:
                    AlarmDefinition(**d)
            except TypeError as e:
                raise ApiError(400, f'alarm definition: {e}')
        elif name.endswith('.opcua') or name == 'opcua':
            from .opcua_port import load_config
            try:
                load_config(content)
            except (ValueError, KeyError, TypeError) as e:
                raise ApiError(400, f'OPC UA configuration: {e}')
        elif name == 'notifications':
            from .notify import validate, NotifyError
            try:
                validate(content)
            except NotifyError as e:
                raise ApiError(400, f'notifications: {e}')
        elif name.endswith('.mqtt') or name == 'mqtt':
            from .mqtt_port import load_config
            try:
                load_config(content)
            except (ValueError, KeyError, TypeError) as e:
                raise ApiError(400, f'MQTT configuration: {e}')
        elif name.endswith('.wits0') or name == 'wits0':
            from .wits0 import load_config
            try:
                load_config(content)
            except (ValueError, KeyError, TypeError) as e:
                raise ApiError(400, f'WITS0 configuration: {e}')
        elif name.endswith('.witsml') or name == 'witsml':
            from .witsml import load_config
            try:
                load_config(content)
            except (ValueError, KeyError, TypeError) as e:
                raise ApiError(400, f'WITSML configuration: {e}')
        elif name.endswith('.etp') or name == 'etp':
            from .etp import load_config
            try:
                load_config(content)
            except (ValueError, KeyError, TypeError) as e:
                raise ApiError(400, f'ETP configuration: {e}')

    def config_rollback(self, user: dict, name: str, version: int, note: str) -> dict:
        from .config_versioning import ConfigStore
        cs = ConfigStore(self.ws.config_dir)
        meta = cs.rollback(name, int(version), user['name'], note or 'rollback from the dashboard')
        cs.export(name, os.path.join(self.ws.config_dir, f'{name}.json'))
        self.ws.audit(user['name'], 'config.rollback', {'name': name, 'to_version': int(version), 'note': note})
        if name == 'notifications':
            self.notifier.reload()
        return meta

    def verify(self, user: dict, what: str) -> dict:
        if what == 'accept':
            return self._job(['accept'], user, 'acceptance gate from the page')
        if what == 'fat':
            return self._job(['fat-sat', '--kind', 'FAT', '--out', os.path.join(self.ws.reports_dir)], user, 'FAT protocol')
        if what == 'sat':
            return self._job(['fat-sat', '--kind', 'SAT', '--out', os.path.join(self.ws.reports_dir)], user, 'SAT protocol')
        if what == 'sbom':
            return self._job(['sbom', '--out', self.ws.reports_dir], user, 'SBOM')
        if what == 'soak':
            n = max(1, len(self.patches.store.list()))        # the site's own patch count, never fewer than one
            return self._job(['soak', '--patches', str(n), '--seconds', '900', '--outage-every', '120', '--outage-len', '20', '--with-service',
                              '--out', os.path.join(self.ws.reports_dir, 'soak')], user, f'soak: {n} patch(es) through link outages for 15 min')
        if what == 'standalone':
            tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'standalone_check.py')
            if not os.path.isfile(tool):
                raise ApiError(404, 'the standalone check ships with the repository, not the wheel; run it from a checkout')
            import subprocess
            r = subprocess.run([sys.executable, tool], capture_output=True, text=True)
            self.ws.audit(user['name'], 'verify.standalone', {'returncode': r.returncode})
            return {'returncode': r.returncode, 'output': r.stdout[-4000:]}
        raise ApiError(400, "what must be accept | fat | sat | sbom | standalone")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'gea-service'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):      # quiet; the audit log is the record
            pass

        # -- plumbing ------------------------------------------------------------------
        def _cookie(self, name: str = 'gea_session') -> Optional[str]:
            c = self.headers.get('Cookie', '')
            for part in c.split(';'):
                k, _, v = part.strip().partition('=')
                if k == name:
                    return v
            return None

        def _wall_pass(self) -> Optional[dict]:
            from . import control_room as CR
            return CR.pass_check(app.ws, self._cookie('gea_wall'))

        def _secure_headers(self) -> None:
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                                        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
            if self._https():
                self.send_header('Strict-Transport-Security', 'max-age=15552000')

        def _https(self) -> bool:
            if app.tls:
                return True
            return app.behind_proxy and self.headers.get('X-Forwarded-Proto', '').lower() == 'https'

        def _client(self) -> str:
            if app.behind_proxy:
                xff = self.headers.get('X-Forwarded-For', '')
                if xff:
                    return xff.split(',')[0].strip()
            return self.client_address[0]

        def _json(self, status: int, obj, set_cookie: Optional[str] = None, retry_after: Optional[int] = None) -> None:
            if status >= 400 and isinstance(obj, dict) and isinstance(obj.get('error'), str):
                obj = {**obj, 'error': friendly_error(obj['error'])}
            body = json.dumps(_finite(obj), default=str, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self._secure_headers()
            if set_cookie is not None:
                self.send_header('Set-Cookie', set_cookie + ('; Secure' if self._https() else ''))
            if retry_after:
                self.send_header('Retry-After', str(int(retry_after)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: str, status: int = 200) -> None:
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/json',):
                ctype += '; charset=utf-8'
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self._secure_headers()
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 64 * 1024 * 1024:
                raise ApiError(413, 'request too large (64 MB limit)')
            raw = self.rfile.read(n) if n else b''
            if not raw:
                return {}
            try:
                return json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(400, 'the request body must be JSON')

        def _user(self) -> Optional[dict]:
            return app.session(self._cookie())

        def _page_note(self, status: int, title: str, message: str, clear_flow: bool = False) -> None:
            """A plain page for an answer a browser lands on directly (the company sign-in's return), not the API."""
            import html as _html
            body = (f'<!doctype html><meta charset="utf-8"><title>{_html.escape(title)}</title><h2>{_html.escape(title)}</h2>'
                    f'<p>{_html.escape(message)}</p><p><a href="/">Back to the sign-in page</a></p>').encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self._secure_headers()
            if clear_flow:
                self.send_header('Set-Cookie', 'gea_oidc=; HttpOnly; SameSite=Lax; Path=/api/oidc; Max-Age=0' + ('; Secure' if self._https() else ''))
            self.end_headers()
            self.wfile.write(body)

        # -- GET ------------------------------------------------------------------------------
        def do_GET(self):
            try:
                u = urlsplit(self.path)
                path, qs = unquote(u.path), parse_qs(u.query)
                if path == '/' or path == '/index.html':
                    page = os.path.join(WEB_DIR, 'app.html')
                    if os.path.isfile(page):
                        app.page_opened(self._client())
                        return self._file(page)
                    return self._json(200, {'service': 'gea', 'note': 'the page is not installed; the API is at /api/'})
                if path.startswith('/reports/'):
                    user = App.require(self._user(), 'viewer')
                    rel = os.path.normpath(path[len('/reports/'):]).replace('\\', '/')
                    if rel.startswith('..') or os.path.isabs(rel):
                        raise ApiError(400, 'bad path')
                    full = os.path.join(app.ws.reports_dir, rel)
                    if os.path.isdir(full):
                        full = os.path.join(full, 'index.html')
                    if not os.path.isfile(full):
                        raise ApiError(404, 'no such report')
                    return self._file(full)
                if path == '/api/session':
                    s = self._user()
                    from . import oidc as _OI
                    return self._json(200, {'user': ({'name': s['name'], 'role': s['role'], 'prev_login_utc': s.get('prev_login_utc'),
                                                      'prefs': app.users.prefs(s['name']), 'via': s.get('via', 'password')} if s else None),
                                            'users': app.users.count(), 'company_sign_in': _OI.public(app.ws),
                                            'workspace': app.ws.manifest['name'], 'site': app.ws.manifest.get('site', {}),
                                            'wall_pass': (lambda p: {k: p[k] for k in ('id', 'label', 'expires_utc')} if p else None)(self._wall_pass())})
                if path == '/api/oidc/start':
                    from . import oidc as _OI
                    try:
                        url, binding = _OI.begin(app.ws, app.oidc_ssl_context)
                    except _OI.OidcError as e:
                        return self._page_note(400, 'Company sign-in', str(e))
                    self.send_response(302)
                    self.send_header('Location', url)
                    self.send_header('Set-Cookie', f'gea_oidc={binding}; HttpOnly; SameSite=Lax; Path=/api/oidc; Max-Age={_OI.FLOW_S}'
                                     + ('; Secure' if self._https() else ''))
                    self.send_header('Content-Length', '0')
                    self.send_header('Cache-Control', 'no-store')
                    self._secure_headers()
                    self.end_headers()
                    return None
                if path == '/api/oidc/callback':
                    from . import oidc as _OI
                    client = self._client()
                    # no lock counts here: the password, if any, was checked by the company's provider, which keeps its own;
                    # a misconfigured map must not lock a whole office behind one address out of its local accounts too
                    try:
                        res = _OI.finish(app.ws, {k: v[0] for k, v in qs.items()}, self._cookie('gea_oidc'), app.oidc_ssl_context)
                        token, u = app.login_company(res, client)
                    except (_OI.OidcError, ApiError) as e:
                        why = e.message if isinstance(e, ApiError) else str(e)
                        app.ws.audit('unknown', 'login.failed', {'client': client, 'via': 'company', 'why': why[:300]})
                        return self._page_note(401, 'Company sign-in', why, clear_flow=True)
                    sec = '; Secure' if self._https() else ''
                    body = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/"><title>Signed in</title>'
                            '<p>Signed in. <a href="/">Open the control panel</a>.</p>').encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self._secure_headers()
                    # the session cookie is SameSite=Strict, and this answer is the end of a navigation that began at the
                    # company's sign-in page: a redirect would carry that cross-site start to the next page and the browser
                    # would not send the new cookie with it. A page that moves on by itself starts a navigation of its own.
                    self.send_header('Set-Cookie', f'gea_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}{sec}')
                    self.send_header('Set-Cookie', f'gea_oidc=; HttpOnly; SameSite=Lax; Path=/api/oidc; Max-Age=0{sec}')
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                if path == '/api/wall':
                    if self._user() is None and self._wall_pass() is None:
                        raise ApiError(401, 'sign in, or enter a wall pass')
                    return self._json(200, app.wall())
                user = App.require(self._user(), 'viewer')
                if path == '/api/handover':
                    from . import control_room as CR
                    return self._json(200, {'notes': CR.notes(app.ws, int((qs.get('limit') or ['20'])[0]))})
                if path == '/api/wall/passes':
                    from . import control_room as CR
                    App.require(user, 'admin')
                    return self._json(200, {'passes': CR.pass_list(app.ws)})
                if path == '/api/overview':
                    return self._json(200, app.overview())
                if path == '/api/badges':
                    return self._json(200, app.badges())
                if path == '/api/help' or path.startswith('/api/help/'):
                    from . import helplib as H
                    if path == '/api/help':
                        return self._json(200, {'topics': [{**t, 'summary': H.summary_of(t['topic'])} for t in H.topics()], 'views': H.VIEW_TOPIC})
                    t = path[len('/api/help/'):]
                    md = H.page(t)
                    if md is None:
                        raise ApiError(404, f'no help page named {t}')
                    return self._json(200, {'topic': t, 'markdown': md, 'html': H.to_html(md)})
                if path == '/api/sessions':
                    App.require(user, 'admin')
                    return self._json(200, {'sessions': app.sessions_list(), 'session_hours': SESSION_HOURS,
                                            'note': 'sessions live in the service process; a restart signs everyone out'})
                if path == '/api/oidc':
                    from . import oidc as _OI
                    App.require(user, 'admin')
                    return self._json(200, {**_OI.status(app.ws), 'roles': list(_OI.ROLES), 'https': bool(app.tls) or app.behind_proxy})
                if path == '/api/tls':
                    from . import tls as TLS
                    App.require(user, 'admin')
                    st = TLS.status(app.ws)
                    return self._json(200, {**st, 'serving': ({'https': True, 'facts': app.tls} if app.tls else {'https': False, 'behind_proxy': app.behind_proxy}),
                                            'default_names': TLS.default_names(), 'max_days': TLS.MAX_DAYS, 'bits': list(TLS.BITS),
                                            'restart_needed': bool(st.get('configured')) != bool(app.tls) or bool(
                                                app.tls and (st.get('facts') or {}).get('sha256_fingerprint') != app.tls.get('sha256_fingerprint'))})
                if path == '/api/tls/certificate':
                    from . import tls as TLS
                    pem = TLS.certificate_pem(app.ws)
                    if not pem:
                        raise ApiError(404, 'HTTPS is off: there is no certificate to hand out')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-pem-file')
                    self.send_header('Content-Disposition', 'attachment; filename="gea-site-certificate.crt"')
                    self.send_header('Content-Length', str(len(pem)))
                    self.send_header('Cache-Control', 'no-store')
                    self._secure_headers()
                    self.end_headers()
                    self.wfile.write(pem)
                    return None
                if path.startswith('/api/wells/') and path.endswith('/instruments'):
                    return self._json(200, app.instruments(path[len('/api/wells/'):-len('/instruments')]))
                if path == '/api/since':
                    return self._json(200, app.since((qs.get('after') or [user.get('prev_login_utc') or ''])[0] or None, int((qs.get('limit') or ['50'])[0])))
                if path == '/api/search':
                    return self._json(200, app.search(user, (qs.get('q') or [''])[0], int((qs.get('limit') or ['40'])[0])))
                if path == '/api/prefs':
                    return self._json(200, {'prefs': app.users.prefs(user['name']), 'site': app.ws.manifest.get('site', {})})
                if path == '/api/notifications':
                    App.require(user, 'operator')
                    return self._json(200, app.notifications_view(user))
                if path == '/api/wells':
                    return self._json(200, {'wells': app.ws.wells()})
                if path == '/api/control':
                    from . import supervisor as _SV
                    from . import __version__ as _v
                    st = _SV.state(app.ws.path, getattr(self.server, 'gea_started_utc', None) or app.started_utc, _v)
                    svc = getattr(self.server, 'gea_service', None)
                    res = getattr(svc, 'resume', None) if svc else None
                    st['resume'] = ({k: v for k, v in res.items() if not k.startswith('_')} if res else None)
                    if res and res.get('_result'):
                        st['catch_up'] = res['_result'] or {'status': 'RUNNING',
                                                            'detail': 'the catch-up is still running behind the stream'}
                    st['runlog_tail'] = _SV.tail(app.ws.path, 30)
                    st['auto_restart'] = bool(st.get('auto_restart_flag', {}).get('enabled'))
                    st['auto_restart_manifest'] = bool((app.ws.manifest.get('control') or {}).get('auto_restart', False))
                    return self._json(200, st)
                if path == '/api/sites':
                    import os as _os
                    rows = []
                    for st in app.ws.sites():
                        f = _os.path.join(app.ws.dir('reports', 'sites', st['id']), 'site_report.html')
                        g = _os.path.join(app.ws.dir('reports', 'sites', st['id']), 'sra_packet.html')
                        sra_status = None
                        if _os.path.isfile(g):
                            try:
                                with open(g[:-5] + '.json', encoding='utf-8') as fh:
                                    sra_status = (json.load(fh).get('packet') or {}).get('status')
                            except Exception:
                                sra_status = None
                        rows.append({**st, 'report': (_os.path.relpath(f, app.ws.reports_dir).replace('\\', '/') if _os.path.isfile(f) else None),
                                     'report_utc': (datetime.fromtimestamp(_os.path.getmtime(f), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                                                    if _os.path.isfile(f) else None),
                                     'sra_report': (_os.path.relpath(g, app.ws.reports_dir).replace('\\', '/') if _os.path.isfile(g) else None),
                                     'sra_status': sra_status,
                                     'sra_utc': (datetime.fromtimestamp(_os.path.getmtime(g), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                                                 if _os.path.isfile(g) else None)})
                    return self._json(200, {'sites': rows})
                if path == '/api/seismic':
                    import os as _os
                    sdir = app.ws.dir('reports', 'seismic')
                    site = {}
                    for nm in ('seismic_dataset_report', 'seismic_field_report'):
                        f = _os.path.join(sdir, nm + '.html')
                        if _os.path.isfile(f):
                            site[nm] = {'generated_utc': datetime.fromtimestamp(_os.path.getmtime(f), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
                    return self._json(200, {'stations': [{**st, 'summary': app.ws.seismic_results(st['id']).get('summary'),
                                                          **({'live_state': app.live.state(st['id'])} if st.get('live') else {})} for st in app.ws.seismic_stations()],
                                            'site_reports': site})
                if path == '/api/layers':
                    return self._json(200, {'layers': app.ws.layers(), 'stress': app.ws.stress(), 'stress_map': app.ws.stress_map()})
                if path == '/api/layers/members':
                    raise ApiError(405, 'POST the zip to list its shapefiles')
                if path.startswith('/api/layers/') and path.endswith('/at'):
                    lid = path[len('/api/layers/'):-len('/at')]
                    try:
                        app.ws.layer(lid)
                        lat = float(qs.get('lat', [''])[0]); lon = float(qs.get('lon', [''])[0]); rad = float(qs.get('radius_km', ['50'])[0])
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    except ValueError:
                        raise ApiError(400, 'lat and lon are needed')
                    res = [r for r in app.ws.structure_at(lat, lon, rad) if r['layer_id'] == lid]
                    return self._json(200, res[0] if res else {'status': 'NO LAYER'})
                if path.startswith('/api/wells/') and path.endswith('/structure'):
                    wid = path[len('/api/wells/'):-len('/structure')]
                    try:
                        return self._json(200, app.ws.well_structure(wid, float(qs.get('radius_km', ['50'])[0]), actor='page', write=False))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path.startswith('/api/wells/') and path.endswith('/interval'):
                    wid = path[len('/api/wells/'):-len('/interval')]
                    try:
                        return self._json(200, app.ws.well_interval(wid, float(qs.get('radius_km', ['50'])[0]), actor='page', write=False))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path.startswith('/api/wells/') and path.endswith('/slip'):
                    wid = path[len('/api/wells/'):-len('/slip')]
                    try:
                        return self._json(200, app.ws.well_slip(wid, float(qs.get('radius_km', ['25'])[0]), actor='page', write=False))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path == '/api/stress':
                    return self._json(200, {'stress': app.ws.stress(), 'stress_map': app.ws.stress_map()})
                if path == '/api/tools':
                    from . import tool_catalog as _TC
                    return self._json(200, _TC.catalog())
                if path == '/api/setup/checklist':
                    from . import setup_checklist as _SC
                    return self._json(200, _SC.checklist(app.ws))
                if path == '/api/tools/taxonomy':
                    from . import tool_taxonomy as _TX
                    q = qs.get('q', [''])[0]; cat = qs.get('category', [''])[0] or None
                    return self._json(200, {'summary': _TX.summary(), 'parts': [[p, _TX.PART_TITLES[p]] for p in _TX.PARTS],
                                            'classes': _TX.classes(cat, q), 'fleet': _TX.fleet_list(app.ws.path),
                                            'gauge_class_map': _TX.GAUGE_CLASS_TO_TAXONOMY})
                if path == '/api/tools/fleet':
                    from . import tool_taxonomy as _TX
                    return self._json(200, {'tools': _TX.fleet_list(app.ws.path)})
                if path == '/api/catalog/map':
                    from . import catalog_map as _CM, catalog_register as _CR
                    reg = {r['entry']: r for r in _CR.register()}
                    rows = _CM.sidecar_positions()
                    for r in rows:
                        rr = reg.get(r['entry'], {})
                        r['register'] = rr.get('status', ''); r['kind'] = rr.get('kind', ''); r['used_by'] = rr.get('used_by', [])
                        r['well'] = str(app_catalog_well(r['entry']))[:140]
                    return self._json(200, {'entries': rows, 'coast': _CM.coastline(), 'coast_meta': _CM.coastline_meta(), 'counts': _CM.counts(rows),
                                            'statement': _CM.text(rows).splitlines()[0], 'check': _CM.check_sidecars()})
                if path.startswith('/api/catalog/entry/'):
                    from . import catalog_register as _CR
                    from .profile_catalog import CATALOG as _CAT
                    name = path[len('/api/catalog/entry/'):]
                    e = _CAT.get(name)
                    if e is None or e.provenance.get('tier', 'public') != 'public':
                        return self._json(404, {'error': f'no catalogue entry {name!r}'})
                    reg = next((r for r in _CR.register() if r['entry'] == name), {})
                    return self._json(200, {'entry': name, 'register': reg, 'provenance': e.provenance, 'reads_as': _CR.check_entry(e)['shape']})
                if path == '/api/stress/wsm':
                    try:
                        lat, lon = float(qs['lat'][0]), float(qs['lon'][0]); rad = float(qs.get('radius_km', ['100'])[0])
                    except (KeyError, ValueError):
                        raise ApiError(400, 'lat and lon are needed')
                    return self._json(200, app.ws.wsm_at(lat, lon, rad))
                if path == '/api/seismic/live':
                    from .seedlink import EXAMPLE_CONFIG as _SLX
                    return self._json(200, {'stations': app.live.states(), 'example_config': _SLX})
                if path == '/api/tracks':
                    return self._json(200, {'tracks': [{**tk, 'summary': app.ws.track_results(tk['id']).get('summary')} for tk in app.ws.tracks()]})
                if path.startswith('/api/tracks/'):
                    tid = path[len('/api/tracks/'):]
                    try:
                        tk = app.ws.track(tid)
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, {'track': tk, 'results': app.ws.track_results(tid),
                                            'stations': [app.ws.seismic_station(sid) for sid in tk['stations'] if sid in app.ws.manifest.get('seismic', [])]})
                if path == '/api/seismic/sar':
                    return self._json(200, {'film': app.ws.sar_film_summary(), 'url': f'/reports/seismic/{app.ws.SAR_ID}/sar_film.json', 'label': 'SIMULATION_SELF_TEST'})
                if path.startswith('/api/seismic/'):
                    sid = path[len('/api/seismic/'):]
                    try:
                        st = app.ws.seismic_station(sid)
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, {'station': st, 'results': app.ws.seismic_results(sid)})
                if path.startswith('/api/wells/') and '/chart/' in path:
                    wid, _, what = path[len('/api/wells/'):].partition('/chart/')
                    try:
                        return self._json(200, app.chart(wid, what, qs))
                    except (WorkspaceError, KeyError, ValueError) as e:
                        why = str(e) if isinstance(e, WorkspaceError) else str(e.args[0] if e.args else e)
                        if what == 'meta' and any(w['id'] == wid for w in app.ws.wells()):   # a well with nothing to chart yet: a state of the page
                            return self._json(200, {'available': False, 'reason': friendly_error(why)})
                        raise ApiError(404 if isinstance(e, WorkspaceError) else 400, why)
                if path == '/api/map' or path.startswith('/api/map/'):
                    from . import site_map as _SM
                    def _bbox():
                        try:
                            b = [float(x) for x in qs.get('bbox', [''])[0].split(',')]
                            assert len(b) == 4 and b[0] < b[2] and b[1] < b[3]
                            return b
                        except (ValueError, AssertionError):
                            raise ApiError(400, 'bbox is west,south,east,north in degrees')
                    tol = float(qs.get('tol', ['0'])[0] or 0)
                    if path == '/api/map':
                        return self._json(200, _SM.overview(app.ws))
                    if path == '/api/map/outline':
                        return self._json(200, _SM.outline_view(_bbox(), tol))
                    if path == '/api/map/stress':
                        return self._json(200, _SM.stress_view(app.ws, _bbox()))
                    if path == '/api/map/at':
                        try:
                            lat, lon = float(qs['lat'][0]), float(qs['lon'][0])
                        except (KeyError, ValueError):
                            raise ApiError(400, 'lat and lon are needed')
                        return self._json(200, _SM.what_here(app.ws, lat, lon, float(qs.get('radius_km', ['25'])[0])))
                    if path.startswith('/api/map/layer/'):
                        try:
                            return self._json(200, _SM.layer_view(app.ws, path[len('/api/map/layer/'):], _bbox(), tol))
                        except WorkspaceError as e:
                            raise ApiError(404, str(e))
                    raise ApiError(404, 'no such map route')
                if path == '/api/economics':
                    from . import economics as EC
                    return self._json(200, EC.economics(app.ws))
                if path == '/api/collision':
                    from . import collision as CO
                    return self._json(200, App._cr(CO.page, app.ws, (qs.get('scene') or ['workspace'])[0]))
                if path == '/api/plan':
                    from . import wellplan as WPL
                    return self._json(200, App._cr(WPL.page, app.ws, (qs.get('well') or [None])[0]))
                if path == '/api/decline':
                    from . import decline as DCA
                    return self._json(200, App._cr(DCA.page, app.ws, (qs.get('well') or [None])[0], (qs.get('as_of') or [None])[0]))
                if path == '/api/hydraulics':
                    from . import hydraulics as HYD
                    return self._json(200, App._cr(HYD.page, app.ws, (qs.get('scene') or ['workspace'])[0], (qs.get('well') or [None])[0]))
                if path == '/api/casing-design':
                    from . import casing_design as CSG
                    return self._json(200, App._cr(CSG.page, app.ws, (qs.get('scene') or ['workspace'])[0], (qs.get('well') or [None])[0]))
                if path == '/api/torque-drag':
                    from . import torque_drag as TND
                    return self._json(200, App._cr(TND.page, app.ws, (qs.get('scene') or ['workspace'])[0], (qs.get('well') or [None])[0]))
                if path == '/api/pore-pressure':
                    from . import pore_pressure as PPF
                    return self._json(200, App._cr(PPF.page, app.ws, (qs.get('scene') or ['workspace'])[0], (qs.get('well') or [None])[0]))
                if path == '/api/frac-hits':
                    from . import frac_hits as FH
                    return self._json(200, App._cr(FH.page, app.ws))
                if path == '/api/tool-models':
                    from . import wellpos as WP
                    return self._json(200, {'models': WP.model_list(), 'default': WP.DEFAULT_MODEL})
                if path == '/api/rig-time':
                    from . import rig_state as RS
                    return self._json(200, App._cr(RS.page, app.ws))
                if path == '/api/invoice-check':
                    from . import invoice_check as IC
                    return self._json(200, App._cr(IC.page, app.ws, (qs.get('well') or [None])[0]))
                if path == '/api/3d/scenes':
                    from . import well3d as W3
                    return self._json(200, {'scenes': W3.scenes(app.ws)})
                if path.startswith('/api/3d/scene/'):
                    from . import well3d as W3
                    try:
                        return self._json(200, W3.scene(app.ws, path[len('/api/3d/scene/'):]))
                    except KeyError as e:
                        raise ApiError(404, str(e.args[0]))
                if path == '/api/drillstring':
                    from . import drill_string as DS
                    try:
                        return self._json(200, DS.page(app.ws, (qs.get('well') or [None])[0]))
                    except KeyError as e:
                        raise ApiError(404, str(e.args[0]))
                if path == '/api/declare/template':
                    from . import declarations as DC
                    if (qs.get('format') or [''])[0] == 'xlsx':
                        import base64 as _b64
                        import tempfile as _tf
                        with _tf.TemporaryDirectory() as td_:
                            p_ = os.path.join(td_, 'declarations.xlsx')
                            DC.template_xlsx(app.ws, p_)
                            with open(p_, 'rb') as f_:
                                b_ = f_.read()
                        return self._json(200, {'xlsx_b64': _b64.b64encode(b_).decode('ascii'), 'filename': 'declarations.xlsx'})
                    return self._json(200, {'csv': DC.template(app.ws), 'filename': 'declarations.csv'})
                if path == '/api/opseis':
                    from . import op_seismicity as OS
                    over = {}
                    for k_ in ('radius_km', 'screen_km', 'post_days', 'mc'):
                        v_ = (qs.get(k_) or [''])[0]
                        if v_:
                            try:
                                over[k_] = float(v_)
                            except ValueError:
                                raise ApiError(400, f'{k_} must be a number')
                    pr_ = (qs.get('protocol') or [''])[0]
                    if pr_:
                        over['protocol'] = pr_
                    try:
                        return self._json(200, OS.page(app.ws, (qs.get('scene') or ['workspace'])[0], over or None))
                    except KeyError as e:
                        raise ApiError(404, str(e.args[0]))
                if path == '/api/alerts':
                    from . import alerts as AL
                    return self._json(200, {'alerts': AL.recent(app.ws, 200), 'notifications_configured': bool(app.notifier.cfg),
                                            'events': list(AL.EVENTS)})
                if path == '/api/dossier':
                    from . import dossier as DZ
                    return self._json(200, DZ.page(app.ws))
                if path == '/api/pad-summary':
                    from . import pad_summary as PS
                    return self._json(200, PS.status(app.ws))
                if path == '/api/penetrator':
                    from . import penetrator as PN
                    return self._json(200, PN.page(app.ws))
                if path == '/api/tools/motors':
                    from . import drill_string as DS
                    return self._json(200, DS.catalogue())
                if path == '/api/sim/scenes':
                    from . import simulation as SIM
                    return self._json(200, {'scenes': SIM.scenes(app.ws)})
                if path.startswith('/api/sim/scene/'):
                    from . import simulation as SIM
                    import urllib.parse as _up
                    try:
                        return self._json(200, SIM.scene(app.ws, _up.unquote(path[len('/api/sim/scene/'):])))
                    except KeyError as e:
                        raise ApiError(404, str(e.args[0]))
                if path == '/api/pad/progress':
                    return self._json(200, app.pad_progress())
                if path.startswith('/api/wells/') and path.endswith('/series'):
                    return self._json(200, app.series(path[len('/api/wells/'):-len('/series')], int(qs.get('points', ['1500'])[0])))
                if path.startswith('/api/wells/'):
                    return self._json(200, app.well_detail(path[len('/api/wells/'):]))
                if path == '/api/catalog':
                    return self._json(200, {'entries': app.catalog()})
                if path == '/api/reports':
                    return self._json(200, app.reports_index())
                if path == '/api/month-end':
                    from . import month_end as ME
                    sched = next((e for e in app.scheduler.entries() if e['name'] == 'month-end-monthly'), None)
                    return self._json(200, {'packets': ME.packets(app.ws), 'previous': ME.previous_period(), 'schedule': sched})
                if path == '/api/jobs':
                    return self._json(200, {'jobs': app.runner.list(int(qs.get('limit', ['50'])[0]))})
                if path.startswith('/api/jobs/') and path.endswith('/log'):
                    jid = path[len('/api/jobs/'):-len('/log')]
                    return self._json(200, {'id': jid, 'log': app.runner.log(jid, int(qs.get('tail', ['400'])[0]))})
                if path.startswith('/api/jobs/'):
                    return self._json(200, app.runner.status(path[len('/api/jobs/'):]))
                if path == '/api/audit':
                    rows = app.ws.audit_log()
                    actor = qs.get('actor', [''])[0].strip(); action = qs.get('action', [''])[0].strip(); since = qs.get('since', [''])[0].strip()
                    if actor:
                        rows = [r for r in rows if r.get('actor') == actor]
                    if action:
                        rows = [r for r in rows if action in str(r.get('action', ''))]
                    if since:
                        rows = [r for r in rows if str(r.get('utc', '')) >= since]
                    total = len(rows)
                    limit = int(qs.get('limit', ['200'])[0])
                    actors = sorted({r.get('actor', '') for r in app.ws.audit_log()})
                    actions = sorted({str(r.get('action', '')) for r in app.ws.audit_log()})
                    return self._json(200, {'entries': rows[-limit:] if limit else rows, 'total': total, 'actors': actors, 'actions': actions})
                if path == '/api/update':
                    return self._json(200, app.update_view(qs.get('check', ['1'])[0] != '0'))
                if path == '/api/backup':
                    App.require(user, 'admin')
                    return self._json(200, app.backup_view())
                if path == '/api/config':
                    return self._json(200, {'configs': app.config_index()})
                if path.startswith('/api/config/defaults/'):
                    return self._json(200, {'name': path[len('/api/config/defaults/'):], 'content': app.config_defaults(path[len('/api/config/defaults/'):])})
                if path.startswith('/api/config/'):
                    return self._json(200, app.config_detail(path[len('/api/config/'):]))
                if path == '/api/approvals':
                    return self._json(200, app.approvals_queue())
                if path.startswith('/api/files/'):
                    return self._json(200, app.files_api(user, 'GET', path[len('/api/files/'):], qs, {}))
                if path == '/api/patches':
                    return self._json(200, {'patches': app.patches.states(), 'protocols': list(PROTOCOLS)})
                if path.startswith('/api/patches/') and path.endswith('/config'):
                    p = app.patches.store.get(path[len('/api/patches/'):-len('/config')])
                    return self._json(200, {'patch': p, 'config': app.patches.store.load_config(p)})
                if path == '/api/schedule':
                    return self._json(200, {'entries': app.scheduler.entries()})
                if path == '/api/users':
                    App.require(user, 'admin')
                    return self._json(200, {'users': app.users.list()})
                raise ApiError(404, 'no such route')
            except ApiError as e:
                self._json(e.status, {'error': e.message}, retry_after=e.retry_after)
            except (WorkspaceError, FileNotFoundError) as e:
                self._json(404, {'error': str(e)})
            except Exception as e:                                   # never a silent 500
                self._json(500, {'error': f'{type(e).__name__}: {e}'})

        # -- POST ------------------------------------------------------------------------------
        def do_POST(self):
            try:
                path = unquote(urlsplit(self.path).path)
                if self.headers.get('X-GEA-Action') != '1':
                    raise ApiError(403, 'actions need the X-GEA-Action header (the page sets it; a foreign form cannot)')
                body = self._body()
                if path == '/api/login':
                    token, u = app.login(str(body.get('name', '')), str(body.get('password', '')), client=self._client())
                    return self._json(200, {'user': u}, set_cookie=f'gea_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}')
                if path == '/api/logout':
                    app.logout(self._cookie())
                    return self._json(200, {'ok': True}, set_cookie='gea_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if path == '/api/setup':
                    # first administrator, only while the workspace has no users at all
                    if app.users.count() > 0:
                        raise ApiError(409, 'setup is closed: the workspace already has users')
                    u = app.users.add(str(body.get('name', '')), str(body.get('password', '')), 'admin')
                    app.ws.audit(u['name'], 'user.add', {'name': u['name'], 'role': 'admin', 'first': True})
                    return self._json(200, {'user': u})
                if path == '/api/wall/enter':
                    from . import control_room as CR
                    code = str(body.get('code', '')).strip()
                    p = app.wall_enter(code, client=self._client())
                    from datetime import datetime as _dt, timezone as _tz
                    age = max(60, int((_dt.fromisoformat(p['expires_utc'].replace('Z', '+00:00')) - _dt.now(_tz.utc)).total_seconds()))
                    return self._json(200, {'wall_pass': {k: p[k] for k in ('id', 'label', 'expires_utc')}},
                                      set_cookie=f'gea_wall={code}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}')
                if path == '/api/wall/leave':
                    return self._json(200, {'ok': True}, set_cookie='gea_wall=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                user = self._user()
                if path == '/api/handover':
                    App.require(user, 'operator')
                    return self._json(200, app.handover_write(user, body))
                if path == '/api/handover/ack':
                    App.require(user, 'operator')
                    return self._json(200, app.handover_ack(user, body))
                if path == '/api/wall/pass':
                    from . import control_room as CR
                    App.require(user, 'admin')
                    return self._json(200, App._cr(CR.pass_create, app.ws, user['name'], str(body.get('label', '')), body.get('days') or CR.PASS_DAYS_DEFAULT))
                if path == '/api/wall/pass/revoke':
                    from . import control_room as CR
                    App.require(user, 'admin')
                    return self._json(200, App._cr(CR.pass_revoke, app.ws, user['name'], str(body.get('id', ''))))
                if path == '/api/jobs':
                    App.require(user, 'operator')
                    return self._json(200, app._job([str(a) for a in body.get('args', [])], user, str(body.get('label', ''))))
                if path.startswith('/api/jobs/') and path.endswith('/cancel'):
                    App.require(user, 'operator')
                    return self._json(200, app.runner.cancel(path[len('/api/jobs/'):-len('/cancel')], user['name']))
                if path == '/api/wells/file':
                    App.require(user, 'operator')
                    return self._json(200, app.add_well_file(user, str(body.get('display', '')), str(body.get('filename', '')),
                                                             str(body.get('content_b64', '')), body.get('station_md')))
                if path == '/api/wells/catalog':
                    App.require(user, 'operator')
                    try:
                        return self._json(200, app.ws.add_well_catalog(str(body.get('entry') or ''), str(body.get('well') or ''), body.get('station_md'),
                                                                       actor=user['name'], display=body.get('display')))
                    except (KeyError, WorkspaceError) as e:
                        raise ApiError(400, str(e))
                if path == '/api/wells/live':
                    App.require(user, 'operator')
                    cfg_dir = os.path.join(app.ws.path, 'jobs', '_uploads')
                    os.makedirs(cfg_dir, exist_ok=True)
                    tmp = os.path.join(cfg_dir, f'{secrets.token_hex(4)}_port.json')
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(body.get('config', {}), f, indent=1)
                    try:
                        return self._json(200, app.ws.add_well_live(str(body.get('display', '')), str(body.get('port', '')), tmp,
                                                                    actor=user['name'], station_md_ft=body.get('station_md')))
                    except WorkspaceError as e:
                        raise ApiError(400, str(e))
                    finally:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                if path.startswith('/api/wells/') and path.endswith('/remove'):
                    App.require(user, 'admin')
                    app.ws.remove_well(path[len('/api/wells/'):-len('/remove')], actor=user['name'])
                    return self._json(200, {'ok': True})
                if path == '/api/seismic/add':
                    App.require(user, 'operator')
                    return self._json(200, app.add_seismic(user, body))
                if path == '/api/layers/add':
                    App.require(user, 'operator')
                    return self._json(200, app.add_layer(user, body))
                if path == '/api/layers/members':
                    App.require(user, 'operator')
                    from . import shapefile as _SF
                    f = body.get('file') or {}
                    tmp_dir = os.path.join(app.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
                    os.makedirs(tmp_dir, exist_ok=True)
                    try:
                        p = os.path.join(tmp_dir, 'members.zip')
                        with open(p, 'wb') as fh:
                            fh.write(base64.b64decode(f.get('content_b64') or ''))
                        return self._json(200, {'members': _SF.members(p)})
                    finally:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                if path.startswith('/api/layers/') and path.endswith('/remove'):
                    App.require(user, 'admin')
                    try:
                        app.ws.remove_layer(path[len('/api/layers/'):-len('/remove')], actor=user['name'])
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, {'ok': True})
                if path.startswith('/api/wells/') and path.endswith('/structure'):
                    App.require(user, 'operator')
                    wid = path[len('/api/wells/'):-len('/structure')]
                    try:
                        return self._json(200, app.ws.well_structure(wid, float((body or {}).get('radius_km') or 50.0), actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path.startswith('/api/wells/') and path.endswith('/interval'):
                    App.require(user, 'operator')
                    wid = path[len('/api/wells/'):-len('/interval')]
                    try:
                        return self._json(200, app.ws.well_interval(wid, float((body or {}).get('radius_km') or 50.0), actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path.startswith('/api/wells/') and path.endswith('/slip'):
                    App.require(user, 'operator')
                    wid = path[len('/api/wells/'):-len('/slip')]
                    b = body or {}
                    try:
                        return self._json(200, app.ws.well_slip(wid, float(b.get('radius_km') or 25.0), delta_pp_psi=(float(b['delta_pp_psi']) if b.get('delta_pp_psi') is not None else None),
                                                                depth_ft=(float(b['depth_ft']) if b.get('depth_ft') is not None else None), actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                if path == '/api/setup/confirm':
                    App.require(user, 'operator')
                    from . import setup_checklist as _SC
                    b = body or {}
                    try:
                        return self._json(200, _SC.confirm(app.ws, str(b.get('item') or ''), user['name'], str(b.get('note') or '')))
                    except ValueError as e:
                        raise ApiError(400, str(e))
                if path == '/api/sites/add':
                    App.require(user, 'operator')
                    b = body or {}
                    try:
                        return self._json(200, app.ws.add_site(str(b.get('display') or '').strip(), wells=[str(x) for x in (b.get('wells') or [])],
                                                               client=str(b.get('client') or '').strip(), note=str(b.get('note') or ''), actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(400, str(e))
                if path == '/api/tools/fleet':
                    App.require(user, 'operator')
                    from . import tool_taxonomy as _TX
                    b = body or {}
                    try:
                        od = float(b['od_in']) if b.get('od_in') not in (None, '') else None
                        rec = _TX.fleet_add(app.ws.path, str(b.get('class_id') or ''), str(b.get('maker') or ''), str(b.get('model') or ''),
                                            str(b.get('datasheet') or ''), serial=str(b.get('serial') or ''), od_in=od, note=str(b.get('note') or ''),
                                            actor=user['name'], ratings=b.get('ratings') if isinstance(b.get('ratings'), dict) else None)
                    except (ValueError, TypeError) as e:
                        raise ApiError(400, str(e))
                    app.ws.audit(user['name'], 'fleet.add', {'id': rec['id'], 'class': rec['class_id'], 'datasheet': rec['datasheet']})
                    return self._json(200, rec)
                if path.startswith('/api/tools/fleet/') and path.endswith('/remove'):
                    App.require(user, 'operator')
                    from . import tool_taxonomy as _TX
                    try:
                        rec = _TX.fleet_remove(app.ws.path, path[len('/api/tools/fleet/'):-len('/remove')])
                    except ValueError as e:
                        raise ApiError(404, str(e))
                    app.ws.audit(user['name'], 'fleet.remove', {'id': rec['id'], 'class': rec['class_id']})
                    return self._json(200, {'ok': True, 'removed': rec['id']})
                if path == '/api/tools/rating':
                    from . import tool_catalog as _TC
                    b = body or {}
                    try:
                        stations = [(float(x['md_ft']), str(x['tool'])) for x in (b.get('stations') or [])]
                    except (KeyError, TypeError, ValueError):
                        raise ApiError(400, 'each station needs md_ft and tool')
                    try:                                        # the conditions are the declared gradients, and the answer says so
                        return self._json(200, _TC.check_string(stations, profile=None, deviation=None,
                                                                surface_temp_F=float(b.get('surface_temp_F', 75.0)),
                                                                temp_gradient_F_per_ft=float(b.get('temp_gradient_F_per_ft', 0.018)),
                                                                surface_pressure_psi=float(b.get('surface_pressure_psi', 14.7)),
                                                                pressure_gradient_psi_per_ft=float(b.get('pressure_gradient_psi_per_ft', 0.465)),
                                                                name=str(b.get('name', 'toolstring'))))
                    except (ValueError, KeyError) as e:
                        raise ApiError(400, str(e))
                if path == '/api/stress':
                    App.require(user, 'operator')
                    try:
                        return self._json(200, app.ws.set_stress(body or {}, actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(400, str(e))
                if path == '/api/stress/map':
                    App.require(user, 'operator')
                    f = (body or {}).get('file') or {}
                    tmp_dir = os.path.join(app.ws.path, 'jobs', '_uploads', secrets.token_hex(4))
                    os.makedirs(tmp_dir, exist_ok=True)
                    try:
                        p_ = os.path.join(tmp_dir, 'wsm.csv')
                        with open(p_, 'wb') as fh:
                            fh.write(base64.b64decode(f.get('content_b64') or ''))
                        try:
                            return self._json(200, app.ws.add_stress_map(p_, actor=user['name'], citation=str((body or {}).get('citation', ''))))
                        except WorkspaceError as e:
                            raise ApiError(400, str(e))
                    finally:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                if path == '/api/declare':
                    from . import declarations as DC
                    App.require(user, 'operator')
                    t_ = (body or {}).get('text')
                    if (body or {}).get('content_b64'):
                        import base64 as _b64
                        from . import xlsx_read as XR
                        try:
                            raw_ = _b64.b64decode(str(body['content_b64']), validate=True)
                            t_ = XR.text_of(raw_)
                        except (ValueError, KeyError) as e:
                            raise ApiError(400, f'the sheet does not read: {e}')
                    if not isinstance(t_, str) or not t_.strip():
                        raise ApiError(400, 'send the sheet as text (CSV) or an Excel workbook')
                    return self._json(200, DC.apply(app.ws, t_, user['name'], '', write=bool((body or {}).get('apply'))))
                if path.startswith('/api/opseis/'):
                    from . import op_seismicity as OS
                    import base64 as _b64
                    App.require(user, 'operator')
                    act = path[len('/api/opseis/'):]
                    b_ = dict(body or {})
                    if act == 'operation':
                        return self._json(200, App._cr(OS.save_operation, app.ws, user['name'], str(b_.get('well_id', '')), str(b_.get('kind', '')), b_.get('start'),
                                                       b_.get('end'), str(b_.get('source', '')), str(b_.get('note', '')), b_.get('op_id') or None))
                    if act == 'operation/remove':
                        return self._json(200, App._cr(OS.remove_operation, app.ws, user['name'], str(b_.get('op_id', ''))))
                    if act == 'catalog':
                        try:
                            content = _b64.b64decode(str(b_.get('content_b64') or ''), validate=True)
                        except ValueError:
                            raise ApiError(400, 'the catalogue must be sent as base64')
                        if not content:
                            raise ApiError(400, 'send the catalogue file')
                        return self._json(200, App._cr(OS.attach_catalog, app.ws, user['name'], str(b_.get('filename', 'catalog.txt')), content, b_.get('start'), b_.get('end'),
                                                       b_.get('min_magnitude'), b_.get('lat'), b_.get('lon'), b_.get('radius_km'), str(b_.get('source', ''))))
                    if act == 'catalog/remove':
                        return self._json(200, App._cr(OS.remove_catalog, app.ws, user['name'], str(b_.get('file', ''))))
                    if act == 'settings':
                        return self._json(200, App._cr(OS.set_settings, app.ws, user['name'], b_.get('protocol') or None, b_.get('radius_km'), b_.get('screen_km'),
                                                       b_.get('post_days'), b_.get('mc')))
                    if act == 'watch':
                        from . import seismic_watch as SW
                        App._cr(SW.set_settings, app.ws, user['name'], b_.get('enabled'), b_.get('mode') or None, b_.get('every_min'))
                        return self._json(200, SW.status(app.ws))
                    if act == 'watch/poll':
                        from . import seismic_watch as SW
                        r_ = SW.poll(app.ws, app.notifier, force=True)
                        app.ws.audit(user['name'], 'seismicity.watch.poll', {k: r_.get(k) for k in ('ok', 'how', 'in_circle', 'new', 'error', 'why')})
                        return self._json(200, {'result': {k: v for k, v in r_.items() if k != 'alerts'}, 'alerts': r_.get('alerts', []), 'status': SW.status(app.ws)})
                    raise ApiError(404, 'no such operations and seismicity action')
                if path in ('/api/collision/settings', '/api/collision/write'):
                    from . import collision as CO
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    if path.endswith('/settings'):
                        hole = (str(b_['well_id']), b_.get('hole_in')) if b_.get('well_id') else None
                        return self._json(200, App._cr(CO.set_settings, app.ws, user['name'], b_.get('k'), b_.get('surface_margin_m'), b_.get('sigma_pa_m'),
                                                       hole, str(b_.get('source') or '')))
                    r_ = App._cr(CO.write_report, app.ws, _v, user['name'], str(b_.get('scene') or 'workspace'))
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/rig-time/casing', '/api/rig-time/casing/remove'):
                    from . import rig_state as RS
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    if path.endswith('/remove'):
                        return self._json(200, App._cr(RS.remove_casing, app.ws, user['name'], str(b_.get('well_id') or ''), str(b_.get('size') or '')))
                    return self._json(200, App._cr(RS.set_casing, app.ws, user['name'], str(b_.get('well_id') or ''), str(b_.get('size') or ''),
                                                   b_.get('shoe_md_ft'), str(b_.get('source') or ''), b_.get('set')))
                if path in ('/api/decline/attach', '/api/decline/remove', '/api/decline/settings', '/api/decline/write'):
                    from . import decline as DCA
                    from . import wellplan as WPL
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/attach'):
                        return self._json(200, App._cr(DCA.attach, app.ws, wid, str(b_.get('filename') or 'production.csv'), App._cr(WPL.decode_content, b_),
                                                       user['name'], str(b_.get('source') or '')))
                    if path.endswith('/remove'):
                        return self._json(200, App._cr(DCA.remove, app.ws, wid, user['name']))
                    if path.endswith('/settings'):
                        cfg = {k: b_.get(k) for k in list(DCA.LIMITS) + ['price_basis', 'start'] if k in b_}
                        return self._json(200, App._cr(DCA.set_config, app.ws, user['name'], wid, str(b_.get('source') or ''), **cfg))
                    r_ = App._cr(DCA.write_report, app.ws, _v, wid or None, user['name'])
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/hydraulics/settings', '/api/hydraulics/write'):
                    from . import hydraulics as HYD
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/settings'):
                        fann = {k[1:]: b_[k] for k in ('r600', 'r300', 'r200', 'r100', 'r6', 'r3') if b_.get(k) not in (None, '')} if any(
                            b_.get(k) not in (None, '') for k in ('r600', 'r300')) else b_.get('fann')
                        vals = {k: b_.get(k) for k in HYD.LIMITS if k in b_}
                        return self._json(200, App._cr(HYD.set_well, app.ws, user['name'], wid, str(b_.get('source') or ''),
                                                       (b_.get('string') or None), (b_.get('hole') or None), fann, (b_.get('nozzles_32') or None), **vals))
                    r_ = App._cr(HYD.write_report, app.ws, _v, wid or None, user['name'], str(b_.get('scene') or 'workspace'))
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/casing-design/settings', '/api/casing-design/write'):
                    from . import casing_design as CSG
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/settings'):
                        vals = {k: b_.get(k) for k in CSG.LIMITS if k in b_}
                        return self._json(200, App._cr(CSG.set_well, app.ws, user['name'], wid, str(b_.get('source') or ''), (b_.get('strings') or None), **vals))
                    r_ = App._cr(CSG.write_report, app.ws, _v, wid or None, user['name'], str(b_.get('scene') or 'workspace'))
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/torque-drag/settings', '/api/torque-drag/write'):
                    from . import torque_drag as TND
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/settings'):
                        st_ = b_.get('string')
                        if isinstance(st_, str):
                            st_ = App._cr(TND.parse_string, st_) if st_.strip() else None
                        return self._json(200, App._cr(TND.set_well, app.ws, user['name'], wid, str(b_.get('source') or ''), st_, b_.get('mud_ppg'),
                                                       b_.get('mu_cased'), b_.get('mu_open'), b_.get('block_klbf')))
                    r_ = App._cr(TND.write_report, app.ws, _v, wid or None, user['name'], str(b_.get('scene') or 'workspace'))
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/pore-pressure/settings', '/api/pore-pressure/measure', '/api/pore-pressure/unmeasure', '/api/pore-pressure/write'):
                    from . import pore_pressure as PPF
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/settings'):
                        cfg = {k: b_.get(k) for k in list(PPF.LIMITS) + ['indicator'] if k in b_}
                        return self._json(200, App._cr(PPF.set_well, app.ws, user['name'], wid, str(b_.get('source') or ''), **cfg))
                    if path.endswith('/measure'):
                        return self._json(200, App._cr(PPF.add_measurement, app.ws, user['name'], wid, b_.get('depth_m'), b_.get('value'),
                                                       str(b_.get('unit') or 'psi'), str(b_.get('kind') or 'pore'), str(b_.get('depth_kind') or 'tvd'),
                                                       str(b_.get('source') or '')))
                    if path.endswith('/unmeasure'):
                        return self._json(200, App._cr(PPF.remove_measurement, app.ws, user['name'], wid, int(b_.get('index', -1))))
                    r_ = App._cr(PPF.write_report, app.ws, _v, wid or None, user['name'], str(b_.get('scene') or 'workspace'))
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/frac-hits/stages', '/api/frac-hits/remove', '/api/frac-hits/settings', '/api/frac-hits/write'):
                    from . import frac_hits as FH
                    from . import wellplan as WPL
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/stages'):
                        return self._json(200, App._cr(FH.attach_stages, app.ws, wid, str(b_.get('filename') or 'stages.csv'), App._cr(WPL.decode_content, b_),
                                                       user['name'], str(b_.get('source') or ''), str(b_.get('depth_unit') or 'ft'), b_.get('utc_offset_h')))
                    if path.endswith('/remove'):
                        return self._json(200, App._cr(FH.remove_stages, app.ws, wid, user['name']))
                    if path.endswith('/settings'):
                        mon = (str(b_['monitor_id']), b_.get('channel')) if b_.get('monitor_id') else None
                        rule = {k: b_.get(k) for k in FH.DEFAULTS if k in b_}
                        return self._json(200, App._cr(FH.set_settings, app.ws, user['name'], str(b_.get('source') or ''), mon, **rule))
                    r_ = App._cr(FH.write_report, app.ws, _v, user['name'])
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/plan/attach', '/api/plan/remove', '/api/plan/settings', '/api/plan/write'):
                    from . import wellplan as WPL
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    wid = str(b_.get('well_id') or '')
                    if path.endswith('/attach'):
                        dec = b_.get('declination_deg')
                        r_ = App._cr(WPL.attach, app.ws, wid, str(b_.get('filename') or 'plan.csv'), App._cr(WPL.decode_content, b_), user['name'],
                                     str(b_.get('azimuth_reference') or ''), str(b_.get('md_unit') or 'ft'), b_.get('grid') or None,
                                     float(dec) if dec not in (None, '') else None, str(b_.get('source') or ''))
                        return self._json(200, r_)
                    if path.endswith('/remove'):
                        return self._json(200, App._cr(WPL.remove, app.ws, wid, user['name']))
                    if path.endswith('/settings'):
                        prog = b_.get('program')
                        if isinstance(prog, str):
                            prog = App._cr(WPL.parse_program, prog)
                        return self._json(200, App._cr(WPL.set_well, app.ws, user['name'], wid, prog, b_.get('bit_offset_m'), b_.get('lookahead_m'),
                                                       str(b_.get('source') or '')))
                    r_ = App._cr(WPL.write_report, app.ws, _v, wid, user['name'])
                    return self._json(200, {'report_id': r_['report_id']})
                if path in ('/api/rig-time/settings', '/api/rig-time/write'):
                    from . import rig_state as RS
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    if path.endswith('/settings'):
                        bw = (str(b_['well_id']), b_.get('block_weight_klbf')) if b_.get('well_id') else None
                        return self._json(200, App._cr(RS.set_settings, app.ws, user['name'], str(b_.get('source') or ''), b_.get('w2w_min'),
                                                       b_.get('trip_in_ft_h'), b_.get('trip_out_ft_h'), bw))
                    r_ = App._cr(RS.write_report, app.ws, _v, user['name'], b_.get('well_id') or None)
                    return self._json(200, {'report_id': r_['report_id']})
                if path.startswith('/api/invoice-check/'):
                    from . import invoice_check as IC
                    from . import __version__ as _v
                    import base64 as _b64
                    App.require(user, 'operator')
                    b_ = dict(body or {})
                    act = path[len('/api/invoice-check/'):]
                    if act == 'terms':
                        return self._json(200, App._cr(IC.set_terms, app.ws, user['name'], str(b_.get('source') or ''), str(b_.get('day_start') or '06:00'),
                                                       str(b_.get('report_date') or 'start'), {k: b_.get(k) for k in IC.KINDS if b_.get(k) not in (None, '')},
                                                       b_.get('repair_hours_per_job'), b_.get('repair_hours_per_month')))
                    if act == 'attach':
                        try:
                            content = _b64.b64decode(str(b_.get('content_b64') or ''), validate=True)
                        except ValueError:
                            raise ApiError(400, 'the file must be sent as base64')
                        return self._json(200, App._cr(IC.attach, app.ws, user['name'], str(b_.get('well_id') or ''), str(b_.get('kind') or ''),
                                                       str(b_.get('filename') or ''), content, str(b_.get('source') or '')))
                    if act == 'remove':
                        return self._json(200, App._cr(IC.remove, app.ws, user['name'], str(b_.get('file') or '')))
                    if act == 'write':
                        r_ = App._cr(IC.write_report, app.ws, str(b_.get('well_id') or ''), _v, user['name'])
                        return self._json(200, {k: v for k, v in r_.items() if k != 'paths'})
                    raise ApiError(404, 'no such invoice cross-check action')
                if path in ('/api/dossier/timestamp/settings', '/api/dossier/timestamp/test', '/api/dossier/stamp'):
                    from . import dossier as DZ
                    from . import timestamp as TSM
                    b_ = dict(body or {})
                    try:
                        if path.endswith('/settings'):
                            App.require(user, 'admin')
                            en = b_.get('enabled')
                            return self._json(200, App._cr(DZ.set_timestamp, app.ws, user['name'], b_.get('url') if 'url' in b_ else None,
                                                           None if en is None else bool(en), b_.get('anchors') if 'anchors' in b_ else None))
                        if path.endswith('/test'):
                            App.require(user, 'admin')
                            return self._json(200, App._cr(DZ.test_timestamp, app.ws, str(b_.get('url') or '') or None))
                        App.require(user, 'operator')
                        return self._json(200, App._cr(DZ.stamp, app.ws, user['name'], str(b_.get('dossier_id') or '')))
                    except TSM.TimestampError as e:
                        raise ApiError(502, str(e))
                if path == '/api/dossier/rotate':
                    from . import dossier as DZ
                    App.require(user, 'admin')
                    r_ = App._cr(DZ.rotate_key, app.ws, user['name'], str((body or {}).get('reason') or ''))
                    return self._json(200, {'old': r_['old'], 'new': r_['new'], 'statement_sha256': r_['rotation']['statement_sha256'],
                                            'signed_by_old': bool(r_['rotation']['old_signature'])})
                if path in ('/api/pad-summary', '/api/pad-summary/settings'):
                    from . import pad_summary as PS
                    from . import __version__ as _v
                    b_ = dict(body or {})
                    if path == '/api/pad-summary/settings':
                        App.require(user, 'admin')
                        return self._json(200, App._cr(PS.set_settings, app.ws, user['name'], b_.get('enabled'), b_.get('at'), b_.get('send')))
                    App.require(user, 'operator')
                    r_ = App._cr(PS.write, app.ws, _v, user['name'], app.notifier, None, bool(b_.get('send')))
                    return self._json(200, {k: v for k, v in r_.items() if k != 'paths'})
                if path in ('/api/dossier/sign', '/api/dossier/verify'):
                    from . import dossier as DZ
                    from . import __version__ as _v
                    import base64 as _b64
                    App.require(user, 'viewer')
                    b_ = dict(body or {})
                    if path == '/api/dossier/sign':
                        App.require(user, 'operator')
                        return self._json(200, App._cr(DZ.build, app.ws, user['name'], _v, b_.get('well_id') or None, bool(b_.get('include_sources'))))
                    if b_.get('path'):
                        rel = os.path.normpath(str(b_['path'])).replace('\\', '/')
                        if rel.startswith('..') or os.path.isabs(rel) or not rel.startswith('dossiers/'):
                            raise ApiError(400, 'bad path')
                        full = os.path.join(app.ws.reports_dir, rel)
                        if not os.path.isfile(full):
                            raise ApiError(404, 'no such dossier')
                        return self._json(200, DZ.verify(full, app.ws))
                    try:
                        content = _b64.b64decode(str(b_.get('content_b64') or ''), validate=True)
                    except ValueError:
                        raise ApiError(400, 'the dossier must be sent as base64')
                    if not content:
                        raise ApiError(400, 'send a dossier, or the path of one this site signed')
                    import tempfile as _tf
                    td = _tf.mkdtemp()
                    try:
                        p_ = os.path.join(td, 'dossier.zip')
                        with open(p_, 'wb') as fh:
                            fh.write(content)
                        r_ = DZ.verify(p_, app.ws)
                        r_['dossier'] = str(b_.get('filename') or 'uploaded dossier')
                        return self._json(200, r_)
                    finally:
                        shutil.rmtree(td, ignore_errors=True)
                if path.startswith('/api/penetrator/'):
                    from . import penetrator as PN
                    App.require(user, 'operator')
                    act = path[len('/api/penetrator/'):]
                    b_ = dict(body or {})
                    wid = str(b_.pop('well_id', ''))
                    if act == 'install':
                        pid = str(b_.pop('penetrator', ''))
                        keep = ('installed', 'installer', 'certified', 'certificate', 'motor_type', 'gauge_distance_in', 'insulation', 'ir_test_volts',
                                'ir_min_megohm', 'ir_min_source', 'void_test_psi', 'void_test_minutes', 'void_held', 'note')
                        return self._json(200, App._cr(PN.save_install, app.ws, user['name'], wid, pid, **{k: b_[k] for k in keep if k in b_}))
                    if act == 'drive':
                        keep = ('maker', 'model', 'max_volts', 'max_amps', 'freq_min_hz', 'freq_max_hz', 'phases', 'motor_volts', 'motor_amps', 'area',
                                'gas_group', 'temp_class', 'ambient_min_f', 'ambient_max_f', 'source')
                        return self._json(200, App._cr(PN.save_drive, app.ws, user['name'], wid, **{k: b_[k] for k in keep if k in b_}))
                    if act == 'remove':
                        return self._json(200, App._cr(PN.remove, app.ws, user['name'], wid, str(b_.get('what', ''))))
                    raise ApiError(404, 'no such penetrator action')
                if path.startswith('/api/drillstring/'):
                    from . import drill_string as DS
                    App.require(user, 'operator')
                    act = path[len('/api/drillstring/'):]
                    if act == 'run':
                        b_ = body or {}
                        return self._json(200, App._cr(DS.save_run, app.ws, user['name'], str(b_.get('well_id', '')), str(b_.get('name', '')),
                                                       b_.get('components') or [], b_.get('start') or None, b_.get('end') or None, b_.get('depth_in_ft'),
                                                       b_.get('depth_out_ft'), str(b_.get('note', '')), b_.get('run_id') or None))
                    if act == 'remove':
                        return self._json(200, App._cr(DS.remove_run, app.ws, user['name'], str((body or {}).get('run_id', ''))))
                    raise ApiError(404, 'no such drill string action')
                if path == '/api/reports/well-report':
                    from . import well_report as WR
                    from . import __version__ as _v
                    App.require(user, 'operator')
                    if body.get('pad'):
                        r = WR.write_pad(app.ws, _v, actor=user['name'])
                        return self._json(200, {'report_id': r['report_id'], 'url': '/reports/pad_report.html'})
                    wid = str(body.get('well_id', ''))
                    if not any(w['id'] == wid for w in app.ws.wells()):
                        raise ApiError(404, f'no well {wid}')
                    r = WR.write_well(app.ws, wid, _v, actor=user['name'])
                    return self._json(200, {'report_id': r['report_id'], 'missing': r['missing'], 'url': f'/reports/wells/{wid}/well_report.html'})
                if path.startswith('/api/economics/'):
                    from . import economics as EC
                    import base64 as _b64
                    App.require(user, 'operator')
                    act = path[len('/api/economics/'):]
                    wid = str(body.get('well_id', ''))
                    if act == 'rate':
                        return self._json(200, App._cr(EC.set_rate, app.ws, wid, user['name'], body.get('usd_per_day'), str(body.get('what', '')),
                                                       str(body.get('source', '')), body.get('from') or None, body.get('to') or None))
                    if act == 'item':
                        return self._json(200, App._cr(EC.add_item, app.ws, wid, user['name'], body.get('usd'), str(body.get('what', '')),
                                                       str(body.get('source', '')), body.get('section')))
                    if act == 'afe':
                        return self._json(200, App._cr(EC.set_afe, app.ws, wid, user['name'], body.get('usd'), str(body.get('source', ''))))
                    if act == 'remove':
                        return self._json(200, App._cr(EC.remove, app.ws, wid, user['name'], str(body.get('kind', '')), int(body.get('index') or 0)))
                    if act == 'refresh':
                        return self._json(200, {'series': EC.refresh_prices(app.ws, user['name'])})
                    if act == 'import':
                        try:
                            content = _b64.b64decode(str(body.get('content_b64', '')), validate=True)
                        except ValueError:
                            raise ApiError(400, 'content_b64 is not base64')
                        return self._json(200, App._cr(EC.import_series, app.ws, user['name'], str(body.get('name', '')), str(body.get('unit', '')),
                                                       str(body.get('source', '')), content, body.get('date_column') or None, body.get('value_column') or None))
                    raise ApiError(404, 'no such economics action')
                if path.startswith('/api/wells/') and path.endswith('/trajectory'):
                    from . import well3d as W3
                    import base64 as _b64
                    App.require(user, 'operator')
                    wid = path[len('/api/wells/'):-len('/trajectory')]
                    try:
                        content = _b64.b64decode(str(body.get('content_b64', '')), validate=True)
                    except ValueError:
                        raise ApiError(400, 'content_b64 is not base64')
                    if not content:
                        raise ApiError(400, 'the survey file is empty')
                    dec = body.get('declination_deg')
                    return self._json(200, App._cr(W3.attach_trajectory, app.ws, wid, str(body.get('filename') or 'survey.csv'), content, user['name'],
                                                   str(body.get('azimuth_reference', '')), str(body.get('md_unit') or 'ft'),
                                                   body.get('depth_reference') or None, body.get('grid') or None,
                                                   (float(dec) if dec not in (None, '') else None), str(body.get('source', ''))))
                if path.startswith('/api/wells/') and path.endswith('/declare'):
                    App.require(user, 'operator')
                    wid = path[len('/api/wells/'):-len('/declare')]
                    b = body or {}
                    try:
                        return self._json(200, app.ws.set_well(wid, surface=(b.get('surface') or None), disposal=(b.get('disposal') or None),
                                                               identity=(b.get('identity') or None), actor=user['name']))
                    except WorkspaceError as e:
                        raise ApiError(400, str(e))
                if path == '/api/elevation':
                    App.require(user, 'operator')
                    from .elevation import EPQS_URL as _EPQS, epqs as _epqs
                    b = body or {}
                    try:
                        lat, lon = float(b['lat']), float(b['lon'])
                    except (KeyError, TypeError, ValueError):
                        raise ApiError(400, 'lat and lon are needed')
                    return self._json(200, _epqs(lat, lon, url=str(b.get('url') or _EPQS)))
                if path == '/api/seismic/live/add':
                    App.require(user, 'operator')
                    return self._json(200, app.add_live_station(user, body))
                if path.startswith('/api/seismic/live/') and path.count('/') == 5:
                    App.require(user, 'operator')
                    _, _, _, _, sid, action = path.split('/')
                    try:
                        app.ws.seismic_station(sid)
                        if action == 'start':
                            return self._json(200, app.live.start(sid, user['name']))
                        if action == 'stop':
                            return self._json(200, app.live.stop(sid, user['name']))
                        if action == 'fold':
                            return self._json(200, app.ws.fold_live_station(sid, actor=user['name'], include_open=bool((body or {}).get('include_open'))))
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    except (ValueError, OSError) as e:
                        raise ApiError(409, str(e))
                    raise ApiError(404, f'no such live-station action: {action}')
                if path == '/api/tracks/add':
                    App.require(user, 'operator')
                    return self._json(200, app.add_track(user, body))
                if path.startswith('/api/tracks/') and path.endswith('/refresh'):
                    App.require(user, 'operator')
                    tid = path[len('/api/tracks/'):-len('/refresh')]
                    try:
                        app.ws.track(tid)
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, app._job(['workspace', '--path', app.ws.path, '--action', 'refresh-track', '--track', tid, '--actor', user['name']],
                                                    user, f'refresh track {tid}'))
                if path.startswith('/api/tracks/') and path.endswith('/remove'):
                    App.require(user, 'admin')
                    try:
                        app.ws.remove_track(path[len('/api/tracks/'):-len('/remove')], actor=user['name'])
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, {'ok': True})
                if path == '/api/seismic/sar/run':
                    App.require(user, 'operator')
                    hours = float(body.get('hours', 8.0)); step = float(body.get('step_s', 600.0)); seed = int(body.get('seed', 5))
                    method = str(body.get('method', 'bartlett')); band = body.get('band_hz') or [1.0, 20.0]; scene = str(body.get('scene', 'rigs'))
                    if not (0.5 <= hours <= 24 and 60 <= step <= 3600 and method in ('bartlett', 'capon') and len(band) == 2 and 0 < float(band[0]) < float(band[1]) <= 25
                            and scene in ('rigs', 'lateral')):
                        raise ApiError(400, 'sar-film: hours 0.5-24, step 60-3600 s, method bartlett|capon, band 0 < lo < hi <= 25 Hz, scene rigs|lateral')
                    args = ['workspace', '--path', app.ws.path, '--action', 'sar-film', '--hours', str(hours), '--step', str(step), '--seed', str(seed),
                            '--method', method, '--band', str(float(band[0])), str(float(band[1])), '--scene', scene, '--actor', user['name']]
                    return self._json(200, app._job(args, user, 'SAR film (synthetic scene)'))
                if path in ('/api/control/shutdown', '/api/control/restart'):
                    App.require(user, 'admin')
                    from . import supervisor as _SV
                    svc = getattr(self.server, 'gea_service', None)
                    if svc is None:
                        raise ApiError(409, 'this process is not the serving one, so it cannot stop or start it')
                    restart = path.endswith('restart')
                    # a restart is authorised explicitly, every time. A control that reboots the program on a
                    # stray click is not a control, and the launcher is what actually brings it back - so the
                    # answer says what will happen rather than leaving the operator looking at a dead window.
                    if restart and not bool(body.get('authorize')):
                        raise ApiError(400, 'a restart needs authorising: send {"authorize": true}. The control panel will stop and the launcher will '
                                            'start it again; anyone using it will be signed out and will have to sign in once it is back')
                    reason = str(body.get('reason') or ('authorised from the control panel' if restart else 'stopped from the control panel'))[:200]
                    r = svc.request_exit(_SV.EXIT_RESTART if restart else _SV.EXIT_CLEAN, user['name'], reason)
                    return self._json(200, r)
                if path == '/api/oidc':
                    from . import oidc as _OI
                    App.require(user, 'admin')
                    act = str(body.get('action') or '')
                    if act == 'set':
                        sett = {k: body[k] for k in list(_OI.DEFAULTS) + ['enabled'] if k in body}
                        return self._json(200, App._cr(_OI.configure, app.ws, user['name'], sett, str(body.get('client_secret') or '') or None))
                    if act == 'test':
                        return self._json(200, App._cr(_OI.test, app.ws, app.oidc_ssl_context))
                    if act == 'off':
                        return self._json(200, _OI.turn_off(app.ws, user['name']))
                    raise ApiError(400, 'action is set, test or off')
                if path == '/api/tls':
                    from . import tls as TLS
                    App.require(user, 'admin')
                    act = str(body.get('action') or '')
                    if act == 'self-signed':
                        names = body.get('names') or []
                        if isinstance(names, str):
                            names = [n for n in names.replace(',', ' ').split() if n]
                        st = App._cr(TLS.make_self_signed, app.ws, user['name'], [str(n) for n in names] or None,
                                     int(body.get('days') or TLS.MAX_DAYS), int(body.get('bits') or 2048))
                    elif act == 'site':
                        st = App._cr(TLS.use_site_certificate, app.ws, user['name'], str(body.get('cert') or ''), str(body.get('key') or ''))
                    elif act == 'off':
                        st = TLS.turn_off(app.ws, user['name'])
                    else:
                        raise ApiError(400, 'action is self-signed, site or off')
                    return self._json(200, {**st, 'restart_needed': True,
                                            'next': 'takes effect when the control panel next starts: Restart it from Operations control, then open it at '
                                                    + ('https://' if st.get('configured') else 'http://') + 'the same address'})
                if path == '/api/control/auto-restart':
                    App.require(user, 'admin')
                    on = bool(body.get('enabled'))
                    ctl = app.ws.manifest.setdefault('control', {})
                    ctl['auto_restart'] = on
                    app.ws._save()
                    app.ws.audit(user['name'], 'control.auto_restart', {'enabled': on})
                    from . import supervisor as _SV
                    # the manifest is the record of the choice; the flag file is how it reaches the launcher,
                    # which is a batch file and cannot read JSON. Both, or the setting is decoration.
                    _SV.set_auto_restart(app.ws.path, on)
                    _SV.log(app.ws.path, 'auto_restart', enabled=on, actor=user['name'], flag=_SV.auto_flag_path(app.ws.path))
                    return self._json(200, {'auto_restart': on, 'flag': _SV.auto_flag_path(app.ws.path),
                                            'launcher_reads': _SV.auto_restart(app.ws.path),
                                            'detail': ('the launcher will start the control panel again by itself if it stops unexpectedly'
                                                       if on else
                                                       'the launcher will hand the window to a PowerShell prompt when the control panel stops')})
                if path == '/api/month-end/run':
                    App.require(user, 'operator')
                    from . import month_end as ME
                    period = str(body.get('period') or 'previous')
                    if period != 'previous':
                        try:
                            ME.period_bounds(period)
                        except ValueError as e:
                            raise ApiError(400, str(e))
                    args = ['workspace', '--path', app.ws.path, '--action', 'month-end', '--period', period, '--actor', user['name']]
                    if body.get('no_refresh'):
                        args.append('--no-refresh')
                    return self._json(200, app._job(args, user, f'month-end packet {period}'))
                if path == '/api/month-end/schedule':
                    App.require(user, 'admin')
                    try:
                        day = int(body.get('day', 1)); at = str(body.get('at') or '03:00')
                    except (TypeError, ValueError):
                        raise ApiError(400, 'day is a whole number 1..28')
                    if not (1 <= day <= 28) or len(at) != 5 or at[2] != ':' or not (at[:2] + at[3:]).isdigit():
                        raise ApiError(400, 'day is 1..28 and at is HH:MM')
                    args = ['workspace', '--path', app.ws.path, '--action', 'month-end', '--period', 'previous', '--actor', 'schedule']
                    return self._json(200, app.scheduler.add('month-end-monthly', args, user['name'], daily_at=at, monthly_day=day))
                if path == '/api/backup/config':
                    App.require(user, 'admin')
                    return self._json(200, app.backup_set_config(user, body))
                if path == '/api/backup/run':
                    App.require(user, 'admin')
                    return self._json(200, app._job(app.backup_args() + ['--actor', user['name']], user, 'backup: the site copied off the machine'))
                if path == '/api/backup/schedule':
                    App.require(user, 'admin')
                    cfg = app.backup_config()
                    args = app.backup_args() + ['--actor', 'schedule']
                    return self._json(200, app.scheduler.add('backup-daily', args, user['name'], daily_at=cfg['daily_at']))
                if path == '/api/update/program':
                    App.require(user, 'admin')
                    extras = body.get('extras') or 'live,plotting,xls,desktop'
                    return self._json(200, app._job(['update', '--extras', str(extras)], user, 'program update from PyPI'))
                if path == '/api/update/data':
                    App.require(user, 'operator')
                    return self._json(200, app._job(['workspace', '--path', app.ws.path, '--action', 'refresh-all', '--actor', user['name']], user, 'data update: every report from its source'))
                if path.startswith('/api/seismic/') and path.endswith('/refresh'):
                    App.require(user, 'operator')
                    sid = path[len('/api/seismic/'):-len('/refresh')]
                    app.ws.seismic_station(sid)
                    return self._json(200, app._job(['workspace', '--path', app.ws.path, '--action', 'refresh-seismic', '--station', sid, '--actor', user['name']],
                                                    user, f'refresh seismic station {sid}'))
                if path.startswith('/api/seismic/') and path.endswith('/remove'):
                    App.require(user, 'admin')
                    try:
                        app.ws.remove_seismic_station(path[len('/api/seismic/'):-len('/remove')], actor=user['name'])
                    except WorkspaceError as e:
                        raise ApiError(404, str(e))
                    return self._json(200, {'ok': True})
                if path.startswith('/api/wells/') and path.endswith('/tap/start'):
                    App.require(user, 'operator')
                    return self._json(200, app.start_tap(user, path[len('/api/wells/'):-len('/tap/start')], float(body.get('seconds', 60))))
                if path.startswith('/api/wells/') and path.endswith('/tap/stop'):
                    App.require(user, 'operator')
                    return self._json(200, app.stop_tap(user, path[len('/api/wells/'):-len('/tap/stop')]))
                if path.startswith('/api/wells/') and path.endswith('/swaps'):
                    App.require(user, 'operator')
                    return self._json(200, app.swap_add(user, path[len('/api/wells/'):-len('/swaps')], body))
                if path.startswith('/api/wells/') and path.endswith('/certificates'):
                    App.require(user, 'operator')
                    return self._json(200, app.certificate_add(user, path[len('/api/wells/'):-len('/certificates')], body))
                if path.startswith('/api/wells/') and path.endswith('/transient-params'):
                    App.require(user, 'operator')
                    return self._json(200, app.transient_params_set(user, path[len('/api/wells/'):-len('/transient-params')], body['params'] if isinstance(body.get('params'), dict) else body))
                if path.startswith('/api/wells/') and path.endswith('/port-config'):
                    App.require(user, 'operator')
                    return self._json(200, app.set_port_config(user, path[len('/api/wells/'):-len('/port-config')], body.get('config') or {}))
                if path == '/api/survey':
                    App.require(user, 'operator')
                    return self._json(200, app.survey(user, str(body.get('filename', '')), str(body.get('content_b64', '')), str(body.get('family', '')),
                                                      body.get('lat'), body.get('elev'), bool(body.get('demo', False))))
                if path.startswith('/api/files/'):
                    App.require(user, 'operator')
                    return self._json(200, app.files_api(user, 'POST', path[len('/api/files/'):], {}, body))
                if path == '/api/patches/add':
                    App.require(user, 'operator')
                    return self._json(200, app.patch_add(user, body))
                if path.startswith('/api/patches/'):
                    rest = path[len('/api/patches/'):]
                    name, _, action = rest.rpartition('/')
                    App.require(user, 'admin' if action == 'remove' else 'operator')
                    return self._json(200, app.patch_action(user, name, action, body))
                if path == '/api/refresh':
                    App.require(user, 'operator')
                    args = ['workspace', '--path', app.ws.path, '--action', 'refresh', '--actor', user['name']]
                    if body.get('month'):
                        args += ['--month', str(body['month'])]
                    if body.get('sat'):
                        args += ['--sat']
                    for o in body.get('outages', []) or []:
                        args += ['--outage', str(o)]
                    return self._json(200, app._job(args, user, 'refresh the dashboard'))
                if path == '/api/approvals/well-test':
                    App.require(user, 'approver')
                    return self._json(200, app.approve_well_test(user, str(body['well_id']), str(body['test_id']), int(body.get('level', 1)),
                                                                 str(body.get('decision', 'APPROVED')), str(body.get('note', ''))))
                if path == '/api/approvals/refit':
                    App.require(user, 'approver')
                    return self._json(200, app.approve_refit(user, str(body['well_id']), str(body['entry']),
                                                             str(body.get('decision', 'APPLIED')), str(body.get('note', ''))))
                if path == '/api/alarms/ack':
                    App.require(user, 'operator')
                    ids = body.get('alarm_ids') or ([body['alarm_id']] if body.get('alarm_id') else [])
                    return self._json(200, app.alarm_action(user, str(body['well_id']), 'ack', ids, note=str(body.get('note', ''))))
                if path == '/api/alarms/ack-all':
                    App.require(user, 'operator')
                    if body.get('well_id'):
                        return self._json(200, app.alarm_action(user, str(body['well_id']), 'ack-all', [], note=str(body.get('note', ''))))
                    return self._json(200, app.ack_all_wells(user, str(body.get('note', ''))))
                if path == '/api/alarms/shelve':
                    App.require(user, 'operator')
                    ids = body.get('alarm_ids') or ([body['alarm_id']] if body.get('alarm_id') else [])
                    hours = body.get('hours')
                    return self._json(200, app.alarm_action(user, str(body['well_id']), 'shelve', ids,
                                                            hours=(float(hours) if hours not in (None, '') else None), note=str(body.get('note', ''))))
                if path == '/api/alarms/unshelve':
                    App.require(user, 'operator')
                    ids = body.get('alarm_ids') or ([body['alarm_id']] if body.get('alarm_id') else [])
                    return self._json(200, app.alarm_action(user, str(body['well_id']), 'unshelve', ids, note=str(body.get('note', ''))))
                if path == '/api/prefs':
                    return self._json(200, {'prefs': app.users.set_prefs(user['name'], body.get('prefs') or body)})
                if path == '/api/sessions/revoke':
                    if body.get('name') == user['name'] and not body.get('sid'):
                        return self._json(200, app.sessions_revoke(user, name=user['name'], keep_token=self._cookie()))   # sign me out everywhere else
                    App.require(user, 'admin')
                    return self._json(200, app.sessions_revoke(user, sid=body.get('sid'), name=body.get('name')))
                if path == '/api/notifications/test':
                    App.require(user, 'admin')
                    return self._json(200, app.notifications_test(user, str(body.get('channel', ''))))
                if path == '/api/site':
                    App.require(user, 'admin')
                    return self._json(200, {'site': app.set_site(user, body.get('site') or body)})
                if path == '/api/config/commit':
                    App.require(user, 'operator')
                    return self._json(200, app.config_commit(user, str(body.get('name', '')), body.get('content'), str(body.get('note', ''))))
                if path == '/api/config/rollback':
                    App.require(user, 'admin')
                    return self._json(200, app.config_rollback(user, str(body['name']), int(body['version']), str(body.get('note', ''))))
                if path == '/api/verify':
                    App.require(user, 'operator')
                    return self._json(200, app.verify(user, str(body.get('what', 'accept'))))
                if path == '/api/schedule/add':
                    App.require(user, 'admin')
                    args = [str(a) for a in body.get('args', [])]
                    if not args or args[0] not in RUNNABLE:
                        raise ApiError(400, 'a schedule entry needs a runnable command')
                    try:
                        return self._json(200, app.scheduler.add(str(body['name']), args, user['name'], every_s=body.get('every_s'),
                                                                 daily_at=body.get('daily_at'), monthly_day=body.get('monthly_day')))
                    except (KeyError, ValueError) as e:
                        raise ApiError(400, str(e))
                if path == '/api/schedule/remove':
                    App.require(user, 'admin')
                    app.scheduler.remove(str(body['name']), user['name'])
                    return self._json(200, {'ok': True})
                if path == '/api/schedule/enable':
                    App.require(user, 'admin')
                    app.scheduler.set_enabled(str(body['name']), bool(body.get('enabled', True)), user['name'])
                    return self._json(200, {'ok': True})
                if path == '/api/users/add':
                    App.require(user, 'admin')
                    u = app.users.add(str(body.get('name', '')), str(body.get('password', '')), str(body.get('role', 'viewer')))
                    app.ws.audit(user['name'], 'user.add', {'name': u['name'], 'role': u['role']})
                    return self._json(200, {'user': u})
                if path == '/api/users/password':
                    App.require(user, 'viewer')
                    target = str(body.get('name', user['name']))
                    if target != user['name']:
                        App.require(user, 'admin')
                    app.users.set_password(target, str(body.get('password', '')))
                    app.ws.audit(user['name'], 'user.password', {'name': target})
                    return self._json(200, {'ok': True})
                if path == '/api/users/role':
                    App.require(user, 'admin')
                    app.users.set_role(str(body['name']), str(body['role']))
                    app.ws.audit(user['name'], 'user.role', {'name': body['name'], 'role': body['role']})
                    return self._json(200, {'ok': True})
                if path == '/api/users/disable':
                    App.require(user, 'admin')
                    if str(body['name']) == user['name']:
                        raise ApiError(400, 'you cannot disable yourself')
                    app.users.set_disabled(str(body['name']), bool(body.get('disabled', True)))
                    app.ws.audit(user['name'], 'user.disable' if body.get('disabled', True) else 'user.enable', {'name': body['name']})
                    return self._json(200, {'ok': True})
                raise ApiError(404, 'no such route')
            except ApiError as e:
                self._json(e.status, {'error': e.message}, retry_after=e.retry_after)
            except KeyError as e:
                self._json(400, {'error': f'missing field: {e}'})
            except (WorkspaceError, FileNotFoundError) as e:
                self._json(404, {'error': str(e)})
            except Exception as e:
                self._json(500, {'error': f'{type(e).__name__}: {e}'})

    return Handler


class _Server(ThreadingHTTPServer):
    """The HTTP server, speaking TLS when it is given a context. Each connection's handshake is done in that
    connection's own thread, so a slow or broken client never holds up the others; a connection whose first byte is
    not a TLS handshake is plain HTTP on the HTTPS port, and is answered with a redirect to the same address over
    HTTPS when it named this machine by a name the certificate carries, or told to use HTTPS when it did not."""
    tls_context: Optional[ssl.SSLContext] = None
    tls_names: Tuple[str, ...] = ()
    tls_refused = 0

    def finish_request(self, request, client_address):
        ctx = self.tls_context
        if ctx is None:
            return super().finish_request(request, client_address)
        from . import tls as _TLS
        try:
            request.settimeout(_TLS.HANDSHAKE_S)
            first = request.recv(1, socket.MSG_PEEK)
        except OSError:
            return None
        if not first:
            return None
        if first != b'\x16':
            return self._plain_on_tls(request)
        try:
            conn = ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            self.tls_refused += 1                 # an untrusting browser, a scanner, an old client: nothing to log per connection
            return None
        try:
            conn.settimeout(None)
            self.RequestHandlerClass(conn, client_address, self)
        finally:
            try:
                conn.close()
            except OSError:
                pass
        return None

    def _plain_on_tls(self, request) -> None:
        data = b''
        try:
            while b'\r\n\r\n' not in data and len(data) < 8192:
                chunk = request.recv(2048)
                if not chunk:
                    break
                data += chunk
        except OSError:
            return None
        head = data.split(b'\r\n\r\n', 1)[0].decode('latin-1', 'replace').split('\r\n')
        parts = head[0].split(' ') if head else []
        target = parts[1] if len(parts) >= 2 and parts[1].startswith('/') and not parts[1].startswith('//') else '/'
        target = ''.join(c for c in target if 32 < ord(c) < 127)[:2000] or '/'
        host = next((h.split(':', 1)[1].strip() for h in head[1:] if h.lower().startswith('host:')), '')
        name = host[1:host.index(']')] if host.startswith('[') and ']' in host else host.rsplit(':', 1)[0] if host.count(':') == 1 else host
        port = self.server_address[1]
        if name and name.lower() in {n.lower() for n in self.tls_names}:
            loc = f"https://{('[' + name + ']') if ':' in name else name}:{port}{target}"
            body = f'This service speaks HTTPS. Go to {loc}\n'.encode('ascii', 'replace')
            msg = (f'HTTP/1.1 301 Moved Permanently\r\nLocation: {loc}\r\nContent-Type: text/plain; charset=utf-8\r\n'
                   f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode('ascii', 'replace') + body
        else:
            body = (f'This service speaks HTTPS: open https://<this machine>:{port}/ with a name its certificate carries '
                    f'({", ".join(self.tls_names) or "see the administrator"}).\n').encode('ascii', 'replace')
            msg = (f'HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n'
                   'Connection: close\r\n\r\n').encode('ascii') + body
        try:
            request.sendall(msg)
        except OSError:
            pass
        return None

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return                                # a client that went away mid-request: not the service's error
        return super().handle_error(request, client_address)


class Service:
    """Start/stop wrapper (used by `gea serve` and by the acceptance suite in-process)."""

    def __init__(self, workspace_path: str, host: str = '127.0.0.1', port: int = 8765, workers: int = 1, scheduler: bool = True,
                 behind_proxy: bool = False, tls: bool = True):
        self.ws = Workspace(workspace_path)
        self.app = App(self.ws, workers=workers, scheduler=scheduler)
        self.app.behind_proxy = behind_proxy
        from . import tls as _TLS
        ctx = _TLS.context(self.ws) if tls else None          # TlsError when HTTPS is on and its files are not right: no start
        self.httpd = _Server((host, port), make_handler(self.app))
        if ctx is not None:
            st = _TLS.status(self.ws)
            self.httpd.tls_context = ctx
            self.httpd.tls_names = tuple((st.get('facts') or {}).get('names') or ())
            self.app.tls = st.get('facts') or {}
            self.tls_status = st
        else:
            self.tls_status = None
        self.httpd.gea_service = self          # the handler reaches the run it belongs to, to stop or restart it
        self.httpd.gea_started_utc = None
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None
        # how this run ends. The launcher reads it: EXIT_RESTART means run me again, anything else means
        # hand the window to a PowerShell prompt. Nothing else decides this.
        from . import supervisor as _SV
        self.exit_code = _SV.EXIT_CLEAN
        self.started_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.httpd.gea_started_utc = self.started_utc
        self.resume: Optional[dict] = None
        self._exit_reason = ''
        self.console = False                   # `gea serve` turns these on; the gate, which runs the service in-process, leaves them off
        self.open_browser = False

    # what the serving console says as things happen. The audit file is the record and is written first; these
    # are the few entries an operator watching the window wants to see without opening anything.
    CONSOLE_ACTIONS = {
        'login': lambda d: f"signed in: {d['actor']} ({d['detail'].get('role', '?')}) from {d['detail'].get('client') or 'the browser'}",
        'login.failed': lambda d: f"sign-in refused for '{d['actor']}' from {d['detail'].get('client') or 'the browser'}",
        'logout': lambda d: f"signed out: {d['actor']}",
        'user.add': lambda d: (f"first administrator created: {d['detail'].get('name')}" if d['detail'].get('first')
                               else f"user added: {d['detail'].get('name')} ({d['detail'].get('role')}) by {d['actor']}"),
        'job.submit': lambda d: f"job started: {d['detail'].get('label') or d['detail'].get('kind')} [{d['detail'].get('id')}] by {d['actor']}",
        'job.finish': lambda d: f"job {str(d['detail'].get('status', '')).lower() or 'finished'}: [{d['detail'].get('id')}] exit {d['detail'].get('returncode')}",
        'service.stop': lambda d: (f"stop requested by {d['actor']}: {d['detail'].get('reason') or 'no reason given'}"
                                   if d['actor'] != 'system' else 'service stopped'),
        'service.restart': lambda d: f"restart requested by {d['actor']}: {d['detail'].get('reason') or 'no reason given'}",
        'session.revoke': lambda d: f"session(s) revoked by {d['actor']}: {d['detail'].get('revoked')}",
    }

    def _console_line(self, msg: str) -> None:
        print(f"   {datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

    def _on_audit(self, entry: dict) -> None:
        f = self.CONSOLE_ACTIONS.get(entry.get('action') or '')
        if f is not None:
            self._console_line(f(entry))

    def _open_browser_later(self, delay_s: float = 1.0) -> None:
        """Open the operator's browser on the page once the socket is listening (it is: the server bound its
        port when it was built, and a request that lands before serve_forever runs waits in the backlog). A
        browser that cannot be opened is reported, never fatal - the address is on the console."""
        def _go():
            time.sleep(delay_s)
            try:
                import webbrowser
                ok = webbrowser.open(self.url, new=2)
            except Exception as e:
                ok = False
                self._console_line(f'could not open a browser ({e}); open {self.url} yourself')
                return
            if not ok:
                self._console_line(f'no browser could be opened from here; open {self.url} yourself')
        threading.Thread(target=_go, name='gea-open-browser', daemon=True).start()

    def request_exit(self, code: int, actor: str = 'system', reason: str = '') -> dict:
        """Stop this run with a stated code and a stated reason, both written to the run log before the
        server is touched - so a machine that loses power in the middle of a restart still finds out what
        was being attempted."""
        from . import supervisor as _SV
        self.exit_code = int(code)
        self._exit_reason = reason
        event = 'restart' if int(code) == _SV.EXIT_RESTART else 'stop'
        _SV.log(self.ws.path, event, reason=reason, actor=actor, code=int(code), url=self.url)
        self.ws.audit(actor, f'service.{event}', {'reason': reason, 'code': int(code)})
        threading.Thread(target=self.httpd.shutdown, name='gea-exit', daemon=True).start()
        return {'event': event, 'code': int(code), 'reason': reason,
                'detail': ('the launcher will start the control panel again' if event == 'restart'
                           else 'the launcher will hand this window to a PowerShell prompt')}

    @property
    def url(self) -> str:
        if self.app.tls:
            host = self.host
            names = list(self.httpd.tls_names)
            if host in ('0.0.0.0', '::', '') or host not in names:
                host = next((n for n in names if n == 'localhost'), None) if self.host in ('127.0.0.1', '::1') else None
                host = host or (names[0] if names else self.host)
            return f"https://{('[' + host + ']') if ':' in host else host}:{self.port}/"
        return f'http://{self.host}:{self.port}/'

    def start(self) -> 'Service':
        self._thread = threading.Thread(target=self.httpd.serve_forever, name='gea-service', daemon=True)
        self._thread.start()
        self.ws.audit('system', 'service.start', {'url': self.url})
        return self

    def serve_forever(self) -> int:
        from . import supervisor as _SV
        from . import __version__ as _v
        _SV.log(self.ws.path, 'start', version=_v, url=self.url, workspace=self.ws.path)
        # the setting lives in the manifest and is acted on from the flag file. Write it out at every start,
        # so a workspace that was copied without the records directory still launches the way it was set.
        _SV.set_auto_restart(self.ws.path, bool((self.ws.manifest.get('control') or {}).get('auto_restart', False)))
        self.ws.audit('system', 'service.start', {'url': self.url})
        self.resume = _SV.resume(self.ws, self.app.patches, 'supervisor')
        if self.console:
            self.app.console = self._console_line
            self.ws.on_audit = self._on_audit
            self._console_line(f'listening at {self.url} - waiting for a browser to open the page')
        if self.open_browser:
            self._console_line('opening the control panel in your browser; if no page appears within a few seconds, open '
                               f'{self.url} yourself')
            self._open_browser_later()
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            _SV.log(self.ws.path, 'stop', reason='Ctrl+C at the console', actor='console', code=_SV.EXIT_CLEAN)
            self.exit_code = _SV.EXIT_CLEAN
        finally:
            self.stop()
        return self.exit_code

    def stop(self) -> None:
        self.app.stop_background()
        self.app.scheduler.stop()
        self.app.patches.stop_all()
        self.app.live.stop_all()
        self.app.runner.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.ws.audit('system', 'service.stop', {})
