# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""plume - the pressure plume: where the ledger's barrels have raised the formation's pressure, on a grid, through
time, with the mapped faults in it.

The second fluid-flow module. The ledger (`fluid`) knows how much went in and out of a formation each day and
through which well; this module puts those barrels into a formation on a plan grid and steps the pressure
forward in time, so that the rise can be drawn as contours, its reach stated as an area of review, and its
effect on every mapped fault read through time against the declared stress state. Every result of it is a
SIMULATION - a projection on the declared formation, to be checked against a neighbour's gauge, never a
reading of one - and the page says so on every line that is one.

- THE FORMATION ON A GRID. A square plan grid of cells centred on the injection well (a local transverse
  Mercator frame, feet east and north), each cell with the declared net thickness, porosity and permeability
  (uniform: a map of either is the next step and is said to be missing), the declared viscosity and total
  compressibility. Single-phase, slightly compressible flow in field units: the volume a cell stores per psi is
  V_p c_t = dx^2 h phi c_t / 5.6146 bbl/psi; the flow between two cells per psi of difference is the
  transmissibility T = 0.001127 k A / (mu L) bbl/d/psi with A = h dx and L = dx. The grid's edge holds the
  initial pressure (a constant-pressure boundary) and is set far enough out that the front does not reach it
  in the period, and the page says how far.
- THE BARRELS. Each well's days from the ledger become a source in its cell: barrels per day in (an injection
  well) or out (a producer), day by day, the record's own calendar. Beyond the record two projections are
  stepped from the last day's state: CONTINUED (the last thirty days' mean rate held) and SHUT IN (no rate),
  each a year, so the page shows both where the plume goes if nothing changes and how it relaxes if the well
  stops.
- THE STEPPING. Explicit finite differences: each sub-step moves pressure between neighbours by T x (dp)
  and adds the sources; the sub-step is kept under the stability limit (0.45 of the smallest cell's storage
  over its transmissibilities), so a tight formation steps once a day and a permeable one many times. The
  scheme is checked against the line-source solution (Theis) on a uniform grid every time the self-test runs,
  and the page prints the agreement at the last record day as its own check.
- THE FAULTS. Every fault trace within the radius from the structure layers (`slip.faults_in_radius`), cut
  into the grid as the segments the map draws; each segment's cells are sampled for the largest pressure rise
  along the fault at every snapshot, and that rise goes into the slip-tendency arithmetic (`slip`) at the
  declared stress state, dips and frictions - Ts, the margin to the friction line, and the first day the
  margin is used up, if it is. A fault layer can be declared SEALING: its segments then block flow across the
  cell faces they cross (the transmissibility set to zero), and the plume piles up against them; by default a
  fault is OPEN, because a map says nothing about what a fault does to flow, and the page says which it was.
- THE AREA OF REVIEW. The cells whose rise is at or above a declared threshold (psi), as an area in acres and a
  reach in feet from the injection well, at every snapshot; which neighbours and which faults are inside it.
- THE DRAWING. Contours of the rise (marching squares) at every snapshot on the Pressure plume tab's plan view,
  and the same contours at the formation's top depth in the 3D tab, where the wells and the formation tops
  already are.

What it will not do: invent a permeability (undeclared, no plume: the page says what to declare); treat a
fault as sealing or open on its own (declared, default open, said); place the plume in depth without a
declared top (then it is drawn in plan and not in 3D); read a neighbour's pressure (that is the gauge's, and
the comparison is the next module's calibration); call a fault's slip anything but arithmetic on declared
gradients, a declared dip and a declared friction.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import geodesy as G
from . import fluid as FL

FT_PER_M = 1.0 / 0.3048
ACRE_FT2 = 43560.0
T_CONST = 0.001127          # bbl/d/psi per (md ft^2 / (cp ft))
FT3_PER_BBL = FL.FT3_PER_BBL
DEFAULT_THRESHOLD_PSI = 50.0
DEFAULT_CELLS = 81
MAX_CELLS = 121
MAX_SUBSTEPS_PER_DAY = 400
PROJECTION_DAYS = 365
PROJECTION_MARKS = (30, 90, 180, 365)
LEVEL_FACTORS = (0.2, 0.5, 1.0, 2.0, 4.0)
_LOCK = threading.Lock()


class PlumeError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------------------------------------------
# settings: records/plume.json
# --------------------------------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'plume.json')


def settings(ws) -> dict:
    s = {'threshold_psi': DEFAULT_THRESHOLD_PSI, 'cells': DEFAULT_CELLS, 'radius_km': 25.0, 'sealing_layers': [], 'projection_days': PROJECTION_DAYS}
    p = _path(ws)
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            s.update(json.load(f))
    return s


def set_settings(ws, actor: str, threshold_psi=None, cells=None, radius_km=None, sealing: Optional[Dict[str, bool]] = None, projection_days=None, source: str = '') -> dict:
    with _LOCK:
        s = settings(ws)
        if threshold_psi is not None and threshold_psi != '':
            v = float(threshold_psi)
            if not 1.0 <= v <= 5000.0:
                raise PlumeError('the threshold is 1 to 5,000 psi')
            s['threshold_psi'] = v
        if cells is not None and cells != '':
            v = int(cells)
            if not 21 <= v <= MAX_CELLS or v % 2 == 0:
                raise PlumeError(f'the grid is an odd number of cells per side, 21 to {MAX_CELLS}')
            s['cells'] = v
        if radius_km is not None and radius_km != '':
            v = float(radius_km)
            if not 1.0 <= v <= 100.0:
                raise PlumeError('the fault radius is 1 to 100 km')
            s['radius_km'] = v
        if projection_days is not None and projection_days != '':
            v = int(projection_days)
            if not 30 <= v <= 3650:
                raise PlumeError('the projection is 30 to 3,650 days')
            s['projection_days'] = v
        if sealing:
            if any(sealing.values()) and not (source or '').strip():
                raise PlumeError('declaring a fault layer sealing needs its source (--source "the operator\'s fault study ...")')
            ids = {rec['id'] for rec in ws.layers()}
            cur = set(s.get('sealing_layers', []))
            for lid, on in sealing.items():
                if lid not in ids:
                    raise PlumeError(f'no layer {lid!r} in the workspace')
                (cur.add if on else cur.discard)(lid)
            s['sealing_layers'] = sorted(cur)
            if (source or '').strip():
                s['sealing_source'] = source
        s['declared_by'] = actor; s['declared_utc'] = _utc()
        tmp = _path(ws) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, _path(ws))
    ws.audit(actor, 'plume.settings', {k: s.get(k) for k in ('threshold_psi', 'cells', 'radius_km', 'sealing_layers', 'projection_days', 'sealing_source')})
    return s


# --------------------------------------------------------------------------------------------------------------
# the grid and the stepping
# --------------------------------------------------------------------------------------------------------------
class Grid:
    """A square plan grid of n x n cells of side dx (ft), centred on the origin; uniform properties unless arrays are given."""

    def __init__(self, n: int, dx_ft: float, h_ft, phi, k_md, mu_cp: float, ct_per_psi: float):
        self.n = int(n); self.dx = float(dx_ft)
        c = (np.arange(n) - (n - 1) / 2.0) * self.dx
        self.x = c.copy(); self.y = c.copy()                  # cell-centre coordinates along each axis (index 0 = west / south)
        self.h = np.full((n, n), float(h_ft)) if np.isscalar(h_ft) else np.asarray(h_ft, dtype=float)
        self.phi = np.full((n, n), float(phi)) if np.isscalar(phi) else np.asarray(phi, dtype=float)
        self.k = np.full((n, n), float(k_md)) if np.isscalar(k_md) else np.asarray(k_md, dtype=float)
        self.mu = float(mu_cp); self.ct = float(ct_per_psi)
        self.storage = self.dx * self.dx * self.h * self.phi * self.ct / FT3_PER_BBL          # bbl/psi per cell
        kh = self.k * self.h
        # face transmissibilities, harmonic mean of kh: Tx between (i, j) and (i, j+1) along x (columns = x), Ty along y (rows = y)
        self.Tx = T_CONST * 2.0 * kh[:, :-1] * kh[:, 1:] / np.maximum(kh[:, :-1] + kh[:, 1:], 1e-30) / self.mu      # (n, n-1)
        self.Ty = T_CONST * 2.0 * kh[:-1, :] * kh[1:, :] / np.maximum(kh[:-1, :] + kh[1:, :], 1e-30) / self.mu      # (n-1, n)
        self.sealed_faces = 0

    def cell_of(self, x_ft: float, y_ft: float) -> Optional[Tuple[int, int]]:
        j = int(round(x_ft / self.dx + (self.n - 1) / 2.0)); i = int(round(y_ft / self.dx + (self.n - 1) / 2.0))
        if 0 <= i < self.n and 0 <= j < self.n:
            return i, j
        return None

    def seal_segment(self, x0: float, y0: float, x1: float, y1: float) -> int:
        """Zero the transmissibility of every cell face the segment separates: the cells within the segment's extent
        (half a cell beyond each end) are sorted to one side or the other of its line, and a face between two
        cells on opposite sides is sealed - a barrier without gaps, however the segment lies against the grid.
        Returns the faces sealed."""
        dxs, dys = x1 - x0, y1 - y0
        L2 = dxs * dxs + dys * dys
        if L2 <= 0:
            return 0
        X, Y = np.meshgrid(self.x, self.y)                       # X[i, j] = x of column j; Y[i, j] = y of row i
        t = ((X - x0) * dxs + (Y - y0) * dys) / L2
        margin = (self.dx / 2.0) / math.sqrt(L2)
        inside = (t >= -margin) & (t <= 1.0 + margin)
        side = (dxs * (Y - y0) - dys * (X - x0)) >= 0.0
        sealed = 0
        fx = inside[:, :-1] & inside[:, 1:] & (side[:, :-1] != side[:, 1:]) & (self.Tx != 0.0)
        sealed += int(fx.sum()); self.Tx[fx] = 0.0
        fy = inside[:-1, :] & inside[1:, :] & (side[:-1, :] != side[1:, :]) & (self.Ty != 0.0)
        sealed += int(fy.sum()); self.Ty[fy] = 0.0
        self.sealed_faces += sealed
        return sealed

    def dt_max_days(self) -> float:
        """The explicit scheme's stability limit: the smallest storage over the sum of a cell's transmissibilities."""
        tsum = np.zeros((self.n, self.n))
        tsum[:, :-1] += self.Tx; tsum[:, 1:] += self.Tx
        tsum[:-1, :] += self.Ty; tsum[1:, :] += self.Ty
        with np.errstate(divide='ignore', invalid='ignore'):
            r = np.where(tsum > 0, self.storage / np.maximum(tsum, 1e-30), np.inf)
        return 0.45 * float(np.min(r))


def step_days(grid: Grid, dp: np.ndarray, q_cells: Dict[Tuple[int, int], float], days: float, substeps: int) -> np.ndarray:
    """Advance the pressure rise dp (psi, n x n) by `days` with the sources q (bbl/d per cell, in positive), the boundary held at zero."""
    dt = days / substeps
    inv = dt / grid.storage
    q = np.zeros_like(dp)
    for (i, j), v in q_cells.items():
        q[i, j] += v
    for _ in range(substeps):
        flux = np.zeros_like(dp)
        fx = grid.Tx * (dp[:, 1:] - dp[:, :-1])
        flux[:, :-1] += fx; flux[:, 1:] -= fx
        fy = grid.Ty * (dp[1:, :] - dp[:-1, :])
        flux[:-1, :] += fy; flux[1:, :] -= fy
        dp = dp + inv * (flux + q)
        dp[0, :] = 0.0; dp[-1, :] = 0.0; dp[:, 0] = 0.0; dp[:, -1] = 0.0
    return dp


# --------------------------------------------------------------------------------------------------------------
# contours (marching squares) and the area of review
# --------------------------------------------------------------------------------------------------------------
def contours(grid: Grid, dp: np.ndarray, level: float) -> List[List[Tuple[float, float]]]:
    """Polylines (x, y in ft) where dp crosses the level, by marching squares on the cell centres, chained."""
    n = grid.n
    segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    above = dp >= level

    def interp(xa, ya, va, xb, yb, vb):
        t = (level - va) / (vb - va) if vb != va else 0.5
        return (xa + t * (xb - xa), ya + t * (yb - ya))
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = above[i, j], above[i, j + 1], above[i + 1, j + 1], above[i + 1, j]
            idx = (a << 3) | (b << 2) | (c << 1) | int(d)
            if idx in (0, 15):
                continue
            x0, x1, y0, y1 = grid.x[j], grid.x[j + 1], grid.y[i], grid.y[i + 1]
            va, vb, vc, vd = dp[i, j], dp[i, j + 1], dp[i + 1, j + 1], dp[i + 1, j]
            top = interp(x0, y0, va, x1, y0, vb); right = interp(x1, y0, vb, x1, y1, vc)
            bottom = interp(x0, y1, vd, x1, y1, vc); left = interp(x0, y0, va, x0, y1, vd)
            table = {1: [(left, bottom)], 2: [(bottom, right)], 3: [(left, right)], 4: [(top, right)], 5: [(top, left), (bottom, right)] if (va + vb + vc + vd) / 4 < level else [(top, right), (left, bottom)],
                     6: [(top, bottom)], 7: [(top, left)], 8: [(top, left)], 9: [(top, bottom)], 10: [(top, right), (left, bottom)] if (va + vb + vc + vd) / 4 < level else [(top, left), (bottom, right)],
                     11: [(top, right)], 12: [(left, right)], 13: [(bottom, right)], 14: [(left, bottom)]}
            segs.extend(table[idx])
    # chain the segments into polylines
    def key(p):
        return (round(p[0], 3), round(p[1], 3))
    ends: Dict[Tuple[float, float], List[int]] = {}
    for si, (p, q) in enumerate(segs):
        ends.setdefault(key(p), []).append(si); ends.setdefault(key(q), []).append(si)
    used = [False] * len(segs)
    lines: List[List[Tuple[float, float]]] = []
    for si in range(len(segs)):
        if used[si]:
            continue
        used[si] = True
        line = [segs[si][0], segs[si][1]]
        for direction in (1, 0):
            while True:
                tail = line[-1] if direction else line[0]
                nxt = [t for t in ends.get(key(tail), []) if not used[t]]
                if not nxt:
                    break
                t = nxt[0]; used[t] = True
                p, q = segs[t]
                other = q if key(p) == key(tail) else p
                if direction:
                    line.append(other)
                else:
                    line.insert(0, other)
        lines.append(line)
    return lines


def area_of_review(grid: Grid, dp: np.ndarray, threshold: float, x_well: float, y_well: float) -> dict:
    sel = dp >= threshold
    n = int(sel.sum())
    out = {'threshold_psi': threshold, 'cells': n, 'area_acres': round(n * grid.dx * grid.dx / ACRE_FT2, 1)}
    if n:
        ii, jj = np.nonzero(sel)
        r = np.hypot(grid.x[jj] - x_well, grid.y[ii] - y_well)
        out['reach_ft'] = round(float(r.max()) + grid.dx / 2.0, 0)
        out['touches_edge'] = bool(sel[0, :].any() or sel[-1, :].any() or sel[:, 0].any() or sel[:, -1].any())
    else:
        out['reach_ft'] = 0.0; out['touches_edge'] = False
    return out


# --------------------------------------------------------------------------------------------------------------
# the plume of one formation
# --------------------------------------------------------------------------------------------------------------
def _frame(lat0: float, lon0: float) -> dict:
    return G._tm(lat0, lon0, 1.0, 0.0, 0.0, 'WGS84')


def _xy_ft(frame: dict, lat: float, lon: float) -> Tuple[float, float]:
    p = G.project(lat, lon, frame)
    return p['easting_m'] * FT_PER_M, p['northing_m'] * FT_PER_M


def _month_ends(days: List[str]) -> List[str]:
    out = []
    for a, b in zip(days, days[1:] + [None]):
        if b is None or a[:7] != b[:7]:
            out.append(a)
    return out


def _wgs(surface: dict) -> Tuple[float, float]:
    g = G.to_wgs84(float(surface['lat']), float(surface['lon']), 0.0, surface.get('datum') or 'WGS84')
    return g['lat'], g['lon']


def fault_segments(ws, frame: dict, lat0: float, lon0: float, radius_km: float, sealing_ids: List[str]) -> List[dict]:
    """Every mapped fault within the radius, with its segments in the grid frame (ft)."""
    from . import slip as SL
    out = []
    for rec in ws.layers():
        if (rec.get('kind') or 'structure') != 'structure':
            continue
        try:
            L = ws.load_layer(rec['id'])
            faults = SL.faults_in_radius(L, lat0, lon0, radius_km, rec.get('type_attr'), rec.get('name_attr'))
        except (ValueError, OSError) as e:
            out.append({'layer': rec['id'], 'layer_display': rec.get('display', rec['id']), 'status': 'UNREADABLE', 'note': str(e), 'segments': []})
            continue
        for f in faults:
            feat = L.features[f['index']]
            segs = []
            for part in feat.parts:
                pts = [_xy_ft(frame, la, lo) for lo, la in part]
                for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
                    segs.append([round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)])
            out.append({'layer': rec['id'], 'layer_display': rec.get('display', rec['id']), 'index': f['index'], 'name': f.get('name') or f.get('type') or 'unnamed trace',
                        'strike_deg': f['strike_deg'], 'distance_km': f['distance_km'], 'length_km': f['length_km'], 'attrs': f.get('attrs', {}),
                        'sealing': rec['id'] in sealing_ids, 'segments': segs, 'status': 'OK'})
    return out


def _sample_fault(grid: Grid, dp: np.ndarray, segs: List[List[float]]) -> Optional[float]:
    best = None
    for x0, y0, x1, y1 in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        steps = max(2, int(L / (grid.dx * 0.5)) + 1)
        for s in np.linspace(0.0, 1.0, steps):
            c = grid.cell_of(x0 + s * (x1 - x0), y0 + s * (y1 - y0))
            if c is None:
                continue
            v = float(dp[c])
            best = v if best is None or v > best else best
    return best


def build(formation: str, fm: dict, wells: List[dict], st: dict, ws=None, stress: Optional[dict] = None, shmax_az: Optional[float] = None,
          az_source: str = '', dips: Tuple[float, ...] = (60.0, 75.0, 90.0), mus: Tuple[float, ...] = (0.6,)) -> dict:
    """One formation's plume from its ledger wells (each with 'days' of volume_bbl, a 'surface' position and a role).
    `st` is the plume settings. Returns the page's plume dict."""
    from . import slip as SL
    out: Dict[str, Any] = {'formation': formation, 'status': 'OK', 'notes': [], 'lines': [], 'label': 'SIMULATION'}
    inj = [w for w in wells if w.get('role') == 'injection' and w.get('days')]
    if not inj:
        out.update(status='NO INJECTION WELL', notes=['no injection well with a volume record is assigned to this formation'])
        return out
    for k in ('k_md', 'thickness_ft', 'porosity'):
        if fm.get(k) is None:
            out.update(status='NOT DECLARED', notes=[f'the formation has no {k.replace("_", " ")} declared: gea fluid --formation NAME --k-md --thickness-ft --porosity --source ...'])
            return out
    placed = [w for w in wells if w.get('days') and (w.get('surface') or {}).get('lat') is not None]
    unplaced = [w['well'] for w in wells if w.get('days') and (w.get('surface') or {}).get('lat') is None]
    if not any(w.get('role') == 'injection' for w in placed):
        out.update(status='NO SURFACE POSITION', notes=['the injection well has no declared surface position'])
        return out
    sd = FL.storage_declared(fm)
    ct = sd['c_t_per_psi']; mu = fm.get('mu_cp') or FL.MU_WATER_CP
    k = float(fm['k_md']); h = float(fm['thickness_ft']); phi = float(fm['porosity'])
    # the calendar: every day from the first record to the last, each well's rate on it (positive in)
    all_days = sorted({d['date'] for w in placed for d in w['days'] if d['volume_bbl'] is not None})
    if not all_days:
        out.update(status='NO RECORD', notes=['no day with a volume']); return out
    d0 = datetime.strptime(all_days[0], '%Y-%m-%d'); d1 = datetime.strptime(all_days[-1], '%Y-%m-%d')
    n_days = (d1 - d0).days + 1
    cal = [(d0 + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n_days)]
    # the frame: centred on the first injection well; the grid's extent from the radius of investigation at the end of the projection
    main = next(w for w in placed if w.get('role') == 'injection')
    lat0, lon0 = _wgs(main['surface'])
    frame = _frame(lat0, lon0)
    proj_days = int(st.get('projection_days', PROJECTION_DAYS))
    r_inv_end = FL.radius_of_investigation_ft(k, (n_days + proj_days) * 24.0, phi, mu, ct)
    n = int(st.get('cells', DEFAULT_CELLS))
    wxy = {}
    for w in placed:
        la, lo = _wgs(w['surface']); wxy[w['well']] = _xy_ft(frame, la, lo)
    far = max([math.hypot(x, y) for x, y in wxy.values()] + [0.0])
    half = max(2.0 * r_inv_end, 1.2 * far + 2000.0, 5000.0)
    dx = 2.0 * half / (n - 1)
    grid = Grid(n, dx, h, phi, k, mu, ct)
    # the faults
    sealing_ids = list(st.get('sealing_layers', []))
    faults = fault_segments(ws, frame, lat0, lon0, float(st.get('radius_km', 25.0)), sealing_ids) if ws is not None else []
    for f in faults:
        if f.get('sealing') and f['segments']:
            f['faces_sealed'] = sum(grid.seal_segment(*s) for s in f['segments'])
    # the sources per day
    rate = {w['well']: np.zeros(n_days) for w in placed}
    for w in placed:
        for d in w['days']:
            if d['volume_bbl'] is not None:
                i = (datetime.strptime(d['date'], '%Y-%m-%d') - d0).days
                rate[w['well']][i] += d['volume_bbl']
    cells = {}
    for w in placed:
        c = grid.cell_of(*wxy[w['well']])
        if c is None:
            out['notes'].append(f"{w['well']} lies outside the grid and is left out"); continue
        cells[w['well']] = c
    dt_max = grid.dt_max_days()
    sub = max(1, int(math.ceil(1.0 / dt_max)))
    if sub > MAX_SUBSTEPS_PER_DAY:
        out.update(status='TOO FINE', notes=[f'the explicit scheme would need {sub} sub-steps a day on this grid (dx {dx:,.0f} ft, k {k:g} md); fewer cells (--cells) coarsen it'])
        return out
    # snapshots: month ends and the last day of the record, then the projections
    marks = set(_month_ends(cal)); marks.add(cal[-1])
    snaps = []
    dp = np.zeros((n, n))
    cum_in = 0.0
    thr = float(st.get('threshold_psi', DEFAULT_THRESHOLD_PSI))
    # the stress state for the faults
    state = None
    if stress is not None and shmax_az is not None:
        depth = fm.get('depth_ft')
        if depth:
            state = SL.stress_at(float(depth), stress['sv_psi_ft'], stress['shmin_psi_ft'], stress['shmax_psi_ft'], stress.get('pp_psi_ft', SL.PSI_PER_FT_HYDROSTATIC))

    def snapshot(day_index: int, date: str, kind: str, dp: np.ndarray) -> dict:
        levels = [thr * f for f in LEVEL_FACTORS]
        s = {'day': day_index, 'date': date, 'kind': kind, 'dp_max_psi': round(float(dp.max()), 1), 'dp_min_psi': round(float(dp.min()), 1),
             'aor': area_of_review(grid, dp, thr, *wxy[main['well']]), 'contours': {}, 'wells': {}, 'faults': []}
        for lv in levels:
            if dp.max() >= lv or dp.min() <= -lv:
                s['contours'][f'{lv:g}'] = [[[round(x, 0), round(y, 0)] for x, y in line] for line in contours(grid, dp, lv) if len(line) >= 2]
        for w in placed:
            c = cells.get(w['well'])
            if c is not None:
                # the well cell's own rise is the cell average, not the wellbore's; the neighbours' is what the page is for
                s['wells'][w['well']] = round(float(dp[c]), 1)
        for f in faults:
            if not f['segments']:
                continue
            v = _sample_fault(grid, dp, f['segments'])
            row = {'name': f['name'], 'layer': f['layer'], 'dp_max_psi': (round(v, 1) if v is not None else None), 'inside_aor': bool(v is not None and v >= thr)}
            if state is not None and v is not None:
                own = (f.get('attrs') or {}).get('dip_deg')
                cases = [SL.slip_tendency(f['strike_deg'], d_, state, shmax_az, m_, v) for d_ in ((own,) if own is not None else dips) for m_ in mus]
                worst = min(cases, key=lambda c_: c_['margin_after_injection_psi'])
                row.update(Ts=worst['Ts_injection'], margin_psi=worst['margin_after_injection_psi'], slip_status=worst['status'], dip_deg=worst['dip_deg'], mu=worst['mu'])
            s['faults'].append(row)
        return s
    def sources(day_rate) -> Dict[Tuple[int, int], float]:
        q: Dict[Tuple[int, int], float] = {}
        for w, c in cells.items():                            # two wells in one cell add up
            q[c] = q.get(c, 0.0) + float(day_rate(w))
        return q
    for i, date in enumerate(cal):
        q = sources(lambda w: rate[w][i])
        cum_in += sum(v for v in q.values() if v > 0)
        dp = step_days(grid, dp, q, 1.0, sub)
        if date in marks:
            snaps.append(snapshot(i + 1, date, 'record', dp))
    last_dp = dp.copy()
    stored = float(np.sum(grid.storage * dp))
    net = float(sum(rate[w].sum() for w in cells))
    out['balance'] = {'net_bbl': round(net, 0), 'stored_bbl': round(stored, 0), 'left_through_edge_bbl': round(net - stored, 0),
                      'note': 'the barrels the grid holds as pressure against the barrels put in; the difference left through the constant-pressure edge (or the storage a sealed fault denied)'}
    # the check against the line source at the last record day, on the main well's mean rate, at 4 and 8 cells
    if main['well'] in cells and grid.sealed_faces == 0:
        qm = float(rate[main['well']].mean()); ci, cj = cells[main['well']]
        chk = []
        for off in (4, 8):
            if cj + off < n - 1:
                r = off * dx
                ls = FL.line_source_dp_psi(qm, k, h, phi, mu, ct, r, n_days * 24.0)
                fd = float(dp[ci, cj + off])
                chk.append({'r_ft': round(r, 0), 'grid_psi': round(fd, 2), 'line_source_psi': round(ls, 2), 'ratio': (round(fd / ls, 3) if ls > 0 else None)})
        out['check'] = {'how': 'the grid against the line source (Theis) at the mean rate of the main well, same k h phi mu c_t; the rates vary day to day so the two differ a little by right',
                        'points': chk}
    # the projections: continued at the last 30 days' mean, and shut in
    last30 = {w: float(rate[w][-30:].mean()) for w in cells}
    for kind, q in (('continued', sources(lambda w: last30[w])), ('shut-in', sources(lambda w: 0.0))):
        dpp = last_dp.copy()
        prev = 0
        for mark in [m for m in PROJECTION_MARKS if m <= proj_days] + ([proj_days] if proj_days not in PROJECTION_MARKS else []):
            dpp = step_days(grid, dpp, q, float(mark - prev), sub * (mark - prev))
            prev = mark
            snaps.append(snapshot(n_days + mark, (d1 + timedelta(days=mark)).strftime('%Y-%m-%d'), kind, dpp))
    # the words
    rec_last = [s for s in snaps if s['kind'] == 'record'][-1]
    cont_last = [s for s in snaps if s['kind'] == 'continued'][-1]
    shut_last = [s for s in snaps if s['kind'] == 'shut-in'][-1]
    out.update(frame={'lat0': lat0, 'lon0': lon0, 'crs': f'local transverse Mercator on WGS84 centred at {lat0:.5f}, {lon0:.5f}; feet east and north', 'cells': n, 'dx_ft': round(dx, 1),
                      'half_width_ft': round(half, 0), 'why': f'2 x the radius of investigation at the end of the projection ({r_inv_end:,.0f} ft), or the farthest well and 2,000 ft',
                      'substeps_per_day': sub, 'dt_max_days': round(dt_max, 4)},
               properties={'k_md': k, 'thickness_ft': h, 'porosity': phi, 'mu_cp': mu, 'c_t_per_psi': ct, 'defaults': sd.get('defaults', []), 'uniform': True,
                           'source': fm.get('source', ''), 'depth_ft': fm.get('depth_ft'), 'top_ft': fm.get('top_ft'), 'top_subsea_ft': fm.get('top_subsea_ft')},
               wells=[{'well': w['well'], 'display': w.get('display', w['well']), 'role': w.get('role'), 'x_ft': round(wxy[w['well']][0], 0), 'y_ft': round(wxy[w['well']][1], 0),
                       'in_grid': w['well'] in cells, 'cum_bbl': round(float(rate[w['well']].sum()), 0), 'mean_bbl_d': round(float(rate[w['well']].mean()), 1),
                       'last30_bbl_d': round(last30.get(w['well'], 0.0), 1)} for w in placed],
               unplaced=unplaced, period={'start': cal[0], 'end': cal[-1], 'days': n_days}, projection_days=proj_days, threshold_psi=thr,
               faults=[{k_: v for k_, v in f.items() if k_ != 'attrs'} for f in faults], sealing_layers=sealing_ids, snapshots=snaps,
               stress={'state': ({k_: round(v, 1) for k_, v in state.items()} if state else None), 'shmax_azimuth_deg': shmax_az, 'azimuth_source': az_source, 'dips_deg': list(dips), 'mus': list(mus),
                       'why_none': (None if state else ('no stress state declared (set-stress)' if stress is None else 'no SHmax azimuth' if shmax_az is None else 'no depth for the formation: declare the disposal interval'))})
    # the first day each fault's margin is used up
    for f in out['faults']:
        rows = [(s['day'], s['kind'], r) for s in snaps for r in s['faults'] if r['name'] == f['name'] and r['layer'] == f['layer']]
        past = [(d, kd) for d, kd, r in rows if r.get('slip_status') == 'PAST THE FRICTION LINE']
        f['first_past_line'] = ({'day': past[0][0], 'kind': past[0][1]} if past else None)
        f['dp_max_record_psi'] = max([r['dp_max_psi'] for d, kd, r in rows if kd == 'record' and r['dp_max_psi'] is not None] or [None])
    L = out['lines']
    L.append(f"{formation}: {len(placed)} well(s) on a {n} x {n} grid of {dx:,.0f} ft cells ({2 * half / 5280:.1f} mi across), k {k:g} md, h {h:g} ft, porosity {phi:g}, mu {mu:g} cp, c_t {ct:g}/psi"
             + (f" ({'; '.join(sd['defaults'])})" if sd.get('defaults') else '') + f"; {sub} sub-step(s) a day. SIMULATION.")
    L.append(f"Record {cal[0]} to {cal[-1]} ({n_days} days): the rise at the end {rec_last['dp_max_psi']:,.0f} psi at most (the well's cell averaged over {dx:,.0f} ft, not the wellbore); the area over {thr:g} psi "
             f"{rec_last['aor']['area_acres']:,.0f} acres, reaching {rec_last['aor']['reach_ft']:,.0f} ft from {main.get('display', main['well'])}.")
    L.append(f"Continued {proj_days} days at the last thirty days' rates: {cont_last['aor']['area_acres']:,.0f} acres, {cont_last['aor']['reach_ft']:,.0f} ft, {cont_last['dp_max_psi']:,.0f} psi at most. "
             f"Shut in now: {shut_last['aor']['area_acres']:,.0f} acres and {shut_last['dp_max_psi']:,.0f} psi at most after {proj_days} days.")
    if out.get('check') and out['check']['points']:
        L.append('Against the line source at the end of the record: ' + '; '.join(f"{p['r_ft']:,.0f} ft grid {p['grid_psi']:g} / Theis {p['line_source_psi']:g} psi" for p in out['check']['points']) + '.')
    L.append(f"Balance: {out['balance']['net_bbl']:,.0f} bbl net in, {out['balance']['stored_bbl']:,.0f} bbl held by the grid's compressibility, {out['balance']['left_through_edge_bbl']:,.0f} bbl through the edge.")
    if faults:
        n_in = sum(1 for r in rec_last['faults'] if r['inside_aor'])
        L.append(f"{len(faults)} mapped fault trace(s) within {st.get('radius_km', 25.0):g} km ({sum(1 for f in faults if f.get('sealing'))} declared sealing); {n_in} inside the area of review at the end of the record"
                 + (f"; {sum(1 for f in out['faults'] if f.get('first_past_line'))} reach the friction line within the projection in the worst declared case" if state else f"; slip not judged: {out['stress']['why_none']}") + '.')
        for f in sorted(out['faults'], key=lambda f_: -(f_.get('dp_max_record_psi') or 0))[:6]:
            L.append(f"  {f['name']} ({f['layer_display']}, {f['distance_km']:.2f} km, strike {f['strike_deg']:g}, {'sealing' if f.get('sealing') else 'open'}): {f.get('dp_max_record_psi') or 0:g} psi at the end of the record"
                     + (f"; first past the friction line day {f['first_past_line']['day']} ({f['first_past_line']['kind']})" if f.get('first_past_line') else ''))
    else:
        L.append('No mapped fault trace within the radius (no structure layer, or none near): the plume is drawn without faults.')
    if unplaced:
        L.append(f"Not on the grid (no surface position): {', '.join(unplaced)}.")
    L.append('A projection on a uniform declared formation with an infinite-acting edge; a neighbour\'s own gauge is the check, and the next module\'s calibration.')
    return out


# --------------------------------------------------------------------------------------------------------------
# the page, the report, the 3D layers
# --------------------------------------------------------------------------------------------------------------
def _stress_for(ws, lat: float, lon: float) -> Tuple[Optional[dict], Optional[float], str]:
    stv = ws.stress()
    if stv is None:
        return None, None, ''
    az, src = stv.get('shmax_azimuth_deg'), stv.get('azimuth_source', '')
    if az is None:
        try:
            wsm = ws.wsm_at(lat, lon, 100.0)
            if wsm.get('n'):
                az = wsm['azimuth_mean']; src = f"World Stress Map records within 100 km ({wsm['n']})"
        except Exception:
            pass
    return stv, az, src


def page(ws, formation: Optional[str] = None) -> dict:
    st = settings(ws)
    fp = FL.page(ws)
    d = FL.load(ws)
    by_fm: Dict[str, List[dict]] = {}
    for wl in fp['wells']:
        if wl.get('formation') and wl.get('days'):
            by_fm.setdefault(wl['formation'], []).append(wl)
    plumes = []
    for name in sorted(by_fm):
        if formation and name != formation:
            continue
        fm = dict(d['formations'].get(name, {}))
        wells = by_fm[name]
        main = next((w for w in wells if w.get('role') == 'injection'), None)
        # the depth for the stress state: the disposal interval's midpoint of the main well
        if main and (main.get('disposal') or {}).get('top_interval_ft') is not None:
            t_, b_ = main['disposal'].get('top_interval_ft'), main['disposal'].get('base_interval_ft')
            fm['depth_ft'] = (float(t_) + float(b_)) / 2.0 if b_ is not None else float(t_)
            fm['top_ft'] = float(t_)
            el = ((main.get('surface') or {}).get('elevation') or {}).get('ft')
            fm['top_subsea_ft'] = (float(el) - float(t_)) if el is not None else None
        stress, az, src = (None, None, '')
        if main and (main.get('surface') or {}).get('lat') is not None:
            la, lo = _wgs(main['surface'])
            stress, az, src = _stress_for(ws, la, lo)
        dips = tuple((stress or {}).get('dips_deg', [60.0, 75.0, 90.0])); mus = tuple((stress or {}).get('mus', [0.6]))
        plumes.append(build(name, fm, wells, st, ws, stress, az, src, dips, mus))
    out = {'status': 'OK' if plumes else 'EMPTY', 'plumes': plumes, 'settings': st, 'generated_utc': _utc(), 'label': 'SIMULATION',
           'layers': [{'id': r['id'], 'display': r.get('display', r['id']), 'sealing': r['id'] in st.get('sealing_layers', [])} for r in ws.layers() if (r.get('kind') or 'structure') == 'structure'],
           'lines': []}
    if not plumes:
        out['lines'].append('No plume: the ledger has no injection well with a formation and a volume record (the Fluid balance tab is where they are declared).')
    for p in plumes:
        out['lines'].extend(p['lines'] if p['status'] == 'OK' else [f"{p['formation']}: {p['status']} - {'; '.join(p['notes'])}"])
    return out


def text(p: dict) -> str:
    lines = ['Pressure plume (SIMULATION)', '=' * 27]
    lines.extend(p['lines'])
    for pl in p['plumes']:
        if pl['status'] != 'OK':
            continue
        lines.append(f"  {pl['formation']}: snapshots")
        for s in pl['snapshots']:
            lines.append(f"    day {s['day']:4d} {s['date']} {s['kind']:9s} max {s['dp_max_psi']:8,.1f} psi  AoR {s['aor']['area_acres']:10,.0f} acres  reach {s['aor']['reach_ft']:8,.0f} ft"
                         + (f"  faults inside {sum(1 for f in s['faults'] if f['inside_aor'])}" if s['faults'] else ''))
    return '\n'.join(lines)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'plume')
    p['program_version'] = program_version
    with open(os.path.join(d, 'plume.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'plume.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Pressure plume (SIMULATION)\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n' + text(p) + '\n')
    files = ['plume.json', 'plume.md']
    ws.audit(actor, 'plume.report', {'files': files, 'plumes': [(pl['formation'], pl['status']) for pl in p['plumes']]})
    return {'folder': d, 'files': files, 'page': p}


def scene_layers(ws, frame: dict, p: Optional[dict] = None) -> Tuple[List[dict], List[dict]]:
    """The plume's contours as 3D layers in the scene's frame (metres), at the formation's top depth (metres below sea level);
    the end of the record and the end of the continued projection. Returns (layers, not_drawn)."""
    try:
        p = p if p is not None else page(ws)
    except Exception as e:
        return [], [{'id': 'plume', 'display': 'Pressure plume', 'why': f'not computed: {type(e).__name__}: {e}'}]
    layers, skipped = [], []
    for pl in p['plumes']:
        nm = f"Pressure plume, {pl['formation']}"
        if pl['status'] != 'OK':
            skipped.append({'id': f"plume-{pl['formation']}", 'display': nm, 'why': pl['status'].lower() + ': ' + '; '.join(pl.get('notes', []))}); continue
        z_ss = (pl['properties'] or {}).get('top_subsea_ft')
        if z_ss is None:
            skipped.append({'id': f"plume-{pl['formation']}", 'display': nm, 'why': 'no top depth: declare the disposal interval and the reference elevation of the injection well'}); continue
        z_m = -float(z_ss) * 0.3048                                   # subsea ft (negative below) -> metres below sea level, positive down
        pf = _frame(pl['frame']['lat0'], pl['frame']['lon0'])
        picks = [s for s in pl['snapshots'] if s['kind'] == 'record'][-1:] + [s for s in pl['snapshots'] if s['kind'] == 'continued'][-1:]
        for s in picks:
            lines = []
            for lv, polys in s['contours'].items():
                if float(lv) < 0:
                    continue
                for poly in polys:
                    xs, ys = [], []
                    for x, y in poly:
                        la, lo = G.unproject(x / FT_PER_M, y / FT_PER_M, pf)
                        q = G.project(la, lo, frame)
                        xs.append(round(q['easting_m'], 1)); ys.append(round(q['northing_m'], 1))
                    lines.append({'v': f'{lv} psi', 'z': round(z_m, 1), 'x': xs, 'y': ys})
            if lines:
                layers.append({'id': f"plume-{pl['formation']}-{s['kind']}-{s['day']}", 'name': f"{nm}: rise at day {s['day']} ({s['kind']}), psi contours - SIMULATION",
                               'formation': pl['formation'], 'role': 'plume', 'value_attr': 'dp psi', 'lines': lines, 'citation': 'this program\'s projection', 'vertices': sum(len(l_['x']) for l_ in lines)})
    return layers, skipped


# --------------------------------------------------------------------------------------------------------------
# the self-test
# --------------------------------------------------------------------------------------------------------------
def selftest() -> dict:
    checks = []
    k, h, phi, mu, ct = 50.0, 100.0, 0.12, 0.6, 7e-6
    n = 61; days = 60; q = 8000.0
    r_inv = FL.radius_of_investigation_ft(k, days * 24.0, phi, mu, ct)
    dx = 2.0 * 2.5 * r_inv / (n - 1)
    g = Grid(n, dx, h, phi, k, mu, ct)
    sub = max(1, int(math.ceil(1.0 / g.dt_max_days())))
    c = (n // 2, n // 2)
    dp = step_days(g, np.zeros((n, n)), {c: q}, float(days), sub * days)
    ratios = []
    for off in (3, 6, 10):
        ls = FL.line_source_dp_psi(q, k, h, phi, mu, ct, off * dx, days * 24.0)
        ratios.append(float(dp[c[0], c[1] + off]) / ls)
    checks.append({'check': 'uniform grid against the line source at 3, 6 and 10 cells (within 6%)', 'value': [round(r, 3) for r in ratios], 'pass': all(abs(r - 1.0) < 0.06 for r in ratios)})
    stored = float(np.sum(g.storage * dp))
    checks.append({'check': 'the grid holds the barrels put in (within 2%, the edge unreached)', 'value': round(stored / (q * days), 4), 'pass': abs(stored / (q * days) - 1.0) < 0.02})
    sym = float(np.max(np.abs(dp - dp.T)))
    checks.append({'check': 'the field is symmetric', 'value': round(sym, 6), 'pass': sym < 1e-6})
    # a sealing fault east of the well: the far side is shaded, the near side piles up
    g2 = Grid(n, dx, h, phi, k, mu, ct)
    xf = 4 * dx + dx / 2.0
    sealed = g2.seal_segment(xf, -20 * dx, xf, 20 * dx)
    dp2 = step_days(g2, np.zeros((n, n)), {c: q}, float(days), sub * days)
    near = float(dp2[c[0], c[1] + 4]); far_ = float(dp2[c[0], c[1] + 5]); open_near = float(dp[c[0], c[1] + 4]); open_far = float(dp[c[0], c[1] + 5])
    checks.append({'check': 'a sealing fault: the near side higher than open, the far side lower', 'value': {'faces': sealed, 'near': round(near, 1), 'open_near': round(open_near, 1), 'far': round(far_, 1), 'open_far': round(open_far, 1)},
                   'pass': sealed >= 40 and near > open_near * 1.1 and far_ < open_far * 0.5})
    # the contours of a radial field are a circle of the analytic radius
    lv = float(dp[c[0], c[1] + 6])
    lines = contours(g, dp, lv)
    big = max(lines, key=len)
    rr = [math.hypot(x, y) for x, y in big]
    checks.append({'check': 'the contour at the 6-cell rise is a circle of 6 cells radius (within half a cell), closed', 'value': {'r_mean_cells': round(float(np.mean(rr)) / dx, 2), 'spread_cells': round((max(rr) - min(rr)) / dx, 2), 'points': len(big)},
                   'pass': abs(float(np.mean(rr)) / dx - 6.0) < 0.5 and (max(rr) - min(rr)) / dx < 1.0 and len(big) > 20})
    a = area_of_review(g, dp, lv, 0.0, 0.0)
    checks.append({'check': 'the area of review at that level is pi r^2 within 15%', 'value': {'acres': a['area_acres'], 'analytic': round(math.pi * (6 * dx) ** 2 / ACRE_FT2, 1)},
                   'pass': abs(a['area_acres'] / (math.pi * (6 * dx) ** 2 / ACRE_FT2) - 1.0) < 0.15 and not a['touches_edge']})
    # shut in: the peak falls, the stored volume holds (no flow through the edge yet)
    dp3 = step_days(g, dp.copy(), {c: 0.0}, 30.0, sub * 30)
    checks.append({'check': 'shut in 30 days: the peak falls and the grid still holds the barrels', 'value': {'peak_before': round(float(dp.max()), 1), 'peak_after': round(float(dp3.max()), 1), 'held': round(float(np.sum(g.storage * dp3)) / (q * days), 4)},
                   'pass': dp3.max() < dp.max() * 0.7 and abs(float(np.sum(g.storage * dp3)) / (q * days) - 1.0) < 0.03})
    return {'ok': all(c_['pass'] for c_ in checks), 'checks': checks, 'label': 'SIMULATION', 'grid': {'n': n, 'dx_ft': round(dx, 1), 'substeps_per_day': sub}}
