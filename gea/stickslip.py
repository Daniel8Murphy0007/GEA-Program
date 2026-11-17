# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""stickslip - what the drill string's vibration looks like at the surface, read from the torque and the hook load
the recorder already keeps: the torque winding up and letting go (stick-slip), and the hook load jumping from one
second to the next (the bit bouncing). Two rules, replayed over the whole of a well's record with the others on the
Drilling alerts page.

The record is read in blocks of two minutes of rotary drilling on bottom (95 % of a block's samples or more; a gap in
the clock leaves the block out).

- STICK-SLIP. The bit stops, the string winds up, the bit breaks loose: at the surface the torque climbs and falls
  with one period, a few seconds long and longer the deeper the bit. In each block the torque has its straight-line
  trend taken out and is transformed (Hann window); the SWING is the amplitude of what lies at periods of 3 to 15
  seconds - root two times its RMS, the amplitude of a sine of that energy - as a fraction of the block's median
  torque, the same measure the Drill string page calls the oscillation index. The block is FLAGGED when the swing is
  10 % or more AND one period carries it: the strongest period (with its second harmonic, where the band holds it -
  a saw-tooth has one) holds 40 % or more of everything the torque did about its trend, and is a peak inside the
  band, not the shoulder of something slower or faster. Three things are set aside and counted, not flagged: a
  swing as large with no period (weight being fed, a noisy sensor, or a motion faster than the record holds); a
  block whose torque touches zero every cycle (its 5th percentile 15 % of its 95th or less: the pipe rocked left
  and right while sliding); and a period that is the rotary's own speed, or two or three times it, folded onto a
  slower one by the sampling (a bent joint turns once a turn, a three-cone bit three times: real, but not the
  string winding up).
- BIT BOUNCE. The hook load has its nine-second running mean taken out, and the BOUNCE is half the spread between
  the 10th and 90th percentile of what is left: how far the hook load jumps about its own slow level. The block is
  FLAGGED at 5 klbf or more, and a quarter of the weight on bit or more where the record has the weight on bit.

Two flagged blocks running open an event; it ends when three blocks running are quiet, when the bit has been off
bottom for half an hour, or at the end of the record. The numbers 10 %, 5 klbf and a quarter are settings, kept with
the other drilling alerts' (`records/dysfunction.json`).

Both rules need a record every two seconds or faster. Sampled once a second, a swing faster than three seconds a
cycle is blurred or folded onto a slower one - which is most stick-slip above about 4,000 ft, where the string is
short and its period with it: what is read is a floor, and a quiet record is not a quiet string.

What it will not call a measurement: stick-slip - a swing in the SURFACE torque; what the bit's speed did is
measured only by a tool at the bit, and a top drive that damps the torque (a soft-torque system) hides the swing at
the surface while the bit still sticks; bit bounce - the hook load at the deadline, through a mile or two of steel
and the mud, so a deep bit can bounce with nothing showing; sideways vibration and whirl - they do not reach the
surface sensors at all, and no rule here speaks of them.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

BLOCK_S = 120.0
MAX_CADENCE_S = 2.0
PERIOD_S = (3.0, 15.0)
MIN_SAMPLES_A_CYCLE = 3.0
PEAK_SHARE = 0.4
ROCK_RATIO = 0.15
BLOCK_SHARE = 0.95
OPEN_BLOCKS, CLOSE_BLOCKS = 2, 3
GAP_S = 1800.0
FAST_S = 9.0
TURN_HARMONICS = (1, 2, 3)
TURN_BINS, TURN_RPM = 1.5, 0.25
DEFAULTS = {'stickslip_swing': 0.10, 'bounce_min_klbf': 5.0, 'bounce_share': 0.25}
BOUNDS = {'stickslip_swing': (0.03, 1.0), 'bounce_min_klbf': (1.0, 100.0), 'bounce_share': (0.0, 2.0)}
WORDS = {'stickslip_swing': 'the swing of the surface torque about its trend that flags stick-slip, as a fraction of the torque',
         'bounce_min_klbf': 'the least jump of the hook load about its own slow level that flags a bouncing bit, in klbf',
         'bounce_share': 'and the least share of the weight on bit'}
TORQUE_NAMES = ('convertible torque', 'rotary torque', 'top drive torque', 'surface torque')
NOT_A_MEASUREMENT = ['stick-slip: a swing in the surface torque - the bit\'s own speed is measured only by a tool at the bit, and a top drive that damps the torque hides the swing',
                     'bit bounce: the hook load at the deadline, through the whole string and the mud - a deep bit can bounce with nothing showing',
                     'sideways vibration and whirl: they do not reach the surface sensors, and no rule here speaks of them',
                     'a quiet record: sampled once a second, a swing faster than three seconds a cycle is blurred - what is read is a floor']


def _fill(a: np.ndarray) -> np.ndarray:
    """The channel with its missing samples joined across (a recorder drops the odd one)."""
    a = np.asarray(a, dtype=np.float64)
    ok = np.isfinite(a)
    if ok.all() or not ok.any():
        return np.nan_to_num(a)
    i = np.arange(len(a))
    return np.interp(i, i[ok], a[ok])


def _blocks(t: np.ndarray, sel: np.ndarray, cad: float):
    """The record cut into runs of BLOCK_S by its samples: the first sample of each block worth judging (its clock
    unbroken, `sel` true on 95 % of it or more) and the samples in a block."""
    n = int(round(BLOCK_S / cad))
    m = len(t) // n
    if m == 0:
        return np.zeros(0, np.int64), n
    first = np.arange(m, dtype=np.int64) * n
    span = t[first + n - 1] - t[first]
    good = sel[:m * n].reshape(m, n).sum(axis=1)
    keep = (span <= BLOCK_S * 1.05) & (good >= BLOCK_SHARE * n)
    return first[keep], n


def _rows(a: np.ndarray, first: np.ndarray, n: int) -> np.ndarray:
    return a[first[:, None] + np.arange(n)[None, :]]


def _events(kind: str, rows: List[dict], t_end: float, fields) -> List[dict]:
    """Flagged blocks into events: two running open one, three quiet running end it, so does half an hour off bottom."""
    events: List[dict] = []
    ev: Optional[dict] = None
    run: List[dict] = []
    quiet = 0
    prev_end = None

    def close(why: str):
        nonlocal ev
        if ev is not None:
            ev['ended'] = why
            fields(ev)
            events.append(ev)
        ev = None

    for r in rows:
        if prev_end is not None and r['t'] - prev_end > GAP_S:
            close('the bit was off bottom for half an hour or more'); run, quiet = [], 0
        prev_end = r['t_end']
        if ev is None:
            run = (run + [r]) if r['flag'] else []
            if len(run) >= OPEN_BLOCKS:
                ev = {'kind': kind, 't': run[0]['t'], 't_last': r['t_end'], 'depth_ft': run[0]['depth_ft'], 'depth_to_ft': r['depth_ft'], '_rows': list(run), 'ended': None}
                run, quiet = [], 0
            continue
        if r['flag']:
            ev['_rows'].append(r); ev['t_last'] = r['t_end']; ev['depth_to_ft'] = r['depth_ft']; quiet = 0
        else:
            quiet += 1
            if quiet >= CLOSE_BLOCKS:
                close('it died away'); run, quiet = [], 0
    if ev is not None:
        if t_end - ev['t_last'] <= GAP_S:
            ev['open'] = True
            close('open at the end of the record')
        else:
            close('the bit was off bottom for half an hour or more')
    return events


def _med(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.median(xs)) if xs else None


def stickslip(t: np.ndarray, torque: Optional[np.ndarray], rotary: np.ndarray, hole: Optional[np.ndarray], rpm: Optional[np.ndarray], P: dict) -> dict:
    """The surface torque's swing, block by block of rotary drilling. `rotary`: the samples in rotary drilling on bottom."""
    if torque is None:
        return {'status': 'NO CHANNELS', 'events': [], 'why': 'needs the surface torque (a channel named Convertible Torque or Rotary Torque)'}
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    if cad > MAX_CADENCE_S:
        return {'status': 'TOO COARSE', 'events': [], 'why': f'the record is every {cad:g} s; a stick-slip cycle is a few seconds long, so this needs every {MAX_CADENCE_S:g} s or faster'}
    fin = np.isfinite(torque)
    first, n = _blocks(t, rotary & fin, cad)
    out = {'status': 'OK', 'events': [], 'cadence_s': cad, 'block_s': BLOCK_S, 'blocks': int(len(first)), 'hours': round(len(first) * BLOCK_S / 3600.0, 1),
           'flagged_min': 0.0, 'rocked_min': 0.0, 'turn_min': 0.0, 'no_period_min': 0.0, 'series': []}
    if not len(first):
        out.update(status='NO ROTARY DRILLING', why='no two minutes of unbroken rotary drilling on bottom in the record')
        return out
    Y = _rows(_fill(torque), first, n)
    med = np.median(Y, axis=1)
    lo, hi = np.percentile(Y, [5, 95], axis=1)
    x = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    res = Y - Y.mean(axis=1, keepdims=True)
    res = res - ((res @ x) / float(x @ x))[:, None] * x[None, :]
    var = res.var(axis=1)
    S = np.abs(np.fft.rfft(res * np.hanning(n)[None, :], axis=1)) ** 2
    k = np.arange(S.shape[1])
    with np.errstate(divide='ignore'):
        per = np.where(k > 0, n * cad / np.maximum(k, 1), np.inf)
    band = (per >= max(PERIOD_S[0], MIN_SAMPLES_A_CYCLE * cad)) & (per <= PERIOD_S[1])
    kb = np.flatnonzero(band)
    tot = S[:, 1:].sum(axis=1)
    tot = np.where(tot > 0, tot, 1.0)
    ks = kb[np.argmax(S[:, kb], axis=1)]
    rr = np.arange(len(first))
    near = lambda kk: sum(np.where((kk + d >= 1) & (kk + d < S.shape[1]), S[rr, np.clip(kk + d, 0, S.shape[1] - 1)], 0.0) for d in (-1, 0, 1))
    share = (near(ks) + np.where(2 * ks <= kb[-1], near(np.minimum(2 * ks, S.shape[1] - 2)), 0.0)) / tot   # the second harmonic only where the band holds it
    edge = (((ks == kb[0]) & (S[rr, np.maximum(ks - 1, 0)] >= S[rr, ks]))               # the shoulder of something slower than the band, not a period in it
            | ((ks == kb[-1]) & (S[rr, np.minimum(ks + 1, S.shape[1] - 1)] >= S[rr, ks])))  # or of something faster
    rpm_m = np.median(_rows(np.nan_to_num(rpm), first, n), axis=1) if rpm is not None else None
    turn = np.zeros(len(first), bool)
    if rpm_m is not None:                                                               # the rotary's own speed, or two or three times it, folded by the sampling
        f_seen = ks / (n * cad)
        for h in TURN_HARMONICS:
            f = h * rpm_m / 60.0 * cad                                                  # in cycles a sample
            folded = np.abs(f - np.round(f)) / cad
            turn |= (rpm_m > 0) & (np.abs(folded - f_seen) <= TURN_BINS / (n * cad) + h * TURN_RPM / 60.0)
    with np.errstate(divide='ignore', invalid='ignore'):
        swing = np.where(med > 0, np.sqrt(2.0 * var * S[:, kb].sum(axis=1) / tot) / med, 0.0)
        rocked = (hi > 0) & (lo <= ROCK_RATIO * hi) & (np.sqrt(2.0 * var) >= 0.5 * np.maximum(med, 1e-9))
    big = (med > 0) & (swing >= P['stickslip_swing'])
    one = big & ~rocked & ~edge & (share >= PEAK_SHARE)
    flag = one & ~turn
    period = n * cad / ks
    hole0 = np.nan_to_num(hole) if hole is not None else None
    rows = []
    for j in range(len(first)):
        a = int(first[j])
        rows.append({'t': float(t[a]), 't_end': float(t[a + n - 1]) + cad, 'depth_ft': round(float(hole0[a]), 1) if hole0 is not None else None, 'flag': bool(flag[j]),
                     'swing': float(swing[j]), 'period_s': float(period[j]), 'torque': float(med[j]), 'rpm': float(rpm_m[j]) if rpm_m is not None else None})
    out['flagged_min'] = round(float(flag.sum()) * BLOCK_S / 60.0, 0)
    out['rocked_min'] = round(float((rocked & (med > 0)).sum()) * BLOCK_S / 60.0, 0)
    out['turn_min'] = round(float((one & turn).sum()) * BLOCK_S / 60.0, 0)
    out['no_period_min'] = round(float((big & ~rocked & ~one).sum()) * BLOCK_S / 60.0, 0)

    def fields(ev: dict):
        rs_ = ev.pop('_rows')
        ev['minutes'] = round(len(rs_) * BLOCK_S / 60.0, 0)
        ev['swing'] = round(max(r['swing'] for r in rs_), 3)
        ev['swing_median'] = round(float(np.median([r['swing'] for r in rs_])), 3)
        ev['period_s'] = round(float(np.median([r['period_s'] for r in rs_])), 1)
        ev['torque'] = round(float(np.median([r['torque'] for r in rs_])), 2)
        m = _med([r['rpm'] for r in rs_])
        ev['rpm'] = round(m, 0) if m is not None else None
    out['events'] = _events('stickslip', rows, float(t[-1]), fields)
    # the page's chart: the largest swing of each 100 ft of hole, and whether a block there was flagged
    if hole0 is not None:
        d = np.asarray([r['depth_ft'] for r in rows]); b = np.floor(d / 100.0)
        for u in np.unique(b):
            sel = np.flatnonzero(b == u)
            j = sel[np.argmax(swing[sel])]
            out['series'].append([round(float(d[j]), 0), round(float(swing[j]), 3), bool(flag[sel].any())])
    return out


def bounce(t: np.ndarray, hookload: Optional[np.ndarray], rotary: np.ndarray, hole: Optional[np.ndarray], wob: Optional[np.ndarray], P: dict) -> dict:
    """The hook load's jump about its own slow level, block by block of rotary drilling."""
    if hookload is None:
        return {'status': 'NO CHANNELS', 'events': [], 'why': 'needs the hook load'}
    cad = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    if cad > MAX_CADENCE_S:
        return {'status': 'TOO COARSE', 'events': [], 'why': f'the record is every {cad:g} s; a bouncing bit moves the hook load from one second to the next, so this needs every {MAX_CADENCE_S:g} s or faster'}
    first, n = _blocks(t, rotary & np.isfinite(hookload), cad)
    out = {'status': 'OK', 'events': [], 'cadence_s': cad, 'block_s': BLOCK_S, 'blocks': int(len(first)), 'hours': round(len(first) * BLOCK_S / 3600.0, 1), 'flagged_min': 0.0,
           'with_wob': wob is not None, 'series': []}
    if not len(first):
        out.update(status='NO ROTARY DRILLING', why='no two minutes of unbroken rotary drilling on bottom in the record')
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    half = max(1, int(round(FAST_S / cad)) // 2)
    hf = _fill(hookload)
    amp = np.zeros(len(first))
    for a in range(0, len(first), 2000):                       # in pieces: the windows of every block at once would be a large array
        H = _rows(hf, first[a:a + 2000], n)
        slow = sliding_window_view(np.pad(H, ((0, 0), (half, half)), mode='edge'), 2 * half + 1, axis=1).mean(axis=2)
        p10, p90 = np.percentile(H - slow, [10, 90], axis=1)
        amp[a:a + 2000] = (p90 - p10) / 2.0
    wob_m = np.median(_rows(_fill(wob), first, n), axis=1) if wob is not None else None
    lim = np.maximum(P['bounce_min_klbf'], P['bounce_share'] * np.maximum(wob_m, 0.0)) if wob_m is not None else np.full(len(first), P['bounce_min_klbf'])
    flag = amp >= lim
    hole0 = np.nan_to_num(hole) if hole is not None else None
    rows = []
    for j in range(len(first)):
        a = int(first[j])
        rows.append({'t': float(t[a]), 't_end': float(t[a + n - 1]) + cad, 'depth_ft': round(float(hole0[a]), 1) if hole0 is not None else None, 'flag': bool(flag[j]),
                     'amp': float(amp[j]), 'wob': float(wob_m[j]) if wob_m is not None else None})
    out['flagged_min'] = round(float(flag.sum()) * BLOCK_S / 60.0, 0)

    def fields(ev: dict):
        rs_ = ev.pop('_rows')
        ev['minutes'] = round(len(rs_) * BLOCK_S / 60.0, 0)
        ev['bounce_klbf'] = round(max(r['amp'] for r in rs_), 1)
        m = _med([r['wob'] for r in rs_])
        ev['wob_klbf'] = round(m, 1) if m is not None else None
    out['events'] = _events('bounce', rows, float(t[-1]), fields)
    if hole0 is not None:
        d = np.asarray([r['depth_ft'] for r in rows]); b = np.floor(d / 100.0)
        for u in np.unique(b):
            sel = np.flatnonzero(b == u)
            j = sel[np.argmax(amp[sel])]
            out['series'].append([round(float(d[j]), 0), round(float(amp[j]), 2), bool(flag[sel].any())])
    return out


def describe(e: dict) -> str:
    if e['kind'] == 'stickslip':
        return (f"surface torque swinging {e['swing'] * 100:.0f}% of its {e['torque']:g} every {e['period_s']:g} s for {e['minutes']:g} min from {e['depth_ft']:,.0f} ft"
                + (f" at {e['rpm']:g} rpm" if e.get('rpm') is not None else ''))
    return (f"hook load jumping {e['bounce_klbf']:g} klbf about its own level for {e['minutes']:g} min from {e['depth_ft']:,.0f} ft"
            + (f", on {e['wob_klbf']:g} klbf of weight on bit" if e.get('wob_klbf') is not None else ''))


# --------------------------------------------------------------------------------------------------------------
# the made record and the self-test
# --------------------------------------------------------------------------------------------------------------
MADE_PERIOD_S = 7.0
MADE_ROCK_S = 37.3


def _stands(drilling: np.ndarray) -> List[tuple]:
    d = np.r_[False, np.asarray(drilling, dtype=bool), False]
    e = np.flatnonzero(d[1:] != d[:-1])
    return [(int(a), int(b)) for a, b in zip(e[::2], e[1::2]) if b - a >= 600]


def add_swings(rec: dict, seed: int = 11, at_end: bool = False) -> dict:
    """A labelled drilling record (dysfunction.synthetic_record) given a surface torque and a weight on bit: 10
    kft.lbf while the string turns, a saw-tooth of 4 kft.lbf every seven seconds through the third and fourth runs of
    drilling (stick-slip), the fifth with the torque run to zero and back every 18.65 s (the pipe rocked), and the hook
    load jumping 8 klbf either way from second to second through the eighth (the bit bouncing). A run of drilling is a
    stand, or the part of one either side of a stall. `at_end`: the saw-tooth also over the last stretch of drilling,
    to the end of the record. SIMULATION."""
    rng = np.random.default_rng(seed)
    ch = {k: np.array(v, dtype=np.float64) for k, v in rec['ch'].items()}
    t = np.asarray(rec['t'], dtype=np.float64)
    n = len(t)
    tq = np.where(ch['rpm'] > 0, 10.0, 0.0) + np.where(ch['rpm'] > 0, rng.normal(0, 0.12, n), 0.0)
    st = _stands(rec['drilling'])
    saw = [st[2], st[3]] + ([st[-1]] if at_end else [])
    for a, b in saw:
        tq[a:b] = 8.0 + 4.0 * ((t[a:b] % MADE_PERIOD_S) / MADE_PERIOD_S) + rng.normal(0, 0.12, b - a)
    a, b = st[4]
    tq[a:b] = np.abs(5.0 * np.sin(2.0 * np.pi * t[a:b] / MADE_ROCK_S)) + rng.normal(0, 0.05, b - a)
    a, b = st[7]
    ch['hookload'][a:b] += rng.choice([-8.0, 8.0], b - a)
    ch['torque'] = np.maximum(tq, 0.0)
    ch['wob'] = np.where(rec['drilling'], 20.0, 0.0)
    return {**rec, 'ch': ch, 'truth': {**rec.get('truth', {}), 'stickslip_stands': [2, 3] + ([len(st) - 1] if at_end else []), 'rocked_stand': 4, 'bounce_stand': 7,
                                       'stickslip_t': [float(t[x[0]]) for x in saw], 'bounce_t': float(t[st[7][0]])}}


def selftest() -> dict:
    checks = []
    rng = np.random.default_rng(3)
    n = 8 * 3600
    t = np.arange(n, dtype=np.float64)
    tq = 10.0 + rng.normal(0, 0.15, n)
    rpm = np.full(n, 60.0)
    part = lambda h: slice(int(h * 3600), int(h * 3600) + 1800)
    s1 = part(1); tq[s1] = 8.0 + 4.0 * ((t[s1] % 9.0) / 9.0) + rng.normal(0, 0.15, 1800)              # a saw-tooth every 9 s: stick-slip
    s2 = part(2); tq[s2] += 2.0 * np.sin(2.0 * np.pi * t[s2] / 50.0)                                   # weight fed in a 50 s cycle: slower than the band
    s3 = part(3); tq[s3] += rng.normal(0, 2.5, 1800)                                                  # as large, with no period
    s4 = part(4); tq[s4] = np.abs(5.0 * np.sin(2.0 * np.pi * t[s4] / MADE_ROCK_S)) + rng.normal(0, 0.1, 1800)   # the pipe rocked
    s5 = part(5); rpm[s5] = 69.0; tq[s5] += 2.5 * np.sin(2.0 * np.pi * (69.0 / 60.0) * t[s5])          # once a turn at 69 rpm: 1.15 Hz, folded by the one-second sampling onto 6.7 s
    hl = 200.0 + rng.normal(0, 0.4, n); wob = np.full(n, 30.0)
    s6 = part(6); hl[s6] += rng.choice([-9.0, 9.0], 1800)                                             # the bit bouncing
    s7 = part(7); hl[s7] += 12.0 * np.sin(2.0 * np.pi * t[s7] / 60.0)                                 # the driller working the weight, a minute a cycle
    rot = np.ones(n, bool); hole = 5000.0 + t / 60.0
    P = dict(DEFAULTS)
    ss = stickslip(t, tq, rot, hole, rpm, P); bo = bounce(t, hl, rot, hole, wob, P)
    ev = ss['events']
    checks.append({'check': 'a saw-tooth of 4 on 10 every nine seconds for half an hour: one stick-slip event, its period nine seconds, its swing a sixth of the torque',
                   'value': [(e['t'], e['t_last'], e['minutes'], e['swing'], e['period_s'], e['ended']) for e in ev],
                   'pass': len(ev) == 1 and ev[0]['t'] == 3600.0 and ev[0]['t_last'] == 5400.0 and abs(ev[0]['period_s'] - 9.0) <= 0.5 and 0.12 <= ev[0]['swing'] <= 0.20 and ev[0]['ended'] == 'it died away'})
    checks.append({'check': 'set aside and counted: the pipe rocked through zero, a swing at the rotary\'s own speed folded by the sampling, and a swing as large with no period - none of them an event',
                   'value': {k: ss[k] for k in ('flagged_min', 'rocked_min', 'turn_min', 'no_period_min')},
                   'pass': ss['flagged_min'] == 30.0 and ss['rocked_min'] == 30.0 and ss['turn_min'] == 30.0 and ss['no_period_min'] >= 28.0})
    eb = bo['events']
    checks.append({'check': 'the hook load jumping 9 klbf either way from second to second: one bit-bounce event; the weight worked slowly a minute a cycle is not one',
                   'value': [(e['t'], e['t_last'], e['minutes'], e['bounce_klbf']) for e in eb],
                   'pass': len(eb) == 1 and eb[0]['t'] == 6 * 3600.0 and eb[0]['t_last'] == 6 * 3600.0 + 1800.0 and 7.0 <= eb[0]['bounce_klbf'] <= 11.0})
    strict = stickslip(t, tq, rot, hole, rpm, {**P, 'stickslip_swing': 0.5}); heavy = bounce(t, hl, rot, hole, np.full(n, 60.0), P)
    checks.append({'check': 'the settings are the rule: at a swing of 50 % no stick-slip, and on 60 klbf of weight on bit a 9 klbf jump is under a quarter of it', 'value': [len(strict['events']), len(heavy['events'])],
                   'pass': not strict['events'] and not heavy['events']})
    c10 = stickslip(t[::10], tq[::10], rot[::10], hole[::10], rpm[::10], P); b10 = bounce(t[::10], hl[::10], rot[::10], hole[::10], wob[::10], P)
    checks.append({'check': 'a record every ten seconds is not judged, and says so', 'value': [c10['status'], b10['status']], 'pass': c10['status'] == b10['status'] == 'TOO COARSE' and not c10['events'] and not b10['events']})
    c2 = stickslip(t[::2], tq[::2], rot[::2], hole[::2], rpm[::2], P)
    checks.append({'check': 'the same record every two seconds: the nine-second saw-tooth is still found', 'value': [(e['t'], e['period_s'], e['minutes']) for e in c2['events']],
                   'pass': len(c2['events']) == 1 and abs(c2['events'][0]['period_s'] - 9.0) <= 0.7 and c2['events'][0]['t'] == 3600.0})
    gap = np.ones(n, bool); gap[int(1.25 * 3600):int(1.25 * 3600) + 400] = False
    g = stickslip(t, tq, gap, hole, rpm, P)
    off = np.ones(n, bool); off[int(1.1 * 3600):int(1.1 * 3600) + 2400] = False
    tq2 = tq.copy(); late = slice(int(1.1 * 3600) + 2400, int(1.1 * 3600) + 3600); tq2[late] = 8.0 + 4.0 * ((t[late] % 9.0) / 9.0)
    o = stickslip(t, tq2, off, hole, rpm, P)
    checks.append({'check': 'a connection of under seven minutes inside it does not end the event; forty minutes off bottom does, and what follows is a second event',
                   'value': [[(e['t'], e['t_last'], e['ended']) for e in g['events']], [(e['t'], e['t_last'], e['ended']) for e in o['events']]],
                   'pass': len(g['events']) == 1 and g['events'][0]['t_last'] == 5400.0 and len(o['events']) == 2 and o['events'][0]['ended'].startswith('the bit was off bottom')})
    none = stickslip(t, None, rot, hole, rpm, P)
    checks.append({'check': 'no surface torque in the record: not judged, and says which channel it needs', 'value': none['status'], 'pass': none['status'] == 'NO CHANNELS' and 'Rotary Torque' in none['why']})
    return {'ok': all(x['pass'] for x in checks), 'checks': checks, 'label': 'SIMULATION'}
