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

SCENES = 300
SCENE_0 = 1000                  # the card's scenes start here: below it are the scenes policies have been chosen on
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


TOOL_ROWS = (('neither tool', False, False), ('the up and down gamma', True, False), ('the deep resistivity', False, True), ('both', True, True))


def _tools(three: dict, scenes) -> dict:
    """The hole in the layer by what the string carries, and what the up and down gamma is worth to the textbook
    driller: the same scenes drilled with the tool on every string and on none, scene by scene."""
    import numpy as np
    scenes = list(scenes)
    sc = [T.Scene(s) for s in scenes]
    azi = np.array([x.azi_gamma for x in sc], dtype=bool); deep = np.array([x.deep_res for x in sc], dtype=bool)
    il = {k: np.array([e['in_layer_pct'] for e in three[k]['episodes']], dtype=np.float64) for k in ('textbook', 'agent', 'oracle')}
    rows = []
    for name, a, d in TOOL_ROWS:
        m = (azi == a) & (deep == d)
        rows.append({'string': name, 'azimuthal_gamma': a, 'deep_resistivity': d, 'scenes': int(m.sum()),
                     **{k: (round(float(il[k][m].mean()), 1) if m.any() else None) for k in il}})
    other = np.array([e['in_layer_pct'] for e in P.evaluate(T.TextbookDriller(), scenes, 'textbook', azimuthal=[not bool(a) for a in azi])['episodes']], dtype=np.float64)
    on = np.where(azi, il['textbook'], other); off = np.where(azi, other, il['textbook'])

    def worth(m) -> dict:
        d = (on - off)[m]
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
        return {'scenes': int(len(d)), 'without': round(float(off[m].mean()), 1) if len(d) else None, 'with': round(float(on[m].mean()), 1) if len(d) else None,
                'mean': round(float(d.mean()), 2) if len(d) else None, 'se': round(se, 2), 'better': int((d > 0).sum()), 'worse': int((d < 0).sum()), 'same': int((d == 0).sum())}
    w = {'all': worth(np.ones(len(scenes), dtype=bool)), 'no_deep_resistivity': worth(~deep), 'deep_resistivity': worth(deep)}
    a, n = w['all'], w['no_deep_resistivity']
    sure = a['mean'] is not None and abs(a['mean']) >= 2.0 * a['se']
    line = (f"What the string reads: with the up and down gamma on every string the textbook driller kept {a['with']:.1f}% of the hole in the layer, with it on none {a['without']:.1f}% - "
            f"{a['mean']:+.1f} points, give or take {a['se']:.1f}, " + ('a difference these scenes can tell from none' if sure else 'NOT a difference these scenes can tell from none')
            + (f"; on the {n['scenes']} strings without the deep resistivity, {n['without']:.1f}% to {n['with']:.1f}% ({n['mean']:+.1f}, give or take {n['se']:.1f})." if n['scenes'] else '.')
            + f" {int(azi.sum())} of the {len(scenes)} scenes' strings carry the tool.")
    return {'rows': rows, 'azimuthal_worth_textbook': w, 'told_apart': bool(sure), 'share_with_azimuthal': round(float(azi.mean()), 2), 'reach_ft': T.AZI_REACH_FT, 'noise': T.AZI_NOISE, 'line': line,
            'reads': ("The up and down gamma goes to the position filter - the filter the textbook driller, the policy and the rig screen all read the layer through - and the policy's "
                      f"{len(T.OBS_NAMES)} inputs are the ones they were: the filter's answers among them are better for the tool, nothing new is asked of the policy.")}


def build(policy_path: Optional[str] = None, scenes: int = SCENES) -> dict:
    """The card of one policy file (the shipped one by default), its held-out scenes drilled now."""
    from . import service as S
    path = policy_path or S.SHIPPED_POLICY
    pol = P.Policy.load(path)
    three = P.evaluate_all(pol, range(SCENE_0, SCENE_0 + int(scenes)))
    import numpy as np

    def paired(key: str, a: str = 'agent', b: str = 'textbook') -> dict:
        """One driller less another on the same scenes: the mean difference and its standard error."""
        d = np.array([x[key] - y[key] for x, y in zip(three[a]['episodes'], three[b]['episodes'])], dtype=np.float64)
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
        return {'mean': round(float(d.mean()), 2), 'se': round(se, 2), 'better': int((d > 0).sum()), 'worse': int((d < 0).sum()), 'same': int((d == 0).sum())}
    diff = {'in_layer_pct': paired('in_layer_pct'), 'reward': paired('reward'), 'ft_per_day': paired('ft_per_day')}
    sure = abs(diff['in_layer_pct']['mean']) >= 2.0 * diff['in_layer_pct']['se']
    tb, ag, orc = (_row(three[k]) for k in ('textbook', 'agent', 'oracle'))
    tools = _tools(three, range(SCENE_0, SCENE_0 + int(scenes)))
    st = three['standing']
    span_days = ag['days'] / tb['days'] - 1.0 if tb['days'] > 0 else 0.0
    lines = [f"On {scenes} held-out scenes of the twin the agent kept {ag['in_layer_pct']:.1f}% of the hole in the layer, the textbook driller {tb['in_layer_pct']:.1f}% and the oracle "
             f"{orc['in_layer_pct']:.1f}%: the agent stands {st['in_layer_pct_of_the_way']:.0f}% of the way from the textbook to the oracle in hole in the layer, "
             f"{st['reward_of_the_way']:.0f}% in reward.",
             f"Scene by scene against the textbook driller, the agent's hole in the layer is {diff['in_layer_pct']['mean']:+.1f} points, give or take {diff['in_layer_pct']['se']:.1f} (one standard error; "
             f"better on {diff['in_layer_pct']['better']} scenes, worse on {diff['in_layer_pct']['worse']}): "
             + ('a difference these scenes can tell from none.' if sure else 'NOT a difference these scenes can tell from none.')
             + f" Its reward is {diff['reward']['mean']:+.1f}, give or take {diff['reward']['se']:.1f}.",
             f"It made {ag['ft_per_day']:,.0f} ft a day against the textbook's {tb['ft_per_day']:,.0f}: " + (f"{abs(span_days) * 100:.0f}% {'more' if span_days > 0 else 'fewer'} days a lateral." if abs(span_days) >= 0.005 else 'the same days a lateral.'),
             f"Its worst scene kept {ag['worst_in_layer_pct']:.1f}% in the layer; the textbook's worst, {tb['worst_in_layer_pct']:.1f}%.",
             ('As shipped the program recommends by the book - the textbook driller\'s rules for every choice (`policy.steer` is `book` in the config) - because the policy\'s steering is not '
              'told from the book\'s on these scenes; what the policy would steer is kept beside each recommendation on the record, and `policy.steer`: `policy` lets it steer.' if not sure else
              'The policy\'s steering is told from the book\'s on these scenes; `policy.steer` in the config says which of the two the program recommends by (the default is `book`).'),
             tools['line'],
             'SIMULATION: scenes of the twin, drawn from the ranges it was trained on but never trained on nor chosen on. No drilled lateral is in these figures.']
    n_par = int(sum(v.size for v in pol.p.values()))
    book = pol.by_the_book()
    day_ft = round(-(24.0 * T.REWARD['hour'] + T.REWARD['finished_per_day']) / T.REWARD['in_layer'] * T.STEP_FT)
    return {'program': NAME, 'version': __version__, 'made_utc': _utc(), 'label': 'SIMULATION', 'name': 'the directional driller\'s policy',
            'for': FOR, 'not_for': list(NOT_FOR),
            'reads': [{'name': n, 'what': OBS_WORDS.get(n, '')} for n in T.OBS_NAMES],
            'chooses': [{'name': n, 'what': CHOICE_WORDS.get(n, ''), 'between': list(T.ACTION_CHOICES[i]), 'by': 'the book' if n in book else 'the policy'} for i, n in enumerate(T.ACTION_NAMES)],
            'by_the_book': list(book),
            'division': ("The policy steers: the build or drop and where to aim the azimuth are its own, learned. The weight, the rotary speed, the flow and whether to drill on or condition the hole "
                         "are the textbook driller's rules - the same rules the bar is drilled by - and the policy was trained with them in force." if book else
                         'The policy makes all six choices itself.'),
            'network': {'layers': [pol.obs_size, pol.hidden, pol.hidden], 'activation': 'tanh', 'parameters': n_par,
                        'how': f"two hidden layers of {pol.hidden}; {n_par:,} numbers; one decision every {T.STEP_FT:g} ft"},
            'training': {'method': 'proximal policy optimization (Schulman et al., 2017), in the twin, on scenes drawn fresh for every batch', **{k: pol.meta.get(k) for k in ('trained_steps', 'iterations', 'trained_utc', 'note')},
                         'scenes': f"Trained on seeds 100000 and up; the checkpoint chosen on scenes {pol.meta.get('selected_on') or '100-139'}; judged on scenes {SCENE_0} and up, which it never trained on nor was chosen on"},
            'rig_day_ft': day_ft,
            'reward': reward_table(),
            'limits_in_the_twin': {'vibration_index': T.VIB_LIMIT, 'motor_dp_psi': T.DP_LIMIT, 'days': T.DAY_LIMIT, 'corridor_end_ft': T.CORRIDOR_END_FT},
            'evaluation': {'scenes': int(scenes), 'rows': [tb, ag, orc], 'standing': {k: round(float(st[k]), 1) for k in ('in_layer_pct_of_the_way', 'reward_of_the_way')}, 'note': st['note'],
                           'first_scene': SCENE_0, 'against_textbook': diff, 'told_apart': bool(sure), 'tools': tools,
                           'lines': lines},
            'policy': {'file': os.path.basename(path), 'file_sha256': _file_sha256(path), 'weights_sha256': pol.weights_sha256(), 'stamped_version': pol.meta.get('version')},
            'lines': lines}


def text(c: dict) -> str:
    ev = c['evaluation']
    out = [f"{c['program']} {c['version']} - the agent's card ({c['label']})", '=' * 60, 'For: ' + c['for'] + '.', 'Not for:'] + [f'  - {x}' for x in c['not_for']]
    out += ['', f"The policy: {c['network']['how']}; trained {c['training'].get('trained_utc')} over {int(c['training'].get('trained_steps') or 0):,} steps ({c['training']['method']}).",
            f"  {c['training']['scenes']}.", '  ' + c['division'],
            f"  A day of rig time is priced at what {c['rig_day_ft']:,} ft of hole in the layer earns.", f"  weights sha256 {c['policy']['weights_sha256']}", f"  file    sha256 {c['policy']['file_sha256']} ({c['policy']['file']})", '',
            f"The scorecard - {ev['scenes']} held-out scenes of the twin, the same for all three:"]
    out.append(f"  (scenes {ev.get('first_scene', 0)} to {ev.get('first_scene', 0) + ev['scenes'] - 1})")
    out.append(f"  {'driller':<9s} {'in layer':>8s} {'sweet':>6s} {'ft/day':>7s} {'days':>5s} {'DLS':>5s} {'vib over':>8s} {'worst':>6s} {'reward':>7s}")
    for r in ev['rows']:
        out.append(f"  {r['driller']:<9s} {r['in_layer_pct']:>7.1f}% {r['sweet_pct']:>5.1f}% {r['ft_per_day']:>7,.0f} {r['days']:>5.2f} {r['mean_dls']:>5.2f} {r['vibration_excess']:>8.2f} {r['worst_in_layer_pct']:>5.1f}% {r['reward']:>7.1f}")
    out += ['  ' + l for l in ev['lines']]
    tl = ev.get('tools')
    if tl:
        out += ['', 'By what the string carries (hole in the layer):', f"  {'the string carries':<24s} {'scenes':>6s} {'textbook':>9s} {'agent':>7s} {'oracle':>7s}"]
        out += [f"  {r['string']:<24s} {r['scenes']:>6d} " + ' '.join((f"{r[k]:>{w}.1f}%" if r[k] is not None else f"{'-':>{w + 1}s}") for k, w in (('textbook', 8), ('agent', 6), ('oracle', 6))) for r in tl['rows']]
        out += ['  ' + tl['reads']]
    out += ['', 'Where the reward came from, a lateral (the mean):', '  ' + f"{'driller':<9s} " + ' '.join(f'{g:>12s}' for g, _ in T.REWARD_GROUPS)]
    for r in ev['rows']:
        out.append('  ' + f"{r['driller']:<9s} " + ' '.join(f"{r['reward_by'][g]:>12.1f}" for g, _ in T.REWARD_GROUPS))
    out += ['', 'The reward it was trained on:']
    for g, ks in T.REWARD_GROUPS:
        out.append(f'  {g}')
        out += [f"    {T.REWARD[k]:>+6.2f}  {T.REWARD_WORDS[k]}" for k in ks]
    out += ['', f"It reads {len(c['reads'])} things:"] + [f"  {r['name']:<24s} {r['what']}" for r in c['reads']]
    out += ['', 'The choices:'] + [f"  {r['name']:<13s} ({r['by']}) {r['what']}: {', '.join(str(x) for x in r['between'])}" for r in c['chooses']]
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


def ready(policy_path: Optional[str] = None, wait: bool = False, scenes: int = SCENES) -> Optional[dict]:
    """The card of a policy file, worked out once and kept. The scenes take seconds to drill, so a screen asks, is
    answered None while they are drilled in the background, and asks again; `wait` drills them now."""
    import threading
    from . import service as S
    path = os.path.abspath(policy_path or S.SHIPPED_POLICY)
    try:
        key = f'{path}|{os.path.getmtime(path)}|{os.path.getsize(path)}|{int(scenes)}'
    except OSError:
        key = path
    if key in _CACHE:
        return _CACHE[key]
    if wait:
        _CACHE[key] = build(path, scenes)
        return _CACHE[key]
    if not _BUSY.get(key):
        _BUSY[key] = True

        def work():
            try:
                _CACHE[key] = build(path, scenes)
            except Exception as e:                           # a policy file that cannot be read: say so on the card, once
                _CACHE[key] = {'program': NAME, 'version': __version__, 'label': 'NOT READ', 'error': f'{type(e).__name__}: {e}'}
            finally:
                _BUSY[key] = False
        threading.Thread(target=work, name='gea-driller-card', daemon=True).start()
    return None
