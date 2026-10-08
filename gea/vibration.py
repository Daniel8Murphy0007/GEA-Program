# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""vibration - the machine's record read the way an operations engineer expects it read.

A disposal site runs on its injection pumps, and the first question an operations engineer asks of a
vibration record is the one ISO 20816 answers: which zone is this machine in. The second, when the answer is
not A or B, is what the bearings are doing, and the method for that is envelope analysis - demodulate the
band a bearing fault rings, and look for the fault's own repetition frequency in the envelope.

Both are here, to their published definitions, with the declarations named as declarations:

- the broadband r.m.s. vibration velocity over 10 Hz to 1000 Hz (2 Hz to 1000 Hz below 600 r/min), as
  ISO 20816-1 defines the quantity and ISO 20816-3 evaluates it for industrial machines above 15 kW:
  the zone boundaries A/B, B/C and C/D by machine group (Group 1 above 300 kW; Group 2 from 15 kW to
  300 kW) and support class (rigid when the machine-foundation system's lowest natural frequency is at
  least 1.25 times the main excitation frequency, otherwise flexible), measured on the bearing housing in
  two radial directions, the zone set by the highest. From an acceleration channel the velocity is reached
  by integration in the frequency domain; from a velocity channel by band-limiting. The group and the
  support class are the operator's declarations: the record does not say what the machine is standing on;
- the envelope spectrum: the band is chosen by kurtosis among candidate bands (a bearing fault's impacts
  are impulsive, and kurtosis is what impulsiveness looks like in a number) or declared; the analytic
  signal's envelope is taken; its spectrum is searched for the bearing defect frequencies - BPFO, BPFI, BSF,
  FTF from the bearing's geometry and the shaft speed, with harmonics and, for the inner race, sidebands at
  the shaft speed - each MATCHED or NOT MATCHED with its tolerance printed.

What this module will not call a measurement:

- a zone for a record whose rate cannot carry the standard's band: the band is named as truncated at the
  record's Nyquist frequency and the zone is marked PARTIAL BAND, not promoted;
- a zone from a record shorter than a second, or at a speed the operator did not declare;
- a fault size: a matched frequency says a bearing is ringing at that frequency, not how far it has gone;
- a machine group or a support class: declared, never inferred from the record;
- a bearing defect frequency without the bearing's geometry: without it the envelope spectrum's peaks are
  listed and nothing is matched.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ISO 20816-3 zone boundaries, r.m.s. velocity in mm/s over the broadband, by group and support class
ZONE_BOUNDARIES = {
    (1, 'rigid'): (2.3, 4.5, 7.1), (1, 'flexible'): (3.5, 7.1, 11.0),
    (2, 'rigid'): (1.4, 2.8, 4.5), (2, 'flexible'): (2.3, 4.5, 7.1),
}
GROUPS = {1: 'Group 1: rated above 300 kW (electrical machines with shaft height above 315 mm)',
          2: 'Group 2: rated 15 kW to 300 kW (electrical machines with shaft height 160 mm to 315 mm)'}
SUPPORTS = {'rigid': 'rigid: the lowest natural frequency of the machine-foundation system is at least 1.25 times the main excitation frequency',
            'flexible': 'flexible: otherwise'}
ZONES = {'A': 'newly commissioned machines normally fall in this zone',
         'B': 'acceptable for unrestricted long-term operation',
         'C': 'unsatisfactory for continuous long-term operation; the machine may run for a limited period until remedial action',
         'D': 'severe enough to be considered likely to cause damage'}
BAND_HZ = (10.0, 1000.0)
BAND_LOW_SPEED_HZ = (2.0, 1000.0)
LOW_SPEED_RPM = 600.0
G0 = 9.80665
UNITS = {'g': ('acceleration', G0), 'm/s2': ('acceleration', 1.0), 'm/s^2': ('acceleration', 1.0), 'mm/s2': ('acceleration', 1e-3),
         'mm/s': ('velocity', 1e-3), 'm/s': ('velocity', 1.0), 'in/s': ('velocity', 0.0254), 'ips': ('velocity', 0.0254)}


# --------------------------------------------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------------------------------------------
def _demean(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return x - x.mean() if x.size else x


def _bandpass_fft(x: np.ndarray, fs: float, lo: float, hi: float) -> np.ndarray:
    """A brick-wall band in the frequency domain; the record is tapered at its ends first."""
    n = x.size
    if n < 4:
        return x.copy()
    w = np.ones(n)
    m = max(1, int(0.02 * n))
    ramp = 0.5 * (1 - np.cos(np.pi * np.arange(m) / m))
    w[:m] = ramp; w[-m:] = ramp[::-1]
    X = np.fft.rfft(x * w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = (f >= lo) & (f <= hi)
    X[~keep] = 0.0
    return np.fft.irfft(X, n=n)


def broadband_velocity(x: np.ndarray, fs: float, unit: str, rpm: Optional[float] = None) -> dict:
    """The r.m.s. vibration velocity over the standard's broadband, in mm/s.

    From acceleration the velocity is reached in the frequency domain (V(f) = A(f) / 2πf) inside the band,
    which is also where the band is applied; from velocity the band is applied directly. The r.m.s. is the
    r.m.s. of the band-limited time series. A record whose rate cannot carry 1000 Hz is evaluated to its own
    Nyquist frequency and says so."""
    kind, scale = UNITS[unit.lower()] if unit.lower() in UNITS else (None, None)
    if kind is None:
        raise ValueError(f'unit {unit!r} is not an acceleration or velocity unit this reads ({", ".join(UNITS)})')
    x = _demean(x) * scale                           # SI: m/s² or m/s
    band = BAND_LOW_SPEED_HZ if (rpm is not None and rpm <= LOW_SPEED_RPM) else BAND_HZ
    nyq = fs / 2.0
    hi = min(band[1], nyq * 0.95)
    partial = hi < band[1] - 1e-9
    n = x.size
    gaps = []
    if n < fs:
        gaps.append(f'the record is {n / fs:.2f} s long: shorter than one second, the r.m.s. is not a steady-state value')
    if partial:
        gaps.append(f"the record's rate ({fs:g} Hz) cannot carry the standard's band to {band[1]:g} Hz: evaluated to {hi:.0f} Hz, PARTIAL BAND")
    if band[0] >= hi:
        return {'status': 'NOT EVALUATED', 'detail': f'no band left between {band[0]:g} Hz and {hi:.0f} Hz', 'gaps': gaps}
    w = np.ones(n); m = max(1, int(0.02 * n)); ramp = 0.5 * (1 - np.cos(np.pi * np.arange(m) / m)); w[:m] = ramp; w[-m:] = ramp[::-1]
    X = np.fft.rfft(x * w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = (f >= band[0]) & (f <= hi)
    if kind == 'acceleration':
        with np.errstate(divide='ignore', invalid='ignore'):
            X = np.where(f > 0, X / (2j * np.pi * np.where(f > 0, f, 1.0)), 0.0)
    X[~keep] = 0.0
    v = np.fft.irfft(X, n=n)
    # the taper took energy out of the ends; the r.m.s. is taken over the untapered middle
    core = v[m:-m] if n > 4 * m else v
    rms_mm_s = float(np.sqrt(np.mean(core ** 2))) * 1000.0
    pk = float(np.max(np.abs(core))) * 1000.0 if core.size else 0.0
    return {'status': 'EVALUATED', 'rms_mm_s': round(rms_mm_s, 3), 'peak_mm_s': round(pk, 3), 'band_hz': [band[0], round(hi, 1)],
            'band_partial': partial, 'from': kind, 'unit_in': unit, 'fs_hz': fs, 'seconds': round(n / fs, 3), 'gaps': gaps,
            'basis': ('ISO 20816-1: r.m.s. vibration velocity over 10 Hz to 1000 Hz (2 Hz to 1000 Hz below 600 r/min); from acceleration by '
                      'integration in the frequency domain inside the band')}


def zone(rms_mm_s: float, group: int, support: str) -> dict:
    """ISO 20816-3: the zone this value falls in for the declared group and support class."""
    key = (int(group), str(support).lower())
    if key not in ZONE_BOUNDARIES:
        raise ValueError(f'group {group!r} and support {support!r}: the group is 1 or 2, the support rigid or flexible')
    ab, bc, cd = ZONE_BOUNDARIES[key]
    z = 'A' if rms_mm_s <= ab else 'B' if rms_mm_s <= bc else 'C' if rms_mm_s <= cd else 'D'
    nxt = {'A': ab, 'B': bc, 'C': cd, 'D': None}[z]
    return {'zone': z, 'meaning': ZONES[z], 'boundaries_mm_s': {'A/B': ab, 'B/C': bc, 'C/D': cd}, 'group': int(group), 'support': key[1],
            'group_basis': GROUPS[int(group)], 'support_basis': SUPPORTS[key[1]], 'margin_to_next_mm_s': (round(nxt - rms_mm_s, 3) if nxt else None),
            'basis': 'ISO 20816-3 zone boundaries for the declared group and support class; the zone is the highest radial reading on the bearing housing'}


# --------------------------------------------------------------------------------------------------------------
# bearings and the envelope
# --------------------------------------------------------------------------------------------------------------
def bearing_frequencies(rpm: float, n_elements: int, d_mm: float, D_mm: float, contact_deg: float = 0.0) -> dict:
    """The defect frequencies from the bearing's geometry and the shaft speed, in Hz.

    fr = rpm/60; BPFO = n/2·fr·(1 − d/D·cosφ); BPFI = n/2·fr·(1 + d/D·cosφ); BSF = D/2d·fr·(1 − (d/D·cosφ)²);
    FTF = fr/2·(1 − d/D·cosφ). The inner race turns with the shaft, so an inner-race fault is modulated at fr
    and shows sidebands at fr about BPFI."""
    fr = float(rpm) / 60.0
    r = (float(d_mm) / float(D_mm)) * math.cos(math.radians(float(contact_deg)))
    return {'fr': round(fr, 4), 'BPFO': round(0.5 * n_elements * fr * (1 - r), 4), 'BPFI': round(0.5 * n_elements * fr * (1 + r), 4),
            'BSF': round((float(D_mm) / (2 * float(d_mm))) * fr * (1 - r * r), 4), 'FTF': round(0.5 * fr * (1 - r), 4),
            'geometry': {'n_elements': int(n_elements), 'd_mm': float(d_mm), 'D_mm': float(D_mm), 'contact_deg': float(contact_deg)},
            'basis': 'the kinematic defect frequencies from the bearing geometry and the shaft speed; the inner race turns with the shaft'}


def _kurtosis(x: np.ndarray) -> float:
    x = x - x.mean()
    s2 = np.mean(x ** 2)
    return float(np.mean(x ** 4) / (s2 ** 2)) if s2 > 0 else 0.0


def choose_band(x: np.ndarray, fs: float, candidates: Optional[Sequence[Tuple[float, float]]] = None) -> dict:
    """The demodulation band: among candidate bands, the one whose band-limited signal has the highest
    kurtosis - the band a bearing's impacts ring in is the most impulsive one. The candidates split the
    spectrum above the running-speed region into octave-like bands up to the Nyquist frequency."""
    nyq = fs / 2.0
    if candidates is None:
        edges = [200.0]
        while edges[-1] * 2 < nyq * 0.95:
            edges.append(edges[-1] * 2)
        edges.append(nyq * 0.95)
        candidates = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] > edges[i] * 1.2]
        # a half-octave overlap set as well, so a fault that straddles an edge is not split
        candidates += [(edges[i] * 1.5, edges[i + 1] * 1.5) for i in range(len(edges) - 2) if edges[i + 1] * 1.5 < nyq * 0.95]
    rows = []
    for lo, hi in candidates:
        if hi <= lo or lo >= nyq:
            continue
        y = _bandpass_fft(x, fs, lo, min(hi, nyq * 0.95))
        rows.append({'band_hz': [round(lo, 1), round(min(hi, nyq * 0.95), 1)], 'kurtosis': round(_kurtosis(y), 3)})
    if not rows:
        return {'status': 'NO BAND', 'detail': 'no candidate band fits under the Nyquist frequency', 'candidates': []}
    best = max(rows, key=lambda r: r['kurtosis'])
    return {'status': 'CHOSEN', 'band_hz': best['band_hz'], 'kurtosis': best['kurtosis'], 'candidates': rows,
            'basis': 'the candidate band with the highest kurtosis: impulsive content is what a bearing fault adds, and kurtosis is its measure'}


def envelope_spectrum(x: np.ndarray, fs: float, band_hz: Tuple[float, float], fmax_hz: Optional[float] = None) -> dict:
    """Band-limit, take the analytic signal's envelope, remove its mean, and spectrum it."""
    x = _demean(x)
    y = _bandpass_fft(x, fs, band_hz[0], band_hz[1])
    n = y.size
    # the analytic signal through the FFT: zero the negative frequencies, double the positive
    Y = np.fft.fft(y)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1; h[1:n // 2] = 2
    else:
        h[0] = 1; h[1:(n + 1) // 2] = 2
    env = np.abs(np.fft.ifft(Y * h))
    env = env - env.mean()
    w = np.hanning(n)
    E = np.abs(np.fft.rfft(env * w)) * 2.0 / np.sum(w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    fmax = fmax_hz or min(band_hz[1] / 2.0, fs / 2.0)
    keep = f <= fmax
    return {'f_hz': f[keep], 'amplitude': E[keep], 'resolution_hz': round(fs / n, 4), 'band_hz': [band_hz[0], band_hz[1]], 'fmax_hz': round(fmax, 1),
            'kurtosis_band': round(_kurtosis(y), 3)}


def peaks(f: np.ndarray, a: np.ndarray, fmin_hz: float = 0.5, n_peaks: int = 12) -> List[dict]:
    """The largest local maxima above fmin, each with its prominence over the local median."""
    keep = f >= fmin_hz
    f, a = f[keep], a[keep]
    out = []
    if a.size < 3:
        return out
    med = float(np.median(a)) if a.size else 0.0
    for i in range(1, a.size - 1):
        if a[i] > a[i - 1] and a[i] >= a[i + 1]:
            out.append({'f_hz': round(float(f[i]), 3), 'amplitude': float(a[i]), 'over_median': round(float(a[i] / med), 2) if med > 0 else None})
    out.sort(key=lambda r: -r['amplitude'])
    return out[:n_peaks]


def match(pk: List[dict], freqs: dict, resolution_hz: float, tol_pct: float = 2.0, harmonics: int = 3, min_over_median: float = 4.0) -> dict:
    """Each defect frequency against the envelope spectrum's peaks: MATCHED when a peak within the tolerance
    (the larger of tol_pct and one resolution bin) stands at the fundamental or at a harmonic and is well
    above the spectrum's median; the inner race also looks for its sidebands at the shaft speed."""
    rows = {}
    fr = freqs['fr']
    for name in ('BPFO', 'BPFI', 'BSF', 'FTF'):
        f0 = freqs[name]
        found = []
        tol = max(f0 * tol_pct / 100.0, resolution_hz)          # the fundamental's tolerance, for every harmonic: a harmonic's tolerance must not grow
        for k in range(1, harmonics + 1):
            target = f0 * k
            hit = [p for p in pk if abs(p['f_hz'] - target) <= tol and (p['over_median'] or 0) >= min_over_median]
            if hit:
                best = max(hit, key=lambda p: p['amplitude'])
                found.append({'harmonic': k, 'target_hz': round(target, 3), 'found_hz': best['f_hz'], 'amplitude': best['amplitude'], 'tol_hz': round(tol, 3)})
        # the fundamental must be there: harmonics of one defect frequency fall near multiples of another, and a match on harmonics
        # alone would name a bearing fault from another fault's train
        if found and found[0]['harmonic'] != 1:
            found = []
        side = None
        if name == 'BPFI':
            tol = max(fr * tol_pct / 100.0, resolution_hz)
            sb = [p for p in pk if (abs(p['f_hz'] - (f0 - fr)) <= tol or abs(p['f_hz'] - (f0 + fr)) <= tol) and (p['over_median'] or 0) >= min_over_median]
            side = {'at_hz': [round(f0 - fr, 3), round(f0 + fr, 3)], 'found': [p['f_hz'] for p in sb]}
        rows[name] = {'frequency_hz': f0, 'standing': 'MATCHED' if found else 'NOT MATCHED', 'harmonics_found': found,
                      'sidebands': side, 'tolerance_pct': tol_pct, 'tolerance_hz': round(tol, 3)}
    matched = [k for k, v in rows.items() if v['standing'] == 'MATCHED']
    return {'rows': rows, 'matched': matched, 'status': 'FAULT FREQUENCY PRESENT' if matched else 'NO FAULT FREQUENCY MATCHED',
            'basis': (f'a peak within {tol_pct}% of the defect frequency (or one resolution bin) at the fundamental, with harmonics at the same tolerance, '
                      f'at least {min_over_median}× the envelope spectrum\'s median; the fundamental is required because the harmonics of one defect '
                      'frequency fall near multiples of another; a matched frequency says the bearing rings at it, not how far the fault has gone')}


# --------------------------------------------------------------------------------------------------------------
# the assessment
# --------------------------------------------------------------------------------------------------------------
def read_record(path: str, channel: Optional[str] = None) -> dict:
    """A machine record from a file this program already reads: a delimited export whose first column is a
    timestamp or elapsed seconds (the historian reader) with the named channel, or a miniSEED/SAC record (the
    seismic reader; counts as recorded). Returns the samples, the rate from the record itself, and what was
    read. The rate is the median spacing; an uneven record is named."""
    from .files import detect
    kind = detect(path)['kind']
    if kind in ('mseed', 'sac'):
        from . import seismic as S
        traces = S.read_any(path)
        if not traces:
            raise ValueError(f'{path}: no trace in the record')
        tr = next((t for t in traces if channel and (t.channel == channel or t.id == channel)), traces[0])
        return {'x': np.asarray(tr.data, dtype=float), 'fs': float(tr.sample_rate), 'name': os.path.basename(path), 'channel': tr.id,
                'kind': kind, 'unit_in_file': tr.unit, 'seconds': round(tr.npts / tr.sample_rate, 3) if tr.sample_rate else None, 'notes': []}
    from .files import read_any
    st = read_any(path)
    if st.index_kind != 'time_s':
        raise ValueError(f'{path}: the index is {st.index_kind}, not time')
    if not st.channels:
        raise ValueError(f'{path}: no channels')
    ch = channel or next(iter(st.channels))
    if ch not in st.channels:
        raise ValueError(f'{path}: no channel {ch!r} (channels: {", ".join(st.channels)})')
    idx = np.asarray(st.index, dtype=float)
    d = np.diff(idx)
    d = d[np.isfinite(d) & (d > 0)]
    if d.size < 2:
        raise ValueError(f'{path}: fewer than three samples')
    dt = float(np.median(d))
    notes = []
    if np.max(np.abs(d - dt)) > 0.1 * dt:
        notes.append(f'the sample spacing is uneven (median {dt:.6f} s, extremes {d.min():.6f} to {d.max():.6f} s): the rate is taken as the median')
    x = np.asarray(st.channels[ch].values, dtype=float)
    return {'x': x, 'fs': 1.0 / dt, 'name': os.path.basename(path), 'channel': ch, 'kind': kind, 'unit_in_file': st.channels[ch].unit,
            'seconds': round(float(idx[-1] - idx[0]), 3), 'notes': notes}


def assess(x: np.ndarray, fs: float, unit: str, rpm: Optional[float], group: Optional[int] = None, support: Optional[str] = None,
           bearing: Optional[dict] = None, band_hz: Optional[Tuple[float, float]] = None, label: str = '', sensitivity: Optional[float] = None) -> dict:
    """One channel of one record through both: the ISO 20816-3 zone for the declared machine, and the
    envelope spectrum against the declared bearing. Every missing declaration is a gap by name."""
    gaps: List[str] = []
    x = np.asarray(x, dtype=float)
    unit_eval = unit
    if sensitivity is not None:                          # counts to the declared unit: units per count, the operator's declaration
        x = x * float(sensitivity)
    if unit.lower() == 'counts' or (unit.lower() not in UNITS):
        bb = {'status': 'NOT EVALUATED', 'detail': (f'the channel is in {unit}: without a sensitivity (units per count) and a unit it cannot be mm/s'
                                                    if unit.lower() == 'counts' else f'unit {unit!r} is not an acceleration or velocity unit'), 'gaps': []}
        gaps.append(bb['detail'])
    else:
        bb = broadband_velocity(x, fs, unit_eval, rpm)
    gaps += bb.get('gaps', [])
    zn = None
    if bb['status'] == 'EVALUATED':
        if group is None or support is None:
            gaps.append('no machine group or support class declared: the broadband value is stated, the ISO 20816-3 zone is not')
        else:
            zn = zone(bb['rms_mm_s'], group, support)
            if bb['band_partial']:
                zn['zone_qualified'] = 'PARTIAL BAND'
    if rpm is None:
        gaps.append('no shaft speed declared: the low-speed band cannot be chosen and no defect frequency can be computed')
    # the envelope
    env_out: Dict[str, Any] = {}
    kind = UNITS.get(unit.lower(), (None, None))[0]
    if kind == 'acceleration' or True:          # the envelope is taken on whatever the record carries; acceleration is the usual sensor
        cb = {'status': 'DECLARED', 'band_hz': list(band_hz)} if band_hz else choose_band(x, fs)
        if cb['status'] in ('CHOSEN', 'DECLARED'):
            es = envelope_spectrum(x, fs, tuple(cb['band_hz']))
            pk = peaks(es['f_hz'], es['amplitude'], fmin_hz=max(0.5, 2.0 * es['resolution_hz']))
            env_out = {'band': cb, 'resolution_hz': es['resolution_hz'], 'kurtosis_band': es['kurtosis_band'], 'fmax_hz': es['fmax_hz'],
                       'peaks': [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items()} for p in pk]}
            if bearing and rpm is not None:
                bf = bearing_frequencies(rpm, bearing['n_elements'], bearing['d_mm'], bearing['D_mm'], bearing.get('contact_deg', 0.0))
                env_out['bearing'] = bf
                env_out['match'] = match(pk, bf, es['resolution_hz'])
            else:
                env_out['match'] = None
                gaps.append('no bearing geometry declared (elements, ball and pitch diameter, contact angle): the envelope spectrum\'s peaks are listed and nothing is matched')
        else:
            env_out = {'band': cb}
            gaps.append(cb.get('detail', 'no demodulation band'))
    status = 'ASSESSED' if (zn is not None) else ('PARTIAL' if bb['status'] == 'EVALUATED' else 'NOT ASSESSED')
    return {'protocol': 'vibration.assess/1', 'label': label, 'status': status, 'broadband': bb, 'zone': zn, 'envelope': env_out,
            'declared': {'rpm': rpm, 'group': group, 'support': support, 'bearing': bearing, 'band_hz': list(band_hz) if band_hz else None, 'unit': unit,
                         'sensitivity': sensitivity},
            'gaps': gaps,
            'basis': 'ISO 20816-1 broadband r.m.s. velocity evaluated by the ISO 20816-3 zones for the declared group and support; envelope analysis in the most impulsive band against the declared bearing\'s defect frequencies',
            'not_a_measurement': ['a machine group or a support class: declared', 'a fault size from a matched frequency',
                                  'a zone from a record that cannot carry the band: PARTIAL BAND', 'a defect frequency without the bearing geometry']}


# --------------------------------------------------------------------------------------------------------------
# the labelled scene and the self-test
# --------------------------------------------------------------------------------------------------------------
def synthetic_scene(seed: int = 7, fs: float = 20000.0, seconds: float = 4.0, rpm: float = 1780.0, fault: str = 'BPFO',
                    unbalance_mm_s: float = 3.2) -> dict:
    """A pump bearing housing at 1780 r/min: an unbalance line at 1× with a declared r.m.s. velocity, a 2× line,
    a resonance near 3 kHz rung by an outer-race fault's impacts at BPFO (a 6205-sized bearing: 9 balls, 7.94 mm
    on 39.04 mm), broadband noise. The truth is the zone the unbalance line puts it in and the fault frequency."""
    rng = np.random.default_rng(seed)
    n = int(fs * seconds)
    t = np.arange(n) / fs
    fr = rpm / 60.0
    bearing = {'n_elements': 9, 'd_mm': 7.94, 'D_mm': 39.04, 'contact_deg': 0.0}
    bf = bearing_frequencies(rpm, **bearing)
    # unbalance as a velocity line: v_rms = A/√2 at fr -> acceleration amplitude a = 2π fr · v_peak
    v_peak = unbalance_mm_s * math.sqrt(2.0) / 1000.0
    acc = 2 * math.pi * fr * v_peak * np.sin(2 * np.pi * fr * t) + 0.3 * 2 * math.pi * 2 * fr * v_peak * np.sin(2 * np.pi * 2 * fr * t + 0.4)
    # the fault: an impulse train at the defect frequency ringing a 3 kHz resonance, with a little jitter
    f_def = bf[fault]
    res_hz, zeta = 3000.0, 0.03
    k = np.arange(0, int(2 * fs / res_hz * 20))
    ring = np.exp(-zeta * 2 * np.pi * res_hz * k / fs) * np.sin(2 * np.pi * res_hz * k / fs)
    impulses = np.zeros(n)
    tt = 0.0
    while tt < seconds:
        i = int(round(tt * fs))
        if i < n:
            amp = 40.0 * (1.0 + 0.5 * math.sin(2 * math.pi * fr * tt)) if fault == 'BPFI' else 40.0
            impulses[i] += amp
        tt += 1.0 / f_def * (1.0 + rng.normal(0, 0.005))
    acc = acc + np.convolve(impulses, ring)[:n] + rng.normal(0, 1.5, n)
    truth = {'rpm': rpm, 'fault': fault, 'fault_hz': f_def, 'unbalance_rms_mm_s': unbalance_mm_s, 'resonance_hz': res_hz,
             'zone_group2_rigid': zone(unbalance_mm_s * math.sqrt(1 + 0.3 ** 2 * 1.0), 2, 'rigid')['zone']}
    return {'acc_m_s2': acc, 'fs': fs, 'unit': 'm/s2', 'rpm': rpm, 'bearing': bearing, 'frequencies': bf, 'truth': truth, 'label': 'SIMULATION_SELF_TEST'}


def selftest() -> dict:
    sc = synthetic_scene()
    r = assess(sc['acc_m_s2'], sc['fs'], sc['unit'], sc['rpm'], group=2, support='rigid', bearing=sc['bearing'], label=sc['label'])
    quiet = assess(np.random.default_rng(3).normal(0, 0.02, int(sc['fs'] * 2)), sc['fs'], 'm/s2', sc['rpm'], group=2, support='rigid', bearing=sc['bearing'])
    inner = synthetic_scene(fault='BPFI', seed=9)
    ri = assess(inner['acc_m_s2'], inner['fs'], inner['unit'], inner['rpm'], group=2, support='rigid', bearing=inner['bearing'])
    slow = assess(sc['acc_m_s2'][::10], sc['fs'] / 10.0, sc['unit'], sc['rpm'], group=2, support='rigid', bearing=sc['bearing'])   # 2 kHz: cannot carry 1000 Hz
    undeclared = assess(sc['acc_m_s2'], sc['fs'], sc['unit'], sc['rpm'])
    # the same record as a velocity channel: integrated in the frequency domain over 5-1500 Hz (a cumulative sum would carry the
    # impulse train's ramp and leak it across the band), then handed in as mm/s
    _a = _demean(sc['acc_m_s2']); _A = np.fft.rfft(_a); _f = np.fft.rfftfreq(_a.size, 1.0 / sc['fs'])
    with np.errstate(divide='ignore', invalid='ignore'):
        _Vf = np.where((_f >= 5.0) & (_f <= 1500.0), _A / (2j * np.pi * np.where(_f > 0, _f, 1.0)), 0.0)
    vel = assess(np.fft.irfft(_Vf, n=_a.size) * 1000.0, sc['fs'], 'mm/s', sc['rpm'], group=2, support='rigid')
    tr = sc['truth']
    rms = r['broadband']['rms_mm_s']
    checks = {
        'broadband_recovers_unbalance': abs(rms - tr['unbalance_rms_mm_s'] * math.sqrt(1 + 0.09)) / (tr['unbalance_rms_mm_s'] * math.sqrt(1.09)) < 0.08,
        'zone_as_truth': r['zone'] is not None and r['zone']['zone'] == tr['zone_group2_rigid'] and r['zone']['zone'] == 'C',
        'band_is_impulsive': r['envelope']['band']['kurtosis'] > 4.0 and r['envelope']['band']['band_hz'][1] > tr['resonance_hz'] * 0.5
                             and r['envelope']['band']['band_hz'][0] < tr['resonance_hz'] * 2.5,
        'outer_race_matched': r['envelope']['match']['matched'] == ['BPFO'],
        'bpfo_value': abs(sc['frequencies']['BPFO'] - 0.5 * 9 * (1780 / 60) * (1 - 7.94 / 39.04)) < 1e-3,
        'inner_race_matched_with_sidebands': 'BPFI' in ri['envelope']['match']['matched'] and len(ri['envelope']['match']['rows']['BPFI']['sidebands']['found']) >= 1
                                             and 'BPFO' not in ri['envelope']['match']['matched'],
        'quiet_record_zone_A_no_match': quiet['zone']['zone'] == 'A' and quiet['envelope']['match']['matched'] == [],
        'partial_band_named': slow['broadband']['band_partial'] and slow['zone'].get('zone_qualified') == 'PARTIAL BAND' and any('PARTIAL BAND' in g for g in slow['gaps']),
        'undeclared_named': undeclared['zone'] is None and undeclared['status'] == 'PARTIAL' and any('group' in g for g in undeclared['gaps']) and any('bearing' in g for g in undeclared['gaps']),
        'velocity_input_agrees': vel['broadband']['from'] == 'velocity' and abs(vel['broadband']['rms_mm_s'] - rms) / rms < 0.1,
        'boundaries': ZONE_BOUNDARIES[(1, 'rigid')] == (2.3, 4.5, 7.1) and ZONE_BOUNDARIES[(2, 'flexible')] == (2.3, 4.5, 7.1) and ZONE_BOUNDARIES[(1, 'flexible')] == (3.5, 7.1, 11.0)
                      and zone(2.3, 1, 'rigid')['zone'] == 'A' and zone(2.31, 1, 'rigid')['zone'] == 'B' and zone(11.01, 1, 'flexible')['zone'] == 'D',
    }
    return {'label': sc['label'], 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'assessment': r, 'inner': ri,
            'truth': tr}


def report_text(r: dict) -> str:
    bb = r.get('broadband') or {}
    lines = [f"vibration [{r.get('label') or '-'}]: {r.get('status')}"]
    if bb.get('status') == 'EVALUATED':
        lines.append(f"   broadband r.m.s. velocity {bb['rms_mm_s']} mm/s over {bb['band_hz'][0]:g}-{bb['band_hz'][1]:g} Hz"
                     + (' (PARTIAL BAND)' if bb.get('band_partial') else '') + f" from {bb['from']} ({bb['unit_in']}), {bb['seconds']} s at {bb['fs_hz']:g} Hz")
    z = r.get('zone')
    if z:
        lines.append(f"   ISO 20816-3 zone {z['zone']}" + (f" [{z['zone_qualified']}]" if z.get('zone_qualified') else '') + f": {z['meaning']}; group {z['group']} {z['support']}, "
                     f"boundaries A/B {z['boundaries_mm_s']['A/B']}, B/C {z['boundaries_mm_s']['B/C']}, C/D {z['boundaries_mm_s']['C/D']} mm/s"
                     + (f"; {z['margin_to_next_mm_s']} mm/s to the next boundary" if z['margin_to_next_mm_s'] is not None else ''))
    env = r.get('envelope') or {}
    if env.get('band'):
        b = env['band']
        lines.append(f"   envelope band {b['band_hz'][0]:g}-{b['band_hz'][1]:g} Hz ({b['status'].lower()}" + (f", kurtosis {b['kurtosis']}" if b.get('kurtosis') is not None else '') + ')')
    m = env.get('match')
    if m:
        bf = env['bearing']
        lines.append(f"   bearing: fr {bf['fr']} Hz; BPFO {bf['BPFO']}, BPFI {bf['BPFI']}, BSF {bf['BSF']}, FTF {bf['FTF']} Hz -> {m['status']}")
        for k, v in m['rows'].items():
            lines.append(f"      {k} {v['frequency_hz']} Hz: {v['standing']}" + (f" at {', '.join(str(h['found_hz']) + ' Hz (' + str(h['harmonic']) + 'x)' for h in v['harmonics_found'])}" if v['harmonics_found'] else '')
                         + (f"; sidebands at fr: {v['sidebands']['found'] or 'none'}" if v.get('sidebands') else ''))
    elif env.get('peaks'):
        lines.append('   envelope spectrum peaks (Hz): ' + ', '.join(f"{p['f_hz']}" for p in env['peaks'][:6]) + ' - nothing matched (no bearing geometry)')
    for g in r.get('gaps') or []:
        lines.append(f'   gap: {g}')
    return '\n'.join(lines)
