# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""well_records - a well known only from its records: the regulator's well file, a completion report, an old test.

Most wells in GEA arrive with data - a drilling record, a gauge series, a log. A well bought, inherited or shown to a
client often arrives as paper instead: the permit, the completion report with its casing and perforations, the
inclination report, the potential test, the operator changes. This module holds what those papers say, each value with
the document it came from, so the well can sit in a workspace beside the others: its identity and position (through
Workspace.set_well, as for any well), its casing (through the Casing tab, as for any well), and here:

- the completion facts: spud and completion dates, total and plugged-back depth, the producing interval, the field;
- an inclination survey as regulators take it (Texas Form W-12 and its kind): measured depth and inclination at each
  station, no direction. Its true vertical depth by the minimum-curvature arithmetic on a constant azimuth (the azimuth
  cancels out of the vertical), and the regulator's accumulated displacement - each course's length times the sine of
  its station's inclination, summed - which is the farthest the hole can have wandered, not where it went;
- gas well deliverability tests (the back-pressure or multipoint test of Rawlins and Schellhardt, Bureau of Mines
  Monograph 7, 1935; Texas Form G-1): the stabilized shut-in pressure and three or more flowing points, fitted to
  q = C (Pr^2 - Pwf^2)^n by least squares on the logarithms, the absolute open flow at Pwf = 14.65 psia, and what the
  points themselves say about the test - an exponent outside 0.5 to 1.0 (a curve no stabilized gas well draws:
  the points were not stabilized, or the pressures or rates are not what the form calls them), the shut-in pressure at
  the gauge against the shut-in tubing pressure plus the weight of the gas between them (real gas, Dranchuk and
  Abou-Kassem z on Sutton's pseudocritical properties, as the casing design uses), each rate recomputed from the
  choke or orifice figures the form gives, and the choke pressure the form used against the flowing wellhead pressure
  it reports beside it.

What this module will not call a measurement: anything the papers do not say. A value left blank stays blank; a
document's arithmetic is recomputed, never corrected; where two documents disagree both are shown.

Standard library only.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

P_BASE = 14.65                      # psia: the pressure base of Texas gas measurement, and of the open flow
N_RANGE = (0.5, 1.0)                # the exponent of a stabilized gas well's back-pressure curve (Rawlins & Schellhardt)
FILE = 'well_records.json'


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _path(ws, well_id: str) -> str:
    return os.path.join(ws.well_dir(well_id), FILE)


def load(ws, well_id: str) -> dict:
    p = _path(ws, well_id)
    if not os.path.isfile(p):
        return {'completion': {}, 'survey': None, 'gas_tests': []}
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    d.setdefault('completion', {}); d.setdefault('survey', None); d.setdefault('gas_tests', [])
    return d


def _save(ws, well_id: str, d: dict) -> None:
    p = _path(ws, well_id)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(p + '.tmp', p)


def _num(v, what: str, lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        x = float(str(v).replace(',', '').strip())
    except ValueError:
        raise ValueError(f'{what}: {v!r} is not a number')
    if not math.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f'{what}: {x:g} is outside {lo:g} to {hi:g}' if lo is not None and hi is not None else f'{what}: {x:g} is out of range')
    return x


def _need_source(src) -> str:
    s = str(src or '').strip()
    if not s:
        raise ValueError('say which document this comes from (for example: RRC Form G-1, 11/06/94)')
    return s[:300]


# -- the completion facts -------------------------------------------------------------------------------------------
COMPLETION_FIELDS = ('field', 'county', 'state', 'permit', 'spud', 'completed', 'td_md_ft', 'pbtd_md_ft', 'perf_top_ft', 'perf_base_ft',
                     'formation', 'tubing', 'packer', 'lease_line_ft')


def set_completion(ws, actor: str, well_id: str, values: dict, source: str) -> dict:
    ws.well(well_id)
    source = _need_source(source)
    d = load(ws, well_id)
    c = dict(d['completion'])
    for k in COMPLETION_FIELDS:
        if k not in values:
            continue
        v = values[k]
        if k in ('td_md_ft', 'pbtd_md_ft', 'perf_top_ft', 'perf_base_ft', 'lease_line_ft'):
            v = _num(v, k.replace('_', ' '), 0, 40000)
        else:
            v = (str(v).strip()[:120] or None) if v is not None else None
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    if c.get('perf_top_ft') is not None and c.get('perf_base_ft') is not None and c['perf_base_ft'] <= c['perf_top_ft']:
        raise ValueError(f"the producing interval's base ({c['perf_base_ft']:g} ft) must be deeper than its top ({c['perf_top_ft']:g} ft)")
    if c.get('pbtd_md_ft') is not None and c.get('td_md_ft') is not None and c['pbtd_md_ft'] > c['td_md_ft']:
        raise ValueError(f"the plugged-back depth ({c['pbtd_md_ft']:g} ft) cannot be below the total depth ({c['td_md_ft']:g} ft)")
    c['source'] = source
    c['declared_by'], c['declared_utc'] = actor, _utc()
    d['completion'] = c
    _save(ws, well_id, d)
    ws.audit(actor, 'well.records.completion', {'well': well_id, 'completion': c})
    return c


# -- the inclination survey -----------------------------------------------------------------------------------------
def parse_stations(text: str) -> List[List[float]]:
    """'MD, inclination' per line (commas, tabs or spaces; a header line is skipped). Inclination in degrees, as a
    decimal or a fraction the way the forms write it ('1 1/4', '1-3/4')."""
    out = []
    for i, line in enumerate(str(text or '').splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = [p for p in s.replace('\t', ',').replace(';', ',').split(',') if p.strip()]
        if len(parts) == 1:
            parts = s.split(None, 1)
        if len(parts) < 2:
            raise ValueError(f'line {i}: "{s}" - write the measured depth and the inclination, e.g. 2602, 1 3/4')
        try:
            md = float(parts[0].replace(',', '').strip())
        except ValueError:
            if not out:
                continue                              # a header line
            raise ValueError(f'line {i}: the measured depth {parts[0]!r} is not a number')
        out.append([md, _angle(parts[1], i)])
    return out


def _angle(s: str, line: int) -> float:
    t = str(s).strip().replace('°', '').replace('-', ' ')
    try:
        bits = t.split()
        if len(bits) == 2 and '/' in bits[1]:
            n, d = bits[1].split('/')
            return float(bits[0]) + float(n) / float(d)
        if '/' in t:
            n, d = t.split('/')
            return float(n) / float(d)
        return float(t)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f'line {line}: the inclination {s!r} is not a number of degrees')


def survey_calc(stations: List[List[float]]) -> dict:
    """TVD by minimum curvature on a constant azimuth (the vertical is the same whatever the azimuth, when it does not
    change), and the regulator's accumulated displacement: each course's length times the sine of its lower station's
    inclination (Form W-12 columns 12 to 16)."""
    if len(stations) < 1:
        raise ValueError('an inclination survey needs at least one station')
    rows, md0, i0, tvd, disp = [], 0.0, 0.0, 0.0, 0.0
    for md, inc in stations:
        if md <= md0 and rows:
            raise ValueError(f'the stations must deepen: {md:g} ft follows {md0:g} ft')
        if not (0 <= inc < 90):
            raise ValueError(f'an inclination of {inc:g} degrees at {md:g} ft is not a survey of this kind')
        dmd = md - md0
        a1, a2 = math.radians(i0), math.radians(inc)
        dl = abs(a2 - a1)
        rf = 1.0 if dl < 1e-9 else (2 / dl) * math.tan(dl / 2)
        tvd += dmd / 2 * (math.cos(a1) + math.cos(a2)) * rf
        course = dmd * math.sin(a2)                       # column 15: the course times the sine of its station's inclination
        disp += course
        rows.append({'md_ft': md, 'inc_deg': inc, 'course_ft': round(dmd, 2), 'per_100ft': round(math.sin(a2) * 100, 3),
                     'course_disp_ft': round(course, 2), 'accum_disp_ft': round(disp, 2), 'tvd_ft': round(tvd, 1)})
        md0, i0 = md, inc
    return {'rows': rows, 'td_md_ft': md0, 'td_tvd_ft': round(tvd, 1), 'accum_disp_ft': round(disp, 2),
            'max_inc_deg': max(r['inc_deg'] for r in rows)}


def tvd_at(calc: dict, md: float) -> Optional[float]:
    """TVD at a measured depth inside the survey, interpolated along its courses."""
    prev = {'md_ft': 0.0, 'tvd_ft': 0.0}
    for r in calc['rows']:
        if md <= r['md_ft']:
            f = (md - prev['md_ft']) / (r['md_ft'] - prev['md_ft']) if r['md_ft'] > prev['md_ft'] else 0
            return round(prev['tvd_ft'] + f * (r['tvd_ft'] - prev['tvd_ft']), 1)
        prev = r
    return None


def disp_at(calc: dict, md: float) -> Optional[float]:
    prev = {'md_ft': 0.0, 'accum_disp_ft': 0.0}
    for r in calc['rows']:
        if md <= r['md_ft']:
            return round(prev['accum_disp_ft'] + (md - prev['md_ft']) * r['per_100ft'] / 100, 2)
        prev = r
    return None


def set_survey(ws, actor: str, well_id: str, text: str, source: str, stated_disp_ft=None) -> dict:
    ws.well(well_id)
    source = _need_source(source)
    st = parse_stations(text)
    calc = survey_calc(st)
    d = load(ws, well_id)
    d['survey'] = {'stations': st, 'source': source, 'stated_disp_ft': _num(stated_disp_ft, 'the stated displacement', 0, 100000),
                   'declared_by': actor, 'declared_utc': _utc()}
    _save(ws, well_id, d)
    ws.audit(actor, 'well.records.survey', {'well': well_id, 'stations': len(st), 'td_md_ft': calc['td_md_ft'], 'accum_disp_ft': calc['accum_disp_ft']})
    return d['survey']


# -- the gas well test ------------------------------------------------------------------------------------------------
def fit(points: List[dict], pr_psia: float) -> dict:
    """q = C (Pr^2 - Pwf^2)^n, least squares on log q against log(Pr^2 - Pwf^2)."""
    xs, ys = [], []
    for p in points:
        d2 = pr_psia ** 2 - p['pwf_psia'] ** 2
        if d2 <= 0:
            raise ValueError(f"a flowing pressure of {p['pwf_psia']:g} psia is not below the shut-in {pr_psia:g} psia")
        xs.append(math.log(d2)); ys.append(math.log(p['q_mcfd']))
    m = len(xs)
    mx, my = sum(xs) / m, sum(ys) / m
    sxx = sum((x - mx) ** 2 for x in xs)
    if m < 2 or sxx <= 0:
        return {'n': None, 'c': None}
    n = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    c = math.exp(my - n * mx)
    res = [y - (math.log(c) + n * x) for x, y in zip(xs, ys)]
    return {'n': n, 'c': c, 'rms_log': math.sqrt(sum(r * r for r in res) / m)}


def analyze_test(t: dict) -> dict:
    """Everything a back-pressure test's own figures say. t: pr_psia (shut-in pressure at the gauge, or at the
    reservoir), points [{q_mcfd, pwf_psia, pw_psia?, choke?}], and optionally sitp_psia, gauge_ft, gas_sg, bht_f,
    surface_temp_f, stated_aof_mcfd, stated_n, and per point the rate figures (coef, p_used, ftf, fg, fpv) and the
    stated rate."""
    pr = float(t['pr_psia'])
    pts = sorted(t['points'], key=lambda p: -p['q_mcfd'])
    out = {'pr_psia': pr, 'points': [], 'findings': []}
    for p in pts:
        row = {**p, 'dp2_k': round((pr ** 2 - p['pwf_psia'] ** 2) / 1000, 1)}
        if p.get('coef') and p.get('p_used'):
            q_re = p['coef'] * p['p_used'] * (p.get('ftf') or 1.0) * (p.get('fg') or 1.0) * (p.get('fpv') or 1.0)
            row['q_recomputed'] = round(q_re, 1)
            if p.get('pw_psia'):
                q_pw = p['coef'] * p['pw_psia'] * (p.get('ftf') or 1.0) * (p.get('fg') or 1.0) * (p.get('fpv') or 1.0)
                row['q_at_pw'] = round(q_pw, 1)
        out['points'].append(row)
    f = fit(pts, pr)
    out['fit'] = {'n': round(f['n'], 3) if f['n'] is not None else None, 'c': float(f'{f["c"]:.4g}') if f['c'] else None,
                  'rms_log': round(f.get('rms_log', 0.0), 4)}
    if f['n'] is not None:
        out['aof_fitted_mcfd'] = round(f['c'] * (pr ** 2 - P_BASE ** 2) ** f['n'], 0)
        hi = pts[0]
        for n_ in (N_RANGE[1], N_RANGE[0]):
            c_ = hi['q_mcfd'] / (pr ** 2 - hi['pwf_psia'] ** 2) ** n_
            out[f'aof_n{str(n_).replace(".", "")}_mcfd'] = round(c_ * (pr ** 2 - P_BASE ** 2) ** n_, 0)
        if not (N_RANGE[0] <= f['n'] <= N_RANGE[1]):
            out['findings'].append({'level': 'warn', 'what': 'the points do not draw a deliverability curve',
                                    'detail': (f"the fitted exponent n is {f['n']:.2f}; a stabilized gas well's lies between 0.5 and 1.0. The points were not "
                                               "stabilized (short flow periods), or the pressures or rates are not what the form calls them. Through the "
                                               f"highest-rate point the open flow is {out['aof_n10_mcfd']:,.0f} Mcf/d with n = 1.0 and "
                                               f"{out['aof_n05_mcfd']:,.0f} Mcf/d with n = 0.5: the test bounds it no closer than that.")})
    if t.get('stated_aof_mcfd'):
        s = float(t['stated_aof_mcfd'])
        out['stated_aof_mcfd'] = s
        ref = out.get('aof_n10_mcfd') if f['n'] is not None and not (N_RANGE[0] <= f['n'] <= N_RANGE[1]) else out.get('aof_fitted_mcfd')
        if ref and abs(s - ref) / ref > 0.05:
            out['findings'].append({'level': 'info', 'what': 'the stated open flow against the points',
                                    'detail': f'the document states {s:,.0f} Mcf/d; its own points give {ref:,.0f} Mcf/d by the same rule'})
    for row in out['points']:
        if row.get('q_recomputed') is not None and abs(row['q_recomputed'] - row['q_mcfd']) / row['q_mcfd'] > 0.03:
            out['findings'].append({'level': 'warn', 'what': f"the rate at {row.get('choke') or row['q_mcfd']}",
                                    'detail': f"stated {row['q_mcfd']:,.0f} Mcf/d; the form's own figures give {row['q_recomputed']:,.0f}"})
    choke_rows = [r for r in out['points'] if r.get('p_used') and r.get('pw_psia')]
    if choke_rows and all(r['p_used'] > 1.2 * r['pw_psia'] for r in choke_rows):
        lo = min(r['p_used'] / r['pw_psia'] for r in choke_rows); hi_ = max(r['p_used'] / r['pw_psia'] for r in choke_rows)
        out['findings'].append({'level': 'warn', 'what': "the pressure the rates were computed with",
                                'detail': (f"the rates use {min(r['p_used'] for r in choke_rows):g} to {max(r['p_used'] for r in choke_rows):g} psi, "
                                           f"{lo:.1f} to {hi_:.1f} times the flowing wellhead pressures the same form reports. If the gas flowed through a "
                                           "choke at the wellhead, the rates are the flowing pressure's: " +
                                           ', '.join(f"{r['q_at_pw']:,.0f}" for r in choke_rows) + " Mcf/d.")})
    if t.get('sitp_psia') and t.get('gauge_ft'):
        from . import casing_design as CD
        ts = float(t.get('surface_temp_f') or 70.0)
        bht = float(t.get('bht_f') or ts + 1.5 * float(t['gauge_ft']) / 100)
        cfg = {'gas_sg': float(t.get('gas_sg') or 0.65), 'surface_temp_f': ts,
               'geothermal_f_100ft': (bht - ts) / float(t['gauge_ft']) * 100, 'gas_gradient_psi_ft': None}
        p_gauge = CD.gas_column(cfg, float(t['sitp_psia']) - CD.ATM, 0.0, float(t['gauge_ft'])) + CD.ATM
        out['shut_in_check'] = {'sitp_psia': float(t['sitp_psia']), 'gauge_ft': float(t['gauge_ft']), 'expected_psia': round(p_gauge, 1),
                                'stated_psia': pr, 'gas_head_psi': round(p_gauge - float(t['sitp_psia']), 1)}
        if abs(p_gauge - pr) > max(10.0, 0.02 * pr):
            out['findings'].append({'level': 'warn', 'what': 'the shut-in pressures do not agree',
                                    'detail': (f"{float(t['sitp_psia']):g} psia at the surface and {round(p_gauge - float(t['sitp_psia'])):g} psi of gas "
                                               f"(gravity {cfg['gas_sg']:g}, real gas) make {p_gauge:,.0f} psia at {float(t['gauge_ft']):,.0f} ft; the document "
                                               f"states {pr:,.0f}. The open flow above uses the document's {pr:,.0f}.")})
    if t.get('gauge_ft'):
        out['gradient_psi_ft'] = round(pr / float(t['gauge_ft']), 3)
        if out['gradient_psi_ft'] < 0.40:
            out['findings'].append({'level': 'info', 'what': 'a pressure below the hydrostatic',
                                    'detail': f"{pr:g} psia at {float(t['gauge_ft']):,.0f} ft is {out['gradient_psi_ft']} psi/ft, under the 0.433 of fresh water."})
    return out


def parse_points(text: str) -> List[dict]:
    """'rate Mcf/d, flowing pressure psia[, flowing wellhead psia[, choke[, coefficient, pressure used, Ftf, Fg, Fpv]]]' per line."""
    pts = []
    for i, line in enumerate(str(text or '').splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = [p.strip() for p in s.replace('\t', ',').replace(';', ',').split(',')]
        try:
            q, pwf = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            if not pts:
                continue
            raise ValueError(f'line {i}: "{s}" - write the rate (Mcf/d) and the flowing pressure (psia)')
        p = {'q_mcfd': _num(q, f'line {i}: the rate', 0.001, 1e7), 'pwf_psia': _num(pwf, f'line {i}: the flowing pressure', 1, 30000)}
        names = ('pw_psia', 'choke', 'coef', 'p_used', 'ftf', 'fg', 'fpv')
        for k, v in zip(names, parts[2:]):
            if not v:
                continue
            p[k] = v if k == 'choke' else _num(v, f'line {i}: {k}', 0, 100000)
        pts.append(p)
    if len(pts) < 2:
        raise ValueError('a deliverability test needs at least two flowing points (a multipoint test has three or four)')
    return pts


def add_gas_test(ws, actor: str, well_id: str, values: dict, source: str) -> dict:
    ws.well(well_id)
    source = _need_source(source)
    t = {'date': str(values.get('date') or '').strip()[:20] or None,
         'pr_psia': _num(values.get('pr_psia'), 'the shut-in pressure', 1, 30000),
         'points': parse_points(values.get('points', '')) if isinstance(values.get('points'), str) else values.get('points'),
         'sitp_psia': _num(values.get('sitp_psia'), 'the shut-in tubing pressure', 1, 30000),
         'gauge_ft': _num(values.get('gauge_ft'), 'the gauge depth', 1, 40000),
         'gas_sg': _num(values.get('gas_sg'), 'the gas gravity', 0.55, 1.8),
         'bht_f': _num(values.get('bht_f'), 'the bottom-hole temperature', 0, 600),
         'surface_temp_f': _num(values.get('surface_temp_f'), 'the surface temperature', -40, 150),
         'stated_aof_mcfd': _num(values.get('stated_aof_mcfd'), 'the stated open flow', 0, 1e8),
         'source': source, 'declared_by': actor, 'declared_utc': _utc()}
    if t['pr_psia'] is None:
        raise ValueError('a deliverability test needs the shut-in pressure (psia) its points are drawn against')
    t = {k: v for k, v in t.items() if v is not None}
    analyze_test(t)                                       # refuses what cannot be drawn
    d = load(ws, well_id)
    d['gas_tests'].append(t)
    _save(ws, well_id, d)
    ws.audit(actor, 'well.records.gas_test', {'well': well_id, 'date': t.get('date'), 'points': len(t['points']), 'pr_psia': t['pr_psia']})
    return t


def remove_gas_test(ws, actor: str, well_id: str, index: int) -> None:
    d = load(ws, well_id)
    if not (0 <= index < len(d['gas_tests'])):
        raise ValueError(f'no test {index + 1} on this well')
    gone = d['gas_tests'].pop(index)
    _save(ws, well_id, d)
    ws.audit(actor, 'well.records.gas_test.remove', {'well': well_id, 'date': gone.get('date'), 'source': gone.get('source')})


# -- the page ---------------------------------------------------------------------------------------------------------
def page(ws, well_id: str) -> dict:
    w = ws.well(well_id)
    d = load(ws, well_id)
    out = {'well': {k: w.get(k) for k in ('id', 'display', 'kind', 'files', 'identity', 'surface', 'source')},
           'completion': d['completion'], 'survey': None, 'gas_tests': []}
    if d['survey']:
        calc = survey_calc(d['survey']['stations'])
        s = {**d['survey'], 'calc': calc}
        c = d['completion']
        if c.get('perf_top_ft') is not None:
            s['at_pay'] = {'md_ft': c['perf_top_ft'], 'tvd_ft': tvd_at(calc, c['perf_top_ft']), 'max_disp_ft': disp_at(calc, c['perf_top_ft'])}
        if d['survey'].get('stated_disp_ft') is not None:      # the forms' sine tables round (4 degrees is 7.00 per hundred, not 6.98)
            sd = d['survey']['stated_disp_ft']
            s['agrees'] = abs(sd - calc['accum_disp_ft']) <= max(0.5, 0.005 * sd)
        if c.get('lease_line_ft') and s.get('at_pay') and s['at_pay']['max_disp_ft'] is not None:
            s['inside_lease'] = s['at_pay']['max_disp_ft'] < c['lease_line_ft']
        out['survey'] = s
    for i, t in enumerate(d['gas_tests']):
        try:
            a = analyze_test(t)
            svg = svg_backpressure(a, f"Back-pressure test {t.get('date') or i + 1}")
        except ValueError as e:
            a, svg = {'error': str(e)}, ''
        out['gas_tests'].append({'index': i, 'test': t, 'analysis': a, 'svg': svg})
    rd = os.path.join(ws.reports_dir, 'wells', well_id)
    out['report'] = {'html': f'reports/wells/{well_id}/records_review.html' if os.path.isfile(os.path.join(rd, 'records_review.html')) else None,
                     'pdf': f'reports/wells/{well_id}/records_review.pdf' if os.path.isfile(os.path.join(rd, 'records_review.pdf')) else None}
    from . import casing_design as CD
    try:
        out['casing'] = [{'what': s.get('what'), 'size': CD.od_text(CD.size_in(s.get('od_in'))) if s.get('od_in') not in (None, '') else '',
                          'weight_lbft': s.get('weight_lbft'), 'grade': s.get('grade'), 'shoe_md_ft': s.get('shoe_md_ft')}
                         for s in (CD.program(ws, well_id).get('strings') or [])]
    except Exception:
        out['casing'] = []
    return out


EXAMPLE = {
    'well': 'Gilbert No. 3-A, Palo Pinto County, Texas (API 42-363-34303): public record, Railroad Commission of Texas well file',
    'survey': '294, 1\n786, 1\n1289, 1 1/4\n1600, 1 3/4\n1851, 1 1/4\n2100, 2\n2602, 1 3/4\n2913, 1\n3593, 4\n3899, 4\n4137, 4',
    'survey_source': 'RRC Form W-12 inclination report, Salmon Drilling, filed 12/27/94', 'stated_disp_ft': 154.82,
    'test': {'date': '1994-10-12', 'pr_psia': 870, 'sitp_psia': 859.1, 'gauge_ft': 2380, 'gas_sg': 0.70, 'bht_f': 105, 'surface_temp_f': 70,
             'stated_aof_mcfd': 1270,
             'points': '955, 428, 388.7, 14/64, 1.116, 826, 1.000, 0.9258, 1.118\n690, 524, 485.9, 12/64, 0.802, 831, 1.000, 0.9258, 1.118\n'
                       '483, 589, 550.1, 10/64, 0.553, 836, 1.000, 0.9258, 1.118\n306, 648, 605.9, 8/64, 0.347, 842, 1.000, 0.9258, 1.118'},
    'test_source': 'RRC Form G-1 gas well back pressure test, 10/12/94, filed 12/27/94',
}


LABELS = {'field': 'Field', 'county': 'County', 'state': 'State', 'permit': 'Drilling permit', 'spud': 'Spud', 'completed': 'Completed',
          'td_md_ft': 'Total depth (ft MD)', 'pbtd_md_ft': 'Plugged-back depth (ft MD)', 'formation': 'Producing formation', 'tubing': 'Tubing',
          'packer': 'Packer', 'lease_line_ft': 'Nearest lease line (ft)'}


# -- the report: a well's records reviewed, for a client ---------------------------------------------------------------
def svg_backpressure(a: dict, title: str) -> str:
    """The back-pressure plot as the forms draw it: log (Pr^2 - Pwf^2) against log q, the points, the fitted line,
    and the n = 1.0 and n = 0.5 lines through the highest-rate point."""
    from .well_report import FONT, INK, INK2, MUTED, GRID, SLOTS, _e
    W, H, L, R, T, B = 760, 360, 78, 170, 34, 46
    pts = a['points']
    pr2 = a['pr_psia'] ** 2
    q_lo = min(p['q_mcfd'] for p in pts) / 2
    q_hi = max([p['q_mcfd'] for p in pts] + [a.get('aof_n10_mcfd') or 0, a.get('aof_fitted_mcfd') or 0]) * 1.3
    d_lo, d_hi = min(p['dp2_k'] for p in pts) / 2, (pr2 - P_BASE ** 2) / 1000 * 1.5
    lx = lambda q: L + (math.log10(q) - math.log10(q_lo)) / (math.log10(q_hi) - math.log10(q_lo)) * (W - L - R)
    ly = lambda d: T + (math.log10(d_hi) - math.log10(d)) / (math.log10(d_hi) - math.log10(d_lo)) * (H - T - B)
    g = [f'<text x="{L}" y="20" {FONT} font-size="13" font-weight="600" fill="{INK}">{_e(title)}</text>']
    for dec in range(int(math.floor(math.log10(q_lo))), int(math.ceil(math.log10(q_hi))) + 1):
        for m in (1, 2, 5):
            q = m * 10 ** dec
            if q_lo <= q <= q_hi:
                g.append(f'<line x1="{lx(q):.1f}" x2="{lx(q):.1f}" y1="{T}" y2="{H - B}" stroke="{GRID}"/><text x="{lx(q):.1f}" y="{H - B + 15}" {FONT} font-size="11" fill="{INK2}" text-anchor="middle">{q:,.0f}</text>')
    for dec in range(int(math.floor(math.log10(d_lo))), int(math.ceil(math.log10(d_hi))) + 1):
        for m in (1, 2, 5):
            d = m * 10 ** dec
            if d_lo <= d <= d_hi:
                g.append(f'<line x1="{L}" x2="{W - R}" y1="{ly(d):.1f}" y2="{ly(d):.1f}" stroke="{GRID}"/><text x="{L - 6}" y="{ly(d) + 4:.1f}" {FONT} font-size="11" fill="{INK2}" text-anchor="end">{d:,.0f}</text>')
    g.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 8}" {FONT} font-size="11" fill="{MUTED}" text-anchor="middle">rate, Mcf/d (log)</text>')
    g.append(f'<text x="16" y="{(T + H - B) / 2:.0f}" {FONT} font-size="11" fill="{MUTED}" transform="rotate(-90 16 {(T + H - B) / 2:.0f})" text-anchor="middle">Pr² - Pwf², thousands psi² (log)</text>')
    dmax = (pr2 - P_BASE ** 2) / 1000
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{ly(dmax):.1f}" y2="{ly(dmax):.1f}" stroke="{MUTED}" stroke-dasharray="4 4"/>'
             f'<text x="{L + 6}" y="{ly(dmax) - 5:.1f}" {FONT} font-size="11" fill="{MUTED}">open flow: Pwf = {P_BASE} psia</text>')
    hi = max(pts, key=lambda p: p['q_mcfd'])
    keys = []
    lines = []
    if a.get('fit', {}).get('n'):
        n, c = a['fit']['n'], a['fit']['c']
        lines.append((SLOTS[0], f"fitted, n = {n:.2f}", lambda d: c * (d * 1000) ** n))
    for i, n_ in enumerate((1.0, 0.5)):
        c_ = hi['q_mcfd'] / (hi['dp2_k'] * 1000) ** n_
        lines.append((SLOTS[1 + i], f'n = {n_:.1f}, highest rate', (lambda cc, nn: (lambda d: cc * (d * 1000) ** nn))(c_, n_)))
    for k, (col, label, qf) in enumerate(lines):
        seg = []
        for j in range(41):
            d = d_lo * (dmax / d_lo) ** (j / 40)
            q = qf(d)
            if q_lo <= q <= q_hi:
                seg.append(f'{lx(q):.1f},{ly(d):.1f}')
        if len(seg) > 1:
            g.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{col}" stroke-width="2"' + (' stroke-dasharray="6 4"' if k else '') + '/>')
        keys.append((col, label, k))
    for p in pts:
        g.append(f'<circle cx="{lx(p["q_mcfd"]):.1f}" cy="{ly(p["dp2_k"]):.1f}" r="4.5" fill="#fff" stroke="{INK}" stroke-width="1.5"/>')
    for i, (col, label, k) in enumerate(keys):
        g.append(f'<line x1="{W - R + 10}" x2="{W - R + 30}" y1="{T + 10 + i * 20}" y2="{T + 10 + i * 20}" stroke="{col}" stroke-width="2"' + (' stroke-dasharray="6 4"' if k else '') + '/>'
                 f'<text x="{W - R + 36}" y="{T + 14 + i * 20}" {FONT} font-size="11" fill="{INK}">{_e(label[:24])}</text>')
    g.append(f'<circle cx="{W - R + 20}" cy="{T + 10 + len(keys) * 20}" r="4.5" fill="#fff" stroke="{INK}" stroke-width="1.5"/>'
             f'<text x="{W - R + 36}" y="{T + 14 + len(keys) * 20}" {FONT} font-size="11" fill="{INK}">the test points</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(g)}</svg>'


def build_report(ws, well_id: str, program_version: str):
    from .client_reports import Document, Section, Table, Figure, PROGRAM_NAME
    pg = page(ws, well_id)
    w, c = pg['well'], pg['completion']
    ident = w.get('identity') or {}
    now = _utc()
    rid = f"REC-{well_id}-{now.replace('-', '').replace(':', '')}"
    doc_name = (w.get('files') or ['none attached'])[0]
    front = [['Report ID', rid], ['Well', w['display']], ['US Well Number (API)', ident.get('api_number') or 'not declared'],
             ['Operator of record', ident.get('operator') or 'not declared'], ['Field', c.get('field') or 'not declared'],
             ['Source document', doc_name + (f" (SHA-256 {(w.get('source') or {}).get('sha256', '')[:16]})" if (w.get('source') or {}).get('sha256') else '')],
             ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}']]
    secs = []
    rows = [[LABELS.get(k, k), v] for k, v in c.items() if k not in ('source', 'declared_by', 'declared_utc', 'perf_top_ft', 'perf_base_ft')]
    if c.get('perf_top_ft') is not None:
        rows.append(['Producing interval (ft MD)', f"{c['perf_top_ft']:,.0f} to {c['perf_base_ft']:,.0f}" if c.get('perf_base_ft') is not None else f"from {c['perf_top_ft']:,.0f}"])
    secs.append(Section('1', 'The well on record', [f"As the records state it ({c.get('source') or 'no completion declared'}). Every value carries the document it came from; a blank is a value the records do not give."],
                        [Table(['Item', 'Value'], rows or [['-', 'nothing declared yet']])]))
    secs.append(Section('2', 'Casing', ['The casing program as declared on the Casing tab (the strings, their sizes and shoe depths).'],
                        [Table(['String', 'Size (in)', 'Shoe (ft MD)'], [[s['what'], s['size'], s['shoe_md_ft']] for s in pg['casing']] or [['-', '-', 'not declared']])]))
    s = pg['survey']
    if s:
        cl = s['calc']
        p3 = [f"{len(cl['rows'])} stations to {cl['td_md_ft']:,.0f} ft measured depth, the largest inclination {cl['max_inc_deg']:g} degrees "
              f"({s['source']}). True vertical depth at the last station: {cl['td_tvd_ft']:,.1f} ft (minimum curvature on a constant azimuth; "
              f"the survey records no direction). Accumulated displacement: {cl['accum_disp_ft']:,.2f} ft - the farthest the hole can have moved "
              f"from vertical, not where it went."]
        if s.get('stated_disp_ft') is not None:
            p3.append(f"The form states {s['stated_disp_ft']:,.2f} ft; recomputed {cl['accum_disp_ft']:,.2f} ft" +
                      (' - the same within the rounding of the sine tables such forms use.' if s.get('agrees') else ' - they differ.'))
        if s.get('at_pay'):
            ap = s['at_pay']
            p3.append(f"At the top of the producing interval ({ap['md_ft']:,.0f} ft MD): {ap['tvd_ft']:,.1f} ft TVD, at most {ap['max_disp_ft']:,.1f} ft from vertical"
                      + (f"; the nearest lease line is {c['lease_line_ft']:,.0f} ft away." if c.get('lease_line_ft') else '.'))
        secs.append(Section('3', 'Inclination survey', p3, [Table(['MD (ft)', 'Inclination (deg)', 'Course (ft)', 'Displacement (ft)', 'Accumulated (ft)', 'TVD (ft)'],
                                                                   [[r['md_ft'], r['inc_deg'], r['course_ft'], r['course_disp_ft'], r['accum_disp_ft'], r['tvd_ft']] for r in cl['rows']])]))
    for i, gt in enumerate(pg['gas_tests'], 1):
        a, t = gt['analysis'], gt['test']
        if 'error' in a:
            continue
        paras = [f"Test of {t.get('date') or 'an undated day'} ({t['source']}): shut-in pressure {a['pr_psia']:,.1f} psia"
                 + (f" at {t['gauge_ft']:,.0f} ft" if t.get('gauge_ft') else '') + f", {len(a['points'])} flowing points, fitted to q = C (Pr² - Pwf²)^n."]
        if a.get('fit', {}).get('n') is not None:
            paras.append(f"Fitted exponent n = {a['fit']['n']:.2f}; open flow by the fit {a['aof_fitted_mcfd']:,.0f} Mcf/d; through the highest-rate point "
                         f"{a['aof_n10_mcfd']:,.0f} Mcf/d with n = 1.0 and {a['aof_n05_mcfd']:,.0f} Mcf/d with n = 0.5"
                         + (f"; the document states {a['stated_aof_mcfd']:,.0f} Mcf/d." if a.get('stated_aof_mcfd') else '.'))
        for f_ in a['findings']:
            paras.append(f"{f_['what'][0].upper() + f_['what'][1:]}: {f_['detail']}")
        tab = Table(['Rate (Mcf/d)', 'Pwf (psia)', 'Pr² - Pwf² (thousands)', 'Wellhead (psia)', 'Choke', 'Rate from the form\'s figures', 'Rate at the wellhead pressure'],
                    [[p['q_mcfd'], p['pwf_psia'], p['dp2_k'], p.get('pw_psia'), p.get('choke'), p.get('q_recomputed'), p.get('q_at_pw')] for p in a['points']])
        fig = Figure(f'gas_test_{i}', svg_backpressure(a, f"Back-pressure test {t.get('date') or i}"),
                     f"Back-pressure plot: the {len(a['points'])} test points, the fitted line and the n = 1.0 and n = 0.5 lines through the highest rate",
                     f"Log-log plot of the {len(a['points'])} test points with fitted exponent {a['fit']['n']:.2f}")
        secs.append(Section(str(len(secs) + 1), f'Gas well test {i}', paras, [tab], [fig]))
    secs.append(Section(str(len(secs) + 1), 'What this review does not establish',
                        ['It recomputes what the documents state from their own figures and says where those figures disagree. It does not measure the well, '
                         'it does not correct a document, and it does not say which of two disagreeing figures is right: that takes a new test or the '
                         "operator's own records. The survey's displacement is a bound, not a position; the open flow is an extrapolation of the test, "
                         'not a rate the well will make.']))
    return Document(title='Well Records Review', report_id=rid, front=front, sections=secs,
                    data={'well': well_id, 'completion': c, 'survey': s and {k: v for k, v in s.items() if k != 'stations'},
                          'gas_tests': [{k: v for k, v in g_.items() if k != 'svg'} for g_ in pg['gas_tests']]})


def write_report(ws, well_id: str, program_version: str, actor: str = 'system') -> dict:
    from .client_reports import write
    doc = build_report(ws, well_id, program_version)
    paths = write(doc, os.path.join(ws.reports_dir, 'wells', well_id), basename='records_review')
    ws.audit(actor, 'report.records_review', {'well': well_id, 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}
