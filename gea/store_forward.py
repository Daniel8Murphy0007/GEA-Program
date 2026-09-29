"""store_forward — the edge buffer: store-and-forward with chronological,
rate-controlled replay, duplicate suppression and a gap report
(SOW 4.2.1.6; SLA 5.0 remote-site table).

When the link from the field edge to the OT lake is down, samples are
buffered at the edge (a spool file, one JSON line per record, capacity in
hours of the tag cadence). When the link returns, live samples pass through
and the backlog is replayed in chronological order at a controlled rate so
the replay cannot flood the link; every delivered record is stamped with its
ingest time at the receiving layer, so latency becomes measurable per
record and per layer, and every (tag, timestamp) is delivered once - a
replayed record already delivered is suppressed and counted.

The simulation driver replays a historian export against a link schedule
(outage windows) with an injected clock, so the same code path serves an
acceptance test, a client demonstration and a live edge.

    delivered = records with ingest_timestamp_utc set (source_layer OT_LAKE
                for live delivery, OT_LAKE_REPLAY for replayed backlog)
    gap report = per tag: samples expected, delivered live, delivered by
                 replay, dropped over capacity, duplicates suppressed,
                 true gaps after replay (missing in the source itself),
                 latency p50/p95/max, chronological order verified

Headless-safe: numpy only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from .sample_record import SampleRecord, parse_utc

CAPACITY_HOURS = 72.0


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass
class BufferConfig:
    capacity_hours: float = CAPACITY_HOURS
    cadence_s: float = 60.0
    replay_rate_per_s: float = 50.0      # records per second on replay (link protection)
    replay_slot_s: float = 1.0           # replay is metered in slots of this length


@dataclass
class OutageWindow:
    start_utc: str
    end_utc: str

    def contains(self, t: datetime) -> bool:
        return parse_utc(self.start_utc) <= t < parse_utc(self.end_utc)

    def duration_h(self) -> float:
        return (parse_utc(self.end_utc) - parse_utc(self.start_utc)).total_seconds() / 3600.0


class StoreForwardBuffer:
    """Edge buffer for one site (all tags)."""

    def __init__(self, config: BufferConfig | None = None, spool_path: Optional[str] = None, n_tags: int = 1):
        self.cfg = config or BufferConfig()
        self.spool_path = spool_path
        self.n_tags = max(1, n_tags)
        self.capacity_records = int(self.cfg.capacity_hours * 3600.0 / self.cfg.cadence_s) * self.n_tags
        self.backlog: List[SampleRecord] = []
        self.delivered_keys: set = set()
        self.stats = {'buffered': 0, 'replayed': 0, 'live': 0, 'dropped_over_capacity': 0,
                      'duplicates_suppressed': 0, 'peak_backlog': 0}
        if spool_path and os.path.exists(spool_path):
            with open(spool_path, encoding='utf-8') as f:
                self.backlog = [SampleRecord(**json.loads(l)) for l in f if l.strip()]

    def _spool(self) -> None:
        if self.spool_path:
            with open(self.spool_path, 'w', encoding='utf-8') as f:
                for r in self.backlog:
                    f.write(json.dumps(r.row(), default=str) + '\n')

    def offer(self, rec: SampleRecord, link_up: bool, now: datetime) -> Optional[SampleRecord]:
        """One sample arrives at the edge at `now`. Returns the delivered record
        when the link is up (stamped), else buffers and returns None."""
        key = (rec.tag_id, rec.timestamp_utc)
        if link_up:
            if key in self.delivered_keys:
                self.stats['duplicates_suppressed'] += 1
                return None
            self.delivered_keys.add(key)
            self.stats['live'] += 1
            return replace(rec, ingest_timestamp_utc=_iso(now), source_layer='OT_LAKE')
        if len(self.backlog) >= self.capacity_records:
            self.backlog.pop(0)
            self.stats['dropped_over_capacity'] += 1
        self.backlog.append(rec)
        self.stats['buffered'] += 1
        self.stats['peak_backlog'] = max(self.stats['peak_backlog'], len(self.backlog))
        return None

    def replay(self, now: datetime, budget: Optional[int] = None) -> List[SampleRecord]:
        """Deliver up to `budget` backlog records (default: rate x slot) in
        chronological order, stamped with `now`."""
        if not self.backlog:
            return []
        n = int(self.cfg.replay_rate_per_s * self.cfg.replay_slot_s) if budget is None else int(budget)
        self.backlog.sort(key=lambda r: (r.timestamp_utc, r.tag_id))
        out: List[SampleRecord] = []
        while self.backlog and len(out) < n:
            r = self.backlog.pop(0)
            key = (r.tag_id, r.timestamp_utc)
            if key in self.delivered_keys:
                self.stats['duplicates_suppressed'] += 1
                continue
            self.delivered_keys.add(key)
            self.stats['replayed'] += 1
            out.append(replace(r, ingest_timestamp_utc=_iso(now), source_layer='OT_LAKE_REPLAY'))
        self._spool()
        return out

    def occupancy_pct(self) -> float:
        return round(100.0 * len(self.backlog) / self.capacity_records, 2) if self.capacity_records else 0.0


# ---------------------------------------------------------------------------
# Simulation driver
# ---------------------------------------------------------------------------
def simulate(records: Iterable[SampleRecord], outages: List[OutageWindow], config: BufferConfig | None = None,
             edge_latency_s: float = 2.0, spool_path: Optional[str] = None) -> dict:
    """Drive the buffer with the records' own timestamps as the clock: each
    sample arrives at the edge `edge_latency_s` after its timestamp; the link
    is down inside any outage window; after an outage the backlog replays at
    the configured rate in one-second slots interleaved with live traffic."""
    recs = sorted(records, key=lambda r: (r.timestamp_utc, r.tag_id))
    if not recs:
        return {'delivered': [], 'stats': {}, 'gap_report': {}, 'outages': []}
    tags = sorted({r.tag_id for r in recs})
    cfg = config or BufferConfig()
    buf = StoreForwardBuffer(cfg, spool_path=spool_path, n_tags=len(tags))
    delivered: List[SampleRecord] = []
    link_events: List[dict] = []
    prev_up = None
    # walk time in cadence steps from first to last sample, replaying in slots
    t0 = parse_utc(recs[0].timestamp_utc)
    t1 = parse_utc(recs[-1].timestamp_utc)
    by_time: Dict[str, List[SampleRecord]] = {}
    for r in recs:
        by_time.setdefault(r.timestamp_utc, []).append(r)
    times = sorted(by_time)
    step = timedelta(seconds=cfg.cadence_s)
    t = t0
    ti = 0
    replay_slots = 0
    while t <= t1 + step or buf.backlog:
        now = t + timedelta(seconds=edge_latency_s)
        up = not any(o.contains(now) for o in outages)
        if up != prev_up:
            link_events.append({'timestamp_utc': _iso(now), 'link': 'UP' if up else 'DOWN', 'backlog': len(buf.backlog)})
            prev_up = up
        # samples with this timestamp arrive
        while ti < len(times) and parse_utc(times[ti]) <= t:
            for r in by_time[times[ti]]:
                d = buf.offer(r, up, now)
                if d is not None:
                    delivered.append(d)
            ti += 1
        # replay backlog in slots while the link is up
        if up and buf.backlog:
            slots = max(1, int(cfg.cadence_s / cfg.replay_slot_s))
            for k in range(slots):
                if not buf.backlog:
                    break
                out = buf.replay(now + timedelta(seconds=k * cfg.replay_slot_s))
                delivered.extend(out)
                replay_slots += 1
        t += step
        if t > t1 + timedelta(hours=cfg.capacity_hours * 2) :
            break
    return {'delivered': delivered, 'stats': dict(buf.stats), 'replay_slots': replay_slots,
            'outages': [{'start_utc': o.start_utc, 'end_utc': o.end_utc, 'duration_h': round(o.duration_h(), 3)} for o in outages],
            'link_events': link_events, 'config': {'capacity_hours': cfg.capacity_hours, 'cadence_s': cfg.cadence_s,
                                                  'replay_rate_per_s': cfg.replay_rate_per_s, 'capacity_records': buf.capacity_records,
                                                  'edge_latency_s': edge_latency_s},
            'gap_report': gap_report(recs, delivered, tags), 'final_backlog': len(buf.backlog)}


def gap_report(source: List[SampleRecord], delivered: List[SampleRecord], tags: Optional[List[str]] = None) -> dict:
    tags = tags or sorted({r.tag_id for r in source})
    out: Dict[str, dict] = {}
    dl: Dict[str, List[SampleRecord]] = {}
    for d in delivered:
        dl.setdefault(d.tag_id, []).append(d)
    for tag in tags:
        src = [r for r in source if r.tag_id == tag]
        got = dl.get(tag, [])
        keys_src = {r.timestamp_utc for r in src}
        keys_got = [r.timestamp_utc for r in got]
        live = sum(1 for r in got if r.source_layer == 'OT_LAKE')
        rep = sum(1 for r in got if r.source_layer == 'OT_LAKE_REPLAY')
        missing = sorted(keys_src - set(keys_got))
        true_gaps = sum(1 for r in src if r.quality_flag == 'GAP')
        lat = np.array([r.latency_s() for r in got if r.latency_s() is not None], dtype=float)
        # chronological: replayed records must be non-decreasing in sample time within the replay sequence
        rep_times = [r.timestamp_utc for r in got if r.source_layer == 'OT_LAKE_REPLAY']
        chrono = rep_times == sorted(rep_times)
        dup = len(keys_got) - len(set(keys_got))
        out[tag] = {'expected': len(src), 'delivered': len(got), 'delivered_live': live, 'delivered_by_replay': rep,
                    'not_delivered': len(missing), 'source_gaps_in_data': true_gaps,
                    'duplicates_in_delivery': dup, 'chronological_replay': chrono,
                    'latency_p50_s': (round(float(np.percentile(lat, 50)), 1) if len(lat) else None),
                    'latency_p95_s': (round(float(np.percentile(lat, 95)), 1) if len(lat) else None),
                    'latency_max_s': (round(float(lat.max()), 1) if len(lat) else None),
                    'pct_within_2min': (round(100.0 * float(np.mean(lat <= 120)), 2) if len(lat) else None),
                    'pct_within_5min': (round(100.0 * float(np.mean(lat <= 300)), 2) if len(lat) else None)}
    return out


def parse_outages(specs: Iterable[str]) -> List[OutageWindow]:
    """'START,END' ISO pairs -> OutageWindow list."""
    out = []
    for s in specs:
        a, b = s.split(',')
        out.append(OutageWindow(a.strip(), b.strip()))
    return out
