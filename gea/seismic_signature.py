# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_signature - many rigs at once: each source's own lines, and a bearing from only those lines.

Everything before this module works on one source at a time. The beam gives the direction the band's
energy crossed the array from; when two rigs are working, that is one direction for two machines and
the crossing of it means nothing. The leg's whole purpose - a field of wells, each mapped from its own
signature - needs the step this module makes: learn what frequencies belong to each rig while it works
alone, then, in the windows where several work together, beam each rig's own frequency bins and get a
bearing per rig in the same window.

How a signature is learned: in a source's exclusive windows (it working, nothing else on the list
working), the bins that stand above their local floor there and do not in the quiet windows. That is
the same rule the detectability test prints as a source's lines; here it is kept as the source's
fingerprint.

How a bearing is taken from it: the cross-spectral matrix is sliced to that source's bins and beamed.
A machinery line arriving as one plane wave is coherent in its own bin even when the band as a whole
is noise, which is why this works at all.

What this module will not call a measurement:

- a bearing for a source with no signature (it never worked alone, so nothing is known to be its);
- a bearing from bins another source also claims - two rigs whose lines fall within the spectral
  resolution of each other are not separable by this method, and both are marked NOT_SEPARABLE rather
  than given a direction each;
- a bearing from fewer than `min_bins` of a source's own bins, or from bins that were not coherent;
- a signature as proof of a machine: it is a set of frequencies consistent with the source's exclusive
  windows, and a second rig with the same pump at the same rate would wear the same one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .seismic import Trace, iso
from . import seismic as S
from . import seismic_array as AR
from .seismic_array import Sensor, array_geometry
from .seismic_detect import Source, _lines_in_windows, _peaks, haversine_km

MIN_BINS = 2
DEFAULT_TOL_HZ = 0.25


@dataclass
class Signature:
    """The frequencies that belong to one source, learned from the windows it worked alone."""
    source_id: str
    freqs_hz: List[float] = field(default_factory=list)
    excess_db: List[float] = field(default_factory=list)
    exclusive_windows: int = 0
    status: str = 'OK'                      # OK | NO_EXCLUSIVE_WINDOWS | NO_LINES
    detail: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


def learn_signatures(tr: Trace, sources: Sequence[Source], band: Tuple[float, float] = (1.0, 20.0), win_s: float = 600.0,
                     snr_db: float = 6.0, min_windows: int = 3, max_lines: int = 20) -> dict:
    """Each source's own lines, from its exclusive windows against the quiet ones. A source that never
    worked alone gets no signature and says so; nothing is guessed for it."""
    bp = S.band_power(tr, band, win_s)
    spec, times = bp['spec'], bp['times']
    nwin = len(times)
    # several rows may carry the same source_id: one machine with more than one working window (it worked,
    # stopped, worked again). They are one source, and its active windows are the union of its rows'.
    ids: List[str] = []
    for s in sources:
        if s.source_id not in ids:
            ids.append(s.source_id)
    active = np.zeros((len(ids), nwin), dtype=bool)
    for s in sources:
        active[ids.index(s.source_id)] |= (times >= s.start) & (times <= s.end)
    n_active = active.sum(axis=0)
    quiet = np.where(n_active == 0)[0]
    f = spec['freqs']
    fb = f[(f >= band[0]) & (f <= band[1])]
    quiet_lines, _ = _lines_in_windows(spec, quiet, band, snr_db) if len(quiet) else (np.zeros(len(fb)), np.zeros(len(fb)))
    sigs: List[Signature] = []
    for i, sid in enumerate(ids):
        excl = np.where(active[i] & (n_active == 1))[0]
        sig = Signature(source_id=sid, exclusive_windows=int(len(excl)))
        if len(excl) < min_windows:
            sig.status = 'NO_EXCLUSIVE_WINDOWS'
            sig.detail = (f'{len(excl)} window(s) with this source working alone (floor {min_windows}): nothing in the record is known to be '
                          'its, so it has no signature and no bearing of its own')
            sigs.append(sig)
            continue
        frac, excess = _lines_in_windows(spec, excl, band, snr_db)
        own = np.where((frac >= 0.5) & (quiet_lines < 0.2))[0]
        peaks = _peaks(own, excess, fb)[:max_lines]
        if not peaks:
            sig.status = 'NO_LINES'
            sig.detail = 'no bin stands above its floor in this source\'s exclusive windows and not in the quiet ones'
        else:
            sig.freqs_hz = [round(float(x), 3) for x in peaks]
            sig.excess_db = [round(float(excess[int(np.argmin(np.abs(fb - x)))]), 2) for x in peaks]
            sig.detail = f'{len(peaks)} line(s) from {len(excl)} exclusive window(s)'
        sigs.append(sig)
    return {'protocol': 'seismic_signature.learn_signatures', 'band_hz': list(band), 'win_s': win_s, 'snr_db': snr_db,
            'windows': int(nwin), 'quiet_windows': int(len(quiet)), 'signatures': [s.to_dict() for s in sigs], 'source_ids': ids,
            'window_starts_utc': [iso(float(x) - 0.5 * win_s, 0) for x in times], 'window_centres': [float(x) for x in times],
            'sources_working': [int(v) for v in n_active],
            'together_windows': int((n_active >= 2).sum()),
            'n_with_signature': sum(1 for s in sigs if s.status == 'OK'),
            'basis': "a source's lines are the bins above their local floor in its exclusive windows and not in the quiet ones - the same rule the "
                     'detectability test prints',
            'not_a_measurement': ['a signature for a source that never worked alone',
                                  'a signature as proof of a machine: another rig with the same pump at the same rate wears the same one']}


def shared_bins(signatures: Sequence[dict], tol_hz: float = DEFAULT_TOL_HZ) -> List[dict]:
    """Lines two or more sources both claim, within tol_hz. Those bins separate nothing and are dropped."""
    out = []
    sigs = [s for s in signatures if s.get('status') == 'OK']
    for a in range(len(sigs)):
        for b in range(a + 1, len(sigs)):
            for fa in sigs[a]['freqs_hz']:
                for fb_ in sigs[b]['freqs_hz']:
                    if abs(fa - fb_) <= tol_hz:
                        out.append({'sources': [sigs[a]['source_id'], sigs[b]['source_id']], 'freq_hz': round(0.5 * (fa + fb_), 3),
                                    'separation_hz': round(abs(fa - fb_), 4)})
    return out


def _mask_for(freqs: np.ndarray, lines: Sequence[float], drop: Sequence[float], tol_hz: float) -> np.ndarray:
    m = np.zeros(len(freqs), dtype=bool)
    for x in lines:
        if any(abs(x - d) <= tol_hz for d in drop):
            continue
        m |= np.abs(freqs - x) <= tol_hz
    return m


def _csm_subset(csm: dict, mask: np.ndarray) -> dict:
    return {'freqs': csm['freqs'][mask], 'csm': csm['csm'][mask], 't0': csm['t0'], 't1': csm['t1'], 'segments': csm['segments']}


def beam_by_signature(traces: Sequence[Trace], sensors: Sequence[Sensor], signatures: Sequence[dict], band: Tuple[float, float] = (1.0, 20.0),
                      seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 41, method: str = 'bartlett', tol_hz: float = DEFAULT_TOL_HZ,
                      min_bins: int = MIN_BINS, min_coherence: float = 0.5, max_rival: float = 0.9) -> dict:
    """One window, several sources: a bearing per source from that source's own frequency bins. Sources whose
    lines collide within tol_hz are NOT_SEPARABLE and get no bearing; a source whose own bins are too few or
    not coherent gets NOT_COHERENT. The whole-band beam is reported beside them, for the reader to compare."""
    if len(traces) != len(sensors) or len(sensors) < 3:
        raise ValueError('beamforming needs three or more sensors, one record each, in the same order')
    geo = array_geometry(sensors)
    xy = geo['xy_km']
    csm = AR.cross_spectral_matrix(traces, band, seg_s)
    freqs = csm['freqs']
    whole = AR.beam_power(csm, xy, s_max, n_grid, method)
    arf = AR.array_response(xy, freqs, 1.0, 81, (0.0, 0.0))
    collisions = shared_bins(signatures, tol_hz)
    dropped = {c['freq_hz'] for c in collisions}
    collided_ids = {sid for c in collisions for sid in c['sources']}
    rows = []
    for sig in signatures:
        row = {'source_id': sig['source_id'], 'back_azimuth_deg': None, 'bins_used': 0, 'coherent': False}
        if sig.get('status') != 'OK':
            row['verdict'] = 'NO_SIGNATURE'
            row['detail'] = sig.get('detail', 'this source has no signature')
            rows.append(row); continue
        mask = _mask_for(freqs, sig['freqs_hz'], dropped, tol_hz)
        n_own = int(mask.sum())
        row['bins_used'] = n_own
        row['lines_hz'] = [x for x in sig['freqs_hz'] if not any(abs(x - d) <= tol_hz for d in dropped)]
        if sig['source_id'] in collided_ids and n_own < min_bins:
            row['verdict'] = 'NOT_SEPARABLE'
            row['detail'] = ('its lines fall within %.2f Hz of another listed source\'s; the bins they share separate nothing and what is left is '
                             'too little to beam' % tol_hz)
            rows.append(row); continue
        if n_own < min_bins:
            row['verdict'] = 'INSUFFICIENT_BINS'
            row['detail'] = f'{n_own} of its own bin(s) in this window (floor {min_bins})'
            rows.append(row); continue
        sub = _csm_subset(csm, mask)
        bp = AR.beam_power(sub, xy, s_max, n_grid, method)
        # the geometry judged at THIS source's own lines, not at the whole band. A few bins all above the
        # array's spatial Nyquist alias: the pattern repeats and the peak found may be a lobe, not the source.
        # The whole band hides this because bins of different wavelength put their lobes in different places.
        own_arf = AR.array_response(xy, sub['freqs'], s_max, n_grid, (0.0, 0.0))
        slow = bp['slowness_s_km']
        az_res = math.degrees(math.atan2(0.5 * arf['half_power_width_s_km'], slow)) * 2.0 if slow > 0 else 180.0
        row.update({'back_azimuth_deg': round(float(bp['back_azimuth_deg']), 2), 'slowness_s_km': round(float(slow), 4),
                    'apparent_velocity_km_s': round(float(bp['apparent_velocity_km_s']), 3),
                    'coherence_max_bin': round(float(bp['coherence_max_bin']), 4), 'coherent_bins': int(bp['coherent_bins']),
                    'tolerance_deg': round(0.5 * az_res + 1.0, 2), 'aliasing_lobes': bool(own_arf['aliasing_lobes'])})
        # does anything else on the grid compete? A source whose own lines all sit above the array's spatial
        # Nyquist has lobes as tall as its peak, and the array cannot tell which one is the source. The whole
        # band hides this: bins of different wavelength put their lobes in different places and they cancel.
        P = bp['power']
        pmax = float(P.max())
        px_m, py_m = -slow * math.sin(math.radians(bp['back_azimuth_deg'])), -slow * math.cos(math.radians(bp['back_azimuth_deg']))
        apart = max(own_arf['half_power_width_s_km'], float(bp['sx'][0, 1] - bp['sx'][0, 0]))
        rival, alts = 0.0, []
        for ai in range(1, P.shape[0] - 1):
            for aj in range(1, P.shape[1] - 1):
                v = float(P[ai, aj])
                if v < 0.5 * pmax or v < P[ai - 1:ai + 2, aj - 1:aj + 2].max():
                    continue
                px_, py_ = float(bp['sx'][ai, aj]), float(bp['sy'][ai, aj])
                if math.hypot(px_ - px_m, py_ - py_m) <= apart:
                    continue                                       # the maximum itself, inside its own half-power width
                alts.append((round(v / pmax, 3), round((math.degrees(math.atan2(-px_, -py_)) + 360.0) % 360.0, 1)))
                rival = max(rival, v / pmax)
        alts.sort(reverse=True)
        row['rival_peak_ratio'] = round(rival, 3)
        row['aliasing_lobes'] = bool(own_arf['aliasing_lobes'])
        if rival >= max_rival:
            row['verdict'] = 'ALIASED'
            row['alternatives_deg'] = [a[1] for a in alts[:6]]
            row['detail'] = (f"another peak on the slowness grid stands at {rival:.0%} of this one ({', '.join(str(a[1]) for a in alts[:4])} deg): this "
                             f"source's own lines ({', '.join(f'{v:g}' for v in row['lines_hz'][:6])} Hz) alias on this geometry and the array cannot say "
                             'which peak is the source, so no bearing is claimed')
        elif bp['coherence_max_bin'] >= min_coherence and bp['coherent_bins'] >= min_bins:
            row['verdict'] = 'POINTED'
            row['coherent'] = True
            row['detail'] = f"{n_own} own bin(s), {row['coherent_bins']} coherent (best {row['coherence_max_bin']}); nearest rival peak {rival:.0%}"
        else:
            row['verdict'] = 'NOT_COHERENT'
            row['detail'] = (f"{n_own} own bin(s) but only {row['coherent_bins']} coherent (best {row['coherence_max_bin']}, floor {min_coherence}): "
                             'nothing of this source crossed the array as a plane wave in this window')
        rows.append(row)
    return {'protocol': 'seismic_signature.beam_by_signature', 'window': {'start': iso(csm['t0'], 0), 'end': iso(csm['t1'], 0), 'segments': csm['segments']},
            'band_hz': list(band), 'method': method, 'tol_hz': tol_hz, 'min_bins': min_bins, 'max_rival': max_rival,
            'array': {'lat': geo['lat0'], 'lon': geo['lon0'], 'n_sensors': len(sensors), 'aperture_km': round(geo['aperture_km'], 4)},
            'sources': rows, 'n_pointed': sum(1 for r in rows if r['verdict'] == 'POINTED'), 'shared_lines': collisions,
            'whole_band': {'back_azimuth_deg': round(float(whole['back_azimuth_deg']), 2), 'coherence_max_bin': round(float(whole['coherence_max_bin']), 4),
                           'note': 'the beam of the whole band: one direction for every machine in it, which is what a single bearing means when more than one works'},
            'basis': "the cross-spectral matrix sliced to each source's own lines and beamed; a machinery line arriving as one plane wave is coherent in "
                     'its own bin even when the band as a whole is not',
            'not_a_measurement': ['a bearing for a source with no signature, or whose lines another source also claims',
                                  'a bearing from bins that were not coherent in this window',
                                  'a bearing with another peak of the same height elsewhere on the grid: the array cannot say which is the source',
                                  'the whole-band bearing as any one source\'s direction when more than one is working']}


def multi_bearing_history(traces: Sequence[Trace], sensors: Sequence[Sensor], signatures: Sequence[dict], band: Tuple[float, float] = (1.0, 20.0),
                          win_s: float = 600.0, step_s: Optional[float] = None, seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 41,
                          method: str = 'bartlett', tol_hz: float = DEFAULT_TOL_HZ, min_bins: int = MIN_BINS, name: str = '') -> dict:
    """A bearing history per source, all from the same record: window by window, each source beamed on its own
    bins. The per-source histories have the shape `seismic_track.position_history` reads, so several sources
    can be tracked at once from the same arrays."""
    step_s = step_s or win_s
    geo = array_geometry(sensors)
    t0 = max(tr.starttime for tr in traces)
    t1 = min(tr.endtime for tr in traces)
    if t1 - t0 < win_s:
        raise ValueError(f'the common record ({t1 - t0:.0f} s) is shorter than one window ({win_s:.0f} s)')
    band = (float(band[0]), float(min(band[1], 0.4 * traces[0].sample_rate)))
    ids = [s['source_id'] for s in signatures]
    hist: Dict[str, dict] = {sid: {'array': {'name': name or sensors[0].sensor_id, 'lat': geo['lat0'], 'lon': geo['lon0'], 'n_sensors': len(sensors),
                                             'aperture_km': round(geo['aperture_km'], 4), 'source_id': sid},
                                   'band_hz': list(band), 'win_s': win_s, 'step_s': step_s, 'method': method, 'tolerance_deg': None, 'windows': [],
                                   'not_a_measurement': ['a bearing in a window this source was not coherent in',
                                                         'a bearing for a source whose lines another source also claims']} for sid in ids}
    windows_meta = []
    t = t0
    while t + win_s <= t1 + 1e-6:
        win = [tr.slice(t, t + win_s) for tr in traces]
        try:
            r = beam_by_signature(win, sensors, signatures, band, seg_s, s_max, n_grid, method, tol_hz, min_bins)
            rows = {x['source_id']: x for x in r['sources']}
            shared = r['shared_lines']
        except ValueError as e:
            rows, shared = {}, []
            for sid in ids:
                rows[sid] = {'source_id': sid, 'verdict': 'NO_BEAM', 'detail': str(e), 'back_azimuth_deg': None, 'coherent': False}
        for sid in ids:
            x = rows.get(sid, {'verdict': 'NO_BEAM', 'back_azimuth_deg': None, 'coherent': False})
            w = {'start_utc': iso(t, 0), 'end_utc': iso(t + win_s, 0), 'start': t, 'coherent': bool(x.get('coherent')),
                 'back_azimuth_deg': x.get('back_azimuth_deg'), 'tolerance_deg': x.get('tolerance_deg'), 'verdict': x.get('verdict'),
                 'bins_used': x.get('bins_used', 0), 'coherence_max_bin': x.get('coherence_max_bin')}
            if hist[sid]['tolerance_deg'] is None and w['tolerance_deg']:
                hist[sid]['tolerance_deg'] = w['tolerance_deg']
            hist[sid]['windows'].append(w)
        windows_meta.append({'start_utc': iso(t, 0), 'pointed': sum(1 for sid in ids if rows.get(sid, {}).get('verdict') == 'POINTED'), 'shared_lines': len(shared)})
        t += step_s
    for sid in ids:
        h = hist[sid]
        h['n_windows'] = len(h['windows'])
        h['n_coherent'] = sum(1 for w in h['windows'] if w['coherent'])
        tol = h['tolerance_deg']
        for w in h['windows']:
            if w['tolerance_deg'] is None:
                w['tolerance_deg'] = tol
    return {'protocol': 'seismic_signature.multi_bearing_history', 'histories': hist, 'source_ids': ids, 'windows': windows_meta,
            'n_windows': len(windows_meta), 'band_hz': list(band), 'win_s': win_s,
            'basis': 'beam_by_signature on every window; each source beamed on its own lines',
            'not_a_measurement': ['a source with no signature has no history here, only the reason it has none']}


def multi_track(per_array: Sequence[dict], truth: Optional[dict] = None, min_crossing_deg: float = 15.0) -> dict:
    """Several arrays, each with a bearing history per source (multi_bearing_history), crossed into one track
    per source. This is the field: every rig that has a signature and was pointed at by two or more arrays in
    the same window gets its own positions, its own ellipses and its own track, from the same records."""
    from . import seismic_track as T
    if len(per_array) < 2:
        raise ValueError('a track needs two or more arrays')
    ids: List[str] = []
    for a in per_array:
        for sid in a['source_ids']:
            if sid not in ids:
                ids.append(sid)
    out = {'protocol': 'seismic_signature.multi_track', 'source_ids': ids, 'arrays': [a['histories'][a['source_ids'][0]]['array']['name'] for a in per_array if a['source_ids']],
           'tracks': {}, 'min_crossing_deg': min_crossing_deg,
           'basis': "each source's bearing history from every array, crossed window by window by seismic_track.position_history",
           'not_a_measurement': ['a track for a source fewer than two arrays pointed at in the same window',
                                 'a position in any window listed as a gap, or finer than its ellipse']}
    for sid in ids:
        hists = [a['histories'][sid] for a in per_array if sid in a['histories']]
        hists = [h for h in hists if h['n_windows']]
        row = {'source_id': sid, 'arrays': len(hists), 'coherent_windows': [h['n_coherent'] for h in hists]}
        if len(hists) < 2:
            row['status'] = 'INSUFFICIENT_ARRAYS'
            row['detail'] = f'{len(hists)} array(s) have a bearing history for this source; a position needs two'
            out['tracks'][sid] = row
            continue
        pos = T.position_history(hists, min_crossing_deg)
        row['status'] = 'OK'
        row['positions'] = pos['n_positions']
        row['windows'] = pos['n_windows']
        row['track'] = pos['track']
        row['position_history'] = pos
        if truth and sid in truth:
            pts = truth[sid]
            row['verdict'] = T.track_verdict(pos, pts)
        out['tracks'][sid] = row
    out['n_tracked'] = sum(1 for r in out['tracks'].values() if r.get('positions'))
    return out


# ---------------------------------------------------------------------------
# the labelled scene: several sources working at the same time
# ---------------------------------------------------------------------------
def synthetic_multi_scene(seed: int = 13, fs: float = 50.0, hours: float = 6.0, centre=(31.0, -102.0), n_sensors: int = 9, aperture_km: float = 1.2,
                          sources_km: Sequence[Tuple[float, float, float]] = ((40.0, 8.0, 1.7), (140.0, 12.0, 2.9), (250.0, 20.0, 4.3)),
                          solo_hours: float = 1.0, v_km_s: float = 2.5, q_factor: float = 100.0, noise: float = 30.0, sensor_noise: float = 1.0,
                          collide: bool = False) -> Tuple[List[Trace], List[Sensor], List[Source], dict]:
    """An array and several rigs, each at (bearing, distance, pump rate). Every rig works alone for `solo_hours`
    in turn - so each can be learned - and then they all work together for the rest of the record, which is the
    case the whole module exists for. With collide=True two rigs are given the same pump rate, so their lines
    fall on each other and the method must say NOT_SEPARABLE instead of inventing two bearings."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = centre
    sensors: List[Sensor] = [Sensor('M00', lat0, lon0)]
    for k in range(n_sensors - 1):
        ang = 2 * math.pi * k / (n_sensors - 1)
        r = aperture_km / 2.0 * (1.0 if k % 2 == 0 else 0.55)
        lat, lon = AR.xy_to_latlon(r * math.sin(ang), r * math.cos(ang), lat0, lon0)
        sensors.append(Sensor(f'M{k + 1:02d}', lat, lon))
    geo = array_geometry(sensors)
    xy = geo['xy_km']
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    t0 = S.parse_time('2025-06-01T00:00:00Z')
    F = np.fft.rfftfreq(n, 1 / fs)
    data = np.zeros((len(sensors), n))
    bg_baz = float(rng.uniform(0, 360))
    bg = np.cumsum(rng.normal(0, 0.02, n)); bg -= np.linspace(bg[0], bg[-1], n); bg = noise * bg / (bg.std() + 1e-12)
    BG = np.fft.rfft(bg)
    u_bg = np.array([math.sin(math.radians(bg_baz)), math.cos(math.radians(bg_baz))])
    for k in range(len(sensors)):
        tau = -(xy[k] @ u_bg) / 0.4
        data[k] += np.fft.irfft(BG * np.exp(-2j * np.pi * F * tau), n) + sensor_noise * rng.normal(0, 1, n)
    srcs: List[Source] = []
    lines: Dict[str, List[float]] = {}
    quiet_s = solo_hours * 3600.0                                   # the first hour is quiet: the baseline
    for k, (baz, dist, pump) in enumerate(sources_km):
        if collide and k == 1:
            pump = sources_km[0][2]                                 # the same pump rate as the first rig: the lines collide
        lat, lon = AR.xy_to_latlon(dist * math.sin(math.radians(baz)), dist * math.cos(math.radians(baz)), lat0, lon0)
        solo0 = quiet_s + k * solo_hours * 3600.0                   # its own hour alone
        together = quiet_s + len(sources_km) * solo_hours * 3600.0   # from here every rig works at once
        sid = f'RIG-{k + 1}'
        srcs.append(Source(sid, lat, lon, t0 + solo0, t0 + solo0 + solo_hours * 3600.0, 'rig', 'synthetic multi scene: its hour alone'))
        srcs.append(Source(sid, lat, lon, t0 + together, t0 + hours * 3600.0, 'rig', 'synthetic multi scene: all rigs together'))
        comps = [(pump, 1.0), (2 * pump, 0.6), (3 * pump, 0.4), (5 * pump, 0.25)]
        comps = [c for c in comps if c[0] < 0.8 * fs / 2]
        lines[sid] = [round(c[0], 3) for c in comps]
        m = ((t >= solo0) & (t < solo0 + solo_hours * 3600.0)) | (t >= together)
        u = np.array([math.sin(math.radians(baz)), math.cos(math.radians(baz))])
        sig = np.zeros(n)
        for f_hz, rel in comps:
            amp = 6000.0 * rel / dist * math.exp(-math.pi * f_hz * dist / (q_factor * v_km_s))
            sig[m] += amp * np.sin(2 * np.pi * f_hz * t[m] + rng.uniform(0, 2 * np.pi))
        sig *= 1.0 + 0.3 * np.sin(2 * np.pi * 0.05 * t + rng.uniform(0, 2 * np.pi))
        SG = np.fft.rfft(sig)
        for j in range(len(sensors)):
            tau = -(xy[j] @ u) / v_km_s
            data[j] += np.fft.irfft(SG * np.exp(-2j * np.pi * F * tau), n)
    traces = [Trace('XX', s.sensor_id, '', 'HHZ', t0, fs, np.round(data[k]).astype(np.int32), source='synthetic_multi_scene', encoding='SYNTHETIC')
              for k, s in enumerate(sensors)]
    meta = {'status': 'SIMULATION_SELF_TEST', 'v_km_s': v_km_s, 'q_factor': q_factor, 'spreading': '1/r', 'collide': bool(collide),
            'true_bearings_deg': {s.source_id: round(AR.bearing_deg(lat0, lon0, s.lat, s.lon), 2) for s in srcs},
            'distances_km': {s.source_id: round(haversine_km(lat0, lon0, s.lat, s.lon), 2) for s in srcs},
            'together_utc': iso(t0 + quiet_s + len(sources_km) * solo_hours * 3600.0, 0),
            'lines_hz': lines, 'solo_hours': solo_hours, 'quiet_hours': solo_hours, 'aperture_km': round(geo['aperture_km'], 4),
            'array_centre': {'lat': lat0, 'lon': lon0},
            'note': 'every rig works alone for an hour (so it can be learned) and then they all work together; the scene is an assumption for checking '
                    'the arithmetic and says nothing about any ground'}
    return traces, sensors, srcs, meta


def synthetic_multi_field(seed: int = 17, fs: float = 50.0, hours: float = 5.0, centre=(31.0, -102.0),
                          arrays_km: Sequence[Tuple[float, float]] = ((-4.0, -3.0), (4.5, -2.5)), n_sensors: int = 9, aperture_km: float = 1.2,
                          rigs_km: Sequence[Tuple[float, float, float]] = ((0.0, 9.0, 1.7), (-7.0, 7.0, 2.2), (8.0, 10.0, 2.9)),
                          solo_hours: float = 0.5, v_km_s: float = 2.5, q_factor: float = 100.0, noise: float = 30.0,
                          sensor_noise: float = 1.0) -> Tuple[List[List[Trace]], List[List[Sensor]], List[Source], dict]:
    """A field: two or more arrays and several rigs at (x, y) from the scene centre, each with its own pump
    rate. Every rig works alone for `solo_hours` in turn - so each can be learned - and then all of them work
    together for the rest of the record. This is the case the leg exists for: several wells at once, each
    mapped from its own signature. Returns one trace list and one sensor list per array."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = centre
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    t0 = S.parse_time('2025-06-01T00:00:00Z')
    F = np.fft.rfftfreq(n, 1 / fs)
    quiet_s = solo_hours * 3600.0
    together = quiet_s + len(rigs_km) * solo_hours * 3600.0
    srcs: List[Source] = []
    lines: Dict[str, List[float]] = {}
    for k, (rx, ry, pump) in enumerate(rigs_km):
        sid = f'RIG-{k + 1}'
        lat, lon = AR.xy_to_latlon(rx, ry, lat0, lon0)
        solo0 = quiet_s + k * solo_hours * 3600.0
        srcs.append(Source(sid, lat, lon, t0 + solo0, t0 + solo0 + solo_hours * 3600.0, 'rig', 'synthetic field: its spell alone'))
        srcs.append(Source(sid, lat, lon, t0 + together, t0 + hours * 3600.0, 'rig', 'synthetic field: every rig together'))
        lines[sid] = [round(pump * m, 3) for m in (1, 2, 3) if pump * m < 0.8 * fs / 2]
    phases = {f'RIG-{k + 1}': [rng.uniform(0, 2 * np.pi) for _ in range(3)] for k in range(len(rigs_km))}
    all_traces, all_sensors = [], []
    for ai, (ax, ay) in enumerate(arrays_km):
        alat, alon = AR.xy_to_latlon(ax, ay, lat0, lon0)
        sensors: List[Sensor] = [Sensor(f'F{ai + 1}S00', alat, alon)]
        for k in range(n_sensors - 1):
            ang = 2 * math.pi * k / (n_sensors - 1)
            r = aperture_km / 2.0 * (1.0 if k % 2 == 0 else 0.55)
            lat, lon = AR.xy_to_latlon(ax + r * math.sin(ang), ay + r * math.cos(ang), lat0, lon0)
            sensors.append(Sensor(f'F{ai + 1}S{k + 1:02d}', lat, lon))
        geo = array_geometry(sensors)
        xy = geo['xy_km']
        data = np.zeros((len(sensors), n))
        bg_baz = float(rng.uniform(0, 360))
        bg = np.cumsum(rng.normal(0, 0.02, n)); bg -= np.linspace(bg[0], bg[-1], n); bg = noise * bg / (bg.std() + 1e-12)
        BG = np.fft.rfft(bg)
        u_bg = np.array([math.sin(math.radians(bg_baz)), math.cos(math.radians(bg_baz))])
        for k in range(len(sensors)):
            tau = -(xy[k] @ u_bg) / 0.4
            data[k] += np.fft.irfft(BG * np.exp(-2j * np.pi * F * tau), n) + sensor_noise * rng.normal(0, 1, n)
        for k, (rx, ry, pump) in enumerate(rigs_km):
            sid = f'RIG-{k + 1}'
            dx, dy = rx - ax, ry - ay
            dist = max(math.hypot(dx, dy), 0.1)
            baz = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
            u = np.array([math.sin(math.radians(baz)), math.cos(math.radians(baz))])
            solo0 = quiet_s + k * solo_hours * 3600.0
            m = ((t >= solo0) & (t < solo0 + solo_hours * 3600.0)) | (t >= together)
            sig = np.zeros(n)
            for (f_hz, rel), ph in zip([(pump, 1.0), (2 * pump, 0.6), (3 * pump, 0.4)], phases[sid]):
                if f_hz >= 0.8 * fs / 2:
                    continue
                amp = 6000.0 * rel / dist * math.exp(-math.pi * f_hz * dist / (q_factor * v_km_s))
                sig[m] += amp * np.sin(2 * np.pi * f_hz * t[m] + ph)
            sig *= 1.0 + 0.3 * np.sin(2 * np.pi * 0.05 * t + rng.uniform(0, 2 * np.pi))
            SG_ = np.fft.rfft(sig)
            for j in range(len(sensors)):
                tau = -(xy[j] @ u) / v_km_s
                data[j] += np.fft.irfft(SG_ * np.exp(-2j * np.pi * F * tau), n)
        all_traces.append([Trace('XX', s.sensor_id, '', 'HHZ', t0, fs, np.round(data[k]).astype(np.int32), source='synthetic_multi_field', encoding='SYNTHETIC')
                           for k, s in enumerate(sensors)])
        all_sensors.append(sensors)
    meta = {'status': 'SIMULATION_SELF_TEST', 'v_km_s': v_km_s, 'q_factor': q_factor, 'spreading': '1/r',
            'arrays_km': [list(a) for a in arrays_km], 'centre': {'lat': lat0, 'lon': lon0},
            'rigs_km': {f'RIG-{k + 1}': [rigs_km[k][0], rigs_km[k][1]] for k in range(len(rigs_km))},
            'rigs_latlon': {f'RIG-{k + 1}': list(AR.xy_to_latlon(rigs_km[k][0], rigs_km[k][1], lat0, lon0)) for k in range(len(rigs_km))},
            'lines_hz': lines, 'together_utc': iso(t0 + together, 0), 'solo_hours': solo_hours,
            'note': 'every rig works alone for a spell (so it can be learned) and then they all work together; the scene is an assumption for checking '
                    'the arithmetic and says nothing about any ground'}
    return all_traces, all_sensors, srcs, meta


def field_selftest(seed: int = 17, hours: float = 5.0, win_s: float = 600.0) -> dict:
    """The whole thing end to end on a labelled field: learn each rig while it works alone, then, in the hours
    they all work together, give every rig its own bearing from every array and cross them into a position."""
    from . import seismic_track as T
    all_traces, all_sensors, srcs, meta = synthetic_multi_field(seed=seed, hours=hours)
    learned = learn_signatures(all_traces[0][0], srcs, (1.0, 20.0), win_s)
    together = S.parse_time(meta['together_utc'])
    per_array = []
    for i, (trs, sens) in enumerate(zip(all_traces, all_sensors)):
        cut = [tr.slice(together, tr.endtime) for tr in trs]
        per_array.append(multi_bearing_history(cut, sens, learned['signatures'], (1.0, 20.0), win_s, name=f'array-{i + 1}'))
    truth = {sid: [T.TruthPoint(sid, ll[0], ll[1], together + 1.0, 'synthetic field'),
                   T.TruthPoint(sid, ll[0], ll[1], together + hours * 3600.0, 'synthetic field')] for sid, ll in meta['rigs_latlon'].items()}
    mt = multi_track(per_array, truth)
    misses = {}
    for sid, row in mt['tracks'].items():
        if row.get('verdict'):
            misses[sid] = {'verdict': row['verdict']['verdict'], 'hit': row['verdict']['n_hit'], 'of': row['verdict']['n_compared'],
                           'positions': row['positions']}
    tracked = [sid for sid, r in mt['tracks'].items() if r.get('positions')]
    ok = (len(tracked) >= 2 and all(v['verdict'] in ('TRACKED', 'PARTIAL') for v in misses.values() if v['positions'] >= 3))
    return {'status': 'SIMULATION_SELF_TEST', 'ok': bool(ok), 'scene': meta, 'learned': learned, 'multi_track': mt,
            'tracked': tracked, 'verdicts': misses}


def selftest(seed: int = 13, hours: float = 6.0, win_s: float = 600.0) -> dict:
    """Learn three rigs while each works alone, then take a bearing for each in the hours they all work."""
    traces, sensors, srcs, meta = synthetic_multi_scene(seed=seed, hours=hours)
    learned = learn_signatures(traces[0], srcs, (1.0, 20.0), win_s)
    together_from = S.parse_time(meta['together_utc'])
    win = [tr.slice(together_from + 600.0, together_from + 600.0 + win_s) for tr in traces]
    r = beam_by_signature(win, sensors, learned['signatures'], (1.0, 20.0))
    errs = {}
    within = True
    for row in r['sources']:
        if row['verdict'] == 'POINTED':
            e = float(AR._angle_diff(row['back_azimuth_deg'], meta['true_bearings_deg'][row['source_id']]))
            errs[row['source_id']] = round(e, 2)
            if abs(e) > (row.get('tolerance_deg') or 5.0):
                within = False                                   # a claimed bearing outside its own tolerance is the one thing that must never happen
    ids = {s.source_id for s in srcs}
    ok = (learned['n_with_signature'] == len(ids) and r['n_pointed'] >= 2 and within
          and all(x['verdict'] in ('POINTED', 'ALIASED') for x in r['sources']))
    # the colliding case: two rigs with the same pump rate must not be given two bearings
    tr2, sen2, src2, meta2 = synthetic_multi_scene(seed=seed, hours=hours, collide=True)
    l2 = learn_signatures(tr2[0], src2, (1.0, 20.0), win_s)
    tf2 = S.parse_time(meta2['together_utc'])
    r2 = beam_by_signature([x.slice(tf2 + 600.0, tf2 + 600.0 + win_s) for x in tr2], sen2, l2['signatures'], (1.0, 20.0))
    collided = [x['verdict'] for x in r2['sources'] if x['source_id'] in ('RIG-1', 'RIG-2')]
    ok_collide = bool(r2['shared_lines']) and all(v != 'POINTED' for v in collided)
    return {'status': 'SIMULATION_SELF_TEST', 'ok': bool(ok and ok_collide), 'scene': meta, 'learned': learned, 'beam': r,
            'bearing_errors_deg': errs, 'collide': {'shared_lines': r2['shared_lines'], 'verdicts': collided, 'ok': ok_collide}}


def report_text(r: dict) -> str:
    lines = [f"SIGNATURES AND SIMULTANEOUS BEARINGS - {r['window']['start']} to {r['window']['end']}", '=' * 70,
             f"array {r['array']['n_sensors']} sensors, aperture {r['array']['aperture_km']} km; band {r['band_hz'][0]:g}-{r['band_hz'][1]:g} Hz, {r['method']}",
             f"the whole band beams {r['whole_band']['back_azimuth_deg']} deg - one direction for every machine in it"]
    for x in r['sources']:
        if x['verdict'] == 'POINTED':
            lines.append(f"  {x['source_id']}: {x['back_azimuth_deg']} deg +/- {x['tolerance_deg']} from {x['bins_used']} of its own bins "
                         f"({x['coherent_bins']} coherent); lines {', '.join(f'{v:g}' for v in x['lines_hz'][:6])} Hz")
        else:
            lines.append(f"  {x['source_id']}: {x['verdict']} - {x.get('detail', '')}")
    for c in r['shared_lines']:
        lines.append(f"  shared line {c['freq_hz']} Hz: {' and '.join(c['sources'])} both claim it - it separates nothing")
    lines.append('not a measurement: ' + '; '.join(r['not_a_measurement']))
    return '\n'.join(lines)
