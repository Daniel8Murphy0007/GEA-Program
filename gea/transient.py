# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""transient - pressure build-up analysis with a stated uncertainty.

Input: a build-up (shut-in pressures p_ws against shut-in time dt, the
producing time tp before it, the flowing pressure p_wf at shut-in) and,
optionally, the rock and fluid parameters that turn a slope into a
permeability.

What is computed (field units: psi, hours, STB/d, cp, ft, md):

    Horner time          (tp + dt) / dt; semi-log straight line p_ws = p* - m log10(Horner)
    Bourdet derivative   dp/d ln(dt_e) with Agarwal equivalent time dt_e = tp dt / (tp + dt),
                         L-spaced central differences (L = 0.15 log cycle)
    Flow regimes         wellbore storage (log-log slope ~ 1 early), radial flow (a flat
                         derivative: the middle-time region, MTR), a late departure
    Line fit on the MTR  slope m (psi/cycle), p* (extrapolated to infinite shut-in), p_1hr
    With parameters      kh = 162.6 q B mu / m;  k = kh / h;
                         skin s = 1.151 [ (p_1hr - p_wf)/m - log10(k / (phi mu c_t r_w^2)) + 3.23 ]
                         radius of investigation r_inv = 0.029 sqrt(k t / (phi mu c_t)) at the end of the MTR
    Uncertainty          residual bootstrap of the MTR fit (400 draws): the 5th-95th percentile
                         band on m, p*, kh, k and s, plus the plain least-squares standard error on m.

Every number is reported with the MTR it came from and the rule that chose
the MTR, so an engineer can move the window and see the result move. Nothing
here replaces a type-curve or a numerical model; it is the first look that
every shut-in should get automatically, with the caveats printed.

Numpy only.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

DEFAULT_PARAMS = {'q_stb_d': None, 'B_rb_stb': 1.2, 'mu_cp': 1.0, 'h_ft': None, 'phi': 0.2, 'ct_1_psi': 1.0e-5, 'rw_ft': 0.354}


def bourdet_derivative(x_log: np.ndarray, y: np.ndarray, L: float = 0.15) -> np.ndarray:
    """dy/dx with x in natural log units, L-spaced (Bourdet et al.): weighted central differences."""
    n = len(x_log)
    d = np.full(n, np.nan)
    for i in range(n):
        # left point at least L before, right point at least L after (in ln units)
        li = i
        while li > 0 and x_log[i] - x_log[li] < L:
            li -= 1
        ri = i
        while ri < n - 1 and x_log[ri] - x_log[i] < L:
            ri += 1
        if li == i or ri == i:
            continue
        dx1, dx2 = x_log[i] - x_log[li], x_log[ri] - x_log[i]
        dy1, dy2 = y[i] - y[li], y[ri] - y[i]
        d[i] = (dy1 / dx1 * dx2 + dy2 / dx2 * dx1) / (dx1 + dx2)
    return d


def _loglog_slope(x: np.ndarray, y: np.ndarray, L_decades: float = 0.4) -> np.ndarray:
    """d log y / d log x, differenced over at least L_decades either side (noise-tolerant)."""
    lx, ly = np.log10(x), np.log10(np.where(y > 0, y, np.nan))
    return bourdet_derivative(lx * np.log(10), ly * np.log(10), L=L_decades * np.log(10))


def log_bin(dt: np.ndarray, y: np.ndarray, per_decade: int = 20) -> tuple:
    """Median of y in log-spaced bins of dt (per_decade bins per decade): denoises and gives even log coverage."""
    lo, hi = np.log10(dt.min()), np.log10(dt.max())
    nb = max(int(np.ceil((hi - lo) * per_decade)), 1)
    edges = np.linspace(lo, hi + 1e-9, nb + 1)
    idx = np.clip(np.digitize(np.log10(dt), edges) - 1, 0, nb - 1)
    bt, by, bn = [], [], []
    for b in range(nb):
        m = idx == b
        if m.any():
            bt.append(float(10 ** np.mean(np.log10(dt[m]))))
            by.append(float(np.median(y[m])))
            bn.append(int(m.sum()))
    return np.asarray(bt), np.asarray(by), np.asarray(bn)


def find_radial_flow(dt_h: np.ndarray, deriv: np.ndarray, tol: float = 0.15, min_span_decades: float = 0.5,
                     min_points: int = 5) -> Optional[Dict[str, float]]:
    """The middle-time region: the longest window (in decades of dt) over which a straight line through
    log(derivative) vs log(dt) has a slope within +/- tol of zero and is not significantly non-flat
    (|slope| <= 2 standard errors + tol). Works on the derivative as given; callers pass a log-binned one."""
    ok = ~np.isnan(deriv) & (deriv > 0) & (dt_h > 0)
    if ok.sum() < min_points:
        return None
    lt, ld = np.log10(dt_h[ok]), np.log10(deriv[ok])
    orig = np.where(ok)[0]
    n = len(lt)
    best = None
    for i in range(n):
        for j in range(i + min_points - 1, n):
            span = lt[j] - lt[i]
            if span < min_span_decades:
                continue
            x, y = lt[i:j + 1], ld[i:j + 1]
            b, se = _slope(x, y)
            if abs(b) > tol:
                continue
            h = (i + j) // 2                                        # a hump fits a flat line through its middle: test each half too
            b1, _ = _slope(lt[i:h + 1], ld[i:h + 1])
            b2, _ = _slope(lt[h:j + 1], ld[h:j + 1])
            if abs(b1) > 2 * tol or abs(b2) > 2 * tol:
                continue
            if best is None or span > best['span_log'] + 1e-9:
                best = {'start': int(orig[i]), 'end': int(orig[j]), 'span_log': float(span), 'slope': b, 'slope_se': se}
    return best


def _slope(x: np.ndarray, y: np.ndarray) -> tuple:
    if len(x) < 2 or np.ptp(x) == 0:
        return 0.0, float('inf')
    xm = x - x.mean()
    b = float(np.sum(xm * (y - y.mean())) / np.sum(xm ** 2))
    r = y - (y.mean() + b * xm)
    se = float(np.sqrt(np.sum(r ** 2) / max(len(x) - 2, 1) / np.sum(xm ** 2)))
    return b, se


def analyze_buildup(dt_h, p_ws, tp_h: float, p_wf: Optional[float] = None, params: Optional[dict] = None,
                    mtr: Optional[Dict[str, float]] = None, n_boot: int = 400, seed: int = 7) -> dict:
    dt = np.asarray(dt_h, dtype=float)
    p = np.asarray(p_ws, dtype=float)
    m_ok = ~np.isnan(dt) & ~np.isnan(p) & (dt > 0)
    dt, p = dt[m_ok], p[m_ok]
    order = np.argsort(dt)
    dt, p = dt[order], p[order]
    prm = dict(DEFAULT_PARAMS, **(params or {}))
    out: dict = {'n': int(len(dt)), 'tp_h': float(tp_h), 'p_wf': p_wf, 'params': prm, 'caveats': []}
    if len(dt) < 6:
        out['status'] = 'INSUFFICIENT_DATA'
        out['caveats'].append(f'{len(dt)} usable samples; at least 6 are needed')
        return out
    horner = (tp_h + dt) / dt
    dte = tp_h * dt / (tp_h + dt)                                  # Agarwal equivalent time
    dp = p - (p_wf if p_wf is not None else p[0])
    deriv = bourdet_derivative(np.log(dte), dp, L=0.15)            # psi per ln-cycle of equivalent time
    # regimes are read on a log-binned copy (median per 1/20 decade): even coverage, less noise
    b_dt, b_dp, b_n = log_bin(dt, dp)
    b_dte = tp_h * b_dt / (tp_h + b_dt)
    b_der = bourdet_derivative(np.log(b_dte), b_dp, L=0.15)
    out['curves'] = {'dt_h': dt.round(5).tolist(), 'p_ws': p.round(3).tolist(), 'horner': horner.round(4).tolist(),
                     'dp': dp.round(3).tolist(), 'derivative': [None if np.isnan(x) else round(float(x), 3) for x in deriv],
                     'binned': {'dt_h': b_dt.round(5).tolist(), 'dp': b_dp.round(3).tolist(), 'derivative': [None if np.isnan(x) else round(float(x), 3) for x in b_der]}}
    # wellbore storage: early points where log-log slope of dp is ~1
    ll = _loglog_slope(b_dte, np.where(b_dp > 0, b_dp, np.nan))
    storage_end = None
    for i in range(len(ll)):
        if not np.isnan(ll[i]) and ll[i] > 0.8:
            storage_end = i
        elif storage_end is not None:
            break
    if storage_end is not None:
        storage_end_dt = float(b_dt[storage_end])
        out['wellbore_storage'] = {'end_dt_h': storage_end_dt,
                                   'note': 'unit log-log slope early: wellbore storage; the MTR must start after about 1.5 log cycles later'}
    # the middle-time region
    chosen = None
    if mtr and 'start_dt_h' in mtr and 'end_dt_h' in mtr:
        idx = np.where((dt >= mtr['start_dt_h']) & (dt <= mtr['end_dt_h']))[0]
        if len(idx) >= 3:
            chosen = {'start': int(idx[0]), 'end': int(idx[-1]), 'rule': 'window chosen by the analyst'}
    if chosen is None:
        tol = 0.15
        rf = find_radial_flow(b_dt, b_der, tol=tol)
        if rf:
            lo_dt, hi_dt = b_dt[rf['start']], b_dt[rf['end']]
            idx = np.where((dt >= lo_dt * 0.999) & (dt <= hi_dt * 1.001))[0]
            if len(idx) >= 3:
                chosen = {'start': int(idx[0]), 'end': int(idx[-1]),
                          'rule': f"flat derivative (log-log slope {rf['slope']:+.2f} ± {rf['slope_se']:.2f}, within ±{tol:.2f}, over {rf['span_log']:.2f} log cycles)"}
    if chosen is None:
        # fall back: the last half of the data in log time, excluding storage
        lo = int(np.searchsorted(dt, storage_end_dt)) + 1 if storage_end is not None else 0
        lo = max(lo, len(dt) // 2)
        if len(dt) - lo >= 4:
            chosen = {'start': int(lo), 'end': int(len(dt) - 1), 'rule': 'no flat derivative found: late half of the data used, result indicative only'}
            out['caveats'].append('no radial-flow plateau identified; the slope is from the late data and is indicative only')
    if chosen is None:
        out['status'] = 'NO_MTR'
        out['caveats'].append('too few points after wellbore storage to fit a line')
        return out
    i0, i1 = chosen['start'], chosen['end']
    x = np.log10(horner[i0:i1 + 1])
    y = p[i0:i1 + 1]
    A = np.vstack([x, np.ones_like(x)]).T
    coef, res, _, _ = np.linalg.lstsq(A, y, rcond=None)
    slope, intercept = coef                                        # p = intercept + slope*log10(horner); m = -slope
    m = float(-slope)
    p_star = float(intercept)                                      # horner = 1 -> log = 0
    fit = intercept + slope * x
    resid = y - fit
    dof = max(len(x) - 2, 1)
    se_m = float(np.sqrt(np.sum(resid ** 2) / dof / np.sum((x - x.mean()) ** 2))) if len(x) > 2 and np.ptp(x) > 0 else float('nan')
    p_1hr = float(intercept + slope * np.log10((tp_h + 1.0) / 1.0))
    out['mtr'] = {'start_index': int(i0), 'end_index': int(i1), 'start_dt_h': float(dt[i0]), 'end_dt_h': float(dt[i1]), 'n': int(i1 - i0 + 1),
                  'rule': chosen['rule'], 'rms_resid_psi': float(np.sqrt(np.mean(resid ** 2)))}
    out['horner'] = {'m_psi_per_cycle': m, 'm_std_err': se_m, 'p_star': p_star, 'p_1hr': p_1hr}

    def derived(m_, p1_):
        d: Dict[str, Optional[float]] = {}
        q, B, mu, h = prm.get('q_stb_d'), prm.get('B_rb_stb'), prm.get('mu_cp'), prm.get('h_ft')
        if q and B and mu and m_ > 0:
            kh = 162.6 * q * B * mu / m_
            d['kh_md_ft'] = kh
            if h:
                k = kh / h
                d['k_md'] = k
                phi, ct, rw = prm.get('phi'), prm.get('ct_1_psi'), prm.get('rw_ft')
                if p_wf is not None and phi and ct and rw:
                    d['skin'] = 1.151 * ((p1_ - p_wf) / m_ - np.log10(k / (phi * mu * ct * rw ** 2)) + 3.23)
                    d['r_inv_ft'] = 0.029 * np.sqrt(k * dt[i1] / (phi * mu * ct))
                    d['dp_skin_psi'] = 0.87 * m_ * d['skin']
        return d
    out['derived'] = {k: float(v) for k, v in derived(m, p_1hr).items()}
    if not out['derived']:
        out['caveats'].append('no rate/thickness parameters: slope and p* only (give q, B, mu, h, phi, c_t, r_w for kh, k and skin)')
    # bootstrap
    rng = np.random.default_rng(seed)
    draws = {'m': [], 'p_star': [], 'kh_md_ft': [], 'k_md': [], 'skin': []}
    if len(x) >= 4 and np.ptp(x) > 0:
        for _ in range(n_boot):
            yb = fit + rng.choice(resid, size=len(resid), replace=True)
            cb = np.linalg.lstsq(A, yb, rcond=None)[0]
            mb, pb = float(-cb[0]), float(cb[1])
            draws['m'].append(mb); draws['p_star'].append(pb)
            dd = derived(mb, float(cb[1] + cb[0] * np.log10(tp_h + 1.0)))
            for k in ('kh_md_ft', 'k_md', 'skin'):
                if k in dd:
                    draws[k].append(float(dd[k]))
    ci = {}
    for k, v in draws.items():
        if v:
            a = np.asarray(v)
            ci[k] = {'p05': float(np.percentile(a, 5)), 'p50': float(np.percentile(a, 50)), 'p95': float(np.percentile(a, 95))}
    out['uncertainty'] = {'method': f'residual bootstrap, {n_boot} draws over the MTR fit', 'ci90': ci}
    out['status'] = 'OK'
    if out['mtr']['n'] < 8:
        out['caveats'].append(f"only {out['mtr']['n']} points in the MTR; the band is wide for that reason")
    if 'wellbore_storage' in out and dt[i0] < 30 * out['wellbore_storage']['end_dt_h']:
        out['caveats'].append('the MTR begins less than 1.5 log cycles after the end of wellbore storage; the slope may still carry storage')
    return out
