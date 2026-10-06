# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_track - the time dimension of the second leg: bearings over time, positions over time, a track.

The array step gives one bearing for one window. Run window after window it gives a bearing
history; two or more arrays' histories, crossed window by window, give a position history; and
a position history, with the ellipse every point earned, is a track. That is the product the
leg exists for - a well's track from its signature - and this module is where the leg's parts
become it. Nothing here adds a model: every bearing is `seismic_array.beam`, every position is
`seismic_array.intersect_backazimuths`, and every verdict is a comparison against ground truth
the user supplied.

What the tracker calls a measurement and what it does not:

- a bearing in a window is a measurement only when the beam was coherent (best bin >= 0.5 and
  at least two coherent bins); every other window is a gap, printed as one;
- a position in a window is a measurement only when two or more arrays were coherent in it,
  their bearings crossed at 15 degrees or more, and the point lies in front of every array;
- the track's heading and length are the fit through its positions, with the ellipses beside
  them; a heading from fewer than three positions is not given;
- a change point in a bearing history is a move beyond the array's own tolerance, judged against
  the running mean - the array response function's width is the floor below which no motion can
  be claimed;
- the verdict against ground truth (TRACKED, PARTIAL, NOT_TRACKED, INSUFFICIENT) compares the
  track with the lateral's own points in time: a position within its ellipse (or the truth's
  own margin) of where the truth was at that time counts; the verdict names the fraction.

The synthetic lateral scene imitates a bit advancing along a straight lateral at a constant
rate, its machinery lines arriving at two arrays as plane waves; it is an assumption for
checking the arithmetic on motion and is labelled SIMULATION_SELF_TEST wherever it appears.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .seismic import Trace, iso, parse_time
from . import seismic_array as AR
from .seismic_array import Sensor, local_xy, xy_to_latlon, bearing_deg, _angle_diff, array_geometry

LABEL = 'SIMULATION_SELF_TEST'
MIN_CROSSING_DEG = 15.0


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------
@dataclass
class TruthPoint:
    """Where the source was at a time: a surveyed point of the lateral, a permit's surface hole with a date."""
    point_id: str
    lat: float
    lon: float
    utc: float
    note: str = ''

    def to_dict(self) -> dict:
        d = asdict(self)
        d['utc'] = iso(self.utc, 0)
        return d


def load_truth_csv(path: str) -> List[TruthPoint]:
    """point_id, lat, lon, utc[, note]"""
    out = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            out.append(TruthPoint(row['point_id'].strip(), float(row['lat']), float(row['lon']), parse_time(row['utc'].strip()), (row.get('note') or '').strip()))
    out.sort(key=lambda p: p.utc)
    return out


def write_truth_csv(points: Sequence[TruthPoint], path: str) -> str:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['point_id', 'lat', 'lon', 'utc', 'note'])
        for p in points:
            w.writerow([p.point_id, f'{p.lat:.6f}', f'{p.lon:.6f}', iso(p.utc, 0), p.note])
    return path


def truth_at(points: Sequence[TruthPoint], t: float) -> Optional[Tuple[float, float]]:
    """The truth's position at time t by linear interpolation between its points; None outside them."""
    if not points:
        return None
    if t < points[0].utc or t > points[-1].utc:
        return None
    for a, b in zip(points, points[1:]):
        if a.utc <= t <= b.utc:
            f = 0.0 if b.utc == a.utc else (t - a.utc) / (b.utc - a.utc)
            return a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon)
    return points[-1].lat, points[-1].lon


# ---------------------------------------------------------------------------
# bearings over time
# ---------------------------------------------------------------------------
def bearing_history(traces: Sequence[Trace], sensors: Sequence[Sensor], band: Tuple[float, float] = (1.0, 20.0), win_s: float = 600.0,
                    step_s: Optional[float] = None, seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 41, method: str = 'bartlett',
                    min_coherence: float = 0.5, min_bins: int = 2, measure_sigma: bool = False, sigma_parts: int = 3) -> dict:
    """The beam on every window of the record: a bearing with the array's tolerance where the beam was
    coherent, a gap where it was not, and the change points - windows where the bearing moved beyond the
    tolerance against the running mean of the coherent windows before it.

    With `measure_sigma` each coherent window also gets an uncertainty measured from its own sub-windows
    rather than taken from the array's geometry, and the positions crossed from these bearings use it. The
    tolerance stays what it was - it is the test for whether the array pointed at a listed source, which is a
    question about resolution - but the ellipse is drawn from what the data is worth."""
    if len(traces) != len(sensors) or len(sensors) < 3:
        raise ValueError('a bearing history needs three or more sensors, one record each, in the same order')
    step_s = step_s or win_s
    geo = array_geometry(sensors)
    t0 = max(tr.starttime for tr in traces)
    t1 = min(tr.endtime for tr in traces)
    if t1 - t0 < win_s:
        raise ValueError(f'the common record ({t1 - t0:.0f} s) is shorter than one window ({win_s:.0f} s)')
    band = (float(band[0]), float(min(band[1], 0.4 * traces[0].sample_rate)))
    windows: List[dict] = []
    tolerance = None
    t = t0
    while t + win_s <= t1 + 1e-6:
        win = [tr.slice(t, t + win_s) for tr in traces]
        row = {'start_utc': iso(t, 0), 'end_utc': iso(t + win_s, 0), 'start': t, 'coherent': False}
        try:
            b = AR.beam(win, sensors, band, seg_s, s_max, n_grid, method)
            tol = round(float(b['resolution']['azimuth_half_width_deg']) + 1.0, 2)
            tolerance = tol if tolerance is None else tolerance
            coherent = bool(b['coherence_max_bin'] >= min_coherence and b['coherent_bins'] >= min_bins)
            row.update({'back_azimuth_deg': b['back_azimuth_deg'], 'slowness_s_km': b['slowness_s_km'], 'apparent_velocity_km_s': b['apparent_velocity_km_s'],
                        'coherence_max_bin': b['coherence_max_bin'], 'coherent_bins': b['coherent_bins'], 'coherent': coherent, 'tolerance_deg': tol,
                        'aliasing_lobes': b['resolution']['aliasing_lobes']})
            if measure_sigma and coherent:
                from . import seismic_uncertainty as UQ
                try:
                    u = UQ.bearing_sigma(win, sensors, band, seg_s, s_max, n_grid, method, parts=sigma_parts)
                    row.update({'sigma_deg': u['sigma_deg'], 'sigma_status': u['status'], 'sigma_parts': u.get('n_parts'),
                                'sigma_scatter_deg': u.get('sigma_scatter_deg'), 'sigma_floor_deg': u.get('floor_deg')})
                except ValueError:
                    pass
        except ValueError as e:
            row.update({'back_azimuth_deg': None, 'note': str(e)})
        windows.append(row)
        t += step_s
    # change points against the running mean of the coherent bearings so far
    change_points = []
    run: List[float] = []
    for w in windows:
        if not w['coherent']:
            continue
        if len(run) >= 2:
            mean = _circular_mean(run)
            dev = abs(_angle_diff(w['back_azimuth_deg'], mean))
            if dev > w['tolerance_deg']:
                change_points.append({'start_utc': w['start_utc'], 'from_deg': round(mean, 1), 'to_deg': w['back_azimuth_deg'], 'moved_deg': round(dev, 1)})
                run = []
        run.append(w['back_azimuth_deg'])
    coh = [w for w in windows if w['coherent']]
    out = {'protocol': 'seismic_track.bearing_history', 'array': {'lat': geo['lat0'], 'lon': geo['lon0'], 'n_sensors': len(sensors), 'aperture_km': round(geo['aperture_km'], 4),
                                                                   'sensors': [s.sensor_id for s in sensors]},
           'band_hz': list(band), 'win_s': win_s, 'step_s': step_s, 'method': method, 'tolerance_deg': tolerance, 'plane_wave_limit_km': round(5.0 * geo['aperture_km'], 2),
           'windows': windows, 'n_windows': len(windows), 'n_coherent': len(coh), 'change_points': change_points,
           'span_deg': (round(float(max(abs(_angle_diff(a['back_azimuth_deg'], b['back_azimuth_deg'])) for a in coh for b in coh)), 1) if len(coh) >= 2 else None),
           'basis': 'seismic_array.beam on every window; a window is a bearing only when the beam was coherent (best bin >= 0.5, two or more coherent bins); '
                    'a change point is a coherent bearing more than the tolerance from the running mean of the coherent bearings before it',
           'not_a_measurement': ['a bearing in a window that was not coherent (printed as a gap)',
                                 'motion smaller than the tolerance - the array response function\'s width is the floor',
                                 'a distance: a bearing history is directions over time, nothing more']}
    return out


def _circular_mean(deg: Sequence[float]) -> float:
    a = np.radians(deg)
    return (math.degrees(math.atan2(float(np.sin(a).mean()), float(np.cos(a).mean()))) + 360.0) % 360.0


# ---------------------------------------------------------------------------
# positions over time
# ---------------------------------------------------------------------------
def position_history(histories: Sequence[dict], min_crossing_deg: float = MIN_CROSSING_DEG) -> dict:
    """Cross two or more arrays' bearing histories window by window (matched by start time). A window gives a
    position when two or more arrays were coherent in it, the bearings crossed at min_crossing_deg or more and the
    point lies in front of every array that contributed; every other window is a gap with its reason."""
    if len(histories) < 2:
        raise ValueError('a position history needs two or more arrays')
    by_start: Dict[str, List[Tuple[int, dict]]] = {}
    for i, h in enumerate(histories):
        for w in h['windows']:
            by_start.setdefault(w['start_utc'], []).append((i, w))
    rows = []
    for start in sorted(by_start):
        entries = by_start[start]
        coh = [(i, w) for i, w in entries if w.get('coherent')]
        row = {'start_utc': start, 'start': entries[0][1]['start'], 'arrays_coherent': [histories[i]['array'].get('name', str(i)) for i, _ in coh], 'position': None}
        if len(coh) < 2:
            row['gap'] = 'fewer than two arrays coherent'
            rows.append(row); continue
        # the ellipse is drawn from what each bearing is worth: the measured sigma where there is one, and
        # the array's own resolution where there is not - which is a floor, not an error bar, and is marked
        arrays = [{'lat': histories[i]['array']['lat'], 'lon': histories[i]['array']['lon'], 'back_azimuth_deg': w['back_azimuth_deg'],
                   'sigma_deg': (w.get('sigma_deg') or w['tolerance_deg'])}
                  for i, w in coh]
        row['sigma_deg'] = [round(float(w.get('sigma_deg') or w['tolerance_deg']), 3) for _, w in coh]
        row['sigma_measured'] = [bool(w.get('sigma_deg')) for _, w in coh]
        x = AR.intersect_backazimuths(arrays)
        if x['crossing_angle_deg'] < min_crossing_deg:
            row['gap'] = f"bearings cross at {x['crossing_angle_deg']} deg (under {min_crossing_deg:g})"
        elif not x['in_front_of_every_array']:
            row['gap'] = 'the crossing lies behind an array'
        elif x['ellipse_1sigma']['major_km'] is None or not math.isfinite(x['ellipse_1sigma']['major_km']):
            row['gap'] = 'the crossing has no finite ellipse'
        else:
            row['position'] = {'lat': round(x['lat'], 6), 'lon': round(x['lon'], 6), 'ellipse_1sigma': x['ellipse_1sigma'], 'crossing_angle_deg': x['crossing_angle_deg'],
                               'ranges_km': x['ranges_km'], 'residual_deg': x['residual_deg']}
        rows.append(row)
    pts = [r for r in rows if r['position']]
    out = {'protocol': 'seismic_track.position_history', 'arrays': [h['array'] for h in histories], 'n_windows': len(rows), 'n_positions': len(pts),
           'windows': rows, 'min_crossing_deg': min_crossing_deg,
           'basis': 'seismic_array.intersect_backazimuths on each window where two or more arrays were coherent; the ellipse is the 1-sigma covariance from the arrays\' tolerances',
           'not_a_measurement': ['a position in any window listed as a gap', 'a position finer than its ellipse', 'anything at all when fewer than two arrays exist',
                                 'motion smaller than the ellipses the positions carry: a line fitted through a scatter always has a heading, and it means nothing until the extent beats the ellipse']}
    out['track'] = track_summary(pts)
    return out


def split_segments(points: Sequence[dict], jump_factor: float = 2.0) -> List[List[dict]]:
    """Consecutive positions farther apart than jump_factor x (the two ellipses' major axes) are different
    things - a coherent background crossing in a quiet hour is not the same source as the rig an hour later.
    A track is continuous; the positions are cut into segments at every such jump."""
    segs: List[List[dict]] = []
    for w in points:
        if segs:
            prev = segs[-1][-1]['position']; cur = w['position']
            jump = math.hypot(*local_xy(cur['lat'], cur['lon'], prev['lat'], prev['lon']))
            if jump > jump_factor * (prev['ellipse_1sigma']['major_km'] + cur['ellipse_1sigma']['major_km']):
                segs.append([w]); continue
            segs[-1].append(w)
        else:
            segs.append([w])
    return segs


def track_summary(points: Sequence[dict]) -> dict:
    """The fit through the positions of the longest continuous segment: heading and length with the ellipses
    beside them. Fewer than three positions give no heading; the endpoints are the segment's first and last."""
    all_n = len(points)
    if all_n == 0:
        return {'n_positions': 0, 'n_positions_all': 0, 'n_segments': 0, 'segments': [], 'heading_deg': None, 'length_km': None, 'note': 'no positions'}
    segs = split_segments(points)
    seg_rows = [{'n': len(sg), 'start_utc': sg[0]['start_utc'], 'end_utc': sg[-1]['start_utc']} for sg in segs]
    points = max(segs, key=len)
    n = len(points)
    lat0 = float(np.mean([p['position']['lat'] for p in points])); lon0 = float(np.mean([p['position']['lon'] for p in points]))
    xy = np.array([local_xy(p['position']['lat'], p['position']['lon'], lat0, lon0) for p in points])
    maj = [p['position']['ellipse_1sigma']['major_km'] for p in points]
    out = {'n_positions': n, 'n_positions_all': all_n, 'n_segments': len(segs), 'segments': seg_rows,
           'segment_note': (None if len(segs) == 1 else f'{len(segs)} segments: the positions jump farther than their ellipses allow between them; '
                            'the heading and length given are the longest segment\'s, the others are listed, not joined'),
           'first': {'start_utc': points[0]['start_utc'], **{k: points[0]['position'][k] for k in ('lat', 'lon')}},
           'last': {'start_utc': points[-1]['start_utc'], **{k: points[-1]['position'][k] for k in ('lat', 'lon')}},
           'ellipse_major_km': {'median': round(float(np.median(maj)), 3), 'max': round(float(max(maj)), 3)},
           'heading_deg': None, 'length_km': None, 'rms_off_line_km': None}
    if n >= 3:
        c = xy - xy.mean(axis=0)
        u, s, vt = np.linalg.svd(c, full_matrices=False)
        d = vt[0]                                                       # the line's direction (east, north)
        proj = c @ d
        if proj[-1] < proj[0]:
            d = -d; proj = -proj                                        # heading in the direction of time
        off = c - np.outer(proj, d)
        out.update({'heading_deg': round((math.degrees(math.atan2(d[0], d[1])) + 360.0) % 360.0, 1),
                    'length_km': round(float(proj[-1] - proj[0]), 3), 'rms_off_line_km': round(float(np.sqrt((off ** 2).sum(axis=1).mean())), 3),
                    'rate_km_per_h': round(float((proj[-1] - proj[0]) / max((points[-1]['start'] - points[0]['start']) / 3600.0, 1e-9)), 3)})
    else:
        out['note'] = 'fewer than three positions: no heading or length'
    # did it move at all? A line fitted through a scatter always has a heading and a length; they mean
    # something only when the extent is larger than the ellipses the positions carry.
    med = out['ellipse_major_km']['median']
    if out.get('length_km') is None:
        out['motion'] = 'NOT_JUDGED'
        out['motion_note'] = 'fewer than three positions: whether the source moved is not judged'
    elif out['length_km'] <= 2.0 * med:
        out['motion'] = 'NOT_RESOLVED'
        out['motion_note'] = (f"the positions span {out['length_km']:.2f} km, within twice the median 1-sigma ellipse ({med:.2f} km): they are consistent "
                              'with a source that did not move, and the heading below is the scatter\'s, not a direction of travel')
    else:
        out['motion'] = 'RESOLVED'
        out['motion_note'] = (f"the positions span {out['length_km']:.2f} km against a median 1-sigma ellipse of {med:.2f} km: the source moved, and the "
                              'heading is the direction it moved in')
    return out


# ---------------------------------------------------------------------------
# the verdict against ground truth
# ---------------------------------------------------------------------------
def track_verdict(positions: dict, truth: Sequence[TruthPoint], margin_km: float = 0.0) -> dict:
    """Each position against where the truth was at that time: a hit when the miss is within the position's
    1-sigma major axis plus margin_km. TRACKED when at least 80 % of the positions inside the truth's time span
    hit and there are three or more; PARTIAL when half or more; NOT_TRACKED below that; INSUFFICIENT when fewer
    than three positions fall inside the truth's span. The heading is compared too when both exist."""
    rows = []
    outside = 0
    for w in positions['windows']:
        if not w['position']:
            continue
        tt = truth_at(truth, w['start'])
        if tt is None:
            outside += 1
            continue
        p = w['position']
        dx, dy = local_xy(p['lat'], p['lon'], tt[0], tt[1])
        miss = math.hypot(dx, dy)
        allow = p['ellipse_1sigma']['major_km'] + margin_km
        rows.append({'start_utc': w['start_utc'], 'miss_km': round(miss, 3), 'allowed_km': round(allow, 3), 'hit': bool(miss <= allow)})
    n = len(rows)
    hits = sum(1 for r in rows if r['hit'])
    frac = hits / n if n else 0.0
    if n < 3:
        verdict = 'INSUFFICIENT'
    elif frac >= 0.8:
        verdict = 'TRACKED'
    elif frac >= 0.5:
        verdict = 'PARTIAL'
    else:
        verdict = 'NOT_TRACKED'
    out = {'protocol': 'seismic_track.track_verdict', 'verdict': verdict, 'n_compared': n, 'n_hit': hits, 'hit_fraction': round(frac, 3), 'margin_km': margin_km,
           'n_outside_truth': outside, 'rows': rows, 'truth_points': [p.to_dict() for p in truth],
           'outside_note': (None if not outside else f'{outside} position(s) fall outside the truth\'s time span: something coherent crossed the arrays when the '
                            'truth says the source was not working - they are not judged here and not joined to the track')}
    tr = positions.get('track') or {}
    if tr.get('heading_deg') is not None and len(truth) >= 2:
        th = bearing_deg(truth[0].lat, truth[0].lon, truth[-1].lat, truth[-1].lon)
        d = local_xy(truth[-1].lat, truth[-1].lon, truth[0].lat, truth[0].lon)
        out['heading'] = {'track_deg': tr['heading_deg'], 'truth_deg': round(th, 1), 'difference_deg': round(float(abs(_angle_diff(tr['heading_deg'], th))), 1),
                          'track_length_km': tr['length_km'], 'truth_length_km': round(math.hypot(*d), 3)}
    out['basis'] = 'each position against the truth interpolated to its time; a hit is a miss within the position\'s own 1-sigma major axis plus the margin'
    out['not_a_measurement'] = ['a verdict without ground truth', 'a hit finer than the ellipse that allowed it', 'the truth itself: it is the user\'s file, taken as given']
    return out


# ---------------------------------------------------------------------------
# the synthetic lateral scene
# ---------------------------------------------------------------------------
def synthetic_lateral_scene(seed: int = 9, fs: float = 50.0, hours: float = 8.0, centre=(31.0, -102.0), arrays_km: Sequence[Tuple[float, float]] = ((-4.0, -3.0), (4.5, -2.5)),
                            n_sensors: int = 9, aperture_km: float = 1.2, lateral_start_km: Tuple[float, float] = (-1.5, 3.0), heading_deg: float = 80.0,
                            length_km: float = 3.0, quiet_hours: float = 1.0, v_km_s: float = 2.5, q_factor: float = 100.0, noise: float = 30.0,
                            sensor_noise: float = 1.0, chunk_s: float = 60.0) -> Tuple[List[List[Trace]], List[List[Sensor]], List[TruthPoint], dict]:
    """Two (or more) arrays at `arrays_km` from the scene centre; one source that is quiet for `quiet_hours`, then
    advances along a straight lateral from `lateral_start_km` on `heading_deg` for `length_km` over the remaining
    hours at a constant rate, its machinery lines arriving at each array as a plane wave from the source's bearing
    at that moment (recomputed every chunk_s), attenuated by 1/r and exp(-pi f r/(Q v)). Returns one trace list
    and one sensor list per array, the truth points (the lateral sampled every chunk) and the scene's metadata."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = centre
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    t0 = parse_time('2025-06-01T00:00:00Z')
    F = np.fft.rfftfreq(n, 1 / fs)
    comps = [(1.8, 1.0), (3.6, 0.6), (5.4, 0.4), (7.2, 0.25), (10.8, 0.15), (17.5, 0.5)]
    comps = [c for c in comps if c[0] < 0.8 * fs / 2]
    hd = math.radians(heading_deg)
    move_s = max((hours - quiet_hours) * 3600.0, 1.0)
    rate = length_km / move_s                                                # km per second along the lateral
    def source_xy(ts: float) -> Tuple[float, float]:
        s = min(max(ts - quiet_hours * 3600.0, 0.0), move_s) * rate
        return lateral_start_km[0] + s * math.sin(hd), lateral_start_km[1] + s * math.cos(hd)
    phases = [rng.uniform(0, 2 * np.pi) for _ in comps]
    mod_phase = rng.uniform(0, 2 * np.pi)
    all_traces, all_sensors = [], []
    nchunk = int(round(chunk_s * fs))
    for ai, (ax, ay) in enumerate(arrays_km):
        alat, alon = xy_to_latlon(ax, ay, lat0, lon0)
        sensors: List[Sensor] = [Sensor(f'A{ai + 1}S00', alat, alon)]
        for k in range(n_sensors - 1):
            ang = 2 * math.pi * k / (n_sensors - 1)
            r = aperture_km / 2.0 * (1.0 if k % 2 == 0 else 0.55)
            lat, lon = xy_to_latlon(ax + r * math.sin(ang), ay + r * math.cos(ang), lat0, lon0)
            sensors.append(Sensor(f'A{ai + 1}S{k + 1:02d}', lat, lon))
        geo = array_geometry(sensors)
        xy = geo['xy_km']
        data = np.zeros((len(sensors), n))
        # the common background: a weak coloured-noise plane wave from a random bearing at 0.4 km/s, plus sensor noise
        bg_baz = float(rng.uniform(0, 360))
        bg = np.cumsum(rng.normal(0, 0.02, n)); bg -= np.linspace(bg[0], bg[-1], n); bg = noise * bg / (bg.std() + 1e-12)
        BG = np.fft.rfft(bg)
        u_bg = np.array([math.sin(math.radians(bg_baz)), math.cos(math.radians(bg_baz))])
        for k in range(len(sensors)):
            tau = -(xy[k] @ u_bg) / 0.4
            data[k] += np.fft.irfft(BG * np.exp(-2j * np.pi * F * tau), n) + sensor_noise * rng.normal(0, 1, n)
        # the moving source, chunk by chunk: a plane wave from its bearing at the chunk's middle, at its distance
        for c0 in range(int(quiet_hours * 3600 * fs), n, nchunk):
            c1 = min(c0 + nchunk, n)
            tm = 0.5 * (t[c0] + t[c1 - 1])
            sx, sy = source_xy(tm)
            dx, dy = sx - ax, sy - ay
            dist = max(math.hypot(dx, dy), 0.1)
            baz = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
            u = np.array([math.sin(math.radians(baz)), math.cos(math.radians(baz))])
            tt = t[c0:c1]
            env = 1.0 + 0.3 * np.sin(2 * np.pi * 0.05 * tt + mod_phase)
            for j in range(len(sensors)):
                tau = -(xy[j] @ u) / v_km_s
                sig = np.zeros(c1 - c0)
                for (f_hz, rel), ph in zip(comps, phases):
                    amp = 6000.0 * rel / dist * math.exp(-math.pi * f_hz * dist / (q_factor * v_km_s))
                    sig += amp * np.sin(2 * np.pi * f_hz * (tt - tau) + ph)
                data[j, c0:c1] += sig * env
        traces = [Trace('XX', s.sensor_id, '', 'HHZ', t0, fs, np.round(data[k]).astype(np.int32), source='synthetic_lateral_scene', encoding='SYNTHETIC')
                  for k, s in enumerate(sensors)]
        all_traces.append(traces); all_sensors.append(sensors)
    # the truth: the lateral sampled every chunk from the moment the source starts
    truth: List[TruthPoint] = []
    ts = quiet_hours * 3600.0
    k = 0
    while ts <= hours * 3600.0 + 1e-6:
        sx, sy = source_xy(ts)
        lat, lon = xy_to_latlon(sx, sy, lat0, lon0)
        truth.append(TruthPoint(f'L{k:03d}', lat, lon, t0 + ts, 'synthetic lateral'))
        ts += chunk_s * 10; k += 1
    ex, ey = source_xy(hours * 3600.0)
    meta = {'status': LABEL, 'v_km_s': v_km_s, 'q_factor': q_factor, 'spreading': '1/r', 'arrays_km': [list(a) for a in arrays_km], 'centre': {'lat': lat0, 'lon': lon0},
            'lateral': {'start_km': list(lateral_start_km), 'end_km': [round(ex, 3), round(ey, 3)], 'heading_deg': heading_deg, 'length_km': length_km,
                        'quiet_hours': quiet_hours, 'rate_km_per_h': round(rate * 3600.0, 4)},
            'lines_hz': [c[0] for c in comps], 'aperture_km': aperture_km,
            'note': 'the scene is an assumption for checking the arithmetic on motion - a bit advancing along a lateral at a constant rate; it says nothing about any ground'}
    return all_traces, all_sensors, truth, meta


def selftest(seed: int = 9, hours: float = 3.0, win_s: float = 600.0) -> dict:
    """The tracker on the labelled lateral scene: two arrays' bearing histories, the position history, the verdict."""
    traces, sensors, truth, meta = synthetic_lateral_scene(seed=seed, hours=hours)
    hists = []
    for i, (trs, sens) in enumerate(zip(traces, sensors)):
        h = bearing_history(trs, sens, (1.0, 20.0), win_s)
        h['array']['name'] = f'array-{i + 1}'
        hists.append(h)
    pos = position_history(hists)
    ver = track_verdict(pos, truth)
    ok = (ver['verdict'] == 'TRACKED' and ver.get('heading', {}).get('difference_deg', 999) <= 10.0
          and all(h['n_coherent'] >= 0.8 * (h['n_windows'] - 1) for h in hists))
    return {'status': LABEL, 'ok': bool(ok), 'scene': meta, 'bearing_histories': hists, 'positions': pos, 'verdict': ver}


def report_text(pos: dict, ver: Optional[dict] = None) -> str:
    tr = pos.get('track') or {}
    lines = ['SEISMIC TRACK', '=' * 60,
             f"arrays: {len(pos['arrays'])}; windows: {pos['n_windows']}; positions: {pos['n_positions']} (gaps: {pos['n_windows'] - pos['n_positions']})"]
    if tr.get('motion_note'):
        lines.append('motion: ' + tr['motion_note'])
    if tr.get('heading_deg') is not None:
        lines.append(f"track: heading {tr['heading_deg']} deg, length {tr['length_km']} km over {tr['n_positions']} positions, {tr['rate_km_per_h']} km/h; "
                     f"rms off the line {tr['rms_off_line_km']} km; ellipse major median {tr['ellipse_major_km']['median']} km (max {tr['ellipse_major_km']['max']})")
    else:
        lines.append(f"track: {tr.get('note', 'none')}")
    if tr.get('segment_note'):
        lines.append('  ' + tr['segment_note'])
    if ver:
        lines.append(f"verdict against ground truth: {ver['verdict']} ({ver['n_hit']} of {ver['n_compared']} positions within their ellipse)")
        if ver.get('outside_note'):
            lines.append('  ' + ver['outside_note'])
        if ver.get('heading'):
            h = ver['heading']
            lines.append(f"  heading {h['track_deg']} deg vs truth {h['truth_deg']} deg (difference {h['difference_deg']}); length {h['track_length_km']} vs {h['truth_length_km']} km")
    lines.append('not a measurement: ' + '; '.join(pos['not_a_measurement']))
    return '\n'.join(lines)
