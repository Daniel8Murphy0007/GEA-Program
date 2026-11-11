# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""front - the fluid front: where the injected water itself has gone, with its density and its dissolved solids.

The third fluid-flow module. The ledger (`fluid`) knows the barrels; the plume (`plume`) says where they raised
the pressure, which is miles; this module says where the water is, which is hundreds of feet - the pressure
travels as a wave through the formation's compressibility, the water has to push the formation's own water out
of the pore space ahead of it. Every result is a SIMULATION on the declared formation, and the page says so.

- THE FLOW. Around each injection well, a fine plan grid (cells of tens of feet) reaching 2.5 times the swept
  radius at the end of the projection, with every other well of the formation in it or beyond its edge. This
  close to the well the flow is steady and incompressible to a very good approximation (the radius of
  investigation passes the grid's edge in days), so each well's flow field is solved once for a unit rate - the
  steady pressure equation by conjugate gradients, the mapped faults declared SEALING as faces nothing crosses,
  the grid's edge on the well's own logarithmic potential - and a day's flow is the wells' rates times their
  unit fields, added.
- THE WATER. What is tracked is the fraction s of the net thickness the injected water occupies at each cell
  (0 the formation's own water, 1 the injected water top to bottom). It moves with the flow (second-order
  upwind with van Leer's limiter, two-stage in time, the step under the Courant limit) - and it spreads under
  its own weight: with a density difference between the injected and the formation water the heavier one runs
  under the lighter, and for equal viscosities the sharp-interface (vertical-equilibrium) equations give the
  exchange between neighbours as 0.001127 (k / mu) x 0.433 x dsg x h^2 x s (1 - s) x ds barrels a day, added to
  the flow's own. So s between 0 and 1 is a tongue: along the base when the injected water is the denser,
  along the top when it is the lighter, and the page says which; the contour s = 0.05 is the tongue's tip,
  s = 0.95 where the injected water fills the thickness. The gravity number 2 pi G / q (G the exchange
  coefficient above) says whether the well's own flow or the weight shapes the front.
- THE SOLIDS. The injected water's volume-weighted TDS and the formation water's declared TDS make the mixed
  TDS a well would produce where it drains the whole thickness: s x TDS_injected + (1 - s) x TDS_formation.
  For every producer on the grid: the day the injected water arrives (1 % of its stream), the day it is half,
  its share now and at the end of the projection, the TDS that predicts - and, where the producer's own report
  carries a TDS column, the measured value beside the predicted one, with the share the measurement implies.
  That comparison is the check this page asks for: a water analysis from a neighbour confirms the front or
  corrects it.
- THE PROJECTIONS. From the last day of the record: CONTINUED (every well at its last thirty days' mean rate) and
  SHUT IN (the injection stopped, the producers going on - they keep drawing the water in place towards them,
  and the weight keeps spreading it), each to the declared horizon (five years unless changed).
- THE ACCOUNT. Every injected barrel is in one of four places and the page adds them up: in place on the grid,
  produced back by a named producer, across a named open fault, or beyond the grid's edge. The material
  balance's UNACCOUNTED barrels (the Fluid balance tab) stand beside the account - this module moves the water
  the declared formation holds; the barrels the formation's own pressure says it did not hold are not placed
  by it.

What it will not do: move water through rock it has no map of (uniform k, h and porosity, said so); mix by
dispersion or diffusion (the front is sharp but for the scheme's own smearing of a cell or two, said so);
regional flow (no natural gradient is declared, none is applied); call any of it a measurement - the
neighbour's produced water is the measurement, and the next module's calibration is where the two are
reconciled.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import fluid as FL
from . import plume as PM

T_CONST = PM.T_CONST
FT3_PER_BBL = FL.FT3_PER_BBL
PSI_PER_FT_PER_SG = 0.433
DEFAULT_CELLS = 81
MAX_CELLS = 141
DEFAULT_PROJECTION_DAYS = 1825
PROJECTION_MARKS = (365, 730, 1825, 3650, 7300)
LEVELS = (0.05, 0.5, 0.95)
INTERFACE_LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
ARRIVAL_SHARE = 0.01
CFL = 0.4
_LOCK = threading.Lock()


class FrontError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------------------------------------------
# settings: records/front.json
# --------------------------------------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.dir('records'), 'front.json')


def settings(ws) -> dict:
    s = {'cells': DEFAULT_CELLS, 'projection_days': DEFAULT_PROJECTION_DAYS}
    p = _path(ws)
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            s.update(json.load(f))
    return s


def set_settings(ws, actor: str, cells=None, projection_days=None) -> dict:
    with _LOCK:
        s = settings(ws)
        if cells is not None and cells != '':
            v = int(cells)
            if not 41 <= v <= MAX_CELLS or v % 2 == 0:
                raise FrontError(f'the grid is an odd number of cells per side, 41 to {MAX_CELLS}')
            s['cells'] = v
        if projection_days is not None and projection_days != '':
            v = int(projection_days)
            if not 365 <= v <= 7300:
                raise FrontError('the projection is 365 to 7,300 days')
            s['projection_days'] = v
        s['declared_by'] = actor; s['declared_utc'] = _utc()
        tmp = _path(ws) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, _path(ws))
    ws.audit(actor, 'front.settings', {k: s.get(k) for k in ('cells', 'projection_days')})
    return s


# --------------------------------------------------------------------------------------------------------------
# the steady flow of one well at a unit rate
# --------------------------------------------------------------------------------------------------------------
def _ring(n: int) -> np.ndarray:
    m = np.zeros((n, n), dtype=bool)
    m[0, :] = m[-1, :] = m[:, 0] = m[:, -1] = True
    return m


def _outflow(grid: PM.Grid, p: np.ndarray) -> np.ndarray:
    """The net flow out of every cell for a pressure field (bbl/d): the steady operator."""
    out = np.zeros_like(p)
    fx = grid.Tx * (p[:, :-1] - p[:, 1:]); out[:, :-1] += fx; out[:, 1:] -= fx
    fy = grid.Ty * (p[:-1, :] - p[1:, :]); out[:-1, :] += fy; out[1:, :] -= fy
    return out


def log_potential(grid: PM.Grid, xw: float, yw: float, kh_md_ft: float) -> np.ndarray:
    """A unit rate's steady pressure in an unbounded uniform formation: p = -mu / (2 pi 0.001127 k h) ln r, per bbl/d."""
    X, Y = np.meshgrid(grid.x, grid.y)
    r = np.maximum(np.hypot(X - xw, Y - yw), grid.dx / 4.0)
    return -(grid.mu / (2.0 * math.pi * T_CONST * kh_md_ft)) * np.log(r)


def solve_unit(grid: PM.Grid, xw: float, yw: float, kh_md_ft: float, tol: float = 1e-11) -> dict:
    """The steady flow of one well injecting 1 bbl/d at (xw, yw): the pressure by conjugate gradients (Jacobi
    preconditioned) with the grid's edge on the well's logarithmic potential, and the flow across every face.
    A well beyond the grid has no cell in it and acts through the edge alone."""
    n = grid.n
    ring = _ring(n)
    pb = np.where(ring, log_potential(grid, xw, yw, kh_md_ft), 0.0)
    src = np.zeros((n, n))
    cell = grid.cell_of(xw, yw)
    inside = cell is not None and not ring[cell]
    if inside:
        src[cell] = 1.0
    rhs = src - _outflow(grid, pb); rhs[ring] = 0.0
    diag = np.zeros((n, n))
    diag[:, :-1] += grid.Tx; diag[:, 1:] += grid.Tx; diag[:-1, :] += grid.Ty; diag[1:, :] += grid.Ty
    diag = np.where(diag > 0, diag, 1.0)
    x = np.zeros((n, n))
    r = rhs.copy()
    z = r / diag
    p = z.copy()
    rz = float(np.sum(r * z))
    r0 = math.sqrt(float(np.sum(rhs * rhs))) or 1.0
    it = 0; res = 1.0
    for it in range(1, 12 * n + 1):
        Ap = _outflow(grid, p); Ap[ring] = 0.0
        pAp = float(np.sum(p * Ap))
        if pAp <= 0:
            break
        a = rz / pAp
        x += a * p; r -= a * Ap
        res = math.sqrt(float(np.sum(r * r))) / r0
        if res < tol:
            break
        z = r / diag
        rz_new = float(np.sum(r * z))
        p = z + (rz_new / rz) * p
        rz = rz_new
    pres = x + pb
    return {'p': pres, 'Fx': grid.Tx * (pres[:, :-1] - pres[:, 1:]), 'Fy': grid.Ty * (pres[:-1, :] - pres[1:, :]), 'cell': (cell if inside else None),
            'iterations': it, 'residual': res, 'converged': res < 1e-6}


# --------------------------------------------------------------------------------------------------------------
# the water: the thickness fraction s, moved by the flow and by its own weight
# --------------------------------------------------------------------------------------------------------------
def gravity_coefficient(k_md: float, mu_cp: float, dsg: float, h_ft: float) -> float:
    """G (bbl/d): the exchange between two cells is G s (1 - s) ds - the sharp-interface, equal-viscosity result."""
    return T_CONST * (k_md / mu_cp) * PSI_PER_FT_PER_SG * abs(dsg) * h_ft * h_ft


def _lim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """van Leer's limiter as a slope: the harmonic mean of the two differences where they agree in sign, else none."""
    ab = a * b
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(ab > 0, 2.0 * ab / (a + b), 0.0)


def _face(s: np.ndarray, F: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Along axis 1: the limited upwind value of s at each face for the flow F across it, and the two cells' values."""
    n = s.shape[1]
    sp = np.pad(s, ((0, 0), (2, 2)), mode='edge')
    sm1, s0, s1, s2 = sp[:, 1:n], sp[:, 2:n + 1], sp[:, 3:n + 2], sp[:, 4:n + 3]
    d0 = s1 - s0
    up = s0 + 0.5 * _lim(s0 - sm1, d0)
    dn = s1 - 0.5 * _lim(d0, s2 - s1)
    return np.where(F >= 0, up, dn), s0, s1


class Water:
    """The state and the account: s on the grid, and where every injected barrel is."""

    def __init__(self, grid: PM.Grid, pore_bbl: float, G: float):
        self.grid = grid; self.n = grid.n; self.vp = float(pore_bbl); self.G = float(G)
        self.ring = _ring(grid.n)
        self.s = np.zeros((grid.n, grid.n))
        self.open_x = (grid.Tx > 0).astype(float); self.open_y = (grid.Ty > 0).astype(float)
        self.injected = 0.0; self.edge = 0.0; self.clipped = 0.0
        self.produced: Dict[Tuple[int, int], float] = {}
        self.faults: List[dict] = []                 # each {'fx': mask, 'fy': mask, 'sx': sign, 'sy': sign, 'crossed': bbl}
        self.days = 0.0

    def watch_fault(self, fx: np.ndarray, fy: np.ndarray, side: np.ndarray, well_cell: Tuple[int, int]) -> int:
        """Count the injected water crossing these faces away from the well's side."""
        home = bool(side[well_cell])
        sx = np.where(side[:, :-1] == home, 1.0, -1.0) * fx        # flow j -> j+1 leaves the well's side when cell j is on it
        sy = np.where(side[:-1, :] == home, 1.0, -1.0) * fy
        self.faults.append({'sx': sx, 'sy': sy, 'crossed': 0.0})
        return len(self.faults) - 1

    def _rates(self, s: np.ndarray, Fx: np.ndarray, Fy: np.ndarray, inj: Dict[Tuple[int, int], float], prod: Dict[Tuple[int, int], float]):
        vx, a0, a1 = _face(s, Fx)
        qx = Fx * vx
        if self.G:
            sb = 0.5 * (a0 + a1); qx = qx + self.G * sb * (1.0 - sb) * (a0 - a1) * self.open_x
        vy, b0, b1 = _face(s.T, Fy.T)
        qy = Fy.T * vy
        if self.G:
            sb = 0.5 * (b0 + b1); qy = qy + self.G * sb * (1.0 - sb) * (b0 - b1) * self.open_y.T
        qy = qy.T
        dm = np.zeros_like(s)
        dm[:, :-1] -= qx; dm[:, 1:] += qx
        dm[:-1, :] -= qy; dm[1:, :] += qy
        edge = float(dm[self.ring].sum())
        dm[self.ring] = 0.0
        pr = {}
        for c, q in inj.items():
            dm[c] += q
        for c, q in prod.items():
            pr[c] = q * float(s[c]); dm[c] -= pr[c]
        fl = [float(np.sum(f['sx'] * qx) + np.sum(f['sy'] * qy)) for f in self.faults]
        return dm, edge, pr, fl

    def clone(self) -> 'Water':
        w = Water.__new__(Water)
        w.__dict__.update(self.__dict__)
        w.s = self.s.copy(); w.produced = dict(self.produced); w.faults = [dict(f) for f in self.faults]
        return w

    def _active(self, sources) -> np.ndarray:
        """The cells whose s can change this step: the front (0 < s < 1) and two cells around it, and a source not yet
        full. Inside the swept zone (s = 1 with s = 1 upstream) and beyond the tip nothing changes whatever the step."""
        a = (self.s > 1e-6) & (self.s < 1.0 - 1e-6)
        for c in sources:
            if self.s[c] < 1.0 - 1e-6:
                a[c] = True
        for _ in range(2):
            b = a.copy()
            b[1:, :] |= a[:-1, :]; b[:-1, :] |= a[1:, :]; b[:, 1:] |= a[:, :-1]; b[:, :-1] |= a[:, 1:]
            a = b
        return a

    def advance(self, days: float, Fx: np.ndarray, Fy: np.ndarray, inj: Dict[Tuple[int, int], float], prod: Dict[Tuple[int, int], float], max_dt: float = 5.0) -> int:
        """Move the water `days` forward under a constant flow; returns the steps taken. The step is the Courant limit
        over the cells that can change (the front and its surroundings), never over the swept zone behind it."""
        out = np.zeros((self.n, self.n))
        out[:, :-1] += np.maximum(Fx, 0.0); out[:, 1:] += np.maximum(-Fx, 0.0)
        out[:-1, :] += np.maximum(Fy, 0.0); out[1:, :] += np.maximum(-Fy, 0.0)
        for c, q in prod.items():
            out[c] += q
        out[self.ring] = 0.0
        qin = sum(inj.values())
        left = float(days); steps = 0
        while left > 1e-9:
            act = self._active(inj)
            peak = float(out[act].max()) if act.any() else 0.0
            dt = max_dt if peak <= 0 else min(max_dt, CFL * self.vp / peak)
            if self.G and act.any():
                dt = min(dt, CFL * self.vp / self.G)             # 4 faces x G x max s(1-s) = G
            dt = min(dt, left)
            if left - dt < 1e-6 * max(dt, 1e-9):
                dt = left
            k1, e1, p1, f1 = self._rates(self.s, Fx, Fy, inj, prod)
            s1 = np.clip(self.s + dt * k1 / self.vp, 0.0, 1.0)
            k2, e2, p2, f2 = self._rates(s1, Fx, Fy, inj, prod)
            raw = self.s + 0.5 * dt * (k1 + k2) / self.vp
            new = np.clip(raw, 0.0, 1.0)
            self.clipped += float((raw - new).sum()) * self.vp
            self.s = new
            self.injected += qin * dt
            self.edge += 0.5 * (e1 + e2) * dt
            for c in prod:
                self.produced[c] = self.produced.get(c, 0.0) + 0.5 * (p1[c] + p2[c]) * dt
            for i, f in enumerate(self.faults):
                f['crossed'] += 0.5 * (f1[i] + f2[i]) * dt
            left -= dt; steps += 1
        self.days += days
        return steps

    def in_place(self) -> float:
        return float(self.s.sum()) * self.vp

    def radii(self, xw: float, yw: float) -> dict:
        g = self.grid
        X, Y = np.meshgrid(g.x, g.y)
        r = np.hypot(X - xw, Y - yw)
        out = {}
        for name, lv in (('tip', LEVELS[0]), ('half', LEVELS[1]), ('full', LEVELS[2])):
            sel = self.s >= lv
            out[name + '_reach_ft'] = round(float(r[sel].max()) + g.dx / 2.0, 0) if sel.any() else 0.0
            out[name + '_equivalent_ft'] = round(math.sqrt(float(sel.sum()) * g.dx * g.dx / math.pi), 0)
        return out


# --------------------------------------------------------------------------------------------------------------
# the front of one injection well
# --------------------------------------------------------------------------------------------------------------
def _weighted(days: List[dict], key: str) -> Optional[float]:
    num = den = 0.0
    for d in days:
        v, w = d.get(key), d.get('volume_bbl')
        if v is not None and w:
            num += float(v) * abs(w); den += abs(w)
    return (num / den) if den else None


def _regime(gamma: Optional[float]) -> str:
    if gamma is None:
        return 'no density difference declared: the front is a piston through the whole thickness'
    if gamma < 0.1:
        return "the well's own flow shapes the front: close to a piston, the tongue a thin skirt"
    if gamma <= 10.0:
        return "the flow and the weight both shape the front: a tongue ahead of a slanted interface"
    return 'the weight shapes the front: a thin tongue far ahead of the swept zone'


def build(inj: dict, fm: dict, wells: List[dict], st: dict, ws=None, sealing_ids: Optional[List[str]] = None, radius_km: float = 25.0,
          balance: Optional[dict] = None) -> dict:
    """One injection well's front. `wells` are the formation's ledger wells (the injector among them), each with its
    days, role and surface position; `st` the front settings; `balance` the formation's material balance, for the
    account's last line."""
    name = inj.get('display', inj['well'])
    out: Dict[str, Any] = {'well': inj['well'], 'display': name, 'formation': inj.get('formation'), 'status': 'OK', 'notes': [], 'lines': [], 'label': 'SIMULATION'}
    for k_ in ('k_md', 'thickness_ft', 'porosity'):
        if fm.get(k_) is None:
            out.update(status='NOT DECLARED', notes=[f'the formation has no {k_.replace("_", " ")} declared: gea fluid --formation NAME --k-md --thickness-ft --porosity --source ...'])
            return out
    if (inj.get('surface') or {}).get('lat') is None:
        out.update(status='NO SURFACE POSITION', notes=['the injection well has no declared surface position']); return out
    idays = [d for d in inj.get('days', []) if d['volume_bbl'] is not None]
    if not idays or sum(d['volume_bbl'] for d in idays) <= 0:
        out.update(status='NO RECORD', notes=['no day with an injected volume']); return out
    k = float(fm['k_md']); h = float(fm['thickness_ft']); phi = float(fm['porosity']); mu = float(fm.get('mu_cp') or FL.MU_WATER_CP)
    placed = [w for w in wells if w.get('days') and (w.get('surface') or {}).get('lat') is not None]
    if not any(w['well'] == inj['well'] for w in placed):
        placed.append(inj)
    # the calendar: from the injector's first day to the last day of any record
    d0 = datetime.strptime(idays[0]['date'], '%Y-%m-%d')
    d1 = max(datetime.strptime(d['date'], '%Y-%m-%d') for w in placed for d in w['days'] if d['volume_bbl'] is not None)
    n_days = (d1 - d0).days + 1
    cal = [(d0 + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n_days)]
    rate = {w['well']: np.zeros(n_days) for w in placed}
    for w in placed:
        for d in w['days']:
            if d['volume_bbl'] is not None:
                i = (datetime.strptime(d['date'], '%Y-%m-%d') - d0).days
                if 0 <= i < n_days:
                    rate[w['well']][i] += d['volume_bbl']
    last30 = {w: float(r[-30:].mean()) for w, r in rate.items()}
    proj_days = int(st.get('projection_days', DEFAULT_PROJECTION_DAYS))
    cum_in = float(rate[inj['well']].sum())
    v_end = cum_in + max(last30[inj['well']], 0.0) * proj_days
    r_end = FL.swept_radius_ft(v_end, h, phi)
    # the frame and the grid
    lat0, lon0 = PM._wgs(inj['surface'])
    frame = PM._frame(lat0, lon0)
    wxy = {}
    for w in placed:
        la, lo = PM._wgs(w['surface']); wxy[w['well']] = PM._xy_ft(frame, la, lo)
    wxy[inj['well']] = (0.0, 0.0)
    half = max(2.5 * r_end, 1000.0)
    for wid, (x, y) in wxy.items():
        dist = math.hypot(x, y)
        if wid != inj['well'] and dist <= 4.0 * r_end:
            half = max(half, 1.25 * dist)
    n = int(st.get('cells', DEFAULT_CELLS))
    dx = 2.0 * half / (n - 1)
    grid = PM.Grid(n, dx, h, phi, k, mu, 1.0)
    vp = dx * dx * h * phi / FT3_PER_BBL
    ring = _ring(n)
    # the faults: sealing ones close their faces, open ones are watched
    sealing_ids = list(sealing_ids or [])
    faults = PM.fault_segments(ws, frame, lat0, lon0, radius_km, sealing_ids) if ws is not None else []
    for f in faults:
        if f.get('sealing'):
            f['faces_sealed'] = sum(grid.seal_segment(*sg) for sg in f['segments'])
    # the densities
    inj_sg = _weighted(idays, 'sg'); inj_tds = _weighted(idays, 'tds_mgl')
    fm_sg = fm.get('water_sg'); fm_tds = fm.get('water_tds_mgl')
    dsg = (inj_sg - fm_sg) if (inj_sg is not None and fm_sg is not None) else None
    G = gravity_coefficient(k, mu, dsg, h) if dsg else 0.0
    q_mean = cum_in / max(1, int((rate[inj['well']] > 0).sum()))
    gamma = (2.0 * math.pi * G / q_mean) if (dsg is not None and q_mean > 0) else None
    tongue = None if not dsg else ('along the base (the injected water is the denser)' if dsg > 0 else 'along the top (the injected water is the lighter)')
    if dsg is None:
        out['notes'].append('no density for the injected water or the formation water: no gravity term, the front is a piston (declare both on the Fluid balance tab)')
    # the unit flow of every placed well
    unit = {}
    for wid, (x, y) in wxy.items():
        u = solve_unit(grid, x, y, k * h)
        if not u['converged']:
            if wid == inj['well']:
                out.update(status='NO STEADY FLOW', notes=[f"the flow of {name} does not settle on this grid (residual {u['residual']:.1e}): the well is enclosed by sealing faults within {half:,.0f} ft"])
                return out
            out['notes'].append(f"{wid}: its flow did not settle (residual {u['residual']:.1e}); left out")
            continue
        unit[wid] = u
    cells = {wid: u['cell'] for wid, u in unit.items() if u['cell'] is not None}
    water = Water(grid, vp, G)
    icell = cells[inj['well']]
    watch = {}
    for fi, f in enumerate(faults):
        if f.get('sealing') or not f['segments']:
            continue
        idx = []
        for sg in f['segments']:
            sf = grid.segment_faces(*sg)
            if sf is not None and (sf[0].any() or sf[1].any()):
                idx.append(water.watch_fault(sf[0], sf[1], sf[2], icell))
        if idx:
            watch[fi] = idx
    producers = [w for w in placed if w.get('role') == 'production']
    pcells = {w['well']: cells[w['well']] for w in producers if w['well'] in cells}
    thresholds = (ARRIVAL_SHARE, 0.1, 0.5)
    crossed: Dict[str, Dict[float, Optional[dict]]] = {wid: {t: None for t in thresholds} for wid in pcells}
    share_on: Dict[str, Dict[str, float]] = {wid: {} for wid in pcells}

    def flow(rates: Dict[str, float]):
        Fx = np.zeros((n, n - 1)); Fy = np.zeros((n - 1, n)); inj_q: Dict[Tuple[int, int], float] = {}; prod_q: Dict[Tuple[int, int], float] = {}
        for wid, q in rates.items():
            if wid not in unit or q == 0.0:
                continue
            Fx += q * unit[wid]['Fx']; Fy += q * unit[wid]['Fy']
            c = cells.get(wid)
            if c is not None:
                if q > 0:
                    inj_q[c] = inj_q.get(c, 0.0) + q
                else:
                    prod_q[c] = prod_q.get(c, 0.0) - q
        return Fx, Fy, inj_q, prod_q

    def note_shares(w_: Water, day: float, kind: str, before: Dict[str, float], span: float):
        for wid, c in pcells.items():
            v = float(w_.s[c])
            for t in thresholds:
                if crossed[wid][t] is None and v >= t:
                    b = before.get(wid, 0.0)
                    frac = (t - b) / (v - b) if v > b else 1.0
                    crossed[wid][t] = {'day': round(day - span + frac * span, 1), 'kind': kind}

    def snapshot(w_: Water, day: int, date: str, kind: str, interface: bool = False) -> dict:
        sn = {'day': day, 'date': date, 'kind': kind, 'injected_bbl': round(w_.injected, 0), 'in_place_bbl': round(w_.in_place(), 0),
              'produced_back_bbl': {wid: round(w_.produced.get(c, 0.0), 0) for wid, c in pcells.items()}, 'edge_bbl': round(w_.edge, 0),
              'piston_ft': round(FL.swept_radius_ft(max(w_.in_place(), 0.0), h, phi), 0), **w_.radii(0.0, 0.0),
              'shares': {wid: round(float(w_.s[c]), 4) for wid, c in pcells.items()},
              'faults': [{'name': faults[fi]['name'], 'layer': faults[fi]['layer'], 'crossed_bbl': round(sum(w_.faults[i]['crossed'] for i in idx), 0)} for fi, idx in watch.items()],
              'contours': {f'{lv:g}': [[[round(x, 0), round(y, 0)] for x, y in line] for line in PM.contours(grid, w_.s, lv) if len(line) >= 3] for lv in LEVELS}}
        if interface:
            sn['interface'] = {f'{lv:g}': [[[round(x, 0), round(y, 0)] for x, y in line] for line in PM.contours(grid, w_.s, lv) if len(line) >= 3] for lv in INTERFACE_LEVELS}
        return sn
    # the record, day by day
    marks = set(PM._month_ends(cal)); marks.add(cal[-1])
    snaps = []
    steps = 0
    for i, date in enumerate(cal):
        rates = {wid: float(r[i]) for wid, r in rate.items()}
        before = {wid: float(water.s[c]) for wid, c in pcells.items()}
        if any(rates.values()) or G:
            Fx, Fy, iq, pq = flow(rates)
            steps += water.advance(1.0, Fx, Fy, iq, pq, max_dt=1.0)
        note_shares(water, i + 1, 'record', before, 1.0)
        for wid, c in pcells.items():
            share_on[wid][date] = float(water.s[c])
        if date in marks:
            snaps.append(snapshot(water, i + 1, date, 'record', interface=(date == cal[-1])))
    # the projections: continued at the last thirty days' rates, and shut in
    pmarks = [m for m in PROJECTION_MARKS if m < proj_days] + [proj_days]
    shut = {wid: (0.0 if q_ > 0 else q_) for wid, q_ in last30.items()}          # the injection stops; the producers go on
    for kind, rates in (('continued', dict(last30)), ('shut-in', shut)):
        w2 = water.clone()
        if kind == 'shut-in' and not G and not any(shut.values()):
            for m in pmarks[-1:]:
                snaps.append(snapshot(w2, n_days + m, (d1 + timedelta(days=m)).strftime('%Y-%m-%d'), kind))
            continue
        Fx, Fy, iq, pq = flow(rates)
        t = 0
        for m in pmarks:
            while t < m:
                span = float(min(10, m - t))
                before = {wid: float(w2.s[c]) for wid, c in pcells.items()}
                steps += w2.advance(span, Fx, Fy, iq, pq, max_dt=5.0)
                t += int(span)
                if kind == 'continued':
                    note_shares(w2, n_days + t, kind, before, span)
            snaps.append(snapshot(w2, n_days + m, (d1 + timedelta(days=m)).strftime('%Y-%m-%d'), kind, interface=(kind == 'continued' and m == pmarks[-1])))
    rec_last = [s_ for s_ in snaps if s_['kind'] == 'record'][-1]
    cont_last = [s_ for s_ in snaps if s_['kind'] == 'continued'][-1]
    shut_last = [s_ for s_ in snaps if s_['kind'] == 'shut-in'][-1]
    # the producers
    rows = []
    for w in producers:
        x, y = wxy[w['well']]
        row: Dict[str, Any] = {'well': w['well'], 'display': w.get('display', w['well']), 'distance_ft': round(math.hypot(x, y), 0), 'in_grid': w['well'] in pcells}
        if w['well'] not in pcells:
            row['note'] = f'beyond the front\'s grid ({half:,.0f} ft): the injected water does not reach it within the projection'
            rows.append(row); continue
        row.update(share_now=rec_last['shares'][w['well']], share_projected=cont_last['shares'][w['well']],
                   arrival=crossed[w['well']][ARRIVAL_SHARE], tenth=crossed[w['well']][0.1], half=crossed[w['well']][0.5],
                   produced_back_bbl=rec_last['produced_back_bbl'][w['well']])
        if inj_tds is not None and fm_tds is not None:
            row['tds_predicted_now'] = round(row['share_now'] * inj_tds + (1 - row['share_now']) * fm_tds, 0)
            row['tds_predicted_projected'] = round(row['share_projected'] * inj_tds + (1 - row['share_projected']) * fm_tds, 0)
            meas = []
            for d in w['days']:
                if d.get('tds_mgl') is not None and d.get('density_from') == 'the report' and d['date'] in share_on[w['well']]:
                    sp = share_on[w['well']][d['date']]
                    pred = sp * inj_tds + (1 - sp) * fm_tds
                    implied = (d['tds_mgl'] - fm_tds) / (inj_tds - fm_tds) if abs(inj_tds - fm_tds) > 1.0 else None
                    meas.append({'date': d['date'], 'measured_tds': d['tds_mgl'], 'predicted_tds': round(pred, 0), 'predicted_share': round(sp, 4),
                                 'implied_share': (round(implied, 4) if implied is not None else None)})
            # one row per change of the measured value, the last twelve kept: a daily column repeats an analysis
            dedup = [m for i, m in enumerate(meas) if i == 0 or m['measured_tds'] != meas[i - 1]['measured_tds']]
            row['measured'] = dedup[-12:]; row['n_measured'] = len(dedup)
            if dedup:
                row['mean_abs_difference_tds'] = round(sum(abs(m['measured_tds'] - m['predicted_tds']) for m in dedup) / len(dedup), 0)
                last = dedup[-1]
                if last['implied_share'] is not None:
                    row['reading'] = ('the measured water carries more of the injected water than the front predicts: it arrives sooner - a faster path (a streak, a fracture) or a thinner swept thickness than declared'
                                      if last['implied_share'] > last['predicted_share'] + 0.05 else
                                      'the measured water carries less of the injected water than the front predicts: it arrives later - more pore volume in the way, or part of the water going elsewhere'
                                      if last['implied_share'] < last['predicted_share'] - 0.05 else
                                      'the measured water agrees with the front within five points of share')
        elif any(d.get('tds_mgl') is not None for d in w['days']):
            row['note'] = 'its report carries a TDS, but the injected or the formation water\'s TDS is not declared: no comparison'
        rows.append(row)
    # the account at the end of the record
    produced_back = sum(rec_last['produced_back_bbl'].values()); across = sum(f['crossed_bbl'] for f in rec_last['faults'])
    acct = {'injected_bbl': rec_last['injected_bbl'], 'in_place_bbl': rec_last['in_place_bbl'], 'produced_back_bbl': round(produced_back, 0), 'beyond_edge_bbl': rec_last['edge_bbl'],
            'across_open_faults_bbl': round(across, 0), 'across_note': 'the water across an open fault is still in place on the grid, on the far side; it is named, not subtracted',
            'closure_bbl': round(rec_last['injected_bbl'] - rec_last['in_place_bbl'] - produced_back - rec_last['edge_bbl'], 0), 'clipped_bbl': round(water.clipped, 2)}
    if balance and balance.get('unaccounted_bbl') is not None:
        acct['balance_unaccounted_bbl'] = balance['unaccounted_bbl']; acct['balance_verdict'] = balance.get('verdict')
    out.update(frame={'lat0': lat0, 'lon0': lon0, 'crs': f'local transverse Mercator on WGS84 centred on {name} ({lat0:.5f}, {lon0:.5f}); feet east and north', 'cells': n, 'dx_ft': round(dx, 1),
                      'half_width_ft': round(half, 0), 'why': f'2.5 x the swept radius at the end of the projection ({r_end:,.0f} ft), widened to hold the wells within four of them', 'steps': steps},
               properties={'k_md': k, 'thickness_ft': h, 'porosity': phi, 'mu_cp': mu, 'uniform': True, 'source': fm.get('source', ''), 'top_ft': fm.get('top_ft'), 'top_subsea_ft': fm.get('top_subsea_ft')},
               density={'injected_sg': (round(inj_sg, 4) if inj_sg is not None else None), 'formation_sg': fm_sg, 'delta_sg': (round(dsg, 4) if dsg is not None else None),
                        'injected_tds_mgl': (round(inj_tds, 0) if inj_tds is not None else None), 'formation_tds_mgl': fm_tds, 'gravity_bbl_d': round(G, 2),
                        'gravity_number': (round(gamma, 4) if gamma is not None else None), 'regime': _regime(gamma), 'tongue': tongue},
               wells=[{'well': wid, 'display': next((w.get('display', wid) for w in placed if w['well'] == wid), wid), 'role': next((w.get('role') for w in placed if w['well'] == wid), None),
                       'x_ft': round(x, 0), 'y_ft': round(y, 0), 'in_grid': wid in cells, 'last30_bbl_d': round(last30.get(wid, 0.0), 1)} for wid, (x, y) in wxy.items()],
               period={'start': cal[0], 'end': cal[-1], 'days': n_days}, projection_days=proj_days, producers=rows, account=acct, snapshots=snaps,
               faults=[{k_: v for k_, v in f.items() if k_ != 'attrs'} | {'watched': fi in watch} for fi, f in enumerate(faults)], sealing_layers=sealing_ids, mean_rate_bbl_d=round(q_mean, 1))
    L = out['lines']
    L.append(f"{name} into {inj.get('formation')}: {cum_in:,.0f} bbl injected over {n_days} days ({cal[0]} to {cal[-1]}), on a {n} x {n} grid of {dx:,.0f} ft cells ({2 * half:,.0f} ft across), "
             f"h {h:g} ft, porosity {phi:g}, k {k:g} md. SIMULATION.")
    L.append(f"At the end of the record the injected water fills the thickness out to {rec_last['full_equivalent_ft']:,.0f} ft, is half of it at {rec_last['half_equivalent_ft']:,.0f} ft "
             f"(a piston of the same volume: {rec_last['piston_ft']:,.0f} ft) and its tip reaches {rec_last['tip_reach_ft']:,.0f} ft"
             + (f", {tongue}" if tongue else '') + '.')
    if gamma is not None:
        L.append(f"Injected water sg {inj_sg:.4f} against the formation's {fm_sg:g} (difference {dsg:+.4f}): gravity number {gamma:.3g} at {q_mean:,.0f} bbl/d - {_regime(gamma)}.")
    else:
        L.append(_regime(None).capitalize() + '.')
    L.append(f"Continued {proj_days} days at the last thirty days' rates: full thickness to {cont_last['full_equivalent_ft']:,.0f} ft, half at {cont_last['half_equivalent_ft']:,.0f} ft, the tip at {cont_last['tip_reach_ft']:,.0f} ft. "
             f"Injection shut in now (the producers going on): the tip at {shut_last['tip_reach_ft']:,.0f} ft after {proj_days} days"
             + (' - nothing moves it.' if (not G and not any(shut.values())) else ', ' + ', '.join(f"{r_['display']} produces back {shut_last['produced_back_bbl'].get(r_['well'], 0) - rec_last['produced_back_bbl'].get(r_['well'], 0):,.0f} bbl more" for r_ in rows if r_['in_grid']) + '.' if any(r_['in_grid'] for r_ in rows) else ' - the water slumps under its weight and goes nowhere else.'))
    for r in rows:
        if not r['in_grid']:
            L.append(f"{r['display']} ({r['distance_ft']:,.0f} ft): {r['note']}."); continue
        a = r['arrival']
        L.append(f"{r['display']} ({r['distance_ft']:,.0f} ft): " + (f"the injected water arrives on day {a['day']:g} ({a['kind']})" if a else 'the injected water does not arrive within the projection')
                 + f"; {r['share_now'] * 100:.1f}% of its stream now, {r['share_projected'] * 100:.1f}% at the end of the projection"
                 + (f"; predicted TDS {r['tds_predicted_now']:,.0f} mg/L now" if 'tds_predicted_now' in r else '')
                 + (f"; measured {r['measured'][-1]['measured_tds']:,.0f} against {r['measured'][-1]['predicted_tds']:,.0f} predicted on {r['measured'][-1]['date']} - {r['reading']}" if r.get('measured') and r.get('reading') else '') + '.')
    L.append(f"Account at {cal[-1]}: {acct['injected_bbl']:,.0f} bbl injected = {acct['in_place_bbl']:,.0f} in place on the grid + {acct['produced_back_bbl']:,.0f} produced back + {acct['beyond_edge_bbl']:,.0f} beyond the edge"
             f" (closure {acct['closure_bbl']:,.0f} bbl)" + (f"; {acct['across_open_faults_bbl']:,.0f} bbl of it across an open fault" if rec_last['faults'] else '') + '.')
    if acct.get('balance_unaccounted_bbl') is not None and acct.get('balance_verdict') == FL.STATUS_MORE:
        L.append(f"The material balance holds {acct['balance_unaccounted_bbl']:,.0f} bbl UNACCOUNTED at the observed pressure: this page moves the water as the declared formation would; those barrels are not placed by it, "
                 "and the swept zone drawn here is the most the declared thickness and porosity can be said to hold near the well.")
    L.append('Uniform declared rock, a sharp front but for a cell or two of the scheme\'s own smearing, no regional flow: a projection, and a neighbour\'s produced water is the check.')
    return out


# --------------------------------------------------------------------------------------------------------------
# the page, the report, the 3D interface
# --------------------------------------------------------------------------------------------------------------
_CACHE: Dict[str, Tuple[str, dict]] = {}


def page(ws, well: Optional[str] = None) -> dict:
    import hashlib
    st = settings(ws)
    pst = PM.settings(ws)
    fp = FL.page(ws)
    d = FL.load(ws)
    key = hashlib.sha256(json.dumps([st, pst.get('sealing_layers'), pst.get('radius_km'), d['formations'], [r_['id'] for r_ in ws.layers()],
                                     [{k_: v for k_, v in w.items() if k_ in ('well', 'role', 'formation', 'days', 'surface', 'disposal')} for w in fp['wells']],
                                     [(f_['formation'], f_.get('unaccounted_bbl'), f_.get('verdict')) for f_ in fp['formations']]], sort_keys=True, default=str).encode()).hexdigest()
    hit = _CACHE.get(ws.path)
    if hit and hit[0] == key:
        full = hit[1]
    else:
        by_fm: Dict[str, List[dict]] = {}
        for wl in fp['wells']:
            if wl.get('formation') and wl.get('days'):
                by_fm.setdefault(wl['formation'], []).append(wl)
        fronts = []
        for name in sorted(by_fm):
            wells = by_fm[name]
            bal = next((f_ for f_ in fp['formations'] if f_['formation'] == name), None)
            for inj in wells:
                if inj.get('role') != 'injection':
                    continue
                fm = dict(d['formations'].get(name, {}))
                disp = inj.get('disposal') or {}
                if disp.get('top_interval_ft') is not None:
                    fm['top_ft'] = float(disp['top_interval_ft'])
                    el = ((inj.get('surface') or {}).get('elevation') or {}).get('ft')
                    fm['top_subsea_ft'] = (float(el) - float(disp['top_interval_ft'])) if el is not None else None
                fronts.append(build(inj, fm, wells, st, ws, pst.get('sealing_layers', []), float(pst.get('radius_km', 25.0)), bal))
        full = {'status': 'OK' if fronts else 'EMPTY', 'fronts': fronts, 'settings': st, 'generated_utc': _utc(), 'label': 'SIMULATION', 'lines': []}
        if not fronts:
            full['lines'].append('No front: the ledger has no injection well with a formation and a volume record (the Fluid balance tab is where they are declared).')
        for fr in fronts:
            full['lines'].extend(fr['lines'] if fr['status'] == 'OK' else [f"{fr['display']}: {fr['status']} - {'; '.join(fr['notes'])}"])
        _CACHE[ws.path] = (key, full)
    out = dict(full)
    if well:
        out['fronts'] = [fr for fr in full['fronts'] if fr['well'] == well]
        if not out['fronts']:
            out['status'] = 'EMPTY'
    return out


def text(p: dict) -> str:
    lines = ['Fluid front (SIMULATION)', '=' * 24]
    lines.extend(p['lines'])
    for fr in p['fronts']:
        if fr['status'] != 'OK':
            continue
        lines.append(f"  {fr['display']}: snapshots")
        for s in fr['snapshots']:
            lines.append(f"    day {s['day']:5d} {s['date']} {s['kind']:9s} full {s['full_equivalent_ft']:6,.0f} ft  half {s['half_equivalent_ft']:6,.0f} ft  tip {s['tip_reach_ft']:6,.0f} ft  piston {s['piston_ft']:6,.0f} ft  in place {s['in_place_bbl']:12,.0f} bbl"
                         + ''.join(f"  {wid} {v * 100:.1f}%" for wid, v in s['shares'].items()))
    return '\n'.join(lines)


def write_report(ws, program_version: str, actor: str = 'system') -> dict:
    p = page(ws)
    d = ws.dir('reports', 'front')
    p['program_version'] = program_version
    with open(os.path.join(d, 'front.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=1, default=str)
    with open(os.path.join(d, 'front.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Fluid front (SIMULATION)\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}.\n\n" + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n' + text(p) + '\n')
    files = ['front.json', 'front.md']
    ws.audit(actor, 'front.report', {'files': files, 'fronts': [(fr['well'], fr['status']) for fr in p['fronts']]})
    return {'folder': d, 'files': files, 'page': p}


def scene_layers(ws, frame: dict, p: Optional[dict] = None) -> Tuple[List[dict], List[dict]]:
    """The injected water's interface as 3D layers in the scene's frame (metres, depth below sea level positive down): the
    contour where the water fills a fraction L of the thickness, drawn at the depth the interface stands at there - L of
    the thickness above the base when the injected water is the denser, below the top when the lighter. The end of the
    record and the end of the continued projection. Returns (layers, not_drawn)."""
    from . import geodesy as G
    try:
        p = p if p is not None else page(ws)
    except Exception as e:
        return [], [{'id': 'front', 'display': 'Fluid front', 'why': f'not computed: {type(e).__name__}: {e}'}]
    layers, skipped = [], []
    for fr in p['fronts']:
        nm = f"Fluid front, {fr['display']}"
        if fr['status'] != 'OK':
            skipped.append({'id': f"front-{fr['well']}", 'display': nm, 'why': fr['status'].lower() + ': ' + '; '.join(fr.get('notes', []))}); continue
        top_ss = fr['properties'].get('top_subsea_ft')
        if top_ss is None:
            skipped.append({'id': f"front-{fr['well']}", 'display': nm, 'why': 'no top depth: declare the disposal interval and the reference elevation of the injection well'}); continue
        h = fr['properties']['thickness_ft']; dsg = fr['density'].get('delta_sg')
        pf = PM._frame(fr['frame']['lat0'], fr['frame']['lon0'])
        for s in [s_ for s_ in fr['snapshots'] if s_.get('interface')]:
            lines = []
            for lv, polys in s['interface'].items():
                L = float(lv)
                if dsg and dsg > 0:
                    depths = [h * (1.0 - L)]                    # ft below the top: the interface L of the thickness above the base
                elif dsg and dsg < 0:
                    depths = [h * L]
                else:
                    depths = [0.0, h] if abs(L - 0.5) < 1e-9 else []      # a piston: the half contour at the top and at the base
                for dz in depths:
                    z_m = -(float(top_ss) - dz) * 0.3048
                    for poly in polys:
                        xs, ys = [], []
                        for x, y in poly:
                            la, lo = G.unproject(x / PM.FT_PER_M, y / PM.FT_PER_M, pf)
                            q = G.project(la, lo, frame)
                            xs.append(round(q['easting_m'], 1)); ys.append(round(q['northing_m'], 1))
                        lines.append({'v': f'{L:g} of the thickness', 'z': round(z_m, 1), 'x': xs, 'y': ys})
            if lines:
                layers.append({'id': f"front-{fr['well']}-{s['kind']}-{s['day']}", 'name': f"{nm}: the injected water's interface at day {s['day']} ({s['kind']}) - SIMULATION",
                               'formation': fr.get('formation') or '', 'role': 'front', 'value_attr': 'fraction of the thickness', 'lines': lines, 'citation': 'this program\'s projection',
                               'vertices': sum(len(l_['x']) for l_ in lines)})
    return layers, skipped


# --------------------------------------------------------------------------------------------------------------
# the self-test
# --------------------------------------------------------------------------------------------------------------
def selftest() -> dict:
    checks = []
    k, h, phi, mu = 50.0, 100.0, 0.12, 0.6
    n = 81; q = 8000.0; days = 240
    V = q * days; rp = FL.swept_radius_ft(V, h, phi)
    dx = 5.0 * rp / (n - 1)
    g = PM.Grid(n, dx, h, phi, k, mu, 1.0)
    vp = dx * dx * h * phi / FT3_PER_BBL
    u = solve_unit(g, 0.0, 0.0, k * h)
    c = u['cell']; i0, j0 = c; R = 12
    ringq = (u['Fx'][i0 - R:i0 + R + 1, j0 + R].sum() - u['Fx'][i0 - R:i0 + R + 1, j0 - R - 1].sum() + u['Fy'][i0 + R, j0 - R:j0 + R + 1].sum() - u['Fy'][i0 - R - 1, j0 - R:j0 + R + 1].sum())
    checks.append({'check': 'the unit flow settles and a box around the well passes exactly the well\'s rate', 'value': {'iterations': u['iterations'], 'box': round(float(ringq), 9)},
                   'pass': u['converged'] and abs(ringq - 1.0) < 1e-6})
    # along the row through the well the radial flow is q / (2 pi r) per foot of arc: the face 10 cells out carries dx / (2 pi r) of it
    fr_ = float(u['Fx'][i0, j0 + 10]); want = dx / (2.0 * math.pi * 10.5 * dx)
    checks.append({'check': 'the flow across a face ten cells out is the radial share (within 3%)', 'value': round(fr_ / want, 4), 'pass': abs(fr_ / want - 1.0) < 0.03})
    w = Water(g, vp, 0.0)
    w.advance(float(days), u['Fx'] * q, u['Fy'] * q, {c: q}, {}, max_dt=1.0)
    r = w.radii(0.0, 0.0)
    checks.append({'check': 'one well, no density difference: the half contour is the piston radius (within a cell)', 'value': {'piston_ft': round(rp, 0), 'half_ft': r['half_equivalent_ft'], 'dx_ft': round(dx, 1)},
                   'pass': abs(r['half_equivalent_ft'] - rp) < dx})
    checks.append({'check': 'the front is sharp: tip to full within four cells', 'value': round((r['tip_equivalent_ft'] - r['full_equivalent_ft']) / dx, 2), 'pass': (r['tip_equivalent_ft'] - r['full_equivalent_ft']) < 4.0 * dx})
    checks.append({'check': 'every injected barrel is in place', 'value': round(w.in_place() / V, 9), 'pass': abs(w.in_place() / V - 1.0) < 1e-6 and abs(w.edge) < 1e-6 * V})
    # the weight: a denser water runs out along the base - the tip farther, the full thickness nearer, the volume the same
    Gc = gravity_coefficient(k, mu, 0.2, h)
    w2 = Water(g, vp, Gc); w2.advance(float(days), u['Fx'] * q, u['Fy'] * q, {c: q}, {}, max_dt=1.0)
    r2 = w2.radii(0.0, 0.0)
    checks.append({'check': 'a density difference of 0.2 sg: the tip farther, the full thickness nearer, the volume the same', 'value': {'tip_ft': r2['tip_equivalent_ft'], 'full_ft': r2['full_equivalent_ft'], 'gravity_number': round(2 * math.pi * Gc / q, 4)},
                   'pass': r2['tip_equivalent_ft'] > r['tip_equivalent_ft'] + 0.5 * dx and r2['full_equivalent_ft'] < r['full_equivalent_ft'] - 0.5 * dx and abs(w2.in_place() / V - 1.0) < 1e-6})
    # shut in, the tongue keeps slumping and nothing is lost
    w3 = w2.clone(); w3.advance(3650.0, u['Fx'] * 0.0, u['Fy'] * 0.0, {}, {}, max_dt=30.0)
    r3 = w3.radii(0.0, 0.0)
    checks.append({'check': 'shut in ten years: the tongue slumps on, every barrel still in place', 'value': {'tip_ft': r3['tip_equivalent_ft'], 'held': round(w3.in_place() / V, 9)},
                   'pass': r3['tip_equivalent_ft'] > r2['tip_equivalent_ft'] and abs(w3.in_place() / V - 1.0) < 1e-6})
    # the doublet: an injector and an equal producer d apart; the first water arrives at pi phi h d^2 / (3 q)
    d = 600.0; n2 = 101; dx2 = 3000.0 / (n2 - 1)
    g2 = PM.Grid(n2, dx2, h, phi, k, mu, 1.0); vp2 = dx2 * dx2 * h * phi / FT3_PER_BBL
    ui = solve_unit(g2, -d / 2.0, 0.0, k * h); up = solve_unit(g2, d / 2.0, 0.0, k * h)
    Fx = q * (ui['Fx'] - up['Fx']); Fy = q * (ui['Fy'] - up['Fy'])
    tbt = math.pi * phi * h * d * d / (3.0 * q * FT3_PER_BBL)
    w4 = Water(g2, vp2, 0.0); cp = up['cell']; hist = []
    for _ in range(int(1.4 * tbt)):
        w4.advance(1.0, Fx, Fy, {ui['cell']: q}, {cp: q}, max_dt=1.0); hist.append(float(w4.s[cp]))
    day = {t: next((i + 1 for i, v in enumerate(hist) if v >= t), None) for t in (0.01, 0.05, 0.2)}
    checks.append({'check': 'the doublet: the analytic breakthrough between the 1% and the 20% days, the 5% day within 10% of it', 'value': {'analytic_day': round(tbt, 1), 'day_1pct': day[0.01], 'day_5pct': day[0.05], 'day_20pct': day[0.2]},
                   'pass': day[0.01] is not None and day[0.2] is not None and day[0.01] <= tbt <= day[0.2] and abs(day[0.05] / tbt - 1.0) < 0.10})
    closure = w4.injected - w4.in_place() - sum(w4.produced.values()) - w4.edge
    checks.append({'check': 'the doublet\'s account closes: injected = in place + produced back + beyond the edge', 'value': round(closure, 3), 'pass': abs(closure) < 1e-4 * w4.injected and sum(w4.produced.values()) > 0})
    # a sealing fault between the two: nothing crosses, nothing arrives
    g3 = PM.Grid(n2, dx2, h, phi, k, mu, 1.0)
    g3.seal_segment(dx2 / 2.0, -5000.0, dx2 / 2.0, 5000.0)
    ui3 = solve_unit(g3, -d / 2.0, 0.0, k * h)
    w5 = Water(g3, vp2, 0.0)
    for _ in range(60):
        w5.advance(2.0, q * ui3['Fx'], q * ui3['Fy'], {ui3['cell']: q}, {}, max_dt=2.0)
    east = float(w5.s[:, n2 // 2 + 1:].sum())
    checks.append({'check': 'a sealing fault: no injected water beyond it', 'value': {'beyond': east, 'converged': ui3['converged']}, 'pass': ui3['converged'] and east == 0.0 and w5.in_place() > 0})
    return {'ok': all(c_['pass'] for c_ in checks), 'checks': checks, 'label': 'SIMULATION', 'grid': {'n': n, 'dx_ft': round(dx, 1)}}
