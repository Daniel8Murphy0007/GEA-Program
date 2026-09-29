# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""modbus — the real Modbus client, connectivity tier 4 (v1.11.0).

Daniel GO 2026-08-24: implement the live-protocol tap for the G6-class target
(Modbus RS485/TCP surface interface, declared by the tool library from the
GEOQ 177 spec-table footnotes) as REAL protocol code behind a guarded
optional dependency — the same pattern as the PyQt6 front-end:

    pip install pymodbus        (the package works fully without it;
                                 this tier simply refuses until installed)

Honesty rules, unchanged:
  * READ-ONLY. The tap issues ONLY read_holding_registers /
    read_input_registers. There is no code path that writes to a device.
  * REGISTER MAPS ARE USER-SUPPLIED AND CITATION-MANDATORY. No public G6
    register map was published on the fetched pages, so the library ships
    NO device map — a map without a `source` citation is rejected (Rule 7),
    exactly like the gauge specs. The shipped example map is labeled
    EXAMPLE_TEST_FIXTURE and describes the in-process loopback server used
    for verification, NOT a device.
  * Decoding is done with `struct` on the raw 16-bit registers (word order
    from the map) — no dependence on pymodbus payload helpers, so the tap
    is stable across pymodbus versions.

Output is the same normalized `LiveStream` every other port produces; the
reconciler does not know or care that the samples came over a wire.

Headless-safe: numpy + stdlib; pymodbus only inside guarded paths.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .ports import (LiveStream, StreamChannel, PortSpec, PORT_REGISTRY)

PYMODBUS_AVAILABLE = False
try:
    from pymodbus.client import ModbusTcpClient          # noqa: F401
    PYMODBUS_AVAILABLE = True
except ImportError:
    ModbusTcpClient = None

_TYPES = {'float32': 2, 'uint16': 1, 'int16': 1, 'uint32': 2, 'int32': 2}


@dataclass(frozen=True)
class RegisterEntry:
    channel: str
    address: int
    type: str = 'float32'
    unit: str = ''
    scale: float = 1.0

    def word_count(self) -> int:
        return _TYPES[self.type]


@dataclass(frozen=True)
class RegisterMap:
    """The site's register layout. `source` citation is MANDATORY (Rule 7):
    a register map must name the document it came from — a G6/site manual,
    or the loopback test fixture, never an invention."""
    name: str
    source: str
    registers: List[RegisterEntry]
    unit_id: int = 1
    word_order: str = 'big'          # register word order for 32-bit types
    table: str = 'holding'           # 'holding' | 'input'

    def __post_init__(self):
        if not self.source or len(self.source) < 20:
            raise ValueError("register map requires a substantive 'source' citation "
                             "(Rule 7: the library does not invent device layouts)")
        for r in self.registers:
            if r.type not in _TYPES:
                raise ValueError(f"unsupported register type '{r.type}'")
        if self.table not in ('holding', 'input'):
            raise ValueError("table must be 'holding' or 'input'")


def load_register_map(path_or_dict) -> RegisterMap:
    d = path_or_dict
    if not isinstance(d, dict):
        with Path(path_or_dict).open(encoding='utf-8') as f:
            d = json.load(f)
    regs = [RegisterEntry(channel=r['channel'], address=int(r['address']),
                          type=r.get('type', 'float32'), unit=r.get('unit', ''),
                          scale=float(r.get('scale', 1.0)))
            for r in d['registers']]
    return RegisterMap(name=d.get('name', 'register_map'), source=d.get('source', ''),
                       registers=regs, unit_id=int(d.get('unit_id', 1)),
                       word_order=d.get('word_order', 'big'),
                       table=d.get('table', 'holding'))


def _decode(words: List[int], rtype: str, word_order: str) -> float:
    if rtype == 'uint16':
        return float(words[0])
    if rtype == 'int16':
        return float(struct.unpack('>h', struct.pack('>H', words[0]))[0])
    w = words if word_order == 'big' else list(reversed(words))
    raw = struct.pack('>HH', w[0], w[1])
    if rtype == 'float32':
        return float(struct.unpack('>f', raw)[0])
    if rtype == 'uint32':
        return float(struct.unpack('>I', raw)[0])
    if rtype == 'int32':
        return float(struct.unpack('>i', raw)[0])
    raise ValueError(rtype)


class ModbusHistorianTap:
    """READ-ONLY Modbus tap: polls the mapped registers, accumulates samples,
    emits the normalized LiveStream. Caller schedules the polling cadence."""

    def __init__(self, host: str, port: int = 502,
                 register_map: RegisterMap | dict | str = None,
                 timeout_s: float = 3.0):
        if not PYMODBUS_AVAILABLE:
            raise NotImplementedError(
                "Modbus tier requires the optional dependency: pip install pymodbus "
                "(guarded import - the rest of the package runs without it)")
        if register_map is None:
            raise ValueError("a register map is required (user-supplied, cited - Rule 7)")
        self.map = register_map if isinstance(register_map, RegisterMap) else load_register_map(register_map)
        self.host, self.port = host, int(port)
        self.client = ModbusTcpClient(host, port=int(port), timeout=timeout_s)
        self._t0: Optional[float] = None
        self.times: List[float] = []
        self.samples: Dict[str, List[float]] = {r.channel: [] for r in self.map.registers}

    def connect(self) -> bool:
        return bool(self.client.connect())

    def close(self) -> None:
        self.client.close()

    def poll_once(self) -> Dict[str, float]:
        """One READ-ONLY poll of every mapped register."""
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = now
        out: Dict[str, float] = {}
        for r in self.map.registers:
            if self.map.table == 'holding':
                rr = self.client.read_holding_registers(r.address, count=r.word_count(),
                                                        device_id=self.map.unit_id)
            else:
                rr = self.client.read_input_registers(r.address, count=r.word_count(),
                                                      device_id=self.map.unit_id)
            if rr.isError():
                out[r.channel] = float('nan')
            else:
                out[r.channel] = _decode(list(rr.registers), r.type, self.map.word_order) * r.scale
        self.times.append(now - self._t0)
        for ch, v in out.items():
            self.samples[ch].append(v)
        return out

    def collect(self, n_polls: int, interval_s: float = 0.0) -> LiveStream:
        for k in range(int(n_polls)):
            self.poll_once()
            if interval_s > 0 and k < n_polls - 1:
                time.sleep(interval_s)
        return self.to_live_stream()

    def to_live_stream(self) -> LiveStream:
        chans = {r.channel: StreamChannel(name=r.channel, unit=r.unit,
                                          values=np.array(self.samples[r.channel], dtype=float))
                 for r in self.map.registers}
        return LiveStream(name=f"modbus_{self.host}", source_format='modbus_g6',
                          index_kind='time_s', index=np.array(self.times, dtype=float),
                          channels=chans,
                          meta={'host': self.host, 'port': str(self.port),
                                'map': self.map.name, 'map_source': self.map.source[:100]})


def read_modbus(config) -> LiveStream:
    """The registry reader: config is a JSON path or dict with host, port,
    register_map (path/dict), polls, interval_s. Read-only end to end."""
    d = config
    if not isinstance(d, dict):
        with Path(config).open(encoding='utf-8') as f:
            d = json.load(f)
    missing = [k for k in ('host', 'register_map') if not d.get(k)]
    if missing:
        raise NotImplementedError(
            "port 'modbus_g6' is IMPLEMENTED but this call lacks site details - "
            f"config missing: {', '.join(missing)}. The protocol code is real "
            "(loopback-verified); the library refuses ONLY because the site's "
            "host/register map is not supplied, and register maps are "
            "citation-mandatory (Rule 7: no invented device layouts).")
    tap = ModbusHistorianTap(host=d['host'], port=int(d.get('port', 502)),
                             register_map=d['register_map'],
                             timeout_s=float(d.get('timeout_s', 3.0)))
    if not tap.connect():
        raise ConnectionError(f"could not connect to {d['host']}:{d.get('port', 502)} (read-only tap)")
    try:
        return tap.collect(int(d.get('polls', 10)), float(d.get('interval_s', 0.0)))
    finally:
        tap.close()


def _refuse_no_dep(*a, **k):
    raise NotImplementedError(
        "port 'modbus_g6' requires the optional dependency: pip install pymodbus "
        "(protocol code is implemented; only the dependency is missing)")


# Upgrade the registry entry declared in ports (imported by __init__ after ports):
PORT_REGISTRY['modbus_g6'] = PortSpec(
    name='modbus_g6',
    transport='Modbus TCP / RS485-gateway (G6 interface card class)',
    status=('IMPLEMENTED_REQUIRES_SITE_CONFIG' if PYMODBUS_AVAILABLE
            else 'DECLARED_DEPENDENCY_MISSING'),
    reader=(read_modbus if PYMODBUS_AVAILABLE else _refuse_no_dep),
    detail=("READ-ONLY tap; real protocol code (v1.11.0); register map is user-supplied "
            "and citation-mandatory - no public G6 map exists in the fetched sources, so "
            "none is shipped; loopback-verified against an in-process pymodbus server"))
