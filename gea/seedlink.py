"""seedlink - the seismic leg's live port: a station's records as the station writes them.

Everything the second leg has done so far ran on records that were already files - a TexNet day fetched over
FDSN, a Volve archive, a labelled synthetic scene. The stations themselves do not hand out files; they serve
SeedLink, the real-time protocol every data centre and most digitisers speak: a TCP session (port 18000) on
which the customer names its stations and channels, says where it left off, and the server pushes each
miniSEED record as it is written. This module is that customer, and a server to rehearse against.

Two versions of the protocol are on the air and this port speaks both, choosing by what the server offers:

* **SeedLink 3** (the one every SeisComP and ringserver still serves): ASCII commands ending in CR LF,
  answered `OK` or `ERROR`; `STATION STA NET`, `SELECT LLCCC.T`, `DATA [seq]` per station, `END` to start;
  every packet an 8-byte header - the letters `SL` and a six-digit hexadecimal sequence number - followed by
  a 512-byte miniSEED record. `INFO ID` is answered with a packet headed `SLINFO` (`*` in the last byte when
  more follow) carrying XML in a log record. The sequence number is per station and wraps at FFFFFF.
* **SeedLink 4.0** (FDSN, 2022-): `SLPROTO 4.0` first; station patterns `NET_STA`, stream patterns
  `LOC_B_S_SS`; `DATA [seq [start [end]]]` with a decimal sequence number and ISO 8601 times; failures as
  `ERROR <CODE> <text>`; every packet headed `SE`, a format byte (`2` miniSEED 2, `3` miniSEED 3, `J` JSON),
  a subformat byte, the payload length (uint32 LE), the sequence number (uint64 LE), the station id and the
  payload; `INFO` answered in JSON.

What the port writes is the record itself, byte for byte, appended to a day file per channel under the
station's `live/` folder - so what the leg later computes is computed from what the station sent, and the
file can be hashed and folded into the station's record list like any file a person brought. Beside it, a
state file: per station the last sequence number (so a reconnection resumes where it stopped, which is the
point of SeedLink), per channel the last sample time, the latency (arrival minus the record's last sample),
the records, the samples and the gaps between consecutive records.

What it will not do: invent samples across a gap (a gap is counted and left); give a record a time the
header does not carry; keep a session the server has ended (the reason is on the state); decode a payload
format it does not know (counted, with the format byte).
"""
from __future__ import annotations

import fnmatch
import json
import os
import socket
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic as S

DEFAULT_PORT = 18000
V3_RECLEN = 512
INFO_LEVELS = ('ID', 'CAPABILITIES', 'STATIONS', 'STREAMS', 'GAPS', 'CONNECTIONS', 'ALL')
V4_INFO_ITEMS = ('ID', 'FORMATS', 'CAPABILITIES', 'STATIONS', 'STREAMS', 'CONNECTIONS')
V4_ERROR_CODES = ('UNSUPPORTED', 'UNEXPECTED', 'UNAUTHORIZED', 'LIMIT', 'ARGUMENTS', 'AUTH', 'INTERNAL')

EXAMPLE_CONFIG = {
    'host': '127.0.0.1',
    'port': DEFAULT_PORT,
    'protocol': 'auto',                                  # 'auto' takes 4.0 when the server offers it, else 3; or '3' / '4'
    'streams': [{'network': 'XX', 'station': 'PAD3', 'selectors': ['HHZ.D', 'HHN.D', 'HHE.D']}],
    'timeout_s': 30.0,
    'keepalive_s': 30.0,                                 # INFO ID when nothing has arrived for this long
    'reconnect_s': [2, 5, 15, 60],
    'note': ('the example points at `gea seedlink-sim`; a TexNet station is served by EarthScope at rtserve.iris.washington.edu:18000 '
             '(network TX) - put that host and the station code here. Selectors are SeedLink 3 form LLCCC.T (location, channel, type; '
             '? for one character; parts may be omitted) and are converted for a 4.0 server.'),
}


def write_example_config(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE_CONFIG, f, indent=1)
    return path


def load_config(src) -> dict:
    """A dict or a JSON file; checked. `_streams` is the list normalised to (network, station, selectors)."""
    if isinstance(src, str):
        with open(src, encoding='utf-8') as f:
            cfg = json.load(f)
    else:
        cfg = json.loads(json.dumps(src))
    if not isinstance(cfg, dict):
        raise ValueError('seedlink config must be a JSON object')
    host = cfg.get('host')
    if not host or not isinstance(host, str):
        raise ValueError("seedlink config needs 'host'")
    port = cfg.get('port', DEFAULT_PORT)
    if not isinstance(port, int) or not (0 < port < 65536):
        raise ValueError("seedlink config 'port' must be an integer 1..65535")
    proto = str(cfg.get('protocol', 'auto')).lower()
    if proto not in ('auto', '3', '4'):
        raise ValueError("seedlink config 'protocol' must be 'auto', '3' or '4'")
    streams = cfg.get('streams')
    if not isinstance(streams, list) or not streams:
        raise ValueError("seedlink config needs 'streams': a non-empty list of {network, station, selectors}")
    norm = []
    for i, st in enumerate(streams):
        if not isinstance(st, dict) or not st.get('network') or not st.get('station'):
            raise ValueError(f"seedlink stream {i} needs 'network' and 'station'")
        net, sta = str(st['network']).strip().upper(), str(st['station']).strip().upper()
        if not (1 <= len(net) <= 2) or not (1 <= len(sta) <= 5) or '_' in net or '_' in sta:
            raise ValueError(f"seedlink stream {i}: network is 1-2 characters, station 1-5 (SEED codes), got {net!r} {sta!r}")
        sels = st.get('selectors') or []
        if not isinstance(sels, list) or not all(isinstance(x, str) and x for x in sels):
            raise ValueError(f"seedlink stream {i}: 'selectors' is a list of strings like 'HHZ.D'")
        for s_ in sels:
            parse_selector(s_)
        norm.append({'network': net, 'station': sta, 'selectors': [x.upper() for x in sels]})
    cfg['_streams'] = norm
    cfg['protocol'] = proto
    cfg['timeout_s'] = float(cfg.get('timeout_s', 30.0))
    cfg['keepalive_s'] = float(cfg.get('keepalive_s', 30.0))
    cfg['reconnect_s'] = [float(x) for x in (cfg.get('reconnect_s') or [2, 5, 15, 60])]
    return cfg


# ==============================================================================================================
# selectors
# ==============================================================================================================

def parse_selector(sel: str) -> Tuple[str, str, str]:
    """A SeedLink 3 selector LLCCC.T -> (location, channel, type), each '' when not given. 'HHZ.D' -> ('', 'HHZ', 'D');
    '00HHZ' -> ('00', 'HHZ', ''); '??Z' -> ('', '??Z', ''); a leading '!' is a negative selector and is refused here
    (this port names what it wants, it does not subtract)."""
    s = sel.strip().upper()
    if not s or s.startswith('!'):
        raise ValueError(f'selector {sel!r}: give the channels wanted, not a negative selector')
    typ = ''
    if '.' in s:
        s, typ = s.split('.', 1)
        if len(typ) != 1:
            raise ValueError(f'selector {sel!r}: the type after the dot is one letter (D data, E event, L log, T timing, C calibration, O opaque)')
    if len(s) == 5:
        loc, cha = s[:2], s[2:]
    elif len(s) == 3:
        loc, cha = '', s
    elif len(s) == 0:
        loc, cha = '', ''
    else:
        raise ValueError(f'selector {sel!r}: LLCCC.T - a 3-character channel, or 2-character location plus channel')
    return loc, cha, typ


def selector_v4(sel: str) -> str:
    """The SeedLink 3 selector as a 4.0 stream pattern LOC_B_S_SS[.FS]: 'HHZ.D' -> '*_H_H_Z.2D'; '00HHZ' -> '00_H_H_Z'."""
    loc, cha, typ = parse_selector(sel)
    loc_p = (loc.replace('?', '?') if loc else '*')
    if cha:
        parts = [c if c != '?' else '?' for c in cha]
        stream = f"{loc_p}_{parts[0]}_{parts[1]}_{parts[2]}"
    else:
        stream = f"{loc_p}_*_*_*"
    return stream + (f'.2{typ}' if typ else '')


def selector_matches(sel: str, location: str, channel: str, typ: str = 'D') -> bool:
    loc, cha, t = parse_selector(sel)
    if t and t != typ:
        return False
    if loc and not fnmatch.fnmatchcase(location.ljust(2), loc.ljust(2)):
        return False
    if cha and not fnmatch.fnmatchcase(channel, cha):
        return False
    return True


# ==============================================================================================================
# wire helpers
# ==============================================================================================================

class SeedLinkError(Exception):
    """An ERROR the server sent, with its code and text (4.0) or the command it refused (3)."""

    def __init__(self, command: str, code: str = '', text: str = ''):
        self.command, self.code, self.text = command, code, text
        super().__init__(f"{command!r} -> ERROR{(' ' + code) if code else ''}{(' ' + text) if text else ''}")


def parse_hello(line1: str) -> dict:
    """'SeedLink v4.0 (RingServer/2022.075) :: SLPROTO:3.1 SLPROTO:4.0 CAP TIME' -> version, implementation, protocols, capabilities."""
    out = {'software': line1.strip(), 'version': None, 'implementation': None, 'protocols': [], 'capabilities': []}
    head, _, caps = line1.partition('::')
    head = head.strip()
    if head.lower().startswith('seedlink v'):
        rest = head[10:].strip()
        ver, _, impl = rest.partition(' ')
        out['version'] = ver
        out['implementation'] = impl.strip('() ') or None
    for tok in caps.split():
        if tok.upper().startswith('SLPROTO:'):
            out['protocols'].append(tok.split(':', 1)[1])
        else:
            out['capabilities'].append(tok)
    if not out['protocols']:                                  # a SeedLink 3 server lists nothing: it speaks 3
        out['protocols'] = ['3.0']
    return out


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('the server closed the connection')
        buf.extend(chunk)
    return bytes(buf)


def _recv_line(sock: socket.socket, limit: int = 4096) -> str:
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError('the server closed the connection')
        if b == b'\n':
            break
        if b != b'\r':
            buf.extend(b)
        if len(buf) > limit:
            raise ValueError('a response line longer than 4096 bytes')
    return buf.decode('utf-8', 'replace')


def record_header(rec: bytes) -> dict:
    """What the 48-byte fixed header of a miniSEED 2 record says, without decoding the samples: codes, start, rate, nsamp."""
    trs = S.read_mseed(rec)
    if not trs:
        return {'id': None, 'start': None, 'end': None, 'rate': None, 'nsamp': 0, 'network': '', 'station': '', 'location': '', 'channel': ''}
    tr = trs[0]
    return {'id': tr.id, 'start': tr.starttime, 'end': tr.endtime, 'rate': tr.sample_rate, 'nsamp': tr.npts, 'network': tr.network,
            'station': tr.station, 'location': tr.location, 'channel': tr.channel, 'unit': tr.unit}


# ==============================================================================================================
# the client
# ==============================================================================================================

class SeedLinkClient:
    """One TCP session: HELLO, the handshake in 3 or 4.0, then packets. `read_packet()` returns one of
    {'kind': 'data', 'protocol', 'seq', 'station', 'format', 'payload'} / {'kind': 'info', 'more', 'payload', 'format'} /
    {'kind': 'end'}."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout_s: float = 30.0, useragent: str = ''):
        self.host, self.port, self.timeout_s = host, int(port), float(timeout_s)
        self.useragent = useragent
        self.sock: Optional[socket.socket] = None
        self.hello: dict = {}
        self.organization = ''
        self.protocol = '3'
        self.log: List[str] = []

    def connect(self) -> 'SeedLinkClient':
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        self.sock.settimeout(self.timeout_s)
        return self

    def close(self, bye: bool = True) -> None:
        if self.sock is not None:
            try:
                if bye:
                    self.sock.sendall(b'BYE\r\n')
            except OSError:
                pass
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, line: str) -> None:
        assert self.sock is not None
        self.log.append('> ' + line)
        self.sock.sendall(line.encode('ascii') + b'\r\n')

    def command(self, line: str) -> str:
        """A modifier command: sent, its one-line answer returned; ERROR raises SeedLinkError."""
        self._send(line)
        resp = _recv_line(self.sock)
        self.log.append('< ' + resp)
        if resp.startswith('ERROR'):
            parts = resp.split(' ', 2)
            raise SeedLinkError(line, parts[1] if len(parts) > 1 else '', parts[2] if len(parts) > 2 else '')
        if resp != 'OK':
            raise ValueError(f'unexpected answer to {line!r}: {resp!r}')
        return resp

    def say_hello(self) -> dict:
        self._send('HELLO')
        l1 = _recv_line(self.sock)
        l2 = _recv_line(self.sock)
        self.log += ['< ' + l1, '< ' + l2]
        self.hello = parse_hello(l1)
        self.organization = l2.strip()
        return self.hello

    def choose_protocol(self, wanted: str = 'auto') -> str:
        offered = self.hello.get('protocols') or ['3.0']
        has4 = any(p.startswith('4.') for p in offered)
        if wanted == '4' and not has4:
            raise ValueError(f"the server offers SeedLink {', '.join(offered)}, not 4.0")
        self.protocol = '4' if (has4 and wanted in ('auto', '4')) else '3'
        return self.protocol

    def handshake(self, streams: Sequence[dict], resume: Optional[Dict[str, int]] = None, protocol: str = 'auto') -> dict:
        """HELLO, the protocol, one STATION/SELECT/DATA group per stream, END. `resume` maps 'NET_STA' to the last
        sequence number received, so DATA asks for the next one. Returns what was negotiated."""
        if not self.hello:
            self.say_hello()
        self.choose_protocol(protocol)
        resume = resume or {}
        if self.protocol == '4':
            self.command('SLPROTO 4.0')
            if self.useragent:
                try:
                    self.command('USERAGENT ' + self.useragent)
                except (SeedLinkError, ValueError):
                    pass                                     # optional by the specification; a server may not know it
        for st in streams:
            key = f"{st['network']}_{st['station']}"
            if self.protocol == '4':
                self.command(f"STATION {key}")
                for sel in st.get('selectors') or []:
                    self.command('SELECT ' + selector_v4(sel))
                last = resume.get(key)
                self.command('DATA' + (f' {int(last) + 1}' if last is not None else ''))
            else:
                self.command(f"STATION {st['station']} {st['network']}")
                for sel in st.get('selectors') or []:
                    self.command('SELECT ' + sel)
                last = resume.get(key)
                self.command('DATA' + (f' {((int(last) + 1) % 0x1000000):06X}' if last is not None else ''))
        self._send('END')
        return {'protocol': self.protocol, 'server': self.hello, 'organization': self.organization, 'stations': [f"{s['network']}_{s['station']}" for s in streams]}

    def info(self, level: str = 'ID') -> None:
        """Ask for an INFO packet (allowed while streaming; the answer arrives among the data packets)."""
        self._send('INFO ' + level)

    def read_packet(self) -> dict:
        assert self.sock is not None
        head = _recv_exact(self.sock, 2)
        if head == b'SL':
            rest = _recv_exact(self.sock, 6)
            if rest[:4] == b'INFO':                           # 'SLINFO' + 2 bytes: '*' in the last when more follow
                more = rest[5:6] == b'*'
                payload = _recv_exact(self.sock, V3_RECLEN)
                return {'kind': 'info', 'protocol': '3', 'more': more, 'format': '2', 'payload': payload}
            try:
                seq = int(rest.decode('ascii'), 16)
            except ValueError:
                raise ValueError(f'a SeedLink 3 header with a sequence that is not hexadecimal: {rest!r}')
            payload = _recv_exact(self.sock, V3_RECLEN)
            return {'kind': 'data', 'protocol': '3', 'seq': seq, 'station': None, 'format': '2', 'subformat': 'D', 'payload': payload}
        if head == b'SE':
            fixed = _recv_exact(self.sock, 15)
            fmt, sub = chr(fixed[0]), chr(fixed[1])
            length, seq, sid_len = struct.unpack('<IQB', fixed[2:15])
            sid = _recv_exact(self.sock, sid_len).decode('ascii', 'replace') if sid_len else ''
            payload = _recv_exact(self.sock, length)
            if fmt == 'J':
                return {'kind': 'info', 'protocol': '4', 'more': False, 'format': 'J', 'subformat': sub, 'payload': payload, 'error': sub == 'E'}
            return {'kind': 'data', 'protocol': '4', 'seq': seq, 'station': sid, 'format': fmt, 'subformat': sub, 'payload': payload}
        if head == b'EN':
            tail = _recv_exact(self.sock, 1)
            if tail == b'D':
                return {'kind': 'end'}
        if head == b'ER':
            line = _recv_line(self.sock)
            raise SeedLinkError('stream', 'ERROR', line.strip())
        raise ValueError(f'not a SeedLink packet header: {head!r}')


# ==============================================================================================================
# the tap: records to day files, state beside them
# ==============================================================================================================

def _day_name(h: dict) -> str:
    t = datetime.fromtimestamp(h['start'], timezone.utc)
    return f"{h['network']}.{h['station']}.{h['location']}.{h['channel']}.{t.strftime('%Y.%j')}.mseed"


class SeedLinkTap:
    """Runs the session for a config, appends every record byte for byte to a day file per channel under `out_dir`,
    keeps the state (sequence numbers for resuming, last sample, latency, gaps) in `state_path` and reconnects
    with backoff. `run()` returns the state; `stop()` ends it."""

    def __init__(self, config, out_dir: str, state_path: Optional[str] = None, useragent: str = 'GEA-Program'):
        self.cfg = load_config(config)
        self.out_dir = out_dir
        self.state_path = state_path or os.path.join(out_dir, 'seedlink_state.json')
        self.useragent = useragent
        self.stop_event = threading.Event()
        self.client: Optional[SeedLinkClient] = None
        self.state = self._load_state()
        self._files: Dict[str, object] = {}
        self._lat: List[float] = []
        self._last_end: Dict[str, Tuple[float, float]] = {}   # channel -> (end time, rate) of the last record

    # -- state -----------------------------------------------------------------------------------------------
    def _load_state(self) -> dict:
        st = {'status': 'IDLE', 'host': self.cfg['host'], 'port': self.cfg['port'], 'protocol': None, 'server': None, 'organization': None,
              'stations': {f"{s['network']}_{s['station']}": {'seq': None, 'records': 0} for s in self.cfg['_streams']},
              'channels': {}, 'records': 0, 'bytes': 0, 'info_packets': 0, 'unknown_payloads': 0, 'reconnects': 0, 'last_packet_utc': None,
              'latency_p50_s': None, 'latency_p95_s': None, 'last_error': None, 'started_utc': None}
        if os.path.isfile(self.state_path):
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    old = json.load(f)
                for k, v in (old.get('stations') or {}).items():
                    if k in st['stations']:
                        st['stations'][k]['seq'] = v.get('seq')
                        st['stations'][k]['records'] = int(v.get('records') or 0)
                st['channels'] = old.get('channels') or {}
                st['records'] = int(old.get('records') or 0)
                st['bytes'] = int(old.get('bytes') or 0)
            except (OSError, ValueError):
                pass
        return st

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or '.', exist_ok=True)
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_path)

    def resume_seqs(self) -> Dict[str, int]:
        return {k: v['seq'] for k, v in self.state['stations'].items() if v.get('seq') is not None}

    # -- one record ------------------------------------------------------------------------------------------
    def _file_for(self, h: dict):
        name = _day_name(h)
        f = self._files.get(name)
        if f is None:
            os.makedirs(self.out_dir, exist_ok=True)
            f = open(os.path.join(self.out_dir, name), 'ab')
            self._files[name] = f
        return f, name

    def _take(self, pkt: dict, arrival: float, on_record: Optional[Callable[[dict], None]]) -> Optional[dict]:
        if pkt.get('format') not in ('2',):
            self.state['unknown_payloads'] += 1
            self.state['last_error'] = f"payload format {pkt.get('format')!r} (not miniSEED 2) left undecoded"
            return None
        rec = pkt['payload']
        try:
            h = record_header(rec)
        except ValueError as e:
            self.state['unknown_payloads'] += 1
            self.state['last_error'] = f'a record the reader could not parse: {e}'
            return None
        key = f"{h['network']}_{h['station']}"
        st = self.state['stations'].setdefault(key, {'seq': None, 'records': 0})
        st['seq'] = int(pkt['seq'])
        st['records'] += 1
        f, name = self._file_for(h)
        f.write(rec)
        f.flush()
        ch = self.state['channels'].setdefault(h['id'], {'records': 0, 'samples': 0, 'gaps': 0, 'overlaps': 0, 'rate_hz': h['rate'],
                                                         'last_sample_utc': None, 'latency_s': None, 'file': name, 'unit': h.get('unit', 'counts')})
        gap = None
        if h['nsamp'] and h['rate']:
            prev = self._last_end.get(h['id'])
            if prev is not None:
                expected = prev[0] + 1.0 / h['rate']
                d = h['start'] - expected
                if d > 0.5 / h['rate']:
                    ch['gaps'] += 1
                    gap = {'channel': h['id'], 'gap_s': round(d, 4), 'at_utc': S.iso(expected)}
                elif d < -0.5 / h['rate']:
                    ch['overlaps'] += 1
                    gap = {'channel': h['id'], 'overlap_s': round(-d, 4), 'at_utc': S.iso(h['start'])}
            self._last_end[h['id']] = (h['end'], h['rate'])
            ch['last_sample_utc'] = S.iso(h['end'])
            ch['latency_s'] = round(arrival - h['end'], 3)
            self._lat.append(arrival - h['end'])
            if len(self._lat) > 4000:
                del self._lat[:-2000]
            self._latency_stats()                           # per record: the card must not show '-' for the first twenty
        ch['records'] += 1
        ch['samples'] += int(h['nsamp'])
        ch['file'] = name
        self.state['records'] += 1
        self.state['bytes'] += len(rec)
        self.state['last_packet_utc'] = S.iso(arrival)
        out = {'id': h['id'], 'seq': int(pkt['seq']), 'start': h['start'], 'end': h['end'], 'nsamp': h['nsamp'], 'rate': h['rate'], 'file': name, 'gap': gap,
               'latency_s': ch['latency_s']}
        if on_record:
            on_record(out)
        return out

    def _latency_stats(self) -> None:
        if self._lat:
            a = np.asarray(self._lat[-2000:])
            self.state['latency_p50_s'] = round(float(np.percentile(a, 50)), 3)
            self.state['latency_p95_s'] = round(float(np.percentile(a, 95)), 3)

    # -- the session -----------------------------------------------------------------------------------------
    def connect(self) -> dict:
        cl = SeedLinkClient(self.cfg['host'], self.cfg['port'], self.cfg['timeout_s'], useragent=self.useragent + '/' + _version())
        cl.connect()
        neg = cl.handshake(self.cfg['_streams'], self.resume_seqs(), self.cfg['protocol'])
        self.client = cl
        self.state['handshake'] = list(cl.log)
        self.state.update({'status': 'CONNECTED', 'protocol': neg['protocol'], 'server': neg['server'].get('software'), 'organization': neg['organization'],
                           'started_utc': self.state.get('started_utc') or S.iso(time.time()), 'last_error': None})
        return neg

    def run(self, duration_s: Optional[float] = None, on_record: Optional[Callable[[dict], None]] = None, max_records: Optional[int] = None) -> dict:
        t_end = None if duration_s is None else time.time() + float(duration_s)
        backoff_i = 0
        got = 0
        try:
            while not self.stop_event.is_set() and (t_end is None or time.time() < t_end):
                try:
                    if self.client is None:
                        self.connect()
                        backoff_i = 0
                        self._save_state()
                    self.client.sock.settimeout(1.0)
                    last_rx = time.time()
                    keepalive_sent = None
                    while not self.stop_event.is_set() and (t_end is None or time.time() < t_end):
                        try:
                            pkt = self.client.read_packet()
                        except socket.timeout:
                            idle = time.time() - last_rx
                            if keepalive_sent is None and idle >= self.cfg['keepalive_s']:
                                self.client.info('ID')
                                keepalive_sent = time.time()
                            elif keepalive_sent is not None and time.time() - keepalive_sent > self.cfg['timeout_s']:
                                raise ConnectionError(f'no answer to INFO ID for {self.cfg["timeout_s"]:.0f} s')
                            continue
                        last_rx = time.time()
                        keepalive_sent = None
                        if pkt['kind'] == 'end':
                            self.state['status'] = 'ENDED'
                            self.state['last_error'] = 'the server sent END (the stream or time window is over)'
                            self._save_state()
                            return self.state
                        if pkt['kind'] == 'info':
                            self.state['info_packets'] += 1
                            if pkt.get('error'):
                                self.state['last_error'] = 'INFO answered with an error: ' + pkt['payload'][:200].decode('utf-8', 'replace')
                            continue
                        r = self._take(pkt, last_rx, on_record)
                        if r is not None:
                            got += 1
                            if got % 20 == 0:
                                self._latency_stats()
                                self._save_state()
                            if max_records is not None and got >= max_records:
                                self.state['status'] = 'STOPPED'
                                self._latency_stats()
                                self._save_state()
                                return self.state
                except (ConnectionError, OSError, ValueError, SeedLinkError) as e:
                    if isinstance(e, SeedLinkError) and e.command.startswith('STATION'):
                        # the server does not have the station: no amount of reconnecting changes that
                        self.state.update({'status': 'REFUSED', 'last_error': str(e)})
                        self._close()
                        self._save_state()
                        return self.state
                    self.state['last_error'] = str(e)
                    self._close()
                    if self.stop_event.is_set() or (t_end is not None and time.time() >= t_end):
                        break
                    self.state['status'] = 'RECONNECTING'
                    self.state['reconnects'] += 1
                    self._save_state()
                    wait = self.cfg['reconnect_s'][min(backoff_i, len(self.cfg['reconnect_s']) - 1)]
                    backoff_i += 1
                    if t_end is not None:
                        wait = min(wait, max(0.0, t_end - time.time()))
                    self.stop_event.wait(wait)
        finally:
            self._close()
            for f in self._files.values():
                try:
                    f.close()
                except OSError:
                    pass
            self._files.clear()
            if self.state['status'] not in ('ENDED', 'REFUSED'):
                self.state['status'] = 'STOPPED'
            self._latency_stats()
            self._save_state()
        return self.state

    def _close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None

    def stop(self) -> None:
        self.stop_event.set()


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:                                        # pragma: no cover
        return '0'


# ==============================================================================================================
# a server to rehearse against: SeedLink 3 and 4.0 on the loopback, a synthetic station in INT32 records
# ==============================================================================================================

def _info_xml(name: str, started: str, stations: Sequence[Tuple[str, str]]) -> bytes:
    body = f'<?xml version="1.0"?><seedlink software="{name}" organization="GEA" started="{started}">'
    for net, sta in stations:
        body += f'<station name="{sta}" network="{net}" description="simulated" begin_seq="000000" end_seq="000000" stream_check="enabled"/>'
    return (body + '</seedlink>').encode('ascii')


def _log_record(text: bytes, seq: int = 1) -> bytes:
    """A 512-byte miniSEED log record (channel LOG, ASCII encoding 0) carrying `text`, as SeedLink 3 INFO packets are."""
    text = text[:V3_RECLEN - 64]
    year, doy, hh, mm, ss, frac = S._epoch_to_btime(time.time())
    hdr = (f"{seq % 1000000:06d}".encode() + b'D ' + b'INFO '.ljust(5) + b'  ' + b'LOG' + b'SL'
           + struct.pack('>HHBBBBH', year, doy, hh, mm, ss, 0, frac) + struct.pack('>Hhh', len(text), 0, 0)
           + struct.pack('BBBB', 0, 0, 0, 1) + struct.pack('>iHH', 0, 64, 48))
    b1000 = struct.pack('>HHBBBB', 1000, 0, 0, 1, 9, 0)
    return (hdr + b1000).ljust(64, b'\x00') + text.ljust(V3_RECLEN - 64, b'\x00')


class SimulatedStation:
    """A synthetic three-component station: a slow drift plus a line at `line_hz` plus noise, INT32 counts at `rate`,
    emitted as 512-byte records (112 samples each) by a producer thread; a ring of records per station for resuming."""

    def __init__(self, network: str, station: str, channels: Sequence[str], rate: float, seed: int, line_hz: float = 12.5, keep: int = 4000):
        self.network, self.station, self.channels, self.rate = network.upper(), station.upper(), [c.upper() for c in channels], float(rate)
        self.rng = np.random.default_rng(seed)
        self.line_hz = line_hz
        self.seq = 0
        self.ring: deque = deque(maxlen=keep)                   # (seq, record bytes, channel)
        self.t0 = time.time()
        self.k = 0                                              # samples produced so far per channel
        self.pending: Dict[str, List[int]] = {c: [] for c in self.channels}
        self.cond = threading.Condition()
        self.records_served = 0

    @property
    def key(self) -> str:
        return f"{self.network}_{self.station}"

    def produce(self, n: int) -> List[Tuple[int, bytes, str]]:
        """n more samples per channel; records completed by them, appended to the ring and returned."""
        out = []
        t = self.t0 + (self.k + np.arange(n)) / self.rate
        for i, ch in enumerate(self.channels):
            x = 400.0 * np.sin(2 * np.pi * self.line_hz * t + i) + 30.0 * np.sin(2 * np.pi * 0.05 * t) + self.rng.normal(0, 25.0, n)
            self.pending[ch].extend(int(v) for v in np.round(x))
        self.k += n
        per = (V3_RECLEN - 64) // 4
        with self.cond:
            for ch in self.channels:
                buf = self.pending[ch]
                while len(buf) >= per:
                    chunk, self.pending[ch] = buf[:per], buf[per:]
                    buf = self.pending[ch]
                    start = self.t0 + (self.k - len(chunk) - len(buf)) / self.rate
                    tr = S.Trace(self.network, self.station, '', ch, start, self.rate, np.asarray(chunk, dtype=np.int64))
                    rec = S.build_records([tr], 'INT32', V3_RECLEN, first_seq=self.seq + 1)[0]
                    self.seq = (self.seq + 1) % 0x1000000
                    self.ring.append((self.seq, rec, ch))
                    out.append((self.seq, rec, ch))
            if out:
                self.cond.notify_all()
        return out


class SimulatedServer(threading.Thread):
    """A SeedLink server on the loopback speaking 3 and 4.0 for the stations given; `stop()` ends it."""

    def __init__(self, port: int = 0, host: str = '127.0.0.1', stations: Optional[Sequence[SimulatedStation]] = None, interval_s: float = 0.25,
                 protocols: Sequence[str] = ('3.1', '4.0'), name: str = 'GEA simulated SeedLink', verbose: bool = False):
        super().__init__(daemon=True)
        self.stations = list(stations or [SimulatedStation('XX', 'PAD3', ['HHZ', 'HHN', 'HHE'], 100.0, 7)])
        self.by_key = {s.key: s for s in self.stations}
        self.interval_s = float(interval_s)
        self.protocols = list(protocols)
        self.name_ = name
        self.verbose = verbose
        self.stop_event = threading.Event()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.port = self.srv.getsockname()[1]
        self.host = host
        self.started = S.iso(time.time())
        self.stats = {'connections': 0, 'records_sent': 0, 'commands': 0, 'errors': 0, 'info': 0, 'protocol_sessions': {'3': 0, '4': 0}}
        self.clients: List[threading.Thread] = []
        self.conns: List[socket.socket] = []
        self.dropped = 0

    # -- producing ----------------------------------------------------------------------------------------------
    def _producer(self) -> None:
        last = time.time()
        while not self.stop_event.is_set():
            self.stop_event.wait(self.interval_s)
            now = time.time()
            for st in self.stations:
                n = int(round((now - last) * st.rate))
                if n > 0:
                    st.produce(n)
            last = now

    def run(self) -> None:
        prod = threading.Thread(target=self._producer, daemon=True)
        prod.start()
        try:
            while not self.stop_event.is_set():
                try:
                    conn, addr = self.srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.stats['connections'] += 1
                self.conns.append(conn)
                t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
                t.start()
                self.clients.append(t)
        finally:
            try:
                self.srv.close()
            except OSError:
                pass

    def drop_clients(self) -> int:
        """Close every client connection (the link went down; the server and its ring stay) - what a customer must
        survive by reconnecting and resuming from its last sequence number."""
        n = 0
        for c in list(self.conns):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
            n += 1
        self.conns.clear()
        self.dropped += n
        return n

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.srv.close()
        except OSError:
            pass
        self.drop_clients()
        for st in self.stations:
            with st.cond:
                st.cond.notify_all()

    # -- one client ---------------------------------------------------------------------------------------------
    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        proto = '3'
        sel: Dict[str, List[str]] = {}                         # station key -> selectors (v3 form or v4 pattern)
        start: Dict[str, Optional[int]] = {}                   # station key -> first seq wanted (None: next)
        current: Optional[str] = None
        streaming = False
        buf = bytearray()
        sent: Dict[str, int] = {}

        def send(b: bytes) -> None:
            conn.sendall(b)

        def reply(ok: bool, code: str = '', text: str = '') -> None:
            if ok:
                send(b'OK\r\n')
            else:
                self.stats['errors'] += 1
                send((f'ERROR {code} {text}'.rstrip() if proto == '4' else 'ERROR').encode() + b'\r\n')

        def info_packet(item: str) -> None:
            self.stats['info'] += 1
            if proto == '4':
                body = json.dumps({'software': self.name_, 'organization': 'GEA', 'started': self.started,
                                   **({'stations': [{'id': k, 'description': 'simulated'} for k in self.by_key]} if item == 'STATIONS' else {}),
                                   **({'formats': {'2': {'mimetype': 'application/vnd.fdsn.mseed'}}} if item == 'FORMATS' else {})}).encode()
                sub = b'I' if item in V4_INFO_ITEMS else b'E'
                if sub == b'E':
                    body = json.dumps({'code': 'ARGUMENTS', 'message': f'unknown INFO item {item}'}).encode()
                send(b'SE' + b'J' + sub + struct.pack('<IQB', len(body), 0, 0) + body)
            else:
                xml = _info_xml(self.name_, self.started, [(s.network, s.station) for s in self.stations])
                send(b'SLINFO  ' + _log_record(xml))

        def matches(key: str, ch: str) -> bool:
            sels = sel.get(key) or []
            if not sels:
                return True
            if proto == '4':                                   # LOC_B_S_SS[.FS] against location '' and the channel's three letters
                for p in sels:
                    parts = p.split('.')[0].split('_')
                    if len(parts) == 4 and fnmatch.fnmatchcase('', parts[0].replace('*', '')) is not None \
                            and (parts[0] in ('*', '', '--') or fnmatch.fnmatchcase('', parts[0])) \
                            and all(fnmatch.fnmatchcase(ch[i], parts[i + 1]) for i in range(3)):
                        return True
                return False
            return any(selector_matches(s_, '', ch) for s_ in sels)

        try:
            while not self.stop_event.is_set():
                # -- commands (before and during streaming) --
                try:
                    chunk = conn.recv(1024)
                    if not chunk:
                        return
                    buf.extend(chunk)
                except socket.timeout:
                    chunk = b''
                while b'\n' in buf or b'\r' in buf:
                    i = min([j for j in (buf.find(b'\n'), buf.find(b'\r')) if j >= 0])
                    line = bytes(buf[:i]).decode('ascii', 'replace').strip()
                    del buf[:i + 1]
                    if not line:
                        continue
                    self.stats['commands'] += 1
                    parts = line.split()
                    cmd, args = parts[0].upper(), parts[1:]
                    if self.verbose:
                        print(f'[seedlink-sim] {cmd} {" ".join(args)}')
                    if cmd == 'HELLO':
                        caps = ' '.join(f'SLPROTO:{p}' for p in self.protocols) + ' CAP EXTREPLY'
                        send(f'SeedLink v4.0 ({self.name_}) :: {caps}\r\n'.encode() + b'GEA - Pad 3 simulated station\r\n')
                    elif cmd == 'SLPROTO':
                        if args and args[0] in self.protocols and args[0].startswith('4'):
                            proto = '4'
                            reply(True)
                        else:
                            reply(False, 'UNSUPPORTED', f'protocol {" ".join(args)} not offered')
                    elif cmd == 'USERAGENT':
                        reply(True)
                    elif cmd == 'CAT':
                        send(''.join(f'{s.network} {s.station} simulated\r\n' for s in self.stations).encode() + b'END\r\n')
                    elif cmd == 'STATION':
                        if proto == '4':
                            pat = args[0] if args else ''
                            keys = [k for k in self.by_key if fnmatch.fnmatchcase(k, pat)]
                            if not keys:
                                reply(False, 'ARGUMENTS', f'no station matches {pat}')
                                continue
                            current = keys[0]
                            for k in keys:
                                sel.setdefault(k, []); start.setdefault(k, None)
                        else:
                            sta = args[0].upper() if args else ''
                            net = args[1].upper() if len(args) > 1 else None
                            keys = [k for k, s in self.by_key.items() if s.station == sta and (net is None or s.network == net)]
                            if not keys:
                                reply(False)
                                continue
                            current = keys[0]
                            sel.setdefault(current, []); start.setdefault(current, None)
                        reply(True)
                    elif cmd == 'SELECT':
                        if current is None:
                            reply(False, 'UNEXPECTED', 'SELECT before STATION')
                            continue
                        if args:
                            sel[current].append(args[0].upper())
                        else:
                            sel[current] = []
                        reply(True)
                    elif cmd == 'DATA':
                        if current is None:
                            reply(False, 'UNEXPECTED', 'DATA before STATION')
                            continue
                        if args:
                            try:
                                start[current] = 0 if args[0].upper() == 'ALL' else int(args[0], 10 if proto == '4' else 16)
                            except ValueError:
                                reply(False, 'ARGUMENTS', 'bad sequence number')
                                continue
                        reply(True)
                    elif cmd == 'END':
                        streaming = True
                        self.stats['protocol_sessions'][proto] += 1
                        for k in sel:
                            st = self.by_key[k]
                            sent[k] = (start[k] - 1) if start.get(k) is not None else st.seq
                    elif cmd == 'INFO':
                        info_packet(args[0].upper() if args else 'ID')
                    elif cmd == 'BYE':
                        return
                    elif cmd in ('FETCH', 'TIME', 'ENDFETCH'):
                        reply(False, 'UNSUPPORTED', f'{cmd} is not served by the simulator (real-time mode only)')
                    else:
                        reply(False, 'UNSUPPORTED', f'unknown command {cmd}')
                # -- streaming --
                if streaming:
                    for k in list(sel):
                        st = self.by_key[k]
                        with st.cond:
                            todo = [(q, r, c) for (q, r, c) in st.ring if q > sent[k]]
                        for q, r, c in todo:
                            if matches(k, c):
                                if proto == '4':
                                    sid = k.encode()
                                    send(b'SE' + b'2D' + struct.pack('<IQB', len(r), q, len(sid)) + sid + r)
                                else:
                                    send(f'SL{q:06X}'.encode() + r)
                                self.stats['records_sent'] += 1
                                st.records_served += 1
                            sent[k] = q
                    if not chunk:
                        time.sleep(0.05)
        except (ConnectionError, OSError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass


def simulate_server(port: int = 0, host: str = '127.0.0.1', network: str = 'XX', station: str = 'PAD3', channels: Sequence[str] = ('HHZ', 'HHN', 'HHE'),
                    rate: float = 100.0, seed: int = 7, interval_s: float = 0.25, protocols: Sequence[str] = ('3.1', '4.0'),
                    verbose: bool = False) -> SimulatedServer:
    srv = SimulatedServer(port, host, [SimulatedStation(network, station, channels, rate, seed)], interval_s, protocols, verbose=verbose)
    srv.start()
    return srv


# ==============================================================================================================
# the self-test
# ==============================================================================================================

def selftest(seconds: float = 8.0, tmp: Optional[str] = None) -> dict:
    """Both protocols against the simulator on the loopback: the handshake, records into day files, the state,
    a resume from the last sequence number with no record twice, INFO answered, a station the server does not
    have refused and named, a selector honoured."""
    import tempfile
    base = tmp or tempfile.mkdtemp(prefix='gea_seedlink_')
    srv = simulate_server(interval_s=0.1, rate=250.0)                      # 0.45 s per 112-sample record, so a few seconds carry several
    checks: Dict[str, bool] = {}
    detail: Dict[str, object] = {}
    try:
        time.sleep(1.5)                                        # let the station write a few records first
        # -- SeedLink 3: Z only ------------------------------------------------------------------------------
        cfg3 = dict(EXAMPLE_CONFIG, host='127.0.0.1', port=srv.port, protocol='3', streams=[{'network': 'XX', 'station': 'PAD3', 'selectors': ['HHZ.D']}])
        tap3 = SeedLinkTap(cfg3, os.path.join(base, 'v3'))
        st3 = tap3.run(duration_s=seconds, max_records=6)
        ids3 = set(st3['channels'])
        checks['v3_handshake'] = st3['protocol'] == '3' and st3['records'] >= 6 and st3['status'] == 'STOPPED'
        checks['v3_selector_honoured'] = ids3 == {'XX.PAD3..HHZ'}
        files3 = os.listdir(os.path.join(base, 'v3'))
        day = [f for f in files3 if f.endswith('.mseed')]
        checks['v3_day_file'] = len(day) == 1 and os.path.getsize(os.path.join(base, 'v3', day[0])) == st3['records'] * V3_RECLEN
        trs = S.read_mseed(os.path.join(base, 'v3', day[0]))
        checks['v3_file_reads_whole'] = len(trs) == 1 and trs[0].npts == st3['channels']['XX.PAD3..HHZ']['samples'] and not trs[0].gaps
        checks['v3_latency_measured'] = st3['latency_p95_s'] is not None and 0 <= st3['latency_p95_s'] < 3.0
        seq_a = st3['stations']['XX_PAD3']['seq']
        # resume: a second run from the saved state asks DATA seq+1 and the file grows with no record twice
        tap3b = SeedLinkTap(cfg3, os.path.join(base, 'v3'))
        checks['v3_state_reloaded'] = tap3b.resume_seqs() == {'XX_PAD3': seq_a}
        st3b = tap3b.run(duration_s=seconds, max_records=4)
        trs2 = S.read_mseed(os.path.join(base, 'v3', day[0]))
        checks['v3_resume_no_duplicate'] = (st3b['records'] == st3['records'] + 4 and len(trs2) == 1 and not trs2[0].gaps
                                            and trs2[0].npts == trs[0].npts + 4 * 112 and st3b['stations']['XX_PAD3']['seq'] > seq_a)
        checks['v3_data_command_hex'] = any(l.startswith('> DATA ') and l.split()[2] == f'{(seq_a + 1) % 0x1000000:06X}' for l in st3b.get('handshake') or [])
        # -- SeedLink 4.0: all three, resume by decimal sequence ---------------------------------------------
        cfg4 = dict(EXAMPLE_CONFIG, host='127.0.0.1', port=srv.port, protocol='auto', streams=[{'network': 'XX', 'station': 'PAD3', 'selectors': ['HH?.D']}])
        tap4 = SeedLinkTap(cfg4, os.path.join(base, 'v4'))
        st4 = tap4.run(duration_s=seconds, max_records=9)
        checks['v4_negotiated'] = st4['protocol'] == '4' and st4['records'] >= 9
        checks['v4_three_channels'] = set(st4['channels']) == {'XX.PAD3..HHZ', 'XX.PAD3..HHN', 'XX.PAD3..HHE'}
        checks['v4_station_id_carried'] = st4['stations']['XX_PAD3']['records'] >= 9
        # -- INFO answered (both) ----------------------------------------------------------------------------
        cl = SeedLinkClient('127.0.0.1', srv.port, 5.0).connect()
        cl.say_hello()
        cl.info('ID')
        p3 = cl.read_packet()
        checks['v3_info_packet'] = p3['kind'] == 'info' and p3['protocol'] == '3' and not p3['more'] and b'<seedlink' in p3['payload']
        cl.close()
        cl4 = SeedLinkClient('127.0.0.1', srv.port, 5.0).connect()
        cl4.say_hello(); cl4.choose_protocol('4'); cl4.command('SLPROTO 4.0')
        cl4.info('STATIONS')
        p4 = cl4.read_packet()
        j = json.loads(p4['payload'])
        checks['v4_info_json'] = p4['kind'] == 'info' and p4['format'] == 'J' and not p4.get('error') and j['stations'][0]['id'] == 'XX_PAD3'
        cl4.close()
        # -- a station the server does not have: refused and named, no reconnect loop ------------------------
        cfgx = dict(cfg3, streams=[{'network': 'TX', 'station': 'NOPE', 'selectors': []}])
        tapx = SeedLinkTap(cfgx, os.path.join(base, 'x'))
        stx = tapx.run(duration_s=seconds)
        checks['unknown_station_refused'] = stx['status'] == 'REFUSED' and 'STATION NOPE TX' in (stx['last_error'] or '') and stx['reconnects'] == 0
        # -- hello parsing -----------------------------------------------------------------------------------
        h = parse_hello('SeedLink v4.0 (RingServer/2022.075) :: SLPROTO:3.1 SLPROTO:4.0 CAP EXTREPLY NSWILDCARD BATCH WS:13')
        h3 = parse_hello('SeedLink v3.3 (2020.122)')
        checks['hello_parsed'] = h['protocols'] == ['3.1', '4.0'] and h['implementation'] == 'RingServer/2022.075' and 'BATCH' in h['capabilities'] and h3['protocols'] == ['3.0'] and h3['version'] == '3.3' and h3['implementation'] == '2020.122'
        checks['selector_v4_form'] = selector_v4('HHZ.D') == '*_H_H_Z.2D' and selector_v4('00HHZ') == '00_H_H_Z' and selector_v4('??Z') == '*_?_?_Z'
        detail = {'v3': {k: st3[k] for k in ('protocol', 'records', 'latency_p95_s', 'status')}, 'v3_resume': {'records': st3b['records'], 'seq': st3b['stations']['XX_PAD3']['seq']},
                  'v4': {k: st4[k] for k in ('protocol', 'records', 'status')}, 'channels_v4': sorted(st4['channels']), 'server': dict(srv.stats),
                  'unknown_station': stx['last_error']}
    finally:
        srv.stop()
    return {'label': 'SIMULATION_SELF_TEST', 'status': 'OK' if all(checks.values()) else 'FAIL', 'checks': checks, 'detail': detail, 'dir': base}


# ==============================================================================================================
# the live stations of a workspace: one tap thread each, kept up by the serving process
# ==============================================================================================================

class LiveStations:
    """Runs the SeedLink tap for each live station of a workspace in its own thread, writing into the station's
    live/ folder with the state beside it; `start_enabled()` is what a serving process calls at start."""

    def __init__(self, workspace):
        self.ws = workspace
        self._runs: Dict[str, Tuple[threading.Thread, SeedLinkTap]] = {}
        self._lock = threading.Lock()

    def _live_ids(self) -> List[str]:
        return [st['id'] for st in self.ws.seismic_stations() if st.get('live')]

    def start(self, station_id: str, actor: str = 'system') -> dict:
        with self._lock:
            run = self._runs.get(station_id)
            if run and run[0].is_alive():
                return self.state(station_id)
            cfg = self.ws.live_station_config(station_id)
            d = self.ws.live_dir(station_id)
            tap = SeedLinkTap(cfg, d, os.path.join(d, 'seedlink_state.json'))
            th = threading.Thread(target=tap.run, kwargs={'duration_s': None}, daemon=True, name=f'seedlink-{station_id}')
            th.start()
            self._runs[station_id] = (th, tap)
        self.ws.set_live_enabled(station_id, True, actor)
        self.ws.audit(actor, 'seismic.live.start', {'id': station_id, 'host': cfg['host'], 'port': cfg['port']})
        return self.state(station_id)

    def stop(self, station_id: str, actor: str = 'system', disable: bool = True) -> dict:
        with self._lock:
            run = self._runs.pop(station_id, None)
        if run:
            run[1].stop()
            run[0].join(timeout=10)
        if disable:
            self.ws.set_live_enabled(station_id, False, actor)
        self.ws.audit(actor, 'seismic.live.stop', {'id': station_id})
        return self.state(station_id)

    def start_enabled(self) -> List[str]:
        out = []
        for st in self.ws.seismic_stations():
            if st.get('live') and st['live'].get('enabled'):
                try:
                    self.start(st['id'], 'system')
                    out.append(st['id'])
                except Exception as e:                       # one station's bad config must not stop the others
                    self.ws.audit('system', 'seismic.live.start_failed', {'id': st['id'], 'error': str(e)})
        return out

    def stop_all(self) -> None:
        for sid in list(self._runs):
            self.stop(sid, 'system', disable=False)

    def state(self, station_id: str) -> dict:
        run = self._runs.get(station_id)
        if run and run[0].is_alive():
            st = dict(run[1].state)
        else:
            st = dict(self.ws.live_station_state(station_id))
            if st.get('status') in ('CONNECTED', 'RECONNECTING'):
                st['status'] = 'STOPPED'                     # the file says connected but nothing is running: the process ended
        st['running'] = bool(run and run[0].is_alive())
        st['id'] = station_id
        return st

    def states(self) -> List[dict]:
        out = []
        for st in self.ws.seismic_stations():
            if st.get('live'):
                s = self.state(st['id'])
                s['display'] = st['display']
                s['enabled'] = bool(st['live'].get('enabled'))
                s['files_folded'] = len(st.get('files') or [])
                out.append(s)
        return out


def report_text(st: dict) -> str:
    lines = [f"seedlink: {st.get('host')}:{st.get('port')} - {st.get('status')} (SeedLink {st.get('protocol') or '?'}; server {st.get('server') or '?'}; {st.get('organization') or ''})".rstrip(),
             f"  records {st.get('records')}, bytes {st.get('bytes')}, reconnects {st.get('reconnects')}, info packets {st.get('info_packets')}, "
             f"latency p50/p95 {st.get('latency_p50_s')}/{st.get('latency_p95_s')} s"]
    for k, v in (st.get('stations') or {}).items():
        lines.append(f"  {k}: last sequence {v.get('seq')} ({v.get('records')} records)")
    for cid, c in sorted((st.get('channels') or {}).items()):
        lines.append(f"  {cid}: {c.get('records')} records, {c.get('samples')} samples at {c.get('rate_hz')} Hz, last {c.get('last_sample_utc')}, "
                     f"latency {c.get('latency_s')} s, gaps {c.get('gaps')}, overlaps {c.get('overlaps')} -> {c.get('file')}")
    if st.get('last_error'):
        lines.append(f"  note: {st['last_error']}")
    return '\n'.join(lines)
