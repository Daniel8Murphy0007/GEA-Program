# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""catalog_map - where every shipped catalogue entry was drilled, and the world map of them.

A position here comes from the entry's own file, or it is NO POSITION. The chain, in order:

1. the data file's header - a PANGAEA `LATITUDE: x * LONGITUDE: y` event line (WGS84 by PANGAEA's convention; a file
   with several events gives its first, and says how many it holds), a LAS `LATI .DEG` / `LONG .DEG` pair, a LAS
   `LOC` line in degrees-minutes-seconds, or a KTB `LATI`/`LONG` header in Gauss-Krueger zone 4 (Rechtswert and
   Hochwert on DHDN, unprojected on the Bessel ellipsoid and shifted to WGS84 by the published three-parameter
   transformation, which is worth about 3 m);
2. the same well - an entry whose file carries no position but which is declared below to be the same borehole as an
   entry whose file does (the KTB main hole's composite log, strength and trajectory files are the main hole);
3. the provenance sidecar - the `well` statement written at catalogue time from the source database's own record
   (`Lat 37.41565 / Long -101.58701` from the Kansas Geological Survey well record); an `approx` there is carried as
   approximate;
4. NO POSITION, with what the file and the sidecar do say (a place name, a section-township-range, a licence block).

Nothing is looked up, remembered or guessed: a well-known site whose file states no coordinate is NO POSITION.

The positions are written once into each sidecar (`tools/catalog_positions.py`) as the record; this module recomputes
them from the files and the gate holds the two equal. The map is an equirectangular drawing of the Natural Earth 1:110m
coastline (gea/fixtures, public domain) with one mark per positioned entry - the picture of where the catalogue's
49 wells and sites are, which is all a world map is for.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .profile_catalog import CATALOG, CatalogEntry

FIXTURES = Path(__file__).parent / 'fixtures'
COAST_FILE = FIXTURES / 'ne_110m_coastline.json'

# an entry whose own file has no position, and the entry whose file positions the same borehole
SAME_WELL: Dict[str, str] = {
    'ktb_hb_complog_6020_excerpt': 'ktb_hb_hlog246_temperature',
    'ktb_hb_rockmech_compress': 'ktb_hb_hlog246_temperature',
    'ktb_hb_tvd_0_9080_excerpt': 'ktb_hb_hlog246_temperature',
    'ktb_vb_rockmech_compress': 'ktb_vb_vlog251_temperature',
}

_PANGAEA_LAT = re.compile(r'LATITUDE\s*:\s*(-?\d+(?:\.\d+)?)')          # PANGAEA writes it in capitals
_PANGAEA_LON = re.compile(r'LONGITUDE\s*:\s*(-?\d+(?:\.\d+)?)')
_KTB_DEC_LAT = re.compile(r'"Latitude:\s*(-?\d+(?:\.\d+)?)\s*degrees"')   # a KTB data file's own header line
_KTB_DEC_LON = re.compile(r'"Longitude:\s*(-?\d+(?:\.\d+)?)\s*degrees"')
_LAS_LATI = re.compile(r'^\s*LATI\s*\.\s*DEG\s+(-?\d+(?:\.\d+)?)', re.I | re.M)
_LAS_LONG = re.compile(r'^\s*LONG\s*\.\s*DEG\s+(-?\d+(?:\.\d+)?)', re.I | re.M)
_DMS = r"(\d+)\s*[*°]\s*(\d+)\s*'\s*(\d+(?:\.\d+)?)\s*\"?\s*([NSEW])"
_LAS_LAT_DMS = re.compile(r'Lat\s*=\s*' + _DMS, re.I)
_LAS_LON_DMS = re.compile(r'Long\s*=\s*' + _DMS, re.I)
# KTB: `"LONG  UNAL  Longitude   R 45 08 775,24"` / `RW:45 08 590` and `"LATI  UNAL  Latitude   H 55 19 864,35"` / `HW:55 19 865`
_KTB_R = re.compile(r'"LONG\s+UNAL\s+Longitude\s+RW?:?\s*(\d{2})\s+(\d{2})\s+(\d{3}(?:,\d+)?)"')
_KTB_H = re.compile(r'"LATI\s+UNAL\s+Latitude\s+HW?:?\s*(\d{2})\s+(\d{2})\s+(\d{3}(?:,\d+)?)"')
# the sidecar's `well` statement: `Lat 37.41565 / Long -101.58701`, `(approx 80.7 N, -73.1 E ...`, `(87.8900 N, 137.6500 E`
_PROV_LATLONG = re.compile(r'Lat\s+(-?\d+\.\d+)\s*/\s*Long\s+(-?\d+\.\d+)', re.I)
_PROV_PAIR = re.compile(r'\(\s*(approx\.?\s+)?(-?\d+\.\d+)\s*([NS]),\s*(-?\d+\.\d+)\s*([EW])')


def _dms(d, m, s, hemi) -> float:
    v = float(d) + float(m) / 60.0 + float(s) / 3600.0
    return -v if hemi.upper() in ('S', 'W') else v


def _head(path: Path, n: int = 40000) -> str:
    with open(path, 'rb') as f:
        return f.read(n).decode('latin-1')


def parse_file_position(path: Path) -> Optional[dict]:
    """The position the data file's own header states, or None. Every return names the line it came from."""
    head = _head(path)
    la, lo = _PANGAEA_LAT.search(head), _PANGAEA_LON.search(head)
    if la and lo:
        n_ev = len(_PANGAEA_LAT.findall(head))
        return {'lat': float(la.group(1)), 'lon': float(lo.group(1)), 'datum': 'WGS84',
                'source': 'file header: LATITUDE/LONGITUDE (PANGAEA event, WGS84 by PANGAEA convention)' + (f'; the first of {n_ev} events in the file' if n_ev > 1 else ''),
                'line': head[la.start():lo.end()][:120].replace('\n', ' ').replace('\t', ' ')}
    la, lo = _KTB_DEC_LAT.search(head), _KTB_DEC_LON.search(head)
    if la and lo:
        return {'lat': float(la.group(1)), 'lon': float(lo.group(1)), 'datum': 'UNKNOWN',
                'source': 'file header: the KTB file\'s own Latitude/Longitude lines in decimal degrees (datum not stated in the file)',
                'line': (la.group(0) + ' | ' + lo.group(0))[:120]}
    la, lo = _LAS_LATI.search(head), _LAS_LONG.search(head)
    if la and lo:
        return {'lat': float(la.group(1)), 'lon': float(lo.group(1)), 'datum': 'UNKNOWN',
                'source': 'file header: LAS LATI/LONG in decimal degrees (datum not stated in the file)',
                'line': (la.group(0).strip() + ' | ' + lo.group(0).strip())[:120]}
    la, lo = _LAS_LAT_DMS.search(head), _LAS_LON_DMS.search(head)
    if la and lo:
        return {'lat': round(_dms(*la.groups()), 6), 'lon': round(_dms(*lo.groups()), 6), 'datum': 'UNKNOWN',
                'source': 'file header: LAS LOC line in degrees-minutes-seconds (datum not stated in the file)',
                'line': (la.group(0) + ' | ' + lo.group(0))[:120]}
    r, h = _KTB_R.search(head), _KTB_H.search(head)
    if r and h:
        from . import geodesy as G
        E = float(''.join(r.groups()).replace(',', '.'))
        N = float(''.join(h.groups()).replace(',', '.'))
        zone = int(r.group(1)[0])
        lat_d, lon_d = G.unproject(E, N, G.gauss_krueger(zone))
        w = G.to_wgs84(lat_d, lon_d, 0.0, 'DHDN')
        return {'lat': round(w['lat'], 6), 'lon': round(w['lon'], 6), 'datum': 'WGS84',
                'source': f'file header: Gauss-Krueger zone {zone} Rechtswert/Hochwert (DHDN, Bessel) unprojected and shifted to WGS84 by EPSG 1776 ({w["accuracy_m"]:.0f} m)',
                'line': (r.group(0).strip('"') + ' | ' + h.group(0).strip('"'))[:120],
                'grid': {'zone': zone, 'E': E, 'N': N, 'dhdn_lat': round(lat_d, 6), 'dhdn_lon': round(lon_d, 6), 'shift_m': w['shift_m']}}
    return None


def parse_provenance_position(prov: dict) -> Optional[dict]:
    """A coordinate the sidecar's `well` statement carries from the source database's record, or None."""
    well = str(prov.get('well', ''))
    m = _PROV_LATLONG.search(well)
    if m:
        return {'lat': float(m.group(1)), 'lon': float(m.group(2)), 'datum': 'UNKNOWN', 'approximate': False,
                'source': 'provenance sidecar: the source database\'s well record, as written at catalogue time (datum not stated)', 'line': m.group(0)}
    m = _PROV_PAIR.search(well)
    if m:
        lat = float(m.group(2)) * (-1 if m.group(3).upper() == 'S' and float(m.group(2)) > 0 else 1)
        lon = float(m.group(4)) * (-1 if m.group(5).upper() == 'W' and float(m.group(4)) > 0 else 1)
        return {'lat': lat, 'lon': lon, 'datum': 'UNKNOWN', 'approximate': bool(m.group(1)),
                'source': 'provenance sidecar: the source database\'s metadata, as written at catalogue time' + (' (stated as approximate)' if m.group(1) else ''),
                'line': m.group(0)}
    return None


def _place(prov: dict) -> str:
    """What the sidecar does say about where, when it gives no coordinate."""
    well = str(prov.get('well', ''))
    return (well.split(';')[0].split(' - ')[0])[:110]


def position_of(name: str, e: Optional[CatalogEntry] = None) -> dict:
    """One entry's position by the chain in the module docstring."""
    e = e or CATALOG[name]
    fp = parse_file_position(e.las_path)
    if fp:
        return {'status': 'OK', 'how': 'file', **fp}
    if name in SAME_WELL:
        src = SAME_WELL[name]
        base = position_of(src)
        if base.get('status') == 'OK':
            return {'status': 'OK', 'how': 'same_well', 'lat': base['lat'], 'lon': base['lon'], 'datum': base['datum'],
                    'source': f'the same borehole as {src} (its {base["source"]})', 'line': base.get('line', ''), 'same_well_as': src}
    pp = parse_provenance_position(e.provenance)
    if pp:
        return {'status': 'OK', 'how': 'provenance', **pp}
    return {'status': 'NO POSITION', 'how': 'none', 'lat': None, 'lon': None, 'datum': None,
            'source': 'neither the file nor the sidecar states a coordinate', 'place': _place(e.provenance)}


def public() -> Dict[str, CatalogEntry]:
    return {n: e for n, e in CATALOG.items() if e.provenance.get('tier', 'public') == 'public'}


def positions() -> List[dict]:
    """Every public entry's position, recomputed from the files now."""
    out = []
    for name, e in sorted(public().items()):
        p = position_of(name, e)
        p['entry'] = name
        out.append(p)
    return out


def sidecar_positions() -> List[dict]:
    """The positions as the sidecars record them (the record the map reads)."""
    out = []
    for name, e in sorted(public().items()):
        p = dict(e.provenance.get('position') or {'status': 'NOT RECORDED', 'how': 'none', 'lat': None, 'lon': None})
        p['entry'] = name
        out.append(p)
    return out


def check_sidecars() -> dict:
    """The sidecars against a fresh recompute: any entry whose recorded position is not what its file gives now."""
    rec = {p['entry']: p for p in sidecar_positions()}
    bad = []
    for p in positions():
        r = rec.get(p['entry'], {})
        if r.get('status') != p['status']:
            bad.append({'entry': p['entry'], 'recorded': r.get('status'), 'now': p['status']}); continue
        if p['status'] == 'OK' and (abs(float(r['lat']) - p['lat']) > 1e-6 or abs(float(r['lon']) - p['lon']) > 1e-6 or r.get('how') != p['how']):
            bad.append({'entry': p['entry'], 'recorded': (r.get('lat'), r.get('lon'), r.get('how')), 'now': (p['lat'], p['lon'], p['how'])})
    return {'n': len(rec), 'bad': bad, 'ok': not bad}


def write_sidecars() -> List[str]:
    """Write each entry's position into its sidecar (the tool runs this once per leg; the gate checks it)."""
    written = []
    for name, e in sorted(public().items()):
        p = position_of(name, e)
        prov_path = e.las_path.with_suffix('.provenance.json')
        with open(prov_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        d = json.loads(raw)
        rec = {k: v for k, v in p.items() if k in ('status', 'how', 'lat', 'lon', 'datum', 'source', 'line', 'approximate', 'same_well_as', 'place', 'grid')}
        if d.get('position') != rec:
            d['position'] = rec
            with open(prov_path, 'w', encoding='utf-8') as f:          # the sidecars' own layout: two-space indent, characters as they are
                json.dump(d, f, indent=2, ensure_ascii=False)
                if raw.endswith('\n'):
                    f.write('\n')
            written.append(prov_path.name)
        e.provenance['position'] = rec
    return written


def counts(rows: Optional[List[dict]] = None) -> dict:
    rows = rows if rows is not None else sidecar_positions()
    ok = [r for r in rows if r.get('status') == 'OK']
    return {'entries': len(rows), 'positioned': len(ok), 'no_position': len(rows) - len(ok),
            'by_how': {h: sum(1 for r in ok if r.get('how') == h) for h in ('file', 'same_well', 'provenance')},
            'approximate': sum(1 for r in ok if r.get('approximate')),
            'distinct_positions': len({(round(r['lat'], 2), round(r['lon'], 2)) for r in ok})}


# -- the map ---------------------------------------------------------------------------------------------------------
def coastline() -> List[List[List[float]]]:
    with open(COAST_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)['lines']


def coastline_meta() -> dict:
    with open(COAST_FILE, 'r', encoding='utf-8') as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if k != 'lines'} | {'n_lines': len(d['lines']), 'n_points': sum(len(l) for l in d['lines'])}


def project(lon: float, lat: float, w: int, h: int) -> Tuple[float, float]:
    """Plate carree: x by longitude, y by latitude, the whole globe in the box."""
    return (lon + 180.0) / 360.0 * w, (90.0 - lat) / 180.0 * h


def svg(rows: Optional[List[dict]] = None, width: int = 1000, height: int = 500, register: Optional[Dict[str, str]] = None, title: str = 'The catalogue, where it was drilled') -> str:
    """The world map as one SVG: coastline, graticule, one numbered mark per positioned entry, the key below."""
    rows = rows if rows is not None else sidecar_positions()
    ok = [r for r in rows if r.get('status') == 'OK']
    none = [r for r in rows if r.get('status') != 'OK']
    key_h = 14 * (len(ok) + 2) + 14 * (len(none) + 2 if none else 0) + 24
    H = height + key_h
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{H}" viewBox="0 0 {width} {H}" font-family="Arial, Helvetica, sans-serif">',
         f'<title>{_x(title)}</title>', f'<rect x="0" y="0" width="{width}" height="{height}" fill="#eef4fa"/>']
    for lon in range(-180, 181, 30):
        x, _ = project(lon, 0, width, height)
        o.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" stroke="#cfd8e3" stroke-width="0.6"/>')
    for lat in range(-90, 91, 30):
        _, y = project(0, lat, width, height)
        o.append(f'<line x1="0" y1="{y:.1f}" x2="{width}" y2="{y:.1f}" stroke="#cfd8e3" stroke-width="0.6"/>')
    for line in coastline():
        pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (project(lo, la, width, height) for lo, la in line))
        o.append(f'<polyline points="{pts}" fill="none" stroke="#5c6b7a" stroke-width="0.8"/>')
    for g in groups(ok):                                     # one mark per distinct position; its numbers in the key
        x, y = project(g['lon'], g['lat'], width, height)
        used = any((register or {}).get(r['entry'], '') == 'USED' for r in g['rows'])
        fill = '#1f6fb2' if used else '#c77d00'
        nums = ','.join(str(r['n']) for r in g['rows'])
        tip = ' | '.join(f'{r["n"]}. {r["entry"]}' for r in g['rows'])
        o.append(f'<g><title>{_x(tip)}: {g["lat"]:.4f}, {g["lon"]:.4f}</title>'
                 f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4.2 if len(g["rows"]) == 1 else 5.2}" fill="{fill}" stroke="#ffffff" stroke-width="1.2"/>'
                 f'<text x="{x + 5.5:.1f}" y="{y - 4:.1f}" font-size="8.5" fill="#1b2a38">{nums}</text></g>')
    o.append(f'<text x="8" y="{height - 8}" font-size="10" fill="#3b4a5a">{len(ok)} of {len(rows)} entries positioned from their own files; '
             f'{len(none)} NO POSITION. Natural Earth 1:110m coastline (public domain). Plate carree. Blue: an entry a band reads; amber: carried, no band.</text>')
    y = height + 18
    o.append(f'<text x="8" y="{y}" font-size="11" font-weight="bold" fill="#1b2a38">Key</text>'); y += 14
    for i, r in enumerate(ok, 1):
        o.append(f'<text x="8" y="{y}" font-size="9.5" fill="#1b2a38">{i}. {_x(r["entry"])}  {r["lat"]:.4f}, {r["lon"]:.4f} {_x(r.get("datum") or "")}  - {_x(r.get("source", "")[:95])}</text>'); y += 14
    if none:
        y += 8
        o.append(f'<text x="8" y="{y}" font-size="11" font-weight="bold" fill="#1b2a38">NO POSITION ({len(none)})</text>'); y += 14
        for r in none:
            o.append(f'<text x="8" y="{y}" font-size="9.5" fill="#6b4a00">{_x(r["entry"])}  - {_x(r.get("source", ""))}; the sidecar says: {_x(r.get("place", ""))}</text>'); y += 14
    o.append('</svg>')
    return '\n'.join(o)


def groups(ok: List[dict]) -> List[dict]:
    """Positioned rows numbered in key order and grouped by position to 0.01 degree (about a kilometre): the
    entries of one borehole, or of sister holes at one site, share a mark."""
    out: Dict[Tuple[float, float], dict] = {}
    for i, r in enumerate(ok, 1):
        k = (round(r['lat'], 2), round(r['lon'], 2))
        g = out.setdefault(k, {'lat': r['lat'], 'lon': r['lon'], 'rows': []})
        g['rows'].append({'n': i, **r})
    return list(out.values())


def _x(s) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def text(rows: Optional[List[dict]] = None) -> str:
    rows = rows if rows is not None else sidecar_positions()
    c = counts(rows)
    out = [f"catalogue positions: {c['positioned']} of {c['entries']} entries positioned ({c['by_how']['file']} from the file's own header, "
           f"{c['by_how']['same_well']} as the same borehole as one that is, {c['by_how']['provenance']} from the sidecar's source record"
           + (f", {c['approximate']} of those approximate" if c['approximate'] else '') + f"); {c['no_position']} NO POSITION; {c['distinct_positions']} distinct positions"]
    w = max(len(r['entry']) for r in rows)
    for r in rows:
        if r.get('status') == 'OK':
            out.append(f"  {r['entry']:{w}s}  {r['lat']:9.4f} {r['lon']:10.4f}  {str(r.get('datum') or ''):7s} {r.get('source', '')[:90]}")
        else:
            out.append(f"  {r['entry']:{w}s}  NO POSITION           {r.get('source', '')}; the sidecar says: {r.get('place', '')[:70]}")
    return '\n'.join(out)
