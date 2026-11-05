# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""service - the driller's program running on the rig PC: the feed read on its thread, the advisor asked once a
second whether a decision is due and the closed loop watched, the screen served; stopped by the signal or a STOP
file in the data folder, with every record closed and the manifest written."""
from __future__ import annotations

import math
import os
import signal
import threading
import time
from typing import Optional

from . import NAME, __version__, advisor as _advisor, config as _config, rig as _rig, status as _status
from .policy import Policy

SHIPPED_POLICY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'policy.json')


def load_policy(cfg: dict) -> Policy:
    p = (cfg.get('policy') or {}).get('file') or SHIPPED_POLICY
    return Policy.load(p)


class Driller:
    def __init__(self, cfg: dict, feed=None, policy: Optional[Policy] = None, twin_seed: int = 7, twin_pace_s: float = 3.0):
        self.cfg = cfg
        self.policy = policy or load_policy(cfg)
        self.records = _advisor.Records(cfg)
        self.feed = feed or _rig.make(cfg, **({'seed': twin_seed, 'pace_s': twin_pace_s} if cfg['rig']['transport'] == 'sim' else {}))
        self.advisor = _advisor.Advisor(cfg, self.policy, self.feed, self.records)
        self.feed.load_surveys(os.path.join(cfg['output']['dir'], cfg['survey']['file']) if not os.path.isabs(cfg['survey']['file']) else cfg['survey']['file'])
        self.server = _status.StatusServer(cfg, self.live, {'/api/answer': self._answer, '/api/arm': self._arm, '/api/disarm': self._disarm}, cfg['output']['dir'])
        self.t_started = time.time()
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.state = 'starting'
        self.ok = False

    # -- the actions the screen posts ---------------------------------------------------------------------------------
    def _answer(self, body: dict) -> dict:
        rec = self.advisor.answer(str(body.get('what') or ''), str(body.get('by') or ''), str(body.get('note') or ''), body.get('override'))
        self._apply_to_twin()
        return {'ok': True, 'answered': rec['utc']}

    def _arm(self, body: dict) -> dict:
        snap = self.feed.snapshot(); md = snap['state'].get('hole_depth')
        if md is None:
            raise ValueError('no hole depth from the rig yet')
        a = self.advisor.arm(str(body.get('by') or ''), float(md), float(body.get('to_md') or 0), str(body.get('note') or ''))
        self._apply_to_twin()
        return {'ok': True, 'armed': a}

    def _disarm(self, body: dict) -> dict:
        ev = self.advisor.disarm('the operator disarmed it', str(body.get('by') or ''))
        self._apply_to_twin()
        return {'ok': True, 'disarmed': ev}

    def _apply_to_twin(self) -> None:
        if isinstance(self.feed, _rig.TwinFeed):
            follow = bool(self.cfg['rig'].get('sim_follows_agent', True)) and self.advisor.current is not None
            self.feed.set_action(self.advisor.last_action if follow else self.advisor.executed_action())

    # -- the loop ----------------------------------------------------------------------------------------------------------
    def _loop(self) -> None:
        last_surveys = -1
        while not self._stop.is_set():
            try:
                snap = self.feed.snapshot()
                age = snap.get('age_s')
                if not snap['connected'] and snap.get('error'):
                    self.state = f"the rig feed is down: {snap['error']}"; self.ok = False
                elif age is None:
                    self.state = 'waiting for the rig feed'; self.ok = False
                elif age > float(self.cfg['closed_loop']['stale_s']):
                    self.state = f'the rig feed is stale ({age:.0f} s without a frame)'; self.ok = False
                else:
                    self.state = 'advising' if not self.advisor.armed else 'ARMED - commanding the tool'; self.ok = True
                self.advisor.watch(snap)
                if self.ok and self.advisor.due(snap):
                    self.advisor.decide(snap)
                    self._apply_to_twin()
                if snap['n_surveys'] != last_surveys:
                    self.records.write_surveys(list(self.feed.surveys)); last_surveys = snap['n_surveys']
            except Exception as e:
                self.state = f'the advisor failed: {type(e).__name__}: {e}'; self.ok = False
            self._stop.wait(1.0)

    def start(self) -> None:
        self.server.start()
        self.feed.start()
        self.thread = threading.Thread(target=self._loop, name='advisor', daemon=True); self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.advisor.disarm('the program stopped')
        self.feed.stop()
        self.records.write_surveys(list(self.feed.surveys))
        self.records.manifest()
        self.server.stop()
        self.state = 'stopped'; self.ok = False

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.server.port}'

    # -- what the screen reads ------------------------------------------------------------------------------------------
    def path_points(self) -> list:
        m = self.cfg['model']
        heel = self.advisor.first_md
        if heel is None:
            return []
        tvd = float(m.get('heel_tvd_ft') or (float(m['layer_top_tvd_ft']) + 0.5 * float(m['thickness_ft'])))
        pts = [{'vs': 0.0, 'tvd': tvd}]
        prev_md, prev_inc = heel, 90.0
        for s in list(self.feed.surveys):
            if s['md'] <= heel:
                prev_inc = s['inc']; continue
            a0, a1 = math.radians(prev_inc), math.radians(s['inc']); L = s['md'] - prev_md
            tvd += L * (math.cos(a0) + math.cos(a1)) / 2.0
            pts.append({'vs': s['md'] - heel, 'tvd': tvd}); prev_md, prev_inc = s['md'], s['inc']
        snap = self.feed.snapshot(); md = snap['state'].get('hole_depth'); inc = snap['state'].get('continuous_inc')
        if md is not None and md > prev_md:
            a0, a1 = math.radians(prev_inc), math.radians(inc if inc is not None else prev_inc)
            tvd += (md - prev_md) * (math.cos(a0) + math.cos(a1)) / 2.0
            pts.append({'vs': md - heel, 'tvd': tvd})
        return pts

    def live(self) -> dict:
        snap = self.feed.snapshot()
        st = self.advisor.status()
        out = {'program': NAME, 'version': __version__, 'site': self.cfg['site'], 'well': self.cfg['well'], 'ok': self.ok, 'state': self.state,
               'feed_note': (f"feed {'live' if snap['connected'] else 'down'}, {snap['frames']} frames, last {snap['age_s']:.0f} s ago" if snap.get('age_s') is not None else 'no frame yet') + (f" · {snap['error']}" if snap.get('error') else ''),
               'rig': snap['state'], 'n_surveys': snap['n_surveys'], 'path': self.path_points(), 'model': {k: v for k, v in self.cfg['model'].items() if k != 'note'},
               'limits': {k: v for k, v in self.cfg['limits'].items() if k != 'note'}, 'current': st['current'], 'advisor': st, 'uptime_s': time.time() - self.t_started}
        if isinstance(self.feed, _rig.TwinFeed):
            s = self.feed.lat.summary()
            out['twin'] = {'seed': self.feed.scene.seed, 'in_layer_pct': s['in_layer_pct'], 'sweet_pct': s['sweet_pct'], 'days': s['days'], 'events': s['events'][-5:], 'ended': s['ended'],
                           'true_layer_top': self.feed.scene.layer_top(self.feed.lat.vs), 'true_tvd': self.feed.lat.tvd, 'label': 'SIMULATION'}
        return out

    def run_forever(self) -> int:
        done = threading.Event()

        def _sig(*a):
            done.set()
        for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, 'SIGBREAK', None)):
            if s is None:
                continue
            try:
                signal.signal(s, _sig)
            except (ValueError, OSError):
                pass
        stop_file = os.path.join(self.cfg['output']['dir'], 'STOP')
        self.start()
        try:
            while not done.is_set():
                done.wait(1.0)
                if os.path.isfile(stop_file):
                    break
        finally:
            self.stop()
            try:
                os.remove(stop_file)
            except OSError:
                pass
        return 0
