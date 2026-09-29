"""dashboard — the web view over the client report family (SOW 4.2.12;
4.2.4 real-time dashboards): status tiles, well ranking, alarm wall and
drill-down links, as one self-contained HTML page beside the reports.

The page is generated, not served: every figure on it is read from the
machine JSON the report generators wrote, so the dashboard can never show a
number a report does not carry. Open `index.html`; every tile and every row
links to the report it summarises. Light and dark themes from the same
tokens; status is always an icon plus a label, never colour alone.

`orchestrate()` runs the report family for the wells and files given and then
builds the page; the CLI command `dashboard` wraps it.

Headless-safe: standard library only for the page; the report generators
bring their own dependencies (numpy).
"""

from __future__ import annotations

import html as _html
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

STATUS_ICON = {'good': '&#9679;', 'warning': '&#9650;', 'serious': '&#9632;', 'critical': '&#10006;', 'neutral': '&#9675;'}


def _load(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _rel(base: str, path: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, '/')


# ---------------------------------------------------------------------------
# Collect: read what the report generators wrote
# ---------------------------------------------------------------------------
def collect(out_dir: str) -> dict:
    wells: List[dict] = []
    wells_dir = os.path.join(out_dir, 'wells')
    if os.path.isdir(wells_dir):
        for name in sorted(os.listdir(wells_dir)):
            d = os.path.join(wells_dir, name)
            if not os.path.isdir(d):
                continue
            meta = _load(os.path.join(d, 'well.json')) or {}
            w = {'name': meta.get('display', name), 'slug': name, 'dir': d, 'source': meta.get('source', '')}
            gd = _load(os.path.join(d, 'gauge_drift_report.json'))
            if gd:
                ev = gd.get('evaluation', {})
                st = ev.get('stations', [])
                w['drift'] = {'detected': gd.get('drift_detected'), 'counts': ev.get('classification_counts', {}),
                              'worst': max((s['classification'] for s in st), key=lambda c: _sev(c), default='-'),
                              'bias_max': max((abs(s.get('bias_psi') or 0) for s in st), default=None),
                              'n_stations': len(st), 'report': os.path.join(d, 'gauge_drift_report.html'),
                              'evaluated_at': gd.get('evaluated_at_utc')}
                dq = gd.get('data_quality', {})
                w['quality'] = {'pct_good_min': min((v['pct_good'] for v in dq.values()), default=None),
                                'n_tags': len(dq), 'flagged': sum(sum(c for f, c in v['counts'].items() if f != 'GOOD') for v in dq.values())}
            wt = _load(os.path.join(d, 'well_test_validation.json'))
            if wt:
                det = wt.get('detection', {})
                appr = wt.get('approvals', [])
                approved = {a['test_id'] for a in appr if a.get('decision') == 'APPROVED' and a.get('level') == 2}
                w['well_tests'] = {'accepted': det.get('n_accepted', 0), 'rejected': det.get('n_rejected', 0),
                                   'approved': len({t['test_id'] for t in det.get('tests', [])} & approved),
                                   'report': os.path.join(d, 'well_test_validation.html')}
            al = _load(os.path.join(d, 'alarm_event_report.json'))
            if al:
                k = al.get('kpis', {})
                w['alarms'] = {'active': al.get('active', []), 'n_active': len(al.get('active', [])),
                               'unacked': len(k.get('active_unacknowledged', [])), 'activations': k.get('n_activations', 0),
                               'floods': k.get('flood_10min_bins', 0), 'report': os.path.join(d, 'alarm_event_report.html')}
            dr = _load(os.path.join(d, 'data_resilience_report.json'))
            if dr:
                sim = dr.get('simulation', {})
                gr = sim.get('gap_report', {})
                w['resilience'] = {'lost': sum(v.get('not_delivered', 0) for v in gr.values()),
                                   'dup': sum(v.get('duplicates_in_delivery', 0) for v in gr.values()),
                                   'outages': len(sim.get('outages', [])), 'replayed': sim.get('stats', {}).get('replayed', 0),
                                   'report': os.path.join(d, 'data_resilience_report.html')}
            wells.append(w)
    site = {}
    acc = _load(os.path.join(out_dir, 'accuracy_statement.json'))
    if acc:
        bt = acc.get('backtest', {})
        site['accuracy'] = {'n_ok': bt.get('n_ok', 0), 'bands': bt.get('bands', {}), 'pending': bt.get('n_pending', 0),
                            'report': os.path.join(out_dir, 'accuracy_statement.html')}
    slas = sorted(f for f in os.listdir(out_dir) if f.startswith('sla_report_') and f.endswith('.json')) if os.path.isdir(out_dir) else []
    if slas:
        s = _load(os.path.join(out_dir, slas[-1]))
        site['sla'] = {'month': s['measurement']['month'], 'counts': s['measurement'].get('status_counts', {}),
                       'report': os.path.join(out_dir, slas[-1].replace('.json', '.html'))}
    mc = _load(os.path.join(out_dir, 'model_cards_index.json'))
    if mc:
        site['model_cards'] = {'n': len(mc.get('cards', [])), 'cards': [(c['model_id'], os.path.join(out_dir, f"model_card_{c['model_id']}.html")) for c in mc.get('cards', [])]}
    sb = _load(os.path.join(out_dir, 'sbom.json'))
    if sb:
        site['sbom'] = {'n': sb.get('n_components'), 'path': os.path.join(out_dir, 'sbom.csv')}
    for kind in ('fat', 'sat'):
        p = _load(os.path.join(out_dir, f'{kind}_protocol.json'))
        if p:
            pr = p.get('protocol', {})
            site[kind] = {'result': pr.get('result'), 'steps': pr.get('n_steps'), 'fail': pr.get('n_fail'),
                          'report': os.path.join(out_dir, f'{kind}_protocol.html')}
    return {'wells': wells, 'site': site, 'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


def _sev(cls: str) -> int:
    return {'UNEXPLAINED_TREND': 5, 'UNEXPLAINED_OFFSET': 5, 'CALIBRATION_OFFSET': 4, 'DRIFT_CONSISTENT': 3,
            'TRANSIENTS': 2, 'INSUFFICIENT_DATA': 1, 'IN_FAMILY': 0}.get(cls, 0)


def _drift_status(w: dict) -> tuple:
    d = w.get('drift')
    if not d:
        return 'neutral', 'NOT EVALUATED'
    if d['detected']:
        return 'critical', 'MODEL DRIFT DETECTED'
    if d['worst'] == 'DRIFT_CONSISTENT':
        return 'warning', 'AGING WITHIN ENVELOPE'
    if d['worst'] == 'INSUFFICIENT_DATA':
        return 'neutral', 'PENDING (thin data)'
    return 'good', 'NO DRIFT'


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
_CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--ring:rgba(11,11,11,.10);
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--neutral:#898781;--accent:#2a78d6;--track:#dbe8f7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--accent:#3987e5;--track:#1f2f44}}
:root[data-theme=dark]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--accent:#3987e5;--track:#1f2f44}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.45 Segoe UI,system-ui,Arial,sans-serif;padding:0 16px 40px}
header{max-width:1240px;margin:0 auto;padding:24px 0 8px}h1{font-size:1.35rem;margin:0 0 4px}.sub{color:var(--ink2);font-size:.9rem}
main{max-width:1240px;margin:0 auto}h2{font-size:1.05rem;margin:28px 0 10px;color:var(--ink)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 16px;text-decoration:none;color:inherit;display:block}
.tile:hover{border-color:var(--accent)}.tile .label{font-size:.82rem;color:var(--ink2)}.tile .value{font-size:1.75rem;font-weight:600;margin:2px 0 4px}
.tile.hero .value{font-size:3rem}.status{font-size:.82rem;color:var(--ink2)}.status .ic{font-size:.9rem;margin-right:5px}
.good .ic{color:var(--good)}.warning .ic{color:var(--warning)}.serious .ic{color:var(--serious)}.critical .ic{color:var(--critical)}.neutral .ic{color:var(--neutral)}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--ring);border-radius:10px;overflow:hidden;font-size:.88rem}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}th{color:var(--ink2);font-weight:600;font-size:.8rem}
td.num{font-variant-numeric:tabular-nums;text-align:right}th.num{text-align:right}tr:last-child td{border-bottom:none}
a{color:var(--accent)}.meter{height:6px;background:var(--track);border-radius:3px;overflow:hidden;margin-top:4px}.meter i{display:block;height:100%;background:var(--accent);border-radius:3px}
.small{font-size:.8rem;color:var(--muted)}footer{max-width:1240px;margin:32px auto 0;color:var(--muted);font-size:.8rem}
.links a{margin-right:14px;white-space:nowrap}
"""


def _tile(base: str, label: str, value: str, status: str, status_text: str, href: Optional[str] = None, hero: bool = False) -> str:
    e = _html.escape
    inner = (f'<div class="label">{e(label)}</div><div class="value">{e(value)}</div>'
             f'<div class="status {status}"><span class="ic">{STATUS_ICON.get(status, "")}</span>{e(status_text)}</div>')
    cls = 'tile hero' if hero else 'tile'
    if href:
        return f'<a class="{cls}" href="{e(_rel(base, href))}">{inner}</a>'
    return f'<div class="{cls}">{inner}</div>'


def render(data: dict, out_dir: str, site_name: str = 'site', program_version: str = '') -> str:
    e = _html.escape
    wells = data['wells']
    site = data['site']
    n_drift = sum(1 for w in wells if w.get('drift', {}).get('detected'))
    n_unacked = sum(w.get('alarms', {}).get('unacked', 0) for w in wells)
    n_active = sum(w.get('alarms', {}).get('n_active', 0) for w in wells)
    n_wt_pending = sum(w['well_tests']['accepted'] - w['well_tests']['approved'] for w in wells if w.get('well_tests'))
    q = [w['quality']['pct_good_min'] for w in wells if w.get('quality') and w['quality']['pct_good_min'] is not None]
    lost = sum(w.get('resilience', {}).get('lost', 0) for w in wells if w.get('resilience'))
    dup = sum(w.get('resilience', {}).get('dup', 0) for w in wells if w.get('resilience'))

    tiles = [_tile(out_dir, 'Wells with model drift detected', f'{n_drift} of {len(wells)}',
                   'critical' if n_drift else ('good' if wells else 'neutral'),
                   'ACTION - fallback and investigation' if n_drift else ('NO DRIFT' if wells else 'NO WELL EVALUATED'), hero=True)]
    tiles.append(_tile(out_dir, 'Active alarms (unacknowledged)', f'{n_active} ({n_unacked})',
                       'serious' if n_unacked else ('warning' if n_active else 'good'),
                       'ACKNOWLEDGE' if n_unacked else ('ACTIVE, ACKNOWLEDGED' if n_active else 'NO ACTIVE ALARM')))
    tiles.append(_tile(out_dir, 'Well tests awaiting approval', str(n_wt_pending),
                       'warning' if n_wt_pending else 'good', 'REVIEW' if n_wt_pending else 'ALL APPROVED' if any(w.get('well_tests') for w in wells) else 'NONE DETECTED'))
    if q:
        mn = min(q)
        tiles.append(_tile(out_dir, 'Data quality, worst tag GOOD %', f'{mn:.1f} %', 'good' if mn >= 95 else ('warning' if mn >= 85 else 'serious'),
                           'WITHIN 95 %' if mn >= 95 else 'FLAGGED SAMPLES - see rules that fired'))
    if site.get('accuracy'):
        a = site['accuracy']
        meets = a['bands'].get('MEETS_TARGET', 0)
        tiles.append(_tile(out_dir, 'Accuracy statement, quantities meeting 95 %', f'{meets} of {a["n_ok"]}',
                           'good' if meets == a['n_ok'] else ('warning' if meets else 'serious'),
                           f'{a["bands"].get("NOT_ACCEPTABLE", 0)} NOT ACCEPTABLE, {a["pending"]} pending', a['report']))
    if any(w.get('resilience') for w in wells):
        tiles.append(_tile(out_dir, 'Store-and-forward, samples lost / duplicated', f'{lost} / {dup}',
                           'good' if lost == 0 and dup == 0 else 'critical', 'ALL DELIVERED, NO DUPLICATES' if lost == 0 and dup == 0 else 'LOSS OR DUPLICATION'))
    if site.get('sla'):
        s = site['sla']
        c = s['counts']
        tiles.append(_tile(out_dir, f'SLA {s["month"]}: MET / NOT MET / not measured', f'{c.get("MET", 0)} / {c.get("NOT MET", 0)} / {c.get("NOT MEASURED", 0)}',
                           'good' if not c.get('NOT MET') else 'serious', 'ALL MEASURED LINES MET' if not c.get('NOT MET') else 'LINES NOT MET - see report', s['report']))
    for kind in ('sat', 'fat'):
        if site.get(kind):
            p = site[kind]
            tiles.append(_tile(out_dir, f'{kind.upper()} protocol', f'{p["steps"]} steps, {p["fail"]} fail',
                               'good' if p['result'] == 'ACCEPTED' else 'critical', p['result'], p['report']))
            break
    if site.get('model_cards'):
        tiles.append(_tile(out_dir, 'Model cards', str(site['model_cards']['n']), 'neutral', 'inputs, settings, back-tests, hashes',
                           site['model_cards']['cards'][0][1] if site['model_cards']['cards'] else None))

    # well ranking: by drift severity, then unacked alarms, then quality
    def rank_key(w):
        d = w.get('drift') or {}
        return (-_sev(d.get('worst', '-')), -(w.get('alarms', {}).get('unacked', 0)), (w.get('quality') or {}).get('pct_good_min') or 100.0)
    rows = []
    for w in sorted(wells, key=rank_key):
        st, txt = _drift_status(w)
        d = w.get('drift') or {}
        qd = w.get('quality') or {}
        al = w.get('alarms') or {}
        wt = w.get('well_tests') or {}
        rs = w.get('resilience') or {}
        links = []
        if d.get('report'):
            links.append(f'<a href="{e(_rel(out_dir, d["report"]))}">drift</a>')
        if wt.get('report'):
            links.append(f'<a href="{e(_rel(out_dir, wt["report"]))}">well tests</a>')
        if al.get('report'):
            links.append(f'<a href="{e(_rel(out_dir, al["report"]))}">alarms</a>')
        if rs.get('report'):
            links.append(f'<a href="{e(_rel(out_dir, rs["report"]))}">resilience</a>')
        pg = qd.get('pct_good_min')
        meter = f'<div class="meter"><i style="width:{pg:.0f}%"></i></div>' if pg is not None else ''
        rows.append('<tr>'
                    f'<td>{e(w["name"])}<div class="small">{e(w.get("source", ""))}</div></td>'
                    f'<td><span class="status {st}"><span class="ic">{STATUS_ICON[st]}</span>{e(txt)}</span></td>'
                    f'<td>{e(d.get("worst", "-"))}</td>'
                    f'<td class="num">{("%.1f" % d["bias_max"]) if d.get("bias_max") is not None else "-"}</td>'
                    f'<td class="num">{d.get("n_stations", "-")}</td>'
                    f'<td>{(f"{pg:.1f} %" if pg is not None else "-")}{meter}</td>'
                    f'<td class="num">{al.get("n_active", "-")} / {al.get("unacked", "-")}</td>'
                    f'<td class="num">{wt.get("accepted", "-")} / {wt.get("approved", "-")}</td>'
                    f'<td class="links">{" ".join(links)}</td></tr>')
    ranking = ('<table><tr><th>Well</th><th>Drift status</th><th>Worst classification</th><th class="num">Max bias (psi)</th>'
               '<th class="num">Stations</th><th>Quality (worst tag GOOD %)</th><th class="num">Alarms active / unacked</th>'
               '<th class="num">Well tests accepted / approved</th><th>Reports</th></tr>' + ''.join(rows) + '</table>') if rows else '<p class="small">No well evaluated.</p>'

    # alarm wall
    arows = []
    prio = {'P1': 0, 'P2': 1, 'P3': 2, 'P4': 3, 'P5': 4}
    for w in wells:
        for a in (w.get('alarms') or {}).get('active', []):
            arows.append((prio.get(a['priority'], 9), w['name'], a))
    arows.sort(key=lambda x: (x[0], x[1], x[2]['alarm_id']))
    wall = ('<table><tr><th>Priority</th><th>Well</th><th>Alarm</th><th>Tag</th><th>State</th><th>Active since (UTC)</th><th class="num">Last value</th></tr>'
            + ''.join(f'<tr><td><span class="status {"critical" if a["priority"] in ("P1", "P2") else "warning" if a["priority"] == "P3" else "neutral"}"><span class="ic">{STATUS_ICON["critical" if a["priority"] in ("P1", "P2") else "warning" if a["priority"] == "P3" else "neutral"]}</span>{e(a["priority"])}</span></td>'
                      f'<td>{e(wn)}</td><td>{e(a["alarm_id"])}</td><td>{e(a["tag_id"])}</td><td>{e(a["state"])}</td><td>{e(a["active_since_utc"] or "-")}</td>'
                      f'<td class="num">{a["last_value"] if a["last_value"] is not None else "-"}</td></tr>' for _, wn, a in arows[:200])
            + '</table>') if arows else '<p class="small">No active alarm.</p>'

    # report index
    idx = []
    for w in wells:
        for k in ('drift', 'well_tests', 'alarms', 'resilience'):
            r = (w.get(k) or {}).get('report')
            if r:
                idx.append(f'<li>{e(w["name"])} - <a href="{e(_rel(out_dir, r))}">{k.replace("_", " ")}</a></li>')
    for k in ('accuracy', 'sla', 'sat', 'fat'):
        if site.get(k) and site[k].get('report'):
            idx.append(f'<li><a href="{e(_rel(out_dir, site[k]["report"]))}">{k.upper() if k in ("sla", "sat", "fat") else k}</a></li>')
    for mid, p in (site.get('model_cards') or {}).get('cards', []):
        idx.append(f'<li>model card - <a href="{e(_rel(out_dir, p))}">{e(mid)}</a></li>')
    if site.get('sbom'):
        idx.append(f'<li><a href="{e(_rel(out_dir, site["sbom"]["path"]))}">SBOM (CSV)</a></li>')

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gauge Monitoring Dashboard</title><style>{_CSS}</style></head><body>
<header><h1>Downhole Gauge Monitoring - {e(site_name)}</h1><div class="sub">Generated {e(data['generated_utc'])}{(' - build ' + e(program_version)) if program_version else ''} - every figure on this page is read from a report below; open the report for the numbers behind it.</div></header>
<main>
<h2>Status</h2><div class="tiles">{''.join(tiles)}</div>
<h2>Well ranking</h2>{ranking}
<h2>Alarm wall</h2>{wall}
<h2>Reports</h2><ul>{''.join(idx)}</ul>
</main>
<footer>Status is shown as an icon with a label, never colour alone. Tiles and rows link to the report they summarise; nothing on this page is computed here.</footer>
</body></html>"""
    path = os.path.join(out_dir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(page)
    with open(os.path.join(out_dir, 'dashboard.json'), 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in data.items()}, f, indent=1, default=str)
    return path


# ---------------------------------------------------------------------------
# Orchestrate: run the report family, then the page
# ---------------------------------------------------------------------------
def orchestrate(out_dir: str, catalog_wells: List[dict], file_wells: List[dict], td_ft: float = 10500.0,
                gauge_spec=None, outages: Optional[List[str]] = None, month: Optional[str] = None,
                site_name: str = 'site', criteria_path: Optional[str] = None, monitor_root: Optional[str] = None,
                run_sat: bool = False) -> dict:
    """catalog_wells: [{'entry', 'well', 'md_ft'}]; file_wells: [{'path', 'name'}]."""
    from . import (production_live_stream, ingest, Reconciler, SimulatorConfig, CATALOG, __version__ as _v)
    from .sample_record import TagCatalogue, records_from_stream
    from .client_reports import (gauge_drift_report, well_test_report, alarm_event_report, data_resilience_report,
                                 accuracy_statement_report, model_card_report, monthly_sla_report, fat_sat_report, write)
    from .well_test_validation import WellTestValidator, ApprovalTrail, load_criteria, volve_channel_map
    from .alarm_engine import AlarmEngine, defaults_from_catalogue
    from .store_forward import simulate, parse_outages, BufferConfig
    from .accuracy_statement import library_backtest
    from .model_card import build_cards, card_index
    from .sbom import generate as sbom_generate, write as sbom_write
    from .sla_report import measure
    from .drift_monitor import DriftMonitor
    os.makedirs(out_dir, exist_ok=True)
    made: Dict[str, List[str]] = {}
    cfg = SimulatorConfig(td_ft=td_ft, gauge_spec=gauge_spec) if gauge_spec is not None else SimulatorConfig(td_ft=td_ft)
    for cw in catalog_wells:
        name = f"{cw['entry']}__{cw['well']}".replace('/', '-')
        d = os.path.join(out_dir, 'wells', name)
        os.makedirs(d, exist_ok=True)
        stream, smap = production_live_stream(cw['entry'], cw['well'], cw['md_ft'])
        with open(os.path.join(d, 'well.json'), 'w', encoding='utf-8') as f:
            json.dump({'display': cw['well'], 'source': f"{cw['entry']} (station MD {cw['md_ft']:g} ft, caller-supplied)"}, f)
        ms = None
        if monitor_root:
            mon = DriftMonitor(cfg, os.path.join(monitor_root, name), well_name=cw['well'])
            r = mon.run_scheduled(stream, station_map=smap, force=True)
            ev = r['evaluation']
            ms = mon.status(); ms['history'] = mon.history(); ms['change_log'] = mon.change_log()
        else:
            ev = Reconciler(cfg).reconcile(stream, station_map=smap)
        write(gauge_drift_report(ev, stream, well_name=cw['well'], program_version=_v, gauge_spec=gauge_spec, monitor_status=ms), d)
        made.setdefault(name, []).append('drift')
        crit = load_criteria(criteria_path)
        src = CATALOG[cw['entry']].stream()
        det = WellTestValidator(crit, volve_channel_map(cw['well'])).detect(src)
        trail = ApprovalTrail(os.path.join(d, 'well_test_records'), levels=crit.get('approval_levels'))
        write(well_test_report(det, approvals=trail, well_name=cw['well'], program_version=_v), d, basename='well_test_validation')
        made[name].append('well_tests')
    for fw in file_wells:
        name = fw.get('name') or os.path.splitext(os.path.basename(fw['path']))[0]
        d = os.path.join(out_dir, 'wells', name)
        os.makedirs(d, exist_ok=True)
        stream = ingest(fw['path'])
        with open(os.path.join(d, 'well.json'), 'w', encoding='utf-8') as f:
            json.dump({'display': name, 'source': fw['path']}, f)
        cat = TagCatalogue.from_stream(stream, gauge_spec=gauge_spec)
        ev = Reconciler(cfg).reconcile(stream)
        write(gauge_drift_report(ev, stream, catalogue=cat, well_name=name, program_version=_v, gauge_spec=gauge_spec), d)
        made.setdefault(name, []).append('drift')
        recs = records_from_stream(stream, cat)
        eng = AlarmEngine(defaults_from_catalogue(cat), event_log_path=os.path.join(d, 'alarm_events.jsonl'))
        eng.process(recs)
        write(alarm_event_report(eng, well_name=name, program_version=_v), d, basename='alarm_event_report')
        made[name].append('alarms')
        if outages:
            cad = next((t.cadence_s for t in cat.tags.values() if t.cadence_s), 60.0)
            sim = simulate([r for r in recs if r.tag_id.startswith('P_raw')], parse_outages(outages), BufferConfig(cadence_s=cad))
            write(data_resilience_report(sim, site_name=name, program_version=_v), d, basename='data_resilience_report')
            made[name].append('resilience')
    write(accuracy_statement_report(library_backtest(), program_version=_v), out_dir, basename='accuracy_statement')
    cards = build_cards()
    for c in cards:
        write(model_card_report(c), out_dir, basename=f'model_card_{c.model_id}')
    with open(os.path.join(out_dir, 'model_cards_index.json'), 'w', encoding='utf-8') as f:
        json.dump(card_index(cards), f, indent=1)
    sb = sbom_generate()
    sbom_write(sb, out_dir)
    if month:
        first_file = next((n for n in made if 'resilience' in made[n]), None)
        sfj = os.path.join(out_dir, 'wells', first_file, 'data_resilience_report.json') if first_file else None
        first_alarm = next((os.path.join(out_dir, 'wells', n, 'alarm_events.jsonl') for n in made if 'alarms' in made[n]), None)
        m = measure(month, store_forward_json=sfj, alarm_log=first_alarm,
                    accuracy_json=os.path.join(out_dir, 'accuracy_statement.json'),
                    monitor_log_dir=(os.path.join(monitor_root, next(iter(made))) if monitor_root and made else None))
        write(monthly_sla_report(m, site_name=site_name, program_version=_v, sbom=sb), out_dir, basename=f'sla_report_{month}')
    if run_sat:
        from .fat_sat import run_protocol
        write(fat_sat_report(run_protocol('SAT'), site_name=site_name, program_version=_v), out_dir, basename='sat_protocol')
    data = collect(out_dir)
    page = render(data, out_dir, site_name=site_name, program_version=_v)
    return {'index': page, 'wells': list(made), 'reports': made, 'site_reports': sorted(data['site'].keys())}
