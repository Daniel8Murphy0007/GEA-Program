# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_unlisted - what else is out there. The first part of this leg that finds instead of confirming.

Every test before this one answers a question the user already asked: here is a list of rigs, were they
heard, where are they, when were they working. The program could only ever confirm or deny what it was
told. It had no way to say "there is a machine here that your list does not have" - which is the thing a
monitoring programme is actually for, because the source nobody listed is the one nobody is watching.

The pieces were already here. `seismic_harmonic.track_lines` follows every line in a record;
`attribute_tracks` gives each one to the listed source whose learned lines it matches, or to a
whole-number ratio with a line that source already claims - and lists the ones nobody claims. This
module takes that list seriously: groups the orphans into combs, and a comb with a fundamental is a
machine running at a rate. Then it beams each candidate on its own lines, exactly as the signature band
beams a listed rig, and the candidate gets a direction.

What it refuses to call a machine matters more here than anywhere else in the leg, because this is the
one test whose output is a claim nobody asked for:

- lines on the mains frequency or its multiples are electrical, not mechanical, and are named as such.
  A 60 Hz line is a power line, a generator's alternator or the recorder's own supply;
- a candidate that beams to zero slowness arrives everywhere at once: it is common-mode, in the cabling
  or the supply, not a wavefront crossing the ground;
- a candidate whose lines are a whole-number ratio of a listed source's rate is that source's harmonic
  that attribution missed, not a new machine;
- a single line is not a machine; neither are two, because any two lines define a comb;
- and a found candidate is a direction with a rate on it, never an identification. A compressor, a pump
  jack, a water pump, a passing train and a drilling rig all put lines in this band. The program says
  where it is and what rate it runs at, and stops there.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import seismic as S
from . import seismic_array as AR
from . import seismic_harmonic as HM
from . import seismic_signature as SG
from .seismic import Trace, iso
from .seismic_array import Sensor
from .seismic_detect import Source

MAINS_HZ = (50.0, 60.0)
MIN_LINES = 3
ZERO_SLOWNESS_S_KM = 0.05


def _alias(f: float, fs: float) -> float:
    """Where a frequency above half the sample rate lands after folding."""
    if fs <= 0:
        return f
    g = math.fmod(abs(f), fs)
    return fs - g if g > fs / 2.0 else g


def mains_lines(freqs: Sequence[float], mains: Sequence[float] = MAINS_HZ, tol_hz: float = 0.3, max_order: int = 8,
                sample_rate_hz: Optional[float] = None) -> Dict[str, List[dict]]:
    """Which of these lines sit on a mains frequency, one of its multiples, or - the one that catches people -
    where one of those multiples lands after folding. Electrical, not mechanical: a line at 60.0 Hz is a power
    line, an alternator or the recorder's own supply, and calling it a machine is the easiest mistake this
    module could make. A mains harmonic above half the sample rate does not disappear; it folds back into the
    band at some arbitrary-looking frequency and looks exactly like a machine running at that rate."""
    out: Dict[str, List[dict]] = {}
    for m in mains:
        hits = []
        for f in freqs:
            f = float(f)
            for k in range(1, max_order + 1):
                direct = k * m
                if abs(f - direct) <= tol_hz:
                    hits.append({'freq_hz': round(f, 3), 'order': k, 'folded': False,
                                 'note': f'the {k}x multiple of the {m:g} Hz mains' if k > 1 else f'the {m:g} Hz mains'})
                    break
                if sample_rate_hz and direct > sample_rate_hz / 2.0 and abs(f - _alias(direct, sample_rate_hz)) <= tol_hz:
                    hits.append({'freq_hz': round(f, 3), 'order': k, 'folded': True,
                                 'note': f'the {k}x multiple of the {m:g} Hz mains ({direct:g} Hz) folded back by the '
                                         f'{sample_rate_hz:g} Hz sample rate'})
                    break
        if hits:
            out[f'{m:g}'] = sorted(hits, key=lambda d: d['freq_hz'])
    return out


def unlisted_candidates(tracks: dict, signatures: Sequence[dict], band: Tuple[float, float] = (1.0, 20.0),
                        min_lines: int = MIN_LINES, mains: Sequence[float] = MAINS_HZ, mains_tol_hz: float = 0.3,
                        tol_hz: float = HM.DEFAULT_TOL_HZ, sample_rate_hz: Optional[float] = None) -> dict:
    """The tracked lines no listed source claims, grouped into candidate machines by their combs.

    Each candidate comes out in the shape the signature band reads, so it can be beamed with exactly the
    same code that beams a listed rig - which is the point: an unlisted source is not a special case, it is
    a source whose name nobody wrote down."""
    at = HM.attribute_tracks(tracks, signatures)
    by_id = {t['id']: t for t in tracks.get('tracks', [])}
    orphan_ids = [u['track_id'] for u in at['unattributed']]
    orphans = [by_id[i] for i in orphan_ids if i in by_id]
    out = {'protocol': 'seismic_unlisted.unlisted_candidates', 'band_hz': [float(band[0]), float(band[1])],
           'n_tracks': len(tracks.get('tracks', [])), 'n_attributed': at['n_attributed'], 'n_unattributed': len(orphans),
           'attribution': at, 'candidates': [], 'mains': {}, 'leftover_hz': [],
           'basis': 'the tracked lines no listed source claims, grouped by the comb that explains them, each in the shape the signature band reads',
           'not_a_measurement': ['a machine from one or two lines: any two lines define a comb',
                                 'a mechanical source from a line on the mains frequency or its multiples',
                                 'an identification: a compressor, a pump jack, a water pump and a rig all put lines in this band',
                                 'a source for a line that is a whole-number ratio of a listed rate: that is the listed machine\'s harmonic']}
    if not orphans:
        out['status'] = 'NOTHING_UNLISTED'
        out['detail'] = f"every one of the {out['n_tracks']} tracked line(s) belongs to a source on the list"
        return out
    freqs = [t['freq_median_hz'] for t in orphans]
    mh = mains_lines(freqs, mains, mains_tol_hz, sample_rate_hz=sample_rate_hz)
    electrical = {h['freq_hz'] for v in mh.values() for h in v}
    out['mains'] = mh
    mech = [t for t in orphans if round(t['freq_median_hz'], 3) not in electrical]
    if not mech:
        out['status'] = 'ONLY_ELECTRICAL'
        out['detail'] = ('every line nobody claims sits on the mains frequency or a multiple of it: ' +
                         ', '.join(f'{v:g}' for v in sorted(electrical)) + ' Hz. That is electrical, not a machine in the ground')
        return out
    # group what is left into combs. Each comb that survives the family test is a candidate machine.
    left = list(mech)
    n = 0
    while len(left) >= min_lines:
        fam = HM.harmonic_families([t['freq_median_hz'] for t in left], [t['excess_db_median'] for t in left], band,
                                   min_members=min_lines, max_families=1)
        if not fam['families']:
            break
        f0 = fam['families'][0]
        used = [t for t in left if any(abs(t['freq_median_hz'] - x) <= max(tol_hz, 0.01 * x) for x in f0['freqs_hz'])]
        if len(used) < min_lines:
            break
        n += 1
        ids = [t['id'] for t in used]
        sub = {'tracks': used, 'bin_width_hz': tracks.get('bin_width_hz'), 'window_starts_utc': tracks.get('window_starts_utc')}
        ts = HM.tracked_signature(sub, source_id=f'UNLISTED-{n}')
        out['candidates'].append({'source_id': f'UNLISTED-{n}', 'status': 'OK' if ts['status'] == 'OK' else ts['status'],
                                  'freqs_hz': ts['freqs_hz'], 'excess_db': ts['excess_db'], 'exclusive_windows': ts['exclusive_windows'],
                                  'window': ts['window'], 'drift_tol_frac': ts.get('drift_tol_frac'),
                                  'fundamental_hz': f0['fundamental_hz'], 'line_rate_per_min': f0['line_rate_per_min'],
                                  'orders': f0['orders'], 'by_chance': f0['by_chance'], 'fill': f0['fill'],
                                  'track_ids': ids, 'detail': f0['detail']})
        left = [t for t in left if t['id'] not in set(ids)]
    out['leftover_hz'] = sorted(round(t['freq_median_hz'], 3) for t in left)
    if out['candidates']:
        out['status'] = 'CANDIDATES'
        out['detail'] = (f"{len(out['candidates'])} machine-shaped comb(s) in the {len(mech)} line(s) no listed source claims"
                         + (f"; {len(out['leftover_hz'])} line(s) left over, which are lines and not machines" if out['leftover_hz'] else ''))
    else:
        out['status'] = 'UNEXPLAINED_LINES'
        out['detail'] = (f"{len(mech)} line(s) nobody claims, but none of them forms a comb of {min_lines} or more: "
                         + ', '.join(f'{v:g}' for v in out['leftover_hz'][:10]) + ' Hz. Something is making them; nothing here says what')
    return out


def find_unlisted(traces: Sequence[Trace], sensors: Sequence[Sensor], signatures: Sequence[dict], band: Tuple[float, float] = (1.0, 20.0),
                  win_s: float = 600.0, seg_s: float = 10.0, s_max: float = 3.0, n_grid: int = 41, min_lines: int = MIN_LINES,
                  tracks: Optional[dict] = None, name: str = '') -> dict:
    """One array, one record: the candidates nobody listed, each with a direction of its own.

    A candidate is beamed on its own lines by the same code that beams a listed rig, so it inherits the same
    refusals - no bearing from bins that were not coherent, none where the lines alias on this geometry. One
    more is added here: a candidate that beams to zero slowness arrived everywhere at once, which is not a
    wavefront crossing the ground but something common to the cabling or the supply."""
    tl = tracks or HM.track_lines(traces[0], band, win_s)
    cand = unlisted_candidates(tl, signatures, band, min_lines, sample_rate_hz=traces[0].sample_rate)
    out = {'protocol': 'seismic_unlisted.find_unlisted', 'array': name or (sensors[0].sensor_id if sensors else ''),
           'band_hz': [float(band[0]), float(band[1])], 'win_s': win_s, 'candidates': cand, 'sources': [],
           'basis': "each unlisted candidate's own lines sliced out of the cross-spectral matrix and beamed, exactly as a listed source is",
           'not_a_measurement': ['a bearing for a candidate whose lines were not coherent, or that aliases on this geometry',
                                 'a source for a candidate that beams to zero slowness: it arrived everywhere at once',
                                 'an identification of any kind']}
    if cand['status'] != 'CANDIDATES':
        out['status'] = cand['status']
        out['detail'] = cand['detail']
        return out
    sigs = [{'source_id': c['source_id'], 'status': 'OK', 'freqs_hz': c['freqs_hz'], 'excess_db': c['excess_db'],
             'exclusive_windows': c['exclusive_windows'], 'detail': 'an unlisted candidate'} for c in cand['candidates']]
    # a window the candidates are actually in: the one their tracks were read at
    centres = tl.get('window_centres') or []
    w = cand['candidates'][0].get('window') or 0
    c0 = float(centres[w]) if w < len(centres) else (traces[0].starttime + win_s / 2.0)
    win = [t.slice(c0 - win_s / 2.0, c0 + win_s / 2.0) for t in traces]
    try:
        bb = SG.beam_by_signature(win, sensors, sigs, band, seg_s, s_max, n_grid)
    except ValueError as e:
        out['status'] = 'NO_BEAM'
        out['detail'] = str(e)
        return out
    rows = {r['source_id']: r for r in bb['sources']}
    byid = {c['source_id']: c for c in cand['candidates']}
    for sid, r in rows.items():
        c = byid[sid]
        row = dict(r)
        row.update({'fundamental_hz': c['fundamental_hz'], 'line_rate_per_min': c['line_rate_per_min'], 'orders': c['orders'],
                    'by_chance': c['by_chance'], 'lines_hz': c['freqs_hz']})
        if r.get('verdict') == 'POINTED' and (r.get('slowness_s_km') or 0.0) <= ZERO_SLOWNESS_S_KM:
            row['verdict'] = 'COMMON_MODE'
            row['detail'] = (f"it beams to {r.get('slowness_s_km')} s/km - everywhere at once. That is not a wavefront crossing the ground but "
                             'something shared by the sensors: the cabling, the supply, or the recorder itself')
        out['sources'].append(row)
    out['window'] = bb['window']
    out['whole_band'] = bb['whole_band']
    found = [r for r in out['sources'] if r['verdict'] == 'POINTED']
    out['n_found'] = len(found)
    out['status'] = 'FOUND' if found else 'NO_BEARING'
    out['detail'] = (f"{len(found)} unlisted source(s) with a direction of their own: "
                     + '; '.join(f"{r['source_id']} at {r['back_azimuth_deg']:.0f} deg running {r['line_rate_per_min']:.0f} a minute" for r in found)
                     if found else
                     f"{len(out['sources'])} candidate(s) but none of them gave a bearing this array will stand behind")
    return out


def synthetic_unlisted_scene(seed: int = 29, fs: float = 150.0, hours: float = 1.8, mains_hz: float = 60.0,
                             mains_counts: float = 120.0, hide: str = 'RIG-3') -> Tuple[List[Trace], List[Sensor], List[Source], List[Source], dict]:
    """The labelled scene: three rigs round one array, of which one is NOT on the list handed to the program,
    and a mains line common to every sensor. The test is whether the program finds the rig nobody listed, at
    the right bearing and the right rate, and whether it leaves the mains alone."""
    # each rig needs enough windows alone for a signature to be learned; a sixth of the record each leaves
    # a quiet spell, three solo spells and a long spell with all three working, which is the case the leg is for
    traces, sensors, srcs, meta = SG.synthetic_multi_scene(seed=seed, fs=fs, hours=hours, solo_hours=hours / 6.0,
                                                           sources_km=((40.0, 8.0, 1.7), (140.0, 12.0, 2.9), (250.0, 9.0, 2.2)))
    n = len(traces[0].data)
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed + 3)
    ph = rng.uniform(0, 2 * math.pi)
    hum = mains_counts * np.sin(2 * math.pi * mains_hz * t + ph)      # the same in every sensor: it is not a wavefront
    out = [Trace(tr.network, tr.station, tr.location, tr.channel, tr.starttime, tr.sample_rate,
                 np.round(np.asarray(tr.data, dtype=np.float64) + hum).astype(np.int32),
                 source='synthetic_unlisted_scene', encoding='SYNTHETIC') for tr in traces]
    listed = [s for s in srcs if s.source_id != hide]
    meta = dict(meta)
    meta.update({'hidden': hide, 'mains_hz': mains_hz, 'mains_counts': mains_counts,
                 'hidden_bearing_deg': meta['true_bearings_deg'][hide], 'hidden_distance_km': meta['distances_km'][hide],
                 'hidden_pump_hz': meta['lines_hz'][hide][0],
                 'note': 'one rig is left off the list the program is given, and a mains line is added to every sensor identically; '
                         'the scene says nothing about any real ground'})
    return out, sensors, listed, srcs, meta


def selftest(seed: int = 29) -> dict:
    """SIMULATION_SELF_TEST: three rigs, one of them not on the list, and a mains line in every sensor. The
    program must find the rig nobody listed - at its true bearing, at its true pump rate - and must not
    report the mains as a machine."""
    traces, sensors, listed, allsrc, meta = synthetic_unlisted_scene(seed)
    band = (1.0, 100.0)
    learned = SG.learn_signatures(traces[0], listed, band, 300.0)
    tl = HM.track_lines(traces[0], band, 300.0, max_drift_hz=0.3)
    r = find_unlisted(traces, sensors, learned['signatures'], band, 300.0, tracks=tl, name='array-1')
    found = [x for x in r.get('sources', []) if x['verdict'] == 'POINTED']
    best = None
    if found:
        best = min(found, key=lambda x: abs(float(AR._angle_diff(x['back_azimuth_deg'], meta['hidden_bearing_deg']))))
    err = abs(float(AR._angle_diff(best['back_azimuth_deg'], meta['hidden_bearing_deg']))) if best else None
    rate_err = abs(best['fundamental_hz'] - meta['hidden_pump_hz']) if best else None
    cand = r.get('candidates') or {}
    mains_named = any(abs(h['freq_hz'] - meta['mains_hz']) <= 0.5 for v in (cand.get('mains') or {}).values() for h in v)
    folded_named = any(h['folded'] for v in (cand.get('mains') or {}).values() for h in v)
    mains_claimed = any(any(abs(x - meta['mains_hz']) <= 0.5 for x in c['freqs_hz']) for c in (cand.get('candidates') or []))
    # and with every rig listed there is nothing unlisted to find
    learned_all = SG.learn_signatures(traces[0], allsrc, band, 300.0)
    none = unlisted_candidates(tl, learned_all['signatures'], band, sample_rate_hz=traces[0].sample_rate)
    ok = bool(r['status'] == 'FOUND' and best is not None and err is not None and err <= best.get('tolerance_deg', 10.0)
              and rate_err is not None and rate_err <= 0.1
              and mains_named and folded_named and not mains_claimed
              and none['status'] in ('NOTHING_UNLISTED', 'ONLY_ELECTRICAL', 'UNEXPLAINED_LINES')
              and not (none.get('candidates') or []))
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'scene': meta, 'result': r,
            'bearing_error_deg': (round(err, 2) if err is not None else None),
            'tolerance_deg': (best.get('tolerance_deg') if best else None),
            'rate_found_hz': (best['fundamental_hz'] if best else None), 'rate_true_hz': meta['hidden_pump_hz'],
            'mains_named': mains_named, 'mains_folded_named': folded_named, 'mains_claimed_as_machine': mains_claimed,
            'mains': cand.get('mains'),
            'with_every_rig_listed': {'status': none['status'], 'n_candidates': len(none.get('candidates') or [])}}


def report_text(res: dict) -> str:
    p = res.get('protocol', '')
    lines: List[str] = []
    if p.endswith('unlisted_candidates'):
        lines.append(f"unlisted [{res['status']}]: {res['n_unattributed']} of {res['n_tracks']} tracked line(s) belong to nobody on the list")
        for k, v in (res.get('mains') or {}).items():
            for hgt in v:
                lines.append(f"  {hgt['freq_hz']:g} Hz is {hgt['note']} - electrical, not a machine")
        for c in res.get('candidates', []):
            lines.append(f"  {c['source_id']}: {c['fundamental_hz']:.3f} Hz ({c['line_rate_per_min']:.0f}/min), orders "
                         + ','.join(str(n) for n in c['orders']) + f", by chance {c['by_chance']:.1%}")
        if res.get('leftover_hz'):
            lines.append('  left over: ' + ', '.join(f'{v:g}' for v in res['leftover_hz'][:10]) + ' Hz')
        lines.append('  ' + res.get('detail', ''))
    elif p.endswith('find_unlisted'):
        lines.append(f"unlisted sources [{res['status']}] at {res.get('array', '')}: " + res.get('detail', ''))
        for r in res.get('sources', []):
            lines.append(f"  {r['source_id']:<12} {r['verdict']:<14} "
                         + (f"{r['back_azimuth_deg']:6.1f} deg +/- {r.get('tolerance_deg', 0):.1f}  " if r.get('back_azimuth_deg') is not None else '                    ')
                         + f"{r['fundamental_hz']:.3f} Hz ({r['line_rate_per_min']:.0f}/min)")
            if r['verdict'] != 'POINTED':
                lines.append('    ' + (r.get('detail') or ''))
    for nm in res.get('not_a_measurement', []):
        lines.append('  not a measurement: ' + nm)
    return '\n'.join(lines)
