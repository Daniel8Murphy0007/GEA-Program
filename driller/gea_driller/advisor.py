# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""advisor - the agent on the rig: the observation the twin trained on, built from what the rig says and what the
pre-drill model says - the bit placed in the plan's frame by minimum curvature through the surveys, along the plan,
right of its line and in depth; the policy's decision in the driller's units, the build and the turn as one toolface;
the operator's answer to it; the record of
every decision, every answer and every command; and the downlink - the port a command would go through to a
rotary steerable system, which ships as the null downlink: it keeps every command it would have sent and sends
none. The closed loop is armed by a named operator for one section at a time, inside the limits, and disarms
itself when the feed goes stale, a limit is crossed, the section is drilled or the operator says so."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import __version__, twin as T
from .geometry import dls_of, min_curvature, toolface_of, wrap180
from .policy import Policy
from .position import PositionFilter


def _utc() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# -- the records ------------------------------------------------------------------------------------------------------------

class Records:
    """decisions.jsonl, commands.jsonl, surveys.csv, each with a .sha256 beside it refreshed on every write, and
    manifest.json GEA reads. Append-only; nothing is ever deleted."""

    def __init__(self, cfg: dict):
        self.dir = cfg['output']['dir']
        os.makedirs(self.dir, exist_ok=True)
        self.cfg = cfg
        self.lock = threading.Lock()
        self.counts = {'decisions': 0, 'commands': 0, 'answers': 0}
        for n in ('decisions.jsonl', 'commands.jsonl'):
            p = os.path.join(self.dir, n)
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    k = sum(1 for _ in f)
                self.counts[n.split('.')[0]] = k

    def append(self, name: str, rec: dict) -> None:
        with self.lock:
            p = os.path.join(self.dir, name)
            with open(p, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, separators=(',', ':')) + '\n')
            self._sidecar(p)
            key = name.split('.')[0]
            if key in self.counts:
                self.counts[key] += 1
            self.manifest()

    def write_surveys(self, surveys: List[dict]) -> None:
        with self.lock:
            p = os.path.join(self.dir, 'surveys.csv')
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write('md_ft,inclination_deg,azimuth_deg,utc,source\n')
                for s in surveys:
                    az = '' if s.get('azi') is None else f"{s['azi']:.2f}"
                    f.write(f"{s['md']:.1f},{s['inc']:.2f},{az},{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s['utc']))},{s['source']}\n")
            self._sidecar(p)

    def _sidecar(self, p: str) -> None:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        with open(p + '.sha256', 'w', encoding='utf-8') as f:
            f.write(f'{h.hexdigest()}  {os.path.basename(p)}\n')

    def manifest(self) -> dict:
        files = []
        for n in sorted(os.listdir(self.dir)):
            p = os.path.join(self.dir, n)
            if n.endswith('.sha256') or n == 'manifest.json' or not os.path.isfile(p):
                continue
            sc = p + '.sha256'
            digest = open(sc, encoding='utf-8').read().split()[0] if os.path.isfile(sc) else None
            files.append({'file': n, 'bytes': os.path.getsize(p), 'sha256': digest})
        m = {'program': 'GEA Driller', 'version': __version__, 'site': self.cfg['site'], 'well': self.cfg['well'], 'written_utc': _utc(), 'files': files,
             'counts': dict(self.counts), 'model': {k: v for k, v in self.cfg['model'].items() if k != 'note'}}
        tmp = os.path.join(self.dir, 'manifest.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(m, f, indent=1)
        os.replace(tmp, os.path.join(self.dir, 'manifest.json'))
        return m


# -- the downlink -----------------------------------------------------------------------------------------------------------

class Downlink:
    """What a command goes through to the steering tool. `send` returns what happened; the null downlink answers
    that it kept the command and sent nothing."""
    name = 'null'
    sends = False

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def send(self, command: dict) -> dict:
        return {'sent': False, 'kept': True, 'note': 'the null downlink: no vendor protocol ships with this version; the command is on record and nothing reached the tool'}

    def describe(self) -> str:
        return 'null downlink - commands are recorded and never sent (no vendor protocol in this version)'


def make_downlink(cfg: dict) -> Downlink:
    kind = (cfg.get('closed_loop') or {}).get('downlink', 'null')
    if kind != 'null':
        raise ValueError(f'no downlink named {kind} in this version')
    return Downlink(cfg)


# -- the advisor ------------------------------------------------------------------------------------------------------------

class Advisor:
    def __init__(self, cfg: dict, policy: Policy, feed, records: Records, downlink: Optional[Downlink] = None):
        self.cfg = cfg; self.policy = policy; self.feed = feed; self.records = records
        self.downlink = downlink or make_downlink(cfg)
        self.lock = threading.Lock()
        self.gammas: List[float] = []
        self.last_build = 0.0
        self.last_aim = 0.0
        self.last_turn = 0.0
        self.last_decision_md: Optional[float] = None
        self.last_decision_t = 0.0
        self.last_action: Tuple[int, ...] = T.HOLD_ACTION
        self.plan_az: Optional[float] = None
        self.pos: Optional[dict] = None
        self.bed = 0.1; self.balling = 0.0; self.wear = 0.0; self.footage = 0.0
        self.first_md: Optional[float] = None
        self.current: Optional[dict] = None
        self.history: List[dict] = []
        self.answers = {'accepted': 0, 'rejected': 0, 'overridden': 0, 'unanswered': 0}
        self.armed: Optional[dict] = None
        self.arm_log: List[dict] = []
        self.stopped_reason: Optional[str] = None
        self.filter: Optional[PositionFilter] = None
        self.filter_pos: Optional[Tuple[float, float]] = None
        self.est: Optional[dict] = None

    # -- the observation from the rig -----------------------------------------------------------------------------
    def observation(self, snap: dict) -> Tuple[Optional[np.ndarray], dict]:
        st = snap['state']; m = self.cfg['model']; lim = self.cfg['limits']
        md = st.get('hole_depth')
        if md is None or st.get('gamma') is None:
            return None, {'missing': [k for k in ('hole_depth', 'gamma') if st.get(k) is None]}
        if self.first_md is None:
            self.first_md = float(m.get('heel_md_ft') or md)
        sv = self.feed.latest_survey()
        inc = st.get('continuous_inc') if st.get('continuous_inc') is not None else (sv['inc'] if sv else 90.0)
        if self.plan_az is None:
            # the plan's azimuth: the config's, else the first azimuth the rig gives at the heel
            pa = m.get('plan_azimuth_deg')
            if pa is None:
                pa = st.get('continuous_azi') if st.get('continuous_azi') is not None else (sv['azi'] if sv and sv.get('azi') is not None else 0.0)
            self.plan_az = float(pa) % 360.0
        az_abs = st.get('continuous_azi') if st.get('continuous_azi') is not None else (sv['azi'] if sv and sv.get('azi') is not None else self.plan_az)
        az = wrap180(float(az_abs) - self.plan_az)                   # off the plan, right positive
        # the bit in the plan's frame from the heel's and the surveys (minimum curvature; the directional driller's own projection would be better)
        pos = self._position_at_bit(md, float(inc), float(az_abs))
        self.pos = pos
        vs = max(0.0, pos['vs']); y = pos['y']; tvd = pos['tvd']
        self.footage = max(0.0, md - self.first_md)
        model_top = float(m['layer_top_tvd_ft']) + vs * math.tan(math.radians(float(m['dip_deg']))) + y * math.tan(math.radians(float(m.get('cross_dip_deg') or 0.0)))
        d_top = tvd - model_top; d_base = model_top + float(m['thickness_ft']) - tvd
        corridor = float(m.get('corridor_ft') or 100.0)
        g = float(st['gamma'])
        if not self.gammas:
            self.gammas = [g] * 5
        gs = (self.gammas + [g])[-5:]
        trend = (gs[-1] - gs[0]) / 4.0
        deep = st.get('deep_res_dist'); have = deep is not None and bool(m.get('deep_resistivity_tool', False))
        gi = abs(g - float(m['gamma_in'])); ga = abs(g - float(m['gamma_above'])); gb = abs(g - float(m['gamma_below']))
        belief = 1.0 if gi < min(ga, gb) else 0.0
        # the position filter: the geosteerer's estimate of where the bit sits in the layer, from the readings
        if self.filter is None:
            self.filter = PositionFilter(float(m['layer_top_tvd_ft']), float(m['dip_deg']), float(m['thickness_ft']), float(m['gamma_in']), float(m['gamma_above']), float(m['gamma_below']),
                                         deep_range=float(m.get('deep_range_ft', 15.0)), seed=1, model_cross_dip_deg=float(m.get('cross_dip_deg') or 0.0))
            self.filter.vs = vs; self.filter.y = y
            self.filter_pos = (vs, y)
        elif vs > self.filter_pos[0] or y != self.filter_pos[1]:
            self.filter.predict(vs - self.filter_pos[0], y - self.filter_pos[1]); self.filter_pos = (vs, y)
        self.filter.update(tvd, g, float(deep) if have else None)
        self.est = self.filter.estimate(tvd)
        e = self.est
        rop = float(st.get('rop') or 0.0); vib = float(st.get('vibration') or 0.0)
        flow = float(st.get('flow_in') or 500.0); rpm = float(st.get('rpm') or 120.0); wob = float(st.get('wob') or 25.0)
        ecd = float(st['ecd']) if st.get('ecd') is not None else float(lim['mud_weight_ppg']) + 0.4 * (flow / 500.0) ** 2 + 0.8 * self.bed
        dp = max(0.0, float(st.get('spp') or 2800.0) - 2800.0) if st.get('spp') is not None else 170.0 * (wob / 25.0)
        obs = np.array([
            (gs[-1] - 100.0) / 50.0, (gs[-2] - 100.0) / 50.0, (gs[-3] - 100.0) / 50.0, (gs[-4] - 100.0) / 50.0, (gs[-5] - 100.0) / 50.0,
            trend / 10.0, (float(inc) - 90.0) / 5.0, d_top / 30.0, d_base / 30.0,
            (float(deep) / 15.0) if have else 0.0, 1.0 if have else 0.0, self.last_build / 3.0,
            rop / 100.0, vib / 1.5, (float(lim['frac_gradient_ppg']) - ecd) / 1.0, self.bed, self.balling, self.wear,
            dp / 600.0, vs / float(m.get('lateral_length_ft', 4000.0)), belief, float(m['thickness_ft']) / 50.0,
            e['p_in'], e['p_above'], e['p_below'], e['d_top'] / 30.0, e['d_base'] / 30.0, e['sigma'] / 15.0,
            az / 5.0, y / 50.0, (corridor - abs(y)) / 50.0, float(m.get('cross_dip_deg') or 0.0) / 2.0, self.last_aim / 4.0, e['cross_dip_deg'] / 2.0], dtype=np.float64)
        ctx = {'md': md, 'vs': vs, 'y': y, 'tvd': tvd, 'inc': inc, 'az': az, 'az_abs': float(az_abs), 'plan_az': self.plan_az, 'corridor': corridor,
               'model_top': model_top, 'model_base': model_top + float(m['thickness_ft']), 'gamma': g, 'trend': trend, 'estimate': dict(e),
               'deep': deep if have else None, 'belief': belief, 'rop': rop, 'vibration': vib, 'ecd': ecd, 'dp': dp, 'wob': wob, 'rpm': rpm, 'flow': flow,
               'bed': self.bed, 'balling': self.balling, 'wear': self.wear}
        return obs, ctx

    def _position_at_bit(self, md: float, inc_now: float, az_now: float) -> dict:
        """The bit in the plan's frame - along the plan, right of the plan line, TVD - by minimum curvature through
        the surveys from the heel, then the last station to the bit at the continuous inclination and azimuth. A
        station without an azimuth is taken along the plan."""
        m = self.cfg['model']
        svs = list(self.feed.surveys) if hasattr(self.feed, 'surveys') else []
        tvd = float(m.get('heel_tvd_ft') or (float(m['layer_top_tvd_ft']) + 0.5 * float(m['thickness_ft'])))      # the heel, from the survey
        vs = 0.0; y = float(m.get('heel_offset_ft') or 0.0)
        heel = self.first_md if self.first_md is not None else md
        plan = self.plan_az or 0.0
        prev_md, prev_inc, prev_az = heel, 90.0, 0.0
        for s in svs:
            s_az = wrap180(float(s['azi']) - plan) if s.get('azi') is not None else prev_az
            if s['md'] <= heel:
                prev_inc, prev_az = s['inc'], s_az; continue
            dn, de, dv, _ = min_curvature(prev_inc, prev_az, s['inc'], s_az, s['md'] - prev_md)
            vs += dn; y += de; tvd += dv
            prev_md, prev_inc, prev_az = s['md'], s['inc'], s_az
        if md > prev_md:
            dn, de, dv, _ = min_curvature(prev_inc, prev_az, inc_now, wrap180(az_now - plan), md - prev_md)
            vs += dn; y += de; tvd += dv
        return {'vs': vs, 'y': y, 'tvd': tvd}

    def _tvd_at_bit(self, md: float, inc_now: float) -> float:
        return self._position_at_bit(md, inc_now, (self.plan_az or 0.0))['tvd']

    # -- a decision -------------------------------------------------------------------------------------------------
    def due(self, snap: dict) -> bool:
        st = snap['state']; md = st.get('hole_depth')
        if md is None:
            return False
        d = self.cfg['decision']
        if self.last_decision_md is None:
            return True
        if md - self.last_decision_md >= float(d['every_ft']):
            return True
        return (time.time() - self.last_decision_t) >= float(d['every_s']) and (st.get('rop') or 0) > 0

    def decide(self, snap: dict) -> Optional[dict]:
        obs, ctx = self.observation(snap)
        if obs is None:
            return None
        ex = self.policy.explain(obs)
        ch = ex['choices']
        build = float(ch['build']['choice']); wob = float(ch['wob']['choice']); rpm = float(ch['rpm']['choice']); flow = float(ch['flow']['choice']); cond = ch['conditioning']['choice']
        aim = float(ch['aim']['choice'])
        turn = T.turn_toward(aim, float(ctx['az']))                   # what the aim asks of the tool from where the hole points now
        lim = self.cfg['limits']; tool = self.cfg['tool']
        clipped = []
        if abs(build) > float(lim['max_dls_deg_100ft']):
            build = math.copysign(float(lim['max_dls_deg_100ft']), build); clipped.append('build to the DLS limit')
        total = dls_of(build, turn, float(ctx['inc']))
        if total > float(lim['max_dls_deg_100ft']):
            k_ = float(lim['max_dls_deg_100ft']) / total; build *= k_; turn *= k_; clipped.append('build and turn together to the DLS limit')
        wob = min(max(wob, float(lim['wob_klb'][0])), float(lim['wob_klb'][1])); rpm = min(max(rpm, float(lim['rpm'][0])), float(lim['rpm'][1])); flow = min(max(flow, float(lim['flow_gpm'][0])), float(lim['flow_gpm'][1]))
        ratio = min(1.0, dls_of(build, turn, float(ctx['inc'])) / max(float(tool['max_dls_deg_100ft']), 0.1))
        rec = {
            'utc': _utc(), 'md': ctx['md'], 'vs': ctx['vs'], 'y': ctx['y'], 'tvd_est': ctx['tvd'], 'inc': ctx['inc'], 'az': ctx['az_abs'],
            'recommend': {
                'steer': ('hold' if build == 0 else ('build' if build > 0 else 'drop')), 'build_deg_100ft': build,
                'aim_deg': aim, 'aim_azimuth_deg': (float(ctx['plan_az']) + aim) % 360.0,
                'steer_az': ('hold' if abs(turn) < 0.5 else ('turn right' if turn > 0 else 'turn left')), 'turn_deg_100ft': turn,
                'toolface_deg': toolface_of(build, turn, float(ctx['inc'])), 'steering_ratio': ratio,
                'wob_klb': wob, 'rpm': rpm, 'flow_gpm': flow, 'conditioning': cond,
                'words': self._words(build, wob, rpm, flow, cond, ctx, ch, turn, aim),
            },
            'confidence': {k: round(v['confidence'], 3) for k, v in ch.items()}, 'value': round(ex['value'], 2),
            'reasons': self._reasons(ctx, ch), 'context': {k: (round(v, 2) if isinstance(v, float) else ({kk: round(vv, 3) for kk, vv in v.items()} if isinstance(v, dict) else v)) for k, v in ctx.items()},
            'action': [ch[n]['index'] for n in T.ACTION_NAMES], 'clipped': clipped, 'answer': None, 'armed': bool(self.armed), 'policy': self.policy.meta.get('trained_utc'),
        }
        with self.lock:
            if self.current is not None and self.current.get('answer') is None:
                self.answers['unanswered'] += 1; self.current['answer'] = {'what': 'unanswered', 'utc': _utc()}
                self.records.append('decisions.jsonl', self.current)
            self.current = rec
            self.history.append(rec); self.history = self.history[-500:]
            self.gammas = (self.gammas + [ctx['gamma']])[-5:]
            self.last_build = build; self.last_aim = aim; self.last_turn = turn
            self.last_decision_md = ctx['md']; self.last_decision_t = time.time()
            self.last_action = tuple(rec['action'])
            # the hole model advanced by what the rig did (the same arithmetic as the twin)
            transport = (ctx['flow'] / 500.0) ** 1.5 * (ctx['rpm'] / 120.0) ** 0.4
            self.bed = min(1.0, max(0.0, self.bed + 0.02 * (ctx['rop'] / 60.0) - 0.03 * transport))
            self.wear = min(1.0, self.wear + 0.00004 * float(self.cfg['decision']['every_ft']) * (ctx['wob'] / 25.0) ** 1.5)
        if self.armed:
            self._command(rec)
        return rec

    def _words(self, build, wob, rpm, flow, cond, ctx, ch, turn: float = 0.0, aim: Optional[float] = None) -> str:
        if cond != 'drill':
            what = 'Circulate bottoms up before drilling on' if cond == 'circulate' else 'Ream and wash the last stand, then drill on'
            return f"{what} - the hole model puts the cuttings bed at {ctx['bed']:.0%}" + (f", the bit balled {ctx['balling']:.0%}" if ctx['balling'] > 0.2 else '') + '.'
        inc = float(ctx.get('inc') or 90.0)
        tf = toolface_of(build, turn, inc); ratio = min(1.0, dls_of(build, turn, inc) / 6.0)
        parts = []
        if build != 0:
            parts.append(f'{"Build" if build > 0 else "Drop"} {build:+.1f}°/100 ft')
        plan = ctx.get('plan_az')
        if aim is not None and aim != 0:
            where = f'aim {abs(aim):.0f}° {"right" if aim > 0 else "left"} of the plan' + (f' (azimuth {(plan + aim) % 360.0:.1f}°)' if plan is not None else '')
            parts.append(where + (f', turning {"right" if turn > 0 else "left"} {abs(turn):.1f}°/100 ft' if abs(turn) >= 0.05 else ''))
        elif abs(turn) >= 0.5:
            parts.append(f'turn {"right" if turn > 0 else "left"} {abs(turn):.1f}°/100 ft back to the plan' + (f' (azimuth {plan:.1f}°)' if plan is not None else ''))
        if not parts:
            steer = 'Hold angle and azimuth'
        else:
            steer = ' and '.join(parts) + f' (toolface {tf:.0f}°, {ratio:.0%} steering)'
        return f"{steer}; {wob:.0f} klb WOB, {rpm:.0f} rpm, {flow:.0f} gpm."

    def _reasons(self, ctx, ch) -> List[str]:
        out = []
        m = self.cfg['model']
        e = ctx.get('estimate')
        if e:
            where = 'in the layer' if e['p_in'] >= 0.5 else ('above the layer' if e['p_above'] > e['p_below'] else 'below the layer')
            out.append(f"the position filter puts the bit {where} ({e['p_in']:.0%} in, {e['p_above']:.0%} above, {e['p_below']:.0%} below): {e['d_top']:.1f} ft below the top, {e['d_base']:.1f} ft above the base, ±{e['sigma']:.1f} ft")
        if ctx['deep'] is not None:
            out.append(f"the deep-resistivity tool puts the {'top' if ctx['deep'] > 0 else 'base'} {abs(ctx['deep']):.1f} ft {'above' if ctx['deep'] > 0 else 'below'} the bit")
        out.append(f"gamma {ctx['gamma']:.0f} API, {'in the layer' if ctx['belief'] else 'not the layer'} by the model's values ({m['gamma_in']:.0f} in, {m['gamma_above']:.0f} above, {m['gamma_below']:.0f} below)" + (f", trending {ctx['trend']:+.1f} API a decision" if abs(ctx['trend']) >= 2 else ''))
        out.append(f"the model's bracket: {ctx['tvd'] - ctx['model_top']:.1f} ft below the top, {ctx['model_base'] - ctx['tvd']:.1f} ft above the base")
        if ctx.get('az') is not None:
            side = 'right' if ctx['y'] > 0 else 'left'
            out.append(f"azimuth {ctx['az_abs']:.1f}° against the plan's {ctx['plan_az']:.1f}° ({ctx['az']:+.1f}°), {abs(ctx['y']):.0f} ft {side} of the plan line in a corridor of ±{ctx['corridor']:.0f} ft"
                       + (f"; the model's cross dip {float(m.get('cross_dip_deg') or 0.0):+.2f}°, the filter's {e['cross_dip_deg']:+.2f}°" if e and abs(float(m.get('cross_dip_deg') or 0.0)) + abs(e.get('cross_dip_deg', 0.0)) > 0.05 else ''))
        if ctx['vibration'] > float(self.cfg['limits']['vibration_index']):
            out.append(f"vibration {ctx['vibration']:.2f} over the limit {self.cfg['limits']['vibration_index']}")
        if ctx['ecd'] > float(self.cfg['limits']['frac_gradient_ppg']) - 0.2:
            out.append(f"ECD {ctx['ecd']:.2f} ppg against the fracture gradient {self.cfg['limits']['frac_gradient_ppg']}")
        out.append(f"confidence: steer {ch['build']['confidence']:.0%}, aim {ch['aim']['confidence']:.0%}, weight {ch['wob']['confidence']:.0%}, speed {ch['rpm']['confidence']:.0%}, flow {ch['flow']['confidence']:.0%}")
        return out

    # -- the operator's answer ------------------------------------------------------------------------------------------
    def answer(self, what: str, operator: str, note: str = '', override: Optional[dict] = None) -> dict:
        if what not in ('accept', 'reject', 'override'):
            raise ValueError('the answer is accept, reject or override')
        if not operator.strip():
            raise ValueError('the answer carries the name of who gave it')
        with self.lock:
            if self.current is None:
                raise ValueError('no recommendation to answer yet')
            if self.current.get('answer') is not None:
                raise ValueError('this recommendation was answered already')
            ans = {'what': what, 'by': operator.strip(), 'note': note.strip(), 'utc': _utc()}
            if what == 'override':
                ov = override or {}
                ans['override'] = {k: ov.get(k) for k in ('build_deg_100ft', 'aim_deg', 'turn_deg_100ft', 'wob_klb', 'rpm', 'flow_gpm', 'conditioning') if ov.get(k) is not None}
                if not ans['override']:
                    raise ValueError('an override names what was done instead')
            self.current['answer'] = ans
            self.answers[{'accept': 'accepted', 'reject': 'rejected', 'override': 'overridden'}[what]] += 1
            self.records.append('decisions.jsonl', self.current)
            rec = dict(self.current)
            self.current = None
        return rec

    def executed_action(self) -> Tuple[int, ...]:
        """What the rig is drilling under now: the armed agent's action, the last accepted or overridden one, else hold."""
        with self.lock:
            if self.armed:
                return self.last_action
            for r in reversed(self.history):
                a = r.get('answer')
                if a and a['what'] == 'accept':
                    return tuple(r['action'])
                if a and a['what'] == 'override':
                    return self._action_of(a['override'], r['action'])
            return T.HOLD_ACTION

    @staticmethod
    def _action_of(ov: dict, base: List[int]) -> Tuple[int, ...]:
        a = list(T.full_action(base))
        if ov.get('aim_deg') is not None:
            a[5] = int(np.argmin([abs(float(ov['aim_deg']) - c) for c in T.AIM_CHOICES]))
        elif ov.get('turn_deg_100ft') is not None:                       # a turn made instead: the aim it amounts to, by its direction
            t_ = float(ov['turn_deg_100ft'])
            a[5] = 2 if t_ > 0.25 else (0 if t_ < -0.25 else 1)
        if ov.get('build_deg_100ft') is not None:
            a[0] = int(np.argmin([abs(float(ov['build_deg_100ft']) - c) for c in T.BUILD_CHOICES]))
        if ov.get('wob_klb') is not None:
            a[1] = int(np.argmin([abs(float(ov['wob_klb']) - c) for c in T.WOB_CHOICES]))
        if ov.get('rpm') is not None:
            a[2] = int(np.argmin([abs(float(ov['rpm']) - c) for c in T.RPM_CHOICES]))
        if ov.get('flow_gpm') is not None:
            a[3] = int(np.argmin([abs(float(ov['flow_gpm']) - c) for c in T.FLOW_CHOICES]))
        if ov.get('conditioning') in T.COND_CHOICES:
            a[4] = T.COND_CHOICES.index(ov['conditioning'])
        return tuple(a)

    # -- the closed loop ------------------------------------------------------------------------------------------------
    def arm(self, operator: str, from_md: float, to_md: float, note: str = '') -> dict:
        cl = self.cfg['closed_loop']
        if not cl.get('allowed'):
            raise ValueError('the closed loop is not allowed in this configuration (closed_loop.allowed)')
        if not operator.strip():
            raise ValueError('arming carries the name of the operator who armed it')
        if to_md <= from_md or (to_md - from_md) > float(cl['max_section_ft']):
            raise ValueError(f"a section is at most {cl['max_section_ft']} ft and ends below where it starts")
        snap = self.feed.snapshot()
        if snap.get('age_s') is None or snap['age_s'] > float(cl['stale_s']):
            raise ValueError('the rig feed is stale; nothing is armed on a feed that is not live')
        with self.lock:
            self.armed = {'by': operator.strip(), 'from_md': float(from_md), 'to_md': float(to_md), 'utc': _utc(), 'note': note.strip(), 'commands': 0,
                          'downlink': self.downlink.name, 'sends': self.downlink.sends}
            self.arm_log.append({'event': 'armed', **self.armed})
            self.records.append('commands.jsonl', {'event': 'armed', **self.armed})
        return dict(self.armed)

    def disarm(self, reason: str, by: str = '') -> Optional[dict]:
        with self.lock:
            if not self.armed:
                return None
            ev = {'event': 'disarmed', 'reason': reason, 'by': by, 'utc': _utc(), 'section': dict(self.armed)}
            self.arm_log.append(ev)
            self.records.append('commands.jsonl', ev)
            self.armed = None
        return ev

    def watch(self, snap: dict) -> None:
        """What disarms the loop by itself: a stale feed, a limit crossed, the section drilled."""
        if not self.armed:
            return
        lim = self.cfg['limits']; cl = self.cfg['closed_loop']; st = snap['state']
        if snap.get('age_s') is None or snap['age_s'] > float(cl['stale_s']):
            self.disarm(f"the rig feed went stale ({snap.get('age_s') and round(snap['age_s'])} s)"); return
        md = st.get('hole_depth')
        if md is not None and md >= self.armed['to_md']:
            self.disarm('the armed section is drilled'); return
        if st.get('vibration') is not None and float(st['vibration']) > float(lim['vibration_index']) * 1.25:
            self.disarm(f"vibration {st['vibration']:.2f} crossed the limit"); return
        if st.get('ecd') is not None and float(st['ecd']) > float(lim['frac_gradient_ppg']):
            self.disarm(f"ECD {st['ecd']:.2f} crossed the fracture gradient"); return

    def _command(self, rec: dict) -> None:
        r = rec['recommend']
        cmd = {'utc': _utc(), 'md': rec['md'], 'by': self.armed['by'], 'section': [self.armed['from_md'], self.armed['to_md']],
               'command': {'toolface_deg': r['toolface_deg'], 'steering_ratio': round(r['steering_ratio'], 3), 'build_deg_100ft': r['build_deg_100ft'], 'turn_deg_100ft': r.get('turn_deg_100ft', 0.0),
                           'aim_azimuth_deg': r.get('aim_azimuth_deg'),
                           'wob_klb': r['wob_klb'], 'rpm': r['rpm'], 'flow_gpm': r['flow_gpm'], 'conditioning': r['conditioning']}}
        result = self.downlink.send(cmd)
        cmd['result'] = result
        with self.lock:
            self.armed['commands'] += 1
            rec['command'] = cmd
        self.records.append('commands.jsonl', {'event': 'command', **cmd})

    # -- what the screen shows ------------------------------------------------------------------------------------------
    def status(self) -> dict:
        with self.lock:
            cur = dict(self.current) if self.current else None
            hist = [{k: r[k] for k in ('utc', 'md', 'recommend', 'answer', 'confidence', 'armed')} for r in self.history[-40:]]
            return {'current': cur, 'history': hist, 'answers': dict(self.answers), 'armed': dict(self.armed) if self.armed else None,
                    'arm_log': self.arm_log[-10:], 'closed_loop_allowed': bool(self.cfg['closed_loop'].get('allowed')), 'downlink': self.downlink.describe(),
                    'hole_model': {'bed': self.bed, 'balling': self.balling, 'wear': self.wear}, 'decisions': self.records.counts['decisions'], 'estimate': dict(self.est) if self.est else None,
                    'position': dict(self.pos) if self.pos else None, 'plan_azimuth_deg': self.plan_az,
                    'policy': {k: self.policy.meta.get(k) for k in ('trained_steps', 'iterations', 'trained_utc', 'version')}}
