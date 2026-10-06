# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""supervisor - the control panel owns its own stopping and starting, and a power cut is a thing it recovers from.

Until now the service had one way to end: the window closed, or Ctrl+C, and whatever the shell underneath
happened to be was what the operator was left looking at. On a Windows box where the console was started
from a Python profile, that was a bare Python prompt - a working program replaced by `>>>`, which tells an
operator nothing and invites them to type into an interpreter that is not the program. A control panel that
cannot stop and start itself cleanly is not a control panel.

So there is a contract here, and it is only three things:

- the service decides how it ends and says so in its exit code: 0 means stopped on purpose, 86 means
  restart me;
- the launcher reads that code. 86 and it runs the service again. Anything else and it hands the window to
  a PowerShell prompt that names the program and says how to start it. It never falls back to whatever
  shell happened to be underneath;
- every start, every stop, every restart and every recovery is one appended line in `records/runlog.jsonl`,
  written and flushed to the disk before the thing it describes is attempted. A log written after the event
  is no use to a machine that lost power during it.

That last point is what makes a power cut recoverable. A run that logged a start and never logged a stop
was killed, and the next start can see that, say so, and do something about it: bring the live patches back
up first, because the incoming stream is the thing that cannot be recovered later, and then reach back and
rebuild every recorded dataset whose source has grown since the report that describes it. The stream comes
first and the catching-up happens behind it.

What this module will not call a measurement:

- a clean stop from a run that logged no stop: it was killed, and the difference matters;
- the gap in a live stream as recoverable - what did not arrive did not arrive, and the records say where
  the gap is rather than interpolating across it;
- a catch-up as a refresh of anything whose source has not changed: it rebuilds what is behind its source
  and leaves the rest alone, so what the catch-up touched is a short list and not everything.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

EXIT_CLEAN = 0
EXIT_RESTART = 86
RUNLOG = os.path.join('records', 'runlog.jsonl')
# The launcher is a batch file and a shell script. It cannot read the workspace manifest, so the one setting
# it needs - may I start the program again by myself after it died without being asked to - is written where
# a launcher can read it: one character in one file. The manifest stays the record of the operator's choice;
# this file is how that choice reaches the thing that acts on it.
AUTO_FLAG = os.path.join('records', 'auto_restart.flag')
# A program that crashes while starting would otherwise be restarted for ever. The launcher counts
# consecutive failures and stops after this many, so the operator gets a prompt and an error instead of a
# window that flickers all night.
AUTO_MAX = 5


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def runlog_path(ws_path: str) -> str:
    return os.path.join(ws_path, RUNLOG)


def auto_flag_path(ws_path: str) -> str:
    return os.path.join(ws_path, AUTO_FLAG)


def set_auto_restart(ws_path: str, on: bool) -> bool:
    """Write the operator's choice where the launcher can read it."""
    f = auto_flag_path(ws_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write('1\n' if on else '0\n')
        fh.flush()
        os.fsync(fh.fileno())
    return bool(on)


def auto_restart(ws_path: str) -> bool:
    """What the launcher will read. Absent or unreadable means no: a program does not start itself again on
    the strength of a file nobody can be sure about."""
    try:
        with open(auto_flag_path(ws_path), 'r', encoding='utf-8') as fh:
            return fh.read().strip() == '1'
    except Exception:
        return False


def log(ws_path: str, event: str, **fields: Any) -> dict:
    """One appended line, flushed to the disk before this call returns.

    Not buffered, not written at exit: a line that is still in a buffer when the power goes is a line that
    was never written, and the whole point of this log is to be readable by the run that comes after a
    machine that lost power."""
    row = {'utc': _utc(), 'event': event, 'pid': os.getpid(), **fields}
    p = runlog_path(ws_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, default=str) + '\n')
        f.flush()
        os.fsync(f.fileno())
    return row


def tail(ws_path: str, n: int = 100) -> List[dict]:
    p = runlog_path(ws_path)
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out[-n:]


def previous_run(ws_path: str, exclude_pid: Optional[int] = None) -> dict:
    """What the run before this one did, and whether it ended on purpose.

    A start with no stop after it is a run that was killed - a power cut, a reboot, a taskkill. The
    difference between that and a clean stop is the difference between 'carry on' and 'find out what was
    lost', so it is the first thing a new run asks."""
    rows = tail(ws_path, 5000)
    starts = [i for i, r in enumerate(rows) if r.get('event') == 'start']
    # asked from inside a running service, the LAST start is this run; the previous one is the one before it.
    # Only that one start is dropped, by position - process ids come round again and matching on the number
    # alone would throw away an older run that happens to share it.
    if exclude_pid is not None and starts and rows[starts[-1]].get('pid') == exclude_pid:
        starts = starts[:-1]
    if not starts:
        return {'status': 'FIRST_RUN', 'detail': 'no previous run is recorded for this workspace'}
    i = starts[-1]
    after = rows[i + 1:]
    ended = next((r for r in after if r.get('event') in ('stop', 'restart')), None)
    out = {'started_utc': rows[i].get('utc'), 'pid': rows[i].get('pid'), 'version': rows[i].get('version')}
    if ended:
        out.update({'status': 'CLEAN' if ended['event'] == 'stop' else 'RESTARTED', 'ended_utc': ended.get('utc'),
                    'reason': ended.get('reason'), 'actor': ended.get('actor'),
                    'detail': f"the previous run ended on purpose at {ended.get('utc')}"})
    else:
        last = after[-1] if after else rows[i]
        out.update({'status': 'UNCLEAN', 'last_seen_utc': last.get('utc'), 'last_event': last.get('event'),
                    'detail': f"the previous run started at {rows[i].get('utc')} and never logged a stop; the last thing it wrote was "
                              f"'{last.get('event')}' at {last.get('utc')}. It was killed - a power cut, a reboot or a kill - so anything the "
                              'live stream carried after that was not recorded by this program'})
    return out


def behind(ws) -> List[dict]:
    """Every recorded dataset whose source has grown since the report that describes it.

    This is the list a catch-up works through. It comes from the workspace's own staleness table, so it is
    the same answer the Audit page gives and not a second opinion."""
    try:
        rows = ws.report_ages()
    except Exception:
        return []
    return [r for r in rows if r.get('status') == 'stale']


def catch_up(ws, actor: str = 'supervisor', only_if_behind: bool = True) -> dict:
    """Rebuild what is behind its source, and nothing else.

    After a restart the live files have usually grown while nothing was reading them - the stream was
    recorded but the reports that describe it were written before those records existed. This reaches back
    over exactly those and leaves everything else alone."""
    stale = behind(ws)
    out = {'protocol': 'supervisor.catch_up/1', 'behind': [r.get('what') or r.get('id') for r in stale], 'n_behind': len(stale),
           'basis': "the workspace's own staleness table: a report older than the source it was made from",
           'not_a_measurement': ['a refresh of anything whose source has not changed',
                                 'a gap in the stream as recovered: what did not arrive did not arrive']}
    if only_if_behind and not stale:
        out.update({'status': 'NOTHING_BEHIND', 'detail': 'every report is current with its source; nothing to catch up'})
        return out
    log(ws.path, 'catch_up.start', behind=out['behind'], actor=actor)
    t0 = time.time()
    try:
        r = ws.refresh_all(actor=actor)
    except Exception as e:                       # a catch-up must never stop the stream that is running beside it
        out.update({'status': 'FAILED', 'detail': f'the catch-up failed: {e}; the live stream was not touched by it'})
        log(ws.path, 'catch_up.failed', error=str(e), actor=actor)
        return out
    # read what the refresh reported rather than requiring it: a catch-up that threw a KeyError because the
    # refresh grew a key would stop the recovery over a counter nobody reads
    r = r if isinstance(r, dict) else {}
    errs = list(r.get('errors') or [])
    n = {k: int(r.get(k) or 0) for k in ('wells', 'seismic', 'tracks')}
    out.update({'status': 'CAUGHT_UP' if not errs else 'PARTIAL', **n, 'errors': errs, 'seconds': round(time.time() - t0, 2),
                'detail': (f"{n['wells']} well report(s), {n['seismic']} seismic station(s) and {n['tracks']} track(s) rebuilt from their sources in "
                           f"{time.time() - t0:.0f} s" + (f"; {len(errs)} error(s), listed" if errs else ''))})
    log(ws.path, 'catch_up.done', **{k: out[k] for k in ('status', 'wells', 'seismic', 'tracks', 'seconds')}, errors=len(errs), actor=actor)
    return out


def resume(ws, patches, actor: str = 'supervisor', do_catch_up: bool = True) -> dict:
    """The order a restart happens in, and it is not negotiable: the stream first.

    A live patch that is not running is losing records that nothing can recover. A report that is behind its
    source can be rebuilt at any time from records already on the disk. So the patches come up first and
    return straight away, and the catching-up runs behind them on its own thread - the incoming stream is
    never waiting on a rebuild."""
    prev = previous_run(ws.path)
    started: List[str] = []
    running: List[str] = []
    err = None
    try:
        started = patches.start_enabled()          # idempotent: one already up is left alone, not restarted
        running = [s_['name'] for s_ in patches.states() if s_.get('status') not in ('STOPPED', None)]
    except Exception as e:
        err = str(e)
    log(ws.path, 'resume', previous=prev.get('status'), patches_started=started, patches_running=running, error=err, actor=actor)
    out = {'protocol': 'supervisor.resume/1', 'previous_run': prev, 'patches_started': started, 'patches_running': running,
           'patch_error': err, 'catch_up': None,
           'basis': 'the live patches are started before anything else and the catch-up runs behind them on its own thread',
           'not_a_measurement': ['records the stream carried while nothing was running: they were not recorded and are not recovered']}
    if do_catch_up:
        res: Dict[str, Any] = {}

        def _work():
            try:
                res.update(catch_up(ws, actor))
            except Exception as e:
                res.update({'status': 'FAILED', 'detail': str(e)})

        th = threading.Thread(target=_work, name='gea-catch-up', daemon=True)
        th.start()
        out['catch_up'] = {'status': 'RUNNING', 'thread': th.name,
                           'detail': 'the catch-up is running behind the stream; the control panel shows it when it finishes'}
        out['_thread'] = th
        out['_result'] = res
    return out


def state(ws_path: str, started_utc: Optional[str] = None, version: str = '') -> dict:
    """What the control panel shows about its own running: when this run started, what the run before it
    did, and what the launcher will do when this one ends."""
    prev = previous_run(ws_path, exclude_pid=os.getpid())
    return {'protocol': 'supervisor.state/1', 'pid': os.getpid(), 'started_utc': started_utc, 'version': version,
            'runlog': runlog_path(ws_path), 'previous_run': prev,
            'exit_codes': {'clean': EXIT_CLEAN, 'restart': EXIT_RESTART},
            'launcher_contract': ('the launcher runs this service again when it exits with %d, and hands the window to a PowerShell prompt on any '
                                  'other code. It never drops to a Python prompt' % EXIT_RESTART),
            'auto_restart_flag': {'path': auto_flag_path(ws_path), 'enabled': auto_restart(ws_path), 'max_consecutive': AUTO_MAX},
            'not_a_measurement': ['a clean stop from a run that logged no stop',
                                  'anything about a window this program did not open',
                                  'an auto-restart setting the launcher has not read: what the flag file says is what will happen']}
