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
}
DATASETS = {FORGE['id']: FORGE}
# the recorder channels the program reads, by the header's name without its unit
KEEP = ('Hole Depth', 'Bit Depth', 'Rate Of Penetration', 'Weight on Bit', 'Rotary RPM', 'Standpipe Pressure', 'Hook Load', 'Differential Pressure', 'Total Pump Output', 'Block Height',
        'Convertible Torque', 'Rotary Torque', 'On Bottom', 'Gamma', 'Interpolated TVD', 'Trip Tank Mud Volume', 'Total Mud Volume', 'Inclination', 'Azimuth', 'Pump Total Strokes Rate')
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
    return {'dataset': ds['id'], 'name': ds['name'], 'about': ds['about'], 'licence': ds['licence'], 'credit': ds['credit'], 'folder': folder, 'folder_exists': bool(folder) and os.path.isdir(folder),
            'files': rows, 'present': n, 'ready': n >= 2, 'total_bytes': sum(w['bytes'] for w in ds['wells']),
            'lines': [f"{ds['name']}: {n} of {len(rows)} recorder files are in the folder" + ('' if n >= 2 else '; two or more make a pad') + '.',
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
    if added:
        ws.audit(actor, 'public_pad.build', {'dataset': ds['id'], 'added': added, 'folder': os.path.abspath(folder), 'hashes_match': {e['well']: e.get('hash_matches') for e in rec['wells']}})
    say({'step': 'done', 'added': added})
    return {'path': ws.path, 'name': ds['site_name'], 'added': added, 'wells': [e['well'] for e in rec['wells']], 'record': rec}


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
    return {'ok': all(c['pass'] for c in checks), 'checks': checks, 'label': 'SIMULATION'}
