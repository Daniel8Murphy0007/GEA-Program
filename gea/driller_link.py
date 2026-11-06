# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""driller_link - GEA Driller from the control panel: the agent's records pulled from the rig PC into the well (every
decision, every answer, every command, each file checked against its SHA-256) with a summary the page shows - how
many decisions, how many accepted, rejected or done otherwise, the armed sections and their commands, the hole
drilled under advice - and, before there is a rig, the twin as the rig on this computer: the driller's own program
run here with a scene drilling itself, its screen opened in a window. GEA imports nothing of the driller; it finds
the program by name at run time and runs it as a process of its own, as it does the recorder."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SIM_PORT = 8791
SIM_DIR = 'sim-driller'
RECORD_FILES = ('decisions.jsonl', 'commands.jsonl', 'surveys.csv')


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def program() -> Optional[dict]:
    """Where the driller's program is: installed beside GEA (the kit, or pip), or the checkout's driller/ folder."""
    spec = importlib.util.find_spec('gea_driller')
    if spec is not None and spec.origin:
        here = Path(spec.origin).resolve().parent
        if here.parent.name == 'driller' and (here.parent / 'pyproject.toml').is_file():
            return {'how': 'checkout', 'path': str(here), 'env': str(here.parent)}
        return {'how': 'installed', 'path': str(here), 'env': None}
    src = Path(__file__).resolve().parent.parent / 'driller'
    if (src / 'gea_driller' / '__init__.py').is_file():
        return {'how': 'checkout', 'path': str(src / 'gea_driller'), 'env': str(src)}
    return None


# -- the twin as the rig, on this computer ------------------------------------------------------------------------------

def sim_dir(ws) -> str:
    return os.path.join(ws.path, 'field', SIM_DIR)


def _run_path(ws) -> str:
    return os.path.join(sim_dir(ws), 'run.json')


def health(port: int = SIM_PORT, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/health', timeout=timeout) as r:
            h = json.load(r)
        return h if h.get('program') == 'GEA Driller' else None
    except Exception:
        return None


def status(ws, port: int = SIM_PORT) -> dict:
    h = health(port)
    run = None
    try:
        with open(_run_path(ws), encoding='utf-8') as f:
            run = json.load(f)
    except (OSError, ValueError):
        pass
    prog = program()
    return {'program': prog['how'] if prog else None, 'running': h is not None, 'url': f'http://127.0.0.1:{int(port)}', 'screen': f'http://127.0.0.1:{int(port)}/live',
            'pid': run.get('pid') if run else None, 'started_utc': run.get('started_utc') if run else None, 'seed': run.get('seed') if run else None, 'health': h,
            'folder': os.path.join(sim_dir(ws), 'data'), 'label': 'SIMULATION'}


def start_sim(ws, actor: str = 'system', port: int = SIM_PORT, seed: int = 7, pace_s: float = 3.0, closed_loop: bool = True, popen=None, wait_s: float = 10.0) -> dict:
    from . import launcher as L
    from .workspace import WorkspaceError
    if health(port) is not None:
        st = status(ws, port); st['already_running'] = True
        return st
    prog = program()
    if prog is None:
        raise WorkspaceError("the driller's program (gea-driller) is not installed beside GEA: the kit and the setup carry it; a pip install takes `pip install gea-driller`")
    d = os.path.join(sim_dir(ws), 'data'); os.makedirs(d, exist_ok=True)
    cmd = [sys.executable, '-m', 'gea_driller', 'sim', '--seed', str(int(seed)), '--pace', str(float(pace_s)), '--port', str(int(port)), '--dir', d, '--bind', '127.0.0.1']
    if closed_loop:
        cmd.append('--closed-loop')
    env = dict(os.environ)
    if prog['env']:
        env['PYTHONPATH'] = prog['env'] + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    log = open(os.path.join(sim_dir(ws), 'console.log'), 'ab')
    kw = {'stdin': subprocess.DEVNULL, 'stdout': log, 'stderr': subprocess.STDOUT, 'close_fds': True, 'cwd': L.neutral_cwd(), 'env': env}
    if L.is_windows():
        kw['creationflags'] = L.CREATE_NEW_PROCESS_GROUP | L.DETACHED_PROCESS
    else:
        kw['start_new_session'] = True
    p = (popen or subprocess.Popen)(cmd, **kw)
    pid = int(getattr(p, 'pid', 0) or 0)
    with open(_run_path(ws), 'w', encoding='utf-8') as f:
        json.dump({'pid': pid, 'port': int(port), 'seed': int(seed), 'started_utc': _utc(), 'command': cmd, 'by': actor}, f, indent=1)
    t_end = time.monotonic() + wait_s
    h = None
    while time.monotonic() < t_end and h is None:
        time.sleep(0.25)
        h = health(port, timeout=1.0)
    ws.audit(actor, 'driller.sim_start', {'pid': pid, 'port': int(port), 'seed': int(seed), 'answered': h is not None, 'program': prog['how']})
    st = status(ws, port); st['already_running'] = False; st['command'] = cmd
    return st


def stop_sim(ws, actor: str = 'system', port: int = SIM_PORT, wait_s: float = 10.0) -> dict:
    import signal
    st = status(ws, port)
    pid = st.get('pid')
    if not st['running'] and not pid:
        return st | {'stopped': False, 'note': 'not running'}
    stop_file = os.path.join(sim_dir(ws), 'data', 'STOP')
    try:
        os.makedirs(os.path.dirname(stop_file), exist_ok=True)
        with open(stop_file, 'w', encoding='utf-8') as f:
            f.write('stop requested by GEA\n')
        sent = 'STOP file'
    except OSError as e:
        sent = f'no STOP file ({e})'
    if pid and os.name != 'nt':
        try:
            os.kill(pid, signal.SIGTERM); sent += ' + SIGTERM'
        except (ProcessLookupError, PermissionError, OSError):
            pass
    t_end = time.monotonic() + wait_s
    while time.monotonic() < t_end and health(port, timeout=0.5) is not None:
        time.sleep(0.25)
    gone = health(port, timeout=0.5) is None
    if not gone and pid and os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True); gone = True
    try:
        os.remove(_run_path(ws))
    except OSError:
        pass
    ws.audit(actor, 'driller.sim_stop', {'pid': pid, 'signal': sent, 'stopped': gone})
    return status(ws, port) | {'stopped': gone, 'signal': sent}


def screen_url(address: str) -> str:
    a = str(address or '').strip().rstrip('/')
    if not a:
        raise ValueError("the rig PC's address, as the driller's page shows it (http://192.168.1.60:8790)")
    if not a.lower().startswith(('http://', 'https://')):
        a = 'http://' + a
    if a.count(':') < 2:
        a += ':8790'
    return a + '/live'


# -- the pull -----------------------------------------------------------------------------------------------------------

def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Source:
    def __init__(self, source: str, timeout: float = 30.0):
        self.source = source.strip()
        self.is_url = self.source.lower().startswith(('http://', 'https://'))
        self.timeout = timeout
        if not self.is_url and not os.path.isdir(self.source):
            raise ValueError(f"{self.source} is neither the rig PC's address (http://...) nor a folder")

    def read(self, rel: str) -> bytes:
        if self.is_url:
            req = urllib.request.Request(self.source.rstrip('/') + '/' + urllib.parse.quote(rel), headers={'User-Agent': 'GEA-Program driller pull'})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        with open(os.path.join(self.source, rel), 'rb') as f:
            return f.read()


def summarize(folder: str) -> dict:
    """The record as the page shows it, from the files pulled."""
    dec: List[dict] = []; cmds: List[dict] = []
    p = os.path.join(folder, 'decisions.jsonl')
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            dec = [json.loads(l) for l in f if l.strip()]
    p = os.path.join(folder, 'commands.jsonl')
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            cmds = [json.loads(l) for l in f if l.strip()]
    answers: Dict[str, int] = {'accept': 0, 'reject': 0, 'override': 0, 'unanswered': 0}
    for d in dec:
        a = (d.get('answer') or {}).get('what', 'unanswered')
        answers[a if a in answers else 'unanswered'] += 1
    mds = [d['md'] for d in dec if isinstance(d.get('md'), (int, float))]
    steer = {}
    for d in dec:
        s = (d.get('recommend') or {}).get('steer', '?'); steer[s] = steer.get(s, 0) + 1
    cond = sum(1 for d in dec if (d.get('recommend') or {}).get('conditioning', 'drill') != 'drill')
    sections = [c for c in cmds if c.get('event') == 'armed']
    n_cmd = sum(1 for c in cmds if c.get('event') == 'command')
    sent = sum(1 for c in cmds if c.get('event') == 'command' and (c.get('result') or {}).get('sent'))
    disarms = [c for c in cmds if c.get('event') == 'disarmed']
    by = {}
    for d in dec:
        a = d.get('answer') or {}
        if a.get('by'):
            by[a['by']] = by.get(a['by'], 0) + 1
    return {'decisions': len(dec), 'answers': answers, 'md_from': min(mds) if mds else None, 'md_to': max(mds) if mds else None,
            'hole_under_advice_ft': (max(mds) - min(mds)) if len(mds) >= 2 else 0.0, 'steer': steer, 'conditioning_calls': cond,
            'armed_sections': len(sections), 'commands': n_cmd, 'commands_sent': sent, 'disarmed': [{'utc': c.get('utc'), 'reason': c.get('reason')} for c in disarms][-5:],
            'answered_by': by, 'first_utc': dec[0]['utc'] if dec else None, 'last_utc': dec[-1]['utc'] if dec else None,
            'mean_confidence_steer': (sum(float((d.get('confidence') or {}).get('build', 0)) for d in dec) / len(dec)) if dec else None,
            'last': [{'utc': d['utc'], 'md': d.get('md'), 'words': (d.get('recommend') or {}).get('words'), 'answer': (d.get('answer') or {}).get('what'), 'by': (d.get('answer') or {}).get('by')} for d in dec[-10:]]}


def pull(ws, source: str, well_id: str, actor: str = 'system', timeout: float = 30.0) -> dict:
    """The driller's records into wells/<well>/driller/: the manifest, then every file whose hash is not already
    here, each checked against the manifest's SHA-256 before it is kept; a file that fails is named and not taken."""
    well = next((w for w in ws.wells() if w['id'] == well_id), None)
    if well is None:
        raise ValueError(f'no well {well_id}')
    src = Source(source, timeout)
    man = json.loads(src.read('manifest.json').decode('utf-8'))
    if man.get('program') != 'GEA Driller' or 'files' not in man:
        raise ValueError('this is not a GEA Driller manifest')
    d = ws.dir('wells', well_id, 'driller')
    fetched, kept, bad = [], [], []
    for f in man['files']:
        name = f['file']
        if name not in RECORD_FILES:
            continue
        dst = os.path.join(d, name)
        if os.path.isfile(dst) and f.get('sha256') and _sha256(dst) == f['sha256']:
            kept.append(name); continue
        data = src.read(name)
        got = hashlib.sha256(data).hexdigest()
        if f.get('sha256') and got != f['sha256']:
            bad.append(name); continue
        with open(dst + '.part', 'wb') as fh:
            fh.write(data)
        os.replace(dst + '.part', dst); fetched.append(name)
    with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(man, fh, indent=1)
    summ = summarize(d)
    rec = {'source': source, 'well': well_id, 'site': man.get('site'), 'rig_well': man.get('well'), 'driller_version': man.get('version'), 'pulled_utc': _utc(), 'by': actor,
           'fetched': fetched, 'kept': kept, 'bad': bad, 'summary': summ, 'model': man.get('model')}
    with open(os.path.join(d, 'last_pull.json'), 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1)
    ws.audit(actor, 'driller.pull', {'well': well_id, 'source': source, 'fetched': fetched, 'bad': bad, 'decisions': summ['decisions'], 'commands': summ['commands']},
             inputs=[os.path.join(d, n) for n in fetched + kept])
    return rec


def last_pull(ws, well_id: str) -> Optional[dict]:
    p = os.path.join(ws.path, 'wells', well_id, 'driller', 'last_pull.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def wells_with_records(ws) -> List[dict]:
    out = []
    for w in ws.wells():
        lp = last_pull(ws, w['id'])
        if lp:
            out.append({'well': w['id'], 'display': w.get('display', w['id']), 'pulled_utc': lp['pulled_utc'], 'source': lp['source'], 'summary': lp['summary'], 'rig_well': lp.get('rig_well')})
    return out


# -- shadow mode: a drilled well of this workspace replayed through the advisor ---------------------------------------------

LATERAL_COLUMNS = ('md_ft', 'tvd_ft', 'gamma_api', 'wob_klb', 'rpm', 'flow_gpm', 'rop_ft_hr', 'spp_psi', 'hours')


def lateral_from_edr(ws, well_id: str, out_csv: str) -> dict:
    """The driller's lateral file from a well's drilling-recorder export: one row per foot of hole made while on
    bottom - the first second the hole reached that foot - with the gamma as the median of the seconds since the
    foot before, and the rig's parameters at that second. Written beside the well; the export is read, not copied."""
    import csv as _csv
    from . import edr as E
    well = next((w for w in ws.wells() if w['id'] == well_id), None)
    if well is None:
        raise ValueError(f'no well {well_id}')
    src = next((os.path.join(ws.path, 'wells', well_id, 'source', f) for f in well.get('files', []) if f.lower().endswith('.csv')), None)
    if not src or not E.is_edr(src):
        raise ValueError(f"{well_id} has no drilling-recorder export (an EDR CSV) under its sources")
    st = E.read(src)
    ch = {k: E._ch(st, k) for k in ('hole', 'tvd', 'gamma', 'wob', 'rpm', 'flow', 'rop', 'spp', 'onb')}
    hole = ch['hole']
    if hole is None or ch['tvd'] is None:
        raise ValueError('the export carries no hole depth or no interpolated TVD; the lateral cannot be placed')
    # the depth channel read for what it can be trusted for, as the drilling record reads it: a section that opens on
    # another well's depth, a re-reference, a sidetrack's abandoned hole are left out
    de = E.depth_events(st)
    keep_h, _ = E._keep_masks(st, de)
    secs = st.index
    n = len(hole)
    rows = []
    seen = set(); g_acc = []
    for i in range(n):
        h = hole[i]
        if h is None or (isinstance(h, float) and (h != h)) or not keep_h[i]:
            continue
        if ch['gamma'] is not None and not (ch['gamma'][i] != ch['gamma'][i]):
            g_acc.append(float(ch['gamma'][i]))
        foot = int(h)
        if foot in seen:
            continue
        seen.add(foot)
        tvd = ch['tvd'][i]
        rows.append({'md_ft': float(h), 'tvd_ft': float(tvd) if not (tvd != tvd) else float('nan'),
                     'gamma_api': float(sorted(g_acc)[len(g_acc) // 2]) if g_acc else float('nan'),
                     'wob_klb': _v(ch['wob'], i), 'rpm': _v(ch['rpm'], i), 'flow_gpm': _v(ch['flow'], i), 'rop_ft_hr': _v(ch['rop'], i), 'spp_psi': _v(ch['spp'], i),
                     'hours': float(secs[i]) / 3600.0})
        g_acc = []
    if len(rows) < 100:
        raise ValueError(f'the export yields only {len(rows)} feet of new hole; a lateral is thousands')
    rows.sort(key=lambda r: r['md_ft'])
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', encoding='utf-8', newline='') as f:
        w = _csv.DictWriter(f, fieldnames=list(LATERAL_COLUMNS)); w.writeheader()
        for r in rows:
            w.writerow({c: ('' if (isinstance(r[c], float) and r[c] != r[c]) or r[c] is None else f'{r[c]:.3f}') for c in LATERAL_COLUMNS})
    rows.sort(key=lambda r: r['md_ft'])
    return {'rows': len(rows), 'source': src, 'md_from': rows[0]['md_ft'], 'md_to': rows[-1]['md_ft'], 'gamma_rows': sum(1 for r in rows if r['gamma_api'] == r['gamma_api']),
            'seconds': int(n), 'excluded_rows': int((~keep_h).sum()), 'depth_events': {k: len(v) for k, v in de.items() if isinstance(v, list)}}


def _v(arr, i):
    if arr is None:
        return float('nan')
    x = arr[i]
    return float(x) if not (x != x) else float('nan')


def shadow(ws, well_id: str, actor: str = 'system', model: Optional[dict] = None, timeout_s: float = 1800.0) -> dict:
    """SHADOW MODE on a well of this workspace: the lateral file from its export, the driller's own program run
    on it as a process of its own, the report kept under the well's reports and the summary in the driller folder."""
    from . import __version__
    prog = program()
    if prog is None:
        raise ValueError("the driller's program (gea-driller) is not installed beside GEA: the kit and the setup carry it")
    well = next((w for w in ws.wells() if w['id'] == well_id), None)
    if well is None:
        raise ValueError(f'no well {well_id}')
    d = ws.dir('wells', well_id, 'driller')
    lat_csv = os.path.join(d, 'lateral.csv')
    lat = lateral_from_edr(ws, well_id, lat_csv)
    if model is None and os.path.isfile(os.path.join(d, 'model.json')):       # the site's pre-drill model, kept beside the well
        with open(os.path.join(d, 'model.json'), encoding='utf-8') as f:
            model = json.load(f)
        model = model.get('model', model)
    out = ws.dir('reports', 'wells', well_id, 'driller_shadow')
    cmd = [sys.executable, '-m', 'gea_driller', 'shadow', '--lateral', lat_csv, '--out', out, '--well', str(well.get('display', well_id)), '--site', str(ws.manifest.get('name') or 'site'), '--json']
    if model:
        mp = os.path.join(d, 'model.json')
        if not os.path.isfile(mp):
            with open(mp, 'w', encoding='utf-8') as f:
                json.dump({'model': model}, f, indent=1)
        cmd += ['--model', mp]
    env = dict(os.environ)
    if prog['env']:
        env['PYTHONPATH'] = prog['env'] + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout_s)
    if r.returncode != 0:
        raise RuntimeError(f"the driller's shadow run failed: {(r.stderr or r.stdout).strip()[-600:]}")
    res = json.loads(r.stdout)
    rec = {'well': well_id, 'display': well.get('display', well_id), 'run_utc': res['run_utc'], 'by': actor, 'label': res['label'], 'driller_version': res['version'],
           'lateral': lat, 'model': res['model'], 'model_derived': res['model_derived'], 'summary': res['summary'], 'exits': res['exits'][:50],
           'report': {k: os.path.relpath(v, ws.reports_dir) for k, v in res['paths'].items()}, 'gea_version': __version__}
    with open(os.path.join(d, 'shadow.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    ws.audit(actor, 'driller.shadow', {'well': well_id, 'decisions': res['summary']['decisions'], 'steer_agree_pct': round(res['summary']['steer_agree_pct'], 1),
                                       'model_derived': res['model_derived'], 'label': res['label']}, inputs=[lat['source']])
    return rec


def last_shadow(ws, well_id: str) -> Optional[dict]:
    p = os.path.join(ws.path, 'wells', well_id, 'driller', 'shadow.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def shadow_wells(ws) -> List[dict]:
    """The wells a shadow run can take (a drilling-recorder export among their sources), with the last run if any."""
    from . import edr as E
    out = []
    for w in ws.wells():
        srcs = [os.path.join(ws.path, 'wells', w['id'], 'source', f) for f in w.get('files', []) if f.lower().endswith('.csv')]
        if any(os.path.isfile(p) and E.is_edr(p) for p in srcs):
            out.append({'well': w['id'], 'display': w.get('display', w['id']), 'shadow': last_shadow(ws, w['id'])})
    return out
