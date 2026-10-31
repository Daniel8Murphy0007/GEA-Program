# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""bit_seismic - the image: the drilling itself as the seismic source, recorded from the ground around the rig.

The second leg so far locates a rig from the ground (seismic_array, seismic_track). While a well is being drilled
the program already knows where the bit is - the directional survey and the drilling record's depth against time
put it within feet every second - so the rig is a seismic source of KNOWN position, moving along the well. Every
station on the ground, paired with every bit position, is one ray through the rock between them; the rays
crossing one another at different angles resolve the velocity of the rock layer by layer, and the arrivals that
went down from the bit to a deeper layer and back up place that layer below the bit. This is drill-bit seismic
(seismic while drilling; Rector and Marion 1991; Poletto and Miranda 2004), done here with nothing on the rig: no
sensor on the string, no pilot signal. The station nearest the substructure is the reference, and every other
station is cross-correlated against it, which gives the DIFFERENCE in travel time between them with no knowledge
of what the bit's signal looked like (as ambient-noise interferometry works). Differences are enough: in a column
of rock a bit at depth sends its wave to a station 6,000 ft away later than to the station beside the rig by an
amount set by the velocities above it, and a column of such differences, over every bit depth the well passed,
determines those velocities (a check-shot survey turned inside out).

What this module does, end to end, on a labelled synthetic scene (SIMULATION_SELF_TEST, as the SAR film is):

1. the scene: a layered earth (velocities, densities, rock names), a well path from a directional survey (minimum
   curvature, as the 3D tab draws it), the bit advancing along it at a stated rate, a ground array (stations in
   rings around the pad, one reference beside the substructure), and the bit's signal as broadband noise the
   program does not know, arriving at each station delayed, spread and attenuated along the ray, with a reflection
   from the first layer below the well's deepest point, under ground noise of a stated level;
2. the correlation: window by window (every 50 ft drilled), each station against the reference; the peak of the
   correlation, read to a fraction of a sample, is the time difference; its precision is measured against the
   scene's own truth;
3. the velocity column: the differences inverted (Gauss-Newton, second-difference smoothing) for the velocity in
   cells of 100 ft from the surface to the deepest bit position, the rays traced through the layers by Snell's law;
   the recovered column is compared to the scene's cell by cell and interface by interface;
4. the reflector: the two-way arrival scanned over depth below the bit on the recovered velocities; the depth that
   stacks the correlations highest is the layer, compared to the scene's within a quarter wavelength;
5. the property column: for each recovered layer, the rocks of the inventory whose velocity range holds it, its
   density by Gardner's relation, the overburden it adds, and, for a reservoir rock, the porosity Wyllie's
   time-average gives - each marked as what a log would calibrate;
6. what a field recorder must capture for the real thing to work: sample rate, window, station count and spread,
   the reference station's place, the timing precision, the bit-depth log - every figure derived from this run.

Nothing here is a measurement of any ground. The scene says what the arithmetic recovers from a known earth under
stated noise; the resolution it states (a quarter wavelength at the band's dominant frequency) is the physics'
limit, not the program's promise. numpy only.

Sources: Rector, J. W. and Marion, B. P., "The use of drill-bit energy as a downhole seismic source", Geophysics
56(5), 1991; Poletto, F. and Miranda, F., Seismic While Drilling: Fundamentals of Drill-Bit Seismic for
Exploration, Elsevier 2004; Shapiro, N. M. and Campillo, M., "Emergence of broadband Rayleigh waves from
correlations of the ambient seismic noise", GRL 31, 2004 (interferometry); Dix, C. H., "Seismic velocities from
surface measurements", Geophysics 20, 1955; Gardner, Gardner and Gregory, Geophysics 39, 1974; Wyllie, Gregory and
Gardner, "Elastic wave velocities in heterogeneous and porous media", Geophysics 21, 1956.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

LABEL = 'SIMULATION_SELF_TEST'
FT_PER_KM = 3280.84
V_FLUID_FT_S = 5300.0                        # Wyllie's brine
V_MATRIX_FT_S = {'sandstone': 18000.0, 'limestone': 21000.0, 'dolomite': 23000.0}
REPORT_ID = 'SIMULATION'

# -- the labelled scene -------------------------------------------------------------------------------------------------

EXAMPLE_LAYERS: List[dict] = [
    {'top_ft': 0.0, 'vp_ft_s': 5500.0, 'rock': 'unconsolidated'},
    {'top_ft': 500.0, 'vp_ft_s': 8500.0, 'rock': 'shale'},
    {'top_ft': 1800.0, 'vp_ft_s': 13000.0, 'rock': 'limestone'},
    {'top_ft': 2400.0, 'vp_ft_s': 10500.0, 'rock': 'sandstone (the pay)'},
    {'top_ft': 2900.0, 'vp_ft_s': 9800.0, 'rock': 'shale'},
    {'top_ft': 3600.0, 'vp_ft_s': 14500.0, 'rock': 'limestone'},
]

# the example well: vertical to the kick-off, a 10 deg/100 ft build to horizontal, a 5,000 ft lateral due east,
# landed in the pay (the survey as a W-12 / directional survey lists it: MD, inclination, azimuth)
def example_survey() -> List[dict]:
    st = [{'md': 0.0, 'incl': 0.0, 'azi': 90.0}, {'md': 2077.0, 'incl': 0.0, 'azi': 90.0}]
    md, inc = 2077.0, 0.0
    while inc < 90.0:
        md += 100.0; inc = min(inc + 10.0, 90.0)
        st.append({'md': md, 'incl': inc, 'azi': 90.0})
    md_land = md
    for k in range(1, 11):
        st.append({'md': md_land + 500.0 * k, 'incl': 90.0, 'azi': 90.0})
    return st


def gardner_gcc(v_ft_s) -> np.ndarray:
    """Gardner, Gardner and Gregory 1974: rho = 0.23 V^0.25 (g/cm3, V in ft/s)."""
    return 0.23 * np.power(np.asarray(v_ft_s, dtype=float), 0.25)


def well_path(survey: Sequence[dict]) -> dict:
    """x east, y north, z down (ft) at each survey station, and along the path every 10 ft of MD, by minimum curvature."""
    from .well3d import min_curvature
    md = np.array([s['md'] for s in survey], float)
    inc = np.array([s['incl'] for s in survey], float)
    azi = np.array([s['azi'] for s in survey], float)
    mc = min_curvature(md, inc, azi)
    fine = np.arange(0.0, md[-1] + 1e-9, 10.0)
    return {'md': md, 'x': mc['e'], 'y': mc['n'], 'z': mc['v'],
            'fine_md': fine, 'fine_x': np.interp(fine, md, mc['e']), 'fine_y': np.interp(fine, md, mc['n']), 'fine_z': np.interp(fine, md, mc['v'])}


def bit_positions(path: dict, step_md_ft: float = 50.0, first_md_ft: float = 100.0) -> np.ndarray:
    """The bit's position at the middle of each window: one window per `step_md_ft` drilled. Rows: md, x, y, z."""
    mds = np.arange(first_md_ft, path['md'][-1] + 1e-9, step_md_ft)
    return np.column_stack([mds, np.interp(mds, path['fine_md'], path['fine_x']), np.interp(mds, path['fine_md'], path['fine_y']),
                            np.interp(mds, path['fine_md'], path['fine_z'])])


def ring_array(n_stations: int = 24, radius_ft: float = 6000.0, reference_offset_ft: float = 40.0, rings: int = 3, seed: int = 0) -> np.ndarray:
    """Stations on `rings` rings around the pad, the reference (row 0) beside the substructure. Rows: x, y (ft)."""
    rng = np.random.default_rng(seed)
    pts = [(reference_offset_ft, 0.0)]
    per = max(1, (n_stations - 1) // rings)
    k = 1
    for r in range(1, rings + 1):
        rad = radius_ft * r / rings
        n_here = per if r < rings else n_stations - 1 - per * (rings - 1)
        for j in range(n_here):
            ang = 2 * math.pi * (j + 0.5 * (r % 2)) / max(n_here, 1) + rng.uniform(-0.05, 0.05)
            pts.append((rad * math.sin(ang), rad * math.cos(ang)))
            k += 1
    return np.array(pts[:n_stations], float)


# -- rays in a layered earth ---------------------------------------------------------------------------------------------

def _stack_from_column(tops: np.ndarray, v: np.ndarray, z_from: float, z_to: float) -> Tuple[np.ndarray, np.ndarray]:
    """The segments (thickness, velocity) a vertical interval [z_from, z_to] crosses in a column of layers with `tops`."""
    z0, z1 = min(z_from, z_to), max(z_from, z_to)
    bots = np.append(tops[1:], np.inf)
    lo = np.maximum(tops, z0); hi = np.minimum(bots, z1)
    h = np.clip(hi - lo, 0.0, None)
    return h, v


def snell_times(H: np.ndarray, V: np.ndarray, X: np.ndarray, iters: int = 40, lengths: bool = False):
    """Travel time of the least-time ray through a stack of flat layers, for each ray: H, V of shape (n_rays, n_seg)
    (thickness ft, velocity ft/s; zero-thickness segments allowed) and horizontal offset X (n_rays). Snell's law: the
    ray parameter p that makes the offset come out at X, found by bisection (the offset grows with p). With `lengths`,
    also the path length in each segment (what the time's derivative against each segment's slowness is, by Fermat)."""
    H = np.asarray(H, float); V = np.asarray(V, float); X = np.abs(np.asarray(X, float))
    used = H > 0
    vmax = np.max(np.where(used, V, 0.0), axis=1)
    vmax = np.where(vmax > 0, vmax, 1.0)
    lo = np.zeros(len(X)); hi = (1.0 - 1e-9) / vmax
    for _ in range(iters):
        p = 0.5 * (lo + hi)
        s = np.sqrt(np.clip(1.0 - (p[:, None] * V) ** 2, 1e-12, None))
        x = np.sum(H * p[:, None] * V / s, axis=1)
        more = x < X
        lo = np.where(more, p, lo); hi = np.where(more, hi, p)
    p = 0.5 * (lo + hi)
    s = np.sqrt(np.clip(1.0 - (p[:, None] * V) ** 2, 1e-12, None))
    if lengths:
        return np.sum(H / (V * s), axis=1), H / s
    return np.sum(H / (V * s), axis=1)


def direct_times(tops: np.ndarray, v: np.ndarray, stations: np.ndarray, bits: np.ndarray) -> np.ndarray:
    """Direct-wave travel time (s) from each bit position (rows md,x,y,z) to each station (rows x,y): shape (n_bits, n_stations)."""
    nb, ns = len(bits), len(stations)
    H = np.zeros((nb, len(tops))); Vv = np.tile(v, (nb, 1))
    for i in range(nb):
        H[i], _ = _stack_from_column(tops, v, 0.0, float(bits[i, 3]))
    X = np.hypot(bits[:, 1][:, None] - stations[:, 0][None, :], bits[:, 2][:, None] - stations[:, 1][None, :])
    Hr = np.repeat(H, ns, axis=0); Vr = np.repeat(Vv, ns, axis=0)
    return snell_times(Hr, Vr, X.ravel()).reshape(nb, ns)


def reflection_times(tops: np.ndarray, v: np.ndarray, stations: np.ndarray, bits: np.ndarray, z_refl: float) -> np.ndarray:
    """Two-way time: bit down to a flat reflector at z_refl and up to the station (the mirrored source): (n_bits, n_stations)."""
    nb, ns = len(bits), len(stations)
    nseg = 2 * len(tops)
    H = np.zeros((nb, nseg)); Vv = np.zeros((nb, nseg))
    for i in range(nb):
        h1, _ = _stack_from_column(tops, v, 0.0, z_refl)              # up from the reflector to the surface
        h2, _ = _stack_from_column(tops, v, float(bits[i, 3]), z_refl)  # down from the bit to the reflector
        H[i] = np.concatenate([h1, h2]); Vv[i] = np.concatenate([v, v])
    X = np.hypot(bits[:, 1][:, None] - stations[:, 0][None, :], bits[:, 2][:, None] - stations[:, 1][None, :])
    return snell_times(np.repeat(H, ns, axis=0), np.repeat(Vv, ns, axis=0), X.ravel()).reshape(nb, ns)


def ray_lengths(stations: np.ndarray, bits: np.ndarray) -> np.ndarray:
    return np.sqrt((bits[:, 1][:, None] - stations[:, 0][None, :]) ** 2 + (bits[:, 2][:, None] - stations[:, 1][None, :]) ** 2 + bits[:, 3][:, None] ** 2)


# -- the synthetic records and their correlation --------------------------------------------------------------------------

def _band_mask(F: np.ndarray, band: Tuple[float, float]) -> np.ndarray:
    lo, hi = band
    m = ((F >= lo) & (F <= hi)).astype(float)
    # soft edges, a tenth of the band each side, so the correlation peak is clean
    w = 0.1 * (hi - lo)
    m = np.where((F > lo - w) & (F < lo), 0.5 * (1 - np.cos(np.pi * (F - (lo - w)) / w)), m)
    m = np.where((F > hi) & (F < hi + w), 0.5 * (1 + np.cos(np.pi * (F - hi) / w)), m)
    return m


def correlate_scene(tops: np.ndarray, v: np.ndarray, rho: np.ndarray, stations: np.ndarray, bits: np.ndarray, z_refl: Optional[float],
                    fs: float = 200.0, window_s: float = 40.0, band: Tuple[float, float] = (8.0, 80.0), noise: float = 1.0, q_factor: float = 80.0,
                    amp_ref: float = 50.0, seed: int = 1, max_lag_s: float = 2.5) -> dict:
    """The scene's records, window by window, correlated against the reference station (row 0) at once - the records
    themselves are never kept. The bit's signal in each window is broadband noise the processing never sees; each
    station receives it delayed by the direct travel time, scaled by 1/r and exp(-pi f r / (Q v)), plus the reflection
    from z_refl (its coefficient from the densities and velocities across it), plus white ground noise of std `noise`
    (amp_ref is the direct arrival's amplitude at the reference). Returns the correlations (n_bits, n_stations, n_lags),
    the lag axis, the picked time differences and the truth."""
    rng = np.random.default_rng(seed)
    n = int(round(window_s * fs)); nfft = 2 * n
    F = np.fft.rfftfreq(nfft, 1.0 / fs)
    mask = _band_mask(F, band)
    t_dir = direct_times(tops, v, stations, bits)
    r_dir = ray_lengths(stations, bits)
    t_ref = t_dir[:, 0]
    have_refl = z_refl is not None
    if have_refl:
        t_refl = reflection_times(tops, v, stations, bits, float(z_refl))
        k = int(np.searchsorted(tops, z_refl, side='right') - 1)
        k = min(max(k, 0), len(tops) - 2)
        z1, z2 = rho[k] * v[k], rho[k + 1] * v[k + 1]
        r_coef = (z2 - z1) / (z2 + z1)
        r_refl = r_dir + 2.0 * np.clip(z_refl - bits[:, 3], 0.0, None)[:, None]
    else:
        t_refl = None; r_coef = 0.0; r_refl = None
    v_mean = float(np.average(v[:-1], weights=np.diff(tops)) if len(tops) > 1 else v[0])
    nlag = int(round(max_lag_s * fs))
    lags = np.arange(-nlag, nlag + 1) / fs
    nb, ns = t_dir.shape
    corr = np.zeros((nb, ns, 2 * nlag + 1), dtype=np.float32)
    r0 = float(r_dir[0, 0])
    for i in range(nb):
        src = rng.normal(0.0, 1.0, nfft)
        S = np.fft.rfft(src) * mask
        att = np.exp(-np.pi * F[None, :] * r_dir[i][:, None] / (q_factor * v_mean))
        amp = amp_ref * r0 / np.maximum(r_dir[i], 1.0)
        rec = S[None, :] * amp[:, None] * att * np.exp(-2j * np.pi * F[None, :] * t_dir[i][:, None])
        if have_refl:
            att2 = np.exp(-np.pi * F[None, :] * r_refl[i][:, None] / (q_factor * v_mean))
            amp2 = amp_ref * r0 * r_coef / np.maximum(r_refl[i], 1.0)
            rec = rec + S[None, :] * amp2[:, None] * att2 * np.exp(-2j * np.pi * F[None, :] * t_refl[i][:, None])
        x = np.fft.irfft(rec, nfft)[:, :n] + noise * rng.normal(0.0, 1.0, (ns, n))
        X = np.fft.rfft(x, nfft)
        c = np.fft.irfft(X * np.conj(X[0:1]), nfft)         # station against the reference: positive lag = station later
        c = np.concatenate([c[:, -nlag:], c[:, :nlag + 1]], axis=1)
        corr[i] = c / (np.sqrt(np.sum(x * x, axis=1) * np.sum(x[0] * x[0]))[:, None] + 1e-12)
    picks = pick_peaks(corr, lags, fs)
    truth = t_dir - t_ref[:, None]
    return {'corr': corr, 'lags': lags, 'fs': fs, 'picks': picks, 'truth_dt': truth, 't_dir': t_dir, 't_refl': t_refl, 'r_coef': float(r_coef),
            'band': band, 'window_s': window_s, 'noise': noise, 'amp_ref': amp_ref, 'q_factor': q_factor}


def pick_peaks(corr: np.ndarray, lags: np.ndarray, fs: float) -> dict:
    """The lag of each correlation's highest peak, read to a fraction of a sample by a parabola through the three
    samples around it, and the peak's height against the correlation's own noise (its RMS away from the peak)."""
    nb, ns, nl = corr.shape
    k = np.argmax(corr, axis=2)
    k = np.clip(k, 1, nl - 2)
    ii = np.arange(nb)[:, None]; jj = np.arange(ns)[None, :]
    ym, y0, yp = corr[ii, jj, k - 1], corr[ii, jj, k], corr[ii, jj, k + 1]
    den = ym - 2 * y0 + yp
    frac = np.where(np.abs(den) > 1e-12, 0.5 * (ym - yp) / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    dt = lags[k] + frac / fs
    rms = np.sqrt(np.mean(corr ** 2, axis=2)) + 1e-12
    snr = y0 / rms
    return {'dt': dt, 'snr': snr, 'peak': y0}


# -- the velocity column from the time differences --------------------------------------------------------------------------

def cell_tops(z_max: float, dz: float = 100.0) -> np.ndarray:
    return np.arange(0.0, z_max + dz - 1e-9, dz)[: int(math.ceil(z_max / dz))]


def invert_column(stations: np.ndarray, bits: np.ndarray, dt: np.ndarray, weights: np.ndarray, dz: float = 100.0, v0: float = 9000.0,
                  smooth: float = 0.03, damp: float = 0.003, iters: int = 14) -> dict:
    """The velocity of each cell of `dz` ft from the surface to the deepest bit position, from the time differences
    dt[bit, station] = t(station) - t(reference) (reference = station 0), weighted. Gauss-Newton in slowness: at each
    step the rays are traced through the current column (Snell), the derivative of each difference against each
    cell's slowness is the ray's path length in that cell (Fermat), and the update solves the weighted least squares
    with a second-difference smoothing of weight `smooth` and a damping of `damp` towards the start (both relative
    to the data's scale). Returns the cells, their velocities, the fit and the path through the iterations."""
    z_max = float(np.max(bits[:, 3]))
    tops = cell_tops(z_max, dz)
    nc = len(tops)
    slow = np.full(nc, 1.0 / v0)
    nb, ns = dt.shape
    X = np.hypot(bits[:, 1][:, None] - stations[:, 0][None, :], bits[:, 2][:, None] - stations[:, 1][None, :])
    Hfull = np.zeros((nb, nc))
    for i in range(nb):
        Hfull[i], _ = _stack_from_column(tops, np.ones(nc), 0.0, float(bits[i, 3]))
    Hr = np.repeat(Hfull, ns, axis=0)
    w = np.sqrt(np.clip(weights, 0.0, None)).ravel()
    use = w > 0
    L2 = np.zeros((max(nc - 2, 0), nc))
    for i in range(nc - 2):
        L2[i, i:i + 3] = [1.0, -2.0, 1.0]
    history = []
    for it in range(iters):
        V = np.repeat((1.0 / slow)[None, :], nb * ns, axis=0)
        T, Lseg = snell_times(Hr, V, X.ravel(), lengths=True)
        T = T.reshape(nb, ns); Lseg = Lseg.reshape(nb, ns, nc)
        pred = T - T[:, 0:1]
        J = (Lseg - Lseg[:, 0:1, :]).reshape(nb * ns, nc)
        r = (dt - pred).ravel()
        rw = (w * r)[use]; Jw = (w[:, None] * J)[use]
        scale = np.sqrt(np.mean(Jw ** 2)) * math.sqrt(len(rw)) + 1e-12
        lam = smooth * scale; mu = damp * scale
        A = Jw.T @ Jw + lam ** 2 * (L2.T @ L2) + mu ** 2 * np.eye(nc)
        b = Jw.T @ rw - lam ** 2 * (L2.T @ (L2 @ slow))
        step = np.linalg.solve(A, b)
        rms = float(np.sqrt(np.mean(rw ** 2)))
        # the step is taken whole when it lowers the misfit, else halved until it does (a Gauss-Newton step on a
        # curved problem can overshoot; the misfit never rises from one iteration to the next)
        frac = 1.0
        for _ in range(6):
            trial = np.clip(slow + frac * step, 1.0 / 25000.0, 1.0 / 2500.0)
            Vt = np.repeat((1.0 / trial)[None, :], nb * ns, axis=0)
            Tt = snell_times(Hr, Vt, X.ravel()).reshape(nb, ns)
            rt = ((dt - (Tt - Tt[:, 0:1])).ravel() * w)[use]
            rms_t = float(np.sqrt(np.mean(rt ** 2)))
            if rms_t <= rms * 1.0001 or frac < 0.05:
                break
            frac *= 0.5
        slow = trial
        history.append({'iteration': it + 1, 'rms_ms': round(rms_t * 1000.0, 3), 'step': frac})
        if it > 2 and abs(history[-2]['rms_ms'] - rms_t * 1000.0) < 1e-3:
            break
    V = np.repeat((1.0 / slow)[None, :], nb * ns, axis=0)
    T = snell_times(Hr, V, X.ravel()).reshape(nb, ns)
    r = ((dt - (T - T[:, 0:1])).ravel() * w)[use]
    return {'tops_ft': tops, 'dz_ft': dz, 'v_ft_s': 1.0 / slow, 'rms_ms': round(float(np.sqrt(np.mean(r ** 2))) * 1000.0, 3), 'n_used': int(use.sum()),
            'n_all': int(len(use)), 'history': history, 'z_max_ft': z_max}


def column_of(tops: np.ndarray, v: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """A layered column sampled at the middle of each cell."""
    mids = cells + 0.5 * (cells[1] - cells[0] if len(cells) > 1 else 100.0)
    idx = np.clip(np.searchsorted(tops, mids, side='right') - 1, 0, len(tops) - 1)
    return v[idx]


def interfaces(cells: np.ndarray, v: np.ndarray, min_jump: float = 0.08, trust_to_ft: Optional[float] = None) -> List[dict]:
    """Where the recovered column changes by more than `min_jump` (fractional) between neighbouring cells: a layer
    boundary at the cells' common edge, the velocities either side. A boundary deeper than `trust_to_ft` (the deepest
    bit position less half a cell: the last cell is crossed only in part) is not read."""
    out = []
    for i in range(1, len(v)):
        a, b = v[i - 1], v[i]
        if trust_to_ft is not None and cells[i] > trust_to_ft:
            break
        if abs(b - a) / max(a, 1.0) >= min_jump:
            out.append({'z_ft': float(cells[i]), 'v_above_ft_s': float(a), 'v_below_ft_s': float(b), 'jump': round(float((b - a) / a), 3)})
    # merge neighbours of the same sign within one cell of each other: one interface, at the bigger jump
    merged: List[dict] = []
    for f in out:
        if merged and abs(f['z_ft'] - merged[-1]['z_ft']) <= (cells[1] - cells[0]) + 1e-9 and np.sign(f['jump']) == np.sign(merged[-1]['jump']):
            if abs(f['jump']) > abs(merged[-1]['jump']):
                merged[-1] = f
        else:
            merged.append(f)
    return merged


# -- the layer below the bit ---------------------------------------------------------------------------------------------

def envelope(c: np.ndarray) -> np.ndarray:
    """The analytic envelope along the last axis (Hilbert transform by FFT)."""
    n = c.shape[-1]
    C = np.fft.fft(c, axis=-1)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1.0; h[1:n // 2] = 2.0
    else:
        h[0] = 1.0; h[1:(n + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(C * h, axis=-1))


def scan_reflector(corr: np.ndarray, lags: np.ndarray, stations: np.ndarray, bits: np.ndarray, column: dict, weights: np.ndarray,
                   below_min_ft: float = 100.0, below_max_ft: float = 2500.0, step_ft: float = 10.0, min_separation_s: float = 0.06) -> dict:
    """The depth of the first layer below the well's deepest point: for each trial depth the two-way lag of the
    reflection at every station and bit position is predicted on the recovered column (the last cell's velocity
    continued below it, as stated) and the correlations' envelopes are stacked along it; the depth that stacks highest
    is the layer. Lags within `min_separation_s` of the direct arrival are left out of the stack, so the direct
    peak's own skirt cannot be mistaken for a layer just under the bit."""
    tops = np.asarray(column['tops_ft']); v = np.asarray(column['v_ft_s'])
    z_max = float(column['z_max_ft'])
    env = envelope(corr.astype(np.float64))
    fs = 1.0 / (lags[1] - lags[0])
    nb, ns, nl = corr.shape
    w = np.clip(weights, 0.0, None); w[:, 0] = 0.0
    tops_ext = np.append(tops, z_max); v_ext = np.append(v, v[-1])
    t_dir = direct_times(tops_ext, v_ext, stations, bits)
    direct_lag = t_dir - t_dir[:, 0:1]
    ii = np.arange(nb)[:, None]; jj = np.arange(ns)[None, :]

    def stack_at(D: float) -> float:
        t_r = reflection_times(tops_ext, v_ext, stations, bits, float(D))
        lag = t_r - t_dir[:, 0:1]
        ok = (lag - direct_lag > min_separation_s) & (lag < lags[-1]) & (w > 0)
        idx = np.clip(np.round((lag - lags[0]) * fs).astype(int), 0, nl - 1)
        return float(np.sum(env[ii, jj, idx] * w * ok) / max(np.sum(w * ok), 1.0))
    # a coarse scan over the whole range, then a fine one around its peak
    coarse = np.arange(z_max + below_min_ft, z_max + below_max_ft + 1e-9, 5.0 * step_ft)
    sc = np.array([stack_at(D) for D in coarse])
    kc = int(np.argmax(sc))
    fine = np.arange(coarse[max(kc - 1, 0)], coarse[min(kc + 1, len(coarse) - 1)] + 1e-9, step_ft)
    sf = np.array([stack_at(D) for D in fine])
    depths = np.concatenate([coarse, fine]); stack = np.concatenate([sc, sf])
    order = np.argsort(depths); depths, stack = depths[order], stack[order]
    best = int(np.argmax(stack))
    noise_floor = float(np.median(sc))
    return {'depths_ft': depths, 'stack': stack, 'z_ft': float(depths[best]), 'peak': float(stack[best]), 'floor': noise_floor,
            'contrast': round(float(stack[best] / max(noise_floor, 1e-12)), 2), 'v_assumed_below_ft_s': float(v[-1])}


# -- the property column ------------------------------------------------------------------------------------------------

def rocks_for_velocity(v_ft_s: float) -> List[str]:
    """The rocks of the inventory whose compressional-velocity range holds this velocity (rocks, not minerals or fluids)."""
    from . import rock_inventory as RI
    v_km = v_ft_s / FT_PER_KM
    out = []
    for r in RI.table():
        if r.get('kind') != 'rock' or not r.get('vp_range_km_s'):
            continue
        lo, hi = r['vp_range_km_s']
        if lo <= v_km <= hi:
            out.append(r['name'])
    return out


def wyllie_porosity(v_ft_s: float, matrix: str) -> Optional[float]:
    """Wyllie's time average: 1/V = phi/Vf + (1 - phi)/Vm, for a stated matrix; None when the velocity is above the matrix's."""
    vm = V_MATRIX_FT_S.get(matrix)
    if not vm or v_ft_s >= vm:
        return 0.0 if vm and v_ft_s >= vm else None
    phi = (1.0 / v_ft_s - 1.0 / vm) / (1.0 / V_FLUID_FT_S - 1.0 / vm)
    return None if phi > 0.45 else float(max(phi, 0.0))          # above 45% the rock is not that matrix at this velocity


def layers_from_column(cells: np.ndarray, v: np.ndarray, ifcs: List[dict]) -> List[dict]:
    """The recovered column cut at its interfaces into layers: top, base, mean velocity, Gardner density, the overburden
    each adds (psi), the rocks that velocity fits, and the time-average porosity for the reservoir rocks among them."""
    dz = float(cells[1] - cells[0]) if len(cells) > 1 else 100.0
    edges = [0.0] + [f['z_ft'] for f in ifcs] + [float(cells[-1] + dz)]
    out = []
    ob = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (cells >= a - 1e-9) & (cells < b - 1e-9)
        if not sel.any():
            continue
        vm = float(np.mean(v[sel]))
        rho = float(gardner_gcc(vm))
        add = 0.433 * rho * (b - a)                 # psi: 0.433 psi/ft per unit specific gravity
        ob += add
        rocks = rocks_for_velocity(vm)
        por = {m: wyllie_porosity(vm, m) for m in ('sandstone', 'limestone', 'dolomite') if m in rocks}
        out.append({'top_ft': float(a), 'base_ft': float(b), 'thickness_ft': float(b - a), 'v_ft_s': round(vm), 'rho_gcc': round(rho, 2),
                    'overburden_add_psi': round(add), 'overburden_base_psi': round(ob), 'hydrostatic_base_psi': round(0.433 * b),
                    'rocks': rocks, 'porosity': {k: (round(p_, 3) if p_ is not None else None) for k, p_ in por.items()}})
    return out


def recorder_spec(res: dict) -> List[dict]:
    """What a field recorder must capture for the real thing to do what this run did, each figure from the run."""
    band = res['band_hz']; fs = res['fs_hz']
    prec_ms = res['pick_precision_ms']
    far = res['farthest_usable_ft']
    return [
        {'item': 'Ground stations', 'value': f"{res['n_stations'] - 1} plus the reference", 'why': f"this run; usable rays reached {far:,.0f} ft at noise {res['noise']:g}"},
        {'item': 'Spread', 'value': f"{res['radius_ft']:,.0f} ft radius, {res['rings']} rings", 'why': 'rays crossing at different angles resolve the layers; the spread about twice the depth of interest'},
        {'item': 'Reference station', 'value': f"within {res['reference_offset_ft']:,.0f} ft of the substructure", 'why': 'every other station is correlated against it; the bit\'s own signal is never needed'},
        {'item': 'Sample rate', 'value': f"{fs:g} samples/s (at least {4 * band[1]:g})", 'why': f"the band used is {band[0]:g}-{band[1]:g} Hz; four samples per cycle at the top of it"},
        {'item': 'Window', 'value': f"{res['window_s']:g} s per {res['step_md_ft']:g} ft drilled, continuous recording", 'why': 'the correlation gains as the square root of the window; the bit must not move far inside one'},
        {'item': 'Timing', 'value': f"GPS-disciplined, pulse-per-second to every channel, better than {max(prec_ms / 2.0, 0.1):.1f} ms between stations",
         'why': f"the picks here are good to {prec_ms:.2f} ms; a clock error becomes a velocity error"},
        {'item': 'Bit depth against time', 'value': '1 sample/s from the drilling recorder, on the same clock', 'why': 'the source position for every window; the survey gives the path'},
        {'item': 'Sensors', 'value': 'vertical geophones (4.5-10 Hz) or short-period seismometers, buried', 'why': f"the band is above {band[0]:g} Hz; a geophone's own frequency below it"},
        {'item': 'Files', 'value': 'miniSEED, one file per station per hour, SHA-256 beside each', 'why': "the Seismic tab reads miniSEED as it is; the dossier carries the hashes"},
        {'item': 'Dynamic range', 'value': '24-bit', 'why': 'the reference beside the rig and a station 6,000 ft away differ by a factor of a hundred or more'},
    ]


# -- the run, end to end --------------------------------------------------------------------------------------------------

def run_scene(n_stations: int = 24, radius_ft: float = 6000.0, noise: float = 1.0, seed: int = 1, survey: Optional[Sequence[dict]] = None,
              layers: Optional[Sequence[dict]] = None, step_md_ft: float = 50.0, fs: float = 200.0, window_s: float = 40.0,
              band: Tuple[float, float] = (8.0, 80.0), snr_min: float = 6.0, rings: int = 3, reference_offset_ft: float = 40.0,
              dz_ft: float = 100.0, keep_arrays: bool = False) -> dict:
    """The labelled scene end to end: scene, correlation, column, reflector, properties, recorder; every verdict against
    the scene's own truth. Returns a JSON-ready result (and, with keep_arrays, the correlations for drawing)."""
    layers = list(layers or EXAMPLE_LAYERS)
    survey = list(survey or example_survey())
    tops = np.array([l['top_ft'] for l in layers], float)
    v = np.array([l['vp_ft_s'] for l in layers], float)
    rho = np.array([l.get('rho_gcc') or float(gardner_gcc(l['vp_ft_s'])) for l in layers], float)
    path = well_path(survey)
    bits = bit_positions(path, step_md_ft)
    z_max = float(np.max(bits[:, 3]))
    stations = ring_array(n_stations, radius_ft, reference_offset_ft, rings, seed)
    # the first layer boundary below the well's deepest point is the reflector the scene carries
    below = [t for t in tops if t > z_max + 1.0]
    z_refl = float(below[0]) if below else None
    sc = correlate_scene(tops, v, rho, stations, bits, z_refl, fs=fs, window_s=window_s, band=band, noise=noise, seed=seed)
    picks = sc['picks']
    usable = picks['snr'] >= snr_min
    usable[:, 0] = False
    err_ms = (picks['dt'] - sc['truth_dt']) * 1000.0
    good = usable & (np.abs(err_ms) < 5.0)
    prec = float(np.sqrt(np.mean(err_ms[good] ** 2))) if good.any() else float('nan')
    wrong = int(np.sum(usable & ~good))
    rl = ray_lengths(stations, bits)
    farthest = float(np.max(rl[usable])) if usable.any() else 0.0
    weights = usable.astype(float)
    column = invert_column(stations, bits, picks['dt'], weights, dz=dz_ft)
    truth_cells = column_of(tops, v, column['tops_ft'])
    cell_err = (column['v_ft_s'] - truth_cells) / truth_cells
    ifcs = interfaces(column['tops_ft'], column['v_ft_s'], trust_to_ft=z_max - 0.5 * dz_ft)
    true_ifcs = [float(t) for t in tops[1:] if t <= z_max - 0.5 * dz_ft]
    matched = []
    for tz in true_ifcs:
        near = min(ifcs, key=lambda f: abs(f['z_ft'] - tz)) if ifcs else None
        matched.append({'true_ft': tz, 'found_ft': near['z_ft'] if near else None, 'error_ft': round(near['z_ft'] - tz) if near else None})
    refl = scan_reflector(sc['corr'], sc['lags'], stations, bits, column, weights) if z_refl is not None else None
    f_dom = 0.5 * (band[0] + band[1])
    v_last = float(column['v_ft_s'][-1])
    quarter_wave = v_last / f_dom / 4.0
    layers_out = layers_from_column(column['tops_ft'], column['v_ft_s'], ifcs)
    res = {
        'label': LABEL, 'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'params': {'n_stations': n_stations, 'radius_ft': radius_ft, 'noise': noise, 'seed': seed, 'step_md_ft': step_md_ft, 'fs_hz': fs, 'window_s': window_s,
                   'band_hz': list(band), 'snr_min': snr_min, 'rings': rings, 'reference_offset_ft': reference_offset_ft, 'dz_ft': dz_ft},
        'scene': {'layers': [{'top_ft': float(t), 'vp_ft_s': float(vv), 'rho_gcc': round(float(r), 2), 'rock': l.get('rock', '')} for t, vv, r, l in zip(tops, v, rho, layers)],
                  'survey_stations': len(survey), 'td_md_ft': float(path['md'][-1]), 'td_tvd_ft': z_max, 'lateral_end_x_ft': float(path['x'][-1]), 'lateral_end_y_ft': float(path['y'][-1]),
                  'windows': int(len(bits)), 'reflector_ft': z_refl, 'reflection_coefficient': round(sc['r_coef'], 3), 'q_factor': sc['q_factor'],
                  'note': 'a layered earth and a well path chosen to check the arithmetic; nothing in it is any ground'},
        'array': {'n_stations': int(len(stations)), 'radius_ft': radius_ft, 'rings': rings, 'reference_offset_ft': reference_offset_ft,
                  'stations': [{'id': 'REF' if i == 0 else f'S{i:02d}', 'x_ft': round(float(x)), 'y_ft': round(float(y))} for i, (x, y) in enumerate(stations)]},
        'correlation': {'windows': int(len(bits)), 'pairs': int(usable.size - len(bits)), 'usable': int(usable.sum()), 'wrong_picks': wrong,
                        'pick_precision_ms': round(prec, 3), 'farthest_usable_ft': round(farthest), 'snr_median': round(float(np.median(picks['snr'][:, 1:])), 1),
                        'per_station_usable': [int(usable[:, j].sum()) for j in range(len(stations))]},
        'column': {'tops_ft': [float(t) for t in column['tops_ft']], 'v_ft_s': [round(float(x)) for x in column['v_ft_s']], 'truth_ft_s': [float(x) for x in truth_cells],
                   'cell_error_rms_pct': round(float(100.0 * np.sqrt(np.mean(cell_err ** 2))), 2), 'cell_error_max_pct': round(float(100.0 * np.max(np.abs(cell_err))), 1),
                   'fit_rms_ms': column['rms_ms'], 'iterations': len(column['history']), 'interfaces': ifcs, 'interfaces_matched': matched, 'z_max_ft': z_max},
        'reflector': None if refl is None else {'true_ft': z_refl, 'found_ft': refl['z_ft'], 'error_ft': round(refl['z_ft'] - z_refl), 'contrast': refl['contrast'],
                                                 'quarter_wavelength_ft': round(quarter_wave), 'v_assumed_below_ft_s': round(refl['v_assumed_below_ft_s']),
                                                 'within_quarter_wavelength': bool(abs(refl['z_ft'] - z_refl) <= max(quarter_wave, 100.0)),
                                                 'depths_ft': [float(d) for d in refl['depths_ft']], 'stack': [round(float(x), 5) for x in refl['stack']]},
        'layers': layers_out,
        'resolution': {'dominant_hz': f_dom, 'wavelength_ft_at_pay': round(v_last / f_dom), 'quarter_wavelength_ft': round(quarter_wave),
                       'note': 'no number of stations resolves a layer thinner than a quarter wavelength of the band used; more stations lower the noise, not this limit'},
        'verdict': {'column': bool(100.0 * np.sqrt(np.mean(cell_err ** 2)) < 5.0 and all(m['error_ft'] is not None and abs(m['error_ft']) <= 100 for m in matched)),
                    'reflector': bool(refl is not None and abs(refl['z_ft'] - z_refl) <= max(quarter_wave, 100.0)),
                    'picks': bool(np.isfinite(prec) and prec < 2.0 and wrong <= 0.02 * max(usable.sum(), 1))},
    }
    extra = [f['z_ft'] for f in ifcs if all(abs(f['z_ft'] - t) > 100.0 for t in true_ifcs)]
    res['column']['extra_interfaces_ft'] = extra
    res['verdict']['all'] = all(res['verdict'].values()) and not extra
    res['recorder'] = recorder_spec({'band_hz': band, 'fs_hz': fs, 'pick_precision_ms': prec if np.isfinite(prec) else 2.0, 'farthest_usable_ft': farthest,
                                     'n_stations': len(stations), 'noise': noise, 'radius_ft': radius_ft, 'rings': rings, 'reference_offset_ft': reference_offset_ft,
                                     'window_s': window_s, 'step_md_ft': step_md_ft})
    res['path'] = {'md_ft': [round(float(m)) for m in path['md']], 'x_ft': [round(float(x)) for x in path['x']], 'z_ft': [round(float(z)) for z in path['z']]}
    res['moveout'] = _moveout_samples(stations, bits, picks['dt'], sc['truth_dt'], usable)
    if keep_arrays:
        res['_arrays'] = {'corr': sc['corr'], 'lags': sc['lags'], 'bits': bits, 'stations': stations}
    return res


def _moveout_samples(stations: np.ndarray, bits: np.ndarray, dt: np.ndarray, truth: np.ndarray, usable: np.ndarray, n_windows: int = 4) -> List[dict]:
    """A few windows' time differences against station offset, for the drawing: picked and true."""
    idx = np.linspace(0, len(bits) - 1, n_windows).round().astype(int)
    out = []
    for i in idx:
        off = np.hypot(stations[:, 0] - bits[i, 1], stations[:, 1] - bits[i, 2])
        rows = [{'offset_ft': round(float(off[j])), 'dt_ms': round(float(dt[i, j]) * 1000.0, 2), 'true_ms': round(float(truth[i, j]) * 1000.0, 2), 'usable': bool(usable[i, j])}
                for j in range(1, len(stations))]
        out.append({'window': int(i), 'md_ft': round(float(bits[i, 0])), 'z_ft': round(float(bits[i, 3])), 'x_ft': round(float(bits[i, 1])), 'rows': rows})
    return out


# -- drawings -------------------------------------------------------------------------------------------------------------

def _vcolor(v: float, lo: float = 5000.0, hi: float = 15000.0) -> str:
    t = min(max((v - lo) / (hi - lo), 0.0), 1.0)
    # pale sand to deep blue, readable on white and in print
    r = int(245 - 190 * t); g = int(222 - 150 * t); b = int(179 + 40 * t)
    return f'rgb({r},{g},{b})'


def svg_section(res: dict, title: str = 'The section along the well') -> str:
    """Left: the layers the scene used. Right: what the program recovered from the ground. The well path, the stations
    on the surface, the reflector found against the scene's. Vertical scale: depth below the pad."""
    W, H = 960, 520
    sc = res['scene']; col = res['column']
    x0, x1 = -1500.0, max(sc['lateral_end_x_ft'], 1000.0) + 1500.0
    zdeep = max((sc['reflector_ft'] or col['z_max_ft']) + 800.0, col['z_max_ft'] + 800.0)
    panels = [(40, 'The scene (what the program never sees)'), (500, 'Recovered from the ground stations')]
    pw, ph, py = 420, 400, 60
    g = [f'<text x="{W / 2}" y="22" text-anchor="middle" font-size="15" font-weight="600" fill="#1b2430">{title}</text>']

    def sx(px, x): return px + (x - x0) / (x1 - x0) * pw
    def sy(z): return py + z / zdeep * ph
    for px, label in panels:
        g.append(f'<text x="{px}" y="{py - 8}" font-size="12" fill="#333">{label}</text>')
        g.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="#fff" stroke="#999"/>')
    # left: the scene's layers
    L = sc['layers']
    for k, l in enumerate(L):
        zt = l['top_ft']; zb = L[k + 1]['top_ft'] if k + 1 < len(L) else zdeep
        zb = min(zb, zdeep)
        if zt >= zdeep:
            continue
        g.append(f'<rect x="{panels[0][0]}" y="{sy(zt):.1f}" width="{pw}" height="{max(sy(zb) - sy(zt), 0):.1f}" fill="{_vcolor(l["vp_ft_s"])}" stroke="#777" stroke-width="0.5"/>')
        g.append(f'<text x="{panels[0][0] + 6}" y="{sy(zt) + 13:.1f}" font-size="10" fill="#222">{l["rock"]} · {l["vp_ft_s"]:,.0f} ft/s</text>')
    # right: the recovered cells (full width), the layer below the bit as found
    tops = col['tops_ft']; dz = res['params']['dz_ft']
    for zt, vv in zip(tops, col['v_ft_s']):
        g.append(f'<rect x="{panels[1][0]}" y="{sy(zt):.1f}" width="{pw}" height="{max(sy(min(zt + dz, zdeep)) - sy(zt), 0):.1f}" fill="{_vcolor(vv)}" stroke="none"/>')
    for f in col['interfaces']:
        g.append(f'<line x1="{panels[1][0]}" x2="{panels[1][0] + pw}" y1="{sy(f["z_ft"]):.1f}" y2="{sy(f["z_ft"]):.1f}" stroke="#333" stroke-width="1"/>')
        g.append(f'<text x="{panels[1][0] + pw - 6}" y="{sy(f["z_ft"]) - 3:.1f}" font-size="10" text-anchor="end" fill="#222">{f["z_ft"]:,.0f} ft · {f["v_above_ft_s"]:,.0f} over {f["v_below_ft_s"]:,.0f}</text>')
    rf = res.get('reflector')
    if rf:
        g.append(f'<rect x="{panels[1][0]}" y="{sy(col["z_max_ft"]):.1f}" width="{pw}" height="{max(sy(zdeep) - sy(col["z_max_ft"]), 0):.1f}" fill="#f4f4f4"/>')
        g.append(f'<line x1="{panels[1][0]}" x2="{panels[1][0] + pw}" y1="{sy(rf["found_ft"]):.1f}" y2="{sy(rf["found_ft"]):.1f}" stroke="#b03030" stroke-width="2"/>')
        g.append(f'<line x1="{panels[1][0]}" x2="{panels[1][0] + pw}" y1="{sy(rf["true_ft"]):.1f}" y2="{sy(rf["true_ft"]):.1f}" stroke="#b03030" stroke-width="1" stroke-dasharray="4 3"/>')
        g.append(f'<text x="{panels[1][0] + 6}" y="{max(sy(rf["found_ft"]), sy(rf["true_ft"])) + 13:.1f}" font-size="10" fill="#b03030">layer below the bit: found {rf["found_ft"]:,.0f} ft (the scene\'s {rf["true_ft"]:,.0f}, dashed)</text>')
        g.append(f'<text x="{panels[1][0] + pw - 6}" y="{sy(zdeep) - 6:.1f}" font-size="10" text-anchor="end" fill="#666">below the deepest bit position the velocity is taken as the last cell\'s</text>')
    # the well path and stations on both panels
    P = res['path']
    for px, _ in panels:
        pts = ' '.join(f'{sx(px, x):.1f},{sy(z):.1f}' for x, z in zip(P['x_ft'], P['z_ft']) if z <= zdeep)
        g.append(f'<polyline points="{pts}" fill="none" stroke="#111" stroke-width="2"/>')
        for st in res['array']['stations']:
            x = st['x_ft']
            if x0 <= x <= x1:
                X = sx(px, x)
                g.append(f'<polygon points="{X - 4:.1f},{py} {X + 4:.1f},{py} {X:.1f},{py - 7}" fill="{"#b03030" if st["id"] == "REF" else "#1b6e3a"}"/>')
        for z in range(0, int(zdeep) + 1, 1000):
            g.append(f'<text x="{px - 4}" y="{sy(z) + 4:.1f}" font-size="9" text-anchor="end" fill="#555">{z:,}</text>')
        for xk in range(int(x0 // 2000 * 2000), int(x1) + 1, 2000):
            if x0 <= xk <= x1:
                g.append(f'<text x="{sx(px, xk):.1f}" y="{py + ph + 12}" font-size="9" text-anchor="middle" fill="#555">{xk:,}</text>')
    g.append(f'<text x="{W / 2}" y="{H - 24}" text-anchor="middle" font-size="10" fill="#555">east of the wellhead, ft (the stations projected onto the section; green stations, red reference) · depth below the pad, ft · colour: velocity, {_legend()}</text>')
    g.append(f'<text x="{W / 2}" y="{H - 8}" text-anchor="middle" font-size="10" fill="#888">{LABEL}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui, sans-serif">{"".join(g)}</svg>'


def _legend() -> str:
    return '5,000 ft/s pale to 15,000 ft/s blue'


def _ticks(lo: float, hi: float, n: int = 6) -> List[float]:
    span = max(hi - lo, 1e-9)
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), key=lambda m: m) * mag
    start = math.ceil(lo / step) * step
    return [start + k * step for k in range(int((hi - start) / step) + 1)]


def svg_moveout(res: dict) -> str:
    """The time differences against offset for a few windows: the picks (dots) and the scene's own (lines)."""
    W, H = 960, 360
    px, py, pw, ph = 60, 30, 700, 290
    M = res['moveout']
    offs = [r['offset_ft'] for m in M for r in m['rows']]; dts = [r['dt_ms'] for m in M for r in m['rows'] if r['usable']] + [r['true_ms'] for m in M for r in m['rows']]
    xmax = max(offs + [1.0]) * 1.05; ylo, yhi = min(dts + [0.0]), max(dts + [1.0])
    yr = max(yhi - ylo, 1.0); ylo -= 0.05 * yr; yhi += 0.05 * yr
    def sx(x): return px + x / xmax * pw
    def sy(y): return py + (yhi - y) / (yhi - ylo) * ph
    cols = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e']
    g = [f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="#fff" stroke="#999"/>']
    for k, m in enumerate(M):
        c = cols[k % len(cols)]
        rows = sorted(m['rows'], key=lambda r: r['offset_ft'])
        pts = ' '.join(f'{sx(r["offset_ft"]):.1f},{sy(r["true_ms"]):.1f}' for r in rows)
        g.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="1" opacity="0.6"/>')
        for r in rows:
            if r['usable']:
                g.append(f'<circle cx="{sx(r["offset_ft"]):.1f}" cy="{sy(r["dt_ms"]):.1f}" r="3" fill="{c}"/>')
            else:
                g.append(f'<circle cx="{sx(r["offset_ft"]):.1f}" cy="{sy(r["true_ms"]):.1f}" r="3" fill="none" stroke="{c}"/>')
        g.append(f'<rect x="{px + pw + 16}" y="{py + 10 + 18 * k}" width="10" height="10" fill="{c}"/><text x="{px + pw + 30}" y="{py + 19 + 18 * k}" font-size="10" fill="#333">bit at {m["md_ft"]:,} ft MD ({m["z_ft"]:,} ft deep)</text>')
    g.append(f'<text x="{px + pw + 16}" y="{py + 30 + 18 * len(M)}" font-size="10" fill="#555">dots: picked · lines: the scene\'s</text>')
    g.append(f'<text x="{px + pw + 16}" y="{py + 44 + 18 * len(M)}" font-size="10" fill="#555">open: below the SNR floor</text>')
    xs = _ticks(0.0, xmax); ys = _ticks(ylo, yhi)
    for t in xs:
        g.append(f'<text x="{sx(t):.1f}" y="{py + ph + 14}" font-size="9" text-anchor="middle" fill="#555">{t:,.0f}</text>')
    for t in ys:
        g.append(f'<text x="{px - 6}" y="{sy(t) + 3:.1f}" font-size="9" text-anchor="end" fill="#555">{t:,.0f}</text>')
        g.append(f'<line x1="{px}" x2="{px + pw}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="#eee"/>')
    g.append(f'<text x="{px + pw / 2}" y="{H - 10}" text-anchor="middle" font-size="10" fill="#555">offset from the bit, ft</text>')
    g.append(f'<text transform="translate(14,{py + ph / 2}) rotate(-90)" text-anchor="middle" font-size="10" fill="#555">later than the reference, ms</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui, sans-serif">{"".join(g)}</svg>'


# -- the report -----------------------------------------------------------------------------------------------------------

def build_report(res: dict, program_version: str):
    from .client_reports import Document, Section, Table, Figure, PROGRAM_NAME
    now = res['generated_utc']
    rid = f"BIT-{REPORT_ID}-{now.replace('-', '').replace(':', '')}"
    p, sc, co, cr, rf, vd = res['params'], res['scene'], res['column'], res['correlation'], res.get('reflector'), res['verdict']
    front = [['Report ID', rid], ['Label', LABEL], ['Scene', f"{len(sc['layers'])} layers, a well to {sc['td_md_ft']:,.0f} ft MD ({sc['td_tvd_ft']:,.0f} ft TVD), {sc['windows']} windows"],
             ['Array', f"{res['array']['n_stations']} stations, {p['radius_ft']:,.0f} ft radius, noise {p['noise']:g}"], ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}']]
    secs = []
    secs.append(Section('1', 'The scene', [
        'The drilling itself is the seismic source: while the well is drilled the bit\'s position is known from the directional survey and the '
        'depth against time, so every station on the ground paired with every bit position is one ray through the rock between them. '
        'Nothing is on the rig; the station beside the substructure is the reference and every other station is correlated against it, which '
        'gives the difference in arrival time with no knowledge of the bit\'s own signal.',
        f"This scene: a layered earth the program never sees, a well vertical to the kick-off, built to horizontal and run {sc['lateral_end_x_ft']:,.0f} ft east "
        f"in the pay, the bit advancing {p['step_md_ft']:g} ft per window; {res['array']['n_stations'] - 1} stations in {p['rings']} rings to {p['radius_ft']:,.0f} ft "
        f"plus the reference {p['reference_offset_ft']:,.0f} ft from the wellhead; the bit's signal broadband noise in {p['band_hz'][0]:g}-{p['band_hz'][1]:g} Hz, "
        f"spread as 1/r and attenuated with Q = {sc['q_factor']:g}; ground noise of {p['noise']:g} against a reference arrival of 50; a reflection from the first layer "
        f"below the well's deepest point (coefficient {sc['reflection_coefficient']:.2f}). {sc['note'].capitalize()}."],
        [Table(['Top (ft)', 'Velocity (ft/s)', 'Density (g/cm3, Gardner)', 'Rock'], [[l['top_ft'], l['vp_ft_s'], l['rho_gcc'], l['rock']] for l in sc['layers']])]))
    secs.append(Section('2', 'The correlation', [
        f"{cr['windows']} windows, {cr['pairs']} station-window pairs; {cr['usable']} above the SNR floor of {p['snr_min']:g} (median SNR {cr['snr_median']}), "
        f"{cr['wrong_picks']} of those more than 5 ms from the scene's own time. The usable picks are good to {cr['pick_precision_ms']:.2f} ms RMS; the farthest usable "
        f"ray is {cr['farthest_usable_ft']:,.0f} ft long. That precision, against the sample interval of {1000.0 / p['fs_hz']:.1f} ms, is the parabola through the "
        f"peak's three samples; the gain over one station listening alone is the window ({p['window_s']:g} s) times the band."],
        [], [Figure('moveout', svg_moveout(res), 'Time differences against offset for four windows: the picks and the scene\'s own',
                    'Scatter of picked time differences in ms against offset in ft for four bit positions, with the true differences as lines')]))
    rows = [[t, v_, tr, f"{100.0 * (v_ - tr) / tr:+.1f}"] for t, v_, tr in zip(co['tops_ft'], co['v_ft_s'], co['truth_ft_s'])]
    secs.append(Section('3', 'The velocity column', [
        f"The differences inverted for the velocity of each {p['dz_ft']:g} ft cell from the surface to the deepest bit position ({co['z_max_ft']:,.0f} ft): "
        f"Gauss-Newton in slowness with the ray path lengths as derivatives, a second-difference smoothing, {co['iterations']} iterations to a fit of {co['fit_rms_ms']:.2f} ms RMS. "
        f"Against the scene: {co['cell_error_rms_pct']:.1f}% RMS over the cells, {co['cell_error_max_pct']:.1f}% at worst (the cells just above the deepest bit position, "
        f"which only the build section crosses). Interfaces found where neighbouring cells differ by 8% or more, down to half a cell above the deepest bit "
        f"position (the last cell is crossed only in part): "
        + '; '.join(f"{m['true_ft']:,.0f} ft in the scene, found at {m['found_ft']:,.0f} ({m['error_ft']:+d} ft)" if m['found_ft'] is not None else f"{m['true_ft']:,.0f} ft in the scene, not found" for m in co['interfaces_matched']) + '.',
        f"Verdict: {'the column is recovered' if vd['column'] else 'the column is NOT recovered'} (cells within 5% RMS, every interface within 100 ft)."],
        [Table(['Cell top (ft)', 'Recovered (ft/s)', 'Scene (ft/s)', 'Error (%)'], rows)],
        [Figure('section', svg_section(res), 'The section along the well: the scene\'s layers (left) and the recovered column with the layer found below the bit (right)',
                'Two panels of depth against distance east: the scene\'s layers coloured by velocity, and the recovered cells with interfaces and the reflector')]))
    if rf:
        secs.append(Section('4', 'The layer below the bit', [
            f"The two-way arrival (bit down to a layer and up to each station) scanned over depth below {co['z_max_ft']:,.0f} ft on the recovered column, the velocity below "
            f"the deepest bit position taken as the last cell's ({rf['v_assumed_below_ft_s']:,.0f} ft/s): the correlations' envelopes stack highest at {rf['found_ft']:,.0f} ft "
            f"({rf['contrast']:.1f} times the scan's median), the scene's layer is at {rf['true_ft']:,.0f} ft: {rf['error_ft']:+d} ft, against a quarter wavelength of "
            f"{rf['quarter_wavelength_ft']:,.0f} ft at {res['resolution']['dominant_hz']:g} Hz. {'Within it.' if rf['within_quarter_wavelength'] else 'Outside it.'} "
            f"The error is the assumed velocity below the bit: a slower layer there, as this scene has, puts the reflector shallow by the time difference."],
            [Table(['Depth scanned (ft)', 'Stack'], [[d, s_] for d, s_ in zip(rf['depths_ft'][::5], rf['stack'][::5])])]))
    lrows = [[l['top_ft'], l['base_ft'], l['v_ft_s'], l['rho_gcc'], l['overburden_base_psi'], l['hydrostatic_base_psi'], ', '.join(l['rocks']) or 'none in the inventory',
              ', '.join(f"{k} {v_:.0%}" for k, v_ in l['porosity'].items() if v_ is not None) or '-'] for l in res['layers']]
    secs.append(Section(str(len(secs) + 1), 'The property column', [
        'Each recovered layer read with standard relations: density by Gardner (0.23 V^0.25), the overburden as its weight summed from the surface (0.433 psi/ft per '
        'unit specific gravity) against hydrostatic water, the rocks of the inventory whose velocity range holds the layer\'s, and for the reservoir rocks among them the '
        'porosity by Wyllie\'s time average on a brine of 5,300 ft/s. Every figure is what a log at the well calibrates: a density log for the density, a sonic and a '
        'neutron-density for the porosity. Saturation needs resistivity, which no seismic array gives; that is the electrode array\'s band.'],
        [Table(['Top (ft)', 'Base (ft)', 'Velocity (ft/s)', 'Density (g/cm3)', 'Overburden at base (psi)', 'Hydrostatic at base (psi)', 'Rocks fitting', 'Porosity (Wyllie)'], lrows)]))
    secs.append(Section(str(len(secs) + 1), 'What a field recorder must capture', [
        'For the real thing to do what this run did, from a rig with nothing on it. Every figure comes from this run.'],
        [Table(['Item', 'Value', 'Why'], [[r['item'], r['value'], r['why']] for r in res['recorder']])]))
    secs.append(Section(str(len(secs) + 1), 'What this does not establish', [
        f"The scene is an assumption: flat layers, one velocity each, a bit whose signal is white in the band, noise that is white. It says that the arithmetic recovers "
        f"such an earth under such noise, to the stated precision; it says nothing about any ground. Resolution: a quarter wavelength at the band's dominant frequency, "
        f"{res['resolution']['quarter_wavelength_ft']:,.0f} ft here - no number of stations resolves a thinner layer; more stations lower the noise. Head waves along a "
        f"fast layer, converted waves, dipping layers and lateral changes are not in the scene; a real record has all of them, and the first field test is one well and "
        f"one station: a correlated arrival at a stated distance, then a column against a sonic log."]))
    return Document(title='Drill-bit seismic image (synthetic)', report_id=rid, front=front, sections=secs,
                    data={k: v_ for k, v_ in res.items() if not k.startswith('_')})


def write_image(ws, program_version: str, actor: str = 'system', **params) -> dict:
    """Run the scene and write the report and its machine copy under reports/seismic/SIMULATION/."""
    from .client_reports import write
    res = run_scene(**params)
    out = ws.seismic_reports_dir(REPORT_ID)
    doc = build_report(res, program_version)
    paths = write(doc, out, basename='bit_image')
    summary = {k: v_ for k, v_ in res.items() if k not in ('_arrays', 'moveout', 'path')}
    summary['report_id'] = doc.report_id
    summary['paths'] = {k: os.path.relpath(v_, ws.reports_dir).replace(os.sep, '/') for k, v_ in paths.items() if isinstance(v_, str)}
    with open(os.path.join(out, 'bit_image.summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1)
    ws.audit(actor, 'seismic.bit_image', {'label': LABEL, 'report_id': doc.report_id, 'verdict': res['verdict'], 'params': res['params']})
    return summary


def image_summary(ws) -> Optional[dict]:
    p = os.path.join(ws.reports_dir, 'seismic', REPORT_ID, 'bit_image.summary.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def report_text(res: dict) -> str:
    co, cr, rf, vd = res['column'], res['correlation'], res.get('reflector'), res['verdict']
    lines = [f"drill-bit seismic image - {LABEL}",
             f"  scene: {len(res['scene']['layers'])} layers, well to {res['scene']['td_md_ft']:,.0f} ft MD, {res['scene']['windows']} windows; {res['array']['n_stations']} stations to {res['params']['radius_ft']:,.0f} ft; noise {res['params']['noise']:g}",
             f"  correlation: {cr['usable']} of {cr['pairs']} pairs usable, picks good to {cr['pick_precision_ms']:.2f} ms, {cr['wrong_picks']} wrong, farthest usable ray {cr['farthest_usable_ft']:,.0f} ft",
             f"  column: {co['cell_error_rms_pct']:.1f}% RMS over {len(co['tops_ft'])} cells (max {co['cell_error_max_pct']:.1f}%), fit {co['fit_rms_ms']:.2f} ms; interfaces "
             + ', '.join(f"{m['true_ft']:,.0f}->{m['found_ft']:,.0f}" if m['found_ft'] is not None else f"{m['true_ft']:,.0f}->none" for m in co['interfaces_matched'])
             + (f"; not in the scene: {', '.join(f'{z:,.0f}' for z in co['extra_interfaces_ft'])}" if co.get('extra_interfaces_ft') else '')]
    if rf:
        lines.append(f"  layer below the bit: {rf['true_ft']:,.0f} ft in the scene, found {rf['found_ft']:,.0f} ({rf['error_ft']:+d} ft; quarter wavelength {rf['quarter_wavelength_ft']:,.0f} ft)")
    lines.append(f"  verdict: column {'OK' if vd['column'] else 'NO'}, reflector {'OK' if vd['reflector'] else 'NO'}, picks {'OK' if vd['picks'] else 'NO'}")
    for l in res['layers']:
        por = ', '.join(f"{k} {v_:.0%}" for k, v_ in l['porosity'].items() if v_ is not None)
        lines.append(f"  {l['top_ft']:>6,.0f}-{l['base_ft']:<6,.0f} ft  {l['v_ft_s']:>6,} ft/s  rho {l['rho_gcc']}  {', '.join(l['rocks']) or '-'}{('  porosity ' + por) if por else ''}")
    return '\n'.join(lines)


def selftest(seed: int = 1) -> dict:
    return run_scene(seed=seed)
