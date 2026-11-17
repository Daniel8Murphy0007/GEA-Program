# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""card - the agent's model card and scorecard (after Mitchell et al., "Model Cards for Model Reporting", 2019):
what the policy is for and what it is not for, what it reads, what it chooses between, how it was trained, the
reward it was trained on - printed from the very table the twin pays from - and how it does on the held-out scenes
beside the two drillers it is read between: the textbook driller (the bar) and the oracle told the layer (the
ceiling). Worked out from the policy file each time it is asked for, so the card cannot say more than the policy
does. Every figure on it is SIMULATION: scenes of the twin, not a well.

Two hashes name the policy. The file's SHA-256 changes whenever the file does, and each release stamps its version
in the file; the weights' SHA-256 is taken over the numbers that decide - the weights and the observation's
scaling - and stays the same for as long as the agent is the same agent."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import NAME, __version__, twin as T
from . import policy as P

SCENES = 60
OBS_WORDS: Dict[str, str] = {
    'gamma_now': 'the gamma at the bit', 'gamma_1': 'the gamma one step back', 'gamma_2': 'two steps back', 'gamma_3': 'three steps back', 'gamma_4': 'four steps back',
    'gamma_trend': 'how the gamma has moved over those five readings', 'inclination_minus_90': 'the inclination, from horizontal',
    'model_dist_top': "the distance below the layer's top as the pre-drill model has it", 'model_dist_base': "the distance above its base, the same",
    'deep_res_dist': 'the deep-resistivity tool\'s distance to the nearest boundary', 'deep_res_available': 'whether the string carries that tool',
    'last_build': 'the build or drop asked for last', 'rop': 'the rate of penetration', 'vibration': 'the vibration index (the downhole tool\'s, else read off the surface data)',
    'ecd_margin': 'the room between the ECD and the fracture gradient', 'cuttings_bed': 'the cuttings bed, as the twin keeps it', 'balling': 'bit balling, the same',
    'bit_wear': 'bit wear, the same', 'motor_dp': 'the motor\'s differential pressure', 'progress': 'how far along the lateral the bit is',
    'in_layer_belief': 'whether the gamma is nearer the layer\'s own value than the rock\'s above or below',
    'layer_thickness_model': 'the layer\'s thickness in the pre-drill model', 'filter_p_in': 'the position filter\'s probability the bit is in the layer',
    'filter_p_above': 'that it is above it', 'filter_p_below': 'that it is below it', 'filter_dist_top': 'the filter\'s distance to the top',
    'filter_dist_base': 'its distance to the base', 'filter_sigma': 'the spread of those distances', 'azimuth_minus_plan': 'the azimuth, from the plan\'s',
    'offset_right_of_plan': 'how far right of the plan line the hole is', 'corridor_margin': 'the room left inside the corridor',
    'model_cross_dip': 'the layer\'s dip across the plan in the pre-drill model', 'last_aim': 'where the azimuth was aimed last',
    'filter_cross_dip': 'the cross dip as the filter has corrected it'}
CHOICE_WORDS = {'build': 'the build (+) or drop (-) asked of the steering tool, deg/100 ft', 'wob': 'the weight on bit, klb', 'rpm': 'the rotary speed', 'flow': 'the flow rate, gal/min',
                'conditioning': 'drill on, circulate, or ream and wash', 'aim': 'where to aim the azimuth: degrees off the plan, right positive'}
FOR = ('advice to a directional driller in a lateral: every 30 ft, the build or drop and the turn to ask of the steering tool, the weight, the rotary speed and the flow, '
       'and whether to drill on, circulate or ream - each for the driller to accept, change or turn down')
NOT_FOR = ['a judgement of a well: every figure here is a scene of the twin, and this card holds no result from a drilled lateral',
           'drilling unattended: the closed loop is armed by a named operator for one section, and this version sends nothing to a steering tool (the null downlink)',
           'a formation the pre-drill model does not describe: the agent reads the layer through that model and the position filter, and is as good as they are',
           'well control, or any limit of the rig: it knows the limits it was given in the config and no others']


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def reward_table() -> List[dict]:
    """The reward as the twin pays it: every term, its weight and what it is charged on, under its heading."""
    return [{'group': g, 'term': k, 'weight': T.REWARD[k], 'on': T.REWARD_WORDS[k]} for g, ks in T.REWARD_GROUPS for k in ks]


def _row(e: dict) -> dict:
    by_group = {g: round(sum(e['reward_terms'][k] for k in ks), 2) for g, ks in T.REWARD_GROUPS}
    return {'driller': e['label'], 'in_layer_pct': round(e['in_layer_pct'], 1), 'sweet_pct': round(e['sweet_pct'], 1), 'corridor_pct': round(e['corridor_pct'], 1),
            'ft_per_day': round(e['ft_per_day'], 0), 'days': round(e['days'], 2), 'mean_dls': round(e['mean_dls'], 2), 'vibration_excess': round(e['vibration_excess'], 2),
            'worst_in_layer_pct': round(e['worst_in_layer_pct'], 1), 'stuck': e['stuck'], 'lost': e['lost'], 'losses': e['losses'], 'reward': round(e['reward'], 1), 'reward_by': by_group}


def build(policy_path: Optional[str] = None, scenes: int = SCENES) -> dict:
    """The card of one policy file (the shipped one by default), its held-out scenes drilled now."""
    from . import service as S
    path = policy_path or S.SHIPPED_POLICY
    pol = P.Policy.load(path)
    three = P.evaluate_all(pol, range(int(scenes)))
    tb, ag, orc = (_row(three[k]) for k in ('textbook', 'agent', 'oracle'))
    st = three['standing']
    span_days = ag['days'] / tb['days'] - 1.0 if tb['days'] > 0 else 0.0
    lines = [f"On {scenes} held-out scenes of the twin the agent kept {ag['in_layer_pct']:.1f}% of the hole in the layer, the textbook driller {tb['in_layer_pct']:.1f}% and the oracle "
             f"{orc['in_layer_pct']:.1f}%: the agent stands {st['in_layer_pct_of_the_way']:.0f}% of the way from the textbook to the oracle in hole in the layer, "
             f"{st['reward_of_the_way']:.0f}% in reward.",
             f"It made {ag['ft_per_day']:,.0f} ft a day against the textbook's {tb['ft_per_day']:,.0f}: " + (f"{abs(span_days) * 100:.0f}% {'more' if span_days > 0 else 'fewer'} days a lateral." if abs(span_days) >= 0.005 else 'the same days a lateral.'),
             f"Its worst scene kept {ag['worst_in_layer_pct']:.1f}% in the layer; the textbook's worst, {tb['worst_in_layer_pct']:.1f}%.",
             'SIMULATION: scenes of the twin, drawn from the ranges it was trained on but never trained on nor chosen on. No drilled lateral is in these figures.']
    n_par = int(sum(v.size for v in pol.p.values()))
    return {'program': NAME, 'version': __version__, 'made_utc': _utc(), 'label': 'SIMULATION', 'name': 'the directional driller\'s policy',
            'for': FOR, 'not_for': list(NOT_FOR),
            'reads': [{'name': n, 'what': OBS_WORDS.get(n, '')} for n in T.OBS_NAMES],
            'chooses': [{'name': n, 'what': CHOICE_WORDS.get(n, ''), 'between': list(T.ACTION_CHOICES[i])} for i, n in enumerate(T.ACTION_NAMES)],
            'network': {'layers': [pol.obs_size, pol.hidden, pol.hidden], 'activation': 'tanh', 'parameters': n_par,
                        'how': f"two hidden layers of {pol.hidden}; {n_par:,} numbers; one decision every {T.STEP_FT:g} ft"},
            'training': {'method': 'proximal policy optimization (Schulman et al., 2017), in the twin, on scenes drawn fresh for every batch', **{k: pol.meta.get(k) for k in ('trained_steps', 'iterations', 'trained_utc', 'note')},
                         'scenes': 'Trained on seeds 100000 and up; the checkpoint chosen on scenes 100-139; judged on scenes 0-59, which it never trained on nor was chosen on'},
            'reward': reward_table(),
            'limits_in_the_twin': {'vibration_index': T.VIB_LIMIT, 'motor_dp_psi': T.DP_LIMIT, 'days': T.DAY_LIMIT, 'corridor_end_ft': T.CORRIDOR_END_FT},
            'evaluation': {'scenes': int(scenes), 'rows': [tb, ag, orc], 'standing': {k: round(float(st[k]), 1) for k in ('in_layer_pct_of_the_way', 'reward_of_the_way')}, 'note': st['note'],
                           'lines': lines},
            'policy': {'file': os.path.basename(path), 'file_sha256': _file_sha256(path), 'weights_sha256': pol.weights_sha256(), 'stamped_version': pol.meta.get('version')},
            'lines': lines}


def text(c: dict) -> str:
    ev = c['evaluation']
    out = [f"{c['program']} {c['version']} - the agent's card ({c['label']})", '=' * 60, 'For: ' + c['for'] + '.', 'Not for:'] + [f'  - {x}' for x in c['not_for']]
    out += ['', f"The policy: {c['network']['how']}; trained {c['training'].get('trained_utc')} over {int(c['training'].get('trained_steps') or 0):,} steps ({c['training']['method']}).",
            f"  {c['training']['scenes']}.", f"  weights sha256 {c['policy']['weights_sha256']}", f"  file    sha256 {c['policy']['file_sha256']} ({c['policy']['file']})", '',
            f"The scorecard - {ev['scenes']} held-out scenes of the twin, the same for all three:"]
    out.append(f"  {'driller':<9s} {'in layer':>8s} {'sweet':>6s} {'ft/day':>7s} {'days':>5s} {'DLS':>5s} {'vib over':>8s} {'worst':>6s} {'reward':>7s}")
    for r in ev['rows']:
        out.append(f"  {r['driller']:<9s} {r['in_layer_pct']:>7.1f}% {r['sweet_pct']:>5.1f}% {r['ft_per_day']:>7,.0f} {r['days']:>5.2f} {r['mean_dls']:>5.2f} {r['vibration_excess']:>8.2f} {r['worst_in_layer_pct']:>5.1f}% {r['reward']:>7.1f}")
    out += ['  ' + l for l in ev['lines']]
    out += ['', 'Where the reward came from, a lateral (the mean):', '  ' + f"{'driller':<9s} " + ' '.join(f'{g:>12s}' for g, _ in T.REWARD_GROUPS)]
    for r in ev['rows']:
        out.append('  ' + f"{r['driller']:<9s} " + ' '.join(f"{r['reward_by'][g]:>12.1f}" for g, _ in T.REWARD_GROUPS))
    out += ['', 'The reward it was trained on:']
    for g, ks in T.REWARD_GROUPS:
        out.append(f'  {g}')
        out += [f"    {T.REWARD[k]:>+6.2f}  {T.REWARD_WORDS[k]}" for k in ks]
    out += ['', f"It reads {len(c['reads'])} things:"] + [f"  {r['name']:<24s} {r['what']}" for r in c['reads']]
    out += ['', 'It chooses:'] + [f"  {r['name']:<13s} {r['what']}: {', '.join(str(x) for x in r['between'])}" for r in c['chooses']]
    return '\n'.join(out)


def markdown(c: dict) -> str:
    return f"# {c['program']} {c['version']} - the agent's card\n\nMade {c['made_utc']}. {c['label']}.\n\n```\n{text(c)}\n```\n"


def write(c: dict, folder: str) -> List[str]:
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'model_card.json'), 'w', encoding='utf-8') as f:
        json.dump(c, f, indent=1)
    with open(os.path.join(folder, 'model_card.md'), 'w', encoding='utf-8') as f:
        f.write(markdown(c))
    return ['model_card.json', 'model_card.md']


# -- the card kept ready ---------------------------------------------------------------------------------------------
_CACHE: Dict[str, dict] = {}
_BUSY: Dict[str, bool] = {}


def ready(policy_path: Optional[str] = None, wait: bool = False) -> Optional[dict]:
    """The card of a policy file, worked out once and kept. The scenes take seconds to drill, so a screen asks, is
    answered None while they are drilled in the background, and asks again; `wait` drills them now."""
    import threading
    from . import service as S
    path = os.path.abspath(policy_path or S.SHIPPED_POLICY)
    try:
        key = f'{path}|{os.path.getmtime(path)}|{os.path.getsize(path)}'
    except OSError:
        key = path
    if key in _CACHE:
        return _CACHE[key]
    if wait:
        _CACHE[key] = build(path)
        return _CACHE[key]
    if not _BUSY.get(key):
        _BUSY[key] = True

        def work():
            try:
                _CACHE[key] = build(path)
            except Exception as e:                           # a policy file that cannot be read: say so on the card, once
                _CACHE[key] = {'program': NAME, 'version': __version__, 'label': 'NOT READ', 'error': f'{type(e).__name__}: {e}'}
            finally:
                _BUSY[key] = False
        threading.Thread(target=work, name='gea-driller-card', daemon=True).start()
    return None
