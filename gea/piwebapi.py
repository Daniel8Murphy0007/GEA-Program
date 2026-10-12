# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""piwebapi - the historian pull: an operator's gauge history out of the AVEVA (OSIsoft) PI System through PI Web API.

Most operators' gauge data already sits in a PI Data Archive, and the sanctioned way out is PI Web API: REST
over HTTPS, JSON, each PI Point found by its path (`\\\\<data archive>\\<tag>`) and answered with a WebId, then
its history read from `/streams/{WebId}/recorded` (every archived value between two times, paged by the
server through `Links.Next`), `/streams/{WebId}/interpolated` (values at a fixed interval), or `/streams/
{WebId}/value` (the current value). Every value comes with its timestamp (UTC, the archive's) and three
flags - Good, Questionable, Substituted - and a unit abbreviation; a digital state arrives as an object with
a Name, not a number, and a system state (Shutdown, No Data, Pt Created, I/O Timeout, Calc Failed ...) is
`IsSystem`.

This client is read-only: GET only, on the point and stream controllers; nothing in it writes a value,
creates a point or changes a configuration. Authentication is whatever the site's PI Web API runs - basic
(the user and password read from the environment variable names the map gives, never from the map itself),
a bearer token the same way, or Kerberos through a proxy the site puts in front - and the client refuses to
send a credential over plain HTTP. Every PI value becomes a SampleRecord with the archive timestamp as the
source time and the fetch time as the ingest time: Good is GOOD; Questionable is kept with the flag in the
rule; Substituted the same; not Good is BAD with the flag; a digital or system state is GAP with the state's
name. The mapping is the site's tag list with its source, and the port refuses to run without one.

`SimServer` is an in-process stand-in that answers the same endpoints (with paging, a digital state and a
system state) for the site's rehearsal and the gate.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .live_ports import TagMapping, conversion, load_mappings, make_record, records_to_stream, utc_now
from .sample_record import SampleRecord, parse_utc

EXAMPLE_CONFIG = {
    'base_url': 'https://pi.example.local/piwebapi', 'auth': 'basic', 'user_env': 'GEA_PI_USER', 'password_env': 'GEA_PI_PASSWORD', 'token_env': 'GEA_PI_TOKEN',
    'verify_tls': True, 'timeout_s': 30.0, 'mode': 'recorded', 'start': '*-1d', 'end': '*', 'interval': '1m', 'max_count': 10000,
    'mapping': [{'address': '\\\\PISRV01\\SWD1.DHP', 'tag_id': 'DHP_psi', 'unit': 'psi', 'tag_class': 'pressure'},
                {'address': '\\\\PISRV01\\SWD1.DHT', 'tag_id': 'DHT_degF', 'unit': 'degF', 'tag_class': 'temperature'},
                {'address': '\\\\PISRV01\\SWD1.PUMP', 'tag_id': 'PUMP_STATE', 'unit': '', 'tag_class': 'status'}],
    'tag_source': 'EXAMPLE - the stand-in server this port ships for rehearsal, not a historian',
}
MODES = ('recorded', 'interpolated', 'value')


class PiError(ConnectionError):
    pass


class PiWebApiClient:
    """GET only. `opener` may be given for a session that needs one (a proxy, a cookie jar); the default is urllib's."""

    def __init__(self, base_url: str, auth: str = 'none', user_env: str = '', password_env: str = '', token_env: str = '', verify_tls: bool = True,
                 timeout_s: float = 30.0):
        self.base = base_url.rstrip('/')
        self.timeout = float(timeout_s)
        self.headers = {'Accept': 'application/json', 'User-Agent': 'gea-program'}
        self.calls: List[str] = []
        if auth in ('basic', 'bearer') and not self.base.lower().startswith('https://') and not self.base.startswith('http://127.0.0.1') and not self.base.startswith('http://localhost'):
            raise PiError('a credential is not sent over plain HTTP: use https:// (the loopback is the one exception, for the stand-in)')
        if auth == 'basic':
            u, pw = os.environ.get(user_env or '', ''), os.environ.get(password_env or '', '')
            if not u or not pw:
                raise PiError(f'basic auth needs the user and password in the environment variables the map names ({user_env!r}, {password_env!r}); the map never holds them')
            self.headers['Authorization'] = 'Basic ' + base64.b64encode(f'{u}:{pw}'.encode()).decode()
        elif auth == 'bearer':
            tok = os.environ.get(token_env or '', '')
            if not tok:
                raise PiError(f'bearer auth needs the token in the environment variable the map names ({token_env!r})')
            self.headers['Authorization'] = 'Bearer ' + tok
        elif auth not in ('none', 'kerberos'):
            raise PiError(f"auth must be none, basic, bearer or kerberos (through the site's proxy), got {auth!r}")
        self.ctx = None
        if not verify_tls:
            import ssl
            self.ctx = ssl.create_default_context(); self.ctx.check_hostname = False; self.ctx.verify_mode = ssl.CERT_NONE

    def get(self, path_or_url: str, params: Optional[dict] = None) -> dict:
        url = path_or_url if path_or_url.startswith('http') else self.base + path_or_url
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        self.calls.append(url)
        req = urllib.request.Request(url, headers=self.headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode('utf-8', 'replace')
            raise PiError(f'HTTP {e.code} from {url.split("?")[0]}: {body}')
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise PiError(f'{url.split("?")[0]}: {e}')

    def point(self, path: str) -> dict:
        """The PI Point for a path: WebId, Name, Descriptor, EngineeringUnits, PointType."""
        d = self.get('/points', {'path': path})
        if 'WebId' not in d:
            raise PiError(f'no WebId in the answer for {path}: {str(d)[:200]}')
        return d

    def recorded(self, web_id: str, start: str, end: str, max_count: int = 10000, boundary: str = 'Inside') -> List[dict]:
        items: List[dict] = []
        d = self.get(f'/streams/{web_id}/recorded', {'startTime': start, 'endTime': end, 'maxCount': int(max_count), 'boundaryType': boundary})
        items.extend(d.get('Items', []))
        nxt = (d.get('Links') or {}).get('Next')
        hops = 0
        while nxt and hops < 1000:                            # the server pages; follow Links.Next until it stops
            d = self.get(nxt)
            items.extend(d.get('Items', []))
            nxt = (d.get('Links') or {}).get('Next'); hops += 1
        return items

    def interpolated(self, web_id: str, start: str, end: str, interval: str = '1m') -> List[dict]:
        d = self.get(f'/streams/{web_id}/interpolated', {'startTime': start, 'endTime': end, 'interval': interval})
        return d.get('Items', [])

    def value(self, web_id: str) -> dict:
        return self.get(f'/streams/{web_id}/value')


def classify(item: dict) -> Tuple[Optional[float], str, str]:
    """A PI value to (number or None, quality, rule)."""
    v = item.get('Value')
    good, q, sub = item.get('Good', True), item.get('Questionable', False), item.get('Substituted', False)
    if isinstance(v, dict):                                    # a digital or system state
        name = str(v.get('Name', 'state'))
        return None, 'GAP', (f'system state {name}' if v.get('IsSystem') else f'digital state {name}')
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None, 'GAP', f'non-numeric value {v!r}'
    if not good:
        return x, 'BAD', 'not Good'
    flags = [n for n, f in (('Questionable', q), ('Substituted', sub)) if f]
    return x, 'GOOD', ', '.join(flags)


class PiPort:
    def __init__(self, config: dict):
        self.cfg = dict(config)
        if not self.cfg.get('mapping'):
            raise ValueError('a tag mapping is required (the PI Point path per tag) - the port maps what the site declares')
        if not self.cfg.get('tag_source'):
            raise ValueError("the tag list needs its source (tag_source: the site's PI tag list, document and revision)")
        if not self.cfg.get('base_url'):
            raise ValueError('base_url is required (https://<server>/piwebapi)')
        if self.cfg.get('mode', 'recorded') not in MODES:
            raise ValueError(f'mode must be one of {MODES}')
        self.maps: Dict[str, TagMapping] = load_mappings(self.cfg['mapping'], 'address')
        self.client = PiWebApiClient(self.cfg['base_url'], str(self.cfg.get('auth', 'none')), str(self.cfg.get('user_env', '')), str(self.cfg.get('password_env', '')),
                                     str(self.cfg.get('token_env', '')), bool(self.cfg.get('verify_tls', True)), float(self.cfg.get('timeout_s', 30.0)))
        self.records: List[SampleRecord] = []
        self.points: Dict[str, dict] = {}
        self.faults: List[str] = []

    def _take(self, m: TagMapping, items: List[dict], unit_abbr: str, fetched: datetime) -> int:
        n = 0
        for it in items:
            x, q, rule = classify(it)
            ts = it.get('Timestamp')
            try:
                src = parse_utc(ts) if ts else fetched
            except Exception:                                   # noqa: BLE001
                src = fetched; rule = (rule + '; ' if rule else '') + f'timestamp {ts!r} not parsed'
            unit_in = str(it.get('UnitsAbbreviation') or unit_abbr or '')
            if x is not None and unit_in and m.unit and unit_in != m.unit and not m.unit_in:
                conv = conversion(unit_in, m.unit)
                if conv is None:
                    q, rule, x = 'BAD', f'archive unit {unit_in} has no conversion to {m.unit}', None
                else:
                    x = x * conv[0] + conv[1]
            self.records.append(make_record(m, x, src, q, rule, fetched))
            if q == 'BAD' and x is not None:
                self.records[-1].value = x
            n += 1
        return n

    def pull(self) -> dict:
        fetched = utc_now()
        mode = self.cfg.get('mode', 'recorded')
        out = {'mode': mode, 'tags': {}}
        for addr, m in self.maps.items():
            try:
                pt = self.points.get(addr) or self.client.point(addr)
                self.points[addr] = pt
                wid = pt['WebId']
                if mode == 'recorded':
                    items = self.client.recorded(wid, str(self.cfg.get('start', '*-1d')), str(self.cfg.get('end', '*')), int(self.cfg.get('max_count', 10000)))
                elif mode == 'interpolated':
                    items = self.client.interpolated(wid, str(self.cfg.get('start', '*-1d')), str(self.cfg.get('end', '*')), str(self.cfg.get('interval', '1m')))
                else:
                    items = [self.client.value(wid)]
                n = self._take(m, items, str(pt.get('EngineeringUnits') or ''), fetched)
                out['tags'][m.tag_id] = {'point': pt.get('Name'), 'items': n, 'units': pt.get('EngineeringUnits', '')}
            except PiError as e:
                self.faults.append(f'{m.tag_id}: {e}')
                self.records.append(make_record(m, None, fetched, 'GAP', str(e)[:200], fetched))
                out['tags'][m.tag_id] = {'error': str(e)[:200]}
        return out

    def stream(self):
        return records_to_stream(self.records, name='piwebapi', source_format='piwebapi')

    def summary(self) -> dict:
        q: Dict[str, int] = {}
        for r in self.records:
            q[r.quality_flag] = q.get(r.quality_flag, 0) + 1
        return {'records': len(self.records), 'quality': q, 'points': {k: v.get('Name') for k, v in self.points.items()}, 'faults': self.faults[-20:],
                'calls': len(self.client.calls)}


def load_config(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with Path(path_or_dict).open(encoding='utf-8') as f:
        return json.load(f)


def read_piwebapi(config) -> 'LiveStream':   # noqa: F821
    cfg = load_config(config)
    if cfg.get('transport') == 'sim' or cfg.get('base_url') == 'sim':
        sim = SimServer(); h, port = sim.serve()
        try:
            p = PiPort({**cfg, 'base_url': f'http://127.0.0.1:{port}/piwebapi', 'auth': 'none'})
            p.pull()
        finally:
            h.close()
        return p.stream()
    p = PiPort(cfg)
    p.pull()
    return p.stream()


# ---- a stand-in server ----------------------------------------------------------------------------------------------------
class SimServer:
    """Answers /points?path=, /streams/{id}/recorded (paged, 100 per page), /interpolated, /value with a day of
    synthetic archive values per point; a digital point answers states; one system state is planted; a path it
    does not know is a 404 with PI's error shape; a bad credential (when `require_basic`) is a 401."""

    def __init__(self, require_basic: Optional[Tuple[str, str]] = None, n_values: int = 250, page: int = 100):
        self.require_basic, self.n, self.page = require_basic, int(n_values), int(page)
        self.points = {'\\\\PISRV01\\SWD1.DHP': ('P0DHP', 'SWD1.DHP', 'psi', 'Float32'), '\\\\PISRV01\\SWD1.DHT': ('P0DHT', 'SWD1.DHT', 'degF', 'Float32'),
                       '\\\\PISRV01\\SWD1.PUMP': ('P0PMP', 'SWD1.PUMP', '', 'Digital'), '\\\\PISRV01\\SWD1.DHP_BAR': ('P0BAR', 'SWD1.DHP_BAR', 'bar', 'Float32')}
        self.requests: List[str] = []
        self.t0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)

    def items(self, web_id: str) -> List[dict]:
        out = []
        for i in range(self.n):
            ts = (self.t0 + timedelta(minutes=5 * i)).strftime('%Y-%m-%dT%H:%M:%SZ')
            if web_id == 'P0PMP':
                out.append({'Timestamp': ts, 'Value': {'Name': 'ON' if i % 7 else 'OFF', 'Value': 1 if i % 7 else 0, 'IsSystem': False}, 'UnitsAbbreviation': '', 'Good': True, 'Questionable': False, 'Substituted': False})
            elif i == 10:
                out.append({'Timestamp': ts, 'Value': {'Name': 'I/O Timeout', 'Value': 246, 'IsSystem': True}, 'UnitsAbbreviation': '', 'Good': False, 'Questionable': False, 'Substituted': False})
            else:
                base = {'P0DHP': 3185.5, 'P0DHT': 212.3, 'P0BAR': 219.6}[web_id]
                out.append({'Timestamp': ts, 'Value': round(base + 0.01 * i, 3), 'UnitsAbbreviation': {'P0DHP': 'psi', 'P0DHT': 'degF', 'P0BAR': 'bar'}[web_id],
                            'Good': i != 20, 'Questionable': i == 30, 'Substituted': i == 40})
        return out

    def serve(self, host: str = '127.0.0.1', port: int = 0):
        sim = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
                sim.requests.append(self.path)
                if sim.require_basic:
                    want = 'Basic ' + base64.b64encode(f'{sim.require_basic[0]}:{sim.require_basic[1]}'.encode()).decode()
                    if self.headers.get('Authorization') != want:
                        return self._send(401, {'Errors': ['Unauthorized']})
                path = u.path
                if path.endswith('/points'):
                    p = q.get('path', [''])[0]
                    if p not in sim.points:
                        return self._send(404, {'Errors': [f'The point with path {p} was not found.']})
                    wid, name, units, ptype = sim.points[p]
                    return self._send(200, {'WebId': wid, 'Name': name, 'Path': p, 'Descriptor': name + ' descriptor', 'PointType': ptype, 'EngineeringUnits': units})
                parts = path.strip('/').split('/')
                if len(parts) >= 4 and parts[-3] == 'streams':
                    wid, what = parts[-2], parts[-1]
                    if wid not in [v[0] for v in sim.points.values()]:
                        return self._send(404, {'Errors': ['Unknown WebId']})
                    items = sim.items(wid)
                    if what == 'value':
                        return self._send(200, items[-1])
                    if what == 'interpolated':
                        return self._send(200, {'Items': items[::5]})
                    if what == 'recorded':
                        start = int(q.get('startIndex', ['0'])[0])
                        pg = items[start:start + sim.page]
                        body = {'Items': pg, 'Links': {}}
                        if start + sim.page < len(items):
                            body['Links']['Next'] = f'http://127.0.0.1:{self.server.server_address[1]}/piwebapi/streams/{wid}/recorded?startIndex={start + sim.page}'
                        return self._send(200, body)
                return self._send(404, {'Errors': ['not found']})
        srv = ThreadingHTTPServer((host, port), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        return _Handle(srv, th), srv.server_address[1]


class _Handle:
    def __init__(self, srv, th):
        self.srv, self.th = srv, th

    def close(self) -> None:
        self.srv.shutdown(); self.srv.server_close(); self.th.join(timeout=2)


def selftest() -> dict:
    out = {}
    out['classify'] = (classify({'Value': 1.5, 'Good': True}) == (1.5, 'GOOD', '') and classify({'Value': 2.0, 'Good': False}) == (2.0, 'BAD', 'not Good')
                       and classify({'Value': {'Name': 'Shutdown', 'IsSystem': True}, 'Good': False})[1:] == ('GAP', 'system state Shutdown')
                       and classify({'Value': 3.0, 'Good': True, 'Questionable': True, 'Substituted': True})[2] == 'Questionable, Substituted')
    sim = SimServer(); h, port = sim.serve()
    try:
        p = PiPort({**EXAMPLE_CONFIG, 'base_url': f'http://127.0.0.1:{port}/piwebapi', 'auth': 'none',
                    'mapping': EXAMPLE_CONFIG['mapping'] + [{'address': '\\\\PISRV01\\SWD1.DHP_BAR', 'tag_id': 'DHP2_psi', 'unit': 'psi'}, {'address': '\\\\PISRV01\\NOPE', 'tag_id': 'X', 'unit': ''}]})
        r = p.pull(); s = p.summary()
        dhp = [x for x in p.records if x.tag_id == 'DHP_psi']
        out['paged_recorded'] = len(dhp) == 250 and sum('startIndex=' in u for u in p.client.calls) >= 2 and dhp[0].timestamp_utc.startswith('2026-10-01T00:00:00')
        out['flags'] = (dhp[10].quality_flag == 'GAP' and 'system state I/O Timeout' in dhp[10].rule_fired and dhp[20].quality_flag == 'BAD' and dhp[30].rule_fired == 'Questionable'
                        and dhp[40].rule_fired == 'Substituted' and all(x.latency_s() > 0 for x in dhp[:3]))
        pump = [x for x in p.records if x.tag_id == 'PUMP_STATE']
        out['digital'] = len(pump) == 250 and pump[0].quality_flag == 'GAP' and 'digital state OFF' in pump[0].rule_fired and 'digital state ON' in pump[1].rule_fired
        bar = [x for x in p.records if x.tag_id == 'DHP2_psi']
        out['unit_converted'] = abs(bar[0].value - 219.6 * 14.503773773) < 1e-3 and bar[0].unit == 'psi'
        out['missing_named'] = r['tags']['X'].get('error', '').startswith('HTTP 404') and any('X:' in f for f in s['faults'])
    finally:
        h.close()
    sim2 = SimServer(require_basic=('pi_reader', 'pw')); h, port = sim2.serve()
    try:
        os.environ['GEA_PI_USER'], os.environ['GEA_PI_PASSWORD'] = 'pi_reader', 'wrong'
        p2 = PiPort({**EXAMPLE_CONFIG, 'base_url': f'http://127.0.0.1:{port}/piwebapi', 'mode': 'value', 'mapping': EXAMPLE_CONFIG['mapping'][:1]})
        r2 = p2.pull()
        os.environ['GEA_PI_PASSWORD'] = 'pw'
        p3 = PiPort({**EXAMPLE_CONFIG, 'base_url': f'http://127.0.0.1:{port}/piwebapi', 'mode': 'value', 'mapping': EXAMPLE_CONFIG['mapping'][:1]})
        r3 = p3.pull()
        out['basic_auth'] = 'HTTP 401' in r2['tags']['DHP_psi'].get('error', '') and r3['tags']['DHP_psi'].get('items') == 1 and abs(p3.records[-1].value - (3185.5 + 0.01 * 249)) < 1e-6
    finally:
        h.close()
        os.environ.pop('GEA_PI_USER', None); os.environ.pop('GEA_PI_PASSWORD', None)
    try:
        PiWebApiClient('http://pi.example.local/piwebapi', auth='bearer', token_env='X')
        out['plain_http_refused'] = False
    except PiError as e:
        out['plain_http_refused'] = 'plain HTTP' in str(e)
    out['ok'] = all(out.values())
    return out
