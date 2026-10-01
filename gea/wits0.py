# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""wits0 - the WITS Level 0 port: the drill-floor feed from the mud logger or EDR.

WITS Level 0 is the plain-text rig-site transfer format most logging units
and electronic drilling recorders can emit: a frame is a line `&&`, then one
item per line as a four-digit code followed by the value (two-digit record
number + two-digit item number, e.g. `0112` = record 01 item 12, hookload),
then a line `!!`. The format carries no units: the site's map declares them.

    {
      "transport": "tcp" | "listen" | "serial" | "replay",
      "host": "192.168.10.5", "port": 5001,          tcp: connect to the sender
      "listen_port": 5001,                           listen: the sender connects to us
      "device": "COM3", "baudrate": 9600,            serial: pip install pyserial (extra `serial`)
      "items": [ {"code": "0112", "tag_id": "HKLD_klbf", "unit": "klbf", "tag_class": "hookload", "scale": 1.0}, ... ]
    }

Items not in the map are kept under their standard names (`wits_0112` with
the record-01 dictionary's name and class) so nothing on the wire is lost;
items in the map get the site's tag id, unit and scale. Timestamps come from
items 0105/0106 (date YYMMDD, time HHMMSS, site clock, treated as UTC unless
`clock_offset_h` says otherwise) when the frame carries them, else arrival;
the ingest timestamp is always arrival, so latency is measured. Sentinels
(-9999, -999.25, empty) become GAP with the rule named.

`simulate_server(port, ...)` is an in-process WITS0 sender that emits a
deterministic drilling sequence - the way a site tests its patch before the
rig is on line, and the way the acceptance suite tests this port with no
hardware. Every received frame can be written to a Recording and replayed.

Headless-safe: stdlib for tcp/listen/replay; pyserial only for `serial`.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .live_ports import TagMapping, load_mappings, make_record, records_to_stream, Recording, iso, utc_now
from .ports import LiveStream, PortSpec, PORT_REGISTRY
from .sample_record import SampleRecord

SERIAL_AVAILABLE = False
try:
    import serial as _serial                                  # noqa: F401
    SERIAL_AVAILABLE = True
except ImportError:
    _serial = None

# The record-01 (general time-based) item dictionary of WITS Level 0, as published
# by the industry (POSC/Energistics WITS specification, record 1). Names are the
# specification's; units are NOT part of the wire format and come from the map.
RECORD_01 = {
    '0101': ('well_id', 'identity'), '0102': ('sidetrack_hole', 'identity'), '0103': ('record_id', 'identity'),
    '0104': ('sequence', 'identity'), '0105': ('date', 'time'), '0106': ('time', 'time'), '0107': ('activity_code', 'state'),
    '0108': ('depth_bit_measured', 'depth'), '0109': ('depth_hole_measured', 'depth'), '0110': ('block_position', 'position'),
    '0111': ('rop_average', 'rate'), '0112': ('hookload_average', 'hookload'), '0113': ('hookload_maximum', 'hookload'),
    '0114': ('wob_average', 'weight_on_bit'), '0115': ('wob_maximum', 'weight_on_bit'), '0116': ('rpm_average', 'rotary_speed'),
    '0117': ('torque_average', 'torque'), '0118': ('torque_maximum', 'torque'), '0119': ('standpipe_pressure_average', 'pressure'),
    '0120': ('standpipe_pressure_maximum', 'pressure'), '0121': ('casing_pressure', 'pressure'), '0122': ('mud_flow_out_percent', 'flow'),
    '0123': ('mud_flow_out', 'flow'), '0124': ('mud_flow_in', 'flow'), '0125': ('mud_density_out', 'density'),
    '0126': ('mud_density_in', 'density'), '0127': ('mud_temperature_out', 'temperature'), '0128': ('mud_temperature_in', 'temperature'),
    '0129': ('mud_conductivity_out', 'conductivity'), '0130': ('mud_conductivity_in', 'conductivity'), '0131': ('pump_stroke_rate_1', 'pump'),
    '0132': ('pump_stroke_rate_2', 'pump'), '0133': ('pump_stroke_rate_3', 'pump'), '0134': ('tank_volume_active', 'volume'),
    '0135': ('tank_volume_change', 'volume'), '0136': ('total_mud_volume', 'volume'), '0137': ('gas_total', 'gas'),
    '0138': ('lag_strokes', 'pump'), '0139': ('lag_time', 'time'), '0140': ('total_pump_strokes', 'pump'),
}
SENTINELS = {'-9999', '-9999.0', '-9999.00', '-999.25', '-999.250', ''}

EXAMPLE_CONFIG = {
    'transport': 'tcp', 'host': '127.0.0.1', 'port': 5001, 'listen_port': 5001, 'device': 'COM3', 'baudrate': 9600,
    'clock_offset_h': 0.0, 'keep_unmapped': True,
    'items': [
        {'code': '0108', 'tag_id': 'DBTM_ft', 'unit': 'ft', 'tag_class': 'depth', 'scale': 1.0, 'description': 'bit depth, measured'},
        {'code': '0109', 'tag_id': 'DMEA_ft', 'unit': 'ft', 'tag_class': 'depth', 'scale': 1.0, 'description': 'hole depth, measured'},
        {'code': '0112', 'tag_id': 'HKLD_klbf', 'unit': 'klbf', 'tag_class': 'hookload', 'scale': 1.0, 'description': 'hookload, average'},
        {'code': '0114', 'tag_id': 'WOB_klbf', 'unit': 'klbf', 'tag_class': 'weight_on_bit', 'scale': 1.0, 'description': 'weight on bit, average'},
        {'code': '0116', 'tag_id': 'RPM', 'unit': 'rpm', 'tag_class': 'rotary_speed', 'scale': 1.0, 'description': 'rotary speed'},
        {'code': '0119', 'tag_id': 'SPP_psi', 'unit': 'psi', 'tag_class': 'pressure', 'scale': 1.0, 'description': 'standpipe pressure'},
        {'code': '0124', 'tag_id': 'FLWI_gpm', 'unit': 'gal/min', 'tag_class': 'flow', 'scale': 1.0, 'description': 'mud flow in'},
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
    d = json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith('_')}))   # never mutate the caller's dict
    t = d.get('transport', 'tcp')
    if t not in ('tcp', 'listen', 'serial', 'replay'):
        raise ValueError("wits0 config 'transport' must be tcp | listen | serial | replay")
    if t == 'tcp' and not (d.get('host') and d.get('port')):
        raise ValueError("wits0 tcp transport needs 'host' and 'port'")
    if t == 'listen' and not d.get('listen_port'):
        raise ValueError("wits0 listen transport needs 'listen_port'")
    if t == 'serial' and not d.get('device'):
        raise ValueError("wits0 serial transport needs 'device'")
    for it in d.get('items', []):
        code = str(it.get('code', ''))
        if not (len(code) == 4 and code.isdigit()):
            raise ValueError(f"wits0 item code must be four digits (record + item), got {code!r}")
    d['_mappings'] = load_mappings(d.get('items', []), 'code') if d.get('items') else {}
    if not d['_mappings'] and not d.get('keep_unmapped', True):
        raise ValueError('wits0: an empty item map with keep_unmapped false would record nothing')
    return d


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------
def encode_frame(items: Dict[str, object]) -> bytes:
    """{code: value} -> one WITS0 frame (CRLF line endings, as senders do)."""
    lines = ['&&'] + [f'{code}{"" if v is None else v}' for code, v in items.items()] + ['!!']
    return ('\r\n'.join(lines) + '\r\n').encode('ascii', 'replace')


class FrameParser:
    """Feed bytes in any chunking; get complete frames out as {code: text}."""

    def __init__(self):
        self.buf = b''
        self.in_frame = False
        self.cur: Dict[str, str] = {}

    def feed(self, data: bytes) -> List[Dict[str, str]]:
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\n')
            if i < 0:
                break
            line, self.buf = self.buf[:i], self.buf[i + 1:]
            s = line.decode('ascii', 'replace').strip()
            if s == '&&':
                self.in_frame, self.cur = True, {}
            elif s == '!!':
                if self.in_frame:
                    out.append(self.cur)
                self.in_frame, self.cur = False, {}
            elif self.in_frame and len(s) >= 4 and s[:4].isdigit():
                self.cur[s[:4]] = s[4:].strip()
        return out


def frame_time(frame: Dict[str, str], clock_offset_h: float = 0.0) -> Optional[datetime]:
    """Items 0105 (YYMMDD) and 0106 (HHMMSS) -> UTC datetime, or None."""
    d, t = frame.get('0105', ''), frame.get('0106', '')
    if len(d) == 6 and len(t) == 6 and d.isdigit() and t.isdigit():
        try:
            dt = datetime(2000 + int(d[:2]), int(d[2:4]), int(d[4:6]), int(t[:2]), int(t[2:4]), int(t[4:6]), tzinfo=timezone.utc)
            return dt - timedelta(hours=float(clock_offset_h or 0.0))
        except ValueError:
            return None
    return None


def frame_to_records(cfg: dict, frame: Dict[str, str], received_at: Optional[datetime] = None) -> List[SampleRecord]:
    """One frame -> records through the site's map (unmapped items kept under standard names when asked)."""
    received_at = received_at or utc_now()
    src = frame_time(frame, cfg.get('clock_offset_h', 0.0))
    out = []
    for code, text in frame.items():
        m = cfg['_mappings'].get(code)
        if m is None:
            if not cfg.get('keep_unmapped', True) or code in ('0101', '0102', '0103', '0104', '0105', '0106'):
                continue
            name, cls = RECORD_01.get(code, (f'item_{code}', 'generic'))
            if cls in ('identity', 'time', 'state'):
                continue
            m = TagMapping(address=code, tag_id=f'wits_{code}_{name}', unit='', tag_class=cls, description=f'WITS0 {code} {name} (unmapped; unit not declared)')
        if text in SENTINELS:
            out.append(make_record(m, None, src, 'GAP', f'WITS0 sentinel {text!r}', received_at))
            continue
        try:
            v = float(text)
        except ValueError:
            out.append(make_record(m, None, src, 'GAP', f'WITS0 non-numeric {text!r}', received_at))
            continue
        if math.isnan(v):
            out.append(make_record(m, None, src, 'GAP', 'WITS0 NaN', received_at))
            continue
        out.append(make_record(m, v, src, 'GOOD', '', received_at))
    return out


# ---------------------------------------------------------------------------
# The tap
# ---------------------------------------------------------------------------
class Wits0Tap:
    """Read WITS0 frames over TCP (connect or listen) or serial for a duration or until stopped."""

    def __init__(self, config, recording_path: Optional[str] = None):
        self.cfg = load_config(config)
        if self.cfg['transport'] == 'serial' and not SERIAL_AVAILABLE:
            raise NotImplementedError("wits0 serial transport requires the optional dependency: pip install pyserial "
                                      "(tcp and listen transports need nothing)")
        self.recording = Recording(recording_path)
        self.records: List[SampleRecord] = []
        self.frames = 0
        self.stop_event = threading.Event()

    def _open(self, timeout_s: float):
        t = self.cfg['transport']
        if t == 'tcp':
            s = socket.create_connection((self.cfg['host'], int(self.cfg['port'])), timeout=timeout_s)
            s.settimeout(1.0)
            return s
        if t == 'listen':
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.cfg.get('bind', '0.0.0.0'), int(self.cfg['listen_port'])))
            srv.listen(1)
            srv.settimeout(timeout_s)
            try:
                conn, _ = srv.accept()
            finally:
                srv.close()
            conn.settimeout(1.0)
            return conn
        return _serial.Serial(self.cfg['device'], int(self.cfg.get('baudrate', 9600)), timeout=1.0)

    def run(self, duration_s: Optional[float] = 10.0, on_record: Optional[Callable[[SampleRecord], None]] = None,
            timeout_s: float = 5.0) -> List[SampleRecord]:
        """Run for duration_s seconds (None: until stop()). Returns the records received."""
        try:
            conn = self._open(timeout_s)
        except (OSError, socket.timeout) as e:
            raise ConnectionError(f"wits0: could not open {self.cfg['transport']} source: {e}")
        parser = FrameParser()
        got: List[SampleRecord] = []
        t_end = None if duration_s is None else time.time() + float(duration_s)
        try:
            while not self.stop_event.is_set() and (t_end is None or time.time() < t_end):
                try:
                    data = conn.recv(4096) if hasattr(conn, 'recv') else conn.read(4096)
                except (socket.timeout, TimeoutError):
                    continue
                if not data and hasattr(conn, 'recv'):
                    if duration_s is None:                   # supervised: the caller reconnects
                        raise ConnectionError('wits0: the sender closed the connection')
                    break                                    # bounded run: the sender is done, so are we
                for frame in parser.feed(data):
                    recv = utc_now()
                    self.frames += 1
                    self.recording.write({'received_at': iso(recv), 'frame': frame})
                    for r in frame_to_records(self.cfg, frame, recv):
                        got.append(r)
                        if on_record:
                            on_record(r)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.records.extend(got)
        return got

    def stop(self) -> None:
        self.stop_event.set()

    def to_stream(self, name: str = 'wits0') -> LiveStream:
        return records_to_stream(self.records, name=name, source_format='wits0', meta={'transport': self.cfg['transport']})


def replay(config, recording_path: str) -> List[SampleRecord]:
    cfg = load_config(config)
    out = []
    for msg in Recording.read(recording_path):
        recv = datetime.fromisoformat(msg['received_at'].replace('Z', '+00:00'))
        out.extend(frame_to_records(cfg, msg['frame'], recv))
    return out


def read_wits0(config) -> LiveStream:
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    if d.get('replay'):
        return records_to_stream(replay(d, d['replay']), name='wits0-replay', source_format='wits0', meta={'replay': d['replay']})
    tap = Wits0Tap(d, recording_path=d.get('recording'))
    tap.run(float(d.get('duration_s', 10.0)))
    return tap.to_stream()


# ---------------------------------------------------------------------------
# The simulator: a WITS0 sender for testing a patch without a rig
# ---------------------------------------------------------------------------
def simulated_frame(k: int, t: datetime, seed: int = 1) -> Dict[str, object]:
    """A deterministic drilling sequence: drilling ahead with pumps on, a connection every 90 frames."""
    import random
    rng = random.Random(seed * 100003 + k)
    connection = (k % 90) >= 80
    depth = 9850.0 + 0.35 * k
    f = {'0101': 'SIM-1', '0103': '01', '0104': f'{k:06d}', '0105': t.strftime('%y%m%d'), '0106': t.strftime('%H%M%S'),
         '0107': '2' if not connection else '5',
         '0108': f'{(depth - (0.0 if not connection else 30.0)):.2f}', '0109': f'{depth:.2f}', '0110': f'{(75.0 - (k % 90) * 0.9):.2f}',
         '0111': f'{(0.0 if connection else 42.0 + rng.uniform(-3, 3)):.1f}', '0112': f'{(185.0 if connection else 162.0 + rng.uniform(-2, 2)):.1f}',
         '0114': f'{(0.0 if connection else 22.0 + rng.uniform(-1.5, 1.5)):.1f}', '0116': f'{(0 if connection else 120 + rng.randint(-2, 2)):d}',
         '0117': f'{(0.0 if connection else 9.8 + rng.uniform(-0.4, 0.4)):.2f}', '0119': f'{(0.0 if connection else 2850.0 + rng.uniform(-40, 40)):.0f}',
         '0124': f'{(0.0 if connection else 480.0 + rng.uniform(-5, 5)):.0f}', '0125': f'{10.4:.2f}', '0137': f'{(rng.uniform(5, 30)):.1f}'}
    if k % 37 == 0:
        f['0137'] = '-9999'                                   # a sentinel now and then, as real senders do
    return f


def simulate_server(port: int = 0, frames: int = 60, interval_s: float = 1.0, seed: int = 1, host: str = '127.0.0.1',
                    start_time: Optional[datetime] = None, verbose: bool = False) -> Tuple[threading.Thread, int, threading.Event]:
    """Listen once, then send `frames` frames `interval_s` apart to the first client. Returns (thread, port, stop)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    bound = srv.getsockname()[1]
    stop = threading.Event()
    t0 = start_time or utc_now()

    def _run():
        srv.settimeout(1.0)
        waited = 0
        conn = None
        while not stop.is_set():
            try:
                conn, peer = srv.accept()
                break
            except socket.timeout:
                waited += 1
                if verbose and waited % 15 == 0:
                    print(f'wits0-sim: waiting for a client on {host}:{bound} ({waited} s) - add a wits0 patch with transport tcp, host {host}, port {bound}', flush=True)
            except OSError:
                break
        srv.close()
        if conn is None:
            return
        if verbose:
            print(f'wits0-sim: client connected from {peer[0]}:{peer[1]}; sending {frames} frames {interval_s:g} s apart', flush=True)
        try:
            for k in range(frames):
                if stop.is_set():
                    break
                conn.sendall(encode_frame(simulated_frame(k, utc_now() if start_time is None else t0 + timedelta(seconds=k * interval_s), seed)))
                if verbose and (k + 1) % 20 == 0:
                    print(f'wits0-sim: {k + 1} frames sent', flush=True)
                if stop.wait(interval_s):
                    break
            if verbose:
                print('wits0-sim: done; closing the connection', flush=True)
        except OSError as e:
            if verbose:
                print(f'wits0-sim: the client went away ({e})', flush=True)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    th = threading.Thread(target=_run, name='wits0-sim', daemon=True)
    th.start()
    return th, bound, stop


def simulate_client(host: str, port: int, frames: int = 60, interval_s: float = 1.0, seed: int = 1) -> int:
    """Connect to a listening tap and push frames (the 'sender connects to us' arrangement)."""
    with socket.create_connection((host, port), timeout=10.0) as conn:
        t0 = utc_now()
        for k in range(frames):
            conn.sendall(encode_frame(simulated_frame(k, t0 + timedelta(seconds=k * interval_s), seed)))
            time.sleep(interval_s)
    return frames


def _refuse_serial(*a, **k):
    raise NotImplementedError("wits0 serial transport requires: pip install pyserial (tcp and listen need nothing)")


PORT_REGISTRY['wits0'] = PortSpec(
    name='wits0', transport='WITS Level 0 over TCP (connect or listen) or serial (pyserial extra)',
    status='IMPLEMENTED_REQUIRES_SITE_CONFIG',
    reader=read_wits0,
    detail='READ-ONLY; the site map declares tag ids and units (the wire carries none); unmapped record-01 items kept under '
           'standard names; sentinels -> GAP; frame date/time used as the source timestamp; recording + replay; in-package simulator')
