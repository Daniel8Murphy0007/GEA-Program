"""soak - the patches through link outages, for as long as the site will run them, measured.

The load test says whether the machine carries N patches with the page still answering. The soak says what
happens to those patches when the link goes down and comes back, again and again, over hours: how long each
protocol takes to reconnect, what it loses while the link is down, whether the resume leaves a gap, whether
the page keeps answering through it. The gate renders a Data Resilience report from a synthetic buffer model;
this is the same question asked of the running supervisor, with the numbers it actually produced.

Every patch and the live station connect through a relay on the loopback - a plain TCP forwarder in front of
each simulator. An outage is the relay closing its listener and every connection it carries for `outage_s`
seconds, then listening again; the simulators keep producing meanwhile, as a floor and a station do. The
supervisor's own backoff, reconnection and (for SeedLink) resume by sequence number are what is measured.

What it will not call a measurement: the field link - these are loopback relays, and the outage lengths are
declared, not observed; a protocol's loss - WITS0 and ETP have no replay, so frames sent while the link was
down are gone by the protocol's nature and are counted, never recovered; a soak shorter than the schedule
asked for - the report says how long it ran.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import statistics
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class LinkRelay(threading.Thread):
    """A TCP forwarder on the loopback: clients connect to `port`, bytes flow both ways to `target`. `cut()` is the
    link going down - the listener and every connection close; `restore()` is the link coming back."""

    def __init__(self, target: Tuple[str, int], port: int = 0, host: str = '127.0.0.1'):
        super().__init__(daemon=True)
        self.target, self.host = target, host
        self.port = port
        self.srv: Optional[socket.socket] = None
        self.up = threading.Event()
        self.stop_event = threading.Event()
        self.conns: List[socket.socket] = []
        self.lock = threading.Lock()
        self.stats = {'connections': 0, 'cuts': 0, 'bytes_to_client': 0, 'bytes_to_target': 0}
        self._listen()

    def _listen(self) -> None:
        last: Optional[OSError] = None
        for _ in range(60):                                      # the port just closed may take a moment to be bindable again
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((self.host, self.port))
                break
            except OSError as e:
                last = e
                s.close()
                time.sleep(0.1)
        else:
            raise last or OSError('could not bind the relay port')
        s.listen(16)
        s.settimeout(0.2)
        self.port = s.getsockname()[1]
        self.srv = s
        self.up.set()

    def cut(self) -> int:
        """Close the listener and every carried connection; return how many connections were cut."""
        self.up.clear()
        self.stats['cuts'] += 1
        with self.lock:
            cs = list(self.conns); self.conns.clear()
        for c in cs:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        if self.srv is not None:
            try:
                self.srv.shutdown(socket.SHUT_RDWR)              # a listening socket shut down refuses at once, whatever thread is in accept()
            except OSError:
                pass
            try:
                self.srv.close()
            except OSError:
                pass
            self.srv = None
        return len(cs) // 2

    def restore(self) -> None:
        if not self.up.is_set():
            self._listen()

    def stop(self) -> None:
        self.stop_event.set()
        self.cut()

    def _pump(self, a: socket.socket, b: socket.socket, key: str) -> None:
        try:
            while not self.stop_event.is_set():
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
                self.stats[key] += len(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    def run(self) -> None:
        while not self.stop_event.is_set():
            srv = self.srv
            if srv is None or not self.up.is_set():
                time.sleep(0.1)
                continue
            try:
                c, _ = srv.accept()
            except (socket.timeout, OSError):
                continue
            if not self.up.is_set():                             # accepted in the instant of a cut: the link is down, so is this
                c.close()
                continue
            try:
                t = socket.create_connection(self.target, timeout=5.0)
            except OSError:
                c.close()
                continue
            c.settimeout(None); t.settimeout(None)
            with self.lock:
                self.conns += [c, t]
            self.stats['connections'] += 1
            threading.Thread(target=self._pump, args=(c, t, 'bytes_to_target'), daemon=True).start()
            threading.Thread(target=self._pump, args=(t, c, 'bytes_to_client'), daemon=True).start()


def run(seconds: float = 120.0, patches: int = 2, etp: bool = True, seedlink: bool = True, outage_every_s: float = 40.0, outage_s: float = 8.0,
        interval_s: float = 1.0, with_service: bool = False, keep: bool = False, out_dir: Optional[str] = None, workspace_path: Optional[str] = None,
        reconnect_budget_s: float = 30.0, verbose: bool = True, site_name: str = 'soak') -> dict:
    """The soak: N WITS0 patches (+ an ETP patch, + a SeedLink live station) through link relays, with an outage of
    `outage_s` every `outage_every_s`, for `seconds`. Writes soak.json and the Soak Report into out_dir (or the
    workspace's reports/) and returns the result with its verdict."""
    from . import wits0 as W0
    from . import etp as E
    from . import seedlink as L
    from .workspace import Workspace
    from .patches import PatchSupervisor
    from . import __version__
    tmp = workspace_path or tempfile.mkdtemp(prefix='gea_soak_')
    wsp = os.path.join(tmp, 'ws')
    ws = Workspace.create(wsp, site_name, actor='soak')
    out_dir = out_dir or ws.dir('reports', 'soak')
    os.makedirs(out_dir, exist_ok=True)
    frames = int(seconds / interval_s) + 60
    sims: List[dict] = []
    relays: List[LinkRelay] = []
    t_start = time.time()
    # -- the simulators and their relays ---------------------------------------------------------------------------
    for i in range(patches):
        th, port, stop = W0.simulate_server(frames=frames, interval_s=interval_s, seed=i + 1, repeat=True)
        rl = LinkRelay(('127.0.0.1', port)); rl.start(); relays.append(rl)
        cfgp = os.path.join(tmp, f'wits0_{i}.json')
        with open(cfgp, 'w', encoding='utf-8') as f:
            json.dump(dict(W0.EXAMPLE_CONFIG, port=rl.port), f)
        w = ws.add_well_live(f'soak wits0 {i + 1}', 'wits0', cfgp, actor='soak')
        sims.append({'kind': 'wits0', 'well': w['id'], 'thread': th, 'stop': stop, 'relay': rl, 'stats': th.stats})
    if etp:
        th, port, stop = E.simulate_store(frames=frames, interval_s=interval_s, seed=9)
        rl = LinkRelay(('127.0.0.1', port)); rl.start(); relays.append(rl)
        cfgp = os.path.join(tmp, 'etp.json')
        with open(cfgp, 'w', encoding='utf-8') as f:
            json.dump(dict(E.EXAMPLE_CONFIG, url=f'ws://127.0.0.1:{rl.port}/'), f)
        w = ws.add_well_live('soak etp', 'etp', cfgp, actor='soak')
        sims.append({'kind': 'etp', 'well': w['id'], 'thread': th, 'stop': stop, 'relay': rl, 'stats': th.stats})
    sl_srv = None
    if seedlink:
        sl_srv = L.simulate_server(interval_s=0.25, rate=100.0, seed=3)
        rl = LinkRelay(('127.0.0.1', sl_srv.port)); rl.start(); relays.append(rl)
        cfg = dict(L.EXAMPLE_CONFIG, host='127.0.0.1', port=rl.port, protocol='auto', reconnect_s=[1, 2, 5], keepalive_s=5.0, timeout_s=10.0)
        st = ws.add_live_station('soak station', cfg, lat=31.5, lon=-103.2, actor='soak')
        sims.append({'kind': 'seedlink', 'station': st['id'], 'server': sl_srv, 'relay': rl})
    # -- the supervisor, the live station, the service ---------------------------------------------------------------
    sup = PatchSupervisor(ws)
    for p in sup.store.list():
        sup.start(p['name'], 'soak')
    live = L.LiveStations(ws)
    if seedlink:
        live.start(sims[-1]['station'], 'soak')
    svc = None
    if with_service:
        from .service import Service
        svc = Service(wsp, host='127.0.0.1', port=0, scheduler=False).start()
    # -- the schedule --------------------------------------------------------------------------------------------------
    outages: List[dict] = []
    page_lat: List[float] = []
    page_lat_out: List[float] = []
    deadline = t_start + seconds
    next_out = t_start + outage_every_s
    sample_log: List[dict] = []

    def snapshot() -> dict:
        snap = {s['name']: {'status': s['status'], 'samples': s.get('samples_total') or 0, 'reconnects': s.get('reconnects') or 0, 'p95': s.get('latency_p95_s')} for s in sup.states()}
        if seedlink:
            ls = live.state(sims[-1]['station'])
            snap['station'] = {'status': ls.get('status'), 'samples': ls.get('records') or 0, 'reconnects': ls.get('reconnects') or 0, 'p95': ls.get('latency_p95_s'),
                               'gaps': sum(c.get('gaps', 0) for c in (ls.get('channels') or {}).values())}
        return snap

    def all_connected(snap: dict) -> bool:
        return all(v['status'] == 'CONNECTED' for v in snap.values())

    def page_probe(bucket: List[float]) -> None:
        if svc is None:
            return
        import urllib.request
        a = time.time()
        try:
            with urllib.request.urlopen(svc.url.rstrip('/') + '/api/session', timeout=5) as r:
                r.read()
        except Exception:
            pass
        bucket.append(time.time() - a)

    # wait for the first connection of everything (the budget applies here too)
    t_first = None
    while time.time() < min(deadline, t_start + reconnect_budget_s):
        if all_connected(snapshot()):
            t_first = time.time() - t_start
            break
        time.sleep(0.5)
    if verbose:
        print(f"[soak] {len(sims)} source(s) through link relays; first connection of all after {t_first if t_first is not None else 'NEVER'} s; "
              f"outage {outage_s:g} s every {outage_every_s:g} s for {seconds:g} s", flush=True)
    while time.time() < deadline:
        now = time.time()
        if now >= next_out and now + outage_s + 5 < deadline:
            before = snapshot()
            cut_n = sum(r.cut() for r in relays)
            t_cut = time.time()
            while time.time() < t_cut + outage_s:
                page_probe(page_lat_out)
                time.sleep(1.0)
            during = snapshot()
            for r in relays:
                r.restore()
            t_restore = time.time()
            recon: Dict[str, Optional[float]] = {k: None for k in before}
            while time.time() < t_restore + reconnect_budget_s and any(v is None for v in recon.values()):
                snap = snapshot()
                for k, v in snap.items():
                    if recon[k] is None and v['status'] == 'CONNECTED' and (v['samples'] > during[k]['samples'] or k == 'station' and v['samples'] > during[k]['samples']):
                        recon[k] = round(time.time() - t_restore, 1)
                page_probe(page_lat)
                time.sleep(0.5)
            after = snapshot()
            outages.append({'n': len(outages) + 1, 'cut_utc': datetime.fromtimestamp(t_cut, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'outage_s': round(t_restore - t_cut, 1),
                            'connections_cut': cut_n, 'reconnected_after_s': recon,
                            'down_during': [k for k, v in during.items() if v['status'] != 'CONNECTED'],
                            'all_back': all(v is not None for v in recon.values())})
            if verbose:
                print(f"[soak] outage {len(outages)}: {cut_n} connection(s) cut for {outage_s:g} s; back after {recon}", flush=True)
            next_out = time.time() + outage_every_s
        else:
            page_probe(page_lat)
            sample_log.append({'t': round(time.time() - t_start, 1), **{k: v['samples'] for k, v in snapshot().items()}})
            time.sleep(1.0)
    ran_s = time.time() - t_start
    final = snapshot()
    final_states = {x['well_id']: x for x in sup.states()}
    final_station = live.state(sims[-1]['station']) if seedlink else {}
    # -- stop everything ------------------------------------------------------------------------------------------------
    sup.stop_all()
    if seedlink:
        live.stop_all()
    for s in sims:
        if s['kind'] == 'seedlink':
            s['server'].stop()
        else:
            s['stop'].set()
    for r in relays:
        r.stop()
    if svc is not None:
        svc.stop()
    # -- the numbers ----------------------------------------------------------------------------------------------------
    per = []
    for s in sims:
        if s['kind'] == 'seedlink':
            ls = final_station
            srv = s['server']
            sent = sum(st_.records_served for st_ in srv.stations)
            per.append({'source': s['station'], 'protocol': 'seedlink', 'status': ls.get('status'), 'received': ls.get('records') or 0, 'sent_while_connected': sent,
                        'produced': srv.stations[0].seq, 'lost': 0 if sum(c.get('gaps', 0) for c in (ls.get('channels') or {}).values()) == 0 else None,
                        'gaps': sum(c.get('gaps', 0) for c in (ls.get('channels') or {}).values()), 'reconnects': ls.get('reconnects') or 0,
                        'latency_p95_s': ls.get('latency_p95_s'), 'resume': 'by sequence number: the records the link missed were served on reconnection',
                        'relay': dict(s['relay'].stats)})
        else:
            st_ = final_states.get(s['well'], {})
            produced = s['stats'].get('frames_sent', 0)
            received = st_.get('samples_total') or 0
            n_tags = 2 if s['kind'] == 'etp' else max(1, round(received / max(produced, 1)))
            per.append({'source': s['well'], 'protocol': s['kind'], 'status': st_.get('status'), 'received': received, 'sent_while_connected': produced,
                        'produced': produced + s['stats'].get('frames_unsent', 0), 'lost': s['stats'].get('frames_unsent', 0) if s['kind'] == 'wits0' else None,
                        'gaps': None, 'reconnects': st_.get('reconnects') or 0, 'latency_p95_s': st_.get('latency_p95_s'),
                        'resume': ('none in the protocol: frames sent while the link was down are gone (lost = seconds with no client listening, at one frame per second)' if s['kind'] == 'wits0' else
                                  'none in this store: a new session starts at the store\'s current sample'), 'relay': dict(s['relay'].stats), 'tags_per_frame': n_tags})
    p95 = lambda a: (round(1000 * sorted(a)[int(0.95 * (len(a) - 1))], 1) if a else None)
    checks = {
        'all_connected_at_start': t_first is not None,
        'all_connected_at_end': all_connected(final),
        'every_outage_recovered': bool(outages) and all(o['all_back'] for o in outages),
        'seedlink_no_gap': (not seedlink) or all(p['gaps'] == 0 for p in per if p['protocol'] == 'seedlink'),
        'page_answered_through_outages': (svc is None) or ((p95(page_lat) or 1e9) < 1000 and (p95(page_lat_out) or 0) < 1000),
    }
    res = {'protocol': 'gea.soak/1', 'generated_utc': _utc(), 'program_version': __version__, 'site': site_name,
           'config': {'seconds_asked': seconds, 'seconds_ran': round(ran_s, 1), 'patches_wits0': patches, 'etp': etp, 'seedlink': seedlink, 'interval_s': interval_s,
                      'outage_every_s': outage_every_s, 'outage_s': outage_s, 'reconnect_budget_s': reconnect_budget_s, 'with_service': with_service},
           'first_connection_s': t_first, 'outages': outages, 'per_source': per,
           'service': ({'probes': len(page_lat), 'p95_ms': p95(page_lat), 'probes_during_outage': len(page_lat_out), 'p95_ms_during_outage': p95(page_lat_out)} if svc is not None else None),
           'checks': checks, 'ok': all(checks.values()), 'samples_timeline': sample_log[-600:], 'workspace': wsp if keep else None}
    worst = max((max((v for v in o['reconnected_after_s'].values() if v is not None), default=0.0) for o in outages), default=None)
    res['verdict'] = (f"{len(outages)} outage(s) of {outage_s:g} s over {ran_s / 60:.1f} min: "
                      + (f"every source back within {worst} s" if checks['every_outage_recovered'] else 'a source did NOT come back within the budget')
                      + ('; SeedLink resumed with no gap' if seedlink and checks['seedlink_no_gap'] else ('; SeedLink shows a GAP' if seedlink else ''))
                      + (f"; page p95 {res['service']['p95_ms']} ms (during outages {res['service']['p95_ms_during_outage']} ms)" if svc is not None else '')
                      + (' - OK' if res['ok'] else ' - NOT OK'))
    with open(os.path.join(out_dir, 'soak.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1, default=str)
    try:
        from .client_reports import write as _write
        paths = _write(soak_report(res), out_dir, 'soak_report')
        res['report'] = paths['html']
    except Exception as e:                                   # the numbers are on disk already; the report's failure is said, not hidden
        res['report_error'] = str(e)
    if verbose:
        print(f"{'source':22s} {'protocol':9s} {'status':10s} {'received':>8s} {'produced':>8s} {'lost':>6s} {'recon':>5s} {'p95 s':>6s}")
        for p in per:
            print(f"{p['source']:22s} {p['protocol']:9s} {str(p['status']):10s} {p['received']:8d} {p['produced']:8d} {str(p['lost'] if p['lost'] is not None else '-'):>6s} {p['reconnects']:5d} {str(p['latency_p95_s']):>6s}")
        print('[soak]', res['verdict'])
        if res.get('report'):
            print('  report:', res['report'])
    if not keep and workspace_path is None:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def soak_report(res: dict):
    """The Soak Report: what was run, every outage and who came back when, every source's count, the verdict."""
    from .client_reports import Document, Section, Table, PROGRAM_NAME
    now = res['generated_utc']
    cfg = res['config']
    report_id = f"SOAK-{now.replace('-', '').replace(':', '')}"
    secs = [Section('1', 'Summary', [
        f"{cfg['patches_wits0']} WITS0 patch(es){', one ETP patch' if cfg['etp'] else ''}{', one SeedLink live station' if cfg['seedlink'] else ''} ran for "
        f"{cfg['seconds_ran'] / 60:.1f} min (asked: {cfg['seconds_asked'] / 60:.1f} min) through link relays, with the link cut for {cfg['outage_s']:g} s every "
        f"{cfg['outage_every_s']:g} s - {len(res['outages'])} outage(s). " + res['verdict'] + '.',
        'Every connection went through a relay on this machine; an outage is the relay closing its listener and every connection for the declared time, then '
        'listening again, while the simulators kept producing. What is measured is the supervisor\'s own reconnection, backoff and resume.']),
        Section('2', 'Outages and recovery', ['For each outage: when the link was cut, for how long, how many connections it carried, which sources were down '
                                               'during it, and how many seconds after the link came back each source was CONNECTED and receiving again.'],
                [Table(['#', 'Cut (UTC)', 'Outage s', 'Connections cut', 'Down during', 'Back after (s) per source'],
                       [[o['n'], o['cut_utc'], o['outage_s'], o['connections_cut'], ', '.join(o['down_during']) or 'none',
                         ', '.join(f"{k}: {v if v is not None else 'NOT BACK'}" for k, v in o['reconnected_after_s'].items())] for o in res['outages']])]),
        Section('3', 'Per source', ['Received is what the supervisor filed; produced is what the simulator made; lost is the protocol\'s own loss while the link '
                                     'was down (WITS0 has no replay; the ETP store starts a new session at its current sample; SeedLink resumes by sequence number, '
                                     'so a gap count of zero is the proof). A WITS0 frame carries several samples, so received counts samples and produced counts frames; the '
                                     'latency p95 includes the records a resume delivered late, which is the cost of an outage, not of the link.'],
                [Table(['Source', 'Protocol', 'Status at end', 'Received (samples / records)', 'Produced (frames / records)', 'Lost (frames)', 'Gaps', 'Reconnection attempts', 'Latency p95 s', 'Resume'],
                       [[p['source'], p['protocol'], p['status'], p['received'], p['produced'], p['lost'] if p['lost'] is not None else '-', p['gaps'] if p['gaps'] is not None else '-',
                         p['reconnects'], p['latency_p95_s'] if p['latency_p95_s'] is not None else '-', p['resume']] for p in res['per_source']])]),
    ]
    if res.get('service'):
        s = res['service']
        secs.append(Section('4', 'The page through the outages', [f"{s['probes']} probes of the service outside outages, p95 {s['p95_ms']} ms; {s['probes_during_outage']} during outages, "
                                                                 f"p95 {s['p95_ms_during_outage']} ms. The page is served by the same process that runs the patches; it must answer while they reconnect."]))
    secs.append(Section(str(len(secs) + 1), 'Checks', [], [Table(['Check', 'Result'], [[k, 'PASS' if v else 'FAIL'] for k, v in res['checks'].items()])]))
    secs.append(Section(str(len(secs) + 1), 'What this report does not call a measurement', [
        'The field link - these are relays on the loopback, and the outage lengths were declared, not observed.',
        'A protocol\'s loss recovered - WITS0 and ETP frames sent while the link was down are gone by the protocol\'s nature; they are counted, never recovered.',
        'A soak shorter than asked - the summary says how long it ran.',
        'A site\'s real patch count - run it at the number the site will carry.']))
    front = [['Report ID', report_id], ['Site', res['site']], ['Generated (UTC)', now], ['Program', f"{PROGRAM_NAME} build {res['program_version']}"],
             ['Result', 'OK' if res['ok'] else 'NOT OK']]
    return Document(title=f'{PROGRAM_NAME} - Soak Report', report_id=report_id, front=front, sections=secs,
                    footer='The supervisor\'s own numbers through declared link outages on the loopback; nothing modelled.',
                    data={k: v for k, v in res.items() if k not in ('samples_timeline',)} | {'_records': [], '_catalogue_obj': None})
