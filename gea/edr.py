# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""edr - the rig's electronic drilling recorder export, read as delivered.

An EDR system exports one row per second with the date and the time in two columns, then the WITS record-1
channels with their units in parentheses - `Hole Depth (feet)`, `Rate Of Penetration (ft_per_hr)`,
`Weight on Bit (klbs)`, `On Bottom (unitless)`, `Interpolated TVD (feet)` and so on. A value the recorder
did not have is `-999.25` (the WITS sentinel) or an empty cell; the file stops when the rig is off the
well and starts again when it is back, so one well's file is several sections separated by gaps of days.

This port reads that layout and nothing else (the historian port takes a timestamp in one column; a LAS or
DLIS is by depth). It streams the file - a 300 MB export is three million rows - into numeric arrays, turns
every sentinel and blank into a missing value and counts them per channel, names the sections, and keeps
the two time columns' origin so every sample has its wall-clock stamp back.

From the stream it writes the DRILLING RECORD: per section, the span, the hole depth drilled, the hours on
bottom and rotating, the rate of penetration while on bottom, the connections, the peaks; for the well, the
total depth and the recorder's own TVD at it; the table of hole depth against the recorder's interpolated
TVD, reduced to depth steps - a table the interval band can convert a measured depth with, labelled as the
recorder's interpolation and not a survey. Every number is the file's arithmetic. The record does not say
what formation was drilled, whether the well was drilled well, or anything the recorder did not write.
"""
from __future__ import annotations

import csv
import json
import math
import os
from array import array
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .ports import LiveStream, StreamChannel

SENTINEL = -999.25
GAP_SECONDS = 60.0                       # a break longer than this starts a new section (one second is the recorder's cadence)
DATE_COL, TIME_COL = 'YYYY/MM/DD', 'HH:MM:SS'
UNITS = {'feet': 'ft', 'ft': 'ft', 'ft_per_hr': 'ft/h', 'klbs': 'klbf', 'rpm': 'rpm', 'psi': 'psi', 'gal_per_min': 'gal/min',
         'kft_lb': 'kft.lbf', 'unitless': '', 'api': 'gAPI', 'spm': 'spm', 'barrels': 'bbl', 'degf': 'degF', 'ppg': 'ppg', 'm': 'm',
         'm_per_hr': 'm/h', 'kn': 'kN', 'kpa': 'kPa', 'l_per_min': 'L/min', 'knm': 'kN.m', 'm3': 'm3', 'degc': 'degC'}
# the channels the record reads by name (the header's name without its unit); a file may carry more or fewer
CH = {'hole': 'Hole Depth', 'bit': 'Bit Depth', 'rop': 'Rate Of Penetration', 'wob': 'Weight on Bit', 'rpm': 'Rotary RPM',
      'spp': 'Standpipe Pressure', 'hkld': 'Hook Load', 'dp': 'Differential Pressure', 'flow': 'Total Pump Output',
      'block': 'Block Height', 'torque': 'Convertible Torque', 'onb': 'On Bottom', 'gamma': 'Gamma', 'spm': 'Pump Total Strokes Rate',
      'trip': 'Trip Tank Mud Volume', 'tvd': 'Interpolated TVD'}


def is_edr(path: str) -> bool:
    """The layout and nothing more: the first two header cells are the date and the time."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            first = f.readline()
    except OSError:
        return False
    cells = [c.strip() for c in first.split(',')]
    return len(cells) >= 3 and cells[0].upper() == DATE_COL and cells[1].upper() == TIME_COL


def split_header(h: str) -> Tuple[str, str]:
    """`Hole Depth (feet)` -> ('Hole Depth', 'ft'); a header without a unit keeps its name and gets ''."""
    h = h.strip()
    if h.endswith(')') and '(' in h:
        name, unit = h[:h.rfind('(')].strip(), h[h.rfind('(') + 1:-1].strip()
        return name, UNITS.get(unit.lower(), unit)
    return h, ''


def _stamp(d: str, t: str) -> datetime:
    return datetime.strptime(d.strip() + ' ' + t.strip(), '%Y/%m/%d %H:%M:%S')


def read(path: str) -> LiveStream:
    """The file as a LiveStream: elapsed seconds from the first stamp, one channel per column after the two
    time columns, sentinels and blanks missing and counted. Refuses a file that is not this layout."""
    p = Path(path)
    if not is_edr(str(p)):
        raise ValueError(f'{p.name}: not an EDR export (the first two columns must be {DATE_COL} and {TIME_COL})')
    with p.open('r', newline='', encoding='utf-8', errors='replace') as f:
        rd = csv.reader(f)
        header = next(rd)
        names_units = [split_header(h) for h in header[2:]]
        ncol = len(names_units)
        cols = [array('d') for _ in range(ncol)]
        secs = array('d')
        sentinel = [0] * ncol; blank = [0] * ncol; bad = [0] * ncol
        t0 = None; prev = None; n = 0; out_of_order = 0; duplicate = 0
        bounds: List[int] = [0]
        for row in rd:
            if len(row) < 2 or not row[0].strip():
                continue
            try:
                t = _stamp(row[0], row[1])
            except ValueError:
                continue                                    # a row with no readable stamp is not a sample
            if t0 is None:
                t0 = t
            el = (t - t0).total_seconds()
            if prev is not None:
                d = el - prev
                if d < 0:
                    out_of_order += 1
                elif d == 0:
                    duplicate += 1
                elif d > GAP_SECONDS:
                    bounds.append(n)
            prev = el
            secs.append(el)
            for j in range(ncol):
                cell = row[j + 2].strip() if j + 2 < len(row) else ''
                if not cell:
                    blank[j] += 1; cols[j].append(math.nan); continue
                try:
                    x = float(cell)
                except ValueError:
                    bad[j] += 1; cols[j].append(math.nan); continue
                if x == SENTINEL:
                    sentinel[j] += 1; cols[j].append(math.nan); continue
                cols[j].append(x)
            n += 1
    if n == 0 or t0 is None:
        raise ValueError(f'{p.name}: no readable rows')
    index = np.frombuffer(secs, dtype=np.float64).copy()
    channels: Dict[str, StreamChannel] = {}
    counts = {}
    for j, (name, unit) in enumerate(names_units):
        channels[name] = StreamChannel(name=name, unit=unit, values=np.frombuffer(cols[j], dtype=np.float64).copy())
        counts[name] = {'sentinel': sentinel[j], 'blank': blank[j], 'bad': bad[j]}
    bounds.append(n)
    sections = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    meta = {'path': str(p), 'columns': str(len(header)), 'start_time': t0.isoformat(), 'end_time': (t0 + timedelta(seconds=float(index[-1]))).isoformat(),
            'rows': str(n), 'sections': json.dumps(sections), 'counts': json.dumps(counts), 'out_of_order': str(out_of_order), 'duplicate_stamps': str(duplicate),
            'layout': f'EDR one-second export: {DATE_COL} and {TIME_COL} columns, {ncol} channels with units in the header, {SENTINEL} and blank cells missing'}
    return LiveStream(name=p.stem, source_format='edr', index_kind='time_s', index=index, channels=channels, meta=meta)


# -- the drilling record ---------------------------------------------------------------------------------------------
def _ch(stream: LiveStream, key: str) -> Optional[np.ndarray]:
    c = stream.channels.get(CH[key])
    return c.values if c is not None else None


def _hours(seconds: float) -> float:
    return round(seconds / 3600.0, 2)


def _span(idx: np.ndarray, a: int, b: int) -> float:
    """Seconds covered by samples a..b-1 (the samples are one second apart; the span counts the samples, not the clock)."""
    return float(b - a)


def record(stream: LiveStream) -> dict:
    """The drilling record from an EDR stream: the file's own arithmetic, per section and for the well."""
    if stream.source_format != 'edr':
        raise ValueError('a drilling record is written from an EDR stream')
    t0 = datetime.fromisoformat(stream.meta['start_time'])
    idx = stream.index
    sections = json.loads(stream.meta['sections'])
    counts = json.loads(stream.meta['counts'])
    hole, bit, rop, wob, rpm, spp, onb, tvd, torque, hkld, flow = (_ch(stream, k) for k in ('hole', 'bit', 'rop', 'wob', 'rpm', 'spp', 'onb', 'tvd', 'torque', 'hkld', 'flow'))
    missing = [CH[k] for k in ('hole', 'onb') if _ch(stream, k) is None]
    out = {'well': stream.name, 'source': os.path.basename(stream.meta['path']), 'layout': stream.meta['layout'], 'rows': int(len(idx)),
           'start': stream.meta['start_time'], 'end': stream.meta['end_time'], 'span_days': round(float(idx[-1]) / 86400.0, 2),
           'seconds_recorded': int(len(idx)), 'hours_recorded': _hours(len(idx)), 'n_sections': len(sections), 'sections': [], 'channels': [], 'notes': [], 'lines': []}
    if missing:
        out['notes'].append('channels this record needs are not in the file: ' + ', '.join(missing))
    on = (onb == 1.0) if onb is not None else np.zeros(len(idx), dtype=bool)
    rot = on & (rpm > 0) if rpm is not None else on
    prev_end = None
    for si, (a, b) in enumerate(sections, 1):
        h = hole[a:b] if hole is not None else None
        sec = {'n': si, 'start': (t0 + timedelta(seconds=float(idx[a]))).isoformat(), 'end': (t0 + timedelta(seconds=float(idx[b - 1]))).isoformat(),
               'rows': int(b - a), 'hours': _hours(_span(idx, a, b)),
               'gap_before_hours': (_hours(float(idx[a] - idx[prev_end - 1])) if prev_end else None)}
        if h is not None and np.any(~np.isnan(h)):
            sec['hole_depth_from_ft'] = round(float(np.nanmin(h)), 1); sec['hole_depth_to_ft'] = round(float(np.nanmax(h)), 1)
            # where the hole stood when drilling began in this section: the first on-bottom sample's hole depth (a recorder restarting
            # may carry a stale depth in its first rows); the first present sample when the section never went on bottom
            ob = np.flatnonzero(o_sec & ~np.isnan(h)) if (o_sec := on[a:b]).any() else np.flatnonzero(~np.isnan(h))
            first_h = float(h[ob[0]]) if len(ob) else float(h[np.argmax(~np.isnan(h))])
            sec['hole_depth_at_start_ft'] = round(first_h, 1)
            sec['feet_drilled'] = round(sec['hole_depth_to_ft'] - first_h, 1)          # the deepest the hole got, less where it stood when the section began
            sec['feet_by_increments'] = _feet_drilled(h)                                   # the sum of the channel's own upward steps - the same unless the recorder reset
            drops = _drops(h)
            if drops:
                sec['hole_depth_drops'] = [{'at': (t0 + timedelta(seconds=float(idx[a + i]))).isoformat(), 'from_ft': round(float(h[i - 1]), 1), 'to_ft': round(float(h[i]), 1)} for i in drops[:5]]
                out['notes'].append(f"section {si}: the hole depth channel fell {len(drops)} time(s) by more than {DROP_FT:.0f} ft (first at {sec['hole_depth_drops'][0]['at'][:16]}, "
                                    f"{sec['hole_depth_drops'][0]['from_ft']:,} to {sec['hole_depth_drops'][0]['to_ft']:,} ft) - a reset or re-reference by the recorder, so the channel's "
                                    f"upward steps sum to {sec['feet_by_increments']:,} ft against {sec['feet_drilled']:,} ft from start to deepest; the record carries both and the "
                                    f"daily drilling report decides")
        o = on[a:b]; r = rot[a:b]
        sec['on_bottom_hours'] = _hours(float(o.sum())); sec['rotating_hours'] = _hours(float(r.sum()))
        if rop is not None:
            v = rop[a:b][o]; v = v[~np.isnan(v)]
            sec['rop_on_bottom_mean_ft_h'] = round(float(v.mean()), 1) if len(v) else None
            sec['rop_on_bottom_median_ft_h'] = round(float(np.median(v)), 1) if len(v) else None
        if sec.get('feet_drilled') is not None and sec['on_bottom_hours']:
            sec['feet_per_on_bottom_hour'] = round(sec['feet_drilled'] / sec['on_bottom_hours'], 1)
        for key, nm in (('wob', 'wob_max_klbf'), ('rpm', 'rpm_max'), ('spp', 'spp_max_psi'), ('torque', 'torque_max_kft_lbf'), ('hkld', 'hookload_max_klbf'), ('flow', 'flow_max_gal_min')):
            arr = _ch(stream, key)
            if arr is not None:
                v = arr[a:b]; v = v[~np.isnan(v)]
                sec[nm] = round(float(v.max()), 2) if len(v) else None
        # a connection: the string comes off bottom and goes back within the section (the on-bottom flag falls then rises)
        if len(o) > 1:
            sec['connections'] = int(np.sum((~o[1:]) & o[:-1]))
        out['sections'].append(sec)
        prev_end = b
    if hole is not None and np.any(~np.isnan(hole)):
        td = float(np.nanmax(hole)); out['total_depth_md_ft'] = round(td, 1)
        at_td = np.flatnonzero((hole >= td - 0.5) & on)                      # on bottom at total depth: when it was drilled, not a stale row
        i_td = int(at_td[0]) if len(at_td) else int(np.nanargmax(hole))
        ob0 = np.flatnonzero(on & ~np.isnan(hole))
        out['first_hole_depth_ft'] = round(float(hole[ob0[0]]) if len(ob0) else float(hole[np.argmax(~np.isnan(hole))]), 1)
        out['reached_td'] = (t0 + timedelta(seconds=float(idx[i_td]))).isoformat()
        out['reached_td_how'] = 'the first on-bottom sample at total depth' if len(at_td) else 'the first sample at total depth (never on bottom there)'
        if tvd is not None and np.any(~np.isnan(tvd)):
            both = ~np.isnan(tvd) & ~np.isnan(hole)
            if both.any():
                j = np.flatnonzero(both)[np.nanargmax(hole[both])]           # the deepest hole depth that carries a TVD
                out['tvd_at_td_ft'] = round(float(tvd[j]), 1); out['tvd_at_td_md_ft'] = round(float(hole[j]), 1)
                if td - float(hole[j]) > 1.0:
                    out['notes'].append(f"the recorder's TVD stops at {float(hole[j]):,.1f} ft MD, {td - float(hole[j]):,.1f} ft short of total depth; the TVD at TD is the last one it wrote")
            out['tvd_max_ft'] = round(float(np.nanmax(tvd)), 1)
    out['on_bottom_hours'] = _hours(float(on.sum())); out['rotating_hours'] = _hours(float(rot.sum()))
    if hole is not None and out.get('total_depth_md_ft') is not None and out['on_bottom_hours']:
        out['feet_drilled'] = round(out['total_depth_md_ft'] - out['first_hole_depth_ft'], 1)
        out['feet_by_increments'] = _feet_drilled(hole)
        out['feet_per_on_bottom_hour'] = round(out['feet_drilled'] / out['on_bottom_hours'], 1)
    for name, ch in stream.channels.items():
        v = ch.values; ok = ~np.isnan(v)
        c = counts.get(name, {})
        out['channels'].append({'channel': name, 'unit': ch.unit, 'present': int(ok.sum()), 'coverage_pct': round(100.0 * ok.sum() / len(v), 2),
                                'sentinel': c.get('sentinel', 0), 'blank': c.get('blank', 0), 'not_numeric': c.get('bad', 0),
                                'min': (round(float(v[ok].min()), 3) if ok.any() else None), 'max': (round(float(v[ok].max()), 3) if ok.any() else None)})
    out['md_tvd'] = md_tvd_table(stream)
    if out['md_tvd']['status'] == 'OK':
        out['lateral'] = lateral_from_table(out['md_tvd']['rows'])
    out['observations'] = observations(stream)
    gl = gamma_log(stream)
    if gl is not None:
        out['gamma'] = {'status': 'OK', 'bins': int(len(gl.index)), 'depth_from_ft': float(gl.index[0]), 'depth_to_ft': float(gl.index[-1]),
                        'bins_covered_pct': float(gl.meta['bins_covered_pct']), 'samples': int(gl.meta['samples']), 'how': gl.meta['how']}
        try:
            out['gamma']['lithology'] = gamma_lithology(gl)
        except ValueError as ex:
            out['gamma']['lithology'] = {'status': 'NOT ESTIMATED', 'why': str(ex)}
    else:
        out['gamma'] = {'status': 'NO GAMMA ON BOTTOM', 'how': 'the file carries no gamma value while the on-bottom flag is set'}
    out['lines'] = lines(out)
    return out


DROP_FT = 10.0                               # a fall in hole depth larger than this is a reset, not drilling


def _feet_drilled(h: np.ndarray) -> float:
    """The sum of the positive steps in hole depth between consecutive present samples: what was drilled, by the
    recorder's own channel, whatever it was reset to in between."""
    v = h[~np.isnan(h)]
    if len(v) < 2:
        return 0.0
    d = np.diff(v)
    return round(float(d[d > 0].sum()), 1)


def _drops(h: np.ndarray) -> List[int]:
    """Indexes at which the hole depth fell by more than DROP_FT from the previous present sample."""
    out = []
    prev = None
    for i, x in enumerate(h):
        if x != x:
            continue
        if prev is not None and prev - x > DROP_FT:
            out.append(i)
        prev = x
    return out


def md_tvd_table(stream: LiveStream, step_ft: float = 100.0) -> dict:
    """Hole depth against the recorder's interpolated TVD, one row per `step_ft` of hole depth (the deepest sample
    in each step, the last time the hole was that deep). The recorder interpolates its TVD from the directional
    surveys it was given; this table is that interpolation and is labelled so - it is not a survey."""
    hole, tvd = _ch(stream, 'hole'), _ch(stream, 'tvd')
    if hole is None or tvd is None:
        return {'status': 'NO CHANNEL', 'rows': [], 'note': f'needs {CH["hole"]} and {CH["tvd"]}'}
    ok = ~np.isnan(hole) & ~np.isnan(tvd)
    if not ok.any():
        return {'status': 'NO DATA', 'rows': [], 'note': 'no sample carries both a hole depth and a TVD'}
    h, t = hole[ok], tvd[ok]
    steps = np.floor(h / step_ft).astype(int)
    rows = []
    for s in np.unique(steps):
        m = steps == s
        i = np.flatnonzero(m)[-1]                       # the last sample in the step: the deepest the hole got there
        rows.append({'md_ft': round(float(h[i]), 1), 'tvd_ft': round(float(t[i]), 2)})
    rows.sort(key=lambda r: r['md_ft'])
    non_mono = sum(1 for a, b in zip(rows, rows[1:]) if b['tvd_ft'] < a['tvd_ft'] - 0.5)
    return {'status': 'OK', 'rows': rows, 'step_ft': step_ft, 'n': len(rows), 'md_min_ft': rows[0]['md_ft'], 'md_max_ft': rows[-1]['md_ft'],
            'tvd_max_ft': round(max(r['tvd_ft'] for r in rows), 2), 'tvd_decreasing_steps': non_mono,
            'source': f"the recorder's '{CH['tvd']}' channel against '{CH['hole']}', one row per {step_ft:.0f} ft of hole depth; an interpolation the recorder "
                      "made from the directional surveys it was given, not a survey, and not a measurement by this program",
            'samples_with_both': int(ok.sum()), 'samples_without_tvd': int(np.isnan(tvd).sum())}


def lateral_from_table(rows: List[dict]) -> dict:
    """Where the hole stopped going down: the first table row at which TVD is within 1% of its maximum, read as the
    start of the lateral; the lateral length is total MD minus that MD. Arithmetic on the table, named as such."""
    tmax = max(r['tvd_ft'] for r in rows)
    first = next((r for r in rows if r['tvd_ft'] >= 0.99 * tmax), rows[-1])
    return {'tvd_max_ft': round(tmax, 1), 'lateral_start_md_ft': first['md_ft'], 'lateral_length_ft': round(rows[-1]['md_ft'] - first['md_ft'], 1),
            'how': 'the first 100-ft step at which the recorder\'s TVD is within 1% of its maximum; a landing point is the directional survey\'s to give'}


# -- the gamma log the recorder carries -------------------------------------------------------------------------------
GAMMA_BIN_FT = 1.0
ROP_SPIKE_FT_H = 1000.0


def gamma_log(stream: LiveStream, bin_ft: float = GAMMA_BIN_FT) -> Optional[LiveStream]:
    """The recorder's gamma channel as a log by depth: the on-bottom samples that carry a gamma value, binned to
    `bin_ft` of bit depth, the median per bin and the sample count per bin. Indexed to BIT depth: the gamma sensor
    sits some distance behind the bit on the drilling assembly, a distance the recorder does not state, so the log
    is shallow by that offset and says so. None when the file carries no gamma on bottom."""
    g, bd, onb = _ch(stream, 'gamma'), _ch(stream, 'bit'), _ch(stream, 'onb')
    if g is None or bd is None:
        return None
    on = (onb == 1.0) if onb is not None else np.ones(len(g), dtype=bool)
    m = on & ~np.isnan(g) & ~np.isnan(bd)
    if not m.any():
        return None
    b = np.floor(bd[m] / bin_ft).astype(np.int64)
    gv = g[m]
    order = np.argsort(b, kind='stable')
    b, gv = b[order], gv[order]
    edges = np.flatnonzero(np.diff(b)) + 1
    starts = np.concatenate([[0], edges]); ends = np.concatenate([edges, [len(b)]])
    depth = (b[starts] * bin_ft).astype(np.float64)
    med = np.array([np.median(gv[a:e]) for a, e in zip(starts, ends)])
    cnt = (ends - starts).astype(np.float64)
    lo, hi = int(b[starts][0]), int(b[starts][-1])
    meta = {'path': stream.meta['path'], 'index_channel': 'DEPT', 'index_units': 'ft', 'bin_ft': str(bin_ft), 'bins': str(len(depth)),
            'bins_covered_pct': f'{100.0 * len(depth) / (hi - lo + 1):.1f}', 'samples': str(int(m.sum())),
            'how': f"the recorder's '{CH['gamma']}' while the on-bottom flag is set, binned to {bin_ft:g} ft of '{CH['bit']}': the median per bin; "
                   f"indexed to bit depth, so shallow by the sensor's distance behind the bit, which the recorder does not state",
            'source_format': 'edr'}
    return LiveStream(name=stream.name + '_gamma', source_format='edr_gamma', index_kind='depth', index=depth,
                      channels={'GR': StreamChannel(name='GR', unit='gAPI', values=med), 'GR_N': StreamChannel(name='GR_N', unit='', values=cnt)}, meta=meta)


def write_las(log: LiveStream, path: str, well: str, source: str) -> None:
    """A LAS 2.0 file of a depth-indexed stream (the form `gea dlis --to-las` writes)."""
    idx = log.index
    with open(path, 'w', encoding='utf-8') as f:
        f.write('~VERSION INFORMATION\n VERS.   2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0\n WRAP.   NO  : ONE LINE PER DEPTH STEP\n')
        f.write('~WELL INFORMATION\n')
        f.write(f" STRT.ft     {float(idx[0]):.4f} : START\n STOP.ft     {float(idx[-1]):.4f} : STOP\n STEP.ft     {float(log.meta.get('bin_ft', 0.0)):.4f} : STEP (bins; a missing bin is a gap, not a step)\n NULL.       -999.25 : NULL VALUE\n")
        f.write(f" WELL.       {well} : WELL (the recorder's job number until the operator declares the well)\n SRC .       EDR {source} : SOURCE (gea edr; {log.meta.get('how', '')})\n")
        f.write('~CURVE INFORMATION\n DEPT    .ft       : INDEX (bit depth bin)\n')
        for c in log.channels:
            f.write(f" {c:<8s}.{log.channels[c].unit:<8s} : {('gamma, median of the bin' if c == 'GR' else 'samples in the bin')}\n")
        f.write('~A\n')
        for i in range(len(idx)):
            f.write(f"{float(idx[i]):.4f}" + ''.join(f" {(-999.25 if log.channels[c].values[i] != log.channels[c].values[i] else float(log.channels[c].values[i])):.4f}" for c in log.channels) + '\n')


def gamma_lithology(log: LiveStream, cutoff: float = 0.5) -> dict:
    """The lithology-from-GR arithmetic this program already applies to a catalogue log, on the recorder's gamma log."""
    from .gamma import shale_volume, formation_flags, METHOD_LABEL
    g = log.channels['GR'].values
    vsh, meta = shale_volume(g)
    iv = formation_flags(log.index, vsh, cutoff=cutoff)
    fin = g[~np.isnan(g)]
    return {'status': 'OK', 'bins': int(len(g)), 'depth_from_ft': float(log.index[0]), 'depth_to_ft': float(log.index[-1]),
            'gr_api': {'min': round(float(fin.min()), 1), 'max': round(float(fin.max()), 1), 'p50': round(float(np.median(fin)), 1)},
            'vsh': meta, 'cutoff': cutoff, 'n_intervals': len(iv), 'sand_ft': round(sum(x['base_depth'] - x['top_depth'] for x in iv if x['flag'] == 'SAND'), 1),
            'shale_ft': round(sum(x['base_depth'] - x['top_depth'] for x in iv if x['flag'] == 'SHALE'), 1),
            'intervals': iv[:400], 'intervals_truncated': max(0, len(iv) - 400),
            'how': f"{METHOD_LABEL}; picks from the log's own 5th and 95th percentiles; a sand/shale flag per bin at a {cutoff:g} cutoff - a gamma index, "
                   "not a formation name, on a log indexed to bit depth"}


# the quantities a recorder cannot sensibly write below zero, and the one it writes negative by design
_NON_NEGATIVE = ('rop', 'wob', 'rpm', 'spp', 'hkld', 'flow', 'block', 'torque', 'spm', 'trip', 'hole', 'bit')


def observations(stream: LiveStream) -> List[dict]:
    """What the channels carry that a reader should see before trusting a number: values below zero where the quantity
    cannot be, rate-of-penetration spikes, the differential pressure's negative stretches (the recorder's reference,
    not a fault). Counted and named; nothing is altered or removed."""
    out = []
    for key in _NON_NEGATIVE:
        v = _ch(stream, key)
        if v is None:
            continue
        n = int(np.nansum(v < 0))
        if n:
            out.append({'channel': CH[key], 'what': 'below zero', 'samples': n, 'min': round(float(np.nanmin(v)), 2),
                        'note': 'a block height or depth below zero is a calibration offset in the recorder; the value is carried as written' if key in ('block', 'hole', 'bit') else 'carried as written'})
    rop = _ch(stream, 'rop')
    if rop is not None:
        n = int(np.nansum(rop > ROP_SPIKE_FT_H))
        if n:
            out.append({'channel': CH['rop'], 'what': f'above {ROP_SPIKE_FT_H:,.0f} ft/h', 'samples': n, 'max': round(float(np.nanmax(rop)), 1),
                        'note': 'instantaneous rate spikes at a connection or a depth correction; the on-bottom mean and median in the sections are the rates to read'})
    dp = _ch(stream, 'dp')
    if dp is not None:
        n = int(np.nansum(dp < 0))
        if n:
            out.append({'channel': CH['dp'], 'what': 'below zero', 'samples': n, 'min': round(float(np.nanmin(dp)), 1),
                        'note': "the recorder's differential pressure is standpipe pressure less an off-bottom reference; it is negative with the pumps off by design"})
    return out


def lines(rec: dict) -> List[str]:
    L = [f"Drilling record for {rec['well']} from {rec['source']}: {rec['rows']:,} one-second rows, {rec['start'][:16]} to {rec['end'][:16]} "
         f"({rec['span_days']} days on the clock, {rec['hours_recorded']:,} hours recorded in {rec['n_sections']} section(s))."]
    if rec.get('total_depth_md_ft') is not None:
        L.append(f"Total depth {rec['total_depth_md_ft']:,} ft MD reached {rec['reached_td'][:16]}" + (f", the recorder's TVD there {rec['tvd_at_td_ft']:,} ft" if rec.get('tvd_at_td_ft') else '')
                 + f"; {rec.get('feet_drilled', 0):,} ft drilled in {rec['on_bottom_hours']:,} hours on bottom ({rec['rotating_hours']:,} rotating)"
                 + (f", {rec['feet_per_on_bottom_hour']:,} ft per on-bottom hour" if rec.get('feet_per_on_bottom_hour') else '') + '.')
    for s in rec['sections']:
        L.append(f"Section {s['n']}: {s['start'][:16]} to {s['end'][:16]}, {s['hours']:,} h" + (f" after a {s['gap_before_hours']:,} h gap" if s.get('gap_before_hours') else '')
                 + (f"; hole {s['hole_depth_at_start_ft']:,} to {s['hole_depth_to_ft']:,} ft ({s['feet_drilled']:,} ft)" if s.get('feet_drilled') is not None else '')
                 + f"; on bottom {s['on_bottom_hours']:,} h" + (f", ROP on bottom mean {s['rop_on_bottom_mean_ft_h']:,} ft/h" if s.get('rop_on_bottom_mean_ft_h') is not None else '')
                 + (f"; {s['connections']} connections" if s.get('connections') is not None else '') + '.')
    if rec.get('lateral'):
        la = rec['lateral']
        L.append(f"Lateral: the recorder's TVD reaches within 1% of its {la['tvd_max_ft']:,} ft maximum at {la['lateral_start_md_ft']:,} ft MD; {la['lateral_length_ft']:,} ft of hole beyond it. {la['how']}.")
    mt = rec.get('md_tvd') or {}
    if mt.get('status') == 'OK':
        L.append(f"MD-TVD table: {mt['n']} rows at {mt['step_ft']:.0f} ft steps, {mt['md_min_ft']:,} to {mt['md_max_ft']:,} ft MD; {mt['samples_without_tvd']:,} samples carry no TVD. {mt['source']}.")
    ga = rec.get('gamma') or {}
    if ga.get('status') == 'OK':
        li = ga.get('lithology') or {}
        L.append(f"Gamma log: {ga['bins']:,} one-foot bins from {ga['depth_from_ft']:,.0f} to {ga['depth_to_ft']:,.0f} ft bit depth ({ga['bins_covered_pct']:.1f}% of the span covered) from "
                 f"{ga['samples']:,} on-bottom samples - written as gamma_log.las; {ga['how']}."
                 + (f" Lithology from GR: picks {li['vsh']['gr_clean_api']} / {li['vsh']['gr_shale_api']} gAPI, {li['n_intervals']} intervals, {li['sand_ft']:,} ft flagged SAND and {li['shale_ft']:,} ft SHALE at the {li['cutoff']:g} cutoff - a gamma index, not a formation name."
                    if li.get('status') == 'OK' else (f" Lithology from GR: NOT ESTIMATED - {li.get('why', '')}." if li else '')))
    elif ga:
        L.append(f"Gamma log: {ga['status']} - {ga.get('how', '')}.")
    for o in rec.get('observations', []):
        L.append(f"Observation: {o['channel']} {o['what']} in {o['samples']:,} samples" + (f" (min {o['min']:,})" if 'min' in o else '') + (f" (max {o['max']:,})" if 'max' in o else '') + f" - {o['note']}.")
    gaps = [c for c in rec['channels'] if c['sentinel'] or c['blank'] or c['not_numeric']]
    if gaps:
        L.append('Missing values: ' + '; '.join(f"{c['channel']} {c['sentinel']:,} sentinel, {c['blank']:,} blank" + (f", {c['not_numeric']:,} not numeric" if c['not_numeric'] else '') for c in gaps) + '.')
    for n in rec['notes']:
        L.append('Note: ' + n + '.')
    L.append("Every number above is the recorder's own, read as delivered; this record does not say what was drilled through, how the well was drilled, or anything the recorder did not write.")
    return L


def document(rec: dict, program_version: str):
    """The drilling record as a client report."""
    from .client_reports import Document, Section, Table, PROGRAM_NAME
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    rid = f"EDR-{rec['well']}-{now.replace('-', '').replace(':', '')}"
    front = [['Report ID', rid], ['Well', rec['well']], ['Source file', rec['source']], ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {program_version}'],
             ['Rows', f"{rec['rows']:,}"], ['Recorded', f"{rec['start'][:19]} to {rec['end'][:19]}"]]
    secs = [Section('1', 'The well', [l for l in rec['lines'][:2]]),
            Section('2', 'Sections', [rec['lines'][i] for i in range(2, 2 + len(rec['sections']))],
                    [Table(['#', 'Start', 'End', 'Hours', 'Gap before (h)', 'Hole at start (ft)', 'Hole deepest (ft)', 'Feet drilled', 'On bottom (h)', 'Rotating (h)', 'ROP on bottom mean (ft/h)', 'Connections', 'WOB max (klbf)', 'RPM max', 'SPP max (psi)'],
                           [[s['n'], s['start'][:16], s['end'][:16], s['hours'], s.get('gap_before_hours'), s.get('hole_depth_at_start_ft'), s.get('hole_depth_to_ft'), s.get('feet_drilled'), s['on_bottom_hours'], s['rotating_hours'],
                             s.get('rop_on_bottom_mean_ft_h'), s.get('connections'), s.get('wob_max_klbf'), s.get('rpm_max'), s.get('spp_max_psi')] for s in rec['sections']], 'One row per section of the file')])]
    mt = rec.get('md_tvd') or {}
    if mt.get('status') == 'OK':
        secs.append(Section('3', 'Hole depth against the recorder\'s TVD', [l for l in rec['lines'] if l.startswith('Lateral:') or l.startswith('MD-TVD table:')],
                            [Table(['MD (ft)', 'TVD (ft)'], [[r['md_ft'], r['tvd_ft']] for r in mt['rows']], mt['source'])]))
    ga = rec.get('gamma') or {}
    if ga.get('status') == 'OK':
        li = ga.get('lithology') or {}
        secs.append(Section(str(len(secs) + 1), "The recorder's gamma log", [l for l in rec['lines'] if l.startswith('Gamma log:')],
                            ([Table(['Top (ft)', 'Base (ft)', 'Flag', 'Bins', 'Mean Vsh'], [[x['top_depth'], x['base_depth'], x['flag'], x['n_samples'], x['mean_vsh']] for x in li['intervals'][:120]],
                                    f"the first {min(120, len(li['intervals']))} of {li['n_intervals']} intervals; the whole list is in drilling_record.json")] if li.get('status') == 'OK' else [])))
    obs = rec.get('observations') or []
    secs.append(Section(str(len(secs) + 1), 'Channels', [l for l in rec['lines'] if l.startswith('Missing values:')],
                        ([Table(['Channel', 'What', 'Samples', 'Note'], [[o['channel'], o['what'], o['samples'], o['note']] for o in obs], 'Counted and named; nothing altered')] if obs else []) +
                        [Table(['Channel', 'Unit', 'Present', 'Coverage %', 'Sentinel', 'Blank', 'Not numeric', 'Min', 'Max'],
                               [[c['channel'], c['unit'], c['present'], c['coverage_pct'], c['sentinel'], c['blank'], c['not_numeric'], c['min'], c['max']] for c in rec['channels']],
                               f'A sentinel is {SENTINEL}, the value the recorder writes when it has none')]))
    secs.append(Section(str(len(secs) + 1), 'What this record will not say', [rec['lines'][-1]]))
    return Document(title=f'{PROGRAM_NAME} - Drilling Record', report_id=rid, front=front, sections=secs,
                    footer="The recorder's own numbers, read as delivered.",
                    data={k: v for k, v in rec.items() if k != 'lines'} | {'_records': [], '_catalogue_obj': None})


def write_record(stream: LiveStream, out_dir: str, program_version: str) -> dict:
    """The record beside the well's other reports: drilling_record.json / .md / .html, and the MD-TVD table as CSV."""
    from .client_reports import write
    rec = record(stream)
    os.makedirs(out_dir, exist_ok=True)
    paths = write(document(rec, program_version), out_dir, basename='drilling_record')
    with open(os.path.join(out_dir, 'drilling_record.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1, default=str)
    gl = gamma_log(stream)
    if gl is not None:
        lp = os.path.join(out_dir, 'gamma_log.las')
        write_las(gl, lp, stream.name, os.path.basename(stream.meta['path']))
        paths['gamma_las'] = lp
    if rec['md_tvd']['status'] == 'OK':
        tp = os.path.join(out_dir, 'md_tvd.csv')
        with open(tp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['md_ft', 'tvd_ft', 'source'])
            for r in rec['md_tvd']['rows']:
                w.writerow([r['md_ft'], r['tvd_ft'], "EDR interpolated TVD (not a survey)"])
        paths['md_tvd_csv'] = tp
    paths['record'] = rec
    return paths


def selftest(tmp: str, rows_per_section: int = 3000) -> dict:
    """A synthetic EDR export in this layout - three sections with gaps, a sentinel gamma, blank TVD, a lateral -
    written, read back and recorded. The gate runs this; the port is proven on a real export on the machine that holds one."""
    p = os.path.join(tmp, 'edr_selftest.csv')
    t = datetime(2026, 6, 1, 0, 0, 0)
    hdr = [DATE_COL, TIME_COL, 'Hole Depth (feet)', 'Bit Depth (feet)', 'Rate Of Penetration (ft_per_hr)', 'Weight on Bit (klbs)', 'Rotary RPM (RPM)', 'Standpipe Pressure (psi)',
           'Hook Load (klbs)', 'Differential Pressure (psi)', 'Total Pump Output (gal_per_min)', 'Block Height (feet)', 'Convertible Torque (kft_lb)', 'On Bottom (unitless)',
           'Gamma (api)', 'Pump Total Strokes Rate (SPM)', 'Trip Tank Mud Volume (barrels)', 'Interpolated TVD (feet)']
    depth = 1500.0
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(hdr)
        for s in range(3):
            if s:
                t += timedelta(days=2)                         # the rig is off the well for two days between sections
            for i in range(rows_per_section):
                on = 1 if (i % 120) < 100 else 0                # a 20-second connection every 120 seconds
                if on:
                    depth += 600.0 / 3600.0                     # 600 ft/h while on bottom (a self-test cadence, not a rig's)
                tvd = depth if depth < 2000.0 else 2000.0 + (depth - 2000.0) * 0.02   # a lateral past 2,000 ft
                row = [t.strftime('%Y/%m/%d'), t.strftime('%H:%M:%S'), f'{depth:.1f}', f'{depth - (0 if on else 30):.1f}', '600.00' if on else '0.00',
                       '20.0' if on else '0.0', '60.00' if on else '0.00', '2500.00', '150.0', '400.00', '500.00', '50.00', '10.000', str(on),
                       '-999.25' if s == 0 else f'{(45.0 if depth < 2300.0 else 115.0) + (i % 7):.2f}', '100', '0.00' if i % 500 == 0 else '45.00', '' if (i % 10 == 0) else f'{tvd:.2f}']
                w.writerow(row); t += timedelta(seconds=1)
    st = read(p)
    rec = record(st)
    return {'path': p, 'stream': st.summary(), 'record': rec}
