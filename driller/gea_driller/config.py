# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""config - the driller's one file: the well and the site, where the rig's feed comes from, what the pre-drill
model says of the target layer, the tool's limits, the limits the agent must keep inside, the policy file, and
whether the closed loop may ever be armed. JSON, checked on load so a mistake is named before a decision is made."""
from __future__ import annotations

import json
import os
from typing import Dict

DEFAULT_PATH = '/etc/gea-driller/config.json'
TRANSPORTS = ('tcp', 'serial', 'sim')
DOWNLINKS = ('null',)

EXAMPLE: Dict = {
    "site": "Pad 3",
    "well": "Pad 3 H-1",
    "rig": {"transport": "sim", "host": "10.0.0.5", "port": 5001, "serial": "COM3", "baud": 9600, "items": {}, "sim_follows_agent": True, "vibration_window_s": 60.0, "vibration_tool": False,
            "note": "the EDR's WITS0 feed: tcp (host, port), serial (the COM port), or sim (the twin runs as the rig, for the office; sim_follows_agent: the twin drills every recommendation as if accepted); items: a rig that numbers the WITS items differently; vibration_window_s: the minute of surface torque, weight and hook load the vibration index is read off when the string has no downhole vibration tool (its item 0830 is taken when it comes); vibration_tool: for the twin as the rig, whether it publishes a downhole index"},
    "survey": {"file": "surveys.csv", "note": "measured depth, inclination, azimuth per station as the directional driller keeps them; the continuous inclination item of the feed between stations"},
    "model": {"layer_top_tvd_ft": 9000.0, "thickness_ft": 40.0, "dip_deg": 0.0, "cross_dip_deg": 0.0, "gamma_in": 60.0, "gamma_above": 130.0, "gamma_below": 115.0,
              "deep_resistivity_tool": False, "deep_range_ft": 15.0, "lateral_length_ft": 4000.0, "heel_md_ft": 10200.0, "heel_tvd_ft": 9020.0,
              "plan_azimuth_deg": None, "corridor_ft": 100.0, "heel_offset_ft": 0.0,
              "note": "the pre-drill model from the offset wells: the target layer's top at the heel, its thickness, its dip along the lateral (dip_deg, positive deepening ahead) and across it (cross_dip_deg, positive deepening to the right of the plan line), the gamma of the layer and of the rock above and below; heel_md_ft and heel_tvd_ft: where the lateral starts, from the survey; plan_azimuth_deg: the lateral's planned azimuth (null: the first survey's azimuth at the heel is taken); corridor_ft: how far to the right and left of the plan line the hole may run (the lease line, the offset wells); heel_offset_ft: where the heel sits across the plan line, right positive, from the survey"},
    "tool": {"max_dls_deg_100ft": 6.0, "tendency_deg_100ft": 0.0, "note": "the steering tool's cap, and what it builds or drops on its own"},
    "limits": {"max_dls_deg_100ft": 4.0, "vibration_index": 1.2, "frac_gradient_ppg": 12.2, "mud_weight_ppg": 10.4, "motor_dp_psi": 600.0,
               "wob_klb": [10.0, 40.0], "rpm": [60.0, 180.0], "flow_gpm": [350.0, 650.0],
               "note": "what no recommendation and no command may go outside; the agent is penalized for them in the twin and stopped by them on the rig"},
    "decision": {"every_ft": 30.0, "every_s": 60.0, "note": "a decision every 30 ft of new hole, and at least every 60 s while drilling"},
    "policy": {"file": "", "note": "empty: the policy shipped inside the program"},
    "closed_loop": {"allowed": False, "downlink": "null", "max_section_ft": 500.0, "stale_s": 120.0,
                    "note": "allowed false: the switch is not on the screen at all; downlink null: commands are logged and never sent (no vendor protocol ships); max_section_ft: an arming lasts at most this much hole"},
    "output": {"dir": "/var/lib/gea-driller/data", "note": "decisions.jsonl, commands.jsonl, surveys.csv, manifest.json; GEA pulls them"},
    "status": {"port": 8790, "bind": "0.0.0.0"},
}


class ConfigError(ValueError):
    pass


def load(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return check(json.load(f))


def check(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ConfigError('the config is a JSON object')
    for k in ('site', 'well', 'rig', 'model', 'limits', 'output', 'status'):
        if k not in cfg:
            raise ConfigError(f'the config needs "{k}"')
    rig = cfg['rig']
    if rig.get('transport') not in TRANSPORTS:
        raise ConfigError(f'rig.transport is one of {", ".join(TRANSPORTS)}')
    m = cfg['model']
    for k in ('layer_top_tvd_ft', 'thickness_ft', 'dip_deg', 'gamma_in', 'gamma_above', 'gamma_below', 'lateral_length_ft'):
        if k not in m:
            raise ConfigError(f'model needs "{k}"')
    if not (5.0 <= float(m['thickness_ft']) <= 300.0):
        raise ConfigError('model.thickness_ft is 5 to 300')
    if not (-15.0 <= float(m['dip_deg']) <= 15.0):
        raise ConfigError('model.dip_deg is -15 to 15 along the lateral')
    m.setdefault('cross_dip_deg', 0.0); m.setdefault('plan_azimuth_deg', None); m.setdefault('corridor_ft', 100.0); m.setdefault('heel_offset_ft', 0.0)
    if m['cross_dip_deg'] is None:
        m['cross_dip_deg'] = 0.0
    if not (-15.0 <= float(m['cross_dip_deg']) <= 15.0):
        raise ConfigError('model.cross_dip_deg is -15 to 15 across the lateral')
    if m['plan_azimuth_deg'] is not None and not (0.0 <= float(m['plan_azimuth_deg']) < 360.0):
        raise ConfigError('model.plan_azimuth_deg is 0 to 360, or null for the first survey\'s')
    if not (10.0 <= float(m['corridor_ft']) <= 2000.0):
        raise ConfigError('model.corridor_ft is 10 to 2000 ft each side of the plan line')
    lim = cfg['limits']
    for k in ('max_dls_deg_100ft', 'vibration_index', 'frac_gradient_ppg', 'mud_weight_ppg', 'motor_dp_psi'):
        if k not in lim:
            raise ConfigError(f'limits needs "{k}"')
    if not (0.5 <= float(lim['max_dls_deg_100ft']) <= 15.0):
        raise ConfigError('limits.max_dls_deg_100ft is 0.5 to 15')
    if float(lim['frac_gradient_ppg']) <= float(lim['mud_weight_ppg']):
        raise ConfigError('limits.frac_gradient_ppg is above limits.mud_weight_ppg')
    for k in ('wob_klb', 'rpm', 'flow_gpm'):
        v = lim.get(k)
        if not (isinstance(v, (list, tuple)) and len(v) == 2 and float(v[0]) < float(v[1])):
            raise ConfigError(f'limits.{k} is [low, high]')
    cl = cfg.setdefault('closed_loop', {})
    cl.setdefault('allowed', False); cl.setdefault('downlink', 'null'); cl.setdefault('max_section_ft', 500.0); cl.setdefault('stale_s', 120.0)
    if cl['downlink'] not in DOWNLINKS:
        raise ConfigError(f'closed_loop.downlink is one of {", ".join(DOWNLINKS)}: no vendor protocol ships with this version')
    d = cfg.setdefault('decision', {})
    d.setdefault('every_ft', 30.0); d.setdefault('every_s', 60.0)
    cfg.setdefault('tool', {}).setdefault('max_dls_deg_100ft', 6.0); cfg['tool'].setdefault('tendency_deg_100ft', 0.0)
    cfg.setdefault('policy', {}).setdefault('file', '')
    cfg.setdefault('survey', {}).setdefault('file', 'surveys.csv')
    m.setdefault('heel_md_ft', 10200.0); m.setdefault('heel_tvd_ft', float(m['layer_top_tvd_ft']) + 0.5 * float(m['thickness_ft']))
    cfg['rig'].setdefault('sim_follows_agent', True); cfg['rig'].setdefault('vibration_window_s', 60.0); cfg['rig'].setdefault('vibration_tool', False)
    if not (10.0 <= float(cfg['rig']['vibration_window_s']) <= 600.0):
        raise ConfigError('rig.vibration_window_s is 10 to 600 seconds')
    st = cfg['status']
    st.setdefault('port', 8790); st.setdefault('bind', '0.0.0.0')
    if not (0 <= int(st['port']) <= 65535):
        raise ConfigError('status.port is 0-65535')
    if not cfg['output'].get('dir'):
        raise ConfigError('output.dir names the data folder')
    return cfg


def write_example(path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(EXAMPLE, f, indent=1)
    return path
