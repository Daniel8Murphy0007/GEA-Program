# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""position - where the bit sits in the layer, estimated the way a geosteerer does it, as a particle filter.

The pre-drill model says where the layer's top is and how it dips; the real layer differs from it by an offset,
a dip error, a thickness error, undulation and faults. Each particle is one guess at that difference - the top's
offset from the model at the bit, the dip's error, the thickness - and the filter keeps a few hundred of them.
Every decision it moves them (the offset drifts as the layer undulates, and now and then jumps as a fault
would), reads the gamma at the bit and, when a deep-resistivity tool is on the string, its distance to the
nearest boundary, and keeps the guesses that explain the readings. What comes out is a distribution: the
probability the bit is in the layer, above it or below it, the distance to the top and to the base with their
spread, and the layer's top and base ahead of the bit for the screen. The twin trains the agent with it and the
rig runs the same code; nothing in it knows the twin's secret. Its limit is a geosteerer's: inside a layer whose
gamma is flat the position is unobservable until a boundary is read, so the model's dip error accumulates with the
distance drilled, and which side an exit is on is the model's guess where the gamma above and below are alike -
the deep-resistivity tool, when the string has one, is what settles it."""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np

N_PARTICLES = 400
TRANSITION_FT = 3.0


class PositionFilter:
    def __init__(self, model_top0: float, model_dip_deg: float, model_thickness: float, gamma_in: float, gamma_above: float, gamma_below: float,
                 gamma_sigma: float = 7.0, deep_sigma: float = 3.0, deep_range: float = 15.0, n: int = N_PARTICLES, seed: int = 0,
                 top_sigma0: float = 12.0, dip_sigma0: float = 0.8, thick_sigma0: float = 0.15, walk_ft_per_100: float = 1.8, fault_per_1000: float = 0.5):
        self.r = np.random.RandomState(seed)
        self.m_top0 = float(model_top0); self.m_dip = float(model_dip_deg); self.m_thick = float(model_thickness)
        self.g_in, self.g_above, self.g_below = float(gamma_in), float(gamma_above), float(gamma_below)
        self.g_sigma = float(gamma_sigma); self.d_sigma = float(deep_sigma); self.d_range = float(deep_range)
        self.n = int(n)
        self.walk = float(walk_ft_per_100); self.fault_rate = float(fault_per_1000)
        self.offset = self.r.normal(0.0, top_sigma0, self.n)             # the real top minus the model's, at the bit
        self.dip_err = self.r.normal(0.0, dip_sigma0, self.n)             # degrees
        self.thick = self.m_thick * np.clip(self.r.normal(1.0, thick_sigma0, self.n), 0.5, 1.6)
        self.w = np.full(self.n, 1.0 / self.n)
        self.vs = 0.0
        self.updates = 0

    # -- geometry -------------------------------------------------------------------------------------------------------
    def model_top(self, vs: float) -> float:
        return self.m_top0 + vs * math.tan(math.radians(self.m_dip))

    def tops(self, vs: Optional[float] = None) -> np.ndarray:
        """Each particle's layer top at a distance along the lateral."""
        v = self.vs if vs is None else float(vs)
        return self.model_top(v) + self.offset + (v - self.vs) * (np.tan(np.radians(self.m_dip + self.dip_err)) - math.tan(math.radians(self.m_dip)))

    # -- the step -------------------------------------------------------------------------------------------------------
    def predict(self, ds_ft: float) -> None:
        """The bit moved ds along the lateral: the top at the new position by each particle's dip error, plus the
        undulation's walk and, now and then, a fault's jump."""
        ds = abs(float(ds_ft))
        # the real top moves by the real dip, the model's by the model's: the offset moves by the difference
        self.offset = self.offset + ds * (np.tan(np.radians(self.m_dip + self.dip_err)) - math.tan(math.radians(self.m_dip))) + self.r.normal(0.0, self.walk * ds / 100.0, self.n)
        self.dip_err = self.dip_err + self.r.normal(0.0, 0.05 * ds / 100.0, self.n)
        jump = self.r.rand(self.n) < self.fault_rate * ds / 1000.0
        if jump.any():
            self.offset[jump] += self.r.choice([-1.0, 1.0], jump.sum()) * self.r.uniform(5.0, 20.0, jump.sum())
        self.vs += ds

    def _expected_gamma(self, d_top: np.ndarray, d_base: np.ndarray) -> np.ndarray:
        """The gamma each particle expects at the bit: the layer's inside a transition zone of the boundaries."""
        tz = TRANSITION_FT
        g = np.full(self.n, self.g_in)
        above = d_top < -tz
        g[above] = self.g_above
        near_top = (d_top >= -tz) & (d_top < tz)
        f = (d_top[near_top] + tz) / (2 * tz)
        g[near_top] = self.g_above + f * (self.g_in - self.g_above)
        below = d_base < -tz
        g[below] = self.g_below
        near_base = (d_base >= -tz) & (d_base < tz)
        f = (tz - d_base[near_base]) / (2 * tz)
        g[near_base] = self.g_in + f * (self.g_below - self.g_in)
        return g

    def update(self, tvd: float, gamma: float, deep: Optional[float] = None) -> None:
        """The readings at the bit: the gamma, and the deep-resistivity distance when there is one (signed: + the
        top is above the bit by that much, - the base is below)."""
        top = self.tops()
        d_top = float(tvd) - top                        # positive inside, below the top
        d_base = top + self.thick - float(tvd)          # positive inside, above the base
        ll = -0.5 * ((self._expected_gamma(d_top, d_base) - float(gamma)) / self.g_sigma) ** 2
        if deep is not None:
            # the tool reports the nearer boundary when it is within range; each particle's nearer boundary
            near_top = np.abs(d_top) <= np.abs(d_base)
            pred = np.where(near_top, d_top, -d_base)
            within = np.abs(pred) <= self.d_range * 1.3
            ll = ll + np.where(within, -0.5 * ((pred - float(deep)) / self.d_sigma) ** 2, -8.0)
        w = self.w * np.exp(ll - ll.max())
        s = w.sum()
        if not np.isfinite(s) or s <= 0:
            w = np.full(self.n, 1.0 / self.n)
        else:
            w = w / s
        self.w = w
        self.updates += 1
        ess = 1.0 / float((w * w).sum())
        if ess < self.n / 2:
            self._resample()

    def _resample(self) -> None:
        pos = (self.r.rand() + np.arange(self.n)) / self.n
        cum = np.cumsum(self.w)
        idx = np.searchsorted(cum, pos)
        idx = np.clip(idx, 0, self.n - 1)
        self.offset = self.offset[idx] + self.r.normal(0.0, 0.5, self.n)
        self.dip_err = self.dip_err[idx] + self.r.normal(0.0, 0.02, self.n)
        self.thick = self.thick[idx] * np.clip(self.r.normal(1.0, 0.01, self.n), 0.9, 1.1)
        self.w = np.full(self.n, 1.0 / self.n)

    # -- what it says --------------------------------------------------------------------------------------------------
    def estimate(self, tvd: float, ahead_ft: float = 60.0) -> Dict[str, float]:
        top = self.tops()
        d_top = float(tvd) - top; d_base = top + self.thick - float(tvd)
        w = self.w
        p_in = float(w[(d_top >= 0) & (d_base >= 0)].sum())
        p_above = float(w[d_top < 0].sum())
        p_below = float(w[d_base < 0].sum())
        mt = float((w * d_top).sum()); mb = float((w * d_base).sum())
        st = float(math.sqrt(max((w * (d_top - mt) ** 2).sum(), 0.0)))
        top_ahead = self.tops(self.vs + ahead_ft)
        return {'p_in': p_in, 'p_above': p_above, 'p_below': p_below, 'd_top': mt, 'd_base': mb, 'sigma': st,
                'top_tvd': float((w * top).sum()), 'base_tvd': float((w * (top + self.thick)).sum()), 'thickness': float((w * self.thick).sum()),
                'top_ahead_tvd': float((w * top_ahead).sum()), 'base_ahead_tvd': float((w * (top_ahead + self.thick)).sum()),
                'dip_deg': self.m_dip + float((w * self.dip_err).sum()), 'updates': self.updates}
