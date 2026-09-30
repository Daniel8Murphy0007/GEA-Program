# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""live_ports - what every live protocol port shares.

A live port (OPC UA, MQTT, Modbus) turns messages on a wire into
`SampleRecord`s: tag, source timestamp (UTC), value, unit, quality flag with
the rule that set it, source layer FIELD_EDGE, and the ingest timestamp at
arrival - so latency is measured per record from the first sample. From
records the rest of the program follows: `records_to_stream` builds the
time-indexed LiveStream the reconciler consumes; `feed_buffer` hands records
to the store-and-forward buffer; a `Recording` writes every received message
to JSON lines and replays it later, so a site session can be reproduced
offline and the mapping logic is tested without a server.

Tag mapping is a file the client owns (one entry per node or topic, with
the tag id, unit, class and an optional scale), and the port refuses to run
without it - a port maps what the site declares; it never guesses tags.

Headless-safe: numpy + stdlib.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np

from .sample_record import SampleRecord, parse_utc
from .ports import LiveStream, StreamChannel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


@dataclass(frozen=True)
class TagMapping:
    """One wire address -> one tag."""
    address: str                     # node id (OPC UA) or topic[/metric] (MQTT)
    tag_id: str
    unit: str = ''
    tag_class: str = 'generic'
    scale: float = 1.0
    offset: float = 0.0
    description: str = ''

    def apply(self, raw: float) -> float:
        return raw * self.scale + self.offset


def load_mappings(entries: Iterable[dict], address_key: str) -> Dict[str, TagMapping]:
    out: Dict[str, TagMapping] = {}
    for e in entries:
        if address_key not in e or 'tag_id' not in e:
            raise ValueError(f"tag mapping entry needs '{address_key}' and 'tag_id': {e}")
        m = TagMapping(address=str(e[address_key]), tag_id=str(e['tag_id']), unit=str(e.get('unit', '')),
                       tag_class=str(e.get('tag_class', 'generic')), scale=float(e.get('scale', 1.0)),
                       offset=float(e.get('offset', 0.0)), description=str(e.get('description', '')))
        out[m.address] = m
    if not out:
        raise ValueError('tag mapping is empty - a live port maps what the site declares; it never guesses tags')
    return out


def make_record(m: TagMapping, value, source_ts: Optional[datetime], quality_flag: str, rule: str,
                received_at: Optional[datetime] = None) -> SampleRecord:
    received_at = received_at or utc_now()
    ts = source_ts or received_at
    v = None
    if value is not None and quality_flag != 'GAP':
        try:
            v = m.apply(float(value))
        except (TypeError, ValueError):
            v, quality_flag, rule = None, 'GAP', f'non-numeric payload {value!r}'
    return SampleRecord(tag_id=m.tag_id, timestamp_utc=iso(ts), value=v, unit=m.unit, quality_flag=quality_flag,
                        rule_fired=rule, source_layer='FIELD_EDGE', ingest_timestamp_utc=iso(received_at))


def records_to_stream(records: Iterable[SampleRecord], name: str = 'live', source_format: str = 'live_port',
                      meta: Optional[dict] = None) -> LiveStream:
    """Time-indexed LiveStream from records: one channel per tag, aligned on
    the union of timestamps (NaN where a tag has no sample at that instant).
    Quality flags ride along per channel so the record layer keeps them."""
    recs = sorted(records, key=lambda r: (r.timestamp_utc, r.tag_id))
    if not recs:
        raise ValueError('no records to build a stream from')
    times = sorted({r.timestamp_utc for r in recs})
    idx = {t: i for i, t in enumerate(times)}
    t0 = parse_utc(times[0])
    index = np.array([(parse_utc(t) - t0).total_seconds() for t in times], dtype=float)
    tags = sorted({r.tag_id for r in recs})
    vals = {t: np.full(len(times), np.nan) for t in tags}
    qual = {t: ['MISSING'] * len(times) for t in tags}
    units = {}
    for r in recs:
        i = idx[r.timestamp_utc]
        if r.value is not None:
            vals[r.tag_id][i] = r.value
        qual[r.tag_id][i] = {'GOOD': 'OK', 'GAP': 'MISSING', 'FLATLINE': 'STUCK', 'SPIKE': 'SPIKE'}.get(r.quality_flag, r.quality_flag)
        units[r.tag_id] = r.unit
    channels = {t: StreamChannel(name=t, unit=units[t], values=vals[t], quality=qual[t]) for t in tags}
    m = {'start_time': times[0].replace('Z', '+00:00'), 'records': str(len(recs)), 'tags': str(len(tags))}
    if meta:
        m.update({k: str(v) for k, v in meta.items()})
    return LiveStream(name=name, source_format=source_format, index_kind='time_s', index=index, channels=channels, meta=m)


def feed_buffer(buffer, records: Iterable[SampleRecord], link_up: bool = True, now: Optional[datetime] = None) -> List[SampleRecord]:
    """Offer records to a store-and-forward buffer; returns what was delivered."""
    out = []
    now = now or utc_now()
    for r in records:
        d = buffer.offer(r, link_up, now)
        if d is not None:
            out.append(d)
    return out


class Recording:
    """Append-only JSON lines of received messages; replayable."""

    def __init__(self, path: Optional[str]):
        self.path = path
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, entry: dict) -> None:
        if not self.path:
            return
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + '\n')

    @staticmethod
    def read(path: str) -> List[dict]:
        with open(path, encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]


def write_records_csv(records: Iterable[SampleRecord], path: str) -> str:
    from .sample_record import write_records_csv as _w
    return _w(records, path)


def summarize(records: List[SampleRecord]) -> dict:
    if not records:
        return {'records': 0}
    lat = [r.latency_s() for r in records if r.latency_s() is not None]
    flags: Dict[str, int] = {}
    for r in records:
        flags[r.quality_flag] = flags.get(r.quality_flag, 0) + 1
    return {'records': len(records), 'tags': len({r.tag_id for r in records}),
            'first_utc': min(r.timestamp_utc for r in records), 'last_utc': max(r.timestamp_utc for r in records),
            'quality': flags,
            'latency_p50_s': (round(float(np.percentile(lat, 50)), 3) if lat else None),
            'latency_p95_s': (round(float(np.percentile(lat, 95)), 3) if lat else None)}
