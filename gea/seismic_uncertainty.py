# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_uncertainty - how well a bearing is known, measured rather than assumed, and then checked.

Every ellipse this leg has drawn came from geometry alone: the array response function's half-power
width, which is what the array could resolve at perfect signal-to-noise. That is a floor, not an error
bar. Two records of the same array, one clean and one barely above the noise, got the same ellipse, and
a track verdict asking whether the truth lies inside it was asking a question about the geometry rather
than about the data.

What is measured here instead: the window is cut into equal sub-windows and each is beamed on its own.
Every sub-window sees the same source and a different draw of the noise, so the scatter of their
bearings is what the noise does to the answer - measured from the record, needing no model of the noise
at all. The standard error of their mean is what the whole window is worth.

The first thing tried here was beaming each frequency bin on its own, which is wrong on a small array
and instructively so: one frequency has no diversity to break the array's spatial aliasing, so the
single-bin bearings scatter by tens of degrees whatever the signal-to-noise, and the "uncertainty" it
measured was the geometry rather than the data. The per-bin bearings are still reported, as the
diagnostic they are.

The floor is the beamformer's own search grid turned into degrees: nothing can be reported finer than
the step the maximum was found on. The sub-windows are therefore beamed on a grid narrowed to the
slowness the whole window already found, which is where the answer is and costs nothing, so the floor
sits well below what the noise is doing instead of hiding it. Note that this floor is NOT the array
response half-power width - that is how far apart two sources must be to be seen as two, and finding
the middle of one peak is a different and easier thing than separating two. All three are printed.

And then the part that makes it a measurement rather than a number: `coverage` takes a record whose
true bearing is known, runs every window, and counts how often the truth actually falls inside one
sigma and two. If the answer is not near 68 and 95 per cent, the uncertainty is wrong, the module says
which way it is wrong, and it returns the factor that would put it right.

That factor is not a cosmetic. Resampling measures the part of the error that is random between
sub-windows. It cannot see a bias that is the same all through the window - another source in the band
pulling the beam a fixed amount, say - and on a record where that dominates, the sigma will be too
small and coverage is what finds out. The honest shape of this is to measure, to check, and to carry
the check's answer, rather than to publish a sigma nobody has counted.

What this module will not call a measurement:

- an uncertainty from fewer than `min_parts` sub-windows: the scatter of two numbers is not a scatter;
- an uncertainty finer than the beamformer's own search grid;
- the array response half-power width as an error bar: it is a resolution, which is a different thing;
- a calibration from one scene: coverage is measured on the record it is given and says so;
- a sigma that has not been checked by coverage as an error bar anyone should rely on: resampling sees
  the random part and not a bias steady through the window.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic_array as AR
from .seismic import Trace, iso
from .seismic_array import Sensor, array_geometry

MIN_BINS = 3
MIN_PARTS = 4
DEFAULT_PARTS = 5


def circular_stats(deg: Sequence[float], weights: Optional[Sequence[float]] = None) -> dict:
    """Mean and standard deviation of a set of directions. Angles do not average arithmetically: 359 and 1
    average to 0, not 180."""
    a = np.radians(np.asarray(list(deg), dtype=float))
    w = np.ones(len(a)) if weights is None else np.clip(np.asarray(list(weights), dtype=float), 0.0, None)
    if len(a) == 0 or w.sum() <= 0:
        return {'mean_deg': None, 'sd_deg': None, 'R': 0.0, 'n': 0}
    C, Sm = float((w * np.cos(a)).sum() / w.sum()), float((w * np.sin(a)).sum() / w.sum())
    R = min(max(math.hypot(C, Sm), 1e-12), 1.0)
    return {'mean_deg': round((math.degrees(math.atan2(Sm, C)) + 360.0) % 360.0, 4),
            'sd_deg': round(math.degrees(math.sqrt(max(-2.0 * math.log(R), 0.0))), 4), 'R': round(R, 6), 'n': int(len(a))}


def bin_bearings(csm: dict, xy_km: np.ndarray, s_max: float = 3.0, n_grid: int = 41, method: str = 'bartlett',
                 min_coherence: float = 0.5, max_bins: int = 60) -> dict:
    """A bearing from each coherent frequency bin on its own. One plane wave gives the same direction in every
    bin it is in; noise gives a different one each time, and the difference between those two cases is the
    whole of the uncertainty."""
    whole = AR.beam_power(csm, xy_km, s_max, n_grid, method)
    per = np.asarray(whole['coherence_by_bin'], dtype=float)
    idx = [int(i) for i in np.argsort(-per) if per[int(i)] >= min_coherence][:max_bins]
    idx.sort()
    # each bin's weight is its own contribution to the beam: the power in that bin times how coherent it is.
    # The whole-band beam is that weighted sum, so the uncertainty of the whole-band beam is the scatter of
    # the same weighted set - not of bins counted equally, most of which carry almost none of the energy.
    tot = np.real(np.einsum('fii->f', csm['csm']))
    rows = []
    for i in idx:
        one = {'freqs': csm['freqs'][i:i + 1], 'csm': csm['csm'][i:i + 1], 't0': csm.get('t0'), 't1': csm.get('t1'),
               'segments': csm.get('segments'), 'fs': csm.get('fs'), 'nseg': csm.get('nseg')}
        b = AR.beam_power(one, xy_km, s_max, n_grid, method)
        rows.append({'freq_hz': round(float(csm['freqs'][i]), 4), 'back_azimuth_deg': round(float(b['back_azimuth_deg']), 3),
                     'slowness_s_km': round(float(b['slowness_s_km']), 4), 'coherence': round(float(per[i]), 4),
                     'weight': float(max(per[i], 0.0) * max(float(tot[i]), 0.0))})
    return {'bins': rows, 'n_bins': len(rows), 'min_coherence': min_coherence,
            'whole_band': {'back_azimuth_deg': round(float(whole['back_azimuth_deg']), 3), 'slowness_s_km': round(float(whole['slowness_s_km']), 4),
                           'coherence_max_bin': round(float(whole['coherence_max_bin']), 4)}}


def bearing_sigma(traces: Sequence[Trace], sensors: Sequence[Sensor], band: Tuple[float, float] = (1.0, 20.0), seg_s: float = 10.0,
                  s_max: float = 3.0, n_grid: int = 41, method: str = 'bartlett', min_coherence: float = 0.5,
                  parts: int = DEFAULT_PARTS, min_parts: int = MIN_PARTS, diagnose_bins: bool = False) -> dict:
    """How well this window's bearing is known, from the scatter of its own sub-windows.

    Each sub-window sees the same source and an independent draw of the noise, so how far their bearings move
    apart is what the noise is worth in degrees. The floor is the beamformer's own grid step."""
    geo = array_geometry(sensors)
    xy = np.asarray(geo['xy_km'], dtype=float)
    t0 = max(tr.starttime for tr in traces)
    t1 = min(tr.endtime for tr in traces)
    span = t1 - t0
    whole = AR.beam(list(traces), list(sensors), band, seg_s, s_max, n_grid, method)
    arf_half = float(whole['resolution']['azimuth_half_width_deg'])
    slow = float(whole['slowness_s_km'])
    # the sub-windows are searched on a grid narrowed to the slowness this window already found: the answer
    # is there, the grid step is then small enough not to be the limit, and it costs nothing extra
    s_local = min(s_max, max(2.5 * slow, 0.2)) if slow > 1e-9 else s_max
    step = (2.0 * s_local / max(n_grid - 1, 1)) * 3.0 / 40.0
    grid_deg = (math.degrees(math.atan2(step, slow)) if slow > 1e-9 else 180.0)
    out = {'protocol': 'seismic_uncertainty.bearing_sigma', 'band_hz': [float(band[0]), float(band[1])],
           'back_azimuth_deg': round(float(whole['back_azimuth_deg']), 3), 'coherence_max_bin': whole['coherence_max_bin'],
           'resolution_half_width_deg': round(arf_half, 3), 'grid_step_deg': round(grid_deg, 3), 'floor_deg': round(grid_deg, 3),
           's_max_local_s_km': round(s_local, 4),
           'parts_asked': parts, 'window': {'start': iso(t0, 0), 'end': iso(t1, 0)},
           'basis': 'the window cut into equal sub-windows, each beamed on its own; the scatter of their bearings is what the noise does to the '
                    "answer, and the standard error of their mean is what the whole window is worth. The floor is the beamformer's own grid step",
           'not_a_measurement': [f'an uncertainty from fewer than {min_parts} sub-windows: the scatter of two numbers is not a scatter',
                                 "an uncertainty finer than the beamformer's own search grid, printed here",
                                 'the array response half-power width as an error bar: that is how far apart two sources must be to be seen as two, '
                                 'and finding the middle of one peak is an easier thing',
                                 'a bearing itself: this says how well the bearing is known, not where the source is']}
    if diagnose_bins:
        try:
            csm = AR.cross_spectral_matrix(list(traces), band, seg_s)
            bb = bin_bearings(csm, xy, s_max, n_grid, method, min_coherence)
            st_b = circular_stats([r['back_azimuth_deg'] for r in bb['bins']], [r['weight'] for r in bb['bins']])
            out['per_bin'] = {'n_bins': bb['n_bins'], 'scatter_deg': st_b['sd_deg'],
                              'note': 'one frequency has no diversity to break the array\'s spatial aliasing, so this scatter is the geometry and '
                                      'not the noise; it is a diagnostic, not the uncertainty'}
        except ValueError:
            out['per_bin'] = None
    n = int(max(parts, 2))
    sub = span / n
    bazs, ok_parts = [], []
    for k in range(n):
        a, b = t0 + k * sub, t0 + (k + 1) * sub
        if b - a < max(2.0 * seg_s, 1.0):
            continue
        try:
            r = AR.beam([tr.slice(a, b) for tr in traces], list(sensors), band, seg_s, s_local, n_grid, method)
        except ValueError:
            continue
        bazs.append(float(r['back_azimuth_deg']))
        ok_parts.append({'start_utc': iso(a, 0), 'back_azimuth_deg': round(float(r['back_azimuth_deg']), 3),
                         'coherence_max_bin': r['coherence_max_bin']})
    out['parts'] = ok_parts
    out['n_parts'] = len(ok_parts)
    if len(ok_parts) < min_parts:
        out.update({'status': 'INSUFFICIENT_PARTS', 'sigma_deg': round(arf_half, 3), 'sigma_scatter_deg': None, 'sigma_mean_deg': None,
                    'detail': f'{len(ok_parts)} sub-window(s) of {n} were long enough to beam (floor {min_parts}): nothing is measured, so the '
                              f"array's own resolution ({arf_half:.2f} deg) is used, and it is a resolution and not an error bar"})
        return out
    st = circular_stats(bazs)
    sem = st['sd_deg'] / math.sqrt(len(bazs))
    sig = max(sem, grid_deg)
    out.update({'status': 'OK', 'sigma_scatter_deg': st['sd_deg'], 'sigma_mean_deg': round(sem, 4), 'sigma_deg': round(sig, 3),
                'part_mean_deg': st['mean_deg'],
                'detail': f"{len(bazs)} sub-window(s) of {sub:.0f} s give bearings scattering by {st['sd_deg']:.3f} deg, so their mean is worth "
                          f"{sem:.3f} deg; the grid step is {grid_deg:.3f} deg and the larger of the two is used. The array could only separate two "
                          f"sources {arf_half:.1f} deg apart, which is a different question"})
    return out


def coverage(traces: Sequence[Trace], sensors: Sequence[Sensor], true_back_azimuth_deg: float, band: Tuple[float, float] = (1.0, 20.0),
             win_s: float = 300.0, step_s: Optional[float] = None, seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 41,
             method: str = 'bartlett', start: Optional[float] = None, end: Optional[float] = None, min_windows: int = 5,
             parts: int = DEFAULT_PARTS) -> dict:
    """Does the uncertainty mean what it says? Window by window against a known bearing: how often does the
    truth fall inside one sigma, and inside two? For an honest sigma those are about 68 and 95 per cent. An
    uncertainty nobody has counted is a decoration."""
    step_s = step_s or win_s
    t0 = max(start or 0.0, max(tr.starttime for tr in traces))
    t1 = min(end or float('inf'), min(tr.endtime for tr in traces))
    rows = []
    t = t0
    while t + win_s <= t1 + 1e-6:
        win = [tr.slice(t, t + win_s) for tr in traces]
        try:
            r = bearing_sigma(win, sensors, band, seg_s, s_max, n_grid, method, parts=parts)
        except ValueError:
            t += step_s
            continue
        err = float(AR._angle_diff(r['back_azimuth_deg'], true_back_azimuth_deg))
        rows.append({'start_utc': iso(t, 0), 'back_azimuth_deg': r['back_azimuth_deg'], 'error_deg': round(err, 3),
                     'sigma_deg': r['sigma_deg'], 'n_parts': r.get('n_parts'), 'status': r['status'],
                     'inside_1': bool(abs(err) <= r['sigma_deg']), 'inside_2': bool(abs(err) <= 2.0 * r['sigma_deg'])})
        t += step_s
    out = {'protocol': 'seismic_uncertainty.coverage', 'true_back_azimuth_deg': float(true_back_azimuth_deg),
           'band_hz': [float(band[0]), float(band[1])], 'win_s': win_s, 'windows': rows, 'n': len(rows),
           'basis': 'every window of this record judged against a bearing known independently of it; the fraction of windows whose error is inside '
                    'one sigma and inside two',
           'not_a_measurement': ['a calibration for any other record: this is measured on the one it was given',
                                 'a true bearing: the truth here comes from outside this program']}
    if len(rows) < min_windows:
        out.update({'status': 'TOO_FEW_WINDOWS', 'detail': f'{len(rows)} window(s) (floor {min_windows}): nothing can be counted'})
        return out
    f1 = sum(1 for r in rows if r['inside_1']) / len(rows)
    f2 = sum(1 for r in rows if r['inside_2']) / len(rows)
    med_e = float(np.median([abs(r['error_deg']) for r in rows]))
    med_s = float(np.median([r['sigma_deg'] for r in rows]))
    # the factor that would make the claim true: the 68th percentile of |error|/sigma. Multiply the sigma by
    # it and 68 % of the windows fall inside one of them, which is what one sigma is supposed to mean.
    ratios = sorted(abs(r['error_deg']) / r['sigma_deg'] for r in rows if r['sigma_deg'] > 0)
    scale = float(np.percentile(ratios, 68)) if ratios else None
    scaled_1 = (sum(1 for r in rows if abs(r['error_deg']) <= scale * r['sigma_deg']) / len(rows)) if scale else None
    out.update({'recommended_scale': (round(scale, 3) if scale else None),
                'fraction_1sigma_after_scaling': (round(scaled_1, 3) if scaled_1 is not None else None),
                'inside_1sigma': sum(1 for r in rows if r['inside_1']), 'inside_2sigma': sum(1 for r in rows if r['inside_2']),
                'fraction_1sigma': round(f1, 3), 'fraction_2sigma': round(f2, 3),
                'median_abs_error_deg': round(med_e, 3), 'median_sigma_deg': round(med_s, 3),
                'ratio_error_to_sigma': round(med_e / med_s, 3) if med_s > 0 else None})
    if f1 < 0.5:
        out['status'] = 'OPTIMISTIC'
        out['detail'] = (f'the truth is inside one sigma in {f1:.0%} of {len(rows)} windows where about 68 % is expected: the uncertainty is too '
                         f'small, and an ellipse drawn from it claims more than the data supports. Multiplying it by {scale:.2f} would make the '
                         'claim true on this record - the part resampling cannot see is a bias steady through each window, not noise between them')
    elif f1 > 0.9 and f2 > 0.98:
        out['status'] = 'CONSERVATIVE'
        out['detail'] = (f'the truth is inside one sigma in {f1:.0%} of {len(rows)} windows where about 68 % is expected: the uncertainty is larger '
                         'than the data needs, which costs resolution but claims nothing false')
    else:
        out['status'] = 'CALIBRATED'
        out['detail'] = (f'the truth is inside one sigma in {f1:.0%} and inside two in {f2:.0%} of {len(rows)} windows, against about 68 % and 95 %: '
                         'the uncertainty means what it says on this record')
    return out


def selftest(seed: int = 5) -> dict:
    """SIMULATION_SELF_TEST on a labelled scene: the sigma measured by resampling must be the right SIZE -
    the factor coverage says would centre the claim must be near one, not near ten or a tenth - the truth
    must fall inside it about as often as it claims, nothing may be claimed finer than the beamformer's own
    grid, and a window too short to resample must say so instead of guessing.

    It also keeps the estimator that was tried first and rejected, as a number rather than a story: beaming
    each frequency bin on its own gives a scatter of tens of degrees on this array whatever the
    signal-to-noise, because one frequency cannot break the array's spatial aliasing. That scatter is the
    geometry, not the data, and it is reported beside the real one so the difference is visible."""
    traces, sensors, srcs, meta = AR.synthetic_array_scene(seed=seed, hours=2.2, distances_km=(6.0,))
    src = srcs[0]
    true_baz = meta['true_bearings_deg'][src.source_id]
    cov = coverage(traces, sensors, true_baz, (1.0, 20.0), 300.0, step_s=450.0, start=src.start, end=src.end,
                   min_windows=6, parts=4)
    one = bearing_sigma([t.slice(src.start, src.start + 300.0) for t in traces], sensors, (1.0, 20.0), parts=4, diagnose_bins=True)
    floored = one['sigma_deg'] >= one['floor_deg'] - 1e-6
    few = bearing_sigma([t.slice(src.end + 300.0, src.end + 600.0) for t in traces], sensors, (1.0, 20.0), parts=2)
    scale = cov.get('recommended_scale')
    right_size = bool(scale is not None and 0.4 <= scale <= 2.5)
    ok = bool(cov['status'] in ('CALIBRATED', 'CONSERVATIVE') and cov['n'] >= 6 and right_size
              and cov['fraction_2sigma'] >= 0.8
              and floored and one['status'] == 'OK' and one['n_parts'] >= MIN_PARTS
              and one['sigma_deg'] <= one['resolution_half_width_deg']
              and few['status'] == 'INSUFFICIENT_PARTS'
              and (one.get('per_bin') or {}).get('scatter_deg', 0) > 10.0 * one['sigma_deg'])
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'true_back_azimuth_deg': true_baz, 'coverage': cov,
            'one_window': one, 'too_few_parts': few, 'sigma_is_the_right_size': right_size,
            'recommended_scale': scale, 'floor_respected': floored,
            'per_bin_scatter_deg': (one.get('per_bin') or {}).get('scatter_deg'),
            'sigma_deg': one['sigma_deg'], 'grid_floor_deg': one['grid_step_deg'],
            'resolution_half_width_deg': one['resolution_half_width_deg']}


def report_text(res: dict) -> str:
    p = res.get('protocol', '')
    lines: List[str] = []
    if p.endswith('bearing_sigma'):
        lines.append(f"bearing {res['back_azimuth_deg']:.2f} deg +/- {res['sigma_deg']:.2f} [{res['status']}] from {res.get('n_parts', 0)} sub-window(s)")
        lines.append('  ' + res.get('detail', ''))
    elif p.endswith('coverage'):
        lines.append(f"coverage [{res['status']}]: {res.get('inside_1sigma', 0)} of {res['n']} windows inside 1 sigma "
                     f"({res.get('fraction_1sigma', 0):.0%}), {res.get('inside_2sigma', 0)} inside 2 ({res.get('fraction_2sigma', 0):.0%})")
        if res.get('median_sigma_deg'):
            lines.append(f"  median error {res['median_abs_error_deg']:.2f} deg against a median sigma of {res['median_sigma_deg']:.2f} deg")
        if res.get('recommended_scale'):
            lines.append(f"  the factor that would centre the claim: {res['recommended_scale']:.2f}")
        lines.append('  ' + res.get('detail', ''))
    for nm in res.get('not_a_measurement', []):
        lines.append('  not a measurement: ' + nm)
    return '\n'.join(lines)
