"""The simulation: one well drilled again on the screen. The bit runs down the well's path in 3D among the mapped
formations, on the clock it was drilled by when that clock was recorded, while a mud-log panel beside it grows with
the hole - the rock and fluid volumes, the formation, the logs and the drilling parameters at each depth - and a
readout says what is at the bit.

Nothing here is modelled. Every value drawn is one the well's own record carries:

- Volve 15/9-F-4 (shipped): the survey's path and the field's picks and horizons (the 3D tab's excerpt), with the
  logging-while-drilling record of its 8.5 in section and Schlumberger's petrophysical interpretation of it - the
  mineral volumes and the total porosity, which sum to one, the water saturation and the hydrocarbon volume the
  interpretation computed, the permeability. Its survey stations were resurveyed in one go, so they carry no
  drilling times: the bit is stepped along measured depth, and the page says so.
- Volve 15/9-F-5 and 15/9-F-10 (shipped): the bit on each survey station's own time stamp; the formation picks and
  the survey's inclination and dogleg along the path. No logs ship for them.
- A workspace well with a drilling record: the bit and the bottom of the hole on the drilling recorder's own clock,
  its channels binned by bit depth while drilling (kept off hole a later sidetrack abandoned), and the gamma index
  lithology of its drilling record. The path is its directional survey when one is attached and the well is placed;
  otherwise the recorder's own MD-TVD pairs drawn in one vertical plane, whose azimuth nothing in the record gives.

Depths inside the program are metres along hole (MD) and metres below the scene's reference; the page shows feet for
a workspace well where the site works in feet.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
REF_DIR = os.path.join(os.path.dirname(__file__), 'reference')
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[tuple, dict]] = {}

# the volume column of the interpretation, in the order it is stacked (rock first, then the pore space)
F4_VOLUMES = [('Quartz, feldspar, mica', ['VQFM']), ('Clay', ['VCLA']), ('Carbonate (calcite, dolomite)', ['VCLC', 'VDOL']),
              ('Pyrite, siderite and other minerals', ['VPYR', 'VSID', 'VANH', 'VEVA', 'VCOA'])]
# (id, name, unit, file, mnemonic, scale, conventional range or None for the data's own)
F4_TRACKS = [('gr', 'Gamma ray', 'gAPI', 'lwd', 'GRMA', 'linear', (0.0, 150.0)),
             ('rhob', 'Bulk density', 'g/cm3', 'lwd', 'ROBB', 'linear', (1.95, 2.95)),
             ('nphi', 'Neutron porosity', 'v/v', 'lwd', 'BPHI', 'linear', (0.0, 0.6)),
             ('pef', 'Photoelectric factor', 'b/e', 'lwd', 'PEB', 'linear', (0.0, 10.0)),
             ('rt', 'True resistivity', 'ohm.m', 'lwd', 'RT_ARC', 'log', (0.2, 2000.0)),
             ('rop', 'Rate of penetration, last 1.5 m', 'm/h', 'lwd', 'ROP5_RM', 'linear', (0.0, 60.0)),
             ('phit', 'Total porosity', 'v/v', 'interpretation', 'PHIT', 'linear', (0.0, 0.4)),
             ('sw', 'Water saturation, uninvaded zone', 'v/v', 'interpretation', 'SWT', 'linear', (0.0, 1.0)),
             ('vcl', 'Clay volume', 'v/v', 'interpretation', 'VCL', 'linear', (0.0, 1.0)),
             ('k', 'Intrinsic permeability', 'mD', 'interpretation', 'KINT', 'log', (0.01, 10000.0))]
# a drilling recorder's channels, by the names the reader gives them (edr.CH), with their usual names on a mud log
EDR_TRACKS = [('gamma', 'Gamma', 'Gamma'), ('rop', 'Rate of penetration', 'Rate Of Penetration'), ('wob', 'Weight on bit', 'Weight on Bit'),
              ('rpm', 'Rotary speed', 'Rotary RPM'), ('torque', 'Torque', 'Convertible Torque'), ('spp', 'Standpipe pressure', 'Standpipe Pressure'),
              ('dp', 'Differential pressure', 'Differential Pressure'), ('flow', 'Pump output', 'Total Pump Output'), ('hkld', 'Hook load', 'Hook Load')]
DEPTH_STEPS = 900                  # the bit's steps along measured depth when no clock was recorded


def _r(v, nd: int = 4):
    """A number for the page: four significant figures, None for a gap."""
    if v is None:
        return None
    v = float(v)
    if not math.isfinite(v):
        return None
    if v == 0:
        return 0.0
    return float(f'{v:.{nd}g}')


def _prov() -> dict:
    with open(os.path.join(REF_DIR, 'volve_f4_logs.provenance.json'), encoding='utf-8') as f:
        return json.load(f)


def _read_shipped_las(key: str):
    """A shipped LAS file, read by the program's own LAS reader from the verbatim gzip."""
    from .ports import read_las
    p = _prov()['files'][key]
    with open(os.path.join(REF_DIR, p['file']), 'rb') as f:
        raw = gzip.decompress(f.read())
    fd, tmp = tempfile.mkstemp(suffix='.las')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
        return read_las(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _ch(stream, name) -> Optional[np.ndarray]:
    c = stream.channels.get(name)
    if c is None:
        return None
    v = np.asarray(c.values, dtype=np.float64).copy()
    v[v <= -999.0] = np.nan                       # the LAS null, whichever way the reader left it
    return v


def _range(v: np.ndarray, scale: str, conv) -> List[float]:
    if conv:
        return [conv[0], conv[1]]
    ok = v[np.isfinite(v)]
    if scale == 'log':
        ok = ok[ok > 0]
    if not len(ok):
        return [0.0, 1.0]
    lo, hi = np.percentile(ok, [1, 99])
    return [_r(lo), _r(hi if hi > lo else lo + 1)]


def _formations(picks: List[dict]) -> List[dict]:
    """Each pick opens an interval down to the next deeper pick: the formation a depth is in, as the well's picks name it."""
    ps = sorted([p for p in picks if p.get('md') is not None], key=lambda p: p['md'])
    out = []
    for i, p in enumerate(ps):
        nxt = ps[i + 1]['md'] if i < len(ps) - 1 else None
        nm = p['name'][:-4].rstrip() if p['name'].lower().endswith(' top') else p['name']
        out.append({'name': nm, 'pick': p['name'], 'top': _r(p['md'], 7), 'base': _r(nxt, 7) if nxt is not None else None, 'surface': p.get('surface')})
    # two picks at one depth: the one listed after names the interval; the other has no thickness and is left out
    return [f for f in out if f['base'] is None or f['base'] > f['top']]


def _depth_replay(path_md: List[float], how: str) -> dict:
    md = np.linspace(float(path_md[0]), float(path_md[-1]), DEPTH_STEPS)
    return {'kind': 'measured depth', 'how': how, 't': [None] * len(md), 'md': [_r(v, 7) for v in md], 'hole': [_r(v, 7) for v in md]}


# -- the scenes ---------------------------------------------------------------------------------------
def scenes(ws=None) -> List[dict]:
    out = [{'id': 'volve-f4', 'title': 'Volve 15/9-F-4 - logs and their interpretation', 'kind': 'reference', 'clock': 'measured depth',
            'note': 'the 8.5 in reservoir section logged while drilling and interpreted; the bit stepped along measured depth (no drilling times)'},
           {'id': 'volve-f5', 'title': 'Volve 15/9-F-5 - on the survey clock', 'kind': 'reference', 'clock': 'survey stations',
            'note': "the bit on each survey station's own time; formations and the path's inclination"},
           {'id': 'volve-f10', 'title': 'Volve 15/9-F-10 - on the survey clock', 'kind': 'reference', 'clock': 'survey stations',
            'note': "the bit on each survey station's own time; formations and the path's inclination"}]
    if ws is not None:
        for w in ws.wells():
            if os.path.isfile(os.path.join(ws.reports_dir, 'wells', w['id'], 'drilling_record.json')):
                out.append({'id': 'well:' + w['id'], 'title': f"{w.get('display') or w['id']} - on the drilling recorder's clock", 'kind': 'workspace',
                            'clock': 'recorded', 'note': "the bit and the bottom of the hole on the recorder's own clock, its channels by depth"})
    return out


def scene(ws, sid: str, build_cache: bool = True) -> dict:
    if sid == 'volve-f4':
        return _volve(sid, 'F-4')
    if sid in ('volve-f5', 'volve-f10'):
        return _volve(sid, sid.split('-')[1].upper().replace('F', 'F-'))
    if sid.startswith('well:') and ws is not None:
        return _workspace(ws, sid[5:], build_cache=build_cache)
    raise KeyError(f'no simulation {sid}')


def _volve(sid: str, wid: str) -> dict:
    with _LOCK:
        hit = _CACHE.get(sid)
        if hit:
            return hit[1]
    from . import well3d as W3
    vs = W3.volve_scene()
    w = dict(next(x for x in vs['wells'] if x['id'] == wid))
    tracks, columns, sources, not_measured = [], [], [], []
    for name, ch in (w.get('channels') or {}).items():
        v = np.asarray([np.nan if x is None else x for x in ch['v']], dtype=np.float64)
        tracks.append({'id': name.lower().replace(' ', '_'), 'name': name, 'unit': ch['unit'], 'scale': 'linear', 'range': _range(v, 'linear', None),
                       'md': ch['md'], 'v': ch['v'], 'source': 'the directional survey, by the minimum-curvature method', 'group': 'path'})
    if wid == 'F-4':
        lwd, itp = _read_shipped_las('lwd'), _read_shipped_las('interpretation')
        files = {'lwd': lwd, 'interpretation': itp}
        prov = _prov()
        for tid, name, unit, fk, mn, scale, conv in F4_TRACKS:
            st = files[fk]
            v = _ch(st, mn)
            if v is None:
                continue
            md = np.asarray(st.index, dtype=np.float64)
            tracks.append({'id': tid, 'name': name, 'unit': unit, 'scale': scale, 'range': _range(v, scale, conv),
                           'md': [_r(x, 7) for x in md], 'v': [_r(x) for x in v], 'source': f"{prov['files'][fk]['what']}, curve {mn}",
                           'group': 'log' if fk == 'lwd' else 'interpretation'})
        md = np.asarray(itp.index, dtype=np.float64)
        step = float(np.median(np.diff(md)))
        parts, fr = [], []
        for name, mns in F4_VOLUMES:
            acc = np.zeros(len(md))
            for mn in mns:
                v = _ch(itp, mn)
                if v is not None:
                    acc += np.nan_to_num(v)
            parts.append(name); fr.append(acc)
        phit, swt = _ch(itp, 'PHIT'), _ch(itp, 'SWT')
        hc = _ch(itp, 'VUHY')
        water = phit - np.nan_to_num(hc)
        parts = ['Water (in the pores)', 'Hydrocarbon (in the pores)'] + parts      # the pore fluids first, then the rock
        fr = [water, np.nan_to_num(hc)] + fr
        F = np.vstack(fr).T
        columns.append({'id': 'volumes', 'name': 'Rock and fluid volumes', 'parts': parts, 'kinds': ['water', 'hydrocarbon'] + ['rock'] * 4,
                        'top': [_r(x - step / 2, 7) for x in md], 'base': [_r(x + step / 2, 7) for x in md],
                        'f': [[_r(x, 3) for x in row] for row in F],
                        'source': prov['files']['interpretation']['what'],
                        'how': "the interpretation's mineral volume fractions and its total porosity, which sum to one at every depth; the pore "
                               "space split by its water saturation of the uninvaded zone into water and the hydrocarbon volume it computed "
                               "(PHIT times one less SWT). The interpretation does not tell oil from gas; its parameters take the "
                               "hydrocarbon as oil of 10 cP"})
        sources.append({'what': 'logs and interpretation of the 8.5 in section, 2,956 to 3,510 m MD', 'attribution': prov['attribution']})
        w['replay'] = _depth_replay(w['path']['md'], "stepped along measured depth: 15/9-F-4's survey was run in one go, so its "
                                                     "stations carry no drilling times, and no drilling clock ships for it")
        clock = {'kind': 'measured depth', 'note': w['replay']['how']}
        not_measured.append('above 2,956 m MD: no log ships for the upper sections, so the panel shows the formations alone there')
    else:
        rp = w.get('replay')
        if rp:
            hole = np.maximum.accumulate(np.asarray(rp['md'], dtype=np.float64))
            w['replay'] = {**rp, 'hole': [_r(v, 7) for v in hole]}
            clock = {'kind': 'recorded', 'note': rp.get('how') or "each survey station's own time stamp"}
        else:
            w['replay'] = _depth_replay(w['path']['md'], 'stepped along measured depth (no station times)')
            clock = {'kind': 'measured depth', 'note': w['replay']['how']}
        not_measured.append('no logs ship for this well: the panel shows its formations and the path itself')
    w['channels'] = {}                                   # the page colours the path from the tracks themselves (one copy of each)
    out = {'id': sid, 'title': next(s['title'] for s in scenes() if s['id'] == sid), 'kind': 'reference', 'well_id': wid,
           'scene': {'id': 'volve', 'title': vs['title'], 'kind': 'reference', 'frame': vs['frame'], 'wells': [w], 'picks_only': [],
                     'surfaces': vs['surfaces'], 'layers': [], 'attribution': vs['attribution'], 'not_drawn': vs.get('not_drawn', []), 'z_ref': 'sea level'},
           'clock': clock, 'tracks': tracks, 'columns': columns, 'formations': _formations(w.get('picks') or []),
           'intervals': [], 'td_md': w['td_md'], 'sources': sources + [{'what': 'the path, the picks and the horizons', 'attribution': vs['attribution']}],
           'not_measured': not_measured, 'depth_unit': 'm'}
    with _LOCK:
        _CACHE[sid] = ((), out)
    return out


def _mdtvd_path(rec: dict, ref_elev_m: Optional[float]) -> Tuple[dict, str]:
    """The recorder's own MD-TVD pairs in one vertical plane: the horizontal displacement each step adds is what the
    measured length leaves over the vertical one; the azimuth is not in the record."""
    mt = rec.get('md_tvd') or {}
    z0 = -(ref_elev_m or 0.0)
    if mt.get('status') == 'OK' and mt.get('rows'):
        md = [0.0] + [r['md_ft'] * FT for r in mt['rows']]
        tvd = [0.0] + [r['tvd_ft'] * FT for r in mt['rows']]
        x = [0.0]
        for i in range(1, len(md)):
            dm, dv = md[i] - md[i - 1], tvd[i] - tvd[i - 1]
            x.append(x[-1] + math.sqrt(max(0.0, dm * dm - min(dv * dv, dm * dm))))
        td = (rec.get('total_depth_md_ft') or 0) * FT
        if td > md[-1]:                                  # below the last pair the hole is drawn on at the last angle
            k = (x[-1] - x[-2]) / ((md[-1] - md[-2]) or 1), (tvd[-1] - tvd[-2]) / ((md[-1] - md[-2]) or 1)
            dm = td - md[-1]; md.append(td); x.append(x[-1] + k[0] * dm); tvd.append(tvd[-1] + k[1] * dm)
        how = ("the drilling recorder's own MD-TVD pairs, drawn in one vertical plane: the record gives no azimuth, so the plane's "
               "direction is not known" + ('' if td <= mt['rows'][-1]['md_ft'] * FT else '; below its last pair the hole is drawn on at the last angle'))
        return {'md': [_r(v, 7) for v in md], 'x': [_r(v, 6) for v in x], 'y': [0.0] * len(md), 'z': [_r(v + z0, 6) for v in tvd]}, how
    td = (rec.get('total_depth_md_ft') or 0) * FT
    return ({'md': [0.0, _r(td, 7)], 'x': [0.0, 0.0], 'y': [0.0, 0.0], 'z': [_r(z0, 6), _r(td + z0, 6)]},
            'drawn vertical: there is neither a survey nor a TVD channel in the record')


def _workspace(ws, wid: str, build_cache: bool = True) -> dict:
    from . import series_cache as SCH
    w = ws.well(wid)
    rp = os.path.join(ws.reports_dir, 'wells', wid, 'drilling_record.json')
    if not os.path.isfile(rp):
        raise KeyError(f'{wid} has no drilling record: refresh the reports once its drilling-recorder export is attached')
    d, src = SCH.cache_dir(ws, wid), SCH.source_path(ws, wid)
    c = SCH.ensure(ws, wid) if build_cache else SCH.load(d, src)
    if c is None:
        raise KeyError(f"{wid}'s chart cache is not built: open the well's charts once (or refresh) and the simulation follows")
    key = (os.path.getmtime(rp), c.meta.get('built_utc'), c.meta.get('source_mtime'), json.dumps(w.get('surface'), sort_keys=True, default=str),
           json.dumps(w.get('trajectory'), sort_keys=True, default=str))
    with _LOCK:
        hit = _CACHE.get('well:' + wid)
        if hit and hit[0] == key:
            return hit[1]
    with open(rp, encoding='utf-8') as f:
        rec = json.load(f)
    name = w.get('display') or wid
    elev = ((w.get('surface') or {}).get('elevation') or {})
    ref_elev_m = elev['ft'] * FT if elev.get('ft') is not None else None
    # the path: the survey in the workspace's 3D frame when the well is drawn there; else the recorder's MD-TVD
    path, path_how, picks, frame, layers, surfaces, z_ref = None, '', [], None, [], [], None
    if w.get('trajectory'):
        try:
            from . import well3d as W3
            ws3 = W3.workspace_scene(ws)
            w3 = next((x for x in ws3['wells'] if x['id'] == wid), None)
            if w3:
                path, picks, frame, layers = w3['path'], w3.get('picks') or [], ws3.get('frame'), ws3.get('layers') or []
                path_how = f"the directional survey ({w3.get('source', '')}), by the minimum-curvature method, below sea level from the declared reference"
                z_ref = 'sea level'
        except Exception:
            path = None
    if path is None:
        path, path_how = _mdtvd_path(rec, ref_elev_m)
        z_ref = 'sea level' if ref_elev_m is not None else 'the rig floor (no reference elevation is declared)'
    # the channels by bit depth while drilling
    names = [n for _, _, n in EDR_TRACKS if n in c._ch]
    dw = SCH.depth_window(c, names, points=1500, depth_channel='Bit Depth') if names and 'Bit Depth' in c._ch else {'x': [], 'channels': {}}
    xs = [None if v is None else v * FT for v in dw.get('x', [])]
    tracks = []
    for tid, label, n in EDR_TRACKS:
        ch = dw['channels'].get(n)
        if not ch:
            continue
        v = np.asarray([np.nan if x is None else x for x in ch['mean']], dtype=np.float64)
        if not np.isfinite(v).any():
            continue
        tracks.append({'id': tid, 'name': label, 'unit': ch['unit'] or '', 'scale': 'linear', 'range': _range(v, 'linear', None),
                       'md': [_r(x, 7) for x in xs], 'v': [_r(x) for x in v], 'group': 'drilling' if tid != 'gamma' else 'log',
                       'source': f"the drilling recorder's {n}, the mean of the drilling samples in each {dw.get('bucket', 0):.1f} ft of bit depth"})
    # the gamma index lithology of the drilling record
    columns, intervals = [], []
    li = ((rec.get('gamma') or {}).get('lithology') or {})
    if li.get('status') == 'OK':
        rows = li.get('intervals') or []
        top = [_r(x['top_depth'] * FT, 7) for x in rows]; base = [_r(x['base_depth'] * FT, 7) for x in rows]
        f = [[_r(1 - x['mean_vsh'], 3), _r(x['mean_vsh'], 3)] for x in rows]
        columns.append({'id': 'gamma_index', 'name': 'Gamma index lithology', 'parts': ['Clean (low gamma)', 'Shaly (high gamma)'], 'kinds': ['rock', 'rock'],
                        'top': top, 'base': base, 'f': f, 'source': "the drilling record's gamma log (bit depth, on bottom)",
                        'how': f"the shale index from the recorder's gamma between its clean and shale picks ({li['vsh']['gr_clean_api']} and "
                               f"{li['vsh']['gr_shale_api']} gAPI), one bar per interval; a gamma index, not a mineral analysis"})
        intervals = [{'top': top[i], 'base': base[i], 'flag': rows[i]['flag']} for i in range(len(rows))]
    # the bit and the bottom of the hole on the recorder's clock
    replay = None
    if 'Bit Depth' in c._ch:
        tw = SCH.window(c, ['Bit Depth'] + (['Hole Depth'] if 'Hole Depth' in c._ch else []), points=2400)
        t0 = datetime.fromisoformat(c.meta['start_time']) if c.meta.get('start_time') else None
        if tw.get('mode') == 'samples':
            bit = tw['channels']['Bit Depth']['value']; hole = (tw['channels'].get('Hole Depth') or {}).get('value') or bit
        else:
            bit = tw['channels']['Bit Depth']['mean']; hole = (tw['channels'].get('Hole Depth') or {}).get('max') or tw['channels']['Bit Depth']['max']
        bottom = c.extra('bottom')
        T, B, H = [], [], []
        lastb, lasth = 0.0, 0.0
        hx = None
        if bottom is not None:                           # the running bottom of the hole from the depth pass, not the raw channel
            idx = np.asarray(c.index, dtype=np.float64); bo = np.asarray(bottom, dtype=np.float64)
            half = (tw.get('bucket') or 0.0) / 2.0              # the bottom as it stood at the end of each bucket
            pos = np.searchsorted(idx, np.asarray(tw['x'], dtype=np.float64) + half, side='right') - 1
            run = np.maximum.accumulate(np.where(np.isfinite(bo), bo, -np.inf))
            hx = [float(run[p]) if p >= 0 and run[p] > -np.inf else None for p in np.clip(pos, -1, len(bo) - 1)]
        for i, x in enumerate(tw['x']):
            b = bit[i] if bit[i] is not None else lastb
            h = (hx[i] if hx is not None and hx[i] is not None else (hole[i] if hole[i] is not None else lasth))
            lastb, lasth = b, max(lasth, h) if hx is None else h
            T.append((t0 + timedelta(seconds=round(float(x)))).isoformat() if t0 else None)
            B.append(_r(max(0.0, b) * FT, 7)); H.append(_r(lasth * FT, 7))
        replay = {'kind': "the drilling recorder's clock", 'how': f"the bit depth (the mean in each {(tw.get('bucket') or 1) / 60:.0f} min) and the "
                                                                   f"bottom of the hole from the depth pass, on the recorder's own clock (not UTC)",
                  't': T, 'md': B, 'hole': H}
    if replay is None:
        replay = _depth_replay(path['md'], 'stepped along measured depth: the record has no bit depth channel')
    clock = {'kind': 'recorded' if replay.get('t') and replay['t'][0] else 'measured depth', 'note': replay['how']}
    well = {'id': wid, 'name': name, 'path': path, 'picks': picks, 'stations': {'md': path['md'], 'incl': [None] * len(path['md']), 'azi': [None] * len(path['md'])},
            'channels': {}, 'replay': replay, 'td_md': _r((rec.get('total_depth_md_ft') or 0) * FT, 7), 'path_how': path_how}
    not_measured = ['density, porosity and the fluids: a drilling recorder does not measure them, and no log is attached for this well']
    if not picks:
        not_measured.append('the formations along the hole: ' + ('no mapped top or pick is met along the surveyed path' if z_ref == 'sea level' and w.get('trajectory')
                                                               else 'the well is not placed with a survey, so no mapped top is met along it'))
    out = {'id': 'well:' + wid, 'title': f"{name} - on the drilling recorder's clock", 'kind': 'workspace', 'well_id': wid,
           'scene': {'id': 'workspace', 'title': name, 'kind': 'workspace', 'frame': frame, 'wells': [well], 'picks_only': [], 'surfaces': surfaces,
                     'layers': layers, 'attribution': '', 'not_drawn': [], 'z_ref': z_ref, 'no_north': frame is None},
           'clock': clock, 'tracks': tracks, 'columns': columns, 'formations': _formations(picks), 'intervals': intervals,
           'td_md': well['td_md'], 'path_how': path_how, 'sources': [{'what': f"the drilling record from {rec.get('source')}", 'attribution': ''}],
           'not_measured': not_measured, 'depth_unit': 'ft'}
    with _LOCK:
        _CACHE['well:' + wid] = (key, out)
    return out


# -- what is at the bit -------------------------------------------------------------------------------
def _interp(md: List[float], v: List[float], x: float) -> Optional[float]:
    a = [m for m in md if m is not None]
    if not a or x < a[0] or x > a[-1]:
        return None
    i = int(np.searchsorted(np.asarray(md, dtype=np.float64), x, side='right')) - 1
    i = max(0, min(i, len(md) - 2))
    if v[i] is None or v[i + 1] is None:
        return v[i] if v[i] is not None else v[i + 1]
    f = (x - md[i]) / ((md[i + 1] - md[i]) or 1.0)
    return v[i] + (v[i + 1] - v[i]) * min(1.0, max(0.0, f))


def at(sim: dict, md: float) -> dict:
    """What the record says at one measured depth (metres): the formation, each track, each column's composition."""
    out = {'md_m': md, 'formation': None, 'tracks': {}, 'columns': {}}
    for f in sim['formations']:
        if f['top'] <= md and (f['base'] is None or md < f['base']):
            out['formation'] = f['name']
    for t in sim['tracks']:
        out['tracks'][t['name']] = {'value': _interp(t['md'], t['v'], md), 'unit': t['unit']}
    for c in sim['columns']:
        k = next((i for i in range(len(c['top'])) if c['top'][i] <= md < c['base'][i]), None)
        out['columns'][c['name']] = None if k is None else dict(zip(c['parts'], c['f'][k]))
    p = sim['scene']['wells'][0]['path']
    out['z_m'] = _interp(p['md'], p['z'], md)
    return out


def _num(v) -> str:
    return '-' if v is None else f'{v:,.4g}'


def text(sim: dict, md: Optional[float] = None) -> str:
    unit = sim.get('depth_unit', 'm')
    k = FT if unit == 'ft' else 1.0
    L = [f"{sim['title']}", f"  clock: {sim['clock']['kind']} - {sim['clock']['note']}",
         f"  tracks: {', '.join(t['name'] for t in sim['tracks']) or 'none'}",
         f"  columns: {', '.join(c['name'] for c in sim['columns']) or 'none'}; formations: {len(sim['formations'])}",
         f"  replay: {len(sim['scene']['wells'][0]['replay']['md'])} steps to {sim['td_md'] / k:,.1f} {unit} MD"]
    for n in sim['not_measured']:
        L.append(f'  not shown: {n}')
    if md is not None:
        a = at(sim, md * k)
        L.append(f"At {md:,.1f} {unit} MD" + (f", {a['z_m'] / k:,.1f} {unit} below {sim['scene'].get('z_ref') or 'the reference'}" if a['z_m'] is not None else '')
                 + (f", in {a['formation']}" if a['formation'] else '') + ':')
        for n, t in a['tracks'].items():
            L.append(f"  {n}: {_num(t['value'])} {t['unit']}")
        for n, comp in a['columns'].items():
            L.append(f"  {n}: " + ('-' if comp is None else ', '.join(f'{p} {v:.0%}' for p, v in comp.items() if v)))
    return '\n'.join(L)
