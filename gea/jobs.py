# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""jobs - every action the dashboard offers is a job, and every job is a
`gea` command.

A job runs `python -m gea <args>` as a subprocess with the workspace as its
working directory, captures its output to `jobs/<id>/log.txt`, and keeps its
state in `jobs/<id>/job.json` (QUEUED, RUNNING, DONE, FAILED, CANCELLED, with
the return code and times). There is one code path: the command the page
runs is the command the acceptance gate covers and the command a person would
type. A job that fails shows its log; nothing is silently skipped.

The scheduler keeps recurring jobs in `jobs/schedule.json` - each entry a
name, an interval (`every_s`) or a daily time (`daily_at` "HH:MM" UTC) or a
monthly day (`monthly_day`, with `daily_at`), the command's arguments, and
the last run - and submits them when due. Nothing runs twice for one due time.

Headless-safe: stdlib only.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional

STATES = ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED')


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class JobRunner:
    """A queue of `gea` commands run one or more at a time, each with its own log."""

    def __init__(self, workspace, workers: int = 1, python: Optional[str] = None, on_finish: Optional[Callable[[dict], None]] = None):
        self.ws = workspace
        self.python = python or sys.executable
        self.q: 'queue.Queue[str]' = queue.Queue()
        self._procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_finish = on_finish
        self._threads = [threading.Thread(target=self._worker, name=f'gea-job-worker-{i}', daemon=True) for i in range(max(1, workers))]
        for t in self._threads:
            t.start()

    # -- files ---------------------------------------------------------------------
    def _dir(self, job_id: str) -> str:
        return os.path.join(self.ws.jobs_dir, job_id)

    def _read(self, job_id: str) -> dict:
        p = os.path.join(self._dir(job_id), 'job.json')
        for attempt in range(20):                        # a writer may be mid-replace; the file is never half-written (see _write)
            try:
                with open(p, encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                time.sleep(0.05)
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def _write(self, job: dict) -> None:
        p = os.path.join(self._dir(job['id']), 'job.json')
        tmp = p + '.tmp'
        with self._lock:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(job, f, indent=1)
            os.replace(tmp, p)                            # atomic on every platform the program runs on

    # -- API ------------------------------------------------------------------------
    def submit(self, args: List[str], actor: str, label: str = '', kind: str = '', inputs: Optional[List[str]] = None) -> str:
        """Queue `python -m gea <args>`; returns the job id at once."""
        job_id = _iso(_now()).replace(':', '').replace('-', '') + '-' + uuid.uuid4().hex[:6]
        os.makedirs(self._dir(job_id), exist_ok=True)
        job = {'id': job_id, 'kind': kind or (args[0] if args else ''), 'label': label or ' '.join(args), 'actor': actor,
               'args': list(args), 'status': 'QUEUED', 'submitted_utc': _iso(_now()), 'started_utc': None,
               'finished_utc': None, 'returncode': None, 'log': os.path.join(self._dir(job_id), 'log.txt')}
        self._write(job)
        self.ws.audit(actor, 'job.submit', {'id': job_id, 'kind': job['kind'], 'label': job['label']}, inputs=inputs)
        self.q.put(job_id)
        return job_id

    def status(self, job_id: str) -> dict:
        return self._read(job_id)

    def log(self, job_id: str, tail: Optional[int] = None) -> str:
        p = os.path.join(self._dir(job_id), 'log.txt')
        if not os.path.isfile(p):
            return ''
        with open(p, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return ''.join(lines[-tail:] if tail else lines)

    def list(self, limit: int = 50, status: Optional[str] = None) -> List[dict]:
        out = []
        if not os.path.isdir(self.ws.jobs_dir):
            return out
        for name in sorted(os.listdir(self.ws.jobs_dir), reverse=True):
            p = os.path.join(self.ws.jobs_dir, name, 'job.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    j = json.load(f)
                if status is None or j['status'] == status:
                    out.append(j)
            if len(out) >= limit:
                break
        return out

    def cancel(self, job_id: str, actor: str) -> dict:
        job = self._read(job_id)
        with self._lock:
            proc = self._procs.get(job_id)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if job['status'] in ('QUEUED', 'RUNNING'):
            job['status'] = 'CANCELLED'
            job['finished_utc'] = _iso(_now())
            self._write(job)
        self.ws.audit(actor, 'job.cancel', {'id': job_id})
        return job

    def wait(self, job_id: str, timeout_s: float = 600.0, poll_s: float = 0.2) -> dict:
        """Block until the job leaves QUEUED/RUNNING (for scripts and the gate)."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            j = self._read(job_id)
            if j['status'] not in ('QUEUED', 'RUNNING'):
                return j
            time.sleep(poll_s)
        return self._read(job_id)

    def shutdown(self) -> None:
        self._stop.set()

    # -- the worker -----------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            job = self._read(job_id)
            if job['status'] != 'QUEUED':
                continue
            job['status'] = 'RUNNING'
            job['started_utc'] = _iso(_now())
            self._write(job)
            env = dict(os.environ)
            env['PYTHONIOENCODING'] = 'utf-8'
            env['GEA_WORKSPACE'] = self.ws.path
            env['GEA_JOB_ID'] = job_id
            pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env['PYTHONPATH'] = pkg_root + os.pathsep + env.get('PYTHONPATH', '')
            with open(job['log'], 'w', encoding='utf-8') as log:
                log.write(f"$ gea {' '.join(job['args'])}\n")
                log.flush()
                try:
                    proc = subprocess.Popen([self.python, '-m', 'gea'] + job['args'], cwd=self.ws.path, env=env,
                                            stdout=log, stderr=subprocess.STDOUT, text=True)
                    with self._lock:
                        self._procs[job_id] = proc
                    rc = proc.wait()
                except Exception as e:                       # the interpreter itself failed to start
                    log.write(f'job runner error: {e}\n')
                    rc = -1
                finally:
                    with self._lock:
                        self._procs.pop(job_id, None)
            job = self._read(job_id)
            if job['status'] != 'CANCELLED':
                job['status'] = 'DONE' if rc == 0 else 'FAILED'
            job['returncode'] = rc
            job['finished_utc'] = _iso(_now())
            self._write(job)
            self.ws.audit('system', 'job.finish', {'id': job_id, 'status': job['status'], 'returncode': rc})
            if self.on_finish:
                try:
                    self.on_finish(job)
                except Exception:
                    pass


class Scheduler:
    """Recurring jobs, persisted in jobs/schedule.json, checked every `tick_s`."""

    def __init__(self, runner: JobRunner, tick_s: float = 30.0):
        self.runner = runner
        self.ws = runner.ws
        self.path = os.path.join(self.ws.jobs_dir, 'schedule.json')
        self.tick_s = tick_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if not os.path.isfile(self.path):
            self._save({'entries': []})

    def _load(self) -> dict:
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def _save(self, d: dict) -> None:
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)

    def entries(self) -> List[dict]:
        return self._load()['entries']

    def add(self, name: str, args: List[str], actor: str, every_s: Optional[float] = None, daily_at: Optional[str] = None,
            monthly_day: Optional[int] = None, enabled: bool = True) -> dict:
        if not (every_s or daily_at):
            raise ValueError('a schedule entry needs every_s or daily_at (optionally with monthly_day)')
        d = self._load()
        d['entries'] = [e for e in d['entries'] if e['name'] != name]
        e = {'name': name, 'args': list(args), 'every_s': every_s, 'daily_at': daily_at, 'monthly_day': monthly_day,
             'enabled': enabled, 'last_run_utc': None, 'last_job': None, 'created_by': actor}
        d['entries'].append(e)
        self._save(d)
        self.ws.audit(actor, 'schedule.add', {'name': name, 'every_s': every_s, 'daily_at': daily_at, 'monthly_day': monthly_day})
        return e

    def remove(self, name: str, actor: str) -> None:
        d = self._load()
        d['entries'] = [e for e in d['entries'] if e['name'] != name]
        self._save(d)
        self.ws.audit(actor, 'schedule.remove', {'name': name})

    def set_enabled(self, name: str, enabled: bool, actor: str) -> None:
        d = self._load()
        for e in d['entries']:
            if e['name'] == name:
                e['enabled'] = enabled
        self._save(d)
        self.ws.audit(actor, 'schedule.enable' if enabled else 'schedule.disable', {'name': name})

    @staticmethod
    def due(e: dict, now: datetime) -> bool:
        if not e.get('enabled', True):
            return False
        last = datetime.strptime(e['last_run_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) if e.get('last_run_utc') else None
        if e.get('every_s'):
            return last is None or (now - last).total_seconds() >= float(e['every_s'])
        hh, mm = [int(x) for x in e['daily_at'].split(':')]
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if e.get('monthly_day') and now.day != int(e['monthly_day']):
            return False
        if now < target:
            return False
        return last is None or last < target

    def run_due(self, now: Optional[datetime] = None) -> List[str]:
        now = now or _now()
        d = self._load()
        started = []
        for e in d['entries']:
            if self.due(e, now):
                jid = self.runner.submit(e['args'], actor='scheduler', label=f"scheduled: {e['name']}", kind=e['args'][0] if e['args'] else '')
                e['last_run_utc'] = _iso(now)
                e['last_job'] = jid
                started.append(jid)
        if started:
            self._save(d)
        return started

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='gea-scheduler', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception:
                pass
            self._stop.wait(self.tick_s)
