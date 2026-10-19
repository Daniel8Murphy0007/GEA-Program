"""The live earthquake watch: while a well operates, read the USGS catalogue every few minutes and raise an alert when an
earthquake near it reaches the chosen regulator's protocol level.

The Operations & Seismicity tab reads catalogue files the site attaches. The machine that serves this program can
usually reach the USGS service itself; this module does so on a schedule, from the service's background poller:

- it polls only while an operation is under way (a declared operation that has started and not ended) at a well
  with a surface position - or always, if the site sets it so - every `every_min` minutes (5 by default);
- a poll reads the USGS real-time summary feed of the past day (`all_day.geojson`, all magnitudes, updated every
  minute - what USGS asks automated applications to use) and keeps the earthquakes inside the watch circle (the
  wells' centre, out to the screening distance beyond the farthest well); when the last good poll is older than a
  day, or on the first poll, it asks the FDSN event service for the whole gap instead (from the earliest active
  operation's start, at most 30 days back);
- what it reads is merged into the workspace's live catalogue (`seismicity/live/events.json`, each event's latest
  version: USGS revises magnitudes and locations, and the newest wins), written out as a catalogue the tab reads
  (`seismicity/catalogs/live_usgs.txt`, with the window and circle the watch covered);
- the frame is then computed as the tab computes it, and every earthquake within the association radius during or
  after an operation is an alert (`seismic.event`) once, and every one that reaches a protocol level is an alert
  (`seismic.protocol`) once per level - a revision that raises it to a higher level is a new alert;
- a watch that has not reached the catalogue for three intervals while an operation runs raises
  `seismic.watch_down`, and `seismic.watch_up` when it does again: a silent watch is not a quiet one.

The fetch is plain HTTPS (the standard library), with a 20-second timeout, identified as GEA-Program.
"""
from __future__ import annotations

import json
import math
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

FEED_URL = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson'
QUERY_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query'
DEFAULTS = {'enabled': True, 'mode': 'operations', 'every_min': 5.0, 'feed_url': FEED_URL, 'query_url': QUERY_URL}
MODES = ('operations', 'always')
LIVE_FILE = 'live_usgs.txt'
_LOCK = threading.Lock()


def _utc(now: datetime) -> str:
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'seismicity', *p)


def _load(p: str, default):
    if not os.path.isfile(p):
        return default
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
    return {**DEFAULTS, **_load(_dir(ws, 'watch.json'), {})}


def set_settings(ws, actor: str, enabled=None, mode=None, every_min=None) -> dict:
    s = settings(ws)
    if enabled is not None:
        s['enabled'] = bool(enabled) if not isinstance(enabled, str) else enabled.lower() in ('1', 'true', 'yes', 'on')
    if mode:
        if mode not in MODES:
            raise ValueError(f'the watch runs during {MODES[0]} or {MODES[1]}, not {mode!r}')
        s['mode'] = mode
    if every_min not in (None, ''):
        x = float(every_min)
        if not math.isfinite(x) or x < 1 or x > 1440:
            raise ValueError('the watch polls every 1 to 1,440 minutes')
        s['every_min'] = x
    _store(_dir(ws, 'watch.json'), {k: s[k] for k in ('enabled', 'mode', 'every_min', 'feed_url', 'query_url')})
    ws.audit(actor, 'seismicity.watch', {k: s[k] for k in ('enabled', 'mode', 'every_min')})
    return s


def watch_state(ws) -> dict:
    return _load(_dir(ws, 'live', 'state.json'), {'polls': 0, 'failures': 0, 'alerted': {}, 'events_alerted': []})


def live_events(ws) -> Dict[str, dict]:
    return _load(_dir(ws, 'live', 'events.json'), {})


# -- what is under way, and where to look ----------------------------------------------------------------------------
def active(ws, now: datetime) -> dict:
    """The operations under way now at wells with a position, and the circle that covers them."""
    from . import geodesy as GD
    from . import op_seismicity as OS
    st = OS.settings(ws)
    wells = {}
    for w in ws.wells():
        s = w.get('surface') or {}
        if s.get('lat') is not None:
            p = GD.to_wgs84(s['lat'], s['lon'], 0.0, s.get('datum') or 'WGS84')
            L = OS.lateral_of(ws, w, p['lat'], p['lon'])
            wells[w['id']] = {'id': w['id'], 'display': w.get('display') or w['id'], 'lat': p['lat'], 'lon': p['lon'],
                              'reach_km': (L or {}).get('reach_km') or 0.0}
    naive = now.replace(tzinfo=None)
    ops = []
    for o in OS.operations(ws):
        a = OS._dt(o['start'])
        b = OS._dt(o.get('end'))
        if a and a <= naive and (b is None or b >= naive) and o['well_id'] in wells:
            ops.append(o)
    use = [wells[o['well_id']] for o in ops] or list(wells.values())
    circle = None
    if use:
        la = sum(w['lat'] for w in use) / len(use)
        lo = sum(w['lon'] for w in use) / len(use)
        spread = max(OS.km(la, lo, w['lat'], w['lon']) + w['reach_km'] for w in use)    # the laterals' reach counts
        circle = {'lat': round(la, 5), 'lon': round(lo, 5), 'radius_km': round(spread + float(st['screen_km']), 2)}
    return {'operations': ops, 'wells_placed': len(wells), 'circle': circle,
            'earliest_start': min((o['start'] for o in ops), default=None)}


# -- the fetch -------------------------------------------------------------------------------------------------------
def http_get(url: str, timeout: float = 20.0) -> bytes:
    from . import __version__
    req = urllib.request.Request(url, headers={'User-Agent': f'GEA-Program/{__version__} (earthquake watch)', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _features(doc: dict, circle: dict) -> List[dict]:
    from . import op_seismicity as OS
    out = []
    for f in doc.get('features') or []:
        p = f.get('properties') or {}
        g = (f.get('geometry') or {}).get('coordinates') or []
        if len(g) < 2 or p.get('mag') is None or p.get('time') is None:
            continue
        lon, lat = float(g[0]), float(g[1])
        if OS.km(circle['lat'], circle['lon'], lat, lon) > circle['radius_km']:
            continue
        t = datetime.fromtimestamp(p['time'] / 1000.0, timezone.utc)
        out.append({'event_id': str(f.get('id') or p.get('code')), 'time': t.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t.microsecond // 1000:03d}',
                    'lat': lat, 'lon': lon, 'depth_km': float(g[2]) if len(g) > 2 and g[2] is not None else None,
                    'magnitude': float(p['mag']), 'mag_type': p.get('magType') or '', 'contributor': p.get('net') or '',
                    'place': p.get('place') or '', 'status': p.get('status') or '', 'type': p.get('type') or '',
                    'updated': p.get('updated')})
    return out


def _write_catalog(ws, events: Dict[str, dict], start: str, end: str, circle: dict) -> None:
    lines = ['#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName']
    for e in sorted(events.values(), key=lambda e: e['time']):
        if e.get('type') not in ('', 'earthquake'):
            continue
        d = '' if e.get('depth_km') is None else f"{e['depth_km']:g}"
        lines.append('|'.join([e['event_id'], e['time'], f"{e['lat']:g}", f"{e['lon']:g}", d, e['contributor'], e['contributor'], e['contributor'],
                               e['event_id'], e['mag_type'], f"{e['magnitude']:g}", e['contributor'], e['place'].replace('|', '/')]))
    p = _dir(ws, 'catalogs', LIVE_FILE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(p + '.tmp', p)
    from . import op_seismicity as OS
    cs = [c for c in OS.catalogs(ws) if c['file'] != LIVE_FILE]
    n = len(lines) - 1
    cs.append({'file': LIVE_FILE, 'live': True, 'sha256': None, 'start': start[:10], 'end': end[:16], 'min_magnitude': 0.0, 'circle': circle,
               'source': 'USGS ComCat, read live by the GEA earthquake watch (the real-time feed and the FDSN event service)',
               'n_events': n, 'n_skipped': 0, 'attached_by': 'watch', 'attached_utc': end})
    OS._store(ws, 'catalogs.json', {'schema': 1, 'catalogs': cs})


# -- one poll --------------------------------------------------------------------------------------------------------
def poll(ws, notifier=None, now: Optional[datetime] = None, fetch: Optional[Callable[[str], bytes]] = None, force: bool = False) -> dict:
    """Poll once if due (or forced): fetch, merge, raise what is new. Returns what happened."""
    from . import alerts as AL
    from . import op_seismicity as OS
    now = now or datetime.now(timezone.utc)
    fetch = fetch or http_get
    s = settings(ws)
    with _LOCK:
        st = watch_state(ws)
        act = active(ws, now)
        running = bool(act['operations'])
        if not s['enabled']:
            return {'polled': False, 'why': 'the watch is off'}
        if not force and not running and s['mode'] == 'operations':
            st['idle_utc'] = _utc(now)
            _store(_dir(ws, 'live', 'state.json'), st)
            return {'polled': False, 'why': 'no operation under way at a well with a position'}
        if act['circle'] is None:
            return {'polled': False, 'why': 'no well has a surface position'}
        last_try = _parse(st.get('last_attempt_utc'))
        if not force and last_try and (now - last_try).total_seconds() < s['every_min'] * 60 - 1:
            return {'polled': False, 'why': 'not due'}
        circle = act['circle']
        last_ok = _parse(st.get('last_ok_utc'))
        st['last_attempt_utc'] = _utc(now)
        st['polls'] = st.get('polls', 0) + 1
        raised: List[dict] = []
        try:
            if last_ok is None or (now - last_ok) > timedelta(hours=23) or st.get('circle') != circle:
                start = last_ok - timedelta(hours=1) if last_ok and st.get('circle') == circle else None
                if start is None:
                    es = OS._dt(act['earliest_start']) if act['earliest_start'] else None
                    start = (es.replace(tzinfo=timezone.utc) if es else now - timedelta(days=1))
                start = max(start, now - timedelta(days=30))
                url = s['query_url'] + '?' + urllib.parse.urlencode({'format': 'geojson', 'starttime': start.strftime('%Y-%m-%dT%H:%M:%S'),
                                                                     'latitude': circle['lat'], 'longitude': circle['lon'], 'maxradiuskm': circle['radius_km'],
                                                                     'orderby': 'time-asc', 'limit': 20000})
                how = 'query'
                st['window_start_utc'] = min(st.get('window_start_utc') or _utc(start), _utc(start))
            else:
                url, how = s['feed_url'], 'feed'
            doc = json.loads(fetch(url).decode('utf-8'))
            feats = _features(doc, circle)
        except Exception as e:
            st['failures'] = st.get('failures', 0) + 1
            st['last_error'] = f'{type(e).__name__}: {e}'
            st['last_error_utc'] = _utc(now)
            down_after = 3 * s['every_min'] * 60
            ref = last_ok or _parse(st.get('first_attempt_utc')) or now
            st.setdefault('first_attempt_utc', _utc(now))
            if running and not st.get('down_since') and (now - ref).total_seconds() >= down_after - 1:
                st['down_since'] = _utc(now)
                raised.append(AL.raise_alert(ws, notifier, {'event': 'seismic.watch_down', 'key': 'seismic.watch_down',
                                                            'detail': f"no answer from the catalogue since {st.get('last_ok_utc') or 'the watch began'}: {st['last_error']}",
                                                            'last_ok_utc': st.get('last_ok_utc')}, now))
            _store(_dir(ws, 'live', 'state.json'), st)
            return {'polled': True, 'ok': False, 'error': st['last_error'], 'alerts': raised}
        st.setdefault('first_attempt_utc', _utc(now))
        if st.get('down_since'):
            raised.append(AL.raise_alert(ws, notifier, {'event': 'seismic.watch_up', 'key': 'seismic.watch_up',
                                                        'detail': f"the catalogue answers again (down since {st['down_since']})"}, now))
            st.pop('down_since', None)
        events = live_events(ws)
        new = 0
        for e in feats:
            old = events.get(e['event_id'])
            if old is None:
                new += 1
            if old is None or (e.get('updated') or 0) >= (old.get('updated') or 0):
                events[e['event_id']] = {**e, 'first_seen_utc': (old or {}).get('first_seen_utc') or _utc(now)}
        _store(_dir(ws, 'live', 'events.json'), events)
        st['circle'] = circle
        st.setdefault('window_start_utc', _utc(now - timedelta(days=1)))
        st['last_ok_utc'] = _utc(now)
        st['last_how'] = how
        st['last_n'] = len(feats)
        st['failures'] = 0
        st.pop('last_error', None)
        _write_catalog(ws, events, st['window_start_utc'], _utc(now), circle)
        # the frame, as the tab computes it; alert what is new
        res = OS.page(ws, 'workspace')
        prot = res.get('protocol') or {}
        alerted = st.setdefault('alerted', {})
        seen_ev = set(st.setdefault('events_alerted', []))
        for w in res['wells']:
            for ev in w.get('events', []):
                if ev['event_id'] not in events or not ev['within_radius'] or ev['relation'] not in ('DURING', 'AFTER'):
                    continue
                live = events[ev['event_id']]
                base = {'well_id': w['id'], 'well': w['display'], 'event_id': ev['event_id'], 'magnitude': ev['magnitude'], 'mag_type': ev['mag_type'],
                        'time_utc': ev['time'], 'distance_km': ev['distance_km'], 'relation': ev['relation'], 'operation': (ev.get('operation') or {}).get('kind'),
                        'place': ev.get('place'), 'status': live.get('status'), 'rig': (ev.get('rig') or {}).get('state'),
                        'distance_basis': ev.get('distance_basis'), 'nearest_md_ft': ev.get('nearest_md_ft')}
                frm = (f"the lateral of {w['display']} (nearest at {ev['nearest_md_ft']:,.0f} ft MD)" if ev.get('distance_basis') == 'lateral' and ev.get('nearest_md_ft') is not None
                       else w['display'])
                k_ev = f"{ev['event_id']}:{w['id']}"
                if k_ev not in seen_ev:
                    seen_ev.add(k_ev)
                    raised.append(AL.raise_alert(ws, notifier, {'event': 'seismic.event', 'key': f'seismic.event:{k_ev}', **base,
                                                                'detail': f"M {ev['magnitude']:g} {ev['mag_type']} {ev['distance_km']:g} km from {frm}, {ev['relation'].lower()} its {base['operation']}"}, now))
                pr = ev.get('protocol')
                if pr:
                    m = pr['highest']['m']
                    if alerted.get(k_ev) is None or m > alerted[k_ev]:
                        alerted[k_ev] = m
                        raised.append(AL.raise_alert(ws, notifier, {'event': 'seismic.protocol', 'key': f'seismic.protocol:{k_ev}:{m:g}', **base,
                                                                    'protocol': prot.get('name'), 'level_m': m, 'action': pr['highest']['action'],
                                                                    'detail': f"M {ev['magnitude']:g} {ev['distance_km']:g} km from {frm} reaches {prot.get('name')} level M {m:g}: {pr['highest']['action']}"}, now))
        st['events_alerted'] = sorted(seen_ev)
        _store(_dir(ws, 'live', 'state.json'), st)
        return {'polled': True, 'ok': True, 'how': how, 'url': url, 'in_circle': len(feats), 'new': new, 'alerts': raised}


def status(ws, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = settings(ws)
    st = watch_state(ws)
    act = active(ws, now)
    last_ok = _parse(st.get('last_ok_utc'))
    last_try = _parse(st.get('last_attempt_utc'))
    running = bool(act['operations'])
    if not s['enabled']:
        state = 'OFF'
    elif not running and s['mode'] == 'operations':
        state = 'IDLE'
    elif act['circle'] is None:
        state = 'NO POSITION'
    elif st.get('down_since') or (st.get('last_error') and (not last_ok or (last_try and last_try > last_ok))):
        state = 'NOT REACHING THE CATALOGUE'
    elif last_ok and (now - last_ok).total_seconds() <= s['every_min'] * 60 * 3:
        state = 'WATCHING'
    else:
        state = 'WAITING FOR THE FIRST POLL'
    nxt = (last_try + timedelta(minutes=s['every_min'])) if last_try else now
    return {'state': state, 'settings': s, 'operations': act['operations'], 'circle': act['circle'], 'wells_placed': act['wells_placed'],
            'last_ok_utc': st.get('last_ok_utc'), 'last_attempt_utc': st.get('last_attempt_utc'), 'last_error': st.get('last_error'),
            'last_how': st.get('last_how'), 'last_n': st.get('last_n'), 'polls': st.get('polls', 0), 'events': len(live_events(ws)),
            'next_utc': _utc(max(nxt, now)) if state not in ('OFF', 'IDLE', 'NO POSITION') else None, 'down_since': st.get('down_since')}


def tick(app, now: Optional[datetime] = None) -> Optional[dict]:
    """Called from the service's background poller every few seconds: the watch if due, and the condition checks."""
    from . import alerts as AL
    ws = app.ws
    out = None
    try:
        out = poll(ws, app.notifier, now)
    except Exception as e:                           # the poller outlives any one bad poll
        out = {'polled': False, 'error': f'{type(e).__name__}: {e}'}
    try:
        AL.check_conditions(ws, app.notifier, now)
    except Exception:
        pass
    return out
