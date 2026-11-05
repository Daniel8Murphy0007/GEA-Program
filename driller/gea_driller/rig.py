# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""rig - what the rig says, as the driller's program reads it: the EDR's WITS0 feed over TCP or a serial line
(record 1: depths, ROP, weight, speed, torque, flow, standpipe pressure; record 7: the survey; record 8: the MWD
gamma; a vendor's items for the deep-resistivity distance and a vibration index, mapped in the config), the surveys
the directional driller keeps in a CSV, and - for the office, before a rig exists - the twin itself as the rig,
drilling in real time at a chosen pace under whatever the driller decides. The feed is read; the only thing ever
sent to the rig goes through the downlink, and only when it is armed."""
from __future__ import annotations

import csv
import os
import socket
import threading
import time
from typing import Dict, List, Optional

from . import twin as T

STANDARD_ITEMS: Dict[str, str] = {
    '0108': 'bit_depth', '0110': 'hole_depth', '0112': 'block_height', '0113': 'rop', '0114': 'hookload', '0116': 'wob',
    '0120': 'rpm', '0121': 'torque', '0123': 'flow_in', '0130': 'spp', '0105': 'rig_date', '0106': 'rig_time',
    '0708': 'survey_md', '0713': 'survey_inc', '0714': 'survey_azi', '0824': 'gamma', '0825': 'deep_res_dist', '0830': 'vibration', '0831': 'continuous_inc',
}
FIELDS = ('bit_depth', 'hole_depth', 'rop', 'wob', 'rpm', 'torque', 'flow_in', 'spp', 'gamma', 'deep_res_dist', 'vibration', 'continuous_inc', 'ecd')


class FrameParser:
    def __init__(self):
        self.buf = b''; self.in_frame = False; self.cur: Dict[str, str] = {}

    def feed(self, data: bytes) -> List[Dict[str, str]]:
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\n')
            if i < 0:
                break
            line, self.buf = self.buf[:i], self.buf[i + 1:]
            s = line.decode('ascii', 'replace').strip()
            if s == '&&':
                self.in_frame, self.cur = True, {}
            elif s == '!!':
                if self.in_frame:
                    out.append(self.cur)
                self.in_frame, self.cur = False, {}
            elif self.in_frame and len(s) >= 4 and s[:4].isdigit():
                self.cur[s[:4]] = s[4:].strip()
        return out


class Feed:
    """The rig as the program knows it now: the last value of every field with when it came, the surveys."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state: Dict[str, Optional[float]] = {k: None for k in FIELDS}
        self.last_utc: float = 0.0
        self.frames = 0
        self.error: Optional[str] = None
        self.connected = False
        self.surveys: List[dict] = []
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name='rig-feed', daemon=True); self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        pass

    def snapshot(self) -> dict:
        with self.lock:
            return {'state': dict(self.state), 'last_utc': self.last_utc, 'frames': self.frames, 'error': self.error, 'connected': self.connected,
                    'age_s': (time.time() - self.last_utc) if self.last_utc else None, 'surveys': list(self.surveys[-3:]), 'n_surveys': len(self.surveys)}

    def latest_survey(self) -> Optional[dict]:
        with self.lock:
            return dict(self.surveys[-1]) if self.surveys else None

    def add_survey(self, md: float, inc: float, azi: float, source: str = 'feed') -> None:
        with self.lock:
            if self.surveys and abs(self.surveys[-1]['md'] - md) < 1.0:
                return
            self.surveys.append({'md': float(md), 'inc': float(inc), 'azi': float(azi), 'utc': time.time(), 'source': source})

    def load_surveys(self, path: str) -> int:
        """The directional driller's CSV: md, inc, azi (a header line is skipped)."""
        n = 0
        if not os.path.isfile(path):
            return 0
        with open(path, encoding='utf-8', newline='') as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                try:
                    md, inc, azi = float(row[0]), float(row[1]), float(row[2])
                except ValueError:
                    continue
                self.add_survey(md, inc, azi, 'csv'); n += 1
        return n


class Wits0Feed(Feed):
    """WITS0 over TCP or a serial line; every frame's items into the state."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.items = dict(STANDARD_ITEMS); self.items.update({str(k): str(v) for k, v in (cfg['rig'].get('items') or {}).items()})

    def handle(self, frame: Dict[str, str], t: Optional[float] = None) -> None:
        t = time.time() if t is None else t
        with self.lock:
            for code, text in frame.items():
                name = self.items.get(code)
                if name is None:
                    continue
                try:
                    v = float(text)
                except ValueError:
                    continue
                if v <= -999.0:
                    continue
                if name in FIELDS:
                    self.state[name] = v
                elif name == 'survey_md':
                    self._sv_md = v
                elif name == 'survey_inc':
                    self._sv_inc = v
                elif name == 'survey_azi':
                    self._sv_azi = v
            self.frames += 1; self.last_utc = t
        md = getattr(self, '_sv_md', None); inc = getattr(self, '_sv_inc', None); azi = getattr(self, '_sv_azi', None)
        if md is not None and inc is not None:
            self.add_survey(md, inc, azi if azi is not None else 0.0, 'wits0')

    def _run(self) -> None:
        rig = self.cfg['rig']
        while not self._stop.is_set():
            parser = FrameParser()
            try:
                if rig['transport'] == 'tcp':
                    with socket.create_connection((rig['host'], int(rig['port'])), timeout=10) as s:
                        s.settimeout(5.0); self.connected = True; self.error = None
                        while not self._stop.is_set():
                            try:
                                data = s.recv(4096)
                            except socket.timeout:
                                continue
                            if not data:
                                raise ConnectionError('the EDR closed the connection')
                            for fr in parser.feed(data):
                                self.handle(fr)
                else:
                    import serial  # the serial extra
                    with serial.Serial(rig['serial'], int(rig.get('baud', 9600)), timeout=1) as ser:
                        self.connected = True; self.error = None
                        while not self._stop.is_set():
                            data = ser.read(256)
                            if data:
                                for fr in parser.feed(data):
                                    self.handle(fr)
            except Exception as e:
                self.connected = False; self.error = f'{type(e).__name__}: {e}'
                self._stop.wait(5.0)


class TwinFeed(Feed):
    """The twin as the rig, for the office: it drills one 30-ft step every `pace_s` seconds under the action the
    driller's program hands it (the agent's when armed, the accepted recommendation otherwise, the last one until
    then), and reports what a rig would - depths, the gamma at the bit, the deep-resistivity distance when the
    scene has the tool, the vibration index, a survey every 90 ft."""

    def __init__(self, cfg: dict, seed: int = 7, pace_s: float = 3.0):
        super().__init__(cfg)
        self.scene = T.Scene(seed, length_ft=float(cfg['model'].get('lateral_length_ft', 4000.0)))
        self.lat = T.Lateral(self.scene)
        self.pace_s = float(pace_s)
        self.action = (2, 1, 1, 1, 0)
        self.heel_md = float(cfg['model'].get('heel_md_ft', 10200.0))
        self.obs = self.lat.reset()
        # the config's model becomes what the pre-drill model of this scene says (with the error a real model has), and
        # the heel what the survey would give; the true layer stays the twin's secret
        s = self.scene; m = cfg['model']
        m.update({'layer_top_tvd_ft': round(s.model_top(0.0), 1), 'thickness_ft': round(s.model_thickness, 1), 'dip_deg': round(s.dip_deg + s.model_dip_error, 2),
                  'gamma_in': round(s.g_in), 'gamma_above': round(s.g_above), 'gamma_below': round(s.g_below), 'deep_resistivity_tool': s.deep_res,
                  'lateral_length_ft': s.length_ft, 'heel_tvd_ft': round(self.lat.tvd, 1)})
        self.last_info: dict = {}
        self.connected = True
        self._publish()
        self.add_survey(self.heel_md, self.lat.incl, 0.0, 'twin')

    def set_action(self, action) -> None:
        with self.lock:
            self.action = tuple(int(a) for a in action)

    def _publish(self) -> None:
        lat = self.lat; s = self.scene
        deep, have = s.deep_distance(lat.vs, lat.tvd, lat.r)
        with self.lock:
            self.state.update({'bit_depth': self.heel_md + lat.footage, 'hole_depth': self.heel_md + lat.footage, 'rop': lat.rop,
                               'wob': T.WOB_CHOICES[self.action[1]], 'rpm': T.RPM_CHOICES[self.action[2]], 'flow_in': T.FLOW_CHOICES[self.action[3]],
                               'torque': 8.0 + 0.1 * lat.dp, 'spp': 2800.0 + lat.dp, 'gamma': lat.gammas[-1], 'deep_res_dist': deep if have else None,
                               'vibration': lat.vib, 'continuous_inc': lat.incl, 'ecd': lat.ecd})
            self.frames += 1; self.last_utc = time.time()

    def _run(self) -> None:
        next_survey = self.heel_md + 90.0
        while not self._stop.is_set():
            if self._stop.wait(self.pace_s):
                break
            if self.lat.done:
                self.error = f'the twin finished: {self.lat.ended}'
                continue
            with self.lock:
                a = self.action
            self.obs, r, done, info = self.lat.step(a)
            self.last_info = info
            self._publish()
            if self.heel_md + self.lat.footage >= next_survey:
                self.add_survey(next_survey, self.lat.incl, 0.0, 'twin'); next_survey += 90.0


def make(cfg: dict, **kw) -> Feed:
    if cfg['rig']['transport'] == 'sim':
        return TwinFeed(cfg, **kw)
    return Wits0Feed(cfg)
