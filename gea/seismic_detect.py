# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_detect - the detectability test: at what distance does a known rig show in this record?

The second leg's first number. Before any mapping is promised, one station's
continuous record is held against a list of KNOWN sources - rigs with a
position and an active window from a public permit register or the
operator's own schedule - and the test answers, source by source: did the
drilling band's power rise above the quiet baseline while this rig, and only
this rig, was working? The farthest rig that did and the nearest that did not
bracket the station's detection radius. That radius is measured here, never
assumed, and everything the leg can later claim sits inside it.

Verdicts per source:
    DETECTED              band power in the source's exclusive windows exceeds the quiet baseline
                          by snr_db at the median, in at least half of those windows
    NOT_DETECTED          it does not
    AMBIGUOUS             the source was never active alone - its windows cannot be attributed to it
    INSUFFICIENT_WINDOWS  fewer exclusive windows than the floor

Status of the whole test:
    OK                    a quiet baseline exists and at least one source was judged
    NO_QUIET_BASELINE     no window without an active source - nothing to compare against;
                          the test needs a longer record or a fuller source list

Standard physics, stated: the observable is band-limited power (Welch PSD
integrated over the drilling band) per window; the comparison is a median
and a percentile; the distance is the great-circle distance. The synthetic
scene used by the self-test assumes body-wave geometric spreading (1/r) and
anelastic attenuation exp(-pi f r / (Q v)) for its own tones - an assumption
of the scene, labelled SIMULATION_SELF_TEST, not a property claimed of any
ground.

What this test will not call a measurement: a well's position or track (one
station detects, it does not locate - locating needs an array and is the next
step, after this number exists); a radius larger than the farthest source on
the ground-truth list; the detection of a source that has no window of its
own; anything about a source that is not on the list.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .seismic import Trace, band_power, iso, noise_floor_db, parse_time

EARTH_RADIUS_KM = 6371.0088
DRILLING_BAND_HZ: Tuple[float, float] = (1.0, 50.0)     # mud pumps and their harmonics, rotary, top drive, engines: where rig machinery lines sit


@dataclass
class Source:
    """A known source: a rig (or any machine) with a position and the window it was working."""
    source_id: str
    lat: float
    lon: float
    start: float          # epoch s UTC
    end: float
    kind: str = 'rig'
    note: str = ''        # where the ground truth came from (a permit number, an operator schedule)
    datum: str = 'WGS84'  # the datum lat/lon are on AFTER loading: a row on another datum is converted and its note says so
    datum_as_given: str = ''   # what the file said, before the conversion

    def to_dict(self) -> dict:
        d = asdict(self)
        d['start'], d['end'] = iso(self.start, 0), iso(self.end, 0)
        return d


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def load_sources_csv(path: str, datum: Optional[str] = None) -> List[Source]:
    """Columns: source_id, lat, lon, start_utc, end_utc [, kind, note, datum]. One row per rig per working window.

    A `datum` column (or the `datum` argument for the whole file) says which datum the positions are on; the
    row is converted to WGS84 on the way in and its note records the shift. A row that says nothing is taken
    as given and marked UNKNOWN - which is a statement that nobody said, not a claim that it is WGS84."""
    from . import geodesy as GD
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        rd = csv.DictReader(f)
        need = {'source_id', 'lat', 'lon', 'start_utc', 'end_utc'}
        missing = need - set(c.strip() for c in (rd.fieldnames or []))
        if missing:
            raise ValueError(f"sources CSV lacks columns {sorted(missing)}; the columns are source_id, lat, lon, start_utc, end_utc[, kind, note]")
        for row in rd:
            row = {k.strip(): (v or '').strip() for k, v in row.items() if k}
            if not row.get('source_id'):
                continue
            d_in = GD.datum_name(row.get('datum') or datum or '')
            la, lo, note = float(row['lat']), float(row['lon']), row.get('note') or ''
            if d_in not in ('WGS84', 'UNKNOWN'):
                c = GD.to_wgs84(la, lo, 0.0, d_in)
                la, lo = c['lat'], c['lon']
                note = '; '.join([x for x in (note, f"converted from {d_in} to WGS84, moved {c['shift_m']:.1f} m "
                                                    f"(this conversion is good to about {c['accuracy_m']:.0f} m)") if x])
            out.append(Source(row['source_id'], la, lo, parse_time(row['start_utc']), parse_time(row['end_utc']),
                              row.get('kind') or 'rig', note, 'WGS84' if d_in != 'UNKNOWN' else 'UNKNOWN', d_in))
    return out


def write_sources_csv(sources: Sequence[Source], path: str) -> str:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['source_id', 'lat', 'lon', 'start_utc', 'end_utc', 'kind', 'note', 'datum'])
        for s in sources:
            w.writerow([s.source_id, f'{s.lat:.6f}', f'{s.lon:.6f}', iso(s.start, 0), iso(s.end, 0), s.kind, s.note, s.datum])
    return path


def _lines_in_windows(spec: dict, idx: np.ndarray, band: Tuple[float, float], snr_db: float, floor_width_hz: float = 2.0) -> Tuple[np.ndarray, np.ndarray]:
    """Per in-band bin over the chosen windows: the fraction of windows in which it stands snr_db above
    its local floor, and its median excess (dB)."""
    f = spec['freqs']
    m = (f >= band[0]) & (f <= band[1])
    fb = f[m]
    rows = spec['psd_db'][idx][:, m]
    if rows.shape[0] == 0:
        return np.zeros(int(m.sum())), np.zeros(int(m.sum()))
    excess = np.vstack([row - noise_floor_db(row, fb, floor_width_hz) for row in rows])
    return (excess >= snr_db).mean(axis=0), np.median(excess, axis=0)


def _peaks(idx: np.ndarray, excess: np.ndarray, fb: np.ndarray) -> List[float]:
    """Runs of adjacent bins collapse to one line each, at the bin with the largest excess."""
    out = []
    run: List[int] = []
    for j in list(idx) + [None]:
        if j is not None and (not run or j == run[-1] + 1):
            run.append(int(j))
            continue
        if run:
            best = max(run, key=lambda q: excess[q])
            out.append(round(float(fb[best]), 3))
        run = [int(j)] if j is not None else []
    return out


def detectability_test(tr: Trace, station_lat: float, station_lon: float, sources: Sequence[Source], band: Tuple[float, float] = DRILLING_BAND_HZ,
                       win_s: float = 600.0, snr_db: float = 6.0, min_windows: int = 3, line_snr_db: float = 6.0) -> dict:
    """One station, one record, a list of known sources -> a verdict per source and the bracketed radius."""
    bp = band_power(tr, band, win_s)
    times, pdb, spec = bp['times'], bp['power_db'], bp['spec']
    nwin = len(times)
    active = np.zeros((len(sources), nwin), dtype=bool)
    for i, s in enumerate(sources):
        active[i] = (times >= s.start) & (times <= s.end)
    n_active = active.sum(axis=0)
    quiet = n_active == 0
    out = {'protocol': 'seismic_detect.detectability_test', 'station': {'id': tr.id, 'lat': station_lat, 'lon': station_lon},
           'record': {'start': iso(tr.starttime, 0), 'end': iso(tr.endtime, 0), 'sample_rate_hz': tr.sample_rate, 'npts': tr.npts, 'gaps': len(tr.gaps),
                      'unit': tr.unit_label},
           'band_hz': list(band), 'win_s': win_s, 'snr_db': snr_db, 'min_windows': min_windows, 'windows': int(nwin),
           'quiet_windows': int(quiet.sum()), 'sources': [], 'n_sources': len(sources)}
    if quiet.sum() < min_windows:
        out['status'] = 'NO_QUIET_BASELINE'
        out['detail'] = (f"{int(quiet.sum())} window(s) with no source active (floor {min_windows}): there is nothing to compare against. "
                         "A longer record, or the hours when every listed rig was down, is needed before any source can be judged.")
        out.update(_closing(None, None, [], band))
        return out
    q_med = float(np.median(pdb[quiet]))
    q_p95 = float(np.percentile(pdb[quiet], 95))
    out['quiet_baseline'] = {'median_db': round(q_med, 2), 'p95_db': round(q_p95, 2), 'spread_db': round(float(np.percentile(pdb[quiet], 95) - np.percentile(pdb[quiet], 5)), 2)}
    quiet_lines, _ = _lines_in_windows(spec, np.where(quiet)[0], band, line_snr_db)
    f = spec['freqs']
    fb = f[(f >= band[0]) & (f <= band[1])]
    judged = []
    for i, s in enumerate(sources):
        excl = active[i] & (n_active == 1)
        d_km = haversine_km(station_lat, station_lon, s.lat, s.lon)
        row = {'source_id': s.source_id, 'kind': s.kind, 'distance_km': round(d_km, 3), 'active_windows': int(active[i].sum()),
               'exclusive_windows': int(excl.sum()), 'note': s.note}
        if active[i].sum() == 0:
            row['verdict'] = 'INSUFFICIENT_WINDOWS'
            row['detail'] = 'the source was not active inside this record'
        elif excl.sum() == 0:
            row['verdict'] = 'AMBIGUOUS'
            row['detail'] = 'never active alone: every window of this source has another source working - the record cannot say which one it hears'
        elif excl.sum() < min_windows:
            row['verdict'] = 'INSUFFICIENT_WINDOWS'
            row['detail'] = f'{int(excl.sum())} exclusive window(s), floor {min_windows}'
        else:
            med = float(np.median(pdb[excl]))
            excess = med - q_med
            frac = float((pdb[excl] > q_p95).mean())
            row.update({'median_power_db': round(med, 2), 'excess_over_quiet_db': round(excess, 2), 'fraction_above_quiet_p95': round(frac, 3)})
            if excess >= snr_db and frac >= 0.5:
                row['verdict'] = 'DETECTED'
                row['detail'] = f'band power {excess:+.1f} dB over the quiet median in {frac:.0%} of {int(excl.sum())} exclusive windows'
                # lines that belong to these windows and not to the quiet ones
                fl, fx = _lines_in_windows(spec, np.where(excl)[0], band, line_snr_db)
                own = np.where((fl >= 0.5) & (quiet_lines < 0.2))[0]
                row['lines_hz'] = _peaks(own, fx, fb)[:20]
                row['lines_note'] = 'bins above their floor in these windows and not in the quiet ones; consistent with this source, not proof of it'
            else:
                row['verdict'] = 'NOT_DETECTED'
                row['detail'] = f'band power {excess:+.1f} dB over the quiet median, above its 95th percentile in {frac:.0%} of windows (needs >= {snr_db:g} dB and 50 %)'
            judged.append(row)
        out['sources'].append(row)
    out['status'] = 'OK' if judged else 'NO_SOURCE_JUDGED'
    det = [r['distance_km'] for r in judged if r['verdict'] == 'DETECTED']
    miss = [r['distance_km'] for r in judged if r['verdict'] == 'NOT_DETECTED']
    out.update(_closing(max(det) if det else None, min(miss) if miss else None, judged, band))
    return out


def _closing(farthest_detected: Optional[float], nearest_missed: Optional[float], judged: List[dict], band) -> dict:
    r = {'farthest_detected_km': None if farthest_detected is None else round(farthest_detected, 3),
         'nearest_not_detected_km': None if nearest_missed is None else round(nearest_missed, 3)}
    if farthest_detected is None and nearest_missed is None:
        r['radius'] = 'no source judged - no radius'
    elif farthest_detected is None:
        r['radius'] = f'no source detected; the nearest listed source ({nearest_missed:.1f} km) was not heard - the radius at this station is below that'
    elif nearest_missed is None:
        r['radius'] = f'every judged source detected out to {farthest_detected:.1f} km; the radius is at least that and is not bounded above by this list'
    elif nearest_missed > farthest_detected:
        r['radius'] = f'between {farthest_detected:.1f} km (farthest detected) and {nearest_missed:.1f} km (nearest not detected)'
    else:
        r['radius'] = (f'not a single number here: a source at {nearest_missed:.1f} km was missed while one at {farthest_detected:.1f} km was heard '
                       '(rig size, depth of drilling, path or noise differ between them) - the table, not a radius, is the result')
    r['basis'] = ('band-limited power per window (Welch PSD integrated over the band) compared with the median and 95th percentile of the windows '
                  'with no listed source active; great-circle distance from the station to each listed position')
    r['not_a_measurement'] = ['a well position or track - one station detects, it does not locate; locating needs an array',
                              'a radius beyond the farthest source on the ground-truth list',
                              'any source that is absent from the list, or that never worked alone',
                              f'anything outside the band {band[0]:g}-{band[1]:g} Hz']
    return r


def report_text(res: dict) -> str:
    L = [f"detectability test - {res['station']['id']} at ({res['station']['lat']:.4f}, {res['station']['lon']:.4f})",
         f"  record {res['record']['start']} to {res['record']['end']}, {res['record']['sample_rate_hz']:g} Hz, {res['record']['gaps']} gap(s); "
         f"{res['windows']} windows of {res['win_s']:g} s, {res['quiet_windows']} quiet; band {res['band_hz'][0]:g}-{res['band_hz'][1]:g} Hz; threshold {res['snr_db']:g} dB",
         f"  status {res['status']}" + (f" - {res['detail']}" if res.get('detail') else '')]
    if res.get('quiet_baseline'):
        qb = res['quiet_baseline']
        L.append(f"  quiet baseline: median {qb['median_db']:.1f} dB, p95 {qb['p95_db']:.1f} dB")
    for r in sorted(res['sources'], key=lambda r: r['distance_km']):
        ex = f" {r['excess_over_quiet_db']:+.1f} dB" if 'excess_over_quiet_db' in r else ''
        lines = f"  lines {', '.join(f'{x:g}' for x in r['lines_hz'][:6])} Hz" if r.get('lines_hz') else ''
        L.append(f"  {r['source_id']:16s} {r['distance_km']:7.1f} km  {r['verdict']:20s}{ex}  ({r['exclusive_windows']} excl. windows){lines}")
    L.append(f"  radius: {res['radius']}")
    L.append(f"  basis: {res['basis']}")
    L.append("  not a measurement: " + '; '.join(res['not_a_measurement']))
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# the synthetic scene - SIMULATION_SELF_TEST
# ---------------------------------------------------------------------------
def synthetic_scene(seed: int = 3, fs: float = 50.0, hours: float = 10.0, station=(31.0, -102.0), q_factor: float = 100.0, v_km_s: float = 2.5,
                    distances_km: Sequence[float] = (3.0, 8.0, 20.0, 45.0, 90.0)) -> Tuple[Trace, List[Source], dict]:
    """A station, five rigs at increasing distance, each working alone for an hour, quiet hours between.
    Each rig carries its own machinery lines (pump stroke rate and harmonics, rotary, engine), attenuated
    by 1/r and exp(-pi f r / (Q v)) - the SCENE's assumption, labelled, so the arithmetic can be checked."""
    rng = np.random.default_rng(seed)
    n = int(hours * 3600 * fs)
    t = np.arange(n) / fs
    # coloured background: red-ish noise plus white
    white = rng.normal(0.0, 1.0, n)
    red = np.cumsum(rng.normal(0.0, 0.02, n)); red -= np.linspace(red[0], red[-1], n)
    x = 30.0 * white + 30.0 * red / (np.std(red) + 1e-12)
    t0 = parse_time('2025-06-01T00:00:00Z')
    sources: List[Source] = []
    lines = {}
    lat0, lon0 = station
    for k, d in enumerate(distances_km):
        # place the rig d km east-north-east of the station
        bearing = math.radians(30.0 + 40.0 * k)
        dlat = (d / EARTH_RADIUS_KM) * math.cos(bearing)
        dlon = (d / EARTH_RADIUS_KM) * math.sin(bearing) / math.cos(math.radians(lat0))
        lat, lon = lat0 + math.degrees(dlat), lon0 + math.degrees(dlon)
        s0 = t0 + (2 * k + 1) * 3600.0          # hour 1, 3, 5, 7, 9 - quiet hours 0, 2, 4, 6, 8
        src = Source(f'RIG-{k + 1}', lat, lon, s0, s0 + 3600.0, 'rig', 'synthetic scene')
        sources.append(src)
        pump = 1.4 + 0.25 * k                   # strokes per second, different per rig
        rotary = 1.0 + 0.3 * k
        engine = 18.0 + 2.5 * k
        comps = [(pump, 1.0), (2 * pump, 0.6), (3 * pump, 0.4), (4 * pump, 0.25), (rotary, 0.5), (engine, 0.8), (2 * engine, 0.3)]
        comps = [c for c in comps if c[0] < 0.8 * fs / 2]      # a recorder's anti-alias filter: nothing above 80 % of Nyquist
        lines[src.source_id] = [round(c[0], 3) for c in comps]
        m = (t + t0 >= s0) & (t + t0 < s0 + 3600.0)
        a0 = 6000.0                             # source amplitude in counts at 1 km (the scene's number)
        for f_hz, rel in comps:
            amp = a0 * rel / d * math.exp(-math.pi * f_hz * d / (q_factor * v_km_s))
            x[m] += amp * np.sin(2 * np.pi * f_hz * t[m] + rng.uniform(0, 2 * np.pi))
    tr = Trace('XX', 'SYN', '', 'HHZ', t0, fs, np.round(x).astype(np.int32), source='synthetic_scene', encoding='SYNTHETIC')
    meta = {'status': 'SIMULATION_SELF_TEST', 'q_factor': q_factor, 'v_km_s': v_km_s, 'spreading': '1/r (body wave)', 'a0_counts_at_1km': 6000.0,
            'lines_hz': lines, 'note': 'the scene is an assumption for checking the arithmetic; it says nothing about any ground'}
    return tr, sources, meta


def selftest(seed: int = 3) -> dict:
    """SIMULATION_SELF_TEST: near rigs detected, the farthest not, the radius bracketed between them."""
    tr, sources, meta = synthetic_scene(seed)
    res = detectability_test(tr, 31.0, -102.0, sources, win_s=600.0)
    verdicts = {r['source_id']: r['verdict'] for r in res['sources']}
    ok = (res['status'] == 'OK' and verdicts.get('RIG-1') == 'DETECTED' and verdicts.get('RIG-2') == 'DETECTED'
          and verdicts.get('RIG-5') == 'NOT_DETECTED' and res['farthest_detected_km'] is not None and res['nearest_not_detected_km'] is not None
          and res['nearest_not_detected_km'] > res['farthest_detected_km'])
    return {'status': 'SIMULATION_SELF_TEST', 'ok': ok, 'scene': meta, 'result': res}
