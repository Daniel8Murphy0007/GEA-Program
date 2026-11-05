# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gea-driller - the directional driller's agent, its few commands:

    gea-driller run [--config C:\\gea-driller\\config.json]        advise (or command, when armed) until stopped
    gea-driller sim [--seed 7] [--pace 3] [--port 8790]               the twin as the rig, on this computer, to look at the screen
    gea-driller train --seconds 600 --out policy.json [--resume policy.json]   train the policy in the twin
    gea-driller evaluate [--policy policy.json] [--scenes 20] [--json]  the policy against the textbook driller on held-out scenes
    gea-driller selftest [--json]                                      the twin, the policy and the service checked; exit 0 when right
    gea-driller status [--url http://127.0.0.1:8790]                   what the running program says
    gea-driller config --write C:\\gea-driller\\config.json          the example config to edit
    gea-driller install                                                the systemd unit (Linux) or the start script (Windows)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import NAME, __version__, config as _config

UNIT = """[Unit]
Description=GEA Driller - the directional driller's agent
After=network.target

[Service]
Type=simple
ExecStart={python} -m gea_driller run --config {config}
Restart=always
RestartSec=5
User={user}
WorkingDirectory={datadir}

[Install]
WantedBy=multi-user.target
"""


def selftest(as_json: bool = False, seconds: float = 12.0) -> int:
    """The twin's arithmetic on a fixed scene, the shipped policy against the textbook driller on held-out scenes,
    a short training that learns, and the service run on this machine with the twin as the rig: a decision made,
    answered, the records and the manifest written, the closed loop armed and disarmed by a limit. The same check
    the build runs before the program is published."""
    import numpy as np
    from . import advisor as A, policy as P, service as S, twin as T
    checks = []

    def add(name, ok, detail=''):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    # the twin
    lat = T.Lateral(T.Scene(0)); o = lat.reset()
    add('observation shape', o.shape == (T.OBS_SIZE,) and len(T.OBS_NAMES) == T.OBS_SIZE, f'{o.shape}')
    r1 = T.run_episode(T.Lateral(T.Scene(0)), T.textbook_action); r2 = T.run_episode(T.Lateral(T.Scene(0)), T.textbook_action)
    add('the twin repeats itself for a seed', abs(r1['reward'] - r2['reward']) < 1e-9 and r1['footage_ft'] == r2['footage_ft'], f"{r1['reward']:.2f}")
    lb = T.Lateral(T.Scene(3)); lb.reset(); lh = T.Lateral(T.Scene(3)); lh.reset()
    for _ in range(8):
        lb.step((4, 1, 1, 1, 0)); lh.step((2, 1, 1, 1, 0))
    add('building turns the bit upward against holding', lb.incl > lh.incl + 1.5 and lb.tvd < lh.tvd, f'incl {lh.incl:.1f} -> {lb.incl:.1f}, tvd {lh.tvd:.1f} vs {lb.tvd:.1f}')
    lat = T.Lateral(T.Scene(3)); lat.reset(); b0 = lat.bed; lat.bed = 0.5; lat.step((2, 1, 1, 1, 1))
    add('circulating cleans the hole without footage', lat.bed < 0.5 and lat.footage == 0.0, f'bed {lat.bed:.2f}')
    # the policy
    pol = P.Policy.load(S.SHIPPED_POLICY)
    ev = P.evaluate(P.policy_act_fn(pol), range(10), 'agent'); tb = P.evaluate(T.textbook_action, range(10), 'textbook')
    add('the shipped policy beats the textbook driller on held-out scenes', ev['reward'] > tb['reward'] and ev['in_layer_pct'] > tb['in_layer_pct'],
        f"agent in-layer {ev['in_layer_pct']:.1f}% reward {ev['reward']:.1f}; textbook {tb['in_layer_pct']:.1f}% {tb['reward']:.1f}")
    fresh = P.Policy(seed=5); before = P.evaluate(P.policy_act_fn(fresh), range(10, 14), 'fresh')
    P.train(fresh, seconds=seconds, batch_steps=2048, n_envs=8, scene_seed0=500000)
    after = P.evaluate(P.policy_act_fn(fresh), range(10, 14), 'trained')
    add('a short training raises the return', after['reward'] > before['reward'], f"{before['reward']:.1f} -> {after['reward']:.1f} in {fresh.meta['iterations']} batches")
    # the service with the twin as the rig
    d = tempfile.mkdtemp(prefix='gea-driller-selftest-')
    cfg = json.loads(json.dumps(_config.EXAMPLE)); cfg['output']['dir'] = d; cfg['status']['port'] = 0; cfg['status']['bind'] = '127.0.0.1'
    cfg['rig']['transport'] = 'sim'; cfg['closed_loop']['allowed'] = True; cfg['closed_loop']['stale_s'] = 30
    cfg = _config.check(cfg)
    drl = S.Driller(cfg, policy=pol, twin_seed=11, twin_pace_s=1.0)
    try:
        drl.start()
        t_end = time.monotonic() + 15
        while time.monotonic() < t_end and not drl.advisor.current:
            time.sleep(0.2)
        cur = drl.advisor.current
        add('a decision was made from the feed', cur is not None and 'words' in cur['recommend'] and cur['confidence']['build'] > 0, cur['recommend']['words'] if cur else 'none')
        with urllib.request.urlopen(drl.url + '/live.json', timeout=5) as r:
            live = json.load(r)
        add('the screen answers', live['ok'] and live['current'] is not None and len(live['path']) >= 1 and live.get('twin', {}).get('label') == 'SIMULATION', live['state'])
        req = urllib.request.Request(drl.url + '/api/answer', data=json.dumps({'what': 'accept', 'by': 'selftest'}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as r:
            ans = json.load(r)
        add('the operator answered', ans.get('ok') and drl.advisor.answers['accepted'] == 1 and drl.feed.action == tuple(cur['action']), str(drl.feed.action))
        md = drl.feed.snapshot()['state']['hole_depth']
        req = urllib.request.Request(drl.url + '/api/arm', data=json.dumps({'by': 'selftest', 'to_md': md + 200}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as r:
            armed = json.load(r)
        t_end = time.monotonic() + 30
        while time.monotonic() < t_end and (drl.advisor.armed is not None):
            time.sleep(0.3)
        cmds = [json.loads(l) for l in open(os.path.join(d, 'commands.jsonl'), encoding='utf-8')]
        add('armed for a section, commands kept by the null downlink, disarmed at the section end',
            armed.get('ok') and any(c['event'] == 'command' and c['result']['sent'] is False for c in cmds) and any(c['event'] == 'disarmed' and 'drilled' in c['reason'] for c in cmds),
            f"{sum(1 for c in cmds if c['event'] == 'command')} command(s)")
    finally:
        drl.stop()
    man = json.load(open(os.path.join(d, 'manifest.json'), encoding='utf-8'))
    import hashlib
    hashes_ok = all(hashlib.sha256(open(os.path.join(d, f['file']), 'rb').read()).hexdigest() == f['sha256'] for f in man['files'] if f['sha256'])
    add('the records and the manifest with every hash', {'decisions.jsonl', 'commands.jsonl', 'surveys.csv'} <= {f['file'] for f in man['files']} and hashes_ok, f"{len(man['files'])} files")
    shutil.rmtree(d, ignore_errors=True)
    ok = all(c['ok'] for c in checks)
    if as_json:
        print(json.dumps({'program': NAME, 'version': __version__, 'ok': ok, 'checks': checks}, indent=1))
    else:
        for c in checks:
            print(f"  {'OK  ' if c['ok'] else 'FAIL'} {c['check']}" + (f"  - {c['detail']}" if c['detail'] else ''))
        print(f"{NAME} {__version__} selftest: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='gea-driller', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('run'); p.add_argument('--config', default=_config.DEFAULT_PATH)
    p = sub.add_parser('sim'); p.add_argument('--seed', type=int, default=7); p.add_argument('--pace', type=float, default=3.0); p.add_argument('--port', type=int, default=8790)
    p.add_argument('--dir', default=''); p.add_argument('--closed-loop', action='store_true', help='allow the switch on the screen (the null downlink: nothing is sent)'); p.add_argument('--bind', default='127.0.0.1')
    p = sub.add_parser('train'); p.add_argument('--seconds', type=float, default=600.0); p.add_argument('--out', default='policy.json'); p.add_argument('--resume', default=''); p.add_argument('--seed', type=int, default=0)
    p.add_argument('--batch', type=int, default=8192); p.add_argument('--envs', type=int, default=32); p.add_argument('--lr', type=float, default=3e-4)
    p = sub.add_parser('evaluate'); p.add_argument('--policy', default=''); p.add_argument('--scenes', type=int, default=20); p.add_argument('--json', action='store_true')
    p = sub.add_parser('selftest'); p.add_argument('--json', action='store_true'); p.add_argument('--seconds', type=float, default=12.0)
    p = sub.add_parser('status'); p.add_argument('--url', default='http://127.0.0.1:8790')
    p = sub.add_parser('config'); p.add_argument('--write', required=True)
    p = sub.add_parser('install'); p.add_argument('--config', default=_config.DEFAULT_PATH); p.add_argument('--user', default='gea')
    a = ap.parse_args(argv)
    if a.cmd == 'run':
        from . import service as S
        cfg = _config.load(a.config)
        print(f'{NAME} {__version__}: {cfg["well"]} at {cfg["site"]}; the rig by {cfg["rig"]["transport"]}; the screen on port {cfg["status"]["port"]}; records in {cfg["output"]["dir"]}', flush=True)
        return S.Driller(cfg).run_forever()
    if a.cmd == 'sim':
        from . import service as S
        cfg = json.loads(json.dumps(_config.EXAMPLE)); cfg['rig']['transport'] = 'sim'; cfg['status']['port'] = a.port; cfg['status']['bind'] = a.bind
        cfg['output']['dir'] = a.dir or tempfile.mkdtemp(prefix='gea-driller-sim-'); cfg['closed_loop']['allowed'] = bool(a.closed_loop); cfg['well'] = f'Twin scene {a.seed}'
        cfg['model']['deep_resistivity_tool'] = True
        cfg = _config.check(cfg)
        print(f'{NAME} {__version__}: the twin as the rig (scene {a.seed}, a step every {a.pace} s); the screen at http://{a.bind}:{a.port}/live; records in {cfg["output"]["dir"]}; a STOP file there stops it', flush=True)
        return S.Driller(cfg, twin_seed=a.seed, twin_pace_s=a.pace).run_forever()
    if a.cmd == 'train':
        from . import policy as P
        pol = P.Policy.load(a.resume) if a.resume else P.Policy(seed=a.seed)
        rec = P.train(pol, seconds=a.seconds, batch_steps=a.batch, n_envs=a.envs, lr=a.lr, seed=a.seed, log=lambda s: print(s, flush=True))
        pol.save(a.out)
        with open(os.path.splitext(a.out)[0] + '.training.json', 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1)
        print(f"saved {a.out}: {pol.meta['trained_steps']:,} steps in {pol.meta['iterations']} batches")
        return 0
    if a.cmd == 'evaluate':
        from . import policy as P, service as S, twin as T
        pol = P.Policy.load(a.policy or S.SHIPPED_POLICY)
        ev = P.evaluate(P.policy_act_fn(pol), range(a.scenes), 'agent'); tb = P.evaluate(T.textbook_action, range(a.scenes), 'textbook')
        if a.json:
            print(json.dumps({'agent': {k: v for k, v in ev.items() if k != 'episodes'}, 'textbook': {k: v for k, v in tb.items() if k != 'episodes'}}, indent=1))
        else:
            print(f'SIMULATION - {a.scenes} held-out twin scenes, the same for both:')
            for e in (ev, tb):
                print(f"  {e['label']:<9s} in the layer {e['in_layer_pct']:5.1f}%  sweet spot {e['sweet_pct']:5.1f}%  {e['ft_per_day']:6.0f} ft/day  mean DLS {e['mean_dls']:.2f}  stuck {e['stuck']}  losses {e['losses']}  reward {e['reward']:7.1f}")
        return 0
    if a.cmd == 'selftest':
        return selftest(a.json, a.seconds)
    if a.cmd == 'status':
        with urllib.request.urlopen(a.url.rstrip('/') + '/health', timeout=5) as r:
            print(json.dumps(json.load(r), indent=1))
        return 0
    if a.cmd == 'config':
        print('written:', _config.write_example(a.write)); return 0
    if a.cmd == 'install':
        if os.name == 'nt':
            p = os.path.join(os.path.dirname(a.config) or '.', 'start-gea-driller.cmd')
            os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
            if os.path.isfile(a.config):                              # the example's data folder is the Linux one: beside the config here
                try:
                    cfg = json.load(open(a.config, encoding='utf-8'))
                    if str(cfg.get('output', {}).get('dir', '')).startswith('/var/'):
                        cfg['output']['dir'] = os.path.join(os.path.dirname(os.path.abspath(a.config)), 'data')
                        json.dump(cfg, open(a.config, 'w', encoding='utf-8'), indent=1)
                except (OSError, ValueError):
                    pass
            with open(p, 'w', encoding='utf-8') as f:
                f.write(f'@echo off\r\ntitle GEA Driller\r\n"{sys.executable}" -m gea_driller run --config "{a.config}"\r\n')
            print(f'written: {p} - put a shortcut to it in the Startup folder'); return 0
        unit = UNIT.format(python=sys.executable, config=a.config, user=a.user, datadir=os.path.dirname(a.config))
        with open('/etc/systemd/system/gea-driller.service', 'w', encoding='utf-8') as f:
            f.write(unit)
        subprocess.run(['systemctl', 'daemon-reload']); subprocess.run(['systemctl', 'enable', '--now', 'gea-driller'])
        print('installed and started: gea-driller.service'); return 0
    ap.print_help(); return 2


if __name__ == '__main__':
    sys.exit(main())
