# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""policy - the agent: a small neural network (two hidden layers of 64) that reads the twin's observation and
answers five choices at once - the build or drop to ask of the tool, the weight on bit, the rotary speed, the
flow rate, and drill, circulate or ream - and a value estimate beside them; trained by proximal policy
optimization (Schulman et al., 2017: clipped surrogate objective, generalized advantage estimation, several
epochs of minibatch updates per batch of experience) with Adam, on scenes drawn fresh for every batch so no
formation is learned by heart. Numpy only: the arithmetic is written out, including the gradients, so the program
depends on nothing it cannot carry. The trained weights are one JSON file (policy.json) the rig PC loads."""
from __future__ import annotations

import json
import math
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import __version__, twin as T

HIDDEN = 64
N_LOGITS = sum(T.ACTION_SIZES)
HEAD_SLICES = []
_o = 0
for _n in T.ACTION_SIZES:
    HEAD_SLICES.append(slice(_o, _o + _n)); _o += _n


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


class Policy:
    """The network and what it answers; weights as plain arrays."""

    def __init__(self, seed: int = 0, obs_size: int = T.OBS_SIZE, hidden: int = HIDDEN):
        r = np.random.RandomState(seed)
        self.obs_size = obs_size; self.hidden = hidden
        s1 = math.sqrt(2.0 / obs_size); s2 = math.sqrt(2.0 / hidden)
        self.p: Dict[str, np.ndarray] = {
            'W1': r.normal(0, s1, (obs_size, hidden)), 'b1': np.zeros(hidden),
            'W2': r.normal(0, s2, (hidden, hidden)), 'b2': np.zeros(hidden),
            'W3': r.normal(0, 0.01, (hidden, N_LOGITS + 1)), 'b3': np.zeros(N_LOGITS + 1),
        }
        self.obs_mean = np.zeros(obs_size); self.obs_std = np.ones(obs_size)
        self.meta: Dict[str, object] = {'trained_steps': 0, 'iterations': 0, 'version': __version__}

    # -- forward ----------------------------------------------------------------------------------------------------------
    def _norm(self, obs: np.ndarray) -> np.ndarray:
        return np.clip((obs - self.obs_mean) / self.obs_std, -5.0, 5.0)

    def forward(self, obs: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """obs (n, obs_size) -> logits (n, N_LOGITS), value (n,), and the hidden activations for the backward pass."""
        x = self._norm(np.atleast_2d(obs))
        h1 = np.tanh(x @ self.p['W1'] + self.p['b1'])
        h2 = np.tanh(h1 @ self.p['W2'] + self.p['b2'])
        z = h2 @ self.p['W3'] + self.p['b3']
        return z[:, :N_LOGITS], z[:, N_LOGITS], h1, h2

    def probs(self, obs: np.ndarray) -> List[np.ndarray]:
        logits, _, _, _ = self.forward(obs)
        return [_softmax(logits[:, sl]) for sl in HEAD_SLICES]

    def act(self, obs: np.ndarray, r: Optional[np.random.RandomState] = None, greedy: bool = False) -> Tuple[Tuple[int, ...], float, float]:
        """One decision: the action, its log-probability, the value. Greedy takes the likeliest of each head."""
        logits, v, _, _ = self.forward(obs)
        acts = []; logp = 0.0
        for sl in HEAD_SLICES:
            p = _softmax(logits[0, sl])
            a = int(np.argmax(p)) if greedy else int((r or np.random).choice(len(p), p=p))
            acts.append(a); logp += math.log(max(p[a], 1e-12))
        return tuple(acts), logp, float(v[0])

    def explain(self, obs: np.ndarray) -> dict:
        """The decision in operational units with the confidence of each choice and the value the agent puts on
        the state - what the advisory screen shows."""
        logits, v, _, _ = self.forward(obs)
        out = {'value': float(v[0]), 'choices': {}}
        for name, sl, choices in zip(T.ACTION_NAMES, HEAD_SLICES, (T.BUILD_CHOICES, T.WOB_CHOICES, T.RPM_CHOICES, T.FLOW_CHOICES, T.COND_CHOICES)):
            p = _softmax(logits[0, sl]); a = int(np.argmax(p))
            out['choices'][name] = {'choice': choices[a], 'confidence': float(p[a]), 'distribution': [float(x) for x in p], 'index': a}
        return out

    # -- the file ---------------------------------------------------------------------------------------------------------
    def to_json(self) -> dict:
        return {'program': 'GEA Driller', 'version': __version__, 'obs_size': self.obs_size, 'hidden': self.hidden, 'action_sizes': list(T.ACTION_SIZES),
                'obs_names': list(T.OBS_NAMES), 'action_names': list(T.ACTION_NAMES), 'obs_mean': self.obs_mean.tolist(), 'obs_std': self.obs_std.tolist(),
                'weights': {k: v.tolist() for k, v in self.p.items()}, 'meta': self.meta}

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_json(), f)

    @classmethod
    def load(cls, path: str) -> 'Policy':
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return cls.from_json(d)

    @classmethod
    def from_json(cls, d: dict) -> 'Policy':
        if list(d.get('action_sizes', [])) != list(T.ACTION_SIZES) or int(d.get('obs_size', 0)) != T.OBS_SIZE:
            raise ValueError('this policy file was made for another observation or action layout')
        pol = cls(obs_size=int(d['obs_size']), hidden=int(d['hidden']))
        pol.p = {k: np.array(v, dtype=np.float64) for k, v in d['weights'].items()}
        pol.obs_mean = np.array(d['obs_mean'], dtype=np.float64); pol.obs_std = np.array(d['obs_std'], dtype=np.float64)
        pol.meta = dict(d.get('meta', {}))
        return pol


# -- training -------------------------------------------------------------------------------------------------------------

class _Adam:
    def __init__(self, params: Dict[str, np.ndarray], lr: float = 3e-4, b1: float = 0.9, b2: float = 0.999, eps: float = 1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, b1, b2, eps
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params: Dict[str, np.ndarray], grads: Dict[str, np.ndarray]) -> None:
        self.t += 1
        for k in params:
            g = grads[k]
            self.m[k] = self.b1 * self.m[k] + (1 - self.b1) * g
            self.v[k] = self.b2 * self.v[k] + (1 - self.b2) * g * g
            mh = self.m[k] / (1 - self.b1 ** self.t); vh = self.v[k] / (1 - self.b2 ** self.t)
            params[k] -= self.lr * mh / (np.sqrt(vh) + self.eps)


def _gae(rewards: np.ndarray, values: np.ndarray, dones: np.ndarray, last_value: float, gamma: float, lam: float) -> Tuple[np.ndarray, np.ndarray]:
    n = len(rewards)
    adv = np.zeros(n); last = 0.0
    for t in range(n - 1, -1, -1):
        nv = last_value if t == n - 1 else values[t + 1]
        nonterminal = 1.0 - dones[t]
        delta = rewards[t] + gamma * nv * nonterminal - values[t]
        last = delta + gamma * lam * nonterminal * last
        adv[t] = last
    return adv, adv + values


def _loss_and_grads(pol: Policy, obs: np.ndarray, acts: np.ndarray, old_logp: np.ndarray, adv: np.ndarray, ret: np.ndarray,
                    clip: float, vf_coef: float, ent_coef: float) -> Tuple[Dict[str, np.ndarray], dict]:
    n = obs.shape[0]
    logits, v, h1, h2 = pol.forward(obs)
    x = pol._norm(obs)
    # the policy: log-probability of the taken action, its gradient on the logits
    logp = np.zeros(n); dlogits = np.zeros_like(logits); ent = np.zeros(n); dent = np.zeros_like(logits)
    for hi, sl in enumerate(HEAD_SLICES):
        p = _softmax(logits[:, sl])
        a = acts[:, hi]
        onehot = np.zeros_like(p); onehot[np.arange(n), a] = 1.0
        logp += np.log(np.maximum(p[np.arange(n), a], 1e-12))
        dlogits[:, sl] = onehot - p                                  # d logp / d logits
        logp_all = np.log(np.maximum(p, 1e-12))
        h = -(p * logp_all).sum(axis=1)
        ent += h
        dent[:, sl] = -p * (logp_all + h[:, None])                   # d entropy / d logits
    ratio = np.exp(logp - old_logp)
    unclipped = ratio * adv
    clipped = np.clip(ratio, 1 - clip, 1 + clip) * adv
    use_unclipped = unclipped <= clipped                            # min(): the gradient flows where the unclipped term is the minimum
    pg_loss = -np.mean(np.minimum(unclipped, clipped))
    dL_dlogp = np.where(use_unclipped, -ratio * adv, 0.0) / n
    v_err = v - ret
    v_loss = 0.5 * np.mean(v_err ** 2)
    ent_mean = ent.mean()
    # gradient on the output z = [logits, value]
    dz = np.zeros((n, N_LOGITS + 1))
    dz[:, :N_LOGITS] = dL_dlogp[:, None] * dlogits - ent_coef * dent / n
    dz[:, N_LOGITS] = vf_coef * v_err / n
    g = {}
    g['W3'] = h2.T @ dz; g['b3'] = dz.sum(axis=0)
    dh2 = dz @ pol.p['W3'].T * (1 - h2 ** 2)
    g['W2'] = h1.T @ dh2; g['b2'] = dh2.sum(axis=0)
    dh1 = dh2 @ pol.p['W2'].T * (1 - h1 ** 2)
    g['W1'] = x.T @ dh1; g['b1'] = dh1.sum(axis=0)
    approx_kl = float(np.mean(old_logp - logp))
    clipfrac = float(np.mean(np.abs(ratio - 1.0) > clip))
    return g, {'pg_loss': float(pg_loss), 'v_loss': float(v_loss), 'entropy': float(ent_mean), 'approx_kl': approx_kl, 'clipfrac': clipfrac}


def train(pol: Policy, seconds: float = 60.0, iterations: Optional[int] = None, batch_steps: int = 4096, n_envs: int = 16, epochs: int = 6,
          minibatch: int = 512, lr: float = 3e-4, gamma: float = 0.99, lam: float = 0.95, clip: float = 0.2, vf_coef: float = 0.5,
          ent_coef: float = 0.01, seed: int = 0, scene_seed0: int = 100000, log: Optional[Callable[[str], None]] = None) -> dict:
    """PPO until the time or the iteration count is used up; scenes drawn fresh for every batch (seeds above
    scene_seed0, so the held-out scenes below it are never trained on). Returns the training record."""
    r = np.random.RandomState(seed)
    opt = _Adam(pol.p, lr=lr)
    t0 = time.monotonic()
    rec = {'iterations': [], 'batch_steps': batch_steps, 'n_envs': n_envs, 'epochs': epochs, 'minibatch': minibatch, 'lr': lr, 'gamma': gamma, 'lam': lam,
           'clip': clip, 'ent_coef': ent_coef, 'seed': seed}
    say = log or (lambda s: None)
    scene_counter = scene_seed0 + int(pol.meta.get('iterations', 0)) * n_envs * 4
    it = 0
    # observation normalization from a first batch of the textbook driller and random actions, then frozen
    if int(pol.meta.get('trained_steps', 0)) == 0:
        sample = []
        for k in range(8):
            lat = T.Lateral(T.Scene(scene_counter + k))
            o = lat.reset()
            while not lat.done and lat.steps < 200:
                sample.append(o)
                a = T.textbook_action(o) if r.rand() < 0.5 else tuple(r.randint(n) for n in T.ACTION_SIZES)
                o, _, _, _ = lat.step(a)
        sample = np.array(sample)
        pol.obs_mean = sample.mean(axis=0); pol.obs_std = sample.std(axis=0) + 1e-3
    while True:
        if iterations is not None and it >= iterations:
            break
        if iterations is None and time.monotonic() - t0 >= seconds:
            break
        # rollout
        envs = [T.Lateral(T.Scene(scene_counter + k)) for k in range(n_envs)]
        scene_counter += n_envs
        obs_cur = [e.reset() for e in envs]
        B_obs, B_act, B_logp, B_val, B_rew, B_done = [], [], [], [], [], []
        ep_returns: List[float] = []; ep_inlayer: List[float] = []; ep_cur = [0.0] * n_envs
        steps = 0
        while steps < batch_steps:
            for k, e in enumerate(envs):
                a, lp, v = pol.act(obs_cur[k], r)
                o2, rew, done, _ = e.step(a)
                B_obs.append(obs_cur[k]); B_act.append(a); B_logp.append(lp); B_val.append(v); B_rew.append(rew); B_done.append(1.0 if done else 0.0)
                ep_cur[k] += rew
                steps += 1
                if done:
                    ep_returns.append(ep_cur[k]); ep_inlayer.append(e.summary()['in_layer_pct']); ep_cur[k] = 0.0
                    envs[k] = T.Lateral(T.Scene(scene_counter)); scene_counter += 1
                    o2 = envs[k].reset()
                obs_cur[k] = o2
                if steps >= batch_steps:
                    break
        # the batch as arrays; GAE along the interleaved sequence is wrong, so compute per env by re-walking the order
        obs_a = np.array(B_obs); act_a = np.array(B_act); logp_a = np.array(B_logp); val_a = np.array(B_val); rew_a = np.array(B_rew); done_a = np.array(B_done)
        # per-env streams: indices k, k+n_envs, ... (the interleaving above)
        adv_a = np.zeros(len(rew_a)); ret_a = np.zeros(len(rew_a))
        for k in range(n_envs):
            idx = np.arange(k, len(rew_a), n_envs)
            if len(idx) == 0:
                continue
            _, _, lastv = pol.act(obs_cur[k], r)
            adv_k, ret_k = _gae(rew_a[idx], val_a[idx], done_a[idx], lastv, gamma, lam)
            adv_a[idx] = adv_k; ret_a[idx] = ret_k
        adv_n = (adv_a - adv_a.mean()) / (adv_a.std() + 1e-8)
        # the updates
        n = len(rew_a); stats = {}
        for ep in range(epochs):
            perm = r.permutation(n)
            for s0 in range(0, n, minibatch):
                mb = perm[s0:s0 + minibatch]
                g, stats = _loss_and_grads(pol, obs_a[mb], act_a[mb], logp_a[mb], adv_n[mb], ret_a[mb], clip, vf_coef, ent_coef)
                gn = math.sqrt(sum(float((x * x).sum()) for x in g.values()))
                if gn > 0.5:
                    for kk in g:
                        g[kk] *= 0.5 / gn
                opt.step(pol.p, g)
        it += 1
        pol.meta['trained_steps'] = int(pol.meta.get('trained_steps', 0)) + n
        pol.meta['iterations'] = int(pol.meta.get('iterations', 0)) + 1
        line = {'iteration': pol.meta['iterations'], 'steps': pol.meta['trained_steps'], 'episodes': len(ep_returns),
                'mean_return': float(np.mean(ep_returns)) if ep_returns else None, 'mean_in_layer_pct': float(np.mean(ep_inlayer)) if ep_inlayer else None,
                'elapsed_s': round(time.monotonic() - t0, 1), **{k: round(v, 4) for k, v in stats.items()}}
        rec['iterations'].append(line)
        say(f"it {line['iteration']:4d}  steps {line['steps']:8d}  return {line['mean_return'] if line['mean_return'] is not None else float('nan'):8.1f}  "
            f"in-layer {line['mean_in_layer_pct'] if line['mean_in_layer_pct'] is not None else float('nan'):5.1f}%  ent {stats.get('entropy', 0):.3f}  kl {stats.get('approx_kl', 0):.4f}  {line['elapsed_s']}s")
    pol.meta['trained_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    rec['final'] = rec['iterations'][-1] if rec['iterations'] else None
    return rec


def evaluate(act_fn: Callable[[np.ndarray], Sequence[int]], scenes: Sequence[int] = range(20), label: str = '') -> dict:
    """The held-out scenes, one episode each, and the means that matter."""
    res = [T.run_episode(T.Lateral(T.Scene(s)), act_fn) for s in scenes]
    f = lambda k: float(np.mean([x[k] for x in res]))
    return {'label': label, 'scenes': list(scenes), 'n': len(res), 'in_layer_pct': f('in_layer_pct'), 'sweet_pct': f('sweet_pct'), 'ft_per_day': f('ft_per_day'),
            'reward': f('reward'), 'mean_dls': f('mean_dls'), 'tortuosity_deg': f('tortuosity_deg'), 'stuck': int(sum(x['stuck'] for x in res)),
            'losses': int(sum(1 for x in res for e in x['events'] if e.startswith('losses'))), 'days': f('days'), 'vibration_excess': f('vibration_excess'),
            'conditioning_steps': f('conditioning_steps'), 'episodes': res}


def policy_act_fn(pol: Policy, greedy: bool = True, seed: int = 0) -> Callable[[np.ndarray], Tuple[int, ...]]:
    r = np.random.RandomState(seed)
    return lambda o: pol.act(o, r, greedy=greedy)[0]
