"""The control room: one board for a wall screen, and the shift handover.

The board is one tile per well, read from what the workspace already holds - the alarm
report, the gauge-drift evaluation, the drilling record and its chart cache, the live
patches - and judged into one of four states, each with its own shape and word so it
reads without colour:

    SERIOUS   an unacknowledged P1/P2 alarm; a gauge reading off with no explanation;
              a live feed down, or silent for five times its stale limit (15 min at least)
    WATCH     any other active or shelved alarm; a gauge drift that has an explanation;
              a live feed degraded, stopped, or past its stale limit
    GOOD      reports exist and nothing above applies
    NO DATA   no reports for the well and no live sample

A recorded well (a file, a catalogue entry) is never called stale: its tile says when the
record ends. Only a live feed has an age, and that age is measured, not assumed.

The wall itself refreshes every REFRESH_S seconds; the page says how old its last answer
is and puts a full-width notice up when the service stops answering, so old numbers are
never shown as current.

The shift handover is a note the outgoing operator writes - what is open, what is being
watched, what was done - kept with the board's counts at the moment it was written. The
incoming operator acknowledges it; the author cannot acknowledge their own note. Both
are append-only lines in handover/notes.jsonl and both go to the audit log.

A wall pass lets a screen show the board without a person's account: an administrator
issues it (the code is shown once, only its hash is kept), the screen enters it once,
and it reads the board and the latest handover note and nothing else. It expires, and
it can be revoked at any time.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

REFRESH_S = 30
STATES = ('SERIOUS', 'WATCH', 'GOOD', 'NO_DATA')
WORD = {'SERIOUS': 'Serious', 'WATCH': 'Watch', 'GOOD': 'Good', 'NO_DATA': 'No data'}
SHAPE = {'SERIOUS': 'octagon', 'WATCH': 'triangle', 'GOOD': 'circle', 'NO_DATA': 'ring'}
SERIOUS_PRIORITIES = ('P1', 'P2')
LIVE_SERIOUS_MULT = 5.0
LIVE_SERIOUS_MIN_S = 900.0
DRILLING_RECENT_S = 1800.0          # drilling within this long of the record's end counts as "drilling now"
ROP_WINDOW_S = 600.0                # the latest ROP: the mean over the last ten minutes of drilling
NOTE_FIELDS = ('open', 'watching', 'done')
NOTE_MAX = 2000
PASS_DAYS_DEFAULT = 90
PASS_DAYS_MAX = 365
HANDOVER_FILE = 'notes.jsonl'
PASS_FILE = 'passes.json'

_LOCK = threading.Lock()
_SEV = {'UNEXPLAINED_TREND': 5, 'UNEXPLAINED_OFFSET': 5, 'CALIBRATION_OFFSET': 4, 'DRIFT_CONSISTENT': 3,
        'TRANSIENTS': 2, 'INSUFFICIENT_DATA': 1, 'IN_FAMILY': 0}


def _utc(dt: Optional[datetime] = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(iso) -> Optional[datetime]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _load(p: str):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _reports_dir(ws, well_id: str) -> str:
    return os.path.join(ws.reports_dir, 'wells', well_id)


def _human(code: str) -> str:
    return str(code or '').replace('_', ' ').lower()


def _ago(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 90:
        return f'{s} s'
    if s < 5400:
        return f'{s // 60} min'
    if s < 172800:
        return f'{s / 3600:.1f} h'
    return f'{s / 86400:.1f} days'


# -- live feeds ------------------------------------------------------------------------------------
def stored_patch_states(ws) -> List[dict]:
    """The patches' last written state, for a reader with no running service (the CLI): every one is STOPPED."""
    out = []
    for w in ws.wells():
        d = os.path.join(ws.path, 'wells', w['id'], 'records', 'live')
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.startswith('patch_') and fn.endswith('.state.json'):
                s = _load(os.path.join(d, fn)) or {}
                s['well_id'] = s.get('well_id') or w['id']
                s['status'] = 'STOPPED'
                out.append(s)
    return out


def _live(states: List[dict], now: datetime) -> Optional[dict]:
    if not states:
        return None
    lasts = [d for d in (_parse(s.get('last_sample_utc')) for s in states) if d]
    last = max(lasts) if lasts else None
    stale = min(float((s.get('patch') or {}).get('stale_after_s') or s.get('stale_after_s') or 120.0) for s in states)
    statuses = [str(s.get('status') or 'STOPPED') for s in states]
    return {'patches': [str(s.get('name') or (s.get('patch') or {}).get('name') or '') for s in states], 'statuses': statuses,
            'last_sample_utc': _utc(last) if last else None, 'age_s': (now - last).total_seconds() if last else None,
            'stale_after_s': stale, 'serious_after_s': max(LIVE_SERIOUS_MIN_S, LIVE_SERIOUS_MULT * stale)}


# -- the drilling tile: from the record and its chart cache ----------------------------------------
def _drilling(ws, well_id: str, rec: dict) -> dict:
    out = {'td_md_ft': rec.get('total_depth_md_ft'), 'feet_drilled': rec.get('feet_drilled'), 'on_bottom_hours': rec.get('on_bottom_hours'),
           'record_end_utc': rec.get('end'), 'bottom_ft': None, 'bit_ft': None, 'rop_ft_h': None, 'drilling_now': None, 'off_bottom_since_utc': None,
           'cache': False}
    try:
        from . import series_cache as SCH
        import numpy as np
        c = SCH.load(SCH.cache_dir(ws, well_id), SCH.source_path(ws, well_id))
    except Exception:
        c = None
    if c is None:
        out['note'] = 'the chart cache is not built yet; a refresh builds it and adds the bit, bottom and ROP at the end of the record'
        return out
    out['cache'] = True
    idx = np.asarray(c.index, dtype=np.float64)
    n = len(idx)
    if not n:
        return out
    t_end = float(idx[-1])

    def last_finite(a) -> Optional[float]:
        if a is None:
            return None
        for i0 in range(n, 0, -65536):
            seg = np.asarray(a[max(0, i0 - 65536):i0], dtype=np.float64)
            ok = np.flatnonzero(~np.isnan(seg))
            if len(ok):
                return float(seg[ok[-1]])
        return None
    out['bottom_ft'] = last_finite(c.extra('bottom'))
    names = {x['name'] for x in c.meta.get('channels', []) if x.get('present')}
    if 'Bit Depth' in names:
        out['bit_ft'] = last_finite(c.channel('Bit Depth'))
    dr = c.extra('drilling')
    if dr is not None:
        i_recent = int(np.searchsorted(idx, t_end - DRILLING_RECENT_S, 'left'))
        tail = np.asarray(dr[i_recent:], dtype=bool)
        last_dr = None
        for i0 in range(n, 0, -65536):
            seg = np.asarray(dr[max(0, i0 - 65536):i0], dtype=bool)
            k = np.flatnonzero(seg)
            if len(k):
                last_dr = max(0, i0 - 65536) + int(k[-1])
                break
        out['drilling_now'] = bool(tail.any())
        if last_dr is not None and not out['drilling_now']:
            st = _parse(c.meta.get('start_time'))
            if st:
                out['off_bottom_since_utc'] = _utc(st + timedelta(seconds=float(idx[last_dr])))
        if last_dr is not None and out['drilling_now'] and 'Rate Of Penetration' in names:
            j0 = int(np.searchsorted(idx, float(idx[last_dr]) - ROP_WINDOW_S, 'left'))
            r = np.asarray(c.channel('Rate Of Penetration')[j0:last_dr + 1], dtype=np.float64)
            m = np.asarray(dr[j0:last_dr + 1], dtype=bool) & ~np.isnan(r)
            if m.any():
                out['rop_ft_h'] = round(float(r[m].mean()), 1)
    return out


# -- one tile --------------------------------------------------------------------------------------
def _tile(ws, w: dict, dash: Optional[dict], live_states: List[dict], now: datetime) -> dict:
    wid = w['id']
    d = _reports_dir(ws, wid)
    has_reports = os.path.isdir(d) and any(f.endswith('.json') and f != 'well.json' for f in os.listdir(d))
    serious: List[str] = []
    watch: List[str] = []
    t = {'well_id': wid, 'display': w.get('display') or wid, 'kind': w.get('kind'), 'alarms': None, 'drift': None, 'live': None,
         'drilling': None, 'recorded': None}
    # alarms
    al = _load(os.path.join(d, 'alarm_event_report.json'))
    if al:
        act = al.get('active', []) or []
        unacked = [a for a in act if a.get('state') == 'ACTIVE_UNACKED']
        shelved = al.get('shelved', []) or []
        top = min((str(a.get('priority') or 'P9') for a in act), default=None)
        t['alarms'] = {'active': len(act), 'unacked': len(unacked), 'shelved': len(shelved), 'top_priority': top,
                       'top': [{'alarm_id': a.get('alarm_id'), 'priority': a.get('priority'), 'state': a.get('state')}
                               for a in sorted(act, key=lambda a: (str(a.get('priority') or 'P9'), a.get('state') != 'ACTIVE_UNACKED'))[:3]]}
        hi = [a for a in unacked if a.get('priority') in SERIOUS_PRIORITIES]
        if hi:
            serious.append(f"{len(hi)} unacknowledged {'/'.join(sorted({a['priority'] for a in hi}))} alarm(s)")
        rest = len(act) - len(hi)
        if rest:
            watch.append(f"{rest} {'other ' if hi else ''}active alarm(s)" + (f', {len(unacked) - len(hi)} not acknowledged' if len(unacked) - len(hi) else ''))
        if shelved:
            watch.append(f'{len(shelved)} shelved alarm(s)')
    # gauge drift
    dr = (dash or {}).get('drift')
    if dr:
        worst = dr.get('worst') or '-'
        t['drift'] = {'detected': bool(dr.get('detected')), 'worst': worst, 'stations': dr.get('n_stations'), 'bias_max_psi': dr.get('bias_max')}
        sv = _SEV.get(worst, 0)
        if sv >= 5:
            serious.append(f'a gauge reading off with no explanation ({_human(worst)})')
        elif sv >= 3:
            watch.append(f'gauge drift with an explanation ({_human(worst)})')
    # drilling record
    rec = _load(os.path.join(d, 'drilling_record.json'))
    if rec:
        t['drilling'] = _drilling(ws, wid, rec)
    # live feed, or the record's end
    lv = _live(live_states, now)
    if lv:
        t['live'] = lv
        down = [s for s in lv['statuses'] if s == 'DOWN']
        if lv['age_s'] is None:
            (serious if down else watch).append('the live feed has delivered no sample yet' + (' and is down' if down else ''))
        elif lv['age_s'] > lv['serious_after_s']:
            serious.append(f"no live sample for {_ago(lv['age_s'])}")
        elif lv['age_s'] > lv['stale_after_s']:
            watch.append(f"last live sample {_ago(lv['age_s'])} ago, past the {_ago(lv['stale_after_s'])} stale limit")
        if down and lv['age_s'] is not None:
            serious.append(f'{len(down)} live patch(es) down')
        bad = [s for s in lv['statuses'] if s in ('DEGRADED', 'STOPPED', 'CONNECTING')]
        if bad:
            watch.append(', '.join(sorted({s.lower() for s in bad})) + ' live patch(es)')
    else:
        end = (t['drilling'] or {}).get('record_end_utc')
        t['recorded'] = {'record_end_utc': end, 'note': 'a recorded well: its tile describes the end of the record, not the present'}
    if serious:
        state = 'SERIOUS'
    elif watch:
        state = 'WATCH'
    elif has_reports or (lv and lv['age_s'] is not None):
        state = 'GOOD'
    else:
        state = 'NO_DATA'
    reasons = serious + watch
    if state == 'GOOD':
        reasons = ['nothing needs attention']
    elif state == 'NO_DATA':
        reasons = ['no reports yet and no live sample' + ('; refresh the reports' if w.get('kind') != 'live' else '; start its patch')]
    t.update({'state': state, 'word': WORD[state], 'shape': SHAPE[state], 'reasons': reasons})
    return t


def _recent_events(ws, wells: List[dict], limit: int = 6) -> List[dict]:
    ev = []
    for w in wells:
        p = os.path.join(_reports_dir(ws, w['id']), 'alarm_events.jsonl')
        if not os.path.isfile(p):
            continue
        try:
            with open(p, 'rb') as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                lines = f.read().decode('utf-8', 'replace').splitlines()[-limit * 3:]
        except OSError:
            continue
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get('alarm_id'):
                ev.append({'well_id': w['id'], 'display': w.get('display') or w['id'], 'utc': e.get('timestamp_utc'), 'alarm_id': e.get('alarm_id'),
                           'priority': e.get('priority'), 'event': e.get('event'), 'actor': e.get('operator') or e.get('actor')})
    ev.sort(key=lambda e: str(e.get('utc') or ''), reverse=True)
    return ev[:limit]


def board(ws, patch_states: Optional[List[dict]] = None, now: Optional[datetime] = None) -> dict:
    """The wall board: a tile per well, worst first, with the counts and the latest handover note."""
    now = now or datetime.now(timezone.utc)
    states = stored_patch_states(ws) if patch_states is None else patch_states
    dash = _load(os.path.join(ws.reports_dir, 'dashboard.json')) or {}
    by_slug = {x.get('slug'): x for x in dash.get('wells', [])}
    wells = ws.wells()
    tiles = []
    for w in wells:
        mine = [s for s in states if (s.get('well_id') or (s.get('patch') or {}).get('well_id')) == w['id']]
        tiles.append(_tile(ws, w, by_slug.get(w['id']), mine, now))
    order = {s: i for i, s in enumerate(STATES)}
    tiles.sort(key=lambda t: (order[t['state']], t['display'].lower()))
    counts = {s: sum(1 for t in tiles if t['state'] == s) for s in STATES}
    site = ws.manifest.get('site', {}) or {}
    return {'generated_utc': _utc(now), 'refresh_s': REFRESH_S, 'site': site.get('display_name') or ws.manifest.get('name'),
            'counts': counts, 'tiles': tiles, 'recent_events': _recent_events(ws, wells),
            'reports_generated_utc': dash.get('generated_utc'), 'handover': latest(ws),
            'rule': 'SERIOUS: an unacknowledged P1/P2 alarm, a gauge off with no explanation, a live feed down or silent five times its stale limit. '
                    'WATCH: any other active or shelved alarm, an explained drift, a live feed past its stale limit. '
                    'A recorded well is described at the end of its record and is never called stale.'}


def text(b: dict) -> str:
    c = b['counts']
    L = [f"{b['site']} - control room at {b['generated_utc']}",
         f"  {c['SERIOUS']} serious · {c['WATCH']} watch · {c['GOOD']} good · {c['NO_DATA']} no data"]
    mark = {'SERIOUS': '[X]', 'WATCH': '[!]', 'GOOD': '[ok]', 'NO_DATA': '[--]'}
    for t in b['tiles']:
        L.append(f"\n{mark[t['state']]} {t['word'].upper():8} {t['display']}")
        for r in t['reasons']:
            L.append(f'     - {r}')
        dr = t.get('drilling')
        if dr:
            parts = []
            if dr.get('td_md_ft') is not None:
                parts.append(f"TD {dr['td_md_ft']:,.0f} ft")
            if dr.get('bottom_ft') is not None:
                parts.append(f"bottom {dr['bottom_ft']:,.0f} ft")
            if dr.get('bit_ft') is not None:
                parts.append(f"bit {dr['bit_ft']:,.0f} ft")
            if dr.get('rop_ft_h') is not None:
                parts.append(f"ROP {dr['rop_ft_h']:,.1f} ft/h")
            elif dr.get('off_bottom_since_utc'):
                parts.append(f"off bottom since {dr['off_bottom_since_utc']}")
            if dr.get('on_bottom_hours') is not None:
                parts.append(f"{dr['on_bottom_hours']:,.1f} h on bottom")
            L.append('     ' + ' · '.join(parts))
            if dr.get('note'):
                L.append(f"     ({dr['note']})")
        if t.get('live') and t['live'].get('age_s') is not None:
            L.append(f"     latest live sample {_ago(t['live']['age_s'])} ago")
        elif t.get('recorded') and t['recorded'].get('record_end_utc'):
            L.append(f"     recorded; the record ends {t['recorded']['record_end_utc']}")
    h = b.get('handover')
    if h:
        L.append(f"\nHandover {h['id']} by {h['by']} at {h['utc']}" + (f"; acknowledged by {h['ack']['by']} at {h['ack']['utc']}" if h.get('ack') else '; NOT YET ACKNOWLEDGED'))
        for k in NOTE_FIELDS:
            if h.get(k):
                L.append(f'  {k}: {h[k]}')
    return '\n'.join(L)


# -- the shift handover ----------------------------------------------------------------------------
def _hfile(ws, make: bool = False) -> str:
    """The notes file; its folder is made only by a write - reading the board changes nothing in the workspace."""
    return os.path.join(ws.dir('handover') if make else os.path.join(ws.path, 'handover'), HANDOVER_FILE)


def _hlines(ws) -> List[dict]:
    p = _hfile(ws)
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for ln in f:
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def notes(ws, limit: int = 20) -> List[dict]:
    """The handover notes, newest first, each with its acknowledgement when it has one."""
    lines = _hlines(ws)
    acks = {x['note_id']: x for x in lines if x.get('kind') == 'ack'}
    out = []
    for x in lines:
        if x.get('kind') == 'note':
            a = acks.get(x['id'])
            out.append({**x, 'ack': ({k: a[k] for k in ('by', 'utc', 'comment')} if a else None)})
    out.reverse()
    return out[:limit] if limit else out


def latest(ws) -> Optional[dict]:
    n = notes(ws, 1)
    return n[0] if n else None


def write_note(ws, actor: str, open_items: str = '', watching: str = '', done: str = '', board_counts: Optional[dict] = None) -> dict:
    vals = {'open': (open_items or '').strip(), 'watching': (watching or '').strip(), 'done': (done or '').strip()}
    if not any(vals.values()):
        raise ValueError('a handover note needs something in it: what is open, what is being watched, or what was done')
    for k, v in vals.items():
        if len(v) > NOTE_MAX:
            raise ValueError(f'the "{k}" part is {len(v)} characters; keep each part under {NOTE_MAX}')
    if not (actor or '').strip():
        raise ValueError('a handover note needs its author')
    now = datetime.now(timezone.utc)
    rec = {'kind': 'note', 'id': f"H-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}", 'by': actor, 'utc': _utc(now), **vals,
           'board': board_counts or {}}
    with _LOCK:
        with open(_hfile(ws, True), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, sort_keys=True) + '\n')
    ws.audit(actor, 'handover.write', {'id': rec['id'], 'open': vals['open'][:200], 'watching': vals['watching'][:200], 'done': vals['done'][:200],
                                       'board': rec['board']})
    return {**rec, 'ack': None}


def ack_note(ws, note_id: str, actor: str, comment: str = '') -> dict:
    with _LOCK:
        lines = _hlines(ws)
        note = next((x for x in lines if x.get('kind') == 'note' and x.get('id') == note_id), None)
        if note is None:
            raise KeyError(f'no handover note {note_id}')
        prev = next((x for x in lines if x.get('kind') == 'ack' and x.get('note_id') == note_id), None)
        if prev:
            raise ValueError(f"handover {note_id} was acknowledged by {prev['by']} at {prev['utc']}")
        if actor == note['by']:
            raise PermissionError("the note's author cannot acknowledge their own handover; the incoming operator does")
        rec = {'kind': 'ack', 'note_id': note_id, 'by': actor, 'utc': _utc(), 'comment': (comment or '').strip()[:NOTE_MAX]}
        with open(_hfile(ws, True), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, sort_keys=True) + '\n')
    ws.audit(actor, 'handover.ack', {'id': note_id, 'author': note['by'], 'comment': rec['comment'][:200]})
    return rec


# -- wall passes -----------------------------------------------------------------------------------
def _pfile(ws, make: bool = False) -> str:
    return os.path.join(ws.dir('wall') if make else os.path.join(ws.path, 'wall'), PASS_FILE)


def _pread(ws) -> dict:
    return _load(_pfile(ws)) or {'schema': 1, 'passes': []}


def _pwrite(ws, d: dict) -> None:
    p = _pfile(ws, True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(p + '.tmp', p)


def _h(code: str) -> str:
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def pass_create(ws, actor: str, label: str, days: int = PASS_DAYS_DEFAULT) -> dict:
    label = (label or '').strip()
    if not label:
        raise ValueError('name the screen the pass is for, e.g. "doghouse wall"')
    days = int(days or PASS_DAYS_DEFAULT)
    if not 1 <= days <= PASS_DAYS_MAX:
        raise ValueError(f'a wall pass lasts 1 to {PASS_DAYS_MAX} days')
    code = secrets.token_urlsafe(18)
    now = datetime.now(timezone.utc)
    rec = {'id': f'W{secrets.token_hex(3)}', 'label': label[:80], 'created_by': actor, 'created_utc': _utc(now),
           'expires_utc': _utc(now + timedelta(days=days)), 'hash': _h(code), 'revoked_utc': None, 'revoked_by': None}
    with _LOCK:
        d = _pread(ws)
        d['passes'].append(rec)
        _pwrite(ws, d)
    ws.audit(actor, 'wall.pass.create', {'id': rec['id'], 'label': rec['label'], 'expires_utc': rec['expires_utc']})
    return {**{k: v for k, v in rec.items() if k != 'hash'}, 'code': f"{rec['id']}.{code}",
            'note': 'the code is shown this once; only its hash is kept. Type it into the wall screen once.'}


def pass_list(ws) -> List[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for p in _pread(ws)['passes']:
        st = 'REVOKED' if p.get('revoked_utc') else ('EXPIRED' if (_parse(p.get('expires_utc')) or now) <= now else 'ACTIVE')
        out.append({**{k: v for k, v in p.items() if k != 'hash'}, 'status': st})
    return out


def pass_revoke(ws, actor: str, pass_id: str) -> dict:
    with _LOCK:
        d = _pread(ws)
        p = next((x for x in d['passes'] if x['id'] == pass_id), None)
        if p is None:
            raise KeyError(f'no wall pass {pass_id}')
        if not p.get('revoked_utc'):
            p['revoked_utc'], p['revoked_by'] = _utc(), actor
            _pwrite(ws, d)
    ws.audit(actor, 'wall.pass.revoke', {'id': pass_id, 'label': p['label']})
    return {k: v for k, v in p.items() if k != 'hash'}


def pass_check(ws, token: Optional[str]) -> Optional[dict]:
    """The pass a wall cookie carries (`<id>.<code>`), when it is live; None otherwise."""
    if not token or '.' not in token:
        return None
    pid, _, code = token.partition('.')
    now = datetime.now(timezone.utc)
    for p in _pread(ws)['passes']:
        if p['id'] == pid:
            if p.get('revoked_utc') or (_parse(p.get('expires_utc')) or now) <= now:
                return None
            return {k: v for k, v in p.items() if k != 'hash'} if hmac.compare_digest(p['hash'], _h(code)) else None
    return None
