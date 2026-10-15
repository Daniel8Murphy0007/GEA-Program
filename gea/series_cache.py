# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""series_cache - the charts' copy of a well's stream: every sample, in a form a window can be cut from quickly.

A drilling recorder's export is three million rows; reading it takes half a minute, and a chart that zooms cannot
wait that long on every turn of the wheel. The refresh that reads a well's file once for its reports also writes
this cache from the same read (`cache/series/<well>/` in the workspace): the index, each channel as float32, which
samples were flagged, and for a drilling recorder the depth pass's masks (drilling, the samples a depth table may
use, the excluded samples) and the running bottom of the hole. A window is then cut from memory-mapped arrays:

  * on the time axis, the window is split into equal time buckets and each bucket carries the minimum, the maximum
    and the mean of the samples in it and how many there were - an envelope, so a one-second spike survives a
    month-wide view; a bucket with no samples is a gap and the line breaks there, never bridged;
  * when the window holds fewer samples than buckets, the samples themselves are returned;
  * on the depth axis (a drilling recorder only), the drilling samples are binned by bit depth, kept off any hole a
    later sidetrack abandoned - the same samples the gamma log uses;
  * the drilling progress is the running bottom of the hole against days since the first sample.

The cache is a copy, never a source: it carries the source file's size and modification time, and a cache that
does not match its source is rebuilt from the source. It is left out of backups and rebuilt on demand.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

SCHEMA = 1
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(path: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(os.path.abspath(path), threading.Lock())


def _source_sig(path: Optional[str]) -> dict:
    if path and os.path.isfile(path):
        st = os.stat(path)
        return {'source': os.path.abspath(path), 'source_size': st.st_size, 'source_mtime': int(st.st_mtime)}
    return {'source': path, 'source_size': None, 'source_mtime': None}


def build(stream, cache_dir: str, extras: Optional[Dict[str, np.ndarray]] = None, source_key: Optional[str] = None) -> dict:
    """Write the cache for `stream` into `cache_dir` (replacing what is there) and return its meta."""
    os.makedirs(cache_dir, exist_ok=True)
    n = len(stream.index)
    src_path = (stream.meta or {}).get('path')
    meta = {'schema': SCHEMA, 'name': stream.name, 'n': int(n), 'index_kind': stream.index_kind, 'source_format': stream.source_format,
            'start_time': (stream.meta or {}).get('start_time'), 'channels': [], 'extras': [], **_source_sig(src_path)}
    if source_key:
        meta['source'] = source_key
    secs = (stream.meta or {}).get('sections')
    if secs:
        try:
            pairs = json.loads(secs) if isinstance(secs, str) else secs
            idx = np.asarray(stream.index, dtype=np.float64)
            meta['sections'] = [[float(idx[a]), float(idx[b - 1])] for a, b in pairs if b > a]
        except (ValueError, TypeError, IndexError):
            pass
    np.save(os.path.join(cache_dir, 'index.npy'), np.asarray(stream.index, dtype=np.float64))
    for k, (name, ch) in enumerate(stream.channels.items()):
        v = np.asarray(ch.values, dtype=np.float32)
        np.save(os.path.join(cache_dir, f'c{k}.npy'), v)
        rec = {'name': name, 'unit': ch.unit or '', 'file': f'c{k}.npy', 'present': int(np.count_nonzero(~np.isnan(v)))}
        q = getattr(ch, 'quality', None)
        if q is not None and len(q) == n:
            flagged = np.array([x not in (None, '', 'OK', 'GOOD') for x in q], dtype=bool)
            if flagged.any():
                np.save(os.path.join(cache_dir, f'q{k}.npy'), flagged)
                rec['flags'] = f'q{k}.npy'
        meta['channels'].append(rec)
    for name, arr in (extras or {}).items():
        a = np.asarray(arr)
        a = a.astype(bool) if a.dtype == bool else a.astype(np.float32)
        np.save(os.path.join(cache_dir, f'x_{name}.npy'), a)
        meta['extras'].append(name)
    with open(os.path.join(cache_dir, 'meta.json.tmp'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1)
    os.replace(os.path.join(cache_dir, 'meta.json.tmp'), os.path.join(cache_dir, 'meta.json'))
    return meta


class Cache:
    def __init__(self, cache_dir: str, meta: dict):
        self.dir, self.meta = cache_dir, meta
        self.index = np.load(os.path.join(cache_dir, 'index.npy'), mmap_mode='r')
        self._ch = {c['name']: c for c in meta['channels']}

    def channel(self, name: str) -> np.ndarray:
        if name not in self._ch:
            raise KeyError(f'no channel {name!r} in this well; it has: ' + ', '.join(self._ch))
        return np.load(os.path.join(self.dir, self._ch[name]['file']), mmap_mode='r')

    def flags(self, name: str) -> Optional[np.ndarray]:
        f = self._ch.get(name, {}).get('flags')
        return np.load(os.path.join(self.dir, f), mmap_mode='r') if f else None

    def extra(self, name: str) -> Optional[np.ndarray]:
        return np.load(os.path.join(self.dir, f'x_{name}.npy'), mmap_mode='r') if name in self.meta.get('extras', []) else None

    def unit(self, name: str) -> str:
        if name not in self._ch:
            raise KeyError(f'no channel {name!r} in this well; it has: ' + ', '.join(self._ch))
        return self._ch[name]['unit']


def load(cache_dir: str, source_path: Optional[str] = None) -> Optional[Cache]:
    """The cache in `cache_dir` if it exists and matches its source (when a source file is named), else None."""
    p = os.path.join(cache_dir, 'meta.json')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if meta.get('schema') != SCHEMA:
        return None
    if source_path and os.path.isfile(source_path):
        sig = _source_sig(source_path)
        if (meta.get('source_size'), meta.get('source_mtime')) != (sig['source_size'], sig['source_mtime']):
            return None
    try:
        return Cache(cache_dir, meta)
    except (OSError, ValueError):
        return None


def source_path(ws, well_id: str) -> Optional[str]:
    w = ws.well(well_id)
    if w['kind'] == 'file':
        return os.path.join(ws.path, 'wells', well_id, 'source', w['files'][0])
    if w['kind'] == 'live':
        return ws.latest_live_stream_csv(well_id)
    return None


def cache_dir(ws, well_id: str) -> str:
    return os.path.join(ws.path, 'cache', 'series', well_id)


def ensure(ws, well_id: str) -> Cache:
    """The well's cache, built from its source when it is missing or no longer matches the source."""
    d, src = cache_dir(ws, well_id), source_path(ws, well_id)
    c = load(d, src)
    if c is not None:
        return c
    with _lock(d):
        c = load(d, src)
        if c is not None:
            return c
        stream = ws.well_stream(well_id)
        extras = None
        if stream.source_format == 'edr':
            from .edr import chart_masks
            extras = chart_masks(stream)
        w = ws.well(well_id)
        key = None if src else f"catalog:{(w.get('source') or {}).get('entry')}:{(w.get('source') or {}).get('well')}"
        build(stream, d, extras=extras, source_key=key)
        return load(d, src)


def _clean(a: np.ndarray, nd: int = 4) -> list:
    return [None if (x != x) else round(float(x), nd) for x in a]


def _bucket(x: np.ndarray, v: np.ndarray, edges: np.ndarray, flags: Optional[np.ndarray] = None) -> dict:
    """Min, max, mean and count of v per bucket of x (x ascending); empty buckets are None (a gap)."""
    nb = len(edges) - 1
    starts = np.searchsorted(x, edges[:-1], side='left')
    ends = np.searchsorted(x, edges[1:], side='left')
    ends[-1] = np.searchsorted(x, edges[-1], side='right')
    v = np.asarray(v, dtype=np.float64)
    ok = ~np.isnan(v)
    cnt_all = ends - starts
    out_min = np.full(nb, np.nan); out_max = np.full(nb, np.nan); out_mean = np.full(nb, np.nan); cnt = np.zeros(nb, dtype=np.int64)
    flg = np.zeros(nb, dtype=bool)
    nz = np.flatnonzero(cnt_all > 0)
    if len(nz):
        s = starts[nz]
        vmin = np.where(ok, v, np.inf); vmax = np.where(ok, v, -np.inf); vsum = np.where(ok, v, 0.0)
        mn = np.minimum.reduceat(vmin, s); mx = np.maximum.reduceat(vmax, s)
        sm = np.add.reduceat(vsum, s); c = np.add.reduceat(ok.astype(np.int64), s)
        # reduceat runs to the next start; the last non-empty bucket must stop at its own end, not the array's
        last = nz[-1]
        if ends[last] < len(v):
            seg = slice(starts[last], ends[last])
            mn[-1] = np.min(vmin[seg]); mx[-1] = np.max(vmax[seg]); sm[-1] = np.sum(vsum[seg]); c[-1] = np.sum(ok[seg])
        good = c > 0
        out_min[nz[good]] = mn[good]; out_max[nz[good]] = mx[good]; out_mean[nz[good]] = sm[good] / c[good]; cnt[nz] = c
        if flags is not None:
            fr = np.add.reduceat(np.asarray(flags, dtype=np.int64), s)
            if ends[last] < len(v):
                fr[-1] = int(np.sum(flags[starts[last]:ends[last]]))
            flg[nz] = fr > 0
    return {'min': _clean(out_min), 'max': _clean(out_max), 'mean': _clean(out_mean), 'n': cnt.tolist(), 'flagged': flg.tolist()}


def window(c: Cache, channels: List[str], x0: Optional[float] = None, x1: Optional[float] = None, points: int = 1200) -> dict:
    """Each channel over [x0, x1] of the index: the samples when they fit, else an envelope per equal bucket."""
    idx = c.index
    n = len(idx)
    if n == 0:
        return {'mode': 'empty', 'x': [], 'channels': {}}
    lo, hi = float(idx[0]), float(idx[-1])
    x0 = lo if x0 is None else max(lo, float(x0)); x1 = hi if x1 is None else min(hi, float(x1))
    if x1 <= x0:
        x1 = min(hi, x0 + 1.0)
    points = int(max(50, min(points, 4000)))
    i0, i1 = int(np.searchsorted(idx, x0, 'left')), int(np.searchsorted(idx, x1, 'right'))
    m = i1 - i0
    out = {'x0': x0, 'x1': x1, 'extent': [lo, hi], 'samples': m, 'channels': {}}
    xs = np.asarray(idx[i0:i1], dtype=np.float64)
    if m <= points:
        out['mode'] = 'samples'; out['x'] = _clean(xs, 3)
        for name in channels:
            v = np.asarray(c.channel(name)[i0:i1], dtype=np.float64)
            fl = c.flags(name)
            out['channels'][name] = {'unit': c.unit(name), 'value': _clean(v), 'flagged': (np.asarray(fl[i0:i1]).tolist() if fl is not None else None)}
        return out
    edges = np.linspace(x0, x1, points + 1)
    out['mode'] = 'envelope'; out['x'] = _clean((edges[:-1] + edges[1:]) / 2.0, 3); out['bucket'] = round(float(edges[1] - edges[0]), 4)
    for name in channels:
        fl = c.flags(name)
        out['channels'][name] = {'unit': c.unit(name), **_bucket(xs, c.channel(name)[i0:i1], edges, (np.asarray(fl[i0:i1]) if fl is not None else None))}
    return out


def depth_window(c: Cache, channels: List[str], d0: Optional[float] = None, d1: Optional[float] = None, points: int = 1200,
                 depth_channel: str = 'Bit Depth') -> dict:
    """A drilling recorder's channels against depth: the drilling samples kept off abandoned hole, binned by depth."""
    drilling, keep_b = c.extra('drilling'), c.extra('keep_b')
    if drilling is None:
        raise ValueError('the depth axis is for a drilling recorder export: this well has no drilling samples marked')
    dep = np.asarray(c.channel(depth_channel), dtype=np.float64)
    sel = np.asarray(drilling, dtype=bool) & np.asarray(keep_b, dtype=bool) & ~np.isnan(dep)
    ii = np.flatnonzero(sel)
    if not len(ii):
        return {'mode': 'empty', 'x': [], 'channels': {}, 'axis': depth_channel}
    order = ii[np.argsort(dep[ii], kind='stable')]
    ds = dep[order]
    lo, hi = float(ds[0]), float(ds[-1])
    d0 = lo if d0 is None else max(lo, float(d0)); d1 = hi if d1 is None else min(hi, float(d1))
    if d1 <= d0:
        d1 = min(hi, d0 + 1.0)
    points = int(max(50, min(points, 4000)))
    edges = np.linspace(d0, d1, points + 1)
    out = {'mode': 'envelope', 'axis': depth_channel, 'x0': d0, 'x1': d1, 'extent': [lo, hi], 'samples': int(np.count_nonzero((ds >= d0) & (ds <= d1))),
           'x': _clean((edges[:-1] + edges[1:]) / 2.0, 2), 'bucket': round(float(edges[1] - edges[0]), 4), 'channels': {},
           'how': f"the drilling samples, kept off hole a later sidetrack abandoned, binned by {depth_channel.lower()}"}
    for name in channels:
        v = np.asarray(c.channel(name), dtype=np.float64)[order]
        out['channels'][name] = {'unit': c.unit(name), **_bucket(ds, v, edges)}
    return out


def progress(c: Cache, points: int = 1500) -> Optional[dict]:
    """Days since the first sample against the running bottom of the hole (a drilling recorder only)."""
    bottom = c.extra('bottom')
    if bottom is None:
        return None
    idx = np.asarray(c.index, dtype=np.float64)
    b = np.asarray(bottom, dtype=np.float64)
    ok = ~np.isnan(b)
    if not ok.any():
        return None
    t0 = float(idx[np.flatnonzero(ok)[0]])
    edges = np.linspace(t0, float(idx[-1]), int(max(50, min(points, 4000))) + 1)
    sel = np.flatnonzero(ok)
    r = _bucket(idx[sel], b[sel], edges)
    days = (edges[:-1] + edges[1:]) / 2.0
    start = c.meta.get('start_time')
    t_first = (datetime.fromisoformat(start) + timedelta(seconds=t0)).isoformat() if start else None
    return {'days': _clean((days - t0) / 86400.0, 4), 'bottom_max': r['max'], 'bottom_min': r['min'], 'first_utc': t_first,
            'how': 'the running bottom of the hole from the depth pass - stale and re-referenced depths excluded, a sidetrack as a step back'}


def connections(c: Cache, x0: float, x1: float, limit: int = 600) -> List[float]:
    """The times drilling stopped within [x0, x1] (the string coming off bottom), when there are few enough to mark."""
    dr = c.extra('drilling')
    if dr is None:
        return []
    idx = c.index
    i0, i1 = int(np.searchsorted(idx, x0, 'left')), int(np.searchsorted(idx, x1, 'right'))
    d = np.asarray(dr[i0:i1], dtype=bool)
    if len(d) < 2:
        return []
    off = np.flatnonzero(d[:-1] & ~d[1:]) + i0 + 1
    if len(off) > limit:
        return []
    return [round(float(idx[i]), 1) for i in off]
