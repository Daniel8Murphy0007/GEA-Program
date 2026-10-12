"""shapefile - the ESRI shapefile read as the specification lays it out, without a dependency.

A shapefile is three files: `.shp` (the geometry, a 100-byte header then records each with an 8-byte header
and a shape of one of a dozen types), `.dbf` (the attributes, an xBase table: a 32-byte header, field
descriptors of 32 bytes each ended by 0x0D, fixed-width records), and `.shx` (the index, not needed to read
sequentially). A `.prj` beside them carries the coordinate system as WKT. This reader takes a folder, a base
path or a zip, returns every feature's parts as lists of (lon, lat) pairs with its attributes, and says what
the coordinate system is. Geographic coordinates on NAD83 or WGS84 are taken as they are (the two differ by
about a metre, within every use here); a projected layer in one of the Texas State Plane zones the program
knows is unprojected through `geodesy`; anything else is refused by name rather than guessed at.

Shape types (ESRI Shapefile Technical Description, 1998): 0 Null, 1 Point, 3 PolyLine, 5 Polygon, 8
MultiPoint, 11 PointZ, 13 PolyLineZ, 15 PolygonZ, 18 MultiPointZ, 21 PointM, 23 PolyLineM, 25 PolygonM,
28 MultiPointM. Z and M values are read past, not kept: this program uses the plan position.
"""
from __future__ import annotations

import io
import os
import re
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SHAPE_TYPES = {0: 'Null', 1: 'Point', 3: 'PolyLine', 5: 'Polygon', 8: 'MultiPoint', 11: 'PointZ', 13: 'PolyLineZ', 15: 'PolygonZ',
               18: 'MultiPointZ', 21: 'PointM', 23: 'PolyLineM', 25: 'PolygonM', 28: 'MultiPointM', 31: 'MultiPatch'}
_BASE = {1: 'Point', 11: 'Point', 21: 'Point', 3: 'PolyLine', 13: 'PolyLine', 23: 'PolyLine', 5: 'Polygon', 15: 'Polygon', 25: 'Polygon',
         8: 'MultiPoint', 18: 'MultiPoint', 28: 'MultiPoint', 0: 'Null'}


@dataclass
class Feature:
    kind: str                                   # Point | PolyLine | Polygon | MultiPoint | Null
    parts: List[List[Tuple[float, float]]]     # each part a list of (x, y) = (lon, lat) once unprojected
    attrs: Dict[str, object]
    bbox: Optional[Tuple[float, float, float, float]] = None


@dataclass
class Layer:
    name: str
    shape_type: str
    crs: dict
    bbox: Tuple[float, float, float, float]    # xmin, ymin, xmax, ymax in lon/lat
    fields: List[dict]
    features: List[Feature] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    declared_kind: Optional[str] = None          # 'fault' | 'fold' when the site declares what every feature is (a layer whose attributes do not say)

    @property
    def n(self) -> int:
        return len(self.features)

    def counts(self, attr: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.features:
            k = str(f.attrs.get(attr, ''))
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------------------------------------------
# .prj
# ---------------------------------------------------------------------------------------------------------------

def parse_prj(wkt: Optional[str]) -> dict:
    """What the WKT says, enough to decide: geographic (and which datum) or projected (and which zone, if known)."""
    if not wkt or not wkt.strip():
        return {'kind': 'unknown', 'datum': None, 'wkt': None, 'usable': False, 'note': 'no .prj beside the shapefile: the coordinate system is not stated'}
    w = wkt.strip()
    datum = None
    m = re.search(r'DATUM\["([^"]+)"', w)
    if m:
        d = m.group(1).upper()
        datum = 'NAD83' if '1983' in d or 'NAD83' in d else 'WGS84' if 'WGS' in d and '84' in d else 'NAD27' if '1927' in d else d
    if w.upper().startswith('GEOGCS'):
        usable = datum in ('NAD83', 'WGS84')
        return {'kind': 'geographic', 'datum': datum, 'wkt': w[:400], 'usable': usable,
                'note': '' if usable else f'geographic on {datum}: this program takes NAD83 or WGS84 as they are; another datum is not transformed here'}
    if w.upper().startswith('PROJCS'):
        name = re.search(r'PROJCS\["([^"]+)"', w)
        pname = name.group(1) if name else '?'
        zone = None
        up = pname.upper().replace(' ', '_')
        for z in ('TX_NORTH_CENTRAL', 'TX_SOUTH_CENTRAL', 'TX_NORTH', 'TX_CENTRAL', 'TX_SOUTH'):
            key = z.replace('TX_', 'TEXAS_')
            if key in up or z in up:
                zone = z
                break
        # the linear unit is the PROJCS's own (the last UNIT[...] in the string); the GEOGCS inside carries Degree
        units = re.findall(r'UNIT\["([^"]*)"\s*,\s*([0-9.eE+-]+)', w)
        unit = 'm'
        if units:
            uname, ufac = units[-1][0].upper(), float(units[-1][1])
            unit = 'usft' if abs(ufac - 0.3048006096) < 1e-7 else ('ft' if abs(ufac - 0.3048) < 1e-7 else 'm')
        # a generic Transverse Mercator (UTM, a state plane zone, any TM) or Lambert Conformal Conic from its own parameters
        zone_def = None
        proj = re.search(r'PROJECTION\["([^"]+)"', w)
        pr = (proj.group(1) if proj else '').upper()
        params = {k.upper(): float(v) for k, v in re.findall(r'PARAMETER\["([^"]+)"\s*,\s*([0-9.eE+-]+)\]', w)}
        ell = 'GRS80' if datum == 'NAD83' else ('WGS84' if datum == 'WGS84' else ('CLARKE1866' if datum == 'NAD27' else None))
        if ell and pr == 'TRANSVERSE_MERCATOR' and 'CENTRAL_MERIDIAN' in params:
            zone_def = {'kind': 'tm', 'lat0': params.get('LATITUDE_OF_ORIGIN', 0.0), 'lon0': params['CENTRAL_MERIDIAN'], 'k0': params.get('SCALE_FACTOR', 1.0),
                        'fe': params.get('FALSE_EASTING', 0.0), 'fn': params.get('FALSE_NORTHING', 0.0), 'ellipsoid': ell}
        elif ell and pr in ('LAMBERT_CONFORMAL_CONIC', 'LAMBERT_CONFORMAL_CONIC_2SP') and 'STANDARD_PARALLEL_1' in params:
            zone_def = {'kind': 'lcc', 'lat0': params.get('LATITUDE_OF_ORIGIN', 0.0), 'lon0': params.get('CENTRAL_MERIDIAN', 0.0), 'lat1': params['STANDARD_PARALLEL_1'],
                        'lat2': params.get('STANDARD_PARALLEL_2', params['STANDARD_PARALLEL_1']), 'fe': params.get('FALSE_EASTING', 0.0), 'fn': params.get('FALSE_NORTHING', 0.0), 'ellipsoid': ell}
        usable = (zone is not None and datum == 'NAD83') or zone_def is not None
        how = ('the Texas State Plane zone ' + zone if zone and datum == 'NAD83' else (f'{pr.replace("_", " ").title()} on {ell} from the .prj parameters' if zone_def else ''))
        return {'kind': 'projected', 'datum': datum, 'projection': pname, 'zone': zone, 'zone_def': zone_def, 'unit': unit, 'wkt': w[:400], 'usable': usable, 'how': how,
                'note': '' if usable else f'projected ({pname}, {pr or "projection not named"}, datum {datum}): not a Transverse Mercator or Lambert Conformal Conic on NAD83/WGS84/NAD27 '
                                          f'that this program unprojects from its parameters - supply the layer in geographic NAD83/WGS84'}
    return {'kind': 'unknown', 'datum': datum, 'wkt': w[:400], 'usable': False, 'note': 'a coordinate system this reader does not recognise'}


# ---------------------------------------------------------------------------------------------------------------
# .dbf
# ---------------------------------------------------------------------------------------------------------------

def read_dbf(data: bytes, encoding: str = 'latin-1') -> Tuple[List[dict], List[dict]]:
    """(fields, records): xBase III/IV - C text, N/F numbers, L logical, D dates (as YYYYMMDD text)."""
    if len(data) < 32:
        raise ValueError('the .dbf is shorter than its header')
    n_rec, hdr_len, rec_len = struct.unpack('<IHH', data[4:12])
    fields = []
    p = 32
    while p + 32 <= len(data) and data[p:p + 1] != b'\r':
        fd = data[p:p + 32]
        name = fd[:11].split(b'\x00')[0].decode(encoding, 'replace').strip()
        fields.append({'name': name, 'type': chr(fd[11]), 'length': fd[16], 'decimals': fd[17]})
        p += 32
    if sum(f['length'] for f in fields) + 1 != rec_len:
        raise ValueError(f'the .dbf record length {rec_len} does not match its fields ({sum(f["length"] for f in fields) + 1})')
    out = []
    p = hdr_len
    for _ in range(n_rec):
        r = data[p:p + rec_len]
        if len(r) < rec_len:
            break
        p += rec_len
        if r[0:1] == b'*':                                      # a deleted record
            continue
        rec: Dict[str, object] = {}
        q = 1
        for f in fields:
            raw = r[q:q + f['length']]; q += f['length']
            t = f['type']
            if t in ('N', 'F'):
                s = raw.decode('ascii', 'replace').strip()
                try:
                    rec[f['name']] = (float(s) if ('.' in s or 'e' in s.lower() or f['decimals']) else int(s)) if s not in ('', '*' * len(s)) else None
                except ValueError:
                    rec[f['name']] = None
            elif t == 'L':
                rec[f['name']] = {b'T': True, b't': True, b'Y': True, b'y': True, b'F': False, b'f': False, b'N': False, b'n': False}.get(raw[:1], None)
            else:
                rec[f['name']] = raw.decode(encoding, 'replace').rstrip('\x00 ').strip()
        out.append(rec)
    return fields, out


# ---------------------------------------------------------------------------------------------------------------
# .shp
# ---------------------------------------------------------------------------------------------------------------

def _points(buf: bytes, off: int, n: int) -> Tuple[List[Tuple[float, float]], int]:
    pts = list(struct.unpack_from(f'<{2 * n}d', buf, off))
    return [(pts[2 * i], pts[2 * i + 1]) for i in range(n)], off + 16 * n


def read_shx(data: bytes) -> List[Tuple[int, int]]:
    """The index: (offset in bytes, content length in bytes) per record."""
    if len(data) < 100 or struct.unpack('>i', data[:4])[0] != 9994:
        raise ValueError('not a shapefile index (.shx)')
    n = (len(data) - 100) // 8
    out = []
    for i in range(n):
        off, cl = struct.unpack('>ii', data[100 + 8 * i:108 + 8 * i])
        out.append((off * 2, cl * 2))
    return out


def read_shp(data: bytes, shx: Optional[bytes] = None) -> Tuple[int, Tuple[float, float, float, float], List[Tuple[str, List[List[Tuple[float, float]]], Optional[Tuple[float, float, float, float]]]]]:
    """(shape type code, bbox, [(kind, parts, bbox), ...]). With the .shx, records are found by its offsets - the
    index exists because a record's own content length is not always right (a USGS layer understated one of
    15,699); without it, sequentially. Z and M are read past."""
    if len(data) < 100 or struct.unpack('>i', data[:4])[0] != 9994:
        raise ValueError('not a shapefile (.shp): the file code is not 9994')
    file_len = struct.unpack('>i', data[24:28])[0] * 2
    version, shp_type = struct.unpack('<ii', data[28:36])
    if version != 1000:
        raise ValueError(f'shapefile version {version} is not 1000')
    bbox = struct.unpack('<4d', data[36:68])
    shapes = []
    end = min(file_len, len(data))
    index = read_shx(shx) if shx else None
    positions = iter(index) if index is not None else None
    p = 100
    while True:
        if positions is not None:
            nxt = next(positions, None)
            if nxt is None:
                break
            p, content_bytes = nxt
            if p + 8 > len(data):
                break
            p += 8
            rec_end = p + content_bytes
        else:
            if p + 8 > end:
                break
            rec_no, content_len = struct.unpack('>ii', data[p:p + 8])
            p += 8
            rec_end = p + content_len * 2
        t = struct.unpack('<i', data[p:p + 4])[0]
        kind = _BASE.get(t)
        if kind is None:
            raise ValueError(f'record {len(shapes) + 1}: shape type {t} ({SHAPE_TYPES.get(t, "unknown")}) is not one the specification names'
                             + ('' if index is not None else ' - a record before it may carry a wrong content length; put the .shx beside the .shp so records are found by the index'))
        if kind == 'Null':
            shapes.append(('Null', [], None))
        elif kind == 'Point':
            x, y = struct.unpack_from('<2d', data, p + 4)
            shapes.append(('Point', [[(x, y)]], (x, y, x, y)))
        elif kind == 'MultiPoint':
            bb = struct.unpack_from('<4d', data, p + 4)
            n = struct.unpack_from('<i', data, p + 36)[0]
            pts, _ = _points(data, p + 40, n)
            shapes.append(('MultiPoint', [[q] for q in pts], bb))
        else:                                                      # PolyLine / Polygon
            bb = struct.unpack_from('<4d', data, p + 4)
            n_parts, n_pts = struct.unpack_from('<2i', data, p + 36)
            idx = list(struct.unpack_from(f'<{n_parts}i', data, p + 44))
            pts, _ = _points(data, p + 44 + 4 * n_parts, n_pts)
            parts = [pts[idx[i]:(idx[i + 1] if i + 1 < n_parts else n_pts)] for i in range(n_parts)]
            shapes.append((kind, parts, bb))
        p = rec_end
    return shp_type, bbox, shapes


# ---------------------------------------------------------------------------------------------------------------
# the layer
# ---------------------------------------------------------------------------------------------------------------

def members(src: str) -> List[str]:
    """The shapefiles a zip or a folder holds, by base name (a public download often carries several)."""
    if os.path.isfile(src) and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            return sorted(os.path.basename(n)[:-4] for n in z.namelist() if n.lower().endswith('.shp'))
    if os.path.isdir(src):
        return sorted(n[:-4] for n in os.listdir(src) if n.lower().endswith('.shp'))
    if src.lower().endswith('.shp'):
        return [os.path.basename(src)[:-4]]
    return []


def _load_parts(src: str, member: Optional[str] = None) -> Tuple[str, Dict[str, bytes]]:
    """From a zip, a folder, a .shp path or a base path: the base name and {ext: bytes} for shp/dbf/prj/xml.
    A zip or folder holding several shapefiles needs `member` (the base name) to say which."""
    want = ('.shp', '.dbf', '.prj', '.shp.xml', '.shx')
    parts: Dict[str, bytes] = {}
    if os.path.isfile(src) and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if not n.endswith('/')]
            shps = [n for n in names if n.lower().endswith('.shp')]
            if not shps:
                raise ValueError(f'{os.path.basename(src)} holds no .shp')
            if member:
                pick = [n for n in shps if os.path.basename(n)[:-4].lower() == member.lower().removesuffix('.shp')]
                if not pick:
                    raise ValueError(f'{os.path.basename(src)} holds no shapefile named {member!r}; it holds: {", ".join(os.path.basename(s)[:-4] for s in shps)}')
                shps = pick
            if len(shps) > 1:
                raise ValueError(f'{os.path.basename(src)} holds {len(shps)} shapefiles - name one with member=: {", ".join(os.path.basename(s)[:-4] for s in shps)}')
            base = shps[0][:-4]
            for n in names:
                low = n.lower()
                for ext in want:
                    if low == (base + ext).lower():
                        parts[ext] = z.read(n)
            return os.path.basename(base), parts
    if os.path.isdir(src):
        shps = [n for n in os.listdir(src) if n.lower().endswith('.shp')]
        if member:
            shps = [n for n in shps if n[:-4].lower() == member.lower().removesuffix('.shp')]
            if not shps:
                raise ValueError(f'{src} holds no shapefile named {member!r}')
        if len(shps) != 1:
            raise ValueError(f'{src} holds {len(shps)} shapefile(s) - name one with member=: {", ".join(n[:-4] for n in shps)}')
        src = os.path.join(src, shps[0])
    base = src[:-4] if src.lower().endswith('.shp') else src
    if not os.path.isfile(base + '.shp'):
        raise ValueError(f'no such shapefile: {base}.shp')
    d = os.path.dirname(base) or '.'
    for n in os.listdir(d):
        low = n.lower()
        for ext in want:
            if low == (os.path.basename(base) + ext).lower():
                with open(os.path.join(d, n), 'rb') as f:
                    parts[ext] = f.read()
    return os.path.basename(base), parts


def _metadata(xml: Optional[bytes]) -> dict:
    """What the FGDC/ISO metadata beside the layer says of itself: titles, originators, dates, abstract."""
    if not xml:
        return {}
    t = xml.decode('utf-8', 'replace')
    get = lambda tag, n=4: [re.sub(r'\s+', ' ', m).strip()[:300] for m in re.findall(rf'<{tag}>(.*?)</{tag}>', t, flags=re.S)[:n]]
    return {'titles': get('title'), 'originators': get('origin'), 'pubdates': get('pubdate'), 'abstract': (get('abstract', 1) or [''])[0]}


def read(src: str, max_features: Optional[int] = None, member: Optional[str] = None) -> Layer:
    """A shapefile as a Layer in lon/lat: from a zip, a folder, a .shp path or a base path; `member` names the
    shapefile inside a zip or folder that holds several."""
    from . import geodesy as GD
    name, parts = _load_parts(src, member)
    if '.shp' not in parts:
        raise ValueError(f'{name}: no .shp')
    if '.dbf' not in parts:
        raise ValueError(f'{name}: no .dbf beside the .shp - the attributes are the layer')
    crs = parse_prj(parts['.prj'].decode('utf-8', 'replace') if '.prj' in parts else None)
    if not crs['usable']:
        raise ValueError(f"{name}: {crs['note']}")
    shp_type, bbox, shapes = read_shp(parts['.shp'], parts.get('.shx'))
    fields, records = read_dbf(parts['.dbf'])
    if len(records) != len(shapes):
        raise ValueError(f'{name}: {len(shapes)} shapes but {len(records)} attribute records - the .shp and .dbf do not belong together')
    feats: List[Feature] = []
    unproj = None
    if crs['kind'] == 'projected':
        zone = GD.ZONES[crs['zone']] if (crs.get('zone') and crs.get('datum') == 'NAD83') else crs['zone_def']
        scale = GD.length_unit(crs['unit'])[0] if crs['unit'] != 'm' else 1.0

        def unproj(x: float, y: float) -> Tuple[float, float]:
            lat, lon = GD.unproject(x * scale, y * scale, zone)
            return lon, lat
    for (kind, pts_parts, bb), rec in zip(shapes, records):
        if unproj is not None:
            pts_parts = [[unproj(x, y) for (x, y) in part] for part in pts_parts]
            bb = None
        feats.append(Feature(kind, pts_parts, rec, bb))
        if max_features and len(feats) >= max_features:
            break
    if unproj is not None:
        xs = [x for f in feats for part in f.parts for (x, _) in part]; ys = [y for f in feats for part in f.parts for (_, y) in part]
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    return Layer(name=name, shape_type=SHAPE_TYPES.get(shp_type, str(shp_type)), crs=crs, bbox=tuple(bbox), fields=fields, features=feats,
                 source={'file': os.path.basename(src), 'metadata': _metadata(parts.get('.shp.xml')), 'has_shx': '.shx' in parts})


# ---------------------------------------------------------------------------------------------------------------
# a writer, so the reader is proven on files this program made (and the gate needs no download)
# ---------------------------------------------------------------------------------------------------------------

def write(base_path: str, kind: str, features: List[Tuple[List[List[Tuple[float, float]]], dict]], fields: List[Tuple[str, str, int, int]],
          prj_wkt: Optional[str] = None) -> Dict[str, str]:
    """Write <base>.shp/.shx/.dbf (and .prj): kind is Point | PolyLine | Polygon; each feature (parts, attrs);
    fields are (name, type, length, decimals)."""
    code = {'Point': 1, 'PolyLine': 3, 'Polygon': 5}[kind]
    recs = []
    xs: List[float] = []; ys: List[float] = []
    for parts, _ in features:
        for part in parts:
            for x, y in part:
                xs.append(x); ys.append(y)
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    for i, (parts, _) in enumerate(features):
        if kind == 'Point':
            body = struct.pack('<i2d', code, parts[0][0][0], parts[0][0][1])
        else:
            pts = [p for part in parts for p in part]
            px = [p[0] for p in pts]; py = [p[1] for p in pts]
            idx = []; k = 0
            for part in parts:
                idx.append(k); k += len(part)
            body = struct.pack('<i4d2i', code, min(px), min(py), max(px), max(py), len(parts), len(pts)) + struct.pack(f'<{len(parts)}i', *idx) \
                + b''.join(struct.pack('<2d', x, y) for x, y in pts)
        recs.append(struct.pack('>ii', i + 1, len(body) // 2) + body)
    content = b''.join(recs)
    header = struct.pack('>i5i', 9994, 0, 0, 0, 0, 0) + struct.pack('>i', (100 + len(content)) // 2) + struct.pack('<ii', 1000, code) \
        + struct.pack('<4d', *bbox) + struct.pack('<4d', 0, 0, 0, 0)
    out = {'shp': base_path + '.shp', 'shx': base_path + '.shx', 'dbf': base_path + '.dbf'}
    with open(out['shp'], 'wb') as f:
        f.write(header + content)
    shx = b''; off = 50
    for r in recs:
        shx += struct.pack('>ii', off, (len(r) - 8) // 2); off += len(r) // 2
    with open(out['shx'], 'wb') as f:
        f.write(header[:24] + struct.pack('>i', (100 + len(shx)) // 2) + header[28:] + shx)
    rec_len = 1 + sum(ln for _, _, ln, _ in fields)
    hdr = struct.pack('<B3BIHH20x', 3, 24, 1, 1, len(features), 32 + 32 * len(fields) + 1, rec_len)
    fdesc = b''.join(struct.pack('<11sB4xBB14x', nm.encode('ascii')[:11].ljust(11, b'\x00'), ord(t), ln, dec) for nm, t, ln, dec in fields) + b'\r'
    body = b''
    for _, attrs in features:
        row = b' '
        for nm, t, ln, dec in fields:
            v = attrs.get(nm, '')
            if t in ('N', 'F'):
                s = ('' if v is None else (f'{float(v):.{dec}f}' if dec else str(int(v)))).rjust(ln)[:ln]
            elif t == 'L':
                s = ('T' if v else 'F') if v is not None else '?'
            else:
                s = str(v if v is not None else '')[:ln].ljust(ln)
            row += s.encode('latin-1', 'replace')
        body += row
    with open(out['dbf'], 'wb') as f:
        f.write(hdr + fdesc + body + b'\x1a')
    if prj_wkt:
        out['prj'] = base_path + '.prj'
        with open(out['prj'], 'w', encoding='utf-8') as f:
            f.write(prj_wkt)
    return out


NAD83_GEOGCS = ('GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137.0,298.257222101]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
