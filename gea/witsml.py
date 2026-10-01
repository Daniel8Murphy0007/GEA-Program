# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""witsml - the WITSML 1.4.1 read-only client: poll one log object from a store.

WITSML (Energistics) is the rig-site data standard behind most service-company
and operator real-time stores. A store exposes a SOAP endpoint (the `STORE`
interface, `WMLS_GetFromStore`); a client sends a query template and gets the
matching objects back as XML. This port asks one `log` object for the rows
newer than the last row it has seen, at a polling interval, and turns each row
into measurement records through the site's mnemonic map. It only ever calls
GetVersion, GetCap and GetFromStore - it cannot write to a store.

    {
      "url": "https://store.example.com/witsml/store.asmx",
      "auth": {"username_env": "GEA_WITSML_USER", "password_env": "GEA_WITSML_PASSWORD"},
      "uid_well": "W-1", "uid_wellbore": "WB-1", "uid_log": "LOG-1",
      "poll_s": 10, "lookback_s": 600, "timeout_s": 30,
      "items": [ {"mnemonic": "HKLD", "tag_id": "HKLD_klbf", "unit": "klbf", "tag_class": "hookload", "scale": 1.0}, ... ]
    }

Date-time-indexed logs give the source timestamp from the index curve; a
depth-indexed log is stamped at arrival and its index becomes a depth tag.
Mnemonics not in the map are kept as `witsml_<MNEMONIC>` with the unit the
store declares, so nothing is lost. Null values (the store's `nullValue`, or
an empty field) become GAP with the rule named.

`TestStore` is an in-process WITSML store on the standard library that
answers the three calls with a deterministic time log - how a site rehearses
its patch before it has credentials, and how the acceptance suite tests this
port with no server.

Headless-safe: stdlib only (urllib, xml.etree, http.server).
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, Recording, iso, utc_now
from .ports import LiveStream, PortSpec, PORT_REGISTRY
from .sample_record import SampleRecord

SOAP_NS = 'http://schemas.xmlsoap.org/soap/envelope/'
WMLS_NS = 'http://www.witsml.org/wsdl/120'
DATA_NS = 'http://www.witsml.org/schemas/1series'
VERSION = '1.4.1.1'

EXAMPLE_CONFIG = {
    'url': 'http://127.0.0.1:8000/witsml/store',
    'auth': {'username_env': 'GEA_WITSML_USER', 'password_env': 'GEA_WITSML_PASSWORD'},
    'uid_well': 'W-1', 'uid_wellbore': 'WB-1', 'uid_log': 'LOG-1',
    'poll_s': 5.0, 'lookback_s': 600.0, 'timeout_s': 30.0, 'keep_unmapped': True,
    'items': [
        {'mnemonic': 'DBTM', 'tag_id': 'DBTM_ft', 'unit': 'ft', 'tag_class': 'depth', 'scale': 1.0, 'description': 'bit depth'},
        {'mnemonic': 'HKLD', 'tag_id': 'HKLD_klbf', 'unit': 'klbf', 'tag_class': 'hookload', 'scale': 1.0, 'description': 'hookload'},
        {'mnemonic': 'SPPA', 'tag_id': 'SPP_psi', 'unit': 'psi', 'tag_class': 'pressure', 'scale': 1.0, 'description': 'standpipe pressure'},
        {'mnemonic': 'ROP', 'tag_id': 'ROP_ft_h', 'unit': 'ft/h', 'tag_class': 'rate', 'scale': 1.0, 'description': 'rate of penetration'},
    ],
}


def write_example_config(path) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE_CONFIG, f, indent=1)
    return str(path)


def load_config(config) -> dict:
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    d = json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith('_')}))
    for k in ('url', 'uid_well', 'uid_wellbore', 'uid_log'):
        if not d.get(k):
            raise ValueError(f"witsml config needs '{k}'")
    d['_mappings'] = load_mappings(d.get('items', []), 'mnemonic') if d.get('items') else {}
    return d


# ---------------------------------------------------------------------------
# SOAP plumbing
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def log_query(uid_well: str, uid_wellbore: str, uid_log: str, start_index: Optional[str] = None) -> str:
    start = f'<startDateTimeIndex>{_esc(start_index)}</startDateTimeIndex>' if start_index else ''
    return (f'<logs xmlns="{DATA_NS}" version="{VERSION}"><log uidWell="{_esc(uid_well)}" uidWellbore="{_esc(uid_wellbore)}" uid="{_esc(uid_log)}">'
            f'<nameWell/><nameWellbore/><name/><indexType/><indexCurve/><nullValue/>{start}'
            f'<logCurveInfo><mnemonic/><unit/><typeLogData/></logCurveInfo>'
            f'<logData><mnemonicList/><unitList/><data/></logData></log></logs>')


def soap_envelope(op: str, params: Dict[str, str]) -> bytes:
    body = ''.join(f'<{k}>{_esc(v)}</{k}>' for k, v in params.items())
    return (f'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body>'
            f'<{op} xmlns="{WMLS_NS}">{body}</{op}></soap:Body></soap:Envelope>').encode('utf-8')


def soap_result(xml_text: str, op: str) -> Dict[str, str]:
    root = ET.fromstring(xml_text)
    out = {}
    for el in root.iter():
        tag = el.tag.split('}')[-1]
        if tag in ('Result', 'XMLout', 'SuppMsgOut', f'{op}Result', 'return'):
            out[tag] = el.text or ''
    return out


class WitsmlClient:
    """The three read-only calls."""

    def __init__(self, url: str, user: str = '', password: str = '', timeout_s: float = 30.0):
        self.url, self.timeout = url, timeout_s
        self.auth = base64.b64encode(f'{user}:{password}'.encode()).decode() if user else None

    def call(self, op: str, params: Dict[str, str]) -> Dict[str, str]:
        req = urllib.request.Request(self.url, data=soap_envelope(op, params), method='POST')
        req.add_header('Content-Type', 'text/xml; charset=utf-8')
        req.add_header('SOAPAction', f'"{WMLS_NS}/{op}"')
        if self.auth:
            req.add_header('Authorization', 'Basic ' + self.auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return soap_result(r.read().decode('utf-8', 'replace'), op)
        except urllib.error.HTTPError as e:
            raise ConnectionError(f'witsml: {op} failed with HTTP {e.code}')
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f'witsml: could not reach {self.url}: {getattr(e, "reason", e)}')

    def get_version(self) -> str:
        r = self.call('WMLS_GetVersion', {})
        return r.get('WMLS_GetVersionResult') or r.get('return') or r.get('Result', '')

    def get_cap(self) -> str:
        r = self.call('WMLS_GetCap', {'OptionsIn': f'dataVersion={VERSION}'})
        return r.get('CapabilitiesOut') or r.get('XMLout', '')

    def get_from_store(self, type_in: str, query: str, options: str = 'returnElements=all') -> Tuple[int, str, str]:
        r = self.call('WMLS_GetFromStore', {'WMLtypeIn': type_in, 'QueryIn': query, 'OptionsIn': options, 'CapabilitiesIn': ''})
        code = r.get('Result') or r.get('WMLS_GetFromStoreResult') or '0'
        try:
            code_i = int(code)
        except ValueError:
            code_i = 0
        return code_i, r.get('XMLout', ''), r.get('SuppMsgOut', '')


# ---------------------------------------------------------------------------
# Log XML -> rows -> records
# ---------------------------------------------------------------------------
def parse_log(xml_text: str) -> dict:
    """XMLout of a log query -> {'index_type', 'index_curve', 'null_value', 'mnemonics', 'units', 'rows': [[...]]}."""
    if not xml_text.strip():
        return {'index_type': '', 'index_curve': '', 'null_value': '', 'mnemonics': [], 'units': [], 'rows': []}
    root = ET.fromstring(xml_text)
    ns = {'w': DATA_NS}
    log = root.find('w:log', ns)
    if log is None:
        return {'index_type': '', 'index_curve': '', 'null_value': '', 'mnemonics': [], 'units': [], 'rows': []}
    t = lambda tag: (log.findtext(f'w:{tag}', default='', namespaces=ns) or '').strip()
    ld = log.find('w:logData', ns)
    mn = (ld.findtext('w:mnemonicList', default='', namespaces=ns) if ld is not None else '').split(',')
    un = (ld.findtext('w:unitList', default='', namespaces=ns) if ld is not None else '').split(',')
    rows = [d.text.split(',') for d in (ld.findall('w:data', ns) if ld is not None else []) if d.text]
    units = {m.strip(): (un[i].strip() if i < len(un) else '') for i, m in enumerate(mn)}
    for lci in log.findall('w:logCurveInfo', ns):                       # curve info may carry units the list lacks
        m = (lci.findtext('w:mnemonic', default='', namespaces=ns) or '').strip()
        u = (lci.findtext('w:unit', default='', namespaces=ns) or '').strip()
        if m and u and not units.get(m):
            units[m] = u
    return {'index_type': t('indexType'), 'index_curve': t('indexCurve'), 'null_value': t('nullValue'),
            'mnemonics': [m.strip() for m in mn if m.strip()], 'units': units, 'rows': rows}


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.strip().replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def rows_to_records(cfg: dict, parsed: dict, received_at: Optional[datetime] = None) -> List[SampleRecord]:
    received_at = received_at or utc_now()
    mn, units, null = parsed['mnemonics'], parsed['units'], (parsed.get('null_value') or '').strip()
    if not mn:
        return []
    idx_name = parsed.get('index_curve') or mn[0]
    idx_pos = mn.index(idx_name) if idx_name in mn else 0
    time_indexed = 'date' in (parsed.get('index_type') or '').lower() or 'time' in (parsed.get('index_type') or '').lower()
    out: List[SampleRecord] = []
    for row in parsed['rows']:
        if len(row) < len(mn):
            row = row + [''] * (len(mn) - len(row))
        src = _parse_dt(row[idx_pos]) if time_indexed else None
        for i, m in enumerate(mn):
            if i == idx_pos and time_indexed:
                continue
            tm = cfg['_mappings'].get(m)
            if tm is None:
                if not cfg.get('keep_unmapped', True):
                    continue
                tm = TagMapping(address=m, tag_id=f'witsml_{m}', unit=units.get(m, ''), tag_class='generic', description=f'WITSML {m} (unmapped)')
            text = row[i].strip()
            if text == '' or (null and text == null):
                out.append(make_record(tm, None, src, 'GAP', f'WITSML null {text!r}' if text else 'WITSML empty field', received_at))
                continue
            try:
                out.append(make_record(tm, float(text), src, 'GOOD', '', received_at))
            except ValueError:
                out.append(make_record(tm, None, src, 'GAP', f'WITSML non-numeric {text!r}', received_at))
    return out


# ---------------------------------------------------------------------------
# The tap
# ---------------------------------------------------------------------------
class WitsmlTap:
    def __init__(self, config, recording_path: Optional[str] = None):
        self.cfg = load_config(config)
        auth = self.cfg.get('auth') or {}
        user = os.environ.get(auth.get('username_env', ''), '') if auth.get('username_env') else ''
        pwd = os.environ.get(auth.get('password_env', ''), '') if auth.get('password_env') else ''
        self.client = WitsmlClient(self.cfg['url'], user, pwd, float(self.cfg.get('timeout_s', 30.0)))
        self.recording = Recording(recording_path)
        self.records: List[SampleRecord] = []
        self.last_index: Optional[str] = None
        self.polls = 0
        self.stop_event = threading.Event()

    def poll_once(self, on_record: Optional[Callable[[SampleRecord], None]] = None) -> List[SampleRecord]:
        start = self.last_index or iso(utc_now() - timedelta(seconds=float(self.cfg.get('lookback_s', 600.0))))
        q = log_query(self.cfg['uid_well'], self.cfg['uid_wellbore'], self.cfg['uid_log'], start)
        code, xml_out, msg = self.client.get_from_store('log', q)
        recv = utc_now()
        self.polls += 1
        if code < 1:
            raise ConnectionError(f'witsml: GetFromStore returned {code}: {msg or "no message"}')
        parsed = parse_log(xml_out)
        self.recording.write({'received_at': iso(recv), 'result': code, 'xml': xml_out})
        got = rows_to_records(self.cfg, parsed, recv)
        if parsed['rows']:
            idx_name = parsed.get('index_curve') or parsed['mnemonics'][0]
            pos = parsed['mnemonics'].index(idx_name) if idx_name in parsed['mnemonics'] else 0
            last = parsed['rows'][-1][pos].strip()
            dt = _parse_dt(last)
            self.last_index = iso(dt + timedelta(milliseconds=1)) if dt else last    # strictly after the last row seen
        for r in got:
            if on_record:
                on_record(r)
        self.records.extend(got)
        return got

    def run(self, duration_s: Optional[float] = 30.0, on_record: Optional[Callable[[SampleRecord], None]] = None) -> List[SampleRecord]:
        got: List[SampleRecord] = []
        t_end = None if duration_s is None else time.time() + float(duration_s)
        poll = float(self.cfg.get('poll_s', 5.0))
        while not self.stop_event.is_set() and (t_end is None or time.time() < t_end):
            got.extend(self.poll_once(on_record))
            if self.stop_event.wait(poll):
                break
        return got

    def stop(self) -> None:
        self.stop_event.set()

    def to_stream(self, name: str = 'witsml') -> LiveStream:
        return records_to_stream(self.records, name=name, source_format='witsml', meta={'url': self.cfg['url']})


def replay(config, recording_path: str) -> List[SampleRecord]:
    cfg = load_config(config)
    out = []
    for msg in Recording.read(recording_path):
        recv = datetime.fromisoformat(msg['received_at'].replace('Z', '+00:00'))
        out.extend(rows_to_records(cfg, parse_log(msg.get('xml', '')), recv))
    return out


def read_witsml(config) -> LiveStream:
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    if d.get('replay'):
        return records_to_stream(replay(d, d['replay']), name='witsml-replay', source_format='witsml', meta={'replay': d['replay']})
    tap = WitsmlTap(d, recording_path=d.get('recording'))
    tap.run(float(d.get('duration_s', 30.0)))
    return tap.to_stream()


# ---------------------------------------------------------------------------
# An in-process store for rehearsal and acceptance
# ---------------------------------------------------------------------------
class TestStore:
    """Answers GetVersion, GetCap and GetFromStore for one time-indexed log whose rows
    are generated on demand (one row per `step_s` since `t0`), optionally behind basic auth."""

    def __init__(self, host: str = '127.0.0.1', port: int = 0, step_s: float = 1.0, t0: Optional[datetime] = None,
                 user: Optional[str] = None, password: Optional[str] = None, uids=('W-1', 'WB-1', 'LOG-1')):
        self.step_s, self.t0 = step_s, (t0 or utc_now() - timedelta(seconds=120))
        self.user, self.password, self.uids = user, password, uids
        self.calls: List[str] = []
        store = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if store.user:
                    hdr = self.headers.get('Authorization', '')
                    if hdr != 'Basic ' + base64.b64encode(f'{store.user}:{store.password}'.encode()).decode():
                        self.send_response(401); self.end_headers(); return
                n = int(self.headers.get('Content-Length') or 0)
                body = html.unescape(self.rfile.read(n).decode('utf-8', 'replace'))     # QueryIn arrives XML-escaped inside the envelope
                action = (self.headers.get('SOAPAction') or '').strip('"').split('/')[-1]
                store.calls.append(action)
                if action == 'WMLS_GetVersion':
                    resp = f'<WMLS_GetVersionResponse xmlns="{WMLS_NS}"><return>{VERSION}</return></WMLS_GetVersionResponse>'
                elif action == 'WMLS_GetCap':
                    resp = f'<WMLS_GetCapResponse xmlns="{WMLS_NS}"><Result>1</Result><CapabilitiesOut>&lt;capServers version="{VERSION}"/&gt;</CapabilitiesOut></WMLS_GetCapResponse>'
                else:
                    m = re.search(r'<startDateTimeIndex>([^<]*)</startDateTimeIndex>', body)
                    uw = re.search(r'uidWell="([^"]*)"', body)
                    if uw and uw.group(1) != store.uids[0]:
                        resp = f'<WMLS_GetFromStoreResponse xmlns="{WMLS_NS}"><Result>1</Result><XMLout>&lt;logs xmlns="{DATA_NS}" version="{VERSION}"/&gt;</XMLout><SuppMsgOut></SuppMsgOut></WMLS_GetFromStoreResponse>'
                    else:
                        start = _parse_dt(m.group(1)) if m else None
                        xml_out = store.log_xml(start)
                        resp = f'<WMLS_GetFromStoreResponse xmlns="{WMLS_NS}"><Result>1</Result><XMLout>{_esc(xml_out)}</XMLout><SuppMsgOut></SuppMsgOut></WMLS_GetFromStoreResponse>'
                data = (f'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body>{resp}</soap:Body></soap:Envelope>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer((host, port), H)
        self.url = f'http://{self.httpd.server_address[0]}:{self.httpd.server_address[1]}/witsml/store'
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def rows(self, start: Optional[datetime], now: Optional[datetime] = None) -> List[List[str]]:
        now = now or utc_now()
        k = 0 if start is None else max(0, int((start - self.t0).total_seconds() // self.step_s))
        rows = []
        while True:
            t = self.t0 + timedelta(seconds=k * self.step_s)
            if t > now:
                break
            if start is not None and t <= start:
                k += 1
                continue
            rows.append([iso(t), f'{9850 + 0.3 * k:.2f}', f'{160 + (k % 7) * 0.5:.1f}', ('-999.25' if k % 23 == 0 else f'{2850 + (k % 5) * 3:.0f}'), f'{40 + (k % 3):.1f}'])
            k += 1
            if len(rows) >= 5000:
                break
        return rows

    def log_xml(self, start: Optional[datetime]) -> str:
        rows = self.rows(start)
        data = ''.join(f'<data>{",".join(r)}</data>' for r in rows)
        return (f'<logs xmlns="{DATA_NS}" version="{VERSION}"><log uidWell="{self.uids[0]}" uidWellbore="{self.uids[1]}" uid="{self.uids[2]}">'
                f'<nameWell>Test well</nameWell><name>Time log</name><indexType>date time</indexType><indexCurve>TIME</indexCurve><nullValue>-999.25</nullValue>'
                f'<logCurveInfo uid="TIME"><mnemonic>TIME</mnemonic><unit>unitless</unit></logCurveInfo>'
                f'<logCurveInfo uid="DBTM"><mnemonic>DBTM</mnemonic><unit>ft</unit></logCurveInfo>'
                f'<logCurveInfo uid="HKLD"><mnemonic>HKLD</mnemonic><unit>klbf</unit></logCurveInfo>'
                f'<logCurveInfo uid="SPPA"><mnemonic>SPPA</mnemonic><unit>psi</unit></logCurveInfo>'
                f'<logCurveInfo uid="ROP"><mnemonic>ROP</mnemonic><unit>ft/h</unit></logCurveInfo>'
                f'<logData><mnemonicList>TIME,DBTM,HKLD,SPPA,ROP</mnemonicList><unitList>unitless,ft,klbf,psi,ft/h</unitList>{data}</logData></log></logs>')

    def start(self) -> 'TestStore':
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


PORT_REGISTRY['witsml'] = PortSpec(
    name='witsml', transport='WITSML 1.4.1 store (SOAP; GetVersion / GetCap / GetFromStore on a log object)',
    status='IMPLEMENTED_REQUIRES_SITE_CONFIG',
    reader=read_witsml,
    detail='READ-ONLY; credentials by environment-variable name; rows newer than the last row seen at each poll; time-indexed logs stamped '
           'from the index curve; null values -> GAP; recording + replay; in-package test store')
