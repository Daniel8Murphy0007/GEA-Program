# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""vibration - a vibration index read off the surface data every EDR sends, for rigs without a downhole vibration
tool: the surface torque's oscillation about its mean says how hard the string is stick-slipping (the bit stops,
the string winds up, the bit breaks loose; at surface the torque swings with the same period), the weight on bit's
and the hook load's oscillation say how much the string is whirling and bouncing. Each oscillation is its RMS about
the mean over the last minute, as a fraction of the mean - the same measure GEA's drill-string page calls the
oscillation index - turned into an amplitude ratio (root two times the RMS, for a sinusoid), and the two amplitude
ratios are scaled onto the index the agent was trained with in the twin, where a stick-slip of 1.2 (the limit)
swings the surface torque by 48% and a lateral vibration of 1.2 swings the weight by 24%. The index is the larger
of the two, as the twin's is. A surface proxy, labelled: the downhole tool's index, when the string carries one,
is taken instead, and the record says which it was."""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, Dict, Optional, Sequence, Tuple

import numpy as np

STATS = ('torque_mean', 'torque_rms', 'rpm_mean', 'rpm_rms', 'wob_mean', 'wob_rms', 'hkld_mean', 'hkld_rms', 'spp_mean', 'spp_rms')
CHANNELS = ('torque', 'rpm', 'wob', 'hkld', 'spp')
SSI_PER_INDEX = 0.4             # the twin: torque amplitude ratio = 0.4 x stick-slip index
LAT_PER_INDEX = 0.2             # the twin: weight amplitude ratio = 0.2 x lateral index
MIN_SAMPLES = 10
WINDOW_S = 60.0
SOURCE = 'derived from the surface data'
HOW = ('the surface torque, the weight on bit and the hook load over the last minute, each its RMS about its mean as a fraction of the mean, '
       'times root two for the amplitude; stick-slip = torque amplitude / 0.4, lateral = weight amplitude / 0.2 (the twin\'s scale, where 1.2 is '
       'the limit); the index is the larger. A surface proxy, not a downhole measurement.')


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


def stats(samples: Dict[str, Sequence[float]]) -> Dict[str, float]:
    """The window's statistics per channel: the mean and the RMS about the mean (NaN where fewer than
    MIN_SAMPLES values)."""
    out: Dict[str, float] = {}
    for ch in CHANNELS:
        vals = samples.get(ch)
        a = np.array([_f(v) for v in (vals if vals is not None else [])], dtype=float)
        a = a[np.isfinite(a)]
        if len(a) < MIN_SAMPLES:
            out[f'{ch}_mean'] = math.nan; out[f'{ch}_rms'] = math.nan
            continue
        out[f'{ch}_mean'] = float(a.mean())
        out[f'{ch}_rms'] = float(math.sqrt(float(((a - a.mean()) ** 2).mean())))
    return out


def _ratio(mean: float, rms: float) -> Optional[float]:
    if mean is None or rms is None or not (math.isfinite(mean) and math.isfinite(rms)) or mean <= 1e-6:
        return None
    return math.sqrt(2.0) * rms / mean


def index_from_stats(st: Dict[str, float]) -> Optional[dict]:
    """The index from the window's statistics; None when neither the torque nor the weight is there."""
    tq = _ratio(st.get('torque_mean'), st.get('torque_rms'))
    wob = _ratio(st.get('wob_mean'), st.get('wob_rms'))
    hk = _ratio(st.get('hkld_mean'), st.get('hkld_rms'))
    rpm = _ratio(st.get('rpm_mean'), st.get('rpm_rms'))
    spp = _ratio(st.get('spp_mean'), st.get('spp_rms'))
    if tq is None and wob is None:
        return None
    ss = (tq / SSI_PER_INDEX) if tq is not None else None
    # the hook load carries the weight's oscillation diluted by the string's weight: scale it back by the means
    hk_as_wob = None
    if hk is not None and st.get('wob_mean') and math.isfinite(st['wob_mean']) and st['wob_mean'] > 1e-6:
        hk_as_wob = hk * float(st['hkld_mean']) / float(st['wob_mean'])
    lat_ratio = max(v for v in (wob, hk_as_wob) if v is not None) if (wob is not None or hk_as_wob is not None) else None
    lat = (lat_ratio / LAT_PER_INDEX) if lat_ratio is not None else None
    parts = [v for v in (ss, lat) if v is not None]
    idx = max(parts)
    kind = 'stick-slip' if (ss is not None and (lat is None or ss >= lat)) else 'lateral'
    return {'index': round(idx, 3), 'stick_slip': (None if ss is None else round(ss, 3)), 'lateral': (None if lat is None else round(lat, 3)), 'kind': kind,
            'torque_amplitude': (None if tq is None else round(tq, 3)), 'wob_amplitude': (None if wob is None else round(wob, 3)),
            'rpm_amplitude': (None if rpm is None else round(rpm, 3)), 'spp_ripple': (None if spp is None else round(spp, 3)),
            'source': SOURCE, 'how': HOW}


def index(samples: Dict[str, Sequence[float]]) -> Optional[dict]:
    st = stats(samples)
    out = index_from_stats(st)
    if out is not None:
        out['n'] = int(max(len(samples[ch]) for ch in CHANNELS if samples.get(ch) is not None))
    return out


def combine_stats(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Several windows' statistics as one (a shadow replay's feet since the last decision): the means averaged, the
    RMS pooled."""
    out: Dict[str, float] = {}
    for ch in CHANNELS:
        ms = [float(r[f'{ch}_mean']) for r in rows if r.get(f'{ch}_mean') is not None and math.isfinite(float(r[f'{ch}_mean']))]
        rs = [float(r[f'{ch}_rms']) for r in rows if r.get(f'{ch}_rms') is not None and math.isfinite(float(r[f'{ch}_rms']))]
        out[f'{ch}_mean'] = float(np.mean(ms)) if ms else math.nan
        out[f'{ch}_rms'] = float(math.sqrt(float(np.mean(np.square(rs))))) if rs else math.nan
    return out


def words(v: dict, limit: float) -> str:
    """One clause for the reasons."""
    what = f"{v['kind']}" + (f", the surface torque swinging ±{100 * v['torque_amplitude']:.0f}%" if v['kind'] == 'stick-slip' and v.get('torque_amplitude') is not None
                            else (f", the weight swinging ±{100 * v['wob_amplitude']:.0f}%" if v.get('wob_amplitude') is not None else ''))
    return f"vibration {v['index']:.2f} {SOURCE} ({what}){' over the limit ' + f'{limit:g}' if v['index'] > limit else ''}"


class Window:
    """The last minute of the surface channels as the feed brings them, one sample per frame."""

    def __init__(self, window_s: float = WINDOW_S, max_n: int = 600):
        self.window_s = float(window_s)
        self.q: Deque[Tuple[float, Dict[str, float]]] = deque(maxlen=max_n)

    def append(self, t: float, **channels) -> None:
        vals = {ch: _f(channels.get(ch)) for ch in CHANNELS}
        if all(math.isnan(v) for v in vals.values()):
            return
        self.q.append((float(t), vals))

    def samples(self, now: Optional[float] = None) -> Dict[str, list]:
        if not self.q:
            return {ch: [] for ch in CHANNELS}
        t1 = self.q[-1][0] if now is None else float(now)
        keep = [v for t, v in self.q if t1 - t <= self.window_s]
        return {ch: [v[ch] for v in keep if math.isfinite(v[ch])] for ch in CHANNELS}

    def current(self, now: Optional[float] = None) -> Optional[dict]:
        return index(self.samples(now))

    def __len__(self) -> int:
        return len(self.q)
