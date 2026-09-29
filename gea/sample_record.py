"""sample_record — the canonical measurement record and tag catalogue.

Every measurement the client-facing reports touch passes through ONE record
shape, so quality flags, gaps, staleness and ingest latency can be counted
and reported per tag in the vocabulary a production-operations client uses
(tag catalogue with owner / unit / engineering range; per-sample quality
flag with the rule that fired; latency measured per source layer).

    SampleRecord(tag_id, timestamp_utc, value, unit, quality_flag,
                 rule_fired, source_layer, ingest_timestamp_utc)

Quality flags (fixed enumeration):

    GOOD      passed every rule in force for the tag
    RANGE     outside the tag's engineering range
    ROC       rate of change above the tag's limit
    FLATLINE  value unchanged for at least the tag's flatline run length
    SPIKE     single-sample excursion (from the source's own despike pass
              or the rule here)
    STALE     the sample arrived after the tag's allowed silence
    GAP       no value (missing sample)

Source flags carried by an existing stream are mapped, never discarded:
OK -> GOOD, MISSING -> GAP, STUCK -> FLATLINE, SPIKE -> SPIKE.

Rules are engineering configuration, not derivations: each TagDefinition
holds its own limits, and every flagged record names the rule and the limit
that fired, so the client can audit the flag against the tag definition.

Headless-safe: numpy only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

QUALITY_FLAGS = ('GOOD', 'RANGE', 'ROC', 'FLATLINE', 'SPIKE', 'STALE', 'GAP')
SOURCE_LAYERS = ('FIELD_EDGE', 'OT_LAKE', 'DOF')
LEGACY_FLAG_MAP = {'OK': 'GOOD', 'MISSING': 'GAP', 'STUCK': 'FLATLINE',
                   'SPIKE': 'SPIKE', '': 'GOOD'}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_utc(s: str) -> datetime:
    """ISO-8601 (date or datetime, optional Z) -> aware UTC datetime."""
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1]
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Tag catalogue
# ---------------------------------------------------------------------------
@dataclass
class TagDefinition:
    tag_id: str
    description: str = ''
    unit: str = ''
    owner: str = 'Production Operations'
    tag_class: str = 'downhole_pressure'
    eng_range: Tuple[Optional[float], Optional[float]] = (None, None)
    roc_limit_per_s: Optional[float] = None     # |dv/dt| above this -> ROC
    flatline_min_samples: int = 0               # 0 disables the FLATLINE rule
    stale_after_s: Optional[float] = None       # silence longer than this -> STALE
    cadence_s: Optional[float] = None
    source_layer: str = 'FIELD_EDGE'
    source: str = ''                             # where the tag comes from (file, port, catalogue entry)
    spike_n_sigma: Optional[float] = None       # |v - rolling median| > n x MAD -> SPIKE (None disables)
    spike_window: int = 21                      # rolling window (odd) for the spike rule
    limits_basis: str = 'class defaults'        # where the limits came from (datasheet citation or setting)

    def row(self) -> dict:
        lo, hi = self.eng_range
        return {
            'tag_id': self.tag_id, 'description': self.description, 'unit': self.unit,
            'owner': self.owner, 'tag_class': self.tag_class,
            'eng_range_lo': '' if lo is None else lo, 'eng_range_hi': '' if hi is None else hi,
            'roc_limit_per_s': '' if self.roc_limit_per_s is None else self.roc_limit_per_s,
            'flatline_min_samples': self.flatline_min_samples,
            'stale_after_s': '' if self.stale_after_s is None else self.stale_after_s,
            'cadence_s': '' if self.cadence_s is None else self.cadence_s,
            'source_layer': self.source_layer, 'source': self.source,
            'spike_n_sigma': '' if self.spike_n_sigma is None else self.spike_n_sigma,
            'spike_window': self.spike_window, 'limits_basis': self.limits_basis,
        }


# Default rule sets per tag class. Values are engineering conventions for a
# downhole quartz gauge historian at the stated cadence; a client overrides
# them per tag in the catalogue, and the report prints whatever is in force.
DEFAULT_TAG_CLASSES: Dict[str, dict] = {
    'downhole_pressure': dict(unit='psi', eng_range=(0.0, 30000.0),
                              roc_limit_per_s=None, flatline_min_samples=3,
                              stale_after_s=None),
    'downhole_temperature': dict(unit='degF', eng_range=(-40.0, 500.0),
                                 roc_limit_per_s=None, flatline_min_samples=3,
                                 stale_after_s=None),
    'generic': dict(unit='', eng_range=(None, None), roc_limit_per_s=None,
                    flatline_min_samples=0, stale_after_s=None),
}


def rules_from_gauge_spec(spec, tag_class: str, cadence_s: Optional[float] = None,
                          roc_limit_per_s: Optional[float] = None,
                          stale_multiple: float = 3.0, flatline_samples: int = 3,
                          spike_n_sigma: Optional[float] = 6.0) -> dict:
    """Quality limits for a tag class from a gauge datasheet (`GaugeSpec`).

    What the datasheet supplies: the engineering RANGE - pressure 0 to
    full_scale_psi (absolute gauge), temperature up to max_temp_C (converted
    to degF; the lower bound is the class default since datasheets state a
    rating, not a floor). What the datasheet does not supply and is therefore
    an operations setting, printed as such: the rate-of-change limit (a real
    shut-in is a fast transient the gauge sees correctly), the flatline run
    length and the staleness multiple (both from cadence), and the spike
    threshold. `limits_basis` carries the citation so the report can print
    where each limit came from."""
    d = dict(DEFAULT_TAG_CLASSES.get(tag_class, DEFAULT_TAG_CLASSES['generic']))
    basis = []
    if tag_class == 'downhole_pressure' and getattr(spec, 'full_scale_psi', None):
        d['eng_range'] = (0.0, float(spec.full_scale_psi))
        basis.append(f"range 0 to {spec.full_scale_psi:g} psi from datasheet '{spec.name}' full scale")
    elif tag_class == 'downhole_temperature' and getattr(spec, 'max_temp_C', None) is not None:
        hi_f = float(spec.max_temp_C) * 9.0 / 5.0 + 32.0
        d['eng_range'] = (d['eng_range'][0], round(hi_f, 1))
        basis.append(f"range upper bound {hi_f:.0f} degF from datasheet '{spec.name}' rating {spec.max_temp_C:g} C; lower bound class default")
    else:
        basis.append('range: class default')
    d['roc_limit_per_s'] = roc_limit_per_s
    basis.append('rate-of-change: operations setting' + (f' {roc_limit_per_s:g}/s' if roc_limit_per_s is not None else ' (off)'))
    d['flatline_min_samples'] = flatline_samples
    basis.append(f'flatline: {flatline_samples} samples at cadence')
    d['stale_after_s'] = (cadence_s * stale_multiple) if cadence_s else None
    basis.append(f'staleness: {stale_multiple:g} x cadence' if cadence_s else 'staleness: off (cadence unknown)')
    d['spike_n_sigma'] = spike_n_sigma
    basis.append(f'spike: {spike_n_sigma:g} x MAD of rolling median' if spike_n_sigma else 'spike: off')
    d['limits_basis'] = '; '.join(basis)
    d['cadence_s'] = cadence_s
    return d


def _class_for(name: str, unit: str) -> str:
    n = name.lower()
    if unit == 'psi' or n.startswith('p_') or 'press' in n:
        return 'downhole_pressure'
    if unit in ('degF', 'degC') or n.startswith('t_') or 'temp' in n:
        return 'downhole_temperature'
    return 'generic'


class TagCatalogue:
    """The canonical data model: one TagDefinition per tag."""

    def __init__(self, tags: Optional[Iterable[TagDefinition]] = None):
        self.tags: Dict[str, TagDefinition] = {}
        for t in tags or ():
            self.add(t)

    def add(self, tag: TagDefinition) -> TagDefinition:
        self.tags[tag.tag_id] = tag
        return tag

    def get(self, tag_id: str) -> TagDefinition:
        return self.tags[tag_id]

    def __contains__(self, tag_id: str) -> bool:
        return tag_id in self.tags

    def __len__(self) -> int:
        return len(self.tags)

    def rows(self) -> List[dict]:
        return [t.row() for t in self.tags.values()]

    @classmethod
    def from_stream(cls, stream, owner: str = 'Production Operations',
                    source_layer: str = 'FIELD_EDGE', cadence_s: Optional[float] = None,
                    stale_multiple: float = 3.0, gauge_spec=None,
                    roc_limits: Optional[Dict[str, float]] = None,
                    spike_n_sigma: Optional[float] = 6.0) -> 'TagCatalogue':
        """Build definitions for every channel of a LiveStream. With a gauge
        datasheet (`gauge_spec`) the engineering ranges come from the
        datasheet and the basis is recorded per tag; `roc_limits` maps a tag
        class to a rate-of-change limit per second (operations setting).
        Cadence from the argument or the stream meta."""
        cad = cadence_s
        if cad is None:
            try:
                cad = float(stream.meta.get('cadence_s'))
            except (TypeError, ValueError):
                cad = None
        if cad is None and getattr(stream, 'index_kind', '') == 'time_s' and len(stream.index) > 1:
            d = np.diff(np.asarray(stream.index, dtype=float))
            d = d[d > 0]
            cad = float(np.median(d)) if len(d) else None
        cat = cls()
        for name, ch in stream.channels.items():
            k = _class_for(name, ch.unit)
            roc = (roc_limits or {}).get(k)
            if gauge_spec is not None:
                d = rules_from_gauge_spec(gauge_spec, k, cadence_s=cad, roc_limit_per_s=roc,
                                          stale_multiple=stale_multiple, spike_n_sigma=spike_n_sigma)
            else:
                d = dict(DEFAULT_TAG_CLASSES[k])
                d['roc_limit_per_s'] = roc
                d['stale_after_s'] = (cad * stale_multiple) if cad else None
                d['spike_n_sigma'] = spike_n_sigma
                d['limits_basis'] = ('class defaults; rate-of-change: ' + (f'operations setting {roc:g}/s' if roc is not None else 'off')
                                     + f'; staleness: {stale_multiple:g} x cadence'
                                     + (f'; spike: {spike_n_sigma:g} x MAD of rolling median' if spike_n_sigma else '; spike: off'))
            if ch.unit:
                d['unit'] = ch.unit
            cat.add(TagDefinition(
                tag_id=name, description=f'{name} from {stream.name}',
                unit=d['unit'], owner=owner, tag_class=k,
                eng_range=d['eng_range'], roc_limit_per_s=d['roc_limit_per_s'],
                flatline_min_samples=d['flatline_min_samples'],
                stale_after_s=d['stale_after_s'], cadence_s=cad, source_layer=source_layer,
                source=str(stream.meta.get('path') or stream.meta.get('source_channel') or stream.source_format),
                spike_n_sigma=d.get('spike_n_sigma'), limits_basis=d.get('limits_basis', 'class defaults')))
        return cat


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
@dataclass
class SampleRecord:
    tag_id: str
    timestamp_utc: str
    value: Optional[float]
    unit: str
    quality_flag: str = 'GOOD'
    rule_fired: str = ''
    source_layer: str = 'FIELD_EDGE'
    ingest_timestamp_utc: str = ''

    def latency_s(self) -> Optional[float]:
        if not self.ingest_timestamp_utc:
            return None
        return (parse_utc(self.ingest_timestamp_utc) - parse_utc(self.timestamp_utc)).total_seconds()

    def row(self) -> dict:
        d = asdict(self)
        d['value'] = '' if self.value is None or (isinstance(self.value, float) and np.isnan(self.value)) else self.value
        return d


RECORD_COLUMNS = ['tag_id', 'timestamp_utc', 'value', 'unit', 'quality_flag',
                  'rule_fired', 'source_layer', 'ingest_timestamp_utc']


# ---------------------------------------------------------------------------
# Quality rules
# ---------------------------------------------------------------------------
def apply_quality_rules(values: np.ndarray, times_s: np.ndarray, tag: TagDefinition,
                        source_flags: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """Return (quality_flag, rule_fired) per sample.

    Precedence when several rules fire on one sample: GAP > source flag
    (FLATLINE/SPIKE from the stream's own pass) > RANGE > ROC > FLATLINE >
    SPIKE > STALE > GOOD. The first rule in that order that fires is reported."""
    v = np.asarray(values, dtype=float)
    t = np.asarray(times_s, dtype=float)
    n = len(v)
    out: List[Tuple[str, str]] = [('GOOD', '')] * n
    lo, hi = tag.eng_range
    # SPIKE (rolling median / MAD, computed once; NaNs ignored inside the window)
    spike = np.zeros(n, dtype=bool)
    spike_dev = np.zeros(n)
    if tag.spike_n_sigma and n >= 5:
        h = max(1, int(tag.spike_window) // 2)
        for i in range(n):
            if np.isnan(v[i]):
                continue
            w = v[max(0, i - h):i + h + 1]
            w = w[~np.isnan(w)]
            if len(w) < 5:
                continue
            med = float(np.median(w))
            mad = 1.4826 * float(np.median(np.abs(w - med)))
            if mad > 0 and abs(v[i] - med) > tag.spike_n_sigma * mad:
                spike[i] = True
                spike_dev[i] = abs(v[i] - med) / mad
    # FLATLINE runs (computed once)
    flat = np.zeros(n, dtype=bool)
    if tag.flatline_min_samples and tag.flatline_min_samples > 1:
        run_start = 0
        for i in range(1, n + 1):
            if i == n or np.isnan(v[i]) or np.isnan(v[i - 1]) or v[i] != v[i - 1]:
                if i - run_start >= tag.flatline_min_samples and not np.isnan(v[run_start]):
                    flat[run_start:i] = True
                run_start = i
    last_good_t: Optional[float] = None
    for i in range(n):
        if np.isnan(v[i]):
            out[i] = ('GAP', 'no value')
            continue
        sf = LEGACY_FLAG_MAP.get((source_flags[i] if source_flags and i < len(source_flags) else '').strip().upper(), None) \
            if source_flags else None
        if sf in ('FLATLINE', 'SPIKE'):
            out[i] = (sf, f'source flag {source_flags[i].strip()}')
        elif lo is not None and v[i] < lo:
            out[i] = ('RANGE', f'value {v[i]:g} < eng_range_lo {lo:g}')
        elif hi is not None and v[i] > hi:
            out[i] = ('RANGE', f'value {v[i]:g} > eng_range_hi {hi:g}')
        elif tag.roc_limit_per_s is not None and i > 0 and not np.isnan(v[i - 1]) and t[i] > t[i - 1] \
                and abs((v[i] - v[i - 1]) / (t[i] - t[i - 1])) > tag.roc_limit_per_s:
            out[i] = ('ROC', f'|dv/dt| {abs((v[i]-v[i-1])/(t[i]-t[i-1])):.4g}/s > roc_limit {tag.roc_limit_per_s:g}/s')
        elif flat[i]:
            out[i] = ('FLATLINE', f'unchanged >= {tag.flatline_min_samples} samples')
        elif spike[i]:
            out[i] = ('SPIKE', f'|v - rolling median| = {spike_dev[i]:.1f} x MAD > {tag.spike_n_sigma:g} x MAD (window {tag.spike_window})')
        elif tag.stale_after_s is not None and last_good_t is not None and (t[i] - last_good_t) > tag.stale_after_s:
            out[i] = ('STALE', f'silence {t[i]-last_good_t:.0f} s > stale_after {tag.stale_after_s:g} s')
        else:
            out[i] = ('GOOD', '')
        last_good_t = t[i]
    return out


def records_from_stream(stream, catalogue: Optional[TagCatalogue] = None,
                        t0_utc: Optional[str] = None, source_layer: Optional[str] = None,
                        ingest_utc: Optional[str] = None) -> List[SampleRecord]:
    """Every channel of a time-indexed LiveStream -> SampleRecords with the
    catalogue's rules applied. t0_utc anchors elapsed seconds; when absent
    the stream's start_date meta is used, and failing that 1970-01-01 with
    the timestamps understood as elapsed time from an unknown origin."""
    if getattr(stream, 'index_kind', 'time_s') != 'time_s':
        raise ValueError('records_from_stream needs a time-indexed stream')
    if catalogue is None:
        catalogue = TagCatalogue.from_stream(stream, source_layer=source_layer or 'FIELD_EDGE')
    t0s = t0_utc or stream.meta.get('start_date') or stream.meta.get('start_time') or '1970-01-01T00:00:00Z'
    t0 = parse_utc(t0s)
    times = np.asarray(stream.index, dtype=float)
    recs: List[SampleRecord] = []
    for name, ch in stream.channels.items():
        tag = catalogue.get(name) if name in catalogue else catalogue.add(
            TagDefinition(tag_id=name, unit=ch.unit, tag_class='generic', source=stream.name))
        flags = apply_quality_rules(ch.values, times, tag, getattr(ch, 'quality', None))
        layer = source_layer or tag.source_layer
        for i, (q, rule) in enumerate(flags):
            val = float(ch.values[i])
            recs.append(SampleRecord(
                tag_id=name, timestamp_utc=_iso(t0 + timedelta(seconds=float(times[i]))),
                value=None if np.isnan(val) else val, unit=tag.unit,
                quality_flag=q, rule_fired=rule, source_layer=layer,
                ingest_timestamp_utc=ingest_utc or ''))
    return recs


# ---------------------------------------------------------------------------
# Data quality summary (the §4.2.2 table)
# ---------------------------------------------------------------------------
def quality_summary(records: Iterable[SampleRecord]) -> Dict[str, dict]:
    """Per tag: n, counts per flag, % GOOD, longest gap (samples and seconds),
    first/last timestamp, latency p95 where ingest stamps exist."""
    by: Dict[str, List[SampleRecord]] = {}
    for r in records:
        by.setdefault(r.tag_id, []).append(r)
    out: Dict[str, dict] = {}
    for tag, rs in by.items():
        rs = sorted(rs, key=lambda r: r.timestamp_utc)
        counts = {f: 0 for f in QUALITY_FLAGS}
        for r in rs:
            counts[r.quality_flag] = counts.get(r.quality_flag, 0) + 1
        n = len(rs)
        # longest GAP run
        best = cur = 0
        best_span = 0.0
        run_start = None
        for i, r in enumerate(rs):
            if r.quality_flag == 'GAP':
                if cur == 0:
                    run_start = i
                cur += 1
                if cur > best:
                    best = cur
                    a = parse_utc(rs[run_start].timestamp_utc)
                    b = parse_utc(rs[i].timestamp_utc)
                    best_span = (b - a).total_seconds()
            else:
                cur = 0
        lat = [r.latency_s() for r in rs if r.ingest_timestamp_utc]
        lat = [x for x in lat if x is not None]
        out[tag] = {
            'n': n, 'unit': rs[0].unit, 'source_layer': rs[0].source_layer,
            'counts': counts,
            'pct_good': round(100.0 * counts['GOOD'] / n, 2) if n else 0.0,
            'longest_gap_samples': best, 'longest_gap_s': round(best_span, 0),
            'first_utc': rs[0].timestamp_utc, 'last_utc': rs[-1].timestamp_utc,
            'latency_p95_s': (round(float(np.percentile(lat, 95)), 1) if lat else None),
            'rule_examples': {f: next(r.rule_fired for r in rs if r.quality_flag == f)
                              for f in QUALITY_FLAGS if counts.get(f) and f != 'GOOD'},
        }
    return out


def write_records_csv(records: Iterable[SampleRecord], path) -> str:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=RECORD_COLUMNS)
        w.writeheader()
        for r in records:
            w.writerow(r.row())
    return str(path)


def write_catalogue_csv(catalogue: TagCatalogue, path) -> str:
    rows = catalogue.rows()
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ['tag_id'])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)
