# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""twin - the physics twin of a lateral the agent is trained in and judged against: a target layer whose top rolls
with dip, undulation and small faults; the gamma and the deep-resistivity boundary the near-bit tools read of it;
a steering tool that builds and drops with its own tendency and a cap; a bit whose rate, wear and balling follow
the weight, the speed and the flow; the hydraulics (ECD against the fracture gradient), the hole (the cuttings bed
and the stuck pipe it leads to) and the hours each decision costs. Every scene is drawn from the same ranges with a
seed (domain randomization), so an agent that does well on scenes it has not seen has learned the drilling and not
a formation. Labelled: nothing here is a measurement; a report of a twin run says SIMULATION.

One decision per step of 30 ft (a third of a stand): the build or drop asked of the tool, the weight on bit, the
rotary speed, the flow rate, and whether to drill, circulate or ream and wash instead."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from .position import PositionFilter

STEP_FT = 30.0
BUILD_CHOICES = (-3.0, -1.5, 0.0, 1.5, 3.0)            # deg per 100 ft asked of the tool
WOB_CHOICES = (15.0, 25.0, 35.0)                       # klb
RPM_CHOICES = (80.0, 120.0, 160.0)
FLOW_CHOICES = (400.0, 500.0, 600.0)                   # gpm
COND_CHOICES = ('drill', 'circulate', 'ream_wash')
ACTION_SIZES = (len(BUILD_CHOICES), len(WOB_CHOICES), len(RPM_CHOICES), len(FLOW_CHOICES), len(COND_CHOICES))
ACTION_NAMES = ('build', 'wob', 'rpm', 'flow', 'conditioning')
OBS_SIZE = 28
OBS_NAMES = ('gamma_now', 'gamma_1', 'gamma_2', 'gamma_3', 'gamma_4', 'gamma_trend', 'inclination_minus_90', 'model_dist_top', 'model_dist_base',
             'deep_res_dist', 'deep_res_available', 'last_build', 'rop', 'vibration', 'ecd_margin', 'cuttings_bed', 'balling', 'bit_wear',
             'motor_dp', 'progress', 'in_layer_belief', 'layer_thickness_model',
             'filter_p_in', 'filter_p_above', 'filter_p_below', 'filter_dist_top', 'filter_dist_base', 'filter_sigma')
VIB_LIMIT = 1.2
DP_LIMIT = 600.0
DAY_LIMIT = 12.0
DLS_MAX_TOOL = 6.0


class Scene:
    """One randomized lateral: the layer, the lithology, the tool, the pre-drill model's error."""

    def __init__(self, seed: int, length_ft: float = 4000.0):
        r = np.random.RandomState(seed)
        self.seed = int(seed)
        self.length_ft = float(length_ft)
        self.top0 = 9000.0 + r.uniform(-200, 200)
        self.thickness = r.uniform(22.0, 60.0)
        self.dip_deg = r.normal(0.0, 1.5)
        self.und_amp = r.uniform(4.0, 25.0)
        self.und_wl = r.uniform(800.0, 3000.0)
        self.und_phase = r.uniform(0, 2 * math.pi)
        n_faults = r.randint(0, 4)
        self.faults = sorted((float(r.uniform(300, length_ft - 300)), float(r.choice([-1, 1]) * r.uniform(5, 20))) for _ in range(n_faults))
        self.g_in = r.uniform(40, 80)
        self.g_above = r.uniform(100, 160)
        self.g_below = r.uniform(90, 150)
        self.g_noise = r.uniform(3, 7)
        self.deep_res = bool(r.rand() < 0.6)
        self.deep_range = r.uniform(10, 18)
        self.deep_noise = r.uniform(1.5, 3.5)
        self.tendency = r.normal(0.0, 0.6)             # deg/100 ft the tool builds or drops on its own
        self.tool_noise = r.uniform(0.2, 0.5)
        self.k_in = r.uniform(0.85, 1.2)                # drillability of the layer
        self.k_above = r.uniform(0.55, 0.85)
        self.k_below = r.uniform(0.6, 0.95)
        self.hard_above = r.uniform(1.15, 1.45)        # stick-slip proneness of the shale above
        self.hard_in = r.uniform(0.8, 1.05)
        self.frac_gradient = r.uniform(11.8, 12.8)     # ppg
        self.mud_weight = r.uniform(10.0, 10.8)
        self.model_top_error = r.normal(0.0, 10.0)      # the pre-drill model's error at the heel
        self.model_dip_error = r.normal(0.0, 0.7)
        self.model_thickness = self.thickness * r.uniform(0.85, 1.15)
        self.model_g_in = self.g_in + r.normal(0.0, 5.0)              # the model's gamma values, as the offset wells gave them
        self.model_g_above = self.g_above + r.normal(0.0, 8.0)
        self.model_g_below = self.g_below + r.normal(0.0, 8.0)
        self.balling_prone = r.uniform(0.3, 1.0)
        self.start_incl = 90.0 + r.uniform(-2.0, 2.0)
        self.start_offset = r.uniform(0.25, 0.75)       # where in the layer the heel lands (fraction from the top)

    def layer_top(self, vs: float) -> float:
        t = self.top0 + vs * math.tan(math.radians(self.dip_deg)) + self.und_amp * math.sin(2 * math.pi * vs / self.und_wl + self.und_phase)
        for at, throw in self.faults:
            if vs >= at:
                t += throw
        return t

    def layer_base(self, vs: float) -> float:
        return self.layer_top(vs) + self.thickness

    def model_top(self, vs: float) -> float:
        """What the pre-drill model (offset wells) says the top is: a line with the error the model has."""
        return self.top0 + self.model_top_error + vs * math.tan(math.radians(self.dip_deg + self.model_dip_error))

    def lithology(self, vs: float, tvd: float) -> str:
        top = self.layer_top(vs)
        if tvd < top:
            return 'above'
        if tvd > top + self.thickness:
            return 'below'
        return 'in'

    def gamma(self, vs: float, tvd: float, r: np.random.RandomState) -> float:
        top = self.layer_top(vs); base = top + self.thickness
        tz = 3.0                                        # the transition the tool sees across a boundary, ft
        if tvd < top - tz:
            g = self.g_above
        elif tvd < top + tz:
            f = (tvd - (top - tz)) / (2 * tz); g = self.g_above + f * (self.g_in - self.g_above)
        elif tvd < base - tz:
            g = self.g_in
        elif tvd < base + tz:
            f = (tvd - (base - tz)) / (2 * tz); g = self.g_in + f * (self.g_below - self.g_in)
        else:
            g = self.g_below
        return float(g + r.normal(0, self.g_noise))

    def deep_distance(self, vs: float, tvd: float, r: np.random.RandomState) -> Tuple[float, bool]:
        """The deep azimuthal resistivity tool's distance to the nearest boundary, signed (+ the top is above, - the
        base is below), when the tool is on the string and the boundary is within its range."""
        if not self.deep_res:
            return 0.0, False
        top = self.layer_top(vs); base = top + self.thickness
        d_top = tvd - top; d_base = base - tvd
        if abs(d_top) <= abs(d_base) and abs(d_top) <= self.deep_range:
            return float(d_top + r.normal(0, self.deep_noise)), True
        if abs(d_base) <= self.deep_range:
            return float(-(d_base + r.normal(0, self.deep_noise))), True
        return 0.0, False

    def describe(self) -> dict:
        return {'seed': self.seed, 'length_ft': self.length_ft, 'layer_top_heel_ft': round(self.top0, 1), 'thickness_ft': round(self.thickness, 1),
                'dip_deg': round(self.dip_deg, 2), 'undulation_ft': round(self.und_amp, 1), 'faults': [(round(a), round(t, 1)) for a, t in self.faults],
                'gamma_in_above_below': [round(self.g_in), round(self.g_above), round(self.g_below)], 'deep_resistivity_tool': self.deep_res,
                'tool_tendency_deg_100ft': round(self.tendency, 2), 'frac_gradient_ppg': round(self.frac_gradient, 2), 'mud_weight_ppg': round(self.mud_weight, 2),
                'model_top_error_ft': round(self.model_top_error, 1), 'model_dip_error_deg': round(self.model_dip_error, 2)}


class Lateral:
    """The twin running: reset to a scene, step with an action, read the observation and the reward."""

    def __init__(self, scene: Scene, seed: Optional[int] = None):
        self.scene = scene
        self.r = np.random.RandomState((scene.seed * 7919 + 1) % (2 ** 32 - 1) if seed is None else seed)
        self.reset()

    def reset(self) -> np.ndarray:
        s = self.scene
        self.vs = 0.0
        self.tvd = s.layer_top(0.0) + s.start_offset * s.thickness
        self.incl = s.start_incl
        self.hours = 0.0
        self.footage = 0.0
        self.footage_in = 0.0
        self.footage_sweet = 0.0
        self.tortuosity = 0.0
        self.bed = 0.1
        self.balling = 0.0
        self.wear = 0.0
        self.last_build = 0.0
        self.rop = 0.0
        self.vib = 0.0
        self.ecd = s.mud_weight
        self.dp = 0.0
        self.gammas: List[float] = [s.gamma(self.vs, self.tvd, self.r) for _ in range(5)]
        self.done = False
        self.ended = ''
        self.steps = 0
        self.conditioning_steps = 0
        self.events: List[str] = []
        self.dls_sum = 0.0
        self.max_dls = 0.0
        self.vib_excess = 0.0
        self.ecd_excess = 0.0
        self.belief = 1.0
        self.path: List[Tuple[float, float, float]] = [(self.vs, self.tvd, self.incl)]
        self.filter = PositionFilter(s.model_top(0.0), s.dip_deg + s.model_dip_error, s.model_thickness, s.model_g_in, s.model_g_above, s.model_g_below,
                                     deep_range=s.deep_range, seed=(s.seed * 31 + 7) % (2 ** 32 - 1))
        deep, have = s.deep_distance(self.vs, self.tvd, self.r)
        self.filter.update(self.tvd, self.gammas[-1], deep if have else None)
        self.est = self.filter.estimate(self.tvd)
        return self.observe()

    # -- the observation -------------------------------------------------------------------------------------------
    def observe(self) -> np.ndarray:
        s = self.scene
        g = self.gammas
        trend = (g[-1] - g[-5]) / 4.0
        mt = s.model_top(self.vs)
        d_top = self.tvd - mt
        d_base = (mt + s.model_thickness) - self.tvd
        deep, have = s.deep_distance(self.vs, self.tvd, self.r)
        # a belief the agent can lean on: gamma near the layer's own value says inside
        gi = abs(g[-1] - s.model_g_in); ga = abs(g[-1] - s.model_g_above); gb = abs(g[-1] - s.model_g_below)
        self.belief = float(1.0 if gi < min(ga, gb) else 0.0)
        e = self.est
        obs = np.array([
            (g[-1] - 100.0) / 50.0, (g[-2] - 100.0) / 50.0, (g[-3] - 100.0) / 50.0, (g[-4] - 100.0) / 50.0, (g[-5] - 100.0) / 50.0,
            trend / 10.0, (self.incl - 90.0) / 5.0, d_top / 30.0, d_base / 30.0,
            deep / 15.0 if have else 0.0, 1.0 if have else 0.0, self.last_build / 3.0,
            self.rop / 100.0, self.vib / 1.5, (s.frac_gradient - self.ecd) / 1.0, self.bed, self.balling, self.wear,
            self.dp / 600.0, self.vs / s.length_ft, self.belief, s.model_thickness / 50.0,
            e['p_in'], e['p_above'], e['p_below'], e['d_top'] / 30.0, e['d_base'] / 30.0, e['sigma'] / 15.0], dtype=np.float64)
        return obs

    # -- one decision ------------------------------------------------------------------------------------------------
    def step(self, action) -> Tuple[np.ndarray, float, bool, dict]:
        if self.done:
            raise RuntimeError('the lateral is finished; reset it')
        s = self.scene
        bi, wi, ri, fi, ci = (int(a) for a in action)
        build = BUILD_CHOICES[bi]; wob = WOB_CHOICES[wi]; rpm = RPM_CHOICES[ri]; flow = FLOW_CHOICES[fi]; cond = COND_CHOICES[ci]
        info: Dict[str, object] = {'build_asked': build, 'wob': wob, 'rpm': rpm, 'flow': flow, 'conditioning': cond}
        reward = 0.0
        self.steps += 1
        lith = s.lithology(self.vs, self.tvd)
        hard = s.hard_above if lith == 'above' else (s.hard_in if lith == 'in' else 1.1)
        k = {'in': s.k_in, 'above': s.k_above, 'below': s.k_below}[lith]
        transport = (flow / 500.0) ** 1.5 * (rpm / 120.0) ** 0.4
        if cond != 'drill':
            # conditioning: no footage, three quarters of an hour, the hole cleaner and the bit washed
            needed = self.bed > 0.35 or self.balling > 0.3
            hrs = 0.75
            self.hours += hrs
            self.conditioning_steps += 1
            if cond == 'circulate':
                self.bed = max(0.0, self.bed - 0.25 * transport)
                self.balling = max(0.0, self.balling - 0.3)
            else:
                self.bed = max(0.0, self.bed - 0.18 * transport)
                self.balling = 0.0
                self.tortuosity = max(0.0, self.tortuosity - 0.2)        # the hole reamed smoother
            self.rop = 0.0; self.vib = 0.3 * (rpm / 120.0); self.dp = 0.0
            self.ecd = s.mud_weight + 0.4 * (flow / 500.0) ** 2 + 0.8 * self.bed
            # conditioning a hole that needs it is cheap; conditioning one that does not is rig time thrown away
            reward -= 0.1 if needed else 0.6
            reward -= 0.05 * hrs
            ecd_over = max(0.0, self.ecd - s.frac_gradient)
            if ecd_over > 0:
                reward -= 1.0 * ecd_over; self.ecd_excess += ecd_over
            info['hours'] = hrs
            self.gammas.append(s.gamma(self.vs, self.tvd, self.r)); self.gammas = self.gammas[-5:]
            return self._finish(reward, info)
        # steering: the tool does what was asked plus its tendency and noise, within its cap
        b_eff = build + s.tendency + self.r.normal(0, s.tool_noise)
        b_eff = max(-DLS_MAX_TOOL, min(DLS_MAX_TOOL, b_eff))
        d_incl = b_eff * STEP_FT / 100.0
        incl_new = max(70.0, min(110.0, self.incl + d_incl))          # a lateral: the tool is not asked past these
        d_incl = incl_new - self.incl
        dls = abs(b_eff)
        self.dls_sum += dls; self.max_dls = max(self.max_dls, dls)
        self.tortuosity += abs(d_incl)
        # minimum curvature in the vertical plane
        a0 = math.radians(self.incl); a1 = math.radians(incl_new)
        if abs(a1 - a0) < 1e-9:
            dv = STEP_FT * math.cos(a0); dh = STEP_FT * math.sin(a0)
        else:
            rf = (STEP_FT / (a1 - a0))
            dv = rf * (math.sin(a1) - math.sin(a0)); dh = rf * (math.cos(a0) - math.cos(a1))
        self.incl = incl_new; self.tvd += dv; self.vs += dh
        self.last_build = build
        # the bit
        balled = 1.0 - 0.5 * self.balling
        rop = k * 62.0 * (wob / 25.0) ** 0.8 * (rpm / 120.0) ** 0.6 * (flow / 500.0) ** 0.3 * (1.0 - 0.45 * self.wear) * balled
        rop *= self.r.uniform(0.9, 1.1)
        self.rop = rop
        hrs = STEP_FT / max(rop, 5.0)
        self.hours += hrs
        self.footage += STEP_FT
        self.wear = min(1.0, self.wear + 0.00004 * STEP_FT * (wob / 25.0) ** 1.5 * (1.3 if lith != 'in' else 1.0))
        # vibration: stick-slip with weight and slow rotation in hard rock, lateral with fast rotation and light weight
        v_ss = 0.75 * (wob / 25.0) ** 1.5 * (120.0 / rpm) ** 0.8 * hard
        v_lat = 0.55 * (rpm / 120.0) ** 2 * (25.0 / wob) ** 0.5
        self.vib = max(v_ss, v_lat) * self.r.uniform(0.9, 1.15)
        # the motor's differential, the hydraulics, the hole
        self.dp = 170.0 * (wob / 25.0) * k * (1.0 + 0.4 * self.balling) * self.r.uniform(0.95, 1.05)
        bed_gain = 0.02 * (rop / 60.0) * (1.0 + 0.5 * max(0.0, self.incl - 88.0) / 4.0)
        self.bed = min(1.0, max(0.0, self.bed + bed_gain - 0.03 * transport))
        self.ecd = s.mud_weight + 0.4 * (flow / 500.0) ** 2 + 0.8 * self.bed
        if lith != 'in' and wob >= 30.0 and flow <= 500.0:
            self.balling = min(1.0, self.balling + 0.12 * s.balling_prone)
        elif flow >= 600.0:
            self.balling = max(0.0, self.balling - 0.05)
        # where the bit is now
        new_lith = s.lithology(self.vs, self.tvd)
        in_layer = new_lith == 'in'
        top = s.layer_top(self.vs); base = top + s.thickness
        centre = (self.tvd - top) / s.thickness if in_layer else -1.0
        sweet = in_layer and 0.25 <= centre <= 0.75
        if in_layer:
            self.footage_in += STEP_FT
        if sweet:
            self.footage_sweet += STEP_FT
        # the reward: contact first, then the hole's quality, then the hours, then the limits
        reward += 1.0 if in_layer else -0.2
        reward += 0.4 if sweet else 0.0
        reward -= 0.25 * (dls / 3.0) ** 2
        reward -= 0.05 * hrs
        vib_over = max(0.0, self.vib - VIB_LIMIT)
        if vib_over > 0:
            reward -= 1.2 * vib_over; self.vib_excess += vib_over
        ecd_over = max(0.0, self.ecd - s.frac_gradient)
        if ecd_over > 0:
            reward -= 1.0 * ecd_over; self.ecd_excess += ecd_over
            if self.r.rand() < 0.08 * min(1.0, ecd_over):
                self.events.append(f'losses at {self.vs:.0f} ft: ECD {self.ecd:.2f} over the fracture gradient {s.frac_gradient:.2f}')
                reward -= 3.0; self.hours += 6.0
        dp_over = max(0.0, self.dp - DP_LIMIT)
        if dp_over > 0:
            reward -= 0.8 * dp_over / 100.0
            if self.r.rand() < 0.05 * min(1.0, dp_over / 200.0):
                self.events.append(f'motor stall at {self.vs:.0f} ft: differential {self.dp:.0f} psi'); reward -= 1.0; self.hours += 0.5
        if self.bed > 0.6 and self.r.rand() < 0.15 * (self.bed - 0.6) / 0.4:
            self.events.append(f'stuck pipe at {self.vs:.0f} ft: cuttings bed {self.bed:.2f}')
            reward -= 8.0; self.hours += 24.0
            self.done = True; self.ended = 'stuck pipe'
        self.gammas.append(s.gamma(self.vs, self.tvd, self.r)); self.gammas = self.gammas[-5:]
        self.filter.predict(dh)
        deep_r, have_r = s.deep_distance(self.vs, self.tvd, self.r)
        self.filter.update(self.tvd, self.gammas[-1], deep_r if have_r else None)
        self.est = self.filter.estimate(self.tvd)
        info.update({'hours': hrs, 'rop': rop, 'estimate': dict(self.est), 'in_layer': in_layer, 'sweet': sweet, 'dls': dls, 'lithology': new_lith, 'tvd': self.tvd, 'vs': self.vs,
                     'layer_top': top, 'layer_base': base, 'vibration': self.vib, 'ecd': self.ecd, 'bed': self.bed, 'balling': self.balling, 'dp': self.dp})
        self.path.append((self.vs, self.tvd, self.incl))
        return self._finish(reward, info)

    def _finish(self, reward: float, info: dict) -> Tuple[np.ndarray, float, bool, dict]:
        s = self.scene
        if not self.done and self.vs >= s.length_ft:
            self.done = True; self.ended = 'total depth'
            reward += 10.0 - 0.5 * self.hours / 24.0                  # the lateral finished: the days it took, against ten
        if not self.done and self.hours >= DAY_LIMIT * 24.0:
            self.done = True; self.ended = 'out of days'
            reward -= 5.0
        if not self.done and self.steps >= 400:
            self.done = True; self.ended = 'out of steps'
            reward -= 5.0
        info['done'] = self.done; info['ended'] = self.ended
        return self.observe(), float(reward), self.done, info

    def summary(self) -> dict:
        s = self.scene
        return {'seed': s.seed, 'footage_ft': self.footage, 'footage_in_layer_ft': self.footage_in, 'in_layer_pct': 100.0 * self.footage_in / max(self.footage, 1.0),
                'sweet_pct': 100.0 * self.footage_sweet / max(self.footage, 1.0), 'hours': self.hours, 'days': self.hours / 24.0,
                'ft_per_day': self.footage / max(self.hours / 24.0, 1e-6), 'tortuosity_deg': self.tortuosity, 'mean_dls': self.dls_sum / max(self.steps - self.conditioning_steps, 1),
                'max_dls': self.max_dls, 'conditioning_steps': self.conditioning_steps, 'events': list(self.events), 'ended': self.ended, 'steps': self.steps,
                'vibration_excess': self.vib_excess, 'ecd_excess': self.ecd_excess, 'stuck': self.ended == 'stuck pipe', 'bit_wear': self.wear}


# -- the textbook driller, to be beaten ----------------------------------------------------------------------------------

def textbook_action(obs: np.ndarray, state: Optional[dict] = None) -> Tuple[int, ...]:
    """What a careful directional driller and geosteerer do by the book from the same observation: aim the well at
    the middle of the layer as the position filter places it (the deep-resistivity tool's distance when it has
    one), a build or drop in proportion to the miss and capped, 25 klb at 120 rpm and 500 gpm, lighter and faster
    when the vibration limit is crossed, circulate when the hole fills, wash a balled bit."""
    deep = obs[9] * 15; have = obs[10] > 0.5; incl = 90.0 + obs[6] * 5
    bed = obs[15]; balling = obs[16]; vib = obs[13] * 1.5
    d_top = obs[25] * 30; d_base = obs[26] * 30; thick = obs[21] * 50
    if have and 0 < deep < 6:                  # the top close above: aim down to the middle
        e = -(thick / 2 - deep)
    elif have and 0 < -deep < 6:               # the base close below: aim up
        e = thick / 2 + deep
    else:
        e = d_top - d_base                     # positive: nearer the base, so go up
    e = max(-60.0, min(60.0, e))
    target = 90.0 + max(-4.0, min(4.0, e * 0.12))
    d = target - incl
    bi = 4 if d > 0.6 else 3 if d > 0.15 else 1 if d < -0.15 else 0 if d < -0.6 else 2
    wi, ri, fi = 1, 1, 1
    if vib > VIB_LIMIT:
        wi, ri = 0, 1
    ci = 0
    if bed > 0.5:
        ci = 1
    if balling > 0.5:
        ci = 2
    return (bi, wi, ri, fi, ci)


def run_episode(lat: Lateral, act_fn, max_steps: int = 400) -> dict:
    obs = lat.reset()
    total = 0.0
    while not lat.done and lat.steps < max_steps:
        a = act_fn(obs)
        obs, r, done, _ = lat.step(a)
        total += r
    out = lat.summary(); out['reward'] = total
    return out
