# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_array - the array step: from "a rig is in this record" to "it is over there".

One station detects. An array - several sensors within a kilometre or two of
each other, recording together - gives a direction: the delays of the same
wavefront across the sensors fix the direction it came from (the
back-azimuth) and how fast it crossed (the apparent velocity, the inverse
of the slowness). Two or more arrays at different places give a position
where their directions cross. This module is that arithmetic, in the form
every seismological array uses (Rost and Thomas 2002 is the review):

  * frequency-domain beamforming over a grid of slowness vectors - the
    conventional (Bartlett) beam, and the Capon beam that is sharper at the
    price of needing more data than sensors;
  * the array response function, so the resolution of a direction is a
    number that comes with it, and the aliasing lobes of a sparse array are
    named rather than mistaken for sources;
  * the intersection of back-azimuths from several arrays, weighted by their
    own uncertainties, with the error ellipse of the crossing;
  * location from time lags between distant single stations, by Gauss-Newton
    on the travel-time differences, which needs a velocity and says so;
  * the array detectability test: for each rig on the ground-truth list, in
    the windows it worked alone, did the array point at it? The verdict is
    POINTED when the beam's direction is within the array's own resolution
    of the rig's true bearing.

What this will not call a measurement, printed with every result: a position
from one array (an array gives a direction); a source closer to the array
than a few apertures (the plane-wave assumption behind beamforming fails
there); a velocity (the apparent velocity across the array is measured, the
medium velocity a lag location needs is an input, named); a direction
sharper than the array response function allows; and any rig that is not on
the list. The synthetic scene behind the self-test states its own
assumptions and is labelled SIMULATION_SELF_TEST.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .seismic import Trace, iso, parse_time
from .seismic_detect import EARTH_RADIUS_KM, Source, haversine_km

# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
@dataclass
class Sensor:
    sensor_id: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    datum: str = 'WGS84'        # after loading; a sensor on another datum is converted on the way in
    datum_as_given: str = ''


def load_sensors_csv(path: str, datum: Optional[str] = None) -> List[Sensor]:
    """Columns: sensor_id, lat, lon[, elevation_m, datum]. A datum column (or the argument, for the whole
    file) converts the array to WGS84 on the way in. An array whose sensors are on two different datums is
    not an array - the geometry is wrong by the separation between them - so that is refused here."""
    from . import geodesy as GD
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        rd = csv.DictReader(f)
        need = {'sensor_id', 'lat', 'lon'}
        missing = need - set(c.strip() for c in (rd.fieldnames or []))
        if missing:
            raise ValueError(f"sensors CSV lacks columns {sorted(missing)}; the columns are sensor_id, lat, lon[, elevation_m]")
        for row in rd:
            row = {k.strip(): (v or '').strip() for k, v in row.items() if k}
            if row.get('sensor_id'):
                d_in = GD.datum_name(row.get('datum') or datum or '')
                la, lo = float(row['lat']), float(row['lon'])
                if d_in not in ('WGS84', 'UNKNOWN'):
                    c = GD.to_wgs84(la, lo, 0.0, d_in)
                    la, lo = c['lat'], c['lon']
                out.append(Sensor(row['sensor_id'], la, lo, float(row.get('elevation_m') or 0.0),
                                  'WGS84' if d_in != 'UNKNOWN' else 'UNKNOWN', d_in))
    mixed = sorted({s_.datum_as_given for s_ in out if s_.datum_as_given})
    if len(mixed) > 1:
        raise ValueError(f"the sensors in {path} are on more than one datum ({', '.join(mixed)}): an array's geometry is the differences between "
                         'its sensors, so a mixed-datum sensor list is not an array. Put them all on one datum first')
    return out


def local_xy(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    """East and north (km) of a point from a reference, on the local tangent plane (good to <0.1 % within 100 km)."""
    x = math.radians(lon - lon0) * math.cos(math.radians(0.5 * (lat + lat0))) * EARTH_RADIUS_KM
    y = math.radians(lat - lat0) * EARTH_RADIUS_KM
    return x, y


def xy_to_latlon(x: float, y: float, lat0: float, lon0: float) -> Tuple[float, float]:
    lat = lat0 + math.degrees(y / EARTH_RADIUS_KM)
    lon = lon0 + math.degrees(x / (EARTH_RADIUS_KM * math.cos(math.radians(0.5 * (lat + lat0)))))
    return lat, lon


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def array_geometry(sensors: Sequence[Sensor]) -> dict:
    lat0 = float(np.mean([s.lat for s in sensors]))
    lon0 = float(np.mean([s.lon for s in sensors]))
    xy = np.array([local_xy(s.lat, s.lon, lat0, lon0) for s in sensors])
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    return {'lat0': lat0, 'lon0': lon0, 'xy_km': xy, 'aperture_km': float(d.max()), 'min_spacing_km': float(d[d > 0].min()) if len(sensors) > 1 else 0.0,
            'n': len(sensors)}


# ---------------------------------------------------------------------------
# the cross-spectral matrix and the beams
# ---------------------------------------------------------------------------
def _common_window(traces: Sequence[Trace]) -> Tuple[float, float, float]:
    fs = traces[0].sample_rate
    if any(abs(t.sample_rate - fs) > 1e-9 for t in traces):
        raise ValueError("the sensors' records must share one sample rate (resample first)")
    t0 = max(t.starttime for t in traces)
    t1 = min(t.endtime for t in traces)
    if t1 <= t0:
        raise ValueError("the sensors' records do not overlap in time")
    return fs, t0, t1


def cross_spectral_matrix(traces: Sequence[Trace], band: Tuple[float, float], seg_s: float = 10.0, overlap: float = 0.5) -> dict:
    """Welch cross-spectral matrix C[f, i, j] over the band, from the common window of the records."""
    fs, t0, t1 = _common_window(traces)
    X = np.vstack([t.slice(t0, t1).data.astype(np.float64) for t in traces])
    n = X.shape[1]
    X = X - X.mean(axis=1, keepdims=True)
    nseg = int(min(n, max(64, round(seg_s * fs))))
    step = max(1, int(nseg * (1 - overlap)))
    win = np.hanning(nseg)
    freqs = np.fft.rfftfreq(nseg, 1.0 / fs)
    m = (freqs >= band[0]) & (freqs <= band[1])
    fb = freqs[m]
    C = np.zeros((len(fb), X.shape[0], X.shape[0]), dtype=complex)
    k = 0
    for s in range(0, n - nseg + 1, step):
        F = np.fft.rfft(X[:, s:s + nseg] * win, axis=1)[:, m]          # sensors x freqs
        C += np.einsum('if,jf->fij', F, np.conj(F))
        k += 1
    if k == 0:
        raise ValueError("the common window is shorter than one segment")
    return {'freqs': fb, 'csm': C / k, 'segments': k, 'fs': fs, 't0': t0, 't1': t1, 'nseg': nseg}


def slowness_grid(s_max: float = 3.0, n: int = 61) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    s = np.linspace(-s_max, s_max, n)
    sx, sy = np.meshgrid(s, s)                       # east, north components, s/km
    return s, sx, sy


def _beam_on_grid(csm: dict, xy_km: np.ndarray, sx: np.ndarray, sy: np.ndarray, method: str, loading: float) -> np.ndarray:
    freqs, C = csm['freqs'], csm['csm']
    nsen = xy_km.shape[0]
    P = np.zeros(sx.shape)
    delay = sx[..., None] * xy_km[:, 0] + sy[..., None] * xy_km[:, 1]          # grid x sensors: p . r  (s)
    for fi, f in enumerate(freqs):
        Cf = C[fi]
        tr = float(np.real(np.trace(Cf))) + 1e-30
        A = np.exp(-2j * np.pi * f * delay).reshape(-1, nsen)                   # steering, grid x sensors
        if method == 'capon':
            Ci = np.linalg.inv(Cf + loading * tr / nsen * np.eye(nsen))
            q = np.einsum('gi,ij,gj->g', np.conj(A), Ci, A)
            Pf = (1.0 / np.maximum(np.real(q), 1e-30)).reshape(sx.shape)
            Pf = Pf / Pf.max()
        else:
            q = np.einsum('gi,ij,gj->g', np.conj(A), Cf, A)
            Pf = (np.real(q) / (nsen * tr)).reshape(sx.shape)                   # 1 for a perfectly coherent plane wave at its own slowness
        P += Pf
    return P / max(len(freqs), 1)


def beam_power(csm: dict, xy_km: np.ndarray, s_max: float = 3.0, n_grid: int = 61, method: str = 'bartlett', loading: float = 0.01) -> dict:
    """Beam power over the slowness grid, averaged over the band's frequency bins and normalized to the total power,
    then refined on a fine grid around the maximum (the coarse step would otherwise be the azimuth error).
    bartlett: a^H C a; capon: 1 / (a^H C^-1 a) with diagonal loading. Slowness vector = propagation direction."""
    s, sx, sy = slowness_grid(s_max, n_grid)
    P = _beam_on_grid(csm, xy_km, sx, sy, method, loading)
    i, j = np.unravel_index(int(np.argmax(P)), P.shape)
    px, py = float(sx[i, j]), float(sy[i, j])
    ds = float(s[1] - s[0])
    # refine: a 41 x 41 grid spanning +/- 1.5 coarse steps around the maximum
    fx = np.linspace(px - 1.5 * ds, px + 1.5 * ds, 41)
    fy = np.linspace(py - 1.5 * ds, py + 1.5 * ds, 41)
    gx, gy = np.meshgrid(fx, fy)
    Pf = _beam_on_grid(csm, xy_km, gx, gy, method, loading)
    fi, fj = np.unravel_index(int(np.argmax(Pf)), Pf.shape)
    px, py = float(gx[fi, fj]), float(gy[fi, fj])
    coh = float(Pf.max()) if method == 'bartlett' else float(_beam_on_grid(csm, xy_km, np.array([[px]]), np.array([[py]]), 'bartlett', loading)[0, 0])
    # per-frequency coherence at the found slowness: a machinery line arriving as one plane wave is near 1 in its own bin
    # even when the band as a whole is mostly incoherent noise
    nsen = xy_km.shape[0]
    a = np.exp(-2j * np.pi * csm['freqs'][:, None] * (px * xy_km[:, 0] + py * xy_km[:, 1]))          # freqs x sensors
    per_bin = np.real(np.einsum('fi,fij,fj->f', np.conj(a), csm['csm'], a)) / (nsen * np.real(np.einsum('fii->f', csm['csm'])) + 1e-30)
    slow = math.hypot(px, py)
    baz = (math.degrees(math.atan2(-px, -py)) + 360.0) % 360.0                  # the direction the wave came FROM
    coherent = csm['freqs'][per_bin >= 0.5]
    return {'power': P, 'sx': sx, 'sy': sy, 'max_power': float(P.max()), 'coherence': coh, 'coherence_by_bin': per_bin,
            'coherence_max_bin': float(per_bin.max()), 'coherent_bins': int((per_bin >= 0.5).sum()),
            'coherent_frequencies_hz': [round(float(x), 3) for x in coherent[:40]], 'slowness_s_km': slow,
            'apparent_velocity_km_s': (1.0 / slow if slow > 0 else None), 'back_azimuth_deg': baz, 'method': method,
            'band_hz': [float(csm['freqs'].min()), float(csm['freqs'].max())], 'n_freqs': int(len(csm['freqs'])), 'fine_step_s_km': float(fx[1] - fx[0])}


def array_response(xy_km: np.ndarray, freqs: Sequence[float], s_max: float = 3.0, n_grid: int = 61, p0=(0.0, 0.0)) -> dict:
    """The array response function: the Bartlett beam of a unit plane wave at slowness p0 - the pattern any
    source is smeared into. Its half-power width is the array's resolution; its secondary maxima above 0.5
    are the aliasing lobes of the geometry at this band."""
    s, sx, sy = slowness_grid(s_max, n_grid)
    nsen = xy_km.shape[0]
    delay = (sx[..., None] - p0[0]) * xy_km[:, 0] + (sy[..., None] - p0[1]) * xy_km[:, 1]
    P = np.zeros(sx.shape)
    for f in freqs:
        P += np.abs(np.exp(-2j * np.pi * f * delay).sum(-1)) ** 2 / nsen ** 2
    P /= max(len(freqs), 1)
    ds = float(s[1] - s[0])
    main = P >= 0.5
    # the connected half-power region around p0
    i0, j0 = np.unravel_index(int(np.argmax(P)), P.shape)
    seen = np.zeros_like(main)
    stack = [(i0, j0)]
    while stack:
        a, b = stack.pop()
        if a < 0 or b < 0 or a >= P.shape[0] or b >= P.shape[1] or seen[a, b] or not main[a, b]:
            continue
        seen[a, b] = True
        stack += [(a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)]
    width = 2.0 * math.sqrt(seen.sum() * ds * ds / math.pi)                    # the diameter of a circle of the same area
    lobes = main & ~seen
    return {'power': P, 'sx': sx, 'sy': sy, 'half_power_width_s_km': width, 'aliasing_lobes': bool(lobes.any()),
            'aliasing_lobe_fraction': float(lobes.mean()), 'grid_step_s_km': ds}


def beam(traces: Sequence[Trace], sensors: Sequence[Sensor], band: Tuple[float, float] = (1.0, 20.0), seg_s: float = 10.0,
         s_max: float = 3.0, n_grid: int = 61, method: str = 'bartlett') -> dict:
    """The direction a band of energy crossed the array from, with the array's own resolution beside it."""
    if len(traces) != len(sensors) or len(sensors) < 3:
        raise ValueError("beamforming needs three or more sensors, one record each, in the same order")
    geo = array_geometry(sensors)
    csm = cross_spectral_matrix(traces, band, seg_s)
    bp = beam_power(csm, geo['xy_km'], s_max, n_grid, method)
    arf = array_response(geo['xy_km'], csm['freqs'], 1.0, 81, (0.0, 0.0))          # the pattern is shift-invariant: a fine grid around zero gives the width
    arf_full = array_response(geo['xy_km'], csm['freqs'], s_max, n_grid, (0.0, 0.0))  # the coarse full grid finds the aliasing lobes
    arf['aliasing_lobes'] = arf['aliasing_lobes'] or arf_full['aliasing_lobes']
    arf['aliasing_lobe_fraction'] = max(arf['aliasing_lobe_fraction'], arf_full['aliasing_lobe_fraction'])
    slow = bp['slowness_s_km']
    # the azimuth resolution from the slowness half-width at the found slowness
    az_res = math.degrees(math.atan2(0.5 * arf['half_power_width_s_km'], slow)) * 2.0 if slow > 0 else 180.0
    f_c = 0.5 * (band[0] + band[1])
    out = {'protocol': 'seismic_array.beam', 'sensors': [s.sensor_id for s in sensors], 'n_sensors': len(sensors),
           'array_centre': {'lat': geo['lat0'], 'lon': geo['lon0']}, 'aperture_km': round(geo['aperture_km'], 4), 'min_spacing_km': round(geo['min_spacing_km'], 4),
           'window': {'start': iso(csm['t0'], 0), 'end': iso(csm['t1'], 0), 'segments': csm['segments'], 'segment_s': seg_s},
           'band_hz': list(band), 'method': method,
           'back_azimuth_deg': round(bp['back_azimuth_deg'], 2), 'slowness_s_km': round(slow, 4), 'apparent_velocity_km_s': round(bp['apparent_velocity_km_s'], 3),
           'coherence': round(bp['coherence'], 4), 'coherence_max_bin': round(bp['coherence_max_bin'], 4), 'coherent_bins': bp['coherent_bins'],
           'coherent_frequencies_hz': bp['coherent_frequencies_hz'],
           'resolution': {'half_power_width_s_km': round(arf['half_power_width_s_km'], 4), 'azimuth_half_width_deg': round(0.5 * az_res, 2),
                          'aliasing_lobes': arf['aliasing_lobes'], 'grid_step_s_km': arf['grid_step_s_km'],
                          'note': 'the azimuth half-width is the array response function\'s half-power width projected at the measured slowness; '
                                  'aliasing_lobes true means the geometry repeats the pattern elsewhere on the grid at this band and a lone peak may be a lobe'},
           'plane_wave_limit_km': round(5.0 * geo['aperture_km'], 2),
           'basis': 'frequency-domain beamforming over a slowness grid (Bartlett, or Capon with 1 % diagonal loading) on the Welch cross-spectral matrix; '
                    f'band {band[0]:g}-{band[1]:g} Hz, {csm["segments"]} segments of {seg_s:g} s; slowness vector = propagation direction, back-azimuth = direction the energy came from',
           'not_a_measurement': ['a position - one array gives a direction and an apparent velocity, not a distance',
                                 f'a source nearer than about five apertures ({5.0 * geo["aperture_km"]:.1f} km): the wavefront is not plane there',
                                 'a direction sharper than the array response function allows (the half-width above)',
                                 'the medium velocity: the apparent velocity across the array is measured; it equals the medium velocity only for a horizontally travelling wave'],
           '_grid': bp, '_arf': arf}
    # secondary peaks above half the maximum, for the reader to see what else the beam holds
    P = bp['power']
    peaks = []
    for i in range(1, P.shape[0] - 1):
        for j in range(1, P.shape[1] - 1):
            v = P[i, j]
            if v >= 0.5 * bp['max_power'] and v >= P[i - 1:i + 2, j - 1:j + 2].max():
                px, py = float(bp['sx'][i, j]), float(bp['sy'][i, j])
                peaks.append({'back_azimuth_deg': round((math.degrees(math.atan2(-px, -py)) + 360.0) % 360.0, 1), 'slowness_s_km': round(math.hypot(px, py), 3), 'power': round(float(v), 4)})
    peaks.sort(key=lambda d: -d['power'])
    # the first peak is the refined maximum; the coarse cell it came from is dropped
    peaks = [{'back_azimuth_deg': round(bp['back_azimuth_deg'], 1), 'slowness_s_km': round(slow, 3), 'power': round(bp['max_power'], 4)}] + peaks[1:]
    out['peaks'] = peaks[:8]
    return out


# ---------------------------------------------------------------------------
# location
# ---------------------------------------------------------------------------
def intersect_backazimuths(arrays: Sequence[dict]) -> dict:
    """arrays: [{'lat','lon','back_azimuth_deg','sigma_deg'}, ...] (two or more). The weighted least-squares
    crossing point on the local plane, its covariance from the azimuth uncertainties, the error ellipse (1 sigma),
    and whether every array sees the point in front of it."""
    if len(arrays) < 2:
        raise ValueError("a crossing needs two or more arrays")
    lat0 = float(np.mean([a['lat'] for a in arrays]))
    lon0 = float(np.mean([a['lon'] for a in arrays]))
    pos = np.array([local_xy(a['lat'], a['lon'], lat0, lon0) for a in arrays])
    th = np.radians([a['back_azimuth_deg'] for a in arrays])
    sig = np.radians([max(a.get('sigma_deg', 1.0), 0.05) for a in arrays])
    u = np.stack([np.sin(th), np.cos(th)], axis=1)                 # bearing unit vectors (east, north)
    nrm = np.stack([np.cos(th), -np.sin(th)], axis=1)              # normals
    x = pos.mean(axis=0)
    for _ in range(20):                                            # iterate the range-dependent weights
        d = np.maximum(np.linalg.norm(pos - x, axis=1), 0.1)
        w = 1.0 / (sig * d) ** 2
        A = nrm * np.sqrt(w)[:, None]
        b = (nrm * pos).sum(axis=1) * np.sqrt(w)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        if np.linalg.norm(sol - x) < 1e-6:
            x = sol
            break
        x = sol
    d = np.maximum(np.linalg.norm(pos - x, axis=1), 0.1)
    w = 1.0 / (sig * d) ** 2
    N = (nrm.T * w) @ nrm
    cov = np.linalg.inv(N) if np.linalg.cond(N) < 1e12 else np.full((2, 2), np.inf)
    evals, evecs = np.linalg.eigh(cov) if np.all(np.isfinite(cov)) else (np.array([np.inf, np.inf]), np.eye(2))
    in_front = [bool(((x - pos[i]) @ u[i]) > 0) for i in range(len(arrays))]
    resid_deg = [round(float(_angle_diff(math.degrees(math.atan2(*(x - pos[i]))), math.degrees(th[i]))), 3) for i in range(len(arrays))]
    lat, lon = xy_to_latlon(float(x[0]), float(x[1]), lat0, lon0)
    major, minor = (math.sqrt(max(evals[1], 0)), math.sqrt(max(evals[0], 0))) if np.all(np.isfinite(evals)) else (None, None)
    return {'protocol': 'seismic_array.intersect_backazimuths', 'lat': lat, 'lon': lon, 'x_km': float(x[0]), 'y_km': float(x[1]),
            'reference': {'lat': lat0, 'lon': lon0}, 'ranges_km': [round(float(v), 3) for v in d], 'in_front_of_every_array': all(in_front), 'in_front': in_front,
            'residual_deg': resid_deg,
            'ellipse_1sigma': {'major_km': (round(major, 3) if major is not None else None), 'minor_km': (round(minor, 3) if minor is not None else None),
                               'major_azimuth_deg': round((math.degrees(math.atan2(evecs[0, 1], evecs[1, 1])) + 360.0) % 360.0, 1) if np.all(np.isfinite(evals)) else None},
            'crossing_angle_deg': round(float(min(abs(_angle_diff(math.degrees(th[i]), math.degrees(th[j]))) % 180.0 for i in range(len(arrays)) for j in range(i + 1, len(arrays)))), 1),
            'basis': 'weighted least squares on the perpendicular distances to each bearing line, weights 1/(sigma x range)^2; the covariance is the inverse normal matrix',
            'not_a_measurement': ['a point from bearings that nearly coincide (the crossing angle is printed; under 15 degrees the ellipse says it)',
                                  'a point behind any array (in_front false): the bearing was not of this source']}


def envelope(x: np.ndarray) -> np.ndarray:
    n = len(x)
    X = np.fft.fft(x, n)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1
        h[1:n // 2] = 2
    else:
        h[0] = 1
        h[1:(n + 1) // 2] = 2
    return np.abs(np.fft.ifft(X * h))


def bandpass(x: np.ndarray, fs: float, band: Tuple[float, float]) -> np.ndarray:
    n = len(x)
    X = np.fft.rfft(x - x.mean())
    f = np.fft.rfftfreq(n, 1 / fs)
    X[(f < band[0]) | (f > band[1])] = 0
    return np.fft.irfft(X, n)


def pair_lag(a: Trace, b: Trace, band: Tuple[float, float], max_lag_s: float, use_envelope: bool = True) -> dict:
    """The lag of b after a (s) from the cross-correlation of the band-passed records (their envelopes by default,
    which do not cycle-skip on narrow-band machinery); with the peak's normalized height and a width-based sigma."""
    fs, t0, t1 = _common_window([a, b])
    xa = bandpass(a.slice(t0, t1).data.astype(float), fs, band)
    xb = bandpass(b.slice(t0, t1).data.astype(float), fs, band)
    if use_envelope:
        xa, xb = envelope(xa), envelope(xb)
        xa, xb = xa - xa.mean(), xb - xb.mean()
    n = min(len(xa), len(xb))
    xa, xb = xa[:n], xb[:n]
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(xb, nfft) * np.conj(np.fft.rfft(xa, nfft)), nfft)
    cc = np.concatenate([cc[-(nfft // 2):], cc[:nfft // 2]])
    lags = (np.arange(nfft) - nfft // 2) / fs
    norm = math.sqrt(float((xa ** 2).sum() * (xb ** 2).sum())) + 1e-30
    cc = cc / norm
    m = np.abs(lags) <= max_lag_s
    k = int(np.argmax(cc[m]))
    lag = float(lags[m][k])
    peak = float(cc[m][k])
    # sigma: the half-width of the peak above 0.5 of its height
    seg = cc[m]
    half = seg >= 0.5 * peak
    left = k
    while left > 0 and half[left - 1]:
        left -= 1
    right = k
    while right < len(seg) - 1 and half[right + 1]:
        right += 1
    sigma = max((right - left + 1) / fs / 2.355, 1.0 / fs)
    return {'lag_s': lag, 'peak': peak, 'sigma_s': sigma, 'envelope': use_envelope, 'band_hz': list(band)}


def locate_from_lags(stations: Sequence[Tuple[float, float]], lags: Sequence[Tuple[int, int, float, float]], v_km_s: float,
                     start: Optional[Tuple[float, float]] = None, iterations: int = 50) -> dict:
    """stations: (lat, lon) per station; lags: (i, j, lag_s, sigma_s) meaning station j received lag_s after station i;
    v_km_s: the medium velocity (an input, not a measurement). Gauss-Newton on d_j - d_i = v * lag."""
    lat0 = float(np.mean([s[0] for s in stations]))
    lon0 = float(np.mean([s[1] for s in stations]))
    pos = np.array([local_xy(la, lo, lat0, lon0) for la, lo in stations])
    x = np.array(local_xy(*start, lat0, lon0)) if start else pos.mean(axis=0) + np.array([0.01, 0.01])
    I = np.array([l[0] for l in lags]); J = np.array([l[1] for l in lags])
    obs = np.array([l[2] for l in lags]) * v_km_s
    sig = np.maximum(np.array([l[3] for l in lags]) * v_km_s, 1e-3)
    converged = False
    for _ in range(iterations):
        di = np.linalg.norm(x - pos[I], axis=1)
        dj = np.linalg.norm(x - pos[J], axis=1)
        r = obs - (dj - di)
        G = ((x - pos[J]) / np.maximum(dj, 1e-6)[:, None]) - ((x - pos[I]) / np.maximum(di, 1e-6)[:, None])
        Gw = G / sig[:, None]
        rw = r / sig
        try:
            dx, *_ = np.linalg.lstsq(Gw, rw, rcond=None)
        except np.linalg.LinAlgError:
            break
        x = x + dx
        if np.linalg.norm(dx) < 1e-5:
            converged = True
            break
    di = np.linalg.norm(x - pos[I], axis=1); dj = np.linalg.norm(x - pos[J], axis=1)
    r = obs - (dj - di)
    G = ((x - pos[J]) / np.maximum(dj, 1e-6)[:, None]) - ((x - pos[I]) / np.maximum(di, 1e-6)[:, None])
    N = (G / sig[:, None]).T @ (G / sig[:, None])
    cov = np.linalg.inv(N) if np.linalg.cond(N) < 1e12 else np.full((2, 2), np.inf)
    evals = np.linalg.eigvalsh(cov) if np.all(np.isfinite(cov)) else np.array([np.inf, np.inf])
    lat, lon = xy_to_latlon(float(x[0]), float(x[1]), lat0, lon0)
    return {'protocol': 'seismic_array.locate_from_lags', 'lat': lat, 'lon': lon, 'converged': converged, 'v_km_s_assumed': v_km_s,
            'rms_residual_s': round(float(np.sqrt(np.mean(r ** 2))) / v_km_s, 4), 'n_lags': len(lags),
            'ellipse_1sigma_km': {'major': round(math.sqrt(max(evals[1], 0)), 3), 'minor': round(math.sqrt(max(evals[0], 0)), 3)} if np.all(np.isfinite(evals)) else None,
            'basis': 'Gauss-Newton on the differences of distance to station pairs, d_j - d_i = v x lag, weights 1/sigma^2',
            'not_a_measurement': ['the velocity: it is the input named above, and the position scales with it',
                                  'a position from lags measured on a pure tone (the envelope lag is used so that cycles are not skipped; a steady tone has no envelope to lag)']}


# ---------------------------------------------------------------------------
# the array detectability test
# ---------------------------------------------------------------------------
def array_detectability(traces: Sequence[Trace], sensors: Sequence[Sensor], sources: Sequence[Source], band: Tuple[float, float] = (1.0, 20.0),
                        win_s: float = 600.0, seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 61, method: str = 'bartlett', min_coherence: float = 0.5) -> dict:
    """For each listed source, in the windows it worked alone: did the array point at it?
    POINTED when the beam's back-azimuth is within the array's azimuth half-width (plus a degree) of the true bearing
    and at least two frequency bins are coherent (>= 0.5) at that slowness; NOT_POINTED when it is coherent but points elsewhere; INCOHERENT when nothing coherent crossed
    the array in those windows; AMBIGUOUS when the source never worked alone; INSUFFICIENT_WINDOWS when it was not in the record."""
    geo = array_geometry(sensors)
    fs, t0, t1 = _common_window(traces)
    edges = np.arange(t0, t1 - win_s + 1e-9, win_s)
    centres = edges + win_s / 2
    active = np.array([[(c >= s.start) and (c <= s.end) for c in centres] for s in sources], dtype=bool).reshape(len(sources), -1)
    n_active = active.sum(axis=0) if len(sources) else np.zeros(len(centres), dtype=int)
    out = {'protocol': 'seismic_array.array_detectability', 'array': {'centre_lat': geo['lat0'], 'centre_lon': geo['lon0'], 'n_sensors': len(sensors),
           'aperture_km': round(geo['aperture_km'], 4)}, 'band_hz': list(band), 'win_s': win_s, 'method': method, 'windows': int(len(centres)), 'sources': []}
    for i, s in enumerate(sources):
        excl = active[i] & (n_active == 1)
        true_baz = bearing_deg(geo['lat0'], geo['lon0'], s.lat, s.lon)
        dist = haversine_km(geo['lat0'], geo['lon0'], s.lat, s.lon)
        row = {'source_id': s.source_id, 'distance_km': round(dist, 3), 'true_back_azimuth_deg': round(true_baz, 2), 'exclusive_windows': int(excl.sum()), 'note': s.note}
        if active[i].sum() == 0:
            row.update(verdict='INSUFFICIENT_WINDOWS', detail='the source was not active inside this record')
        elif excl.sum() == 0:
            row.update(verdict='AMBIGUOUS', detail='never active alone')
        else:
            bazs, cohs, res, nb, cf = [], [], [], [], []
            for w in np.where(excl)[0]:
                sub = [t.slice(edges[w], edges[w] + win_s) for t in traces]
                b = beam(sub, sensors, band, seg_s, s_max, n_grid, method)
                bazs.append(b['back_azimuth_deg']); cohs.append(b['coherence_max_bin']); res.append(b['resolution']['azimuth_half_width_deg'])
                nb.append(b['coherent_bins']); cf.extend(b['coherent_frequencies_hz'])
            # the circular mean of the window azimuths
            ang = np.radians(bazs)
            mean_baz = (math.degrees(math.atan2(np.sin(ang).mean(), np.cos(ang).mean())) + 360.0) % 360.0
            R = float(min(max(np.hypot(np.sin(ang).mean(), np.cos(ang).mean()), 1e-12), 1.0))   # one window gives R == 1 exactly; float error can exceed it
            spread = float(np.degrees(np.sqrt(max(-2 * np.log(R), 0.0))))
            diff = abs(_angle_diff(mean_baz, true_baz))
            half = float(np.median(res))
            tol = half + 1.0                                                       # the fine grid's step is well under a degree at any slowness of interest
            coh = float(np.median(cohs))
            bins = int(np.median(nb))
            freqs_seen = sorted({round(x, 1) for x in cf})
            row.update(beam_back_azimuth_deg=round(mean_baz, 2), azimuth_error_deg=round(diff, 2), window_spread_deg=round(spread, 2),
                       coherence=round(coh, 4), coherent_bins=bins, coherent_frequencies_hz=freqs_seen[:20], azimuth_half_width_deg=round(half, 2), tolerance_deg=round(tol, 2))
            if coh < min_coherence or bins < 2:
                row.update(verdict='INCOHERENT', detail=f'best-bin coherence {coh:.2f} ({bins} coherent bins): nothing coherent enough crossed the array in these windows')
            elif diff <= tol:
                row.update(verdict='POINTED', detail=f'the beam points {diff:.1f} deg from the rig, inside the array\'s {tol:.1f} deg')
            else:
                row.update(verdict='NOT_POINTED', detail=f'the beam points {diff:.1f} deg from the rig, outside the array\'s {tol:.1f} deg - something else, or a lobe')
            if dist < 5.0 * geo['aperture_km']:
                row['caveat'] = f'the rig is {dist:.1f} km away, under five apertures: the plane-wave assumption is weak here'
        out['sources'].append(row)
    out['basis'] = ('per exclusive window, the Bartlett/Capon beam over the slowness grid; the circular mean of the window back-azimuths against the '
                    'great-circle bearing to the listed position; the tolerance is the array response function\'s azimuth half-width at the measured slowness plus one grid step')
    out['not_a_measurement'] = ['a position: this test says whether the array points at a listed rig, not where an unlisted one is',
                                'anything for a rig that never worked alone, or nearer than five apertures',
                                'a direction finer than the tolerance printed with it']
    return out


def report_text(res: dict) -> str:
    L = [f"array detectability - {res['array']['n_sensors']} sensors, aperture {res['array']['aperture_km']:.3f} km, centre ({res['array']['centre_lat']:.4f}, {res['array']['centre_lon']:.4f}); "
         f"band {res['band_hz'][0]:g}-{res['band_hz'][1]:g} Hz, {res['windows']} windows of {res['win_s']:g} s, {res['method']}"]
    for r in sorted(res['sources'], key=lambda r: r['distance_km']):
        extra = (f" beam {r['beam_back_azimuth_deg']:6.1f} vs true {r['true_back_azimuth_deg']:6.1f} deg (err {r['azimuth_error_deg']:.1f}, tol {r['tolerance_deg']:.1f}, "
                 f"best-bin coh {r['coherence']:.2f}, {r['coherent_bins']} coherent bins at {', '.join(f'{x:g}' for x in r['coherent_frequencies_hz'][:5])} Hz)"
                 if 'beam_back_azimuth_deg' in r else '')
        L.append(f"  {r['source_id']:12s} {r['distance_km']:7.1f} km  {r['verdict']:20s}{extra}" + (f"  [{r['caveat']}]" if r.get('caveat') else ''))
    L.append(f"  basis: {res['basis']}")
    L.append("  not a measurement: " + '; '.join(res['not_a_measurement']))
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# the synthetic array scene - SIMULATION_SELF_TEST
# ---------------------------------------------------------------------------
def synthetic_array_scene(seed: int = 5, fs: float = 50.0, hours: float = 10.0, centre=(31.0, -102.0), n_sensors: int = 9, aperture_km: float = 1.2,
                          v_km_s: float = 2.5, q_factor: float = 100.0, distances_km: Sequence[float] = (6.0, 12.0, 25.0, 45.0), noise: float = 30.0,
                          sensor_noise: float = 1.0) -> Tuple[List[Trace], List[Sensor], List[Source], dict]:
    """An array of n_sensors on two rings inside aperture_km around `centre`, and rigs at the listed distances on
    different bearings, each working alone for an hour. Each rig's machinery lines arrive as a plane wave at
    velocity v_km_s (the scene's assumption), attenuated by 1/r and exp(-pi f r/(Q v)); the background is coloured
    noise common to the array (a slow horizontal wave from a random direction) plus incoherent sensor noise."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = centre
    # sensors: centre + ring of (n-1)
    sensors: List[Sensor] = [Sensor('S00', lat0, lon0)]
    for k in range(n_sensors - 1):
        ang = 2 * math.pi * k / (n_sensors - 1)
        r = aperture_km / 2.0 * (1.0 if k % 2 == 0 else 0.55)
        lat, lon = xy_to_latlon(r * math.sin(ang), r * math.cos(ang), lat0, lon0)
        sensors.append(Sensor(f'S{k + 1:02d}', lat, lon))
    geo = array_geometry(sensors)
    xy = geo['xy_km']
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    t0 = parse_time('2025-06-01T00:00:00Z')
    # common background: a weak plane wave of coloured noise from a random bearing at 0.4 km/s plus per-sensor white noise
    bg_baz = float(rng.uniform(0, 360))
    bg = np.cumsum(rng.normal(0, 0.02, n)); bg -= np.linspace(bg[0], bg[-1], n); bg = noise * bg / (bg.std() + 1e-12)
    data = np.zeros((len(sensors), n))
    F = np.fft.rfftfreq(n, 1 / fs)
    BG = np.fft.rfft(bg)
    sources: List[Source] = []
    lines: Dict[str, List[float]] = {}
    u_bg = np.array([math.sin(math.radians(bg_baz)), math.cos(math.radians(bg_baz))])
    for k, s in enumerate(sensors):
        tau = -(xy[k] @ u_bg) / 0.4
        data[k] += np.fft.irfft(BG * np.exp(-2j * np.pi * F * tau), n) + sensor_noise * rng.normal(0, 1, n)
    for k, d in enumerate(distances_km):
        baz = (40.0 + 95.0 * k) % 360.0                                     # bearings spread round the compass
        lat, lon = xy_to_latlon(d * math.sin(math.radians(baz)), d * math.cos(math.radians(baz)), lat0, lon0)
        s0 = t0 + (2 * k + 1) * 3600.0
        src = Source(f'RIG-{k + 1}', lat, lon, s0, s0 + 3600.0, 'rig', 'synthetic array scene')
        sources.append(src)
        pump = 1.5 + 0.3 * k
        comps = [(pump, 1.0), (2 * pump, 0.6), (3 * pump, 0.4), (4 * pump, 0.25), (6 * pump, 0.15), (18.0 + 1.5 * k, 0.5)]
        comps = [c for c in comps if c[0] < 0.8 * fs / 2]
        lines[src.source_id] = [round(c[0], 3) for c in comps]
        m = (t + t0 >= s0) & (t + t0 < s0 + 3600.0)
        u = np.array([math.sin(math.radians(baz)), math.cos(math.radians(baz))])
        sig = np.zeros(n)
        for f_hz, rel in comps:
            amp = 6000.0 * rel / d * math.exp(-math.pi * f_hz * d / (q_factor * v_km_s))
            sig[m] += amp * np.sin(2 * np.pi * f_hz * t[m] + rng.uniform(0, 2 * np.pi))
        # a slow amplitude modulation, so an envelope exists (machinery is never perfectly steady)
        sig *= 1.0 + 0.3 * np.sin(2 * np.pi * 0.05 * t + rng.uniform(0, 2 * np.pi))
        S = np.fft.rfft(sig)
        for j in range(len(sensors)):
            tau = -(xy[j] @ u) / v_km_s                                        # nearer the source arrives earlier
            data[j] += np.fft.irfft(S * np.exp(-2j * np.pi * F * tau), n)
    traces = [Trace('XX', s.sensor_id, '', 'HHZ', t0, fs, np.round(data[k]).astype(np.int32), source='synthetic_array_scene', encoding='SYNTHETIC')
              for k, s in enumerate(sensors)]
    meta = {'status': 'SIMULATION_SELF_TEST', 'v_km_s': v_km_s, 'q_factor': q_factor, 'spreading': '1/r', 'background_back_azimuth_deg': bg_baz,
            'true_bearings_deg': {s.source_id: round(bearing_deg(lat0, lon0, s.lat, s.lon), 2) for s in sources}, 'lines_hz': lines,
            'aperture_km': round(geo['aperture_km'], 4), 'note': 'the scene is an assumption for checking the arithmetic; it says nothing about any ground'}
    return traces, sensors, sources, meta


def selftest(seed: int = 5) -> dict:
    """SIMULATION_SELF_TEST: the array points at every rig it can hear, the far one is incoherent or not pointed,
    and two more arrays' bearings cross at the rig."""
    traces, sensors, sources, meta = synthetic_array_scene(seed)
    res = array_detectability(traces, sensors, sources, band=(1.0, 20.0), win_s=600.0)
    verdicts = {r['source_id']: r['verdict'] for r in res['sources']}
    # three arrays' bearings at the first rig: this array's beam plus two synthetic bearings with 2 deg sigma from other sites
    rig = sources[0]
    a0 = res['sources'][0]
    arrays = [{'lat': res['array']['centre_lat'], 'lon': res['array']['centre_lon'], 'back_azimuth_deg': a0.get('beam_back_azimuth_deg', a0['true_back_azimuth_deg']), 'sigma_deg': max(a0.get('tolerance_deg', 2.0), 0.5)}]
    rng = np.random.default_rng(seed + 1)
    for dlat, dlon in ((0.15, 0.10), (-0.08, 0.18)):
        la, lo = rig.lat + dlat, rig.lon + dlon
        arrays.append({'lat': la, 'lon': lo, 'back_azimuth_deg': bearing_deg(la, lo, rig.lat, rig.lon) + float(rng.normal(0, 1.0)), 'sigma_deg': 2.0})
    cross = intersect_backazimuths(arrays)
    miss_km = haversine_km(cross['lat'], cross['lon'], rig.lat, rig.lon)
    ok = (verdicts.get('RIG-1') == 'POINTED' and verdicts.get('RIG-2') == 'POINTED' and verdicts.get('RIG-3') == 'POINTED'
          and cross['in_front_of_every_array'] and miss_km < 3.0 * max(cross['ellipse_1sigma']['major_km'], 0.3))
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'scene': meta, 'detectability': res, 'crossing': {**{k: v for k, v in cross.items()}, 'miss_km': round(miss_km, 3)}}
