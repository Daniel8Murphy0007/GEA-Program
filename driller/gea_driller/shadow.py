# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""shadow - a drilled lateral replayed through the advisor, decision by decision, against what the directional
driller actually did. The lateral comes as a CSV (one row per foot of hole: measured depth, TVD, the gamma at the
bit, the weight, the speed, the flow, the rate, the standpipe pressure, the hours into the record) - GEA writes it
from a drilling recorder's export, or anyone from their own records. The pre-drill model comes as JSON when the
site has it; otherwise one is read off the lateral's own gamma in hindsight and labelled so. Every 30 ft the
advisor gets what it would have seen on the rig - the readings to that point and nothing past it - and its
recommendation is set beside what was drilled next: the build actually made over the following 90 ft, the weight,
speed and flow actually used, and, where the gamma says the well left the layer, whether the advisor called the
correction at the first reading out, before it or after it. SHADOW MODE: advisory in hindsight; nothing here
touched the well, and agreement with a driller is not a verdict on either of them."""
from __future__ import annotations

import csv
import html as _html
import json
import math
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import __version__, config as _config, twin as T
from .advisor import Advisor, Records
from .policy import Policy
from .rig import Feed

LABEL = 'SHADOW MODE'
COLUMNS = ('md_ft', 'tvd_ft', 'gamma_api', 'wob_klb', 'rpm', 'flow_gpm', 'rop_ft_hr', 'spp_psi', 'hours')


# -- the lateral file --------------------------------------------------------------------------------------------------------

def read_lateral(path: str) -> Dict[str, np.ndarray]:
    """The CSV as arrays, one row per foot of hole (gaps allowed), sorted by MD; a missing value is NaN."""
    cols: Dict[str, List[float]] = {c: [] for c in COLUMNS}
    with open(path, encoding='utf-8', newline='') as f:
        rd = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f'the lateral file needs the columns {", ".join(COLUMNS)}; missing {", ".join(missing)}')
        for row in rd:
            for c in COLUMNS:
                v = (row.get(c) or '').strip()
                try:
                    cols[c].append(float(v) if v not in ('', 'nan', 'NaN') else math.nan)
                except ValueError:
                    cols[c].append(math.nan)
    out = {c: np.array(v, dtype=np.float64) for c, v in cols.items()}
    if len(out['md_ft']) < 100:
        raise ValueError('the lateral file holds fewer than 100 rows; a lateral is thousands of feet')
    order = np.argsort(out['md_ft'], kind='stable')
    return {c: v[order] for c, v in out.items()}


def write_lateral(path: str, rows: List[dict]) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNS)); w.writeheader()
        for r in rows:
            w.writerow({c: ('' if r.get(c) is None or (isinstance(r.get(c), float) and math.isnan(r[c])) else f'{float(r[c]):.3f}') for c in COLUMNS})


# -- the heel and the model ------------------------------------------------------------------------------------------------

def inclination(md: np.ndarray, tvd: np.ndarray, span_ft: float = 30.0) -> np.ndarray:
    """The inclination along the hole from the TVD curve: 90 degrees where the TVD stops changing with MD."""
    inc = np.full(len(md), 90.0)
    for i in range(len(md)):
        j = np.searchsorted(md, md[i] - span_ft)
        if j < i and md[i] - md[j] > 1.0 and not (np.isnan(tvd[i]) or np.isnan(tvd[j])):
            s = (tvd[i] - tvd[j]) / (md[i] - md[j])
            inc[i] = math.degrees(math.acos(max(-1.0, min(1.0, s))))
    return inc


def fill_tvd(md: np.ndarray, tvd: np.ndarray) -> np.ndarray:
    """The TVD with its gaps interpolated along MD (a recorder leaves a cell blank now and then)."""
    t = tvd.copy()
    bad = np.isnan(t)
    if bad.any() and (~bad).sum() >= 2:
        t[bad] = np.interp(md[bad], md[~bad], t[~bad])
    return t


def find_heel(md: np.ndarray, tvd: np.ndarray, inc_deg: float = 85.0, window_ft: float = 120.0) -> int:
    """The first row from which the hole runs within `inc_deg` of horizontal for two windows of `window_ft`: where
    the lateral begins. Read from the TVD made against the MD made, so a sparse record is read the same way."""
    t = fill_tvd(md, tvd)
    real = ~np.isnan(tvd)                                    # a window read on blanks the fill made flat is no window
    n = len(md)
    cos_lim = math.cos(math.radians(inc_deg))

    def flat(i: int, j: int) -> bool:
        return j < n and real[i] and real[j] and md[j] - md[i] >= window_ft * 0.8 and abs(t[j] - t[i]) / (md[j] - md[i]) <= cos_lim
    for i in range(n):
        j = int(np.searchsorted(md, md[i] + window_ft)); k = int(np.searchsorted(md, md[i] + 2 * window_ft))
        if flat(i, j) and flat(j, k):
            return i
    return 0


def derive_model(lat: Dict[str, np.ndarray], heel: int) -> dict:
    """A layer model read off the lateral's own gamma, in hindsight: the layer's gamma is the lower mode, the rock
    outside it the higher; the layer's TVD window is where the low gamma was drilled; the dip is its trend along
    the lateral. Labelled: this is not the pre-drill model, it is what the well itself says afterwards."""
    md, tvd, g = lat['md_ft'][heel:], fill_tvd(lat['md_ft'], lat['tvd_ft'])[heel:], lat['gamma_api'][heel:]
    ok = ~np.isnan(g) & ~np.isnan(tvd)
    if ok.sum() < 50:
        raise ValueError('the lateral carries too little gamma to read a model from; give the model as JSON')
    gv = g[ok]; tv = tvd[ok]; mv = md[ok]
    lo, hi = np.percentile(gv, 20), np.percentile(gv, 80)
    if hi - lo < 15.0:
        # one mode: the well stayed in one rock; the layer is that rock and the rest is unread
        cut = float(np.median(gv)) + 25.0
        inside = gv <= cut
        g_in = float(np.median(gv)); g_out = g_in + 50.0; one_mode = True
    else:
        cut = 0.5 * (lo + hi)
        inside = gv <= cut
        g_in = float(np.median(gv[inside])) if inside.any() else float(lo)
        g_out = float(np.median(gv[~inside])) if (~inside).any() else float(hi)
        one_mode = False
    vs = mv - mv[0]
    if inside.sum() >= 20:
        a, b = np.polyfit(vs[inside], tv[inside], 1)
        resid = tv[inside] - (a * vs[inside] + b)
        top0 = float(b + np.percentile(resid, 5)); base0 = float(b + np.percentile(resid, 95))
        dip = math.degrees(math.atan(a))
    else:
        top0 = float(np.percentile(tv, 5)); base0 = float(np.percentile(tv, 95)); dip = 0.0
    thickness = min(150.0, max(10.0, base0 - top0))                # a lateral's target, not a column
    dip = max(-10.0, min(10.0, dip))
    return {'layer_top_tvd_ft': round(top0, 1), 'thickness_ft': round(thickness, 1), 'dip_deg': round(dip, 2), 'gamma_in': round(g_in), 'gamma_above': round(g_out),
            'gamma_below': round(g_out), 'deep_resistivity_tool': False, 'deep_range_ft': 15.0, 'lateral_length_ft': float(md[-1] - md[0]), 'heel_md_ft': float(md[0]),
            'heel_tvd_ft': float(tvd[0]) if not np.isnan(tvd[0]) else round(top0 + thickness / 2, 1), 'gamma_cut': round(float(cut)),
            'how': 'read off this lateral\'s own gamma in hindsight: the lower mode of the gamma is the layer, the higher the rock outside it; the TVD window of the low gamma and its trend along the lateral give the top, the thickness and the dip. Not the pre-drill model.'
                   + (' The gamma has one mode: the well stayed in one rock, and the rock outside it was never read.' if one_mode else '')}


# -- the replay ------------------------------------------------------------------------------------------------------------

class ShadowFeed(Feed):
    """The rig as it was at one moment of the record: the row at the current MD, and the surveys up to there."""

    def __init__(self, cfg: dict, lat: Dict[str, np.ndarray], heel: int):
        super().__init__(cfg)
        self.lat = lat; self.heel = heel; self.i = heel
        self.inc = inclination(lat['md_ft'], lat['tvd_ft'])
        self.connected = True
        self.set_index(heel)

    def set_index(self, i: int) -> None:
        self.i = int(i)
        l = self.lat
        g = l['gamma_api'][max(self.heel, self.i - 10):self.i + 1]
        g = g[~np.isnan(g)]
        with self.lock:
            self.state.update({'bit_depth': float(l['md_ft'][self.i]), 'hole_depth': float(l['md_ft'][self.i]), 'rop': _f(l['rop_ft_hr'][self.i]), 'wob': _f(l['wob_klb'][self.i]),
                               'rpm': _f(l['rpm'][self.i]), 'flow_in': _f(l['flow_gpm'][self.i]), 'spp': _f(l['spp_psi'][self.i]), 'gamma': float(np.median(g)) if len(g) else None,
                               'deep_res_dist': None, 'vibration': None, 'continuous_inc': float(self.inc[self.i]), 'ecd': None, 'torque': None})
            self.frames += 1; self.last_utc = time.time()
        # the surveys: a station every 90 ft of the hole drilled so far
        md_now = l['md_ft'][self.i]
        st = self.heel_md() + 90.0 * (len(self.surveys))
        while st <= md_now:
            j = int(np.searchsorted(l['md_ft'], st))
            if j >= len(l['md_ft']):
                break
            self.add_survey(float(l['md_ft'][j]), float(self.inc[j]), 0.0, 'record')
            st += 90.0

    def heel_md(self) -> float:
        return float(self.lat['md_ft'][self.heel])

    def _run(self) -> None:
        pass


def _f(x) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def actual_build(md: np.ndarray, inc: np.ndarray, i: int, ahead_ft: float = 90.0) -> Optional[float]:
    """The build or drop the driller actually made over the next `ahead_ft`, in degrees per 100 ft."""
    j = int(np.searchsorted(md, md[i] + ahead_ft))
    if j >= len(md) or md[j] - md[i] < ahead_ft * 0.6:
        return None
    return float((inc[j] - inc[i]) / (md[j] - md[i]) * 100.0)


def _mean_ahead(arr: np.ndarray, md: np.ndarray, i: int, ahead_ft: float = 30.0) -> Optional[float]:
    j = int(np.searchsorted(md, md[i] + ahead_ft))
    seg = arr[i:max(j, i + 1)]
    seg = seg[~np.isnan(seg)]
    return float(seg.mean()) if len(seg) else None


def run(lateral_csv: str, policy: Policy, model: Optional[dict] = None, out_dir: Optional[str] = None, well: str = '', site: str = '',
        every_ft: float = 30.0, limits: Optional[dict] = None) -> dict:
    """The replay: the advisor's decisions along the lateral against the record's own next steps; the result as
    a dict, and the report files when out_dir is given."""
    lat = read_lateral(lateral_csv)
    heel = find_heel(lat['md_ft'], lat['tvd_ft'])
    derived = model is None
    model = dict(model) if model else derive_model(lat, heel)
    cfg = json.loads(json.dumps(_config.EXAMPLE))
    cfg['site'] = site or cfg['site']; cfg['well'] = well or cfg['well']
    cfg['rig']['transport'] = 'sim'; cfg['status']['port'] = 0; cfg['status']['bind'] = '127.0.0.1'
    cfg['output']['dir'] = out_dir or os.path.join(os.path.dirname(os.path.abspath(lateral_csv)), 'shadow')
    cfg['model'].update({k: v for k, v in model.items() if k in cfg['model'] or k in ('heel_md_ft', 'heel_tvd_ft')})
    if limits:
        cfg['limits'].update(limits)
    cfg['closed_loop']['allowed'] = False
    cfg = _config.check(cfg)
    os.makedirs(cfg['output']['dir'], exist_ok=True)
    feed = ShadowFeed(cfg, lat, heel)
    records = Records(cfg)
    adv = Advisor(cfg, policy, feed, records)
    md, tvd, g = lat['md_ft'], lat['tvd_ft'], lat['gamma_api']
    inc = feed.inc
    g_cut = float(model.get('gamma_cut') or 0.5 * (float(model['gamma_in']) + 0.5 * (float(model['gamma_above']) + float(model['gamma_below']))))
    decisions: List[dict] = []
    i = heel
    n = len(md)
    next_md = md[heel]
    while i < n:
        feed.set_index(i)
        snap = feed.snapshot()
        rec = adv.decide(snap)
        if rec is None:
            i += 1; continue
        a_build = actual_build(md, inc, i)
        a_wob = _mean_ahead(lat['wob_klb'], md, i); a_rpm = _mean_ahead(lat['rpm'], md, i); a_flow = _mean_ahead(lat['flow_gpm'], md, i); a_rop = _mean_ahead(lat['rop_ft_hr'], md, i)
        gw = g[max(heel, i - 10):i + 1]; gw = gw[~np.isnan(gw)]
        g_now = float(np.median(gw)) if len(gw) else None
        out_now = (g_now is not None and g_now > g_cut)
        r = rec['recommend']
        d = {'md': float(md[i]), 'tvd': float(tvd[i]) if not np.isnan(tvd[i]) else None, 'inc': float(inc[i]), 'gamma': g_now, 'out_by_gamma': out_now,
             'recommended': {'build': r['build_deg_100ft'], 'steer': r['steer'], 'wob': r['wob_klb'], 'rpm': r['rpm'], 'flow': r['flow_gpm'], 'conditioning': r['conditioning'], 'words': r['words']},
             'actual': {'build': a_build, 'wob': a_wob, 'rpm': a_rpm, 'flow': a_flow, 'rop': a_rop},
             'estimate': {k: rec['context']['estimate'][k] for k in ('p_in', 'p_above', 'p_below', 'd_top', 'd_base', 'sigma')} if rec.get('context', {}).get('estimate') else None,
             'confidence': rec['confidence']}
        # agreement on the steer: build, hold or drop, with a dead band of half a degree per hundred feet on the record
        if a_build is not None:
            sr = 0 if abs(r['build_deg_100ft']) < 0.25 else (1 if r['build_deg_100ft'] > 0 else -1)
            sa = 0 if abs(a_build) < 0.5 else (1 if a_build > 0 else -1)
            d['steer_agrees'] = (sr == sa)
            d['steer_opposes'] = (sr * sa < 0)
            d['steer_pair'] = ('drop', 'hold', 'build')[sr + 1] + '/' + ('drop', 'hold', 'build')[sa + 1]
        for k, tol in (('wob', 0.25), ('rpm', 0.25), ('flow', 0.2)):
            av = d['actual'][k]
            d[f'{k}_agrees'] = (av is not None and av > 0 and abs(d['recommended'][k] - av) <= tol * max(av, 1.0))
        decisions.append(d)
        adv.answer('accept', 'the record', 'shadow mode: what was drilled next stands for the answer') if adv.current else None
        next_md = md[i] + every_ft
        i = int(np.searchsorted(md, next_md))
    # the exits: stretches the gamma says were out of the layer, and what the advisor said at the first reading out
    exits = []
    k = 0
    while k < len(decisions):
        if decisions[k]['out_by_gamma'] and (k == 0 or not decisions[k - 1]['out_by_gamma']):
            start = k
            while k < len(decisions) and decisions[k]['out_by_gamma']:
                k += 1
            seg = decisions[start:k]
            first = seg[0]; before = decisions[start - 1] if start > 0 else None
            # the way back: the layer is above or below by the filter's word at the first reading out
            est = first['estimate'] or {}
            back = 1 if est.get('p_below', 0) > est.get('p_above', 0) else -1          # below the layer: build (+); above: drop (-)
            called_first = (first['recommended']['build'] * back > 0)
            called_before = bool(before and before['recommended']['build'] * back > 0)
            driller_first = (first['actual']['build'] is not None and first['actual']['build'] * back > 0.5)
            exits.append({'from_md': first['md'], 'to_md': seg[-1]['md'], 'decisions_out': len(seg), 'layer_is': 'below' if back > 0 else 'above',
                          'advisor_called_at_first_reading': called_first, 'advisor_called_before': called_before, 'driller_corrected_at_first_reading': driller_first,
                          'advisor_words': first['recommended']['words']})
        else:
            k += 1
    n_d = len(decisions)
    with_build = [d for d in decisions if 'steer_agrees' in d]
    summary = {
        'decisions': n_d, 'lateral_ft': float(md[-1] - md[heel]), 'heel_md': float(md[heel]), 'rows': int(n),
        'steer_agree_pct': 100.0 * sum(d['steer_agrees'] for d in with_build) / max(len(with_build), 1),
        'steer_oppose_pct': 100.0 * sum(d['steer_opposes'] for d in with_build) / max(len(with_build), 1),
        'steer_pairs': {k: sum(1 for d in with_build if d['steer_pair'] == k) for k in sorted({d['steer_pair'] for d in with_build})},
        'advisor_steered_record_held_pct': 100.0 * sum(1 for d in with_build if d['steer_pair'].endswith('/hold') and not d['steer_pair'].startswith('hold')) / max(len(with_build), 1),
        'record_steered_advisor_held_pct': 100.0 * sum(1 for d in with_build if d['steer_pair'].startswith('hold/') and not d['steer_pair'].endswith('/hold')) / max(len(with_build), 1),
        'wob_agree_pct': 100.0 * sum(d['wob_agrees'] for d in decisions) / max(n_d, 1),
        'rpm_agree_pct': 100.0 * sum(d['rpm_agrees'] for d in decisions) / max(n_d, 1),
        'flow_agree_pct': 100.0 * sum(d['flow_agrees'] for d in decisions) / max(n_d, 1),
        'conditioning_calls': sum(1 for d in decisions if d['recommended']['conditioning'] != 'drill'),
        'out_by_gamma_pct': 100.0 * sum(d['out_by_gamma'] for d in decisions) / max(n_d, 1),
        'exits': len(exits), 'exits_called_at_first_reading': sum(e['advisor_called_at_first_reading'] for e in exits),
        'exits_called_before': sum(e['advisor_called_before'] for e in exits), 'exits_driller_corrected_at_first_reading': sum(e['driller_corrected_at_first_reading'] for e in exits),
        'mean_actual_rop': float(np.nanmean([d['actual']['rop'] for d in decisions if d['actual']['rop'] is not None])) if decisions else None,
        'recommended_wob_mean': float(np.mean([d['recommended']['wob'] for d in decisions])) if decisions else None,
        'actual_wob_mean': float(np.nanmean([d['actual']['wob'] for d in decisions if d['actual']['wob'] is not None])) if decisions else None,
        'recommended_rpm_mean': float(np.mean([d['recommended']['rpm'] for d in decisions])) if decisions else None,
        'actual_rpm_mean': float(np.nanmean([d['actual']['rpm'] for d in decisions if d['actual']['rpm'] is not None])) if decisions else None,
    }
    # whether the model describes this well: a derived model that leaves the well out of its layer most of the way
    # was read off the wrong rock, or the well was not steered to the low gamma at all
    fit = 'given' if not derived else ('poor: the well is out of the derived layer most of the way - the low gamma may not be the target, or the layer changed along the lateral; give the pre-drill model'
                                       if summary['out_by_gamma_pct'] > 40.0 else ('fair' if summary['out_by_gamma_pct'] > 15.0 else 'good'))
    summary['model_fit'] = fit
    result = {'label': LABEL, 'program': 'GEA Driller', 'version': __version__, 'well': cfg['well'], 'site': cfg['site'], 'lateral_file': os.path.abspath(lateral_csv),
              'run_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'model': model, 'model_derived': derived, 'policy': {k: policy.meta.get(k) for k in ('trained_steps', 'trained_utc', 'version')},
              'summary': summary, 'exits': exits, 'decisions': decisions,
              'reading': ("A replay in hindsight: the advisor saw, at each decision, what the rig had shown to that point and nothing past it. Agreement with the "
                          "driller says the two would have done alike, not that either was right; opposition says they would have differed, and the record alone "
                          "cannot say who was right. The exits are the gamma's word, on a log indexed to the bit and shallow by the sensor's offset.")}
    if out_dir:
        write_report(result, out_dir)
    return result


# -- the report --------------------------------------------------------------------------------------------------------------

def _section_svg(result: dict) -> str:
    ds = [d for d in result['decisions'] if d['tvd'] is not None]
    if len(ds) < 2:
        return ''
    m = result['model']; W, H = 900, 300
    heel = result['summary']['heel_md']
    vs = [d['md'] - heel for d in ds]; tv = [d['tvd'] for d in ds]
    vs1 = max(vs) + 50
    tops = [(v, m['layer_top_tvd_ft'] + v * math.tan(math.radians(m['dip_deg']))) for v in np.linspace(0, vs1, 40)]
    bases = [(v, t + m['thickness_ft']) for v, t in tops]
    tmin = min(min(tv), min(t for _, t in tops)) - 15; tmax = max(max(tv), max(t for _, t in bases)) + 15
    X = lambda v: 50 + v / vs1 * (W - 70); Y = lambda t: 15 + (t - tmin) / (tmax - tmin) * (H - 45)
    e = _html.escape
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="The lateral against the layer model, with the advisor\'s word at each decision" style="max-width:900px;font:11px sans-serif">']
    poly = ' '.join(f'{X(v):.1f},{Y(t):.1f}' for v, t in tops) + ' ' + ' '.join(f'{X(v):.1f},{Y(t):.1f}' for v, t in reversed(bases))
    s.append(f'<polygon points="{poly}" fill="#dff2e4" stroke="#2a7" stroke-dasharray="4 3"/>')
    s.append('<polyline points="' + ' '.join(f'{X(v):.1f},{Y(t):.1f}' for v, t in zip(vs, tv)) + '" fill="none" stroke="#2b6cb0" stroke-width="2"/>')
    for d, v, t in zip(ds, vs, tv):
        col = '#b03030' if d['out_by_gamma'] else '#2b6cb0'
        if d.get('steer_opposes'):
            s.append(f'<circle cx="{X(v):.1f}" cy="{Y(t):.1f}" r="3.5" fill="#f5a623" stroke="#8a5a00"><title>{e(d["recommended"]["words"])} - the record built {d["actual"]["build"]:+.1f}</title></circle>')
        elif d['out_by_gamma']:
            s.append(f'<circle cx="{X(v):.1f}" cy="{Y(t):.1f}" r="2.5" fill="{col}"><title>gamma {d["gamma"]:.0f}: out of the layer by the gamma</title></circle>')
    for i in range(5):
        t = tmin + (tmax - tmin) * i / 4; s.append(f'<text x="2" y="{Y(t) + 4:.1f}" fill="#555">{t:.0f} ft</text>')
        v = vs1 * i / 4; s.append(f'<text x="{X(v) - 12:.1f}" y="{H - 4}" fill="#555">{v:.0f} ft</text>')
    s.append('</svg>')
    s.append('<p class="muted">Green: the layer by the model. Blue: the hole from the record. Red dots: the gamma says out of the layer. Orange: the advisor would have steered the other way from the record (point at one for both).</p>')
    return '\n'.join(s)


def report_md(result: dict) -> str:
    s = result['summary']; m = result['model']
    L = [f"# {LABEL} - {result['well']}", '',
         f"> GEA Driller {result['version']}, the policy of {result['policy'].get('trained_utc') or 'the kit'}, replayed over the lateral of {result['well']} at {result['site']} "
         f"({s['lateral_ft']:,.0f} ft from the heel at {s['heel_md']:,.0f} ft MD; {s['decisions']} decisions, one every 30 ft). {result['reading']}", '',
         '## The model', '',
         f"Layer top {m['layer_top_tvd_ft']:,.1f} ft TVD at the heel, {m['thickness_ft']:.1f} ft thick, dip {m['dip_deg']:+.2f}° along the lateral; gamma {m['gamma_in']:.0f} in the layer, "
         f"{m['gamma_above']:.0f} above, {m['gamma_below']:.0f} below. " + (f"**{m.get('how', '')}**" if result['model_derived'] else 'The site\'s pre-drill model, as given.')
         + (f" How well it describes this well: **{s['model_fit']}**." if result['model_derived'] else ''), '',
         '## Agreement', '',
         '| What | Agree | Oppose |', '|---|---|---|',
         f"| The steer (build, hold or drop, over the next 90 ft) | {s['steer_agree_pct']:.0f}% | {s['steer_oppose_pct']:.0f}% |",
         f"| - the advisor would have steered where the record held | {s['advisor_steered_record_held_pct']:.0f}% | |",
         f"| - the record steered where the advisor would have held | {s['record_steered_advisor_held_pct']:.0f}% | |",
         f"| Weight on bit (within 25%) | {s['wob_agree_pct']:.0f}% | |", f"| Rotary speed (within 25%) | {s['rpm_agree_pct']:.0f}% | |", f"| Flow (within 20%) | {s['flow_agree_pct']:.0f}% | |", '',
         'Advisor/record pairs: ' + ', '.join(f"{k} {v}" for k, v in s['steer_pairs'].items()) + '.', '',
         f"The advisor asked for conditioning {s['conditioning_calls']} time(s). Its weight averaged {s['recommended_wob_mean']:.0f} klb against the record's {s['actual_wob_mean'] or 0:.0f}; "
         f"its speed {s['recommended_rpm_mean']:.0f} rpm against {s['actual_rpm_mean'] or 0:.0f}; the record's rate averaged {s['mean_actual_rop'] or 0:.0f} ft/h.", '',
         '## The exits', '',
         f"The gamma says the well was out of the layer at {s['out_by_gamma_pct']:.0f}% of the decisions, in {s['exits']} stretch(es). At the first reading out the advisor called the way back "
         f"{s['exits_called_at_first_reading']} time(s), and was already steering that way at the decision before {s['exits_called_before']} time(s); the record shows the driller correcting at the "
         f"first reading {s['exits_driller_corrected_at_first_reading']} time(s).", '']
    if result['exits']:
        L += ['| From MD | To MD | Decisions out | The layer was | Advisor at first reading | Advisor before | Driller at first reading | The advisor said |', '|---|---|---|---|---|---|---|---|']
        for x in result['exits'][:40]:
            L.append(f"| {x['from_md']:,.0f} | {x['to_md']:,.0f} | {x['decisions_out']} | {x['layer_is']} | {'yes' if x['advisor_called_at_first_reading'] else 'no'} | {'yes' if x['advisor_called_before'] else 'no'} | {'yes' if x['driller_corrected_at_first_reading'] else 'no'} | {x['advisor_words']} |")
        L.append('')
    L += ['## The decisions', '', '| MD | Gamma | Filter in | Advisor | Record built | WOB rec/act | rpm rec/act | flow rec/act |', '|---|---|---|---|---|---|---|---|']
    for d in result['decisions'][:600]:
        a = d['actual']; r = d['recommended']; est = d['estimate'] or {}
        fmt = lambda v, p: '—' if v is None else f'{v:{p}}'
        L.append(f"| {d['md']:,.0f} | {fmt(d['gamma'], '.0f')} | {est.get('p_in', 0):.0%} | {r['steer']} {r['build']:+.1f} | {fmt(a['build'], '+.1f')} | "
                 f"{r['wob']:.0f}/{fmt(a['wob'], '.0f')} | {r['rpm']:.0f}/{fmt(a['rpm'], '.0f')} | {r['flow']:.0f}/{fmt(a['flow'], '.0f')} |")
    if len(result['decisions']) > 600:
        L.append(f"| … | | | {len(result['decisions']) - 600} more in the JSON | | | | |")
    L += ['', f"{LABEL}. Nothing here touched the well. The lateral file: `{os.path.basename(result['lateral_file'])}`."]
    return '\n'.join(L)


def write_report(result: dict, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    p = os.path.join(out_dir, 'driller_shadow.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=1)
    paths['json'] = p
    md = report_md(result)
    p = os.path.join(out_dir, 'driller_shadow.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(md)
    paths['md'] = p
    # the html: the markdown's tables and paragraphs, the section drawing under the model
    e = _html.escape
    body = []
    for block in md.split('\n\n'):
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].startswith('# '):
            body.append(f'<h1>{e(lines[0][2:])}</h1>')
        elif lines[0].startswith('## '):
            body.append(f'<h2>{e(lines[0][3:])}</h2>')
            if lines[0][3:] == 'Agreement':
                body.append(_section_svg(result))
        elif lines[0].startswith('> '):
            body.append(f'<p class="muted">{e(lines[0][2:])}</p>')
        elif lines[0].startswith('|'):
            rows = [l for l in lines if l.startswith('|') and not l.startswith('|---')]
            cells = [[c.strip() for c in r.strip().strip('|').split('|')] for r in rows]
            body.append('<table><thead><tr>' + ''.join(f'<th>{e(c)}</th>' for c in cells[0]) + '</tr></thead><tbody>'
                        + ''.join('<tr>' + ''.join(f'<td>{e(c)}</td>' for c in r) + '</tr>' for r in cells[1:]) + '</tbody></table>')
        else:
            body.append('<p>' + e(' '.join(lines)).replace('**', '') + '</p>')
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(LABEL)} - {e(result["well"])}</title>'
            '<style>body{font:14px/1.45 system-ui,sans-serif;color:#1b2430;max-width:1000px;margin:20px auto;padding:0 16px}table{border-collapse:collapse;font-size:12px;margin:8px 0}'
            'td,th{border:1px solid #ccc;padding:2px 6px;text-align:left}.muted{color:#555}h1{font-size:20px}h2{font-size:16px;margin-top:20px}</style></head><body>'
            + '\n'.join(body) + '</body></html>')
    p = os.path.join(out_dir, 'driller_shadow.html')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(html)
    paths['html'] = p
    result['paths'] = paths
    return paths


# -- a lateral from the twin, for the self-test ---------------------------------------------------------------------------------

def twin_lateral(path: str, seed: int = 4, act_fn=None) -> dict:
    """A lateral CSV drilled in the twin by the textbook driller (or the given policy), one row per foot, so the
    replay can be proven against a lateral whose layer is known."""
    sc = T.Scene(seed); lat = T.Lateral(sc); o = lat.reset()
    act = act_fn or T.textbook_action
    rows: List[dict] = []
    heel_md = 10000.0
    hours = 0.0
    rng = np.random.RandomState(seed)
    while not lat.done:
        a = act(o)
        vs0, tvd0 = lat.vs, lat.tvd
        o, r, d, info = lat.step(a)
        if info.get('conditioning', 'drill') != 'drill':
            hours += info['hours']; continue
        n_ft = int(round(T.STEP_FT))
        for k in range(n_ft):
            f = (k + 1) / n_ft
            vs = vs0 + (lat.vs - vs0) * f; tvd = tvd0 + (lat.tvd - tvd0) * f
            rows.append({'md_ft': heel_md + vs, 'tvd_ft': tvd, 'gamma_api': sc.gamma(vs, tvd, rng), 'wob_klb': info['wob'], 'rpm': info['rpm'], 'flow_gpm': info['flow'],
                         'rop_ft_hr': info['rop'], 'spp_psi': 2800.0 + info['dp'], 'hours': hours + info['hours'] * f})
        hours += info['hours']
    # a vertical-ish approach above the heel, so the heel finder has something to find
    pre = [{'md_ft': heel_md - 300 + k, 'tvd_ft': rows[0]['tvd_ft'] - sum(math.cos(math.radians(45 + 45 * kk / 300.0)) for kk in range(k, 300)), 'gamma_api': sc.g_above + rng.normal(0, 5),
            'wob_klb': 20.0, 'rpm': 100.0, 'flow_gpm': 450.0, 'rop_ft_hr': 50.0, 'spp_psi': 2600.0, 'hours': -1.0} for k in range(300)]
    write_lateral(path, pre + rows)
    return {'scene': sc.describe(), 'summary': lat.summary(), 'rows': len(pre) + len(rows), 'heel_md': heel_md}
