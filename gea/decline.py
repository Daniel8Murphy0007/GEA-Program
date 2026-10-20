"""Decline curves: each well's production fitted by Arps's curves, forecast to its economic limit, and valued.

Arps ("Analysis of decline curves", Trans. AIME 160, 1945) describes a well's falling rate with three numbers: the
initial rate qi, the initial nominal decline Di and the exponent b - exponential (b = 0), hyperbolic (0 < b < 1, and
above 1 for the transient flow of tight wells) and harmonic (b = 1):

    q(t) = qi / (1 + b Di t)^(1/b)            q(t) = qi exp(-Di t) at b = 0

The modified hyperbolic (Robertson, "Generalized hyperbolic equation", SPE-18731, 1988) turns exponential where the
instantaneous decline Di / (1 + b Di t) falls to a terminal decline Dmin, so a b above 1 does not run to an unbounded
recovery. Declines are given as the industry quotes them: the effective annual decline (secant for the hyperbolic,
1 - (1 + b Di 365.25)^(-1/b); 1 - exp(-D 365.25) for the exponential), and the terminal decline as an effective
annual exponential one.

The fit is least squares in the logarithm of the rate, over every b from 0 to 2 by 0.01 and a fine grid of Di, qi in
closed form for each pair; over the months from the peak (or a declared start) with production days. The forecast
runs month by month to the economic limit - the month the net cash flow (the revenue at the net revenue interest less
the severance tax, less the operating cost) is no longer positive - or to 50 years; the estimated ultimate recovery is
the production to date plus the forecast. The value is the forecast's net cash flow discounted at the declared rate
(10 % by default) mid-month. The price is the operator's flat price, or the SEC's: the unweighted average of the
first-day-of-the-month prices of the 12 months before the as-of date (U.S. SEC, Modernization of Oil and Gas
Reporting, 2008: Regulation S-X Rule 4-10(a)(22)(v)), from the EIA series shipped with the program; gas is sold per Mcf
at the Henry Hub price per MMBtu times the declared heat content (1.0 MMBtu/Mcf unless declared). A forecast is not a
reserves report: nothing here is classified as reserves.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

DAYS_YEAR = 365.25


def rate(t, qi, Di, b, Dmin=None):
    """Arps rate at t (days); Di, Dmin nominal per day; with Dmin, the modified hyperbolic (exponential from where the
    instantaneous decline falls to Dmin)."""
    t = np.asarray(t, dtype=np.float64)
    if b <= 1e-9:
        return qi * np.exp(-Di * t)
    q = qi / (1.0 + b * Di * t) ** (1.0 / b)
    if Dmin and Dmin < Di:
        ts = (Di / Dmin - 1.0) / (b * Di)
        qs = qi / (1.0 + b * Di * ts) ** (1.0 / b)
        q = np.where(t <= ts, q, qs * np.exp(-Dmin * (t - ts)))
    return q


def cum(t, qi, Di, b, Dmin=None):
    t = np.asarray(t, dtype=np.float64)

    def hyp(tt):
        if b <= 1e-9:
            return qi / Di * (1.0 - np.exp(-Di * tt))
        if abs(b - 1.0) < 1e-9:
            return qi / Di * np.log(1.0 + Di * tt)
        return qi / ((1.0 - b) * Di) * (1.0 - (1.0 + b * Di * tt) ** (1.0 - 1.0 / b))
    if b > 1e-9 and Dmin and Dmin < Di:
        ts = (Di / Dmin - 1.0) / (b * Di)
        qs = qi / (1.0 + b * Di * ts) ** (1.0 / b)
        return np.where(t <= ts, hyp(t), hyp(ts) + qs / Dmin * (1.0 - np.exp(-Dmin * (t - ts))))
    return hyp(t)


def effective_annual(D, b):
    """The effective annual decline (secant) of a nominal daily decline."""
    if b <= 1e-9:
        return 1.0 - math.exp(-D * DAYS_YEAR)
    return 1.0 - (1.0 + b * D * DAYS_YEAR) ** (-1.0 / b)


def nominal_daily(De, b):
    """The nominal daily decline of an effective annual (secant) one."""
    if b <= 1e-9:
        return -math.log(1.0 - De) / DAYS_YEAR
    return ((1.0 - De) ** (-b) - 1.0) / (b * DAYS_YEAR)


def fit(t, q, b_max=2.0):
    """Least squares in log rate over b in [0, b_max] (0.01) and Di (log-spaced); qi in closed form for each pair."""
    t = np.asarray(t, dtype=np.float64); q = np.asarray(q, dtype=np.float64)
    ok = (q > 0) & np.isfinite(q) & np.isfinite(t)
    t, lq = t[ok], np.log(q[ok])
    best = None
    Ds = np.exp(np.linspace(math.log(1e-5), math.log(0.5), 400))
    for b in np.round(np.arange(0.0, b_max + 1e-9, 0.01), 2):
        if b == 0:
            g = -np.outer(Ds, t)
        else:
            g = -(1.0 / b) * np.log1p(b * np.outer(Ds, t))
        lqi = np.mean(lq[None, :] - g, axis=1)
        sse = np.sum((lq[None, :] - (lqi[:, None] + g)) ** 2, axis=1)
        j = int(np.argmin(sse))
        if best is None or sse[j] < best[0]:
            best = (float(sse[j]), float(math.exp(lqi[j])), float(Ds[j]), float(b))
    # refine Di around the best
    sse0, qi0, D0, b0 = best
    Ds2 = D0 * np.exp(np.linspace(-0.05, 0.05, 201))
    g = -np.outer(Ds2, t) if b0 == 0 else -(1.0 / b0) * np.log1p(b0 * np.outer(Ds2, t))
    lqi = np.mean(lq[None, :] - g, axis=1)
    sse = np.sum((lq[None, :] - (lqi[:, None] + g)) ** 2, axis=1)
    j = int(np.argmin(sse))
    n = len(t)
    return {'qi': float(math.exp(lqi[j])), 'Di': float(Ds2[j]), 'b': b0, 'rmse_log': float(math.sqrt(sse[j] / max(1, n))), 'points': n}


DEFAULTS = {'b_max': 2.0, 'terminal_decline': 0.08, 'start': None, 'oil_price': None, 'gas_price': None, 'price_basis': 'sec', 'nri': 0.80,
            'severance': 0.046, 'opex_month': 8000.0, 'discount': 0.10, 'years': 50.0, 'gas_mmbtu_per_mcf': 1.0, 'source': ''}
STREAMS = {'oil': ('oil', 'oil (bbl)', 'oil bbl', 'oil_bbl', 'oil volume', 'oil production', 'crude', 'condensate'),
           'gas': ('gas', 'gas (mcf)', 'gas mcf', 'gas_mcf', 'gas volume', 'gas production'),
           'water': ('water', 'water (bbl)', 'water bbl', 'water_bbl', 'water volume')}
DATE_NAMES = ('date', 'month', 'production month', 'prod month', 'period', 'production date')
DAYS_NAMES = ('days', 'days on', 'days produced', 'producing days', 'days online', 'on days')
NOT_A_MEASUREMENT = ['reserves: a forecast is not a reserves report, and nothing here is classified as proved or otherwise',
                     'the future price: the SEC average or the declared price is held flat for the life of the forecast',
                     'what the well will make: the fitted curve carries the past forward; a change of operation breaks it']


# -- the production table ---------------------------------------------------------------------------------------------------
def _norm(h: str) -> str:
    return re.sub(r'\s+', ' ', (h or '').strip().lower().replace('_', ' '))


def _month(s: str) -> date:
    s = (s or '').strip()
    for fmt in ('%Y-%m-%d', '%Y-%m', '%m/%d/%Y', '%m/%Y', '%Y/%m/%d', '%Y/%m', '%b %Y', '%B %Y', '%Y%m'):
        try:
            d = datetime.strptime(s[:10] if fmt.endswith('%d') else s, fmt).date()
            return date(d.year, d.month, 1)
        except ValueError:
            continue
    raise ValueError(f'{s!r} is not a month')


def read_production(content: bytes) -> List[dict]:
    """Monthly production: one row per month - the month, oil (bbl), gas (Mcf), water (bbl) and the days the well
    produced when given; columns found by their names."""
    from . import xlsx_read as XR
    text = XR.to_csv(content) if XR.is_xlsx(content) else content.decode('utf-8-sig', errors='replace')
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError('the production table has no rows')
    head = [_norm(h) for h in rows[0]]
    col = {}
    for j, h in enumerate(head):
        if h in DATE_NAMES and 'month' not in col:
            col['month'] = j
        elif h in DAYS_NAMES and 'days' not in col:
            col['days'] = j
        else:
            for k, names in STREAMS.items():
                if h in names and k not in col:
                    col[k] = j
    if 'month' not in col:
        raise ValueError(f"the production table needs a month column (found: {', '.join(rows[0])})")
    if not any(k in col for k in STREAMS):
        raise ValueError('the production table needs an oil, gas or water column')
    out, seen = [], set()
    for i, r in enumerate(rows[1:], start=2):
        g = lambda k: r[col[k]].strip().replace(',', '') if k in col and col[k] < len(r) else ''
        if not g('month'):
            continue
        try:
            m = _month(g('month'))
            rec = {'month': m.isoformat()[:7]}
            for k in STREAMS:
                v = g(k)
                rec[k] = float(v) if v else None
                if rec[k] is not None and rec[k] < 0:
                    raise ValueError(f'a negative {k} volume')
            dv = g('days')
            rec['days'] = float(dv) if dv else None
            if rec['days'] is not None and not 0 <= rec['days'] <= 31:
                raise ValueError('the producing days of a month are 0 to 31')
        except ValueError as e:
            raise ValueError(f'row {i}: {e}') from None
        if rec['month'] in seen:
            raise ValueError(f"row {i}: the month {rec['month']} is listed twice")
        seen.add(rec['month']); out.append(rec)
    out.sort(key=lambda x: x['month'])
    return out


def month_days(m: str) -> int:
    y, mo = int(m[:4]), int(m[5:7])
    return ((date(y + (mo == 12), mo % 12 + 1, 1)) - date(y, mo, 1)).days


def rates(prod: List[dict], stream: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, date]:
    """(t days from the first month's start to each month's middle, rate per producing day, volume, first month)."""
    m0 = date.fromisoformat(prod[0]['month'] + '-01')
    t, q, v = [], [], []
    for r in prod:
        vol = r.get(stream)
        if vol is None:
            continue
        md = month_days(r['month'])
        days = r['days'] if r.get('days') is not None else md
        d0 = (date.fromisoformat(r['month'] + '-01') - m0).days
        t.append(d0 + md / 2.0); v.append(vol)
        q.append(vol / days if days > 0 else np.nan)
    return np.asarray(t), np.asarray(q, dtype=np.float64), np.asarray(v, dtype=np.float64), m0


def sec_price(series_dates: List[str], series_values: List[float], as_of: date) -> Optional[dict]:
    """The unweighted average of the first-day-of-the-month prices of the 12 months before `as_of` (the price on the
    first day with a quote in each month)."""
    by_month: Dict[str, Tuple[str, float]] = {}
    for d_, v in zip(series_dates, series_values):
        k = d_[:7]
        if v is None or not math.isfinite(v):
            continue
        if k not in by_month or d_ < by_month[k][0]:
            by_month[k] = (d_, v)
    months = []
    y, m = as_of.year, as_of.month
    for _ in range(12):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append(f'{y:04d}-{m:02d}')
    got = [by_month[k] for k in months if k in by_month]
    if len(got) < 12:
        return None
    return {'price': round(sum(v for _, v in got) / 12.0, 4), 'months': [d_ for d_, _ in got][::-1]}


# -- the forecast and its value ----------------------------------------------------------------------------------------------
def analyse_stream(prod: List[dict], stream: str, cfg: dict) -> Optional[dict]:
    t, q, v, m0 = rates(prod, stream)
    if len(t) < 6 or not np.isfinite(q).any() or np.nansum(v) <= 0:
        return None
    okq = np.isfinite(q) & (q > 0)
    if cfg.get('start'):
        s0 = (date.fromisoformat(str(cfg['start'])[:7] + '-01') - m0).days
        sel = okq & (t >= s0)
    else:
        k = int(np.nanargmax(np.where(okq, q, -1)))
        sel = okq & (t >= t[k])
    if sel.sum() < 6:
        return None
    t0 = float(t[sel][0])
    f = fit(t[sel] - t0, q[sel], float(cfg.get('b_max') or 2.0))
    dmin = nominal_daily(float(cfg['terminal_decline']), 0.0) if cfg.get('terminal_decline') else None
    produced = float(np.nansum(v))
    last_m = date.fromisoformat(prod[-1]['month'] + '-01')
    return {'stream': stream, 'fit': f, 't0_days': t0, 'fit_from': (m0 + timedelta(days=t0)).isoformat()[:7], 'months_fitted': int(sel.sum()),
            'produced': round(produced, 1), 'last_month': prod[-1]['month'], 'Dmin': dmin,
            'De_annual': round(effective_annual(f['Di'], f['b']), 4), 'Dterm_annual': cfg.get('terminal_decline'),
            'history': {'t': [round(float(x), 1) for x in t], 'q': [None if not np.isfinite(x) else round(float(x), 3) for x in q], 'v': [round(float(x), 1) for x in v]},
            'last_t': float(t[-1]), 'm0': m0.isoformat()}


def forecast(streams: Dict[str, dict], cfg: dict, prices: Dict[str, float]) -> dict:
    """Month by month from the month after the last, each stream on its curve; the net cash flow and its value; stopped
    at the economic limit or the declared years."""
    any_s = next(iter(streams.values()))
    m0 = date.fromisoformat(any_s['m0'])
    last = date.fromisoformat(any_s['last_month'] + '-01')
    months, rows = [], []
    nri, sev, opex, disc = float(cfg['nri']), float(cfg['severance']), float(cfg['opex_month']), float(cfg['discount'])
    y, mo = last.year, last.month
    pv, cum_cf = 0.0, 0.0
    tot = {k: 0.0 for k in streams}
    limit_month = None
    for i in range(int(float(cfg['years']) * 12)):
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
        ms = f'{y:04d}-{mo:02d}'
        md = month_days(ms)
        a = (date(y, mo, 1) - m0).days
        vols = {}
        for k, s_ in streams.items():
            f = s_['fit']
            ta, tb = a - s_['t0_days'], a + md - s_['t0_days']
            vols[k] = float(cum(np.array([tb]), f['qi'], f['Di'], f['b'], s_['Dmin'])[0] - cum(np.array([max(ta, 0.0)]), f['qi'], f['Di'], f['b'], s_['Dmin'])[0])
        rev = vols.get('oil', 0.0) * prices.get('oil', 0.0) + vols.get('gas', 0.0) * prices.get('gas', 0.0) * float(cfg.get('gas_mmbtu_per_mcf') or 1.0)
        net = rev * nri * (1.0 - sev) - opex
        if net <= 0:                                      # the first month that does not pay: the economic limit
            limit_month = ms
            break
        dfac = (1.0 + disc) ** (-((i + 0.5) / 12.0))
        pv += net * dfac; cum_cf += net
        for k in vols:
            tot[k] += vols[k]
        rows.append({'month': ms, **{k: round(vols[k], 1) for k in vols}, 'net_usd': round(net, 0)})
    return {'months': rows, 'remaining': {k: round(v, 0) for k, v in tot.items()}, 'pv_usd': round(pv, 0), 'undiscounted_usd': round(cum_cf, 0),
            'limit_month': limit_month, 'end_month': rows[-1]['month'] if rows else None, 'stopped_by': 'the economic limit' if limit_month else f"the {cfg['years']:g}-year cap"}


def analyse(prod: List[dict], cfg: dict, prices: Dict[str, float]) -> dict:
    streams = {}
    for k in ('oil', 'gas'):
        s_ = analyse_stream(prod, k, cfg)
        if s_:
            streams[k] = s_
    if not streams:
        raise ValueError('fewer than six producing months to fit')
    fc = forecast(streams, cfg, prices)
    eur = {k: round(s_['produced'] + fc['remaining'].get(k, 0.0), 0) for k, s_ in streams.items()}
    for k, s_ in streams.items():                          # the curve for drawing: history months and the forecast
        f = s_['fit']
        tt = np.linspace(0.0, max(s_['last_t'] - s_['t0_days'], 1.0) + 30.4375 * len(fc['months']), 300)
        s_['curve'] = {'t': [round(float(x + s_['t0_days']), 1) for x in tt], 'q': [round(float(x), 3) for x in rate(tt, f['qi'], f['Di'], f['b'], s_['Dmin'])]}
    return {'streams': streams, 'forecast': fc, 'eur': eur, 'prices': prices, 'config': cfg}


# -- the workspace --------------------------------------------------------------------------------------------------------
def _dir(ws, *p) -> str:
    return os.path.join(ws.path, 'economics', 'production', *p)


def _load(ws) -> dict:
    try:
        with open(_dir(ws, 'production.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wells': {}}


def _save(ws, d: dict) -> None:
    os.makedirs(_dir(ws), exist_ok=True)
    with open(_dir(ws, 'production.json.tmp'), 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(_dir(ws, 'production.json.tmp'), _dir(ws, 'production.json'))


def well_config(ws, well_id: str) -> dict:
    w = (_load(ws).get('wells') or {}).get(well_id) or {}
    return {**DEFAULTS, **(w.get('config') or {})}


def attach(ws, well_id: str, filename: str, content: bytes, actor: str, source: str) -> dict:
    import hashlib
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    if not (source or '').strip():
        raise ValueError('say where the production came from (e.g. "RRC production data query, lease 12345" or "operator\'s monthly report")')
    prod = read_production(content)
    fn = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(filename)) or 'production.csv'
    os.makedirs(_dir(ws, well_id), exist_ok=True)
    with open(_dir(ws, well_id, fn), 'wb') as f:
        f.write(content)
    d = _load(ws)
    cur = (d.get('wells') or {}).get(well_id) or {}
    cur.update({'file': fn, 'sha256': hashlib.sha256(content).hexdigest(), 'source': source.strip(), 'months': len(prod), 'first': prod[0]['month'],
                'last': prod[-1]['month'], 'by': actor, 'utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    d.setdefault('wells', {})[well_id] = cur
    _save(ws, d)
    ws.audit(actor, 'production.attach', {'well': well_id, 'file': fn, 'months': len(prod), 'source': source.strip()})
    return {k: v for k, v in cur.items() if k != 'config'}


def remove(ws, well_id: str, actor: str) -> dict:
    d = _load(ws)
    cur = (d.get('wells') or {}).pop(well_id, None)
    if not cur or not cur.get('file'):
        raise KeyError(f'{well_id} has no production attached')
    _save(ws, d)
    ws.audit(actor, 'production.remove', {'well': well_id, 'file': cur['file'], 'sha256': cur['sha256']})
    return cur


LIMITS = {'b_max': (0.0, 3.0), 'terminal_decline': (0.0, 0.5), 'oil_price': (0.0, 1000.0), 'gas_price': (0.0, 100.0), 'nri': (0.0, 1.0),
          'severance': (0.0, 0.5), 'opex_month': (0.0, 1e7), 'discount': (0.0, 0.5), 'years': (1.0, 100.0), 'gas_mmbtu_per_mcf': (0.8, 1.6)}


def set_config(ws, actor: str, well_id: str, source: str = '', **cfg) -> dict:
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    d = _load(ws)
    cur = (d.setdefault('wells', {}).setdefault(well_id, {})).setdefault('config', {})
    changed = False
    for k, v in cfg.items():
        if k == 'price_basis' and v not in (None, ''):
            if v not in ('sec', 'flat'):
                raise ValueError('the price basis is sec (the 12-month first-of-month average) or flat (the declared prices)')
            cur[k] = v; changed = True
        elif k == 'start':
            if v in (None, ''):
                cur.pop('start', None)
            else:
                cur['start'] = _month(str(v)).isoformat()[:7]
            changed = True
        elif k in LIMITS and v not in (None, ''):
            x = float(v)
            lo, hi = LIMITS[k]
            if not (math.isfinite(x) and lo <= x <= hi):
                raise ValueError(f'{k} must be between {lo:g} and {hi:g}')
            cur[k] = x; changed = True
    if changed:
        if not (source or '').strip():
            raise ValueError('say where these terms came from (e.g. "lease terms, 2026 budget")')
        cur['source'] = source.strip()
    _save(ws, d)
    ws.audit(actor, 'production.terms', {'well': well_id, **cur})
    return well_config(ws, well_id)


def production(ws, well_id: str) -> List[dict]:
    w = (_load(ws).get('wells') or {}).get(well_id) or {}
    if not w.get('file'):
        return []
    with open(_dir(ws, well_id, w['file']), 'rb') as f:
        return read_production(f.read())


def prices_for(ws, cfg: dict, as_of: date) -> Tuple[Dict[str, float], dict]:
    from . import economics as EC
    out, how = {}, {}
    for k, sid in (('oil', 'wti'), ('gas', 'henry_hub')):
        if cfg.get('price_basis') == 'flat' and cfg.get(f'{k}_price') is not None:
            out[k] = float(cfg[f'{k}_price']); how[k] = f"{out[k]:g} USD/{'bbl' if k == 'oil' else 'MMBtu'} declared ({cfg.get('source') or ''})"
            continue
        try:
            d_, v_, meta = EC.read_series(ws, sid)
        except Exception:
            d_, v_, meta = [], [], {}
        sp = sec_price(d_, v_, as_of)
        if sp:
            out[k] = sp['price']; how[k] = f"{sp['price']:.2f} USD/{'bbl' if k == 'oil' else 'MMBtu'}: the SEC average of the first-of-month {meta.get('name', sid)} prices, {sp['months'][0][:7]} to {sp['months'][-1][:7]}"
        elif cfg.get(f'{k}_price') is not None:
            out[k] = float(cfg[f'{k}_price']); how[k] = f"{out[k]:g} declared (the price series does not cover the 12 months before {as_of.isoformat()})"
        else:
            out[k] = 0.0; how[k] = f'no price: the series does not cover the 12 months before {as_of.isoformat()} and none is declared'
    return out, how


def page(ws, well: Optional[str] = None, as_of: Optional[str] = None) -> dict:
    d = _load(ws)
    wl = [{'id': w['id'], 'display': w.get('display') or w['id'], 'production': {k: v for k, v in ((d.get('wells') or {}).get(w['id']) or {}).items() if k != 'config'} or None,
           'config': well_config(ws, w['id'])} for w in ws.wells()]
    choice = well if any(w['id'] == well for w in wl) else next((w['id'] for w in wl if (w['production'] or {}).get('file')), wl[0]['id'] if wl else None)
    out = {'wells': wl, 'well': choice, 'result': None, 'detail': '', 'defaults': DEFAULTS, 'not_a_measurement': NOT_A_MEASUREMENT}
    if choice is None:
        out['detail'] = 'no wells yet'
        return out
    prod = production(ws, choice)
    if not prod:
        out['detail'] = 'attach the well\'s monthly production'
        return out
    cfg = well_config(ws, choice)
    ao = date.fromisoformat(as_of) if as_of else date.fromisoformat(prod[-1]['month'] + '-01') + timedelta(days=32)
    ao = date(ao.year, ao.month, 1)
    prices, how = prices_for(ws, cfg, ao)
    try:
        r = analyse(prod, cfg, prices)
    except ValueError as e:
        out['detail'] = str(e)
        return out
    r.update({'as_of': ao.isoformat(), 'price_how': how, 'well': choice, 'display': next(w['display'] for w in wl if w['id'] == choice),
              'source': ((d.get('wells') or {}).get(choice) or {}).get('source', ''), 'months': len(prod)})
    out['result'] = r
    return out


def text(p: dict) -> str:
    r = p.get('result')
    if not r:
        return f"Decline curves: {p.get('detail', '')}"
    L = [f"Decline curves - {r['display']}: {r['months']} months to {next(iter(r['streams'].values()))['last_month']}, as of {r['as_of']}"]
    for k, s_ in r['streams'].items():
        f = s_['fit']
        u = 'bbl/d' if k == 'oil' else 'Mcf/d'
        L.append(f"  {k}: qi {f['qi']:,.1f} {u}, b {f['b']:.2f}, Di {s_['De_annual'] * 100:.1f} %/yr effective, terminal {s_['Dterm_annual'] * 100 if s_['Dterm_annual'] else 0:.0f} %/yr; "
                 f"fitted over {s_['months_fitted']} months from {s_['fit_from']} (rms {f['rmse_log']:.3f} in log rate); produced {s_['produced']:,.0f}, EUR {r['eur'][k]:,.0f}")
    fc = r['forecast']
    if fc['end_month']:
        L.append(f"  forecast to {fc['end_month']} ({fc['stopped_by']}): PV{int(r['config']['discount'] * 100)} {fc['pv_usd']:,.0f} USD, undiscounted {fc['undiscounted_usd']:,.0f} USD")
    else:
        L.append(f"  no forecast: {fc['limit_month']}, the first month after the last, does not pay (the economic limit)")
    for k, h in r['price_how'].items():
        L.append(f"  {k} price: {h}")
    return '\n'.join(L)


def build_report(ws, version: str, well: Optional[str] = None):
    from . import client_reports as CR
    p = page(ws, well)
    r = p['result']
    if not r:
        raise ValueError(p.get('detail') or 'nothing to fit')
    now = datetime.now(timezone.utc)
    rid = f"DCA-{now.strftime('%Y%m%dT%H%M%SZ')}"
    fc = r['forecast']
    front = [['Report ID', rid], ['Well', r['display']], ['Generated (UTC)', now.strftime('%Y-%m-%dT%H:%M:%SZ')], ['Program', f'Downhole Gauge Monitoring build {version}'],
             ['As of', r['as_of']], ['EUR', ', '.join(f"{k} {v:,.0f} {'bbl' if k == 'oil' else 'Mcf'}" for k, v in r['eur'].items())],
             [f"PV{int(r['config']['discount'] * 100)}", f"{fc['pv_usd']:,.0f} USD to {fc['end_month']} ({fc['stopped_by']})" if fc['end_month'] else 'none: the next month does not pay']]
    rows = []
    for k, s_ in r['streams'].items():
        f = s_['fit']
        rows.append([k, f"{f['qi']:,.1f}", f"{f['b']:.2f}", f"{s_['De_annual'] * 100:.1f}", f"{(s_['Dterm_annual'] or 0) * 100:.0f}", s_['fit_from'], str(s_['months_fitted']),
                     f"{f['rmse_log']:.3f}", f"{s_['produced']:,.0f}", f"{r['eur'][k]:,.0f}"])
    s1 = CR.Section('1', 'The curves', [f"Arps's curves, modified hyperbolic to the terminal decline, fitted by least squares in log rate. Production: {r['source']}."], [])
    s1.tables.append(CR.Table(['Stream', 'qi (per day)', 'b', 'Di (%/yr eff.)', 'Terminal (%/yr)', 'From', 'Months', 'RMS (log)', 'Produced', 'EUR'], rows))
    c = r['config']
    s2 = CR.Section('2', 'The forecast and its value', [
        f"Net revenue interest {c['nri']:g}, severance {c['severance'] * 100:g} %, operating cost {c['opex_month']:,.0f} USD a month, discounted at {c['discount'] * 100:g} % a year mid-month. "
        + ' '.join(f"{k.capitalize()}: {h}." for k, h in r['price_how'].items()) + f" Terms: {c.get('source') or 'the defaults'}."], [])
    k_ = max(1, len(fc['months']) // 40)
    s2.tables.append(CR.Table(['Month'] + [s.capitalize() for s in r['streams']] + ['Net (USD)'],
                              [[m['month']] + [f"{m[s]:,.0f}" for s in r['streams']] + [f"{m['net_usd']:,.0f}"] for m in fc['months'][::k_]]))
    s3 = CR.Section('3', 'What this will not call a measurement', ['; '.join(p['not_a_measurement']) + '.'], [])
    return CR.Document(f"Decline curves - {r['display']}", rid, front, [s1, s2, s3],
                       footer='Arps, Trans. AIME 160 (1945); Robertson, SPE-18731 (1988); SEC Regulation S-X Rule 4-10(a)(22)(v) for the 12-month average price.',
                       data={'report_id': rid, **{k: v for k, v in r.items() if k != 'streams'},
                             'streams': {k: {kk: vv for kk, vv in s_.items() if kk not in ('history', 'curve')} for k, s_ in r['streams'].items()}})


def write_report(ws, version: str, well: Optional[str] = None, actor: str = 'system') -> dict:
    from . import client_reports as CR
    doc = build_report(ws, version, well)
    dd = os.path.join(ws.reports_dir, 'wells', doc.data['well'])
    os.makedirs(dd, exist_ok=True)
    paths = CR.write(doc, dd, 'decline_curves')
    ws.audit(actor, 'report.decline_curves', {'well': doc.data['well'], 'report_id': doc.report_id})
    return {'report_id': doc.report_id, 'paths': paths}


def selftest_files(tmp: str, seed: int = 11) -> dict:
    """A well's monthly production built on known curves: oil from 900 bbl/d with b 1.1, 65 % a year effective decline
    and an 8 % terminal decline; gas at a gas-oil ratio rising from 1.2 to 2 Mcf/bbl; four months shut in for part of
    the month (the days say so); 3 % noise; 48 months from 2022-01."""
    rng = np.random.default_rng(seed)
    qi, De, b, Dt = 900.0, 0.65, 1.1, 0.08
    Di, Dmin = nominal_daily(De, b), nominal_daily(Dt, 0.0)
    rows, t0 = [], date(2022, 1, 1)
    for i in range(48):
        y, m = 2022 + i // 12, i % 12 + 1
        ms = f'{y:04d}-{m:02d}'
        md = month_days(ms)
        days = md - (10 if i in (7, 19, 30, 41) else 0)
        a = (date(y, m, 1) - t0).days
        # a shut-in month produces on its producing days at the rate of the curve's time (time is calendar time)
        q = float(rate(np.array([a + md / 2.0]), qi, Di, b, Dmin)[0]) * math.exp(rng.normal(0.0, 0.03))
        oil = q * days
        gor = 1.2 + 0.8 * i / 47.0
        rows.append([ms, round(oil, 1), round(oil * gor, 1), round(oil * (0.5 + i / 60.0), 1), days])
    p = os.path.join(tmp, 'production_selftest.csv')
    os.makedirs(tmp, exist_ok=True)
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Month', 'Oil (bbl)', 'Gas (Mcf)', 'Water (bbl)', 'Days'])
        w.writerows(rows)
    return {'path': p, 'qi': qi, 'De': De, 'b': b, 'Dterm': Dt}
