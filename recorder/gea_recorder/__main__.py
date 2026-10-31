# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gea-recorder - the black box's few commands, for the person who sets it up once:

    gea-recorder run [--config /etc/gea-recorder/config.json]     record until stopped (what the service runs)
    gea-recorder selftest [--seconds 20] [--json]                  record the test signal into a temporary folder, read every
                                                                   file back, check every sample and the manifest; exit 0 when right
    gea-recorder status [--url http://127.0.0.1:8780]              what the running box says
    gea-recorder config --write /etc/gea-recorder/config.json      the example config to edit
    gea-recorder install                                           write the systemd unit and enable it (as root)
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
Description=GEA Recorder - the field recorder (the black box)
After=network.target chrony.service
Wants=chrony.service

[Service]
Type=simple
ExecStart={python} -m gea_recorder run --config {config}
Restart=always
RestartSec=5
User={user}
WorkingDirectory={datadir}

[Install]
WantedBy=multi-user.target
"""


def selftest(seconds: float = 20.0, as_json: bool = False, keep: str = '') -> int:
    """The test signal recorded free-running for `seconds` of samples into a temporary folder; every file read back
    with this program's own reader; every sample compared to what the source made; the manifest and the hashes
    checked. The same check the build runs before the box is published."""
    import numpy as np
    from . import mseed, service, sources, writer as _writer
    cfg = json.loads(json.dumps(_config.EXAMPLE))
    cfg['adc']['driver'] = 'sim'
    cfg['output']['dir'] = keep or tempfile.mkdtemp(prefix='gea-recorder-selftest-')
    cfg['output']['flush_s'] = 1
    cfg['status']['port'] = 0; cfg['status']['bind'] = '127.0.0.1'
    cfg['clock']['source'] = 'system'
    cfg = _config.check(cfg)
    checks = []

    def add(name, ok, detail=''):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    t0 = time.time()
    h = service.run_for(cfg, seconds, realtime=False, seed=7)
    elapsed = time.time() - t0
    fs = float(cfg['adc']['sample_rate'])
    add('recorded', h['samples'] >= seconds * fs and h['state'] == 'stopped', f"{h['samples']:,} samples per channel in {elapsed:.1f} s")
    man_path = os.path.join(cfg['output']['dir'], 'manifest.json')
    try:
        with open(man_path, encoding='utf-8') as f:
            man = json.load(f)
    except Exception as e:
        man = {'files': []}
        add('manifest', False, str(e))
    else:
        add('manifest', man.get('n_files', 0) == len(cfg['adc']['channels']) and man['n_files'] == len(man['files']) and len(man['sensors']) == len(cfg['adc']['channels']),
            f"{man.get('n_files')} files, {len(man.get('sensors', []))} sensors")
    # the source again, with the same seed, free-running: what every sample should be
    src = sources.SimSource(cfg, realtime=False, seed=7); src.open()
    want = []
    while sum(b.shape[1] for b in want) < h['samples']:
        want.append(src.read()[0])
    want = np.concatenate(want, axis=1)[:, :h['samples']]
    all_ok = True; detail = []
    for j, c in enumerate(cfg['adc']['channels']):
        entries = [e for e in man['files'] if e['sensor_id'] == c['id'] and e['code'] == c['code']]
        if not entries:
            all_ok = False; detail.append(f"{c['id']}: no file"); continue
        e = entries[0]
        p = os.path.join(cfg['output']['dir'], e['file'])
        recs = mseed.read(p)
        data = np.concatenate([r['data'] for r in recs]) if recs else np.zeros(0, dtype=np.int32)
        ratio = int(round(fs / float(c['rate'])))
        exp = want[j]
        if ratio > 1:
            n_full = len(exp) // ratio
            exp = np.round(exp[:n_full * ratio].reshape(n_full, ratio).mean(axis=1)).astype(np.int32)
        exp = exp[:len(data)]
        same = len(data) == e['samples'] and len(data) >= len(exp) - ratio and np.array_equal(data[:len(exp)], exp)
        digest = _writer.sha256_file(p) == e['sha256']
        times_ok = all(abs((recs[k + 1]['starttime'] - recs[k]['starttime']) - len(recs[k]['data']) / float(c['rate'])) < 1e-4 for k in range(len(recs) - 1))
        hdr_ok = all(r['station'] == c['id'][:5] and r['channel'] == c['code'] and r['network'] == cfg['network'] and abs(r['rate'] - float(c['rate'])) < 1e-9 for r in recs)
        ok_c = same and digest and times_ok and hdr_ok and recs and recs[0]['timing_quality'] == 0
        all_ok = all_ok and ok_c
        detail.append(f"{c['id']}.{c['code']}: {len(data):,} samples in {len(recs)} records{'' if ok_c else ' - WRONG'}")
    add('every sample read back as written, records contiguous, headers right, hashes right', all_ok, '; '.join(detail))
    sens = os.path.join(cfg['output']['dir'], 'sensors.csv')
    add('sensors.csv', os.path.isfile(sens) and open(sens, encoding='utf-8').readline().startswith('sensor_id,lat,lon'), sens)
    ok = all(c['ok'] for c in checks)
    if not keep:
        shutil.rmtree(cfg['output']['dir'], ignore_errors=True)
    if as_json:
        print(json.dumps({'ok': ok, 'program': NAME, 'version': __version__, 'checks': checks, 'seconds': seconds}, indent=1))
    else:
        print(f"{NAME} {__version__} self-test: {'OK' if ok else 'FAILED'}")
        for c in checks:
            print(f"  {'ok' if c['ok'] else 'NO'}  {c['check']}: {c['detail']}")
    return 0 if ok else 1


def install(config_path: str, user: str, datadir: str) -> int:
    if os.name != 'posix':
        print('install is for the Pi (systemd)'); return 1
    unit = UNIT.format(python=sys.executable, config=config_path, user=user, datadir=datadir)
    try:
        os.makedirs(datadir, exist_ok=True)
        if not os.path.isfile(config_path):
            _config.write_example(config_path)
            print(f'wrote the example config: {config_path} - edit the channels, the wellhead and the output folder')
        with open('/etc/systemd/system/gea-recorder.service', 'w', encoding='utf-8') as f:
            f.write(unit)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        subprocess.run(['systemctl', 'enable', '--now', 'gea-recorder.service'], check=False)
        print('gea-recorder.service enabled: it starts at boot. Status page: http://<this box>:8780/')
        return 0
    except PermissionError:
        print('install writes /etc/systemd/system: run it with sudo'); return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='gea-recorder', description=f'{NAME} {__version__}: the field recorder, the black box')
    sub = ap.add_subparsers(dest='cmd')
    p_run = sub.add_parser('run', help='record until stopped'); p_run.add_argument('--config', default=_config.DEFAULT_PATH)
    p_st = sub.add_parser('selftest', help='record the test signal and read it back'); p_st.add_argument('--seconds', type=float, default=20.0); p_st.add_argument('--json', action='store_true'); p_st.add_argument('--keep', default='', help='a folder to leave the files in')
    p_s = sub.add_parser('status', help='what the running box says'); p_s.add_argument('--url', default='http://127.0.0.1:8780')
    p_c = sub.add_parser('config', help='the example config'); p_c.add_argument('--write', default='', help='write it here'); p_c.add_argument('--check', default='', help='check this one')
    p_i = sub.add_parser('install', help='the systemd unit, enabled'); p_i.add_argument('--config', default=_config.DEFAULT_PATH); p_i.add_argument('--user', default='pi'); p_i.add_argument('--datadir', default='/var/lib/gea-recorder')
    a = ap.parse_args(argv)
    if a.cmd == 'run':
        from . import service
        cfg = _config.load(a.config)
        print(f'{NAME} {__version__}: {cfg["site"]} / {cfg["station"]}, {len(cfg["adc"]["channels"])} channels at {cfg["adc"]["sample_rate"]:g} samples/s -> {cfg["output"]["dir"]}; status on port {cfg["status"].get("port", 8780)}', flush=True)
        return service.Recorder(cfg).run_forever()
    if a.cmd == 'selftest':
        return selftest(a.seconds, a.json, a.keep)
    if a.cmd == 'status':
        with urllib.request.urlopen(a.url.rstrip('/') + '/health', timeout=5) as r:
            h = json.load(r)
        print(f"{h['program']} {h['version']} · {h['site']} / {h['station']} · {'RECORDING' if h['recording'] else 'NOT RECORDING'}: {h['state']}")
        print(f"  clock: {h['clock']['detail']} (quality {h['clock']['quality']})")
        for c in h['channels']:
            print(f"  {c['sensor_id']:>5} {c['code']} {c['kind']:<12} {c['rate']:g}/s  {c['samples_this_hour']:>10,} this hour  {c['file'] or ''}")
        print(f"  rig: {h['rig']}")
        print(f"  disk: {h['free_mb']:,} MB free; {h['n_files']} files closed")
        return 0
    if a.cmd == 'config':
        if a.write:
            print(_config.write_example(a.write)); return 0
        if a.check:
            try:
                cfg = _config.load(a.check)
            except _config.ConfigError as e:
                print(f'NO: {e}'); return 1
            print(f"ok: {cfg['site']} / {cfg['station']}, {len(cfg['adc']['channels'])} channels"); return 0
        print(json.dumps(_config.EXAMPLE, indent=1)); return 0
    if a.cmd == 'install':
        return install(a.config, a.user, a.datadir)
    ap.print_help(); return 0


if __name__ == '__main__':
    sys.exit(main())
