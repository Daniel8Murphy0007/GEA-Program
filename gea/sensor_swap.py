# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""sensor_swap - a replaced gauge is a new instrument; the record must say so.

When a downhole or wellhead gauge is swapped, everything fitted to the old
one (bias, drift slope, the re-fit history) stops applying. Two things live
here:

1. The swap register: `sensor_swaps.jsonl` in a well's record folder. One
   line per swap: tag, time (UTC), old and new serial numbers, the
   certificate id of the new instrument, who recorded it, a note. Append-only.

2. Swap detection: a sustained level shift in a tag that is not explained by
   the neighbouring tags (a true process change moves every gauge on the
   string; a swap moves one). The detector proposes candidates with the step
   size and its confidence; a person confirms a candidate into the register,
   or records a swap directly from the work order. Detection never writes the
   register on its own.

Downstream: `segment_mask(stream, register)` blanks the samples before the
latest swap of each swapped tag, so the drift evaluation fits the current
instrument only, and the report names the swap. The certificates register
(`certificates.py`) is where the new serial's calibration lives.

Stdlib + numpy.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SwapRegister:
    """Append-only JSON lines; one well."""

    def __init__(self, path: str):
        self.path = path

    def list(self) -> List[dict]:
        if not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, encoding='utf-8') as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def add(self, tag_id: str, swap_utc: str, actor: str, old_serial: str = '', new_serial: str = '',
            certificate_id: str = '', note: str = '', source: str = 'recorded') -> dict:
        if not tag_id:
            raise ValueError('a swap needs the tag id')
        when = _parse(swap_utc)                                  # validates the stamp
        if not new_serial:
            raise ValueError('a swap needs the new instrument serial number')
        e = {'swap_id': f"SWP-{tag_id}-{when.strftime('%Y%m%dT%H%M%SZ')}", 'tag_id': tag_id, 'swap_utc': _iso(when),
             'old_serial': old_serial, 'new_serial': new_serial, 'certificate_id': certificate_id, 'recorded_by': actor,
             'recorded_utc': _iso(datetime.now(timezone.utc)), 'note': note, 'source': source}
        if any(x['swap_id'] == e['swap_id'] for x in self.list()):
            raise ValueError(f"swap already recorded: {e['swap_id']}")
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(e, sort_keys=True) + '\n')
        return e

    def latest_by_tag(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for e in self.list():
            cur = out.get(e['tag_id'])
            if cur is None or e['swap_utc'] > cur['swap_utc']:
                out[e['tag_id']] = e
        return out


# -- detection --------------------------------------------------------------------------
def _median_level(x: np.ndarray) -> float:
    v = x[~np.isnan(x)]
    return float(np.median(v)) if v.size else float('nan')


def detect_swaps(stream, tags: Optional[List[str]] = None, window: int = 48, min_step_sigma: float = 6.0,
                 min_gap_s: Optional[float] = None) -> List[dict]:
    """Propose swap candidates.

    A swap shows as a jump in the gauge's offset against its peers (the other tags of the same unit), while
    a process change moves every gauge together. So for each tag with peers the test signal is the residual
    tag - mean(peers); for a tag without peers it is the tag itself (lower confidence, said so in the basis).
    At every index the median of the `window` samples after is compared with the median before; a candidate
    is a step larger than `min_step_sigma` robust-sigmas of the test signal. A step across a data gap longer
    than `min_gap_s` (default 6 x the cadence) needs only 3 sigma: a pulled gauge leaves a gap. The strongest
    candidate per tag per 2 x window region is kept.
    """
    if stream.index_kind != 'time_s':
        return []
    t = np.asarray(stream.index, dtype=float)
    n = len(t)
    if n < 2 * window + 2:
        return []
    names = tags or [c for c, ch in stream.channels.items() if np.issubdtype(np.asarray(ch.values).dtype, np.number)]
    all_num = [c for c, ch in stream.channels.items() if np.issubdtype(np.asarray(ch.values).dtype, np.number)]
    units = {c: stream.channels[c].unit for c in all_num}
    cad = float(np.median(np.diff(t))) if n > 1 else 60.0
    gap_s = min_gap_s if min_gap_s is not None else 6.0 * cad
    start_iso = stream.meta.get('start_time') if hasattr(stream, 'meta') else None
    t0 = _parse(start_iso) if start_iso else None
    out = []
    for c in names:
        x = np.asarray(stream.channels[c].values, dtype=float)
        peers = [p for p in all_num if p != c and units[p] == units[c] and p.split('_')[0] == c.split('_')[0]]
        if peers:
            pm = np.nanmedian(np.vstack([np.asarray(stream.channels[p].values, dtype=float) for p in peers]), axis=0)
            sigx = x - pm
            basis_peers = (f'offset against the median of {len(peers)} peer tags of the same unit' if len(peers) > 1 else
                           f'offset against the one peer tag ({peers[0]}): the step could be on either gauge - confirm from the work order')
        else:
            sigx = x
            basis_peers = 'no peer tag of the same unit: the raw signal was tested (a process change can look like a step)'
        v = sigx[~np.isnan(sigx)]
        if v.size < 2 * window:
            continue
        d = np.diff(v)
        sig = float(1.4826 * np.median(np.abs(d - np.median(d)))) / np.sqrt(2) if d.size > 8 else 0.0
        if not sig:
            sig = float(np.std(v)) or 1e-9
        steps = np.full(n, np.nan)
        for i in range(window, n - window):
            steps[i] = _median_level(sigx[i:i + window]) - _median_level(sigx[i - window:i])
        cands = []
        for i in range(window, n - window):
            if np.isnan(steps[i]):
                continue
            z = abs(steps[i]) / sig
            at_gap = (t[i] - t[i - 1]) > gap_s
            if z >= min_step_sigma or (at_gap and z >= 3.0):
                cands.append((z, i, at_gap))
        cands.sort(reverse=True)
        taken: List[int] = []
        for z, i, at_gap in cands:
            if any(abs(i - j) < 2 * window for j in taken):
                continue
            # refine to the sharpest point in the neighbourhood: the index where the one-step jump is largest
            lo, hi = max(1, i - window // 2), min(n - 1, i + window // 2)
            jumps = np.abs(np.diff(sigx[lo - 1:hi + 1]))
            jumps = np.where(np.isnan(jumps), -1, jumps)
            i_ref = int(lo + np.argmax(jumps)) if jumps.size else i
            taken.append(i)
            when = _iso(datetime.fromtimestamp(t0.timestamp() + t[i_ref], tz=timezone.utc)) if t0 else None
            conf = 'high' if (len(peers) > 1 and z >= 2 * min_step_sigma) or (at_gap and z >= min_step_sigma) else ('medium' if peers else 'low')
            out.append({'tag_id': c, 'index': i_ref, 'elapsed_s': float(t[i_ref]), 'swap_utc': when, 'step': round(float(steps[i]), 3),
                        'unit': units[c], 'z': round(float(z), 1), 'at_gap': bool(at_gap), 'confidence': conf,
                        'ambiguous_with': (peers[0] if len(peers) == 1 else None),
                        'basis': f'{basis_peers}; level after vs before ({window} samples each): {float(steps[i]):+.3g} {units[c]} = {z:.1f} sigma'
                                 + ('; across a data gap' if at_gap else '')})
    # peers share the residual: when several tags of one unit step at the same place, the one that moved most is the swap
    out.sort(key=lambda e: (e['elapsed_s'], e['tag_id']))
    kept = []
    used = [False] * len(out)
    for i, e in enumerate(out):
        if used[i]:
            continue
        group = [j for j in range(i, len(out)) if not used[j] and out[j]['unit'] == e['unit'] and abs(out[j]['index'] - e['index']) <= window]
        for j in group:
            used[j] = True
        biggest = max(group, key=lambda j: abs(out[j]['step']))
        others = [j for j in group if j != biggest]
        if others and all(abs(out[biggest]['step']) >= 1.5 * abs(out[j]['step']) for j in others):
            kept.append(out[biggest])
        else:
            for j in group:                                        # no clear winner (two gauges): report both, flagged
                if len(group) > 1:
                    out[j]['ambiguous_with'] = ', '.join(out[k]['tag_id'] for k in group if k != j)
                    out[j]['confidence'] = 'medium' if out[j]['confidence'] == 'high' else out[j]['confidence']
                kept.append(out[j])
    kept.sort(key=lambda e: (e['elapsed_s'], e['tag_id']))
    return kept


def segment_mask(stream, register: SwapRegister):
    """A copy of the stream with samples before each tag's latest swap set to NaN; returns (stream, notes)."""
    import copy
    latest = register.latest_by_tag()
    if not latest or stream.index_kind != 'time_s':
        return stream, []
    start_iso = stream.meta.get('start_time') if hasattr(stream, 'meta') else None
    if not start_iso:
        return stream, [{'tag_id': k, 'applied': False, 'reason': 'stream has no start time; cannot place the swap'} for k in latest]
    t0 = _parse(start_iso).timestamp()
    t = np.asarray(stream.index, dtype=float)
    s2 = copy.copy(stream)
    s2.channels = dict(stream.channels)
    notes = []
    for tag, e in latest.items():
        if tag not in s2.channels:
            continue
        cut = _parse(e['swap_utc']).timestamp() - t0
        ch = copy.copy(s2.channels[tag])
        v = np.array(ch.values, dtype=float)
        before = t < cut
        v[before] = np.nan
        ch.values = v
        s2.channels[tag] = ch
        notes.append({'tag_id': tag, 'applied': True, 'swap_utc': e['swap_utc'], 'new_serial': e['new_serial'],
                      'samples_excluded': int(before.sum()), 'samples_kept': int((~before).sum()), 'swap_id': e['swap_id']})
    return s2, notes
