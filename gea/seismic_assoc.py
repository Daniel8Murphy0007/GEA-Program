# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_assoc - association: several stations heard the same thing, or they did not.

Until now the leg could say what one station heard, what direction an array heard it from, and where two
arrays' bearings cross. It could not say the one thing every monitoring pipeline is built around: that the
arrivals at three or more stations belong to one event, and where and when that event was. SeisComP and
Earthworm have an associator at their centre; this program had none, so an earthquake inside a Seismicity
Response Area was known only from the catalogue somebody else published.

The method is the classic one, in three honest pieces:

- a pick: the onset of an impulsive arrival on one record, found by the short-term over long-term average
  ratio (Allen's detector) and refined back to the sample where the energy first left the background. A pick
  carries its time, its signal-to-noise ratio, and the station it came from, and nothing else;
- an association: a set of picks, one per station, consistent with a single origin under a declared velocity.
  The origin is found by a grid search over the ground around the stations with the origin time solved at
  every node, and the set is accepted when three or more stations fit with a root-mean-square residual
  inside a declared tolerance. A pick that will not fit is dropped as long as three remain; fewer than three
  stations is NOT ASSOCIATED, not a located event with a wide error;
- a location: the best node, with the region of nodes whose misfit is within one pick-uncertainty of the best,
  reported as its east-west and north-south extent. That region is what the data can say about where the
  event was, not a formal error ellipse; it is called a misfit region here and nowhere called a confidence
  interval.

What this module will not call a measurement:

- a depth: the hypocentre's depth is declared (every plan on file works from a regional value), and the
  location is of the epicentre under it. A straight ray at one velocity on a flat earth is the model, and it
  is named on every result;
- a magnitude: it needs the instrument response and an attenuation relation, and neither is in this band.
  An associated event carries the signal-to-noise ratio at every station and no magnitude;
- a match to the catalogue beyond what time and distance say: an associated event within the declared time
  window of a catalogue event, and within the misfit region's extent plus the catalogue's own location
  uncertainty, AGREES; otherwise it DIFFERS and both positions are printed. Which one is right is not decided
  here;
- an event from fewer than three stations, or from picks on an array's sensors alone - an array is one
  station here, its sensors' picks reduced to one by the median, because sensors a few hundred metres apart
  do not constrain an epicentre tens of kilometres away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import geodesy as G
from .seismic import Trace
from .seismic_array import local_xy, xy_to_latlon

DEFAULT_VP_KM_S = 5.8          # a crustal P velocity of the kind the plans on file work from; declared, never fitted
DEFAULT_DEPTH_KM = 6.0         # the hypocentral depth the location is made under; declared
DEFAULT_PICK_SIGMA_S = 0.05    # one pick uncertainty, in seconds, for the misfit region


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


# --------------------------------------------------------------------------------------------------------------
# picks
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Pick:
    station: str
    time: float                      # epoch seconds UTC
    snr: float
    duration_s: float
    channel: str = ''
    used: bool = False


def picks(trace: Trace, sta_s: float = 0.5, lta_s: float = 10.0, on: float = 4.0, off: float = 1.5,
          band: Optional[Tuple[float, float]] = None, min_gap_s: float = 2.0, max_picks: int = 200) -> List[Pick]:
    """Onsets on one record by the short-term over long-term average of the squared signal, each onset refined
    back to where the short-term energy first rose above twice the long-term level, so the time is the first
    energy and not the point where the ratio crossed the trigger."""
    x = np.asarray(trace.data, dtype=float)
    fs = float(trace.sample_rate)
    if x.size < int(lta_s * fs) + 10 or fs <= 0:
        return []
    if band:
        from .seismic_array import bandpass
        x = bandpass(x, fs, (band[0], band[1]))
    x = x - np.mean(x)
    e = x * x
    n_sta, n_lta = max(2, int(sta_s * fs)), max(10, int(lta_s * fs))
    n_ref = max(2, int(0.05 * fs))                               # the refinement window: short, so the onset is the first energy, not half a window early
    c = np.cumsum(np.concatenate([[0.0], e]))
    sta = (c[n_sta:] - c[:-n_sta]) / n_sta                      # ends at sample i (length N-n_sta+1)
    lta = (c[n_lta:] - c[:-n_lta]) / n_lta
    ref = (c[n_ref:] - c[:-n_ref]) / n_ref                      # ends at sample i (length N-n_ref+1)
    # align: ratio at sample i uses sta ending at i and lta ending at i - n_sta (the background before the window)
    k = len(x) - n_lta - n_sta + 1
    if k <= 0:
        return []
    r = sta[n_lta:n_lta + k] / np.maximum(lta[:k], 1e-30)
    base = n_lta + n_sta - 1                                     # sample index of r[0]
    out: List[Pick] = []
    i = 0
    last_t = -1e18
    while i < k and len(out) < max_picks:
        if r[i] >= on:
            j = i
            while j < k and r[j] >= off:
                j += 1
            # refine the onset: from the trigger, walk back while the energy in a 50 ms window ending here is still
            # well above the background. The threshold is the larger of six times the background and a twentieth
            # of the arrival's own peak energy - twice the background is crossed by noise often enough that the
            # walk ran on into the quiet before the onset and every pick came out early (the first scene did)
            peak = float(ref[base + i - n_ref + 1: base + j - n_ref + 2].max()) if j > i else float(ref[base + i - n_ref + 1])
            thr = max(6.0 * lta[i], 0.05 * peak)
            m = base + i                                         # absolute sample of the trigger
            lo = base + i - n_sta
            while m > lo and m - n_ref + 1 >= 0 and ref[m - n_ref + 1] > thr:
                m -= 1
            t = trace.starttime + (m + 1) / fs
            if t - last_t >= min_gap_s:
                out.append(Pick(trace.station or trace.id, t, round(float(r[i:j].max()), 2) if j > i else round(float(r[i]), 2),
                                round((j - i) / fs, 3), trace.channel or ''))
                last_t = t
            i = j + 1
        else:
            i += 1
    return out


def collapse_array_picks(raw: Sequence[Pick], station_id: str, within_s: float = 0.5, min_sensors: int = 2) -> List[Pick]:
    """An array is one station here. Its sensors' picks that fall within `within_s` of each other become one
    pick at the median time, carrying the median signal-to-noise ratio and how many sensors agreed; a pick that
    fewer than `min_sensors` sensors share is dropped, because one sensor's burst is not an arrival at the array."""
    ps = sorted(raw, key=lambda p: p.time)
    out: List[Pick] = []
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1].time - ps[i].time <= within_s:
            j += 1
        grp = ps[i:j + 1]
        if len(grp) >= min_sensors:
            out.append(Pick(station_id, float(np.median([p.time for p in grp])), float(np.median([p.snr for p in grp])),
                            float(np.median([p.duration_s for p in grp])), channel=f'{len(grp)} sensor(s)'))
        i = j + 1
    return out


# --------------------------------------------------------------------------------------------------------------
# stations and the model
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Station:
    id: str
    lat: float
    lon: float
    datum: str = 'WGS84'
    kind: str = 'single'
    x_km: float = 0.0
    y_km: float = 0.0

    def wgs84(self) -> Tuple[float, float]:
        p = G.to_wgs84(self.lat, self.lon, 0.0, G.datum_name(self.datum))
        return p['lat'], p['lon']


def frame(stations: Sequence[Station]) -> dict:
    """A local east-north frame on WGS84 about the stations' centroid, every station carried to its datum first."""
    ll = [s.wgs84() for s in stations]
    lat0 = sum(a for a, _ in ll) / len(ll); lon0 = sum(b for _, b in ll) / len(ll)
    for s, (la, lo) in zip(stations, ll):
        s.x_km, s.y_km = local_xy(la, lo, lat0, lon0)
    return {'lat0': lat0, 'lon0': lon0, 'n': len(stations),
            'aperture_km': round(max((math.hypot(a.x_km - b.x_km, a.y_km - b.y_km) for a in stations for b in stations), default=0.0), 2)}


def travel_time(x_km: float, y_km: float, s: Station, vp: float, depth_km: float) -> float:
    return math.sqrt((x_km - s.x_km) ** 2 + (y_km - s.y_km) ** 2 + depth_km ** 2) / vp


# --------------------------------------------------------------------------------------------------------------
# the grid search
# --------------------------------------------------------------------------------------------------------------
def locate(pset: Dict[str, float], stations: Dict[str, Station], vp: float = DEFAULT_VP_KM_S, depth_km: float = DEFAULT_DEPTH_KM,
           half_km: float = 40.0, step_km: float = 0.5, pick_sigma_s: float = DEFAULT_PICK_SIGMA_S, refine: bool = True,
           centre: Optional[Tuple[float, float]] = None) -> dict:
    """One origin from one pick per station: a grid over the ground about the stations, the origin time solved
    at every node as the mean of (pick - travel time), the node with the least RMS residual, and the region of
    nodes within one pick uncertainty of it."""
    ids = [k for k in pset if k in stations]
    if len(ids) < 3:
        return {'status': 'NOT ASSOCIATED', 'detail': f'{len(ids)} station(s): an epicentre needs three', 'n_stations': len(ids)}
    sx = np.array([stations[k].x_km for k in ids]); sy = np.array([stations[k].y_km for k in ids]); tp = np.array([pset[k] for k in ids])
    cx, cy = centre if centre is not None else (float(sx.mean()), float(sy.mean()))
    gx = np.arange(cx - half_km, cx + half_km + 1e-9, step_km); gy = np.arange(cy - half_km, cy + half_km + 1e-9, step_km)
    X, Y = np.meshgrid(gx, gy)
    tt = np.sqrt((X[..., None] - sx) ** 2 + (Y[..., None] - sy) ** 2 + depth_km ** 2) / vp       # (ny, nx, n)
    t0 = np.mean(tp - tt, axis=-1)                                                                 # origin time per node
    res = tp - (t0[..., None] + tt)
    rms = np.sqrt(np.mean(res ** 2, axis=-1))
    iy, ix = np.unravel_index(int(np.argmin(rms)), rms.shape)
    best = {'x_km': float(X[iy, ix]), 'y_km': float(Y[iy, ix]), 't0': float(t0[iy, ix]), 'rms_s': float(rms[iy, ix])}
    if refine and step_km > 0.1:                      # a finer grid about the best node, so the answer is not quantised to the coarse step
        fine = locate(pset, stations, vp, depth_km, half_km=2.0 * step_km, step_km=step_km / 10.0, pick_sigma_s=pick_sigma_s, refine=False,
                      centre=(best['x_km'], best['y_km']))   # about the coarse best, not the stations' centroid (the first scene's second event)
        if fine.get('status') == 'LOCATED':
            # keep the coarse grid's misfit region (it spans the whole search) and the fine grid's best point
            fine_best = fine['best']
            best = {**best, **{k: fine_best[k] for k in ('x_km', 'y_km', 't0', 'rms_s')}}
    # the misfit region: nodes whose RMS is within one pick sigma of the best
    inside = rms <= best['rms_s'] + pick_sigma_s
    ex = (float(X[inside].min()), float(X[inside].max())) if inside.any() else (best['x_km'], best['x_km'])
    ey = (float(Y[inside].min()), float(Y[inside].max())) if inside.any() else (best['y_km'], best['y_km'])
    on_edge = (ex[0] <= gx[0] + 1e-9 or ex[1] >= gx[-1] - 1e-9 or ey[0] <= gy[0] + 1e-9 or ey[1] >= gy[-1] - 1e-9)
    residuals = {k: round(float(pset[k] - (best['t0'] + travel_time(best['x_km'], best['y_km'], stations[k], vp, depth_km))), 3) for k in ids}
    return {'status': 'LOCATED', 'best': best, 'residuals_s': residuals, 'n_stations': len(ids), 'stations': ids,
            'region_km': {'east': [round(ex[0], 2), round(ex[1], 2)], 'north': [round(ey[0], 2), round(ey[1], 2)],
                          'extent_east_km': round(ex[1] - ex[0], 2), 'extent_north_km': round(ey[1] - ey[0], 2), 'nodes': int(inside.sum()),
                          'reaches_grid_edge': bool(on_edge)},
            'grid': {'half_km': half_km, 'step_km': step_km, 'nodes': int(rms.size)},
            'model': {'vp_km_s': vp, 'depth_km': depth_km, 'pick_sigma_s': pick_sigma_s,
                      'basis': 'a straight ray at one velocity on a flat earth, the hypocentre at the declared depth'}}


def associate(all_picks: Sequence[Pick], stations: Dict[str, Station], vp: float = DEFAULT_VP_KM_S, depth_km: float = DEFAULT_DEPTH_KM,
              rms_tol_s: float = 0.15, pick_sigma_s: float = DEFAULT_PICK_SIGMA_S, half_km: float = 40.0, step_km: float = 0.5,
              min_stations: int = 3, frame_info: Optional[dict] = None) -> dict:
    """Picks from several stations into events, or into a reason there is none.

    Each unused pick in turn is a candidate first arrival; the picks at other stations that could follow it
    under the model (within the aperture's travel time plus the tolerance) are gathered, the earliest per
    station; the set is located; while the RMS is outside the tolerance and more than min_stations remain,
    the pick with the largest residual is dropped. A set that fits is an event and its picks are used."""
    fr = frame_info or frame(list(stations.values()))
    window = fr['aperture_km'] / vp + 4.0 * rms_tol_s
    picks_sorted = sorted(all_picks, key=lambda p: p.time)
    events: List[dict] = []
    refused: List[dict] = []
    for p in picks_sorted:
        if p.used or p.station not in stations:
            continue
        cand: Dict[str, Pick] = {p.station: p}
        for q in picks_sorted:
            if q.used or q is p or q.station not in stations or q.station in cand:
                continue
            if 0.0 <= q.time - p.time <= window:
                cand[q.station] = q
        if len(cand) < min_stations:
            refused.append({'first_pick': _iso(p.time), 'station': p.station, 'n_stations': len(cand),
                            'why': f'{len(cand)} station(s) within {window:.1f} s: fewer than {min_stations}'})
            continue
        trial = dict(cand)
        loc = None
        while len(trial) >= min_stations:
            loc = locate({k: v.time for k, v in trial.items()}, stations, vp, depth_km, half_km, step_km, pick_sigma_s)
            if loc['status'] != 'LOCATED' or loc['best']['rms_s'] <= rms_tol_s:
                break
            worst = max(loc['residuals_s'], key=lambda k: abs(loc['residuals_s'][k]))
            trial.pop(worst)
        if loc and loc['status'] == 'LOCATED' and loc['best']['rms_s'] <= rms_tol_s and len(trial) >= min_stations:
            for v in trial.values():
                v.used = True
            lat, lon = xy_to_latlon(loc['best']['x_km'], loc['best']['y_km'], fr['lat0'], fr['lon0'])
            dropped = sorted(set(cand) - set(trial))
            events.append({'id': f"assoc-{_iso(loc['best']['t0'])[:19].replace(':', '').replace('-', '')}",
                           'origin_utc': _iso(loc['best']['t0']), 'lat': round(lat, 5), 'lon': round(lon, 5), 'datum': 'WGS84',
                           'depth_km': depth_km, 'depth_basis': 'declared', 'rms_s': round(loc['best']['rms_s'], 3),
                           'n_stations': len(trial), 'stations': sorted(trial), 'dropped': dropped,
                           'picks': {k: {'time': _iso(v.time), 'snr': v.snr, 'residual_s': loc['residuals_s'][k]} for k, v in trial.items()},
                           'region_km': loc['region_km'], 'magnitude': None,
                           'magnitude_basis': 'not estimated: needs the instrument response and an attenuation relation, neither in this band',
                           'model': loc['model']})
        else:
            refused.append({'first_pick': _iso(p.time), 'station': p.station, 'n_stations': len(cand),
                            'why': (f"no set of {min_stations} or more fits within {rms_tol_s} s RMS (best {loc['best']['rms_s']:.2f} s)"
                                    if loc and loc.get('best') else 'no location')})
    unused = [q for q in picks_sorted if not q.used]
    return {'protocol': 'seismic_assoc.associate/1', 'n_picks': len(picks_sorted), 'n_events': len(events), 'events': events,
            'refused': refused, 'n_unused_picks': len(unused), 'frame': fr,
            'model': {'vp_km_s': vp, 'depth_km': depth_km, 'rms_tol_s': rms_tol_s, 'pick_sigma_s': pick_sigma_s, 'min_stations': min_stations,
                      'window_s': round(window, 2)},
            'basis': ('one pick per station per event; a grid search for the origin under a straight-ray, one-velocity, flat-earth model at a '
                      'declared depth; three or more stations within the RMS tolerance, the worst pick dropped while more than three remain'),
            'not_a_measurement': ['a depth: declared, not located', 'a magnitude: not estimated here',
                                  'an event from fewer than three stations', 'the misfit region as a formal error ellipse',
                                  'which of two positions is right when the catalogue and the stations differ']}


# --------------------------------------------------------------------------------------------------------------
# against the catalogue
# --------------------------------------------------------------------------------------------------------------
def compare_catalog(assoc: dict, catalog_events: Sequence[dict], time_window_s: float = 30.0, catalog_sigma_km: float = 2.0) -> dict:
    """Each associated event against the catalogue: the nearest catalogue event in time, the separation on the
    ground, and AGREES when it lies within the misfit region's extent plus the catalogue's own uncertainty;
    and each catalogue event the stations did not associate, named."""
    from .permits import parse_date
    rows = []
    matched_cat = set()
    for ev in assoc['events']:
        t = datetime.strptime(ev['origin_utc'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
        best = None
        for i, c in enumerate(catalog_events):
            ct = parse_date(c['time'])
            if ct is None:
                continue
            dt = abs(ct.timestamp() - t)
            if dt <= time_window_s and (best is None or dt < best[0]):
                best = (dt, i, c)
        if best is None:
            rows.append({'event': ev['id'], 'origin_utc': ev['origin_utc'], 'catalog_event': None, 'standing': 'NOT IN CATALOGUE',
                         'detail': f'no catalogue event within {time_window_s:.0f} s'})
            continue
        dt, i, c = best
        matched_cat.add(i)
        g = G.geodesic_m(ev['lat'], ev['lon'], c['lat'], c['lon'])
        sep_km = g['distance_m'] / 1000.0
        allow = 0.5 * max(ev['region_km']['extent_east_km'], ev['region_km']['extent_north_km']) + catalog_sigma_km
        rows.append({'event': ev['id'], 'origin_utc': ev['origin_utc'], 'catalog_event': c.get('event_id'), 'catalog_time': c['time'],
                     'catalog_magnitude': c.get('magnitude'), 'dt_s': round(dt, 2), 'separation_km': round(sep_km, 2), 'allowed_km': round(allow, 2),
                     'standing': 'AGREES' if sep_km <= allow else 'DIFFERS',
                     'detail': (f"{sep_km:.1f} km from the catalogue's position, {dt:.1f} s from its origin time; the stations' misfit region plus the "
                                f"catalogue's {catalog_sigma_km:g} km allows {allow:.1f} km")})
    missed = [{'catalog_event': c.get('event_id'), 'time': c['time'], 'magnitude': c.get('magnitude'), 'standing': 'NOT ASSOCIATED BY THE STATIONS'}
              for i, c in enumerate(catalog_events) if i not in matched_cat]
    return {'rows': rows, 'missed': missed, 'n_agree': sum(1 for r in rows if r['standing'] == 'AGREES'),
            'n_differ': sum(1 for r in rows if r['standing'] == 'DIFFERS'), 'n_not_in_catalogue': sum(1 for r in rows if r['catalog_event'] is None),
            'time_window_s': time_window_s, 'catalog_sigma_km': catalog_sigma_km,
            'not_a_measurement': ['which position is right when they differ: both are printed', 'a magnitude for an event the catalogue does not carry']}


# --------------------------------------------------------------------------------------------------------------
# the labelled scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_scene(seed: int = 11, fs: float = 100.0, minutes: float = 20.0, vp: float = DEFAULT_VP_KM_S, depth_km: float = DEFAULT_DEPTH_KM) -> dict:
    """Five stations round a site, two events inside the ring and one far outside it, each an impulsive arrival
    at every station at the travel time the model gives plus a small timing jitter; one station carries a
    spurious burst that belongs to nothing; one event is seen by only two stations and must be refused."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = 31.95, -102.25
    sts = {'STA1': Station('STA1', 31.99, -102.30), 'STA2': Station('STA2', 31.99, -102.19, datum='NAD27'),
           'STA3': Station('STA3', 31.90, -102.20), 'STA4': Station('STA4', 31.90, -102.31), 'STA5': Station('STA5', 31.95, -102.25)}
    fr = frame(list(sts.values()))
    t_start = datetime(2026, 3, 5, 11, 0, 0, tzinfo=timezone.utc).timestamp()
    n = int(minutes * 60 * fs)
    truth_events = [{'id': 'ev-A', 't0': t_start + 180.0, 'lat': 31.955, 'lon': -102.235, 'seen': ['STA1', 'STA2', 'STA3', 'STA4', 'STA5']},
                    {'id': 'ev-B', 't0': t_start + 600.0, 'lat': 31.925, 'lon': -102.275, 'seen': ['STA1', 'STA2', 'STA3', 'STA4', 'STA5']},
                    {'id': 'ev-C', 't0': t_start + 900.0, 'lat': 31.97, 'lon': -102.22, 'seen': ['STA2', 'STA5']}]        # two stations: refused
    traces = {}
    for sid, s in sts.items():
        x = rng.normal(0, 1.0, n)
        for ev in truth_events:
            if sid not in ev['seen']:
                continue
            ex, ey = local_xy(ev['lat'], ev['lon'], fr['lat0'], fr['lon0'])
            tt = travel_time(ex, ey, s, vp, depth_km) + rng.normal(0, 0.02)
            i0 = int(round((ev['t0'] + tt - t_start) * fs))
            k = np.arange(0, int(6 * fs))
            burst = 25.0 * np.exp(-k / (1.2 * fs)) * np.sin(2 * np.pi * 8.0 * k / fs)
            x[i0:i0 + len(k)] += burst[:max(0, min(len(k), n - i0))]
        if sid == 'STA3':                                                  # a spurious burst that belongs to nothing
            i0 = int(round(420.0 * fs)); k = np.arange(0, int(3 * fs))
            x[i0:i0 + len(k)] += 20.0 * np.exp(-k / (0.8 * fs)) * np.sin(2 * np.pi * 12.0 * k / fs)
        traces[sid] = Trace('XX', sid, '', 'HHZ', t_start, fs, x.astype(np.float32), 'synthetic', 'FLOAT32')
    catalog = [{'event_id': 'cat-A', 'time': _iso(truth_events[0]['t0'] + 0.4)[:19] + 'Z', 'magnitude': 2.1, 'lat': 31.956, 'lon': -102.234},
               {'event_id': 'cat-B', 'time': _iso(truth_events[1]['t0'] - 0.3)[:19] + 'Z', 'magnitude': 2.4, 'lat': 31.94, 'lon': -102.30},   # ~2.7 km off: DIFFERS
               {'event_id': 'cat-D', 'time': _iso(t_start + 1100.0)[:19] + 'Z', 'magnitude': 1.2, 'lat': 31.93, 'lon': -102.26}]                # nobody picked it
    return {'stations': sts, 'traces': traces, 'frame': fr, 'vp': vp, 'depth_km': depth_km, 'catalog': catalog, 'truth': truth_events,
            'label': 'SIMULATION_SELF_TEST'}


def selftest() -> dict:
    sc = synthetic_scene()
    all_picks: List[Pick] = []
    for sid, tr in sc['traces'].items():
        all_picks += picks(tr, band=(2.0, 20.0))
    res = associate(all_picks, sc['stations'], sc['vp'], sc['depth_km'], frame_info=sc['frame'])
    cmp = compare_catalog(res, sc['catalog'])
    tr = sc['truth']
    def err_km(ev, t):
        return G.geodesic_m(ev['lat'], ev['lon'], t['lat'], t['lon'])['distance_m'] / 1000.0
    evs = sorted(res['events'], key=lambda e: e['origin_utc'])
    eA = evs[0] if evs else None; eB = evs[1] if len(evs) > 1 else None
    checks = {
        'picks_found': sum(1 for p in all_picks if p.station == 'STA5') >= 3 and sum(1 for p in all_picks if p.station == 'STA3') >= 3,
        'two_events': res['n_events'] == 2,
        'event_A_located': bool(eA) and eA['n_stations'] == 5 and err_km(eA, tr[0]) < 1.5 and abs(
            datetime.strptime(eA['origin_utc'][:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp() - tr[0]['t0']) < 0.3,
        'event_B_located': bool(eB) and eB['n_stations'] == 5 and err_km(eB, tr[1]) < 1.5,
        'two_station_event_refused': any(r['n_stations'] == 2 for r in res['refused']) and not any(e['n_stations'] < 3 for e in res['events']),
        'spurious_pick_unused': res['n_unused_picks'] >= 3,
        'residuals_small': all(abs(v) < 0.15 for e in evs for v in (p['residual_s'] for p in e['picks'].values())),
        'region_finite': all(not e['region_km']['reaches_grid_edge'] and 0 < e['region_km']['extent_east_km'] < 10 for e in evs),
        'no_magnitude': all(e['magnitude'] is None for e in evs),
        'catalogue': cmp['n_agree'] == 1 and cmp['n_differ'] == 1 and len(cmp['missed']) == 1 and cmp['missed'][0]['catalog_event'] == 'cat-D',
    }
    return {'label': sc['label'], 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks,
            'n_picks': len(all_picks), 'events': evs, 'refused': res['refused'], 'catalogue': cmp,
            'errors_km': {'ev-A': round(err_km(eA, tr[0]), 2) if eA else None, 'ev-B': round(err_km(eB, tr[1]), 2) if eB else None}}


def report_text(res: dict, cmp: Optional[dict] = None) -> str:
    m = res['model']
    lines = [f"association: {res['n_picks']} pick(s) at {res['frame']['n']} station(s) (aperture {res['frame']['aperture_km']} km) -> "
             f"{res['n_events']} event(s); {len(res['refused'])} refused; {res['n_unused_picks']} pick(s) unused",
             f"  model: Vp {m['vp_km_s']} km/s, depth {m['depth_km']} km (declared), RMS tolerance {m['rms_tol_s']} s, {m['min_stations']}+ stations"]
    for e in res['events']:
        r = e['region_km']
        lines.append(f"  {e['id']}: {e['origin_utc']}  {e['lat']:.4f}, {e['lon']:.4f}  {e['n_stations']} stations  RMS {e['rms_s']} s  "
                     f"misfit region {r['extent_east_km']} x {r['extent_north_km']} km" + (f"  dropped {', '.join(e['dropped'])}" if e['dropped'] else '')
                     + "  magnitude: not estimated")
    for r in res['refused'][:6]:
        lines.append(f"  refused at {r['first_pick']} ({r['station']}): {r['why']}")
    if cmp:
        for r in cmp['rows']:
            lines.append(f"  {r['event']} vs catalogue: {r['standing']}" + (f" - {r['detail']}" if r.get('detail') else ''))
        for r in cmp['missed']:
            lines.append(f"  catalogue {r['catalog_event']} (M {r['magnitude']}): {r['standing']}")
    return '\n'.join(lines)
