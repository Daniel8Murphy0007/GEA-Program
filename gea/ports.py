# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""ports — the live-stream ports/plug-in layer (v1.8.0 extension).

Piece 2 of the two-stream build (Daniel's architecture, PAPER_2256 appendix 6):
READ-ONLY taps that ingest live-stream data from site logging systems into a
single normalized form — `LiveStream` — that the reconciler (piece 3) will
coordinate against the closed stream's predictions.

Design rules:
  * READ-ONLY BY DESIGN. Ports ingest; they never write to, poll-configure,
    or control a site system. File readers open files for reading only, and
    the declared network taps are specified read-only.
  * PLUG-IN REGISTRY. Every port is an entry in `PORT_REGISTRY` with an
    explicit status: IMPLEMENTED entries parse today; DECLARED entries name
    the protocol (from the tool library's telemetry_interface declarations)
    but REFUSE to run until real site details exist — the same
    no-invented-behavior pattern as the tool library's user-supplied specs.
    `register_port()` lets a site plug in its own reader without touching
    this module.
  * ROUND-TRIP VERIFIED. The v1.3.0 telemetry layer exports field-historian
    CSVs; the historian port re-ingests them bit-consistently (MISSING ->
    NaN, flags carried). Closed stream -> simulated live file -> port ->
    the same numbers: the ingest path is proven in simulation before it
    ever touches a site.

Implemented file formats:
  * historian_csv — wide-format historian export: first column timestamp
    (ISO or numeric), remaining columns channels; blank cells = missing.
    Auto-detects the v1.3.0 telemetry export layout (flag_* columns).
  * las2 — LAS 2.0 well-log files (public standard, Canadian Well Logging
    Society): ~V/~W/~C/~A sections, NULL substitution, unwrapped data.
    Wrapped-mode files are REFUSED (unsupported), never mis-parsed.

Headless-safe: numpy only.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# The normalized live-stream model
# ---------------------------------------------------------------------------
@dataclass
class StreamChannel:
    name: str
    unit: str
    values: np.ndarray                      # NaN = missing
    quality: Optional[List[str]] = None     # per-sample flags if the source carries them

    def coverage_pct(self) -> float:
        n = len(self.values)
        return round(100.0 * float(np.sum(~np.isnan(self.values))) / n, 2) if n else 0.0


@dataclass
class LiveStream:
    """The normalized ingest product: an index (time OR depth) + channels.

    index_kind is 'time_s' (elapsed seconds; historian) or 'depth' (LAS).
    This is the object the reconciler hangs on the closed stream.
    """
    name: str
    source_format: str
    index_kind: str
    index: np.ndarray
    channels: Dict[str, StreamChannel] = field(default_factory=dict)
    meta: Dict[str, str] = field(default_factory=dict)

    def channel(self, name: str) -> StreamChannel:
        return self.channels[name]

    def summary(self) -> dict:
        return {
            'name': self.name,
            'source_format': self.source_format,
            'index_kind': self.index_kind,
            'samples': int(len(self.index)),
            'channels': {n: {'unit': c.unit, 'coverage_pct': c.coverage_pct()}
                         for n, c in self.channels.items()},
            'meta': dict(self.meta),
        }


# ---------------------------------------------------------------------------
# historian_csv reader (auto-detects the v1.3.0 telemetry export layout)
# ---------------------------------------------------------------------------
def _parse_time(s: str, t0: Optional[datetime]) -> tuple:
    try:
        return float(s), t0
    except ValueError:
        dt = datetime.fromisoformat(s)
        if t0 is None:
            t0 = dt
        return (dt - t0).total_seconds(), t0


def read_historian_csv(path) -> LiveStream:
    """Wide-format historian export: col 0 = timestamp (ISO or numeric),
    remaining columns = channels; blank cells = missing -> NaN. Columns named
    flag_* (the v1.3.0 telemetry layout) become per-gauge quality flags
    attached to that gauge's channels instead of numeric channels."""
    p = Path(path)
    with p.open(newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError(f"{p}: no data rows")
    header = rows[0]
    data = rows[1:]
    n = len(data)
    times = np.zeros(n)
    t0 = None
    for i, r in enumerate(data):
        times[i], t0 = _parse_time(r[0], t0)
    flag_cols = {j: h for j, h in enumerate(header) if h.startswith('flag_')}
    chan_cols = [j for j in range(1, len(header)) if j not in flag_cols]
    channels: Dict[str, StreamChannel] = {}
    for j in chan_cols:
        vals = np.full(n, np.nan)
        for i, r in enumerate(data):
            cell = r[j].strip() if j < len(r) else ''
            if cell:
                try:
                    vals[i] = float(cell)
                except ValueError:
                    pass                      # non-numeric cell in a numeric channel -> missing
        name = header[j]
        unit = 'psi' if 'psi' in name.lower() else ('degF' if ('_f_' in name.lower() or name.lower().endswith('_f') or 't_' in name.lower()[:2]) else '')
        channels[name] = StreamChannel(name=name, unit=unit, values=vals)
    for j, h in flag_cols.items():
        tag = h[len('flag_'):]                # e.g. 'S1'
        flags = [(r[j].strip() if j < len(r) else '') for r in data]
        for cname, ch in channels.items():
            if cname.endswith('_' + tag) or ('_' + tag + '_') in cname:
                ch.quality = flags
    meta = {'path': str(p), 'columns': str(len(header))}
    if t0 is not None:
        meta['start_time'] = t0.isoformat()      # ISO origin of the elapsed-seconds index (client records)
    return LiveStream(name=p.stem, source_format='historian_csv',
                      index_kind='time_s', index=times, channels=channels,
                      meta=meta)


# ---------------------------------------------------------------------------
# LAS 2.0 reader (public well-log standard; unwrapped mode)
# ---------------------------------------------------------------------------
def read_las(path) -> LiveStream:
    """LAS 2.0 reader: ~Version/~Well/~Parameter/~Curve/~ASCII sections,
    NULL-value substitution -> NaN, depth-indexed curves.

    WRAP. NO: one line per depth step. WRAP. YES (v1.13.0, driven by the real
    Kennetcook #2 / P-129 catalogue well): records are assembled by
    accumulating values until the curve count is reached - honest parsing
    replaced the earlier refusal once a real wrapped file existed to verify
    against. Records with a wrong value count are DROPPED, not guessed.

    Meta captures WELL/COMP/FLD/DATE from ~W plus real downhole anchors from
    ~P when present (BHT, TMAX, MRT1, TDL, TDD, with units) - the converter's
    measured-BHT tier feeds on these."""
    p = Path(path)
    lines = p.read_text(encoding='utf-8', errors='ignore').splitlines()
    section = ''
    wrap = False
    vers = 2.0
    null_val = -999.25                        # LAS convention default
    curves: List[tuple] = []                  # (mnemonic, unit)
    data_rows: List[List[float]] = []
    pend: List[float] = []                    # wrapped-record accumulator
    meta: Dict[str, str] = {}
    _P_ANCHORS = ('BHT', 'TMAX', 'MRT1', 'TDL', 'TDD')
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('~'):
            section = s[1].upper()
            continue
        if section == 'V':
            if s.upper().startswith('WRAP') and '.' in s:
                wrap = s.split('.', 1)[1].strip().upper().startswith('YES')
            if s.upper().startswith('VERS') and '.' in s:
                v = s.split('.', 1)[1].split(':')[0].strip().split()
                if v and _is_float(v[0]):
                    vers = float(v[0])
        elif section == 'W':
            if s.upper().startswith('NULL') and '.' in s:
                body = s.split('.', 1)[1]
                val = body.split(':')[0].strip().split()
                if val:
                    try:
                        null_val = float(val[-1])
                    except ValueError:
                        pass
            for key in ('WELL', 'COMP', 'FLD', 'DATE'):
                if s.upper().startswith(key):
                    if vers < 2.0 and ':' in s:
                        # LAS 1.x convention: the VALUE sits AFTER the colon
                        meta[key] = s.split(':', 1)[1].strip()
                    else:
                        meta[key] = s.split(':', 1)[0].split('.', 1)[-1].strip() if '.' in s else s
        elif section == 'P':
            head = s.split(':', 1)[0]
            if '.' in head:
                mnem, rest = head.split('.', 1)
                mnem = mnem.strip().upper()
                if mnem in _P_ANCHORS:
                    parts = rest.strip().split()
                    if parts:
                        unit = parts[0] if not _is_float(parts[0]) else ''
                        vals = [x for x in parts if _is_float(x)]
                        if vals:
                            meta[mnem] = vals[-1]
                            if unit:
                                meta[mnem + '_UNIT'] = unit
        elif section == 'C':
            head = s.split(':', 1)[0]
            if '.' in head:
                mnem, rest = head.split('.', 1)
                curves.append((mnem.strip(), rest.strip().split()[0] if rest.strip() else ''))
        elif section == 'A':
            try:
                vals = [float(x) for x in s.split()]
            except ValueError:
                continue
            if not wrap:
                data_rows.append(vals)
            else:
                pend.extend(vals)
                while len(pend) >= len(curves) > 0:
                    data_rows.append(pend[:len(curves)])
                    pend = pend[len(curves):]
    if wrap and pend:
        pass                                   # trailing partial record dropped, not guessed
    if not curves or not data_rows:
        raise ValueError(f"{p}: no curves or no data (need ~Curve and ~ASCII sections)")
    width = len(curves)
    arr = np.full((len(data_rows), width), np.nan)
    for i, r in enumerate(data_rows):
        for j in range(min(width, len(r))):
            arr[i, j] = r[j]
    arr[arr == null_val] = np.nan
    index = arr[:, 0]
    channels = {m: StreamChannel(name=m, unit=u, values=arr[:, j])
                for j, (m, u) in enumerate(curves) if j > 0}
    return LiveStream(name=p.stem, source_format='las2',
                      index_kind='depth', index=index, channels=channels, meta=meta)


def _is_float(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# The plug-in registry
# ---------------------------------------------------------------------------
IMPLEMENTED = "IMPLEMENTED"
DECLARED_SITE_DETAILS_REQUIRED = "DECLARED_SITE_DETAILS_REQUIRED"


@dataclass(frozen=True)
class PortSpec:
    name: str
    transport: str
    status: str
    reader: Optional[Callable] = None
    detail: str = ""


def _refuse(name, need):
    def f(*a, **k):
        raise NotImplementedError(
            f"port '{name}' is DECLARED but not implemented - {need}. "
            "The registry names the protocol; it does not invent site behavior. "
            "Provide site details (or a site reader via register_port).")
    return f


PORT_REGISTRY: Dict[str, PortSpec] = {
    'historian_csv': PortSpec(
        name='historian_csv', transport='file (wide-format historian CSV export)',
        status=IMPLEMENTED, reader=read_historian_csv,
        detail="round-trip verified against the v1.3.0 telemetry export layout"),
    'las2': PortSpec(
        name='las2', transport='file (LAS 2.0 well log, CWLS public standard)',
        status=IMPLEMENTED, reader=read_las,
        detail="unwrapped mode; NULL substitution; wrapped mode refused"),
    # NOTE (v1.41.0): this base entry is the PRE-IMPORT fallback only. Importing
    # modbus (which the package __init__ always does) UPGRADES this entry in
    # place to the real pymodbus TCP client (reader=read_modbus, status=
    # IMPLEMENTED_REQUIRES_SITE_CONFIG when pymodbus is installed). A static read
    # of this file alone therefore understates the shipped capability - this
    # comment exists so source and runtime tell the same story.
    'modbus_g6': PortSpec(
        name='modbus_g6', transport='Modbus RS485 (G6 interface card; 4-20mA analog alt.)',
        status=DECLARED_SITE_DETAILS_REQUIRED, reader=_refuse('modbus_g6',
            "needs the site's register map and polling parameters (target declared by the "
            "tool library's surface_interface_g6 entry, GEOQ 177 spec-table footnote); "
            "NOTE: modbus upgrades this entry to the real client at package import"),
        detail="READ-ONLY tap; base declaration - upgraded in place by modbus at package import (see modbus.py tail)"),
    'witsml': PortSpec(
        name='witsml', transport='WITSML server (rig-site data exchange standard)',
        status=DECLARED_SITE_DETAILS_REQUIRED, reader=_refuse('witsml',
            "needs the site's server URL, version (1.4.1/2.0), and credentials"),
        detail="READ-ONLY query of log/trajectory objects"),
    'opcua': PortSpec(
        name='opcua', transport='OPC-UA (plant/SCADA historian access)',
        status=DECLARED_SITE_DETAILS_REQUIRED, reader=_refuse('opcua',
            "needs the site's endpoint and node ids"),
        detail="READ-ONLY subscription to gauge tags"),
}


def register_port(name: str, transport: str, reader: Callable, detail: str = "") -> None:
    """Plug a site-specific reader in without touching this module.
    The reader must return a LiveStream. Registration is additive only."""
    PORT_REGISTRY[name] = PortSpec(name=name, transport=transport,
                                   status=IMPLEMENTED, reader=reader, detail=detail)


def ingest(source, port: str = 'historian_csv') -> LiveStream:
    """The single entry point: ingest a source through a named port."""
    spec = PORT_REGISTRY[port]
    stream = spec.reader(source)
    if not isinstance(stream, LiveStream):
        raise TypeError(f"port '{port}' returned {type(stream).__name__}, not LiveStream")
    return stream
