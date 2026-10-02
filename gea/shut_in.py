# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""shut_in - find the shut-ins in a well's record and cut out each build-up.

A shut-in is a period with the well closed in: the rate channel at zero (or
the on-stream hours at zero) for at least a minimum duration, after at least
a minimum flowing time. During it the bottom-hole pressure builds up; that
build-up is the input to the pressure-transient analysis (`transient.py`).

Two ways to find one:

    rate-based (preferred)   a rate or on-stream channel says the well is closed;
    pressure-only            no rate channel: a sustained rise that starts with a
                             sharp change in slope and decays towards a plateau is
                             proposed as a build-up, marked `inferred: True`. A
                             person decides whether it was a shut-in.

Each shut-in carries its start and end, duration, the flowing time before it
(tp, the Horner producing time), the pressure at shut-in (p_wf), the final
pressure, the rise, the sample count, and a qualification with the rule that
failed when it does not qualify for analysis.

Configuration (`shut_in.json`, versioned like the other criteria files):

    rate_zero_fraction   rate below this fraction of the window's maximum = closed   (0.02)
    min_duration_h       shortest shut-in worth analysing                            (6)
    min_flowing_h        shortest flowing period before it for a usable tp           (24)
    min_samples          fewest build-up samples                                     (8)
    min_rise             smallest pressure rise, in the pressure channel's unit      (5)
    smooth               samples in the running median applied to the rate          (3)

Numpy only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import numpy as np

DEFAULT_SHUT_IN = {'name': 'default shut-in detection criteria', 'rate_zero_fraction': 0.02, 'min_duration_h': 6.0,
                   'min_flowing_h': 24.0, 'min_samples': 8, 'min_rise': 5.0, 'smooth': 3}


def load_criteria(path: Optional[str]) -> dict:
    c = dict(DEFAULT_SHUT_IN)
    if path and os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            c.update(json.load(f))
    return c


def _runmed(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) < k:
        return x
    out = np.array(x, dtype=float)
    h = k // 2
    for i in range(len(x)):
        w = x[max(0, i - h):i + h + 1]
        w = w[~np.isnan(w)]
        out[i] = np.median(w) if w.size else np.nan
    return out


def _stamp(stream, t_s: float) -> Optional[str]:
    st = stream.meta.get('start_time') if hasattr(stream, 'meta') else None
    if not st:
        return None
    t0 = datetime.fromisoformat(st.replace('Z', '+00:00'))
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    return (t0 + timedelta(seconds=float(t_s))).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def guess_channels(stream) -> Dict[str, Optional[str]]:
    """Pressure: the first downhole pressure channel; rate: a channel whose name says rate/oil/gas/qo; on-stream: hours on stream."""
    names = list(stream.channels)
    low = {n: n.lower() for n in names}
    p = next((n for n in names if low[n].startswith('p_') and 'psi' in low[n] and 'clean' in low[n]), None) \
        or next((n for n in names if low[n].startswith('p_') or 'pressure' in low[n] or 'bhp' in low[n] or 'dhp' in low[n]), None)
    rate = next((n for n in names if any(k in low[n] for k in ('oil_rate', 'qo', 'rate', 'bore_oil', 'oil_vol', 'gas_vol', 'liquid'))), None)
    on = next((n for n in names if 'on_stream' in low[n] or 'onstream' in low[n] or 'hours_on' in low[n]), None)
    return {'pressure': p, 'rate': rate, 'on_stream': on}


def detect_shut_ins(stream, pressure: Optional[str] = None, rate: Optional[str] = None, on_stream: Optional[str] = None,
                    criteria: Optional[dict] = None) -> dict:
    c = dict(DEFAULT_SHUT_IN, **(criteria or {}))
    if stream.index_kind != 'time_s':
        return {'shut_ins': [], 'mode': 'none', 'reason': 'a depth-indexed log has no time axis', 'criteria': c}
    g = guess_channels(stream)
    pressure = pressure or g['pressure']
    rate = rate or g['rate']
    on_stream = on_stream or g['on_stream']
    if not pressure or pressure not in stream.channels:
        return {'shut_ins': [], 'mode': 'none', 'reason': 'no pressure channel', 'criteria': c}
    t = np.asarray(stream.index, dtype=float)
    p = np.asarray(stream.channels[pressure].values, dtype=float)
    n = len(t)
    closed = None
    mode = 'pressure-only'
    if on_stream and on_stream in stream.channels:
        closed = np.asarray(stream.channels[on_stream].values, dtype=float) <= 0.0
        mode = 'on-stream hours'
    elif rate and rate in stream.channels:
        q = _runmed(np.asarray(stream.channels[rate].values, dtype=float), int(c['smooth']))
        qmax = float(np.nanmax(q)) if np.any(~np.isnan(q)) else 0.0
        closed = q <= c['rate_zero_fraction'] * qmax if qmax > 0 else np.zeros(n, bool)
        mode = f'rate ({rate})'
    if closed is None:
        closed = _infer_closed_from_pressure(t, p, c)
    closed = np.asarray(closed, bool) & ~np.isnan(p)
    out = []
    i = 0
    while i < n:
        if not closed[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and closed[j + 1]:
            j += 1
        # flowing time before: back to the previous closed sample (or the record start)
        k = i - 1
        while k >= 0 and not closed[k]:
            k -= 1
        tp_h = (t[i] - t[k + 1]) / 3600.0 if i > 0 else 0.0
        dur_h = (t[j] - t[i]) / 3600.0
        seg = p[i:j + 1]
        valid = seg[~np.isnan(seg)]
        rise = float(valid[-1] - valid[0]) if valid.size >= 2 else 0.0
        reason = None
        if dur_h < c['min_duration_h']:
            reason = f"DURATION_BELOW_MIN:{dur_h:.1f}h<{c['min_duration_h']:g}h"
        elif tp_h < c['min_flowing_h']:
            reason = f"FLOWING_TIME_BELOW_MIN:{tp_h:.1f}h<{c['min_flowing_h']:g}h"
        elif valid.size < c['min_samples']:
            reason = f"SAMPLES_BELOW_MIN:{valid.size}<{c['min_samples']}"
        elif rise < c['min_rise']:
            reason = f"RISE_BELOW_MIN:{rise:.1f}<{c['min_rise']:g}"
        out.append({'shut_in_id': f'SI-{len(out) + 1:03d}', 'start_index': int(i), 'end_index': int(j), 'start_utc': _stamp(stream, t[i]),
                    'end_utc': _stamp(stream, t[j]), 'start_s': float(t[i]), 'end_s': float(t[j]), 'duration_h': round(dur_h, 2),
                    'flowing_before_h': round(tp_h, 2), 'p_wf': (round(float(valid[0]), 2) if valid.size else None),
                    'p_end': (round(float(valid[-1]), 2) if valid.size else None), 'rise': round(rise, 2), 'n': int(valid.size),
                    'qualified': reason is None, 'reason': reason, 'inferred': mode == 'pressure-only', 'pressure_channel': pressure,
                    'unit': stream.channels[pressure].unit})
        i = j + 1
    return {'shut_ins': out, 'mode': mode, 'pressure_channel': pressure, 'rate_channel': rate if mode.startswith('rate') else None,
            'on_stream_channel': on_stream if mode.startswith('on-stream') else None, 'criteria': c,
            'n_qualified': sum(1 for s in out if s['qualified'])}


def _infer_closed_from_pressure(t: np.ndarray, p: np.ndarray, c: dict) -> np.ndarray:
    """Pressure-only: a build-up starts where the slope turns sharply positive and stays positive while decaying."""
    n = len(t)
    closed = np.zeros(n, bool)
    if n < 2 * int(c['min_samples']):
        return closed
    ps = _runmed(p, 5)
    dp = np.gradient(ps, t) * 3600.0                         # unit per hour
    sig = float(1.4826 * np.nanmedian(np.abs(dp - np.nanmedian(dp)))) or float(np.nanstd(dp)) or 1e-9
    i = 1
    while i < n:
        if dp[i] > 4 * sig and dp[i - 1] <= 4 * sig:          # a sharp start of a rise
            j = i
            while j + 1 < n and dp[j + 1] > 0 and not np.isnan(ps[j + 1]):
                j += 1
            if (t[j] - t[i]) / 3600.0 >= c['min_duration_h'] and dp[j] < dp[i]:     # decaying rise: a build-up, not a ramp
                closed[i:j + 1] = True
            i = j + 1
        else:
            i += 1
    return closed


def extract_buildup(stream, shut_in: dict, pressure: Optional[str] = None) -> dict:
    """The build-up arrays for one shut-in: dt_h (hours since shut-in), p_ws, tp_h, p_wf."""
    pressure = pressure or shut_in.get('pressure_channel')
    t = np.asarray(stream.index, dtype=float)
    p = np.asarray(stream.channels[pressure].values, dtype=float)
    i, j = shut_in['start_index'], shut_in['end_index']
    # the clock starts at the last flowing sample: the well closed between it and the first closed sample, and its
    # pressure is p_wf. With no flowing sample on record the first closed sample stands in (said so in the result).
    k = i - 1
    while k >= 0 and np.isnan(p[k]):
        k -= 1
    if k >= 0:
        t_shut, p_wf, first = t[k], float(p[k]), i
        basis = 'shut-in clock and p_wf from the last flowing sample'
    else:
        t_shut, p_wf, first = t[i], float(p[i]), i + 1
        basis = 'no flowing sample before the shut-in: the first closed sample stands in for p_wf'
    seg_t, seg_p = t[first:j + 1], p[first:j + 1]
    m = ~np.isnan(seg_p)
    seg_t, seg_p = seg_t[m], seg_p[m]
    dt_h = (seg_t - t_shut) / 3600.0
    return {'dt_h': dt_h.tolist(), 'p_ws': seg_p.tolist(), 'tp_h': float(shut_in['flowing_before_h']), 'p_wf': p_wf, 'basis': basis,
            'unit': stream.channels[pressure].unit, 'shut_in_id': shut_in['shut_in_id'], 'pressure_channel': pressure}
