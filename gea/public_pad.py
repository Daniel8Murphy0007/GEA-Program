# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""public_pad - a real pad from public data: the Utah FORGE wells' drilling records made into a site of their own.

Every page of this program can be tried on labelled records it makes itself, and on a client's records nobody
else may see. Between the two there was nothing a figure could be quoted from. The Utah FORGE project (the U.S.
Department of Energy's geothermal field laboratory near Milford, Utah) publishes the rig recorders of its wells
under Creative Commons Attribution 4.0: five wells through the same sediments and granite, drilled between 2017
and 2023. This module turns those published files into a workspace - a site like any other - so the rig time, the
roadmap, the drilling alerts and the benchmark run on real records whose figures anyone may check and repeat.

- THE FILES are not in the program and are not fetched by it. The site's operator downloads them from the
  Geothermal Data Repository in a browser and puts them in one folder; this module names each file, where it is,
  its size and the SHA-256 it had when this release was checked against it. A file whose hash differs is still
  used, and said to differ (the repository revises its files).
- THREE of the five are the recorder's own one-second export and are attached as they are. They carry no
  on-bottom flag; the drilling record takes on bottom as the bit within 0.1 ft of the hole depth, and says so.
- 16B(78)-32 is a ten-second export of over a hundred channels inside a zip: the channels the program reads are
  written out, and the true vertical depth is added from the well's own published survey when its zip is in the
  folder (the hole is deviated 65 degrees; without the survey its measured depth stands in, and that is said).
- 58-32 is the 2017 recorder in another layout (two columns a quantity, in two unit systems): the U.S.-unit
  columns are written out under the names the program reads. It has no weight on bit and no differential
  pressure, and those pages say so for it.
- 16A(78)-32 is a different recorder's layout and has no bit depth at all. The bit depth is RECONSTRUCTED: at the
  hole depth while the on-bottom flag is set; unmoved while the slips are set; otherwise moved by what the block
  moved. It is exact on bottom and drifts by feet over a long trip, so this well's trip speeds are approximate,
  and the site's record says the channel was reconstructed and how.

What it writes: the workspace, with each well attached and `records/public_pad.json` - the dataset, the licence,
the credit, each source file with its size and hash as found, and what was done to it. Nothing is sent anywhere.

What it will not call a measurement: the reconstructed bit depth of 16A(78)-32; the measured depth standing in
for true vertical depth where a record has neither a TVD channel nor a survey beside it; and its own hashes as a
warrant of the data - they say the file is the one this release was checked against, nothing about what the
recorder recorded.
"""
from __future__ import annotations

import bisect
import hashlib
import io
import json
import os
import threading
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

GDR = 'https://gdr.openei.org/submissions/'
LICENCE = {'name': 'Creative Commons Attribution 4.0 International (CC BY 4.0)', 'url': 'https://creativecommons.org/licenses/by/4.0/'}
CREDIT = ('Utah FORGE (Frontier Observatory for Research in Geothermal Energy), Energy & Geoscience Institute at the University of Utah, funded by the U.S. Department of Energy; '
          'published in the Geothermal Data Repository (gdr.openei.org).')
FORGE = {
    'id': 'utah-forge', 'name': 'Utah FORGE', 'site_name': 'Utah FORGE - public data',
    'about': "The U.S. Department of Energy's geothermal field laboratory near Milford, Utah: five wells through the same basin fill and granite, 2017 to 2023, with their rig recorders published.",
    'licence': LICENCE, 'credit': CREDIT,
    'wells': [
        {'well': '58-32', 'year': 2017, 'page': GDR + '1113', 'title': 'Utah FORGE: Drilling Data for Student Competition', 'file': 'Well_58-32_raw_pason_log.csv', 'bytes': 632712512,
         'sha256': '5af0241d325b9cf5b4d6a9d524218cb2cbc3694fa324816cfb879f1f556161c2', 'format': 'forge-58-32', 'what': 'vertical pilot well; one-second recorder in the 2017 layout'},
        {'well': '16A(78)-32', 'year': 2020, 'page': GDR + '1283', 'doi': '10.15121/1776602', 'title': 'Utah FORGE: Well 16A(78)-32 Drilling Data', 'file': '16A(78)-32_time_data_1s_intervals.csv',
         'bytes': 1245123633, 'sha256': 'a5b3cd699eacd2b900ab1a1d96ce5c2d628fda613129b888fa81ccf3eea6ef8f', 'format': 'forge-16a', 'what': 'injection well, deviated 65 degrees; one-second record without a bit depth'},
        {'well': '56-32', 'year': 2021, 'page': GDR + '1295', 'title': 'Utah FORGE: Well 56-32 Drilling Data and Logs', 'file': '56-32 1sec data 27029986.csv', 'bytes': 357142224,
         'sha256': 'fed92fa2a75c3a2c600dca558aa0e6c38d871f242fa98f1f09a06279e7357f4c', 'format': 'edr', 'what': 'vertical monitoring well; the recorder\'s one-second export'},
        {'well': '78B-32', 'year': 2021, 'page': GDR + '1330', 'title': 'Utah FORGE: Well 78B-32 Daily Drilling Reports and Logs', 'file': '78B-32 1 sec data 27200701.csv', 'bytes': 411194824,
         'sha256': '98818c7cac9f44b6ad7ef84111c576c2654fd2fbc6eb7779847c8afc64eeb2ae', 'format': 'edr', 'what': 'vertical monitoring well; the recorder\'s one-second export'},
        {'well': '16B(78)-32', 'year': 2023, 'page': GDR + '1516', 'doi': '10.15121/1998591', 'title': 'Utah FORGE: Well 16B(78)-32 Drilling Data', 'file': '16B_Pason.zip', 'member': '10 Second Data.csv',
         'bytes': 135676899, 'sha256': '858a6eaf56c2cb3cbf45b59f431e59f82fc0ef2502ced244608d4d7b76ac54a2', 'format': 'zip-edr', 'what': 'production well, deviated 65 degrees; ten-second export in a zip',
         'survey_file': '16B(78)-32 Well Survey.zip', 'survey_member': '16B(78)-32 Final Survey Report.txt', 'survey_bytes': 608878,
         'survey_sha256': '38c59c9452da709fa971448699eb46fb13f452d2aa8c08fbe88fe73f08ce5302'},
    ],
    # the stimulation of the two deviated wells: each stage's pumping record, second by second, as the service companies gave it
    'treatments': [
        {'well': '16A(78)-32', 'year': 2022, 'page': GDR + '1379', 'doi': '10.15121/1871203', 'title': 'Utah FORGE: Well 16A(78)-32 Stimulation Data (April, 2022)',
         'file': '16A78-32_Stimulation_Stage 1.zip', 'bytes': 10606717, 'sha256': '6f4e164ddbeb5b2ba2f0d5bf11ae6beac3bddc3ade8d78a516206980d1112eeb',
         'members': {'1': 'Stage 1/University of Utah Forge 16A(78)-32 Stage 1.xlsx'}},
        {'well': '16A(78)-32', 'year': 2022, 'page': GDR + '1379', 'doi': '10.15121/1871203', 'title': 'Utah FORGE: Well 16A(78)-32 Stimulation Data (April, 2022)',
         'file': '16A78-32_Stimulation_Stage 2.zip', 'bytes': 12239514, 'sha256': '4bc2dd974eda150f3f1f8c136b2ad0c445e35c3655358df31095ca2021216239',
         'members': {'2': 'Stage 2/University of Utah Forge 16A(78)-32 Stage 2.xlsx'}},
        {'well': '16A(78)-32', 'year': 2022, 'page': GDR + '1379', 'doi': '10.15121/1871203', 'title': 'Utah FORGE: Well 16A(78)-32 Stimulation Data (April, 2022)',
         'file': '16A78-32_Stimulation_Stage 3.zip', 'bytes': 15423876, 'sha256': 'aeb7bc754126a263c13369345ec3329caaaf0c0a81a05406a2418d55bdda78e6',
         'members': {'3': 'Stage 3/University of Utah Forge 16A(78)-32 Stage 3.xlsx'}},
        {'well': '16A(78)-32', 'year': 2024, 'page': GDR + '1611', 'doi': '10.15121/2371032', 'title': 'Utah FORGE: Wells 16A(78)-32 and 16B(78)-32 Stimulation Pressure and Circulation Data April, 2024',
         'file': '16A Frac Data.zip', 'bytes': 25037870, 'sha256': '9511881e1c2b5fe52b60d47d9f3c0fd70722807a062c39625743c52f425560f4',
         'members': {k: f'16A(78)-32_Stage {k}_Treatment Data.xlsx' for k in ('3R', '4', '5', '6A', '6B', '7', '8', '9', '10')}},
        {'well': '16B(78)-32', 'year': 2024, 'page': GDR + '1611', 'doi': '10.15121/2371032', 'title': 'Utah FORGE: Wells 16A(78)-32 and 16B(78)-32 Stimulation Pressure and Circulation Data April, 2024',
         'file': '16B Frac Data.zip', 'bytes': 3123455, 'sha256': '6e820e388dc93432d5268ab56093c48c83f6666122ec1482218e048e14962e0a',
         'members': {k: f'16B(78)-32_Stage {k}_Treatment Data.xlsx' for k in ('1', '2', '3', '4')}},
    ],
}
DATASETS = {FORGE['id']: FORGE}
SUBFOLDERS = ('', 'stimulation')
# the recorder channels the program reads, by the header's name without its unit
KEEP = ('Hole Depth', 'Bit Depth', 'Rate Of Penetration', 'Weight on Bit', 'Rotary RPM', 'Standpipe Pressure', 'Hook Load', 'Differential Pressure', 'Total Pump Output', 'Block Height',
        'Convertible Torque', 'Rotary Torque', 'On Bottom', 'Gamma', 'Interpolated TVD', 'Trip Tank Mud Volume', 'Total Mud Volume', 'Inclination', 'Azimuth', 'Pump Total Strokes Rate', 'Flow', 'PVT Total Mud Gain/Loss')
SENTINELS = ('-999.25', '-9999.00', '-9999', '-999.250000')
MONTHS = {m: f'{i + 1:02d}' for i, m in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'))}
_LOCK = threading.Lock()
_JOB: Dict[str, Any] = {'state': 'idle'}


class PublicPadError(ValueError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def dataset(dataset_id: str = 'utah-forge') -> dict:
    if dataset_id not in DATASETS:
        raise PublicPadError(f'no public dataset {dataset_id!r}: {", ".join(DATASETS)}')
    return DATASETS[dataset_id]


# --------------------------------------------------------------------------------------------------------------
# the folder: which files are there
# --------------------------------------------------------------------------------------------------------------
def sha256_of(path: str, progress: Optional[Callable[[int], None]] = None) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(4 * 1024 * 1024)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


def _find(folder: str, name: str) -> Optional[str]:
    """A dataset file in the folder, or in its `stimulation` folder."""
    for sub in SUBFOLDERS:
        p = os.path.join(folder, sub, name) if sub else os.path.join(folder, name)
        if folder and os.path.isfile(p):
            return p
    return None


def treatment_status(folder: str, dataset_id: str = 'utah-forge', verify: bool = False) -> List[dict]:
    """The stimulation files against the folder, as `status` does the recorder files. They are optional."""
    rows = []
    for x in dataset(dataset_id).get('treatments', []):
        p = _find(folder, x['file'])
        r = {'well': x['well'], 'year': x['year'], 'file': x['file'], 'page': x['page'], 'doi': x.get('doi'), 'title': x['title'], 'bytes_expected': x['bytes'], 'sha256_expected': x['sha256'],
             'stages': list(x['members']), 'present': p is not None, 'bytes': None, 'size_matches': None, 'sha256': None, 'hash_matches': None}
        if p is not None:
            r['bytes'] = os.path.getsize(p); r['size_matches'] = r['bytes'] == x['bytes']
            if verify:
                r['sha256'] = sha256_of(p); r['hash_matches'] = r['sha256'] == x['sha256']
        rows.append(r)
    return rows


def status(folder: str, dataset_id: str = 'utah-forge', verify: bool = False) -> dict:
    """Each of the dataset's files against the folder: there or not, its size against the size this release was
    checked with, and - with `verify` - its SHA-256 against the one it had then."""
    ds = dataset(dataset_id)
    folder = str(folder or '').strip().strip('"')
    rows = []
    for w in ds['wells']:
        p = os.path.join(folder, w['file']) if folder else ''
        r = {'well': w['well'], 'year': w['year'], 'what': w['what'], 'file': w['file'], 'page': w['page'], 'doi': w.get('doi'), 'title': w['title'], 'bytes_expected': w['bytes'], 'sha256_expected': w['sha256'],
             'format': w['format'], 'present': bool(p) and os.path.isfile(p), 'bytes': None, 'size_matches': None, 'sha256': None, 'hash_matches': None}
        if r['present']:
            r['bytes'] = os.path.getsize(p); r['size_matches'] = r['bytes'] == w['bytes']
            if verify:
                r['sha256'] = sha256_of(p); r['hash_matches'] = r['sha256'] == w['sha256']
        if w.get('survey_file'):
            sp = os.path.join(folder, w['survey_file']) if folder else ''
            r['survey'] = {'file': w['survey_file'], 'present': bool(sp) and os.path.isfile(sp), 'bytes_expected': w['survey_bytes'], 'page': w['page'], 'optional': True,
                           'why': 'the true vertical depth of this deviated hole; without it the measured depth stands in'}
        rows.append(r)
    n = sum(1 for r in rows if r['present'])
    tr = treatment_status(folder, dataset_id, verify)
    nt = sum(1 for r in tr if r['present'])
    sv = survey_status(folder)
    ns = sum(1 for r in sv if r['present'] and r['well'])
    return {'dataset': ds['id'], 'name': ds['name'], 'about': ds['about'], 'licence': ds['licence'], 'credit': ds['credit'], 'folder': folder, 'folder_exists': bool(folder) and os.path.isdir(folder),
            'files': rows, 'present': n, 'ready': n >= 2, 'total_bytes': sum(w['bytes'] for w in ds['wells']), 'treatments': tr, 'treatments_present': nt,
            'treatment_bytes': sum(x['bytes'] for x in ds.get('treatments', [])), 'surveys': sv, 'surveys_present': ns,
            'lines': [f"{ds['name']}: {n} of {len(rows)} recorder files are in the folder" + ('' if n >= 2 else '; two or more make a pad') + '.',
                      f"{nt} of {len(tr)} stimulation files are in the folder or its stimulation folder (optional: the stages' pumping records, for the Frac treatment tab).",
                      f"{ns} of {len(SURVEYS)} survey files are in the folder (optional: they place the wells, for the 3D, Collision and Map tabs)" + (
                          '' if sv[-1]['present'] else f"; {LOCATIONS['file']}, the field's GPS survey of its wells, is not") + '.',
                      f"Licence: {ds['licence']['name']}. Credit: {ds['credit']}"]}


# --------------------------------------------------------------------------------------------------------------
# the converters: a published file to the recorder export the program reads
# --------------------------------------------------------------------------------------------------------------
def _name(h: str) -> str:
    return h.split(' (')[0].strip()


def survey_tvd(text: str) -> Tuple[List[float], List[float]]:
    """The MD and TVD columns of a published survey report: the rows of numbers under a heading line that names both."""
    md, tvd = [], []
    cols: Optional[List[str]] = None
    for line in text.splitlines():
        parts = line.split()
        if cols is None:
            if 'MD' in parts and 'TVD' in parts:
                cols = parts
            continue
        if len(parts) < max(cols.index('MD'), cols.index('TVD')) + 1:
            continue
        try:
            a, b = float(parts[cols.index('MD')]), float(parts[cols.index('TVD')])
        except ValueError:
            continue
        if md and a <= md[-1]:
            continue
        md.append(a); tvd.append(b)
    if len(md) < 2:
        raise PublicPadError('the survey report has no table of MD and TVD this program can read')
    return md, tvd


def _interp(x: float, xs: List[float], ys: List[float]) -> float:
    if x <= xs[0]:
        return ys[0] + (x - xs[0])
    if x >= xs[-1]:
        return ys[-1] + (x - xs[-1]) * ((ys[-1] - ys[-2]) / (xs[-1] - xs[-2]))
    i = bisect.bisect_right(xs, x)
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])


def convert_edr_slim(lines: Iterable[str], out, survey: Optional[Tuple[List[float], List[float]]] = None, tick: Optional[Callable[[int], None]] = None) -> dict:
    """A recorder export with its date and time columns, written out with only the channels the program reads; the
    true vertical depth added from the survey (interpolated at the hole depth) when one is given."""
    it = iter(lines)
    hdr = next(it).rstrip('\r\n').split(',')
    if len(hdr) < 3 or hdr[0].strip().upper() != 'YYYY/MM/DD' or hdr[1].strip().upper() != 'HH:MM:SS':
        raise PublicPadError('not a recorder export: the first two columns are not the date and the time')
    keep = [0, 1] + [i for i, h in enumerate(hdr) if i > 1 and _name(h) in KEEP]
    names = [hdr[i].strip() for i in keep]
    i_hole = next((i for i, h in enumerate(hdr) if _name(h) == 'Hole Depth'), None)
    add_tvd = survey is not None and i_hole is not None and 'Interpolated TVD' not in [_name(h) for h in hdr]
    out.write(','.join(names + (['Interpolated TVD (feet)'] if add_tvd else [])) + '\n')
    n = 0; last_d = None; last_t = ''
    hi = max(keep)
    for line in it:
        c = line.rstrip('\r\n').split(',')
        if len(c) <= hi:
            continue
        row = [c[i] for i in keep]
        if add_tvd:
            try:
                d = float(c[i_hole])
                if d != last_d:
                    last_d = d; last_t = f'{_interp(d, survey[0], survey[1]):.2f}' if d > -900 else ''
                row.append(last_t)
            except ValueError:
                row.append('')
        out.write(','.join(row) + '\n')
        n += 1
        if tick and n % 200000 == 0:
            tick(n)
    return {'rows': n, 'channels': [_name(x) for x in names[2:]] + (['Interpolated TVD'] if add_tvd else []), 'tvd_from_survey': bool(add_tvd),
            'notes': (['the true vertical depth is interpolated at the hole depth from the well\'s published survey'] if add_tvd else [])}


def convert_58_32(lines: Iterable[str], out, tick: Optional[Callable[[int], None]] = None) -> dict:
    """The 2017 recorder layout (Date, Time, each quantity in feet and metres, pounds and kilograms...) to the export the
    program reads: the U.S.-unit columns under their names, the sentinel -9999 left blank."""
    it = iter(lines)
    hdr = [h.strip() for h in next(it).rstrip('\r\n').split(',')]
    want = (('Depth (ft)', 'Hole Depth (feet)'), ('Bit Depth (ft)', 'Bit Depth (feet)'), ('ROP(1 ft)', 'Rate Of Penetration (ft_per_hr)'), ('Rotary Speed (rpm)', 'Rotary RPM (RPM)'),
            ('Pump Pressure (psi)', 'Standpipe Pressure (psi)'), ('Hookload (k lbs)', 'Hook Load (klbs)'), ('Flow In (gal/min)', 'Total Pump Output (gal_per_min)'), ('Block Ht (ft)', 'Block Height (feet)'))
    if hdr[:2] != ['Date', 'Time'] or 'Depth (ft)' not in hdr or 'Bit Depth (ft)' not in hdr:
        raise PublicPadError('not the 58-32 recorder layout: it starts Date, Time and has Depth (ft) and Bit Depth (ft)')
    idx = [(hdr.index(a), b) for a, b in want if a in hdr]
    out.write('YYYY/MM/DD,HH:MM:SS,' + ','.join(b for _, b in idx) + '\n')
    n = 0; hi = max(i for i, _ in idx)
    for line in it:
        c = line.rstrip('\r\n').split(',')
        if len(c) <= hi:
            continue
        d = c[0].split('/')
        if len(d) != 3:
            continue
        yy = d[2] if len(d[2]) == 4 else '20' + d[2]
        tm = c[1] if len(c[1]) == 8 else c[1].zfill(8)
        vals = [('' if c[i] in SENTINELS or c[i].startswith('-9999') else c[i]) for i, _ in idx]
        out.write(f'{yy}/{int(d[0]):02d}/{int(d[1]):02d},{tm},' + ','.join(vals) + '\n')
        n += 1
        if tick and n % 200000 == 0:
            tick(n)
    return {'rows': n, 'channels': [_name(b) for _, b in idx],
            'notes': ['the 2017 layout written out under the names the program reads, U.S. units; this recorder has no weight on bit and no differential pressure in its time record']}


def convert_16a(lines: Iterable[str], out, tick: Optional[Callable[[int], None]] = None) -> dict:
    """The 16A(78)-32 recorder layout to the export the program reads, with the bit depth RECONSTRUCTED: the hole
    depth while the on-bottom flag is set; unmoved while the slips are set; otherwise moved by what the block moved
    (a block step of more than 15 ft in a sample is a re-set of the block, not pipe)."""
    it = iter(lines)
    hdr = [h.strip() for h in next(it).rstrip('\r\n').split(',')]
    need = ('Time', 'Block Position', 'Hookload', 'Slips Set', 'On Bottom', 'Total Depth')
    miss = [x for x in need if x not in hdr]
    if miss:
        raise PublicPadError('not the 16A(78)-32 recorder layout: it has no ' + ', '.join(miss))
    ix = {h: i for i, h in enumerate(hdr)}
    cols = (('Total Depth', 'Hole Depth (feet)', 1.0), (None, 'Bit Depth (feet)', 1.0), ('ROP Depth/Hour', 'Rate Of Penetration (ft_per_hr)', 1.0), ('Weight on Bit', 'Weight on Bit (klbs)', 1.0),
            ('Top Drive RPM', 'Rotary RPM (RPM)', 1.0), ('Pump Pressure', 'Standpipe Pressure (psi)', 1.0), ('Hookload', 'Hook Load (klbs)', 1.0), ('Differential Pressure', 'Differential Pressure (psi)', 1.0),
            ('Flow In', 'Total Pump Output (gal_per_min)', 1.0), ('Block Position', 'Block Height (feet)', 1.0), ('Top Drive Torque (ft-lbs)', 'Convertible Torque (kft_lb)', 0.001),
            ('On Bottom', 'On Bottom (unitless)', 1.0), ('Depth Hole TVD', 'Interpolated TVD (feet)', 1.0), ('MWD Gamma (API)', 'Gamma (api)', 1.0))
    cols = tuple(c for c in cols if c[0] is None or c[0] in ix)
    out.write('YYYY/MM/DD,HH:MM:SS,' + ','.join(c[1] for c in cols) + '\n')
    i_t, i_blk, i_slip, i_onb, i_hole = ix['Time'], ix['Block Position'], ix['Slips Set'], ix['On Bottom'], ix['Total Depth']
    hi = max(ix[c[0]] for c in cols if c[0])
    bit: Optional[float] = None; prev_blk: Optional[float] = None
    n = 0; on_rows = 0; moved = 0.0

    def num(s: str) -> Optional[float]:
        if not s or s in SENTINELS:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
        return None if v <= -999.0 else v
    for line in it:
        c = line.rstrip('\r\n').split(',')
        if len(c) <= hi:
            continue
        t = c[i_t]                                              # 25/Oct/2020 12:38:20
        if len(t) != 20 or t[3:6] not in MONTHS:
            continue
        hole, blk = num(c[i_hole]), num(c[i_blk])
        onb, slip = c[i_onb] in ('1', '1.0', '1.00'), c[i_slip] in ('1', '1.0', '1.00')
        if onb and hole is not None:
            bit = hole; on_rows += 1
        elif bit is not None and not slip and blk is not None and prev_blk is not None:
            d = prev_blk - blk                                  # the block down is the bit down
            if -15.0 <= d <= 15.0:
                bit += d; moved += abs(d)
                if hole is not None and bit > hole:
                    bit = hole
                if bit < 0.0:
                    bit = 0.0
        if blk is not None:
            prev_blk = blk
        vals = []
        for src, _, k in cols:
            if src is None:
                vals.append('' if bit is None else f'{bit:.2f}')
                continue
            v = num(c[ix[src]])
            vals.append('' if v is None else (c[ix[src]] if k == 1.0 else f'{v * k:.4f}'))
        out.write(f'{t[7:11]}/{MONTHS[t[3:6]]}/{t[0:2]},{t[12:20]},' + ','.join(vals) + '\n')
        n += 1
        if tick and n % 200000 == 0:
            tick(n)
    return {'rows': n, 'channels': [_name(c[1]) for c in cols], 'bit_depth_reconstructed': True, 'on_bottom_rows': on_rows,
            'notes': ['the bit depth is RECONSTRUCTED, not recorded: the hole depth while the on-bottom flag is set, unmoved in the slips, otherwise moved by the block; exact on bottom, approximate over a trip',
                      'the surface torque is the top drive\'s, in kft-lb; the true vertical depth is the recorder\'s own']}


def _text_lines(path: str, member: Optional[str] = None):
    """The lines of a file, or of a member of a zip, as text - read as a stream, never whole."""
    if member is None:
        return open(path, 'r', encoding='utf-8', errors='replace', newline=''), None
    z = zipfile.ZipFile(path)
    if member not in z.namelist():
        z.close()
        raise PublicPadError(f'{os.path.basename(path)} has no {member} in it')
    return io.TextIOWrapper(z.open(member), encoding='utf-8', errors='replace', newline=''), z


def convert(w: dict, folder: str, out_path: str, tick: Optional[Callable[[int], None]] = None) -> dict:
    """One well's published file to the recorder export at `out_path`. Returns what was done."""
    src = os.path.join(folder, w['file'])
    fh, z = _text_lines(src, w.get('member'))
    try:
        with open(out_path, 'w', encoding='utf-8', newline='') as out:
            if w['format'] == 'forge-58-32':
                r = convert_58_32(fh, out, tick)
            elif w['format'] == 'forge-16a':
                r = convert_16a(fh, out, tick)
            elif w['format'] == 'zip-edr':
                survey = None; notes = []
                sp = os.path.join(folder, w.get('survey_file') or '')
                if w.get('survey_file') and os.path.isfile(sp):
                    with zipfile.ZipFile(sp) as sz:
                        survey = survey_tvd(sz.read(w['survey_member']).decode('utf-8', 'replace'))
                else:
                    notes.append('no survey beside it: this deviated hole has no true vertical depth in its record, and its measured depth stands in on the pages that need one')
                r = convert_edr_slim(fh, out, survey, tick)
                r['notes'] = ['the channels the program reads, written out of the published export of over a hundred'] + r['notes'] + notes
            else:
                raise PublicPadError(f"no converter for {w['format']}")
    finally:
        fh.close()
        if z is not None:
            z.close()
    return r


# --------------------------------------------------------------------------------------------------------------
# the site
# --------------------------------------------------------------------------------------------------------------
def record(ws) -> Optional[dict]:
    p = os.path.join(ws.path, 'records', 'public_pad.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def suggest(current_ws_path: str, dataset_id: str = 'utah-forge') -> dict:
    """Where the files and the site most likely are, so that the form opens filled in: a `forge` folder in the user's
    Downloads (or the Downloads folder itself) that has two or more of the dataset's files in it; and for the site, the
    public-data site this computer already knows, or a `forge-site` folder beside the current workspace."""
    from . import workspaces as WSS
    ds = dataset(dataset_id)
    home = os.path.expanduser('~')
    folder = ''
    for c in (os.path.join(home, 'Downloads', 'forge'), os.path.join(home, 'Downloads'), os.path.join(home, 'Documents', 'forge')):
        try:
            if os.path.isdir(c) and sum(1 for w in ds['wells'] if os.path.isfile(os.path.join(c, w['file']))) >= 2:
                folder = c
                break
        except OSError:
            continue
    path = ''
    try:
        for r in WSS.listing(current_ws_path):
            if r.get('path') and os.path.isfile(os.path.join(r['path'], 'records', 'public_pad.json')):
                path = r['path']
                break
    except Exception:
        path = ''
    made = bool(path)
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(current_ws_path)), 'forge-site')
    return {'folder': folder, 'path': path, 'site_exists': made}


# --------------------------------------------------------------------------------------------------------------
# the wells placed: each published survey read, with the wellhead it names
# --------------------------------------------------------------------------------------------------------------
USFT = 0.3048006096012192
FT_M = 0.3048
DECLINATION_DEG = 11.29                 # the magnetic declination the 16A(78)-32 survey report gives for this pad (2020)
LOCATIONS = {'file': 'Utah FORGE Well and Seismic Locations.csv', 'bytes': 4438, 'sha256': '7bfc9cf730b8521a71a878ab38381a772ea06821e20f07c06c7ee5e3a5351471', 'page': GDR + '1358',
             'doi': '10.15121/1838418', 'title': 'Utah FORGE: Updated Well, Well Pad, and Seismic Station GPS Coordinates December, 2021', 'points': {'16A(78)-32': '16A-32-WELL'}}
SURVEYS = {
    '58-32': {'files': ('Directional_Survey_well_58_32.zip',), 'member': 'DirectionalSurveyRpt_171027103004.pdf', 'format': 'listing-pdf', 'bytes': 425858,
              'sha256': 'b705a123a89ef619c305ee25d42dfbebd34ed2a51f1b39e914acd9f5e14b518d', 'page': GDR + '1006', 'needs_locations': 'its wellhead and its elevation'},
    '16A(78)-32': {'files': ('16A(78)-32 Survey.xlsx',), 'format': 'compass-xlsx', 'bytes': 48839, 'sha256': 'ad03773329769f702f748c96244a9e1cba16cd1d762d4e8b93b8c8fea9d36b8d', 'page': GDR + '1283'},
    '56-32': {'files': ('Well 56-32 trajectory survey data.zip',), 'member': '56-32 DirectionalSurveyRpt_210323105345.pdf', 'positions': '56-32 well_survey_final.csv', 'format': 'listing-pdf',
              'bytes': 106287, 'sha256': 'c7ec6b7bd091cf898a91cdfaac4824f19fde801f82cc8bc7c879dac19d7060cd', 'page': GDR + '1295'},
    '78B-32': {'files': ('78B-32 directional survey (1).zip', '78B-32 directional survey.zip'), 'member': 'directional survey78B.csv', 'format': 'stations-csv', 'bytes': 76688,
               'sha256': '8a94ad6ad8124d281bfd033119765a024c9b66e06c8b39c9f999436b2a7dd854', 'page': GDR + '1330', 'needs_locations': 'its elevation'},
    '16B(78)-32': {'files': ('16B(78)-32 Well Survey.zip',), 'member': '16B(78)-32 Final Survey Report.txt', 'format': 'report-txt', 'bytes': 608878,
                   'sha256': '38c59c9452da709fa971448699eb46fb13f452d2aa8c08fbe88fe73f08ce5302', 'page': GDR + '1516'},
}


def _f(x) -> Optional[float]:
    try:
        return float(str(x).replace(',', '').strip())
    except ValueError:
        return None


def pdf_text_rows(content: bytes) -> List[List[str]]:
    """The words of a PDF whose pages are plain compressed text - each word placed by its own position - as rows read
    left to right, top to bottom. Nothing is drawn or laid out: a PDF made another way gives no rows."""
    import re
    import zlib
    out: List[List[str]] = []
    for m in re.finditer(rb'stream\r?\n', content):
        s = m.end(); e = content.find(b'endstream', s)
        if e < 0:
            continue
        try:
            d = zlib.decompress(content[s:e])
        except zlib.error:
            continue
        if b'BT' not in d:
            continue
        items = []
        for t in re.finditer(rb'([-\d.]+)\s+([-\d.]+)\s+Tm\b(?:(?!\bTm\b).)*?\(((?:\\.|[^\\)])*)\)\s*Tj', d, re.S):
            txt = re.sub(rb'\\(.)', rb'\1', t.group(3)).decode('latin-1')
            items.append((float(t.group(2)), float(t.group(1)), txt))
        items.sort(key=lambda i: (-i[0], i[1]))
        rows: List[list] = []
        for y, x, txt in items:
            if rows and abs(rows[-1][0] - y) <= 2.0:
                rows[-1][1].append((x, txt))
            else:
                rows.append([y, [(x, txt)]])
        out.extend([c for _, c in sorted(r[1])] for r in rows)
    return out


def read_survey_listing_pdf(content: bytes) -> dict:
    """A directional survey report printed to PDF - a survey type, then measured depth, inclination, azimuth, true
    vertical depth, northing and easting on each line. Such a report names no wellhead and no elevation."""
    rows = pdf_text_rows(content)
    head = next((i for i, r in enumerate(rows) if 'Inc.' in r and 'Azimuth' in r and 'TVD' in r and 'N-S' in r and 'E-W' in r), None)
    if head is None:
        raise PublicPadError('the survey report is not a listing this reads: no line of headings with Inc., Azimuth, TVD, N-S and E-W')
    order = [c for c in rows[head] if c in ('Depth', 'Inc.', 'Azimuth', 'TVD', 'N-S', 'E-W')]
    if order != ['Depth', 'Inc.', 'Azimuth', 'TVD', 'N-S', 'E-W']:
        raise PublicPadError(f'the survey report\'s columns are not in the order this reads: {order}')
    st = []
    for r in rows:
        c = r[1:] if r and r[0] == '**' else r
        if len(c) < 7 or not c[0].replace(' ', '').isalpha():
            continue
        v = [_f(x) for x in c[1:7]]
        if any(x is None for x in v):
            continue
        st.append({'md': v[0], 'inc': v[1], 'azi': v[2], 'tvd': v[3], 'ns': v[4], 'ew': v[5]})
    st.sort(key=lambda s: s['md'])
    if len(st) < 2:
        raise PublicPadError('the survey report has no stations this could read')
    return {'stations': st, 'north': None, 'how': []}


def read_positions_csv(text: str) -> dict:
    """A well path published as positions: UTM easting and northing in metres, measured and true vertical depth in
    feet, and the elevation at each. Its first line is the wellhead."""
    import csv as _csv
    rows = [r for r in _csv.DictReader(io.StringIO(text)) if _f(r.get('UTM_E')) is not None]
    need = ('UTM_E', 'UTM_N', 'Depth', 'Tru_Vdepth', 'Elev_msl_ft')
    if not rows or any(k not in rows[0] for k in need):
        raise PublicPadError(f"the path file needs the columns {', '.join(need)}")
    return {'easting_m': float(rows[0]['UTM_E']), 'northing_m': float(rows[0]['UTM_N']), 'elev_ft': _f(rows[0]['Elev_msl_ft']),
            'points': [(float(r['Depth']), float(r['UTM_E']), float(r['UTM_N']), float(r['Tru_Vdepth'])) for r in rows]}


def read_locations(text: str) -> Dict[str, dict]:
    """The field's GPS survey of its wells: for each point, its UTM position, latitude, longitude and elevation."""
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(text)))
    hi = next((i for i, r in enumerate(rows) if r and r[0].strip() == 'Point ID'), None)
    if hi is None:
        raise PublicPadError('the locations file has no line of headings starting with Point ID')
    h = [c.strip() for c in rows[hi]]
    need = ('Latitude', 'Longitude', 'Elevation')
    if any(k not in h for k in need):
        raise PublicPadError(f"the locations file needs the columns {', '.join(need)}")
    head = ' '.join(' '.join(r) for r in rows[:hi])
    out = {}
    for r in rows[hi + 1:]:
        v = [_f(r[h.index(k)]) if h.index(k) < len(r) else None for k in need]
        if r and r[0].strip() and all(x is not None for x in v):
            out[r[0].strip()] = {'lat': v[0], 'lon': v[1], 'elev_m': v[2], 'nad83': 'NAD 1983' in head or 'NAD83' in head}
    return out


def read_survey_report(text: str) -> dict:
    """A directional company's final survey report as text: its heading (the wellhead on a named grid, the elevation
    of the kelly bushing, the north its azimuths are from) and its stations."""
    import re
    head: Dict[str, str] = {}
    st = []
    cols: Optional[List[str]] = None
    for ln in text.splitlines():
        if ':' in ln and cols is None:
            k, v = ln.split(':', 1)
            head[' '.join(k.split()).upper()] = v.strip()
        t = ln.split()
        if cols is None:
            up = [x.upper() for x in t]
            if 'MD' in up and 'INC' in up and 'AZI' in up:
                cols = up
            continue
        v = [_f(x) for x in t]
        if len(v) >= 4 and all(x is not None for x in v[:4]):
            g = lambda name: v[cols.index(name)] if name in cols and cols.index(name) < len(v) else None
            st.append({'md': g('MD'), 'inc': g('INC'), 'azi': g('AZI'), 'tvd': g('TVD'), 'ns': g('N/S'), 'ew': g('E/W')})
    num = lambda k: _f((head.get(k) or '').lower().replace('ft', '').replace('deg', ''))
    if len(st) < 2:
        raise PublicPadError('the survey report has no stations')
    if num('WELL EASTING') is None or num('WELL NORTHING') is None:
        return {'stations': st, 'north': None, 'how': []}
    zone = head.get('MAP ZONE', '')
    m = re.search(r'Zone\s+(\d{1,2})\s*([NS])', zone)
    if 'TRANSVERSE MERCATOR' not in head.get('MAP SYSTEM', '').upper() or not m:
        raise PublicPadError(f"the survey report's map system is not a UTM zone this reads: {head.get('MAP SYSTEM')!r} {zone!r}")
    unit = USFT if 'US SURVEY FEET' in head.get('MAP SYSTEM', '').upper() else FT_M
    north = head.get('NORTH REF', '').strip().lower()
    if north not in ('grid', 'true'):
        raise PublicPadError(f"the survey report's north reference is {head.get('NORTH REF')!r}: grid or true is read")
    kb = num('KB ELEV')
    if kb is None:
        raise PublicPadError('the survey report gives no KB ELEV')
    return {'stations': st, 'easting_m': num('WELL EASTING') * unit, 'northing_m': num('WELL NORTHING') * unit, 'grid': f'UTM{int(m.group(1))}{m.group(2)}', 'datum': 'NAD83',
            'elev_ft': kb, 'elev_datum': 'KB', 'gl_ft': num('GL ELEV'), 'north': north,
            'how': [f"the wellhead is the report's WELL EASTING and NORTHING on {head.get('MAP SYSTEM')}, {zone}, {head.get('GEODIC DATUM')}",
                    f"the azimuths are from {north} north (the report's NORTH REF)", f"the depths are from the kelly bushing, {kb:,.2f} ft above mean sea level (the report's KB ELEV)"]}


def read_survey_compass(content: bytes, grid: str = 'UTM12N') -> dict:
    """A planning package's survey design report saved as a spreadsheet, a word a cell: the platform reference point,
    the elevation of the kelly bushing, the corrections applied to the azimuths, and the stations."""
    from . import xlsx_read as X
    rows = X.rows(content, X.sheets(content)[0])
    lines = [[str(c) for c in r if c != ''] for r in rows]
    txt = [' '.join(r) for r in lines]
    after = lambda r, word: _f(r[r.index(word) + 1]) if word in r and r.index(word) + 1 < len(r) else None
    plat = next((i for i, t in enumerate(txt) if 'Platform reference point' in t), None)
    kb = next((after(r, 'permanent.......:') for r in lines if r[:2] == ['KB', 'above']), None)
    gl = next((after(r, 'permanent.......:') for r in lines if r[:2] == ['GL', 'above']), None)
    conv = next((after(r, '(+E/W-).:') for r in lines if 'convergence' in r), None)
    grid_corr = any('Total az corr = magnetic dec - grid conv' in t for t in txt)
    if plat is None or kb is None:
        raise PublicPadError('the survey design report names no platform reference point, or no kelly bushing elevation')
    if not grid_corr:
        raise PublicPadError('the survey design report does not say its azimuths are corrected by the declination less the grid convergence: which north they are from is not known')
    nn = ee = None
    for r in lines[plat + 1:plat + 4]:
        if r[:1] == ['Latitude']:
            nn = after(r, '(+N/S-).........:')
        if r[:1] == ['Departure']:
            ee = after(r, '(+E/W-)........:')
    st = []
    for r in lines:
        if len(r) >= 12 and r[0].isdigit():
            v = [_f(x) for x in r[:9]]
            if all(x is not None for x in v):
                st.append({'md': v[1], 'inc': v[2], 'azi': v[3], 'tvd': v[5], 'ns': v[7], 'ew': v[8]})
    if nn is None or ee is None or len(st) < 2:
        raise PublicPadError('the survey design report has no platform northing and easting, or no stations')
    return {'stations': st, 'easting_m': ee * USFT, 'northing_m': nn * USFT, 'grid': grid, 'datum': 'NAD83', 'elev_ft': kb, 'elev_datum': 'KB', 'gl_ft': gl, 'north': 'grid', 'convergence_deg': conv,
            'grid_assumed': True,
            'how': [f"the wellhead is the report's platform reference point, {nn:,.2f} N and {ee:,.2f} E in US survey feet. The report does not name its grid: it is read on the grid the survey report of "
                    f"16B(78)-32, drilled from the same pad, names - UTM zone 12N, NAD83",
                    'the azimuths are from grid north: the report corrects them by the magnetic declination less the grid convergence' + (f' ({conv:+.2f} degrees)' if conv is not None else ''),
                    f"the depths are from the kelly bushing, {kb:,.0f} ft above mean sea level (the report's KB above permanent)"]}


def read_survey_stations_csv(text: str) -> dict:
    """A survey listing as a spreadsheet's CSV: a type, then the measured depth, inclination and azimuth of each
    station, and beside the table the wellhead's latitude and longitude."""
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(text)))
    st = []
    lat = lon = None
    datum = ''
    for i, r in enumerate(rows):
        if 'Lat' in r and 'Long' in r and i + 1 < len(rows):
            a, b = r.index('Lat'), r.index('Long')
            nx = rows[i + 1]
            lat, lon = (_f(nx[a]) if a < len(nx) else None), (_f(nx[b]) if b < len(nx) else None)
        for c in r:
            if c.strip().upper().replace(' ', '') in ('NAD83', 'NAD27', 'WGS84'):
                datum = c.strip().upper().replace(' ', '')
        if len(r) >= 7 and r[0].strip():
            v = [_f(x) for x in r[1:7]]
            if all(x is not None for x in v):
                st.append({'md': v[0], 'inc': v[1], 'azi': v[2], 'tvd': v[3], 'ns': v[4], 'ew': v[5]})
    if len(st) < 2:
        raise PublicPadError('the survey listing has no stations')
    out: Dict[str, Any] = {'stations': st, 'north': None, 'how': []}
    if lat is not None and lon is not None:
        out.update(lat=lat, lon=lon, datum=datum or 'NAD83')
        out['how'].append(f"the wellhead is the latitude and longitude written beside the table ({datum or 'no datum named: read as NAD83'})")
    return out


def _convergence_deg(lat: float, lon: float, grid: str = 'UTM12N') -> float:
    """Grid north from true north at a point of a UTM zone, degrees (east positive)."""
    import math
    import re
    zone = int(re.match(r'^UTM(\d{1,2})', grid).group(1))
    return math.degrees(math.atan(math.tan(math.radians(lon - (zone * 6 - 183))) * math.sin(math.radians(lat))))


def read_survey(well: str, folder: str) -> Optional[dict]:
    """A well's published survey from the folder, by the layout the list names, with the wellhead and the elevation it
    is placed by and where each came from. None when its file is not in the folder."""
    import math
    from . import geodesy as GD
    sv = SURVEYS.get(well)
    if not sv:
        return None
    src = next((p for p in (_find(folder, f) for f in sv['files']) if p), None)
    if src is None:
        return None
    positions = None
    if sv.get('member'):
        with zipfile.ZipFile(src) as z:
            names = z.namelist()
            if sv['member'] not in names:
                raise PublicPadError(f"{os.path.basename(src)} has no {sv['member']} in it")
            content = z.read(sv['member'])
            if sv.get('positions') and sv['positions'] in names:
                positions = read_positions_csv(z.read(sv['positions']).decode('utf-8-sig', 'replace'))
    else:
        with open(src, 'rb') as f:
            content = f.read()
    text = content.decode('utf-8-sig', 'replace')
    r = {'report-txt': lambda: read_survey_report(text), 'compass-xlsx': lambda: read_survey_compass(content), 'stations-csv': lambda: read_survey_stations_csv(text),
         'listing-pdf': lambda: read_survey_listing_pdf(content)}[sv['format']]()
    st = r['stations']
    r.update(file=os.path.basename(src), member=sv.get('member'), page=sv['page'], sha256=sha256_of(src), sha256_expected=sv['sha256'], bytes=os.path.getsize(src), sources=[])
    r['hash_matches'] = r['sha256'] == sv['sha256']
    if positions is not None:                                                    # the publisher's own positions, beside the report
        r.update(easting_m=positions['easting_m'], northing_m=positions['northing_m'], grid='UTM12N', datum='NAD83')
        r['how'].append(f"the wellhead is the first line of {sv['positions']}, beside the report in the same file: UTM zone 12N, NAD83, metres")
        by = {round(s['md'], 1): s for s in st}
        worst = None
        for md, e, n, _tv in positions['points']:
            s = by.get(round(md, 1))
            if s is not None:
                d = math.hypot(positions['easting_m'] + s['ew'] * FT_M - e, positions['northing_m'] + s['ns'] * FT_M - n) / FT_M
                worst = d if worst is None else max(worst, d)
        if worst is not None and worst <= 1.0:
            r['north'] = 'grid'; r['positions_miss_ft'] = round(worst, 2)
            r['how'].append(f"the report does not say which north its azimuths are from. The publisher's own positions in {sv['positions']} put its northings and eastings straight on the UTM grid "
                            f"(they agree to {worst:.2f} ft at every station), so the azimuths are read as from grid north")
        if positions.get('elev_ft') is not None:
            r.update(elev_ft=positions['elev_ft'], elev_datum='GL')
            r['how'].append(f"the depths are counted from the elevation the path file starts at, {positions['elev_ft']:,.2f} ft above mean sea level - the wellhead at ground level. The rig floor the survey "
                            'was measured from stood higher (30 and 32 ft on this pad\'s two deviated wells), and the file does not give that height: the well is drawn that much too deep')
    need_head = r.get('lat') is None and r.get('easting_m') is None
    need_elev = r.get('elev_ft') is None
    if need_head or need_elev:
        lp = _find(folder, LOCATIONS['file'])
        point = LOCATIONS['points'].get(well, f'{well}-WELL')
        if lp is not None:
            with open(lp, 'rb') as f:
                loc = read_locations(f.read().decode('utf-8-sig', 'replace')).get(point)
            if loc is not None:
                r['sources'].append({'file': LOCATIONS['file'], 'page': LOCATIONS['page'], 'doi': LOCATIONS['doi'], 'sha256': sha256_of(lp), 'sha256_expected': LOCATIONS['sha256'], 'point': point})
                if need_head:
                    r.update(lat=loc['lat'], lon=loc['lon'], datum='NAD83')
                    r['how'].append(f"the report names no wellhead: it is the point {point} of the field's GPS survey of its wells ({LOCATIONS['file']}), latitude and longitude on NAD83")
                if need_elev:
                    r.update(elev_ft=round(loc['elev_m'] / FT_M, 2), elev_datum='GL')
                    r['how'].append(f"the survey gives no elevation: the depths are counted from the ground at the wellhead, {loc['elev_m'] / FT_M:,.2f} ft above mean sea level ({loc['elev_m']:,.2f} m, the point "
                                    f"{point} of {LOCATIONS['file']}; that file does not name its unit - its figure for 16A(78)-32 is, in metres, the ground level that well's own survey report gives). "
                                    'The rig floor the survey was measured from stood higher (30 and 32 ft on this pad\'s two deviated wells), and no file gives that height: the well is drawn that much too deep')
    if r.get('lat') is None and r.get('easting_m') is not None:
        g = GD.grid_to_wgs84(r['easting_m'], r['northing_m'], r['grid'], r['datum'], 'm')
        r.update(lat=g['on_zone_datum']['lat'], lon=g['on_zone_datum']['lon'])
    if r.get('north') is None:                                                   # no file says: said, and what it could move
        r['north'] = 'true'; r['north_unstated'] = True
        if any(s.get('ns') is None or s.get('ew') is None for s in st):
            from . import well3d as W3
            mc = W3.min_curvature([s['md'] for s in st], [s['inc'] for s in st], [s['azi'] for s in st])
            reach = max(math.hypot(float(n), float(e)) for n, e in zip(mc['n'], mc['e']))
        else:
            reach = max(math.hypot(s['ns'], s['ew']) for s in st)
        conv = abs(_convergence_deg(r['lat'], r['lon'])) if r.get('lat') is not None else 1.2
        r['how'].append(f"no file says which north the azimuths are from: they are read as from true north. The hole reaches {reach:,.0f} ft from its wellhead; were the azimuths from grid north "
                        f"({conv:.2f} degrees away here) its bottom would be {reach * math.radians(conv):,.1f} ft from where it is drawn, and were they magnetic and uncorrected "
                        f"({DECLINATION_DEG} degrees, the declination the 16A(78)-32 report gives for this pad) {2 * reach * math.sin(math.radians(DECLINATION_DEG) / 2):,.0f} ft")
    return r


def survey_miss_ft(st: List[dict]) -> Optional[float]:
    """How far the path computed here by minimum curvature from the stations' inclinations and azimuths is from the
    positions the file itself carries, at the worst station - the reading checked against the file's own arithmetic."""
    from . import well3d as W3
    if len(st) < 2 or any(s.get('tvd') is None or s.get('ns') is None or s.get('ew') is None for s in st):
        return None
    mc = W3.min_curvature([s['md'] for s in st], [s['inc'] for s in st], [s['azi'] for s in st])
    worst = 0.0
    for i, s in enumerate(st):
        d = ((float(mc['n'][i]) - (s['ns'] - st[0]['ns'])) ** 2 + (float(mc['e'][i]) - (s['ew'] - st[0]['ew'])) ** 2 + (float(mc['v'][i]) - (s['tvd'] - st[0]['tvd'])) ** 2) ** 0.5
        worst = max(worst, d)
    return round(worst, 2)


def survey_status(folder: str) -> List[dict]:
    """The survey files against the folder, and the field's GPS survey of its wells. They are optional: they place the
    wells for the 3D, Collision and Map tabs."""
    rows = []
    lp = _find(folder, LOCATIONS['file'])
    for well, sv in SURVEYS.items():
        p = next((q for q in (_find(folder, f) for f in sv['files']) if q), None)
        r = {'well': well, 'file': os.path.basename(p) if p else sv['files'][0], 'page': sv['page'], 'bytes_expected': sv['bytes'], 'sha256_expected': sv['sha256'], 'present': p is not None,
             'bytes': os.path.getsize(p) if p else None, 'needs_locations': sv.get('needs_locations'), 'locations_present': lp is not None}
        r['size_matches'] = r['bytes'] == sv['bytes'] if p else None
        r['can'] = ('not in the folder' if p is None else 'drawn in 3D' if not sv.get('needs_locations') or lp is not None else
                    ('not placed: ' if 'wellhead' in sv['needs_locations'] else 'placed on the map, not drawn in 3D: ') + f"{sv['needs_locations']} is in {LOCATIONS['file']}, which is not in the folder")
        rows.append(r)
    rows.append({'well': None, 'file': LOCATIONS['file'], 'page': LOCATIONS['page'], 'doi': LOCATIONS['doi'], 'bytes_expected': LOCATIONS['bytes'], 'sha256_expected': LOCATIONS['sha256'], 'present': lp is not None,
                 'bytes': os.path.getsize(lp) if lp else None, 'size_matches': (os.path.getsize(lp) == LOCATIONS['bytes']) if lp else None,
                 'can': 'the wellhead of 58-32, and the ground elevation of 58-32 and 78B-32' if lp else 'not in the folder'})
    return rows


def place_wells(ws, folder: str, rec: dict, actor: str, say: Optional[Callable[[dict], None]] = None, only: Optional[List[str]] = None) -> List[str]:
    """Each well that has no survey yet and whose published survey is in the folder: its wellhead declared, its
    elevation with where it came from, and its survey attached - so the 3D, Collision and Map tabs can draw it."""
    from . import well3d as W3
    say = say or (lambda d: None)
    done: List[str] = []
    entries = {e['well']: e for e in rec.get('wells', [])}
    for w in ws.wells():
        name = w.get('display')
        if name not in SURVEYS or name not in entries or (only is not None and name not in only):
            continue
        if w.get('trajectory') and ((w.get('surface') or {}).get('elevation') or {}).get('ft') is not None:
            continue                                                             # placed and drawn already; one with a survey and no elevation is read again
        e = entries[name]
        try:
            sv = read_survey(name, folder)
        except (PublicPadError, ValueError, KeyError, OSError, zipfile.BadZipFile) as ex:
            e['placed'] = {'drawn': False, 'why': f'its survey file could not be read: {ex}', 'placed_utc': _utc()}
            continue
        if sv is None:
            continue
        say({'well': name, 'step': 'placing'})
        sfile = f"{sv['file']}" + (f" ({sv['member']})" if sv.get('member') else '')
        src = f"Utah FORGE published survey, {sfile}, {sv['page']}"
        placed: Dict[str, Any] = {'file': sv['file'], 'member': sv.get('member'), 'page': sv['page'], 'bytes': sv['bytes'], 'sha256': sv['sha256'], 'sha256_expected': sv['sha256_expected'],
                                  'hash_matches': sv['hash_matches'], 'sources': sv['sources'], 'stations': len(sv['stations']), 'td_md_ft': sv['stations'][-1]['md'], 'north': sv['north'],
                                  'north_unstated': bool(sv.get('north_unstated')), 'how': list(sv['how']), 'placed_utc': _utc()}
        if sv.get('lat') is None:
            placed.update(drawn=False, why=f"its survey names no wellhead, and {LOCATIONS['file']} - the field's GPS survey of its wells, {LOCATIONS['page']} - "
                                           + ('has no point for it' if _find(folder, LOCATIONS['file']) else 'is not in the folder'))
            e['placed'] = placed
            continue
        esrc = src if not sv['sources'] or sv.get('elev_datum') == 'KB' else f"Utah FORGE GPS survey of its wells, {LOCATIONS['file']} ({sv['sources'][0]['point']}), {LOCATIONS['page']}"
        if sv.get('positions_miss_ft') is not None:
            esrc = f"Utah FORGE published well path, {sv['file']}, {sv['page']}"
        surface: Dict[str, Any] = {'lat': round(sv['lat'], 8), 'lon': round(sv['lon'], 8), 'datum': sv.get('datum') or 'NAD83'}
        if sv.get('elev_ft') is not None:
            surface['elevation'] = {'ft': sv['elev_ft'], 'datum': sv['elev_datum'], 'source': esrc}
        ws.set_well(w['id'], surface=surface, actor=actor)
        csv_ = 'MD (ft),Inc (deg),Azi (deg)\n' + ''.join(f"{s['md']:.2f},{s['inc']:.4f},{s['azi']:.3f}\n" for s in sv['stations'])
        miss = survey_miss_ft(sv['stations'])
        W3.attach_trajectory(ws, w['id'], f"{name.replace('(', '_').replace(')', '')}_survey.csv", csv_.encode('utf-8'), actor, sv['north'], 'ft',
                             depth_reference=sv.get('elev_datum'), grid=sv.get('grid', 'UTM12N') if sv['north'] == 'grid' else None,
                             source=src + ('; azimuth reference not stated in the file, read as true north' if sv.get('north_unstated') else ''))
        placed.update(lat=surface['lat'], lon=surface['lon'], datum=surface['datum'], elev_ft=sv.get('elev_ft'), elev_datum=sv.get('elev_datum'), grid=sv.get('grid') if sv['north'] == 'grid' else None,
                      miss_ft=miss, drawn=sv.get('elev_ft') is not None)
        if miss is not None:
            placed['how'].append(f'the path computed here by minimum curvature from the inclinations and azimuths is within {miss:g} ft of the positions the file itself carries, at every station')
        if not placed['drawn']:
            placed['why'] = (f"its survey gives no elevation, and {LOCATIONS['file']} - the field's GPS survey of its wells, {LOCATIONS['page']} - is not in the folder: the well is on the map, "
                             'and is drawn in 3D once that file is there or an elevation is declared on the Setup tab')
        e['placed'] = placed
        done.append(name)
    return done


def credit(ws) -> Optional[dict]:
    """For a site made from public data: what its pages and reports must carry - the name, the licence, the credit."""
    try:
        rec = record(ws)
    except (OSError, ValueError):
        return None
    if not rec:
        return None
    return {'name': rec.get('name'), 'licence': rec.get('licence'), 'credit': rec.get('credit'),
            'line': f"Data: {rec.get('name')}, public, under {(rec.get('licence') or {}).get('name')}. {rec.get('credit')}",
            'placed': [dict(e['placed'], well=e['well']) for e in rec.get('wells', []) if e.get('placed')], 'wells': [e['well'] for e in rec.get('wells', [])],
            'reconstructed': [e['well'] for e in rec.get('wells', []) if e.get('bit_depth_reconstructed')]}


def build(folder: str, ws_path: str, actor: str, dataset_id: str = 'utah-forge', wells: Optional[List[str]] = None, verify: bool = True, copy_users_from: Optional[str] = None,
          current_path: Optional[str] = None, progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Make the public pad's workspace at `ws_path` from the files in `folder`, or add to it the wells it does not have
    yet. Two wells at least. Each source file is hashed and the hash kept, matched or not."""
    from . import __version__
    from .workspace import Workspace, WorkspaceError
    ds = dataset(dataset_id)
    folder = str(folder or '').strip().strip('"'); ws_path = str(ws_path or '').strip().strip('"')
    if not folder or not os.path.isdir(folder):
        raise PublicPadError(f'no such folder: {folder or "(none named)"}')
    if not ws_path:
        raise PublicPadError('name the folder the site is to be made in')
    say = progress or (lambda d: None)
    todo = [w for w in ds['wells'] if os.path.isfile(os.path.join(folder, w['file'])) and (wells is None or w['well'] in wells)]
    if os.path.isfile(os.path.join(ws_path, 'workspace.json')):
        ws = Workspace(ws_path)
        rec = record(ws)
        if rec is None or rec.get('dataset') != ds['id']:
            raise PublicPadError(f'{ws_path} is a workspace that was not made from {ds["name"]}: choose another folder')
    else:
        if len(todo) < 2:
            raise PublicPadError(f"two wells make a pad: {len(todo)} of {ds['name']}'s recorder files are in {folder}")
        from . import workspaces as WSS
        try:
            WSS.create(ws_path, ds['site_name'], actor, copy_users_from=copy_users_from, current_path=current_path)
        except WorkspaceError as e:
            raise PublicPadError(str(e))
        ws = Workspace(ws_path)
        rec = {'dataset': ds['id'], 'name': ds['name'], 'about': ds['about'], 'licence': ds['licence'], 'credit': ds['credit'], 'folder': os.path.abspath(folder), 'wells': []}
    have = {w.get('display') for w in ws.wells()}
    tmp_dir = os.path.join(ws.path, '_incoming', 'public'); os.makedirs(tmp_dir, exist_ok=True)
    added = []
    for k, w in enumerate(todo):
        if w['well'] in have:
            continue
        src = os.path.join(folder, w['file'])
        entry: Dict[str, Any] = {'well': w['well'], 'year': w['year'], 'title': w['title'], 'page': w['page'], 'doi': w.get('doi'), 'file': w['file'], 'bytes': os.path.getsize(src), 'bytes_expected': w['bytes'],
                                 'sha256_expected': w['sha256'], 'format': w['format'], 'notes': []}
        say({'well': w['well'], 'step': 'hashing', 'n': k + 1, 'of': len(todo)})
        if verify:
            entry['sha256'] = sha256_of(src); entry['hash_matches'] = entry['sha256'] == w['sha256']
            if not entry['hash_matches']:
                entry['notes'].append('this file is not byte for byte the one this release was checked against (the repository revises its files): it is used as it is')
        t0 = time.time()
        if w['format'] == 'edr':
            path = src
            entry['notes'].append('the recorder\'s own export, attached as it is')
        else:
            say({'well': w['well'], 'step': 'converting', 'n': k + 1, 'of': len(todo)})
            path = os.path.join(tmp_dir, f"{w['well'].replace('(', '_').replace(')', '')}_recorder.csv")
            r = convert(w, folder, path, lambda n_, w_=w, k_=k: say({'well': w_['well'], 'step': 'converting', 'rows': n_, 'n': k_ + 1, 'of': len(todo)}))
            entry.update({kk: vv for kk, vv in r.items() if kk != 'notes'}); entry['notes'].extend(r['notes'])
            if w.get('survey_file') and os.path.isfile(os.path.join(folder, w['survey_file'])):
                entry['survey_file'] = w['survey_file']
        say({'well': w['well'], 'step': 'attaching', 'n': k + 1, 'of': len(todo)})
        made = ws.add_well_file(path, display=w['well'], actor=actor)
        if path != src:
            try:
                os.remove(path)
            except OSError:
                pass
        entry.update(well_id=made['id'], seconds=round(time.time() - t0, 1), attached_utc=_utc())
        rec['wells'].append(entry); added.append(w['well'])
        rec.update(built_utc=_utc(), program_version=__version__)
        os.makedirs(os.path.join(ws.path, 'records'), exist_ok=True)
        with open(os.path.join(ws.path, 'records', 'public_pad.json'), 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1)
    stages = attach_treatments(ws, folder, rec, actor, ds, verify, say) if wells is None or any(x['well'] in wells for x in ds.get('treatments', [])) else []
    before = {e['well']: (e.get('placed') or {}).get('why') for e in rec['wells']}
    placed = place_wells(Workspace(ws.path), folder, rec, actor, say, only=wells)
    need = [e for e in rec['wells'] if not os.path.isfile(os.path.join(ws.reports_dir, 'wells', e['well_id'], 'drilling_record.json'))]
    unwritten: List[str] = []
    if need and (added or placed):                                             # each well's drilling record written once now: the Simulation, Control room and Drill string tabs read it
        from . import edr as E
        from . import series_cache as SCH
        for e in need:
            say({'well': e['well'], 'step': 'record'})
            try:
                wsn = Workspace(ws.path)
                cached = SCH.load(SCH.cache_dir(wsn, e['well_id']), SCH.source_path(wsn, e['well_id'])) is not None
                E.write_record(wsn.well_stream(e['well_id']), os.path.join(wsn.reports_dir, 'wells', e['well_id']), __version__,
                               cache_dir=None if cached else SCH.cache_dir(wsn, e['well_id']))      # a chart cache that is there may be open in the site's own window: it is left alone
            except Exception as ex:
                unwritten.append(f"{e['well']} ({type(ex).__name__}: {ex})")
        if unwritten:
            rec['notes'] = [n for n in rec.get('notes', []) if not n.startswith('the drilling record of')] + [
                'the drilling record of ' + '; '.join(unwritten) + ' was not written by the build: press Refresh on the Home tab of the site, and the Simulation tab has it']
        else:
            rec['notes'] = [n for n in rec.get('notes', []) if not n.startswith('the drilling record of')]
    if stages or placed or need or before != {e['well']: (e.get('placed') or {}).get('why') for e in rec['wells']}:
        rec.update(built_utc=_utc(), program_version=__version__)
        with open(os.path.join(ws.path, 'records', 'public_pad.json'), 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1)
    if added or stages or placed:
        ws.audit(actor, 'public_pad.build', {'dataset': ds['id'], 'added': added, 'stages': stages, 'placed': placed, 'folder': os.path.abspath(folder),
                                             'hashes_match': {e['well']: e.get('hash_matches') for e in rec['wells']}})
    done: Dict[str, Any] = {'step': 'done', 'added': added}
    if stages:
        done['stages'] = stages
    if placed:
        done['placed'] = placed
    say(done)
    return {'path': ws.path, 'name': ds['site_name'], 'added': added, 'stages': stages, 'placed': placed, 'wells': [e['well'] for e in rec['wells']], 'notes': rec.get('notes', []), 'record': rec}


def attach_treatments(ws, folder: str, rec: dict, actor: str, ds: dict, verify: bool = True, say: Optional[Callable[[dict], None]] = None) -> List[str]:
    """The published pumping record of each stimulation stage whose file is in the folder, attached to its well for
    the Frac treatment tab - the stages the site does not have yet."""
    from . import frac_treatment as FT
    say = say or (lambda d: None)
    ids = {w.get('display'): w['id'] for w in ws.wells()}
    have = FT._index(ws)
    done: List[str] = []
    kept = rec.setdefault('treatments', [])
    for x in ds.get('treatments', []):
        src = _find(folder, x['file'])
        wid = ids.get(x['well'])
        if src is None or wid is None:
            continue
        todo = [st for st in x['members'] if st not in (have.get(wid) or {})]
        if not todo:
            continue
        entry: Dict[str, Any] = {'well': x['well'], 'year': x['year'], 'title': x['title'], 'page': x['page'], 'doi': x.get('doi'), 'file': x['file'], 'bytes': os.path.getsize(src),
                                 'bytes_expected': x['bytes'], 'sha256_expected': x['sha256'], 'stages': [], 'notes': []}
        if verify:
            entry['sha256'] = sha256_of(src); entry['hash_matches'] = entry['sha256'] == x['sha256']
            if not entry['hash_matches']:
                entry['notes'].append('this file is not byte for byte the one this release was checked against (the repository revises its files): it is used as it is')
        with zipfile.ZipFile(src) as z:
            names = set(z.namelist())
            for st in todo:
                mem = x['members'][st]
                if mem not in names:
                    entry['notes'].append(f'stage {st}: {mem} is not in the zip')
                    continue
                say({'well': x['well'], 'step': 'treatment', 'stage': st})
                try:
                    FT.attach(ws, wid, st, os.path.basename(mem), z.read(mem), actor, f"{x['title']} ({x['page']}), {x['file']}")
                except ValueError as e:
                    entry['notes'].append(f'stage {st}: {e}')
                    continue
                entry['stages'].append(st); done.append(f"{x['well']} stage {st}")
        entry['attached_utc'] = _utc()
        kept.append(entry)
        os.makedirs(os.path.join(ws.path, 'records'), exist_ok=True)                   # after each file: a build that is stopped has kept what it did
        with open(os.path.join(ws.path, 'records', 'public_pad.json'), 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1)
    return done


def start_build(folder: str, ws_path: str, actor: str, **kw) -> dict:
    """The build in a thread of its own - it reads gigabytes - with its progress kept for the page to ask after."""
    with _LOCK:
        if _JOB.get('state') == 'running':
            raise PublicPadError('a public pad is being built already')
        _JOB.clear(); _JOB.update(state='running', started_utc=_utc(), folder=folder, path=ws_path, at={})

    def run():
        try:
            r = build(folder, ws_path, actor, progress=lambda d: _JOB.__setitem__('at', d), **kw)
            _JOB.update(state='done', finished_utc=_utc(), result={k: v for k, v in r.items() if k != 'record'})
        except Exception as e:
            _JOB.update(state='failed', finished_utc=_utc(), error=(str(e) if isinstance(e, (PublicPadError, OSError)) else f'{type(e).__name__}: {e}'))
    threading.Thread(target=run, name='public-pad-build', daemon=True).start()
    return dict(_JOB)


def job() -> dict:
    return dict(_JOB)


def text(s: dict) -> str:
    out = [f"{s['name']} - public data", '=' * (len(s['name']) + 14)]
    out.extend(s['lines'])
    for r in s['files']:
        st = 'not in the folder' if not r['present'] else ('there' + ('' if r['size_matches'] else f", {r['bytes']:,} bytes where {r['bytes_expected']:,} were expected")
                                                         + ('' if r['hash_matches'] is None else (', hash matches' if r['hash_matches'] else ', HASH DIFFERS from the file this release was checked against')))
        out.append(f"  {r['well']:12s} {r['year']}  {r['file']}  ({r['bytes_expected'] / 1e6:,.0f} MB)  {st}")
        out.append(f"               from {r['page']}" + (f"  doi:{r['doi']}" if r.get('doi') else ''))
        if r.get('survey'):
            out.append(f"               and, for its true vertical depth, {r['survey']['file']}: " + ('there' if r['survey']['present'] else 'not in the folder (optional)'))
    for r in s.get('treatments') or []:
        st = 'not in the folder' if not r['present'] else ('there' + ('' if r['size_matches'] else f", {r['bytes']:,} bytes where {r['bytes_expected']:,} were expected")
                                                         + ('' if r['hash_matches'] is None else (', hash matches' if r['hash_matches'] else ', HASH DIFFERS from the file this release was checked against')))
        out.append(f"  {r['well']:12s} {r['year']}  {r['file']}  ({r['bytes_expected'] / 1e6:,.0f} MB; stage {', '.join(r['stages'])})  {st}")
        out.append(f"               from {r['page']}" + (f"  doi:{r['doi']}" if r.get('doi') else ''))
    for r in s.get('surveys') or []:
        st = 'not in the folder' if not r['present'] else ('there' + ('' if r['size_matches'] else f", {r['bytes']:,} bytes where {r['bytes_expected']:,} were expected"))
        out.append(f"  {r['well'] or 'all wells':12s} survey  {r['file']}  ({r['bytes_expected'] / 1e3:,.0f} kB)  {st}" + (f": {r['can']}" if r['present'] else ''))
        out.append(f"               from {r['page']}" + (f"  doi:{r['doi']}" if r.get('doi') else ''))
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------------------------
# the self-test: stand-ins in each of the published layouts
# --------------------------------------------------------------------------------------------------------------
def standins(folder: str, stands: int = 14) -> dict:
    """Small labelled records written in each of the published layouts under the published file names, so the build
    can be run without the real files. SIMULATION, and nothing like the size."""
    import numpy as np
    from . import dysfunction as DY
    os.makedirs(folder, exist_ok=True)
    made = {}
    for k, w in enumerate(FORGE['wells']):
        rec = DY.synthetic_record(False, seed=20 + k, stands=stands)
        ch = rec['ch']; n = len(rec['t']); t0 = rec['t0']
        ts = [t0 + __import__('datetime').timedelta(seconds=int(s)) for s in rec['t']]
        onb = (ch['hole'] - ch['bit'] < 0.05).astype(int)
        p = os.path.join(folder, w['file'])
        if w['format'] == 'edr':
            with open(p, 'w', encoding='utf-8', newline='') as f:
                f.write('YYYY/MM/DD,HH:MM:SS,Hole Depth (feet),Bit Depth (feet),Rate Of Penetration (ft_per_hr),Weight on Bit (klbs),Rotary RPM (RPM),Standpipe Pressure (psi),Rotary Torque (kft_lb),Hook Load (klbs),'
                        'Differential Pressure (psi),Total Pump Output (gal_per_min),Block Height (feet),Gamma (api)\n')
                for i in range(n):
                    f.write(f"{ts[i]:%Y/%m/%d},{ts[i]:%H:%M:%S},{ch['hole'][i]:.2f},{ch['bit'][i]:.2f},{150.0 if rec['drilling'][i] else 0.0:.1f},{20.0 if rec['drilling'][i] else 0.0:.1f},{ch['rpm'][i]:.1f},"
                            f"{ch['spp'][i]:.1f},5.0,{ch['hookload'][i]:.1f},{ch['diff'][i]:.1f},{ch['flow'][i]:.1f},{ch['block'][i]:.2f},-999.25\n")
        elif w['format'] == 'forge-58-32':
            with open(p, 'w', encoding='utf-8', newline='') as f:
                f.write('Date,Time,Depth (ft),Depth (m),Block Ht (ft),Block Ht (m),Bit Depth (ft),Bit Depth (m),ROP(1 ft),ROP(1 m),Hookload (k lbs),Hookload (kg),Pump Pressure (psi),Pump Press (KPa),'
                        'Surface Torque (psi),Surface Torque (KPa),Rotary Speed (rpm),Pump 1 (spm),Pump 2 (spm),Pit Total (bbls),Pit Total (m3),Flow In (gal/min),Flow In (liters/min)\n')
                for i in range(n):
                    blk = '-9999.00' if i < 5 else f"{ch['block'][i]:.2f}"
                    f.write(f"{ts[i].month}/{ts[i].day}/{ts[i]:%y},{ts[i]:%H:%M:%S},{ch['hole'][i]:.2f},{ch['hole'][i] * 0.3048:.2f},{blk},0,{ch['bit'][i]:.2f},{ch['bit'][i] * 0.3048:.2f},"
                            f"{150.0 if rec['drilling'][i] else 0.0:.1f},0,{ch['hookload'][i]:.1f},0,{ch['spp'][i]:.1f},0,100,0,{ch['rpm'][i]:.0f},60,60,300,48,{ch['flow'][i]:.1f},0\n")
        elif w['format'] == 'forge-16a':
            slips = (ch['hookload'] < 60.0).astype(int)
            with open(p, 'w', encoding='utf-8', newline='') as f:
                f.write('Time,Block Position,Weight on Bit,Hookload,Slips Set,ROP Depth/Hour,MWD Gamma (API),On Bottom,Top Drive RPM,Top Drive Torque (ft-lbs),Flow In,Pump Pressure,SPM Total,RigMode,'
                        'Total Depth,Bit Diameter,Bit RPM,Depth Hole TVD,Differential Pressure,Downhole Torque,Drill Mode\n')
                for i in range(n):
                    f.write(f"{ts[i]:%d}/{ts[i]:%b}/{ts[i]:%Y} {ts[i]:%H:%M:%S},{ch['block'][i]:.4f},{20.0 if rec['drilling'][i] else 0.0:.2f},{ch['hookload'][i]:.3f},{slips[i]},{150.0 if rec['drilling'][i] else 0.0:.1f},"
                            f"-999.25,{onb[i]},{ch['rpm'][i]:.2f},{5000.0 if ch['rpm'][i] > 0 else 0.0:.1f},{ch['flow'][i]:.2f},{ch['spp'][i]:.2f},120,2,{ch['hole'][i]:.4f},8.75,0,{ch['hole'][i] * 0.9:.4f},"
                            f"{ch['diff'][i]:.2f},0,2\n")
        else:                                                   # the zip of a wide ten-second export, and its survey
            buf = io.StringIO()
            buf.write('YYYY/MM/DD,HH:MM:SS,Rate Of Penetration (ft_per_hr),Time Of Penetration (min_per_ft),Hole Depth (feet),Bit Depth (feet),Hook Load (klbs),Standpipe Pressure (psi),Pump 1 strokes/min (SPM),'
                      'Rotary RPM (RPM),Weight on Bit (klbs),Total Pump Output (gal_per_min),Block Height (feet),Mud Tank 1 Volume (barrels),Differential Pressure (psi),Convertible Torque (kft_lb),EYE TEMP (F)\n')
            for i in range(0, n, 10):
                buf.write(f"{ts[i]:%Y/%m/%d},{ts[i]:%H:%M:%S},{150.0 if rec['drilling'][i] else 0.0:.1f},0.4,{ch['hole'][i]:.2f},{ch['bit'][i]:.2f},{ch['hookload'][i]:.1f},{ch['spp'][i]:.1f},60,{ch['rpm'][i]:.1f},"
                          f"{20.0 if rec['drilling'][i] else 0.0:.1f},{ch['flow'][i]:.1f},{ch['block'][i]:.2f},200,{ch['diff'][i]:.1f},5.0,150\n")
            with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr(w['member'], buf.getvalue()); z.writestr('kpi_daily/readme.txt', 'x')
            lo, hi = float(np.nanmin(ch['hole'])), float(np.nanmax(ch['hole']))
            rows = ['WELL   \t\t: 16B(78)-32', 'CALCULATION METHOD\t: Minimum Curvature', '', 'SURVEY ', '', 'MD         INC       AZI       TVD        SSTVD   ', '(ft)      (deg)     (deg)      (ft)       (ft)  ', '']
            for m in np.arange(0.0, hi + 500.0, 250.0):
                rows.append(f'{m:.2f}   {0.0 if m < lo else 60.0:.2f}   100.00   {(m if m < lo else lo + (m - lo) * 0.5):.2f}   {-5447.65 + m:.2f}')
            with zipfile.ZipFile(os.path.join(folder, w['survey_file']), 'w') as z:
                z.writestr(w['survey_member'], '\n'.join(rows) + '\n')
            made['tvd_at'] = (lo, lambda m_, lo_=lo: m_ if m_ < lo_ else lo_ + (m_ - lo_) * 0.5)
        made[w['well']] = {'rows': n, 'hole_from': float(ch['hole'][0]), 'hole_to': float(ch['hole'][-1])}
    return made


def _standin_stations(td: float, kick: float, inc: float, azi: float, step: float = 250.0, wobble: float = 0.0) -> List[dict]:
    """A made-up hole: straight down to the kick-off, built at 3 degrees a hundred feet to the inclination, held to
    the end; its positions by minimum curvature, as a survey report carries them."""
    from . import well3d as W3
    mds = [0.0]
    while mds[-1] < td:
        mds.append(min(td, mds[-1] + step))
    incs = [0.0 if m <= kick else min(inc, (m - kick) * 0.03) for m in mds]
    azis = [0.0 if i == 0.0 else azi + wobble * ((k % 3) - 1) for k, i in enumerate(incs)]
    mc = W3.min_curvature(mds, incs, azis)
    return [{'md': mds[k], 'inc': round(incs[k], 2), 'azi': round(azis[k], 2), 'tvd': round(float(mc['v'][k]), 2), 'ns': round(float(mc['n'][k]), 2), 'ew': round(float(mc['e'][k]), 2)} for k in range(len(mds))]


def _standin_pdf(pages: List[List[List[Tuple[float, str]]]]) -> bytes:
    """A PDF of plain compressed text, a word at its own position, in the way the survey reports are printed."""
    import zlib
    objs: List[bytes] = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /TrueType /BaseFont /Arial /Encoding /WinAnsiEncoding >>']
    kids = []
    for rows in pages:
        d = []
        for k, row in enumerate(rows):
            y = 700.0 - 12.0 * k
            for x, word in row:
                d.append('BT\n\t0 Tw\n\t 8 0 0 8 %.4f %.4f Tm\n\t/F2 1 Tf\n\t0 Tc\n\t(%s) Tj\n\t0 Tr\nET\n' % (x, y, word.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')))
        z = zlib.compress(''.join(d).encode('latin-1'))
        objs.append(b'<< /Length %d /Filter /FlateDecode >>\nstream\r\n' % len(z) + z + b'\r\nendstream')
        objs.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F2 3 0 R >> >> /Contents %d 0 R >>' % len(objs))
        kids.append(len(objs))
    objs[1] = b'<< /Type /Pages /Count %d /Kids [%s] >>' % (len(kids), b' '.join(b'%d 0 R' % k for k in kids))
    out = b'%PDF-1.3\n'
    at = []
    for k, o in enumerate(objs):
        at.append(len(out)); out += b'%d 0 obj\n' % (k + 1) + o + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % a for a in at)
    return out + b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, x)


def survey_standins(folder: str, td: Optional[Dict[str, float]] = None, locations: bool = True) -> dict:
    """A made-up survey for each well, written in the layout its published survey has and under the published name -
    a text report, a planning package's report in a spreadsheet, two reports printed to PDF, a spreadsheet's CSV -
    and the field's GPS survey of its wells. SIMULATION. Returns what each was made with."""
    import tempfile
    from . import xlsx_read as X
    os.makedirs(folder, exist_ok=True)
    td = td or {}
    E0, N0 = 334600.0, 4263400.0                                             # metres, UTM zone 12N: a made-up pad
    plan = {'58-32': (850.0, 0.0, 6200.0, 4.0, 280.0, 1690.0), '16A(78)-32': (0.0, 0.0, 5000.0, 60.0, 100.0, 1650.0), '56-32': (900.0, 60.0, 7000.0, 5.0, 240.0, 1661.0),
            '78B-32': (1250.0, -400.0, 7500.0, 3.0, 235.0, 1705.0), '16B(78)-32': (2.0, 36.0, 5000.0, 60.0, 100.0, 1650.5)}
    made: Dict[str, dict] = {}
    for well, (de, dn, kick, inc, azi, gl_m) in plan.items():
        end = float(td.get(well) or kick + 3000.0)
        st = _standin_stations(end, min(kick, end * 0.6), inc, azi, wobble=0.4 if inc < 10 else 0.0)
        made[well] = {'stations': st, 'easting_m': E0 + de, 'northing_m': N0 + dn, 'gl_ft': round(gl_m / FT_M, 2), 'kb_ft': round(gl_m / FT_M + 30.0, 2), 'td': end, 'inc': inc, 'azi': azi}
    listing = lambda st, kind: [[(78.0, 'Survey'), (132.0, 'Meas.'), (326.0, 'Coordinates'), (452.0, 'Vertical'), (498.0, 'Dog Leg')],
                                [(83.0, 'Type'), (131.0, 'Depth'), (174.0, 'Inc.'), (210.0, 'Azimuth'), (265.0, 'TVD'), (318.0, 'N-S'), (366.0, 'E-W'), (407.0, 'Closure'), (452.0, 'Section'), (498.0, 'Severity')],
                                [(138.0, '(ft)'), (171.0, '(deg)'), (217.0, '(deg)'), (268.0, '(ft)'), (319.0, '(ft)'), (369.0, '(ft)'), (418.0, '(ft)'), (464.0, '(ft)'), (497.0, '(deg/100)')]] + [
        ([(78.0, '**'), (86.0, 'TieIn')] if k == 0 else [(86.0, kind)]) + [(134.0, f"{s['md']:,.1f}"), (181.0, f"{s['inc']:.2f}"), (226.0, f"{s['azi']:.2f}"), (268.0, f"{s['tvd']:,.2f}"), (330.0, f"{s['ns']:.2f}"),
                                                                           (372.0, f"{s['ew']:.2f}"), (420.0, f"{(s['ns'] ** 2 + s['ew'] ** 2) ** 0.5:.2f}"), (469.0, f"{s['ns']:.2f}"), (510.0, '0.500')]
        for k, s in enumerate(st)] + [[(73.0, 'Calculations using Minimum Curvature Method')]]
    # 58-32: a report printed to PDF, alone in its zip
    m = made['58-32']; sv = SURVEYS['58-32']
    half = len(m['stations']) // 2
    with zipfile.ZipFile(os.path.join(folder, sv['files'][0]), 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(sv['member'], _standin_pdf([listing(m['stations'][:half], 'MSS'), listing(m['stations'][half:], 'MWD')[:-1][3:] + [[(73.0, 'Calculations using Minimum Curvature Method')]]]))
        z.writestr('DirectionalPlanRpt_171027102949.pdf', b'%PDF-1.3 stand-in')
    # 56-32: the same kind of report, and beside it the publisher's positions
    m = made['56-32']; sv = SURVEYS['56-32']
    pos = 'UTM_E,UTM_N,Depth,Tru_Vdepth,Elev_msl_ft\n' + ''.join(f"{m['easting_m'] + s['ew'] * FT_M:.2f},{m['northing_m'] + s['ns'] * FT_M:.2f},{s['md']:.2f},{s['tvd']:.2f},{m['gl_ft'] - s['tvd']:.2f}\n"
                                                                 for s in m['stations']) + ',,,,\n,,,,\n'
    with zipfile.ZipFile(os.path.join(folder, sv['files'][0]), 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(sv['member'], _standin_pdf([listing(m['stations'], 'MWD')]))
        z.writestr(sv['positions'], pos); z.writestr('readme.txt', 'stand-in')
    # 78B-32: a spreadsheet's CSV, its headings on several lines, thousands with commas, the wellhead beside the table
    m = made['78B-32']; sv = SURVEYS['78B-32']
    from . import geodesy as GD
    ll = GD.grid_to_wgs84(m['easting_m'], m['northing_m'], 'UTM12N', 'NAD83', 'm')['on_zone_datum']
    m['lat'], m['lon'] = round(ll['lat'], 8), round(ll['lon'], 8)
    q = lambda v: f'"{v:,.2f}"' if abs(v) >= 1000 else f'{v:g}'
    rows = ['"Survey\nType","Measured\nDepth (ft)","Inclination\n(degrees)","Azimuth\n(degrees)","True Vertical\nDepth (ft)",N-S (ft),E-W (ft),Closure (ft),"Vertical\nSection (ft)","Dog Leg\nSeverity\n(deg/100)",,Well Head,NAD 83,,']
    for k, s in enumerate(m['stations']):
        tail = ',,Lat,Long,UTM E,UTM N' if k == 0 else f",,{m['lat']},{m['lon']},{m['easting_m']},{m['northing_m']}" if k == 1 else ',,,,,'
        rows.append(('** TieIn' if k == 0 else 'MWD') + f",{q(s['md'])},{s['inc']:g},{s['azi']:g},{q(s['tvd'])},{s['ns']:g},{s['ew']:g},{(s['ns'] ** 2 + s['ew'] ** 2) ** 0.5:.1f},{s['ns']:g},0.5" + tail)
    with zipfile.ZipFile(os.path.join(folder, sv['files'][0]), 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(sv['member'], '\n'.join(rows) + '\n'); z.writestr('directional survey78B.xlsx', b'stand-in')
    # 16A: a planning package's design report in a spreadsheet, a word a cell
    m = made['16A(78)-32']; sv = SURVEYS['16A(78)-32']
    nn, ee = round(m['northing_m'] / USFT, 2), round(m['easting_m'] / USFT, 2)
    m['northing_m'], m['easting_m'] = nn * USFT, ee * USFT
    cells: List[List[object]] = [[], ['COMPASS'], ['Design', 'Report', '2020-12-19', 'Page', 1, 'of', 2], ['Well.....................:', '16A(78)-32'], ['Permanent', 'datum..........:', 'Mean', 'Sea', 'Level'],
                                 ['GL', 'above', 'permanent.......:', round(m['gl_ft']), 'usft'], ['KB', 'above', 'permanent.......:', round(m['gl_ft']) + 30, 'usft'], [],
                                 ['-----', 'Vertical', 'section', 'origin----------------'], ['Latitude', '(+N/S-).........:', 0, 'usft'], ['Departure', '(+E/W-)........:', 0, 'usft'], [],
                                 ['-----', 'Platform', 'reference', 'point---------------', '-----', 'Corrections', '---------------------------'],
                                 ['Latitude', '(+N/S-).........:', nn, 'usft', 'Magnetic', 'dec', '(+E/W-).....:', 11.29, 'degrees'],
                                 ['Departure', '(+E/W-)........:', ee, 'usft', 'Grid', 'convergence', '(+E/W-).:', -1.18, 'degrees'], ['Total', 'az', 'corr', '(+E/W-)....:', 12.47, 'degrees'],
                                 ['Azimuth', 'from', 'rotary', 'table', 'to', 'target:', 0, 'degrees', '(Total', 'az', 'corr', '=', 'magnetic', 'dec', '-', 'grid', 'conv)'], [],
                                 ['Seq', 'Measured', 'Incl', 'Azimuth', 'Course', 'TVD', 'Vertical', 'Displ', 'Displ', 'Total', 'At', 'DLS', 'Srvy', 'Tool'],
                                 ['#', 'depth', 'angle', 'angle', 'length', 'depth', 'section', '+N/S-', '+E/W-', 'displ', 'Azim', '(deg/', 'tool', 'qual']]
    m['kb_ft'] = float(round(m['gl_ft']) + 30)
    for k, s in enumerate(m['stations']):
        cells.append([k + 1, s['md'], s['inc'], s['azi'], s['md'] - (m['stations'][k - 1]['md'] if k else 0.0), s['tvd'], s['ew'], s['ns'], s['ew'], round((s['ns'] ** 2 + s['ew'] ** 2) ** 0.5, 2), s['azi'], 0.5,
                      'UNDEFINED' if k == 0 else 'MWD', -1])
    X.write_simple(os.path.join(folder, sv['files'][0]), cells)
    # 16B: the directional company's final report as text
    m = made['16B(78)-32']; sv = SURVEYS['16B(78)-32']
    nn, ee = round(m['northing_m'] / USFT, 2), round(m['easting_m'] / USFT, 2)
    m['northing_m'], m['easting_m'] = nn * USFT, ee * USFT
    rep = ['COMPANY \t\t: Utah Forge', 'WELL   \t\t: 16B(78)-32', 'MAP SYSTEM\t\t: Universal Transverse Mercator (US Survey Feet)', 'GEODIC DATUM\t\t: NAD83 Utah - HARN', 'MAP ZONE\t\t: Zone 12N (114 W to 108 W)',
           'CALCULATION METHOD\t: Minimum Curvature', f'WELL EASTING \t\t: {ee:.2f}ft ', f'WELL NORTHING \t\t: {nn:.2f}ft ', 'VSECT DIREC \t\t: 100.00deg ', 'NORTH REF\t\t: Grid', f"KB ELEV  \t\t: {m['kb_ft']:.2f}ft ",
           f"GL ELEV \t\t: {m['gl_ft']:.2f}ft ", 'DATE\t\t\t: 6/26/2023', '', 'SURVEY ', '', 'MD         INC       AZI       TVD        SSTVD        N/S       NORTHING       E/W        EASTING       VSEC        DLS            ',
           '(ft)      (deg)     (deg)      (ft)       (ft)         (ft)       (Ft)          (ft)        (Ft)         (ft)     (deg/100ft)  ', '', '']
    rep += [f"{s['md']:.2f}   {s['inc']:.2f}   {s['azi']:.2f}   {s['tvd']:.2f}   {s['tvd'] - m['kb_ft']:.2f}   {s['ns']:.2f}   {nn + s['ns']:.2f}   {s['ew']:.2f}   {ee + s['ew']:.2f}   {s['ew']:.2f}   0.50   " for s in m['stations']]
    with zipfile.ZipFile(os.path.join(folder, sv['files'][0]), 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(sv['member'], '\r\n'.join(rep) + '\r\n')
    # the field's GPS survey of its wells: metres, and no unit named
    if locations:
        loc = ['"UTM Zone:12 North, Datum Trans:NAD 1983 (Conus)  (Molodensky), Global Ref Datum:WGS 1984, Global Ref Epoch:2005, Geoid Model:GEOID18 (Conus)",,,,,,,', ',,,,,,,',
               'Point ID,Easting (X),Northing (Y),Latitude,Longitude ,Elevation,Feature Code,Notes']
        for well in ('16A(78)-32', '56-32', '58-32', '78B-32'):
            m = made[well]
            ll = GD.grid_to_wgs84(m['easting_m'], m['northing_m'], 'UTM12N', 'NAD83', 'm')['on_zone_datum']
            pt = LOCATIONS['points'].get(well, f'{well}-WELL')
            loc.append(f"{pt[:-5]}-NEC,{m['easting_m'] + 40:.4f},{m['northing_m'] + 40:.4f},{ll['lat'] + 0.0004:.8f},{ll['lon'] + 0.0004:.8f},{m['gl_ft'] * FT_M - 0.3:.4f},WELL-PAD,Measured on pad")
            loc.append(f"{pt},{m['easting_m']:.4f},{m['northing_m']:.4f},{ll['lat']:.8f},{ll['lon']:.8f},{m['gl_ft'] * FT_M:.4f},WELL,")
            m.setdefault('lat', round(ll['lat'], 8)); m.setdefault('lon', round(ll['lon'], 8))
        loc.append('FORB,NA,NA,NA,NA,NA,SEISMIC,General coordinates place station within a locked building')
        with open(os.path.join(folder, LOCATIONS['file']), 'w', encoding='utf-8', newline='') as f:
            f.write('\r\n'.join(loc) + '\r\n')
    return made


def treatment_standins(folder: str, sub: str = 'stimulation') -> dict:
    """Small labelled stages written in the two published layouts of the stimulation records - a spreadsheet with a
    stacked header, and a frac van's data-acquisition export opened in a spreadsheet - zipped under the published
    names. SIMULATION. Returns each stage's built-in ISIP."""
    import tempfile
    from datetime import datetime as _dt, timedelta as _td
    from . import frac_treatment as FT
    from . import xlsx_read as X
    out_dir = os.path.join(folder, sub) if sub else folder
    os.makedirs(out_dir, exist_ok=True)
    made: Dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        k = 0
        for x in FORGE['treatments']:
            with zipfile.ZipFile(os.path.join(out_dir, x['file']), 'w', zipfile.ZIP_DEFLATED) as z:
                for st, mem in x['members'].items():
                    k += 1
                    isip = 3800.0 + 60.0 * k
                    rec = FT.synthetic(isip=isip, pump_min=8.0, falloff_min=7.0, step_up=False, seed=40 + k)
                    t0 = _dt(x['year'], 4, 3 + (k % 20), 6, 0, 0)
                    n = len(rec['t'])
                    if x['year'] == 2022:                                   # the van's export, a word a cell
                        rows: List[List[object]] = [['*File', 'was', 'generated', 'by', 'DataAcq', 'ver', 1], [], ['*Start', 'date', 'and', 'time:', t0.strftime('%Y-%m-%d'), t0.strftime('%I:%M:%S'), t0.strftime('%p')], [],
                                                    ['*Step,', 'Start', 'and', 'Depth', 'flag:'], [], [0.0166667, 0, 0], [], ['*Total', 'number', 'of', 'Data', 'Channels:'], [], [5], [], ['*Names', 'of', 'Data', 'Channels:'], [],
                                                    ['Time', '(min)', 'TIME'], ['Time', 'um61'], ['A1:', 'Treating', 'Pressure', 'UA46'], ['Slurry', 'Rate', 'UP50'], ['Stage', 'Slurry', 'Volume', 'UX70'], []]
                        rows += [['', round(float(rec['t'][i]) / 60.0, 5), 0, round(float(rec['pressure'][i]), 1), round(float(rec['rate'][i]), 2), round(float(rec['slurry_total'][i]), 2)] for i in range(n)]
                    else:                                                   # the stacked header
                        rows = [['', '', 'Primary', '', '', 'Slurry', '', 'Prop'], ['', 'Time of', 'Treating', 'Slurry', 'Clean', 'Stage', 'Prop', 'Stage'], ['Date', 'Day', 'Pressure', 'Rate', 'Rate', 'Total', 'Conc', 'Total'],
                                ['(mm:dd:yyyy)', '(hh:mm:ss)', '(psi)', '(bpm)', '(bpm)', '(bbl)', '(ppg)', '(lbm)']]
                        prop = 0.0
                        for i in range(n):
                            ts = t0 + _td(seconds=int(rec['t'][i]))
                            prop += float(rec['conc'][i]) * float(rec['clean_rate'][i]) * 42.0 / 60.0
                            rows.append([ts.strftime('%Y-%m-%d'), ts.strftime('%H:%M:%S'), round(float(rec['pressure'][i])), round(float(rec['rate'][i]), 2), round(float(rec['clean_rate'][i]), 3),
                                         round(float(rec['slurry_total'][i]), 3), round(float(rec['conc'][i]), 2), round(prop, 1)])
                    tp = os.path.join(tmp, 'x.xlsx')
                    X.write_simple(tp, rows)
                    z.write(tp, mem)
                    made[f"{x['well']}|{st}"] = isip
                z.writestr('Readme.txt', 'stand-in')
    return made


def selftest() -> dict:
    import tempfile
    import numpy as np
    from . import rig_state as RS
    from . import roadmap as RM
    from .workspace import Workspace
    checks = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        folder = os.path.join(tmp, 'files'); site = os.path.join(tmp, 'site')
        made = standins(folder)
        st0 = status(folder, verify=True)
        checks.append({'check': 'the folder read against the list: every file there, none the size or the hash of the published one (these are stand-ins)', 'value': [st0['present'], sorted({r['size_matches'] for r in st0['files']}), sorted({r['hash_matches'] for r in st0['files']})],
                       'pass': st0['present'] == 5 and st0['ready'] and not any(r['size_matches'] or r['hash_matches'] for r in st0['files']) and st0['files'][4]['survey']['present']})
        md, tv = survey_tvd('junk\nMD   INC  AZI  TVD\n(ft) (deg) (deg) (ft)\n\n0.00 0 0 0.00\n100.00 1 5 99.90\n200.00 30 5 190.00\nnot a row\n')
        checks.append({'check': 'a survey report\'s MD and TVD columns, and the depth between two stations', 'value': [md, tv, _interp(150.0, md, tv)], 'pass': md == [0.0, 100.0, 200.0] and tv == [0.0, 99.9, 190.0] and abs(_interp(150.0, md, tv) - 144.95) < 1e-9})
        seen = []
        r = build(folder, site, 'selftest', progress=seen.append)
        ws = Workspace(site); rec = record(ws)
        by = {e['well']: e for e in rec['wells']}
        checks.append({'check': 'the site made: five wells, each with its source\'s hash kept and said not to match, the licence and the credit on the record', 'value': [r['added'], rec['licence']['name'][:12]],
                       'pass': r['added'] == ['58-32', '16A(78)-32', '56-32', '78B-32', '16B(78)-32'] and all(len(e['sha256']) == 64 and e['hash_matches'] is False and any('not byte for byte' in n for n in e['notes']) for e in rec['wells'])
                               and 'Attribution 4.0' in rec['licence']['name'] and 'Utah FORGE' in rec['credit'] and seen[-1]['step'] == 'done' and not os.listdir(os.path.join(site, '_incoming', 'public'))})
        rs = {w['display']: RS.well(ws, w['id']) for w in ws.wells()}
        ft = {k: v['footage'].get('rotary_ft', 0) + v['footage'].get('slide_ft', 0) for k, v in rs.items()}
        checks.append({'check': 'every layout read as a drilling record: the footage of each well its own, the connections found', 'value': {k: (round(v), len(rs[k]['connections'])) for k, v in ft.items()},
                       'pass': all(abs(ft[w] - (made[w]['hole_to'] - made[w]['hole_from'])) < 60.0 for w in ft) and all(len(v['connections']) >= 10 for v in rs.values())})
        from . import drill_string as DS
        c16a = DS._cache(ws, by['16A(78)-32']['well_id']); c16b = DS._cache(ws, by['16B(78)-32']['well_id']); c58 = DS._cache(ws, by['58-32']['well_id'])
        hole, bit = np.asarray(c16a.channel('Hole Depth'), float), np.asarray(c16a.channel('Bit Depth'), float)
        onb = np.asarray(c16a.channel('On Bottom'), float) == 1
        off = hole - bit
        checks.append({'check': '16A: the bit depth reconstructed - at the hole depth on bottom, some 30 ft off it at each connection, never below the hole - and said to be', 'value': [round(float(np.nanmax(off)), 1), round(float(np.nanmax(np.abs(off[onb]))), 3)],
                       'pass': by['16A(78)-32']['bit_depth_reconstructed'] and float(np.nanmax(np.abs(off[onb]))) < 0.01 and 25.0 <= float(np.nanmax(off)) <= 40.0 and float(np.nanmin(off)) >= -0.01
                               and any('RECONSTRUCTED' in n for n in by['16A(78)-32']['notes']) and abs(float(np.nanmax(np.asarray(c16a.channel('Convertible Torque'), float))) - 5.0) < 1e-3})
        tvd = np.asarray(c16b.channel('Interpolated TVD'), float); h16b = np.asarray(c16b.channel('Hole Depth'), float)
        lo, f_tvd = made['tvd_at']
        k = int(np.nanargmax(h16b))
        checks.append({'check': '16B: the channels the program reads out of the wide export, the true vertical depth from its survey', 'value': [sorted(c16b._ch)[:4], round(float(tvd[k]), 1), round(f_tvd(float(h16b[k])), 1)],
                       'pass': 'Mud Tank 1 Volume' not in c16b._ch and 'EYE TEMP' not in c16b._ch and 'Differential Pressure' in c16b._ch and by['16B(78)-32']['tvd_from_survey'] and abs(float(tvd[k]) - f_tvd(float(h16b[k]))) < 1.0})
        checks.append({'check': '58-32: the U.S.-unit columns under the names the program reads, the sentinel left blank, no weight on bit and said so', 'value': sorted(c58._ch),
                       'pass': {'Hole Depth', 'Bit Depth', 'Hook Load', 'Standpipe Pressure', 'Rotary RPM', 'Total Pump Output', 'Block Height'} <= set(c58._ch) and 'Weight on Bit' not in c58._ch
                               and bool(np.isnan(np.asarray(c58.channel('Block Height'), float)[:5]).all()) and any('no weight on bit' in n for n in by['58-32']['notes'])})
        pg = RM.page(ws)
        checks.append({'check': 'the roadmap over the five wells: every well in it, the stretches shared', 'value': [len(pg['wells']), len(pg['checks']), max(len(s['wells']) for s in pg['stretches'])],
                       'pass': pg['status'] == 'OK' and len(pg['wells']) == 5 and len(pg['checks']) == 5 and max(len(s['wells']) for s in pg['stretches']) >= 4})
        again = build(folder, site, 'selftest')
        errs = {}
        for name, fn in (('not_public', lambda: build(folder, os.path.join(tmp, 'other'), 'selftest', wells=['56-32'])), ('no_folder', lambda: build(os.path.join(tmp, 'nowhere'), os.path.join(tmp, 'x'), 'selftest')),
                         ('not_edr', lambda: convert_edr_slim(['a,b,c\n', '1,2,3\n'], io.StringIO())), ('not_16a', lambda: convert_16a(['Time,Hookload\n'], io.StringIO()))):
            try:
                fn(); errs[name] = ''
            except PublicPadError as e:
                errs[name] = str(e)
        other = Workspace.create(os.path.join(tmp, 'plain'), 'Plain', actor='selftest')
        try:
            build(folder, other.path, 'selftest'); errs['plain'] = ''
        except PublicPadError as e:
            errs['plain'] = str(e)
        checks.append({'check': 'built again adds nothing; one well is no pad; a folder that is not there, a file in another layout and a workspace not made from this data are each stopped', 'value': [again['added'], sorted(k for k, v in errs.items() if v)],
                       'pass': again['added'] == [] and 'two wells make a pad' in errs['not_public'] and 'no such folder' in errs['no_folder'] and 'not a recorder export' in errs['not_edr'] and 'has no' in errs['not_16a'] and 'was not made from' in errs['plain']})
        from . import frac_treatment as FT
        made_t = treatment_standins(folder)
        st1 = status(folder, verify=True)
        more = build(folder, site, 'selftest')
        pg_t = FT.page(Workspace(site))
        rec1 = record(Workspace(site))
        off = [abs((r_.get('isip_psi') or 0) - made_t[f"{r_['display']}|{r_['stage']}"]) for r_ in pg_t['stages']]
        third = build(folder, site, 'selftest')
        checks.append({'check': 'the stimulation files, put in the folder afterwards: found in its stimulation folder, sixteen stages attached to the two wells in both layouts, each stage\'s ISIP read, the hashes kept, the credit on the page; built again it adds none',
                       'value': [st1['treatments_present'], len(more['stages']), more['added'], [r_['stage'] for r_ in pg_t['stages']][:5], round(max(off)) if off else None, third['stages']],
                       'pass': st1['treatments_present'] == 5 and len(more['stages']) == 16 and more['added'] == [] and len(pg_t['stages']) == 16 and [r_['stage'] for r_ in pg_t['stages']][:5] == ['1', '2', '3', '3R', '4']
                               and max(off) <= 40.0 and len(rec1['treatments']) == 5 and all(e['hash_matches'] is False and len(e['sha256']) == 64 for e in rec1['treatments'])
                               and pg_t['lines'][-1].startswith('Data: Utah FORGE') and third['stages'] == [] and third['added'] == []})
        # the wells placed: a made-up survey in each published layout, the wellhead and the elevation each is placed by
        from . import collision as CO
        from . import geodesy as GD
        from . import well3d as W3
        bare = os.path.join(tmp, 'bare'); site_b = os.path.join(tmp, 'site-bare')
        standins(bare, stands=6)
        made_b = survey_standins(bare, locations=False)
        rb = build(bare, site_b, 'selftest', verify=False)
        pb = {e['well']: e.get('placed') or {} for e in rb['record']['wells']}
        sc_b = W3.workspace_scene(Workspace(site_b))
        survey_standins(bare)                                                    # the same surveys, and now the field's GPS survey of its wells
        rb2 = build(bare, site_b, 'selftest', verify=False)
        pb2 = {e['well']: e.get('placed') or {} for e in rb2['record']['wells']}
        checks.append({'check': 'without the field\'s GPS survey of its wells: the three wells whose own files give a wellhead and an elevation are drawn, 78B-32 is on the map and not drawn, 58-32 is not placed, and each is told why; '
                                'with it in the folder, the two are placed and the three are left as they were',
                       'value': [rb['placed'], sorted(w['name'] for w in sc_b['wells']), (pb['58-32'].get('why') or '')[:40], rb2['placed']],
                       'pass': rb['placed'] == ['16A(78)-32', '56-32', '78B-32', '16B(78)-32'] and sorted(w['name'] for w in sc_b['wells']) == ['16A(78)-32', '16B(78)-32', '56-32']
                               and pb['78B-32']['drawn'] is False and pb['78B-32'].get('lat') is not None and 'gives no elevation' in pb['78B-32']['why'] and pb['58-32']['drawn'] is False
                               and 'names no wellhead' in pb['58-32']['why'] and rb2['placed'] == ['58-32', '78B-32'] and all(p['drawn'] for p in pb2.values())
                               and pb2['16A(78)-32']['placed_utc'] == pb['16A(78)-32']['placed_utc']})
        made_s = survey_standins(folder)
        st2 = status(folder)
        seen2 = []
        r2 = build(folder, site, 'selftest', progress=seen2.append)
        ws2 = Workspace(site); rec2 = record(ws2)
        pl = {e['well']: e['placed'] for e in rec2['wells']}
        ok_read = []
        for well, m in made_s.items():
            p = pl[well]
            ll = GD.grid_to_wgs84(m['easting_m'], m['northing_m'], 'UTM12N', 'NAD83', 'm')['on_zone_datum']
            kb = well in ('16A(78)-32', '16B(78)-32')
            ok_read.append(p['stations'] == len(m['stations']) and p['td_md_ft'] == m['td'] and abs(p['lat'] - ll['lat']) < 2e-6 and abs(p['lon'] - ll['lon']) < 2e-6
                           and p['elev_datum'] == ('KB' if kb else 'GL') and abs(p['elev_ft'] - (m['kb_ft'] if kb else m['gl_ft'])) < 0.02 and p['miss_ft'] <= 0.05 and p['drawn']
                           and p['north'] == ('true' if well in ('58-32', '78B-32') else 'grid') and p['north_unstated'] == (well in ('58-32', '78B-32'))
                           and p['hash_matches'] is False and len(p['sha256']) == 64 and len(p['how']) >= 3)
        checks.append({'check': 'the surveys, put in the folder afterwards: a text report, a planning package\'s report in a spreadsheet, two reports printed to PDF and a spreadsheet\'s CSV each read - its stations, its '
                                'wellhead, the elevation its depths are counted from and which it is, the north its azimuths are from and whether the file said - and each checked against the positions the file carries',
                       'value': [st2['surveys_present'], r2['placed'], ok_read, {w: (p['elev_datum'], p['north'], p['miss_ft']) for w, p in pl.items()}],
                       'pass': st2['surveys_present'] == 5 and st2['surveys'][-1]['present'] and r2['placed'] == ['58-32', '16A(78)-32', '56-32', '78B-32', '16B(78)-32'] and all(ok_read) and r2['added'] == []
                               and seen2[-1].get('placed') == r2['placed'] and any('grid north' in h and 'agree to' in h for h in pl['56-32']['how']) and any('too deep' in h for h in pl['58-32']['how'])
                               and any('were they magnetic' in h for h in pl['78B-32']['how']) and any('does not name its grid' in h for h in pl['16A(78)-32']['how'])})
        sc = W3.workspace_scene(ws2)
        by_s = {w['name']: w for w in sc['wells']}
        co = CO.page(ws2)
        pair = next(p for p in co['pairs'] if p['reference_name'] == '16B(78)-32' and p['offset_name'] == '16A(78)-32')
        a_, b_ = made_s['16A(78)-32'], made_s['16B(78)-32']
        apart = ((a_['easting_m'] - b_['easting_m']) ** 2 + (a_['northing_m'] - b_['northing_m']) ** 2) ** 0.5
        deep = {w: abs(by_s[w]['td_z'] - (made_s[w]['stations'][-1]['tvd'] - pl[w]['elev_ft']) * FT_M) for w in by_s}
        from . import simulation as SIMU
        sims = [x['id'] for x in SIMU.scenes(ws2) if x['kind'] == 'workspace']
        third2 = build(folder, site, 'selftest')
        checks.append({'check': 'the placed wells drawn: all five in the 3D scene, each bottom at the depth below sea level its survey and its elevation give; the two deviated wells, made parallel, found that far apart by the '
                                'collision scan; each well offered to the simulation with its record; built again it places none',
                       'value': [len(sc['wells']), sc['not_placed'], round(max(deep.values()), 2), round(apart, 2), pair['closest_m'], len(sims), third2['placed']],
                       'pass': len(sc['wells']) == 5 and sc['not_placed'] == [] and max(deep.values()) < 0.6 and abs(pair['closest_m'] - apart) < 1.0 and abs(pair['distance_m'] - apart) < 3.0 and len(sims) == 5
                               and all(w['replay'] for w in sc['wells']) and third2['placed'] == [] and third2['added'] == []})
        errs2 = {}
        good = made_s['16B(78)-32']
        with zipfile.ZipFile(_find(folder, SURVEYS['16B(78)-32']['files'][0])) as z:
            rep_txt = z.read(SURVEYS['16B(78)-32']['member']).decode('utf-8')
        from . import xlsx_read as X
        with open(_find(folder, SURVEYS['16A(78)-32']['files'][0]), 'rb') as f:
            xb = f.read()
        rows_x = [[c for c in r_ if c != ''] for r_ in X.rows(xb, X.sheets(xb)[0])]
        xp = os.path.join(tmp, 'nocorr.xlsx'); X.write_simple(xp, [r_ for r_ in rows_x if 'conv)' not in r_])
        with open(xp, 'rb') as f:
            xb2 = f.read()
        for name, fn in (('magnetic', lambda: read_survey_report(rep_txt.replace(': Grid', ': Magnetic'))), ('state_plane', lambda: read_survey_report(rep_txt.replace('Universal Transverse Mercator', 'State Plane'))),
                         ('no_kb', lambda: read_survey_report(rep_txt.replace('KB ELEV', 'XX ELEV'))), ('not_listing', lambda: read_survey_listing_pdf(_standin_pdf([[[(72.0, 'A'), (120.0, 'page')]]]))),
                         ('not_pdf', lambda: read_survey_listing_pdf(b'not a pdf at all')), ('no_correction', lambda: read_survey_compass(xb2)), ('no_points', lambda: read_locations('a,b\n1,2\n')),
                         ('no_columns', lambda: read_positions_csv('x,y\n1,2\n'))):
            try:
                fn(); errs2[name] = ''
            except PublicPadError as e:
                errs2[name] = str(e)
        rows_pdf = pdf_text_rows(_standin_pdf([[[(200.0, 'right (2)'), (80.0, 'left')], [(80.0, 'below')]]]))
        checks.append({'check': 'what is not read is said: azimuths from magnetic north, a grid that is not a UTM zone, a report with no kelly bushing elevation, a PDF that is no survey listing, a design report that does not '
                                'say how its azimuths were corrected, a locations file and a path file without their columns; a PDF\'s words come back in reading order',
                       'value': [sorted(k for k, v in errs2.items() if v), rows_pdf, good['td']],
                       'pass': all(errs2.values()) and 'grid or true' in errs2['magnetic'] and 'not a UTM zone' in errs2['state_plane'] and 'KB ELEV' in errs2['no_kb'] and 'not a listing' in errs2['not_listing']
                               and 'which north' in errs2['no_correction'] and rows_pdf == [['left', 'right (2)'], ['below']]})
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
