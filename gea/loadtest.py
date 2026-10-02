# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""loadtest - how many patches can this machine carry, and does the page stay
responsive while they run?

Runs N WITS Level 0 simulators in-process, points N supervised patches at
them in a throw-away workspace, and for `seconds` measures: patches that
reach CONNECTED, samples per minute per patch, the supervisor's heartbeat
latency, and - when a service is started too - the time the overview and the
patch-state calls take while everything runs. Prints a table and a verdict;
leaves the throw-away workspace behind only with --keep.

    gea loadtest --patches 8 --seconds 30
    gea loadtest --patches 32 --seconds 60 --interval 0.5 --with-service

The verdict is three plain checks: every patch connected; no patch
DEGRADED/DOWN at the end; the service answered within 1 s at p95. A site
runs this on its own hardware before going live, with the patch count it
intends to carry plus margin.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from typing import List, Optional


def run(patches: int = 8, seconds: float = 30.0, interval_s: float = 1.0, with_service: bool = False, keep: bool = False,
        workspace_path: Optional[str] = None, verbose: bool = True) -> dict:
    from . import wits0 as W0
    from .workspace import Workspace
    from .patches import PatchSupervisor
    tmp = workspace_path or tempfile.mkdtemp(prefix='gea_load_')
    wsp = os.path.join(tmp, 'ws')
    ws = Workspace.create(wsp, 'load test', actor='loadtest')
    frames = int(seconds / interval_s) + 20
    sims = []
    for i in range(patches):
        th, port, stop = W0.simulate_server(frames=frames, interval_s=interval_s, seed=i + 1)
        sims.append((th, port, stop))
    sup = PatchSupervisor(ws)
    svc = None
    if with_service:
        from .service import Service
        svc = Service(wsp, host='127.0.0.1', port=0, scheduler=False).start()
    t0 = time.time()
    for i, (th, port, stop) in enumerate(sims):
        cfg = dict(W0.EXAMPLE_CONFIG, port=port)
        ws.add_well_live(f'load well {i + 1}', 'wits0', _write_cfg(tmp, i, cfg), actor='loadtest')    # a live well brings its patch
    # the live wells each created a patch named after the well; start them all
    names = [p['name'] for p in sup.store.list()]
    for n in names:
        sup.start(n, 'loadtest')
    lat_overview: List[float] = []
    lat_patches: List[float] = []
    connected_at = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        for st in sup.states():
            if st['status'] == 'CONNECTED' and st['name'] not in connected_at:
                connected_at[st['name']] = time.time() - t0
        if svc is not None:
            import urllib.request
            for path, bucket in (('/api/session', lat_overview), ('/', lat_patches)):
                a = time.time()
                try:
                    with urllib.request.urlopen(svc.url.rstrip('/') + path, timeout=5) as r:
                        r.read()
                except Exception:
                    pass
                bucket.append(time.time() - a)
        time.sleep(1.0)
    states = sup.states()
    per = []
    for st in states:
        per.append({'name': st['name'], 'status': st['status'], 'samples_last_min': st.get('samples_last_min'), 'samples_total': st.get('samples_total'),
                    'latency_p95_ms': (round(1000 * st['latency_p95_s']) if st.get('latency_p95_s') is not None else None), 'connected_after_s': round(connected_at.get(st['name'], -1), 1)})
    sup.stop_all()
    for th, port, stop in sims:
        stop.set()
    if svc is not None:
        svc.stop()
    res = {'patches': patches, 'seconds': seconds, 'interval_s': interval_s, 'per_patch': per,
           'connected': sum(1 for p in per if p['connected_after_s'] >= 0), 'healthy_at_end': sum(1 for p in per if p['status'] == 'CONNECTED'),
           'service': ({'n': len(lat_overview), 'session_p50_ms': round(1000 * statistics.median(lat_overview), 1) if lat_overview else None,
                        'session_p95_ms': round(1000 * sorted(lat_overview)[int(0.95 * (len(lat_overview) - 1))], 1) if lat_overview else None,
                        'page_p95_ms': round(1000 * sorted(lat_patches)[int(0.95 * (len(lat_patches) - 1))], 1) if lat_patches else None} if svc is not None else None),
           'workspace': wsp if keep else None}
    checks = [res['connected'] == patches, res['healthy_at_end'] == patches]
    if svc is not None:
        checks.append((res['service']['session_p95_ms'] or 1e9) < 1000)
    res['ok'] = all(checks)
    res['verdict'] = (f"{res['connected']}/{patches} connected, {res['healthy_at_end']}/{patches} healthy at the end"
                      + (f", service p95 {res['service']['session_p95_ms']} ms" if svc is not None else '') + (' - OK' if res['ok'] else ' - NOT OK'))
    if verbose:
        print(f"{'patch':28s} {'status':10s} {'connect s':>9s} {'samples':>8s} {'per min':>8s} {'p95 ms':>7s}")
        for p in per:
            print(f"{p['name']:28s} {p['status']:10s} {p['connected_after_s']:9.1f} {p['samples_total'] or 0:8d} {p['samples_last_min'] or 0:8d} {p['latency_p95_ms'] or 0:7.0f}")
        print('[loadtest]', res['verdict'])
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _write_cfg(tmp: str, i: int, cfg: dict) -> str:
    p = os.path.join(tmp, f'cfg_{i}.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f)
    return p
