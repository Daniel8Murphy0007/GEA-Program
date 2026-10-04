# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_film - the SAR simulator's film: the labelled synthetic array scene played forward in time.

The Seismic page's SAR panel has a screen. What it plays is this film: the second leg's own
arithmetic applied window by window to the synthetic array scene, so a reader can watch what the
program does with a record - the trace scrolling, the spectrogram column arriving, the beam power
map swinging onto the working rig, the bearing against the rig's true bearing with the array's own
tolerance beside it - and, at the end, the array detectability test's verdict on the whole scene,
the same function `gea seismic --action array-detect` runs on a real record.

Every frame carries the label SIMULATION_SELF_TEST. The scene is an assumption for showing the
arithmetic (plane waves at one velocity, 1/r spreading, one Q, machinery lines at chosen
frequencies); nothing in the film is a measurement of any ground, and the film says so in its own
metadata, on every frame, and in the last frame's closing line. A real record never goes through
this module: the real leg runs through `Workspace.refresh_seismic`, and when it has earned a
bearing the Seismic page shows that, not a film.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic as S
from . import seismic_array as AR
from .seismic import Trace, iso

LABEL = 'SIMULATION_SELF_TEST'

FILM_PROTOCOL = 'seismic_film.sar_film/1'


def _decimate_minmax(x: np.ndarray, n_out: int) -> List[List[int]]:
    """Min/max per bin: the trace's envelope for a screen, never a resample that invents a value."""
    if len(x) == 0:
        return []
    n_out = max(1, min(n_out, len(x)))
    edges = np.linspace(0, len(x), n_out + 1).astype(int)
    out = []
    for i in range(n_out):
        seg = x[edges[i]:max(edges[i + 1], edges[i] + 1)]
        out.append([int(seg.min()), int(seg.max())])
    return out


def sar_film(seed: int = 5, hours: float = 8.0, step_s: float = 600.0, band: Tuple[float, float] = (1.0, 20.0), method: str = 'bartlett',
             n_sensors: int = 9, aperture_km: float = 1.2, distances_km: Sequence[float] = (6.0, 12.0, 25.0, 45.0), fs: float = 50.0,
             n_grid: int = 31, s_max: float = 3.0, seg_s: float = 10.0, n_trace_points: int = 240, n_spec_bins: int = 64) -> dict:
    """The film. Returns a dict (JSON-ready) with `meta` (the scene, the label, the tolerance), `frames`
    (one per window) and `final` (the array detectability verdict on the whole scene)."""
    if hours <= 0 or step_s <= 0:
        raise ValueError('hours and step_s must be positive')
    if method not in ('bartlett', 'capon'):
        raise ValueError("method is 'bartlett' or 'capon'")
    # the scene: rigs at the listed distances, each working alone for an hour, at hour 1, 3, 5, ... - so
    # the film length decides how many of them appear
    traces, sensors, sources, scene = AR.synthetic_array_scene(seed=seed, fs=fs, hours=hours, n_sensors=n_sensors, aperture_km=aperture_km,
                                                               distances_km=distances_km)
    band = (float(band[0]), float(min(band[1], 0.4 * fs)))
    geo = AR.array_geometry(sensors)
    xy = geo['xy_km']
    lat0, lon0 = geo['lat0'], geo['lon0']
    t_start, t_end = traces[0].starttime, traces[0].starttime + traces[0].duration_s
    # the array's own resolution at this band: from one beam on the first window (the ARF does not depend on the data)
    first = [tr.slice(t_start, min(t_end, t_start + step_s)) for tr in traces]
    b0 = AR.beam(first, sensors, band, seg_s, s_max, n_grid, method)
    tolerance_deg = round(float(b0['resolution']['azimuth_half_width_deg']) + 1.0, 2)
    sx, sy, _ = AR.slowness_grid(s_max, n_grid)
    rigs = []
    for s in sources:
        d = AR.local_xy(s.lat, s.lon, lat0, lon0)
        rigs.append({'source_id': s.source_id, 'lat': s.lat, 'lon': s.lon, 'x_km': round(d[0], 3), 'y_km': round(d[1], 3),
                     'distance_km': round(math.hypot(*d), 2), 'true_bearing_deg': scene['true_bearings_deg'][s.source_id],
                     'start_utc': iso(s.start, 0), 'end_utc': iso(s.end, 0), 'lines_hz': scene['lines_hz'][s.source_id]})
    frames: List[dict] = []
    tally: Dict[str, dict] = {r['source_id']: {'windows_working': 0, 'windows_pointed': 0, 'windows_coherent': 0} for r in rigs}
    t = t_start
    k = 0
    while t + step_s <= t_end + 1e-6:
        win = [tr.slice(t, t + step_s) for tr in traces]
        # the trace (centre sensor) and its spectrum column
        x0 = win[0].data.astype(float)
        f, p = S.welch_psd(x0, fs, nperseg=min(len(x0), int(60 * fs)))
        m = (f >= band[0]) & (f <= band[1])
        fb, pb = f[m], 10.0 * np.log10(np.maximum(p[m], 1e-30))
        if len(fb) > n_spec_bins:
            edges = np.linspace(0, len(fb), n_spec_bins + 1).astype(int)
            col = [round(float(pb[edges[i]:max(edges[i + 1], edges[i] + 1)].max()), 1) for i in range(n_spec_bins)]
            fcol = [round(float(fb[edges[i]:max(edges[i + 1], edges[i] + 1)].mean()), 2) for i in range(n_spec_bins)]
        else:
            col, fcol = [round(float(v), 1) for v in pb], [round(float(v), 2) for v in fb]
        # the beam on this window
        try:
            csm = AR.cross_spectral_matrix(win, band, seg_s)
            bp = AR.beam_power(csm, xy, s_max, n_grid, method)
            P = bp['power']
            P = (P / max(float(P.max()), 1e-30))
            pointed = bool(bp['coherence_max_bin'] >= 0.5 and bp['coherent_bins'] >= 2)
            beam = {'back_azimuth_deg': round(float(bp['back_azimuth_deg']), 1), 'slowness_s_km': round(float(bp['slowness_s_km']), 3),
                    'apparent_velocity_km_s': round(float(bp['apparent_velocity_km_s']), 2), 'coherence_max_bin': round(float(bp['coherence_max_bin']), 3),
                    'coherent_bins': int(bp['coherent_bins']), 'coherent': pointed,
                    'power': [[round(float(v), 3) for v in row] for row in P]}
        except ValueError as e:
            beam = {'back_azimuth_deg': None, 'slowness_s_km': None, 'apparent_velocity_km_s': None, 'coherence_max_bin': 0.0, 'coherent_bins': 0,
                    'coherent': False, 'power': [], 'note': str(e)}
            pointed = False
        # the rigs in this window: which one works, and whether the beam points at it within the tolerance
        rig_states = []
        working = None
        for r, s in zip(rigs, sources):
            is_working = bool(s.start < t + step_s and s.end > t)
            st = {'source_id': r['source_id'], 'working': is_working}
            if is_working:
                working = r['source_id']
                tally[r['source_id']]['windows_working'] += 1
                if beam['back_azimuth_deg'] is not None and pointed:
                    err = AR._angle_diff(beam['back_azimuth_deg'], r['true_bearing_deg'])
                    st['bearing_error_deg'] = round(float(err), 1)
                    st['within_tolerance'] = bool(abs(err) <= tolerance_deg)
                    tally[r['source_id']]['windows_coherent'] += 1
                    if st['within_tolerance']:
                        tally[r['source_id']]['windows_pointed'] += 1
            rig_states.append(st)
        frames.append({'index': k, 'start_utc': iso(t, 0), 'end_utc': iso(t + step_s, 0), 'hour': round((t - t_start) / 3600.0, 3), 'label': LABEL,
                       'trace': _decimate_minmax(win[0].data, n_trace_points), 'spectrum_db': col, 'spectrum_hz': fcol, 'beam': beam,
                       'working': working, 'rigs': rig_states,
                       'tally': {sid: dict(v) for sid, v in tally.items()}})
        t += step_s
        k += 1
    # the closing: the array detectability test on the whole scene - the real function, the real verdicts
    final = AR.array_detectability(traces, sensors, sources, band, min(step_s, 600.0))
    final_public = {kk: v for kk, v in final.items() if not kk.startswith('_')}
    return {'protocol': FILM_PROTOCOL, 'label': LABEL, 'status': LABEL,
            'meta': {'scene': {kk: v for kk, v in scene.items() if kk != 'lines_hz'}, 'seed': seed, 'hours': hours, 'step_s': step_s, 'band_hz': list(band),
                     'method': method, 'fs_hz': fs, 'n_sensors': len(sensors), 'aperture_km': round(geo['aperture_km'], 4),
                     'sensors': [{'sensor_id': s.sensor_id, 'x_km': round(float(xy[i][0]), 4), 'y_km': round(float(xy[i][1]), 4)} for i, s in enumerate(sensors)],
                     'array_centre': {'lat': lat0, 'lon': lon0}, 'rigs': rigs, 'tolerance_deg': tolerance_deg,
                     'plane_wave_limit_km': round(5.0 * geo['aperture_km'], 2), 'slowness_grid': {'n': n_grid, 's_max_s_km': s_max},
                     'resolution': b0['resolution'], 'frames': len(frames), 'trace_unit': 'counts (synthetic)',
                     'not_a_measurement': ['everything on this screen: the scene is synthetic and labelled ' + LABEL,
                                           'a position - one array gives a direction, never a distance; the rigs are drawn where the scene put them',
                                           'a bearing finer than the tolerance (the array response function\'s half-width plus one degree)',
                                           'any quantity in physical units: the synthetic trace is in counts with no instrument behind it']},
            'frames': frames,
            'final': final_public,
            'closing': f'{LABEL}: the scene is an assumption for showing the arithmetic. On a real record the same functions run through '
                       'the station\'s refresh, and the Seismic page shows what the record earned - a bearing, a crossing, or nothing.'}


def write_film(film: dict, path: str) -> str:
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(film, fh, separators=(',', ':'), default=str)
    return path


def film_summary(film: dict) -> dict:
    """The few numbers the page lists beside the screen."""
    fin = film.get('final') or {}
    verdicts = {r['source_id']: r['verdict'] for r in fin.get('sources', [])} if fin else {}
    return {'label': film.get('label'), 'frames': len(film.get('frames', [])), 'hours': film['meta']['hours'], 'step_s': film['meta']['step_s'],
            'band_hz': film['meta']['band_hz'], 'method': film['meta']['method'], 'n_sensors': film['meta']['n_sensors'],
            'aperture_km': film['meta']['aperture_km'], 'tolerance_deg': film['meta']['tolerance_deg'], 'rigs': len(film['meta']['rigs']),
            'verdicts': verdicts, 'pointed': sum(1 for v in verdicts.values() if v == 'POINTED'), 'final_status': fin.get('status')}


def report_text(film: dict) -> str:
    s = film_summary(film)
    lines = [f"SAR FILM - {s['label']}", '=' * 60,
             f"scene: {s['rigs']} rigs, {s['n_sensors']} sensors, aperture {s['aperture_km']} km; {s['hours']} h in {s['frames']} windows of {s['step_s']:g} s",
             f"band {s['band_hz'][0]:g}-{s['band_hz'][1]:g} Hz, {s['method']}; tolerance {s['tolerance_deg']} deg (the array's own half-width + 1)",
             'closing verdicts (array detectability test on the whole scene):']
    for sid, v in s['verdicts'].items():
        lines.append(f'  {sid}: {v}')
    lines.append(film['closing'])
    return '\n'.join(lines)
