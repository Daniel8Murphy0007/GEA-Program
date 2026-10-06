# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_harmonic - the machine behind the lines: harmonic families, the fundamental, and lines followed through time.

Everything before this module treats a signature as a flat list of frequencies: independent bins that
happened to stand above their floor. Machinery is not like that. A mud pump at 1.7 strokes per second
puts energy at 1.7, 3.4, 5.1 and 8.5 Hz - that is one machine at one rate, not four facts, and the rate
is the thing a driller recognises. Two jobs follow from saying so:

- a family of lines spaced by a common fundamental is a far stronger claim than the same lines listed
  separately, because an accidental comb is rare and the module says how rare (`by_chance`);
- the fundamental is a number with a unit a person can use - strokes per minute - instead of a bin
  index, and it moves when the machine's load moves.

The second job is the reason for the tracker here. A pump's rate follows the work: a line at 1.40 Hz
walks to 1.80 Hz over a tour, and a signature learned with a fixed window around 1.40 Hz loses it and
the rig silently stops being detectable. `track_lines` follows each line window to window, so a
signature survives the rate changing, and the walk itself becomes an observable: the rate over time,
and from it when the machine was working at all.

What this module will not call a measurement:

- a fundamental from fewer than `min_members` lines: any two lines define a comb, so two is not evidence;
- a family that the line density of this record would produce by accident more often than
  `max_by_chance` - it is reported as COULD_BE_CHANCE and no fundamental is taken from it;
- a fundamental when two different spacings explain the same lines equally well (AMBIGUOUS);
- the lowest observed line as the fundamental when half of it falls outside the analysed band: the
  machine may be running at half this rate with its first line unobservable, and the flag says so;
- strokes per minute as a machine's stroke rate: it is the rate of the line, and a pump that puts a
  line out twice per stroke runs at half the figure printed;
- a drift smaller than the spectral bin width as a measured drift (the bin width is reported beside it);
- a family as an identification: another machine of the same make at the same rate wears the same one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic as S
from .seismic import Trace, iso
from .seismic_detect import EARTH_RADIUS_KM, Source

MAX_ORDER = 12
DEFAULT_TOL_HZ = 0.05
REL_TOL = 0.01
MIN_MEMBERS = 3
MIN_FILL = 0.5
MAX_BY_CHANCE = 0.05


@dataclass
class Family:
    """Lines spaced by a common fundamental: one machine at one rate."""
    fundamental_hz: float
    orders: List[int] = field(default_factory=list)
    freqs_hz: List[float] = field(default_factory=list)
    residual_hz: List[float] = field(default_factory=list)
    excess_db: List[float] = field(default_factory=list)
    members: int = 0
    fill: float = 0.0                       # members / orders spanned: how much of the comb is there
    by_chance: float = 1.0                  # how often this record's line density makes such a comb by accident
    fundamental_observed: bool = False      # is there a line at the fundamental itself, or only the spacing
    sub_harmonic_possible: bool = False     # half this rate would put its first line outside the band
    status: str = 'OK'                      # OK | COULD_BE_CHANCE | AMBIGUOUS
    detail: str = ''

    @property
    def line_rate_per_min(self) -> float:
        return round(60.0 * self.fundamental_hz, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['line_rate_per_min'] = self.line_rate_per_min
        return d


def _tol_at(f: float, tol_hz: float, rel_tol: float) -> float:
    return max(tol_hz, rel_tol * abs(f))


def _fit_comb(lines: np.ndarray, f0: float, tol_hz: float, rel_tol: float, max_order: int) -> Tuple[List[int], List[int], List[float]]:
    """Which lines sit on the comb of f0, one line per order (the nearest wins)."""
    best: Dict[int, Tuple[int, float]] = {}
    for i, f in enumerate(lines):
        n = int(round(f / f0))
        if n < 1 or n > max_order:
            continue
        resid = float(f - n * f0)
        if abs(resid) > _tol_at(n * f0, tol_hz, rel_tol):
            continue
        if n not in best or abs(resid) < abs(best[n][1]):
            best[n] = (i, resid)
    orders = sorted(best)
    return orders, [best[n][0] for n in orders], [best[n][1] for n in orders]


def _gcd_list(ns: Sequence[int]) -> int:
    g = 0
    for n in ns:
        g = math.gcd(g, int(n))
    return max(g, 1)


def harmonic_families(freqs_hz: Sequence[float], excess_db: Optional[Sequence[float]] = None, band: Tuple[float, float] = (1.0, 20.0),
                      tol_hz: float = DEFAULT_TOL_HZ, rel_tol: float = REL_TOL, min_members: int = MIN_MEMBERS, max_order: int = MAX_ORDER,
                      max_families: int = 6, min_fill: float = MIN_FILL, max_by_chance: float = MAX_BY_CHANCE) -> dict:
    """A list of lines -> the families in it, each with its fundamental, and the lines that belong to none.

    A candidate fundamental is any observed line divided by any order: if 5.1 Hz is a machine's third
    harmonic then 1.7 is a candidate. The candidate that puts the most lines on its comb wins, ties going
    to the one that fills its comb best and then to the larger spacing - because a comb of every second
    tooth is the same comb at twice the spacing, and the larger one claims less. Whatever is left over is
    searched again, so two machines in one spectrum come out as two families."""
    lines = np.asarray(sorted(float(x) for x in freqs_hz), dtype=float)
    ex = {round(float(f), 6): float(e) for f, e in zip(freqs_hz, excess_db)} if excess_db is not None else {}
    width = max(float(band[1]) - float(band[0]), 1e-9)
    out = {'protocol': 'seismic_harmonic.harmonic_families', 'band_hz': [float(band[0]), float(band[1])], 'tol_hz': tol_hz, 'rel_tol': rel_tol,
           'min_members': min_members, 'max_order': max_order, 'min_fill': min_fill, 'max_by_chance': max_by_chance,
           'n_lines': int(len(lines)), 'line_density_per_hz': round(len(lines) / width, 4), 'families': [], 'refused': [], 'orphan_hz': [],
           'status': 'OK',
           'basis': 'a comb search over every line divided by every order; the comb that explains the most lines wins, normalised to the largest '
                    'spacing consistent with the orders found',
           'not_a_measurement': ['a fundamental from fewer than %d lines: any two lines define a comb' % min_members,
                                 'a family this record\'s line density would make by accident more often than %.0f%%' % (100 * max_by_chance),
                                 'a fundamental when two different spacings explain the same lines equally well',
                                 'the fundamental as a stroke rate: it is the rate of the line, and a pump that puts out two lines per stroke runs '
                                 'at half the figure printed',
                                 'a family as an identification: another machine of the same make at the same rate wears the same one']}
    if len(lines) < min_members:
        out['status'] = 'TOO_FEW_LINES'
        out['detail'] = f'{len(lines)} line(s); a family needs {min_members}, because any two lines define a comb'
        out['orphan_hz'] = [round(float(x), 3) for x in lines]
        out.update({'n_families': 0, 'fundamental_hz': None, 'line_rate_per_min': None})
        return out
    min_f0 = max(0.05, 0.25 * float(band[0]))
    left = list(range(len(lines)))
    while len(left) >= min_members and len(out['families']) < max_families:
        sub = lines[left]
        cands: List[Tuple[int, float, float, float, List[int], List[int], List[float]]] = []
        seen: List[float] = []
        for f in sub:
            for k in range(1, max_order + 1):
                c = f / k
                if c < min_f0:
                    break
                if any(abs(c - s) <= 0.25 * _tol_at(c, tol_hz, rel_tol) for s in seen):
                    continue
                seen.append(c)
                orders, idx, resid = _fit_comb(sub, c, tol_hz, rel_tol, max_order)
                if len(orders) < min_members:
                    continue
                g = _gcd_list(orders)
                if g > 1:                       # a comb of every g-th tooth is that comb at g times the spacing
                    c = c * g
                    orders, idx, resid = _fit_comb(sub, c, tol_hz, rel_tol, max_order)
                    if len(orders) < min_members:
                        continue
                fill = len(orders) / float(max(orders) - min(orders) + 1)
                if fill < min_fill:
                    continue
                mean_res = float(np.mean([abs(r) for r in resid]))
                cands.append((len(orders), fill, c, -mean_res, orders, idx, resid))
        if not cands:
            break
        cands.sort(key=lambda z: (z[0], round(z[1], 3), z[2], z[3]), reverse=True)
        n_mem, fill, f0, negres, orders, idx, resid = cands[0]
        # two different spacings that explain exactly as many lines, as completely, and are not the same comb
        rivals = [z for z in cands[1:] if z[0] == n_mem and abs(round(z[1], 3) - round(fill, 3)) < 1e-9
                  and abs(z[2] - f0) > _tol_at(f0, tol_hz, rel_tol)
                  and min(abs(z[2] / f0 - round(z[2] / f0)), abs(f0 / z[2] - round(f0 / z[2]))) > 0.02]
        mem_f = [float(sub[i]) for i in idx]
        tol_eff = float(np.mean([_tol_at(n * f0, tol_hz, rel_tol) for n in orders]))
        p1 = min(1.0, 2.0 * tol_eff * len(lines) / width)
        by_chance = float(p1 ** max(n_mem - 2, 0))      # two lines define the comb for free; the rest must land on it
        fam = Family(fundamental_hz=round(float(f0), 4), orders=[int(n) for n in orders], freqs_hz=[round(x, 3) for x in mem_f],
                     residual_hz=[round(float(r), 4) for r in resid], excess_db=[round(ex.get(round(x, 6), float('nan')), 2) for x in mem_f] if ex else [],
                     members=int(n_mem), fill=round(float(fill), 3), by_chance=round(by_chance, 5),
                     fundamental_observed=bool(1 in orders), sub_harmonic_possible=bool(0.5 * f0 < float(band[0])))
        if ex:
            fam.excess_db = [round(ex.get(round(x, 6), 0.0), 2) for x in mem_f]
        if by_chance > max_by_chance:
            fam.status = 'COULD_BE_CHANCE'
            fam.detail = (f'{n_mem} lines on a comb of {f0:.3f} Hz, but with {len(lines)} line(s) in {width:g} Hz a comb like this turns up by accident '
                          f'{by_chance:.0%} of the time (ceiling {max_by_chance:.0%}): no fundamental is taken from it')
            out['refused'].append(fam.to_dict())
            break
        if rivals:
            fam.status = 'AMBIGUOUS'
            fam.detail = (f'{f0:.3f} Hz and ' + ', '.join(f'{z[2]:.3f}' for z in rivals[:3]) +
                          ' Hz each put these %d lines on a comb as completely: the spacing is not resolved and no fundamental is claimed' % n_mem)
            out['refused'].append(fam.to_dict())
            break
        bits = [f'{n_mem} line(s) at orders ' + ','.join(str(n) for n in orders) + f' of {f0:.3f} Hz',
                f'comb {fill:.0%} filled', f'by chance {by_chance:.1%}']
        if not fam.fundamental_observed:
            bits.append(f'no line at {f0:.3f} Hz itself: the spacing is what is measured, not the first harmonic')
        if fam.sub_harmonic_possible:
            bits.append(f'half this rate ({0.5 * f0:.3f} Hz) would put its first line below the analysed band, so the machine could be running at half '
                        'this and only its even harmonics seen')
        fam.detail = '; '.join(bits)
        out['families'].append(fam.to_dict())
        keep = {left[i] for i in idx}
        left = [i for i in left if i not in keep]
    out['orphan_hz'] = [round(float(lines[i]), 3) for i in left]
    if not out['families']:
        out['status'] = 'NO_FAMILY'
        out.setdefault('detail', f'no comb of {min_members} or more of these {len(lines)} line(s) survived the fill and chance tests; '
                                 'the lines stand on their own')
    out['n_families'] = len(out['families'])
    out['fundamental_hz'] = out['families'][0]['fundamental_hz'] if out['families'] else None
    out['line_rate_per_min'] = out['families'][0]['line_rate_per_min'] if out['families'] else None
    return out


def sideband_pairs(freqs_hz: Sequence[float], carrier_hz: float, tol_hz: float = DEFAULT_TOL_HZ, rel_tol: float = REL_TOL,
                   max_spacing_hz: float = 5.0, min_spacing_hz: float = 0.02) -> List[dict]:
    """Lines standing in symmetric pairs either side of a carrier. Reciprocating machinery modulates its
    carrier at the rate it reciprocates, so the spacing is a rate in its own right - and a pair that is
    not symmetric is two lines, not a modulation."""
    lines = sorted(float(x) for x in freqs_hz)
    out = []
    for i, lo in enumerate(lines):
        if lo >= carrier_hz:
            break
        d = carrier_hz - lo
        if not (min_spacing_hz <= d <= max_spacing_hz):
            continue
        for hi in lines[i + 1:]:
            if hi <= carrier_hz:
                continue
            if abs((hi - carrier_hz) - d) <= _tol_at(carrier_hz, tol_hz, rel_tol):
                out.append({'carrier_hz': round(float(carrier_hz), 3), 'lower_hz': round(lo, 3), 'upper_hz': round(float(hi), 3),
                            'spacing_hz': round(0.5 * ((carrier_hz - lo) + (hi - carrier_hz)), 4),
                            'asymmetry_hz': round(float((hi - carrier_hz) - d), 4),
                            'rate_per_min': round(60.0 * 0.5 * ((carrier_hz - lo) + (hi - carrier_hz)), 2)})
                break
    return out


def _at_window(t: dict, w: int) -> Optional[float]:
    return float(t['freqs_hz'][t['windows'].index(w)]) if w in t['windows'] else None


def _harmonic_of(base: dict, cand: dict, max_order: int = MAX_ORDER, tol_ratio: float = 0.006) -> Optional[dict]:
    """Are these two tracks two teeth of one comb? In a window they share, their frequencies must stand in a
    small whole-number ratio b:a - then both are multiples of one spacing, base/a, and the spacing is what
    relates them. Compared window by window, not median to median, because a rate that walks moves both."""
    shared = [w for w in cand['windows'] if w in base['windows']]
    if not shared:
        return None
    w = shared[len(shared) // 2]
    fb_, fc = _at_window(base, w), _at_window(cand, w)
    if not fb_ or not fc or fc <= fb_:
        return None
    ratio = fc / fb_
    best = None
    for a in range(1, 7):
        for b in range(a + 1, max_order + 1):
            if math.gcd(a, b) != 1:
                continue
            err = abs(ratio - b / a) / (b / a)
            if err <= tol_ratio and (best is None or err < best['ratio_error']):
                best = {'window': int(w), 'base_hz': round(fb_, 4), 'freq_hz': round(fc, 4), 'a': a, 'b': b,
                        'spacing_hz': round(fb_ / a, 4), 'ratio_error': round(err, 5)}
    return best


def attribute_tracks(tracks: dict, signatures: Sequence[dict], tol_hz: float = 0.25, rel_tol: float = 0.01,
                     max_order: int = MAX_ORDER) -> dict:
    """Which tracked line belongs to which source.

    A track belongs to a source when it passes, in some window, within tolerance of one of the lines that
    source was learned on. A track two sources could both claim is given to neither: it separates nothing,
    which is the same rule the signature band applies to shared bins. A track no source claims is listed as
    well - it is the start of the answer to the question nobody has asked the program yet, which is what
    else is out there."""
    rows: Dict[str, dict] = {}
    ok = [s for s in signatures if s.get('status') == 'OK' and s.get('freqs_hz')]
    for s in ok:
        rows[s['source_id']] = {'source_id': s['source_id'], 'track_ids': [], 'freqs_hz': []}
    contested, unclaimed = [], []
    for t in tracks.get('tracks', []):
        claims = []
        for s in ok:
            hit = any(abs(fw - ln) <= max(tol_hz, rel_tol * ln) for fw in t['freqs_hz'] for ln in s['freqs_hz'])
            if hit:
                claims.append(s['source_id'])
        if len(claims) == 1:
            rows[claims[0]]['track_ids'].append(t['id'])
            rows[claims[0]]['freqs_hz'].append(t['freq_median_hz'])
        elif len(claims) > 1:
            contested.append({'track_id': t['id'], 'freq_median_hz': t['freq_median_hz'], 'sources': claims,
                              'note': 'more than one source was learned on a line this close: the track separates nothing and is given to neither'})
        else:
            unclaimed.append({'track_id': t['id'], 'freq_median_hz': t['freq_median_hz'], 'status': t['status'],
                              'excess_db_median': t['excess_db_median'],
                              'note': 'no listed source was learned on a line this close: something is making it, and this program was not told what'})
    # second pass: a line the fixed bins missed may still be a tooth of a comb a claimed line is on. This is
    # the whole point - a machine whose rate walked is not where it was learned, but its harmonics keep their
    # whole-number ratios to each other wherever they have walked to.
    all_tracks = {t['id']: t for t in tracks.get('tracks', [])}
    by_order = []
    for sid, r in rows.items():
        mine = [all_tracks[i] for i in r['track_ids'] if i in all_tracks]
        if not mine:
            continue
        base = min(mine, key=lambda z: z['freq_median_hz'])
        for u in list(unclaimed):
            t = all_tracks.get(u['track_id'])
            if t is None or t['id'] == base['id']:
                continue
            rel = _harmonic_of(base, t, max_order)
            if not rel:
                continue
            if sum(1 for s2 in ok if s2['source_id'] != sid and any(abs(fw - ln) <= max(tol_hz, rel_tol * ln)
                                                                    for fw in t['freqs_hz'] for ln in s2['freqs_hz'])):
                continue                            # another source's own line is this close: claim nothing
            r['track_ids'].append(t['id'])
            r['freqs_hz'].append(t['freq_median_hz'])
            unclaimed.remove(u)
            by_order.append({'source_id': sid, 'track_id': t['id'], 'freq_median_hz': t['freq_median_hz'],
                             'ratio': f"{rel['b']}:{rel['a']}", 'to_hz': rel['base_hz'], 'in_window': rel['window'],
                             'spacing_hz': rel['spacing_hz'], 'ratio_error': rel['ratio_error'],
                             'note': 'a whole-number ratio to a line already claimed by this source, in a window they share'})
    for r in rows.values():
        r['freqs_hz'] = sorted(r['freqs_hz'])
        r['track_ids'] = sorted(r['track_ids'])
    return {'protocol': 'seismic_harmonic.attribute_tracks', 'tol_hz': tol_hz, 'rel_tol': rel_tol, 'max_order': max_order, 'sources': rows,
            'contested': contested, 'unattributed': unclaimed, 'by_harmonic_order': by_order,
            'n_attributed': sum(len(r['track_ids']) for r in rows.values()),
            'basis': 'a track belongs to a source when it passes within tolerance of a line that source was learned on in any window, or when it stands '
                     'in a small whole-number ratio, in a window they share, to a track that source already claims',
            'not_a_measurement': ['a track as a source\'s: proximity to a learned line, or a whole-number ratio to one, is not proof of the machine',
                                  'a source for an unattributed track: it is only that no listed source claims it']}


def signature_families(signatures: Sequence[dict], band: Tuple[float, float] = (1.0, 20.0), tol_hz: float = DEFAULT_TOL_HZ,
                       rel_tol: float = REL_TOL, min_members: int = MIN_MEMBERS, max_order: int = MAX_ORDER,
                       tracks: Optional[dict] = None) -> dict:
    """Every learned signature's families and fundamental. Two sources whose fundamentals agree are marked:
    their lines fall on each other by construction, and nothing here separates two machines running at the
    same rate.

    With `tracks` (a `track_lines` result), each source's lines are taken from the tracks attributed to it,
    as they stood in the window most of them were present in, instead of from the fixed bins it was learned
    on - so a source whose rate walked still has its family. The row says which was used."""
    attrib = attribute_tracks(tracks, signatures) if tracks else None
    rows = []
    for sig in signatures:
        sid = sig.get('source_id', '')
        row = {'source_id': sid, 'fundamental_hz': None, 'line_rate_per_min': None, 'families': [], 'orphan_hz': [],
               'refused': [], 'n_lines': len(sig.get('freqs_hz') or []), 'lines_from': 'the bins it was learned on'}
        if sig.get('status') != 'OK':
            row['status'] = 'NO_SIGNATURE'
            row['detail'] = sig.get('detail', 'this source has no signature, so there is nothing to look for a family in')
            rows.append(row); continue
        fam = harmonic_families(sig['freqs_hz'], sig.get('excess_db') or None, band, tol_hz, rel_tol, min_members, max_order)
        used, n_used = 'the bins it was learned on', len(sig['freqs_hz'])
        extra: dict = {}
        if attrib and attrib['sources'].get(sid, {}).get('track_ids'):
            ids = set(attrib['sources'][sid]['track_ids'])
            sub = {'tracks': [t for t in tracks['tracks'] if t['id'] in ids], 'bin_width_hz': tracks.get('bin_width_hz'),
                   'window_starts_utc': tracks.get('window_starts_utc')}
            ts = tracked_signature(sub, source_id=sid)
            if ts['status'] == 'OK':
                alt = harmonic_families(ts['freqs_hz'], ts['excess_db'] or None, band, tol_hz, rel_tol, min_members, max_order)
                # whichever set of lines makes the stronger family is the one reported, and the row says which.
                # The tracked lines win a tie: they are where the machine actually is, not where it was learned.
                def _rank(z):
                    t0 = (z['families'] or [{}])[0]
                    return (t0.get('members', 0), t0.get('fill', 0.0))
                if _rank(alt) >= _rank(fam):
                    fam, used, n_used = alt, f"the {len(ids)} track(s) attributed to it, as they stood in window {ts['window']}", len(ts['freqs_hz'])
                    extra = {'track_ids': sorted(ids), 'drift_tol_hz': ts['drift_tol_hz'], 'drift_tol_frac': ts.get('drift_tol_frac')}
        row.update({'status': fam['status'], 'families': fam['families'], 'orphan_hz': fam['orphan_hz'], 'refused': fam['refused'],
                    'fundamental_hz': fam['fundamental_hz'], 'line_rate_per_min': fam['line_rate_per_min'], 'lines_from': used,
                    'n_lines': n_used, 'detail': fam['families'][0]['detail'] if fam['families'] else fam.get('detail', '')})
        row.update(extra)
        rows.append(row)
    shared = []
    withf = [r for r in rows if r.get('fundamental_hz')]
    for a in range(len(withf)):
        for b in range(a + 1, len(withf)):
            fa, fb = withf[a]['fundamental_hz'], withf[b]['fundamental_hz']
            if abs(fa - fb) <= _tol_at(fa, tol_hz, rel_tol):
                shared.append({'sources': [withf[a]['source_id'], withf[b]['source_id']], 'fundamental_hz': round(0.5 * (fa + fb), 4),
                               'separation_hz': round(abs(fa - fb), 4),
                               'note': 'the same rate: their lines fall on each other and no family tells these two apart'})
    return {'protocol': 'seismic_harmonic.signature_families', 'band_hz': [float(band[0]), float(band[1])], 'sources': rows,
            'shared_fundamentals': shared, 'n_with_fundamental': len(withf), 'attribution': attrib,
            'basis': 'harmonic_families on each source\'s lines - its tracked lines where the tracker has them, else the bins it was learned on',
            'not_a_measurement': ['a fundamental for a source with no signature',
                                  'a fundamental as a machine: two rigs at the same rate share it',
                                  'a track as a source\'s: it is attributed by proximity to a learned line, not by proof']}


# ---------------------------------------------------------------------------
# lines followed through time
# ---------------------------------------------------------------------------
def window_lines(spec: dict, iw: int, band: Tuple[float, float] = (1.0, 20.0), snr_db: float = 6.0, floor_width_hz: float = 2.0,
                 max_lines: int = 40) -> Tuple[List[float], List[float]]:
    """One window's lines, each refined inside its bin by a parabola through its three dB values - so a walk
    smaller than the bin width can be followed. The bin width is reported wherever a drift is."""
    f = spec['freqs']
    m = (f >= band[0]) & (f <= band[1])
    fb = f[m]
    if len(fb) < 3:
        return [], []
    df = float(fb[1] - fb[0])
    row = spec['psd_db'][iw][m]
    ex = row - S.noise_floor_db(row, fb, floor_width_hz)
    idx = np.where(ex >= snr_db)[0]
    outf, oute = [], []
    run: List[int] = []
    for j in list(idx) + [None]:
        if j is not None and (not run or j == run[-1] + 1):
            run.append(int(j))
            continue
        if run:
            b = max(run, key=lambda q: ex[q])
            fx = float(fb[b])
            if 0 < b < len(fb) - 1:
                y0, y1, y2 = float(ex[b - 1]), float(ex[b]), float(ex[b + 1])
                den = y0 - 2 * y1 + y2
                if abs(den) > 1e-12:
                    fx += df * float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
            outf.append(round(fx, 4))
            oute.append(round(float(ex[b]), 2))
        run = [int(j)] if j is not None else []
    if len(outf) > max_lines:
        keep = sorted(range(len(outf)), key=lambda q: oute[q], reverse=True)[:max_lines]
        keep.sort()
        outf, oute = [outf[q] for q in keep], [oute[q] for q in keep]
    return outf, oute


def track_lines(tr: Trace, band: Tuple[float, float] = (1.0, 20.0), win_s: float = 600.0, step_s: Optional[float] = None, snr_db: float = 6.0,
                max_drift_hz: float = 0.15, max_gap: int = 1, min_windows: int = 3, max_lines: int = 40, floor_width_hz: float = 2.0,
                spec: Optional[dict] = None) -> dict:
    """Each line followed from window to window: the nearest peak within `max_drift_hz` continues a line, a
    peak that continues nothing starts one, and a line unseen for more than `max_gap` windows has ended.

    This is what makes a signature survive a rate change. It also measures the change: a pump whose rate
    walks with its load walks its harmonics with it, the k-th harmonic k times as fast, and a line that
    walks further than the bin width over the record is DRIFTING rather than STEADY.

    `spec` is an already-computed spectrogram of this record at this window, passed in to save a pass."""
    spec = spec if spec is not None else S.spectrogram(tr, win_s, step_s)
    times = spec['times']
    f = spec['freqs']
    m = (f >= band[0]) & (f <= band[1])
    fb = f[m]
    bin_hz = float(fb[1] - fb[0]) if len(fb) > 1 else float('nan')
    open_: List[dict] = []
    done: List[dict] = []
    for iw in range(len(times)):
        pk, pe = window_lines(spec, iw, band, snr_db, floor_width_hz, max_lines)
        pairs = sorted(((abs(t['freqs'][-1] - p), ti, pi) for ti, t in enumerate(open_) for pi, p in enumerate(pk) if abs(t['freqs'][-1] - p) <= max_drift_hz))
        used_t, used_p = set(), set()
        for dist, ti, pi in pairs:
            if ti in used_t or pi in used_p:
                continue
            used_t.add(ti); used_p.add(pi)
            t = open_[ti]
            t['windows'].append(iw); t['freqs'].append(pk[pi]); t['excess'].append(pe[pi]); t['last'] = iw
        for pi, p in enumerate(pk):
            if pi not in used_p:
                open_.append({'windows': [iw], 'freqs': [p], 'excess': [pe[pi]], 'first': iw, 'last': iw})
        still = []
        for t in open_:
            (still if iw - t['last'] <= max_gap else done).append(t)
        open_ = still
    done.extend(open_)
    tracks, short = [], 0
    for k, t in enumerate(sorted(done, key=lambda z: (z['first'], z['freqs'][0]))):
        n = len(t['windows'])
        span = t['last'] - t['first'] + 1
        if n < min_windows:
            short += 1
            continue
        tt = np.asarray([times[i] for i in t['windows']], dtype=float)
        ff = np.asarray(t['freqs'], dtype=float)
        slope = float(np.polyfit(tt - tt[0], ff, 1)[0]) if n >= 2 else 0.0
        walk = float(slope * (tt[-1] - tt[0]))
        row = {'id': len(tracks) + 1, 'first_window': int(t['first']), 'last_window': int(t['last']), 'windows_present': int(n), 'windows_spanned': int(span),
               'start_utc': iso(float(times[t['first']]) - 0.5 * win_s, 0), 'end_utc': iso(float(times[t['last']]) + 0.5 * win_s, 0),
               'freq_first_hz': round(float(ff[0]), 4), 'freq_last_hz': round(float(ff[-1]), 4), 'freq_median_hz': round(float(np.median(ff)), 4),
               'freq_min_hz': round(float(ff.min()), 4), 'freq_max_hz': round(float(ff.max()), 4),
               'drift_hz': round(walk, 4), 'drift_hz_per_hour': round(slope * 3600.0, 4),
               'excess_db_median': round(float(np.median(t['excess'])), 2), 'freqs_hz': [round(float(x), 4) for x in ff],
               'windows': [int(i) for i in t['windows']]}
        if n < 0.8 * span:
            row['status'] = 'INTERMITTENT'
            row['detail'] = f'present in {n} of the {span} window(s) it spans: it comes and goes, and a rate read from it is read across the gaps'
        elif abs(walk) > max(2.0 * bin_hz, 0.02):
            row['status'] = 'DRIFTING'
            row['detail'] = f'walked {walk:+.3f} Hz over {(tt[-1] - tt[0]) / 3600.0:.2f} h ({slope * 3600.0:+.3f} Hz/h), more than the {bin_hz:.4f} Hz bin'
        else:
            row['status'] = 'STEADY'
            row['detail'] = f'walked {walk:+.3f} Hz, no more than the {bin_hz:.4f} Hz bin can resolve'
        tracks.append(row)
    return {'protocol': 'seismic_harmonic.track_lines', 'id': tr.id, 'band_hz': [float(band[0]), float(band[1])], 'win_s': win_s,
            'step_s': step_s or win_s, 'snr_db': snr_db, 'max_drift_hz': max_drift_hz, 'max_gap': max_gap, 'min_windows': min_windows,
            'bin_width_hz': round(bin_hz, 5), 'n_windows': int(len(times)), 'tracks': tracks, 'n_tracks': len(tracks), 'dropped_short': short,
            'window_starts_utc': [iso(float(x) - 0.5 * win_s, 0) for x in times], 'window_centres': [float(x) for x in times],
            'basis': 'peak picking per window with the peak refined inside its bin by a parabola, then nearest-neighbour linking within max_drift_hz',
            'not_a_measurement': ['a drift no larger than the bin width',
                                  'a line present in fewer than %d windows' % min_windows,
                                  'the continuity of a line across a gap: two lines that stop and start are linked here by proximity, not by proof '
                                  'they are the same machine']}


def tracked_signature(tracks: dict, window: Optional[int] = None, source_id: str = '', min_lines: int = 2) -> dict:
    """The lines as they stood in one window, taken from the tracks that pass through it.

    This is the drift-tolerant signature. A signature learned as fixed bins over a long record belongs to a
    machine that held its rate: when the rate walks, no single bin stands above its floor in enough windows
    and the machine all but disappears from its own signature - which is the defect this module exists to
    fix. The lines here are where each tracked line actually was in the chosen window, so they can be handed
    to `harmonic_families`, to `seismic_signature.beam_by_signature` or to `activity_from_signature`
    unchanged, and `drift_tol_hz` says how far they move over the record, which is the width the same lines
    must be looked for in elsewhere."""
    ts = list(tracks.get('tracks', []))
    if window is None:
        counts = {}
        for t in ts:
            for i in t['windows']:
                counts[i] = counts.get(i, 0) + 1
        window = max(counts, key=lambda i: (counts[i], -i)) if counts else 0
    here = [t for t in ts if window in t['windows']]
    freqs, ex, drifts, fracs = [], [], [], []
    for t in here:
        freqs.append(t['freqs_hz'][t['windows'].index(window)])
        ex.append(t['excess_db_median'])
        drifts.append(abs(t['drift_hz']))
        if t['freq_median_hz']:
            fracs.append(abs(t['drift_hz']) / t['freq_median_hz'])
    order = sorted(range(len(freqs)), key=lambda q: freqs[q])
    out = {'source_id': source_id, 'freqs_hz': [freqs[q] for q in order], 'excess_db': [ex[q] for q in order],
           'exclusive_windows': int(len(ts)), 'window': int(window), 'window_start_utc': (tracks.get('window_starts_utc') or [None] * (window + 1))[window],
           'track_ids': [here[q]['id'] for q in order], 'statuses': [here[q]['status'] for q in order],
           'drift_hz_per_hour': [here[q]['drift_hz_per_hour'] for q in order],
           'drift_tol_hz': round(max(drifts) if drifts else 0.0, 4),
           'drift_tol_frac': round(max(fracs) if fracs else 0.0, 4),
           'bin_width_hz': tracks.get('bin_width_hz')}
    if len(freqs) < min_lines:
        out['status'] = 'NO_LINES'
        out['detail'] = f'{len(freqs)} tracked line(s) pass through window {window} (floor {min_lines})'
    else:
        out['status'] = 'OK'
        out['detail'] = (f'{len(freqs)} tracked line(s) as they stood in window {window}; they move up to {out["drift_tol_hz"]:.3f} Hz over the record '
                         f'({out["drift_tol_frac"]:.1%} of their own frequency), which is the width they must be looked for in elsewhere - and the '
                         'fraction is the figure to carry, because a harmonic walks as far as its order')
    return out


def rate_history(tr: Trace, band: Tuple[float, float] = (1.0, 20.0), win_s: float = 600.0, step_s: Optional[float] = None, snr_db: float = 6.0,
                 tol_hz: float = DEFAULT_TOL_HZ, min_members: int = MIN_MEMBERS, min_windows: int = 3, max_order: int = MAX_ORDER,
                 floor_width_hz: float = 2.0, spec: Optional[dict] = None) -> dict:
    """The fundamental window by window: a machine's rate over the record, read from its own harmonics.

    A rate that is steady within the bin width says so; a rate that moves is the machine's load changing,
    and it is reported as Hz per hour and as a line rate per minute, which is the number a driller reads."""
    spec = spec if spec is not None else S.spectrogram(tr, win_s, step_s)
    times = spec['times']
    f = spec['freqs']
    mb = (f >= band[0]) & (f <= band[1])
    fb = f[mb]
    bin_hz = float(fb[1] - fb[0]) if len(fb) > 1 else float('nan')
    rows = []
    for iw in range(len(times)):
        pk, pe = window_lines(spec, iw, band, snr_db, floor_width_hz)
        fam = harmonic_families(pk, pe, band, tol_hz, REL_TOL, min_members, max_order)
        top = fam['families'][0] if fam['families'] else None
        rows.append({'window': iw, 'start_utc': iso(float(times[iw]) - 0.5 * win_s, 0), 'n_lines': len(pk),
                     'fundamental_hz': top['fundamental_hz'] if top else None, 'line_rate_per_min': top['line_rate_per_min'] if top else None,
                     'members': top['members'] if top else 0, 'status': fam['status'] if top else (fam['status'] or 'NO_FAMILY')})
    got = [r for r in rows if r['fundamental_hz'] is not None]
    out = {'protocol': 'seismic_harmonic.rate_history', 'id': tr.id, 'band_hz': [float(band[0]), float(band[1])], 'win_s': win_s,
           'step_s': step_s or win_s, 'bin_width_hz': round(bin_hz, 5), 'windows': rows, 'n_windows': len(rows), 'n_resolved': len(got),
           'basis': 'harmonic_families on each window\'s own lines; the strongest family\'s fundamental is that window\'s rate',
           'not_a_measurement': ['a rate in a window whose lines made no family',
                                 'a change of rate smaller than the bin width',
                                 'one machine: if two machines with families work at once, the strongest family is whichever is louder in that window']}
    if len(got) < min_windows:
        out['status'] = 'NOT_RESOLVED'
        out['detail'] = f'{len(got)} window(s) of {len(rows)} gave a family (floor {min_windows}): no rate history'
        return out
    v = np.asarray([r['fundamental_hz'] for r in got], dtype=float)
    tt = np.asarray([times[r['window']] for r in got], dtype=float)
    slope = float(np.polyfit(tt - tt[0], v, 1)[0])
    rng = float(v.max() - v.min())
    out.update({'median_hz': round(float(np.median(v)), 4), 'first_hz': round(float(v[0]), 4), 'last_hz': round(float(v[-1]), 4),
                'range_hz': round(rng, 4), 'drift_hz_per_hour': round(slope * 3600.0, 4),
                'median_rate_per_min': round(60.0 * float(np.median(v)), 2),
                'rate_per_min_range': [round(60.0 * float(v.min()), 2), round(60.0 * float(v.max()), 2)]})
    if rng <= max(2.0 * bin_hz, 0.02 * float(np.median(v))):
        out['status'] = 'RATE_STEADY'
        out['detail'] = (f"the rate held at {out['median_hz']:.3f} Hz ({out['median_rate_per_min']:.0f} per minute) across {len(got)} window(s), within "
                         f'what a {bin_hz:.4f} Hz bin can resolve')
    else:
        out['status'] = 'RATE_VARIES'
        out['detail'] = (f"the rate moved over {out['range_hz']:.3f} Hz ({out['rate_per_min_range'][0]:.0f} to {out['rate_per_min_range'][1]:.0f} per "
                         f"minute, {out['drift_hz_per_hour']:+.3f} Hz/h) across {len(got)} window(s) - the machine's load changing, not a different machine")
    return out


def activity_from_signature(tr: Trace, signature: dict, band: Tuple[float, float] = (1.0, 20.0), win_s: float = 600.0, step_s: Optional[float] = None,
                            snr_db: float = 6.0, min_fraction: float = 0.5, sources: Optional[Sequence[Source]] = None, tol_hz: float = DEFAULT_TOL_HZ,
                            floor_width_hz: float = 2.0, drift_tol_hz: float = 0.0, drift_tol_frac: float = 0.0,
                            spec: Optional[dict] = None) -> dict:
    """When was this source working, measured from its own signature instead of taken from the rigs list.

    Every verdict in the leg rests on a declared working window: the rigs CSV says a machine worked from
    here to here, and the detectability test, the array test and the track all believe it. This measures
    it: a window in which the source's own lines stand above their floor is a window it was heard in. With
    the declared windows beside it, the two can disagree, and that disagreement is worth more than either
    alone - a permit date is a permission, not a drilling log.

    `drift_tol_hz` is how far a line is allowed to have moved since it was learned: a machine whose rate
    follows its load walks out of its own bins, and looked for in those bins alone it goes silent while it
    is still working. Set it from the drift the tracker measured (`tracked_signature` reports it); left at
    zero, each line is looked for in its own bin only. `drift_tol_frac` is the same width as a fraction of
    each line's own frequency, which is the one to use: a machine's k-th harmonic walks k times as far in
    Hz as its first, and the same fraction of itself."""
    out = {'protocol': 'seismic_harmonic.activity_from_signature', 'source_id': signature.get('source_id', ''), 'band_hz': [float(band[0]), float(band[1])],
           'win_s': win_s, 'step_s': step_s or win_s, 'snr_db': snr_db, 'min_fraction': min_fraction, 'drift_tol_hz': drift_tol_hz,
           'drift_tol_frac': drift_tol_frac, 'spells': [], 'windows': [],
           'basis': "the fraction of this source's own learned lines standing snr_db above their local floor, window by window, each line looked for "
                    'within drift_tol_hz of where it was learned',
           'not_a_measurement': ['activity for a source with no signature',
                                 'a declared window as a working window: a permit date is a permission, not a drilling log',
                                 'a silent window as a stopped machine: it may be working below this station\'s detection radius, or have walked out '
                                 'of the bins it was learned in (drift_tol_hz is how far it was allowed to walk)',
                                 'a heard window as this machine: another machine with the same lines sounds the same']}
    if signature.get('status') != 'OK' or not signature.get('freqs_hz'):
        out['status'] = 'NO_SIGNATURE'
        out['verdict'] = 'NOT_JUDGED'
        out['detail'] = signature.get('detail', 'this source has no signature, so nothing in the record is known to be its')
        return out
    spec = spec if spec is not None else S.spectrogram(tr, win_s, step_s)
    times = spec['times']
    f = spec['freqs']
    mb = (f >= band[0]) & (f <= band[1])
    fb = f[mb]
    want = [float(x) for x in signature['freqs_hz'] if band[0] <= x <= band[1]]
    half = 0.5 * float(fb[1] - fb[0]) if len(fb) > 1 else 0.0
    cols = [np.where(np.abs(fb - x) <= max(drift_tol_hz, drift_tol_frac * x, half))[0] for x in want]
    cols = [c for c in cols if len(c)]
    if not cols:
        out['status'] = 'NO_SIGNATURE'
        out['verdict'] = 'NOT_JUDGED'
        out['detail'] = 'none of this source\'s lines falls in the band analysed here'
        return out
    heard = []
    for iw in range(len(times)):
        row = spec['psd_db'][iw][mb]
        ex = row - S.noise_floor_db(row, fb, floor_width_hz)
        vals = [float(ex[c].max()) for c in cols]          # the best bin within drift_tol_hz of where the line was learned
        frac = float(np.mean([v >= snr_db for v in vals]))
        on = frac >= min_fraction
        heard.append(on)
        out['windows'].append({'window': iw, 'start_utc': iso(float(times[iw]) - 0.5 * win_s, 0), 'fraction_above': round(frac, 3),
                               'median_excess_db': round(float(np.median(vals)), 2), 'heard': bool(on)})
    spells = []
    i = 0
    while i < len(heard):
        if not heard[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(heard) and heard[j + 1]:
            j += 1
        spells.append({'start_utc': iso(float(times[i]) - 0.5 * win_s, 0), 'end_utc': iso(float(times[j]) + 0.5 * win_s, 0),
                       'windows': j - i + 1, 'hours': round((j - i + 1) * (step_s or win_s) / 3600.0, 3)})
        i = j + 1
    out['spells'] = spells
    out['n_windows'] = len(heard)
    out['heard_windows'] = int(sum(heard))
    out['n_spells'] = len(spells)
    out['status'] = 'OK'
    sid = signature.get('source_id', '')
    mine = [s for s in (sources or []) if s.source_id == sid]
    if not mine:
        out['verdict'] = 'NOT_JUDGED'
        out['detail'] = f'heard in {out["heard_windows"]} of {len(heard)} window(s) in {len(spells)} spell(s); no declared window to compare against'
        return out
    declared = np.zeros(len(times), dtype=bool)
    for s in mine:
        declared |= (times >= s.start) & (times <= s.end)
    hv = np.asarray(heard, dtype=bool)
    both = int((declared & hv).sum())
    dec_silent = int((declared & ~hv).sum())
    heard_undec = int((~declared & hv).sum())
    out['declared'] = [{'start_utc': iso(s.start, 0), 'end_utc': iso(s.end, 0), 'note': s.note} for s in mine]
    out['agreement'] = {'declared_windows': int(declared.sum()), 'heard_windows': int(hv.sum()), 'declared_and_heard': both,
                        'declared_but_silent': dec_silent, 'heard_but_not_declared': heard_undec,
                        'fraction_of_declared_heard': round(both / max(int(declared.sum()), 1), 3)}
    if int(declared.sum()) == 0:
        out['verdict'] = 'NOT_JUDGED'
        out['detail'] = 'no declared window falls inside this record'
    elif both >= 0.8 * int(declared.sum()) and heard_undec <= 0.2 * max(int(hv.sum()), 1):
        out['verdict'] = 'AGREES'
        out['detail'] = (f'heard in {both} of the {int(declared.sum())} declared window(s) and in {heard_undec} that were not declared: the record and '
                         'the list say the same thing')
    else:
        out['verdict'] = 'DIFFERS'
        out['detail'] = (f'heard in {both} of the {int(declared.sum())} declared window(s), silent in {dec_silent} of them, and heard in {heard_undec} '
                         'window(s) the list does not declare: the record and the list disagree, and which is right is not decided here')
    return out


# ---------------------------------------------------------------------------
# the labelled scene: one machine whose rate moves, with stops in it
# ---------------------------------------------------------------------------
def synthetic_harmonic_scene(seed: int = 23, fs: float = 50.0, hours: float = 4.0, station=(31.0, -102.0), dist_km: float = 6.0,
                             pump_hz: Tuple[float, float] = (1.40, 1.85), orders: Sequence[int] = (1, 2, 3), engine_hz: float = 12.0,
                             spells: Sequence[Tuple[float, float]] = ((0.15, 0.45), (0.60, 1.0)), noise: float = 30.0,
                             q_factor: float = 100.0, v_km_s: float = 2.5) -> Tuple[Trace, List[Source], dict]:
    """One station, one rig. Its pump rate ramps from pump_hz[0] to pump_hz[1] across the record, carrying its
    harmonics with it - the k-th k times as fast, which is the fact a tracker can be checked against - and a
    steady engine line beside them. The rig works in `spells` (fractions of the record) with a stop between,
    so the activity test has something to find and something to be wrong about. The scene's numbers are an
    assumption for checking the arithmetic and say nothing about any ground."""
    from .seismic_detect import haversine_km as _hk
    rng = np.random.default_rng(seed)
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    T = hours * 3600.0
    white = rng.normal(0.0, 1.0, n)
    red = np.cumsum(rng.normal(0.0, 0.02, n)); red -= np.linspace(red[0], red[-1], n)
    x = noise * white + noise * red / (np.std(red) + 1e-12)
    t0 = S.parse_time('2025-07-01T00:00:00Z')
    lat0, lon0 = station
    bearing = math.radians(55.0)
    dlat = (dist_km / EARTH_RADIUS_KM) * math.cos(bearing)
    dlon = (dist_km / EARTH_RADIUS_KM) * math.sin(bearing) / math.cos(math.radians(lat0))
    lat, lon = lat0 + math.degrees(dlat), lon0 + math.degrees(dlon)
    on = np.zeros(n, dtype=bool)
    srcs: List[Source] = []
    for a, b in spells:
        m = (t >= a * T) & (t < b * T)
        on |= m
        srcs.append(Source('RIG-H', lat, lon, t0 + a * T, t0 + b * T, 'rig', 'synthetic harmonic scene: a working spell'))
    f_a, f_b = float(pump_hz[0]), float(pump_hz[1])
    ramp = (f_b - f_a) / T                                        # Hz per second
    phase = 2 * math.pi * (f_a * t + 0.5 * ramp * t * t)          # a line whose rate walks; its k-th harmonic walks k times as fast
    rel = {1: 1.0, 2: 0.6, 3: 0.4, 4: 0.25, 5: 0.2}
    for k in orders:
        f_mid = 0.5 * (f_a + f_b) * k
        amp = 6000.0 * rel.get(int(k), 0.2) / dist_km * math.exp(-math.pi * f_mid * dist_km / (q_factor * v_km_s))
        x[on] += amp * np.sin(int(k) * phase[on] + rng.uniform(0, 2 * math.pi))
    amp_e = 6000.0 * 0.5 / dist_km * math.exp(-math.pi * engine_hz * dist_km / (q_factor * v_km_s))
    x[on] += amp_e * np.sin(2 * math.pi * engine_hz * t[on] + rng.uniform(0, 2 * math.pi))
    tr = Trace('XX', 'HRM', '', 'HHZ', t0, fs, np.round(x).astype(np.int32), source='synthetic_harmonic_scene', encoding='SYNTHETIC')
    meta = {'status': 'SIMULATION_SELF_TEST', 'q_factor': q_factor, 'v_km_s': v_km_s, 'spreading': '1/r (body wave)', 'a0_counts_at_1km': 6000.0,
            'distance_km': round(_hk(lat0, lon0, lat, lon), 3), 'pump_start_hz': f_a, 'pump_end_hz': f_b, 'orders': [int(k) for k in orders],
            'pump_mid_hz': round(0.5 * (f_a + f_b), 4), 'pump_drift_hz_per_hour': round((f_b - f_a) / hours, 4), 'engine_hz': engine_hz,
            'hours': float(hours), 'start_utc': iso(t0, 0), 'start_epoch': float(t0),
            'spells_utc': [{'start_utc': iso(t0 + a * T, 0), 'end_utc': iso(t0 + b * T, 0)} for a, b in spells],
            'rig_latlon': {'lat': round(lat, 6), 'lon': round(lon, 6)},
            'note': 'the pump rate ramps across the record and carries its harmonics with it, the k-th k times as fast; the engine line is steady; the '
                    'rig stops between its spells. The scene is an assumption for checking the arithmetic'}
    return tr, srcs, meta


def selftest(seed: int = 23) -> dict:
    """SIMULATION_SELF_TEST, and the whole case for this module in one run: a machine whose pump rate walks
    with its load all but disappears from a signature of fixed bins (the rule every earlier test used), the
    tracker follows every one of its lines through the record with the k-th harmonic walking k times as fast
    as the first, the signature taken from those tracks carries the whole family and gives the fundamental,
    the rate history reads the walk as a rate per minute over time, the working spells come out of the record
    instead of out of the rigs list - and a dense set of lines with no machine behind it is refused."""
    from .seismic_detect import _lines_in_windows, _peaks
    tr, srcs, meta = synthetic_harmonic_scene(seed)
    band = (1.0, 20.0)
    # the fixed-bin signature: the rule the detectability test and the signature band use
    spec = S.spectrogram(tr, 600.0)
    working = np.array([i for i, c in enumerate(spec['times']) if any(s.start <= c <= s.end for s in srcs)])
    frac, excess = _lines_in_windows(spec, working, band, 6.0)
    f = spec['freqs']
    fb = f[(f >= band[0]) & (f <= band[1])]
    fixed_lines = _peaks(np.where(frac >= 0.5)[0], excess, fb)
    fixed_fam = harmonic_families(fixed_lines, [float(excess[int(np.argmin(np.abs(fb - x)))]) for x in fixed_lines], band)
    # every line followed; the k-th harmonic walks k times as fast as the first
    tl = track_lines(tr, band, 600.0, snr_db=6.0, max_drift_hz=0.3)
    drifting = [x for x in tl['tracks'] if x['status'] == 'DRIFTING']
    base = min(drifting, key=lambda z: z['freq_median_hz']) if drifting else None
    ratios = {}
    if base:
        for k in meta['orders']:
            near = [z for z in tl['tracks'] if abs(z['freq_median_hz'] - k * base['freq_median_hz']) <= 0.4]
            if near:
                ratios[int(k)] = round(near[0]['drift_hz_per_hour'] / base['drift_hz_per_hour'], 2)
    steady_engine = [z for z in tl['tracks'] if abs(z['freq_median_hz'] - meta['engine_hz']) <= 0.2 and z['status'] == 'STEADY']
    # the signature taken from the tracks, and the family in it
    ts = tracked_signature(tl, source_id='RIG-H')
    fam = harmonic_families(ts['freqs_hz'], ts['excess_db'], band)
    top = fam['families'][0] if fam['families'] else {}
    # the rate the scene was running at in THAT window, not the average of the ramp
    cen = tl['window_centres'][ts['window']]
    expect_hz = meta['pump_start_hz'] + (meta['pump_end_hz'] - meta['pump_start_hz']) * (cen - meta['start_epoch']) / (meta['hours'] * 3600.0)
    f0_err = abs(top['fundamental_hz'] - expect_hz) if top else None
    engine_orphan = any(abs(x - meta['engine_hz']) <= 0.3 for x in fam['orphan_hz'])
    rh = rate_history(tr, band, 600.0)
    # the working spells, measured from the lines: in their own bins, and allowing for the walk
    sig = {'source_id': 'RIG-H', 'status': 'OK', 'freqs_hz': [round(meta['pump_mid_hz'] * k, 3) for k in meta['orders']] + [meta['engine_hz']],
           'excess_db': [], 'exclusive_windows': len(working), 'detail': "the scene's own lines at the middle of the ramp"}
    act_fixed = activity_from_signature(tr, sig, band, 600.0, sources=srcs)
    act_drift = activity_from_signature(tr, sig, band, 600.0, sources=srcs, drift_tol_hz=round(ts['drift_tol_hz'] * 3.2, 3))
    # a dense set of lines with no machine behind it
    rng = np.random.default_rng(seed)
    noise_lines = sorted(round(float(v), 3) for v in rng.uniform(1.0, 20.0, 45))
    junk = harmonic_families(noise_lines, None, band)
    ok = bool(len(fixed_lines) < len(meta['orders']) and fixed_fam['status'] != 'OK'
              and base is not None and len(ratios) == len(meta['orders']) and all(abs(ratios[k] - k) <= 0.35 for k in ratios)
              and len(steady_engine) == len(meta['spells_utc'])                 # the stop breaks every line: one track per spell
              and len(drifting) == len(meta['orders']) * len(meta['spells_utc'])
              and ts['status'] == 'OK' and ts['drift_tol_hz'] > tl['bin_width_hz']
              and top and f0_err is not None and f0_err <= 0.05 and set(top['orders']) == set(int(k) for k in meta['orders'])
              and top['by_chance'] <= fam['max_by_chance'] and engine_orphan
              and rh['status'] == 'RATE_VARIES' and rh['range_hz'] >= 0.5 * (meta['pump_end_hz'] - meta['pump_start_hz'])
              and act_fixed['verdict'] == 'DIFFERS' and act_drift['verdict'] == 'AGREES'
              and act_drift['n_spells'] == len(meta['spells_utc'])
              and (junk['status'] != 'OK' or all(x['by_chance'] <= junk['max_by_chance'] for x in junk['families'])))
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'scene': meta, 'fixed_bins': {'lines_hz': fixed_lines, 'status': fixed_fam['status'],
                                                                                      'n_families': len(fixed_fam['families'])},
            'tracks': tl, 'drift_ratios': ratios, 'tracked_signature': ts, 'families': fam,
            'fundamental_hz': top.get('fundamental_hz'), 'scene_rate_in_that_window_hz': round(float(expect_hz), 4),
            'fundamental_error_hz': round(f0_err, 4) if f0_err is not None else None, 'rate_history': rh,
            'activity_fixed_bins': act_fixed, 'activity_with_drift': act_drift,
            'dense_lines': {'n': len(noise_lines), 'status': junk['status'], 'n_families': len(junk['families'])}}


def report_text(res: dict) -> str:
    """The families of one signature, or one record's line tracks, as text."""
    p = res.get('protocol', '')
    lines: List[str] = []
    if p.endswith('harmonic_families'):
        lines.append(f"harmonic families: {res['n_lines']} line(s) in {res['band_hz'][0]:g}-{res['band_hz'][1]:g} Hz -> {len(res['families'])} family(ies) [{res['status']}]")
        for fm in res['families']:
            lines.append(f"  {fm['fundamental_hz']:.3f} Hz ({fm['line_rate_per_min']:.0f}/min): orders " + ','.join(str(n) for n in fm['orders'])
                         + f" at {', '.join(f'{v:g}' for v in fm['freqs_hz'])} Hz")
            lines.append('    ' + fm['detail'])
        for fm in res.get('refused', []):
            lines.append(f"  refused {fm['fundamental_hz']:.3f} Hz [{fm['status']}]: {fm['detail']}")
        if res['orphan_hz']:
            lines.append('  in no family: ' + ', '.join(f'{v:g}' for v in res['orphan_hz']) + ' Hz')
        if res.get('detail'):
            lines.append('  ' + res['detail'])
    elif p.endswith('track_lines'):
        lines.append(f"line tracks: {res['n_tracks']} line(s) across {res['n_windows']} window(s) of {res['win_s']:g} s, bin {res['bin_width_hz']:g} Hz"
                     + (f" ({res['dropped_short']} too short)" if res['dropped_short'] else ''))
        for t in res['tracks']:
            lines.append(f"  {t['freq_median_hz']:7.3f} Hz  {t['status']:<12} {t['windows_present']:>3}/{t['windows_spanned']:<3} win  "
                         f"{t['drift_hz_per_hour']:+.3f} Hz/h  {t['excess_db_median']:+.1f} dB")
    elif p.endswith('rate_history'):
        lines.append(f"rate history [{res['status']}]: {res['n_resolved']} of {res['n_windows']} window(s) gave a family")
        if res.get('median_hz'):
            lines.append(f"  {res['median_hz']:.3f} Hz median ({res['median_rate_per_min']:.0f}/min), {res['first_hz']:.3f} -> {res['last_hz']:.3f} Hz, "
                         f"{res['drift_hz_per_hour']:+.3f} Hz/h")
        lines.append('  ' + res.get('detail', ''))
    elif p.endswith('activity_from_signature'):
        lines.append(f"activity {res['source_id']} [{res.get('verdict', '')}]: heard in {res.get('heard_windows', 0)} of {res.get('n_windows', 0)} "
                     f"window(s), {res.get('n_spells', 0)} spell(s)")
        for s in res.get('spells', []):
            lines.append(f"  {s['start_utc']} -> {s['end_utc']}  {s['hours']:g} h")
        a = res.get('agreement')
        if a:
            lines.append(f"  declared {a['declared_windows']} window(s): {a['declared_and_heard']} heard, {a['declared_but_silent']} silent; "
                         f"{a['heard_but_not_declared']} heard and not declared")
        lines.append('  ' + res.get('detail', ''))
    elif p.endswith('signature_families'):
        lines.append(f"signature families: {res['n_with_fundamental']} of {len(res['sources'])} source(s) have a fundamental")
        for r in res['sources']:
            f0 = f"{r['fundamental_hz']:.3f} Hz ({r['line_rate_per_min']:.0f}/min)" if r['fundamental_hz'] else '-'
            lines.append(f"  {r['source_id']:<10} {r['status']:<14} {f0}")
            if r.get('detail'):
                lines.append('    ' + r['detail'])
        for s in res['shared_fundamentals']:
            lines.append(f"  shared rate {s['fundamental_hz']:.3f} Hz: {' and '.join(s['sources'])} - {s['note']}")
    for nm in res.get('not_a_measurement', []):
        lines.append('  not a measurement: ' + nm)
    return '\n'.join(lines)
