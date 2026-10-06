# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_qc - is this array any good? One bad sensor, and every bearing it ever gave was wrong.

Everything the array leg claims rests on the same assumption: that the sensors record the same ground
motion at the same time, scaled the same way, with the same polarity. Nothing checked it. An array is
not a set of records - it is the DIFFERENCES between them, so a fault that would be invisible in one
record is fatal across several:

- a dead or stuck channel contributes noise to every beam and drags the maximum toward nothing;
- a sensor whose clock is 30 ms out puts its energy in the wrong place. At a 1.2 km aperture and
  2.5 km/s that is degrees of azimuth - the whole error budget - and it looks exactly like a source in
  a slightly different direction, which is why nobody notices;
- a sensor wired backwards subtracts where it should add, and the beam of a clean plane wave loses
  most of its height;
- a sensor at a tenth of the gain is almost absent from the beam, so the array quietly has fewer
  sensors than its geometry claims.

The method here does not need a reference clock or a calibration shot. For a plane wave crossing the
array, the arrival delays must lie on a plane: delay = a*x + b*y + c. Measure the delays by
cross-correlation, fit that plane, and each sensor's residual is its timing error - the fit recovers
the slowness as a by-product, so the test is its own reference. Polarity comes from the sign of the
same correlation, gain from the band power against the array's own median, and death from the record
itself.

What this module will not call a measurement:

- a timing error smaller than the sample interval, or than the scatter of the fit;
- orientation: these are vertical-component records, and a sensor's horizontal orientation cannot be
  checked from them at all;
- an absolute clock error - the plane fit has an arbitrary constant, so what is measured is each
  sensor against the array, not the array against UTC;
- a verdict on a sensor from a window where nothing coherent crossed the array: with no plane wave
  there is no plane to fit, and the module says INSUFFICIENT rather than guessing;
- a fault's cause: a sensor that disagrees with its neighbours may be broken, may be on different
  ground, or may be the only one near the source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic_array as AR
from .seismic import Trace, iso
from .seismic_array import Sensor, array_geometry, bandpass

MIN_SENSORS = 3
DEFAULT_BAND = (1.0, 20.0)


@dataclass
class SensorQC:
    sensor_id: str
    verdict: str = 'OK'                 # OK | DEAD | CLIPPED | LOW_GAIN | HIGH_GAIN | TIMING | POLARITY | INCOHERENT | INSUFFICIENT
    flags: List[str] = field(default_factory=list)
    detail: str = ''
    npts: int = 0
    gaps: int = 0
    rms_counts: Optional[float] = None
    band_power_db: Optional[float] = None
    gain_db_vs_median: Optional[float] = None
    flat_fraction: Optional[float] = None
    clipped_fraction: Optional[float] = None
    correlation: Optional[float] = None          # with the array stack, signed
    timing_residual_s: Optional[float] = None
    timing_residual_deg: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _flat_fraction(x: np.ndarray, run: int = 10) -> float:
    """The fraction of samples inside a run of `run` identical values - a stuck digitiser."""
    if len(x) < run + 1:
        return 0.0
    same = np.diff(x) == 0
    if not same.any():
        return 0.0
    n_run, count, total = 0, 0, 0
    for v in same:
        if v:
            count += 1
        else:
            if count >= run - 1:
                total += count + 1
            count = 0
    if count >= run - 1:
        total += count + 1
    n_run = total
    return float(n_run) / float(len(x))


def _clipped_fraction(x: np.ndarray) -> float:
    """Samples sitting on the record's own extreme, repeated - a converter at its rail."""
    if len(x) < 3:
        return 0.0
    lo, hi = float(x.min()), float(x.max())
    if hi == lo:
        return 1.0
    at = ((x >= hi - 1e-9) | (x <= lo + 1e-9))
    # only count a rail value that repeats: a single maximum is just the maximum
    return float(at.sum()) / float(len(x)) if at.sum() > 2 else 0.0


def sensor_stats(traces: Sequence[Trace], sensors: Sequence[Sensor], band: Tuple[float, float] = DEFAULT_BAND) -> List[dict]:
    """What each record is on its own: how much of it there is, how loud it is in the band, whether it is
    stuck or clipped. Nothing here needs the other sensors."""
    out = []
    for tr, sn in zip(traces, sensors):
        x = np.asarray(tr.data, dtype=np.float64)
        info = tr.info()
        xb = bandpass(x, tr.sample_rate, band) if len(x) > 16 else x
        p = float(np.mean(xb ** 2))
        out.append({'sensor_id': sn.sensor_id, 'id': tr.id, 'npts': int(len(x)), 'gaps': int(info.get('gaps') or 0),
                    'sample_rate_hz': tr.sample_rate, 'rms_counts': round(float(np.std(x)), 3),
                    'band_power_db': (round(10.0 * math.log10(max(p, 1e-30)), 2)),
                    'flat_fraction': round(_flat_fraction(x), 5), 'clipped_fraction': round(_clipped_fraction(x), 5)})
    return out


def plane_fit(xy_km: np.ndarray, lags_s: np.ndarray, weights: Optional[np.ndarray] = None) -> dict:
    """Fit delay = a*x + b*y + c. For a plane wave (a, b) is the slowness vector in s/km and the residuals
    are what each sensor's clock is doing that the wavefront does not explain."""
    n = len(lags_s)
    A = np.column_stack([xy_km[:, 0], xy_km[:, 1], np.ones(n)])
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    w = np.clip(w, 1e-6, None)
    sol, *_ = np.linalg.lstsq(A * w[:, None], lags_s * w, rcond=None)
    resid = lags_s - A @ sol
    dof = max(n - 3, 1)
    rms = float(np.sqrt(float((resid ** 2).sum()) / dof))
    return {'slowness_s_km': (float(sol[0]), float(sol[1])), 'constant_s': float(sol[2]),
            'residual_s': [float(v) for v in resid], 'rms_residual_s': rms,
            'slowness_magnitude_s_km': float(math.hypot(sol[0], sol[1])),
            'apparent_velocity_km_s': (1.0 / math.hypot(sol[0], sol[1])) if math.hypot(sol[0], sol[1]) > 1e-9 else None}


def measured_lags(traces: Sequence[Trace], band: Tuple[float, float], max_lag_s: float, reference: int = 0,
                  centres: Optional[Sequence[float]] = None) -> dict:
    """Each sensor's delay against the reference, by cross-correlation of the band-passed records - the
    waveform, not its envelope, because a timing error of milliseconds is the whole point and an envelope
    cannot see it. The signed peak is kept: a sensor wired backwards correlates negatively.

    Machinery is narrow-band, so its correlation function is periodic and a free search picks whichever
    cycle it likes. `centres` is where each sensor's delay is expected - from the beam, which combines
    many frequencies and so has no such ambiguity - and the search runs only within `max_lag_s` of that.
    The consequence is a real limit, stated rather than hidden: a clock error larger than half the period
    of the dominant line cannot be told from a whole cycle of it."""
    fs, t0, t1 = AR._common_window(list(traces))
    xs = [bandpass(tr.slice(t0, t1).data.astype(np.float64), fs, band) for tr in traces]
    n = min(len(x) for x in xs)
    xs = [x[:n] for x in xs]
    nfft = 1 << int(math.ceil(math.log2(max(2 * n, 2))))
    F = [np.fft.rfft(x, nfft) for x in xs]
    ref = F[reference]
    nref = math.sqrt(float((xs[reference] ** 2).sum())) + 1e-30
    lags, peaks = [], []
    for k, x in enumerate(xs):
        cc = np.fft.irfft(F[k] * np.conj(ref), nfft)
        cc = np.concatenate([cc[-(nfft // 2):], cc[:nfft // 2]])
        grid = (np.arange(nfft) - nfft // 2) / fs
        cc = cc / (nref * (math.sqrt(float((x ** 2).sum())) + 1e-30))
        c0 = float(centres[k]) if centres is not None else 0.0
        m = np.abs(grid - c0) <= max_lag_s
        if not m.any():
            m = np.abs(grid) <= max_lag_s
        seg, gseg = cc[m], grid[m]
        j = int(np.argmax(np.abs(seg)))
        lag = float(gseg[j])
        if 0 < j < len(seg) - 1:                    # sub-sample by a parabola through the three values
            y0, y1, y2 = float(seg[j - 1]), float(seg[j]), float(seg[j + 1])
            den = y0 - 2 * y1 + y2
            if abs(den) > 1e-18:
                lag += (1.0 / fs) * float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
        lags.append(lag)
        peaks.append(float(seg[j]))
    return {'lags_s': lags, 'peaks': peaks, 'reference': reference, 'window': {'start': iso(t0, 0), 'end': iso(t1, 0)},
            'sample_interval_s': 1.0 / fs, 'band_hz': list(band), 'max_lag_s': max_lag_s,
            'centred_on_beam': centres is not None}


def array_qc(traces: Sequence[Trace], sensors: Sequence[Sensor], band: Tuple[float, float] = DEFAULT_BAND,
             gain_tol_db: float = 6.0, min_correlation: float = 0.3, timing_sigma: float = 4.0,
             flat_tol: float = 0.2, clip_tol: float = 0.01, max_lag_s: Optional[float] = None) -> dict:
    """Every sensor judged against the array it is part of, and the array judged by what is left.

    The timing test is the plane fit: a sensor whose residual is `timing_sigma` times the scatter of the fit
    (and larger than a sample) has a clock the wavefront does not explain. The scatter is measured, not
    assumed, so a noisy array is held to a looser standard than a quiet one - which is right, because on a
    noisy array a small residual is not evidence of anything."""
    if len(traces) != len(sensors) or len(sensors) < MIN_SENSORS:
        raise ValueError(f'array QC needs {MIN_SENSORS} or more sensors, one record each, in the same order')
    geo = array_geometry(sensors)
    xy = np.asarray(geo['xy_km'], dtype=float)
    stats = sensor_stats(traces, sensors, band)
    rows = [SensorQC(sensor_id=s['sensor_id'], npts=s['npts'], gaps=s['gaps'], rms_counts=s['rms_counts'],
                     band_power_db=s['band_power_db'], flat_fraction=s['flat_fraction'], clipped_fraction=s['clipped_fraction'])
            for s in stats]
    # gain against the array's own median: a median, so one wrong sensor cannot set the standard
    powers = np.array([s['band_power_db'] for s in stats], dtype=float)
    med = float(np.median(powers))
    for r, p in zip(rows, powers):
        r.gain_db_vs_median = round(float(p - med), 2)
    # a dead channel takes no further part: it would drag the plane fit with it
    for r in rows:
        if (r.rms_counts or 0.0) <= 1e-9 or (r.flat_fraction or 0.0) >= flat_tol:
            r.verdict, r.flags = 'DEAD', ['flat']
            r.detail = (f'{r.flat_fraction:.0%} of this record is inside a run of identical samples'
                        if (r.flat_fraction or 0) >= flat_tol else 'this record has no variance at all')
    alive = [i for i, r in enumerate(rows) if r.verdict != 'DEAD']
    out = {'protocol': 'seismic_qc.array_qc', 'band_hz': list(band), 'n_sensors': len(sensors),
           'array': {'lat': geo['lat0'], 'lon': geo['lon0'], 'aperture_km': round(geo['aperture_km'], 4)},
           'median_band_power_db': round(med, 2), 'gain_tol_db': gain_tol_db, 'min_correlation': min_correlation,
           'timing_sigma': timing_sigma,
           'basis': 'per-sensor statistics against the array median, cross-correlation against a reference sensor, and a plane fitted to the '
                    'measured delays - the residual of that fit is each sensor\'s timing error and the fit itself is the reference',
           'not_a_measurement': ['a timing error smaller than the sample interval or the scatter of the fit',
                                 'a sensor\'s orientation: these are vertical-component records',
                                 'an absolute clock error: the plane fit has a free constant, so what is measured is each sensor against the array',
                                 'any verdict from a window in which nothing coherent crossed the array',
                                 'a clock error larger than half the period of the dominant line: it cannot be told from a whole cycle of it',
                                 'a verdict on a badly faulty array: the wavefront this test measures against is the array\'s own beam, so the worse '
                                 'the array the poorer the anchor - the plane is fitted again without the sensors that stand outside it for that reason',
                                 'the cause of a fault: a sensor that disagrees may be broken, on different ground, or nearest the source']}
    if len(alive) < MIN_SENSORS:
        for r in rows:
            if r.verdict == 'OK':
                r.verdict, r.detail = 'INSUFFICIENT', f'only {len(alive)} sensor(s) are alive; the array tests need {MIN_SENSORS}'
        out.update({'sensors': [r.to_dict() for r in rows], 'status': 'NOT_USABLE', 'usable': [], 'n_usable': 0,
                    'detail': f'{len(sensors) - len(alive)} of {len(sensors)} sensors are dead; what is left is not an array'})
        return out
    ref = alive[int(np.argmax([rows[i].band_power_db or -1e9 for i in alive]))]
    sub = [traces[i] for i in alive]
    sub_xy = xy[alive]
    # where the wavefront says each delay should be. The beam combines many frequencies, so its maximum is
    # not one cycle among many - which a correlation on narrow-band machinery lines certainly is.
    centres = None
    beam_note = None
    try:
        b0 = AR.beam(sub, [sensors[i] for i in alive], band, 10.0, 3.0, 61, 'bartlett')
        baz = math.radians(float(b0['back_azimuth_deg']))
        sl = float(b0['slowness_s_km'])
        u = np.array([-math.sin(baz), -math.cos(baz)])          # the direction the wave travels
        pred = sub_xy @ (sl * u)
        beam_note = {'back_azimuth_deg': b0['back_azimuth_deg'], 'slowness_s_km': sl, 'coherence_max_bin': b0['coherence_max_bin']}
    except ValueError:
        pred = None
    # how far from the wavefront's own prediction to look. A quarter of the delay the array itself spans:
    # wide enough for any clock error worth finding, narrow enough that a narrow-band correlation cannot
    # slip a whole cycle into it.
    if max_lag_s is not None:
        lag_cap = max_lag_s
    elif pred is not None:
        lag_cap = max(0.25 * float(np.ptp(pred)), 4.0 / float(traces[0].sample_rate))
    else:
        lag_cap = 0.5 / max(band[0], 0.1)
    if pred is not None:
        # the sign convention is settled by measurement, not by assumption: whichever way round fits better
        best = None
        for sign in (1.0, -1.0):
            c = sign * (pred - pred[alive.index(ref)])
            cand = measured_lags(sub, band, lag_cap, reference=alive.index(ref), centres=c)
            keep = [n_ for n_, i in enumerate(alive) if abs(float(cand['peaks'][n_])) >= min_correlation]
            if len(keep) < MIN_SENSORS:
                continue
            f = plane_fit(sub_xy[keep], np.array([cand['lags_s'][n_] for n_ in keep]))
            if best is None or f['rms_residual_s'] < best[0]:
                best = (f['rms_residual_s'], cand, sign)
        ml = best[1] if best else measured_lags(sub, band, lag_cap, reference=alive.index(ref))
        if best:
            beam_note = dict(beam_note or {}, delay_sign=best[2])
    else:
        ml = measured_lags(sub, band, lag_cap, reference=alive.index(ref))
    out_beam = beam_note
    for n_, i in enumerate(alive):
        rows[i].correlation = round(float(ml['peaks'][n_]), 4)
    # polarity: a sensor that correlates negatively is wired backwards. It is taken out of the plane fit,
    # because its measured lag is half a cycle out and would bend the plane.
    for n_, i in enumerate(alive):
        if (rows[i].correlation or 0.0) <= -min_correlation:
            rows[i].verdict, rows[i].flags = 'POLARITY', ['reversed']
            rows[i].detail = f'correlates {rows[i].correlation:+.2f} with the array: this record is upside down'
    fit_idx = [i for i in alive if rows[i].verdict == 'OK' and abs(rows[i].correlation or 0.0) >= min_correlation]
    if len(fit_idx) >= MIN_SENSORS:
        pos = {i: n_ for n_, i in enumerate(alive)}
        lags = np.array([ml['lags_s'][pos[i]] for i in fit_idx])
        w = np.array([abs(rows[i].correlation or 0.0) for i in fit_idx])
        fit = plane_fit(xy[fit_idx], lags, w)
        # the scatter of the fit is measured robustly - the median absolute deviation, not the rms - because
        # the one sensor whose clock is wrong would otherwise raise the bar until it cleared it. Then the
        # plane is fitted again without the sensors that stand outside it, so each residual is measured
        # against a plane those sensors did not bend.
        def _scatter(res):
            r = np.asarray(res, dtype=float)
            return float(np.median(np.abs(r - np.median(r))) * 1.4826)
        # the floor is a tenth of a sample, not a whole one: the peak is interpolated inside its bin, so the
        # resolution is well below the sample interval at this coherence
        floor_s = 0.1 * ml['sample_interval_s']
        sc = max(_scatter(fit['residual_s']), floor_s)
        keep2 = [n_ for n_ in range(len(fit_idx)) if abs(fit['residual_s'][n_]) < timing_sigma * sc]
        if MIN_SENSORS <= len(keep2) < len(fit_idx):
            fit2 = plane_fit(xy[[fit_idx[n_] for n_ in keep2]], lags[keep2], w[keep2])
            A = np.column_stack([xy[fit_idx][:, 0], xy[fit_idx][:, 1], np.ones(len(fit_idx))])
            sol = np.array([fit2['slowness_s_km'][0], fit2['slowness_s_km'][1], fit2['constant_s']])
            fit = dict(fit2)
            fit['residual_s'] = [float(v) for v in (lags - A @ sol)]
            fit['refit_excluded'] = [rows[fit_idx[n_]].sensor_id for n_ in range(len(fit_idx)) if n_ not in keep2]
            sc = max(_scatter([fit['residual_s'][n_] for n_ in keep2]), floor_s)
        slow = fit['slowness_magnitude_s_km']
        scatter = sc
        for n_, i in enumerate(fit_idx):
            r = rows[i]
            r.timing_residual_s = round(float(fit['residual_s'][n_]), 5)
            # the same error as an angle: a delay error dt on a baseline D at slowness s is dt/(s*D) radians
            d = max(geo['aperture_km'], 1e-6)
            r.timing_residual_deg = (round(math.degrees(abs(r.timing_residual_s) / (slow * d)), 2) if slow > 1e-9 else None)
        out['anchor_beam'] = out_beam
        out['lag_search_s'] = round(lag_cap, 4)
        out['plane_fit'] = {k: v for k, v in fit.items() if k != 'residual_s'}
        out['plane_fit']['scatter_basis'] = 'the median absolute deviation of the residuals, scaled to a standard deviation'
        out['plane_fit']['back_azimuth_deg'] = (round((math.degrees(math.atan2(-fit['slowness_s_km'][0], -fit['slowness_s_km'][1])) + 360.0) % 360.0, 1)
                                                if slow > 1e-9 else None)
        out['timing_scatter_s'] = round(scatter, 5)
        for i in fit_idx:
            r = rows[i]
            if r.verdict != 'OK':
                continue
            if abs(r.timing_residual_s or 0.0) >= timing_sigma * scatter and abs(r.timing_residual_s or 0.0) > ml['sample_interval_s']:
                r.verdict, r.flags = 'TIMING', ['clock']
                r.detail = (f"its arrival is {r.timing_residual_s * 1000:+.1f} ms from where the wavefront puts it ({abs(r.timing_residual_s) / scatter:.1f} "
                            f"times the fit's scatter of {scatter * 1000:.1f} ms) - about {r.timing_residual_deg:.1f} deg of azimuth at this aperture")
    else:
        out['plane_fit'] = None
        out['timing_scatter_s'] = None
        for i in alive:
            if rows[i].verdict == 'OK':
                rows[i].verdict = 'INSUFFICIENT'
                rows[i].detail = 'too few sensors correlate with each other for a plane to be fitted: nothing coherent crossed the array in this window'
    # what is left: gain and coherence
    for i in alive:
        r = rows[i]
        if r.verdict not in ('OK',):
            continue
        if (r.clipped_fraction or 0.0) >= clip_tol:
            r.verdict, r.flags = 'CLIPPED', ['rail']
            r.detail = f'{r.clipped_fraction:.1%} of this record sits on its own extreme: the converter is at its rail'
        elif abs(r.correlation or 0.0) < min_correlation:
            r.verdict, r.flags = 'INCOHERENT', ['uncorrelated']
            r.detail = (f'correlates {r.correlation:+.2f} with the array (floor {min_correlation}): nothing this sensor recorded crossed the array '
                        'with the rest')
        elif (r.gain_db_vs_median or 0.0) <= -gain_tol_db:
            r.verdict, r.flags = 'LOW_GAIN', ['gain']
            r.detail = f'{r.gain_db_vs_median:+.1f} dB against the array median: this sensor is barely in the beam'
        elif (r.gain_db_vs_median or 0.0) >= gain_tol_db:
            r.verdict, r.flags = 'HIGH_GAIN', ['gain']
            r.detail = f'{r.gain_db_vs_median:+.1f} dB against the array median: louder than the array, which is a scale, a mounting or a local source'
        else:
            r.detail = (f'{r.gain_db_vs_median:+.1f} dB of the median, correlates {r.correlation:+.2f}'
                        + (f', timing {r.timing_residual_s * 1000:+.1f} ms' if r.timing_residual_s is not None else ''))
    usable = [r.sensor_id for r in rows if r.verdict == 'OK']
    out['sensors'] = [r.to_dict() for r in rows]
    out['usable'] = usable
    out['n_usable'] = len(usable)
    out['faults'] = {v: [r.sensor_id for r in rows if r.verdict == v] for v in
                     ('DEAD', 'CLIPPED', 'POLARITY', 'TIMING', 'INCOHERENT', 'LOW_GAIN', 'HIGH_GAIN', 'INSUFFICIENT') if any(r.verdict == v for r in rows)}
    if len(usable) < MIN_SENSORS:
        out['status'] = 'NOT_USABLE'
        out['detail'] = f'{len(usable)} of {len(sensors)} sensors pass; an array needs {MIN_SENSORS}, so no bearing from this array means anything'
    elif len(usable) < len(sensors):
        out['status'] = 'DEGRADED'
        out['detail'] = (f'{len(usable)} of {len(sensors)} sensors pass. The others are named above with what is wrong; a beam that uses them is a beam '
                         'through a fault')
    else:
        out['status'] = 'USABLE'
        out['detail'] = f'all {len(sensors)} sensors agree on gain, polarity, coherence and timing'
    return out


def beam_cost(traces: Sequence[Trace], sensors: Sequence[Sensor], qc: dict, band: Tuple[float, float] = DEFAULT_BAND,
              seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 61, method: str = 'bartlett') -> dict:
    """What the faulty sensors were doing to the answer: the beam with every sensor, the beam with only the
    ones that pass, and the difference between them. This is the number that says whether the fault mattered."""
    keep = [i for i, s in enumerate(sensors) if s.sensor_id in set(qc.get('usable') or [])]
    out = {'protocol': 'seismic_qc.beam_cost', 'n_all': len(sensors), 'n_usable': len(keep),
           'basis': 'the same beam over the same window, once with every sensor and once with only those that pass QC',
           'not_a_measurement': ['the true bearing: this is the difference between two beams, not the error of either']}
    try:
        all_b = AR.beam(list(traces), list(sensors), band, seg_s, s_max, n_grid, method)
    except ValueError as e:
        out.update({'status': 'NO_BEAM', 'detail': str(e)})
        return out
    out['with_all'] = {'back_azimuth_deg': all_b['back_azimuth_deg'], 'coherence_max_bin': all_b['coherence_max_bin'],
                       'slowness_s_km': all_b['slowness_s_km']}
    if len(keep) == len(sensors):
        out.update({'status': 'NO_CHANGE', 'detail': 'every sensor passes, so there is nothing to exclude'})
        return out
    if len(keep) < MIN_SENSORS:
        out.update({'status': 'NOT_ENOUGH_LEFT', 'detail': f'{len(keep)} sensor(s) pass; a beam needs {MIN_SENSORS}'})
        return out
    good = AR.beam([traces[i] for i in keep], [sensors[i] for i in keep], band, seg_s, s_max, n_grid, method)
    d = abs(float(AR._angle_diff(all_b['back_azimuth_deg'], good['back_azimuth_deg'])))
    out['with_usable'] = {'back_azimuth_deg': good['back_azimuth_deg'], 'coherence_max_bin': good['coherence_max_bin'],
                          'slowness_s_km': good['slowness_s_km'],
                          'azimuth_half_width_deg': good['resolution']['azimuth_half_width_deg']}
    out['bearing_change_deg'] = round(d, 2)
    out['coherence_change'] = round(float(good['coherence_max_bin'] - all_b['coherence_max_bin']), 4)
    tol = float(good['resolution']['azimuth_half_width_deg'])
    out['status'] = 'CHANGED' if d > tol else 'WITHIN_RESOLUTION'
    out['detail'] = (f"dropping {len(sensors) - len(keep)} sensor(s) moves the bearing {d:.1f} deg"
                     + (f', which is more than this array can resolve ({tol:.1f} deg): the faulty sensors were steering the beam'
                        if d > tol else f', inside what this array can resolve ({tol:.1f} deg)')
                     + f", and changes the best-bin coherence by {out['coherence_change']:+.3f}")
    return out


# ---------------------------------------------------------------------------
# a labelled scene with known faults in it
# ---------------------------------------------------------------------------
def synthetic_qc_scene(seed: int = 11, fs: float = 50.0, hours: float = 3.0, n_sensors: int = 9, aperture_km: float = 1.2,
                       dead: Sequence[int] = (2,), clock_ms: Dict[int, float] = None, reversed_: Sequence[int] = (6,),
                       gain: Dict[int, float] = None, v_km_s: float = 2.5) -> Tuple[List[Trace], List[Sensor], dict]:
    """A clean array with known faults put into it: a dead sensor, a sensor whose clock is out by a stated
    number of milliseconds, a sensor wired backwards, and a sensor at a stated gain. The faults are the
    scene's labels, and the test is whether QC names exactly those and no others."""
    clock_ms = {4: 30.0} if clock_ms is None else clock_ms
    gain = {7: 0.08} if gain is None else gain
    traces, sensors, srcs, meta = AR.synthetic_array_scene(seed=seed, fs=fs, hours=hours, n_sensors=n_sensors,
                                                           aperture_km=aperture_km, v_km_s=v_km_s, distances_km=(6.0,))
    # the scene's rig works in its own hour; QC is a test of an array against a wavefront, so the window
    # given to it is one the wavefront is in. A window with nothing coherent in it is not a failing array.
    src = srcs[0]
    traces = [tr.slice(src.start, src.end) for tr in traces]
    rng = np.random.default_rng(seed + 1)
    realised: Dict[int, float] = {}
    out = []
    for k, tr in enumerate(traces):
        x = np.asarray(tr.data, dtype=np.float64).copy()
        if k in dead:
            x = np.full(len(x), float(np.round(x[0])))           # stuck at one value
        if k in clock_ms:
            shift = int(round(clock_ms[k] * 1e-3 * fs))
            realised[k] = 1000.0 * shift / fs       # the sample grid quantises it; the label is what was actually done
            x = np.roll(x, shift)                                 # the record arrives late by that many samples
        if k in reversed_:
            x = -x
        if k in gain:
            x = x * gain[k] + rng.normal(0, 0.5, len(x))
        out.append(Trace(tr.network, tr.station, tr.location, tr.channel, tr.starttime, tr.sample_rate,
                         np.round(x).astype(np.int32), source='synthetic_qc_scene', encoding='SYNTHETIC'))
    meta = {'status': 'SIMULATION_SELF_TEST', 'v_km_s': v_km_s, 'aperture_km': round(array_geometry(sensors)['aperture_km'], 4),
            'faults': {'dead': [sensors[i].sensor_id for i in dead],
                       'timing': {sensors[i].sensor_id: realised.get(i, clock_ms[i]) for i in clock_ms},
                       'timing_asked_ms': {sensors[i].sensor_id: clock_ms[i] for i in clock_ms},
                       'polarity': [sensors[i].sensor_id for i in reversed_],
                       'gain': {sensors[i].sensor_id: gain[i] for i in gain}},
            'sample_interval_ms': round(1000.0 / fs, 3), 'window': {'start': iso(src.start, 0), 'end': iso(src.end, 0)},
            'source_distance_km': 6.0,
            'note': 'the faults are the scene\'s own labels; the scene says nothing about any real array'}
    return out, sensors, meta


def selftest(seed: int = 11) -> dict:
    """SIMULATION_SELF_TEST: a dead sensor, a 30 ms clock error, a reversed sensor and one at 8 % gain, all
    put into a clean array - and QC must name exactly those four, recover the clock error to within a few
    milliseconds, and say what excluding them does to the bearing. Then a clean array must come back USABLE
    with nothing named at all, because a test that finds a fault in a good array is worse than no test."""
    traces, sensors, meta = synthetic_qc_scene(seed)
    qc = array_qc(traces, sensors, (1.0, 20.0))
    found = qc.get('faults') or {}
    by_id = {r['sensor_id']: r for r in qc['sensors']}
    want = meta['faults']
    tid = list(want['timing'])[0]
    got_ms = (by_id[tid]['timing_residual_s'] or 0.0) * 1000.0
    cost = beam_cost(traces, sensors, qc, (1.0, 20.0))
    anchor = qc.get('anchor_beam') or {}
    clean_tr, clean_sn, _ = synthetic_qc_scene(seed, dead=(), clock_ms={}, reversed_=(), gain={})
    clean = array_qc(clean_tr, clean_sn, (1.0, 20.0))
    ok = bool(qc['status'] == 'DEGRADED'
              and found.get('DEAD') == want['dead']
              and found.get('POLARITY') == want['polarity']
              and found.get('TIMING') == [tid]
              and found.get('LOW_GAIN') == list(want['gain'])
              and abs(abs(got_ms) - want['timing'][tid]) <= 5.0
              and qc['n_usable'] == len(sensors) - 4
              and cost['status'] == 'CHANGED' and cost['bearing_change_deg'] > 10.0 and cost['coherence_change'] > 0.0
              and clean['status'] == 'USABLE' and clean['n_usable'] == len(clean_sn) and not clean.get('faults'))
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'scene': meta, 'qc': qc, 'beam_cost': cost, 'anchor_beam': anchor,
            'timing_recovered_ms': round(got_ms, 2), 'timing_injected_ms': want['timing'][tid],
            'clean_array': {'status': clean['status'], 'n_usable': clean['n_usable'], 'faults': clean.get('faults')}}


def report_text(res: dict) -> str:
    p = res.get('protocol', '')
    lines: List[str] = []
    if p.endswith('array_qc'):
        lines.append(f"array QC [{res['status']}]: {res['n_usable']} of {res['n_sensors']} sensor(s) pass, band "
                     f"{res['band_hz'][0]:g}-{res['band_hz'][1]:g} Hz, aperture {res['array']['aperture_km']:g} km")
        pf = res.get('plane_fit')
        if pf:
            lines.append(f"  the plane fitted to the measured delays: {pf['apparent_velocity_km_s']:.2f} km/s from "
                         f"{pf['back_azimuth_deg']:.0f} deg, scatter {res['timing_scatter_s'] * 1000:.1f} ms")
        for r in res['sensors']:
            lines.append(f"  {r['sensor_id']:<6} {r['verdict']:<12} {r['gain_db_vs_median']:+6.1f} dB  "
                         + (f"corr {r['correlation']:+.2f}  " if r['correlation'] is not None else '            ')
                         + (f"{r['timing_residual_s'] * 1000:+7.1f} ms" if r['timing_residual_s'] is not None else '           ')
                         + ('  ' + r['detail'] if r['verdict'] != 'OK' else ''))
        lines.append('  ' + res.get('detail', ''))
    elif p.endswith('beam_cost'):
        lines.append(f"the cost of the faults [{res['status']}]: " + res.get('detail', ''))
    for nm in res.get('not_a_measurement', []):
        lines.append('  not a measurement: ' + nm)
    return '\n'.join(lines)
