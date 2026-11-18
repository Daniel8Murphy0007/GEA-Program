# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""geosteer - the office's geosteering panel: one well's lateral as the directional driller's program on the rig PC
has it, drawn from the record pulled into the well (wells/<well>/driller/: every decision with what the tools read,
where the position estimate put the layer, what was recommended and what the driller answered) and, when asked, from
the rig PC itself as it stands now.

Four pictures share one axis - the distance along the plan - and each carries one measure: the section (TVD: the
pre-drill model's layer, the layer as the position estimate has it with its spread, the hole, and where the last
recommendation points), the gamma (what was read, the up and down gamma where the string has the tool, and what the
model's three values would have a tool read at the estimated position), the plan view (the hole right or left of
the plan line inside its corridor), and how sure the estimate is that the bit is in the layer.

Nothing here steers. The estimate is the driller's program's own, worked out on the rig PC from the gamma; this
module reads it, sets it beside the pre-drill model and draws it. An answer given from the office is posted to the
rig PC under the name of who gave it, marked as the office's, and is a record of what the office said - the closed
loop is armed and disarmed on the rig PC and nowhere else.

GEA imports nothing of the driller's program: the records are JSON lines and a manifest, read as files."""
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import driller_link as DL

TRANSITION_FT = 3.0             # the driller's program sees a boundary over this much either side (its position filter's own figure)
AHEAD_FT = 90.0                 # where a recommendation points: this far along the hole at the build and the turn it asks for
STATIONS_MAX = 600              # the page draws this many decisions at most, the latest ones
LIVE_KEEP_S = 4.0               # the rig PC is asked at most this often for one well, however many windows follow it
LIVE_TIMEOUT_S = 3.0
ANSWERS = ('accept', 'reject')
ANSWER_WORDS = {'accept': 'accepted', 'reject': 'turned down', 'override': 'done otherwise', None: 'not answered'}
NOT_A_MEASUREMENT = [
    "the layer's top and base: they are the position estimate's, worked out on the rig PC from the gamma and the pre-drill model, and a log run after the well is the measurement",
    "hole in the layer: the count here is of decisions at which the estimate put the bit in the layer, and the estimate can be wrong",
    "the gamma's misfit against the estimate: the estimate was fitted to that gamma, so a small misfit is what fitting does and not a test passed",
    "the point a recommendation aims at: it is the hole carried on at the build and the turn asked for, as geometry; the tool, the bit and the rock decide where the hole goes",
    "the rig PC's figures as they stand now: they are read when the page asks and are as old as the page says",
]

_CACHE: Dict[str, Tuple[tuple, tuple]] = {}
_LIVE: Dict[str, Tuple[float, dict]] = {}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) else None


def _r(v, n: int = 2):
    return None if v is None else round(float(v), n)


# -- the record ---------------------------------------------------------------------------------------------------------

def folder(ws, well_id: str) -> str:
    return os.path.join(ws.path, 'wells', well_id, 'driller')


def _load(ws, well_id: str) -> Tuple[List[dict], dict]:
    """The decisions and the manifest pulled into a well, read once for as long as the files are the files they were."""
    d = folder(ws, well_id)
    p_dec = os.path.join(d, 'decisions.jsonl'); p_man = os.path.join(d, 'manifest.json')
    sig = []
    for p in (p_dec, p_man):
        try:
            st = os.stat(p); sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    key = d
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == tuple(sig):
        return hit[1]
    dec: List[dict] = []; man: dict = {}
    if sig[0] is not None:
        with open(p_dec, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    dec.append(r)
    if sig[1] is not None:
        try:
            with open(p_man, encoding='utf-8') as f:
                man = json.load(f)
        except (OSError, ValueError):
            man = {}
    _CACHE[key] = (tuple(sig), (dec, man if isinstance(man, dict) else {}))
    return _CACHE[key][1]


# -- the geometry and the model's gamma -----------------------------------------------------------------------------------

def expected_gamma(g_in: float, g_above: float, g_below: float, d_top: float, d_base: float, tz: float = TRANSITION_FT) -> float:
    """The gamma the model's three values would have a tool read `d_top` ft below the layer's top and `d_base` ft
    above its base: the layer's own inside, the rock's outside, and a straight blend across `tz` ft either side of a
    boundary - the driller's program's own rule, kept here in step with it."""
    if d_top < -tz:
        return float(g_above)
    if d_top < tz:
        return float(g_above + (d_top + tz) / (2 * tz) * (g_in - g_above))
    if d_base < -tz:
        return float(g_below)
    if d_base < tz:
        return float(g_in + (tz - d_base) / (2 * tz) * (g_below - g_in))
    return float(g_in)


def aim_point(vs: float, y: float, tvd: float, inc_deg: float, az_rel_deg: float, build: float, turn: float, ahead_ft: float = AHEAD_FT) -> dict:
    """Where the hole would be `ahead_ft` on, carried at a build and a turn in deg/100 ft from an inclination and an
    azimuth measured from the plan's: the mean angles over the arc, which is what a short arc comes to."""
    inc2 = float(inc_deg) + float(build) * ahead_ft / 100.0
    az2 = float(az_rel_deg) + float(turn) * ahead_ft / 100.0
    i_m = math.radians((float(inc_deg) + inc2) / 2.0); a_m = math.radians((float(az_rel_deg) + az2) / 2.0)
    horiz = ahead_ft * math.sin(i_m)
    return {'vs': float(vs) + horiz * math.cos(a_m), 'y': float(y) + horiz * math.sin(a_m), 'tvd': float(tvd) + ahead_ft * math.cos(i_m),
            'inc': inc2, 'az_rel': az2, 'ahead_ft': float(ahead_ft)}


def _where(e: dict) -> Optional[str]:
    ps = {'in': _num(e.get('p_in')), 'above': _num(e.get('p_above')), 'below': _num(e.get('p_below'))}
    if any(v is None for v in ps.values()):
        return None
    return max(ps, key=lambda k: ps[k])


def station(d: dict, model: dict, i: int = 0) -> Optional[dict]:
    """One decision as the panel draws it; None for a record that does not carry the position (an early version's)."""
    c = d.get('context') if isinstance(d.get('context'), dict) else {}
    e = c.get('estimate') if isinstance(c.get('estimate'), dict) else {}
    vs = _num(c.get('vs', d.get('vs'))); tvd = _num(c.get('tvd', d.get('tvd_est'))); md = _num(d.get('md', c.get('md'))); g = _num(c.get('gamma'))
    top = _num(e.get('top_tvd')); base = _num(e.get('base_tvd'))
    if None in (vs, tvd, md, g, top, base):
        return None
    m_top = _num(c.get('model_top')); m_base = _num(c.get('model_base'))
    gi, ga, gb = (_num(model.get(k)) for k in ('gamma_in', 'gamma_above', 'gamma_below'))
    have_g = None not in (gi, ga, gb)
    rec = d.get('recommend') if isinstance(d.get('recommend'), dict) else {}
    ans = d.get('answer') if isinstance(d.get('answer'), dict) else {}
    inc = _num(c.get('inc', d.get('inc'))); az = _num(c.get('az'))
    return {'i': i, 'utc': d.get('utc'), 'md': md, 'vs': _r(vs, 1), 'y': _r(_num(c.get('y', d.get('y'))) or 0.0, 1), 'tvd': _r(tvd, 1), 'inc': _r(inc), 'az_rel': _r(az),
            'gamma': _r(g, 1), 'gamma_up': _r(_num(c.get('gamma_up')), 1), 'gamma_down': _r(_num(c.get('gamma_down')), 1), 'deep': _r(_num(c.get('deep')), 1),
            'model_top': _r(m_top, 1), 'model_base': _r(m_base, 1), 'top': _r(top, 1), 'base': _r(base, 1), 'sigma': _r(_num(e.get('sigma')), 1), 'dip': _r(_num(e.get('dip_deg')), 2),
            'p_in': _r(_num(e.get('p_in')), 3), 'p_above': _r(_num(e.get('p_above')), 3), 'p_below': _r(_num(e.get('p_below')), 3), 'where': _where(e),
            'expected': _r(expected_gamma(gi, ga, gb, tvd - top, base - tvd), 1) if have_g else None,
            'expected_model': _r(expected_gamma(gi, ga, gb, tvd - m_top, m_base - tvd), 1) if have_g and None not in (m_top, m_base) else None,
            'build': _num(rec.get('build_deg_100ft')), 'turn': _num(rec.get('turn_deg_100ft')), 'aim_deg': _num(rec.get('aim_deg')), 'words': rec.get('words'),
            'answer': ans.get('what') if ans.get('what') in ('accept', 'reject', 'override') else None, 'by': ans.get('by') if ans else None, 'note': ans.get('note') if ans else None,
            'steer': d.get('steer'), 'armed': bool(d.get('armed')), 'corridor': _num(c.get('corridor'))}


def _rms(xs: List[float]) -> Optional[float]:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else None


def summarize(st: List[dict], n_all: int, model: dict) -> dict:
    where = {'in': 0, 'above': 0, 'below': 0}
    for s in st:
        if s['where'] in where:
            where[s['where']] += 1
    judged = sum(where.values())
    answers = {'accept': 0, 'reject': 0, 'override': 0, 'unanswered': 0}
    by: Dict[str, int] = {}
    for s in st:
        answers[s['answer'] if s['answer'] in answers else 'unanswered'] += 1
        if s['by']:
            by[s['by']] = by.get(s['by'], 0) + 1
    steer: Dict[str, int] = {}
    for s in st:
        k = s['steer'] or 'policy'
        steer[k] = steer.get(k, 0) + 1
    last = st[-1] if st else None
    mis_e = _rms([s['gamma'] - s['expected'] for s in st if s['expected'] is not None])
    mis_m = _rms([s['gamma'] - s['expected_model'] for s in st if s['expected_model'] is not None])
    azi = sum(1 for s in st if s['gamma_up'] is not None and s['gamma_down'] is not None)
    return {'decisions': n_all, 'drawn': len(st), 'not_drawn': n_all - len(st), 'md_from': st[0]['md'] if st else None, 'md_to': last['md'] if last else None,
            'where': where, 'in_layer_pct_by_estimate': _r(100.0 * where['in'] / judged, 1) if judged else None,
            'answers': answers, 'answered_by': by, 'decided_by': steer,
            'gamma_misfit_estimate_api': _r(mis_e, 1), 'gamma_misfit_model_api': _r(mis_m, 1),
            'shift_ft': _r(last['top'] - last['model_top'], 1) if last and last['model_top'] is not None else None,
            'thickness_ft': _r(last['base'] - last['top'], 1) if last else None, 'model_thickness_ft': _r(_num(model.get('thickness_ft')), 1),
            'with_up_down_gamma': azi, 'with_deep_resistivity': sum(1 for s in st if s['deep'] is not None),
            'tools': {'azimuthal_gamma_tool': bool(model.get('azimuthal_gamma_tool')), 'deep_resistivity_tool': bool(model.get('deep_resistivity_tool'))}}


def _aim_of(s: dict) -> Optional[dict]:
    if s is None or s.get('build') is None or s.get('inc') is None:
        return None
    a = aim_point(s['vs'], s['y'], s['tvd'], s['inc'], s.get('az_rel') or 0.0, s['build'], s.get('turn') or 0.0)
    out = {k: _r(v, 1) for k, v in a.items()}
    if s.get('top') is not None and s.get('dip') is not None:
        dz = (a['vs'] - s['vs']) * math.tan(math.radians(s['dip']))                    # the estimate's layer carried on at the estimate's dip along the plan
        out['below_top_ft'] = _r(a['tvd'] - (s['top'] + dz), 1); out['above_base_ft'] = _r((s['base'] + dz) - a['tvd'], 1)
    return out


def _lines(su: dict, last: Optional[dict], aim: Optional[dict], rec: dict) -> List[str]:
    out: List[str] = []
    if not su['drawn']:
        return ["No decision of this record carries the bit's position and the layer's: it was written by an early version of the driller's program."]
    w = su['where']
    out.append(f"{su['decisions']} decisions from {su['md_from']:,.0f} to {su['md_to']:,.0f} ft; at {w['in']} of the {sum(w.values())} drawn the position estimate put the bit in the layer, "
               f"at {w['above']} above it and at {w['below']} below - the estimate's count, not a log's.")
    if su['shift_ft'] is not None:
        sh = su['shift_ft']
        out.append(f"At the last decision the estimate has the layer's top {abs(sh):.1f} ft {'deeper' if sh > 0 else 'shallower'} than the pre-drill model has it there"
                   + (f", and the layer {su['thickness_ft']:.1f} ft thick against the model's {su['model_thickness_ft']:.1f}." if su['model_thickness_ft'] is not None else '.'))
    if su['gamma_misfit_model_api'] is not None and su['gamma_misfit_estimate_api'] is not None:
        out.append(f"The gamma read stands {su['gamma_misfit_model_api']:.0f} API (root mean square) from what the pre-drill model's layer would have a tool read along this hole, and "
                   f"{su['gamma_misfit_estimate_api']:.0f} API from the estimate's - the estimate was fitted to this gamma, so the second figure is small by construction; the first says how far the model was from the hole.")
    a = su['answers']
    out.append(f"Answers: {a['accept']} accepted, {a['reject']} turned down, {a['override']} done otherwise, {a['unanswered']} not answered"
               + (f" (by {', '.join(f'{k}: {v}' for k, v in sorted(su['answered_by'].items()))})." if su['answered_by'] else '.'))
    if last is not None and last.get('words'):
        out.append(f"The last recommendation, at {last['md']:,.0f} ft: {last['words']} - {ANSWER_WORDS.get(last['answer'], last['answer'])}"
                   + (f" by {last['by']}" if last.get('by') else '') + '.')
    if aim is not None and aim.get('below_top_ft') is not None:
        inside = aim['below_top_ft'] >= 0 and aim['above_base_ft'] >= 0
        out.append(f"Carried {aim['ahead_ft']:.0f} ft on at what it asks for, the hole would be {abs(aim['below_top_ft']):.1f} ft {'below' if aim['below_top_ft'] >= 0 else 'above'} the layer's top and "
                   f"{abs(aim['above_base_ft']):.1f} ft {'above' if aim['above_base_ft'] >= 0 else 'below'} its base as the estimate has them there: {'inside' if inside else 'outside'} the layer. Geometry only.")
    t = su['tools']
    out.append("The string's tools, as the rig PC's config declares them: " + ('the up and down gamma' if t['azimuthal_gamma_tool'] else 'no up and down gamma')
               + (', the deep resistivity' if t['deep_resistivity_tool'] else ', no deep resistivity') + f"; {su['with_up_down_gamma']} of the decisions drawn carry the up and down readings.")
    if rec.get('pulled_utc'):
        out.append(f"Pulled {rec['pulled_utc']} from {rec.get('source')}; the record is as old as that.")
    return out


def panel(ws, well_id: str) -> dict:
    """One well's panel from the record pulled into it."""
    well = next((w for w in ws.wells() if w['id'] == well_id), None)
    if well is None:
        raise KeyError(f'no well {well_id}')
    rec = DL.last_pull(ws, well_id) or {}
    base = {'well': well_id, 'display': well.get('display', well_id), 'source': rec.get('source'), 'pulled_utc': rec.get('pulled_utc'), 'rig_well': rec.get('rig_well'),
            'driller_version': rec.get('driller_version'), 'from_rig_pc': str(rec.get('source') or '').lower().startswith(('http://', 'https://'))}
    dec, man = _load(ws, well_id)
    if not rec and not dec:
        return {**base, 'status': 'NO RECORD', 'why': "nothing has been pulled from a rig PC into this well: the Drill string tab's GEA Driller card pulls it", 'stations': [], 'lines': []}
    model = {k: v for k, v in ((man.get('model') or rec.get('model') or {}).items()) if k != 'note'}
    if not dec:
        return {**base, 'status': 'NO DECISIONS', 'why': 'the record pulled holds no decision yet: the advisor decides once the feed is live and the hole has moved', 'model': model, 'stations': [], 'lines': []}
    st_all = []
    for i, d in enumerate(dec):
        s = station(d, model, i)
        if s is not None:
            st_all.append(s)
    su = summarize(st_all, len(dec), model)
    last = st_all[-1] if st_all else None
    aim = _aim_of(last) if last else None
    last_dec = dec[-1]
    latest = None
    if last is not None:
        latest = {k: last[k] for k in ('utc', 'md', 'words', 'answer', 'by', 'note', 'steer', 'armed', 'where', 'p_in', 'p_above', 'p_below', 'sigma', 'gamma', 'gamma_up', 'gamma_down')}
        latest['reasons'] = [str(x) for x in (dec[last['i']].get('reasons') or [])][:12]
    stations = st_all[-STATIONS_MAX:]
    return {**base, 'status': 'OK', 'model': model, 'stations': stations, 'summary': su, 'latest': latest, 'aim': aim, 'last_utc': last_dec.get('utc'),
            'policy': rec.get('policy') or man.get('policy'), 'lines': _lines(su, last, aim, rec), 'label': 'SIMULATION' if _is_twin(man, rec) else None}


def _is_twin(man: dict, rec: dict) -> bool:
    return str(rec.get('source') or '').rstrip('/').endswith(f':{DL.SIM_PORT}') and '127.0.0.1' in str(rec.get('source') or '')


def page(ws, well_id: Optional[str] = None) -> dict:
    """The tab: the wells that hold a pulled record, and one well's panel. Reading it writes nothing."""
    wells = DL.wells_with_records(ws)
    rows = [{'well': w['well'], 'display': w['display'], 'pulled_utc': w['pulled_utc'], 'source': w['source'], 'rig_well': w.get('rig_well'),
             'decisions': (w.get('summary') or {}).get('decisions'), 'md_to': (w.get('summary') or {}).get('md_to')} for w in wells]
    ids = [r['well'] for r in rows]
    if well_id is not None and well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    wid = well_id if well_id in ids else (max(rows, key=lambda r: r['pulled_utc'] or '')['well'] if rows else None)
    p = panel(ws, wid) if wid else None
    lines = list(p['lines']) if p and p['status'] == 'OK' else (
        [p['why']] if p else ["No well of this workspace holds a record pulled from a rig PC. On the Drill string tab, the GEA Driller card pulls one from the rig PC's address - or starts the twin as the rig on this computer, to see the panel before there is a rig."])
    return {'generated_utc': _utc(), 'status': p['status'] if p else 'EMPTY', 'wells': rows, 'well': wid, 'panel': p, 'lines': lines, 'not_a_measurement': list(NOT_A_MEASUREMENT),
            'rules': {'ahead_ft': AHEAD_FT, 'transition_ft': TRANSITION_FT, 'stations_max': STATIONS_MAX}}


# -- the rig PC as it stands now ----------------------------------------------------------------------------------------

def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'GEA-Program geosteering panel'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _post_json(url: str, body: dict, timeout: float) -> Tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json', 'User-Agent': 'GEA-Program geosteering panel'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8') or '{}')
        except ValueError:
            return e.code, {}


def _source(ws, well_id: str) -> Tuple[Optional[dict], Optional[str], Optional[dict]]:
    rec = DL.last_pull(ws, well_id)
    if not rec:
        return None, None, {'status': 'NO RECORD', 'why': 'nothing has been pulled into this well, so there is no rig PC to ask'}
    src = str(rec.get('source') or '').strip().rstrip('/')
    if not src.lower().startswith(('http://', 'https://')):
        return rec, None, {'status': 'NO ADDRESS', 'why': "this well's record was pulled from a folder, not from a rig PC's address"}
    return rec, src, None


def _read_live(src: str, rec: dict, timeout: float) -> Tuple[Optional[dict], Optional[dict]]:
    try:
        l = _get_json(src + '/live.json', timeout)
    except Exception as e:                                    # the rig PC is off, the network is down, or it is not the driller's program
        return None, {'status': 'NOT REACHED', 'why': f'{type(e).__name__}: {e}', 'source': src}
    if not isinstance(l, dict) or l.get('program') != 'GEA Driller':
        return None, {'status': 'NOT REACHED', 'why': "what answered at that address is not the driller's program", 'source': src}
    if rec.get('rig_well') and l.get('well') != rec.get('rig_well'):
        return None, {'status': 'ANOTHER WELL', 'why': f"the rig PC is on {l.get('well')!r} now; this well's record was pulled when it was on {rec.get('rig_well')!r}", 'source': src}
    return l, None


def live(ws, well_id: str, timeout: float = LIVE_TIMEOUT_S, fresh: bool = False) -> dict:
    """The rig PC as it stands now, for the well whose record was pulled from it. One GET of its /live.json, kept
    for a few seconds so that several windows following one well ask the rig PC once."""
    if well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    rec, src, stop = _source(ws, well_id)
    if stop is not None:
        return {**stop, 'well': well_id, 'read_utc': _utc()}
    key = f'{ws.path}|{well_id}'
    hit = _LIVE.get(key)
    if not fresh and hit is not None and time.monotonic() - hit[0] < LIVE_KEEP_S:
        return hit[1]
    l, stop = _read_live(src, rec, timeout)
    if stop is not None:
        out = {**stop, 'well': well_id, 'read_utc': _utc()}
        _LIVE[key] = (time.monotonic(), out)
        return out
    adv = l.get('advisor') or {}; rig = l.get('rig') or {}; cur = l.get('current') if isinstance(l.get('current'), dict) else None
    pos = adv.get('position') if isinstance(adv.get('position'), dict) else None
    est = adv.get('estimate') if isinstance(adv.get('estimate'), dict) else None
    current = None; aim = None
    if cur is not None:
        r_ = cur.get('recommend') or {}; c_ = cur.get('context') or {}; a_ = cur.get('answer') if isinstance(cur.get('answer'), dict) else None
        current = {'utc': cur.get('utc'), 'md': _num(cur.get('md')), 'words': r_.get('words'), 'reasons': [str(x) for x in (cur.get('reasons') or [])][:12],
                   'answer': a_.get('what') if a_ else None, 'by': a_.get('by') if a_ else None, 'steer': cur.get('steer'), 'armed': bool(cur.get('armed')),
                   'build': _num(r_.get('build_deg_100ft')), 'turn': _num(r_.get('turn_deg_100ft'))}
        if None not in (_num(c_.get('vs')), _num(c_.get('tvd')), _num(c_.get('inc')), current['build']):
            a = aim_point(c_['vs'], _num(c_.get('y')) or 0.0, c_['tvd'], c_['inc'], _num(c_.get('az')) or 0.0, current['build'], current['turn'] or 0.0)
            aim = {k: _r(v, 1) for k, v in a.items()}
    hist = [x for x in (adv.get('history') or []) if isinstance(x, dict)]
    last = None
    if hist:
        x = hist[-1]; xa = x.get('answer') if isinstance(x.get('answer'), dict) else {}
        last = {'utc': x.get('utc'), 'md': _num(x.get('md')), 'words': (x.get('recommend') or {}).get('words'),
                'answer': xa.get('what') if xa.get('what') in ('accept', 'reject', 'override') else None, 'by': xa.get('by')}
    n_live = int(adv.get('decisions') or 0)
    n_here = len(_load(ws, well_id)[0])
    tw = l.get('twin') if isinstance(l.get('twin'), dict) else None
    out = {'status': 'LIVE', 'well': well_id, 'source': src, 'read_utc': _utc(), 'rig_well': l.get('well'), 'site': l.get('site'), 'driller_version': l.get('version'),
           'ok': bool(l.get('ok')), 'state': l.get('state'), 'feed': l.get('feed_note'),
           'rig': {k: _r(_num(rig.get(k)), 1) for k in ('hole_depth', 'gamma', 'gamma_up', 'gamma_down', 'continuous_inc', 'continuous_azi', 'rop', 'wob', 'rpm', 'flow_in')},
           'position': {k: _r(_num(pos.get(k)), 1) for k in ('vs', 'y', 'tvd')} if pos else None,
           'estimate': {k: _r(_num(est.get(k)), 3 if k.startswith('p_') else 1) for k in ('p_in', 'p_above', 'p_below', 'top_tvd', 'base_tvd', 'sigma')} if est else None,
           'current': current, 'last': last, 'aim': aim, 'armed': bool(adv.get('armed')), 'closed_loop_allowed': bool(adv.get('closed_loop_allowed')),
           'decisions': n_live, 'pulled': n_here, 'since_pull': max(n_live - n_here, 0), 'answers': adv.get('answers'),
           'twin': ({'label': 'SIMULATION', 'seed': tw.get('seed'), 'in_layer_pct': _r(_num(tw.get('in_layer_pct')), 1)} if tw else None)}
    _LIVE[key] = (time.monotonic(), out)
    return out


def answer(ws, well_id: str, actor: str, what: str, utc: str, note: str = '', timeout: float = 6.0) -> dict:
    """The office's answer to the recommendation the rig PC is showing: posted to the rig PC under the name of who
    gave it, marked as the office's. Only the recommendation the page showed (its time is handed back) and only one
    not yet answered; never while the loop is armed - its commands are the answer then."""
    what = str(what or '').strip().lower(); note = str(note or '').strip()[:300]; actor = str(actor or '').strip()
    if what not in ANSWERS:
        raise ValueError("the answer is accept or reject; what was done instead is recorded on the rig PC's own screen, where the driller is")
    if not actor:
        raise ValueError('an answer carries the name of who gave it')
    if well_id not in [w['id'] for w in ws.wells()]:
        raise KeyError(f'no well {well_id}')
    rec, src, stop = _source(ws, well_id)
    if stop is not None:
        raise ValueError(stop['why'])
    l, stop = _read_live(src, rec, timeout)
    if stop is not None:
        raise ValueError(f"the rig PC was not asked: {stop['why']}")
    adv = l.get('advisor') or {}; cur = l.get('current') if isinstance(l.get('current'), dict) else None
    if adv.get('armed'):
        raise ValueError('the closed loop is armed on the rig PC: its commands are the answer, and it is disarmed there')
    if cur is None:
        raise ValueError('the rig PC shows no recommendation to answer')
    if str(cur.get('utc')) != str(utc):
        raise ValueError(f"the rig PC has moved on: the recommendation now is the one of {cur.get('utc')}, at {cur.get('md')} ft - look at it before answering")
    if isinstance(cur.get('answer'), dict) and cur['answer'].get('what'):
        raise ValueError(f"that recommendation is already answered: {ANSWER_WORDS.get(cur['answer'].get('what'), cur['answer'].get('what'))} by {cur['answer'].get('by')}")
    by = f'{actor} (office)'
    code, body = _post_json(src + '/api/answer', {'what': what, 'by': by, 'note': (note + ' ' if note else '') + '[from the office, through GEA]'}, timeout)
    if code != 200 or not body.get('ok'):
        raise ValueError(f"the rig PC did not take the answer: {body.get('error') or code}")
    _LIVE.pop(f'{ws.path}|{well_id}', None)
    detail = {'well': well_id, 'source': src, 'what': what, 'md': cur.get('md'), 'recommendation_utc': cur.get('utc'), 'words': (cur.get('recommend') or {}).get('words'), 'note': note, 'by': by}
    ws.audit(actor, 'geosteer.answer', detail)
    return {'ok': True, **detail}


def pull(ws, well_id: str, actor: str, timeout: float = 15.0) -> dict:
    """The record pulled again from where it was pulled before (the rig PC's address or the folder)."""
    rec = DL.last_pull(ws, well_id)
    if not rec or not rec.get('source'):
        raise ValueError("nothing has been pulled into this well yet: the first pull names the rig PC, on the Drill string tab")
    try:
        out = DL.pull(ws, rec['source'], well_id, actor=actor, timeout=timeout)
    except (OSError, urllib.error.URLError) as e:
        raise ValueError(f"the rig PC was not reached: {type(e).__name__}: {e}")
    _LIVE.pop(f'{ws.path}|{well_id}', None)
    return {'well': well_id, 'pulled_utc': out['pulled_utc'], 'fetched': out['fetched'], 'kept': out['kept'], 'bad': out['bad'], 'decisions': out['summary']['decisions']}


# -- the words and the report ---------------------------------------------------------------------------------------------

COLUMNS = ('utc', 'md', 'vs', 'y', 'tvd', 'inc', 'gamma', 'gamma_up', 'gamma_down', 'deep', 'model_top', 'model_base', 'top', 'base', 'sigma', 'p_in', 'p_above', 'p_below',
           'where', 'expected', 'expected_model', 'build', 'turn', 'aim_deg', 'steer', 'armed', 'answer', 'by', 'note', 'words')
COLUMN_NAMES = {'utc': 'utc', 'md': 'md_ft', 'vs': 'along_plan_ft', 'y': 'right_of_plan_ft', 'tvd': 'tvd_ft', 'inc': 'inclination_deg', 'gamma': 'gamma_api', 'gamma_up': 'gamma_up_api',
                'gamma_down': 'gamma_down_api', 'deep': 'deep_resistivity_distance_ft', 'model_top': 'model_top_tvd_ft', 'model_base': 'model_base_tvd_ft', 'top': 'estimate_top_tvd_ft',
                'base': 'estimate_base_tvd_ft', 'sigma': 'estimate_spread_ft', 'p_in': 'p_in', 'p_above': 'p_above', 'p_below': 'p_below', 'where': 'estimate_puts_the_bit',
                'expected': 'gamma_expected_by_estimate_api', 'expected_model': 'gamma_expected_by_model_api', 'build': 'build_deg_100ft', 'turn': 'turn_deg_100ft', 'aim_deg': 'aim_off_plan_deg',
                'steer': 'decided_by', 'armed': 'armed', 'answer': 'answer', 'by': 'answered_by', 'note': 'note', 'words': 'recommendation'}


def text(p: dict) -> str:
    pn = p.get('panel')
    if not pn:
        return '\n'.join(p['lines'])
    out = [f"Geosteering - {pn['display']}" + (f" (the rig PC's {pn['rig_well']})" if pn.get('rig_well') else '') + (f" - {pn['label']}" if pn.get('label') else '')]
    out += ['  ' + l for l in p['lines']]
    st = pn.get('stations') or []
    if st:
        out.append(f"  {'MD ft':>9s} {'TVD':>8s} {'gamma':>6s} {'up':>5s} {'down':>5s} {'top':>8s} {'base':>8s} {'in':>5s}  {'answer':<14s} recommendation")
        f = lambda v, w, n=0: (f'{v:>{w},.{n}f}' if v is not None else f"{'-':>{w}s}")
        for s in st[-12:]:
            out.append(f"  {f(s['md'], 9)} {f(s['tvd'], 8, 1)} {f(s['gamma'], 6)} {f(s['gamma_up'], 5)} {f(s['gamma_down'], 5)} {f(s['top'], 8, 1)} {f(s['base'], 8, 1)} "
                       f"{f(100 * s['p_in'] if s['p_in'] is not None else None, 4)}%  {ANSWER_WORDS.get(s['answer'], str(s['answer'])):<14s} {s['words'] or ''}")
    return '\n'.join(out)


def write_report(ws, program_version: str, well_id: Optional[str] = None, actor: str = 'system') -> dict:
    p = page(ws, well_id)
    pn = p.get('panel')
    if not pn or pn['status'] != 'OK':
        raise ValueError(p['lines'][0])
    d = ws.dir('reports', 'geosteering', pn['well'])
    with open(os.path.join(d, 'geosteering.json'), 'w', encoding='utf-8') as f:
        json.dump({'program_version': program_version, **json.loads(json.dumps(p, default=str))}, f, indent=1)
    with open(os.path.join(d, 'geosteering.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Geosteering - {pn['display']}\n\nGenerated {p['generated_utc']} by GEA-Program {program_version}." + (f" {pn['label']}." if pn.get('label') else '') + '\n\n'
                + '\n'.join(f'- {l}' for l in p['lines']) + '\n\n```\n' + text(p) + '\n```\n\nWhat this will not call a measurement:\n\n' + '\n'.join(f'- {x}' for x in NOT_A_MEASUREMENT) + '\n')
    with open(os.path.join(d, 'stations.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow([COLUMN_NAMES[c] for c in COLUMNS])
        for s in pn['stations']:
            wr.writerow(['' if s.get(c) is None else s.get(c) for c in COLUMNS])
    files = ['geosteering.json', 'geosteering.md', 'stations.csv']
    ws.audit(actor, 'geosteer.report', {'well': pn['well'], 'files': files, 'decisions': pn['summary']['decisions']},
             inputs=[os.path.join(folder(ws, pn['well']), n) for n in ('decisions.jsonl', 'manifest.json') if os.path.isfile(os.path.join(folder(ws, pn['well']), n))])
    return {'folder': d, 'files': files, 'page': p}


# -- a made record, and the self-test -------------------------------------------------------------------------------------

MADE_MODEL = {'layer_top_tvd_ft': 9000.0, 'thickness_ft': 30.0, 'dip_deg': 0.0, 'gamma_in': 60.0, 'gamma_above': 130.0, 'gamma_below': 115.0, 'azimuthal_gamma_tool': True,
              'deep_resistivity_tool': False, 'plan_azimuth_deg': 90.0, 'corridor_ft': 100.0, 'heel_md_ft': 10000.0}


def made_record(n: int = 40, shift_ft: float = 8.0) -> Tuple[List[dict], dict]:
    """A record whose every figure is known: a flat layer that is really `shift_ft` deeper than the model has it; a
    hole that starts mid-layer by the model (so near the real top), climbs out through the top and is brought back
    in. The 'estimate' written here is the truth with a 3 ft spread - a made record's privilege."""
    m = MADE_MODEL; top = m['layer_top_tvd_ft'] + shift_ft; base = top + m['thickness_ft']
    dec = []; tvd = m['layer_top_tvd_ft'] + 0.5 * m['thickness_ft'] - 2.0; inc = 91.5; vs = 0.0
    for k in range(n):
        d_top = tvd - top; d_base = base - tvd
        g = expected_gamma(m['gamma_in'], m['gamma_above'], m['gamma_below'], d_top, d_base)
        up = expected_gamma(m['gamma_in'], m['gamma_above'], m['gamma_below'], d_top - 1.5, d_base + 1.5)
        dn = expected_gamma(m['gamma_in'], m['gamma_above'], m['gamma_below'], d_top + 1.5, d_base - 1.5)
        inside = d_top >= 0 and d_base >= 0
        build = -3.0 if d_top < 0.0 else (1.5 if inc < 89.9 and d_top > 5.0 else 0.0)     # a driller who learns he is out when he is out, and levels off once back in
        rec = {'utc': f'2026-01-01T00:{k:02d}:00Z', 'md': m['heel_md_ft'] + 30.0 * k, 'vs': vs, 'y': 0.5 * k, 'tvd_est': tvd, 'inc': inc, 'az': 90.0,
               'recommend': {'steer': 'hold' if build == 0 else ('build' if build > 0 else 'drop'), 'build_deg_100ft': build, 'turn_deg_100ft': 0.0, 'aim_deg': 0.0,
                             'words': ('Hold' if build == 0 else f"{'Build' if build > 0 else 'Drop'} {build:+.1f} deg/100 ft") + '; 25 klb WOB, 120 rpm, 500 gpm.'},
               'reasons': ['a made record: the layer and the hole are known'], 'steer': 'book', 'armed': False,
               'answer': ({'what': 'accept', 'by': 'made'} if k % 5 else ({'what': 'reject', 'by': 'made', 'note': 'held'} if k % 10 else None)),
               'context': {'md': m['heel_md_ft'] + 30.0 * k, 'vs': vs, 'y': 0.5 * k, 'tvd': tvd, 'inc': inc, 'az': 0.0, 'corridor': m['corridor_ft'],
                           'model_top': m['layer_top_tvd_ft'], 'model_base': m['layer_top_tvd_ft'] + m['thickness_ft'], 'gamma': g, 'gamma_up': up, 'gamma_down': dn, 'deep': None,
                           'estimate': {'p_in': 1.0 if inside else 0.0, 'p_above': 1.0 if d_top < 0 else 0.0, 'p_below': 1.0 if d_base < 0 else 0.0, 'd_top': d_top, 'd_base': d_base,
                                        'sigma': 3.0, 'top_tvd': top, 'base_tvd': base, 'dip_deg': 0.0}}}
        dec.append(rec)
        inc2 = inc + build * 0.3
        tvd += 30.0 * math.cos(math.radians((inc + inc2) / 2.0)); vs += 30.0 * math.sin(math.radians((inc + inc2) / 2.0)); inc = inc2
    man = {'program': 'GEA Driller', 'version': 'made', 'site': 'made', 'well': 'made', 'model': dict(m), 'files': []}
    return dec, man


def selftest() -> dict:
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append({'name': name, 'pass': bool(ok)})
    m = MADE_MODEL
    eg = lambda dt, db: expected_gamma(m['gamma_in'], m['gamma_above'], m['gamma_below'], dt, db)
    ck("the model's gamma: the layer's own inside, the rock's above and below, halfway between at a boundary",
       eg(15, 15) == 60.0 and eg(-10, 40) == 130.0 and eg(40, -10) == 115.0 and abs(eg(0, 30) - 95.0) < 1e-9 and abs(eg(30, 0) - 87.5) < 1e-9)
    a = aim_point(1000.0, 5.0, 9000.0, 90.0, 0.0, 0.0, 0.0, 90.0)
    b = aim_point(1000.0, 5.0, 9000.0, 90.0, 0.0, -3.0, 0.0, 90.0)
    c = aim_point(1000.0, 5.0, 9000.0, 90.0, 0.0, 0.0, 2.0, 90.0)
    ck("the aim point: held level it is 90 ft on at the same depth; a 3 deg/100 ft drop over 90 ft takes it 2.1 ft deeper; a 2 deg/100 ft turn takes it 1.4 ft right",
       abs(a['vs'] - 1090.0) < 1e-6 and abs(a['tvd'] - 9000.0) < 1e-6 and abs(b['tvd'] - 9000.0 - 90.0 * math.cos(math.radians(88.65))) < 1e-9 and 2.0 < b['tvd'] - 9000.0 < 2.2
       and abs(c['y'] - 5.0 - 90.0 * math.sin(math.radians(0.9))) < 1e-9 and 1.3 < c['y'] - 5.0 < 1.5)
    dec, man = made_record()
    st = [s for s in (station(d, man['model'], i) for i, d in enumerate(dec)) if s is not None]
    su = summarize(st, len(dec), man['model'])
    ck("a made record: every decision drawn, the layer 8 ft deeper than the model, the estimate's gamma exact and the model's off",
       su['drawn'] == len(dec) == 40 and su['shift_ft'] == 8.0 and su['gamma_misfit_estimate_api'] == 0.0 and su['gamma_misfit_model_api'] > 10.0)
    ck("the hole leaves through the top and comes back: decisions above the layer, none below, and the up and down gamma apart at the top",
       su['where']['above'] >= 1 and su['where']['below'] == 0 and su['where']['in'] >= 20
       and any(s['where'] == 'above' or (s['gamma_up'] - s['gamma_down']) > 20 for s in st))
    ck("the answers are counted as given: accepted, turned down, not answered",
       su['answers'] == {'accept': 32, 'reject': 4, 'override': 0, 'unanswered': 4} and su['answered_by'] == {'made': 36})
    early = station({'md': 1.0, 'recommend': {}, 'context': {}}, man['model'])
    ck("a decision without the position is not drawn, and is counted as not drawn", early is None and summarize(st[:3], 5, man['model'])['not_drawn'] == 2)
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'a made record: a layer and a hole that are known'}
